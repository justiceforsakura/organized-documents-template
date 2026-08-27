"""End-to-end CLI runs: dry-run safety, applying, collisions, flags, and the log.

Every test takes `block_network`, so the whole pipeline — extraction,
classification, naming, filing, reporting — is exercised with sockets
disabled. A single outbound connection anywhere fails the suite.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest
from pdf_fixtures import write_blank_pdf

from organized_docs.cli import EXIT_OK, EXIT_REFUSED, main
from organized_docs.report import APPLY_MARKER, DRY_RUN_MARKER

MOTION = """MOTION TO COMPEL DISCOVERY
Cause No. D-1-GN-24-001234
Filed: March 5, 2024
Notice of Hearing before the Court
"""

GIBBERISH = "qqq wtf zzz plfff\nmmmm nnnn oooo\n"

BANK_STATEMENT = """From: Chase Bank
Statement period 2024-06-01 to 2024-06-30
checking account deposit summary
"""

LOG_NAME = "ORGANIZING-LOG.md"


@pytest.fixture
def source(tmp_path: Path) -> Path:
    """An unsorted folder holding one clear filing and one unclassifiable note."""
    folder = tmp_path / "Unsorted Raw Docs"
    folder.mkdir()
    (folder / "scan_0143.txt").write_text(MOTION, encoding="utf-8")
    (folder / "note.txt").write_text(GIBBERISH, encoding="utf-8")
    return folder


@pytest.fixture
def destination(tmp_path: Path) -> Path:
    return tmp_path / "Organized Documents"


def _tree_digest(root: Path) -> dict[str, str]:
    """Path -> content hash for every file under `root`."""
    return {
        str(path.relative_to(root)): hashlib.sha256(path.read_bytes()).hexdigest()
        for path in sorted(root.rglob("*"))
        if path.is_file()
    }


def _files_under(root: Path) -> list[Path]:
    return sorted(path for path in root.rglob("*") if path.is_file())


def _log(destination: Path, name: str = LOG_NAME) -> str:
    return (destination / name).read_text(encoding="utf-8")


# --------------------------------------------------------------------------
# Dry run is the default and touches nothing
# --------------------------------------------------------------------------


def test_dry_run_leaves_the_source_tree_byte_identical(
    block_network: None, source: Path, destination: Path
) -> None:
    before = _tree_digest(source)

    assert main([str(source), "--output", str(destination)]) == EXIT_OK

    assert _tree_digest(source) == before


def test_dry_run_adds_only_the_log_to_the_destination(
    block_network: None, source: Path, destination: Path
) -> None:
    main([str(source), "--output", str(destination)])

    assert _files_under(destination) == [destination / LOG_NAME]
    assert DRY_RUN_MARKER in _log(destination)


def test_dry_run_log_still_lists_the_plan(
    block_network: None, source: Path, destination: Path
) -> None:
    main([str(source), "--output", str(destination)])
    log = _log(destination)

    assert "scan_0143.txt" in log
    assert "LEGAL_AND_ADVOCACY" in log
    assert "note.txt" in log


# --------------------------------------------------------------------------
# --apply
# --------------------------------------------------------------------------


def test_apply_files_the_motion_and_queues_the_note_for_review(
    block_network: None, source: Path, destination: Path
) -> None:
    assert main([str(source), "--output", str(destination), "--apply"]) == EXIT_OK

    filed = [path for path in _files_under(destination) if "LEGAL_AND_ADVOCACY" in str(path)]
    assert len(filed) == 1
    assert filed[0].read_text(encoding="utf-8") == MOTION
    assert filed[0].name.startswith("2024-03-05_")

    assert (destination / "_Needs Review" / "note.txt").read_text(encoding="utf-8") == GIBBERISH
    assert _files_under(source) == []
    assert APPLY_MARKER in _log(destination)


def test_copy_leaves_the_originals_in_the_source(
    block_network: None, source: Path, destination: Path
) -> None:
    before = _tree_digest(source)

    main([str(source), "--output", str(destination), "--apply", "--copy"])

    assert _tree_digest(source) == before
    assert any("LEGAL_AND_ADVOCACY" in str(path) for path in _files_under(destination))
    assert "**Operation:** copy" in _log(destination)


def test_a_colliding_destination_gets_a_suffix_and_both_files_survive(
    block_network: None, source: Path, destination: Path
) -> None:
    main([str(source), "--output", str(destination), "--apply"])
    # The same document arrives again in a later batch: same text, same
    # computed name, and the first copy must not be overwritten.
    (source / "scan_0143.txt").write_text(MOTION + "Second copy.\n", encoding="utf-8")

    main([str(source), "--output", str(destination), "--apply"])

    filed = [path for path in _files_under(destination) if "LEGAL_AND_ADVOCACY" in str(path)]
    assert len(filed) == 2
    assert any(path.stem.endswith("-1") for path in filed)
    assert {path.read_text(encoding="utf-8") for path in filed} == {
        MOTION,
        MOTION + "Second copy.\n",
    }


def test_a_scanned_pdf_goes_to_review_with_an_ocr_reason(
    block_network: None, source: Path, destination: Path
) -> None:
    write_blank_pdf(source / "IMG_2213.pdf")

    main([str(source), "--output", str(destination), "--apply"])

    assert (destination / "_Needs Review" / "IMG_2213.pdf").exists()
    assert "needs OCR" in _log(destination)


# --------------------------------------------------------------------------
# Refusals
# --------------------------------------------------------------------------


def test_a_destination_inside_the_source_is_refused_with_no_partial_work(
    block_network: None, source: Path
) -> None:
    before = _tree_digest(source)
    nested = source / "Organized Documents"

    with pytest.raises(SystemExit) as exit_info:
        main([str(source), "--output", str(nested), "--apply"])

    assert exit_info.value.code == EXIT_REFUSED
    assert not nested.exists()
    assert _tree_digest(source) == before


def test_a_source_that_is_not_a_directory_is_refused(
    block_network: None, tmp_path: Path, destination: Path
) -> None:
    not_a_directory = tmp_path / "single.txt"
    not_a_directory.write_text("x", encoding="utf-8")

    with pytest.raises(SystemExit) as exit_info:
        main([str(not_a_directory), "--output", str(destination)])

    assert exit_info.value.code == EXIT_REFUSED
    assert not destination.exists()


# --------------------------------------------------------------------------
# Flags
# --------------------------------------------------------------------------


def test_flat_ignores_nested_folders(block_network: None, source: Path, destination: Path) -> None:
    nested = source / "subfolder"
    nested.mkdir()
    (nested / "deep_motion.txt").write_text(MOTION, encoding="utf-8")

    main([str(source), "--output", str(destination), "--flat"])

    assert "deep_motion.txt" not in _log(destination)


def test_recursive_is_the_default(block_network: None, source: Path, destination: Path) -> None:
    nested = source / "subfolder"
    nested.mkdir()
    (nested / "deep_motion.txt").write_text(MOTION, encoding="utf-8")

    main([str(source), "--output", str(destination)])

    assert "deep_motion.txt" in _log(destination)


def test_a_lower_threshold_files_a_document_that_would_otherwise_be_reviewed(
    block_network: None, source: Path, destination: Path
) -> None:
    (source / "note.txt").unlink()
    (source / "bank.txt").write_text(BANK_STATEMENT, encoding="utf-8")

    # At the default threshold this statement lands in review (asserted below);
    # the flag has to be what changes that.
    main([str(source), "--output", str(destination), "--apply"])
    assert (destination / "_Needs Review" / "bank.txt").exists()

    # Put it back in the inbox and rerun with a permissive threshold.
    (destination / "_Needs Review" / "bank.txt").rename(source / "bank.txt")
    main([str(source), "--output", str(destination), "--threshold", "0.05", "--apply"])

    assert any("Finance" in str(path) for path in _files_under(destination))


def test_an_out_of_range_threshold_is_refused(
    block_network: None, source: Path, destination: Path
) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main([str(source), "--output", str(destination), "--threshold", "1.5"])

    assert exit_info.value.code == EXIT_REFUSED
    assert not destination.exists()


def test_config_overrides_the_review_folder(
    block_network: None, source: Path, destination: Path, tmp_path: Path
) -> None:
    config = tmp_path / "my-taxonomy.json"
    config.write_text(
        json.dumps({"version": 2, "review_folder": "_Triage"}),
        encoding="utf-8",
    )

    main([str(source), "--output", str(destination), "--config", str(config), "--apply"])

    assert (destination / "_Triage" / "note.txt").exists()
    assert not (destination / "_Needs Review").exists()


def test_report_flag_renames_the_log(block_network: None, source: Path, destination: Path) -> None:
    main([str(source), "--output", str(destination), "--report", "PLAN.md"])

    assert (destination / "PLAN.md").exists()
    assert not (destination / LOG_NAME).exists()


def test_the_log_is_never_re_ingested_on_a_second_run(block_network: None, tmp_path: Path) -> None:
    # The shipped ignore list covers ORGANIZING-LOG.md, so a log left in a
    # scanned folder is skipped rather than filed as a document.
    folder = tmp_path / "Unsorted"
    folder.mkdir()
    (folder / LOG_NAME).write_text("# Organizing Log — stale\n", encoding="utf-8")
    (folder / "scan_0143.txt").write_text(MOTION, encoding="utf-8")

    main([str(folder), "--output", str(tmp_path / "out"), "--apply"])

    assert (folder / LOG_NAME).exists()
