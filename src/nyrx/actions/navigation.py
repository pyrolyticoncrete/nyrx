# SPDX-License-Identifier: AGPL-3.0-only

"""Navigation mixin: source switching, landing mode, quit, key reference, copy URL."""

from __future__ import annotations

import logging
import subprocess
import time
from typing import TYPE_CHECKING, cast

from textual import work
from textual.app import App
from textual.containers import Container
from textual.widgets import Button, ContentSwitcher, ListView, Static

from nyrx.config import (
    SC_TRENDING_SLUGS,
    SEVERITY_ERROR,
    SEVERITY_INFORMATION,
    SEVERITY_WARNING,
    TIMEOUT_CONFIRM,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
    update_config,
)
from nyrx.models import MediaRequest
from nyrx.modes import MODES, Source, View
from nyrx.queues import QueueItem
from nyrx.screens import (
    HomeScreen,
    MinSizeModal,
    QualitySelector,
    RadioFilterModal,
    TrendingRegionSelector,
)
from nyrx.sources.soundcloud import fetch_trending_playlist
from nyrx.sources.tv_movies import TVMoviesSource
from nyrx.widgets import ModeSwitcher, SCHomeView

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from textual.timer import Timer

    from nyrx.protocols import MediaAppProtocol


class NavigationActions:
    _all_results: list[dict]
    _last_quit_press: float | None
    _source: Source | None
    _play_spinner_timer: Timer | None

    def _exit_landing_mode(self: MediaAppProtocol) -> None:
        """Switch from the welcome/search screen to the results view."""
        self._view = View.RESULTS
        self._apply_view()

    def _update_sidebar_content(self: MediaAppProtocol) -> None:
        """Update non-visibility sidebar content: radio list population and offline UI state."""
        if self._source == Source.RADIO and self._view == View.RESULTS:
            self._populate_radio_list()

    def _sidebar_wanted(self: MediaAppProtocol) -> bool:
        """Whether the sidebar should be visible (mirrors _apply_sidebar visibility).

        Forced secondary screens override the landing-mode heuristics.
        """
        if (
            self._in_following
            or self._in_artist_profile
            or self._in_liked
            or self._in_watchlist
            or self._in_tv_series
        ):
            return True
        if self._view == View.LANDING:
            return (
                self._now_playing_data is not None
                or bool(self._playback_queue)
                or bool(self._download_pending)
                or self._download_state is not None
            )
        return True

    def _apply_sidebar(self: MediaAppProtocol, is_wide: bool) -> None:
        """Sidebar container visibility + landing-playing CSS class. SSOT for both."""
        sidebar = self.query_one("#sidebar", Container)
        mc = self._w_main_content

        desired_visible = self._sidebar_wanted()
        desired_landing = self._view == View.LANDING and self._is_playing

        current_landing = mc is not None and "landing-playing" in mc.classes
        if sidebar.display == desired_visible and current_landing == desired_landing:
            return

        logger.debug(
            "_apply_sidebar: vis=%s->%s land=%s->%s src=%s view=%s playing=%s",
            sidebar.display,
            desired_visible,
            current_landing,
            desired_landing,
            self._source,
            self._view,
            self._is_playing,
        )
        sidebar.display = desired_visible
        if mc is not None:
            if desired_landing:
                mc.add_class("landing-playing")
            else:
                mc.remove_class("landing-playing")

    def _apply_secondary_widgets(self: MediaAppProtocol) -> None:
        """Update offline banners, history list, and radio filter hint visibility."""
        self._update_offline_ui()
        self._rebuild_history_list()
        if hint := self._w_radio_filter_hint:
            hint.display = self._source == Source.RADIO and self._view == View.LANDING
            logger.debug(
                "_apply_secondary_widgets: radio-filter-hint display=%s src=%s view=%s",
                hint.display,
                self._source,
                self._view,
            )
        else:
            logger.debug("_apply_secondary_widgets: _w_radio_filter_hint is None")

    def _set_focus_for_current_view(self: MediaAppProtocol) -> None:
        """Set focus based on current source and view."""
        src = self._source
        view = self._view
        logger.debug("_set_focus_for_current_view: src=%s view=%s", src, view)
        if view == View.LANDING:
            if src == Source.SOUNDCLOUD:
                if sc := self._w_sc_home:
                    chip = sc.query_one(".sch-chip", Button)
                    if chip:
                        chip.focus()
                        logger.debug(
                            "_set_focus: focused sc chip %s", getattr(chip, "id", "?")
                        )
                        return
                    rl = sc.query_one("#sch-recent-list", ListView)
                    if rl.children:
                        rl.focus()
                        logger.debug("_set_focus: focused sc recent list")
                        return
                else:
                    logger.debug("_set_focus: _w_sc_home is None")
            elif src == Source.TV_MOVIES:
                if tv := self._w_tv_home:
                    chips = list(tv.query(".tv-chip"))
                    if chips:
                        chips[0].focus()
                        logger.debug(
                            "_set_focus: focused tv chip %s",
                            getattr(chips[0], "id", "?"),
                        )
                        return
                else:
                    logger.debug("_set_focus: _w_tv_home is None")
            elif src == Source.RADIO:
                if dt := self._w_radio_list:
                    if dt.children:
                        dt.focus()
                        logger.debug("_set_focus: focused radio list")
                        return
                else:
                    logger.debug("_set_focus: _w_radio_list is None")
            hl = self._w_history_list
            if hl is not None:
                if hl.display and hl.children:
                    hl.focus()
                    logger.debug("_set_focus: focused history list")
                else:
                    es = self._w_empty_state
                    if es is not None:
                        if es.display:
                            es.focus()
                            logger.debug("_set_focus: focused empty state (no history)")
                    else:
                        logger.debug("_set_focus: _w_empty_state is None")
            else:
                logger.debug("_set_focus: _w_history_list is None")
        else:
            if (rv := self._w_results_list) is not None:
                if rv.children:
                    rv.focus()
                    logger.debug("_set_focus: focused results list")
            else:
                logger.debug("_set_focus: _w_results_list is None")

    def _show_mode_overlay(self: MediaAppProtocol, key: Source) -> None:
        """Display the floating mode-switcher overlay (Alt+Tab style)."""
        self.query_one(ModeSwitcher).show(key)

    def _apply_view(self: MediaAppProtocol) -> None:
        """Apply all visibility for current source + view. Idempotent."""
        src = self._source
        view = self._view
        is_wide = self.screen.has_class("wide")

        for mode in MODES.values():
            widget = self.query_one(mode.welcome_widget_id)
            widget.display = mode.key == src and view == View.LANDING

        if (rv := self._w_results_list) is not None:
            rv.set_classes("" if view == View.RESULTS else "hidden")
        else:
            logger.debug("_apply_view: _w_results_list is None, skipping classes")
        try:
            self.query_one("#rs-switcher", ContentSwitcher).display = (
                view == View.RESULTS
            )
        except Exception:
            logger.debug("_apply_view: #rs-switcher not found, skipping display update")

        mc = self._w_main_content
        if mc is not None:
            if view == View.LANDING:
                mc.add_class("landing-mode")
            else:
                mc.remove_class("landing-mode")
        else:
            logger.debug("_apply_view: _w_main_content is None, skipping class ops")

        self._apply_secondary_widgets()
        self._apply_sidebar(is_wide)
        self._update_sidebar_content()
        self._update_sidebar_context()
        self._render_focus_indicators()
        self._update_mode_indicator()
        self._set_focus_for_current_view()

    def _enter_landing_mode(self: MediaAppProtocol) -> None:
        """Show the welcome screen with search history and hidden sidebar."""
        self._view = View.LANDING
        self._apply_view()
        if self._source == Source.SOUNDCLOUD:
            sc_home = self.query_one("#sc-home", SCHomeView)
            sc_home.populate(
                searches=self._search_histories.get("soundcloud", []),
                liked=self._sc_liked,
                following=self._sc_followed,
            )
        elif self._source == Source.TV_MOVIES:
            if tv := self._w_tv_home:
                tv.populate_watchlist(self._tv_bookmarks)
            else:
                logger.debug(
                    "_enter_landing_mode: _w_tv_home is None: skipping populate_watchlist"
                )
            self._populate_tv_home()
            self.call_after_refresh(self._set_focus_for_current_view)
        if mc := self._w_main_content:
            if mc.has_class("landing-mode"):
                self.call_after_refresh(self._update_landing_chrome)
        else:
            logger.debug(
                "_enter_landing_mode: _w_main_content is None: skipping has_class check"
            )

    def _update_landing_chrome(self: MediaAppProtocol) -> None:
        """
        Update the welcome-mode #welcome-bottomright bar and offline banner.
        NOTE: This function owns #welcome-bottomright only.
        _render_focus_indicators owns #welcome-topright.
        Do NOT write to #welcome-topright from this function.
        """
        mc = self._w_main_content
        if mc is None:
            logger.debug("_update_landing_chrome: _w_main_content is None")
            return
        if not mc.has_class("landing-mode"):
            self._update_offline_ui()
            return
        self._update_keybind_bar()
        self._update_offline_ui()

    def _update_offline_ui(self: MediaAppProtocol) -> None:
        """Show/hide offline banner and update search hint based on connectivity."""
        try:
            if isinstance(self.screen, HomeScreen):
                self.screen.update_offline(
                    online=self._online,
                    show_back_online=self._show_back_online,
                )
                return
            banners = [
                b
                for b in (
                    self._w_empty_offline_banner,
                    self._w_sc_offline_banner,
                    self._w_tv_offline_banner,
                )
                if b is not None
            ]
            hint = self._w_empty_hint
            mc = self._w_main_content
            if not banners:
                logger.debug("_update_offline_ui: no home banner widgets found")
                return
            if hint is None:
                logger.debug("_update_offline_ui: _w_empty_hint is None")
                return
            in_welcome = mc is not None and mc.has_class("landing-mode")
            show_main = in_welcome and not self._sidebar_wanted()
            if not self._online:
                hint.update("[dim]/ to search \\[unavailable] [/dim]")
                if show_main:
                    for b in banners:
                        b.styles.display = "block"
                        b.update("[red]\u2717 offline \u00b7 reconnecting[/red]")
                else:
                    for b in banners:
                        b.styles.display = "none"
                        b.update("")
            elif self._show_back_online:
                hint.update("/ to search")
                if show_main:
                    for b in banners:
                        b.styles.display = "block"
                        b.update("[green]\u2713 back online[/green]")
                else:
                    for b in banners:
                        b.styles.display = "none"
                        b.update("")
            else:
                for b in banners:
                    b.styles.display = "none"
                    b.update("")
                hint.update("/ to search")
        except Exception:
            logger.debug("Failed to update offline UI")

    def action_toggle_audio(self: MediaAppProtocol) -> None:
        """Toggle audio-only mode (YouTube) or cycle server (TV/Movies)."""
        if self._source == Source.TV_MOVIES:
            src = self._sources["tv_movies"]
            new_mode = src.cycle_server()
            self._render_focus_indicators()
            self._update_sidebar_context()
            if new_mode == "__no_configs__":
                self.notify(
                    "No server configs. Set a manifest URL to enable TV/Movies.",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
            elif new_mode != "auto":
                srv = cast(TVMoviesSource, src)._dispatcher.get_server(new_mode)
                if srv:
                    note = srv.get("notes", "")
                    if note:
                        self.notify(
                            f"[b]{srv['display_name']}[/b]: {note}",
                            title="Server",
                            severity=SEVERITY_INFORMATION,
                            timeout=TIMEOUT_INFO,
                        )
            return
        if self._source in (Source.SOUNDCLOUD, Source.RADIO):
            return
        self._audio_only = not self._audio_only
        self._update_mode_indicator()
        self._update_sidebar_context()
        self._on_source_changed()

    def action_open_filter(self: MediaAppProtocol) -> None:
        """Open the radio station filter modal (f key, radio mode only)."""
        if self._source != Source.RADIO:
            return
        if not self._station_index or not self._station_index.stations:
            self.notify(
                "Radio station index not loaded yet.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        self.push_screen(
            RadioFilterModal(
                self._station_index,
                initial_name=self._radio_filter_name,
                initial_tags=self._radio_filter_tags,
                initial_countries=self._radio_filter_countries,
            ),
            self._on_filter_applied,
        )

    def _on_filter_applied(self: MediaAppProtocol, result: dict | None) -> None:
        if result is None:
            return
        self._radio_page = 0
        self._radio_filter_name = result.get("name", "")
        self._radio_filter_tags = result.get("tags", [])
        self._radio_filter_countries = result.get("countries", [])
        self._populate_radio_list()
        self._update_sidebar_context()

    @work(thread=True, group="trending")
    def _queue_trending_genre(self: MediaAppProtocol, slug: str) -> None:

        label = SC_TRENDING_SLUGS.get(slug, slug)
        try:
            results = fetch_trending_playlist(slug, country_code=self._trending_region)
        except Exception as e:
            self.call_from_thread(self._stop_trending)
            self.call_from_thread(
                self.notify,
                f"Failed to fetch {label} trending: {e}",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            return
        if not results:
            self.call_from_thread(self._stop_trending)
            self.call_from_thread(
                self.notify,
                f"No trending tracks found for {label}",
                timeout=TIMEOUT_INFO,
            )
            return

        self.call_from_thread(self._process_trending_results, results, label)

    def _process_trending_results(
        self: MediaAppProtocol, results: list[dict], label: str
    ) -> None:
        self._stop_trending()
        for i, r in enumerate(results):
            if i == 0:
                self._play(
                    MediaRequest.from_dict(r, source="soundcloud", audio_only=True)
                )
            else:
                item = QueueItem(
                    request=MediaRequest.from_dict(
                        r, source="soundcloud", audio_only=True
                    ),
                )
                self._playback_queue.add(item)
        self._sync_np_widget()
        self._refresh_queue_modal()
        self.notify(
            f"Queued {len(results)} {label} trending tracks", timeout=TIMEOUT_CONFIRM
        )

    def action_go_home(self: MediaAppProtocol) -> None:
        """Return to landing mode, saving current source state."""
        if len(self.screen_stack) > 1:
            return
        if self._source == Source.RADIO:
            return
        if self._in_tv_series:
            assert self._w_tv_series is not None
            self._hide_tv_series(self._w_tv_series)
            return
        if self._in_watchlist:
            self._hide_watchlist()
            return
        if self._in_liked:
            self._hide_liked()
            return
        if self._in_artist_profile:
            self._hide_artist_profile()
            return
        if self._in_following:
            self._hide_following()
            return
        mc = self._w_main_content
        if mc is None:
            logger.debug("action_go_home: _w_main_content is None")
            return
        if not self._all_results and mc.has_class("landing-mode"):
            return
        if self._source is not None:
            self._source_states[str(self._source)] = {
                "query": self._query,
                "results": self._all_results,
                "page": self._page,
            }
        self._all_results = []
        self._page = 0
        self._query = ""
        if (rv := self._w_results_list) is not None:
            rv.clear()
        else:
            logger.debug("action_go_home: _w_results_list is None, skipping clear")
        self._enter_landing_mode()

    def action_set_quality(self: MediaAppProtocol) -> None:
        """Open the quality selector modal."""
        self.push_screen(QualitySelector(self._quality), self._on_quality_selected)

    def _on_quality_selected(self: MediaAppProtocol, label: str | None) -> None:
        """Handle quality selection from the QualitySelector modal."""
        if label:
            self._quality = label
            update_config(quality=label)
            self._update_sidebar_context()

    def action_set_trending_region(self: MediaAppProtocol) -> None:
        """Open the trending region selector modal."""
        self.push_screen(
            TrendingRegionSelector(self._trending_region),
            self._on_trending_region_selected,
        )

    def _on_trending_region_selected(
        self: MediaAppProtocol, country_code: str | None
    ) -> None:
        """Handle region selection from the TrendingRegionSelector modal."""
        if country_code:
            self._trending_region = country_code
            update_config(trending_region=country_code)
            try:
                label = self.query_one("#sc-home #sch-trending-label", Static)
                label.update(f"TRENDING  [#b0b0b0]({country_code})[/]\n")
            except Exception:
                logger.debug("Failed to update trending region label")

    def action_copy_url(self: MediaAppProtocol) -> None:
        """Copy the current video's URL to the clipboard."""
        item = self._current_item()
        if not item:
            return
        self._copy_url_worker(f"https://www.youtube.com/watch?v={item.data['yt_id']}")

    @work(thread=True, group="clipboard")
    def _copy_url_worker(self: MediaAppProtocol, url: str) -> None:
        """Copy a URL to the system clipboard (background thread)."""
        encoded = url.encode()
        for cmd in [
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "-ib"],
        ]:
            try:
                subprocess.run(cmd, timeout=2, input=encoded)
                self.call_from_thread(
                    self.notify, "Copied to clipboard", timeout=TIMEOUT_CONFIRM
                )
                return
            except Exception:
                continue

    def action_switch_source_1(self: MediaAppProtocol) -> None:
        """Switch to the YouTube source (F1)."""
        self._switch_source(Source.YOUTUBE)

    def action_switch_source_2(self: MediaAppProtocol) -> None:
        """Switch to SoundCloud (f2 / Ctrl+2)."""
        if self._source == Source.SOUNDCLOUD:
            return
        self._switch_source(Source.SOUNDCLOUD)

    def action_switch_source_3(self: MediaAppProtocol) -> None:
        """Switch to Radio (f3 / Ctrl+3)."""
        if self._source == Source.RADIO:
            return
        self._switch_source(Source.RADIO)

    def action_switch_source_4(self: MediaAppProtocol) -> None:
        """Switch to TV/Movies (F4)."""
        self._switch_source(Source.TV_MOVIES)

    def _switch_source(
        self: MediaAppProtocol, key: Source, show_overlay: bool = True
    ) -> None:
        """Switch the active content source and clear the current results.

        Immediate: guards, overlay show, flag updates, state save, widget clear.
        Deferred: _hide_* widget work, view setup, focus: via call_after_refresh
        so the overlay renders on the next frame before heavy work runs.
        """
        if key == self._source:
            return
        plugin_key = str(key)
        if plugin_key not in self._sources:
            return

        self._switch_gen += 1
        logger.debug(
            "_switch_source: %s -> %s in_tv_series=%s w_tv_series=%s nav_stack=%d view=%s",
            self._source,
            key,
            self._in_tv_series,
            self._w_tv_series is not None,
            len(self._tv_nav_stack),
            self._view,
        )

        if show_overlay:
            self._show_mode_overlay(key)

        if self._in_liked:
            self._in_liked = False
            self._purge_unlike_buffer()
        if self._in_following or self._in_artist_profile:
            self._in_following = False
            self._in_artist_profile = False
        if self._in_watchlist:
            self._in_watchlist = False
            if mc := self._w_main_content:
                mc.remove_class("watchlist-mode")
            logger.debug(
                "_switch_source: watchlist flag cleared (full _hide_watchlist deferred)"
            )
        if self._in_tv_series:
            self._in_tv_series = False
            logger.debug(
                "_switch_source: tv_series flag cleared (widget removed deferred)"
            )

        if self._source is not None:
            save_key = str(self._source)
            self._source_states[save_key] = {
                "query": self._query,
                "results": self._all_results,
                "page": self._page,
            }
        self._source = key
        for mode in MODES.values():
            widget = self.query_one(mode.welcome_widget_id)
            widget.display = False
        try:
            self.query_one("#rs-switcher", ContentSwitcher).display = False
        except Exception:
            logger.debug("_switch_source: #rs-switcher not found, skipping hide")
        self._all_results = []
        self._page = 0
        self._query = ""
        ra = self._w_radio_area
        hint = self._w_radio_filter_hint
        lv = self._w_results_list
        if lv is not None:
            lv.clear()
        else:
            logger.debug("_switch_source: _w_results_list is None, skipping clear")
        if ra is not None:
            ra.styles.display = "none"
        else:
            logger.debug("_switch_source: _w_radio_area is None, skipping styles")
        if hint is not None:
            hint.display = False
        else:
            logger.debug(
                "_switch_source: _w_radio_filter_hint is None: skipping display"
            )
        if lv is not None:
            lv.set_classes("")
        else:
            logger.debug("_switch_source: _w_results_list is None, skipping classes")
        saved = self._source_states.pop(str(key), None)

        gen = self._switch_gen

        def _deferred() -> None:
            if gen != self._switch_gen:
                return
            self._switch_source_heavy(key, saved)
            # Textual partial updates can drop repaints after a focus+hide+scroll
            # switch (stale screen until a modal / tab forces a full redraw).
            # Force a full re-layout + repaint so the new mode is guaranteed
            # to reach the terminal.
            logger.debug("_switch_source: forcing full refresh after heavy work")
            self.refresh(layout=True)
            # Focus events fired during the heavy switch may be coalesced or
            # delivered before the new mode's widgets/layout settle, leaving
            # the focus/keybind bars stale (e.g. empty YT landing after coming
            # from radio/SC). Re-apply focus + chrome after the refresh so the
            # bars always match the final on-screen state.
            self.call_after_refresh(self._finalize_switch_chrome)

        self.call_after_refresh(_deferred)

    def _finalize_switch_chrome(self: MediaAppProtocol) -> None:
        """Re-apply focus and bars after a source switch settles."""
        self._set_focus_for_current_view()
        self._render_focus_indicators()
        self._update_keybind_bar()

    def _switch_source_heavy(
        self: MediaAppProtocol, key: Source, saved: dict | None
    ) -> None:
        """Deferred heavy work for _switch_source: runs after overlay renders."""
        logger.debug(
            "_switch_source_heavy: key=%s w_tv_series=%s in_tv_series=%s nav_stack=%d",
            key,
            self._w_tv_series is not None,
            self._in_tv_series,
            len(self._tv_nav_stack),
        )

        if self._w_liked_screen is not None and self._w_liked_screen.display:
            self._hide_liked()
        if self._w_following_area is not None and self._w_following_area.display:
            self._hide_following()
        if self._w_watchlist_screen is not None and self._w_watchlist_screen.display:
            self._hide_watchlist()
        if self._w_tv_series is not None:
            logger.debug(
                "_switch_source_heavy: calling _hide_tv_series on %s", self._w_tv_series
            )
            self._tv_nav_stack.clear()
            self._hide_tv_series(self._w_tv_series)
            logger.debug(
                "_switch_source_heavy: after _hide_tv_series nav_stack=%d",
                len(self._tv_nav_stack),
            )
        else:
            logger.debug("_switch_source_heavy: _w_tv_series is None, nothing to hide")

        ra = self._w_radio_area
        hint = self._w_radio_filter_hint
        lv = self._w_results_list

        if saved and saved["results"]:
            self._query = saved["query"]
            self._all_results = saved["results"]
            self._page = saved["page"]
            self._show_page()
        elif key == Source.RADIO:
            self._exit_landing_mode()
            if ra is not None:
                ra.styles.display = "block"
            else:
                logger.debug(
                    "_switch_source_heavy: _w_radio_area is None: skipping radio styles"
                )
            if hint is not None:
                hint.display = True
            else:
                logger.debug(
                    "_switch_source_heavy: _w_radio_filter_hint is None: skipping hint display"
                )
            if lv is not None:
                lv.set_classes("hidden")
            else:
                logger.debug(
                    "_switch_source_heavy: _w_results_list is None: skipping classes hidden"
                )
            self._radio_page = 0
            if es := self._w_empty_state:
                es.display = False
            else:
                logger.debug(
                    "_switch_source_heavy: _w_empty_state is None: skipping display False"
                )
            try:
                self.query_one("#rs-switcher", ContentSwitcher).display = False
            except Exception:
                logger.debug(
                    "_switch_source_heavy: #rs-switcher not found, skipping hide"
                )
        elif key == Source.TV_MOVIES:
            self._enter_landing_mode()
        elif key == Source.YOUTUBE:
            self._enter_landing_mode()
        elif key == Source.SOUNDCLOUD:
            self._enter_landing_mode()
            from nyrx.sources.soundcloud.api import client_id_available

            if not client_id_available():
                self.notify(
                    "Soundcloud: API key not available, some features limited",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
        else:
            self._exit_landing_mode()
            if es := self._w_empty_state:
                es.display = True
            else:
                logger.debug(
                    "_switch_source_heavy: _w_empty_state is None: skipping display True"
                )
        if key == Source.YOUTUBE and not self._all_results:
            hl = self._w_history_list
            if hl is not None:
                if hl.display:
                    self._set_focus_for_current_view()
                    if mc := self._w_main_content:
                        if mc.has_class("landing-mode"):
                            self.call_after_refresh(self._update_landing_chrome)
                    else:
                        logger.debug(
                            "_switch_source_heavy: _w_main_content is None: skipping has_class"
                        )
                logger.debug(
                    "_switch_source_heavy: yt block hist children=%d display=%s focused_after=%s",
                    len(hl.children),
                    hl.display,
                    getattr(getattr(self, "focused", None), "id", "?"),
                )
            else:
                logger.debug(
                    "_switch_source_heavy: _w_history_list is None: skipping focus"
                )

    def key_question_mark(self: MediaAppProtocol) -> None:
        if isinstance(self.screen, (HomeScreen, MinSizeModal)):
            return
        if self.query_one(ModeSwitcher).has_class("visible"):
            return
        self.call_after_refresh(self._push_key_reference)

    def _push_key_reference(self: MediaAppProtocol) -> None:
        from nyrx.screens import KeyReferenceModal

        if isinstance(self.screen, KeyReferenceModal):
            return
        if self._side_focused:
            from nyrx.handlers.keys import detect_sidebar_key_context

            context = detect_sidebar_key_context(sc_np_focused=self._sc_np_focused)
        else:
            context = self._detect_key_context()
        self.push_screen(KeyReferenceModal(kr_context=context))

    def action_quit(self: MediaAppProtocol) -> None:
        """Quit the app (double-press within 1 second)."""
        now = time.monotonic()
        if self._last_quit_press and (now - self._last_quit_press) < 1.0:
            if self._play_spinner_timer:
                self._play_spinner_timer.stop()
                self._play_spinner_timer = None
            self._stop_playback()
            if self._connectivity_timer is not None:
                self._connectivity_timer.stop()
            self.exit()
        else:
            self._last_quit_press = now
            self.notify(
                "Press q again to quit",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_CONFIRM,
            )

    def action_open_commands(self: MediaAppProtocol) -> None:
        from nyrx.commands import CommandScreen

        self.push_screen(CommandScreen())

    def action_command_palette(self: MediaAppProtocol) -> None:
        """Block Textual's ctrl+p palette on the boot screens."""
        if isinstance(self.screen, (HomeScreen, MinSizeModal)):
            return
        App.action_command_palette(self)  # type: ignore[arg-type]

    def action_help_quit(self: MediaAppProtocol) -> None:
        """No-op: NYRX quits via double-press-q; suppress the Ctrl+C
        'Press {key} to quit the app' notification Textual fires."""

    def _on_home_selected(self: MediaAppProtocol, result: object) -> None:
        """User picked a source on the welcome screen → enter that source."""
        if isinstance(result, Source):
            self._switch_source(result, show_overlay=False)
