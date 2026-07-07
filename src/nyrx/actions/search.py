# SPDX-License-Identifier: AGPL-3.0-only

"""Search mixin: search execution, pagination, history, and spinner management."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from textual import work
from textual.widgets import ContentSwitcher, Input, ListItem, Static

from nyrx.config import (
    RADIO_INDEX_PAGE,
    SEVERITY_ERROR,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
    update_config,
)
from nyrx.helpers import BRAILLE_SPINNER
from nyrx.models import MediaRequest
from nyrx.modes import Source
from nyrx.screens import SearchModal
from nyrx.sources.soundcloud import get_watched_secs
from nyrx.widgets import HistoryItem, ResultItem, SCHomeView

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from textual.timer import Timer
    from textual.widgets import Button

    from nyrx.protocols import MediaAppProtocol


class SearchActions:
    _notify_timer: Timer | None
    _last_failed_query: str | None
    _spinning_history_item: HistoryItem | None
    _spinning_chip: Button | None
    _chip_spinner_timer: Timer | None
    _search_token: int

    def _switch_results_to(self: MediaAppProtocol, state: str) -> None:
        try:
            sw = self.query_one("#rs-switcher", ContentSwitcher)
            sw.current = state
            self.log(
                "_switch_results_to: target=%s actual=%s ok=%s children=%s",
                state,
                sw.current,
                sw.current == state,
                [c.id for c in sw.children],
            )
        except Exception as e:
            self.log("_switch_results_to: EXCEPTION state=%s error=%s", state, e)

    @work(thread=True, exclusive=True, group="search")
    def _perform_search(self: MediaAppProtocol, limit: int) -> None:
        self._search_token += 1
        token = self._search_token
        query = self._query
        source = str(self._source)
        try:
            results = self._sources[source].search(query, limit=limit)
            if results:
                self.call_from_thread(
                    self._on_initial_results, results, query, source, token
                )
            else:
                if self._search_token != token:
                    return
                self.call_from_thread(self._show_info, "No results found.")
        except Exception as e:
            if self._search_token != token:
                return
            if not self._online or not self._check_connectivity():
                self._last_failed_query = query
                if self._online:
                    self.call_from_thread(self._handle_connectivity_result, False)
                self.call_from_thread(self._show_info, "No internet connection.")
            else:
                self.call_from_thread(self._show_info, f"Search failed: {e}")

    def _on_initial_results(
        self: MediaAppProtocol, results: list[dict], query: str, source: str, token: int
    ) -> None:
        if (
            token != self._search_token
            or query != self._query
            or source != str(self._source)
        ):
            return
        self._all_results = results
        self._page = 0
        if query:
            h = self._search_histories[self._history_key()]
            if not h or h[0] != query:
                if query in h:
                    h.remove(query)
                h.insert(0, query)
                if len(h) > 10:
                    h[:] = h[:10]
                update_config(search_histories=self._search_histories)
                self._rebuild_history_list()
        self._show_page()
        self._exhausted = len(self._all_results) < self._page_size * 3

    @work(thread=True, exclusive=True, group="search")
    def _fetch_next_page(self: MediaAppProtocol) -> None:
        target_page = self._page + 1
        new_limit = (target_page + 1) * self._page_size
        token = self._search_token
        query = self._query
        source = str(self._source)
        try:
            results = self._sources[source].search(query, limit=new_limit)
            self.call_from_thread(
                self._on_fetch_done, results, target_page, query, source, token
            )
        except Exception as e:
            if self._search_token != token:
                return
            if not self._online or not self._check_connectivity():
                self._last_failed_query = query
                if self._online:
                    self.call_from_thread(self._handle_connectivity_result, False)
                self.call_from_thread(
                    self.notify,
                    "No internet connection. Can't fetch more results.",
                    severity=SEVERITY_ERROR,
                    timeout=TIMEOUT_ERROR,
                    title="Error",
                )
            else:
                self.call_from_thread(
                    self.notify,
                    f"Failed to load more: {e}",
                    severity="error",
                    timeout=3,
                )
            self.call_from_thread(self._switch_results_to, "rs-empty")

    def _on_fetch_done(
        self: MediaAppProtocol,
        results: list[dict],
        target_page: int,
        query: str,
        source: str,
        token: int,
    ) -> None:
        if (
            token != self._search_token
            or query != self._query
            or source != str(self._source)
        ):
            return
        self._all_results = results
        start = target_page * self._page_size
        if start < len(self._all_results):
            self._page = target_page
            self._show_page()
        else:
            self._exhausted = True
            self._notify_once("No more results.")

    def _show_loading(self: MediaAppProtocol) -> None:
        self.log("_show_loading: page=%d total=%d", self._page, len(self._all_results))
        self._exit_landing_mode()
        if es := self._w_empty_state:
            es.display = False
        else:
            logger.debug("_show_loading: _w_empty_state is None")
        self._switch_results_to("rs-loading")

    def _show_page(self: MediaAppProtocol) -> None:
        """Render the current page of search results into the results ListView."""
        self.log(
            "_show_page: page=%d total=%d page_size=%d",
            self._page,
            len(self._all_results),
            self._page_size,
        )
        self._stop_history_spinner()
        self._exit_landing_mode()
        if es := self._w_empty_state:
            es.display = False
        else:
            logger.debug("_show_page: _w_empty_state is None")
        liked_set = {t.get("yt_id", "") for t in self._sc_liked}
        followed_set = {a.get("id", "") for a in self._sc_followed}
        watched_map = get_watched_secs()
        bookmarked_ids = {f"tmdb_{b.get('tmdb_id')}" for b in self._tv_bookmarks}
        lv = self._w_results_list
        if lv is None:
            logger.debug("_show_page: _w_results_list is None, skipping page render")
            return
        lv.clear()
        start = self._page * self._page_size
        page_items = self._all_results[start : start + self._page_size]
        if not page_items:
            self._show_info("No more results.")
            return
        self._switch_results_to("results-list")
        for data in page_items:
            ytid = data.get("yt_id", "")
            source = data.get("source", "")
            liked = ytid in liked_set
            if source == "tv_movies":
                liked = ytid in bookmarked_ids
            watched = False
            if source != "soundcloud":
                ws = watched_map.get(ytid, 0)
                dur = data.get("duration", 0) or 0
                watched = dur > 0 and ws / dur >= 0.8
            lv.append(
                ResultItem(
                    data,
                    liked=liked,
                    watched=watched,
                    following=data.get("uploader_id", "") in followed_set,
                )
            )
        lv.index = 0
        lv.focus()

    def action_next_page(self: MediaAppProtocol) -> None:
        """Advance to the next page of search results."""
        if self._np_focused:
            return
        if self._in_tv_series:
            return
        is_radio = self._source == Source.RADIO
        if is_radio:
            max_page = (
                max(0, (self._radio_total_filtered - 1) // RADIO_INDEX_PAGE)
                if self._radio_total_filtered
                else 0
            )
            if self._radio_page < max_page:
                self._radio_page += 1
                self._populate_radio_list()
                self._update_sidebar_context()
            return
        if not self._all_results:
            return
        next_start = (self._page + 1) * self._page_size
        self.log(
            "action_next_page: page=%d next_start=%d total=%d",
            self._page,
            next_start,
            len(self._all_results),
        )
        if next_start < len(self._all_results):
            self._page += 1
            self._show_page()
            return
        if self._exhausted:
            self._notify_once("No more results.")
            return
        if not self._online:
            self._notify_once(
                "Can't fetch more pages, no internet detected.", severity="error"
            )
            return
        self._show_loading()
        self._fetch_next_page()

    def action_prev_page(self: MediaAppProtocol) -> None:
        """Return to the previous page of search results."""
        if self._np_focused:
            return
        if self._in_tv_series:
            return
        is_radio = self._source == Source.RADIO
        if is_radio:
            if self._radio_page > 0:
                self._radio_page -= 1
                self._populate_radio_list()
                self._update_sidebar_context()
            return
        if self._page == 0 or not self._all_results:
            self.log(
                "action_prev_page: GUARDED page=%d total=%d",
                self._page,
                len(self._all_results),
            )
            return
        self._page -= 1
        self.log("action_prev_page: DECREMENTED page=%d", self._page)
        self._show_page()

    def _show_info(self: MediaAppProtocol, msg: str = "") -> None:
        self.log(
            "_show_info: msg=%s page=%d total=%d",
            msg,
            self._page,
            len(self._all_results),
        )
        self._stop_history_spinner()
        self._exit_landing_mode()
        self._switch_results_to("rs-empty")
        if (rv := self._w_results_list) is not None:
            rv.clear()
        else:
            logger.debug("_show_info: _w_results_list is None, skipping clear")
        if msg:
            self.notify(msg, timeout=TIMEOUT_INFO)

    def _notify_once(
        self: MediaAppProtocol, msg: str, timeout: int = TIMEOUT_INFO, **kwargs: Any
    ) -> None:
        if self._notify_timer is not None:
            return
        self.notify(msg, timeout=timeout, **kwargs)
        self._notify_timer = self.set_timer(
            timeout + 0.1, lambda: setattr(self, "_notify_timer", None)
        )

    def _stop_history_spinner(self: MediaAppProtocol) -> None:
        if self._spinning_history_item:
            self._spinning_history_item.stop_spinner()
            self._spinning_history_item = None

    def _advance_chip_spinner(self: MediaAppProtocol) -> None:
        if not self._spinning_chip:
            return
        frames = list(BRAILLE_SPINNER)
        self._chip_spinner_idx = (self._chip_spinner_idx + 1) % len(frames)
        try:
            self.query_one("#sc-home", SCHomeView).update_chip_spinner(
                f" {frames[self._chip_spinner_idx]}"
            )
        except Exception:
            logger.debug("Failed to advance chip spinner")

    def _start_chip_spinner(self: MediaAppProtocol) -> None:
        self._advance_chip_spinner()
        self._chip_spinner_timer = self.set_interval(0.08, self._advance_chip_spinner)

    def _stop_chip_spinner(self: MediaAppProtocol) -> None:
        if self._chip_spinner_timer:
            self._chip_spinner_timer.stop()
            self._chip_spinner_timer = None
        self._spinning_chip = None
        try:
            self.query_one("#sc-home", SCHomeView).clear_chip_spinner()
        except Exception:
            logger.debug("Failed to stop chip spinner")

    def _run_history_search(self: MediaAppProtocol, item: HistoryItem) -> None:
        if not self._online:
            self.notify(
                "No internet connection.",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            return
        self._query = item._query
        self._all_results = []
        self._page = 0

        def _start_spinner() -> None:
            self._spinning_history_item = item
            item.start_spinner(self)

        self.call_after_refresh(_start_spinner)
        self._perform_search(self._page_size * 3)

    def _on_search_result(self: MediaAppProtocol, result: str | dict | None) -> None:
        if isinstance(result, dict):
            self._enter_landing_mode()
            self._play(MediaRequest.from_dict(result))
        elif isinstance(result, str):
            query = result.strip()
            if query:
                h = self._search_histories[self._history_key()]
                if query in h:
                    h.remove(query)
                h.insert(0, query)
                if len(h) > 10:
                    h[:] = h[:10]
                update_config(search_histories=self._search_histories)
                self._rebuild_history_list()
                self._query = query
                self._all_results = []
                self._page = 0
                self._show_loading()
                self._perform_search(self._page_size * 3)

    def _rebuild_history_list(self: MediaAppProtocol) -> None:
        if self._source != Source.YOUTUBE:
            return
        lv = self._w_history_list
        if lv is None:
            logger.debug("_rebuild_history_list: _w_history_list is None")
            return
        lv.clear()
        history = self._search_histories.get(self._history_key(), [])
        has_items = bool(history)
        lv.display = True
        if eh := self._w_empty_heading:
            eh.display = True
        else:
            logger.debug("_rebuild_history_list: _w_empty_heading is None")
        if has_items:
            for q in history:
                lv.append(HistoryItem(q))
            lv.index = 0
            self._apply_history_gradient()
        else:
            lv.append(ListItem(Static("[dim]No recent searches[/dim]")))

    def action_open_search(self: MediaAppProtocol) -> None:
        if self._np_focused:
            return
        if self._in_tv_series and self._w_tv_series:
            from nyrx.screens.season_jump import SeasonJumpModal

            self.push_screen(
                SeasonJumpModal(
                    season_count=self._w_tv_series._season_count,
                    current_season=self._w_tv_series._current_season,
                ),
                self._on_season_jump_result,
            )
            return
        if self._in_liked:
            if ls := self._w_liked_screen:
                ls.query_one("#ls-search", Input).focus()
            else:
                logger.debug("action_open_search: _w_liked_screen is None")
            return
        if self._in_watchlist:
            if wl := self._w_watchlist_screen:
                wl.query_one("#wl-search", Input).focus()
            else:
                logger.debug("action_open_search: _w_watchlist_screen is None")
            return
        if self._in_following:
            if sc := self._w_sc_home:
                sc.focus_filter()
            return
        if self._source == Source.RADIO:
            self.action_open_filter()
            return
        if not self._online:
            self.notify(
                "No internet connection. Search unavailable.",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            return
        self.push_screen(SearchModal(), self._on_search_result)

    def _history_key(self: MediaAppProtocol) -> str:
        if self._source == Source.YOUTUBE:
            return "youtube"
        if self._source == Source.SOUNDCLOUD:
            return "soundcloud"
        if self._source == Source.TV_MOVIES:
            return "tv_movies"
        return "youtube"
