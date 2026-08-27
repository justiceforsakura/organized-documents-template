"""Naming: date precedence, sender/description extraction, sanitizing, collisions."""

from __future__ import annotations

import os
from datetime import datetime
from pathlib import Path

from organized_docs.naming import (
    DATE_SOURCE_METADATA,
    DATE_SOURCE_MTIME,
    DATE_SOURCE_NONE,
    DATE_SOURCE_TEXT,
    MAX_COMPONENT_LENGTH,
    SANITIZE_FALLBACK,
    UNDATED,
    build_name,
    build_name_parts,
    resolve_date,
    sanitize,
    unique_path,
)

LETTER = """From: Travis County District Clerk
Citation and Notice of Hearing
Filed: March 5, 2024
"""

METADATA = {"/CreationDate": "D:20220102153000-06'00'"}


def _touch(path: Path, *, text: str = "x", mtime: datetime | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    if mtime is not None:
        stamp = mtime.timestamp()
        os.utime(path, (stamp, stamp))
    return path


# --------------------------------------------------------------------------
# Date precedence
# --------------------------------------------------------------------------


def test_date_in_text_beats_metadata_and_mtime(tmp_path: Path) -> None:
    source = _touch(tmp_path / "citation.pdf", mtime=datetime(2019, 7, 4, 12, 0))

    resolved = resolve_date(LETTER, METADATA, source)

    assert resolved.value == "2024-03-05"
    assert resolved.source == DATE_SOURCE_TEXT


def test_metadata_used_when_text_has_no_date(tmp_path: Path) -> None:
    source = _touch(tmp_path / "citation.pdf", mtime=datetime(2019, 7, 4, 12, 0))

    resolved = resolve_date("Notice of hearing, no date printed.", METADATA, source)

    assert resolved.value == "2022-01-02"
    assert resolved.source == DATE_SOURCE_METADATA


def test_mtime_used_when_text_and_metadata_have_no_date(tmp_path: Path) -> None:
    source = _touch(tmp_path / "citation.pdf", mtime=datetime(2019, 7, 4, 12, 0))

    resolved = resolve_date("no date anywhere", {}, source)

    assert resolved.value == "2019-07-04"
    assert resolved.source == DATE_SOURCE_MTIME


def test_undated_when_no_source_yields_a_date(tmp_path: Path) -> None:
    resolved = resolve_date("no date anywhere", {}, tmp_path / "missing.pdf")

    assert resolved.value == UNDATED
    assert resolved.source == DATE_SOURCE_NONE


def test_cued_date_beats_an_earlier_uncued_one(tmp_path: Path) -> None:
    source = _touch(tmp_path / "bill.pdf")
    text = "Account opened 2001-05-09.\nAmount due by 2024-09-01.\nFiled: 2024-06-12\n"

    assert resolve_date(text, {}, source).value == "2024-06-12"


def test_iso_month_name_and_numeric_dates_all_parse(tmp_path: Path) -> None:
    source = _touch(tmp_path / "doc.pdf")

    assert resolve_date("dated 2024-03-05", {}, source).value == "2024-03-05"
    assert resolve_date("dated March 5th, 2024", {}, source).value == "2024-03-05"
    assert resolve_date("dated 03/05/2024", {}, source).value == "2024-03-05"


def test_impossible_date_is_not_accepted(tmp_path: Path) -> None:
    source = _touch(tmp_path / "doc.pdf", mtime=datetime(2019, 7, 4, 12, 0))

    # 2024-02-31 does not exist; the run must fall through to the next source.
    assert resolve_date("filed 2024-02-31", {}, source).source == DATE_SOURCE_MTIME


# --------------------------------------------------------------------------
# sanitize
# --------------------------------------------------------------------------


def test_sanitize_strips_path_separators_and_illegal_characters() -> None:
    assert "/" not in sanitize("Travis/County")
    assert "\\" not in sanitize(r"C:\Users\anna")
    assert sanitize('a<b>c:"d/e\\f|g?h*i') == "a b c d e f g h i"


def test_sanitize_strips_control_characters_and_collapses_whitespace() -> None:
    assert sanitize("Travis\x00\tCounty\n\nClerk") == "Travis County Clerk"


def test_sanitize_removes_the_component_separator() -> None:
    # Underscore separates the three filename components, so no component
    # may contain one or the name becomes unparseable.
    assert "_" not in sanitize("bank_statement_june")


def test_sanitize_caps_length_and_trims_trailing_dots_and_spaces() -> None:
    capped = sanitize("x" * 200)

    assert len(capped) == MAX_COMPONENT_LENGTH
    assert sanitize("Report.  ") == "Report"


def test_sanitize_is_never_empty() -> None:
    assert sanitize("") == SANITIZE_FALLBACK
    assert sanitize("///") == SANITIZE_FALLBACK
    assert sanitize("\x00\x01") == SANITIZE_FALLBACK


# --------------------------------------------------------------------------
# build_name
# --------------------------------------------------------------------------


def test_build_name_uses_date_sender_and_description(tmp_path: Path) -> None:
    source = _touch(tmp_path / "scan_0143.PDF")

    name = build_name(LETTER, {}, source, "LEGAL_AND_ADVOCACY/01_PROBATE_COURT")

    assert name == "2024-03-05_Travis County District Clerk_Citation and Notice of Hearing.pdf"


def test_build_name_falls_back_to_the_leaf_folder_as_sender(tmp_path: Path) -> None:
    source = _touch(tmp_path / "note.txt")

    parts = build_name_parts(
        "Immunization record for the 2024 school year",
        {},
        source,
        "Family Shared/Medical Records/Immunization Records",
    )

    assert parts.sender == "Immunization Records"


def test_build_name_reads_a_signature_block_when_there_is_no_from_line(tmp_path: Path) -> None:
    source = _touch(tmp_path / "note.txt")
    text = "Please find enclosed the executed agreement.\n\nSincerely,\nJane Roe\n"

    assert build_name_parts(text, {}, source, "Personal/Correspondence").sender == "Jane Roe"


def test_build_name_caps_the_description_at_forty_characters(tmp_path: Path) -> None:
    source = _touch(tmp_path / "note.txt")
    text = "From: Clerk\n" + "A" * 120 + "\n"

    parts = build_name_parts(text, {}, source, "Personal/Correspondence")

    assert len(parts.description) == MAX_COMPONENT_LENGTH


def test_build_name_degrades_to_undated_and_a_fallback_description(tmp_path: Path) -> None:
    name = build_name("", {}, tmp_path / "missing.pdf", "Miscellaneous")

    assert name == f"{UNDATED}_Miscellaneous_{SANITIZE_FALLBACK}.pdf"


# --------------------------------------------------------------------------
# unique_path
# --------------------------------------------------------------------------


def test_unique_path_returns_the_plain_name_when_free(tmp_path: Path) -> None:
    assert unique_path(tmp_path, "letter.pdf") == tmp_path / "letter.pdf"


def test_unique_path_appends_incrementing_suffixes(tmp_path: Path) -> None:
    (tmp_path / "letter.pdf").write_bytes(b"first")

    first_collision = unique_path(tmp_path, "letter.pdf")
    assert first_collision == tmp_path / "letter-1.pdf"

    (tmp_path / "letter-1.pdf").write_bytes(b"second")
    assert unique_path(tmp_path, "letter.pdf") == tmp_path / "letter-2.pdf"


def test_unique_path_respects_destinations_reserved_earlier_in_the_run(tmp_path: Path) -> None:
    reserved = {tmp_path / "letter.pdf"}

    assert unique_path(tmp_path, "letter.pdf", reserved) == tmp_path / "letter-1.pdf"
