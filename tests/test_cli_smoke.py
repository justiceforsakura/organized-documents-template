"""Smoke test: the CLI renders its full flag surface without any network I/O."""

from __future__ import annotations

import pytest

from organized_docs.cli import main

EXPECTED_FLAGS = ("--apply", "--copy", "--output", "--config", "--threshold", "--report", "--flat")


def test_help_renders_full_flag_surface(
    block_network: None, capsys: pytest.CaptureFixture[str]
) -> None:
    with pytest.raises(SystemExit) as exc_info:
        main(["--help"])

    assert exc_info.value.code == 0
    stdout = capsys.readouterr().out
    for flag in EXPECTED_FLAGS:
        assert flag in stdout
