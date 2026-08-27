"""The Markdown log: counts, tables, review snippets, errors, and the run mode."""

from __future__ import annotations

from pathlib import Path

from organized_docs.report import (
    APPLY_MARKER,
    DRY_RUN_MARKER,
    STATUS_ERROR,
    STATUS_FILED,
    STATUS_REVIEW,
    DocumentOutcome,
    RunSummary,
    render_report,
    snippet_of,
    write_report,
)

DESTINATION = Path("/out")


def _summary(*outcomes: DocumentOutcome, applied: bool = False, copied: bool = False) -> RunSummary:
    return RunSummary(
        source_root=Path("/in"),
        destination_root=DESTINATION,
        applied=applied,
        copied=copied,
        outcomes=list(outcomes),
    )


FILED = DocumentOutcome(
    source=Path("/in/scan_0143.pdf"),
    status=STATUS_FILED,
    destination=DESTINATION / "LEGAL_AND_ADVOCACY/01_PROBATE_COURT/2024-03-05_Clerk_Citation.pdf",
    confidence=0.87,
    date_source="text",
)
REVIEWED = DocumentOutcome(
    source=Path("/in/IMG_2213.pdf"),
    status=STATUS_REVIEW,
    destination=DESTINATION / "_Needs Review/IMG_2213.pdf",
    reason="no text layer (likely scanned)",
    snippet="To whom it may concern",
)
ERRORED = DocumentOutcome(
    source=Path("/in/weird_file.pdf"),
    status=STATUS_ERROR,
    reason="PermissionError: could not read",
)


def test_dry_run_is_marked_and_apply_is_not() -> None:
    assert DRY_RUN_MARKER in render_report(_summary(FILED))
    assert APPLY_MARKER in render_report(_summary(FILED, applied=True))
    assert DRY_RUN_MARKER not in render_report(_summary(FILED, applied=True))


def test_counts_cover_every_status() -> None:
    rendered = render_report(_summary(FILED, REVIEWED, ERRORED))

    assert "**Processed:** 3" in rendered
    assert "**Filed:** 1" in rendered
    assert "**Needs review:** 1" in rendered
    assert "**Errors:** 1" in rendered


def test_filed_rows_carry_destination_confidence_and_date_source() -> None:
    rendered = render_report(_summary(FILED))

    assert "scan_0143.pdf" in rendered
    assert "LEGAL_AND_ADVOCACY/01_PROBATE_COURT/2024-03-05_Clerk_Citation.pdf" in rendered
    assert "0.87" in rendered
    assert "| text |" in rendered


def test_review_rows_carry_the_reason_and_a_snippet() -> None:
    rendered = render_report(_summary(REVIEWED))

    assert "IMG_2213.pdf" in rendered
    assert "no text layer (likely scanned)" in rendered
    assert "To whom it may concern" in rendered


def test_errors_are_listed_with_their_reason() -> None:
    rendered = render_report(_summary(ERRORED))

    assert "weird_file.pdf — PermissionError: could not read" in rendered


def test_empty_sections_say_so_rather_than_rendering_a_bare_header() -> None:
    rendered = render_report(_summary(FILED))

    assert "_No documents needed review._" in rendered
    assert "_None._" in rendered


def test_operation_reflects_copy_mode() -> None:
    assert "**Operation:** copy" in render_report(_summary(FILED, copied=True))
    assert "**Operation:** move" in render_report(_summary(FILED))


def test_a_pipe_in_a_filename_cannot_break_the_table() -> None:
    piped = DocumentOutcome(
        source=Path("/in/weird|name.pdf"),
        status=STATUS_REVIEW,
        reason="low confidence",
    )

    assert r"weird\|name.pdf" in render_report(_summary(piped))


def test_snippet_flattens_and_truncates() -> None:
    assert snippet_of("first line\n\n  second line ") == "first line second line"
    assert snippet_of("") == "—"
    assert snippet_of("x" * 300, length=10).endswith("…")


def test_write_report_creates_missing_parents(tmp_path: Path) -> None:
    report_path = tmp_path / "out" / "ORGANIZING-LOG.md"

    written = write_report(report_path, _summary(FILED))

    assert written == report_path
    assert report_path.read_text(encoding="utf-8").startswith("# Organizing Log")
