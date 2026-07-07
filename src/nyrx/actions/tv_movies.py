# SPDX-License-Identifier: AGPL-3.0-only

"""TV/Movies mixin: watchlist management, hotswap config, TMDB key handling, TV series viewing, bookmark management."""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, cast

from textual import work
from textual.widget import Widget
from textual.widgets import ContentSwitcher, DataTable, Input, Static

from nyrx import watch_db
from nyrx.config import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
)
from nyrx.modes import Source, View
from nyrx.sources.tv_movies.db import (
    bookmark_exists,
    delete_bookmark,
    load_bookmark,
    load_bookmarks,
    save_bookmark,
    save_seasons,
)
from nyrx.sources.tv_movies.thumb_cache import cache_tv_poster
from nyrx.sources.tv_movies.tmdb_cache import genre_names, movie_details, tv_details
from nyrx.widgets import ResultItem, TVChip, TVSeriesView

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol


class TVMoviesActions:
    _w_tv_series: TVSeriesView | None

    def _on_season_jump_result(self: MediaAppProtocol, result: int | None) -> None:
        if result is None or not self._w_tv_series:
            return
        self._w_tv_series._load_season_episodes(result)

    def action_view_tv_series(self: MediaAppProtocol, tmdb_id: int) -> None:
        """Mount the series screen for a given TMDB series ID.

        Call sites decide "view series" vs "play" and call this action directly.
        """
        prev_focus = self.focused
        was_watchlist = self._in_watchlist
        was_view = self._view

        if was_view == View.LANDING:
            self._save_tv_home_focus()

        def restore() -> None:
            logger.debug(
                "tvs restore: was_view=%s was_watchlist=%s prev_focus_attached=%s",
                was_view,
                was_watchlist,
                prev_focus.is_attached if prev_focus else None,
            )
            self._in_watchlist = was_watchlist
            self._in_tv_series = False
            self._view = was_view
            self._apply_view()
            if was_watchlist:
                self._in_watchlist = True
                if mc := self._w_main_content:
                    mc.add_class("watchlist-mode")
                if wl := self._w_watchlist_screen:
                    wl.display = True
                    if dt := wl.query_one("#wl-list", DataTable):
                        dt.focus()
                self._apply_sidebar(self.screen.has_class("wide"))
            else:
                if prev_focus and prev_focus.is_attached:
                    cast(Widget, prev_focus).focus()

        self._push_tv_nav(restore)

        if self._w_tv_series is not None:
            self._w_tv_series.remove()
            self._w_tv_series = None

        start_season = watch_db.get_last_watched_season(f"tmdb_{tmdb_id}") or 1
        tv_series = TVSeriesView(tmdb_id=tmdb_id, start_season=start_season)
        rw = self.query_one("#results-wrapper")
        if self._w_tv_home:
            self._w_tv_home.display = False
        if self._w_results_list:
            self._w_results_list.add_class("hidden")
        try:
            self.query_one("#rs-switcher", ContentSwitcher).display = False
        except Exception:
            logger.debug("action_view_tv_series: #rs-switcher not found, skipping hide")

        self._w_tv_series = tv_series
        rw.mount(tv_series)
        tv_series.focus()

        self._in_tv_series = True
        self._in_watchlist = False
        self._update_sidebar_context()
        self._render_focus_indicators()

    def _hide_tv_series(self: MediaAppProtocol, widget: TVSeriesView) -> None:
        """Remove TVSeriesView widget and restore previous state via nav stack."""
        attached_before = widget.is_attached
        logger.debug(
            "_hide_tv_series: widget=%s attached_before=%s nav_stack=%d",
            widget,
            attached_before,
            len(self._tv_nav_stack),
        )
        widget.remove()
        self._w_tv_series = None
        self._pop_tv_nav()
        self._update_sidebar_context()
        self._render_focus_indicators()
        logger.debug(
            "_hide_tv_series: after remove attached=%s nav_stack=%d",
            widget.is_attached,
            len(self._tv_nav_stack),
        )

    def _push_tv_nav(self: MediaAppProtocol, restore: Callable[[], None]) -> None:
        self._tv_nav_stack.append(restore)

    def _pop_tv_nav(self: MediaAppProtocol) -> None:
        if self._tv_nav_stack:
            logger.debug(
                "_pop_tv_nav: popping restore closure, remaining=%d",
                len(self._tv_nav_stack) - 1,
            )
            self._tv_nav_stack.pop()()
        else:
            logger.debug("_pop_tv_nav: stack EMPTY, no restore closure to run")

    def action_delete_bookmark(self: MediaAppProtocol) -> None:
        """Two-step delete: first press reddens row, second press removes it.
        Dispatches to action_unfollow_artist when in following context."""
        if self._in_following:
            self.action_unfollow_artist()
            return
        if not self._in_watchlist:
            return
        wl = self._w_watchlist_screen
        if wl is None:
            return
        data = wl.focused_bookmark()
        if data is None:
            return
        tmdb_id = data.get("tmdb_id")
        if tmdb_id is None:
            return

        if wl._pending_delete_tmdb == tmdb_id:
            logger.debug("delete_bookmark: second press tmdb_id=%s", tmdb_id)
            wl._pending_delete_tmdb = None
            wl.remove_bookmark_row(tmdb_id)
            self._tv_bookmarks = [
                b for b in self._tv_bookmarks if b.get("tmdb_id") != tmdb_id
            ]
            self._do_delete_bookmark(tmdb_id)
        else:
            logger.debug("delete_bookmark: first press tmdb_id=%s", tmdb_id)
            wl.set_pending_delete(tmdb_id)

    @work(thread=True, group="tv-bookmark-del")
    def _do_delete_bookmark(self: MediaAppProtocol, tmdb_id: int) -> None:
        """Background DB delete: visual removal already done."""
        try:
            delete_bookmark(tmdb_id)
        except Exception:
            logger.warning("_do_delete_bookmark: delete failed for tmdb_id=%s", tmdb_id)
            self.call_from_thread(
                self.notify, "Failed to delete bookmark", severity="warning", timeout=3
            )

    def action_show_watchlist(self: MediaAppProtocol) -> None:
        """Toggle watchlist screen (ctrl+w)."""
        if self._source != Source.TV_MOVIES:
            return
        tv = self._w_tv_home
        if tv is None or not tv.display:
            logger.debug("action_show_watchlist: _w_tv_home is None or not displayed")
            return
        if self._in_watchlist:
            self._hide_watchlist()
        else:
            self._show_watchlist()

    def _show_watchlist(self: MediaAppProtocol) -> None:
        self._save_tv_home_focus()
        self._in_watchlist = True
        logger.debug("_show_watchlist: _in_watchlist set to True")

        if tv := self._w_tv_home:
            tv.query_one("#tv-center").display = False
        else:
            logger.debug(
                "_show_watchlist: _w_tv_home is None , skipping tv-center hide"
            )

        if wl := self._w_watchlist_screen:
            wl.display = True
            wl.populate(self._tv_bookmarks)
            logger.debug(
                "_show_watchlist: populated with %d bookmarks", len(self._tv_bookmarks)
            )
        else:
            logger.debug(
                "_show_watchlist: _w_watchlist_screen is None , skipping populate"
            )

        if mc := self._w_main_content:
            mc.add_class("watchlist-mode")
        else:
            logger.debug(
                "_show_watchlist: _w_main_content is None , skipping add_class"
            )

        self._apply_sidebar(self.screen.has_class("wide"))
        self._update_sidebar_context()
        self._render_focus_indicators()

    def _hide_watchlist(self: MediaAppProtocol) -> None:
        self._in_watchlist = False
        logger.debug("_hide_watchlist: _in_watchlist set to False")

        if wl := self._w_watchlist_screen:
            wl.clear_pending_delete()
            wl.display = False
            try:
                wl.query_one("#wl-search", Input).value = ""
            except Exception:
                logger.debug("_hide_watchlist: failed to clear search input")
        else:
            logger.debug("_hide_watchlist: _w_watchlist_screen is None , skipping hide")

        if tv := self._w_tv_home:
            tv.query_one("#tv-center").display = True
        else:
            logger.debug(
                "_hide_watchlist: _w_tv_home is None , skipping tv-center show"
            )

        if tv := self._w_tv_home:
            tv.populate_watchlist(self._tv_bookmarks)

        if mc := self._w_main_content:
            mc.remove_class("watchlist-mode")
        else:
            logger.debug(
                "_hide_watchlist: _w_main_content is None , skipping remove_class"
            )

        self._update_sidebar_content()
        self._apply_sidebar(self.screen.has_class("wide"))
        self._update_sidebar_context()
        self._restore_tv_home_focus()
        self._render_focus_indicators()

        try:
            if wl := self._w_watchlist_screen:
                wl.query_one("#wl-keybind-bar", Static).update("")
            else:
                logger.debug(
                    "_hide_watchlist: _w_watchlist_screen is None , skipping keybind-bar"
                )
        except Exception:
            logger.debug("_hide_watchlist: wl-keybind-bar not found")

    def action_check_updates(self: MediaAppProtocol) -> None:
        """Re-discover local configs, then check remote manifest if set."""
        from typing import cast

        from nyrx.config import get_manifest_url
        from nyrx.sources.tv_movies import TVMoviesSource

        tv_src = cast(TVMoviesSource, self._sources["tv_movies"])
        tv_src.reload_configs()
        count = len(tv_src.server_names)

        url = get_manifest_url()
        if url:
            self._check_hotswap(manual=True)
            self.notify(
                f"Refreshed {count} local plugin(s). Checking remote...",
                timeout=TIMEOUT_INFO,
            )
        else:
            self.notify(
                f"Refreshed {count} local plugin(s). "
                "No manifest URL for remote updates.",
                timeout=TIMEOUT_INFO,
            )

    def action_configure_manifest_url(self: MediaAppProtocol) -> None:
        """Open the manifest URL input modal from command palette."""
        from nyrx.config import get_manifest_url
        from nyrx.screens import ManifestUrlModal

        current = get_manifest_url()
        self.push_screen(ManifestUrlModal(current), self._on_manifest_url_submitted)

    def _on_manifest_url_submitted(self: MediaAppProtocol, url: str | None) -> None:
        """Handle manifest URL submission from modal."""
        if url is None:
            return
        from nyrx import config
        from nyrx.config import update_config

        if not url:
            update_config(hotswap_url="", hotswap_enabled=False)
            config.HOTSWAP_MANIFEST_URL = ""
            self.notify("Manifest URL cleared", timeout=TIMEOUT_INFO)
            return

        update_config(hotswap_url=url, hotswap_enabled=True)
        config.HOTSWAP_MANIFEST_URL = url
        self._check_hotswap(manual=True)
        self.notify(
            "Manifest URL saved. Fetching server configs...", timeout=TIMEOUT_INFO
        )

    def action_toggle_hotswap(self: MediaAppProtocol) -> None:
        """Open the hotswap toggle modal from command palette."""
        from nyrx.config import get_config, get_manifest_url
        from nyrx.screens import HotswapToggle

        if not get_manifest_url():
            self.notify(
                "No manifest URL configured. Use 'Configure Lua plugin source' to set one.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        cfg = get_config()
        current = cfg.get("hotswap_enabled", True)
        self.push_screen(HotswapToggle(current), self._on_hotswap_toggled)

    def _on_hotswap_toggled(self: MediaAppProtocol, enabled: bool | None) -> None:
        """Handle hotswap toggle selection from modal."""
        if enabled is None:
            return
        from nyrx.config import get_config, update_config

        cfg = get_config()
        was_enabled = cfg.get("hotswap_enabled", True)
        if enabled == was_enabled:
            return

        update_config(hotswap_enabled=enabled)

        if enabled:
            self._check_hotswap(manual=True)
            self.notify("Lua plugin auto-updates enabled", timeout=TIMEOUT_INFO)
        else:
            self.notify("Lua plugin auto-updates disabled", timeout=TIMEOUT_INFO)

    def action_set_tmdb_key(self: MediaAppProtocol) -> None:
        """Open the TMDB API key input modal."""
        from nyrx.screens import TMDbKeyInputModal

        self.push_screen(TMDbKeyInputModal(), self._on_tmdb_key_result)

    def _on_tmdb_key_result(self: MediaAppProtocol, key: str | None) -> None:
        """Save a validated TMDB API key to keys.json under user_tmdb_keys."""
        if not key:
            return
        from nyrx.config import KEYS_PATH

        keys_path = KEYS_PATH
        try:
            keys_path.parent.mkdir(parents=True, exist_ok=True)
            import json

            existing = json.loads(keys_path.read_text()) if keys_path.is_file() else {}
            user_keys = existing.get("user_tmdb_keys", [])
            if key not in user_keys:
                user_keys.append(key)
            existing["user_tmdb_keys"] = user_keys
            tmp = keys_path.with_suffix(".tmp")
            tmp.write_text(json.dumps(existing, indent=2))
            tmp.replace(keys_path)
            from nyrx.sources.tv_movies.tmdb_cache import load_keys

            load_keys()
            self.notify(
                f"TMDB API key saved ({len(user_keys)} key(s) configured)",
                timeout=TIMEOUT_INFO,
            )
        except Exception as exc:
            self.notify(
                f"Failed to save TMDB key: {exc}",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )

    @work(thread=True, group="tmdb-preheat", exclusive=True)
    def _preheat_tmdb_cache(self: MediaAppProtocol) -> None:
        """Warm the TMDB disk cache in the background on startup."""
        try:
            from nyrx.sources.tv_movies.tmdb_cache import (
                popular,
                recommendations_from_seeds,
                trending,
            )

            self._tv_trending = trending()
            self._tv_popular = popular()
            self._tv_recs = recommendations_from_seeds(self._tv_bookmarks)
            self.call_from_thread(self._populate_tv_home)
            logger.debug("_preheat_tmdb_cache: complete")
        except Exception:
            logger.debug("_preheat_tmdb_cache: failed to warm TMDB caches")

    def _populate_tv_home(self: MediaAppProtocol) -> None:
        """Push cached TV content to the UI if TV mode is active."""
        if self._w_tv_home and self._source == Source.TV_MOVIES:
            logger.debug("_populate_tv_home: populating TV home")
            self._w_tv_home.populate(
                self._tv_bookmarks,
                self._tv_recs,
                self._tv_trending,
                self._tv_popular,
            )
        else:
            logger.debug(
                "_populate_tv_home: skipped (source=%s)",
                self._source.value if self._source is not None else self._source,
            )

    def _toggle_tv_bookmark(self: MediaAppProtocol) -> None:
        """Toggle bookmark for the currently focused TVChip or ResultItem.

        Phase 1 (main thread): minimal save from search data, instant heart.
        Phase 2 (background worker): enrich with full TMDB details.
        """
        focused = self.focused
        data: dict | None = None
        item: ResultItem | None = None

        if isinstance(focused, TVChip):
            data = focused.data
        else:
            candidate = self._current_item()
            if (
                candidate
                and isinstance(candidate, ResultItem)
                and candidate.data.get("source") == "tv_movies"
            ):
                data = candidate.data
                item = candidate

        if data is None and self._in_tv_series and self._w_tv_series:
            sv = self._w_tv_series
            data = {
                "tmdb_id": sv._tmdb_id,
                "title": sv._series_data.get("title", ""),
                "media_type": "tv",
                "year": sv._series_data.get("year", ""),
                "rating": sv._series_data.get("rating", 0),
                "vote_count": sv._series_data.get("vote_count", 0),
                "overview": sv._series_data.get("overview", ""),
            }

        if data is None:
            return

        tmdb_id = data.get("tmdb_id")
        if tmdb_id is None:
            yt_id = data.get("yt_id", "")
            if yt_id.startswith("tmdb_"):
                try:
                    tmdb_id = int(yt_id[5:])
                except (ValueError, IndexError):
                    return
            else:
                return

        if bookmark_exists(tmdb_id):
            delete_bookmark(tmdb_id)
            self._tv_bookmarks = load_bookmarks()
            if item:
                item.set_liked(False)
            logger.debug("_toggle_tv_bookmark: removed tmdb_id=%s", tmdb_id)
        else:
            media_type = data.get("media_type", "movie")
            poster_path = data.get("poster", "") or ""

            minimal = {
                "tmdb_id": tmdb_id,
                "title": data.get("title", ""),
                "media_type": media_type,
                "year": data.get("year", ""),
                "rating": data.get("rating", 0),
                "vote_count": data.get("vote_count", 0),
                "poster_path": poster_path,
                "tagline": None,
                "overview": data.get("overview", ""),
                "genres": "",
                "runtime": None,
                "season_count": None,
                "number_of_episodes": None,
                "enriched_at": None,
            }
            save_bookmark(minimal)
            self._tv_bookmarks = load_bookmarks()
            if item:
                item.set_liked(True)
            logger.debug(
                "_toggle_tv_bookmark: added tmdb_id=%s title=%s",
                tmdb_id,
                data.get("title", ""),
            )
            self._enrich_tv_bookmark(tmdb_id)

        if self._w_tv_home:
            self._refresh_tv_home()

        if self._in_tv_series and self._w_tv_series:
            self._w_tv_series._bookmarked = bookmark_exists(tmdb_id)
            self._w_tv_series._update_header()

    def _refresh_tv_home(self: MediaAppProtocol) -> None:
        """Re-populate TVHomeView with current bookmarks.

        Recommendations are only refreshed on startup via
        ``_preheat_tmdb_cache``; this method updates bookmark state
        on existing chips without triggering a network fetch.
        """
        if self._w_tv_home and self._w_tv_home.display:
            self._w_tv_home.populate_watchlist(self._tv_bookmarks)
            for row_id in (
                "#tv-rec-content",
                "#tv-trending-content",
                "#tv-popular-content",
            ):
                row = self._w_tv_home.query_one(row_id)
                for chip in row.children:
                    if isinstance(chip, TVChip):
                        chip.bookmarked = chip.data.get("tmdb_id") in {
                            b["tmdb_id"] for b in self._tv_bookmarks
                        }
                        chip.refresh()

    @work(thread=True, group="tv-bookmark-enrich")
    def _enrich_tv_bookmark(self: MediaAppProtocol, tmdb_id: int) -> None:
        """Phase 2: enrich a minimal bookmark with full TMDB details."""
        try:
            existing = load_bookmark(tmdb_id)
            if not existing:
                return

            media_type = existing["media_type"]

            if media_type == "movie":
                details = movie_details(tmdb_id)
                if details:
                    genres = genre_names([g["id"] for g in details.get("genres", [])])
                    save_bookmark(
                        {
                            "tmdb_id": tmdb_id,
                            "title": existing["title"],
                            "media_type": "movie",
                            "year": existing.get("year", ""),
                            "rating": details.get(
                                "vote_average", existing.get("rating", 0)
                            )
                            or 0,
                            "vote_count": details.get(
                                "vote_count", existing.get("vote_count", 0)
                            ),
                            "poster_path": details.get("poster_path")
                            or existing.get("poster_path", ""),
                            "tagline": details.get("tagline"),
                            "overview": details.get("overview")
                            or existing.get("overview", ""),
                            "genres": json.dumps(genres),
                            "runtime": details.get("runtime"),
                            "season_count": None,
                            "number_of_episodes": None,
                        }
                    )
                    cache_tv_poster(tmdb_id, details.get("poster_path") or "")
            else:
                details = tv_details(tmdb_id)
                if details:
                    seasons = [
                        {
                            "season_number": s["season_number"],
                            "episode_count": s.get("episode_count", 0),
                            "name": s.get("name", ""),
                            "poster_path": s.get("poster_path", ""),
                            "air_date": s.get("air_date", ""),
                        }
                        for s in details.get("seasons", [])
                        if s.get("season_number", 0) > 0
                    ]
                    genres = genre_names([g["id"] for g in details.get("genres", [])])
                    save_bookmark(
                        {
                            "tmdb_id": tmdb_id,
                            "title": existing["title"],
                            "media_type": "tv",
                            "year": existing.get("year", "")
                            or (details.get("first_air_date") or "")[:4],
                            "rating": details.get(
                                "vote_average", existing.get("rating", 0)
                            )
                            or 0,
                            "vote_count": details.get(
                                "vote_count", existing.get("vote_count", 0)
                            ),
                            "poster_path": details.get("poster_path")
                            or existing.get("poster_path", ""),
                            "tagline": None,
                            "overview": details.get("overview")
                            or existing.get("overview", ""),
                            "genres": json.dumps(genres),
                            "runtime": None,
                            "season_count": len(seasons),
                            "number_of_episodes": details.get("number_of_episodes", 0),
                        }
                    )
                    if seasons:
                        save_seasons(tmdb_id, seasons)
                    cache_tv_poster(tmdb_id, details.get("poster_path") or "")

            self.call_from_thread(self._reload_bookmarks)
            logger.debug("_enrich_tv_bookmark: enriched tmdb_id=%s", tmdb_id)
        except Exception:
            logger.warning("_enrich_tv_bookmark: failed for tmdb_id=%s", tmdb_id)
            self.call_from_thread(
                self.notify, "Failed to enrich bookmark", severity="warning", timeout=3
            )

    def _reload_bookmarks(self: MediaAppProtocol) -> None:
        """Reload bookmarks from DB and refresh the TV home + open watchlist."""
        self._tv_bookmarks = load_bookmarks()
        if self._in_watchlist and self._w_watchlist_screen:
            self._w_watchlist_screen.update_bookmarks(self._tv_bookmarks)
        self._refresh_tv_home()
