"""Extraction tests: one fixture per file type in the spec's scope.

Every test depends on `block_network` to prove the extraction path
touches disk only, never the network.
"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest
from pdf_fixtures import write_blank_pdf, write_text_pdf

from organized_docs.extract import ExtractedText, SkippedFile, extract_text

_DOCX_DOCUMENT_XML = """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:r><w:t>Motion to Compel Discovery</w:t></w:r></w:p>
    <w:p><w:r><w:t>Filed on behalf of the petitioner.</w:t></w:r></w:p>
  </w:body>
</w:document>
"""


def _write_docx(path: Path) -> None:
    """Write a minimal `.docx`: a zip containing just `word/document.xml`.

    Extraction only reads that one part, so the rest of the OOXML
    package (content types, rels) is irrelevant to this test.
    """
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("word/document.xml", _DOCX_DOCUMENT_XML)


def test_pdf_with_text_layer_extracts_text(block_network: None, tmp_path: Path) -> None:
    pdf_path = tmp_path / "citation.pdf"
    write_text_pdf(pdf_path, "Hello Fixture World")

    result = extract_text(pdf_path)

    assert isinstance(result, ExtractedText)
    assert result.file_type == "pdf"
    assert "Hello Fixture World" in result.text
    assert result.needs_ocr is False


def test_image_only_pdf_flags_needs_ocr(block_network: None, tmp_path: Path) -> None:
    pdf_path = tmp_path / "scanned.pdf"
    write_blank_pdf(pdf_path)

    result = extract_text(pdf_path)

    assert isinstance(result, ExtractedText)
    assert result.file_type == "pdf"
    assert result.text == ""
    assert result.needs_ocr is True


def test_txt_file_is_read_directly(block_network: None, tmp_path: Path) -> None:
    txt_path = tmp_path / "letter.txt"
    txt_path.write_text("Dear Anna,\nYour hearing is confirmed.\n", encoding="utf-8")

    result = extract_text(txt_path)

    assert isinstance(result, ExtractedText)
    assert result.file_type == "txt"
    assert result.needs_ocr is False
    assert "Your hearing is confirmed." in result.text


def test_md_file_is_read_directly(block_network: None, tmp_path: Path) -> None:
    md_path = tmp_path / "notes.md"
    md_path.write_text("# Case notes\n\nFollow up next week.\n", encoding="utf-8")

    result = extract_text(md_path)

    assert isinstance(result, ExtractedText)
    assert result.file_type == "md"
    assert "Follow up next week." in result.text


def test_docx_extracts_paragraph_text(block_network: None, tmp_path: Path) -> None:
    docx_path = tmp_path / "motion.docx"
    _write_docx(docx_path)

    result = extract_text(docx_path)

    assert isinstance(result, ExtractedText)
    assert result.file_type == "docx"
    assert result.needs_ocr is False
    assert "Motion to Compel Discovery" in result.text
    assert "Filed on behalf of the petitioner." in result.text


def test_unsupported_extension_is_skipped_with_a_reason(
    block_network: None, tmp_path: Path
) -> None:
    image_path = tmp_path / "photo.jpg"
    image_path.write_bytes(b"not a real jpeg, content is irrelevant")

    result = extract_text(image_path)

    assert isinstance(result, SkippedFile)
    assert ".jpg" in result.reason


@pytest.mark.parametrize("suffix", [".eml", ".csv", ""])
def test_other_unsupported_extensions_are_skipped(
    block_network: None, tmp_path: Path, suffix: str
) -> None:
    other_path = tmp_path / f"mystery{suffix}"
    other_path.write_bytes(b"irrelevant")

    result = extract_text(other_path)

    assert isinstance(result, SkippedFile)
