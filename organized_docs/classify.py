"""Two-stage (group, then leaf) confidence scoring against the taxonomy.

Spec contract: score every group first — using the group's own rules
plus the aggregate signal of its leaves — and pick a winner; then
score only the leaves inside that winning group and pick the best.
Deterministic only: weighted keyword hits and regex matches. No ML, no
model downloads, no network I/O. A file auto-files only when both the
group and leaf confidence clear their configured thresholds.

The scoring model, in one place:

- A rule hit adds weight: keyword 1.0, regex 2.0, folder-name term 0.5
  (all configurable in `taxonomy.json`). Each rule counts once no matter
  how often it appears, so a repeated word cannot manufacture confidence.
- A group's raw score is its own rule hits plus the raw score of its
  strongest leaf: the aggregate signal of what the group actually holds.
- Confidence normalizes a raw score against a saturation point (group 4.0,
  leaf 3.0) and clamps to 0-1, which keeps scores comparable across
  documents and across candidates with wildly different numbers of rules.
- Ties break toward the candidate matching more distinct rule types, then
  by path for determinism. A tie that survives both — same score, same
  rule types — is a coin flip, so its confidence is halved and the
  document lands in review rather than in an arbitrary folder.
- A candidate with no rules scores 0 and can never win.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass

from .taxonomy import Leaf, RuleSet, Taxonomy

#: Confidence multiplier applied when the top two candidates are
#: indistinguishable — same raw score and same distinct rule types.
TIE_DISCOUNT = 0.5

REVIEW_NO_TEXT = "no extractable text"
REVIEW_NO_MATCH = "no taxonomy rule matched"
REVIEW_LOW_GROUP = "group confidence below threshold"
REVIEW_LOW_LEAF = "leaf confidence below threshold"


@dataclass(frozen=True)
class Weights:
    """Per-rule-type score weights, read from the taxonomy."""

    keyword: float = 1.0
    pattern: float = 2.0
    path: float = 0.5

    @classmethod
    def from_taxonomy(cls, taxonomy: Taxonomy) -> Weights:
        return cls(
            keyword=taxonomy.keyword_weight,
            pattern=taxonomy.pattern_weight,
            path=taxonomy.path_weight,
        )


@dataclass(frozen=True)
class RuleHit:
    """One rule that fired, kept so the report can explain a decision."""

    kind: str  # "keyword" | "pattern" | "path"
    rule: str


@dataclass(frozen=True)
class Score:
    """What one candidate (group or leaf) scored against a document."""

    candidate: str
    raw: float
    hits: tuple[RuleHit, ...] = ()

    @property
    def rule_types(self) -> int:
        """How many distinct kinds of rule fired — the primary tie-break."""
        return len({hit.kind for hit in self.hits})

    @property
    def matched_rules(self) -> tuple[str, ...]:
        return tuple(hit.rule for hit in self.hits)


@dataclass(frozen=True)
class ClassificationResult:
    """The winning group/leaf pick and the confidence that produced it."""

    group: str | None
    leaf_path: str | None
    group_confidence: float
    leaf_confidence: float
    needs_review: bool = False
    reason: str = ""
    matched_rules: tuple[str, ...] = ()
    group_runner_up: str | None = None
    leaf_runner_up: str | None = None

    @property
    def is_filed(self) -> bool:
        """True when both stages cleared their thresholds."""
        return not self.needs_review


# --------------------------------------------------------------------------
# Scoring
# --------------------------------------------------------------------------


def score_rules(text: str, rules: RuleSet, weights: Weights) -> tuple[float, tuple[RuleHit, ...]]:
    """Score `text` against one rule set: total weight and the rules that fired.

    Pure: no I/O, no state, and the same text always produces the same score.
    """
    hits: list[RuleHit] = []
    raw = 0.0
    for keyword, matcher in zip(rules.keywords, rules.keyword_matchers):
        if matcher.search(text):
            hits.append(RuleHit("keyword", keyword))
            raw += weights.keyword
    for pattern, matcher in zip(rules.patterns, rules.pattern_matchers):
        if matcher.search(text):
            hits.append(RuleHit("pattern", pattern))
            raw += weights.pattern
    for term, matcher in zip(rules.path_terms, rules.path_matchers):
        if matcher.search(text):
            hits.append(RuleHit("path", term))
            raw += weights.path
    return raw, tuple(hits)


def rank(scores: Iterable[Score]) -> list[Score]:
    """Order candidates best-first: score, then distinct rule types, then name."""
    return sorted(scores, key=lambda s: (-s.raw, -s.rule_types, s.candidate))


def indistinguishable(best: Score, runner_up: Score | None) -> bool:
    """True when the runner-up matched exactly as strongly and as broadly."""
    if runner_up is None:
        return False
    return best.raw == runner_up.raw and best.rule_types == runner_up.rule_types


def normalize(raw: float, saturation: float) -> float:
    """Map a raw score onto 0-1, saturating at `saturation`."""
    if raw <= 0 or saturation <= 0:
        return 0.0
    return min(1.0, raw / saturation)


def stage_confidence(best: Score, runner_up: Score | None, saturation: float) -> float:
    """Normalized 0-1 confidence for a stage winner, discounted on a true tie."""
    confidence = normalize(best.raw, saturation)
    if indistinguishable(best, runner_up):
        confidence *= TIE_DISCOUNT
    return round(confidence, 6)


def score_leaves(text: str, leaves: Sequence[Leaf], weights: Weights) -> dict[str, Score]:
    """Score every leaf once, keyed by leaf path."""
    scored: dict[str, Score] = {}
    for leaf in leaves:
        raw, hits = score_rules(text, leaf.rules, weights)
        scored[leaf.path] = Score(candidate=leaf.path, raw=raw, hits=hits)
    return scored


def score_groups(
    text: str,
    taxonomy: Taxonomy,
    leaf_scores: dict[str, Score],
    weights: Weights,
) -> list[Score]:
    """Stage 1: score each group by its own rules plus its strongest leaf.

    Using the strongest leaf rather than a sum keeps a group with 60 weakly
    matching leaves from outscoring a group holding the one folder that
    actually describes the document.
    """
    scores: list[Score] = []
    for name, group in taxonomy.groups.items():
        own_raw, own_hits = score_rules(text, group.rules, weights)
        leaves = taxonomy.leaves_for(name)
        best_leaf = max(
            (leaf_scores[leaf.path] for leaf in leaves),
            key=lambda s: (s.raw, s.rule_types),
            default=None,
        )
        leaf_raw = best_leaf.raw if best_leaf else 0.0
        leaf_hits = best_leaf.hits if best_leaf else ()
        scores.append(Score(candidate=name, raw=own_raw + leaf_raw, hits=own_hits + leaf_hits))
    return scores


# --------------------------------------------------------------------------
# Classification
# --------------------------------------------------------------------------


def _review(
    reason: str,
    group: str | None = None,
    leaf_path: str | None = None,
    group_confidence: float = 0.0,
    leaf_confidence: float = 0.0,
    matched_rules: tuple[str, ...] = (),
    group_runner_up: str | None = None,
    leaf_runner_up: str | None = None,
) -> ClassificationResult:
    """A review-routed result that still carries the best guess it had."""
    return ClassificationResult(
        group=group,
        leaf_path=leaf_path,
        group_confidence=group_confidence,
        leaf_confidence=leaf_confidence,
        needs_review=True,
        reason=reason,
        matched_rules=matched_rules,
        group_runner_up=group_runner_up,
        leaf_runner_up=leaf_runner_up,
    )


def classify(
    text: str,
    taxonomy: Taxonomy,
    group_threshold: float | None = None,
    leaf_threshold: float | None = None,
) -> ClassificationResult:
    """Return the best-matching leaf path and its two-stage confidence.

    Thresholds default to the taxonomy's (`group_threshold` 0.5,
    `confidence_threshold` 0.6) and are overridable per run — the CLI's
    `--threshold` flag maps to `leaf_threshold`. A result with
    `needs_review=True` still names its best guess and the rules behind it,
    so the review queue in the report is triageable without reopening the
    document.
    """
    group_min = taxonomy.group_threshold if group_threshold is None else group_threshold
    leaf_min = taxonomy.confidence_threshold if leaf_threshold is None else leaf_threshold

    if not text or not text.strip():
        return _review(REVIEW_NO_TEXT)

    weights = Weights.from_taxonomy(taxonomy)
    leaf_scores = score_leaves(text, taxonomy.leaves, weights)
    group_ranking = rank(score_groups(text, taxonomy, leaf_scores, weights))

    if not group_ranking or group_ranking[0].raw <= 0:
        return _review(REVIEW_NO_MATCH)

    best_group = group_ranking[0]
    group_runner_up = group_ranking[1] if len(group_ranking) > 1 else None
    group_confidence = stage_confidence(best_group, group_runner_up, taxonomy.group_saturation)
    runner_up_name = group_runner_up.candidate if group_runner_up else None

    if group_confidence < group_min:
        return _review(
            REVIEW_LOW_GROUP,
            group=best_group.candidate,
            group_confidence=group_confidence,
            matched_rules=best_group.matched_rules,
            group_runner_up=runner_up_name,
        )

    leaf_ranking = rank(
        leaf_scores[leaf.path] for leaf in taxonomy.leaves_for(best_group.candidate)
    )
    if not leaf_ranking or leaf_ranking[0].raw <= 0:
        return _review(
            REVIEW_NO_MATCH,
            group=best_group.candidate,
            group_confidence=group_confidence,
            matched_rules=best_group.matched_rules,
            group_runner_up=runner_up_name,
        )

    best_leaf = leaf_ranking[0]
    leaf_runner_up = leaf_ranking[1] if len(leaf_ranking) > 1 else None
    leaf_confidence = stage_confidence(best_leaf, leaf_runner_up, taxonomy.leaf_saturation)
    leaf_runner_up_path = leaf_runner_up.candidate if leaf_runner_up else None

    if leaf_confidence < leaf_min:
        return _review(
            REVIEW_LOW_LEAF,
            group=best_group.candidate,
            leaf_path=best_leaf.candidate,
            group_confidence=group_confidence,
            leaf_confidence=leaf_confidence,
            matched_rules=best_leaf.matched_rules,
            group_runner_up=runner_up_name,
            leaf_runner_up=leaf_runner_up_path,
        )

    return ClassificationResult(
        group=best_group.candidate,
        leaf_path=best_leaf.candidate,
        group_confidence=group_confidence,
        leaf_confidence=leaf_confidence,
        matched_rules=best_leaf.matched_rules,
        group_runner_up=runner_up_name,
        leaf_runner_up=leaf_runner_up_path,
    )
