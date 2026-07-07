# SPDX-License-Identifier: AGPL-3.0-only

"""Two-panel modal: left = artist collections, right = tracks in selected collection."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static

from nyrx.config import SEVERITY_WARNING, TIMEOUT_CONFIRM, TIMEOUT_WARNING
from nyrx.models import MediaRequest
from nyrx.player import format_duration as fmt_duration
from nyrx.queues import QueueItem
from nyrx.screens.base_modal import BaseModal
from nyrx.widgets import BrailleSpinner, _short_views

from .thumbnail import ThumbnailModal

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


class CollectionBrowser(BaseModal[None]):
    """Two-panel modal: left = artist collections, right = tracks in selected collection."""

    BINDINGS = [
        ("tab", "cycle_panel", "Next Panel"),
        ("z", "show_thumbnail", "Thumbnail"),
        ("d", "download_track", "Download"),
        ("s", "station_track", "Station"),
        ("l", "like_toggle", "Like"),
        ("f", "follow_toggle", "Follow"),
        ("ctrl+a", "queue_all", "Queue All"),
    ]

    _COL_WIDTH = 45

    def __init__(
        self,
        collections: list[dict],
        artist_name: str,
        initial_collection_id: str = "",
        liked_ids: set[str] | None = None,
        followed_set: set[str] | None = None,
    ) -> None:
        super().__init__()
        self._collections = collections
        self._artist_name = artist_name
        self._initial_cid = initial_collection_id
        self._liked_ids = liked_ids or set()
        self._followed_set = followed_set or set()
        self._track_data_map: dict[str, dict] = {}
        self._collection_ids: dict[str, str] = {}
        self._loaded_cid: str = ""

    def compose(self) -> ComposeResult:
        with Container(id="cb-box"):
            with Horizontal(id="cb-panels"):
                with Vertical(id="cb-left-panel"):
                    yield Label("[#edecee]COLLECTIONS[/]", id="cb-header")
                    yield DataTable(
                        id="cb-collection-list", cursor_type="row", show_header=False
                    )
                with Vertical(id="cb-right-panel"):
                    yield Label("[#707070] [/]", id="cb-collection-name")
                    with Vertical(id="cb-loading"):
                        yield BrailleSpinner(id="cb-spinner")
                        yield Label("[dim]loading tracks[/]", id="cb-loading-label")
                    yield DataTable(
                        id="cb-track-list", cursor_type="row", show_header=False
                    )
            yield Static(id="cb-footer")

    def _truncate_label(self, label: str, count: int) -> str:
        count_str = f"({count})"
        max_label_len = self._COL_WIDTH - len(count_str) - 2
        if len(label) <= max_label_len:
            return label
        truncated = label[: max_label_len - 1]
        last_space = truncated.rfind(" ")
        if last_space > 0:
            truncated = truncated[:last_space]
        return truncated

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#cb-loading").display = False
        self.query_one("#cb-track-list").display = False
        dt = self.query_one("#cb-collection-list", DataTable)
        dt.add_column("collections", key="collections", width=45)
        dt.cell_padding = 0
        target_row = -1
        logger.debug(
            "CollectionBrowser.on_mount: artist=%s collections=%s initial_cid=%s",
            self._artist_name,
            len(self._collections),
            self._initial_cid,
        )
        for i, c in enumerate(self._collections):
            label = c.get("title", "?")
            count = c.get("track_count", 0)
            row = Text(f" {self._truncate_label(label, count)}")
            row.append(" ")
            row.append(f"({count})", style="#404040")
            key = c["collection_id"]
            dt.add_row(row, key=key)
            self._collection_ids[key] = c["collection_id"]
            if c["collection_id"] == self._initial_cid:
                target_row = i
        if dt.row_count > 0:
            if target_row >= 0:
                dt.move_cursor(row=target_row)
                self._load_tracks(self._initial_cid)
            else:
                dt.move_cursor(row=0)
            dt.focus()
        self.query_one("#cb-footer", Static).update(
            "[white]ctrl+a[/white] [dim]queue all[/dim]  \u2022  [white]tab[/white] [dim]panel[/dim]  \u2022  [white]l[/white] [dim]like[/dim]  \u2022  [white]s[/white] [dim]station[/dim]  \u2022  [white]esc[/white] [dim]close[/dim]"
        )

    def on_key(self, event: events.Key) -> None:
        left = self.query_one("#cb-collection-list", DataTable)
        if left.has_focus and event.key not in (
            "up",
            "down",
            "enter",
            "tab",
            "escape",
            "pageup",
            "pagedown",
            "home",
            "end",
            "ctrl+home",
            "ctrl+end",
        ):
            event.stop()
            event.prevent_default()

    def watch_focused(self, widget: Widget | None) -> None:
        left = self.query_one("#cb-collection-list", DataTable)
        right = self.query_one("#cb-track-list", DataTable)
        left.show_cursor = widget is left
        right.show_cursor = widget is right and right.row_count > 0

    def _focused_track(self) -> dict | None:
        dt = self.query_one("#cb-track-list", DataTable)
        if dt.cursor_coordinate is not None and dt.row_count > 0:
            cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
            if cell_key and cell_key.row_key.value:
                return self._track_data_map.get(cell_key.row_key.value)
        return None

    def _build_track_cell(self, t: dict) -> Text:
        title = t.get("title", "?")
        channel = t.get("channel", "?")
        duration = fmt_duration(t.get("duration", 0))
        views = _short_views(t.get("view_count", 0))
        likes = _short_views(t.get("like_count", 0))
        stats_color = "#505050"
        heart_color = "#404040"
        artist_color = "#606060"
        line1 = Text(f" {title}", style="#edecee")
        line2 = Text(f" {channel}", style=artist_color)
        if views:
            line2.append(f"  \u25b6 {views}", style=stats_color)
        if likes:
            line2.append(f"  \u2764\ufe0e {likes}", style=heart_color)
        line2.append(f"  {duration}", style=stats_color)
        return Text.assemble(line1, "\n", line2, "\n")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        app = cast("MediaAppProtocol", self.app)
        if event.data_table.id == "cb-collection-list":
            event.stop()
            cid = (
                self._collection_ids.get(event.row_key.value, "")
                if event.row_key.value
                else ""
            )
            logger.debug("on_data_table_row_selected: collection cid=%s", cid)
            if cid:
                self._load_tracks(cid)
        elif event.data_table.id == "cb-track-list":
            event.stop()
            track = (
                self._track_data_map.get(event.row_key.value)
                if event.row_key.value
                else None
            )
            if track:
                logger.debug(
                    "on_data_table_row_selected: track title=%s",
                    track.get("title", "")[:40],
                )
                track.setdefault("yt_id", track.get("track_id", ""))
                app._play(MediaRequest.from_dict(track))

    def _load_tracks(self, cid: str) -> None:
        if cid == self._loaded_cid:
            logger.debug("_load_tracks: already_loaded cid=%s", cid)
            return
        self._loaded_cid = cid
        self._track_data_map.clear()
        collection = next(
            (c for c in self._collections if c["collection_id"] == cid), None
        )
        title = collection.get("title", "") if collection else ""
        logger.debug("_load_tracks: cid=%s title=%s", cid, title[:40])
        self.query_one("#cb-collection-name", Label).update(title)
        self.query_one("#cb-loading").display = True
        self.query_one("#cb-track-list").display = False
        self._load_tracks_worker(cid)

    @work(thread=True)
    def _load_tracks_worker(self, cid: str) -> None:
        from nyrx.sources.soundcloud import fetch_playlist_tracks

        tracks = fetch_playlist_tracks(cid)
        self.app.call_from_thread(self._on_tracks_loaded, tracks)

    def _on_tracks_loaded(self, tracks: list[dict] | None) -> None:
        self.query_one("#cb-loading").display = False
        logger.debug(
            "_on_tracks_loaded: cid=%s count=%s",
            self._loaded_cid,
            len(tracks) if tracks else 0,
        )
        if not tracks:
            self.query_one("#cb-track-list").display = False
            return
        dt = self.query_one("#cb-track-list", DataTable)
        dt.display = True
        dt.clear(columns=True)
        dt.add_column("track", key="track", width=84)
        dt.cell_padding = 0

        for idx, t in enumerate(tracks):
            t.setdefault("yt_id", t.get("track_id", ""))
            tid = t.get("yt_id", "")
            explicit_key = str(tid) if tid else f"row_{idx}"
            cell = self._build_track_cell(t)
            dt.add_row(cell, key=explicit_key, height=None)
            self._track_data_map[explicit_key] = t

        if dt.row_count > 0:
            dt.move_cursor(row=0)
            dt.focus()

    def _queue_all_tracks(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        dt = self.query_one("#cb-track-list", DataTable)
        if dt.row_count == 0:
            return
        tracks = list(self._track_data_map.values())
        if not tracks:
            logger.debug("_queue_all_tracks: no_tracks_loaded")
            app.notify(
                "No tracks loaded.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        logger.debug("_queue_all_tracks: total=%s", len(tracks))
        first = True
        count = 0
        for t in tracks:
            t.setdefault("yt_id", t.get("track_id", ""))
            if not t.get("yt_id"):
                continue
            request = MediaRequest.from_dict(t, source="soundcloud", audio_only=True)
            item = QueueItem(request=request)
            app._playback_queue.add(item)
            count += 1
            if first:
                first = False
                app._play(request)
                app._playback_queue.remove_by_id(request.yt_id)
        app._sync_np_widget()
        app._refresh_queue_modal()
        app.notify(
            f"Queued {count} track{'s' if count != 1 else ''}.", timeout=TIMEOUT_CONFIRM
        )

    def action_cycle_panel(self) -> None:
        left = self.query_one("#cb-collection-list", DataTable)
        right = self.query_one("#cb-track-list", DataTable)
        if left.has_focus:
            if right.display and right.row_count > 0:
                right.focus()
        else:
            left.focus()

    def action_download_track(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        track = self._focused_track()
        if not track:
            return
        track.setdefault("yt_id", track.get("track_id", ""))
        track.setdefault("source", "soundcloud")
        app.action_download(data=dict(track))

    def action_station_track(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        track = self._focused_track()
        if not track:
            return
        track.setdefault("yt_id", track.get("track_id", ""))
        ytid = track.get("yt_id", "")
        if not ytid:
            logger.debug("action_station_track: no_ytid")
            app.notify(
                "Track ID not available.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        logger.debug(
            "action_station_track: yt_id=%s title=%s", ytid, track.get("title", "")[:30]
        )
        app._station_worker(ytid, track.get("title", "?"))

    def action_show_thumbnail(self) -> None:
        track = self._focused_track()
        if not track:
            return
        data = dict(track)
        data.setdefault("yt_id", data.get("track_id", ""))
        data.setdefault("views", data.get("view_count", 0))
        data.setdefault("likes_count", data.get("like_count", 0))
        logger.debug("action_show_thumbnail: yt_id=%s", data.get("yt_id", ""))
        app = cast("MediaAppProtocol", self.app)
        _t0 = time.perf_counter()
        app.push_screen(ThumbnailModal(data), app._on_thumb_result)
        logger.debug(
            "ThumbnailModal: push+mount=%.1fms (from collection_browser)",
            (time.perf_counter() - _t0) * 1000,
        )

    def action_like_toggle(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        track = self._focused_track()
        if not track or track.get("source") != "soundcloud":
            return
        track.setdefault("yt_id", track.get("track_id", ""))
        ytid = track.get("yt_id", "")
        if not ytid:
            return
        logger.debug("action_like_toggle: yt_id=%s", ytid)
        from nyrx.sources.soundcloud import toggle_sc_like

        toggle_sc_like(ytid, track, app._sc_liked)
        app._sync_sc_np_metadata()

    def action_follow_toggle(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            app.notify(
                "Soundcloud login required to follow artists",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        track = self._focused_track()
        if not track or track.get("source") != "soundcloud":
            return
        uploader_id = track.get("uploader_id", "")
        if not uploader_id:
            logger.debug("action_follow_toggle: no_uploader_id")
            app.notify(
                "Artist info not available.",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        name = track.get("channel", "")
        permalink = track.get("permalink", "")
        url = f"https://soundcloud.com/{permalink}" if permalink else ""
        from nyrx.sources.soundcloud import (
            enqueue_artist_cache,
            follow_sc,
            is_sc_followed,
            unfollow_sc,
        )

        if is_sc_followed(uploader_id, app._sc_followed):
            logger.debug(
                "action_follow_toggle: unfollow uploader_id=%s name=%s",
                uploader_id,
                name,
            )
            unfollow_sc(uploader_id, app._sc_followed)
            app.notify(f"Unfollowed: {name}", timeout=TIMEOUT_CONFIRM)
        else:
            logger.debug(
                "action_follow_toggle: follow uploader_id=%s name=%s", uploader_id, name
            )
            follow_sc(uploader_id, permalink, name, url, app._sc_followed)
            enqueue_artist_cache(uploader_id)
            app._cache_worker()
            app.notify(f"Following: {name}", timeout=TIMEOUT_CONFIRM)
        app._populate_following_panels()
        app._sync_sc_np_metadata()

    def action_queue_all(self) -> None:
        self._queue_all_tracks()
