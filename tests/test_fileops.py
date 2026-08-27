"""File operations: move/copy with verification, abort on failure, safety refusals."""

from __future__ import annotations

from pathlib import Path

import pytest

from organized_docs.fileops import (
    ABORTED_REASON,
    DestinationInsideSourceError,
    ensure_destination_outside_source,
    execute_plan,
)


def _source(tmp_path: Path, name: str, content: str = "content") -> Path:
    path = tmp_path / "in" / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# Destination safety
# --------------------------------------------------------------------------


def test_destination_inside_source_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "Unsorted"
    source.mkdir()

    with pytest.raises(DestinationInsideSourceError):
        ensure_destination_outside_source(source, source / "Organized")


def test_destination_equal_to_source_is_refused(tmp_path: Path) -> None:
    source = tmp_path / "Unsorted"
    source.mkdir()

    with pytest.raises(DestinationInsideSourceError):
        ensure_destination_outside_source(source, source)


def test_sibling_destination_is_allowed(tmp_path: Path) -> None:
    source = tmp_path / "Unsorted"
    source.mkdir()

    ensure_destination_outside_source(source, tmp_path / "Organized")


def test_source_inside_destination_is_allowed(tmp_path: Path) -> None:
    # The reverse nesting is safe: the scanner never walks the destination.
    source = tmp_path / "Organized" / "Unsorted"
    source.mkdir(parents=True)

    ensure_destination_outside_source(source, tmp_path / "Organized")


# --------------------------------------------------------------------------
# Execution
# --------------------------------------------------------------------------


def test_dry_run_touches_nothing(tmp_path: Path) -> None:
    source = _source(tmp_path, "letter.pdf")
    destination = tmp_path / "out" / "Personal" / "letter.pdf"

    results = execute_plan([(source, destination)], apply=False, copy=False)

    assert source.exists()
    assert not destination.exists()
    assert not destination.parent.exists()
    assert [(r.applied, r.error) for r in results] == [(False, None)]


def test_apply_creates_folders_and_moves(tmp_path: Path) -> None:
    source = _source(tmp_path, "letter.pdf", "body")
    destination = tmp_path / "out" / "Personal" / "Correspondence" / "letter.pdf"

    results = execute_plan([(source, destination)], apply=True, copy=False)

    assert results[0].applied
    assert results[0].error is None
    assert destination.read_text(encoding="utf-8") == "body"
    assert not source.exists()


def test_copy_leaves_the_original_in_place(tmp_path: Path) -> None:
    source = _source(tmp_path, "letter.pdf", "body")
    destination = tmp_path / "out" / "Personal" / "letter.pdf"

    results = execute_plan([(source, destination)], apply=True, copy=True)

    assert results[0].applied
    assert source.read_text(encoding="utf-8") == "body"
    assert destination.read_text(encoding="utf-8") == "body"


def test_an_existing_destination_is_never_overwritten(tmp_path: Path) -> None:
    source = _source(tmp_path, "letter.pdf", "new")
    destination = tmp_path / "out" / "letter.pdf"
    destination.parent.mkdir(parents=True)
    destination.write_text("existing", encoding="utf-8")

    results = execute_plan([(source, destination)], apply=True, copy=False)

    assert results[0].failed
    assert "refusing to overwrite" in (results[0].error or "")
    assert destination.read_text(encoding="utf-8") == "existing"
    assert source.read_text(encoding="utf-8") == "new"


def test_a_failure_aborts_every_remaining_move(tmp_path: Path) -> None:
    first = _source(tmp_path, "one.pdf")
    missing = tmp_path / "in" / "vanished.pdf"
    third = _source(tmp_path, "three.pdf")
    out = tmp_path / "out"

    results = execute_plan(
        [
            (first, out / "one.pdf"),
            (missing, out / "vanished.pdf"),
            (third, out / "three.pdf"),
        ],
        apply=True,
        copy=False,
    )

    assert results[0].applied
    assert results[1].failed and "FileNotFoundError" in (results[1].error or "")
    assert results[2].error == ABORTED_REASON
    # The run stopped where it said it stopped: file three was never touched.
    assert third.exists()
    assert not (out / "three.pdf").exists()
