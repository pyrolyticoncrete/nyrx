# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``widgets/tv_series.py`` season-state guards (P2 items 40-41).

Covers the token-based staleness guard for ``_fetch_season_episodes`` ->
``_on_episodes_fetched`` (a season switch while a fetch is in-flight must not
save/display the stale season under the new season number), the captured
``season_number`` used for save/load keys, the worker-side ``load_seasons``
pass-through (no main-thread re-query), and the ``on_unmount`` token bump.

Methods run against duck-typed ``SimpleNamespace`` stubs (not ``stub_self``,
because ``app``/``log`` are read-only Textual properties on the widget);
``@work`` methods run through ``.__wrapped__`` (the ``wrapped`` fixture from
``conftest.py``) so no Textual App is booted.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from textual.widgets import Static

from nyrx.widgets.tv_series import TVSeriesView


def _app_with() -> SimpleNamespace:
    return SimpleNamespace(call_from_thread=MagicMock())


def _stub(**attrs) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


class TestFetchSeriesData:
    """``@work`` worker: bookmarked path threads seasons through the callback."""

    def test_bookmarked_cached_path_passes_seasons(self, wrapped):
        bm = {"title": "Severance", "genres": "[]"}
        seasons = [{"season_number": 1}, {"season_number": 2}]
        stub = _stub(
            _tmdb_id=5,
            app=_app_with(),
            _on_series_data_cached=MagicMock(),
            _on_series_data_fetched=MagicMock(),
        )
        with (
            patch("nyrx.sources.tv_movies.db.bookmark_exists", return_value=True),
            patch("nyrx.sources.tv_movies.db.load_bookmark", return_value=dict(bm)),
            patch("nyrx.sources.tv_movies.db.load_seasons", return_value=seasons),
            patch("nyrx.sources.tv_movies.tmdb_cache.tv_details") as details,
        ):
            wrapped(TVSeriesView._fetch_series_data)(stub)
        assert stub._bookmarked is True
        assert details.call_count == 0
        stub.app.call_from_thread.assert_called_once_with(
            stub._on_series_data_cached, bm, seasons
        )

    def test_not_bookmarked_fetches_details(self, wrapped):
        stub = _stub(
            _tmdb_id=5,
            app=_app_with(),
            _on_series_data_cached=MagicMock(),
            _on_series_data_fetched=MagicMock(),
        )
        with (
            patch("nyrx.sources.tv_movies.db.bookmark_exists", return_value=False),
            patch(
                "nyrx.sources.tv_movies.tmdb_cache.tv_details",
                return_value={"name": "X"},
            ),
        ):
            wrapped(TVSeriesView._fetch_series_data)(stub)
        stub.app.call_from_thread.assert_called_once_with(
            stub._on_series_data_fetched, {"name": "X"}
        )


class TestOnSeriesDataCached:
    """Uses worker-provided seasons: no main-thread re-query."""

    def test_uses_passed_seasons_not_a_new_query(self):
        seasons = [{"season_number": 1}]
        stub = _stub(_on_data_ready=MagicMock())
        with patch("nyrx.sources.tv_movies.db.load_seasons") as load:
            load.side_effect = AssertionError("main thread must not re-query")
            TVSeriesView._on_series_data_cached(
                stub,
                {"title": "Severance", "genres": '["Drama", "Thriller"]'},
                seasons,
            )
        assert stub._seasons == seasons
        assert stub._season_count == 1
        assert stub._series_data["genres_display"] == "Drama, Thriller"
        stub._on_data_ready.assert_called_once_with()

    def test_invalid_genres_json_falls_back_to_empty(self):
        stub = _stub(_on_data_ready=MagicMock())
        TVSeriesView._on_series_data_cached(
            stub, {"genres": "not-json"}, [{"season_number": 1}]
        )
        assert stub._series_data["genres_display"] == ""


class TestLoadSeasonEpisodes:
    """Token bump + token/season pass-through to the fetch worker."""

    def test_bumps_token_and_passes_captured_token(self):
        stub = _stub(
            _season_token=0,
            _current_season=1,
            _compact_selector=False,
            _bookmarked=False,
            _show_episodes_loading=MagicMock(),
            _fetch_season_episodes=MagicMock(),
            query_one=MagicMock(),
        )
        TVSeriesView._load_season_episodes(stub, 3)
        assert stub._season_token == 1
        assert stub._current_season == 3
        stub._show_episodes_loading.assert_called_once_with()
        stub._fetch_season_episodes.assert_called_once_with(3, 1)


class TestFetchSeasonEpisodes:
    """Worker threads the captured season number and token into the callback."""

    def test_callback_receives_season_and_token(self, wrapped):
        data = {"episodes": []}
        stub = _stub(
            _tmdb_id=5,
            app=_app_with(),
            _on_episodes_fetched=MagicMock(),
        )
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.season_details", return_value=data
        ):
            wrapped(TVSeriesView._fetch_season_episodes)(stub, 3, 7)
        stub.app.call_from_thread.assert_called_once_with(
            stub._on_episodes_fetched, data, 3, 7
        )


class TestOnEpisodesFetched:
    """Staleness guard + save/load keyed off the captured season number."""

    def test_stale_token_returns_early(self):
        stub = _stub(
            _season_token=2,
            _bookmarked=True,
            _tmdb_id=5,
            _update_chip_focus=MagicMock(),
            _switch_episodes_to=MagicMock(),
            _populate_episode_table=MagicMock(),
            query_one=MagicMock(),
        )
        data = {"episodes": [{"episode_number": 1, "name": "Pilot"}]}
        with (
            patch("nyrx.sources.tv_movies.db.save_episodes") as save,
            patch("nyrx.sources.tv_movies.db.load_episodes") as load,
        ):
            TVSeriesView._on_episodes_fetched(stub, data, 3, 1)
        save.assert_not_called()
        load.assert_not_called()
        stub._populate_episode_table.assert_not_called()

    def test_fresh_token_uses_captured_season_not_current(self):
        loaded = [{"episode_number": 1}]
        stub = _stub(
            _season_token=1,
            _current_season=1,
            _bookmarked=True,
            _tmdb_id=5,
            _episodes=[],
            _update_chip_focus=MagicMock(),
            _switch_episodes_to=MagicMock(),
            _populate_episode_table=MagicMock(),
            query_one=MagicMock(),
        )
        ep = {
            "episode_number": 1,
            "name": "Pilot",
            "still_path": "",
            "overview": "",
            "runtime": 42,
            "air_date": "",
            "vote_average": 7,
        }
        data = {"episodes": [dict(ep)]}
        with (
            patch("nyrx.sources.tv_movies.db.save_episodes") as save,
            patch("nyrx.sources.tv_movies.db.load_episodes", return_value=loaded),
        ):
            TVSeriesView._on_episodes_fetched(stub, data, 3, 1)
        save.assert_called_once_with(5, 3, [ep])
        assert stub._episodes == loaded
        stub._populate_episode_table.assert_called_once_with()

    def test_empty_data_shows_no_episodes(self):
        stub = _stub(
            _season_token=1,
            _current_season=2,
            _bookmarked=True,
            _tmdb_id=5,
            _episodes=[],
            _update_chip_focus=MagicMock(),
            _switch_episodes_to=MagicMock(),
            _populate_episode_table=MagicMock(),
            query_one=MagicMock(),
        )
        with (
            patch("nyrx.sources.tv_movies.db.save_episodes") as save,
            patch("nyrx.sources.tv_movies.db.load_episodes") as load,
        ):
            TVSeriesView._on_episodes_fetched(stub, None, 2, 1)
        save.assert_not_called()
        load.assert_not_called()
        assert stub._episodes == []
        stub.query_one.assert_called_once_with("#tvs-episode-desc", Static)


class TestOnUnmount:
    def test_bumps_token_to_drop_inflight_callbacks(self):
        stub = _stub(
            _season_token=5,
            _tmdb_id=5,
            log=MagicMock(),
        )
        TVSeriesView.on_unmount(stub)
        assert stub._season_token == 6


class TestApplyCompact:
    """``_apply_compact`` toggles ``.compact`` class at the TVS threshold."""

    def _make_stub(self, height: int):
        set_class = MagicMock()
        query_one = MagicMock()
        stub = _stub(
            screen=SimpleNamespace(size=SimpleNamespace(height=height)),
            query_one=query_one,
            set_class=set_class,
        )
        return stub, set_class

    def test_compact_at_29(self):
        stub, set_class = self._make_stub(height=29)
        TVSeriesView._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")

    def test_not_compact_at_30(self):
        stub, set_class = self._make_stub(height=30)
        TVSeriesView._apply_compact(stub)
        set_class.assert_called_once_with(False, "compact")


class TestUpdateHeader:
    """``_update_header`` populates all labels including browsing bar."""

    def test_bookmarked_shows_heart_in_title_and_browsing(self):
        stubs = {
            k: MagicMock()
            for k in (
                "#tvs-title",
                "#tvs-rating",
                "#tvs-genres",
                "#tvs-overview",
                "#tvs-browsing",
            )
        }
        stub = _stub(
            _series_data={
                "title": "Severance",
                "rating": 8.7,
                "vote_count": 1200,
                "year": "2022",
                "season_count": 2,
                "genres_display": "Sci-Fi",
            },
            _bookmarked=True,
            _season_count=0,
            query_one=lambda s, *a: stubs.get(s, MagicMock()),
        )
        TVSeriesView._update_header(stub)
        title_update = stubs["#tvs-title"].update.call_args[0][0]
        assert "Severance" in title_update
        assert "\u2764\ufe0e" in title_update
        browsing_update = stubs["#tvs-browsing"].update.call_args[0][0]
        assert "Browsing: Severance" in browsing_update
        assert "\u2764\ufe0e" in browsing_update
        assert "\u2605 8.7 (1200)" in browsing_update
        assert "2022" in browsing_update
        assert "2 seasons" in browsing_update

    def test_rating_without_votes_omits_parens(self):
        stubs = {
            k: MagicMock()
            for k in (
                "#tvs-title",
                "#tvs-rating",
                "#tvs-genres",
                "#tvs-overview",
                "#tvs-browsing",
            )
        }
        stub = _stub(
            _series_data={
                "title": "X",
                "rating": 6.5,
                "vote_count": 0,
                "year": "2020",
                "season_count": 1,
                "genres_display": "",
            },
            _bookmarked=False,
            _season_count=0,
            query_one=lambda s, *a: stubs.get(s, MagicMock()),
        )
        TVSeriesView._update_header(stub)
        browsing = stubs["#tvs-browsing"].update.call_args[0][0]
        assert "\u2605 6.5" in browsing
        assert "(" not in browsing
        assert "1 season" in browsing
        assert "X  \u2022" in browsing

    def test_empty_title_produces_valid_browse_line(self):
        stubs = {
            k: MagicMock()
            for k in (
                "#tvs-title",
                "#tvs-rating",
                "#tvs-genres",
                "#tvs-overview",
                "#tvs-browsing",
            )
        }
        stub = _stub(
            _series_data={
                "title": "",
                "rating": 0,
                "vote_count": 0,
                "year": "",
                "season_count": 0,
                "genres_display": "",
            },
            _bookmarked=False,
            _season_count=5,
            query_one=lambda s, *a: stubs.get(s, MagicMock()),
        )
        TVSeriesView._update_header(stub)
        browsing = stubs["#tvs-browsing"].update.call_args[0][0]
        assert browsing.startswith("Browsing:  ")
        assert "5 seasons" in browsing
