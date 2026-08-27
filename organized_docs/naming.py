"""Standardized destination filenames and collision-safe paths.

Spec contract: build `YYYY-MM-DD_Sender_Description.ext`, with date
precedence date-found-in-text -> PDF metadata (`/CreationDate`) ->
file mtime -> `Undated`; sender from a `From:`/letterhead/signature
pattern or else the leaf's folder name; description from the first
meaningful line, trimmed to 40 chars. Every component is sanitized. A
destination path is only used after `unique_path` confirms it does not
already exist, appending `-1`, `-2`, ... on collision.
"""

from __future__ import annotations

from pathlib import Path


def build_name(text: str, metadata: dict[str, str], source: Path, leaf_path: str) -> str:
    """Build `YYYY-MM-DD_Sender_Description.ext` (or a degraded variant).

    Raises `NotImplementedError` until naming ships in a later PR.
    """
    raise NotImplementedError(
        "build_name is implemented in a later PR; see the spec's naming.py contract"
    )


def sanitize(component: str) -> str:
    """Strip path separators/control chars, collapse whitespace, cap length.

    Never returns an empty string. Raises `NotImplementedError` until
    naming ships in a later PR.
    """
    raise NotImplementedError(
        "sanitize is implemented in a later PR; see the spec's naming.py contract"
    )


def unique_path(dest_dir: Path, filename: str) -> Path:
    """Return a non-colliding path under `dest_dir`, appending `-1`, `-2`, ...

    Raises `NotImplementedError` until naming ships in a later PR.
    """
    raise NotImplementedError(
        "unique_path is implemented in a later PR; see the spec's naming.py contract"
    )
