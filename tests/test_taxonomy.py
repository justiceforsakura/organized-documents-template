"""Tests for taxonomy loading, the versioned merge, and validation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from organized_docs.taxonomy import (
    Taxonomy,
    TaxonomyError,
    builtin_taxonomy_data,
    compile_taxonomy,
    keyword_regex,
    load_taxonomy,
    merge_taxonomies,
    path_terms_for,
)


def minimal_taxonomy(**overrides: Any) -> dict[str, Any]:
    """A tiny but valid taxonomy, so each test can break exactly one thing."""
    data: dict[str, Any] = {
        "version": 2,
        "groups": {"Finance": {"path_prefix": "Finance", "keywords": ["invoice"], "patterns": []}},
        "leaves": [
            {
                "path": "Finance/Banking",
                "seed_path": "Finance/Banking",
                "group": "Finance",
                "keywords": ["bank statement"],
                "patterns": ["statement period"],
            }
        ],
    }
    data.update(overrides)
    return data


def write_config(tmp_path: Path, data: dict[str, Any]) -> Path:
    path = tmp_path / "taxonomy.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    return path


# --------------------------------------------------------------------------
# The shipped taxonomy
# --------------------------------------------------------------------------


def test_builtin_taxonomy_loads_and_is_internally_consistent(block_network: None) -> None:
    taxonomy = load_taxonomy()

    assert isinstance(taxonomy, Taxonomy)
    assert taxonomy.leaves, "the shipped taxonomy must contain leaves"
    paths = [leaf.path for leaf in taxonomy.leaves]
    assert len(paths) == len(set(paths)), "leaf paths must be unique"
    assert all(leaf.group in taxonomy.groups for leaf in taxonomy.leaves)
    assert taxonomy.group_threshold == 0.5
    assert taxonomy.confidence_threshold == 0.6


def test_builtin_taxonomy_indexes_leaves_by_group() -> None:
    taxonomy = load_taxonomy()

    for name in taxonomy.groups:
        assert all(leaf.group == name for leaf in taxonomy.leaves_for(name))
    total = sum(len(taxonomy.leaves_for(name)) for name in taxonomy.groups)
    assert total == len(taxonomy.leaves)


def test_builtin_data_matches_the_generated_file() -> None:
    data = builtin_taxonomy_data()

    assert data["generated_from"] == "create_organized_folders.sh"
    assert data["version"] == 2


# --------------------------------------------------------------------------
# Validation
# --------------------------------------------------------------------------


def test_duplicate_leaf_paths_are_rejected() -> None:
    data = minimal_taxonomy()
    data["leaves"].append(dict(data["leaves"][0]))

    with pytest.raises(TaxonomyError, match="duplicate leaf paths"):
        compile_taxonomy(data)


def test_leaf_naming_an_undefined_group_is_rejected() -> None:
    data = minimal_taxonomy()
    data["leaves"][0]["group"] = "Nonexistent"

    with pytest.raises(TaxonomyError, match="which is not defined"):
        compile_taxonomy(data)


def test_leaf_without_a_path_is_rejected() -> None:
    data = minimal_taxonomy()
    data["leaves"][0]["path"] = "   "

    with pytest.raises(TaxonomyError, match="missing a non-empty 'path'"):
        compile_taxonomy(data)


def test_invalid_regex_is_rejected_with_its_location() -> None:
    data = minimal_taxonomy()
    data["leaves"][0]["patterns"] = ["("]

    with pytest.raises(TaxonomyError, match="invalid regex"):
        compile_taxonomy(data)


def test_unsupported_version_is_rejected() -> None:
    with pytest.raises(TaxonomyError, match="unsupported taxonomy version"):
        compile_taxonomy(minimal_taxonomy(version=99))


@pytest.mark.parametrize(
    ("key", "value", "message"),
    [
        ("group_threshold", 1.5, "between 0 and 1"),
        ("confidence_threshold", -0.1, "between 0 and 1"),
        ("confidence_threshold", "high", "must be a number"),
        ("keyword_weight", 0, "greater than 0"),
        ("leaf_saturation", -2, "greater than 0"),
    ],
)
def test_out_of_range_settings_are_rejected(key: str, value: Any, message: str) -> None:
    with pytest.raises(TaxonomyError, match=message):
        compile_taxonomy(minimal_taxonomy(**{key: value}))


def test_all_problems_are_reported_together() -> None:
    data = minimal_taxonomy(group_threshold=2.0)
    data["leaves"][0]["group"] = "Nope"

    with pytest.raises(TaxonomyError) as excinfo:
        compile_taxonomy(data)

    message = str(excinfo.value)
    assert "group_threshold" in message and "which is not defined" in message


def test_unreadable_config_names_the_file(tmp_path: Path) -> None:
    missing = tmp_path / "nope.json"

    with pytest.raises(TaxonomyError, match="could not read taxonomy"):
        load_taxonomy(missing)


def test_malformed_config_json_names_the_file(tmp_path: Path) -> None:
    path = tmp_path / "taxonomy.json"
    path.write_text("{not json", encoding="utf-8")

    with pytest.raises(TaxonomyError, match="not valid JSON"):
        load_taxonomy(path)


# --------------------------------------------------------------------------
# Merge: user edits win
# --------------------------------------------------------------------------


def test_user_settings_win_over_the_builtin() -> None:
    merged = merge_taxonomies(minimal_taxonomy(), {"confidence_threshold": 0.9})

    assert merged["confidence_threshold"] == 0.9


def test_user_leaf_edits_win_and_omitted_fields_are_inherited() -> None:
    merged = merge_taxonomies(
        minimal_taxonomy(),
        {"leaves": [{"seed_path": "Finance/Banking", "keywords": ["wire transfer"]}]},
    )

    (leaf,) = merged["leaves"]
    assert leaf["keywords"] == ["wire transfer"]
    assert leaf["patterns"] == ["statement period"], "unset fields still come from the seed"
    assert leaf["group"] == "Finance"


def test_retargeted_leaf_is_updated_in_place_not_duplicated() -> None:
    merged = merge_taxonomies(
        minimal_taxonomy(),
        {"leaves": [{"seed_path": "Finance/Banking", "path": "Finance/Banking/Joint"}]},
    )

    assert len(merged["leaves"]) == 1
    assert merged["leaves"][0]["path"] == "Finance/Banking/Joint"


def test_user_added_group_and_leaf_survive_the_merge() -> None:
    merged = merge_taxonomies(
        minimal_taxonomy(),
        {
            "groups": {
                "Custom": {"path_prefix": "Custom", "keywords": ["bespoke"], "patterns": []}
            },
            "leaves": [
                {"path": "Custom/Things", "group": "Custom", "keywords": ["widget"], "patterns": []}
            ],
        },
    )

    assert "Custom" in merged["groups"]
    assert {leaf["path"] for leaf in merged["leaves"]} == {"Finance/Banking", "Custom/Things"}


def test_regenerated_seed_does_not_clobber_user_edits() -> None:
    """A template update adds a leaf; the user's tuning of an old leaf survives."""
    user = merge_taxonomies(
        minimal_taxonomy(),
        {"leaves": [{"seed_path": "Finance/Banking", "keywords": ["wire transfer"]}]},
    )
    regenerated = minimal_taxonomy()
    regenerated["leaves"].append(
        {
            "path": "Finance/Loans",
            "seed_path": "Finance/Loans",
            "group": "Finance",
            "keywords": ["loan"],
            "patterns": [],
        }
    )

    merged = merge_taxonomies(regenerated, user)

    by_path = {leaf["path"]: leaf for leaf in merged["leaves"]}
    assert by_path["Finance/Banking"]["keywords"] == ["wire transfer"]
    assert "Finance/Loans" in by_path, "a newly generated leaf still arrives"


def test_load_taxonomy_applies_a_partial_user_config(tmp_path: Path) -> None:
    config = write_config(
        tmp_path,
        {
            "confidence_threshold": 0.85,
            "leaves": [
                {
                    "seed_path": "Finance/Banking/Anna",
                    "path": "Finance/Banking/Household",
                    "keywords": ["household ledger"],
                }
            ],
        },
    )

    taxonomy = load_taxonomy(config)

    assert taxonomy.confidence_threshold == 0.85
    retargeted = taxonomy.leaf("Finance/Banking/Household")
    assert retargeted is not None
    assert retargeted.rules.keywords == ("household ledger",)
    assert taxonomy.leaf("Finance/Banking/Anna") is None, "the leaf moved, it was not cloned"


# --------------------------------------------------------------------------
# Rule compilation
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("keyword", "text", "expected"),
    [
        ("deposit", "three deposits cleared", True),
        ("deposit", "DEPOSIT", True),
        ("atmo", "Atmos Energy", True),
        ("att", "attached hereto", False),
        ("bank statement", "Bank   Statement for June", True),
        ("bank statement", "bank of the statement", False),
        ("w-2", "2024 Form W-2 issued", True),
    ],
)
def test_keyword_matching_is_whole_word_and_stem_tolerant(
    keyword: str, text: str, expected: bool
) -> None:
    assert bool(keyword_regex(keyword).search(text)) is expected


def test_blank_keyword_is_rejected() -> None:
    with pytest.raises(TaxonomyError, match="must not be blank"):
        keyword_regex("   ")


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("Finance/Banking/Anna", ("finance", "banking", "anna")),
        (
            "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL",
            ("legal and advocacy", "travis county and local"),
        ),
        ("Family Shared/Medical Records", ("family shared", "medical records")),
        ("Finance/Tax Documents/W2 & 1099 Forms", ("finance", "tax documents", "w2 forms")),
        ("Finance/Bills and Utilities/ATT", ("finance", "bills and utilities")),
        ("Business/Misc", ("business",)),
    ],
)
def test_path_terms_drop_numeric_prefixes_and_generic_segments(
    path: str, expected: tuple[str, ...]
) -> None:
    assert path_terms_for(path) == expected
