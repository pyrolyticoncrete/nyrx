# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import ContentSwitcher, DataTable, Input, Static

from nyrx.player import format_duration as fmt_duration

from .base import BrailleSpinner, _short_views

logger = logging.getLogger(__name__)


class LikedScreen(Vertical):
    """Full-page liked tracks view with search filter and DataTable.

    Composes a search bar with ``/ `` prefix and a DataTable of liked
    tracks (title, artist, stats). DataTable handles large lists
    efficiently without per-row widget overhead.
    """

    can_focus_children = True

    def __init__(self, **kwargs: Any) -> None:
        self._tracks: list[dict] = []
        self._filtered: list[dict] = []
        self._track_data_map: dict[str, dict] = {}
        self._buffer_ids: set[str] = set()
        self._debounce_timer: Timer | None = None
        self._followed_set: set[str] = set()
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="ls-content", id="ls-switcher"):
            with Vertical(id="ls-content"):
                yield Static("", id="ls-keybind-bar")
                yield Static("", id="ls-sep", classes="ap-sep")
                yield Static("LIKED TRACKS", id="ls-section-header")
                with Horizontal(id="ls-search-row"):
                    yield Static("/ ", id="ls-search-prefix")
                    yield Input(
                        id="ls-search",
                        placeholder="filter tracks...",
                        classes="ls-search-input",
                    )
                yield DataTable(id="ls-list", show_header=False, cursor_type="row")
            with Vertical(id="ls-empty"):
                yield Static("Like tracks to populate this section")
            with Vertical(id="ls-loading"):
                yield BrailleSpinner()
                yield Static("syncing liked tracks")

    def populate(self, tracks: list[dict]) -> None:
        """Fill the DataTable with liked tracks."""
        self._tracks = list(tracks)
        self._filtered = list(tracks)
        logger.debug("LikedScreen.populate: track_count=%s", len(tracks))
        self.query_one("#ls-section-header", Static).update(
            f"LIKED TRACKS  [dim]({len(tracks)})[/dim]"
        )
        self._update_seps()
        switcher = self.query_one("#ls-switcher", ContentSwitcher)
        if tracks:
            self._rebuild_table(tracks)
            switcher.current = "ls-content"
            self.query_one("#ls-list", DataTable).focus()
        else:
            switcher.current = "ls-empty"

    def on_resize(self) -> None:
        self._update_seps()

    def _update_seps(self) -> None:
        w = self.size.width
        if w > 0:
            self.query_one("#ls-sep", Static).update("\u2500" * w)

    def _rebuild_table(self, tracks: list[dict]) -> None:
        dt = self.query_one("#ls-list", DataTable)
        self._track_data_map.clear()

        previous_row = None
        try:
            if dt.row_count > 0 and dt.cursor_coordinate is not None:
                previous_row = dt.cursor_coordinate.row
        except Exception:
            logger.debug("Failed to read cursor position when rebuilding table")

        dt.clear(columns=True)
        dt.add_column("track", key="track", width=60)
        dt.add_column("artist", key="artist", width=18)
        dt.add_column("plays", key="plays", width=7)
        dt.add_column("likes", key="likes", width=7)
        dt.add_column("duration", key="duration", width=8)
        if not tracks:
            return

        for t in tracks:
            ytid = t.get("yt_id", "")
            title = t.get("title", "?")
            channel = t.get("channel", "?")
            duration = fmt_duration(t.get("duration", 0))
            views = _short_views(t.get("views", 0))
            likes = _short_views(t.get("likes_count", 0))

            track_cell = Text(title, style="#edecee")
            artist_color = (
                "#A277FF"
                if t.get("uploader_id", "") in self._followed_set
                else "#606060"
            )
            channel_cell = Text(channel, style=artist_color)
            plays_cell = (
                Text(f"\u25b6 {views}", style="#404040")
                if views
                else Text("\u2014", style="#404040")
            )
            heart_color = "#404040" if ytid in self._buffer_ids else "#A277FF"
            likes_cell = (
                Text(f"\u2764\ufe0e {likes}", style=heart_color)
                if likes
                else Text("\u2014", style=heart_color)
            )
            duration_cell = Text(duration, style="#404040")

            explicit_key = str(ytid) if ytid else f"row_{len(self._track_data_map)}"
            dt.add_row(
                track_cell,
                channel_cell,
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

    def on_input_changed(self, event: Input.Changed) -> None:
        """Debounced filter on keystroke: rebuild the table after 150ms idle."""
        if self._debounce_timer:
            self._debounce_timer.stop()
            self._debounce_timer = None
        self._debounce_timer = self.set_timer(
            0.15, lambda: self._apply_filter(event.value)
        )

    def _apply_filter(self, query: str) -> None:
        self._debounce_timer = None
        q = query.strip().lower()
        if not q:
            self._filtered = list(self._tracks)
        else:
            self._filtered = [
                t
                for t in self._tracks
                if q in t.get("title", "").lower() or q in t.get("channel", "").lower()
            ]
        logger.debug(
            "LikedScreen._apply_filter: query=%s filtered=%s total=%s",
            q,
            len(self._filtered),
            len(self._tracks),
        )
        self._rebuild_table(self._filtered)

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.screen.focused

        if focused is self.query_one("#ls-search", Input):
            if key == "escape":
                inp = self.query_one("#ls-search", Input)
                if inp.value:
                    inp.value = ""
                    inp.focus()
                else:
                    self.query_one("#ls-list", DataTable).focus()
                event.stop()
                return
            if key == "down":
                self.query_one("#ls-list", DataTable).focus()
                event.stop()
                return

        if isinstance(focused, DataTable) and focused.id == "ls-list":
            if key == "up":
                dt = self.query_one("#ls-list", DataTable)
                if (
                    dt.cursor_coordinate is not None
                    and dt.row_count > 0
                    and dt.cursor_coordinate.row == 0
                ):
                    self.query_one("#ls-search", Input).focus()
                    event.stop()
                    return

    def on_mount(self) -> None:
        self.display = False

    def focused_track(self) -> dict | None:
        """Return the track dict for the currently-highlighted row."""
        dt = self.query_one("#ls-list", DataTable)
        if dt.cursor_coordinate is None or dt.row_count == 0:
            return None
        cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
        if cell_key and cell_key.row_key.value:
            return self._track_data_map.get(cell_key.row_key.value)
        return None

    def set_loading(self, loading: bool = True) -> None:
        if loading:
            try:
                self.query_one("#ls-switcher", ContentSwitcher).current = "ls-loading"
            except Exception:
                logger.debug("Failed to show loading state in liked screen")

    def update_tracks(self, tracks: list[dict]) -> None:
        """Refresh display after external mutation (e.g. unlike)."""
        self._tracks = list(tracks)
        logger.debug("LikedScreen.update_tracks: new_count=%s", len(tracks))
        if not tracks:
            try:
                self.query_one("#ls-switcher", ContentSwitcher).current = "ls-empty"
            except Exception:
                logger.debug("Failed to show empty state in liked screen")
            return
        self._apply_filter(self.query_one("#ls-search", Input).value)
        try:
            self.query_one("#ls-switcher", ContentSwitcher).current = "ls-content"
        except Exception:
            logger.debug("Failed to show content state in liked screen")
