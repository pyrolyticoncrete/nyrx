# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Lua server-probe dispatcher in ``sources/tv_movies/dispatcher.py``.

Covers config discovery priority (cache dir → user override dir → disabled
exclusion), per-file server loading, and the auto/manual probe orchestration.
A real ``Dispatcher`` is constructed with ``tmp_path`` directories and a
``FakeSandbox`` injected via ``_get_sandbox`` before construction
(``__init__`` runs ``_discover`` immediately).
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from nyrx.sources.tv_movies.dispatcher import Dispatcher
from tests.fakes import FakeSandbox, make_server_globals


@pytest.fixture(autouse=True)
def hermetic_dispatcher(tmp_path):
    """Return tmp_path for hermetic testing."""
    return tmp_path


@pytest.fixture
def fake_sandbox(monkeypatch):
    fake = FakeSandbox()
    monkeypatch.setattr(Dispatcher, "_get_sandbox", lambda self: fake)
    return fake


def make_dispatcher(tmp_path, cache_name="cache", config_name=None):
    cache = tmp_path / cache_name
    config = tmp_path / config_name if config_name else None
    return Dispatcher(lua_cache_dir=cache, lua_config_dir=config)


def write_lua(directory: Path, filename: str, content: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / filename
    path.write_text(content)
    return path


def _server(name: str, **meta) -> dict:
    base = {"name": name}
    base.update(meta)
    return base


# ---------------------------------------------------------------------------
# _ensure_runtime_dirs
# ---------------------------------------------------------------------------


class TestEnsureRuntimeDirs:
    def test_creates_cache_and_config_layout(self, tmp_path, fake_sandbox) -> None:
        make_dispatcher(tmp_path, config_name="config")
        assert (tmp_path / "cache").is_dir()
        assert (tmp_path / "config").is_dir()
        assert (tmp_path / "config" / "disabled").is_dir()
        assert (tmp_path / "cache" / "README.txt").is_file()
        assert (tmp_path / "config" / "README.txt").is_file()

    def test_without_config_dir_only_creates_cache(
        self, tmp_path, fake_sandbox
    ) -> None:
        make_dispatcher(tmp_path)
        assert (tmp_path / "cache").is_dir()
        assert not (tmp_path / "config").exists()
        assert (tmp_path / "cache" / "README.txt").is_file()

    def test_readme_not_overwritten(self, tmp_path, fake_sandbox) -> None:
        cache = tmp_path / "cache"
        cache.mkdir()
        (cache / "README.txt").write_text("custom")
        make_dispatcher(tmp_path)
        assert (cache / "README.txt").read_text() == "custom"


# ---------------------------------------------------------------------------
# _load_one / _discover
# ---------------------------------------------------------------------------


class TestLoadOneNameAndMeta:
    def test_name_from_server_and_display_capitalized(
        self, tmp_path, fake_sandbox
    ) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("mysrv"))
        d = make_dispatcher(tmp_path)
        assert d.get_server("mysrv")["display_name"] == "Mysrv"

    def test_missing_name_uses_path_stem(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "07.lua", "--")
        fake_sandbox.scripts["07.lua"] = make_server_globals(server={})
        d = make_dispatcher(tmp_path)
        assert d.get_server("07")["name"] == "07"
        assert d.get_server("07")["display_name"] == "07"

    def test_meta_fields_flow_through(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server(
                "srv",
                display_name="Custom",
                requires_playwright=True,
                has_subs=False,
                has_audio=False,
                notes="n",
            )
        )
        d = make_dispatcher(tmp_path)
        s = d.get_server("srv")
        assert s["display_name"] == "Custom"
        assert s["requires_playwright"] is True
        assert s["has_subs"] is False
        assert s["has_audio"] is False
        assert s["notes"] == "n"

    def test_none_server_skipped(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = {"SERVER": None}
        d = make_dispatcher(tmp_path)
        assert d.server_names == []

    def test_load_error_skipped(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = RuntimeError("boom")
        d = make_dispatcher(tmp_path)
        assert d.server_names == []

    def test_broken_config_logged_and_skipped(
        self, tmp_path, fake_sandbox, caplog
    ) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("s1"))
        fake_sandbox.scripts["02.lua"] = RuntimeError("boom")
        with caplog.at_level(
            logging.WARNING, logger="nyrx.sources.tv_movies.dispatcher"
        ):
            d = make_dispatcher(tmp_path)
        assert d.server_names == ["s1"]
        assert any(
            "02.lua" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_same_name_second_file_overrides(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("srv", display_name="One")
        )
        fake_sandbox.scripts["02.lua"] = make_server_globals(
            server=_server("srv", display_name="Two")
        )
        d = make_dispatcher(tmp_path)
        assert d.server_names == ["srv"]
        assert d.get_server("srv")["display_name"] == "Two"


class TestDiscoverPriority:
    def test_override_replaces_cache_entry(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "config", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("srv", display_name="Override")
        )
        d = make_dispatcher(tmp_path, config_name="config")
        assert len(d.server_names) == 1
        assert d.get_server("srv")["display_name"] == "Override"
        assert d.get_server("srv")["filepath"].endswith("config/01.lua")

    def test_extra_override_file_adds_new_server(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "config", "09.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("a"))
        fake_sandbox.scripts["09.lua"] = make_server_globals(server=_server("b"))
        d = make_dispatcher(tmp_path, config_name="config")
        assert d.server_names == ["a", "b"]

    def test_disabled_removes_by_server_name(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "config" / "disabled", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("srv"))
        d = make_dispatcher(tmp_path, config_name="config")
        assert d.server_names == []

    def test_disabled_failed_load_removes_by_stem(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "config" / "disabled", "zzz.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("zzz"))
        fake_sandbox.scripts["zzz.lua"] = RuntimeError("boom")
        d = make_dispatcher(tmp_path, config_name="config")
        assert d.server_names == []

    def test_reload_configs_rescans(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("a"))
        d = make_dispatcher(tmp_path)
        assert d.server_names == ["a"]
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["02.lua"] = make_server_globals(server=_server("b"))
        d.reload_configs()
        assert d.server_names == ["a", "b"]


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------


class TestProbeAuto:
    def test_first_success_wins_and_second_not_tried(
        self, tmp_path, fake_sandbox
    ) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1", display_name="One"),
            probe=lambda params: {"stream_url": "http://one"},
        )
        fake_sandbox.scripts["02.lua"] = make_server_globals(
            server=_server("s2"),
            probe=lambda params: {"stream_url": "http://two"},
        )
        d = make_dispatcher(tmp_path)
        fake_sandbox.loaded_paths.clear()
        result = d.probe({"tmdb_id": 1})
        assert result["stream_url"] == "http://one"
        assert result["server"] == "s1"
        assert result["server_display"] == "One"
        assert [Path(p).name for p in fake_sandbox.loaded_paths] == ["01.lua"]

    def test_first_none_second_succeeds(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1"),
            probe=lambda params: None,
        )
        fake_sandbox.scripts["02.lua"] = make_server_globals(
            server=_server("s2"),
            probe=lambda params: {"stream_url": "http://two"},
        )
        d = make_dispatcher(tmp_path)
        result = d.probe({"tmdb_id": 1})
        assert result["server"] == "s2"
        assert result["stream_url"] == "http://two"

    def test_result_without_stream_url_skipped(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1"),
            probe=lambda params: {"format": "mp4"},
        )
        fake_sandbox.scripts["02.lua"] = make_server_globals(
            server=_server("s2"),
            probe=lambda params: {"stream_url": "http://two"},
        )
        d = make_dispatcher(tmp_path)
        assert d.probe({"tmdb_id": 1})["server"] == "s2"

    def test_all_fail_returns_none(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1"),
            probe=lambda params: {"format": "mp4"},
        )
        d = make_dispatcher(tmp_path)
        assert d.probe({"tmdb_id": 1}) is None

    def test_raising_script_skipped_continues(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1"),
            probe=lambda params: {"stream_url": "http://one"},
        )
        fake_sandbox.scripts["02.lua"] = make_server_globals(
            server=_server("s2"),
            probe=lambda params: {"stream_url": "http://two"},
        )
        d = make_dispatcher(tmp_path)
        fake_sandbox.scripts["01.lua"] = RuntimeError("boom")
        assert d.probe({"tmdb_id": 1})["server"] == "s2"

    def test_probe_raise_logs_server_name(self, tmp_path, fake_sandbox, caplog) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("s1"))
        d = make_dispatcher(tmp_path)
        fake_sandbox.scripts["01.lua"] = RuntimeError("boom")
        with caplog.at_level(
            logging.WARNING, logger="nyrx.sources.tv_movies.dispatcher"
        ):
            assert d.probe({"tmdb_id": 1}) is None
        assert any(
            "probe failed for server s1" in r.message and r.levelno == logging.WARNING
            for r in caplog.records
        )

    def test_probe_no_stream_logs_debug(self, tmp_path, fake_sandbox, caplog) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1"),
            probe=lambda params: {"format": "mp4"},
        )
        d = make_dispatcher(tmp_path)
        with caplog.at_level(logging.DEBUG, logger="nyrx.sources.tv_movies.dispatcher"):
            assert d.probe({"tmdb_id": 1}) is None
        assert any("no stream from s1" in r.message for r in caplog.records)


class TestProbeManual:
    def test_server_name_filters_candidates(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        write_lua(tmp_path / "cache", "02.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1"),
            probe=lambda params: {"stream_url": "http://one"},
        )
        fake_sandbox.scripts["02.lua"] = make_server_globals(
            server=_server("s2"),
            probe=lambda params: {"stream_url": "http://two"},
        )
        d = make_dispatcher(tmp_path)
        fake_sandbox.loaded_paths.clear()
        assert d.probe({"tmdb_id": 1}, server_name="s2")["server"] == "s2"
        assert [Path(p).name for p in fake_sandbox.loaded_paths] == ["02.lua"]

    def test_unknown_server_name_returns_none(self, tmp_path, fake_sandbox) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(server=_server("s1"))
        d = make_dispatcher(tmp_path)
        assert d.probe({"tmdb_id": 1}, server_name="nope") is None

    def test_no_candidates_returns_none(self, tmp_path, fake_sandbox) -> None:
        d = make_dispatcher(tmp_path)
        assert d.probe({"tmdb_id": 1}) is None

    def test_list_servers_and_get_server_contracts(
        self, tmp_path, fake_sandbox
    ) -> None:
        write_lua(tmp_path / "cache", "01.lua", "--")
        fake_sandbox.scripts["01.lua"] = make_server_globals(
            server=_server("s1", display_name="One")
        )
        d = make_dispatcher(tmp_path)
        assert d.get_server("missing") is None
        assert [s["name"] for s in d.list_servers()] == ["s1"]
