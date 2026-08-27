"""Execution of the classification plan: move or copy, verify, roll back.

Spec contract: every move is `shutil.move` to a path `unique_path`
verified as unused, re-checked immediately before the move so a
concurrent writer can never be overwritten. A failed move aborts all
remaining moves and the caller records where execution stopped.
`--copy` leaves originals in place. The tool refuses to run when the
destination root is inside the source directory.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class FileOperationResult:
    """Outcome of executing (or dry-running) one planned file move."""

    source: Path
    destination: Path
    applied: bool
    error: str | None


def execute_plan(
    planned_moves: list[tuple[Path, Path]], *, apply: bool, copy: bool
) -> list[FileOperationResult]:
    """Perform (or dry-run, when `apply` is False) the planned moves/copies.

    Raises `NotImplementedError` until execution ships in a later PR.
    """
    raise NotImplementedError(
        "execute_plan is implemented in a later PR; see the spec's fileops.py contract"
    )
