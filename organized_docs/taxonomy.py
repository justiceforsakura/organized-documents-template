"""Taxonomy loading, merging, and validation.

Spec contract: load the built-in taxonomy plus an optional user
`--config` JSON override, perform a versioned merge in which user
edits win, and validate that every leaf path is unique and every
leaf's `group` exists among the defined groups. Regenerating the seed
taxonomy from `create_organized_folders.sh` must never clobber a
user's edits.

Everything here is local: the built-in taxonomy ships inside the
package as `data/taxonomy.json` and a user config is read from disk.
No network I/O, ever.
"""

from __future__ import annotations

import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from re import Pattern
from typing import Any

DATA_DIR = Path(__file__).resolve().parent / "data"
BUILTIN_TAXONOMY_PATH = DATA_DIR / "taxonomy.json"

#: Schema versions this loader understands. `scripts/build_taxonomy.py`
#: writes version 2; a newer file means the package is older than its data.
SUPPORTED_VERSIONS = (2,)

#: Settings a user config may override wholesale (everything except the
#: generated `groups`/`leaves` structures, which are merged entry by entry).
SETTING_KEYS = (
    "output_root",
    "review_folder",
    "group_threshold",
    "confidence_threshold",
    "keyword_weight",
    "pattern_weight",
    "path_weight",
    "group_saturation",
    "leaf_saturation",
    "ignore",
)

DEFAULT_SETTINGS: dict[str, Any] = {
    "output_root": "~/Documents/Organized Documents",
    "review_folder": "_Needs Review",
    "group_threshold": 0.5,
    "confidence_threshold": 0.6,
    # Scoring weights. A regex hit is worth twice a keyword hit because a
    # pattern encodes structure ("cause no. 1234"), not just vocabulary.
    "keyword_weight": 1.0,
    "pattern_weight": 2.0,
    # A leaf's own folder-name words are weak corroborating evidence: enough
    # to separate sibling folders ("Banking/Anna" vs "Banking/Sakura"), not
    # enough to carry a destination on their own.
    "path_weight": 0.5,
    # Raw score at which a stage is considered fully evidenced. Scores
    # saturate here so confidence stays comparable across documents and
    # across candidates with very different numbers of rules.
    "group_saturation": 4.0,
    "leaf_saturation": 3.0,
    "ignore": ["~$", ".DS_Store", "ORGANIZING-LOG.md"],
}

_THRESHOLD_KEYS = ("group_threshold", "confidence_threshold")
_POSITIVE_KEYS = (
    "keyword_weight",
    "pattern_weight",
    "path_weight",
    "group_saturation",
    "leaf_saturation",
)

#: Path segments too generic to be worth matching as folder-name evidence.
_GENERIC_SEGMENTS = frozenset(
    {
        "templates",
        "documents",
        "records",
        "files",
        "misc",
        "miscellaneous",
        "other",
        "shared",
        "info",
        "information",
    }
)

_MIN_PATH_TERM_LENGTH = 4


class TaxonomyError(ValueError):
    """Raised when a taxonomy file is malformed or internally inconsistent."""


# --------------------------------------------------------------------------
# Rule matching
# --------------------------------------------------------------------------


def keyword_regex(keyword: str) -> Pattern[str]:
    """Compile a keyword into a whole-word, stem-tolerant matcher.

    Whitespace inside a phrase matches any run of whitespace, and up to three
    trailing word characters are allowed so `deposit` matches "deposits" and
    `atmo` matches "Atmos" — while `att` still refuses to match "attached".
    """
    parts = [re.escape(part) for part in keyword.split()]
    if not parts:
        raise TaxonomyError("keyword must not be blank")
    return re.compile(r"\b" + r"\s+".join(parts) + r"\w{0,3}\b", re.IGNORECASE)


def _compile_pattern(pattern: str, where: str) -> Pattern[str]:
    try:
        return re.compile(pattern, re.IGNORECASE)
    except re.error as exc:
        raise TaxonomyError(f"{where}: invalid regex {pattern!r} ({exc})") from exc


@dataclass(frozen=True)
class RuleSet:
    """The compiled keyword, regex, and folder-name rules of one candidate."""

    keywords: tuple[str, ...] = ()
    patterns: tuple[str, ...] = ()
    path_terms: tuple[str, ...] = ()
    keyword_matchers: tuple[Pattern[str], ...] = ()
    pattern_matchers: tuple[Pattern[str], ...] = ()
    path_matchers: tuple[Pattern[str], ...] = ()

    @classmethod
    def build(
        cls,
        keywords: Sequence[str],
        patterns: Sequence[str],
        path_terms: Sequence[str] = (),
        where: str = "taxonomy",
    ) -> RuleSet:
        """Compile a rule set, raising `TaxonomyError` on an unusable rule."""
        keywords = tuple(str(word).strip().lower() for word in keywords if str(word).strip())
        patterns = tuple(str(pattern) for pattern in patterns if str(pattern).strip())
        path_terms = tuple(path_terms)
        return cls(
            keywords=keywords,
            patterns=patterns,
            path_terms=path_terms,
            keyword_matchers=tuple(keyword_regex(word) for word in keywords),
            pattern_matchers=tuple(_compile_pattern(p, where) for p in patterns),
            path_matchers=tuple(keyword_regex(term) for term in path_terms),
        )

    @property
    def is_empty(self) -> bool:
        """True when the candidate has no rules and so can never win a stage."""
        return not (self.keywords or self.patterns or self.path_terms)


def path_terms_for(leaf_path: str) -> tuple[str, ...]:
    """Folder-name terms worth matching for a leaf, in path order.

    Numeric ordering prefixes (`01_`, `02.3_`), separators, and generic
    segments are stripped; what remains is the human-meaningful part of the
    folder name.
    """
    terms: list[str] = []
    for segment in leaf_path.split("/"):
        normalized = re.sub(r"[^a-z0-9]+", " ", segment.lower())
        normalized = re.sub(r"\b\d[\d.]*\b", " ", normalized)
        normalized = " ".join(normalized.split())
        if len(normalized) < _MIN_PATH_TERM_LENGTH or normalized in _GENERIC_SEGMENTS:
            continue
        if normalized not in terms:
            terms.append(normalized)
    return tuple(terms)


# --------------------------------------------------------------------------
# Taxonomy model
# --------------------------------------------------------------------------


@dataclass(frozen=True)
class Group:
    """A top-level destination group and the rules that select it."""

    name: str
    path_prefix: str
    rules: RuleSet


@dataclass(frozen=True)
class Leaf:
    """One destination folder, deep in the tree, and the rules that select it."""

    path: str
    group: str
    rules: RuleSet
    seed_path: str = ""


@dataclass(frozen=True)
class Taxonomy:
    """A validated, compiled taxonomy ready for classification."""

    version: int
    output_root: str
    review_folder: str
    group_threshold: float
    confidence_threshold: float
    keyword_weight: float
    pattern_weight: float
    path_weight: float
    group_saturation: float
    leaf_saturation: float
    groups: Mapping[str, Group]
    leaves: tuple[Leaf, ...]
    ignore: tuple[str, ...]
    _leaves_by_group: Mapping[str, tuple[Leaf, ...]] = field(repr=False, default_factory=dict)

    def leaves_for(self, group: str) -> tuple[Leaf, ...]:
        """Every leaf belonging to `group`, in taxonomy order."""
        return self._leaves_by_group.get(group, ())

    def leaf(self, path: str) -> Leaf | None:
        """The leaf at `path`, or None when the taxonomy has no such folder."""
        for leaf in self.leaves:
            if leaf.path == path:
                return leaf
        return None


# --------------------------------------------------------------------------
# Merging
# --------------------------------------------------------------------------


def _merge_entry(base: Mapping[str, Any], overlay: Mapping[str, Any] | None) -> dict[str, Any]:
    """Overlay one entry on its base; every field the overlay sets wins."""
    merged = dict(base)
    if overlay:
        merged.update(overlay)
    return merged


def _leaf_identity(leaf: Mapping[str, Any]) -> str:
    """The template folder a leaf came from, even after a user retargets it."""
    return str(leaf.get("seed_path") or leaf.get("path") or "")


def merge_taxonomies(builtin: Mapping[str, Any], user: Mapping[str, Any]) -> dict[str, Any]:
    """Merge a user config over the built-in taxonomy; user edits win.

    Mirrors the regeneration merge in `scripts/build_taxonomy.py`, with the
    roles inverted: there a fresh seed is merged under an existing user file,
    here a user file is merged over the shipped seed. Both keep the same
    promise — a user's tuning survives a template update.

    - Settings the user set (thresholds, weights, output root, ignore list) win.
    - Groups and leaves present in both keep every field the user set and
      inherit the fields the user omitted.
    - Leaves are matched on `seed_path`, so a leaf the user retargeted to a
      custom `path` is updated in place instead of duplicated.
    - Groups and leaves the user added by hand are appended untouched.
    """
    merged: dict[str, Any] = dict(builtin)

    for key, value in user.items():
        if key in SETTING_KEYS or key not in merged:
            merged[key] = value

    builtin_groups: Mapping[str, Any] = builtin.get("groups", {}) or {}
    user_groups: Mapping[str, Any] = user.get("groups", {}) or {}
    groups = {
        name: _merge_entry(entry, user_groups.get(name)) for name, entry in builtin_groups.items()
    }
    for name, entry in user_groups.items():
        groups.setdefault(name, entry)
    merged["groups"] = groups

    user_leaves = {_leaf_identity(leaf): leaf for leaf in user.get("leaves", []) or []}
    leaves: list[dict[str, Any]] = []
    for leaf in builtin.get("leaves", []) or []:
        leaves.append(_merge_entry(leaf, user_leaves.pop(_leaf_identity(leaf), None)))
    leaves.extend(dict(leaf) for leaf in user_leaves.values())
    merged["leaves"] = leaves

    return merged


# --------------------------------------------------------------------------
# Validation and compilation
# --------------------------------------------------------------------------


def _setting(data: Mapping[str, Any], key: str, problems: list[str]) -> Any:
    value = data.get(key, DEFAULT_SETTINGS[key])
    if key in _THRESHOLD_KEYS or key in _POSITIVE_KEYS:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            problems.append(f"{key} must be a number, got {value!r}")
            return DEFAULT_SETTINGS[key]
        if key in _THRESHOLD_KEYS and not 0.0 <= float(value) <= 1.0:
            problems.append(f"{key} must be between 0 and 1, got {value!r}")
            return DEFAULT_SETTINGS[key]
        if key in _POSITIVE_KEYS and float(value) <= 0:
            problems.append(f"{key} must be greater than 0, got {value!r}")
            return DEFAULT_SETTINGS[key]
        return float(value)
    return value


def _build_rules(
    entry: Mapping[str, Any],
    where: str,
    problems: list[str],
    path_terms: Sequence[str] = (),
) -> RuleSet:
    keywords = entry.get("keywords", []) or []
    patterns = entry.get("patterns", []) or []
    if not isinstance(keywords, list) or not isinstance(patterns, list):
        problems.append(f"{where}: keywords and patterns must be lists")
        return RuleSet()
    try:
        return RuleSet.build(keywords, patterns, path_terms, where=where)
    except TaxonomyError as exc:
        problems.append(str(exc))
        return RuleSet()


def compile_taxonomy(data: Mapping[str, Any]) -> Taxonomy:
    """Validate a taxonomy mapping and compile it into a `Taxonomy`.

    Every problem found is reported together rather than one per run: a
    hand-edited config usually has more than one typo in it.
    """
    if not isinstance(data, Mapping):
        raise TaxonomyError("taxonomy must be a JSON object")

    problems: list[str] = []

    version = data.get("version", SUPPORTED_VERSIONS[-1])
    if version not in SUPPORTED_VERSIONS:
        raise TaxonomyError(
            f"unsupported taxonomy version {version!r}; this build understands "
            f"{', '.join(str(v) for v in SUPPORTED_VERSIONS)}"
        )

    settings = {key: _setting(data, key, problems) for key in SETTING_KEYS}

    raw_groups = data.get("groups", {})
    groups: dict[str, Group] = {}
    if not isinstance(raw_groups, Mapping):
        problems.append("groups must be a JSON object keyed by group name")
        raw_groups = {}
    for name, entry in raw_groups.items():
        if not isinstance(entry, Mapping):
            problems.append(f"group {name!r} must be a JSON object")
            continue
        groups[name] = Group(
            name=name,
            path_prefix=str(entry.get("path_prefix", name)),
            rules=_build_rules(entry, f"group {name!r}", problems),
        )

    raw_leaves = data.get("leaves", [])
    if not isinstance(raw_leaves, list):
        problems.append("leaves must be a JSON array")
        raw_leaves = []

    leaves: list[Leaf] = []
    seen: dict[str, int] = {}
    duplicates: list[str] = []
    for index, entry in enumerate(raw_leaves):
        if not isinstance(entry, Mapping):
            problems.append(f"leaf #{index} must be a JSON object")
            continue
        path = str(entry.get("path", "")).strip()
        if not path:
            problems.append(f"leaf #{index} is missing a non-empty 'path'")
            continue
        if path in seen:
            duplicates.append(path)
            continue
        seen[path] = index
        group_name = str(entry.get("group", "")).strip()
        if not group_name:
            problems.append(f"leaf {path!r} is missing a 'group'")
        elif group_name not in groups:
            problems.append(f"leaf {path!r} names group {group_name!r}, which is not defined")
        leaves.append(
            Leaf(
                path=path,
                group=group_name,
                rules=_build_rules(entry, f"leaf {path!r}", problems, path_terms_for(path)),
                seed_path=str(entry.get("seed_path", path)),
            )
        )

    if duplicates:
        problems.append(f"duplicate leaf paths: {sorted(set(duplicates))}")

    ignore = settings["ignore"]
    if not isinstance(ignore, list) or any(not isinstance(item, str) for item in ignore):
        problems.append("ignore must be a list of strings")
        ignore = list(DEFAULT_SETTINGS["ignore"])

    if problems:
        raise TaxonomyError("invalid taxonomy:\n  - " + "\n  - ".join(problems))

    by_group: dict[str, list[Leaf]] = {name: [] for name in groups}
    for leaf in leaves:
        by_group.setdefault(leaf.group, []).append(leaf)

    return Taxonomy(
        version=int(version),
        output_root=str(settings["output_root"]),
        review_folder=str(settings["review_folder"]),
        group_threshold=float(settings["group_threshold"]),
        confidence_threshold=float(settings["confidence_threshold"]),
        keyword_weight=float(settings["keyword_weight"]),
        pattern_weight=float(settings["pattern_weight"]),
        path_weight=float(settings["path_weight"]),
        group_saturation=float(settings["group_saturation"]),
        leaf_saturation=float(settings["leaf_saturation"]),
        groups=groups,
        leaves=tuple(leaves),
        ignore=tuple(ignore),
        _leaves_by_group={name: tuple(items) for name, items in by_group.items()},
    )


# --------------------------------------------------------------------------
# Entry point
# --------------------------------------------------------------------------


def read_taxonomy_file(path: Path) -> dict[str, Any]:
    """Read one taxonomy JSON file, with errors that name the file."""
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TaxonomyError(f"could not read taxonomy {path}: {exc}") from exc
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TaxonomyError(f"{path} is not valid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise TaxonomyError(f"{path} must contain a JSON object at the top level")
    return data


def builtin_taxonomy_data() -> dict[str, Any]:
    """The taxonomy generated from `create_organized_folders.sh` and shipped."""
    return read_taxonomy_file(BUILTIN_TAXONOMY_PATH)


def load_taxonomy(config_path: Path | None = None) -> Taxonomy:
    """Load and validate the merged taxonomy (built-in plus user overrides).

    `config_path=None` uses the built-in taxonomy only. Otherwise the user
    file is merged over it with `merge_taxonomies`, so a partial config —
    two retuned leaves and a threshold — is a valid config.
    """
    data = builtin_taxonomy_data()
    if config_path is not None:
        user = read_taxonomy_file(Path(config_path))
        data = merge_taxonomies(data, user)
    return compile_taxonomy(data)
