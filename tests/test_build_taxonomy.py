"""Tests for the taxonomy builder.

The coverage test is the load-bearing one: it re-parses the template script
independently of the generator and asserts the shipped `taxonomy.json` names
every template folder exactly once. A folder that can never receive a file is a
silent hole in the product, so it fails CI.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import build_taxonomy as bt
import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT_PATH = REPO_ROOT / "create_organized_folders.sh"
TAXONOMY_PATH = REPO_ROOT / "organized_docs" / "data" / "taxonomy.json"


@pytest.fixture(scope="module")
def template_dirs() -> list[str]:
    """Folder paths parsed straight out of the template shell script."""
    return bt.parse_dirs(SCRIPT_PATH.read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def shipped_taxonomy() -> dict:
    return json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))


# --------------------------------------------------------------------------
# Parsing
# --------------------------------------------------------------------------


def test_parses_quotes_escapes_and_comments() -> None:
    script = """#!/bin/bash
BASE="$HOME/Documents"
dirs=(
  "Finance/Banking"          # trailing comment
  'Home/Utilities/Water'
  "Legal/Notes (draft)"
  "Legal/He said \\"stop\\""
  Bare/Unquoted
)
"""
    assert bt.parse_dirs(script) == [
        "Finance/Banking",
        "Home/Utilities/Water",
        "Legal/Notes (draft)",
        'Legal/He said "stop"',
        "Bare/Unquoted",
    ]


def test_missing_dirs_array_raises() -> None:
    with pytest.raises(ValueError, match="no `dirs=\\(` array"):
        bt.parse_dirs("#!/bin/bash\necho hi\n")


def test_unterminated_dirs_array_raises() -> None:
    with pytest.raises(ValueError, match="unterminated"):
        bt.parse_dirs('dirs=(\n  "Finance/Banking"\n')


# --------------------------------------------------------------------------
# Seeded keywords
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "expected"),
    [
        ("Business/Financial Documents/Bank Statements", ["bank statement", "statement"]),
        ("Finance/Loans/Mortgage Documents", ["mortgage document"]),
        ("Family Shared/Identification/Passport", ["passport"]),
        ("Education/Anna/Transcripts and Grades/Report Cards", ["report card", "card"]),
        ("Business/Miscellaneous", []),
        ("Miscellaneous", []),
    ],
)
def test_seed_keywords(path: str, expected: list[str]) -> None:
    assert bt.seed_keywords(path) == expected


def test_normalize_segment_strips_ordering_prefixes_and_separators() -> None:
    assert bt.normalize_segment("02.3_TWC_Civil_Rights_and_Labor") == "twc civil rights and labor"
    assert bt.normalize_segment("Rent:Mortgage Receipts") == "rent mortgage receipts"
    assert bt.normalize_segment("@Command Center") == "command center"


def test_generic_folders_get_no_rules_so_they_cannot_win(shipped_taxonomy: dict) -> None:
    catch_alls = [
        leaf
        for leaf in shipped_taxonomy["leaves"]
        if leaf["path"].split("/")[-1].lower() == "miscellaneous"
    ]
    assert catch_alls, "expected the template to contain Miscellaneous folders"
    for leaf in catch_alls:
        assert leaf["keywords"] == []
        assert leaf["patterns"] == []


# --------------------------------------------------------------------------
# Coverage invariant
# --------------------------------------------------------------------------


def test_generated_leaves_cover_every_template_folder_exactly_once(
    template_dirs: list[str],
) -> None:
    leaves = bt.build_seed_taxonomy(template_dirs)["leaves"]
    paths = [leaf["path"] for leaf in leaves]

    assert len(paths) == len(set(paths)), "a template folder was emitted more than once"
    assert set(paths) == set(template_dirs)
    assert len(paths) == len(template_dirs)


def test_shipped_taxonomy_is_up_to_date(template_dirs: list[str], shipped_taxonomy: dict) -> None:
    """The committed taxonomy.json matches what the builder generates today."""
    seed = bt.build_seed_taxonomy(template_dirs)
    expected = bt.serialize(bt.merge_taxonomy(shipped_taxonomy, seed))
    assert TAXONOMY_PATH.read_text(encoding="utf-8") == expected


def test_shipped_taxonomy_matches_the_spec_schema(shipped_taxonomy: dict) -> None:
    assert shipped_taxonomy["version"] == 2
    assert shipped_taxonomy["generated_from"] == "create_organized_folders.sh"
    assert shipped_taxonomy["output_root"] == "~/Documents/Organized Documents"
    assert shipped_taxonomy["review_folder"] == "_Needs Review"
    assert shipped_taxonomy["group_threshold"] == 0.5
    assert shipped_taxonomy["confidence_threshold"] == 0.6
    assert shipped_taxonomy["ignore"] == ["~$", ".DS_Store", "ORGANIZING-LOG.md"]

    for name, group in shipped_taxonomy["groups"].items():
        assert group["path_prefix"] == name
        assert isinstance(group["keywords"], list)
        assert isinstance(group["patterns"], list)

    for leaf in shipped_taxonomy["leaves"]:
        assert leaf["group"] == leaf["path"].split("/")[0]
        assert leaf["group"] in shipped_taxonomy["groups"]


def test_every_pattern_compiles(shipped_taxonomy: dict) -> None:
    for group in shipped_taxonomy["groups"].values():
        for pattern in group["patterns"]:
            re.compile(pattern)
    for leaf in shipped_taxonomy["leaves"]:
        for pattern in leaf["patterns"]:
            re.compile(pattern)


def test_duplicate_template_folders_are_rejected() -> None:
    with pytest.raises(ValueError, match="duplicate folders"):
        bt.build_seed_taxonomy(["Finance/Banking", "Finance/Banking"])


# --------------------------------------------------------------------------
# Curated overrides
# --------------------------------------------------------------------------


def test_curated_legal_leaves_beat_name_seeding(shipped_taxonomy: dict) -> None:
    leaves = {leaf["path"]: leaf for leaf in shipped_taxonomy["leaves"]}

    probate = leaves["LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/01_PROBATE_COURT"]
    assert "letters testamentary" in probate["keywords"]
    assert any("estate" in pattern for pattern in probate["patterns"])

    litigation = leaves["LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy"]
    assert {"motion", "exhibit"} <= set(litigation["keywords"])

    immunizations = leaves["Family Shared/Medical Records/Immunization Records"]
    assert "vaccination" in immunizations["keywords"]

    banking = leaves["Finance/Banking/Anna"]
    assert "bank statement" in banking["keywords"], "name-seeding would have produced only 'anna'"


def test_curated_group_rules_are_applied(shipped_taxonomy: dict) -> None:
    legal = shipped_taxonomy["groups"]["LEGAL_AND_ADVOCACY"]
    assert "cause no" in legal["keywords"]
    finance = shipped_taxonomy["groups"]["Finance"]
    assert "invoice" in finance["keywords"]


def test_overrides_for_absent_folders_never_invent_leaves(
    template_dirs: list[str], shipped_taxonomy: dict
) -> None:
    """Tuning for folders the template lacks stays dormant, preserving coverage."""
    orphans = bt.unmatched_override_paths(template_dirs)
    assert orphans, "expected curated tuning that pre-dates the folders it targets"

    shipped_paths = {leaf["path"] for leaf in shipped_taxonomy["leaves"]}
    assert shipped_paths.isdisjoint(orphans)


# --------------------------------------------------------------------------
# Versioned merge
# --------------------------------------------------------------------------


def _seed_from(paths: list[str]) -> dict:
    return bt.build_seed_taxonomy(paths)


def test_merge_preserves_user_edits_on_regeneration() -> None:
    seed = _seed_from(["Finance/Banking/Anna", "Home/Utilities/Water"])
    user = json.loads(json.dumps(seed))
    user["confidence_threshold"] = 0.8
    user["output_root"] = "~/Docs/Sorted"
    user["groups"]["Finance"]["keywords"] = ["my custom finance word"]
    banking = next(leaf for leaf in user["leaves"] if leaf["seed_path"] == "Finance/Banking/Anna")
    banking["path"] = "Money/Anna Bank"
    banking["keywords"] = ["frost bank", "checking"]

    # The template grows a folder; regenerate on top of the user's file.
    regenerated = bt.merge_taxonomy(
        user, _seed_from(["Finance/Banking/Anna", "Home/Utilities/Water", "Home/Utilities/Phone"])
    )

    assert regenerated["confidence_threshold"] == 0.8
    assert regenerated["output_root"] == "~/Docs/Sorted"
    assert regenerated["groups"]["Finance"]["keywords"] == ["my custom finance word"]

    merged_banking = next(
        leaf for leaf in regenerated["leaves"] if leaf["seed_path"] == "Finance/Banking/Anna"
    )
    assert merged_banking["path"] == "Money/Anna Bank"
    assert merged_banking["keywords"] == ["frost bank", "checking"]

    paths = [leaf["seed_path"] for leaf in regenerated["leaves"]]
    assert "Home/Utilities/Phone" in paths, "a new template folder must be added"
    assert len(paths) == len(set(paths)), "merging must not duplicate leaves"


def test_merge_adds_new_seed_fields_to_untouched_entries() -> None:
    seed = _seed_from(["Home/Utilities/Water"])
    user = {
        "version": 1,
        "groups": {"Home": {"keywords": ["homestead"]}},
        "leaves": [{"path": "Home/Utilities/Water", "keywords": ["water bill"]}],
    }

    merged = bt.merge_taxonomy(user, seed)

    assert merged["version"] == 2
    leaf = merged["leaves"][0]
    assert leaf["keywords"] == ["water bill"]
    assert leaf["group"] == "Home", "fields the user omitted come from the seed"
    assert merged["groups"]["Home"]["path_prefix"] == "Home"
    assert merged["groups"]["Home"]["keywords"] == ["homestead"]


def test_merge_keeps_user_authored_leaves_and_groups() -> None:
    seed = _seed_from(["Home/Utilities/Water"])
    user = json.loads(json.dumps(seed))
    user["groups"]["Sakura Case"] = {"path_prefix": "Sakura Case", "keywords": ["sakura"]}
    user["leaves"].append(
        {"path": "Sakura Case/Timeline", "group": "Sakura Case", "keywords": ["timeline"]}
    )

    merged = bt.merge_taxonomy(user, seed)

    assert "Sakura Case" in merged["groups"]
    assert any(leaf["path"] == "Sakura Case/Timeline" for leaf in merged["leaves"])


def test_generate_writes_and_then_merges(tmp_path: Path) -> None:
    script = tmp_path / "create_organized_folders.sh"
    script.write_text('dirs=(\n  "Finance/Banking/Anna"\n)\n', encoding="utf-8")
    output = tmp_path / "taxonomy.json"

    assert bt.main(["--script", str(script), "--output", str(output)]) == 0
    first = json.loads(output.read_text(encoding="utf-8"))
    assert [leaf["path"] for leaf in first["leaves"]] == ["Finance/Banking/Anna"]

    first["leaves"][0]["keywords"] = ["hand tuned"]
    output.write_text(bt.serialize(first), encoding="utf-8")

    assert bt.main(["--script", str(script), "--output", str(output)]) == 0
    second = json.loads(output.read_text(encoding="utf-8"))
    assert second["leaves"][0]["keywords"] == ["hand tuned"]


def test_no_merge_discards_user_edits(tmp_path: Path) -> None:
    script = tmp_path / "create_organized_folders.sh"
    script.write_text('dirs=(\n  "Finance/Banking/Anna"\n)\n', encoding="utf-8")
    output = tmp_path / "taxonomy.json"

    assert bt.main(["--script", str(script), "--output", str(output)]) == 0
    tampered = json.loads(output.read_text(encoding="utf-8"))
    tampered["leaves"][0]["keywords"] = ["hand tuned"]
    output.write_text(bt.serialize(tampered), encoding="utf-8")

    assert bt.main(["--script", str(script), "--output", str(output), "--no-merge"]) == 0
    rebuilt = json.loads(output.read_text(encoding="utf-8"))
    assert rebuilt["leaves"][0]["keywords"] == ["bank statement", "checking account", "deposit"]


def test_check_mode_flags_a_stale_file(tmp_path: Path) -> None:
    script = tmp_path / "create_organized_folders.sh"
    script.write_text('dirs=(\n  "Finance/Banking/Anna"\n)\n', encoding="utf-8")
    output = tmp_path / "taxonomy.json"

    assert bt.main(["--script", str(script), "--output", str(output), "--check"]) == 1
    assert not output.exists()

    assert bt.main(["--script", str(script), "--output", str(output)]) == 0
    assert bt.main(["--script", str(script), "--output", str(output), "--check"]) == 0


def test_builder_touches_no_network(block_network: None, tmp_path: Path) -> None:
    script = tmp_path / "create_organized_folders.sh"
    script.write_text('dirs=(\n  "Finance/Banking/Anna"\n)\n', encoding="utf-8")
    assert bt.main(["--script", str(script), "--output", str(tmp_path / "taxonomy.json")]) == 0
