# SPDX-License-Identifier: AGPL-3.0-only

"""SoundCloud mixin: feed, follow, like, station, artist profile, collections."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import work
from textual.events import Key as KeyEvent
from textual.widgets import (
    ContentSwitcher,
    DataTable,
    Input,
    Static,
)

from nyrx.config import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    TIMEOUT_CONFIRM,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
)
from nyrx.helpers import BRAILLE_SPINNER, require_key
from nyrx.models import MediaRequest
from nyrx.modes import Source
from nyrx.queues import QueueItem
from nyrx.screens import CollectionBrowser, URLInputModal
from nyrx.sources.soundcloud import (
    CACHE_QUEUE,
    ProfileResolveError,
    delete_artist_cache,
    enqueue_artist_cache,
    follow_sc,
    generate_feed,
    get_feed_age,
    is_sc_followed,
    is_sc_liked,
    load_feed,
    needs_artist_refresh,
    process_artist_cache,
    save_feed,
    sync_liked_from_profile,
    toggle_sc_like,
    unfollow_sc,
)
from nyrx.widgets import (
    FeedTrackItem,
    ResultItem,
    SoundCloudNowPlaying,
    _short_views,
)

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from textual.timer import Timer

    from nyrx.protocols import MediaAppProtocol

SPINNER_FRAMES = list(BRAILLE_SPINNER)


class SoundCloudActions:
    _fs_spinner_timer: Timer | None
    _pending_unfollow_artist: str | None

    @work(thread=True, group="sc-warm", exclusive=True)
    def _warm_sc_client(self: MediaAppProtocol) -> None:
        """Resolve the SoundCloud client_id in the background on startup."""
        from nyrx.sources.soundcloud.api import ensure_client_id

        ensure_client_id()
        self.call_from_thread(self._on_sc_client_warmed)

    def _on_sc_client_warmed(self: MediaAppProtocol) -> None:
        """Refresh the SC-home trending label when the client_id becomes available."""
        sc = self._w_sc_home
        if sc is not None and sc.display:
            sc._update_trending_label()

    # ── Following area ─────────────────────────────────────

    def action_show_following(self: MediaAppProtocol) -> None:
        if self._np_focused:
            return
        sc = self._w_sc_home
        if sc is None:
            logger.debug("action_show_following: _w_sc_home is None")
            return
        if not sc.display:
            return
        if self._in_liked:
            return
        if self._in_artist_profile:
            self._hide_artist_profile()
            return
        if self._in_following:
            self._hide_following()
        else:
            self._show_following()

    def _show_following(self: MediaAppProtocol) -> None:
        """Show the two-panel following view (artist list + feed)."""
        self._save_sc_home_focus()
        if sc := self._w_sc_home:
            sc.query_one("#sch-center").display = False
        else:
            logger.debug(
                "_show_following: _w_sc_home is None: skipping sch-center hide"
            )
        if fa := self._w_following_area:
            fa.display = True
        else:
            logger.debug("_show_following: _w_following_area is None: skipping display")
        if ap := self._w_artist_profile:
            ap.display = False
        else:
            logger.debug("_show_following: _w_artist_profile is None, skipping hide")
        if sc := self._w_sc_home:
            sc.query_one("#fs-left").display = True
            sc.query_one("#fs-center").display = True
        else:
            logger.debug(
                "_show_following: _w_sc_home is None: skipping fs-left/center show"
            )
        self._in_following = True
        self._in_artist_profile = False
        if mc := self._w_main_content:
            mc.add_class("following-mode")
        else:
            logger.debug("_show_following: _w_main_content is None: skipping add_class")
        self._apply_sidebar(self.screen.has_class("wide"))

        if (dt := self._w_fs_left_list) is not None:
            dt.clear()
            for a in self._sc_followed:
                artist_id = a.get("id", "")
                name = a.get("name", a.get("permalink", "?"))
                dt.add_row(Text(name), key=artist_id)
        else:
            logger.debug(
                "_show_following: _w_fs_left_list is None: skipping population"
            )
        if sc := self._w_sc_home:
            sc.set_following_artists(self._sc_followed)
            sc.query_one("#fs-search", Input).value = ""
            sc._apply_filter("")
        self._refresh_fs_spinners()

        if not self._sc_followed:
            try:
                self.query_one(
                    "#fs-center-switcher", ContentSwitcher
                ).current = "feed-empty"
            except Exception:
                logger.debug("_restore_following_view: feed-empty switcher not found")
        elif self._regen_in_progress:
            self._set_feed_loading()
        elif get_feed_age() > 24:
            self._set_feed_loading()
            self._regen_feed_worker()
        elif not self._feed_populated:
            self._populate_center_feed()
        else:
            try:
                self.query_one(
                    "#fs-center-switcher", ContentSwitcher
                ).current = "feed-list"
            except Exception:
                logger.debug("_restore_following_view: feed-list switcher not found")
            self._refresh_feed_liked_indicators()

        if (ll := self._w_fs_left_list) is not None:
            ll.focus()
        else:
            logger.debug("_show_following: _w_fs_left_list is None, skipping focus")
        self._update_sidebar_context()

    def _hide_following(self: MediaAppProtocol) -> None:
        """Hide the following view and restore the SoundCloud home layout."""
        if fa := self._w_following_area:
            fa.display = False
        else:
            logger.debug("_hide_following: _w_following_area is None: skipping display")
        if sc := self._w_sc_home:
            sc.query_one("#sch-center").display = True
        else:
            logger.debug(
                "_hide_following: _w_sc_home is None: skipping sch-center show"
            )
        if self._fs_spinner_timer:
            self._fs_spinner_timer.stop()
            self._fs_spinner_timer = None
        self._loading_artists.clear()
        self._in_following = False
        self._in_artist_profile = False
        if mc := self._w_main_content:
            mc.remove_class("following-mode")
        else:
            logger.debug(
                "_hide_following: _w_main_content is None: skipping remove_class"
            )
        if sc := self._w_sc_home:
            sc._populate_following(self._sc_followed)
        else:
            logger.debug(
                "_hide_following: _w_sc_home is None: skipping _populate_following"
            )
        self._update_sidebar_content()
        self._apply_sidebar(self.screen.has_class("wide"))
        self._update_sidebar_context()
        self._restore_sc_home_focus()
        self._render_focus_indicators()

    def _show_artist_profile(self: MediaAppProtocol, artist_id: str) -> None:
        """Show the artist profile view for the given SoundCloud artist ID."""
        if sc := self._w_sc_home:
            sc.query_one("#fs-left").display = False
            sc.query_one("#fs-center").display = False
            sc.query_one("#fs-left").disabled = True
            sc.query_one("#fs-center").disabled = True
        else:
            logger.debug(
                "_show_artist_profile: _w_sc_home is None: skipping fs-left/center hide"
            )
        if w := self._w_artist_profile:
            w.display = True
        else:
            logger.debug(
                "_show_artist_profile: _w_artist_profile is None: skipping display"
            )
        self._in_following = False
        self._in_artist_profile = True
        liked_ids = {t.get("yt_id", "") for t in self._sc_liked}
        followed_set = {a.get("id", "") for a in self._sc_followed}
        if w := self._w_artist_profile:
            w.populate(artist_id, liked_ids=liked_ids, followed_set=followed_set)
        else:
            logger.debug(
                "_show_artist_profile: _w_artist_profile is None: skipping populate"
            )
        self._update_sidebar_context()
        self._render_focus_indicators()

    def _hide_artist_profile(self: MediaAppProtocol) -> None:
        """Hide artist profile and return to the following view."""
        if ap := self._w_artist_profile:
            ap.display = False
        else:
            logger.debug(
                "_hide_artist_profile: _w_artist_profile is None: skipping display"
            )
        if sc := self._w_sc_home:
            sc.query_one("#fs-left").display = True
            sc.query_one("#fs-center").display = True
            sc.query_one("#fs-left").disabled = False
            sc.query_one("#fs-center").disabled = False
        else:
            logger.debug(
                "_hide_artist_profile: _w_sc_home is None: skipping fs-left/center show"
            )
        self._in_artist_profile = False
        self._in_following = True

        if (dt := self._w_fs_left_list) is not None:
            dt.clear()
            for a in self._sc_followed:
                artist_id = a.get("id", "")
                name = a.get("name", a.get("permalink", "?"))
                dt.add_row(Text(name), key=artist_id)
        else:
            logger.debug(
                "_hide_artist_profile: _w_fs_left_list is None: skipping population"
            )
        if sc := self._w_sc_home:
            sc.set_following_artists(self._sc_followed)
            sc.query_one("#fs-search", Input).value = ""
            sc._apply_filter("")
        self._refresh_fs_spinners()

        if not self._sc_followed:
            try:
                self.query_one(
                    "#fs-center-switcher", ContentSwitcher
                ).current = "feed-empty"
            except Exception:
                logger.debug("_hide_artist_profile: feed-empty switcher not found")
        elif self._regen_in_progress:
            self._set_feed_loading()
        elif get_feed_age() > 24:
            self._set_feed_loading()
            self._regen_feed_worker()
        elif not self._feed_populated:
            self._populate_center_feed()
        else:
            try:
                self.query_one(
                    "#fs-center-switcher", ContentSwitcher
                ).current = "feed-list"
            except Exception:
                logger.debug("_hide_artist_profile: feed-list switcher not found")
            self._refresh_feed_liked_indicators()

        if (ll := self._w_fs_left_list) is not None:
            ll.focus()
        else:
            logger.debug(
                "_hide_artist_profile: _w_fs_left_list is None: skipping focus"
            )
        self._update_sidebar_context()
        self._render_focus_indicators()

    # ── Following spinner helpers ───────────────────────────────────

    def _is_artist_fully_cached(self: MediaAppProtocol, artist_id: str) -> bool:
        for cat in ("profile", "collections", "uploads", "likes"):
            if needs_artist_refresh(artist_id, cat):
                return False
        return True

    def _start_following_spinner(
        self: MediaAppProtocol, artist_id: str, name: str
    ) -> None:
        if artist_id in self._loading_artists:
            return
        self._loading_artists[artist_id] = name
        if dt := self._w_fs_left_list:
            try:
                dt.update_cell(
                    artist_id,
                    "name",
                    Text(f"{SPINNER_FRAMES[0]} {name}", style="#808080"),
                )
            except Exception:
                logger.debug(
                    "_start_following_spinner: update_cell failed for artist_id=%s",
                    artist_id,
                )
        if not self._fs_spinner_timer:
            self._fs_spinner_timer = self.set_interval(
                0.08, self._tick_following_spinner
            )

    def _tick_following_spinner(self: MediaAppProtocol) -> None:
        if not self._loading_artists:
            return
        self._fs_spinner_frame = (self._fs_spinner_frame + 1) % len(SPINNER_FRAMES)
        frame = SPINNER_FRAMES[self._fs_spinner_frame]
        dt = self._w_fs_left_list
        if not dt:
            return
        for artist_id, name in self._loading_artists.items():
            try:
                dt.update_cell(
                    artist_id, "name", Text(f"{frame} {name}", style="#808080")
                )
            except Exception:
                logger.debug(
                    "_tick_following_spinner: update_cell failed for artist_id=%s",
                    artist_id,
                )

    def _stop_following_spinner(self: MediaAppProtocol, artist_id: str) -> None:
        name = self._loading_artists.pop(artist_id, None)
        if name is not None and (dt := self._w_fs_left_list):
            try:
                dt.update_cell(artist_id, "name", Text(name, style="#808080"))
            except Exception:
                logger.debug(
                    "_stop_following_spinner: update_cell failed for artist_id=%s",
                    artist_id,
                )
        if not self._loading_artists and self._fs_spinner_timer:
            self._fs_spinner_timer.stop()
            self._fs_spinner_timer = None

    def _refresh_fs_spinners(self: MediaAppProtocol) -> None:
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            return
        ll = self._w_fs_left_list
        if not ll:
            return
        for artist in self._sc_followed:
            aid = artist.get("id", "")
            if not aid:
                continue
            if (
                not self._is_artist_fully_cached(aid)
                and aid not in self._loading_artists
            ):
                name = artist.get("name", artist.get("permalink", "?"))
                self._start_following_spinner(aid, name)

    def on_key(self: MediaAppProtocol, event: KeyEvent) -> None:
        if self._handle_following_nav(event.key):
            event.stop()
            return
        if (
            self._in_following
            and not self._in_artist_profile
            and event.key in ("r", "z", "s", "l")
            and (ll := self._w_fs_left_list) is not None
            and ll.has_focus
        ):
            event.stop()

    def _handle_following_nav(self: MediaAppProtocol, key: str) -> bool:
        if not self._in_following or self._in_artist_profile or self._in_liked:
            return False
        left_list = self._w_fs_left_list
        if left_list is None:
            logger.debug("_handle_following_nav: _w_fs_left_list is None")
            return False
        center_list = self._w_fs_center_list
        if center_list is None:
            logger.debug("_handle_following_nav: _w_fs_center_list is None")
            return False
        if key == "right" and left_list.has_focus:
            if center_list.children and center_list.index is None:
                center_list.index = 0
            center_list.focus()
            return True
        if key == "left" and center_list.has_focus:
            left_list.focus()
            return True
        return False

    # ── Feed ───────────────────────────────────────────────

    @work(thread=True, exclusive=True, name="feed_regen", group="feed-regen")
    def _regen_feed_worker(self: MediaAppProtocol) -> None:
        try:
            feed = generate_feed()
            save_feed(feed)
            self.call_from_thread(self._on_regen_complete)
        except Exception as e:
            self.call_from_thread(self._on_regen_error, str(e))

    def _set_feed_loading(self: MediaAppProtocol) -> None:
        self._regen_in_progress = True
        self._feed_populated = False
        try:
            self.query_one(
                "#fs-center-switcher", ContentSwitcher
            ).current = "feed-loading"
            if (w := self._w_fs_left_list) is not None:
                w.focus()
            else:
                logger.debug(
                    "_set_feed_loading: _w_fs_left_list is None: skipping focus"
                )
        except Exception:
            logger.debug("_set_feed_loading: feed-loading switcher not found")

    def _on_regen_complete(self: MediaAppProtocol) -> None:
        self._regen_in_progress = False
        self._populate_center_feed()

    def _on_regen_error(self: MediaAppProtocol, msg: str) -> None:
        self._regen_in_progress = False
        try:
            self.query_one("#fs-center-header", Static).update(f"FEED: Error: {msg}")
            self.query_one("#fs-center-switcher", ContentSwitcher).current = "feed-list"
        except Exception:
            logger.debug("_on_regen_error: fs-center-switcher not found")

    @work(thread=True, exclusive=True, group="sc-cache")
    def _cache_worker(self: MediaAppProtocol) -> None:
        while True:
            try:
                artist_id = CACHE_QUEUE.popleft()
            except IndexError:
                break
            try:
                result = process_artist_cache(artist_id)
            except Exception:
                logger.exception(
                    "_cache_worker: process_artist_cache failed for %s", artist_id
                )
                self.call_from_thread(
                    self.notify,
                    f"Artist cache failed for {artist_id}",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
                time.sleep(2)
                continue
            failed = [k for k, v in result.items() if not v]
            if failed:
                self.call_from_thread(
                    self.notify,
                    f"Artist cache: {', '.join(failed)} failed for {artist_id}",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
            if result.get("profile"):
                self.call_from_thread(self._stop_following_spinner, artist_id)
            time.sleep(2)

    @work(thread=True, exclusive=True, group="stale-caches")
    def _check_stale_caches(self: MediaAppProtocol) -> None:
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            return
        followed = list(self._sc_followed)
        for artist in followed:
            artist_id = artist.get("id", "")
            if not artist_id:
                continue
            for category in ("profile", "uploads", "collections", "likes"):
                if needs_artist_refresh(artist_id, category):
                    enqueue_artist_cache(artist_id)
                    break
        if CACHE_QUEUE:
            self.call_from_thread(self._cache_worker)

    def _populate_center_feed(self: MediaAppProtocol) -> None:
        feed = load_feed()
        self._feed = feed
        if w := self._w_fs_center_header:
            w.update("FEED")
        else:
            logger.debug(
                "_populate_center_feed: _w_fs_center_header is None: skipping update"
            )
        listened = set()
        try:
            from nyrx.sources.soundcloud import get_listened_ids

            listened = get_listened_ids()
        except Exception:
            logger.debug("_populate_center_feed: get_listened_ids failed")
        center_lv = self._w_fs_center_list
        if center_lv is not None:
            center_lv.clear()
            followed_set = {a.get("id", "") for a in self._sc_followed}
            liked_set = {t.get("yt_id", "") for t in self._sc_liked}
            for t in feed:
                data = t.copy()
                data["consumed"] = data.get("yt_id", "") in listened
                center_lv.append(
                    FeedTrackItem(
                        data,
                        following=data.get("uploader_id", "") in followed_set,
                        liked=data.get("yt_id", "") in liked_set,
                    )
                )
        else:
            logger.debug(
                "_populate_center_feed: _w_fs_center_list is None: skipping populate"
            )
        self._feed_populated = True
        try:
            self.query_one("#fs-center-switcher", ContentSwitcher).current = "feed-list"
        except Exception:
            logger.debug("_populate_center_feed: feed-list switcher not found")

    def _refresh_feed_liked_indicators(self: MediaAppProtocol) -> None:
        liked_set = {t.get("yt_id", "") for t in self._sc_liked}
        if (w := self._w_fs_center_list) is not None:
            for item in w.query(FeedTrackItem):
                ytid = item.data.get("yt_id", "")
                item.set_liked(ytid in liked_set)
        else:
            logger.debug(
                "_refresh_feed_liked_indicators: _w_fs_center_list is None: skipping"
            )

    # ── Following panels + actions ─────────────────────────

    def _populate_following_panels(self: MediaAppProtocol) -> None:
        self._pending_unfollow_artist = None
        if self._fs_spinner_timer:
            self._fs_spinner_timer.stop()
            self._fs_spinner_timer = None
        self._loading_artists.clear()
        if (dt := self._w_fs_left_list) is not None:
            dt.clear()
            for a in self._sc_followed:
                artist_id = a.get("id", "")
                name = a.get("name", a.get("permalink", "?"))
                dt.add_row(Text(name), key=artist_id)
        else:
            logger.debug(
                "_populate_following_panels: _w_fs_left_list is None: skipping population"
            )
        if sc := self._w_sc_home:
            sc.set_following_artists(self._sc_followed)
        self._refresh_fs_spinners()
        self._populate_center_feed()

    def set_pending_unfollow(self: MediaAppProtocol, artist_id: str) -> None:
        """Mark a row as pending unfollow."""
        if (
            self._pending_unfollow_artist is not None
            and self._pending_unfollow_artist != artist_id
        ):
            self._update_following_row_style(self._pending_unfollow_artist, False)
        self._pending_unfollow_artist = artist_id
        self._update_following_row_style(artist_id, True)
        if dt := self._w_fs_left_list:
            dt.add_class("-pending-cursor")

    def clear_pending_unfollow(self: MediaAppProtocol) -> None:
        """Clear pending unfollow state and restore normal styling."""
        prev = self._pending_unfollow_artist
        self._pending_unfollow_artist = None
        if prev is not None:
            self._update_following_row_style(prev, False)
            if dt := self._w_fs_left_list:
                dt.remove_class("-pending-cursor")

    def _update_following_row_style(
        self: MediaAppProtocol, artist_id: str, is_pending: bool
    ) -> None:
        """Toggle a single row between normal and pending-unfollow styling."""
        dt = self._w_fs_left_list
        if dt is None:
            return
        artist = next(
            (a for a in self._sc_followed if a.get("id", "") == artist_id), None
        )
        if is_pending:
            dt.update_cell(
                artist_id, "name", Text("ctrl+d to confirm", style="bold #ff6666")
            )
        else:
            name = artist.get("name", artist.get("permalink", "?")) if artist else "?"
            dt.update_cell(artist_id, "name", name)

    def action_unfollow_artist(self: MediaAppProtocol) -> None:
        """Two-step unfollow: first press reddens row, second press unfollows."""
        if not self._in_following:
            return
        left_list = self._w_fs_left_list
        if left_list is None:
            return
        if left_list.cursor_coordinate is None or left_list.row_count == 0:
            return
        cell_key = left_list.coordinate_to_cell_key(left_list.cursor_coordinate)
        artist_id = cell_key.row_key.value
        if not artist_id:
            return

        if self._pending_unfollow_artist == artist_id:
            logger.debug("action_unfollow_artist: second press artist_id=%s", artist_id)
            self._pending_unfollow_artist = None
            artist = next(
                (a for a in self._sc_followed if a.get("id", "") == artist_id), None
            )
            name = artist.get("name", artist.get("permalink", "?")) if artist else "?"
            delete_artist_cache(artist_id)
            unfollow_sc(artist_id, self._sc_followed)
            self.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
            self._populate_following_panels()
        else:
            logger.debug("action_unfollow_artist: first press artist_id=%s", artist_id)
            self.set_pending_unfollow(artist_id)

    def action_regen_feed(self: MediaAppProtocol) -> None:
        if not self._in_following or self._in_artist_profile or self._regen_in_progress:
            return
        if (ll := self._w_fs_left_list) is not None and ll.has_focus:
            return
        self._set_feed_loading()
        self._regen_feed_worker()

    def action_queue_all_feed(self: MediaAppProtocol) -> None:
        """Queue all feed tracks and start playback (ctrl+a)."""
        if not self._in_following or not self._feed_populated or not self._feed:
            return
        if (ll := self._w_fs_left_list) is not None and ll.has_focus:
            return
        first = True
        count = 0
        for t in self._feed:
            ytid = t.get("yt_id", "")
            if not ytid:
                continue
            item = QueueItem(
                request=MediaRequest.from_dict(t, source="soundcloud", audio_only=True),
            )
            self._playback_queue.add(item)
            count += 1
            if first:
                first = False
                self._play(
                    MediaRequest.from_dict(t, source="soundcloud", audio_only=True)
                )
                self._playback_queue.remove_by_id(ytid)
        self._sync_np_widget()
        self._refresh_queue_modal()
        if count:
            self.notify(
                f"Queued {count} track{'s' if count != 1 else ''} from feed.",
                timeout=TIMEOUT_CONFIRM,
            )

    # ── Station + collections ──────────────────────────────

    def action_station(self: MediaAppProtocol) -> None:
        """Start a station from the currently focused SoundCloud track (s key)."""
        if self._np_focused and not self._sc_np_focused:
            return
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            if getattr(self, "_sc_api_blocked", False):
                return
            self._sc_api_blocked = True
            self.notify(
                "Soundcloud station disabled: API key not available",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            self.set_timer(3.5, lambda: setattr(self, "_sc_api_blocked", False))
            return
        if self._station_in_progress:
            self.notify("Station already loading...", timeout=TIMEOUT_CONFIRM)
            return
        track = self._get_focused_track()
        if not track or track.get("source") != "soundcloud":
            self.notify(
                "No SoundCloud track focused.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        ytid = track.get("yt_id", "") or track.get("track_id", "")
        if not ytid:
            self.notify(
                "Track ID not available.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        self._station_in_progress = True
        self.notify("Fetching station...", timeout=TIMEOUT_INFO)
        self._station_worker(ytid, track.get("title", "?"))

    @work(thread=True, group="sc-station")
    def _station_worker(self: MediaAppProtocol, track_id: str, title: str) -> None:
        try:
            from nyrx.sources.soundcloud import get_station_tracks

            results = get_station_tracks(track_id)
            self.call_from_thread(self._on_station_result, results, title)
        except Exception:
            logger.warning("_station_worker: failed for track %s", track_id)
            self.call_from_thread(self._on_station_result, None, title)

    def _on_station_result(
        self: MediaAppProtocol, results: list[dict] | None, title: str
    ) -> None:
        self._station_in_progress = False
        if not results:
            self.notify(
                f"No station tracks found for: {title}",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
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
            f"Queued {len(results)} station tracks from: {title}",
            timeout=TIMEOUT_CONFIRM,
        )

    def action_browse_collections(
        self: MediaAppProtocol, collection_id: str = ""
    ) -> None:
        if not self._in_artist_profile:
            return
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            if getattr(self, "_sc_api_blocked", False):
                return
            self._sc_api_blocked = True
            self.notify(
                "Client id unavailable: playlists disabled",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            self.set_timer(3.5, lambda: setattr(self, "_sc_api_blocked", False))
            return
        ap = self._w_artist_profile
        if ap is None:
            logger.debug("action_browse_collections: _w_artist_profile is None")
            return
        cols = ap._collections
        if not cols:
            self.notify(
                "No collections available for this artist.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        nm = ap._profile.get("name", "Artist") if ap._profile else "Artist"
        liked_ids = {t.get("yt_id", "") for t in self._sc_liked}
        followed_set = {a.get("id", "") for a in self._sc_followed}
        cb = CollectionBrowser(cols, nm, collection_id, liked_ids, followed_set)
        _t0 = time.perf_counter()
        self.push_screen(cb)
        logger.debug(
            "CollectionBrowser: push+mount=%.1fms", (time.perf_counter() - _t0) * 1000
        )

    # ── Follow ─────────────────────────────────────────────

    def action_follow(self: MediaAppProtocol) -> None:
        """Toggle follow/unfollow on the focused SoundCloud artist."""
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            if getattr(self, "_sc_api_blocked", False):
                return
            self._sc_api_blocked = True
            self.notify(
                "Client id unavailable: follow disabled",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            self.set_timer(3.5, lambda: setattr(self, "_sc_api_blocked", False))
            return
        if self._np_focused:
            if (
                self._sc_np_focused
                and self._now_playing_data
                and self._now_playing_data.get("source") == "soundcloud"
            ):
                np_data = self._now_playing_data
                uploader_id = np_data.get("uploader_id", "")
                if not uploader_id:
                    self.notify(
                        "Artist info not available for this track.",
                        severity=SEVERITY_WARNING,
                        timeout=TIMEOUT_WARNING,
                        title="Warning",
                    )
                    return
                name = np_data.get("channel", "")
                permalink = np_data.get("permalink", "")
                if not permalink:
                    url = np_data.get("uploader_url", np_data.get("url", ""))
                    import re

                    m = re.search(r"soundcloud\.com/([^/]+)", url)
                    if m:
                        permalink = m.group(1)
                url = f"https://soundcloud.com/{permalink}" if permalink else ""
                if is_sc_followed(uploader_id, self._sc_followed):
                    delete_artist_cache(uploader_id)
                    unfollow_sc(uploader_id, self._sc_followed)
                    self.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
                else:
                    follow_sc(uploader_id, permalink, name, url, self._sc_followed)
                    enqueue_artist_cache(uploader_id)
                    self._cache_worker()
                    self.notify(f"Following: {name}", timeout=TIMEOUT_CONFIRM)
                self._populate_following_panels()
                self._sync_sc_np_metadata()
            return
        if self._in_artist_profile:
            ap = self._w_artist_profile
            if ap is None:
                logger.debug("action_follow: _w_artist_profile is None")
                return
            dt = ap.query_one("#ap-track-list", DataTable)
            if (
                self.focused is dt
                and dt.cursor_coordinate is not None
                and dt.row_count > 0
            ):
                cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
                track = ap._track_data_map.get(require_key(cell_key.row_key.value))
                if track:
                    uploader_id = track.get("uploader_id", "")
                    name = track.get("channel", "")
                    permalink = track.get("permalink", "")
                    url = f"https://soundcloud.com/{permalink}" if permalink else ""
                    if not uploader_id:
                        self.notify(
                            "Artist info not available.",
                            severity=SEVERITY_WARNING,
                            timeout=TIMEOUT_WARNING,
                            title="Warning",
                        )
                        return
                    if is_sc_followed(uploader_id, self._sc_followed):
                        delete_artist_cache(uploader_id)
                        unfollow_sc(uploader_id, self._sc_followed)
                        self.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
                    else:
                        follow_sc(uploader_id, permalink, name, url, self._sc_followed)
                        enqueue_artist_cache(uploader_id)
                        self._cache_worker()
                        self.notify(f"Following: {name}", timeout=TIMEOUT_CONFIRM)
                    self._populate_following_panels()
                    if sc := self._w_sc_home:
                        sc._populate_following(self._sc_followed)
                    else:
                        logger.debug(
                            "action_follow: _w_sc_home is None: skipping _populate_following"
                        )
                    ap._followed_set = {a.get("id", "") for a in self._sc_followed}
                    artist_color = (
                        "#A277FF" if uploader_id in ap._followed_set else "#606060"
                    )
                    for key, t in ap._track_data_map.items():
                        if t.get("uploader_id", "") == uploader_id:
                            dt.update_cell(
                                key,
                                "artist",
                                Text(t.get("channel", "?"), style=artist_color),
                            )
                self._sync_sc_np_metadata()
            return
        if self._in_liked:
            liked = self._w_liked_screen
            if liked is None:
                logger.debug("action_follow: _w_liked_screen is None")
                return
            track = liked.focused_track()
            if track and track.get("source") == "soundcloud":
                uploader_id = track.get("uploader_id", "")
                name = track.get("channel", "")
                permalink = track.get("permalink", "")
                url = f"https://soundcloud.com/{permalink}" if permalink else ""
                if not uploader_id:
                    self.notify(
                        "Artist info not available.",
                        severity=SEVERITY_WARNING,
                        timeout=TIMEOUT_WARNING,
                        title="Warning",
                    )
                    return
                if is_sc_followed(uploader_id, self._sc_followed):
                    delete_artist_cache(uploader_id)
                    unfollow_sc(uploader_id, self._sc_followed)
                    self.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
                else:
                    follow_sc(uploader_id, permalink, name, url, self._sc_followed)
                    enqueue_artist_cache(uploader_id)
                    self._cache_worker()
                    self.notify(f"Following: {name}", timeout=TIMEOUT_CONFIRM)
                self._populate_following_panels()
                liked._followed_set = {a.get("id", "") for a in self._sc_followed}
                liked._rebuild_table(liked._filtered)
                self._sync_sc_np_metadata()
            return
        if self._in_following:
            left_list = self._w_fs_left_list
            center_list = self._w_fs_center_list
            if left_list is not None and left_list.has_focus:
                return
            elif center_list is not None and center_list.has_focus:
                track_item = center_list.highlighted_child
                if isinstance(track_item, FeedTrackItem):
                    track_data = track_item.data
                    uploader_id = track_data.get("uploader_id", "")
                    name = track_data.get("channel", "")
                    permalink = track_data.get("permalink", "")
                    url = f"https://soundcloud.com/{permalink}" if permalink else ""
                    if uploader_id and is_sc_followed(uploader_id, self._sc_followed):
                        delete_artist_cache(uploader_id)
                        unfollow_sc(uploader_id, self._sc_followed)
                        self.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
                        if isinstance(track_item, FeedTrackItem):
                            track_item.set_following(False)
                    else:
                        follow_sc(uploader_id, permalink, name, url, self._sc_followed)
                        enqueue_artist_cache(uploader_id)
                        self._cache_worker()
                        self.notify(f"Following: {name}", timeout=TIMEOUT_CONFIRM)
                        if isinstance(track_item, FeedTrackItem):
                            track_item.set_following(True)
                    self._populate_following_panels()
                self._sync_sc_np_metadata()
            return
        data: dict | None = None
        item = self._current_item()
        if item and item.data.get("source") == "soundcloud":
            data = item.data
        if not data:
            self.notify(
                "No SoundCloud artist focused to follow.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        uploader_id = data.get("uploader_id", "")
        if not uploader_id:
            self.notify(
                "Artist info not available for this track.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        name = data.get("channel", "")
        permalink = data.get("permalink", "")
        if not permalink:
            url = data.get("uploader_url", data.get("url", ""))
            import re

            m = re.search(r"soundcloud\.com/([^/]+)", url)
            if m:
                permalink = m.group(1)
        url = f"https://soundcloud.com/{permalink}" if permalink else ""
        if is_sc_followed(uploader_id, self._sc_followed):
            delete_artist_cache(uploader_id)
            unfollow_sc(uploader_id, self._sc_followed)
            self.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
            if isinstance(item, ResultItem):
                item.set_following(False)
            self._populate_following_panels()
        else:
            follow_sc(uploader_id, permalink, name, url, self._sc_followed)
            enqueue_artist_cache(uploader_id)
            self._cache_worker()
            self.notify(f"Following: {name}", timeout=TIMEOUT_CONFIRM)
            if isinstance(item, ResultItem):
                item.set_following(True)
            self._populate_following_panels()
        self._sync_sc_np_metadata()

    # ── Likes ──────────────────────────────────────────────

    def action_like_toggle(self: MediaAppProtocol) -> None:
        """Toggle like on the focused SoundCloud track."""
        if self._np_focused:
            if (
                self._sc_np_focused
                and self._now_playing_data
                and self._now_playing_data.get("source") == "soundcloud"
            ):
                ytid = self._now_playing_data["yt_id"]
                toggle_sc_like(ytid, self._now_playing_data, self._sc_liked)
                self._sync_sc_np_metadata()
            return
        if self._in_artist_profile:
            ap = self._w_artist_profile
            if ap is None:
                logger.debug("action_like_toggle: _w_artist_profile is None")
                return
            dt = ap.query_one("#ap-track-list", DataTable)
            if self.focused is dt and dt.cursor_coordinate is not None:
                cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
                track = ap._track_data_map.get(require_key(cell_key.row_key.value))
                if track and track.get("source") == "soundcloud":
                    ytid = track.get("yt_id") or track.get("track_id") or ""
                    toggle_sc_like(ytid, track, self._sc_liked)
                    ap._liked_ids = {t.get("yt_id", "") for t in self._sc_liked}
                    likes_raw = track.get("like_count", 0) or track.get(
                        "likes_count", 0
                    )
                    likes_str = _short_views(likes_raw)
                    heart_color = "#A277FF" if ytid in ap._liked_ids else "#404040"
                    dt.update_cell(
                        cell_key.row_key,
                        "likes",
                        Text(f"\u2764\ufe0e {likes_str}", style=heart_color)
                        if likes_str
                        else Text("\u2014", style=heart_color),
                    )
                self._sync_sc_np_metadata()
            return
        if self._in_liked:
            ls = self._w_liked_screen
            if ls is None:
                logger.debug("action_like_toggle: _w_liked_screen is None")
                return
            track = ls.focused_track()
            if track:
                ytid = track.get("yt_id") or track.get("track_id") or ""
                if ytid in self._unlike_buffer:
                    del self._unlike_buffer[ytid]
                    ls._buffer_ids = set(self._unlike_buffer.keys())
                    likes_str = (
                        _short_views(track.get("likes_count", 0))
                        if track.get("likes_count")
                        else "\u2014"
                    )
                    ls.query_one("#ls-list", DataTable).update_cell(
                        ytid,
                        "likes",
                        Text(f"\u2764\ufe0e {likes_str}", style="#A277FF"),
                    )
                else:
                    self._unlike_buffer[ytid] = track
                    ls._buffer_ids = set(self._unlike_buffer.keys())
                    likes_str = (
                        _short_views(track.get("likes_count", 0))
                        if track.get("likes_count")
                        else "\u2014"
                    )
                    ls.query_one("#ls-list", DataTable).update_cell(
                        ytid,
                        "likes",
                        Text(f"\u2764\ufe0e {likes_str}", style="#404040"),
                    )
                dt = ls.query_one("#ls-list", DataTable)
                if ytid in self._unlike_buffer:
                    dt.add_class("-buffer-cursor")
                else:
                    dt.remove_class("-buffer-cursor")
                real_count = max(0, len(self._sc_liked) - len(self._unlike_buffer))
                ls.query_one("#ls-section-header", Static).update(
                    f"LIKED TRACKS  [dim]({real_count})[/dim]"
                )
            return
        if self._in_following:
            center_list = self._w_fs_center_list
            if center_list is not None and center_list.has_focus:
                item = center_list.highlighted_child
                if (
                    isinstance(item, FeedTrackItem)
                    and item.data.get("source") == "soundcloud"
                ):
                    ytid = item.data["yt_id"]
                    liked = is_sc_liked(ytid, self._sc_liked)
                    toggle_sc_like(ytid, item.data, self._sc_liked)
                    item.set_liked(not liked)
                self._sync_sc_np_metadata()
            return
        item = self._current_item()
        if item and item.data.get("source") == "soundcloud":
            ytid = item.data["yt_id"]
            liked = is_sc_liked(ytid, self._sc_liked)
            toggle_sc_like(ytid, item.data, self._sc_liked)
            if isinstance(item, ResultItem):
                item.set_liked(not liked)
            self._sync_sc_np_metadata()
            return

    def _sync_sc_np_metadata(self: MediaAppProtocol) -> None:
        """Refresh the SC np-widget's liked/followed state from the DB mirrors."""
        np_side = self._np_widgets.get("soundcloud")
        if np_side is None:
            return
        np_side = cast(SoundCloudNowPlaying, np_side)
        data = self._now_playing_data
        if not data or data.get("source") != "soundcloud":
            return
        ytid = data.get("yt_id", "")
        if not ytid:
            return
        uploader_id = data.get("uploader_id", "")
        if not uploader_id:
            uploader_id = next(
                (
                    t.get("uploader_id", "")
                    for t in self._sc_liked
                    if t.get("yt_id") == ytid
                ),
                "",
            )
        np_side.update_metadata(
            liked=is_sc_liked(ytid, self._sc_liked),
            followed=bool(uploader_id)
            and is_sc_followed(uploader_id, self._sc_followed),
            like_count=np_side._like_count,
            play_count=np_side._play_count,
        )

    def action_sync_liked(self: MediaAppProtocol) -> None:
        """Open URL input modal to sync liked tracks from a SoundCloud profile."""
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            if getattr(self, "_sc_api_blocked", False):
                return
            self._sc_api_blocked = True
            self.notify(
                "Soundcloud sync disabled: API key not available",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            self.set_timer(3.5, lambda: setattr(self, "_sc_api_blocked", False))
            return
        if not self._online:
            self.notify(
                "No internet connection. Sync unavailable.",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            return
        self.push_screen(URLInputModal(), self._on_sync_url)

    def _on_sync_url(self: MediaAppProtocol, result: str | None) -> None:
        if not result:
            return
        self._sync_liked_worker(result)

    @work(thread=True, exclusive=True, group="sc-sync-liked")
    def _sync_liked_worker(self: MediaAppProtocol, profile_url: str) -> None:
        if liked := self._w_liked_screen:
            self.call_from_thread(liked.set_loading)
        else:
            logger.debug("_sync_liked_worker: _w_liked_screen is None")
        try:
            merged, count = sync_liked_from_profile(profile_url, self._sc_liked)
        except ProfileResolveError:
            self.call_from_thread(
                self._finish_sync_resolve_error,
                profile_url,
            )
            return
        self.call_from_thread(self._finish_sync, merged, count)

    def _finish_sync_resolve_error(self: MediaAppProtocol, profile_url: str) -> None:
        self.notify(
            "Failed to resolve Soundcloud profile URL",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )

    def _finish_sync(self: MediaAppProtocol, merged: list[dict], count: int) -> None:
        self._sc_liked = merged
        if self._in_liked:
            if liked := self._w_liked_screen:
                liked._followed_set = {a.get("id", "") for a in self._sc_followed}
                liked.update_tracks(self._sc_liked)
            else:
                logger.debug("_finish_sync: _w_liked_screen is None")
        if sc := self._w_sc_home:
            sc._populate_liked(self._sc_liked)
        else:
            logger.debug("_finish_sync: _w_sc_home is None, skipping _populate_liked")
        if count > 0:
            self.notify(
                f"Synced {count} new liked track{'s' if count != 1 else ''} from profile",
                timeout=TIMEOUT_INFO,
            )
        else:
            self.notify("No new liked tracks found", timeout=TIMEOUT_INFO)
        self._sync_sc_np_metadata()

    def action_show_liked(self: MediaAppProtocol) -> None:
        """Toggle liked screen (ctrl+l)."""
        if self._source == Source.RADIO:
            return
        sc = self._w_sc_home
        if sc is None:
            logger.debug("action_show_liked: _w_sc_home is None")
            return
        if not sc.display:
            return
        if self._in_artist_profile or self._in_following:
            return
        if self._in_liked:
            self._hide_liked()
        else:
            self._show_liked()

    def _purge_unlike_buffer(self: MediaAppProtocol) -> None:
        """Actually unlike all buffered tracks (called on liked screen exit)."""
        for ytid, track in self._unlike_buffer.items():
            toggle_sc_like(ytid, track, self._sc_liked)
        self._unlike_buffer.clear()
        self._sync_sc_np_metadata()

    def _show_liked(self: MediaAppProtocol) -> None:
        """Show the liked-tracks screen with buffered unlike support."""
        self._save_sc_home_focus()
        self._in_liked = True
        self._in_artist_profile = False
        self._in_following = False
        if fa := self._w_following_area:
            fa.display = False
        else:
            logger.debug("_show_liked: _w_following_area is None, skipping display")
        if sc := self._w_sc_home:
            sc.query_one("#sch-center").display = False
        else:
            logger.debug("_show_liked: _w_sc_home is None, skipping sch-center hide")
        if liked := self._w_liked_screen:
            liked.display = True
            liked._followed_set = {a.get("id", "") for a in self._sc_followed}
            liked.populate(self._sc_liked)
        else:
            logger.debug(
                "_show_liked: _w_liked_screen is None: skipping liked screen setup"
            )
        if mc := self._w_main_content:
            mc.add_class("following-mode")
        else:
            logger.debug("_show_liked: _w_main_content is None, skipping add_class")
        self._apply_sidebar(self.screen.has_class("wide"))
        self._update_sidebar_context()
        self._render_focus_indicators()

    def _hide_liked(self: MediaAppProtocol) -> None:
        """Hide the liked screen and purge any buffered unlikes."""
        self._in_liked = False
        self._purge_unlike_buffer()
        if liked := self._w_liked_screen:
            liked.display = False
            liked._buffer_ids.clear()
        else:
            logger.debug("_hide_liked: _w_liked_screen is None, skipping hide")
        if sc := self._w_sc_home:
            sc.query_one("#sch-center").display = True
        else:
            logger.debug("_hide_liked: _w_sc_home is None, skipping sch-center show")
        if liked := self._w_liked_screen:
            liked.query_one("#ls-search", Input).value = ""
        else:
            logger.debug("_hide_liked: _w_liked_screen is None, skipping search clear")
        if mc := self._w_main_content:
            mc.remove_class("following-mode")
        else:
            logger.debug("_hide_liked: _w_main_content is None, skipping remove_class")
        if sc := self._w_sc_home:
            sc._populate_liked(self._sc_liked)
        else:
            logger.debug("_hide_liked: _w_sc_home is None, skipping _populate_liked")
        self._update_sidebar_content()
        self._apply_sidebar(self.screen.has_class("wide"))
        self._update_sidebar_context()
        self._restore_sc_home_focus()
        self._render_focus_indicators()
        try:
            if liked := self._w_liked_screen:
                liked.query_one("#ls-keybind-bar", Static).update("")
            else:
                logger.debug(
                    "_hide_liked: _w_liked_screen is None: skipping keybind-bar"
                )
        except Exception:
            logger.debug("_on_screen_focus_changed: ls-keybind-bar not found")
