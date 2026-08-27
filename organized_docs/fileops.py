"""Execution of the classification plan: move or copy, verify, abort on failure.

Spec contract: every move is `shutil.move` to a path `unique_path`
verified as unused, re-checked immediately before the move so a
concurrent writer can never be overwritten. A failed move aborts all
remaining moves and the caller records where execution stopped.
`--copy` leaves originals in place. The tool refuses to run when the
destination root is inside the source directory.

A failure aborts rather than rolls back. Undoing already-completed
moves would mean writing back into a tree the run has already changed —
more file operations after something has gone wrong, on a corpus of
irreplaceable legal documents. Stopping and naming the stop point in
the report leaves the user with a partially-organized tree they can
inspect, which is recoverable; a botched rollback is not.
"""

from __future__ import annotations

import shutil
from dataclasses import dataclass
from pathlib import Path

#: Recorded against every planned move after execution stops early.
ABORTED_REASON = "aborted: an earlier operation failed"


class DestinationInsideSourceError(ValueError):
    """Raised when the destination root sits inside the source directory.

    Filing documents into a folder the scanner also walks means the run
    re-ingests its own output — self-ingestion is data loss, not an edge
    case, so the run refuses to start rather than doing partial work.
    """


@dataclass(frozen=True)
class FileOperationResult:
    """Outcome of executing (or dry-running) one planned file move."""

    source: Path
    destination: Path
    applied: bool
    error: str | None

    @property
    def failed(self) -> bool:
        return self.error is not None


def ensure_destination_outside_source(source_root: Path, destination_root: Path) -> None:
    """Refuse a destination root that is the source directory or inside it.

    Resolves both paths first so `~`, symlinks, and `..` cannot smuggle a
    nested destination past the check.
    """
    source = Path(source_root).expanduser().resolve()
    destination = Path(destination_root).expanduser().resolve()
    if destination == source or _is_within(destination, source):
        raise DestinationInsideSourceError(
            f"destination root {destination} is inside the source directory {source}; "
            "the run would re-ingest its own output. Choose a destination outside the "
            "source with --output."
        )


def _is_within(candidate: Path, parent: Path) -> bool:
    """True when `candidate` sits under `parent` (both already resolved)."""
    return parent in candidate.parents


def _verify(source: Path, destination: Path, *, copy: bool) -> str | None:
    """Confirm the operation landed; return a reason when it did not."""
    if not destination.exists():
        return f"destination {destination} does not exist after the operation"
    if copy and not source.exists():
        return f"source {source} disappeared during a copy"
    if not copy and source.exists():
        return f"source {source} still exists after a move"
    return None


def _transfer(source: Path, destination: Path, *, copy: bool) -> str | None:
    """Create the parent folder and move or copy one file, then verify it.

    Re-checks the destination immediately before writing: `unique_path`
    ran during planning, and a concurrent writer could have claimed the
    name in between.
    """
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        return f"destination {destination} appeared after planning; refusing to overwrite"
    try:
        if copy:
            shutil.copy2(str(source), str(destination))
        else:
            shutil.move(str(source), str(destination))
    except (OSError, shutil.Error) as exc:
        return f"{type(exc).__name__}: {exc}"
    return _verify(source, destination, copy=copy)


def execute_plan(
    planned_moves: list[tuple[Path, Path]], *, apply: bool, copy: bool
) -> list[FileOperationResult]:
    """Perform (or dry-run, when `apply` is False) the planned moves/copies.

    Without `apply` nothing touches the disk: every planned move comes back
    `applied=False` with no error, which is exactly what the report prints
    as the dry-run plan. With `apply`, the first failure stops execution and
    every remaining entry is recorded as aborted, so the report names where
    the run stopped and which files were never attempted.
    """
    results: list[FileOperationResult] = []
    aborted = False

    for source, destination in planned_moves:
        if not apply:
            results.append(
                FileOperationResult(
                    source=source, destination=destination, applied=False, error=None
                )
            )
            continue
        if aborted:
            results.append(
                FileOperationResult(
                    source=source,
                    destination=destination,
                    applied=False,
                    error=ABORTED_REASON,
                )
            )
            continue

        error = _transfer(source, destination, copy=copy)
        if error is not None:
            aborted = True
        results.append(
            FileOperationResult(
                source=source,
                destination=destination,
                applied=error is None,
                error=error,
            )
        )

    return results
