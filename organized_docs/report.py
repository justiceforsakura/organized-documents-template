"""Markdown audit log for a run.

Spec contract: write `ORGANIZING-LOG.md` to the destination root on
every run, including dry-runs (marked `DRY RUN — no files moved`).
Contents: processed/filed/review/error counts, a per-file table
(source, destination, confidence, date used), a review queue with
extracted snippets and reasons, and any errors encountered.
"""

from __future__ import annotations

from pathlib import Path


def write_report(report_path: Path, run_summary: dict[str, object]) -> None:
    """Write the Markdown execution log for a run to `report_path`.

    Raises `NotImplementedError` until reporting ships in a later PR.
    """
    raise NotImplementedError(
        "write_report is implemented in a later PR; see the spec's report.py contract"
    )
