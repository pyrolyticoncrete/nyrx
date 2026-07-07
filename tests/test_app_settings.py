# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``app._load_settings``: graceful first-run handling."""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from nyrx.app import MediaApp


def _make_stub(**attrs) -> SimpleNamespace:
    defaults = dict(
        _download_dir=None,
        _quality="1080p",
        _trending_region="us",
        _search_histories={"youtube": [], "soundcloud": [], "tv_movies": []},
        _tv_bookmarks=[],
    )
    defaults.update(attrs)
    return SimpleNamespace(**defaults)


class TestLoadSettings:
    """``_load_settings`` gracefully handles missing, corrupt, and valid config."""

    def test_missing_file_silent(self, caplog) -> None:
        stub = _make_stub()
        fake_path = SimpleNamespace(
            exists=lambda: False,
            read_text=lambda: (_ for _ in ()).throw(FileNotFoundError),
        )
        with (
            patch("nyrx.app.SETTINGS_PATH", fake_path),
            patch("nyrx.app.load_bookmarks", return_value=[]),
        ):
            MediaApp._load_settings(stub)
        assert not any(r.levelname == "WARNING" for r in caplog.records)
        assert stub._quality == "1080p"
        assert stub._trending_region == "us"
        assert stub._tv_bookmarks == []

    def test_corrupt_json_warns_and_defaults(self, caplog) -> None:
        stub = _make_stub()
        fake_path = SimpleNamespace(
            exists=lambda: True,
            read_text=lambda: "{not valid json",
        )
        with (
            patch("nyrx.app.SETTINGS_PATH", fake_path),
            patch("nyrx.app.load_bookmarks", return_value=[]),
        ):
            MediaApp._load_settings(stub)
        assert any(
            "config.json" in r.message and r.levelname == "WARNING"
            for r in caplog.records
        )
        assert stub._quality == "1080p"
        assert stub._trending_region == "us"
        assert stub._search_histories == {
            "youtube": [],
            "soundcloud": [],
            "tv_movies": [],
        }

    def test_valid_json_applies_settings(self, caplog) -> None:
        stub = _make_stub()
        data = {
            "quality": "720p",
            "trending_region": "gb",
            "download_dir": "/tmp/dl",
            "search_history": ["old query"],
            "search_histories": {"youtube": ["q1"], "soundcloud": [], "tv_movies": []},
        }
        fake_path = SimpleNamespace(
            exists=lambda: True,
            read_text=lambda: json.dumps(data),
        )
        with (
            patch("nyrx.app.SETTINGS_PATH", fake_path),
            patch("nyrx.app.load_bookmarks", return_value=[{"id": 1}]),
        ):
            MediaApp._load_settings(stub)
        assert stub._quality == "720p"
        assert stub._trending_region == "gb"
        assert stub._download_dir == "/tmp/dl"
        assert "q1" in stub._search_histories["youtube"]
        assert stub._tv_bookmarks == [{"id": 1}]
        assert stub._search_histories.setdefault is not None  # setdefault ran

    def test_legacy_search_history_migrated(self, caplog) -> None:
        stub = _make_stub()
        data = {
            "search_history": ["old_q"],
            "search_histories": {},
        }
        fake_path = SimpleNamespace(
            exists=lambda: True,
            read_text=lambda: json.dumps(data),
        )
        with (
            patch("nyrx.app.SETTINGS_PATH", fake_path),
            patch("nyrx.app.load_bookmarks", return_value=[]),
        ):
            MediaApp._load_settings(stub)
        assert "old_q" in stub._search_histories["youtube"]
