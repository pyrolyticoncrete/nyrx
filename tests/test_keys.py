# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for handlers/keys.py: context detection and keybind bar routing.

``detect_key_context`` and ``detect_sidebar_key_context`` are pure functions.
``KeyHandlers._update_keybind_bar`` routing is tested via a lightweight stub
that implements just enough of the protocol for the method to run.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from nyrx.bindings import KEYBIND_BAR_TEXT, SIDEBAR_KEYBIND_TEXT
from nyrx.handlers.keys import (
    KeyHandlers,
    detect_key_context,
    detect_sidebar_key_context,
)
from nyrx.modes import Source


class TestDetectKeyContext:
    @pytest.mark.parametrize(
        (
            "in_liked",
            "in_following",
            "in_following_feed",
            "in_artist_profile",
            "source",
            "is_landing_mode",
            "has_results",
            "expected",
        ),
        [
            # Liked takes precedence over all else
            (True, False, False, False, Source.YOUTUBE, False, False, "liked"),
            # Following with feed focus
            (False, True, True, False, Source.YOUTUBE, False, False, "feed"),
            # Following with sidebar or unknown focus
            (False, True, False, False, Source.YOUTUBE, False, False, "following"),
            # Artist profile
            (False, False, False, True, Source.YOUTUBE, False, False, "artist-profile"),
            # SoundCloud landing vs search
            (False, False, False, False, Source.SOUNDCLOUD, True, False, "sc-home"),
            (False, False, False, False, Source.SOUNDCLOUD, False, False, "sc-search"),
            # Radio
            (False, False, False, False, Source.RADIO, False, False, "radio"),
            # TV/Movies: landing or no results → home
            (
                False,
                False,
                False,
                False,
                Source.TV_MOVIES,
                True,
                False,
                "tv-movies-home",
            ),
            # TV/Movies: not landing + has results → search
            (
                False,
                False,
                False,
                False,
                Source.TV_MOVIES,
                False,
                True,
                "tv-movies-search",
            ),
            # TV/Movies: not landing + no results → home
            (
                False,
                False,
                False,
                False,
                Source.TV_MOVIES,
                False,
                False,
                "tv-movies-home",
            ),
            # YouTube: landing → home
            (False, False, False, False, Source.YOUTUBE, True, False, "yt-home"),
            # YouTube: not landing + has results → search
            (False, False, False, False, Source.YOUTUBE, False, True, "yt-search"),
            # YouTube: not landing + no results → home
            (False, False, False, False, Source.YOUTUBE, False, False, "yt-home"),
        ],
    )
    def test_detect_key_context(
        self,
        in_liked: bool,
        in_following: bool,
        in_following_feed: bool,
        in_artist_profile: bool,
        source: Source,
        is_landing_mode: bool,
        has_results: bool,
        expected: str,
    ) -> None:
        assert (
            detect_key_context(
                in_liked=in_liked,
                in_following=in_following,
                in_following_feed=in_following_feed,
                in_artist_profile=in_artist_profile,
                source=source,
                is_landing_mode=is_landing_mode,
                has_results=has_results,
            )
            == expected
        )


class TestDetectSidebarKeyContext:
    @pytest.mark.parametrize(
        ("sc_np_focused", "expected"),
        [
            (False, "np"),
            (True, "np-sc"),
        ],
    )
    def test_detect_sidebar_key_context(
        self, sc_np_focused: bool, expected: str
    ) -> None:
        assert detect_sidebar_key_context(sc_np_focused=sc_np_focused) == expected


# ── Stub helpers for _update_keybind_bar routing tests ────────────


class _FakeStyles:
    def __init__(self) -> None:
        self.display = "block"


class _FakeWidget:
    def __init__(self) -> None:
        self.styles = _FakeStyles()
        self.updates: list[str] = []

    def update(self, text: str = "") -> None:
        self.updates.append(text)


class _FakeBar:
    """Fake liked/watchlist screen with a query_one that returns a tracked bar."""

    def __init__(self, bar_id: str) -> None:
        self._bar = _FakeWidget()
        self._bar_id = bar_id

    def query_one(self, selector: str, cls: object = None) -> _FakeWidget:
        if selector == self._bar_id:
            return self._bar
        return _FakeWidget()


class _FakeTVSeries:
    def __init__(self) -> None:
        self.tvs_bar = _FakeWidget()

    def query_one(self, selector: str, cls: object = None) -> _FakeWidget:
        if selector == "#tvs-keybind-bar":
            return self.tvs_bar
        return _FakeWidget()


class _FakeMainContent:
    def __init__(self, landing: bool = False) -> None:
        self._landing = landing

    def has_class(self, name: str) -> bool:
        return self._landing and name == "landing-mode"


class _Stub:
    """Minimal self-like object for _update_keybind_bar routing tests."""

    def __init__(self, **kw: object) -> None:
        self._side_focused: bool = kw.get("_side_focused", False)  # type: ignore[assignment]
        self._sc_np_focused: bool = kw.get("_sc_np_focused", False)  # type: ignore[assignment]
        self._in_tv_series: bool = kw.get("_in_tv_series", False)  # type: ignore[assignment]
        self._br = _FakeWidget()
        self._liked_bar = (
            _FakeBar("#ls-keybind-bar") if kw.get("_w_liked_screen") else None
        )
        self._watchlist_bar = (
            _FakeBar("#wl-keybind-bar") if kw.get("_w_watchlist_screen") else None
        )
        self._hint = _FakeWidget() if kw.get("_w_radio_filter_hint") else None
        self._tv_series = _FakeTVSeries() if kw.get("_w_tv_series") else None
        self._mc = kw.get("_w_main_content", None)
        self._detect_key_context = MagicMock(return_value=kw.get("_context", "yt-home"))

    def _sidebar_keybind_text(self) -> str | None:
        if not self._side_focused:
            return None
        from nyrx.handlers.keys import detect_sidebar_key_context

        ctx = detect_sidebar_key_context(sc_np_focused=self._sc_np_focused)
        from nyrx.bindings import SIDEBAR_KEYBIND_TEXT

        return SIDEBAR_KEYBIND_TEXT.get(ctx, "")

    @property
    def _w_liked_screen(self) -> _FakeBar | None:
        return self._liked_bar

    @property
    def _w_watchlist_screen(self) -> _FakeBar | None:
        return self._watchlist_bar

    @property
    def _w_radio_filter_hint(self) -> _FakeWidget | None:
        return self._hint

    @property
    def _w_tv_series(self) -> _FakeTVSeries | None:
        return self._tv_series

    @property
    def _w_main_content(self) -> _FakeMainContent | None:
        return self._mc

    @_w_main_content.setter
    def _w_main_content(self, val: object) -> None:
        self._mc = val  # type: ignore[attr-defined]

    def query_one(self, selector: str, cls: object = None) -> _FakeWidget:
        if selector == "#welcome-bottomright":
            return self._br
        return _FakeWidget()


def _run_bar(**kw: object) -> _Stub:
    stub = _Stub(**kw)
    KeyHandlers._update_keybind_bar(stub)
    return stub


class TestUpdateKeybindBarRouting:
    def test_radio_sidebar_hint_np(self) -> None:
        stub = _run_bar(
            _context="radio",
            _side_focused=True,
            _sc_np_focused=False,
            _w_radio_filter_hint=True,
        )
        assert stub._br.styles.display == "none"
        assert stub._br.updates == [""]
        assert stub._hint.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_radio_table_hint_radio_text(self) -> None:
        stub = _run_bar(
            _context="radio",
            _side_focused=False,
            _w_radio_filter_hint=True,
        )
        assert stub._br.styles.display == "none"
        assert stub._hint.updates == [KEYBIND_BAR_TEXT["radio"]]

    def test_liked_sidebar_hint_np(self) -> None:
        stub = _run_bar(
            _context="liked",
            _side_focused=True,
            _sc_np_focused=False,
            _w_liked_screen=True,
        )
        assert stub._br.styles.display == "none"
        assert stub._liked_bar._bar.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_liked_list_hint_liked_text(self) -> None:
        stub = _run_bar(
            _context="liked",
            _side_focused=False,
            _w_liked_screen=True,
        )
        assert stub._liked_bar._bar.updates == [KEYBIND_BAR_TEXT["liked"]]

    def test_watchlist_sidebar_hint_np(self) -> None:
        stub = _run_bar(
            _context="watchlist",
            _side_focused=True,
            _w_watchlist_screen=True,
        )
        assert stub._br.styles.display == "none"
        assert stub._watchlist_bar._bar.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_watchlist_list_hint_watchlist_text(self) -> None:
        stub = _run_bar(
            _context="watchlist",
            _side_focused=False,
            _w_watchlist_screen=True,
        )
        assert stub._watchlist_bar._bar.updates == [KEYBIND_BAR_TEXT["watchlist"]]

    def test_yt_search_sidebar_np(self) -> None:
        stub = _run_bar(
            _context="yt-search",
            _side_focused=True,
        )
        assert stub._br.styles.display == "block"
        assert stub._br.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_yt_search_content_yt_text(self) -> None:
        stub = _run_bar(
            _context="yt-search",
            _side_focused=False,
        )
        assert stub._br.styles.display == "block"
        assert stub._br.updates == [KEYBIND_BAR_TEXT["yt-search"]]

    def test_sc_sidebar_np_sc(self) -> None:
        stub = _run_bar(
            _context="sc-search",
            _side_focused=True,
            _sc_np_focused=True,
        )
        assert stub._br.styles.display == "block"
        assert stub._br.updates == [SIDEBAR_KEYBIND_TEXT["np-sc"]]

    def test_series_sidebar_np(self) -> None:
        stub = _run_bar(
            _in_tv_series=True,
            _side_focused=True,
            _w_tv_series=True,
        )
        assert stub._br.styles.display == "none"
        assert stub._tv_series.tvs_bar.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_series_content_series_text(self) -> None:
        stub = _run_bar(
            _in_tv_series=True,
            _side_focused=False,
            _w_tv_series=True,
        )
        assert stub._tv_series.tvs_bar.updates == [KEYBIND_BAR_TEXT["tv-movies-series"]]

    def test_landing_sidebar_np(self) -> None:
        stub = _run_bar(
            _context="sc-home",
            _side_focused=True,
            _w_main_content=_FakeMainContent(landing=True),
        )
        assert stub._br.styles.display == "block"
        assert stub._br.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_landing_content_home_text(self) -> None:
        stub = _run_bar(
            _context="sc-home",
            _side_focused=False,
            _w_main_content=_FakeMainContent(landing=True),
        )
        assert stub._br.styles.display == "block"
        assert stub._br.updates == [KEYBIND_BAR_TEXT["sc-home"]]

    def test_yt_home_sidebar_np(self) -> None:
        """YouTube landing with sidebar focused → main bar shows np."""
        stub = _run_bar(
            _context="yt-home",
            _side_focused=True,
            _w_main_content=_FakeMainContent(landing=True),
        )
        assert stub._br.styles.display == "block"
        assert stub._br.updates == [SIDEBAR_KEYBIND_TEXT["np"]]

    def test_radio_hint_none_falls_to_else(self) -> None:
        """Radio context with no hint widget → falls through to else (hidden)."""
        stub = _run_bar(
            _context="radio",
            _side_focused=False,
        )
        assert stub._br.styles.display == "none"
        assert stub._br.updates == [""]
