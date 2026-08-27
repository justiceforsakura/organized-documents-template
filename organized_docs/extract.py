"""Per-file-type text extraction for the classification pipeline.

Spec contract (see the "Local Document Organizer CLI" spec, module
`extract.py`): `pypdf` for PDFs, a plain read for `.txt`/`.md`, and a
stdlib `zipfile` + XML parse for `.docx`. A PDF with no extractable
text layer (a scanned image) must return empty text and a `needs_ocr`
flag so the caller routes it to `_Needs Review/` instead of guessing.
Extensions outside this set are skipped with a reason rather than
raising, since scanning a real unsorted folder always turns up files
the pipeline doesn't understand yet. No network I/O, ever.
"""

from __future__ import annotations

import xml.etree.ElementTree as ET
import zipfile
from dataclasses import dataclass
from pathlib import Path

from pypdf import PdfReader

# In-scope suffixes read as plain UTF-8 text (no parsing beyond decoding).
_PLAIN_TEXT_SUFFIXES = frozenset({".txt", ".md"})

# WordprocessingML namespace for the runs/paragraphs inside word/document.xml.
_DOCX_NAMESPACE = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
_DOCX_PARAGRAPH_TAG = f"{_DOCX_NAMESPACE}p"
_DOCX_TEXT_TAG = f"{_DOCX_NAMESPACE}t"


@dataclass(frozen=True)
class ExtractedText:
    """Successful extraction result for a supported file type."""

    text: str
    file_type: str
    needs_ocr: bool
    metadata: dict[str, str]


@dataclass(frozen=True)
class SkippedFile:
    """A file the pipeline does not attempt to extract, and why."""

    reason: str


# extract_text returns either a populated result or a reason it was skipped;
# callers branch on type instead of checking a boolean, so a skip can never
# be mistaken for a successful zero-length extraction.
ExtractionResult = ExtractedText | SkippedFile


def extract_text(path: Path) -> ExtractionResult:
    """Extract text and metadata from `path` according to its file type.

    Dispatches on the file suffix (case-insensitive): `.pdf` through
    `pypdf`, `.txt`/`.md` as plain UTF-8 reads, `.docx` through a
    stdlib zip + XML parse of `word/document.xml`. Any other suffix
    comes back as a `SkippedFile` carrying the reason.
    """
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix in _PLAIN_TEXT_SUFFIXES:
        return _extract_plain_text(path, suffix)
    if suffix == ".docx":
        return _extract_docx(path)
    return SkippedFile(reason=f"unsupported extension '{suffix or path.name}'")


def _extract_pdf(path: Path) -> ExtractedText:
    """Read every page's text layer via `pypdf`.

    A reader that yields no text at all (a scanned, image-only PDF)
    is not a failure — it's flagged `needs_ocr` so the caller routes
    the file to review instead of guessing at its content.
    """
    reader = PdfReader(str(path))
    text = "\n".join(page.extract_text() or "" for page in reader.pages).strip()
    return ExtractedText(
        text=text,
        file_type="pdf",
        needs_ocr=not text,
        metadata=_pdf_metadata(reader),
    )


def _pdf_metadata(reader: PdfReader) -> dict[str, str]:
    """Flatten `pypdf`'s document info dictionary to plain strings."""
    info = reader.metadata
    if info is None:
        return {}
    return {str(key): str(value) for key, value in info.items() if value is not None}


def _extract_plain_text(path: Path, suffix: str) -> ExtractedText:
    """Read a `.txt`/`.md` file as UTF-8, replacing any undecodable bytes."""
    text = path.read_text(encoding="utf-8", errors="replace")
    return ExtractedText(
        text=text,
        file_type=suffix.removeprefix("."),
        needs_ocr=False,
        metadata={},
    )


def _extract_docx(path: Path) -> ExtractedText:
    """Read `word/document.xml` out of the `.docx` zip container.

    Uses only the stdlib (`zipfile` + `xml.etree.ElementTree`) per the
    spec — no `python-docx` dependency. Paragraphs are joined with
    newlines; runs within a paragraph are concatenated directly, which
    covers simple documents but may under-extract heavily formatted
    ones (tables, text boxes) — those fall through to review on low
    confidence rather than being mis-filed.
    """
    with zipfile.ZipFile(path) as archive:
        document_xml = archive.read("word/document.xml")
    root = ET.fromstring(document_xml)
    paragraphs = [
        "".join(run.text or "" for run in paragraph.iter(_DOCX_TEXT_TAG))
        for paragraph in root.iter(_DOCX_PARAGRAPH_TAG)
    ]
    return ExtractedText(
        text="\n".join(paragraphs).strip(),
        file_type="docx",
        needs_ocr=False,
        metadata={},
    )
