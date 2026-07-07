# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``widgets/watchlist_screen.py`` compact layout and mini strip.

Covers ``WatchlistScreen._apply_compact`` toggling ``.compact`` class at the
config threshold, and the mini-strip formatter producing correct movie/TV
meta lines.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock

from nyrx.widgets.watchlist_screen import WatchlistScreen


def _stub(**attrs) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


class _Size:
    """Minimal stand-in for ``textual.geometry.Size`` supporting unpacking."""

    __slots__ = ("width", "height")

    def __init__(self, width: int, height: int) -> None:
        self.width = width
        self.height = height

    def __iter__(self):
        return iter((self.width, self.height))


class TestApplyCompact:
    """``_apply_compact`` toggles ``.compact`` class below height or width threshold."""

    def _make_stub(
        self, height: int, width: int = 200
    ) -> tuple[SimpleNamespace, MagicMock]:
        set_class = MagicMock()
        query_one = MagicMock()
        return _stub(
            screen=SimpleNamespace(size=_Size(width=width, height=height)),
            set_class=set_class,
            query_one=query_one,
        ), set_class

    def test_compact_at_24(self) -> None:
        stub, set_class = self._make_stub(height=24)
        WatchlistScreen._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")

    def test_not_compact_at_25(self) -> None:
        stub, set_class = self._make_stub(height=25)
        WatchlistScreen._apply_compact(stub)
        set_class.assert_called_once_with(False, "compact")

    def test_compact_at_23(self) -> None:
        stub, set_class = self._make_stub(height=23)
        WatchlistScreen._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")

    def test_compact_narrow_width(self) -> None:
        stub, set_class = self._make_stub(height=40, width=179)
        WatchlistScreen._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")

    def test_compact_below_width(self) -> None:
        stub, set_class = self._make_stub(height=40, width=178)
        WatchlistScreen._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")

    def test_not_compact_at_180(self) -> None:
        stub, set_class = self._make_stub(height=40, width=180)
        WatchlistScreen._apply_compact(stub)
        set_class.assert_called_once_with(False, "compact")


class TestMiniRatingLine:
    """``_update_mini`` produces correct rating·year·runtime/seasons lines."""

    def _make_stub(self) -> SimpleNamespace:
        return _stub(query_one=MagicMock())

    def _get_updates(self, stub: SimpleNamespace) -> list[str]:
        return [c.args[0] for c in stub.query_one.return_value.update.call_args_list]

    def test_movie_runtime(self) -> None:
        stub = self._make_stub()
        data = {
            "tmdb_id": 123,
            "title": "Le Samourai",
            "media_type": "movie",
            "rating": 7.8,
            "year": 1967,
            "runtime": 105,
            "genres": json.dumps(["Crime", "Thriller", "Drama"]),
        }
        WatchlistScreen._update_mini(stub, data)
        updates = self._get_updates(stub)
        assert updates[0] == "Le Samourai"
        assert "7.8" in updates[1]
        assert "1967" in updates[1]
        assert "1h45min" in updates[1]

    def test_tv_seasons(self) -> None:
        stub = self._make_stub()
        data = {
            "tmdb_id": 456,
            "title": "Severance",
            "media_type": "tv",
            "rating": 7.9,
            "year": 2018,
            "season_count": 4,
            "genres": json.dumps(["Drama", "Sci-Fi"]),
        }
        WatchlistScreen._update_mini(stub, data)
        updates = self._get_updates(stub)
        assert "7.9" in updates[1]
        assert "2018" in updates[1]
        assert "4 Seasons" in updates[1]

    def test_genres_truncated_to_three(self) -> None:
        stub = self._make_stub()
        data = {
            "tmdb_id": 789,
            "title": "Test",
            "media_type": "movie",
            "rating": 6.0,
            "year": 2000,
            "genres": json.dumps(["A", "B", "C", "D"]),
        }
        WatchlistScreen._update_mini(stub, data)
        updates = self._get_updates(stub)
        assert updates[2] == "A \u00b7 B \u00b7 C"
