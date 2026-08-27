"""Command-line entry point for `organized-docs`.

Wires the pipeline together: scan the source directory, extract text,
classify against the taxonomy, name the destination, then either print
the plan (the default dry run) or execute it under `--apply`. Every
run writes `ORGANIZING-LOG.md` to the destination root.

Nothing here opens a socket: extraction, classification, naming, and
filing are all local-disk operations, which is the product's hard
privacy invariant and an acceptance-tested one.
"""

from __future__ import annotations

import argparse
import fnmatch
from dataclasses import dataclass, replace
from pathlib import Path

from . import fileops, naming, report
from .classify import classify
from .extract import ExtractedText, SkippedFile, extract_text
from .taxonomy import Taxonomy, TaxonomyError, load_taxonomy

DEFAULT_OUTPUT = Path("~/Documents/Organized Documents")
DEFAULT_REPORT = Path("ORGANIZING-LOG.md")
DEFAULT_THRESHOLD = 0.6

REVIEW_NO_TEXT_LAYER = "no text layer (likely scanned) — needs OCR"

#: Exit codes. A per-file error is not a failed run (the rest still files),
#: but it must not look like a clean one either.
EXIT_OK = 0
EXIT_WITH_ERRORS = 1
EXIT_REFUSED = 2


@dataclass(frozen=True)
class RunOptions:
    """A fully resolved run: every path expanded, every default settled."""

    source: Path
    destination_root: Path
    report_path: Path
    apply: bool
    copy: bool
    flat: bool
    threshold: float | None
    taxonomy: Taxonomy


def build_parser() -> argparse.ArgumentParser:
    """Build the `organized-docs` argument parser.

    Kept separate from `main` so tests can inspect or drive the parser
    directly without spawning a subprocess.
    """
    parser = argparse.ArgumentParser(
        prog="organized-docs",
        description=(
            "Scan an unsorted directory of legal PDFs and correspondence, "
            "classify each document against the organized-documents-template "
            "folder hierarchy, and file it into the matching deep folder. "
            "Runs entirely on local disk — no network I/O."
        ),
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Directory of unsorted documents to scan and classify.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Execute the plan (move or copy files). Without this flag the run "
            "is a dry-run: the classification and naming plan is printed and "
            "logged, and zero files are touched."
        ),
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        default=False,
        help="Copy files into the destination instead of moving them; originals stay in place.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help=(
            "Destination root; leaf paths resolve under it "
            f"(default: the taxonomy's output_root, {DEFAULT_OUTPUT})."
        ),
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a taxonomy.json overriding the built-in group/leaf mapping and keywords.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "Minimum leaf confidence (0-1) required to auto-file a document "
            f"(default: the taxonomy's confidence_threshold, {DEFAULT_THRESHOLD}). "
            "Below it, the file goes to _Needs Review/."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=(
            "Markdown report path, written to the destination root on every "
            f"run, including dry-runs (default: {DEFAULT_REPORT})."
        ),
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        default=False,
        help="Scan the source directory non-recursively (default is recursive).",
    )
    return parser


# --------------------------------------------------------------------------
# Scanning
# --------------------------------------------------------------------------


def is_ignored(name: str, patterns: tuple[str, ...]) -> bool:
    """True when a filename matches an ignore rule from the taxonomy.

    Rules are matched both as globs (`*.tmp`) and as plain substrings
    (`~$`, `.DS_Store`), because the shipped ignore list is written the
    way a person would write it, not as strict glob syntax.
    """
    return any(fnmatch.fnmatch(name, pattern) or pattern in name for pattern in patterns)


def scan_files(source: Path, patterns: tuple[str, ...], *, flat: bool) -> list[Path]:
    """Every candidate file under `source`, sorted for a deterministic plan."""
    entries = source.iterdir() if flat else source.rglob("*")
    return sorted(
        path
        for path in entries
        if path.is_file() and not is_ignored(path.name, patterns) and not path.name.startswith(".")
    )


# --------------------------------------------------------------------------
# Planning
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class PlannedDocument:
    """One document's outcome plus the move it implies (None when dry data)."""

    outcome: report.DocumentOutcome
    move: tuple[Path, Path] | None


def _review(
    source: Path,
    reason: str,
    options: RunOptions,
    reserved: set[Path],
    text: str = "",
) -> PlannedDocument:
    """Route a document to `_Needs Review/` under its original filename."""
    review_dir = options.destination_root / options.taxonomy.review_folder
    destination = naming.unique_path(review_dir, source.name, reserved)
    reserved.add(destination)
    return PlannedDocument(
        outcome=report.DocumentOutcome(
            source=source,
            status=report.STATUS_REVIEW,
            destination=destination,
            reason=reason,
            snippet=report.snippet_of(text),
        ),
        move=(source, destination),
    )


def plan_document(source: Path, options: RunOptions, reserved: set[Path]) -> PlannedDocument:
    """Decide where one document goes, without touching it.

    Extraction failures are caught per file rather than aborting the scan: a
    single unreadable PDF in a folder of hundreds should be reported and
    skipped, never silently dropped and never fatal. The reason is recorded
    verbatim in the log's Errors section.
    """
    try:
        extraction = extract_text(source)
    except Exception as exc:  # noqa: BLE001 - reported per file, never swallowed
        return PlannedDocument(
            outcome=report.DocumentOutcome(
                source=source,
                status=report.STATUS_ERROR,
                reason=f"{type(exc).__name__}: {exc}",
            ),
            move=None,
        )

    if isinstance(extraction, SkippedFile):
        return _review(source, extraction.reason, options, reserved)
    if extraction.needs_ocr:
        return _review(source, REVIEW_NO_TEXT_LAYER, options, reserved)

    return _plan_classified(source, extraction, options, reserved)


def _plan_classified(
    source: Path,
    extraction: ExtractedText,
    options: RunOptions,
    reserved: set[Path],
) -> PlannedDocument:
    """Classify extracted text and build the destination it earns."""
    result = classify(extraction.text, options.taxonomy, leaf_threshold=options.threshold)
    if result.needs_review or not result.leaf_path:
        best_guess = f" (best guess: {result.leaf_path})" if result.leaf_path else ""
        reason = f"{result.reason}{best_guess}"
        return _review(source, reason, options, reserved, extraction.text)

    parts = naming.build_name_parts(extraction.text, extraction.metadata, source, result.leaf_path)
    destination = naming.unique_path(
        options.destination_root / result.leaf_path, parts.filename, reserved
    )
    reserved.add(destination)
    return PlannedDocument(
        outcome=report.DocumentOutcome(
            source=source,
            status=report.STATUS_FILED,
            destination=destination,
            confidence=result.leaf_confidence,
            date_source=parts.date.source,
        ),
        move=(source, destination),
    )


def plan_run(options: RunOptions) -> list[PlannedDocument]:
    """Plan every scanned document, reserving destinations as it goes."""
    reserved: set[Path] = set()
    return [
        plan_document(source, options, reserved)
        for source in scan_files(options.source, options.taxonomy.ignore, flat=options.flat)
    ]


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def _apply_results(
    planned: list[PlannedDocument], results: list[fileops.FileOperationResult]
) -> list[report.DocumentOutcome]:
    """Fold execution failures back into the outcomes the report renders."""
    errors = {result.source: result.error for result in results if result.failed}
    outcomes: list[report.DocumentOutcome] = []
    for item in planned:
        error = errors.get(item.outcome.source)
        if error is None:
            outcomes.append(item.outcome)
            continue
        outcomes.append(
            replace(item.outcome, status=report.STATUS_ERROR, reason=error, destination=None)
        )
    return outcomes


def run(options: RunOptions) -> report.RunSummary:
    """Plan, execute (or not), and summarize one run.

    Refuses before any planning when the destination root sits inside the
    source directory, so a self-ingesting run does no partial work.
    """
    fileops.ensure_destination_outside_source(options.source, options.destination_root)

    planned = plan_run(options)
    moves = [item.move for item in planned if item.move is not None]
    results = fileops.execute_plan(moves, apply=options.apply, copy=options.copy)

    return report.RunSummary(
        source_root=options.source,
        destination_root=options.destination_root,
        applied=options.apply,
        copied=options.copy,
        outcomes=_apply_results(planned, results),
    )


def resolve_options(args: argparse.Namespace) -> RunOptions:
    """Turn parsed arguments into a fully resolved run, taxonomy included."""
    taxonomy = load_taxonomy(args.config)
    output = args.output if args.output is not None else Path(taxonomy.output_root)
    destination_root = output.expanduser()
    report_argument = Path(args.report).expanduser()
    report_path = (
        report_argument if report_argument.is_absolute() else destination_root / report_argument
    )
    return RunOptions(
        source=args.source.expanduser(),
        destination_root=destination_root,
        report_path=report_path,
        apply=args.apply,
        copy=args.copy,
        flat=args.flat,
        threshold=args.threshold,
        taxonomy=taxonomy,
    )


def _print_summary(summary: report.RunSummary, report_path: Path) -> None:
    """Print the plan (or what was done) so the terminal is useful on its own."""
    verb = "copy" if summary.copied else "move"
    print(f"organized-docs: {summary.mode}")
    for outcome in summary.outcomes:
        if outcome.status == report.STATUS_ERROR:
            print(f"  ERROR   {outcome.source.name}: {outcome.reason}")
        elif outcome.destination is not None:
            marker = "FILE" if outcome.is_filed else "REVIEW"
            print(f"  {marker:<7} {outcome.source.name} -> {outcome.destination}")
    filed = len(summary.with_status(report.STATUS_FILED))
    review = len(summary.with_status(report.STATUS_REVIEW))
    errors = len(summary.with_status(report.STATUS_ERROR))
    planned_note = "" if summary.applied else f" (planned {verb}s, nothing touched)"
    print(
        f"Processed {len(summary.outcomes)} · filed {filed} · review {review} · "
        f"errors {errors}{planned_note}"
    )
    print(f"Log: {report_path}")


def main(argv: list[str] | None = None) -> int:
    """Parse arguments, run the pipeline, and write the log.

    Returns 0 on a clean run, 1 when any file errored (the rest still
    filed), and 2 when the run was refused before doing any work.
    """
    parser = build_parser()
    args = parser.parse_args(argv)

    try:
        options = resolve_options(args)
    except TaxonomyError as exc:
        parser.exit(EXIT_REFUSED, f"organized-docs: {exc}\n")

    if options.threshold is not None and not 0.0 <= options.threshold <= 1.0:
        parser.exit(
            EXIT_REFUSED,
            f"organized-docs: --threshold must be between 0 and 1, got {options.threshold}\n",
        )

    if not options.source.is_dir():
        parser.exit(EXIT_REFUSED, f"organized-docs: source {options.source} is not a directory\n")

    try:
        summary = run(options)
    except fileops.DestinationInsideSourceError as exc:
        parser.exit(EXIT_REFUSED, f"organized-docs: {exc}\n")

    report.write_report(options.report_path, summary)
    _print_summary(summary, options.report_path)
    return EXIT_WITH_ERRORS if summary.with_status(report.STATUS_ERROR) else EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
