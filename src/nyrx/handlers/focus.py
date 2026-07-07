# SPDX-License-Identifier: AGPL-3.0-only

"""Focus and selection handlers: tab cycling, focus indicators, list/table selection."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast

from textual.dom import DOMNode
from textual.events import Focus, Resize
from textual.widget import Widget
from textual.widgets import Button, DataTable, Input, ListView, Static

from nyrx.config import SEVERITY_WARNING, TIMEOUT_INFO, TIMEOUT_WARNING
from nyrx.helpers import require_key
from nyrx.models import MediaRequest
from nyrx.modes import Source
from nyrx.widgets import FeedTrackItem, HistoryItem, ResultItem, TVChip

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol


class FocusHandlers:
    _spinning_chip: Button | None

    def on_resize(self: MediaAppProtocol, event: Resize) -> None:
        self._pixel_size = event.pixel_size
        try:
            self._apply_sidebar(self.screen.has_class("wide"))
        except Exception:
            logger.debug("on_resize: _apply_sidebar failed")
        self._check_size_floor()

    def key_tab(self: MediaAppProtocol) -> None:
        """Cycle focus between watchlist, liked, following, artist profile, TV series, TV home, SC home, history, results, radio grid, and sidebar panels."""
        dl = self._w_download
        if dl is not None and dl.display and self.focused is dl:
            if dl._dl_select_mode:
                dl._cancel_dl_selection()
                return
            # Fall through: directional/default branches handle focus
        if self._in_watchlist:
            wl = self._w_watchlist_screen
            if wl is None:
                logger.debug("key_tab: _w_watchlist_screen is None")
                return
            inp = wl.query_one("#wl-search", Input)
            dt = wl.query_one("#wl-list", DataTable)
            sb, sb_focused = self._sidebar_state
            focused = self.focused
            if focused is dt or focused is inp:
                if sb:
                    sb.focus()
                else:
                    dt.focus()
            elif sb_focused:
                dt.focus()
            else:
                dt.focus()
            return

        if self._in_liked:
            ls = self._w_liked_screen
            if ls is None:
                logger.debug("key_tab: _w_liked_screen is None")
                return
            inp = ls.query_one("#ls-search", Input)
            dt = ls.query_one("#ls-list", DataTable)
            sb, sb_focused = self._sidebar_state
            focused = self.focused
            if focused is dt or focused is inp:
                if sb:
                    sb.focus()
                else:
                    dt.focus()
            elif sb_focused:
                dt.focus()
            else:
                dt.focus()
            return
        if self._in_following:
            sb, sb_focused = self._sidebar_state
            left_list = self._w_fs_left_list
            center_list = self._w_fs_center_list
            if (
                left_list is not None
                and left_list.has_focus
                or (center_list is not None and center_list.has_focus)
            ):
                if sb:
                    sb.focus()
                elif left_list is not None:
                    left_list.focus()
            elif sb_focused:
                if left_list is not None:
                    left_list.focus()
            elif left_list is not None and left_list.row_count > 0:
                left_list.focus()
            return
        if self._in_artist_profile:
            ap = self._w_artist_profile
            if ap is None:
                logger.debug("key_tab: _w_artist_profile is None")
                return
            sb, sb_focused = self._sidebar_state
            dt = ap.query_one("#ap-track-list", DataTable)
            chips = list(ap.query(Button))
            focused = self.focused
            in_main = focused is dt or focused in chips

            if in_main and sb:
                sb.focus()
            elif sb_focused:
                (chips[0] if chips else dt).focus()
            elif sb:
                sb.focus()
            else:
                if chips:
                    if focused is chips[-1]:
                        dt.focus()
                    elif focused in chips:
                        chips[chips.index(focused) + 1].focus()
                    else:
                        chips[0].focus()
                else:
                    dt.focus()
            return

        if self._in_tv_series:
            tv_series = self._w_tv_series
            if tv_series is None:
                return
            sb, sb_focused = self._sidebar_state
            dt = tv_series.query_one("#tvs-episodes", DataTable)
            chips = [cast(Button, c) for c in tv_series.query(".season-chip")]
            if sb_focused:
                (chips[0] if chips else dt).focus()
            elif sb:
                sb.focus()
            else:
                (chips[0] if chips else dt).focus()
            self._update_mode_indicator()
            return

        tv_home = self.query_one("#tv-home")
        if tv_home.display:
            sb, sb_focused = self._sidebar_state
            chips = list(tv_home.query(".tv-chip"))
            if sb_focused:
                if chips:
                    chips[0].focus()
            elif sb:
                sb.focus()
            elif chips:
                chips[0].focus()
            self._update_mode_indicator()
            return

        sc_home = self.query_one("#sc-home")
        if sc_home.display:
            sb, sb_focused = self._sidebar_state
            if sb_focused:
                chip = self.query_one("#sc-home .sch-chip", Button)
                if chip:
                    chip.focus()
                else:
                    rl = self.query_one("#sch-recent-list", ListView)
                    if rl.children:
                        rl.focus()
            elif sb:
                sb.focus()
            else:
                chip = self.query_one("#sc-home .sch-chip", Button)
                if chip:
                    chip.focus()
                else:
                    rl = self.query_one("#sch-recent-list", ListView)
                    if rl.children:
                        rl.focus()
            self._update_mode_indicator()
            return

        history = self._w_history_list
        results = self._w_results_list
        foci: list[Widget] = []
        empty = self._w_empty_state
        if (
            empty is not None
            and empty.display
            and history is not None
            and history.children
        ):
            foci.append(history)
        if results is not None and not results.has_class("hidden") and results.children:
            foci.append(results)
        ra = self._w_radio_area
        if ra is not None and ra.display:
            rl = self._w_radio_list
            if rl is not None and rl.row_count:
                foci.append(rl)
        sb, _ = self._sidebar_state
        if sb:
            foci.append(sb)
        if not foci:
            return
        current = self.focused
        if current and current in foci:
            foci[(foci.index(current) + 1) % len(foci)].focus()
        else:
            foci[0].focus()
        self._update_mode_indicator()

    def _focus_main_panel(self: MediaAppProtocol) -> None:
        """Route focus to the current mode's main content panel.

        Used by the sidebar now-playing ``key_escape`` handler so ESC lands
        on a real, visible panel instead of the (possibly hidden) results
        list. Mirrors the sidebar-aware branches of :meth:`key_tab`.
        """
        if self._in_watchlist:
            wl = self._w_watchlist_screen
            if wl is None:
                logger.debug("_focus_main_panel: _w_watchlist_screen is None")
                return
            wl.query_one("#wl-list", DataTable).focus()
            return

        if self._in_liked:
            ls = self._w_liked_screen
            if ls is None:
                logger.debug("_focus_main_panel: _w_liked_screen is None")
                return
            ls.query_one("#ls-list", DataTable).focus()
            return

        if self._in_following:
            left_list = self._w_fs_left_list
            if left_list is not None:
                left_list.focus()
            else:
                logger.debug("_focus_main_panel: _w_fs_left_list is None")
            return

        if self._in_artist_profile:
            ap = self._w_artist_profile
            if ap is None:
                logger.debug("_focus_main_panel: _w_artist_profile is None")
                return
            dt = ap.query_one("#ap-track-list", DataTable)
            chips = list(ap.query(Button))
            (chips[0] if chips else dt).focus()
            return

        if self._in_tv_series:
            tv_series = self._w_tv_series
            if tv_series is None:
                logger.debug("_focus_main_panel: _w_tv_series is None")
                return
            dt = tv_series.query_one("#tvs-episodes", DataTable)
            chips = [cast(Button, c) for c in tv_series.query(".season-chip")]
            (chips[0] if chips else dt).focus()
            self._update_mode_indicator()
            return

        tv_home = self.query_one("#tv-home")
        if tv_home.display:
            chips = list(tv_home.query(".tv-chip"))
            if chips:
                chips[0].focus()
            self._update_mode_indicator()
            return

        sc_home = self.query_one("#sc-home")
        if sc_home.display:
            chip = self.query_one("#sc-home .sch-chip", Button)
            if chip:
                chip.focus()
            else:
                rl = self.query_one("#sch-recent-list", ListView)
                if rl.children:
                    rl.focus()
            self._update_mode_indicator()
            return

        self._set_focus_for_current_view()

    def action_focus_next(self: MediaAppProtocol) -> None:
        """Override default focus-next to route through custom ``key_tab``.

        ``Screen`` defines ``Binding("tab", "app.focus_next")``, which
        takes precedence over ``app.key_tab`` for any widget outside
        ``SCHomeView`` or ``BaseNowPlaying`` that is focusable.
        By routing ``action_focus_next`` through ``key_tab`` on the main
        screen, every Tab press reaches the custom focus logic regardless
        of which widget is focused, while modals keep the default behavior.
        """
        if len(self.screen_stack) > 1:
            return super().action_focus_next()  # type: ignore[misc]
        self.key_tab()

    def action_focus_previous(self: MediaAppProtocol) -> None:
        """Override default shift+tab: same rationale as ``action_focus_next``."""
        if len(self.screen_stack) > 1:
            return super().action_focus_previous()  # type: ignore[misc]
        self.key_tab()

    def _save_sc_home_focus(self: MediaAppProtocol) -> None:
        """Save SC home focus state for restoration on return from sub-mode."""
        focused = self.screen.focused
        self._sc_home_focus = None
        sc = self._w_sc_home
        if sc is None:
            logger.debug("_save_sc_home_focus: _w_sc_home is None")
            return
        if not sc.display:
            return
        try:
            if isinstance(focused, Button) and focused.has_class("sch-chip"):
                self._sc_home_focus = ("chip", focused.id)
            elif focused is self.query_one("#sch-recent-list", ListView):
                self._sc_home_focus = ("recent", focused.index)
        except Exception:
            logger.debug("_save_sc_home_focus: query failed")

    def _restore_sc_home_focus(self: MediaAppProtocol) -> None:
        """Restore SC home focus from saved state, fallback to first chip."""
        state = self._sc_home_focus
        sc_home = self._w_sc_home
        if sc_home is None:
            logger.debug("_restore_sc_home_focus: _w_sc_home is None")
            return
        try:
            if state is not None:
                ftype, fval = state
                if ftype == "chip" and fval:
                    sc_home.query_one(f"#{fval}", Button).focus()
                    return
                if ftype == "recent" and isinstance(fval, int):
                    rl = sc_home.query_one("#sch-recent-list", ListView)
                    if fval < len(list(rl.children)):
                        rl.index = fval
                        rl.focus()
                        return
        except Exception:
            logger.debug("_restore_sc_home_focus: failed to restore state %s", state)
        try:
            sc_home.query_one(".sch-chip", Button).focus()
        except Exception:
            logger.debug("_restore_sc_home_focus: .sch-chip not found")

    def _save_tv_home_focus(self: MediaAppProtocol) -> None:
        """Save TV home focus state for restoration on return to TV home."""
        focused = self.screen.focused
        self._tv_home_focus = None
        tv = self._w_tv_home
        if tv is None:
            logger.debug("_save_tv_home_focus: _w_tv_home is None")
            return
        if not tv.display:
            return
        try:
            if isinstance(focused, TVChip):
                parent = focused.parent
                if parent is not None:
                    siblings = list(parent.query(TVChip))
                    idx = siblings.index(focused) if focused in siblings else -1
                    self._tv_home_focus = ("chip", parent.id, idx)
        except Exception:
            logger.debug("_save_tv_home_focus: query failed")

    def _restore_tv_home_focus(self: MediaAppProtocol) -> None:
        """Restore TV home focus from saved state, fallback to first chip."""
        state = self._tv_home_focus
        tv = self._w_tv_home
        if tv is None:
            logger.debug("_restore_tv_home_focus: _w_tv_home is None")
            return
        try:
            if state is not None:
                ftype, parent_id, idx = state
                if ftype == "chip" and parent_id and isinstance(idx, int) and idx >= 0:
                    try:
                        parent = tv.query_one(f"#{parent_id}")
                        siblings = list(parent.query(TVChip))
                        if idx < len(siblings):
                            siblings[idx].focus()
                            return
                    except Exception:
                        logger.debug(
                            "_restore_tv_home_focus: parent %s not found", parent_id
                        )
        except Exception:
            logger.debug("_restore_tv_home_focus: failed to restore state %s", state)
        try:
            tv.query_one(".tv-chip", TVChip).focus()
        except Exception:
            logger.debug("_restore_tv_home_focus: .tv-chip not found")

    def on_focus(self: MediaAppProtocol, event: Focus) -> None:
        """Fires after any widget gains focus. self.focused is always accurate here."""
        f = self.focused
        logger.debug(
            "on_focus: focused=%s id=%s display=%s",
            type(f).__name__,
            getattr(f, "id", "?"),
            getattr(f, "display", "?"),
        )
        self._render_focus_indicators()

    @property
    def _side_focused(self: MediaAppProtocol) -> bool:
        focused = self.focused
        np_ok = any(focused is w for w in self._np_widgets.values())
        dl_ok = self._w_download is not None and focused is self._w_download
        return np_ok or dl_ok

    @property
    def _sidebar_state(self: MediaAppProtocol) -> tuple[Widget | None, bool]:
        """Return (sidebar_target, is_any_sidebar_focused).

        Sidebar target is the NP widget (preferred) or download widget
        (fallback) that is currently displayed, or None if neither is.
        ``is_any_sidebar_focused`` is True if *either* the NP widget or
        the download widget is focused (regardless of which one ``target``
        resolved to).
        """
        side = self._displayed_np_widget()
        dl = self._w_download
        target = side if side and side.display else (dl if dl and dl.display else None)
        focused = any(
            w is not None and w.display and self.focused is w for w in [side, dl]
        )
        return target, focused

    def _render_focus_indicators(self: MediaAppProtocol) -> None:
        if len(self.screen_stack) > 1:
            return
        focused = self.focused
        mc = self._w_main_content
        landing = mc is not None and mc.has_class("landing-mode")
        logger.debug(
            "_render_focus_indicators: source=%s view=%s landing=%s focused=%s(id=%s,display=%s) "
            "stack=%d in_following=%s in_liked=%s in_watchlist=%s in_tv_series=%s side_focused=%s",
            self._source,
            self._view,
            landing,
            type(focused).__name__,
            getattr(focused, "id", "?"),
            getattr(focused, "display", "?"),
            len(self.screen_stack),
            self._in_following,
            self._in_liked,
            self._in_watchlist,
            self._in_tv_series,
            self._side_focused,
        )
        bar_on = " [rgb(162,119,255)]\u2501\u2501\u2501\u2501\u2501[/]"
        bar_off = " [dim]\u2501\u2501\u2501\u2501\u2501[/dim]"
        base = "[white]FOCUS[/white]"

        if self._in_following:
            content_lit = (
                focused is self._w_fs_left_list or focused is self._w_fs_center_list
            )
            side_lit = self._side_focused
            if wt := self._w_welcome_topright:
                wt.update(f"{base}{bar_on if content_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_welcome_topright is None")
            if sf := self._w_sidebar_focus:
                sf.update(f"{base}{bar_on if side_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        if self._in_liked:
            np_displayed = self._displayed_np_widget()
            content_lit = (
                focused is not None
                and (np_displayed is None or focused is not np_displayed)
                and not self._side_focused
            )
            side_lit = self._side_focused
            if wt := self._w_welcome_topright:
                wt.update(f"{base}{bar_on if content_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_welcome_topright is None")
            if sf := self._w_sidebar_focus:
                sf.update(f"{base}{bar_on if side_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        if self._in_watchlist:
            wl = self._w_watchlist_screen
            content_lit = False
            if wl is not None:
                dt = wl.query_one("#wl-list", DataTable)
                inp = wl.query_one("#wl-search", Input)
                content_lit = focused is dt or focused is inp
            side_lit = self._side_focused
            if wt := self._w_welcome_topright:
                wt.update(f"{base}{bar_on if content_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_welcome_topright is None")
            src = self._sources.get("tv_movies")
            server_name = src.current_server_display() if src else "Auto"
            sidebar_text = f"[white]\u27f3 {server_name}  \u2022  FOCUS[/white]"
            if sf := self._w_sidebar_focus:
                sf.update(f"{sidebar_text}{bar_on if side_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        if self._in_artist_profile:
            side_lit = self._side_focused
            content_lit = False
            if focused is not None:
                ap = self._w_artist_profile
                if ap is not None:
                    dt = ap.query_one("#ap-track-list", DataTable)
                    if focused is dt or (
                        isinstance(focused, Button) and focused.has_class("ap-chip")
                    ):
                        content_lit = True
                else:
                    logger.debug("_render_focus_indicators: _w_artist_profile is None")
            if wt := self._w_welcome_topright:
                wt.update(f"[white]FOCUS[/white]{bar_on if content_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_welcome_topright is None")
            if sf := self._w_sidebar_focus:
                sf.update(f"{base}{bar_on if side_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        if self._in_tv_series:
            tv_series = self._w_tv_series
            content_lit = False
            if tv_series is not None:
                dt = tv_series.query_one("#tvs-episodes", DataTable)
                content_lit = focused is dt or (
                    isinstance(focused, Button) and focused.has_class("season-chip")
                )
            side_lit = self._side_focused
            if wt := self._w_welcome_topright:
                wt.update(f"{base}{bar_on if content_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_welcome_topright is None")
            src = self._sources.get("tv_movies")
            server_name = src.current_server_display() if src else "Auto"
            sidebar_text = f"[white]\u27f3 {server_name}  \u2022  FOCUS[/white]"
            if sf := self._w_sidebar_focus:
                sf.update(f"{sidebar_text}{bar_on if side_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        mc = self._w_main_content
        if mc is not None and mc.has_class("landing-mode"):
            is_sc = self._source == Source.SOUNDCLOUD
            is_tv = self._source == Source.TV_MOVIES
            if is_sc:
                content_focused = focused is self.query_one("#sch-recent-list") or (
                    isinstance(focused, Button) and focused.has_class("sch-chip")
                )
            elif is_tv:
                content_focused = isinstance(focused, TVChip)
            else:
                content_focused = (
                    focused is self._w_history_list or focused is self._w_empty_state
                )
            logger.debug(
                "_render_focus_indicators: landing branch is_sc=%s is_tv=%s content_focused=%s "
                "hist=%s(id=%s,children=%s,display=%s) empty=%s(id=%s,display=%s)",
                is_sc,
                is_tv,
                content_focused,
                type(self._w_history_list).__name__,
                getattr(self._w_history_list, "id", "?"),
                len(getattr(self._w_history_list, "children", ())),
                getattr(self._w_history_list, "display", "?"),
                type(self._w_empty_state).__name__,
                getattr(self._w_empty_state, "id", "?"),
                getattr(self._w_empty_state, "display", "?"),
            )
            side_lit = self._side_focused
            if is_sc:
                if wt := self._w_welcome_topright:
                    wt.update(
                        f"[white]FOCUS[/white]{bar_on if content_focused else bar_off}"
                    )
                else:
                    logger.debug(
                        "_render_focus_indicators: _w_welcome_topright is None"
                    )
                if sf := self._w_sidebar_focus:
                    sf.update(f"{base}{bar_on if side_lit else bar_off}")
                else:
                    logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            elif is_tv:
                src = self._sources.get("tv_movies")
                server_name = src.current_server_display() if src else "Auto"
                mode_text = f"[white]\u27f3 {server_name}  \u2022  FOCUS[/white]"
                if self._is_playing:
                    if wt := self._w_welcome_topright:
                        wt.update(f"{base}{bar_on if content_focused else bar_off}")
                    else:
                        logger.debug(
                            "_render_focus_indicators: _w_welcome_topright is None"
                        )
                    if sf := self._w_sidebar_focus:
                        sf.update(f"{mode_text}{bar_on if side_lit else bar_off}")
                    else:
                        logger.debug(
                            "_render_focus_indicators: _w_sidebar_focus is None"
                        )
                else:
                    if wt := self._w_welcome_topright:
                        wt.update(
                            f"{mode_text}{bar_on if content_focused else bar_off}"
                        )
                    else:
                        logger.debug(
                            "_render_focus_indicators: _w_welcome_topright is None"
                        )
                    if sf := self._w_sidebar_focus:
                        sf.update(f"{base}{bar_on if side_lit else bar_off}")
                    else:
                        logger.debug(
                            "_render_focus_indicators: _w_sidebar_focus is None"
                        )
            else:
                sym = "\u25b6 " if not self._audio_only else "\u266a"
                label = " VIDEO MODE" if not self._audio_only else " AUDIO MODE"
                if wt := self._w_welcome_topright:
                    wt.update(
                        f"[white]{sym}{label}  \u2022  FOCUS[/white]{bar_on if content_focused else bar_off}"
                    )
                else:
                    logger.debug(
                        "_render_focus_indicators: _w_welcome_topright is None"
                    )
                if sf := self._w_sidebar_focus:
                    sf.update(f"{base}{bar_on if side_lit else bar_off}")
                else:
                    logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        if self._source == Source.RADIO:
            content_lit = focused is self._w_radio_list
            side_lit = self._side_focused
            if rf := self._w_results_focus:
                rf.update(f"{base}{bar_on if content_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_results_focus is None")
            if sf := self._w_sidebar_focus:
                sf.update(f"{base}{bar_on if side_lit else bar_off}")
            else:
                logger.debug("_render_focus_indicators: _w_sidebar_focus is None")
            return

        is_yt = self._source == Source.YOUTUBE
        is_tv = self._source == Source.TV_MOVIES
        base_results = "[white]FOCUS[/white]"
        if is_yt:
            sym = "\u25b6 " if not self._audio_only else "\u266a"
            mode_label = " VIDEO MODE" if not self._audio_only else " AUDIO MODE"
            base_sidebar = f"[white]{sym}{mode_label}  \u2022  FOCUS[/white]"
        elif is_tv:
            src = self._sources.get("tv_movies")
            server_name = src.current_server_display() if src else "Auto"
            base_sidebar = f"[white]\u27f3 {server_name}  \u2022  FOCUS[/white]"
        else:
            base_sidebar = "[white]FOCUS[/white]"
        content_lit = focused is self._w_results_list or focused is self._w_history_list
        side_lit = self._side_focused
        if rf := self._w_results_focus:
            rf.update(f"{base_results}{bar_on if content_lit else bar_off}")
        else:
            logger.debug("_render_focus_indicators: _w_results_focus is None")
        if sf := self._w_sidebar_focus:
            sf.update(f"{base_sidebar}{bar_on if side_lit else bar_off}")
        else:
            logger.debug("_render_focus_indicators: _w_sidebar_focus is None")

    def _on_screen_focus_changed(
        self: MediaAppProtocol, old: DOMNode | None, new: DOMNode | None
    ) -> None:
        """Called when screen.focused changes (any widget focus change)."""
        logger.debug(
            "screen_focus_changed: %s(id=%s) -> %s(id=%s)",
            type(old).__name__,
            getattr(old, "id", "?"),
            type(new).__name__,
            getattr(new, "id", "?"),
        )
        try:
            self._render_focus_indicators()
            self._update_keybind_bar()
        except Exception:
            logger.debug("_on_screen_focus_changed: render/update failed")
        if self._in_liked and self._w_liked_screen is not None:
            prefix = self._w_liked_screen.query_one("#ls-search-prefix", Static)
            inp = self._w_liked_screen.query_one("#ls-search", Input)
            if new is inp:
                prefix.add_class("-active")
            else:
                prefix.remove_class("-active")

        if self._in_watchlist and self._w_watchlist_screen is not None:
            prefix = self._w_watchlist_screen.query_one("#wl-search-prefix", Static)
            inp = self._w_watchlist_screen.query_one("#wl-search", Input)
            if new is inp:
                prefix.add_class("-active")
            else:
                prefix.remove_class("-active")

        if self._in_following and self._w_sc_home is not None:
            prefix = self._w_sc_home.query_one("#fs-search-prefix", Static)
            inp = self._w_sc_home.query_one("#fs-search", Input)
            if new is inp:
                prefix.add_class("-active")
            else:
                prefix.remove_class("-active")

    def _on_app_screen_changed(self: MediaAppProtocol) -> None:
        """Called when the active screen changes. Re-install focus watcher."""
        self.watch(self.screen, "focused", self._on_screen_focus_changed)

    def on_button_pressed(self: MediaAppProtocol, event: Button.Pressed) -> None:
        """Handle genre chip clicks on the SC Home screen and collection chip clicks."""
        bid = event.button.id
        if event.button.has_class("ap-chip"):
            event.stop()
            cid = getattr(event.button, "_cid", "")
            self.action_browse_collections(cid)
            return
        if bid and bid.startswith("sc-genre-"):
            event.stop()
            if self._trending_in_progress:
                return
            from nyrx.sources.soundcloud.api import client_id_available

            if not client_id_available():
                self._trending_in_progress = True
                self.notify(
                    "Client id unavailable: trending disabled",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
                self.set_timer(3.5, self._stop_trending)
                return
            self._stop_chip_spinner()
            btn = event.button
            self._spinning_chip = btn
            self._chip_spinner_idx = 0
            self._start_chip_spinner()
            slug = bid.replace("sc-genre-", "")
            self._trending_in_progress = True
            self._queue_trending_genre(slug)

    def on_list_view_selected(self: MediaAppProtocol, event: ListView.Selected) -> None:
        """Handle selection of a history or result or feed item."""
        if self._np_focused or self._stopping:
            return
        item = event.item
        if isinstance(item, HistoryItem):
            self._run_history_search(item)
        elif isinstance(item, ResultItem):
            data = item.data
            if data.get("source") == "tv_movies" and data.get("media_type") == "tv":
                tmdb_id = data.get("tmdb_id")
                if tmdb_id is not None:
                    self.action_view_tv_series(tmdb_id)
            else:
                self._play(MediaRequest.from_dict(data))
        elif isinstance(item, FeedTrackItem):
            data = item.data
            if data.get("source") == "tv_movies" and data.get("media_type") == "tv":
                tmdb_id = data.get("tmdb_id")
                if tmdb_id is not None:
                    self.action_view_tv_series(tmdb_id)
            else:
                self._play(MediaRequest.from_dict(data))
        return

    def on_data_table_row_selected(
        self: MediaAppProtocol, event: DataTable.RowSelected
    ) -> None:
        """Handle enter on a DataTable row (radio, artist profile, liked, watchlist, or following)."""
        if event.data_table.id == "radio-list":
            station = self._radio_row_stations.get(require_key(event.row_key.value))
            if station:
                self._play_radio(station)
        elif event.data_table.id == "ap-track-list" and self._in_artist_profile:
            if ap := self._w_artist_profile:
                track = ap._track_data_map.get(require_key(event.row_key.value))
                if track:
                    track.setdefault("yt_id", track.get("track_id", ""))
                    self._play(MediaRequest.from_dict(track))
            else:
                logger.debug("on_data_table_row_selected: _w_artist_profile is None")
        elif event.data_table.id == "ls-list" and self._in_liked:
            if ls := self._w_liked_screen:
                track = ls._track_data_map.get(require_key(event.row_key.value))
                if track:
                    track.setdefault("yt_id", track.get("track_id", ""))
                    self._play(MediaRequest.from_dict(track))
            else:
                logger.debug("on_data_table_row_selected: _w_liked_screen is None")
        elif event.data_table.id == "wl-list" and self._in_watchlist:
            if wl := self._w_watchlist_screen:
                data = wl.focused_bookmark()
                if data:
                    if (
                        data.get("source") == "tv_movies"
                        and data.get("media_type") == "tv"
                    ):
                        tmdb_id = data.get("tmdb_id")
                        if tmdb_id is not None:
                            self.action_view_tv_series(tmdb_id)
                    else:
                        self._play(MediaRequest.from_dict(data))
            else:
                logger.debug("on_data_table_row_selected: _w_watchlist_screen is None")
        elif event.data_table.id == "fs-left-list" and self._in_following:
            if self._w_fs_left_list is not None:
                artist_id = require_key(event.row_key.value)
                if not artist_id:
                    return
                if artist_id in self._loading_artists:
                    name = self._loading_artists.get(artist_id, "?")
                    self.notify(
                        f"Caching in progress for {name}...", timeout=TIMEOUT_INFO
                    )
                    return
                self._show_artist_profile(artist_id)

    def on_data_table_row_highlighted(
        self: MediaAppProtocol, event: DataTable.RowHighlighted
    ) -> None:
        if event.data_table.id == "ls-list":
            dt = event.data_table
            if ls := self._w_liked_screen:
                track = ls._track_data_map.get(require_key(event.row_key.value))
                if track and track.get("yt_id") in self._unlike_buffer:
                    dt.add_class("-buffer-cursor")
                else:
                    dt.remove_class("-buffer-cursor")
            else:
                logger.debug("on_data_table_row_highlighted: _w_liked_screen is None")
        elif event.data_table.id == "wl-list" and self._in_watchlist:
            dt = event.data_table
            if wl := self._w_watchlist_screen:
                data = wl._bookmark_data_map.get(require_key(event.row_key.value))
                if data and data.get("tmdb_id") == wl._pending_delete_tmdb:
                    dt.add_class("-pending-cursor")
                else:
                    dt.remove_class("-pending-cursor")
                    wl.clear_pending_delete()
            else:
                logger.debug(
                    "on_data_table_row_highlighted: _w_watchlist_screen is None"
                )
        elif event.data_table.id == "fs-left-list" and self._in_following:
            dt = event.data_table
            if self._pending_unfollow_artist is not None:
                row_key = event.row_key.value
                if row_key == self._pending_unfollow_artist:
                    dt.add_class("-pending-cursor")
                else:
                    dt.remove_class("-pending-cursor")
                    self.clear_pending_unfollow()
            else:
                dt.remove_class("-pending-cursor")

    def on_list_view_highlighted(
        self: MediaAppProtocol, event: ListView.Highlighted
    ) -> None:
        if event.list_view.id != "history-list":
            return
        if self.focused is not event.list_view:
            return
        self._apply_history_gradient()

    def _apply_history_gradient(self: MediaAppProtocol) -> None:
        lv = self._w_history_list
        if lv is None:
            logger.debug("_apply_history_gradient: _w_history_list is None")
            return
        if lv.index is None or not lv.children:
            return
        items = list(lv.children)
        for i, item in enumerate(items):
            for cls in ("-hl0", "-hl1", "-hl2", "-hl3", "-hl4"):
                item.remove_class(cls, update=False)
            dist = abs(i - lv.index)
            item.add_class(f"-hl{min(dist, 4)}", update=False)
        lv.update_node_styles()

    def _watch_app_focus(self: MediaAppProtocol, focus: bool) -> None:
        """Track focused widget across app blur/focus events."""
        self.screen.update_node_styles()
        if not focus:
            self._last_focused_on_app_blur = self.screen.focused
        else:
            self._last_focused_on_app_blur = None
