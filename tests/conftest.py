"""Shared pytest fixtures.

`block_network` monkeypatches `socket.socket` to raise on any attempt
to open a connection, proving the zero-network-I/O invariant from the
spec. Tests that exercise the CLI or pipeline end-to-end should depend
on this fixture.

`scripts/` is added to `sys.path` so the developer tooling that lives
there (`build_taxonomy.py`) is importable by tests without being part of
the installed package.
"""

from __future__ import annotations

import socket
import sys
from collections.abc import Iterator
from pathlib import Path

import pytest

SCRIPTS_DIR = Path(__file__).resolve().parent.parent / "scripts"
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))


class NetworkBlockedError(RuntimeError):
    """Raised when code under test attempts to open a network socket."""


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Block all socket creation for the duration of a test."""

    def _blocked_socket(*_args: object, **_kwargs: object) -> None:
        raise NetworkBlockedError("network access attempted during a no-network test")

    monkeypatch.setattr(socket, "socket", _blocked_socket)
    yield
