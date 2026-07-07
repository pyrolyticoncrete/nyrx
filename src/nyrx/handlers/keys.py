# SPDX-License-Identifier: AGPL-3.0-only

"""Keybinding context detection and keybind bar updates."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from textual.widgets import Static

from nyrx.bindings import KEYBIND_BAR_TEXT, SIDEBAR_KEYBIND_TEXT
from nyrx.modes import Source

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol


def detect_key_context(
    *,
    in_liked: bool = False,
    in_watchlist: bool = False,
    in_following: bool = False,
    in_following_feed: bool = False,
    in_artist_profile: bool = False,
    source: Source = Source.YOUTUBE,
    is_landing_mode: bool = False,
    has_results: bool = False,
) -> str:
    """Detect the current screen context for keybinding display.

    Pure function extracted from ``KeyHandlers._detect_key_context``.
    All inputs are pre-computed booleans: callers are responsible for
    widget-ancestry traversal (``in_following_feed``) and ``None``-safe
    landing-mode checks.
    """
    if in_liked:
        return "liked"
    if in_watchlist:
        return "watchlist"
    if in_following:
        return "feed" if in_following_feed else "following"
    if in_artist_profile:
        return "artist-profile"
    if source == Source.SOUNDCLOUD:
        return "sc-home" if is_landing_mode else "sc-search"
    if source == Source.RADIO:
        return "radio"
    if source == Source.TV_MOVIES:
        return (
            "tv-movies-home"
            if is_landing_mode or not has_results
            else "tv-movies-search"
        )
    if source == Source.YOUTUBE:
        return "yt-home" if is_landing_mode or not has_results else "yt-search"
    return "yt-home"


def detect_sidebar_key_context(*, sc_np_focused: bool = False) -> str:
    """Return the sidebar-specific keybind context based on NP widget focus."""
    return "np-sc" if sc_np_focused else "np"


class KeyHandlers:
    def _update_mode_indicator(self: MediaAppProtocol) -> None:
        """Update the sidebar footer to show available actions for the current focus."""
        mc = self._w_main_content
        if mc is not None and mc.has_class("landing-mode"):
            self._update_landing_chrome()
            return
        self._update_keybind_bar()

    def _detect_key_context(self: MediaAppProtocol) -> str:
        """Detect the current screen context for keybinding display."""
        if self._in_tv_series:
            return "tv-movies-series"
        in_following_feed = False
        if self._in_following and self.focused is not None:
            for node in [self.focused] + list(self.focused.ancestors):
                if node is self._w_fs_center_list:
                    in_following_feed = True
                    break
                if node is self._w_fs_left_list:
                    break
        mc = self._w_main_content
        is_landing = mc is not None and mc.has_class("landing-mode")
        ctx = detect_key_context(
            in_liked=self._in_liked,
            in_watchlist=self._in_watchlist,
            in_following=self._in_following,
            in_following_feed=in_following_feed,
            in_artist_profile=self._in_artist_profile,
            source=self._source or Source.YOUTUBE,
            is_landing_mode=is_landing,
            has_results=bool(self._all_results),
        )
        logger.debug("_detect_key_context: context=%s", ctx)
        return ctx

    def _sidebar_keybind_text(self: MediaAppProtocol) -> str | None:
        """Return sidebar keybind text if sidebar is focused, else None."""
        if not self._side_focused:
            return None
        ctx = detect_sidebar_key_context(sc_np_focused=self._sc_np_focused)
        return SIDEBAR_KEYBIND_TEXT.get(ctx, "")

    def _update_keybind_bar(self: MediaAppProtocol) -> None:
        """Update the keybinding bar for the current screen context."""
        try:
            side_text = self._sidebar_keybind_text()
            context = self._detect_key_context()
            hint = self._w_radio_filter_hint
            if hint is not None:
                hint.display = context == "radio"
            text = (
                side_text
                if side_text is not None
                else KEYBIND_BAR_TEXT.get(context, "")
            )
            br = self.query_one("#welcome-bottomright", Static)
            hint_display = (
                getattr(self._w_radio_filter_hint, "display", "?")
                if self._w_radio_filter_hint is not None
                else None
            )
            logger.debug(
                "_update_keybind_bar: context=%s side=%s target=%s text_len=%d br_display=%s hint_display=%s",
                context,
                side_text is not None,
                "hint" if context == "radio" else "br",
                len(text),
                getattr(br, "display", "?"),
                hint_display,
            )
            if self._in_tv_series:
                br.styles.display = "none"
                br.update("")
                ts = self._w_tv_series
                if ts is not None:
                    ts.query_one("#tvs-keybind-bar", Static).update(
                        side_text
                        if side_text is not None
                        else KEYBIND_BAR_TEXT.get("tv-movies-series", "")
                    )
                return
            if context == "liked" and self._w_liked_screen is not None:
                br.styles.display = "none"
                br.update("")
                if ls := self._w_liked_screen:
                    ls.query_one("#ls-keybind-bar", Static).update(text)
                else:
                    logger.debug("_update_keybind_bar: _w_liked_screen became None")
            elif context == "watchlist" and self._w_watchlist_screen is not None:
                br.styles.display = "none"
                br.update("")
                if wl := self._w_watchlist_screen:
                    wl.query_one("#wl-keybind-bar", Static).update(text)
                else:
                    logger.debug("_update_keybind_bar: _w_watchlist_screen became None")
            elif context == "radio" and self._w_radio_filter_hint is not None:
                br.styles.display = "none"
                br.update("")
                self._w_radio_filter_hint.update(text)
            elif (
                (mc := self._w_main_content) is not None
                and mc.has_class("landing-mode")
                or context in ("yt-search", "sc-search", "tv-movies-search")
                or side_text is not None
            ):
                br.styles.display = "block"
                br.update(text)
            else:
                br.styles.display = "none"
                br.update("")
        except Exception:
            logger.debug("Failed to update keybind bar")
