"""Per-file-type text extraction for the classification pipeline.

Spec contract (see the "Local Document Organizer CLI" spec, module
`extract.py`): `pypdf` for PDFs, a plain read for `.txt`/`.md`, and a
stdlib `zipfile` + XML parse for `.docx`. A PDF with no extractable
text layer (a scanned image) must return empty text and a `needs_ocr`
flag so the caller routes it to `_Needs Review/` instead of guessing.
No network I/O, ever.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class ExtractedText:
    """Result of extracting text and metadata from a single source file."""

    text: str
    needs_ocr: bool
    metadata: dict[str, str]


def extract_text(path: Path) -> ExtractedText:
    """Extract text and metadata from `path` according to its file type.

    Raises `NotImplementedError` until extraction ships in a later PR.
    """
    raise NotImplementedError(
        "extract_text is implemented in a later PR; see the spec's extract.py contract"
    )
