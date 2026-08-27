"""Standardized destination filenames and collision-safe paths.

Spec contract: build `YYYY-MM-DD_Sender_Description.ext`, with date
precedence date-found-in-text -> PDF metadata (`/CreationDate`) ->
file mtime -> `Undated`; sender from a `From:`/letterhead/signature
pattern or else the leaf's folder name; description from the first
meaningful line, trimmed to 40 chars. Every component is sanitized. A
destination path is only used after `unique_path` confirms it does not
already exist, appending `-1`, `-2`, ... on collision.

Everything here is a pure function of the text, the metadata, and the
source path — no network, and (apart from the existence checks in
`unique_path`) no filesystem writes.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path

#: Longest a single filename component (sender, description) may be.
MAX_COMPONENT_LENGTH = 40

#: What `sanitize` returns rather than an empty string, so a filename can
#: never collapse into `_-.pdf` or lose a component silently.
SANITIZE_FALLBACK = "Untitled"

#: Date component used when no date can be established from any source.
UNDATED = "Undated"

#: Where the date in a filename came from, for the report's "Date used" column.
DATE_SOURCE_TEXT = "text"
DATE_SOURCE_METADATA = "metadata"
DATE_SOURCE_MTIME = "mtime"
DATE_SOURCE_NONE = "none"

_MONTHS = {
    "jan": 1,
    "feb": 2,
    "mar": 3,
    "apr": 4,
    "may": 5,
    "jun": 6,
    "jul": 7,
    "aug": 8,
    "sep": 9,
    "oct": 10,
    "nov": 11,
    "dec": 12,
}

# Three shapes cover essentially every date a court filing or a utility bill
# prints: ISO, "March 5, 2024", and "03/05/2024".
_ISO_RE = re.compile(r"\b(\d{4})-(\d{1,2})-(\d{1,2})\b")
_MONTH_NAME_RE = re.compile(
    r"\b(" + "|".join(_MONTHS) + r")[a-z]*\.?\s+(\d{1,2})(?:st|nd|rd|th)?,?\s+(\d{4})\b",
    re.IGNORECASE,
)
_NUMERIC_RE = re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b")

#: A cue word immediately before a date ("dated March 5, 2024", "Filed:
#: 2024-03-05") marks the date the document is *about*, which beats a date
#: that merely appears somewhere in the body.
_CUE_RE = re.compile(r"(?:dated|filed|received|entered|signed|date|on)\W{0,4}$", re.IGNORECASE)
_CUE_WINDOW = 16

#: PDF document info dates look like `D:20240612153000-05'00'`.
_PDF_DATE_RE = re.compile(r"D?:?\s*(\d{4})(\d{2})(\d{2})")
_PDF_DATE_KEYS = ("/CreationDate", "/ModDate")

_FROM_RE = re.compile(r"^\s*from\s*:\s*(?P<sender>.+)$", re.IGNORECASE)
#: Letterhead: a line naming an organization that sends legal or financial mail.
_LETTERHEAD_RE = re.compile(
    r"\b(?:LLP|LLC|PLLC|P\.?C\.?|L\.?L\.?P\.?|Law\s+Offices?|Attorneys?\s+at\s+Law"
    r"|County(?:\s+of\s+\w+)?|District\s+Court|Bank|Credit\s+Union|Hospital|Clinic"
    r"|Department\s+of\s+\w+)\b",
    re.IGNORECASE,
)
_SIGNATURE_CUE_RE = re.compile(
    r"^\s*(?:sincerely|respectfully\s+submitted|respectfully|regards|"
    r"best\s+regards|yours\s+truly)\b[,.]?\s*$",
    re.IGNORECASE,
)

#: Characters illegal in a filename on Windows, plus the POSIX separator.
_ILLEGAL_CHARS_RE = re.compile(r'[<>:"/\\|?*]')
_CONTROL_CHARS_RE = re.compile(r"[\x00-\x1f\x7f]")

#: Lines that carry no description value: pure dates, page markers, headers.
_NOISE_LINE_RE = re.compile(
    r"^\s*(?:page\s+\d+.*|\d+|[-_=*#.\s]+|from\s*:.*|to\s*:.*|re\s*:\s*)$",
    re.IGNORECASE,
)
_MIN_DESCRIPTION_LENGTH = 3


@dataclass(frozen=True)
class ResolvedDate:
    """The date chosen for a filename and which source produced it."""

    value: str
    source: str


@dataclass(frozen=True)
class NameParts:
    """A built filename together with the provenance the report needs."""

    filename: str
    date: ResolvedDate
    sender: str
    description: str


# --------------------------------------------------------------------------
# Dates
# --------------------------------------------------------------------------


def _valid_date(year: int, month: int, day: int) -> date | None:
    """Return the date, or None when the numbers do not form one."""
    try:
        return date(year, month, day)
    except ValueError:
        return None


def _candidate_dates(text: str) -> list[tuple[int, date]]:
    """Every parseable date in `text`, as (position, date), in text order."""
    found: list[tuple[int, date]] = []
    for match in _ISO_RE.finditer(text):
        parsed = _valid_date(int(match[1]), int(match[2]), int(match[3]))
        if parsed:
            found.append((match.start(), parsed))
    for match in _MONTH_NAME_RE.finditer(text):
        parsed = _valid_date(int(match[3]), _MONTHS[match[1][:3].lower()], int(match[2]))
        if parsed:
            found.append((match.start(), parsed))
    for match in _NUMERIC_RE.finditer(text):
        parsed = _valid_date(int(match[3]), int(match[1]), int(match[2]))
        if parsed:
            found.append((match.start(), parsed))
    return sorted(found, key=lambda item: item[0])


def date_from_text(text: str) -> date | None:
    """The date the document is about, preferring one behind a cue word.

    "Filed: 2024-03-05" outranks a due date printed lower on the page; with
    no cue anywhere, the first date in the document wins.
    """
    candidates = _candidate_dates(text)
    if not candidates:
        return None
    for position, parsed in candidates:
        preceding = text[max(0, position - _CUE_WINDOW) : position]
        if _CUE_RE.search(preceding):
            return parsed
    return candidates[0][1]


def date_from_metadata(metadata: dict[str, str]) -> date | None:
    """The creation date recorded in a PDF's document info dictionary."""
    for key in _PDF_DATE_KEYS:
        raw = metadata.get(key)
        if not raw:
            continue
        match = _PDF_DATE_RE.match(str(raw).strip())
        if not match:
            continue
        parsed = _valid_date(int(match[1]), int(match[2]), int(match[3]))
        if parsed:
            return parsed
    return None


def date_from_mtime(source: Path) -> date | None:
    """The source file's modification date, or None when it can't be read."""
    try:
        return datetime.fromtimestamp(source.stat().st_mtime).date()
    except OSError:
        return None


def resolve_date(text: str, metadata: dict[str, str], source: Path) -> ResolvedDate:
    """Apply the spec's date precedence: text -> PDF metadata -> mtime -> Undated."""
    for candidate, label in (
        (date_from_text(text), DATE_SOURCE_TEXT),
        (date_from_metadata(metadata), DATE_SOURCE_METADATA),
        (date_from_mtime(source), DATE_SOURCE_MTIME),
    ):
        if candidate is not None:
            return ResolvedDate(value=candidate.isoformat(), source=label)
    return ResolvedDate(value=UNDATED, source=DATE_SOURCE_NONE)


# --------------------------------------------------------------------------
# Sender and description
# --------------------------------------------------------------------------


def _meaningful_lines(text: str) -> list[str]:
    """Non-empty, non-noise lines of `text`, in order."""
    return [line.strip() for line in text.splitlines() if line.strip()]


def leaf_folder_name(leaf_path: str) -> str:
    """The last segment of a leaf path — the sender fallback."""
    segments = [segment for segment in leaf_path.split("/") if segment.strip()]
    return segments[-1] if segments else SANITIZE_FALLBACK


def sender_from_text(text: str, leaf_path: str) -> str:
    """Who sent the document: `From:`, letterhead, signature, else the folder.

    The fallback is deliberate rather than "Unknown": a citation filed into
    `01_PROBATE_COURT` reads better as `..._01_PROBATE_COURT_Citation.pdf`
    than as `..._Unknown_Citation.pdf`, and it never invents a party.
    """
    lines = _meaningful_lines(text)
    for line in lines:
        match = _FROM_RE.match(line)
        if match and match["sender"].strip():
            return match["sender"].strip()
    for line in lines:
        if _LETTERHEAD_RE.search(line):
            return line
    for index, line in enumerate(lines):
        if _SIGNATURE_CUE_RE.match(line) and index + 1 < len(lines):
            return lines[index + 1]
    return leaf_folder_name(leaf_path)


def description_from_text(text: str, sender: str) -> str:
    """The first meaningful line that isn't the sender or boilerplate."""
    for line in _meaningful_lines(text):
        if line == sender or _NOISE_LINE_RE.match(line):
            continue
        if _candidate_dates(line) and len(line) <= 24:
            continue
        if len(line) < _MIN_DESCRIPTION_LENGTH:
            continue
        return line
    return SANITIZE_FALLBACK


# --------------------------------------------------------------------------
# Sanitizing and assembly
# --------------------------------------------------------------------------


def sanitize(component: str, max_length: int = MAX_COMPONENT_LENGTH) -> str:
    """Make one filename component safe on Windows, macOS, and Linux.

    Strips path separators, control characters, and the characters Windows
    reserves; collapses whitespace and underscores (which separate the
    components, so a component may not contain one); caps length; and trims
    the trailing dots and spaces Windows silently drops. Never empty.
    """
    cleaned = _CONTROL_CHARS_RE.sub(" ", component)
    cleaned = _ILLEGAL_CHARS_RE.sub(" ", cleaned)
    cleaned = cleaned.replace("_", " ")
    cleaned = " ".join(cleaned.split())
    cleaned = cleaned[:max_length].strip(" .")
    return cleaned or SANITIZE_FALLBACK


def build_name_parts(
    text: str, metadata: dict[str, str], source: Path, leaf_path: str
) -> NameParts:
    """Build the destination filename and the provenance behind it."""
    resolved = resolve_date(text, metadata, source)
    raw_sender = sender_from_text(text, leaf_path)
    sender = sanitize(raw_sender)
    description = sanitize(description_from_text(text, raw_sender))
    filename = f"{resolved.value}_{sender}_{description}{source.suffix.lower()}"
    return NameParts(
        filename=filename,
        date=resolved,
        sender=sender,
        description=description,
    )


def build_name(text: str, metadata: dict[str, str], source: Path, leaf_path: str) -> str:
    """Return `YYYY-MM-DD_Sender_Description.ext` (or a degraded variant)."""
    return build_name_parts(text, metadata, source, leaf_path).filename


def unique_path(dest_dir: Path, filename: str, reserved: set[Path] | None = None) -> Path:
    """A path under `dest_dir` that no file — and no earlier plan entry — holds.

    `reserved` carries the destinations already claimed by this run, so two
    documents that name identically in the same batch get `-1` against each
    other and not just against files already on disk. The caller re-checks
    immediately before the move, so a concurrent writer still cannot be
    overwritten.
    """
    taken = reserved if reserved is not None else set()
    stem = Path(filename).stem
    suffix = Path(filename).suffix
    candidate = dest_dir / filename
    index = 0
    while candidate.exists() or candidate in taken:
        index += 1
        candidate = dest_dir / f"{stem}-{index}{suffix}"
    return candidate
