"""Taxonomy loading, merging, and validation.

Spec contract: load the built-in taxonomy plus an optional user
`--config` JSON override, perform a versioned merge in which user
edits win, and validate that every leaf path is unique and every
leaf's `group` exists among the defined groups. Regenerating the seed
taxonomy from `create_organized_folders.sh` must never clobber a
user's edits.
"""

from __future__ import annotations

from pathlib import Path


def load_taxonomy(config_path: Path | None) -> dict[str, object]:
    """Load and validate the merged taxonomy (built-in plus user overrides).

    `config_path=None` uses the built-in taxonomy only. Raises
    `NotImplementedError` until the taxonomy loader ships in a later PR.
    """
    raise NotImplementedError(
        "load_taxonomy is implemented in a later PR; see the spec's taxonomy.py contract"
    )
