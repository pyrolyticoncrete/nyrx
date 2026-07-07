# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import logging
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import ContentSwitcher, DataTable, Input, Static
from textual_image.widget import Image as ThumbImage

from nyrx import watch_db
from nyrx.config import (
    TIMEOUT_INFO,
    TV_THUMBS_DIR,
    WL_COMPACT_MAX_HEIGHT,
    WL_COMPACT_MAX_WIDTH,
)

logger = logging.getLogger(__name__)


class WatchlistScreen(Vertical):
    """Full-page watchlist view with cover panel, search filter, and DataTable.

    Two-panel layout: left cover panel (poster + metadata), right list panel
    (keybind bar, section header, search bar, DataTable).  Mirrors
    LikedScreen's search/filter/debounce pattern.
    """

    can_focus_children = True

    def __init__(self, **kwargs: Any) -> None:
        self._bookmarks: list[dict] = []
        self._filtered: list[dict] = []
        self._bookmark_data_map: dict[str, dict] = {}
        self._debounce_timer: Timer | None = None
        self._pending_delete_tmdb: int | None = None
        self._row_status_map: dict[str, str] = {}
        self._w_series_notified = False
        super().__init__(**kwargs)

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="wl-content", id="wl-switcher"):
            with Horizontal(id="wl-content"):
                with Vertical(id="wl-cover"):
                    yield ThumbImage(id="wl-poster")
                    yield Static("", id="wl-meta")
                with Vertical(id="wl-list-panel"):
                    with Vertical(id="wl-list-inner"):
                        yield Static("", id="wl-keybind-bar")
                        yield Static("", id="wl-sep", classes="ap-sep")
                        yield Static("WATCHLIST", id="wl-section-header")
                        with Horizontal(id="wl-search-row"):
                            yield Static("/ ", id="wl-search-prefix")
                            yield Input(
                                id="wl-search",
                                placeholder="filter watchlist...",
                                classes="wl-search-input",
                            )
                        yield DataTable(
                            id="wl-list", show_header=False, cursor_type="row"
                        )
                    with Horizontal(id="wl-mini"):
                        yield ThumbImage(id="wl-mini-poster")
                        with Vertical(id="wl-mini-meta"):
                            yield Static("", id="wl-mini-title")
                            yield Static("", id="wl-mini-rating")
                            yield Static("", id="wl-mini-genres")
            with Vertical(id="wl-empty"):
                yield Static("Press l on any title to start your watchlist")

    def _apply_compact(self) -> None:
        w, h = self.screen.size
        compact = h <= WL_COMPACT_MAX_HEIGHT or w <= WL_COMPACT_MAX_WIDTH
        try:
            self.query_one("#wl-cover").display = not compact
        except Exception:
            logger.debug("Failed to toggle wl-cover in watchlist compact")
        self.set_class(compact, "compact")

    def on_mount(self) -> None:
        self.display = False
        self._apply_compact()

    def on_resize(self) -> None:
        self._apply_compact()
        self._update_seps()

    def on_show(self) -> None:
        self._apply_compact()

    def populate(
        self, bookmarks: list[dict], status_map: dict[str, str] | None = None
    ) -> None:
        """Fill the DataTable and cover panel from bookmarks.

        Status column reflects watch history via watch_db.
        """
        self._bookmarks = list(bookmarks)
        self._filtered = list(bookmarks)
        self._w_series_notified = False
        logger.debug("WatchlistScreen.populate: count=%s", len(bookmarks))
        self.query_one("#wl-section-header", Static).update(
            f"WATCHLIST  [dim]({len(bookmarks)})[/dim]"
        )
        self._update_seps()
        switcher = self.query_one("#wl-switcher", ContentSwitcher)
        if bookmarks:
            self._rebuild_table(bookmarks)
            switcher.current = "wl-content"
            self.query_one("#wl-list", DataTable).focus()
        else:
            switcher.current = "wl-empty"
            self.query_one("#wl-meta", Static).update("")
            self.query_one("#wl-poster", ThumbImage).display = False
            self._clear_mini()

    def _update_seps(self) -> None:
        w = self.size.width
        if w > 0:
            self.query_one("#wl-sep", Static).update("\u2500" * w)

    def _rebuild_table(self, bookmarks: list[dict]) -> None:
        dt = self.query_one("#wl-list", DataTable)
        self._bookmark_data_map.clear()
        self._row_status_map.clear()

        pending = self._pending_delete_tmdb
        if pending is not None and pending not in {b.get("tmdb_id") for b in bookmarks}:
            self._pending_delete_tmdb = None
            pending = None

        previous_row = None
        try:
            if dt.row_count > 0 and dt.cursor_coordinate is not None:
                previous_row = dt.cursor_coordinate.row
        except Exception:
            logger.debug("Failed to read cursor position when rebuilding table")

        dt.clear(columns=True)
        dt.add_column("Title", key="title", width=45)
        dt.add_column("Type", key="type", width=6)
        dt.add_column("Year", key="year", width=6)
        dt.add_column("Rating", key="rating", width=7)
        dt.add_column("Status", key="status", width=12)
        if not bookmarks:
            return

        movie_ids: list[str] = []
        series_info: list[tuple[int, dict]] = []
        for i, bm in enumerate(bookmarks):
            tmdb_id = bm.get("tmdb_id")
            if not tmdb_id:
                continue
            yt_id = f"tmdb_{tmdb_id}"
            if bm.get("media_type") == "tv":
                series_info.append((i, bm))
            elif bm.get("media_type") == "movie":
                movie_ids.append(yt_id)

        movie_watched: set[str] = set()
        if movie_ids:
            movie_watched = watch_db.get_movie_watched(movie_ids)

        series_status: dict[int, str] = {}
        for idx, bm in series_info:
            tmdb_id = bm["tmdb_id"]
            yt_id = f"tmdb_{tmdb_id}"
            watched = watch_db.get_episode_status(yt_id)
            total = bm.get("number_of_episodes", 0) or 0
            if total:
                series_status[idx] = f"\u25d0 {len(watched)}/{total}"
            elif watched:
                series_status[idx] = f"\u25d0 {len(watched)}"
            else:
                series_status[idx] = "\u25cf unwatched"

        for i, bm in enumerate(bookmarks):
            tmdb_id = bm.get("tmdb_id", "")
            title = bm.get("title", "?")
            media_type = bm.get("media_type", "movie")
            type_label = "Movie" if media_type == "movie" else "TV"
            year = bm.get("year", "")
            rating = bm.get("rating", 0) or 0

            is_pending = pending is not None and tmdb_id == pending
            if is_pending:
                title_style = "bold #ff6666"
                meta_style = "#ff6666"
                title_text = f"press ctrl+d again to remove  [{title}]"
            else:
                title_style = "#edecee"
                meta_style = "#606060"
                title_text = title

            if media_type == "movie":
                status_text = (
                    "\u2713 watched"
                    if f"tmdb_{tmdb_id}" in movie_watched
                    else "\u25cf unwatched"
                )
            else:
                status_text = series_status.get(i, "\u25cf unwatched")

            title_cell = Text(title_text, style=title_style)
            type_cell = Text(type_label, style=meta_style)
            year_cell = Text(
                str(year) if year else "\u2014",
                style="#404040" if not is_pending else meta_style,
            )
            star = "\u2605 " if rating >= 5 else ""
            rating_style = "#b0b0b0" if not is_pending else meta_style
            rating_cell = (
                Text(f"{star}{rating:.1f}", style=rating_style)
                if rating
                else Text("\u2014", style="#404040" if not is_pending else meta_style)
            )
            status_style = meta_style if is_pending else "#404040"
            status_cell = Text(status_text, style=status_style)

            explicit_key = (
                str(tmdb_id) if tmdb_id else f"row_{len(self._bookmark_data_map)}"
            )
            dt.add_row(
                title_cell,
                type_cell,
                year_cell,
                rating_cell,
                status_cell,
                key=explicit_key,
            )
            self._bookmark_data_map[explicit_key] = bm
            self._row_status_map[explicit_key] = status_text

        if previous_row is not None and previous_row < dt.row_count:
            dt.move_cursor(row=previous_row)
        elif dt.row_count > 0:
            dt.move_cursor(row=0)

        self._highlight_first_cover()

    def refresh_statuses(self) -> None:
        """Re-query watch history DB and update Status column cells in-place.
        Called after sync completes if the watchlist is visible.
        """
        dt = self.query_one("#wl-list", DataTable)
        if dt.row_count == 0 or not self._bookmark_data_map:
            return

        movie_ids: list[str] = []
        for row_key, bm in self._bookmark_data_map.items():
            tmdb_id = bm.get("tmdb_id")
            if not tmdb_id:
                continue
            if bm.get("media_type") == "movie":
                movie_ids.append(f"tmdb_{tmdb_id}")

        movie_watched: set[str] = set()
        if movie_ids:
            movie_watched = watch_db.get_movie_watched(movie_ids)

        for row_key, bm in self._bookmark_data_map.items():
            tmdb_id = bm.get("tmdb_id")
            if not tmdb_id:
                continue
            yt_id = f"tmdb_{tmdb_id}"
            row_key = str(tmdb_id)
            if bm.get("media_type") == "movie":
                status_text = (
                    "\u2713 watched" if yt_id in movie_watched else "\u25cf unwatched"
                )
            else:
                total = bm.get("number_of_episodes", 0) or 0
                watched = watch_db.get_episode_status(yt_id)
                status_text = (
                    f"\u25d0 {len(watched)}/{total}"
                    if total
                    else f"\u25d0 {len(watched)}"
                    if watched
                    else "\u25cf unwatched"
                )
            self._row_status_map[row_key] = status_text
            logger.debug(
                "refresh_statuses: row_key=%s status_text=%s", row_key, status_text
            )
            try:
                dt.update_cell(row_key, "status", Text(status_text, style="#404040"))
            except Exception as e:
                logger.debug(
                    "refresh_statuses: update_cell FAILED row_key=%s: %s", row_key, e
                )

    def _toggle_movie_watched(self) -> None:
        dt = self.query_one("#wl-list", DataTable)
        if dt.cursor_coordinate is None:
            return
        cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
        if not cell_key or not cell_key.row_key.value:
            return
        bm = self._bookmark_data_map.get(cell_key.row_key.value)
        if not bm or bm.get("media_type") != "movie":
            return
        tmdb_id = bm.get("tmdb_id")
        if not tmdb_id:
            return
        yt_id = f"tmdb_{tmdb_id}"
        row_key = str(tmdb_id)
        watched = yt_id in watch_db.get_movie_watched([yt_id])
        if watched:
            watch_db.unmark_watched(yt_id)
        else:
            watch_db.mark_watched(yt_id, "movie")
        watched = yt_id in watch_db.get_movie_watched([yt_id])
        status_text = "\u2713 watched" if watched else "\u25cf unwatched"
        self._row_status_map[row_key] = status_text
        logger.debug(
            "_toggle_movie_watched: yt_id=%s now_watched=%s row_key=%s",
            yt_id,
            watched,
            row_key,
        )
        try:
            dt.update_cell(row_key, "status", Text(status_text, style="#404040"))
            logger.debug(
                "_toggle_movie_watched: update_cell OK for row_key=%s", row_key
            )
        except Exception as e:
            logger.debug(
                "_toggle_movie_watched: update_cell FAILED for row_key=%s: %s",
                row_key,
                e,
            )

    def _highlight_first_cover(self) -> None:
        dt = self.query_one("#wl-list", DataTable)
        if dt.row_count > 0 and dt.cursor_coordinate is not None:
            cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
            if cell_key and cell_key.row_key.value:
                self._update_cover(cell_key.row_key.value)

    # ── Row style updates (in-place, no full rebuild) ────────────

    def _update_row_style(self, tmdb_id: int, is_pending: bool) -> None:
        """Toggle a single row between normal and pending-delete styling."""
        dt = self.query_one("#wl-list", DataTable)
        row_key = str(tmdb_id)

        if is_pending:
            red = "#ff6666"
            dt.update_cell(
                row_key,
                "title",
                Text("press ctrl+d again to remove", style=f"bold {red}"),
            )
            dt.update_cell(row_key, "type", Text("", style=red))
            dt.update_cell(row_key, "year", Text("", style=red))
            dt.update_cell(row_key, "rating", Text("", style=red))
            dt.update_cell(row_key, "status", Text("", style=red))
        else:
            bm = self._bookmark_data_map.get(row_key)
            if bm is None:
                return
            title = bm.get("title", "?")
            type_label = "Movie" if bm.get("media_type") == "movie" else "TV"
            year = bm.get("year", "")
            rating = bm.get("rating", 0) or 0

            dt.update_cell(row_key, "title", Text(title, style="#edecee"))
            dt.update_cell(row_key, "type", Text(type_label, style="#606060"))
            dt.update_cell(
                row_key, "year", Text(str(year) if year else "\u2014", style="#404040")
            )
            star = "\u2605 " if rating >= 5 else ""
            rating_text = f"{star}{rating:.1f}" if rating else "\u2014"
            rating_style = "#b0b0b0" if rating else "#404040"
            dt.update_cell(row_key, "rating", Text(rating_text, style=rating_style))
            saved = self._row_status_map.get(row_key, "\u25cf unwatched")
            dt.update_cell(row_key, "status", Text(saved, style="#404040"))

    def remove_bookmark_row(self, tmdb_id: int) -> None:
        """Visually remove a row and clean up data structures: no DB ops."""
        row_key = str(tmdb_id)
        dt = self.query_one("#wl-list", DataTable)
        dt.remove_row(row_key)
        dt.remove_class("-pending-cursor")
        self._bookmark_data_map.pop(row_key, None)
        self._bookmarks = [b for b in self._bookmarks if b.get("tmdb_id") != tmdb_id]
        self._filtered = [b for b in self._filtered if b.get("tmdb_id") != tmdb_id]
        if dt.row_count > 0:
            self._highlight_first_cover()
        else:
            self.query_one("#wl-poster", ThumbImage).display = False
            self.query_one("#wl-meta", Static).update("")
            self._clear_mini()
            self.query_one("#wl-switcher", ContentSwitcher).current = "wl-empty"

    # ── Pending delete ───────────────────────────────────────────

    def set_pending_delete(self, tmdb_id: int) -> None:
        """Mark a row as pending deletion."""
        if (
            self._pending_delete_tmdb is not None
            and self._pending_delete_tmdb != tmdb_id
        ):
            self._update_row_style(self._pending_delete_tmdb, False)
        self._pending_delete_tmdb = tmdb_id
        self._update_row_style(tmdb_id, True)
        self.query_one("#wl-list", DataTable).add_class("-pending-cursor")

    def clear_pending_delete(self) -> None:
        """Clear pending delete state and restore normal styling."""
        prev = self._pending_delete_tmdb
        self._pending_delete_tmdb = None
        if prev is not None:
            self._update_row_style(prev, False)
            self.query_one("#wl-list", DataTable).remove_class("-pending-cursor")

    def on_input_changed(self, event: Input.Changed) -> None:
        """Debounced filter on keystroke: rebuild the table after 150ms idle."""
        if self._pending_delete_tmdb is not None:
            self.clear_pending_delete()
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
            self._filtered = list(self._bookmarks)
        else:
            self._filtered = [
                b
                for b in self._bookmarks
                if q in b.get("title", "").lower()
                or q in b.get("media_type", "").lower()
            ]
        logger.debug(
            "WatchlistScreen._apply_filter: query=%s filtered=%s total=%s",
            q,
            len(self._filtered),
            len(self._bookmarks),
        )
        self._rebuild_table(self._filtered)

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.screen.focused

        if focused is self.query_one("#wl-search", Input):
            if key == "escape":
                inp = self.query_one("#wl-search", Input)
                if inp.value:
                    inp.value = ""
                    inp.focus()
                else:
                    self.query_one("#wl-list", DataTable).focus()
                event.stop()
                return
            if key == "down":
                self.query_one("#wl-list", DataTable).focus()
                event.stop()
                return

        if isinstance(focused, DataTable) and focused.id == "wl-list":
            if self._pending_delete_tmdb is not None and key != "ctrl+d":
                self.clear_pending_delete()
                event.stop()
                return
            if key == "w":
                wl_dt = self.query_one("#wl-list", DataTable)
                if wl_dt.cursor_coordinate is not None:
                    cell_key = wl_dt.coordinate_to_cell_key(wl_dt.cursor_coordinate)
                    bm = (
                        self._bookmark_data_map.get(cell_key.row_key.value)
                        if cell_key and cell_key.row_key.value is not None
                        else None
                    )
                    if (
                        bm
                        and bm.get("media_type") == "tv"
                        and not self._w_series_notified
                    ):
                        self.app.notify(
                            "Progress tracking is episode-based", timeout=TIMEOUT_INFO
                        )
                        self._w_series_notified = True
                        event.stop()
                        return
                self._toggle_movie_watched()
                event.stop()
                return
            if key == "up":
                dt = self.query_one("#wl-list", DataTable)
                if (
                    dt.cursor_coordinate is not None
                    and dt.row_count > 0
                    and dt.cursor_coordinate.row == 0
                ):
                    self.query_one("#wl-search", Input).focus()
                    event.stop()
                    return

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id == "wl-list":
            row_key = event.row_key.value
            if row_key:
                self._update_cover(row_key)
                if self._pending_delete_tmdb is not None:
                    row_data = self._bookmark_data_map.get(row_key)
                    if (
                        row_data is None
                        or row_data.get("tmdb_id") != self._pending_delete_tmdb
                    ):
                        self.clear_pending_delete()

    def _update_cover(self, row_key: str) -> None:
        data = self._bookmark_data_map.get(row_key)
        if data is None:
            self.query_one("#wl-poster", ThumbImage).display = False
            self.query_one("#wl-meta", Static).update("")
            self._clear_mini()
            return

        tmdb_id = data.get("tmdb_id", "")
        poster_path = TV_THUMBS_DIR / f"{tmdb_id}.jpg"
        poster = self.query_one("#wl-poster", ThumbImage)

        if poster_path.exists():
            poster.image = str(poster_path)
            poster.display = True
        else:
            poster.display = False

        self.query_one("#wl-meta", Static).update(self._format_meta(data))
        self._update_mini(data)

    def _clear_mini(self) -> None:
        self.query_one("#wl-mini-poster", ThumbImage).display = False
        self.query_one("#wl-mini-title", Static).update("")
        self.query_one("#wl-mini-rating", Static).update("")
        self.query_one("#wl-mini-genres", Static).update("")

    def _update_mini(self, data: dict) -> None:
        tmdb_id = data.get("tmdb_id", "")
        poster_path = TV_THUMBS_DIR / f"{tmdb_id}.jpg"
        poster = self.query_one("#wl-mini-poster", ThumbImage)
        if poster_path.exists():
            poster.image = str(poster_path)
            poster.display = True
        else:
            poster.display = False

        title = data.get("title", "")
        self.query_one("#wl-mini-title", Static).update(title)

        rating = data.get("rating", 0) or 0
        year = data.get("year", "")
        media_type = data.get("media_type", "movie")
        parts = []
        if rating:
            star = "\u2605 "
            parts.append(f"{star}{rating:.1f}")
        if year:
            parts.append(str(year))
        if media_type == "movie":
            runtime = data.get("runtime")
            if runtime:
                h, m = divmod(runtime, 60)
                if h and m:
                    parts.append(f"{h}h{m:02d}min")
                elif h:
                    parts.append(f"{h}h")
                elif m:
                    parts.append(f"{m}min")
        else:
            season_count = data.get("season_count", 0)
            if season_count:
                parts.append(f"{season_count} Season{'s' if season_count != 1 else ''}")
        self.query_one("#wl-mini-rating", Static).update("  \u00b7  ".join(parts))

        genres_raw = data.get("genres", "")
        genre_str = ""
        if genres_raw:
            try:
                genres = (
                    json.loads(genres_raw)
                    if isinstance(genres_raw, str)
                    else genres_raw
                )
                if isinstance(genres, list) and genres:
                    genre_str = " \u00b7 ".join(genres[:3])
            except (json.JSONDecodeError, TypeError):
                pass
        self.query_one("#wl-mini-genres", Static).update(genre_str)

    def _format_meta(self, data: dict) -> Text:
        t = Text()

        title = data.get("title", "")
        media_type = data.get("media_type", "movie")
        tagline = data.get("tagline") if media_type == "movie" else None

        if media_type == "movie" and tagline:
            t.append(f"{title}\n", style="bold white")
            t.append(f"{tagline}\n\n", style="italic #808080")
        elif media_type == "movie":
            t.append(f"{title}\n\n", style="bold white")
        else:
            t.append(f"{title}\n", style="bold white")

        rating = data.get("rating", 0) or 0
        year = data.get("year", "")

        if media_type == "movie":
            runtime = data.get("runtime")
            runtime_str = ""
            if runtime:
                h, m = divmod(runtime, 60)
                if h and m:
                    runtime_str = f" \u00b7 {h}h{m:02d}min"
                elif h:
                    runtime_str = f" \u00b7 {h}h"
                elif m:
                    runtime_str = f" \u00b7 {m}min"
            star = "\u2605 " if rating >= 5 else ""
            t.append(
                f"{star}{rating:.1f} \u00b7 {year}{runtime_str}\n", style="#b0b0b0"
            )
        else:
            season_count = data.get("season_count", 0)
            season_str = (
                f"{season_count} Season{'s' if season_count != 1 else ''}"
                if season_count
                else ""
            )
            star = "\u2605 " if rating >= 5 else ""
            t.append(
                f"{star}{rating:.1f} \u00b7 {year} \u00b7 {season_str}\n",
                style="#b0b0b0",
            )

        genres_raw = data.get("genres", "")
        if genres_raw:
            try:
                genres = (
                    json.loads(genres_raw)
                    if isinstance(genres_raw, str)
                    else genres_raw
                )
                if isinstance(genres, list) and genres:
                    genre_str = " \u00b7 ".join(genres[:3])
                    t.append(f"{genre_str}\n\n", style="#606060")
                else:
                    t.append("\n", style="#606060")
            except (json.JSONDecodeError, TypeError):
                t.append("\n", style="#606060")
        else:
            t.append("\n", style="#606060")

        overview = data.get("overview", "")
        if overview:
            t.append(
                f"{overview[:269] + '...' if len(overview) > 269 else overview}",
                style="#606060",
            )

        return t

    def focused_bookmark(self) -> dict | None:
        """Return the bookmark dict for the currently-highlighted row."""
        dt = self.query_one("#wl-list", DataTable)
        if dt.cursor_coordinate is None or dt.row_count == 0:
            return None
        cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
        if cell_key and cell_key.row_key.value:
            return self._bookmark_data_map.get(cell_key.row_key.value)
        return None

    def update_bookmarks(self, bookmarks: list[dict]) -> None:
        """Refresh display after external mutation (e.g. bookmark removed)."""
        self._bookmarks = list(bookmarks)
        logger.debug("WatchlistScreen.update_bookmarks: new_count=%s", len(bookmarks))
        if not bookmarks:
            try:
                self.query_one("#wl-switcher", ContentSwitcher).current = "wl-empty"
            except Exception:
                logger.debug("Failed to show empty state in watchlist screen")
            self.query_one("#wl-meta", Static).update("")
            self.query_one("#wl-poster", ThumbImage).display = False
            self._clear_mini()
            return
        self._apply_filter(self.query_one("#wl-search", Input).value)
        try:
            self.query_one("#wl-switcher", ContentSwitcher).current = "wl-content"
        except Exception:
            logger.debug("Failed to show content state in watchlist screen")
