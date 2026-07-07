# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/tv_movies.py`` (5B.6: watchlist management).

Bare ``object.__new__(TVMoviesActions)`` stubs resolve sibling methods through
the class so real orchestration chains run, while every side-effect caller
(``notify``, ``push_screen``, ``_do_delete_bookmark``...) is a recorded mock.
``@work`` methods are invoked via ``__wrapped__``.  Watchlist/tv-home widgets
are plain fakes: no App boot, no Pilot, no real widget instances.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nyrx.actions.tv_movies import TVMoviesActions
from nyrx.config import (
    SEVERITY_ERROR,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
)
from nyrx.modes import Source, View
from tests.fakes import stub_self


class _FakeWatchlist:
    """Stand-in for the watchlist screen widget."""

    def __init__(self, bookmark=None, pending=None):
        self.bookmark = bookmark
        self._pending_delete_tmdb = pending
        self.remove_calls = []
        self.set_pending_calls = []
        self.clear_pending_calls = 0
        self.display = False
        self.populate_calls = []

    def focused_bookmark(self):
        return self.bookmark

    def remove_bookmark_row(self, tmdb_id):
        self.remove_calls.append(tmdb_id)

    def set_pending_delete(self, tmdb_id):
        self.set_pending_calls.append(tmdb_id)
        self._pending_delete_tmdb = tmdb_id

    def populate(self, bookmarks):
        self.populate_calls.append(bookmarks)

    def clear_pending_delete(self):
        self.clear_pending_calls += 1
        self._pending_delete_tmdb = None

    def query_one(self, selector, cls=None):
        return SimpleNamespace(value="", update=MagicMock())


class _FakeTVHome:
    """Stand-in for the TV home screen widget."""

    def __init__(self):
        self.display = True
        self.populate_calls = []
        self.center = SimpleNamespace(display=True)

    def query_one(self, selector, cls=None):
        return self.center

    def populate_watchlist(self, bookmarks):
        self.populate_calls.append(bookmarks)


def _call(cls_method, stub, *args, **kwargs):
    return cls_method(stub, *args, **kwargs)


def _make_stub(**overrides):
    """Bare ``TVMoviesActions`` with sane defaults + side-effect mocks."""
    defaults = {
        "_source": Source.TV_MOVIES,
        "_view": View.RESULTS,
        "_in_watchlist": False,
        "_in_following": False,
        "_in_tv_series": False,
        "_tv_nav_stack": [],
        "_tv_bookmarks": [],
        "_w_tv_series": None,
        "_w_tv_home": None,
        "_w_results_list": None,
        "_w_main_content": None,
        "_w_watchlist_screen": None,
        "focused": SimpleNamespace(is_attached=True, focus=MagicMock()),
        "screen": SimpleNamespace(has_class=MagicMock(return_value=False)),
        "query_one": MagicMock(
            return_value=SimpleNamespace(mount=MagicMock(), display=True)
        ),
        "notify": MagicMock(),
        "call_from_thread": MagicMock(),
        "push_screen": MagicMock(),
        "log": MagicMock(),
        "_apply_view": MagicMock(),
        "_apply_sidebar": MagicMock(),
        "_update_sidebar_context": MagicMock(),
        "_render_focus_indicators": MagicMock(),
        "_save_tv_home_focus": MagicMock(),
        "_restore_tv_home_focus": MagicMock(),
        "_update_sidebar_content": MagicMock(),
        "_populate_tv_home": MagicMock(),
        "_check_hotswap": MagicMock(),
        "_do_delete_bookmark": MagicMock(),
        "_pop_tv_nav": MagicMock(),
        "_show_watchlist": MagicMock(),
        "_hide_watchlist": MagicMock(),
    }
    defaults.update(overrides)
    return stub_self(TVMoviesActions, **defaults)


# ── season jump ──────────────────────────────────────────────────────────────


class TestOnSeasonJumpResult:
    def test_none_returns(self):
        w = SimpleNamespace(_load_season_episodes=MagicMock())
        stub = _make_stub(_w_tv_series=w)
        _call(TVMoviesActions._on_season_jump_result, stub, None)
        w._load_season_episodes.assert_not_called()

    def test_no_series_returns(self):
        stub = _make_stub(_w_tv_series=None)
        _call(TVMoviesActions._on_season_jump_result, stub, 2)
        stub.log.assert_not_called()

    def test_loads_season(self):
        w = SimpleNamespace(_load_season_episodes=MagicMock())
        stub = _make_stub(_w_tv_series=w)
        _call(TVMoviesActions._on_season_jump_result, stub, 3)
        w._load_season_episodes.assert_called_once_with(3)


# ── nav stack ────────────────────────────────────────────────────────────────


class TestTvNavStack:
    def test_push_appends(self):
        restore = lambda: None  # noqa: E731
        stub = _make_stub()
        _call(TVMoviesActions._push_tv_nav, stub, restore)
        assert stub._tv_nav_stack == [restore]

    def test_pop_invokes_restore(self):
        restore = MagicMock()
        stub = _make_stub(_tv_nav_stack=[restore])
        _call(TVMoviesActions._pop_tv_nav, stub)
        restore.assert_called_once_with()
        assert stub._tv_nav_stack == []

    def test_pop_empty_is_noop(self):
        stub = _make_stub(_tv_nav_stack=[])
        _call(TVMoviesActions._pop_tv_nav, stub)


class TestHideTvSeries:
    def test_removes_widget_and_pops_nav(self):
        widget = SimpleNamespace(remove=MagicMock(), is_attached=True)
        stub = _make_stub(_w_tv_series=widget)
        _call(TVMoviesActions._hide_tv_series, stub, widget)
        widget.remove.assert_called_once_with()
        assert stub._w_tv_series is None
        stub._pop_tv_nav.assert_called_once_with()
        stub._update_sidebar_context.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()


# ── action_view_tv_series ────────────────────────────────────────────────────


class TestActionViewTvSeries:
    def test_basic_mount(self):
        stub = _make_stub(_view=View.RESULTS, _w_tv_home=None, _w_results_list=None)
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=3,
            ) as gwls,
            patch("nyrx.actions.tv_movies.TVSeriesView") as tv_cls,
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        gwls.assert_called_once_with("tmdb_42")
        tv_cls.assert_called_once_with(tmdb_id=42, start_season=3)
        tv = tv_cls.return_value
        rw = stub.query_one.return_value
        rw.mount.assert_called_once_with(tv)
        tv.focus.assert_called_once_with()
        assert len(stub._tv_nav_stack) == 1
        assert stub._in_tv_series is True
        assert stub._in_watchlist is False
        stub._update_sidebar_context.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()
        stub._save_tv_home_focus.assert_not_called()

    def test_landing_view_saves_home_focus(self):
        stub = _make_stub(_view=View.LANDING)
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=1,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView"),
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        stub._save_tv_home_focus.assert_called_once_with()

    def test_watch_db_none_falls_back_to_season_one(self):
        stub = _make_stub()
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=None,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView") as tv_cls,
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        tv_cls.assert_called_once_with(tmdb_id=42, start_season=1)

    def test_removes_existing_series(self):
        old = SimpleNamespace(remove=MagicMock())
        stub = _make_stub(_w_tv_series=old)
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=1,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView"),
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        old.remove.assert_called_once_with()

    def test_hides_tv_home_and_results(self):
        tv_home = _FakeTVHome()
        rv = SimpleNamespace(add_class=MagicMock())
        stub = _make_stub(_w_tv_home=tv_home, _w_results_list=rv)
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=1,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView"),
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        assert tv_home.display is False
        rv.add_class.assert_called_once_with("hidden")

    def test_restore_watchlist_branch(self):
        mc = SimpleNamespace(add_class=MagicMock())
        dt = SimpleNamespace(focus=MagicMock())
        wl = SimpleNamespace(display=False, query_one=MagicMock(return_value=dt))
        stub = _make_stub(
            _in_watchlist=True,
            _view=View.LANDING,
            _w_main_content=mc,
            _w_watchlist_screen=wl,
        )
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=1,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView"),
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        restore = stub._tv_nav_stack[0]
        restore()
        assert stub._in_watchlist is True
        assert stub._in_tv_series is False
        assert stub._view == View.LANDING
        stub._apply_view.assert_called_once_with()
        mc.add_class.assert_called_once_with("watchlist-mode")
        assert wl.display is True
        wl.query_one.assert_called_once()
        dt.focus.assert_called_once_with()
        stub._apply_sidebar.assert_called_once_with(False)

    def test_restore_non_watchlist_branch(self):
        prev_focus = SimpleNamespace(is_attached=True, focus=MagicMock())
        stub = _make_stub(
            _in_watchlist=False,
            _view=View.LANDING,
            focused=prev_focus,
        )
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=1,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView"),
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        restore = stub._tv_nav_stack[0]
        restore()
        assert stub._in_watchlist is False
        stub._apply_view.assert_called_once_with()
        prev_focus.focus.assert_called_once_with()
        stub._apply_sidebar.assert_not_called()

    def test_rs_switcher_query_failure_swallowed(self):
        rw = SimpleNamespace(mount=MagicMock())

        def fake_query_one(*args, **_kwargs):
            if args[0] == "#rs-switcher":
                raise RuntimeError("boom")
            return rw

        stub = _make_stub(query_one=MagicMock(side_effect=fake_query_one))
        with (
            patch(
                "nyrx.actions.tv_movies.watch_db.get_last_watched_season",
                return_value=1,
            ),
            patch("nyrx.actions.tv_movies.TVSeriesView") as tv_cls,
        ):
            TVMoviesActions.action_view_tv_series(stub, 42)
        rw.mount.assert_called_once_with(tv_cls.return_value)
        assert stub._in_tv_series is True


# ── delete bookmark ──────────────────────────────────────────────────────────


class TestActionDeleteBookmark:
    def test_not_in_watchlist_returns(self):
        wl = _FakeWatchlist(bookmark={"tmdb_id": 5})
        stub = _make_stub(_in_watchlist=False, _w_watchlist_screen=wl)
        TVMoviesActions.action_delete_bookmark(stub)
        assert wl.set_pending_calls == []
        stub._do_delete_bookmark.assert_not_called()

    def test_no_watchlist_screen_returns(self):
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=None)
        TVMoviesActions.action_delete_bookmark(stub)
        stub._do_delete_bookmark.assert_not_called()

    def test_no_focused_bookmark_returns(self):
        wl = _FakeWatchlist(bookmark=None)
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=wl)
        TVMoviesActions.action_delete_bookmark(stub)
        stub._do_delete_bookmark.assert_not_called()

    def test_bookmark_without_tmdb_id_returns(self):
        wl = _FakeWatchlist(bookmark={"title": "X"})
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=wl)
        TVMoviesActions.action_delete_bookmark(stub)
        stub._do_delete_bookmark.assert_not_called()

    def test_first_press_arms_pending_only(self):
        wl = _FakeWatchlist(bookmark={"tmdb_id": 5}, pending=None)
        stub = _make_stub(
            _in_watchlist=True,
            _w_watchlist_screen=wl,
            _tv_bookmarks=[{"tmdb_id": 5}],
        )
        TVMoviesActions.action_delete_bookmark(stub)
        assert wl.set_pending_calls == [5]
        assert wl.remove_calls == []
        stub._do_delete_bookmark.assert_not_called()
        assert stub._tv_bookmarks == [{"tmdb_id": 5}]

    def test_second_press_same_id_deletes(self):
        wl = _FakeWatchlist(bookmark={"tmdb_id": 5}, pending=5)
        stub = _make_stub(
            _in_watchlist=True,
            _w_watchlist_screen=wl,
            _tv_bookmarks=[{"tmdb_id": 5}, {"tmdb_id": 6}],
        )
        TVMoviesActions.action_delete_bookmark(stub)
        assert wl._pending_delete_tmdb is None
        assert wl.remove_calls == [5]
        assert stub._tv_bookmarks == [{"tmdb_id": 6}]
        stub._do_delete_bookmark.assert_called_once_with(5)

    def test_different_id_re_arms_pending(self):
        wl = _FakeWatchlist(bookmark={"tmdb_id": 6}, pending=5)
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=wl)
        TVMoviesActions.action_delete_bookmark(stub)
        assert wl.set_pending_calls == [6]
        assert wl.remove_calls == []
        stub._do_delete_bookmark.assert_not_called()


class TestDoDeleteBookmark:
    def test_calls_db(self, wrapped):
        stub = _make_stub()
        with patch("nyrx.actions.tv_movies.delete_bookmark") as db:
            wrapped(TVMoviesActions._do_delete_bookmark)(stub, 5)
        db.assert_called_once_with(5)

    def test_db_failure_notifies(self, wrapped):
        stub = _make_stub()
        with (
            patch(
                "nyrx.actions.tv_movies.delete_bookmark",
                side_effect=RuntimeError("locked"),
            ),
            patch("nyrx.actions.tv_movies.logger.warning") as mock_warn,
        ):
            wrapped(TVMoviesActions._do_delete_bookmark)(stub, 5)
        mock_warn.assert_called_once()
        stub.call_from_thread.assert_called_once_with(
            stub.notify, "Failed to delete bookmark", severity="warning", timeout=3
        )


class TestEnrichTvBookmark:
    def test_failure_notifies_and_does_not_crash(self, wrapped):
        stub = _make_stub()
        with (
            patch(
                "nyrx.actions.tv_movies.load_bookmark",
                side_effect=RuntimeError("locked"),
            ),
            patch("nyrx.actions.tv_movies.logger.warning") as mock_warn,
        ):
            wrapped(TVMoviesActions._enrich_tv_bookmark)(stub, 5)
        mock_warn.assert_called_once()
        stub.call_from_thread.assert_called_once_with(
            stub.notify, "Failed to enrich bookmark", severity="warning", timeout=3
        )


# ── watchlist show/hide ──────────────────────────────────────────────────────


class TestActionShowWatchlist:
    def test_non_tv_source_returns(self):
        stub = _make_stub(_source=Source.YOUTUBE)
        TVMoviesActions.action_show_watchlist(stub)
        stub._show_watchlist.assert_not_called()
        stub._hide_watchlist.assert_not_called()

    def test_no_tv_home_returns(self):
        stub = _make_stub(_w_tv_home=None)
        TVMoviesActions.action_show_watchlist(stub)
        stub._show_watchlist.assert_not_called()

    def test_tv_home_not_displayed_returns(self):
        tv = _FakeTVHome()
        tv.display = False
        stub = _make_stub(_w_tv_home=tv)
        TVMoviesActions.action_show_watchlist(stub)
        stub._show_watchlist.assert_not_called()

    def test_shows_when_not_in_watchlist(self):
        tv = _FakeTVHome()
        stub = _make_stub(_in_watchlist=False, _w_tv_home=tv)
        TVMoviesActions.action_show_watchlist(stub)
        stub._show_watchlist.assert_called_once_with()

    def test_hides_when_already_in_watchlist(self):
        tv = _FakeTVHome()
        stub = _make_stub(_in_watchlist=True, _w_tv_home=tv)
        TVMoviesActions.action_show_watchlist(stub)
        stub._hide_watchlist.assert_called_once_with()


class TestShowWatchlist:
    def test_shows_and_populates(self):
        tv = _FakeTVHome()
        wl = _FakeWatchlist()
        mc = SimpleNamespace(add_class=MagicMock())
        stub = _make_stub(
            _tv_bookmarks=[{"tmdb_id": 1}],
            _w_tv_home=tv,
            _w_watchlist_screen=wl,
            _w_main_content=mc,
        )
        _call(TVMoviesActions._show_watchlist, stub)
        assert stub._in_watchlist is True
        stub._save_tv_home_focus.assert_called_once_with()
        assert tv.center.display is False
        assert wl.display is True
        assert wl.populate_calls == [[{"tmdb_id": 1}]]
        mc.add_class.assert_called_once_with("watchlist-mode")
        stub._apply_sidebar.assert_called_once_with(False)
        stub._update_sidebar_context.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_no_watchlist_screen_warns(self):
        tv = _FakeTVHome()
        mc = SimpleNamespace(add_class=MagicMock())
        stub = _make_stub(
            _w_tv_home=tv,
            _w_watchlist_screen=None,
            _w_main_content=mc,
        )
        with patch("nyrx.actions.tv_movies.logger") as mock_logger:
            _call(TVMoviesActions._show_watchlist, stub)
        mock_logger.debug.assert_called()
        mc.add_class.assert_called_once_with("watchlist-mode")

    def test_no_tv_home_warns(self):
        wl = _FakeWatchlist()
        stub = _make_stub(_w_tv_home=None, _w_watchlist_screen=wl)
        with patch("nyrx.actions.tv_movies.logger") as mock_logger:
            _call(TVMoviesActions._show_watchlist, stub)
        mock_logger.debug.assert_called()
        assert wl.display is True


class TestHideWatchlist:
    def test_hides_and_restores(self):
        tv = _FakeTVHome()
        wl = _FakeWatchlist()
        mc = SimpleNamespace(remove_class=MagicMock())
        stub = _make_stub(
            _tv_bookmarks=[{"tmdb_id": 1}],
            _w_tv_home=tv,
            _w_watchlist_screen=wl,
            _w_main_content=mc,
        )
        _call(TVMoviesActions._hide_watchlist, stub)
        assert stub._in_watchlist is False
        assert wl.clear_pending_calls == 1
        assert wl.display is False
        assert tv.center.display is True
        assert tv.populate_calls == [[{"tmdb_id": 1}]]
        mc.remove_class.assert_called_once_with("watchlist-mode")
        stub._update_sidebar_content.assert_called_once_with()
        stub._apply_sidebar.assert_called_once_with(False)
        stub._restore_tv_home_focus.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_no_watchlist_screen_warns(self):
        tv = _FakeTVHome()
        mc = SimpleNamespace(remove_class=MagicMock())
        stub = _make_stub(_w_tv_home=tv, _w_watchlist_screen=None, _w_main_content=mc)
        with patch("nyrx.actions.tv_movies.logger") as mock_logger:
            _call(TVMoviesActions._hide_watchlist, stub)
        assert mock_logger.debug.call_count >= 1
        mc.remove_class.assert_called_once_with("watchlist-mode")

    def test_no_tv_home_warns(self):
        wl = _FakeWatchlist()
        mc = SimpleNamespace(remove_class=MagicMock())
        stub = _make_stub(_w_tv_home=None, _w_watchlist_screen=wl, _w_main_content=mc)
        with patch("nyrx.actions.tv_movies.logger") as mock_logger:
            _call(TVMoviesActions._hide_watchlist, stub)
        mock_logger.debug.assert_called()
        mc.remove_class.assert_called_once_with("watchlist-mode")

    def test_keybind_bar_query_failure_swallowed(self):
        wl = _FakeWatchlist()
        wl.query_one = MagicMock(side_effect=RuntimeError("boom"))
        tv = _FakeTVHome()
        stub = _make_stub(_w_tv_home=tv, _w_watchlist_screen=wl)
        with patch("nyrx.actions.tv_movies.logger") as mock_logger:
            _call(TVMoviesActions._hide_watchlist, stub)
        mock_logger.debug.assert_called()


# ── misc actions ─────────────────────────────────────────────────────────────


class TestActionCheckUpdates:
    def test_always_reloads_local(self):
        mock_src = MagicMock()
        mock_src.server_names = ["a", "b"]
        stub = _make_stub(_sources={"tv_movies": mock_src})
        with patch("nyrx.config.get_manifest_url", return_value=""):
            TVMoviesActions.action_check_updates(stub)
        mock_src.reload_configs.assert_called_once()
        stub._check_hotswap.assert_not_called()
        stub.notify.assert_called_once_with(
            "Refreshed 2 local plugin(s). No manifest URL for remote updates.",
            timeout=TIMEOUT_INFO,
        )

    def test_with_url_also_checks_remote(self):
        mock_src = MagicMock()
        mock_src.server_names = ["a"]
        stub = _make_stub(_sources={"tv_movies": mock_src})
        with patch("nyrx.config.get_manifest_url", return_value="http://x"):
            TVMoviesActions.action_check_updates(stub)
        mock_src.reload_configs.assert_called_once()
        stub._check_hotswap.assert_called_once_with(manual=True)
        stub.notify.assert_called_once_with(
            "Refreshed 1 local plugin(s). Checking remote...",
            timeout=TIMEOUT_INFO,
        )


class TestOnManifestUrlSubmitted:
    def test_none_returns(self):
        stub = _make_stub()
        TVMoviesActions._on_manifest_url_submitted(stub, None)
        stub.notify.assert_not_called()
        stub._check_hotswap.assert_not_called()

    def test_empty_clears_url(self):
        stub = _make_stub()
        with (
            patch("nyrx.config.update_config") as mock_update,
            patch("nyrx.config.HOTSWAP_MANIFEST_URL", ""),
        ):
            TVMoviesActions._on_manifest_url_submitted(stub, "")
        mock_update.assert_called_once_with(hotswap_url="", hotswap_enabled=False)
        stub.notify.assert_called_once_with(
            "Manifest URL cleared", timeout=TIMEOUT_INFO
        )
        stub._check_hotswap.assert_not_called()

    def test_url_saves_and_enables(self):
        stub = _make_stub()
        with (
            patch("nyrx.config.update_config") as mock_update,
            patch("nyrx.config.HOTSWAP_MANIFEST_URL", ""),
        ):
            TVMoviesActions._on_manifest_url_submitted(stub, "http://x")
        mock_update.assert_called_once_with(
            hotswap_url="http://x", hotswap_enabled=True
        )
        stub._check_hotswap.assert_called_once_with(manual=True)
        stub.notify.assert_called_once_with(
            "Manifest URL saved. Fetching server configs...", timeout=TIMEOUT_INFO
        )


class TestOnHotswapToggled:
    def test_enable_runs_check(self):
        stub = _make_stub()
        with (
            patch("nyrx.config.get_config", return_value={"hotswap_enabled": False}),
            patch("nyrx.config.update_config") as mock_update,
        ):
            TVMoviesActions._on_hotswap_toggled(stub, True)
        mock_update.assert_called_once_with(hotswap_enabled=True)
        stub._check_hotswap.assert_called_once_with(manual=True)
        stub.notify.assert_called_once_with(
            "Lua plugin auto-updates enabled", timeout=TIMEOUT_INFO
        )

    def test_disable_persists_no_fetch(self):
        stub = _make_stub()
        with (
            patch("nyrx.config.get_config", return_value={"hotswap_enabled": True}),
            patch("nyrx.config.update_config") as mock_update,
        ):
            TVMoviesActions._on_hotswap_toggled(stub, False)
        mock_update.assert_called_once_with(hotswap_enabled=False)
        stub._check_hotswap.assert_not_called()
        stub.notify.assert_called_once_with(
            "Lua plugin auto-updates disabled", timeout=TIMEOUT_INFO
        )

    def test_none_returns(self):
        stub = _make_stub()
        TVMoviesActions._on_hotswap_toggled(stub, None)
        stub.notify.assert_not_called()
        stub._check_hotswap.assert_not_called()

    def test_same_value_noop(self):
        stub = _make_stub()
        with patch("nyrx.config.get_config", return_value={"hotswap_enabled": True}):
            TVMoviesActions._on_hotswap_toggled(stub, True)
        stub.notify.assert_not_called()
        stub._check_hotswap.assert_not_called()


class TestActionSetTmdbKey:
    def test_pushes_modal(self):
        stub = _make_stub()
        with patch("nyrx.screens.TMDbKeyInputModal") as modal:
            TVMoviesActions.action_set_tmdb_key(stub)
        modal.assert_called_once_with()
        stub.push_screen.assert_called_once_with(
            modal.return_value,
            stub._on_tmdb_key_result,
        )


class TestOnTmdbKeyResult:
    def test_none_returns(self, tmp_path, monkeypatch):
        monkeypatch.setattr("nyrx.config.KEYS_PATH", tmp_path / "keys.json")
        stub = _make_stub()
        _call(TVMoviesActions._on_tmdb_key_result, stub, None)
        stub.notify.assert_not_called()

    def test_saves_key(self, tmp_path, monkeypatch):
        keys_path = tmp_path / "keys.json"
        monkeypatch.setattr("nyrx.config.KEYS_PATH", keys_path)
        stub = _make_stub()
        with patch("nyrx.sources.tv_movies.tmdb_cache.load_keys") as load_keys:
            _call(TVMoviesActions._on_tmdb_key_result, stub, "abc")
        data = json.loads(keys_path.read_text())
        assert data["user_tmdb_keys"] == ["abc"]
        load_keys.assert_called_once_with()
        stub.notify.assert_called_once_with(
            "TMDB API key saved (1 key(s) configured)",
            timeout=TIMEOUT_INFO,
        )

    def test_dedupes_existing_key(self, tmp_path, monkeypatch):
        keys_path = tmp_path / "keys.json"
        monkeypatch.setattr("nyrx.config.KEYS_PATH", keys_path)
        keys_path.write_text(json.dumps({"user_tmdb_keys": ["abc"]}))
        stub = _make_stub()
        with patch("nyrx.sources.tv_movies.tmdb_cache.load_keys"):
            _call(TVMoviesActions._on_tmdb_key_result, stub, "abc")
        data = json.loads(keys_path.read_text())
        assert data["user_tmdb_keys"] == ["abc"]
        stub.notify.assert_called_once_with(
            "TMDB API key saved (1 key(s) configured)",
            timeout=TIMEOUT_INFO,
        )

    def test_save_failure_notifies(self, tmp_path, monkeypatch):
        keys_path = tmp_path / "keys.json"
        monkeypatch.setattr("nyrx.config.KEYS_PATH", keys_path)
        stub = _make_stub()
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.load_keys",
            side_effect=RuntimeError("boom"),
        ):
            _call(TVMoviesActions._on_tmdb_key_result, stub, "abc")
        stub.notify.assert_called_once_with(
            "Failed to save TMDB key: boom",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
