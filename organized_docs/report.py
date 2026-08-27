"""Markdown audit log for a run.

Spec contract: write `ORGANIZING-LOG.md` to the destination root on
every run, including dry-runs (marked `DRY RUN — no files moved`).
Contents: processed/filed/review/error counts, a per-file table
(source, destination, confidence, date used), a review queue with
extracted snippets and reasons, and any errors encountered.

The log is the deliverable a user actually reads, so the review queue
carries the first lines of each unfiled document: triage should never
require reopening a PDF.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

#: What happened to one scanned document.
STATUS_FILED = "filed"
STATUS_REVIEW = "review"
STATUS_ERROR = "error"

DRY_RUN_MARKER = "DRY RUN — no files moved"
APPLY_MARKER = "APPLIED"

#: How much document text the review queue shows per file.
SNIPPET_LENGTH = 120


@dataclass(frozen=True)
class DocumentOutcome:
    """One scanned document's fate, as the report needs to render it."""

    source: Path
    status: str
    destination: Path | None = None
    confidence: float = 0.0
    date_source: str = ""
    reason: str = ""
    snippet: str = ""

    @property
    def is_filed(self) -> bool:
        return self.status == STATUS_FILED


@dataclass(frozen=True)
class RunSummary:
    """Everything one run needs to report about itself."""

    source_root: Path
    destination_root: Path
    applied: bool
    copied: bool = False
    outcomes: list[DocumentOutcome] = field(default_factory=list)
    started_at: datetime = field(default_factory=datetime.now)

    def with_status(self, status: str) -> list[DocumentOutcome]:
        return [outcome for outcome in self.outcomes if outcome.status == status]

    @property
    def mode(self) -> str:
        return APPLY_MARKER if self.applied else DRY_RUN_MARKER


def snippet_of(text: str, length: int = SNIPPET_LENGTH) -> str:
    """The first lines of a document, flattened to one line for a table cell."""
    flattened = " ".join(text.split())
    if not flattened:
        return "—"
    if len(flattened) <= length:
        return flattened
    return flattened[:length].rstrip() + "…"


def _cell(value: object) -> str:
    """Render one table cell: pipes escaped so a filename can't break the row."""
    text = str(value).replace("|", r"\|").replace("\n", " ")
    return text or "—"


def _relative(path: Path, root: Path) -> str:
    """A destination shown relative to the destination root when possible."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)


def _filed_section(summary: RunSummary) -> list[str]:
    filed = summary.with_status(STATUS_FILED)
    if not filed:
        return ["## Filed", "", "_Nothing was filed in this run._", ""]
    lines = [
        "## Filed",
        "",
        "| Original | Destination | Confidence | Date used |",
        "|---|---|---|---|",
    ]
    for outcome in filed:
        destination = (
            _relative(outcome.destination, summary.destination_root) if outcome.destination else "—"
        )
        lines.append(
            f"| {_cell(outcome.source.name)} | {_cell(destination)} "
            f"| {outcome.confidence:.2f} | {_cell(outcome.date_source)} |"
        )
    lines.append("")
    return lines


def _review_section(summary: RunSummary) -> list[str]:
    review = summary.with_status(STATUS_REVIEW)
    if not review:
        return ["## Needs review", "", "_No documents needed review._", ""]
    lines = ["## Needs review", "", "| Original | Reason | First lines |", "|---|---|---|"]
    for outcome in review:
        lines.append(
            f"| {_cell(outcome.source.name)} | {_cell(outcome.reason)} "
            f"| {_cell(outcome.snippet or '—')} |"
        )
    lines.append("")
    return lines


def _errors_section(summary: RunSummary) -> list[str]:
    errors = summary.with_status(STATUS_ERROR)
    if not errors:
        return ["## Errors", "", "_None._", ""]
    lines = ["## Errors", ""]
    for outcome in errors:
        lines.append(f"- {outcome.source.name} — {outcome.reason} (skipped, left in place)")
    lines.append("")
    return lines


def render_report(summary: RunSummary) -> str:
    """Render the full Markdown log for a run."""
    filed = len(summary.with_status(STATUS_FILED))
    review = len(summary.with_status(STATUS_REVIEW))
    errors = len(summary.with_status(STATUS_ERROR))
    operation = "copy" if summary.copied else "move"

    lines = [
        f"# Organizing Log — {summary.started_at:%Y-%m-%d %H:%M}",
        "",
        f"**Mode:** {summary.mode}",
        f"**Operation:** {operation}",
        f"**Source:** {summary.source_root}",
        f"**Destination:** {summary.destination_root}",
        "",
        f"**Processed:** {len(summary.outcomes)} · **Filed:** {filed} · "
        f"**Needs review:** {review} · **Errors:** {errors}",
        "",
    ]
    lines += _filed_section(summary)
    lines += _review_section(summary)
    lines += _errors_section(summary)
    return "\n".join(lines).rstrip() + "\n"


def write_report(report_path: Path, summary: RunSummary) -> Path:
    """Write the Markdown execution log for a run and return where it landed.

    Called on every run, dry or applied — a dry-run's log is the plan the
    user reviews before committing to `--apply`.
    """
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(render_report(summary), encoding="utf-8")
    return report_path
