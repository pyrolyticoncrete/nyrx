# SPDX-License-Identifier: AGPL-3.0-only

"""Shared fixtures and configuration for nyrx tests."""

from __future__ import annotations

import atexit
import os
from types import SimpleNamespace

import pytest


@pytest.fixture(autouse=True)
def _hermetic_config(tmp_path, monkeypatch):
    """Redirect ALL nyrx config/cache paths to tmp_path for every test.

    Patches every writable path constant in ``nyrx.config`` so no test
    can touch the real ``~/.config/nyrx/`` or ``~/.cache/nyrx/`` directories.
    Directories are NOT created here; the code under test creates them.
    """
    cfg = tmp_path / "config"
    cache = tmp_path / "cache"
    monkeypatch.setattr("nyrx.config.CONFIG_DIR", cfg)
    monkeypatch.setattr("nyrx.config.CACHE_DIR", cache)
    monkeypatch.setattr("nyrx.config.SETTINGS_PATH", cfg / "config.json")
    monkeypatch.setattr("nyrx.config.KEYS_PATH", cfg / "keys.json")
    monkeypatch.setattr("nyrx.config.SC_DB_PATH", cfg / "sc_data.db")
    monkeypatch.setattr("nyrx.config.TV_DB_PATH", cfg / "tv_data.db")
    monkeypatch.setattr("nyrx.config.WATCH_HISTORY_DB_PATH", cfg / "watch_history.db")
    monkeypatch.setattr("nyrx.config.TRACKER_V4_PATH", cfg / "tracker_v4.jsonl")
    monkeypatch.setattr("nyrx.config.TRACKER_OFFSET_PATH", cfg / "tracker_offset")
    monkeypatch.setattr("nyrx.config.TV_THUMBS_DIR", cache / "tv_thumbs")
    monkeypatch.setattr("nyrx.config.SC_THUMBS_DIR", cache / "sc_thumbnails")
    monkeypatch.setattr("nyrx.config.TEMP_THUMBS", cache / "tmp_thumbs")
    monkeypatch.setattr("nyrx.config.SC_CLIENT_ID_CACHE", cache / "sc_client_id")
    monkeypatch.setattr("nyrx.config.LUA_CONFIG_DIR", cfg / "lua_configs")
    monkeypatch.setattr("nyrx.config.LUA_CACHE_DIR", cache / "lua_configs")
    monkeypatch.setattr("nyrx.config.DEFAULT_DOWNLOAD_DIR", tmp_path / "downloads")


@pytest.fixture
def patch_clock(monkeypatch) -> SimpleNamespace:
    """Freeze the ``time`` clocks used by the actions layer.

    Patches the ``time`` module attributes ``monotonic``/``time`` to return
    controller-set values and makes ``sleep`` a no-op (the same convention as
    ``patch("time.monotonic", ...)`` used by the 5B.1 download tests).  Returns
    a controller with ``set_monotonic(value)`` / ``set_time(value)``.
    """
    state = {"monotonic": 0.0, "time": 1000.0}

    def set_monotonic(value: float) -> None:
        state["monotonic"] = value

    def set_time(value: float) -> None:
        state["time"] = value

    monkeypatch.setattr("time.monotonic", lambda: state["monotonic"])
    monkeypatch.setattr("time.time", lambda: state["time"])
    monkeypatch.setattr("time.sleep", lambda _seconds: None)
    return SimpleNamespace(set_monotonic=set_monotonic, set_time=set_time)


@pytest.fixture
def wrapped():
    """Unwrap a ``@work``-decorated method for direct synchronous invocation.

    ``@work`` asserts ``isinstance(self, DOMNode)`` when the decorated callable
    is invoked, so tests call ``cls.method.__wrapped__(stub, ...)`` instead::

        worker = wrapped(SoundCloudActions._resolve_then_play)
        worker(stub, source, data, token)
    """
    return lambda method: method.__wrapped__


@pytest.fixture(autouse=True, scope="session")
def _cleanup_textual_timers():
    """Ensure the process exits cleanly after all tests finish.

    Textual ``set_interval`` timers (e.g. ``BrailleSpinner``) keep the event
    loop alive even after tests complete.  Register an ``atexit`` handler that
    forces a clean exit once pytest is done.
    """
    yield
    atexit.register(lambda: os._exit(0))
