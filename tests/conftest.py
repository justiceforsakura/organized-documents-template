"""Shared pytest fixtures.

`block_network` monkeypatches `socket.socket` to raise on any attempt
to open a connection, proving the zero-network-I/O invariant from the
spec. Tests that exercise the CLI or pipeline end-to-end should depend
on this fixture.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator

import pytest


class NetworkBlockedError(RuntimeError):
    """Raised when code under test attempts to open a network socket."""


@pytest.fixture
def block_network(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    """Block all socket creation for the duration of a test."""

    def _blocked_socket(*_args: object, **_kwargs: object) -> None:
        raise NetworkBlockedError("network access attempted during a no-network test")

    monkeypatch.setattr(socket, "socket", _blocked_socket)
    yield
