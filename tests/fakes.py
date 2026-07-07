# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fake/stub implementations for tests.

Fakes replace real I/O-bound objects with deterministic in-process
versions that can be inspected for assertions.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any


def make_server_globals(
    server: dict | None = None,
    probe: Callable[[dict], dict | None] | None = None,
) -> dict:
    """Build the globals dict a ``FakeSandbox.load_script`` returns.

    Mirrors what a real Lua config exposes: a ``SERVER`` table plus an
    optional ``probe`` callable.
    """
    g: dict[str, Any] = {}
    if server is not None:
        g["SERVER"] = server
    if probe is not None:
        g["probe"] = probe
    return g


class FakeSandbox:
    """Deterministic stand-in for ``Sandbox``: never touches ``lupa``.

    ``load_script`` returns a per-file globals dict; ``call_probe`` invokes
    the ``probe`` callable from those globals (or returns ``None``), matching
    the real sandbox's swallow-and-return-None behaviour.
    """

    def __init__(self, scripts: dict[str, dict] | None = None) -> None:
        self.scripts = dict(scripts or {})
        self.loaded_paths: list[str] = []

    def load_script(self, filepath: str | Path) -> dict:
        filepath = str(filepath)
        self.loaded_paths.append(filepath)
        script = self.scripts.get(Path(filepath).name)
        if script is None:
            return {"SERVER": None}
        if isinstance(script, Exception):
            raise script
        return dict(script)

    def call_probe(self, script_globals: dict, params: dict) -> dict | None:
        fn = script_globals.get("probe")
        if fn is None:
            return None
        try:
            result = fn(params)
        except Exception:
            return None
        if result is None:
            return None
        return dict(result)


class FakeDispatcher:
    """Deterministic stand-in for ``Dispatcher`` in TVMoviesSource tests."""

    def __init__(
        self,
        server_names: list[str] | None = None,
        probe_result: dict | None | Callable[..., dict | None] = None,
        get_server_map: dict[str, dict] | None = None,
    ) -> None:
        self.server_names = list(server_names) if server_names is not None else ["a"]
        self.probe_result = probe_result
        self._get_server_map = dict(get_server_map or {})
        self.probe_calls: list[tuple[dict, str | None]] = []

    def get_server(self, name: str) -> dict | None:
        entry = self._get_server_map.get(name)
        return dict(entry) if entry else None

    def probe(self, params: dict, server_name: str | None = None) -> dict | None:
        self.probe_calls.append((params, server_name))
        if callable(self.probe_result):
            return self.probe_result(params, server_name)
        return self.probe_result


class FakeSocket:
    """Simulates a socket for mpv IPC communication.

    When ``chunk_size`` is > 0, responses are split across multiple
    ``recv`` calls so the ``_send_on_socket`` while-loop must
    accumulate chunks until it sees ``\\n``.
    """

    def __init__(self, responses: list[dict], chunk_size: int = 0) -> None:
        self.responses = responses
        self._index = 0
        self._pos = 0
        self.chunk_size = chunk_size
        self.settimeout_called: float | None = None
        self._connected = False
        self._closed = False
        self._sent_data: list[bytes] = []

    def _full_response(self) -> bytes:
        return json.dumps(self.responses[self._index]).encode() + b"\n"

    def settimeout(self, timeout: float) -> None:
        self.settimeout_called = timeout

    def connect(self, path: str) -> None:
        self._connected = True

    def send(self, data: bytes) -> int:
        self._sent_data.append(data)
        return len(data)

    def recv(self, bufsize: int) -> bytes:
        if self._index >= len(self.responses):
            return b""
        full = self._full_response()
        if self.chunk_size == 0:
            self._index += 1
            return full[:bufsize]
        start = self._pos
        stop = min(start + self.chunk_size, len(full))
        chunk = full[start:stop]
        self._pos = stop
        if stop >= len(full):
            self._index += 1
            self._pos = 0
        return chunk[:bufsize]

    def close(self) -> None:
        self._closed = True


def stub_self(cls, **attrs: Any):
    """Build a bare instance of *cls* with *attrs* set on it.

    ``object.__new__`` skips ``__init__`` so no DOM/widget/App machinery runs,
    while instance methods still resolve through the class.  Used to test
    ``actions/`` mixin methods without booting ``MediaApp``.
    """
    obj = object.__new__(cls)
    for name, value in attrs.items():
        setattr(obj, name, value)
    return obj
