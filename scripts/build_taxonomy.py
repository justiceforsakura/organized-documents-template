#!/usr/bin/env python3
"""Generate `taxonomy.json` from the folder tree in `create_organized_folders.sh`.

The shipped taxonomy is generated, not hand-authored: this script parses the
`dirs` array from the template shell script and emits exactly one leaf entry per
folder, seeding each leaf's keywords from its own path segments ("Bank
Statements" -> "bank statement", "statement"). A curated override layer then
hand-tunes the leaves whose folder names are too ambiguous to seed well -- the
`LEGAL_AND_ADVOCACY` litigation folders above all.

Regenerating is safe: when the output file already exists, the new seed is
merged into it with a versioned merge in which user edits win. A leaf the user
retargeted keeps its custom `path` as long as the entry records the template
folder it came from in `seed_path`.

Usage:

    python scripts/build_taxonomy.py                 # regenerate + merge in place
    python scripts/build_taxonomy.py --no-merge      # overwrite, discarding user edits
    python scripts/build_taxonomy.py --check         # verify the file is up to date

Coverage invariant: every folder in the `dirs` array appears exactly once in
`leaves`. `tests/test_build_taxonomy.py` enforces it.
"""

from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_SCRIPT = REPO_ROOT / "create_organized_folders.sh"
DEFAULT_OUTPUT = REPO_ROOT / "organized_docs" / "data" / "taxonomy.json"

SCHEMA_VERSION = 2
DEFAULT_OUTPUT_ROOT = "~/Documents/Organized Documents"
DEFAULT_REVIEW_FOLDER = "_Needs Review"
DEFAULT_GROUP_THRESHOLD = 0.5
DEFAULT_CONFIDENCE_THRESHOLD = 0.6
DEFAULT_IGNORE = ["~$", ".DS_Store", "ORGANIZING-LOG.md"]

# Fields the merge treats as user-owned settings rather than generated output.
SETTING_KEYS = (
    "output_root",
    "review_folder",
    "group_threshold",
    "confidence_threshold",
    "ignore",
)

# Words carrying no classification signal on their own.
STOPWORDS = frozenset(
    {"a", "an", "and", "or", "of", "the", "for", "with", "to", "in", "on", "my"}
)

# A folder whose name reduces to one of these gets no keywords at all: an
# ambiguous document must land in _Needs Review, never in a catch-all folder.
GENERIC_SEGMENTS = frozenset(
    {
        "miscellaneous",
        "misc",
        "other",
        "general",
        "document",
        "doc",
        "file",
        "stuff",
        "2borganized",
        "command center",
    }
)

# Too weak to stand alone as a single-word keyword, though fine inside a phrase.
WEAK_WORDS = frozenset(
    {
        "info",
        "information",
        "document",
        "doc",
        "record",
        "file",
        "material",
        "detail",
        "form",
        "copy",
        "backup",
        "misc",
        "other",
        "shared",
        "data",
        "note",
        "page",
        "item",
    }
)


# --------------------------------------------------------------------------
# Parsing the template script
# --------------------------------------------------------------------------


def extract_dirs_body(script_text: str) -> str:
    """Return the raw text inside `dirs=( ... )`, quote- and escape-aware.

    Scanning by hand rather than with a regex keeps parentheses inside quoted
    folder names (and escaped quotes) from ending the array early.
    """
    match = re.search(r"(?m)^[ \t]*dirs=\(", script_text)
    if match is None:
        raise ValueError("no `dirs=(` array found in the template script")

    chunks: list[str] = []
    quote: str | None = None
    depth = 1
    index = match.end()
    while index < len(script_text):
        char = script_text[index]
        if quote is not None:
            if char == "\\" and quote == '"' and index + 1 < len(script_text):
                chunks.append(char)
                chunks.append(script_text[index + 1])
                index += 2
                continue
            if char == quote:
                quote = None
            chunks.append(char)
        elif char in "\"'":
            quote = char
            chunks.append(char)
        elif char == "(":
            depth += 1
            chunks.append(char)
        elif char == ")":
            depth -= 1
            if depth == 0:
                return "".join(chunks)
            chunks.append(char)
        else:
            chunks.append(char)
        index += 1

    raise ValueError("unterminated `dirs=(` array in the template script")


def parse_dirs(script_text: str) -> list[str]:
    """Parse the `dirs` array into a list of folder paths, in script order.

    Uses `shlex` so single quotes, double quotes, escapes, and trailing shell
    comments are all handled the way bash would handle them.
    """
    entries = shlex.split(extract_dirs_body(script_text), comments=True)
    return [entry.strip("/") for entry in entries if entry.strip("/")]


# --------------------------------------------------------------------------
# Seeding keywords from folder names
# --------------------------------------------------------------------------


def normalize_segment(segment: str) -> str:
    """Reduce one path segment to a lowercase keyword phrase.

    Strips ordering prefixes (`02.3_`), turns separators into spaces, and drops
    punctuation: `02.3_TWC_Civil_Rights_and_Labor` -> `twc civil rights and labor`.
    """
    text = re.sub(r"^\d+(?:\.\d+)*[_\-\s.]+", "", segment)
    text = re.sub(r"[_\-:&/,+]", " ", text)
    text = re.sub(r"[^0-9a-zA-Z ]", " ", text)
    text = re.sub(r"(?<=[a-z])(?=[A-Z])", " ", text)
    return re.sub(r"\s+", " ", text).strip().lower()


def singularize(word: str) -> str:
    """Crude English singularizer, good enough for folder names."""
    if len(word) <= 3 or not word.endswith("s") or word.endswith("ss"):
        return word
    if word.endswith("ies"):
        return word[:-3] + "y"
    if re.search(r"(ch|sh|s|x|z)es$", word):
        return word[:-2]
    return word[:-1]


def seed_keywords(path: str) -> list[str]:
    """Derive keywords for a leaf from its own folder name.

    `Business/Financial Documents/Bank Statements` yields the singularized
    phrase `bank statement` plus its head noun `statement`. A folder whose name
    is purely generic (`Miscellaneous`) yields nothing, so it can never win a
    classification round.
    """
    phrase = normalize_segment(path.split("/")[-1])
    if not phrase or phrase in GENERIC_SEGMENTS:
        return []

    words = [word for word in phrase.split() if word not in STOPWORDS]
    if not words:
        return []

    keywords = [" ".join(singularize(word) for word in words)]
    if len(words) > 1:
        head = singularize(words[-1])
        if len(head) >= 4 and head not in WEAK_WORDS:
            keywords.append(head)

    seen: set[str] = set()
    unique: list[str] = []
    for keyword in keywords:
        if keyword and keyword not in seen:
            seen.add(keyword)
            unique.append(keyword)
    return unique


# --------------------------------------------------------------------------
# Curated overrides
# --------------------------------------------------------------------------

# Groups carry the coarse signal for stage 1 of classification. Every group
# absent from here is seeded from its own name.
GROUP_OVERRIDES: dict[str, dict[str, list[str]]] = {
    "Finance": {
        "keywords": [
            "invoice",
            "statement",
            "balance",
            "payment",
            "receipt",
            "account number",
            "due",
        ],
        "patterns": [r"\$[0-9,]+\.?[0-9]{0,2}\b", r"amount due"],
    },
    "LEGAL_AND_ADVOCACY": {
        "keywords": [
            "court",
            "cause no",
            "plaintiff",
            "defendant",
            "petitioner",
            "respondent",
            "summons",
            "motion",
            "exhibit",
            "affidavit",
            "petition",
            "docket",
            "counsel",
        ],
        "patterns": [
            r"cause\s+(no|number)\.?\s*[0-9a-z\-]+",
            r"motion\s+(to|for)\s+\w+",
            r"in the .{0,40}court",
        ],
    },
    "Family Shared": {
        "keywords": ["family", "household", "benefits", "identification", "certificate"],
        "patterns": [],
    },
}

# Leaves whose folder names are too ambiguous, too abbreviated, or too
# consequential to leave to name-seeding. Entries whose path is absent from the
# template are ignored (see `unmatched_override_paths`) -- they are kept so the
# tuning applies the moment the template grows those folders.
LEAF_OVERRIDES: dict[str, dict[str, list[str]]] = {
    # --- Standard litigation hierarchy -------------------------------------
    "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy": {
        "keywords": [
            "litigation",
            "pleading",
            "motion",
            "exhibit",
            "discovery",
            "affidavit",
            "notice of hearing",
        ],
        "patterns": [r"cause\s+(no|number)\.?\s*[0-9a-z\-]+", r"motion\s+(to|for)\s+\w+"],
    },
    "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy/Motions": {
        "keywords": ["motion", "movant", "relief", "notice of hearing"],
        "patterns": [r"motion\s+(to|for)\s+\w+", r"notice of (hearing|motion)"],
    },
    "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy/Evidence": {
        "keywords": ["exhibit", "evidence", "attached hereto", "declaration", "affidavit"],
        "patterns": [r"exhibit\s+[a-z0-9]{1,3}\b", r"attached\s+(hereto|as exhibit)"],
    },
    "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy/Pleadings": {
        "keywords": ["petition", "answer", "original petition", "counterclaim", "pleading"],
        "patterns": [r"(original|amended)\s+petition", r"general denial"],
    },
    "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy/Correspondence": {
        "keywords": ["letter", "counsel", "meet and confer", "sincerely", "law offices"],
        "patterns": [r"(dear|re):\s+\w+", r"meet\s+and\s+confer"],
    },
    "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy/Orders": {
        "keywords": ["order", "it is ordered", "signed this", "judge presiding"],
        "patterns": [r"it is (hereby )?ordered", r"judge presiding"],
    },
    # --- Travis County and local courts ------------------------------------
    "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/01_PROBATE_COURT": {
        "keywords": [
            "probate",
            "estate",
            "letters testamentary",
            "executor",
            "administrator",
            "guardianship",
        ],
        "patterns": [r"estate of\s+\w+", r"in re:?.*estate", r"probate court"],
    },
    "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/01.2_STATE_DISTRICT_COURT": {
        "keywords": ["district court", "judicial district", "civil action", "district clerk"],
        "patterns": [r"\d{2,3}(st|nd|rd|th)\s+judicial district", r"district court of travis"],
    },
    "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/02_JUSTICE_PEACE_2": {
        "keywords": ["justice of the peace", "precinct 2", "small claims", "eviction"],
        "patterns": [r"precinct\s+(2|two)\b", r"justice court"],
    },
    "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/02.1_JUSTICE_PEACE_1": {
        "keywords": ["justice of the peace", "precinct 1", "small claims", "eviction"],
        "patterns": [r"precinct\s+(1|one)\b", r"justice court"],
    },
    "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/02.2_JUSTICE_PEACE_5": {
        "keywords": ["justice of the peace", "precinct 5", "small claims", "eviction"],
        "patterns": [r"precinct\s+(5|five)\b", r"justice court"],
    },
    "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/03_MUNICIPAL_CRIMINAL": {
        "keywords": ["municipal court", "citation", "criminal complaint", "arraignment", "ticket"],
        "patterns": [r"municipal court", r"citation\s+(no|number)"],
    },
    # --- Texas state agencies and oversight --------------------------------
    "LEGAL_AND_ADVOCACY/02_TEXAS_STATE_AND_OVERSIGHT/02.1_Administrative_PUC_SOAH": {
        "keywords": [
            "public utility commission",
            "soah",
            "administrative hearing",
            "contested case",
        ],
        "patterns": [r"\bsoah\b", r"\bpuc\b", r"docket\s+no"],
    },
    "LEGAL_AND_ADVOCACY/02_TEXAS_STATE_AND_OVERSIGHT/02.2_Professional_Grievances": {
        "keywords": ["grievance", "state bar", "disciplinary", "complaint against", "licensure"],
        "patterns": [r"state bar of texas", r"grievance\s+(no|number)"],
    },
    "LEGAL_AND_ADVOCACY/02_TEXAS_STATE_AND_OVERSIGHT/02.3_TWC_Civil_Rights_and_Labor": {
        "keywords": [
            "texas workforce commission",
            "civil rights division",
            "discrimination",
            "wage claim",
            "retaliation",
        ],
        "patterns": [r"texas workforce commission", r"\btwc\b"],
    },
    "LEGAL_AND_ADVOCACY/02_TEXAS_STATE_AND_OVERSIGHT/02.4_HHSC_AND_BENEFITS": {
        "keywords": ["hhsc", "health and human services", "snap", "medicaid", "benefits denial"],
        "patterns": [r"\bhhsc\b", r"health and human services"],
    },
    "LEGAL_AND_ADVOCACY/02_TEXAS_STATE_AND_OVERSIGHT/02.8_Third_Court_Appeals": {
        "keywords": ["court of appeals", "appellant", "appellee", "brief", "notice of appeal"],
        "patterns": [r"third court of appeals", r"notice of appeal"],
    },
    "LEGAL_AND_ADVOCACY/02_TEXAS_STATE_AND_OVERSIGHT/02.9_Supreme_Court": {
        "keywords": ["supreme court of texas", "petition for review", "mandamus"],
        "patterns": [r"supreme court of texas", r"petition for review"],
    },
    # --- Federal -----------------------------------------------------------
    "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.1_Immigration_Identity": {
        "keywords": ["uscis", "immigration", "visa", "green card", "alien registration"],
        "patterns": [r"\buscis\b", r"form\s+i-\d{3}"],
    },
    "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.2_Civil_Rights_Title_VI": {
        "keywords": ["title vi", "civil rights", "office for civil rights", "discrimination"],
        "patterns": [r"title\s+vi\b", r"office for civil rights"],
    },
    "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.4_US_DISTRICT_COURT": {
        "keywords": [
            "united states district court",
            "western district of texas",
            "federal rules of civil procedure",
        ],
        "patterns": [r"united states district court", r"\d:\d{2}-cv-\d+"],
    },
    "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.5_US_CIRCUIT_COURT": {
        "keywords": ["court of appeals", "fifth circuit", "appellant brief", "notice of appeal"],
        "patterns": [r"(fifth|5th) circuit", r"united states court of appeals"],
    },
    "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.6_FBI": {
        "keywords": ["federal bureau of investigation", "fbi", "tip", "field office"],
        "patterns": [r"federal bureau of investigation", r"\bfbi\b"],
    },
    "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.7_EEOC": {
        "keywords": ["eeoc", "charge of discrimination", "right to sue", "equal employment"],
        "patterns": [r"\beeoc\b", r"right[- ]to[- ]sue"],
    },
    "LEGAL_AND_ADVOCACY/05_FEDERAL_SUBMISSION": {
        "keywords": ["federal submission", "filing receipt", "cm ecf", "certificate of service"],
        "patterns": [r"cm/ecf", r"certificate of service"],
    },
    # --- Medical, identity, finance ----------------------------------------
    "Family Shared/Medical Records/Immunization Records": {
        "keywords": ["immunization", "vaccination", "shot record"],
        "patterns": [r"\b(dtap|tdap|hepb|mmr|varicella)\b"],
    },
    "Family Shared/Medical Records/Family Medical Information": {
        "keywords": ["diagnosis", "patient", "clinic", "discharge summary", "prescription"],
        "patterns": [r"date of (service|visit)", r"\bicd-?10\b"],
    },
    "Family Shared/Medical Records/Insurance Information": {
        "keywords": [
            "explanation of benefits",
            "member id",
            "health plan",
            "coverage",
            "claim number",
        ],
        "patterns": [r"explanation of benefits", r"member\s+id"],
    },
    "Family Shared/Identification/Birth Certificate": {
        "keywords": ["birth certificate", "certificate of birth", "registrar"],
        "patterns": [r"certificate of (live )?birth"],
    },
    "Family Shared/Identification/Passport": {
        "keywords": ["passport", "travel document", "place of issue"],
        "patterns": [r"passport\s+(no|number)"],
    },
    "Family Shared/Identification/Drivers License": {
        "keywords": ["driver license", "drivers license", "dps", "class c"],
        "patterns": [r"driver'?s?\s+license", r"\bdl\s*(no|#)"],
    },
    "Family Shared/CPS": {
        "keywords": [
            "child protective services",
            "cps",
            "caseworker",
            "investigation finding",
        ],
        "patterns": [r"child protective services", r"\bcps\b"],
    },
    "Finance/Banking/Anna": {
        "keywords": ["bank statement", "checking account", "deposit"],
        "patterns": [r"statement period", r"beginning balance"],
    },
    "Finance/Banking/Sakura": {
        "keywords": ["bank statement", "checking account", "deposit"],
        "patterns": [r"statement period", r"beginning balance"],
    },
    "Finance/Tax Documents/W2 & 1099 Forms": {
        "keywords": ["w-2", "1099", "wage and tax statement", "employer identification"],
        "patterns": [r"\bw-?2\b", r"\b1099-?[a-z]{0,4}\b"],
    },
}


def unmatched_override_paths(paths: list[str]) -> list[str]:
    """Override paths that no template folder currently provides."""
    known = set(paths)
    return sorted(path for path in LEAF_OVERRIDES if path not in known)


# --------------------------------------------------------------------------
# Building the taxonomy
# --------------------------------------------------------------------------


def build_group(name: str) -> dict[str, object]:
    """Build one group entry: curated rules if available, else name-seeded."""
    override = GROUP_OVERRIDES.get(name)
    if override is not None:
        return {
            "path_prefix": name,
            "keywords": list(override["keywords"]),
            "patterns": list(override["patterns"]),
        }
    return {"path_prefix": name, "keywords": seed_keywords(name), "patterns": []}


def build_leaf(path: str) -> dict[str, object]:
    """Build one leaf entry, curated rules taking precedence over seeded ones."""
    override = LEAF_OVERRIDES.get(path)
    keywords = list(override["keywords"]) if override else seed_keywords(path)
    patterns = list(override["patterns"]) if override else []
    return {
        "path": path,
        "seed_path": path,
        "group": path.split("/")[0],
        "keywords": keywords,
        "patterns": patterns,
    }


def build_seed_taxonomy(paths: list[str]) -> dict[str, object]:
    """Build the full seed taxonomy from the parsed `dirs` array.

    Duplicate folder paths are a template bug, not something to paper over:
    they raise, because the coverage invariant requires exactly one leaf per
    folder.
    """
    duplicates = sorted({path for path in paths if paths.count(path) > 1})
    if duplicates:
        raise ValueError(f"duplicate folders in the template script: {duplicates}")

    group_names = sorted({path.split("/")[0] for path in paths})
    return {
        "version": SCHEMA_VERSION,
        "generated_from": DEFAULT_SCRIPT.name,
        "output_root": DEFAULT_OUTPUT_ROOT,
        "review_folder": DEFAULT_REVIEW_FOLDER,
        "group_threshold": DEFAULT_GROUP_THRESHOLD,
        "confidence_threshold": DEFAULT_CONFIDENCE_THRESHOLD,
        "groups": {name: build_group(name) for name in group_names},
        "leaves": [build_leaf(path) for path in sorted(paths)],
        "ignore": list(DEFAULT_IGNORE),
    }


# --------------------------------------------------------------------------
# Versioned merge
# --------------------------------------------------------------------------


def _merge_entry(user: dict | None, seed: dict) -> dict[str, object]:
    """Overlay a user entry on its seed entry; every field the user set wins."""
    merged = dict(seed)
    if user:
        merged.update(user)
    return merged


def _leaf_identity(leaf: dict) -> str:
    """The template folder a leaf came from, even after the user retargets it."""
    return str(leaf.get("seed_path") or leaf.get("path"))


def merge_taxonomy(existing: dict, seed: dict) -> dict[str, object]:
    """Merge a freshly generated seed into an existing taxonomy; user edits win.

    - Settings the user changed (thresholds, output root, ignore list) are kept.
    - Groups and leaves present in both keep every field the user's file sets and
      inherit any field it omits, so new seed fields still arrive.
    - Leaves are matched on `seed_path`, so a leaf retargeted to a custom `path`
      is updated in place instead of being duplicated.
    - Groups and leaves the user added by hand survive untouched.
    """
    merged: dict[str, object] = dict(seed)

    for key, value in existing.items():
        if key in SETTING_KEYS or key not in merged:
            merged[key] = value

    seed_groups: dict = seed.get("groups", {})
    user_groups: dict = existing.get("groups", {})
    groups = {
        name: _merge_entry(user_groups.get(name), entry) for name, entry in seed_groups.items()
    }
    for name, entry in user_groups.items():
        groups.setdefault(name, entry)
    merged["groups"] = groups

    user_leaves = {_leaf_identity(leaf): leaf for leaf in existing.get("leaves", [])}
    leaves = []
    for leaf in seed.get("leaves", []):
        identity = _leaf_identity(leaf)
        leaves.append(_merge_entry(user_leaves.pop(identity, None), leaf))
    leaves.extend(user_leaves.values())
    merged["leaves"] = leaves

    merged["version"] = seed["version"]
    merged["generated_from"] = seed["generated_from"]
    return merged


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def generate(script_path: Path, output_path: Path, merge: bool = True) -> dict[str, object]:
    """Build the taxonomy for `script_path`, merging into `output_path` if present."""
    seed = build_seed_taxonomy(parse_dirs(script_path.read_text(encoding="utf-8")))
    if merge and output_path.exists():
        existing = json.loads(output_path.read_text(encoding="utf-8"))
        return merge_taxonomy(existing, seed)
    return seed


def serialize(taxonomy: dict) -> str:
    """Render the taxonomy as the exact bytes written to disk."""
    return json.dumps(taxonomy, indent=2, ensure_ascii=False) + "\n"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--script", type=Path, default=DEFAULT_SCRIPT)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument(
        "--no-merge",
        dest="merge",
        action="store_false",
        default=True,
        help="Overwrite the output instead of merging user edits into the new seed.",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Exit non-zero if the output file differs from what would be generated.",
    )
    args = parser.parse_args(argv)

    taxonomy = generate(args.script, args.output, merge=args.merge)
    rendered = serialize(taxonomy)

    orphans = unmatched_override_paths([str(leaf["path"]) for leaf in taxonomy["leaves"]])
    if orphans:
        print(
            f"note: {len(orphans)} curated override(s) match no current folder "
            f"and were skipped: {', '.join(orphans)}",
            file=sys.stderr,
        )

    if args.check:
        current = args.output.read_text(encoding="utf-8") if args.output.exists() else ""
        if current != rendered:
            print(f"{args.output} is out of date; run scripts/build_taxonomy.py", file=sys.stderr)
            return 1
        print(f"{args.output} is up to date ({len(taxonomy['leaves'])} leaves).")
        return 0

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(rendered, encoding="utf-8")
    print(
        f"wrote {args.output} — {len(taxonomy['leaves'])} leaves "
        f"across {len(taxonomy['groups'])} groups."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
