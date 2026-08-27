"""Tests for the deterministic two-stage classifier."""

from __future__ import annotations

import pytest

from organized_docs.classify import (
    REVIEW_LOW_GROUP,
    REVIEW_LOW_LEAF,
    REVIEW_NO_MATCH,
    REVIEW_NO_TEXT,
    RuleHit,
    Score,
    Weights,
    classify,
    normalize,
    rank,
    score_groups,
    score_leaves,
    score_rules,
    stage_confidence,
)
from organized_docs.taxonomy import Taxonomy, compile_taxonomy, load_taxonomy


@pytest.fixture(scope="module")
def taxonomy() -> Taxonomy:
    return load_taxonomy()


def tiny_taxonomy() -> Taxonomy:
    """A compact taxonomy designed to isolate scoring behavior."""
    return compile_taxonomy(
        {
            "version": 2,
            "group_threshold": 0.5,
            "confidence_threshold": 0.6,
            "group_saturation": 4.0,
            "leaf_saturation": 3.0,
            "groups": {
                "Alpha": {"path_prefix": "Alpha", "keywords": ["alpha"], "patterns": []},
                "Beta": {"path_prefix": "Beta", "keywords": ["beta"], "patterns": []},
                "Gamma": {"path_prefix": "Gamma", "keywords": [], "patterns": []},
            },
            "leaves": [
                {
                    "path": "Alpha/One",
                    "seed_path": "Alpha/One",
                    "group": "Alpha",
                    "keywords": ["shared", "one"],
                    "patterns": ["serial\\s+one"],
                },
                {
                    "path": "Alpha/Two",
                    "seed_path": "Alpha/Two",
                    "group": "Alpha",
                    "keywords": ["shared", "two"],
                    "patterns": [],
                },
                {
                    "path": "Beta/One",
                    "seed_path": "Beta/One",
                    "group": "Beta",
                    "keywords": ["shared"],
                    "patterns": [],
                },
                {
                    "path": "Gamma/Empty",
                    "seed_path": "Gamma/Empty",
                    "group": "Gamma",
                    "keywords": [],
                    "patterns": [],
                },
            ],
        }
    )


# --------------------------------------------------------------------------
# Primitive scoring and normalization
# --------------------------------------------------------------------------


def test_keyword_hits_add_weight_once_even_if_repeated() -> None:
    taxonomy = tiny_taxonomy()
    leaf = taxonomy.leaf("Alpha/One")
    assert leaf is not None

    raw, hits = score_rules(
        "alpha shared shared serial one serial one",
        leaf.rules,
        Weights(keyword=1.0, pattern=2.0, path=0.5),
    )

    assert raw == 4.5  # keywords shared + one, regex once, path term alpha
    assert [hit.kind for hit in hits] == ["keyword", "keyword", "pattern", "path"]
    assert hits.count(RuleHit("pattern", "serial\\s+one")) == 1


@pytest.mark.parametrize(
    ("raw", "saturation", "expected"),
    [(0, 3, 0), (1.5, 3, 0.5), (3, 3, 1), (99, 3, 1), (-1, 3, 0)],
)
def test_normalize_clamps_to_zero_through_one(
    raw: float, saturation: float, expected: float
) -> None:
    assert normalize(raw, saturation) == expected


def test_patterns_are_weighted_twice_keywords_by_default() -> None:
    taxonomy = tiny_taxonomy()
    leaf = taxonomy.leaf("Alpha/One")
    assert leaf is not None

    raw, hits = score_rules("serial one", leaf.rules, Weights())

    assert raw == 3.0  # keyword "one" scores 1.0, the regex scores 2.0
    assert {hit.kind for hit in hits} == {"keyword", "pattern"}


def test_rank_breaks_ties_toward_more_distinct_rule_types() -> None:
    ranked = rank(
        [
            Score("keyword-only", raw=2.0, hits=(RuleHit("keyword", "a"),)),
            Score(
                "keyword-and-pattern",
                raw=2.0,
                hits=(RuleHit("keyword", "a"), RuleHit("pattern", "a+")),
            ),
        ]
    )

    assert ranked[0].candidate == "keyword-and-pattern"


def test_rank_falls_back_to_the_candidate_name_for_full_ties() -> None:
    ranked = rank(
        [
            Score("Zulu", raw=2.0, hits=(RuleHit("keyword", "a"),)),
            Score("Alfa", raw=2.0, hits=(RuleHit("keyword", "b"),)),
        ]
    )

    assert [score.candidate for score in ranked] == ["Alfa", "Zulu"]


def test_stage_confidence_is_discounted_for_an_indistinguishable_tie() -> None:
    best = Score("A", raw=2.0, hits=(RuleHit("keyword", "same"),))
    runner_up = Score("B", raw=2.0, hits=(RuleHit("keyword", "other"),))

    assert stage_confidence(best, runner_up, saturation=4.0) == 0.25


def test_stage_confidence_is_not_discounted_when_rule_types_break_the_tie() -> None:
    best = Score("A", raw=2.0, hits=(RuleHit("keyword", "a"), RuleHit("path", "a")))
    runner_up = Score("B", raw=2.0, hits=(RuleHit("keyword", "b"),))

    assert stage_confidence(best, runner_up, saturation=4.0) == 0.5


# --------------------------------------------------------------------------
# Stage behavior
# --------------------------------------------------------------------------


def test_stage_one_scores_group_rules_plus_strongest_leaf_signal() -> None:
    taxonomy = tiny_taxonomy()
    leaf_scores = score_leaves("alpha serial one", taxonomy.leaves, Weights())
    group_scores = {
        score.candidate: score
        for score in score_groups("alpha serial one", taxonomy, leaf_scores, Weights())
    }

    assert group_scores["Alpha"].raw == 4.5  # group keyword 1.0 + strongest Alpha leaf 3.5
    assert group_scores["Beta"].raw == 0.0, "a group with no signal contributes nothing"
    assert group_scores["Gamma"].raw == 0.0


def test_stage_two_only_scores_leaves_inside_the_winning_group() -> None:
    """`Beta/One` matches "shared" too, but it is never eligible once Alpha wins."""
    result = classify("alpha shared serial one beta", tiny_taxonomy(), leaf_threshold=0.0)

    assert result.group == "Alpha"
    assert result.leaf_path == "Alpha/One"
    assert result.leaf_runner_up == "Alpha/Two", "the runner-up stays inside the winning group"


def test_group_threshold_must_pass_before_a_leaf_can_file() -> None:
    result = classify("shared", tiny_taxonomy(), group_threshold=0.9, leaf_threshold=0.0)

    assert result.needs_review is True
    assert result.reason == REVIEW_LOW_GROUP
    assert result.group is not None
    assert result.leaf_path is None


def test_leaf_threshold_must_pass_after_the_group_threshold() -> None:
    result = classify("alpha one", tiny_taxonomy(), group_threshold=0.0, leaf_threshold=0.95)

    assert result.needs_review is True
    assert result.reason == REVIEW_LOW_LEAF
    assert result.group == "Alpha"
    assert result.leaf_path == "Alpha/One"
    assert 0 < result.leaf_confidence < 0.95


def test_thresholds_default_from_taxonomy_config() -> None:
    taxonomy = compile_taxonomy(
        {
            "version": 2,
            "group_threshold": 0.1,
            "confidence_threshold": 0.95,
            "groups": {"Alpha": {"path_prefix": "Alpha", "keywords": ["alpha"], "patterns": []}},
            "leaves": [
                {
                    "path": "Alpha/One",
                    "seed_path": "Alpha/One",
                    "group": "Alpha",
                    "keywords": ["one"],
                    "patterns": [],
                }
            ],
        }
    )

    result = classify("alpha one", taxonomy)

    assert result.reason == REVIEW_LOW_LEAF


def test_no_text_routes_to_review() -> None:
    result = classify("   ", tiny_taxonomy())

    assert result.needs_review is True
    assert result.reason == REVIEW_NO_TEXT
    assert result.group is None
    assert result.leaf_path is None


def test_no_matching_rule_routes_to_review() -> None:
    result = classify("plain unrelated prose", tiny_taxonomy())

    assert result.needs_review is True
    assert result.reason == REVIEW_NO_MATCH


def test_leaf_with_no_rules_at_all_can_never_win() -> None:
    """A bare `Misc` folder has no keywords, no patterns, and no usable path terms."""
    taxonomy = compile_taxonomy(
        {
            "version": 2,
            "groups": {"Zed": {"path_prefix": "Zed", "keywords": ["zed"], "patterns": []}},
            "leaves": [
                {
                    "path": "Zed/Misc",
                    "seed_path": "Zed/Misc",
                    "group": "Zed",
                    "keywords": [],
                    "patterns": [],
                }
            ],
        }
    )
    leaf = taxonomy.leaf("Zed/Misc")
    assert leaf is not None and leaf.rules.path_terms == ()

    result = classify("zed", taxonomy, group_threshold=0.0, leaf_threshold=0.0)

    assert result.needs_review is True
    assert result.reason == REVIEW_NO_MATCH
    assert result.group == "Zed"
    assert result.leaf_path is None


# --------------------------------------------------------------------------
# Full built-in taxonomy fixtures
# --------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("name", "text", "expected_leaf"),
    [
        (
            "motion",
            """IN THE 419TH JUDICIAL DISTRICT COURT OF TRAVIS COUNTY, TEXAS
            Cause No. D-1-GN-24-001234
            PLAINTIFF'S MOTION TO COMPEL DISCOVERY
            Movant respectfully requests relief. Notice of hearing is attached.
            Exhibit A is an affidavit. This litigation pleading concerns discovery.""",
            "LEGAL_AND_ADVOCACY/00_TEMPLATES/Standard_Litigation_Folder_Hierarchy",
        ),
        (
            "bank statement",
            """FIRST TEXAS BANK - Bank Statement for Anna
            Statement period: June 1 - June 30, 2024.
            Checking account 1234. Beginning balance $1,204.55.
            Deposits totaling $3,000.00.""",
            "Finance/Banking/Anna",
        ),
        (
            "immunization record",
            """Austin Pediatrics Immunization Record.
            Patient shot record with vaccinations administered: DTaP, MMR, HepB, Varicella.""",
            "Family Shared/Medical Records/Immunization Records",
        ),
        (
            "probate filing",
            """Probate Court No. 1 of Travis County.
            In re: Estate of John Doe, deceased. Letters testamentary issued to the executor.
            Cause no. C-1-PB-24-000999.""",
            "LEGAL_AND_ADVOCACY/01_TRAVIS_COUNTY_AND_LOCAL/01_PROBATE_COURT",
        ),
        (
            "w2",
            """2024 Form W-2 Wage and Tax Statement.
            Employer identification number 74-1234567. Federal income tax withheld.""",
            "Finance/Tax Documents/W2 & 1099 Forms",
        ),
        (
            "birth certificate",
            """Certificate of Live Birth issued by the registrar.
            Birth Certificate for county vital records.""",
            "Family Shared/Identification/Birth Certificate",
        ),
        (
            "eeoc",
            """U.S. Equal Employment Opportunity Commission (EEOC).
            Charge of discrimination. Notice of Right-to-Sue issued by EEOC.""",
            "LEGAL_AND_ADVOCACY/03_FEDERAL_AND_SOVEREIGN/03.7_EEOC",
        ),
    ],
)
def test_fixture_documents_land_on_exact_deep_leaf(
    taxonomy: Taxonomy, name: str, text: str, expected_leaf: str
) -> None:
    result = classify(text, taxonomy)

    assert result.needs_review is False, name
    assert result.leaf_path == expected_leaf
    assert result.group_confidence >= taxonomy.group_threshold
    assert result.leaf_confidence >= taxonomy.confidence_threshold


def test_below_threshold_builtin_document_returns_review_with_best_guess(
    taxonomy: Taxonomy,
) -> None:
    result = classify("invoice", taxonomy)

    assert result.needs_review is True
    assert result.reason in {REVIEW_LOW_GROUP, REVIEW_LOW_LEAF}
    assert result.group is not None, "review still records the best guess it had"
    assert result.matched_rules, "and the rules behind it, so triage needs no reopening"


def test_two_words_are_not_enough_to_file_a_real_document(taxonomy: Taxonomy) -> None:
    """ "bank statement" alone is a phrase, not a document — it must not auto-file."""
    result = classify("bank statement", taxonomy)

    assert result.needs_review is True
    assert result.reason == REVIEW_LOW_GROUP
    assert result.leaf_path is None


def test_classification_opens_no_socket(block_network: None) -> None:
    """The privacy invariant: loading and classifying touch local disk only."""
    result = classify(
        "Bank Statement for Anna. Statement period. Checking account. Deposit.",
        load_taxonomy(),
    )

    assert result.leaf_path == "Finance/Banking/Anna"


def test_classification_is_deterministic(taxonomy: Taxonomy) -> None:
    text = "Bank Statement for Anna. Statement period. Checking account. Deposit."
    first = classify(text, taxonomy)

    assert [classify(text, taxonomy) for _ in range(5)] == [first] * 5
