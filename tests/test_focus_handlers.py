# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``handlers/focus.py``: ``_focus_main_panel`` ESC-from-sidebar routing.

Bare ``object.__new__(FocusHandlers)`` stubs (via ``tests.fakes.stub_self``)
resolve the method under test while every side-effect caller
(``_set_focus_for_current_view``, ``_update_mode_indicator``, widget
``focus``/``query*``) is a recorded fake: no App boot.
"""

from __future__ import annotations

from unittest.mock import MagicMock, PropertyMock, patch

from textual.widgets import DataTable

from nyrx.handlers.focus import FocusHandlers
from nyrx.modes import Source, View
from tests.fakes import stub_self


class _Widget:
    """Minimal stand-in: focus-recording, with any extra attrs."""

    def __init__(self, **attrs):
        self.focus_calls = 0
        self.display = attrs.pop("display", True)
        self.__dict__.update(attrs)

    def focus(self):
        self.focus_calls += 1


def _make_stub(**overrides):
    defaults = {
        "_in_watchlist": False,
        "_in_liked": False,
        "_in_following": False,
        "_in_artist_profile": False,
        "_in_tv_series": False,
        "_w_watchlist_screen": None,
        "_w_liked_screen": None,
        "_w_fs_left_list": None,
        "_w_artist_profile": None,
        "_w_tv_series": None,
        "_update_mode_indicator": MagicMock(),
        "_set_focus_for_current_view": MagicMock(),
    }
    defaults.update(overrides)
    return stub_self(FocusHandlers, **defaults)


def test_watchlist_routes_escape_to_wl_list():
    dt = _Widget()
    wl = _Widget(query_one=MagicMock(return_value=dt))
    stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=wl)
    FocusHandlers._focus_main_panel(stub)
    wl.query_one.assert_called_once_with("#wl-list", DataTable)
    assert dt.focus_calls == 1


def test_watchlist_missing_screen_is_noop():
    stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=None)
    FocusHandlers._focus_main_panel(stub)


def test_liked_routes_escape_to_ls_list():
    dt = _Widget()
    ls = _Widget(query_one=MagicMock(return_value=dt))
    stub = _make_stub(_in_liked=True, _w_liked_screen=ls)
    FocusHandlers._focus_main_panel(stub)
    ls.query_one.assert_called_once_with("#ls-list", DataTable)
    assert dt.focus_calls == 1


def test_liked_missing_screen_is_noop():
    stub = _make_stub(_in_liked=True, _w_liked_screen=None)
    FocusHandlers._focus_main_panel(stub)


def test_following_routes_escape_to_left_list():
    lst = _Widget()
    stub = _make_stub(_in_following=True, _w_fs_left_list=lst)
    FocusHandlers._focus_main_panel(stub)
    assert lst.focus_calls == 1


def test_following_missing_left_list_is_noop():
    stub = _make_stub(_in_following=True, _w_fs_left_list=None)
    FocusHandlers._focus_main_panel(stub)


def test_artist_routes_escape_to_first_chip_when_present():
    dt = _Widget()
    chip = _Widget()
    ap = _Widget(
        query_one=MagicMock(return_value=dt), query=MagicMock(return_value=[chip])
    )
    stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap)
    FocusHandlers._focus_main_panel(stub)
    ap.query_one.assert_called_once_with("#ap-track-list", DataTable)
    assert chip.focus_calls == 1
    assert dt.focus_calls == 0


def test_artist_routes_escape_to_track_list_when_no_chips():
    dt = _Widget()
    ap = _Widget(query_one=MagicMock(return_value=dt), query=MagicMock(return_value=[]))
    stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap)
    FocusHandlers._focus_main_panel(stub)
    assert dt.focus_calls == 1


def test_tv_series_routes_escape_to_first_season_chip_when_present():
    dt = _Widget()
    chip = _Widget()
    tvs = _Widget(
        query_one=MagicMock(return_value=dt), query=MagicMock(return_value=[chip])
    )
    stub = _make_stub(_in_tv_series=True, _w_tv_series=tvs)
    FocusHandlers._focus_main_panel(stub)
    tvs.query_one.assert_called_once_with("#tvs-episodes", DataTable)
    assert chip.focus_calls == 1
    assert dt.focus_calls == 0
    stub._update_mode_indicator.assert_called_once()


def test_tv_series_routes_escape_to_episodes_when_no_chips():
    dt = _Widget()
    tvs = _Widget(
        query_one=MagicMock(return_value=dt), query=MagicMock(return_value=[])
    )
    stub = _make_stub(_in_tv_series=True, _w_tv_series=tvs)
    FocusHandlers._focus_main_panel(stub)
    assert dt.focus_calls == 1
    stub._update_mode_indicator.assert_called_once()


def test_tv_home_routes_escape_to_first_chip():
    chip = _Widget()
    home = _Widget(display=True, query=MagicMock(return_value=[chip]))
    stub = _make_stub(query_one=MagicMock(return_value=home))
    FocusHandlers._focus_main_panel(stub)
    home.query.assert_called_once_with(".tv-chip")
    assert chip.focus_calls == 1
    stub._update_mode_indicator.assert_called_once()


def test_sc_home_routes_escape_to_chip():
    chip = _Widget()
    rl = _Widget(children=[1])
    stub = _make_stub(
        query_one=MagicMock(
            side_effect=[_Widget(display=False), _Widget(display=True), chip, rl]
        ),
    )
    FocusHandlers._focus_main_panel(stub)
    assert chip.focus_calls == 1
    stub._update_mode_indicator.assert_called_once()


def test_sc_home_falls_back_to_recent_list():
    rl = _Widget(children=[1])
    stub = _make_stub(
        query_one=MagicMock(
            side_effect=[_Widget(display=False), _Widget(display=True), None, rl]
        ),
    )
    FocusHandlers._focus_main_panel(stub)
    assert rl.focus_calls == 1
    stub._update_mode_indicator.assert_called_once()


def test_default_delegates_to_set_focus_for_current_view():
    hidden = _Widget(display=False)
    stub = _make_stub(query_one=MagicMock(return_value=hidden))
    FocusHandlers._focus_main_panel(stub)
    stub._set_focus_for_current_view.assert_called_once_with()


class TestRenderFocusIndicatorsYtLanding:
    """``_render_focus_indicators`` YT-landing branch: content_focused includes empty state."""

    _BAR_ON = " [rgb(162,119,255)]\u2501\u2501\u2501\u2501\u2501[/]"
    _BAR_OFF = " [dim]\u2501\u2501\u2501\u2501\u2501[/dim]"
    _TOPRIGHT = "[white]\u25b6  VIDEO MODE  \u2022  FOCUS[/white]"
    _SIDEBAR = "[white]FOCUS[/white]"

    @staticmethod
    def _make_stub(focused, *, empty=None, history=None):
        empty = empty or _Widget()
        history = history or _Widget()
        wt = MagicMock()
        sf = MagicMock()
        return stub_self(
            FocusHandlers,
            focused=focused,
            screen_stack=[],
            _in_following=False,
            _in_liked=False,
            _in_watchlist=False,
            _in_artist_profile=False,
            _in_tv_series=False,
            _np_widgets={},
            _w_download=None,
            _source=Source.YOUTUBE,
            _view=View.LANDING,
            _audio_only=False,
            _w_main_content=MagicMock(has_class=MagicMock(return_value=True)),
            _w_empty_state=empty,
            _w_history_list=history,
            _w_welcome_topright=wt,
            _w_sidebar_focus=sf,
        )

    def test_empty_state_focus_lights_bar(self):
        es = _Widget()
        stub = self._make_stub(focused=es, empty=es)
        FocusHandlers._render_focus_indicators(stub)
        expected = f"{self._TOPRIGHT}{self._BAR_ON}"
        stub._w_welcome_topright.update.assert_called_once_with(expected)

    def test_history_focus_lights_bar(self):
        hl = _Widget()
        stub = self._make_stub(focused=hl, history=hl)
        FocusHandlers._render_focus_indicators(stub)
        expected = f"{self._TOPRIGHT}{self._BAR_ON}"
        stub._w_welcome_topright.update.assert_called_once_with(expected)

    def test_unrelated_focus_unlights_bar(self):
        other = _Widget()
        stub = self._make_stub(focused=other)
        FocusHandlers._render_focus_indicators(stub)
        expected = f"{self._TOPRIGHT}{self._BAR_OFF}"
        stub._w_welcome_topright.update.assert_called_once_with(expected)


class TestKeyTabYtLandingEmptyState:
    """``key_tab`` includes ``#empty-state`` in foci when no history."""

    @staticmethod
    def _tab_stub(**overrides):
        defaults = dict(
            focused=MagicMock(),
            _w_download=None,
            _in_watchlist=False,
            _in_liked=False,
            _in_following=False,
            _in_artist_profile=False,
            _in_tv_series=False,
            _w_watchlist_screen=None,
            _w_liked_screen=None,
            _w_fs_left_list=None,
            _w_artist_profile=None,
            _w_tv_series=None,
            _w_history_list=_Widget(children=[]),
            _w_results_list=_Widget(
                children=[], has_class=MagicMock(return_value=True)
            ),
            _w_radio_area=_Widget(display=False),
            _w_radio_list=None,
            _w_empty_state=_Widget(display=True),
            query_one=MagicMock(return_value=_Widget(display=False)),
            _displayed_np_widget=MagicMock(return_value=None),
            _update_mode_indicator=MagicMock(),
        )
        defaults.update(overrides)
        return stub_self(FocusHandlers, **defaults)

    def test_no_focus_when_no_history(self):
        hl = _Widget(children=[])
        stub = self._tab_stub(_w_history_list=hl)
        with patch.object(
            FocusHandlers,
            "_sidebar_state",
            new_callable=PropertyMock,
            return_value=(None, False),
        ):
            FocusHandlers.key_tab(stub)
        assert hl.focus_calls == 0

    def test_history_focused_when_history_present(self):
        hl = _Widget(children=[object()])
        stub = self._tab_stub(_w_history_list=hl)
        with patch.object(
            FocusHandlers,
            "_sidebar_state",
            new_callable=PropertyMock,
            return_value=(None, False),
        ):
            FocusHandlers.key_tab(stub)
        assert hl.focus_calls == 1
