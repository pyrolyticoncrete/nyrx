# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

import requests
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Static
from textual_image.widget import Image as ThumbImage

from nyrx.config import COMPACT_MAX_HEIGHT, COMPACT_MIN_WIDTH
from nyrx.player import format_duration as fmt_duration

from .base import _short_views

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)


class ApChipButton(Button):
    """Button that carries a collection ID for artist profile chips."""

    _cid: str = ""


class ArtistProfileView(Vertical):
    """Artist profile screen opened from following area.

    Shows avatar, name, location, description, stats, album/playlist chips,
    and a DataTable of uploads/liked tracks switchable via left/right.
    """

    can_focus_children = True

    SEP = "\u2500" * 84
    _MAX_CHIPS = 10

    def __init__(self, artist_id: str = "", **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._artist_id = artist_id
        self._artist_data: dict = {}
        self._profile: dict | None = None
        self._collections: list[dict] = []
        self._uploads: list[dict] = []
        self._likes: list[dict] = []
        self._active_tab = "uploads"
        self._track_data_map: dict[str, dict] = {}
        self._liked_ids: set[str] = set()

    def _apply_compact(self) -> None:
        w, h = self.screen.size
        compact = w >= COMPACT_MIN_WIDTH and h < COMPACT_MAX_HEIGHT
        self.set_class(compact, "compact")

    def compose(self) -> ComposeResult:
        with Vertical(id="ap-scroll"):
            yield Static("", id="ap-browsing")
            yield Static("", id="ap-browsing-sep", classes="ap-sep")
            with Horizontal(id="ap-header"):
                yield ThumbImage(id="ap-avatar")
                with Vertical(id="ap-info"):
                    yield Horizontal(
                        Static("NAME", classes="ap-field-label"),
                        Static("", id="ap-name"),
                        classes="ap-field-row",
                    )
                    yield Horizontal(
                        Static("LOCATION", classes="ap-field-label"),
                        Static("", id="ap-location"),
                        classes="ap-field-row",
                    )
                    yield Horizontal(
                        Static("INFO", classes="ap-field-label"),
                        Static("", id="ap-meta"),
                        classes="ap-field-row",
                    )
                    yield Static("", id="ap-desc")
            yield Static(self.SEP, id="ap-sep", classes="ap-sep")
            yield Static("ALBUMS", id="ap-album-label", classes="ap-section-label")
            yield Horizontal(id="ap-album-chips", classes="ap-chip-row")
            yield Static(
                "PLAYLISTS", id="ap-playlist-label", classes="ap-section-label"
            )
            yield Horizontal(id="ap-playlist-chips", classes="ap-chip-row")
            yield Static(self.SEP, id="ap-sep-chips", classes="ap-sep")
            with Vertical(id="ap-tab-section"):
                yield Static("", id="ap-tab-labels")
                yield Static("", id="ap-underline-row")
            yield DataTable(
                id="ap-track-list",
                show_header=False,
                cursor_type="row",
            )

    def populate(
        self,
        artist_id: str,
        liked_ids: set[str] | None = None,
        followed_set: set[str] | None = None,
    ) -> None:
        """Load cached data for artist_id and fill all sections."""
        self._artist_id = artist_id
        self._liked_ids = liked_ids or set()
        self._followed_set = followed_set or set()
        from nyrx.sources.soundcloud import (
            get_cached_artist_collections,
            get_cached_artist_likes,
            get_cached_artist_profile,
            get_cached_artist_uploads,
        )

        self._profile = get_cached_artist_profile(artist_id)
        self._collections = get_cached_artist_collections(artist_id)
        self._uploads = get_cached_artist_uploads(artist_id)
        self._likes = get_cached_artist_likes(artist_id)

        logger.debug(
            "ArtistProfileView.populate: artist_id=%s profile=%s uploads=%s likes=%s collections=%s",
            artist_id,
            bool(self._profile),
            len(self._uploads),
            len(self._likes),
            len(self._collections),
        )
        if not self._profile:
            self.query_one("#ap-name", Static).update(
                "[dim]Profile data not cached yet[/dim]"
            )
            return

        p = self._profile
        self.query_one("#ap-name", Static).update(Text(p.get("name", "?")))
        em_dash = "\u2014"
        self.query_one("#ap-location", Static).update(
            Text(p.get("location") or em_dash, style="dim")
        )
        desc = p.get("description", "") or ""
        self.query_one("#ap-desc", Static).update(
            Text(desc, style="dim") if desc else Text("No description", style="dim")
        )
        followers = p.get("followers_count", 0) or 0
        tracks = p.get("track_count", 0) or 0
        self.query_one("#ap-meta", Static).update(
            f"[dim]{_short_views(followers)} followers \u2022 {tracks} tracks[/dim]"
        )
        self.query_one("#ap-browsing", Static).update(
            f"Browsing: {p.get('name', '?')}  \u2022  "
            f"{_short_views(followers)} followers  \u2022  {tracks} tracks"
        )

        self._load_avatar(p.get("avatar_url", ""))

        self._build_chips()

        album_count = sum(1 for c in self._collections if c.get("type") == "album")
        playlist_count = len(self._collections) - album_count
        self.query_one("#ap-album-label", Static).update(
            f"ALBUMS  [dim]\u2022 {album_count} items[/dim]"
        )
        self.query_one("#ap-playlist-label", Static).update(
            f"PLAYLISTS  [dim]\u2022 {playlist_count} items[/dim]"
        )

        has_albums = album_count > 0
        has_playlists = playlist_count > 0
        self.query_one("#ap-album-label", Static).display = has_albums
        self.query_one("#ap-album-chips", Horizontal).display = has_albums
        self.query_one("#ap-playlist-label", Static).display = has_playlists
        self.query_one("#ap-playlist-chips", Horizontal).display = has_playlists
        self.query_one("#ap-sep-chips", Static).display = has_albums or has_playlists

        has_uploads = len(self._uploads) > 0
        has_likes = len(self._likes) > 0
        tab_section = self.query_one("#ap-tab-section", Vertical)

        if has_uploads and has_likes:
            tab_section.display = True
            self._active_tab = "uploads"
        elif has_uploads:
            tab_section.display = False
            self._active_tab = "uploads"
        else:
            tab_section.display = False
            self._active_tab = "liked"

        self._update_tab_headers()
        self._populate_tracks()

        self.query_one("#ap-track-list", DataTable).focus()

        self._apply_compact()

    def _load_avatar(self, avatar_url: str) -> None:
        if not avatar_url:
            return
        from nyrx.config import CACHE_DIR

        cache_dir = CACHE_DIR / "sc_avatars"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local = cache_dir / f"{self._artist_id}.jpg"
        if local.exists():
            self.query_one("#ap-avatar", ThumbImage).image = str(local)
            return
        self._fetch_avatar(avatar_url)

    @work(thread=True)
    def _fetch_avatar(self, avatar_url: str) -> None:
        from nyrx.config import CACHE_DIR

        cache_dir = CACHE_DIR / "sc_avatars"
        cache_dir.mkdir(parents=True, exist_ok=True)
        local = cache_dir / f"{self._artist_id}.jpg"
        try:
            resp = requests.get(avatar_url, timeout=10)
            resp.raise_for_status()
            local.write_bytes(resp.content)
        except Exception:
            self.log.warning("Failed to fetch avatar for artist %s", self._artist_id)
            return
        if local.exists():
            self.app.call_from_thread(self._set_avatar, str(local))

    def _set_avatar(self, path: str) -> None:
        try:
            self.query_one("#ap-avatar", ThumbImage).image = path
        except Exception:
            logger.debug("Failed to set avatar image in artist profile")

    def _build_chips(self) -> None:
        album_row = self.query_one("#ap-album-chips", Horizontal)
        playlist_row = self.query_one("#ap-playlist-chips", Horizontal)
        album_row.remove_children()
        playlist_row.remove_children()
        logger.debug(
            "ArtistProfileView._build_chips: collections=%s", len(self._collections)
        )
        albums = [c for c in self._collections if c.get("type") == "album"]
        playlists = [c for c in self._collections if c.get("type") != "album"]
        for c in albums[: self._MAX_CHIPS]:
            btn = ApChipButton(Text(c.get("title", "?")), classes="ap-chip")
            btn._cid = c.get("collection_id", "")
            album_row.mount(btn)
        for c in playlists[: self._MAX_CHIPS]:
            btn = ApChipButton(Text(c.get("title", "?")), classes="ap-chip")
            btn._cid = c.get("collection_id", "")
            playlist_row.mount(btn)

    def _update_tab_headers(self) -> None:
        is_uploads = self._active_tab == "uploads"
        labels = self.query_one("#ap-tab-labels", Static)
        ul = self.query_one("#ap-underline-row", Static)
        if is_uploads:
            labels.update("\u2190  [#edecee]UPLOADS[/]   [#707070]LIKED[/]  \u2192")
            ul.update("   " + "\u2594" * 7 + " " * 11)
        else:
            labels.update("\u2190  [#707070]UPLOADS[/]   [#edecee]LIKED[/]  \u2192")
            ul.update(" " * 13 + "\u2594" * 5 + " " * 3)

    def _populate_tracks(self) -> None:
        dt = self.query_one("#ap-track-list", DataTable)
        self._track_data_map.clear()
        previous_row = None
        track_count = len(
            self._uploads if self._active_tab == "uploads" else self._likes
        )
        logger.debug(
            "ArtistProfileView._populate_tracks: tab=%s count=%s",
            self._active_tab,
            track_count,
        )
        if dt.row_count > 0 and dt.cursor_coordinate is not None:
            previous_row = dt.cursor_coordinate.row

        dt.clear(columns=True)
        dt.add_column("track", key="track", width=60)
        dt.add_column("artist", key="artist", width=18)
        dt.add_column("plays", key="plays", width=7)
        dt.add_column("likes", key="likes", width=7)
        dt.add_column("duration", key="duration", width=8)
        tracks = self._uploads if self._active_tab == "uploads" else self._likes
        listened = set()
        try:
            from nyrx.sources.soundcloud import get_listened_ids

            listened = get_listened_ids()
        except Exception:
            logger.debug("Failed to get listened IDs for artist profile tracks")

        for idx, t in enumerate(tracks):
            tid = t.get("track_id", "") or t.get("yt_id", "")
            consumed = tid in listened
            title = t.get("title", "?")
            channel = t.get("channel", "?")
            duration = fmt_duration(t.get("duration", 0))
            views = _short_views(t.get("view_count", 0))
            likes = _short_views(t.get("like_count", 0))

            track_color = "#505050" if consumed else "#edecee"
            stats_color = "#404040"
            heart_color = "#A277FF" if tid in self._liked_ids else stats_color
            track_cell = Text(title, style=track_color)
            artist_color = (
                "#A277FF"
                if t.get("uploader_id", "") in self._followed_set
                else "#606060"
            )
            artist_cell = Text(channel, style=artist_color)
            plays_cell = (
                Text(f"\u25b6 {views}", style=stats_color)
                if views
                else Text("\u2014", style=stats_color)
            )
            likes_cell = (
                Text(f"\u2764\ufe0e {likes}", style=heart_color)
                if likes
                else Text("\u2014", style=heart_color)
            )
            duration_cell = Text(duration, style=stats_color)

            explicit_key = str(tid) if tid else f"row_{idx}"
            dt.add_row(
                track_cell,
                artist_cell,
                plays_cell,
                likes_cell,
                duration_cell,
                key=explicit_key,
            )
            self._track_data_map[explicit_key] = t

        if previous_row is not None and previous_row < dt.row_count:
            dt.move_cursor(row=previous_row)
        elif dt.row_count > 0:
            dt.move_cursor(row=0)
        dt.focus()

    def _switch_tab(self, direction: str) -> None:
        if direction == "left" and self._active_tab == "liked" and self._uploads:
            logger.debug(
                "ArtistProfileView._switch_tab: direction=left new_tab=uploads"
            )
            self._active_tab = "uploads"
            self._update_tab_headers()
            self._populate_tracks()
        elif direction == "right" and self._active_tab == "uploads" and self._likes:
            logger.debug("ArtistProfileView._switch_tab: direction=right new_tab=liked")
            self._active_tab = "liked"
            self._update_tab_headers()
            self._populate_tracks()
        else:
            logger.debug(
                "ArtistProfileView._switch_tab: blocked direction=%s active=%s",
                direction,
                self._active_tab,
            )

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.screen.focused

        # Chip navigation: left/right between chips in the active row
        if isinstance(focused, Button) and focused.has_class("ap-chip"):
            parent = focused.parent
            if parent is None:
                return
            siblings = list(parent.query(Button))
            if not siblings:
                return
            idx = siblings.index(focused)
            if key == "right" and idx < len(siblings) - 1:
                siblings[idx + 1].focus()
                event.stop()
                return
            if key == "left" and idx > 0:
                siblings[idx - 1].focus()
                event.stop()
                return
            if key == "down":
                if parent.id == "ap-playlist-chips":
                    self.query_one("#ap-track-list", DataTable).focus()
                    event.stop()
                    return
                # Down from album chips → first playlist chip
                playlist_row = self.query_one("#ap-playlist-chips", Horizontal)
                playlist_chips = list(playlist_row.query(Button))
                if playlist_chips:
                    playlist_chips[0].focus()
                    event.stop()
                    return
                else:
                    self.query_one("#ap-track-list", DataTable).focus()
                    event.stop()
                    return
            if key == "up":
                if parent.id == "ap-playlist-chips":
                    # Up from playlist chips → first album chip
                    album_row = self.query_one("#ap-album-chips", Horizontal)
                    album_chips = list(album_row.query(Button))
                    if album_chips:
                        album_chips[0].focus()
                    event.stop()
                    return
                if parent.id == "ap-album-chips":
                    event.stop()
                    return  # top of form, stay put
            return

        # DataTable navigation: left/right switches tab, up at first row → chips
        if isinstance(focused, DataTable) and focused.id == "ap-track-list":
            if key == "left" or key == "right":
                self._switch_tab(key)
                event.stop()  # consumed by this widget (not necessarily a successful switch)
                return
            if key == "up":
                if focused.cursor_coordinate is not None:
                    row, _ = focused.cursor_coordinate
                    if row == 0:
                        # Jump to first chip in first row
                        playlist_row = self.query_one("#ap-playlist-chips", Horizontal)
                        playlist_chips = list(playlist_row.query(Button))
                        if playlist_chips:
                            playlist_chips[0].focus()
                        else:
                            album_row = self.query_one("#ap-album-chips", Horizontal)
                            album_chips = list(album_row.query(Button))
                            if album_chips:
                                album_chips[0].focus()
                        event.stop()
                        return

    def on_mount(self) -> None:
        self.display = False
        self._update_seps()

    def on_resize(self) -> None:
        self._update_seps()

    def _update_seps(self) -> None:
        w = self.size.width - 3
        if w > 0:
            sep = "\u2500" * w
            for s in self.query(".ap-sep"):
                cast(Static, s).update(sep)
