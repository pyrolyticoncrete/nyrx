# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/navigation.py`` (5B.5: source switching, landing, quit, copy URL).

Bare ``object.__new__(NavigationActions)`` stubs resolve sibling methods through
the class so real orchestration chains run, while every side-effect caller
(``notify``, ``push_screen``, ``_show_page``...) is a recorded mock.  ``@work``
methods are invoked via ``__wrapped__``.  Widget objects touched by the
DOM-but-fakeable methods (``_apply_view``, ``_enter_landing_mode``,
``_update_offline_ui``...) are plain ``SimpleNamespace`` fakes: no App boot,
no Pilot, no real widget instances.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

from nyrx.actions.navigation import NavigationActions
from nyrx.config import (
    SEVERITY_ERROR,
    SEVERITY_INFORMATION,
    SEVERITY_WARNING,
    TIMEOUT_CONFIRM,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
)
from nyrx.modes import Source, View
from nyrx.queues import PlaybackQueue
from tests.fakes import stub_self


class _FakeTimer:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class _FakeWidget:
    """Minimal widget stand-in: settable display/classes + callable focus."""

    def __init__(self, children=None):
        self.display = True
        self.classes = ""
        self.children = children if children is not None else []
        self.focus_calls = 0
        self.clear_calls = 0

    def focus(self):
        self.focus_calls += 1

    def clear(self):
        self.clear_calls += 1

    def set_classes(self, classes):
        self.classes = classes


def _call(cls_method, stub, *args, **kwargs):
    return cls_method(stub, *args, **kwargs)


def _make_stub(**overrides):
    """Bare ``NavigationActions`` with sane defaults + side-effect mocks."""
    defaults = {
        "_view": View.RESULTS,
        "_source": Source.YOUTUBE,
        "_switch_gen": 0,
        "_sources": {},
        "_source_states": {},
        "_all_results": [],
        "_page": 0,
        "_query": "",
        "_online": True,
        "_is_playing": False,
        "_now_playing_data": None,
        "_download_pending": False,
        "_download_state": None,
        "_audio_only": False,
        "_in_liked": False,
        "_in_following": False,
        "_in_artist_profile": False,
        "_in_watchlist": False,
        "_in_tv_series": False,
        "_radio_page": 0,
        "_radio_filter_name": "",
        "_radio_filter_tags": [],
        "_radio_filter_countries": [],
        "_station_index": None,
        "_last_quit_press": None,
        "_play_spinner_timer": None,
        "_connectivity_timer": None,
        "_trending_region": "us",
        "_quality": "best",
        "_search_histories": {},
        "_sc_liked": [],
        "_sc_followed": [],
        "_tv_bookmarks": [],
        "_show_back_online": False,
        "_w_tv_series": None,
        "_playback_queue": None,
        "_tv_nav_stack": [],
        "screen_stack": [],
        "screen": SimpleNamespace(has_class=MagicMock(return_value=False)),
        "refresh": MagicMock(),
        "_w_main_content": None,
        "_w_liked_screen": None,
        "_w_following_area": None,
        "_w_watchlist_screen": None,
        "_w_results_list": None,
        "_w_radio_area": None,
        "_w_radio_filter_hint": None,
        "_w_empty_state": None,
        "_w_history_list": None,
        "_w_sc_home": None,
        "_w_tv_home": None,
        "_w_radio_list": None,
        "_w_empty_offline_banner": None,
        "_w_empty_hint": None,
        "_w_sc_offline_banner": None,
        "_w_tv_offline_banner": None,
        "_side_focused": False,
        "_sc_np_focused": False,
        "notify": MagicMock(),
        "call_from_thread": MagicMock(),
        "call_after_refresh": MagicMock(),
        "push_screen": MagicMock(),
        "query_one": MagicMock(
            return_value=SimpleNamespace(
                current="",
                children=[],
                update=MagicMock(),
                has_class=MagicMock(return_value=False),
            )
        ),
        "exit": MagicMock(),
        "log": MagicMock(),
        "_apply_view": MagicMock(),
        "_enter_landing_mode": MagicMock(),
        "_set_focus_for_current_view": MagicMock(),
        "_show_mode_overlay": MagicMock(),
        "_show_page": MagicMock(),
        "_populate_radio_list": MagicMock(),
        "_update_sidebar_context": MagicMock(),
        "_render_focus_indicators": MagicMock(),
        "_update_mode_indicator": MagicMock(),
        "_on_source_changed": MagicMock(),
        "_purge_unlike_buffer": MagicMock(),
        "_rebuild_history_list": MagicMock(),
        "_update_keybind_bar": MagicMock(),
        "_update_offline_ui": MagicMock(),
        "_hide_liked": MagicMock(),
        "_hide_following": MagicMock(),
        "_hide_artist_profile": MagicMock(),
        "_hide_watchlist": MagicMock(),
        "_hide_tv_series": MagicMock(),
        "_stop_trending": MagicMock(),
        "_sync_np_widget": MagicMock(),
        "_refresh_queue_modal": MagicMock(),
        "_play": MagicMock(),
        "_current_item": MagicMock(),
        "_copy_url_worker": MagicMock(),
        "_detect_key_context": MagicMock(),
        "_save_tv_home_focus": MagicMock(),
        "_restore_tv_home_focus": MagicMock(),
        "_populate_tv_home": MagicMock(),
        "_stop_playback": MagicMock(),
        "_apply_sidebar": MagicMock(),
        "_apply_secondary_widgets": MagicMock(),
        "_process_trending_results": MagicMock(),
        "_switch_source": MagicMock(),
    }
    defaults.update(overrides)
    return stub_self(NavigationActions, **defaults)


def _all_source_plugins():
    return {str(k): object() for k in Source}


# ── trivial helpers ─────────────────────────────────────────────────────────


class TestExitLandingMode:
    def test_sets_results_and_applies_view(self):
        stub = _make_stub()
        _call(NavigationActions._exit_landing_mode, stub)
        assert stub._view == View.RESULTS
        stub._apply_view.assert_called_once_with()


class TestUpdateSidebarContent:
    def test_radio_results_populates_list(self):
        stub = _make_stub(_source=Source.RADIO, _view=View.RESULTS)
        _call(NavigationActions._update_sidebar_content, stub)
        stub._populate_radio_list.assert_called_once_with()

    def test_radio_landing_does_not_populate(self):
        stub = _make_stub(_source=Source.RADIO, _view=View.LANDING)
        _call(NavigationActions._update_sidebar_content, stub)
        stub._populate_radio_list.assert_not_called()

    def test_non_radio_does_not_populate(self):
        stub = _make_stub(_source=Source.YOUTUBE, _view=View.RESULTS)
        _call(NavigationActions._update_sidebar_content, stub)
        stub._populate_radio_list.assert_not_called()


class TestApplySecondaryWidgets:
    def test_radio_landing_shows_hint(self):
        hint = SimpleNamespace(display=False)
        stub = _make_stub(
            _source=Source.RADIO,
            _view=View.LANDING,
            _w_radio_filter_hint=hint,
        )
        _call(NavigationActions._apply_secondary_widgets, stub)
        assert hint.display is True
        stub._update_offline_ui.assert_called_once_with()
        stub._rebuild_history_list.assert_called_once_with()

    def test_non_radio_hides_hint(self):
        hint = SimpleNamespace(display=True)
        stub = _make_stub(_source=Source.YOUTUBE, _w_radio_filter_hint=hint)
        _call(NavigationActions._apply_secondary_widgets, stub)
        assert hint.display is False

    def test_hint_none_logs(self):
        stub = _make_stub(_w_radio_filter_hint=None)
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._apply_secondary_widgets, stub)
        mock_logger.debug.assert_called()


class TestApplySidebar:
    def _stub_with(self, **over):
        sidebar = SimpleNamespace(display=False)
        mc = SimpleNamespace(
            classes="",
            add_class=MagicMock(),
            remove_class=MagicMock(),
        )
        stub = _make_stub(
            query_one=MagicMock(return_value=sidebar),
            _w_main_content=mc,
            **over,
        )
        return stub, sidebar, mc

    def test_landing_now_playing_shows(self):
        stub, sidebar, _mc = self._stub_with(
            _view=View.LANDING,
            _now_playing_data={"yt_id": "x"},
        )
        _call(NavigationActions._apply_sidebar, stub, True)
        assert sidebar.display is True

    def test_landing_nothing_shows_hidden(self):
        stub, sidebar, _mc = self._stub_with(_view=View.LANDING)
        _call(NavigationActions._apply_sidebar, stub, True)
        assert sidebar.display is False

    def test_landing_download_pending_shows(self):
        stub, sidebar, _mc = self._stub_with(
            _view=View.LANDING,
            _download_pending=True,
        )
        _call(NavigationActions._apply_sidebar, stub, True)
        assert sidebar.display is True

    def test_landing_playing_adds_landing_playing(self):
        stub, _sidebar, mc = self._stub_with(
            _view=View.LANDING,
            _is_playing=True,
        )
        _call(NavigationActions._apply_sidebar, stub, True)
        mc.add_class.assert_called_once_with("landing-playing")

    def test_results_shows_and_removes_landing_playing(self):
        stub, sidebar, mc = self._stub_with(_view=View.RESULTS)
        _call(NavigationActions._apply_sidebar, stub, False)
        assert sidebar.display is True
        mc.remove_class.assert_called_once_with("landing-playing")

    def test_in_watchlist_forces_show(self):
        stub, sidebar, _mc = self._stub_with(
            _view=View.RESULTS,
            _in_watchlist=True,
        )
        _call(NavigationActions._apply_sidebar, stub, False)
        assert sidebar.display is True

    def test_landing_offline_idle_keeps_hidden(self):
        stub, sidebar, _mc = self._stub_with(
            _view=View.LANDING,
            _online=False,
        )
        _call(NavigationActions._apply_sidebar, stub, True)
        assert sidebar.display is False


class TestSidebarWanted:
    def _stub_with(self, **over):
        over.setdefault("_view", View.LANDING)
        stub = _make_stub(**over)
        return stub

    def test_landing_no_data_false(self):
        stub = self._stub_with()
        assert _call(NavigationActions._sidebar_wanted, stub) is False

    def test_landing_now_playing_true(self):
        stub = self._stub_with(_now_playing_data=SimpleNamespace())
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_landing_queue_true(self):
        stub = self._stub_with(_playback_queue=["track"])
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_landing_download_pending_true(self):
        stub = self._stub_with(_download_pending=True)
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_landing_download_state_true(self):
        stub = self._stub_with(_download_state=SimpleNamespace())
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_non_landing_true(self):
        stub = self._stub_with(_view=View.RESULTS)
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_following_overrides_landing(self):
        stub = self._stub_with(_in_following=True)
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_liked_overrides_landing(self):
        stub = self._stub_with(_in_liked=True)
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_watchlist_overrides_landing(self):
        stub = self._stub_with(_in_watchlist=True)
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_artist_profile_overrides_landing(self):
        stub = self._stub_with(_in_artist_profile=True)
        assert _call(NavigationActions._sidebar_wanted, stub) is True

    def test_tv_series_overrides_landing(self):
        stub = self._stub_with(_in_tv_series=True)
        assert _call(NavigationActions._sidebar_wanted, stub) is True


class TestApplyView:
    def _make_widget_map(self):
        widgets = {}

        def fake_query_one(*args, **_kwargs):
            key = args[0]
            if key not in widgets:
                widgets[key] = SimpleNamespace(
                    display=True,
                    classes="",
                    add_class=MagicMock(),
                    remove_class=MagicMock(),
                )
            return widgets[key]

        return widgets, fake_query_one

    @staticmethod
    def _results_view(classes: str = "") -> SimpleNamespace:
        rv = SimpleNamespace(classes=classes, clear=MagicMock())
        rv.set_classes = lambda c: setattr(rv, "classes", c)
        return rv

    def test_landing_radio(self):
        widgets, fake_query_one = self._make_widget_map()
        rv = self._results_view()
        mc = SimpleNamespace(add_class=MagicMock(), remove_class=MagicMock())
        stub = _make_stub(
            _source=Source.RADIO,
            _view=View.LANDING,
            _w_results_list=rv,
            _w_main_content=mc,
            query_one=fake_query_one,
        )
        _call(NavigationActions._apply_view, stub)
        assert widgets["#radio-area"].display is True
        assert widgets["#empty-state"].display is False
        assert widgets["#sc-home"].display is False
        assert widgets["#tv-home"].display is False
        assert rv.classes == "hidden"
        assert widgets["#rs-switcher"].display is False
        mc.add_class.assert_called_once_with("landing-mode")

    def test_results_view(self):
        widgets, fake_query_one = self._make_widget_map()
        rv = self._results_view("hidden")
        mc = SimpleNamespace(add_class=MagicMock(), remove_class=MagicMock())
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _view=View.RESULTS,
            _w_results_list=rv,
            _w_main_content=mc,
            query_one=fake_query_one,
        )
        _call(NavigationActions._apply_view, stub)
        assert widgets["#empty-state"].display is False
        assert rv.classes == ""
        assert widgets["#rs-switcher"].display is True
        mc.remove_class.assert_called_once_with("landing-mode")

    def test_results_list_none_logs(self):
        _widgets, fake_query_one = self._make_widget_map()
        mc = SimpleNamespace(add_class=MagicMock(), remove_class=MagicMock())
        stub = _make_stub(
            _view=View.RESULTS,
            _w_results_list=None,
            _w_main_content=mc,
            query_one=fake_query_one,
        )
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._apply_view, stub)
        mock_logger.debug.assert_called()
        mc.remove_class.assert_called_once_with("landing-mode")

    def test_rs_switcher_query_error_is_swallowed(self):
        widgets, _ = self._make_widget_map()
        rv = self._results_view()
        mc = SimpleNamespace(add_class=MagicMock(), remove_class=MagicMock())

        def fake_query_one(*args, **_kwargs):
            if args[0] == "#rs-switcher":
                raise RuntimeError("boom")
            if args[0] not in widgets:
                widgets[args[0]] = SimpleNamespace(display=True)
            return widgets[args[0]]

        stub = _make_stub(
            _view=View.RESULTS,
            _w_results_list=rv,
            _w_main_content=mc,
            query_one=fake_query_one,
        )
        _call(NavigationActions._apply_view, stub)
        assert rv.classes == ""

    def test_main_content_none_logs(self):
        widgets, fake_query_one = self._make_widget_map()
        rv = self._results_view()
        stub = _make_stub(
            _view=View.RESULTS,
            _w_results_list=rv,
            _w_main_content=None,
            query_one=fake_query_one,
        )
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._apply_view, stub)
        mock_logger.debug.assert_called()
        assert rv.classes == ""


class TestEnterLandingMode:
    def test_sets_landing_and_applies_view(self):
        stub = _make_stub()
        _call(NavigationActions._enter_landing_mode, stub)
        assert stub._view == View.LANDING
        stub._apply_view.assert_called_once_with()

    def test_soundcloud_populates(self):
        sc_home = SimpleNamespace(populate=MagicMock())
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _search_histories={"soundcloud": ["q1"]},
            _sc_liked=[{"id": 1}],
            _sc_followed=[{"id": 2}],
            query_one=MagicMock(return_value=sc_home),
        )
        _call(NavigationActions._enter_landing_mode, stub)
        sc_home.populate.assert_called_once_with(
            searches=["q1"],
            liked=[{"id": 1}],
            following=[{"id": 2}],
        )

    def test_tv_populates_home(self):
        tv_home = SimpleNamespace(populate_watchlist=MagicMock())
        stub = _make_stub(
            _source=Source.TV_MOVIES,
            _tv_bookmarks=[{"tmdb_id": 1}],
            _w_tv_home=tv_home,
        )
        _call(NavigationActions._enter_landing_mode, stub)
        tv_home.populate_watchlist.assert_called_once_with([{"tmdb_id": 1}])
        stub._populate_tv_home.assert_called_once_with()
        stub.call_after_refresh.assert_called_once_with(
            stub._set_focus_for_current_view
        )

    def test_tv_without_home_still_populates(self):
        stub = _make_stub(_source=Source.TV_MOVIES, _w_tv_home=None)
        _call(NavigationActions._enter_landing_mode, stub)
        stub._populate_tv_home.assert_called_once_with()

    def test_landing_chrome_scheduled_when_main_content_landing(self):
        mc = SimpleNamespace(has_class=MagicMock(return_value=True))
        stub = _make_stub(_w_main_content=mc)
        _call(NavigationActions._enter_landing_mode, stub)
        stub.call_after_refresh.assert_called_once_with(stub._update_landing_chrome)

    def test_main_content_none_logs(self):
        stub = _make_stub(_w_main_content=None)
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._enter_landing_mode, stub)
        mock_logger.debug.assert_called()


class TestUpdateLandingChrome:
    def test_main_content_none_logs(self):
        stub = _make_stub(_w_main_content=None)
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._update_landing_chrome, stub)
        mock_logger.debug.assert_called()
        stub._update_offline_ui.assert_not_called()

    def test_not_landing_only_offline_ui(self):
        mc = SimpleNamespace(has_class=MagicMock(return_value=False))
        stub = _make_stub(_w_main_content=mc)
        _call(NavigationActions._update_landing_chrome, stub)
        stub._update_offline_ui.assert_called_once_with()
        stub._update_keybind_bar.assert_not_called()

    def test_landing_updates_keybind_and_offline(self):
        mc = SimpleNamespace(has_class=MagicMock(return_value=True))
        stub = _make_stub(_w_main_content=mc)
        _call(NavigationActions._update_landing_chrome, stub)
        stub._update_keybind_bar.assert_called_once_with()
        stub._update_offline_ui.assert_called_once_with()


class TestUpdateOfflineUi:
    def _stub_with(self, welcome=True, **over):
        banner = SimpleNamespace(
            styles=SimpleNamespace(display=None),
            update=MagicMock(),
        )
        sc_banner = SimpleNamespace(
            styles=SimpleNamespace(display=None),
            update=MagicMock(),
        )
        tv_banner = SimpleNamespace(
            styles=SimpleNamespace(display=None),
            update=MagicMock(),
        )
        hint = SimpleNamespace(update=MagicMock())
        mc = SimpleNamespace(has_class=MagicMock(return_value=welcome))
        stub = _make_stub(
            _view=View.LANDING if welcome else View.RESULTS,
            _w_empty_offline_banner=banner,
            _w_sc_offline_banner=sc_banner,
            _w_tv_offline_banner=tv_banner,
            _w_empty_hint=hint,
            _w_main_content=mc,
            **over,
        )
        return stub, banner, sc_banner, tv_banner, hint

    def test_banner_none_returns(self):
        stub = _make_stub(_w_empty_offline_banner=None)
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._update_offline_ui, stub)
        mock_logger.debug.assert_called()

    def test_hint_none_returns(self):
        stub = _make_stub(
            _w_empty_offline_banner=SimpleNamespace(styles=SimpleNamespace()),
            _w_empty_hint=None,
        )
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._update_offline_ui, stub)
        mock_logger.debug.assert_called()

    def test_offline_in_welcome(self):
        stub, yt, sc, tv, hint = self._stub_with(_online=False)
        _call(NavigationActions._update_offline_ui, stub)
        hint.update.assert_called_once_with("[dim]/ to search \\[unavailable] [/dim]")
        for b in (yt, sc, tv):
            assert b.styles.display == "block"
            b.update.assert_called_once_with(
                "[red]\u2717 offline \u00b7 reconnecting[/red]"
            )

    def test_offline_not_welcome(self):
        stub, yt, sc, tv, _hint = self._stub_with(welcome=False, _online=False)
        _call(NavigationActions._update_offline_ui, stub)
        for b in (yt, sc, tv):
            assert b.styles.display == "none"
            b.update.assert_called_once_with("")

    def test_back_online_in_welcome(self):
        stub, yt, sc, tv, hint = self._stub_with(_online=True, _show_back_online=True)
        _call(NavigationActions._update_offline_ui, stub)
        hint.update.assert_called_once_with("/ to search")
        for b in (yt, sc, tv):
            assert b.styles.display == "block"
            b.update.assert_called_once_with("[green]\u2713 back online[/green]")

    def test_online_default_hides(self):
        stub, yt, sc, tv, hint = self._stub_with(_online=True, _show_back_online=False)
        _call(NavigationActions._update_offline_ui, stub)
        for b in (yt, sc, tv):
            assert b.styles.display == "none"
            b.update.assert_called_once_with("")
        hint.update.assert_called_once_with("/ to search")

    def test_exception_logged(self):
        stub, yt, sc, tv, _hint = self._stub_with(_online=False)
        yt.update.side_effect = RuntimeError("boom")
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._update_offline_ui, stub)
        mock_logger.debug.assert_called()

    def test_back_online_not_welcome_hides(self):
        stub, yt, sc, tv, _hint = self._stub_with(
            welcome=False,
            _online=True,
            _show_back_online=True,
        )
        _call(NavigationActions._update_offline_ui, stub)
        for b in (yt, sc, tv):
            assert b.styles.display == "none"
            b.update.assert_called_once_with("")

    def test_home_screen_branch_offline(self):
        from nyrx.screens.home import HomeScreen

        hs = HomeScreen()
        hs.update_offline = MagicMock()
        stub = _make_stub(screen=hs, _online=False, _show_back_online=False)
        _call(NavigationActions._update_offline_ui, stub)
        hs.update_offline.assert_called_once_with(online=False, show_back_online=False)

    def test_home_screen_branch_back_online(self):
        from nyrx.screens.home import HomeScreen

        hs = HomeScreen()
        hs.update_offline = MagicMock()
        stub = _make_stub(screen=hs, _online=True, _show_back_online=True)
        _call(NavigationActions._update_offline_ui, stub)
        hs.update_offline.assert_called_once_with(online=True, show_back_online=True)

    def test_offline_sidebar_active_hides_banner(self):
        """When sidebar wanted (np data present), main banner stays hidden."""
        stub, yt, sc, tv, hint = self._stub_with(
            _online=False,
            _now_playing_data=SimpleNamespace(),
        )
        _call(NavigationActions._update_offline_ui, stub)
        hint.update.assert_called_once_with("[dim]/ to search \\[unavailable] [/dim]")
        for b in (yt, sc, tv):
            assert b.styles.display == "none"
            b.update.assert_called_once_with("")

    def test_back_online_sidebar_active_hides_banner(self):
        """When sidebar wanted (queue present), back-online banner stays hidden."""
        stub, yt, sc, tv, hint = self._stub_with(
            _online=True,
            _show_back_online=True,
            _playback_queue=["track"],
        )
        _call(NavigationActions._update_offline_ui, stub)
        hint.update.assert_called_once_with("/ to search")
        for b in (yt, sc, tv):
            assert b.styles.display == "none"
            b.update.assert_called_once_with("")


class TestShowModeOverlay:
    def test_shows_key(self):
        switcher = SimpleNamespace(show=MagicMock())
        stub = _make_stub(query_one=MagicMock(return_value=switcher))
        _call(NavigationActions._show_mode_overlay, stub, Source.RADIO)
        switcher.show.assert_called_once_with(Source.RADIO)


class TestSetFocusForCurrentView:
    def test_results_view_focuses_results_list(self):
        rv = _FakeWidget(children=[object()])
        stub = _make_stub(_view=View.RESULTS, _w_results_list=rv)
        _call(NavigationActions._set_focus_for_current_view, stub)
        assert rv.focus_calls == 1

    def test_landing_history_focuses(self):
        hl = _FakeWidget(children=[object()])
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _view=View.LANDING,
            _w_history_list=hl,
        )
        _call(NavigationActions._set_focus_for_current_view, stub)
        assert hl.focus_calls == 1

    def test_landing_soundcloud_chip_focuses(self):
        chip = SimpleNamespace(focus=MagicMock())
        sc_home = SimpleNamespace(query_one=MagicMock(return_value=chip))
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _view=View.LANDING,
            _w_sc_home=sc_home,
        )
        _call(NavigationActions._set_focus_for_current_view, stub)
        chip.focus.assert_called_once_with()

    def test_landing_tv_chip_focuses(self):
        chip = SimpleNamespace(focus=MagicMock())
        tv = SimpleNamespace(query=MagicMock(return_value=[chip]))
        stub = _make_stub(
            _source=Source.TV_MOVIES,
            _view=View.LANDING,
            _w_tv_home=tv,
        )
        _call(NavigationActions._set_focus_for_current_view, stub)
        chip.focus.assert_called_once_with()

    def test_landing_radio_list_focuses(self):
        dt = _FakeWidget(children=[object()])
        stub = _make_stub(
            _source=Source.RADIO,
            _view=View.LANDING,
            _w_radio_list=dt,
        )
        _call(NavigationActions._set_focus_for_current_view, stub)
        assert dt.focus_calls == 1

    def test_landing_history_without_children_uses_empty_state(self):
        hl = _FakeWidget(children=[])
        es = _FakeWidget(children=[object()])
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _view=View.LANDING,
            _w_history_list=hl,
            _w_empty_state=es,
        )
        _call(NavigationActions._set_focus_for_current_view, stub)
        assert hl.focus_calls == 0
        assert es.focus_calls == 1


# ── doc families ────────────────────────────────────────────────────────────


class TestCopyUrlWorker:
    def test_first_command_success(self, wrapped):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.subprocess.run") as run:
            wrapped(NavigationActions._copy_url_worker)(stub, "https://x/y")
        run.assert_called_once_with(["wl-copy"], timeout=2, input=b"https://x/y")
        stub.call_from_thread.assert_called_once_with(
            stub.notify,
            "Copied to clipboard",
            timeout=TIMEOUT_CONFIRM,
        )

    def test_falls_back_to_third_command(self, wrapped):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.subprocess.run") as run:
            run.side_effect = [
                RuntimeError("no wl-copy"),
                RuntimeError("no xclip"),
                None,
            ]
            wrapped(NavigationActions._copy_url_worker)(stub, "https://x/y")
        assert run.call_count == 3
        assert run.call_args_list[2].args[0] == ["xsel", "-ib"]
        stub.call_from_thread.assert_called_once_with(
            stub.notify,
            "Copied to clipboard",
            timeout=TIMEOUT_CONFIRM,
        )

    def test_all_fail_silent(self, wrapped):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.subprocess.run") as run:
            run.side_effect = RuntimeError("nope")
            wrapped(NavigationActions._copy_url_worker)(stub, "https://x/y")
        assert run.call_count == 3
        stub.call_from_thread.assert_not_called()


class TestActionCopyUrl:
    def test_no_current_item_returns(self):
        stub = _make_stub(_current_item=MagicMock(return_value=None))
        NavigationActions.action_copy_url(stub)
        stub._copy_url_worker.assert_not_called()

    def test_copies_youtube_url(self):
        item = SimpleNamespace(data={"yt_id": "abc123"})
        stub = _make_stub(_current_item=MagicMock(return_value=item))
        NavigationActions.action_copy_url(stub)
        stub._copy_url_worker.assert_called_once_with(
            "https://www.youtube.com/watch?v=abc123",
        )


class TestSwitchSource:
    def _plugins(self, *keys):
        return {str(k): object() for k in keys}

    @staticmethod
    def _run_deferred(stub):
        """Invoke the deferred heavy-work callback scheduled by _switch_source."""
        deferred = stub.call_after_refresh.call_args_list[0].args[0]
        deferred()

    def test_same_key_is_noop(self):
        stub = _make_stub(
            _source=Source.YOUTUBE, _sources=self._plugins(Source.YOUTUBE)
        )
        _call(NavigationActions._switch_source, stub, Source.YOUTUBE)
        stub._show_mode_overlay.assert_not_called()
        stub._on_source_changed.assert_not_called()

    def test_unknown_key_returns(self):
        stub = _make_stub(_source=Source.YOUTUBE, _sources={})
        _call(NavigationActions._switch_source, stub, Source.SOUNDCLOUD)
        stub._on_source_changed.assert_not_called()

    def test_restores_saved_results(self):
        rv = _FakeWidget()
        saved = {"query": "q1", "results": [{"yt_id": "x"}], "page": 2}
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _sources=self._plugins(Source.YOUTUBE),
            _source_states={"youtube": saved},
            _w_results_list=rv,
        )
        _call(NavigationActions._switch_source, stub, Source.YOUTUBE)
        self._run_deferred(stub)
        assert stub._query == "q1"
        assert stub._all_results == [{"yt_id": "x"}]
        assert stub._page == 2
        stub._show_page.assert_called_once_with()
        assert "youtube" not in stub._source_states
        assert rv.clear_calls == 1
        stub._show_mode_overlay.assert_called_once_with(Source.YOUTUBE)

    def test_radio_transition(self):
        ra = SimpleNamespace(styles=SimpleNamespace(display="block"))
        hint = SimpleNamespace(display=True)
        rv = _FakeWidget()
        es = SimpleNamespace(display=True)
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _sources=self._plugins(Source.RADIO),
            _w_radio_area=ra,
            _w_radio_filter_hint=hint,
            _w_results_list=rv,
            _w_empty_state=es,
        )
        _call(NavigationActions._switch_source, stub, Source.RADIO)
        self._run_deferred(stub)
        assert stub._view == View.RESULTS
        assert ra.styles.display == "block"
        assert hint.display is True
        assert rv.classes == "hidden"
        assert stub._radio_page == 0
        assert es.display is False

    def test_radio_transition_missing_widgets(self):
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _sources=self._plugins(Source.RADIO),
            _w_radio_area=None,
            _w_radio_filter_hint=None,
            _w_results_list=None,
            _w_empty_state=None,
        )
        with patch("nyrx.actions.navigation.logger"):
            _call(NavigationActions._switch_source, stub, Source.RADIO)
            self._run_deferred(stub)
        assert stub._view == View.RESULTS
        assert stub._radio_page == 0

    def test_saves_old_source_state_on_switch(self):
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _query="old",
            _all_results=[{"yt_id": "a"}],
            _page=1,
            _sources=self._plugins(Source.RADIO),
        )
        _call(NavigationActions._switch_source, stub, Source.RADIO)
        assert stub._source_states["youtube"] == {
            "query": "old",
            "results": [{"yt_id": "a"}],
            "page": 1,
        }

    def test_hides_secondary_views_on_switch(self):
        nav = ["screen"]
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _sources=self._plugins(Source.SOUNDCLOUD),
            _in_liked=True,
            _in_following=True,
            _in_artist_profile=True,
            _in_watchlist=True,
            _in_tv_series=True,
            _tv_nav_stack=nav,
            _w_liked_screen=SimpleNamespace(display=True),
            _w_following_area=SimpleNamespace(display=True),
            _w_watchlist_screen=SimpleNamespace(display=True),
            _w_tv_series=SimpleNamespace(),
        )
        _call(NavigationActions._switch_source, stub, Source.SOUNDCLOUD)
        self._run_deferred(stub)
        stub._hide_liked.assert_called_once_with()
        stub._hide_following.assert_called_once_with()
        stub._hide_watchlist.assert_called_once_with()
        stub._hide_tv_series.assert_called_once()
        assert nav == []

    def test_tv_movies_enters_landing(self):
        stub = _make_stub(_sources=self._plugins(Source.TV_MOVIES))
        _call(NavigationActions._switch_source, stub, Source.TV_MOVIES)
        self._run_deferred(stub)
        stub._enter_landing_mode.assert_called_once_with()

    def test_youtube_playing_enters_landing(self):
        rv = _FakeWidget()
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _is_playing=True,
            _now_playing_data={"yt_id": "x"},
            _sources=self._plugins(Source.YOUTUBE),
            _w_results_list=rv,
        )
        _call(NavigationActions._switch_source, stub, Source.YOUTUBE)
        self._run_deferred(stub)
        stub._enter_landing_mode.assert_called_once_with()

    def test_youtube_not_playing_enters_landing(self):
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _sources=self._plugins(Source.YOUTUBE),
        )
        _call(NavigationActions._switch_source, stub, Source.YOUTUBE)
        self._run_deferred(stub)
        stub._enter_landing_mode.assert_called_once_with()

    def test_soundcloud_with_client_id_no_warning(self):
        stub = _make_stub(_sources=self._plugins(Source.SOUNDCLOUD))
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            _call(NavigationActions._switch_source, stub, Source.SOUNDCLOUD)
            self._run_deferred(stub)
        stub.notify.assert_not_called()

    def test_soundcloud_without_client_id_warns(self):
        stub = _make_stub(_sources=self._plugins(Source.SOUNDCLOUD))
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            _call(NavigationActions._switch_source, stub, Source.SOUNDCLOUD)
            self._run_deferred(stub)
        stub.notify.assert_called_once_with(
            "Soundcloud: API key not available, some features limited",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_youtube_empty_history_focus(self):
        hl = _FakeWidget()
        mc = SimpleNamespace(has_class=MagicMock(return_value=True))
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _sources=self._plugins(Source.YOUTUBE),
            _w_history_list=hl,
            _w_main_content=mc,
        )
        _call(NavigationActions._switch_source, stub, Source.YOUTUBE)
        self._run_deferred(stub)
        # YT landing focus is delegated to _set_focus_for_current_view so the
        # empty-state container is targeted instead of the empty ListView.
        assert hl.focus_calls == 0
        stub._set_focus_for_current_view.assert_called()
        stub.call_after_refresh.assert_any_call(stub._update_landing_chrome)

    def test_youtube_empty_history_hidden_no_focus(self):
        hl = _FakeWidget()
        hl.display = False
        stub = _make_stub(
            _source=Source.SOUNDCLOUD,
            _sources=self._plugins(Source.YOUTUBE),
            _w_history_list=hl,
        )
        _call(NavigationActions._switch_source, stub, Source.YOUTUBE)
        self._run_deferred(stub)
        assert hl.focus_calls == 0
        stub._set_focus_for_current_view.assert_not_called()


class TestFinalizeSwitchChrome:
    """``_finalize_switch_chrome`` re-applies focus + bars after a switch."""

    def test_reapplies_focus_and_bars(self):
        stub = _make_stub()
        NavigationActions._finalize_switch_chrome(stub)
        stub._set_focus_for_current_view.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()
        stub._update_keybind_bar.assert_called_once_with()


class TestActionSwitchSource1To4:
    def test_switch_1(self):
        stub = _make_stub()
        NavigationActions.action_switch_source_1(stub)
        stub._switch_source.assert_called_once_with(Source.YOUTUBE)

    def test_switch_2_same_source_returns(self):
        stub = _make_stub(_source=Source.SOUNDCLOUD)
        NavigationActions.action_switch_source_2(stub)
        stub._switch_source.assert_not_called()

    def test_switch_2(self):
        stub = _make_stub()
        NavigationActions.action_switch_source_2(stub)
        stub._switch_source.assert_called_once_with(Source.SOUNDCLOUD)

    def test_switch_3_same_source_returns(self):
        stub = _make_stub(_source=Source.RADIO)
        NavigationActions.action_switch_source_3(stub)
        stub._switch_source.assert_not_called()

    def test_switch_3(self):
        stub = _make_stub()
        NavigationActions.action_switch_source_3(stub)
        stub._switch_source.assert_called_once_with(Source.RADIO)

    def test_switch_4(self):
        stub = _make_stub()
        NavigationActions.action_switch_source_4(stub)
        stub._switch_source.assert_called_once_with(Source.TV_MOVIES)


class TestActionQuit:
    def test_first_press_warns(self, patch_clock):
        stub = _make_stub()
        NavigationActions.action_quit(stub)
        stub.notify.assert_called_once_with(
            "Press q again to quit",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_CONFIRM,
        )
        stub.exit.assert_not_called()
        assert stub._last_quit_press == 0.0

    def test_double_press_exits(self, patch_clock):
        patch_clock.set_monotonic(10.0)
        spinner = _FakeTimer()
        conn = _FakeTimer()
        stub = _make_stub(
            _play_spinner_timer=spinner,
            _connectivity_timer=conn,
        )
        NavigationActions.action_quit(stub)
        patch_clock.set_monotonic(10.5)
        NavigationActions.action_quit(stub)
        assert spinner.stops == 1
        assert conn.stops == 1
        assert stub._play_spinner_timer is None
        stub._stop_playback.assert_called_once_with()
        stub.exit.assert_called_once_with()
        stub.notify.assert_called_once_with(
            "Press q again to quit",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_CONFIRM,
        )

    def test_stale_press_is_fresh(self, patch_clock):
        stub = _make_stub()
        NavigationActions.action_quit(stub)
        patch_clock.set_monotonic(2.0)
        NavigationActions.action_quit(stub)
        assert stub.notify.call_count == 2
        stub.exit.assert_not_called()


class TestQueueTrendingGenre:
    def test_success_dispatches_to_process(self, wrapped):
        stub = _make_stub()
        results = [{"yt_id": "a", "title": "A"}]
        with patch(
            "nyrx.actions.navigation.fetch_trending_playlist", return_value=results
        ) as fetch:
            wrapped(NavigationActions._queue_trending_genre)(stub, "techno")
        fetch.assert_called_once_with("techno", country_code="us")
        stub.call_from_thread.assert_called_once_with(
            stub._process_trending_results,
            results,
            "Techno",
        )

    def test_unknown_slug_label_falls_back(self, wrapped):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.fetch_trending_playlist", return_value=[]):
            wrapped(NavigationActions._queue_trending_genre)(stub, "weird-slug")
        stub._process_trending_results.assert_not_called()
        stub.call_from_thread.assert_has_calls(
            [
                call(stub._stop_trending),
                call(
                    stub.notify,
                    "No trending tracks found for weird-slug",
                    timeout=TIMEOUT_INFO,
                ),
            ]
        )

    def test_fetch_failure_notifies(self, wrapped):
        stub = _make_stub()
        with patch(
            "nyrx.actions.navigation.fetch_trending_playlist",
            side_effect=RuntimeError("boom"),
        ):
            wrapped(NavigationActions._queue_trending_genre)(stub, "techno")
        stub.call_from_thread.assert_has_calls(
            [
                call(stub._stop_trending),
                call(
                    stub.notify,
                    "Failed to fetch Techno trending: boom",
                    severity=SEVERITY_ERROR,
                    timeout=TIMEOUT_ERROR,
                    title="Error",
                ),
            ]
        )

    def test_empty_results_notifies(self, wrapped):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.fetch_trending_playlist", return_value=[]):
            wrapped(NavigationActions._queue_trending_genre)(stub, "techno")
        stub._process_trending_results.assert_not_called()
        stub.call_from_thread.assert_has_calls(
            [
                call(stub._stop_trending),
                call(
                    stub.notify,
                    "No trending tracks found for Techno",
                    timeout=TIMEOUT_INFO,
                ),
            ]
        )


class TestProcessTrendingResults:
    def _results(self, n):
        return [{"yt_id": f"id{i}", "title": f"T{i}"} for i in range(1, n + 1)]

    def test_single_result_plays_only(self):
        queue = PlaybackQueue()
        stub = _make_stub(_playback_queue=queue)
        _call(
            NavigationActions._process_trending_results,
            stub,
            self._results(1),
            "Techno",
        )
        stub._play.assert_called_once()
        req = stub._play.call_args.args[0]
        assert req.data["yt_id"] == "id1"
        assert req.audio_only is True
        assert req.source == "soundcloud"
        assert len(queue) == 0
        stub._sync_np_widget.assert_called_once_with()
        stub._refresh_queue_modal.assert_called_once_with()
        stub.notify.assert_called_once_with(
            "Queued 1 Techno trending tracks", timeout=TIMEOUT_CONFIRM
        )

    def test_three_results_queue_rest(self):
        queue = PlaybackQueue()
        stub = _make_stub(_playback_queue=queue)
        _call(
            NavigationActions._process_trending_results, stub, self._results(3), "House"
        )
        assert stub._play.call_count == 1
        assert [q.yt_id for q in queue.items] == ["id2", "id3"]
        stub.notify.assert_called_once_with(
            "Queued 3 House trending tracks", timeout=TIMEOUT_CONFIRM
        )
        stub._stop_trending.assert_called_once_with()

    def test_empty_results_still_notifies(self):
        queue = PlaybackQueue()
        stub = _make_stub(_playback_queue=queue)
        _call(NavigationActions._process_trending_results, stub, [], "Pop")
        stub._play.assert_not_called()
        stub.notify.assert_called_once_with(
            "Queued 0 Pop trending tracks", timeout=TIMEOUT_CONFIRM
        )


class TestActionToggleAudio:
    def _tv_source(self, mode, note=None):
        return SimpleNamespace(
            cycle_server=MagicMock(return_value=mode),
            _dispatcher=SimpleNamespace(
                get_server=MagicMock(
                    return_value={"display_name": "Alpha", "notes": note}
                    if note
                    else None,
                ),
            ),
        )

    def test_tv_movies_auto_no_notify(self):
        src = self._tv_source("auto")
        stub = _make_stub(_source=Source.TV_MOVIES, _sources={"tv_movies": src})
        NavigationActions.action_toggle_audio(stub)
        src._dispatcher.get_server.assert_not_called()
        stub.notify.assert_not_called()

    def test_tv_movies_non_auto_with_note(self):
        src = self._tv_source("scraper", note="fast")
        stub = _make_stub(_source=Source.TV_MOVIES, _sources={"tv_movies": src})
        NavigationActions.action_toggle_audio(stub)
        src._dispatcher.get_server.assert_called_once_with("scraper")
        stub.notify.assert_called_once_with(
            "[b]Alpha[/b]: fast",
            title="Server",
            severity=SEVERITY_INFORMATION,
            timeout=TIMEOUT_INFO,
        )

    def test_tv_movies_non_auto_without_note(self):
        src = self._tv_source("scraper")
        stub = _make_stub(_source=Source.TV_MOVIES, _sources={"tv_movies": src})
        NavigationActions.action_toggle_audio(stub)
        stub.notify.assert_not_called()

    def test_soundcloud_returns(self):
        stub = _make_stub(_source=Source.SOUNDCLOUD)
        NavigationActions.action_toggle_audio(stub)
        stub._update_mode_indicator.assert_not_called()

    def test_radio_returns(self):
        stub = _make_stub(_source=Source.RADIO)
        NavigationActions.action_toggle_audio(stub)
        stub._update_mode_indicator.assert_not_called()

    def test_youtube_toggles_audio_only(self):
        stub = _make_stub(_source=Source.YOUTUBE, _audio_only=False)
        NavigationActions.action_toggle_audio(stub)
        assert stub._audio_only is True
        stub._update_mode_indicator.assert_called_once_with()
        stub._on_source_changed.assert_called_once_with()


class TestActionOpenFilter:
    def test_non_radio_returns(self):
        stub = _make_stub(_source=Source.YOUTUBE)
        NavigationActions.action_open_filter(stub)
        stub.push_screen.assert_not_called()

    def test_no_index_notifies(self):
        stub = _make_stub(_source=Source.RADIO, _station_index=None)
        NavigationActions.action_open_filter(stub)
        stub.notify.assert_called_once_with(
            "Radio station index not loaded yet.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub.push_screen.assert_not_called()

    def test_empty_index_notifies(self):
        idx = SimpleNamespace(stations=[])
        stub = _make_stub(_source=Source.RADIO, _station_index=idx)
        NavigationActions.action_open_filter(stub)
        stub.notify.assert_called_once_with(
            "Radio station index not loaded yet.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_pushes_modal(self):
        idx = SimpleNamespace(stations=[{"stationuuid": "u"}])
        stub = _make_stub(
            _source=Source.RADIO,
            _station_index=idx,
            _radio_filter_name="n",
            _radio_filter_tags=["t"],
            _radio_filter_countries=["c"],
        )
        with patch("nyrx.actions.navigation.RadioFilterModal") as modal:
            NavigationActions.action_open_filter(stub)
        modal.assert_called_once_with(
            idx,
            initial_name="n",
            initial_tags=["t"],
            initial_countries=["c"],
        )
        stub.push_screen.assert_called_once_with(
            modal.return_value, stub._on_filter_applied
        )


class TestOnFilterApplied:
    def test_none_returns(self):
        stub = _make_stub()
        _call(NavigationActions._on_filter_applied, stub, None)
        stub._populate_radio_list.assert_not_called()

    def test_applies_filter(self):
        stub = _make_stub(_radio_page=3)
        _call(
            NavigationActions._on_filter_applied,
            stub,
            {
                "name": "n1",
                "tags": ["a"],
                "countries": ["b"],
            },
        )
        assert stub._radio_page == 0
        assert stub._radio_filter_name == "n1"
        assert stub._radio_filter_tags == ["a"]
        assert stub._radio_filter_countries == ["b"]
        stub._populate_radio_list.assert_called_once_with()
        stub._update_sidebar_context.assert_called_once_with()


class TestActionGoHome:
    def test_stack_gt_one_returns(self):
        stub = _make_stub(screen_stack=[object(), object()])
        NavigationActions.action_go_home(stub)
        stub._enter_landing_mode.assert_not_called()

    def test_radio_returns(self):
        stub = _make_stub(_source=Source.RADIO)
        NavigationActions.action_go_home(stub)
        stub._enter_landing_mode.assert_not_called()

    def test_in_tv_series_hides(self):
        w = SimpleNamespace()
        stub = _make_stub(_in_tv_series=True, _w_tv_series=w)
        NavigationActions.action_go_home(stub)
        stub._hide_tv_series.assert_called_once_with(w)
        stub._enter_landing_mode.assert_not_called()

    def test_in_watchlist_hides(self):
        stub = _make_stub(_in_watchlist=True)
        NavigationActions.action_go_home(stub)
        stub._hide_watchlist.assert_called_once_with()

    def test_in_liked_hides(self):
        stub = _make_stub(_in_liked=True)
        NavigationActions.action_go_home(stub)
        stub._hide_liked.assert_called_once_with()

    def test_in_artist_profile_hides(self):
        stub = _make_stub(_in_artist_profile=True)
        NavigationActions.action_go_home(stub)
        stub._hide_artist_profile.assert_called_once_with()

    def test_in_following_hides(self):
        stub = _make_stub(_in_following=True)
        NavigationActions.action_go_home(stub)
        stub._hide_following.assert_called_once_with()

    def test_main_content_none_returns(self):
        stub = _make_stub(_w_main_content=None)
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            NavigationActions.action_go_home(stub)
        mock_logger.debug.assert_called()
        stub._enter_landing_mode.assert_not_called()

    def test_empty_results_in_landing_returns(self):
        mc = SimpleNamespace(has_class=MagicMock(return_value=True))
        stub = _make_stub(_all_results=[], _w_main_content=mc)
        NavigationActions.action_go_home(stub)
        stub._enter_landing_mode.assert_not_called()

    def test_normal_saves_state_and_enters_landing(self):
        rv = _FakeWidget()
        mc = SimpleNamespace(has_class=MagicMock(return_value=False))
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _query="q",
            _all_results=[{"yt_id": "a"}],
            _page=1,
            _w_results_list=rv,
            _w_main_content=mc,
        )
        NavigationActions.action_go_home(stub)
        assert stub._source_states["youtube"] == {
            "query": "q",
            "results": [{"yt_id": "a"}],
            "page": 1,
        }
        assert stub._all_results == []
        assert stub._query == ""
        assert rv.clear_calls == 1
        stub._enter_landing_mode.assert_called_once_with()

    def test_source_none_skips_save(self):
        stub = _make_stub(_source=None)
        NavigationActions.action_go_home(stub)
        assert stub._source_states == {}

    def test_youtube_playing_enters_landing(self):
        rv = _FakeWidget()
        mc = SimpleNamespace(has_class=MagicMock(return_value=False))
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _is_playing=True,
            _all_results=[{"yt_id": "a"}],
            _w_results_list=rv,
            _w_main_content=mc,
        )
        NavigationActions.action_go_home(stub)
        assert stub._all_results == []
        assert stub._query == ""
        assert rv.clear_calls == 1
        stub._enter_landing_mode.assert_called_once_with()

    def test_youtube_playing_missing_widgets(self):
        stub = _make_stub(
            _source=Source.YOUTUBE,
            _is_playing=True,
            _all_results=[{"yt_id": "a"}],
            _w_results_list=None,
            _w_empty_state=None,
            _w_main_content=SimpleNamespace(has_class=MagicMock(return_value=False)),
        )
        with patch("nyrx.actions.navigation.logger"):
            NavigationActions.action_go_home(stub)
        stub._enter_landing_mode.assert_called_once_with()


class TestActionSetQuality:
    def test_pushes_modal(self):
        stub = _make_stub(_quality="best")
        with patch("nyrx.actions.navigation.QualitySelector") as modal:
            NavigationActions.action_set_quality(stub)
        modal.assert_called_once_with("best")
        stub.push_screen.assert_called_once_with(
            modal.return_value, stub._on_quality_selected
        )

    def test_on_selected_none_returns(self):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.update_config") as mock_update:
            _call(NavigationActions._on_quality_selected, stub, None)
        mock_update.assert_not_called()

    def test_on_selected_saves(self):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.update_config") as mock_update:
            _call(NavigationActions._on_quality_selected, stub, "1080p")
        assert stub._quality == "1080p"
        mock_update.assert_called_once_with(quality="1080p")
        stub._update_sidebar_context.assert_called_once_with()


class TestActionSetTrendingRegion:
    def test_pushes_modal(self):
        stub = _make_stub(_trending_region="us")
        with patch("nyrx.actions.navigation.TrendingRegionSelector") as modal:
            NavigationActions.action_set_trending_region(stub)
        modal.assert_called_once_with("us")
        stub.push_screen.assert_called_once_with(
            modal.return_value, stub._on_trending_region_selected
        )

    def test_on_selected_none_returns(self):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.update_config") as mock_update:
            _call(NavigationActions._on_trending_region_selected, stub, None)
        mock_update.assert_not_called()

    def test_on_selected_saves_and_updates_label(self):
        stub = _make_stub()
        with patch("nyrx.actions.navigation.update_config") as mock_update:
            _call(NavigationActions._on_trending_region_selected, stub, "de")
        assert stub._trending_region == "de"
        mock_update.assert_called_once_with(trending_region="de")
        stub.query_one.return_value.update.assert_called_once_with(
            "TRENDING  [#b0b0b0](de)[/]\n",
        )

    def test_on_selected_label_query_failure_swallowed(self):
        stub = _make_stub(query_one=MagicMock(side_effect=RuntimeError("boom")))
        with patch("nyrx.actions.navigation.logger") as mock_logger:
            _call(NavigationActions._on_trending_region_selected, stub, "de")
        assert stub._trending_region == "de"
        mock_logger.debug.assert_called()


class _DummyModal:
    def __init__(self, *args, **kwargs):
        self.args = args
        self.kwargs = kwargs


class TestKeyReference:
    def test_key_question_mark_schedules_push(self):
        stub = _make_stub()
        NavigationActions.key_question_mark(stub)
        stub.call_after_refresh.assert_called_once_with(stub._push_key_reference)

    def test_push_already_on_modal_returns(self):
        stub = _make_stub()
        with patch("nyrx.screens.KeyReferenceModal", _DummyModal):
            stub.screen = _DummyModal()
            _call(NavigationActions._push_key_reference, stub)
        stub.push_screen.assert_not_called()

    def test_push_with_context(self):
        stub = _make_stub(_detect_key_context=MagicMock(return_value="home"))
        with patch("nyrx.screens.KeyReferenceModal", _DummyModal):
            _call(NavigationActions._push_key_reference, stub)
        pushed = stub.push_screen.call_args.args[0]
        assert isinstance(pushed, _DummyModal)
        assert pushed.kwargs == {"kr_context": "home"}

    def test_push_sidebar_focused_uses_np(self):
        stub = _make_stub(_side_focused=True, _sc_np_focused=False)
        with patch("nyrx.screens.KeyReferenceModal", _DummyModal):
            _call(NavigationActions._push_key_reference, stub)
        pushed = stub.push_screen.call_args.args[0]
        assert pushed.kwargs == {"kr_context": "np"}

    def test_push_sidebar_focused_sc_np_uses_np_sc(self):
        stub = _make_stub(_side_focused=True, _sc_np_focused=True)
        with patch("nyrx.screens.KeyReferenceModal", _DummyModal):
            _call(NavigationActions._push_key_reference, stub)
        pushed = stub.push_screen.call_args.args[0]
        assert pushed.kwargs == {"kr_context": "np-sc"}

    def test_blocked_on_home_screen(self):
        from nyrx.screens.home import HomeScreen

        stub = _make_stub(screen=HomeScreen.__new__(HomeScreen))
        NavigationActions.key_question_mark(stub)
        stub.call_after_refresh.assert_not_called()

    def test_blocked_on_min_size_modal(self):
        from nyrx.screens.min_size import MinSizeModal

        stub = _make_stub(screen=MinSizeModal.__new__(MinSizeModal))
        NavigationActions.key_question_mark(stub)
        stub.call_after_refresh.assert_not_called()

    def test_blocked_when_mode_overlay_visible(self):
        visible_widget = SimpleNamespace(has_class=MagicMock(return_value=True))
        stub = _make_stub(query_one=MagicMock(return_value=visible_widget))
        NavigationActions.key_question_mark(stub)
        stub.call_after_refresh.assert_not_called()

    def test_allowed_when_overlay_hidden(self):
        stub = _make_stub()
        NavigationActions.key_question_mark(stub)
        stub.call_after_refresh.assert_called_once_with(stub._push_key_reference)

    def test_open_commands(self):
        stub = _make_stub()
        with patch("nyrx.commands.CommandScreen") as screen:
            NavigationActions.action_open_commands(stub)
        stub.push_screen.assert_called_once_with(screen.return_value)


class TestActionCommandPalette:
    """Block Textual's command palette on the startup screens."""

    def test_blocked_on_home_screen(self):
        from nyrx.screens.home import HomeScreen

        stub = _make_stub(screen=HomeScreen.__new__(HomeScreen))
        with patch("nyrx.actions.navigation.App.action_command_palette") as mock:
            NavigationActions.action_command_palette(stub)
        mock.assert_not_called()

    def test_blocked_on_min_size_modal(self):
        from nyrx.screens.min_size import MinSizeModal

        stub = _make_stub(screen=MinSizeModal.__new__(MinSizeModal))
        with patch("nyrx.actions.navigation.App.action_command_palette") as mock:
            NavigationActions.action_command_palette(stub)
        mock.assert_not_called()

    def test_allowed_elsewhere(self):
        other_screen = SimpleNamespace()
        stub = _make_stub(screen=other_screen)
        with patch("nyrx.actions.navigation.App.action_command_palette") as mock:
            NavigationActions.action_command_palette(stub)
        mock.assert_called_once_with(stub)


class TestActionHelpQuit:
    """Suppress Textual's Ctrl+C 'Press q to quit' notification."""

    def test_noop_does_not_notify(self):
        stub = _make_stub()
        NavigationActions.action_help_quit(stub)

    def test_noop_does_not_exit(self):
        stub = _make_stub(exit=MagicMock())
        NavigationActions.action_help_quit(stub)
        stub.exit.assert_not_called()
