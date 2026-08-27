"""Two-stage (group, then leaf) confidence scoring against the taxonomy.

Spec contract: score every group first — using the group's own rules
plus the aggregate signal of its leaves — and pick a winner; then
score only the leaves inside that winning group and pick the best.
Deterministic only: weighted keyword hits and regex matches. No ML, no
model downloads, no network I/O. A file auto-files only when both the
group and leaf confidence clear their configured thresholds.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ClassificationResult:
    """The winning group/leaf pick and the confidence that produced it."""

    group: str
    leaf_path: str
    group_confidence: float
    leaf_confidence: float


def classify(text: str, taxonomy: dict[str, object]) -> ClassificationResult:
    """Return the best-matching leaf path and its two-stage confidence.

    Raises `NotImplementedError` until scoring ships in a later PR.
    """
    raise NotImplementedError(
        "classify is implemented in a later PR; see the spec's classify.py contract"
    )
