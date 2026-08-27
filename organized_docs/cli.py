"""Command-line entry point for `organized-docs`.

Defines the full argparse surface from the spec's "Command surface"
section. The classification/filing pipeline (`extract`, `taxonomy`,
`classify`, `naming`, `fileops`, `report`) ships in later PRs — until
then, running the CLI parses and validates arguments, then reports
that the pipeline is not implemented yet without touching the
filesystem or the network.
"""

from __future__ import annotations

import argparse
from pathlib import Path

DEFAULT_OUTPUT = Path("~/Documents/Organized Documents")
DEFAULT_REPORT = Path("ORGANIZING-LOG.md")
DEFAULT_THRESHOLD = 0.6


def build_parser() -> argparse.ArgumentParser:
    """Build the `organized-docs` argument parser.

    Kept separate from `main` so tests can inspect or drive the parser
    directly without spawning a subprocess.
    """
    parser = argparse.ArgumentParser(
        prog="organized-docs",
        description=(
            "Scan an unsorted directory of legal PDFs and correspondence, "
            "classify each document against the organized-documents-template "
            "folder hierarchy, and file it into the matching deep folder. "
            "Runs entirely on local disk — no network I/O."
        ),
    )
    parser.add_argument(
        "source",
        type=Path,
        help="Directory of unsorted documents to scan and classify.",
    )
    parser.add_argument(
        "--apply",
        action="store_true",
        default=False,
        help=(
            "Execute the plan (move or copy files). Without this flag the run "
            "is a dry-run: the classification and naming plan is printed and "
            "logged, and zero files are touched."
        ),
    )
    parser.add_argument(
        "--copy",
        action="store_true",
        default=False,
        help="Copy files into the destination instead of moving them; originals stay in place.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"Destination root; leaf paths resolve under it (default: {DEFAULT_OUTPUT}).",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Path to a taxonomy.json overriding the built-in group/leaf mapping and keywords.",
    )
    parser.add_argument(
        "--threshold",
        type=float,
        default=DEFAULT_THRESHOLD,
        help=(
            "Minimum leaf confidence (0-1) required to auto-file a document "
            f"(default: {DEFAULT_THRESHOLD}). Below it, the file goes to _Needs Review/."
        ),
    )
    parser.add_argument(
        "--report",
        type=Path,
        default=DEFAULT_REPORT,
        help=(
            "Markdown report path, written to the destination root on every "
            f"run, including dry-runs (default: {DEFAULT_REPORT})."
        ),
    )
    parser.add_argument(
        "--flat",
        action="store_true",
        default=False,
        help="Scan the source directory non-recursively (default is recursive).",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    """Parse arguments and run the CLI.

    The classification/filing pipeline is not implemented yet; this
    validates the full argument surface and reports that fact rather
    than performing any file or network I/O.
    """
    parser = build_parser()
    parser.parse_args(argv)
    print(
        "organized-docs: argument surface is ready, but the classification "
        "and filing pipeline is not implemented yet."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
