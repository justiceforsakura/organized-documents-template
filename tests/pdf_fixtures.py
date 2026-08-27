"""Synthetic PDF builders for extraction tests.

`pypdf`'s `PdfWriter` has no high-level "draw text" API, so a text-layer
fixture is built by hand: a blank page plus a minimal content stream
(`BT ... Tj ET`) referencing the standard, non-embedded Helvetica font.
No third-party rendering library is pulled in just to produce fixtures.
"""

from __future__ import annotations

from pathlib import Path

from pypdf import PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

_PAGE_WIDTH = 612
_PAGE_HEIGHT = 792


def write_text_pdf(path: Path, text: str) -> None:
    """Write a one-page PDF whose content stream renders `text`."""
    writer = PdfWriter()
    page = writer.add_blank_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
    page[NameObject("/Resources")] = _helvetica_resources(writer)

    stream = DecodedStreamObject()
    stream.set_data(f"BT /F1 24 Tf 72 712 Td ({_escape(text)}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(stream)

    with path.open("wb") as handle:
        writer.write(handle)


def write_blank_pdf(path: Path) -> None:
    """Write a one-page PDF with no content stream at all.

    Stands in for a scanned, image-only page: `pypdf` has no text
    layer to extract, which is exactly the `needs_ocr` case.
    """
    writer = PdfWriter()
    writer.add_blank_page(width=_PAGE_WIDTH, height=_PAGE_HEIGHT)
    with path.open("wb") as handle:
        writer.write(handle)


def _helvetica_resources(writer: PdfWriter) -> DictionaryObject:
    """Build a `/Resources` dict naming the standard Helvetica font `/F1`.

    Helvetica is one of the 14 standard PDF fonts: no embedding or font
    program is required for a viewer (or `pypdf`) to decode it.
    """
    font = DictionaryObject()
    font[NameObject("/Type")] = NameObject("/Font")
    font[NameObject("/Subtype")] = NameObject("/Type1")
    font[NameObject("/BaseFont")] = NameObject("/Helvetica")
    font[NameObject("/Encoding")] = NameObject("/WinAnsiEncoding")

    font_dict = DictionaryObject()
    font_dict[NameObject("/F1")] = writer._add_object(font)

    resources = DictionaryObject()
    resources[NameObject("/Font")] = font_dict
    return resources


def _escape(text: str) -> str:
    """Escape the characters PDF string literals treat specially."""
    return text.replace("\\", r"\\").replace("(", r"\(").replace(")", r"\)")
