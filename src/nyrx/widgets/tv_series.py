# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import logging
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING, Any, cast

import requests
from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, ContentSwitcher, DataTable, Label, Static
from textual_image.widget import Image as ThumbImage

from nyrx import watch_db
from nyrx.bindings import KEYBIND_BAR_TEXT
from nyrx.config import TEMP_THUMBS, TV_THUMBS_DIR, TVS_COMPACT_MAX_HEIGHT
from nyrx.helpers import require_key
from nyrx.models import MediaKind, MediaRequest

from .base import BrailleSpinner

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

EPISODE_TTL = timedelta(days=7)
TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


class SeasonChip(Button):
    """A season selector chip carrying the season number it represents."""

    _season_number: int = 0


class TVSeriesView(Vertical):
    """Embedded TV series detail view: poster, metadata, season chips, episode table."""

    can_focus_children = True

    def __init__(self, tmdb_id: int, start_season: int = 1, **kwargs: Any) -> None:
        kwargs.setdefault("id", "tvs-root")
        super().__init__(**kwargs)
        self._tmdb_id = tmdb_id
        self._bookmarked = False
        self._series_data: dict = {}
        self._seasons: list[dict] = []
        self._season_count = 0
        self._current_season = start_season
        self._season_token = 0
        self._episodes: list[dict] = []
        self._episode_data_map: dict[str, dict] = {}
        self._watched_eps: set[tuple[int, int]] = set()
        self._compact_selector = False

    def _apply_compact(self) -> None:
        compact = self.screen.size.height <= TVS_COMPACT_MAX_HEIGHT
        self.set_class(compact, "compact")

    def compose(self) -> ComposeResult:
        with ContentSwitcher(initial="tvs-loading", id="tvs-switcher"):
            with Vertical(id="tvs-loading"):
                yield BrailleSpinner()
                yield Static("loading series")
            with Vertical(id="tvs-loaded"):
                with Vertical(id="tvs-scroll"):
                    yield Static("", id="tvs-browsing")
                    yield Static("", id="tvs-browsing-sep", classes="ap-sep")
                    with Horizontal(id="tvs-header"):
                        yield ThumbImage(id="tvs-poster")
                        with Vertical(id="tvs-meta"):
                            yield Label("", id="tvs-title")
                            yield Label("", id="tvs-rating")
                            yield Label("", id="tvs-genres")
                            yield Static("", id="tvs-overview")
                    yield Static("", id="tvs-sep", classes="ap-sep")
                    yield Vertical(id="tvs-season-selector")
                    with ContentSwitcher(
                        initial="tvs-episodes", id="tvs-episodes-switcher"
                    ):
                        yield DataTable(
                            id="tvs-episodes",
                            show_header=False,
                            cursor_type="row",
                        )
                        with Vertical(id="tvs-episodes-loading"):
                            yield BrailleSpinner()
                            yield Static("loading season")
                yield Static("", id="tvs-episode-desc")
                yield Static(
                    KEYBIND_BAR_TEXT["tv-movies-series"],
                    id="tvs-keybind-bar",
                )

    def on_mount(self) -> None:
        self._update_seps()
        self._apply_compact()
        self._fetch_series_data()

    def on_unmount(self) -> None:
        self._season_token += 1
        self.log("TVSeriesView.on_unmount: tmdb_id=%s", self._tmdb_id)
        logger.debug("TVSeriesView.on_unmount: tmdb_id=%s", self._tmdb_id)

    def on_resize(self) -> None:
        self._update_seps()
        self._apply_compact()

    def _update_seps(self) -> None:
        w = self.size.width - 5
        if w > 0:
            sep = "\u2500" * w
            for s in self.query(".ap-sep"):
                if isinstance(s, Static):
                    s.update(sep)

    @work(thread=True, exclusive=True)
    def _fetch_series_data(self) -> None:
        from nyrx.sources.tv_movies.db import (
            bookmark_exists,
            load_bookmark,
            load_seasons,
        )
        from nyrx.sources.tv_movies.tmdb_cache import tv_details

        self._bookmarked = bookmark_exists(self._tmdb_id)

        if self._bookmarked:
            bm = load_bookmark(self._tmdb_id)
            seasons = load_seasons(self._tmdb_id)
            if bm and seasons:
                self.app.call_from_thread(
                    self._on_series_data_cached, dict(bm), seasons
                )
                return

        details = tv_details(self._tmdb_id)
        self.app.call_from_thread(self._on_series_data_fetched, details)

    def _on_series_data_cached(self, bm: dict, seasons: list[dict]) -> None:
        self._series_data = dict(bm)
        genres_str = bm.get("genres", "") or ""
        try:
            genre_list = json.loads(genres_str) if genres_str else []
            self._series_data["genres_display"] = ", ".join(genre_list[:3])
        except Exception:
            self._series_data["genres_display"] = ""
        self._seasons = seasons
        self._season_count = len(self._seasons)
        self._on_data_ready()

    def _on_series_data_fetched(self, details: dict | None) -> None:
        if details:
            self._series_data = {
                "title": details.get("name", ""),
                "rating": details.get("vote_average", 0),
                "vote_count": details.get("vote_count", 0),
                "year": (details.get("first_air_date") or "")[:4],
                "overview": details.get("overview", ""),
                "poster_path": details.get("poster_path", ""),
            }
            from nyrx.sources.tv_movies.tmdb_cache import genre_names

            genre_ids = [g["id"] for g in details.get("genres", [])]
            try:
                self._series_data["genres_display"] = ", ".join(
                    genre_names(genre_ids)[:3]
                )
            except Exception:
                self._series_data["genres_display"] = ""

            seasons_raw = details.get("seasons", [])
            self._seasons = [
                {
                    "season_number": s["season_number"],
                    "episode_count": s.get("episode_count", 0),
                    "name": s.get("name", ""),
                    "poster_path": s.get("poster_path", ""),
                    "air_date": s.get("air_date", ""),
                }
                for s in seasons_raw
                if s.get("season_number", 0) > 0
            ]
            self._season_count = len(self._seasons)

        self._on_data_ready()

    def _on_data_ready(self) -> None:
        """Populate the loaded state after series data and seasons are available."""
        try:
            self.query_one("#tvs-switcher", ContentSwitcher).current = "tvs-loaded"
        except Exception:
            logger.debug("_on_data_ready: #tvs-switcher not found, widget unmounted")
        self._update_header()
        self._set_poster()
        self._build_season_selector()
        self._update_chip_focus()
        self._apply_compact()
        self._load_season_episodes(self._current_season)

    def _update_header(self) -> None:
        d = self._series_data
        title = d.get("title", "")
        if self._bookmarked:
            title = f"{title}  [#A277FF]\u2764\ufe0e[/]"
        self.query_one("#tvs-title", Label).update(title)

        rating = d.get("rating", 0)
        year = d.get("year", "")
        season_count = d.get("season_count") or self._season_count
        vote_count = d.get("vote_count", 0)
        parts = []
        if rating:
            parts.append(
                f"\u2605 {rating:.1f} ({vote_count})"
                if vote_count
                else f"\u2605 {rating:.1f}"
            )
        if year:
            parts.append(year)
        if season_count:
            parts.append(f"{season_count} season{'s' if season_count != 1 else ''}")
        self.query_one("#tvs-rating", Label).update("  \u2022  ".join(parts))

        genres = d.get("genres_display", "")
        self.query_one("#tvs-genres", Label).update(genres)

        overview = d.get("overview", "")
        self.query_one("#tvs-overview", Static).update(overview)

        self.query_one("#tvs-browsing", Static).update(
            f"Browsing: {title}  \u2022  " + "  \u2022  ".join(parts)
        )

    def _set_poster(self) -> None:
        cached = TV_THUMBS_DIR / f"{self._tmdb_id}.jpg"
        if cached.exists():
            self.query_one("#tvs-poster", ThumbImage).image = str(cached)
            return
        tmp = TEMP_THUMBS / f"{self._tmdb_id}.jpg"
        if tmp.exists():
            self.query_one("#tvs-poster", ThumbImage).image = str(tmp)
            return
        poster_path = self._series_data.get("poster_path", "")
        if poster_path:
            self._fetch_poster(poster_path)

    @work(thread=True)
    def _fetch_poster(self, poster_path: str) -> None:
        TEMP_THUMBS.mkdir(parents=True, exist_ok=True)
        tmp = TEMP_THUMBS / f"{self._tmdb_id}.jpg"
        url = f"{TMDB_IMAGE_BASE}/w342{poster_path}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            tmp.write_bytes(resp.content)
        except Exception:
            logger.debug("_fetch_poster: failed for tmdb_id=%s", self._tmdb_id)
            return
        if tmp.exists():
            self.app.call_from_thread(self._set_poster_from_path, str(tmp))

    def _set_poster_from_path(self, path: str) -> None:
        try:
            self.query_one("#tvs-poster", ThumbImage).image = path
        except Exception:
            logger.debug("_set_poster_from_path: failed")

    def _build_season_selector(self) -> None:
        container = self.query_one("#tvs-season-selector", Vertical)
        container.remove_children()
        if self._season_count == 0:
            self._compact_selector = True
            container.mount(
                Static("[dim]No season data yet: try again in a moment[/dim]")
            )
            return
        if self._season_count <= 10:
            self._compact_selector = False
            self._build_chip_selector(container)
        else:
            self._compact_selector = True
            label = Static(
                f"<  Season {self._current_season} of {self._season_count}  >"
            )
            container.mount(label)

    def _build_chip_selector(self, container: Vertical) -> None:
        row = Horizontal(id="tvs-chip-row")
        container.mount(row)
        for s in self._seasons:
            sn = s["season_number"]
            label = f"Season {sn}"
            btn = SeasonChip(label, classes="season-chip")
            btn._season_number = sn
            row.mount(btn)
        first = row.query(Button).first()
        if first:
            first.focus()

    def _load_season_episodes(self, season_number: int) -> None:
        self._season_token += 1
        token = self._season_token
        self._current_season = season_number
        if self._compact_selector:
            container = self.query_one("#tvs-season-selector", Vertical)
            container.remove_children()
            label = Static(
                f"<  Season {self._current_season} of {self._season_count}  >"
            )
            container.mount(label)

        if self._bookmarked:
            from nyrx.sources.tv_movies.db import load_episodes

            episodes = load_episodes(self._tmdb_id, season_number)
            if episodes and episodes[0].get("cached_at"):
                try:
                    cached_at = datetime.fromisoformat(episodes[0]["cached_at"])
                    if datetime.now(UTC) - cached_at < EPISODE_TTL:
                        self._episodes = episodes
                        self._switch_episodes_to("tvs-episodes")
                        self._populate_episode_table()
                        if not self._episodes:
                            self.query_one("#tvs-episode-desc", Static).update(
                                "[dim]No episodes on this season[/dim]"
                            )
                        self._update_chip_focus()
                        return
                except Exception:
                    logger.debug(
                        "_load_season_episodes: cached episode data unusable, will re-fetch"
                    )

        self._show_episodes_loading()
        self._fetch_season_episodes(season_number, token)

    def _switch_episodes_to(self, state: str) -> None:
        try:
            self.query_one("#tvs-episodes-switcher", ContentSwitcher).current = state
        except Exception:
            logger.debug(
                "_switch_episodes_to: #tvs-episodes-switcher not found (state=%s)",
                state,
            )

    def _show_episodes_loading(self) -> None:
        self._switch_episodes_to("tvs-episodes-loading")

    @work(thread=True, exclusive=True)
    def _fetch_season_episodes(self, season_number: int, token: int) -> None:
        from nyrx.sources.tv_movies.tmdb_cache import season_details

        data = season_details(self._tmdb_id, season_number)
        self.app.call_from_thread(self._on_episodes_fetched, data, season_number, token)

    def _on_episodes_fetched(
        self, data: dict | None, season_number: int, token: int
    ) -> None:
        if token != self._season_token:
            return
        if data:
            if self._bookmarked:
                from nyrx.sources.tv_movies.db import load_episodes, save_episodes

                ep_list = []
                for ep in data.get("episodes", []):
                    ep_list.append(
                        {
                            "episode_number": ep["episode_number"],
                            "name": ep.get("name", ""),
                            "still_path": ep.get("still_path", "") or "",
                            "overview": ep.get("overview", "") or "",
                            "runtime": ep.get("runtime"),
                            "air_date": ep.get("air_date", "") or "",
                            "vote_average": ep.get("vote_average", 0) or 0,
                        }
                    )
                if ep_list:
                    save_episodes(self._tmdb_id, season_number, ep_list)
                    self._episodes = load_episodes(self._tmdb_id, season_number)
                else:
                    self._episodes = []
            else:
                self._episodes = [
                    {
                        "episode_number": ep["episode_number"],
                        "name": ep.get("name", ""),
                        "still_path": ep.get("still_path", "") or "",
                        "overview": ep.get("overview", "") or "",
                        "runtime": ep.get("runtime"),
                        "air_date": ep.get("air_date", "") or "",
                        "vote_average": ep.get("vote_average", 0) or 0,
                    }
                    for ep in data.get("episodes", [])
                ]
        else:
            self._episodes = []

        self._update_chip_focus()
        self._switch_episodes_to("tvs-episodes")
        self._populate_episode_table()
        if not self._episodes:
            self.query_one("#tvs-episode-desc", Static).update(
                "[dim]No episodes on this season[/dim]"
            )

    def _populate_episode_table(self) -> None:
        """Render the episode DataTable for the current season."""
        dt = self.query_one("#tvs-episodes", DataTable)
        self._episode_data_map.clear()
        self._watched_eps = watch_db.get_episode_status(f"tmdb_{self._tmdb_id}")
        prev_cursor = None
        if dt.row_count > 0 and dt.cursor_coordinate is not None:
            prev_cursor = dt.cursor_coordinate.row

        dt.clear(columns=True)
        dt.add_column("#", key="#", width=4)
        dt.add_column("Title", key="title", width=42)
        dt.add_column("\u2605", key="rating", width=7)
        dt.add_column("Status", key="status", width=6)
        dt.add_column("Dur", key="dur", width=8)

        for ep in self._episodes:
            ep_num = ep.get("episode_number", 0)
            name = ep.get("name", "")
            runtime = ep.get("runtime")
            vote_avg = ep.get("vote_average", 0) or 0
            dur = f"{runtime}min" if runtime else "\u2014"
            watched = (self._current_season, ep_num) in self._watched_eps
            num_cell = Text(f"{ep_num:02d}", style="#808080")
            title_cell = Text(name, style="#808080" if watched else "#edecee")
            rating_cell = (
                Text(f"\u2605 {vote_avg:.1f}", style="#606060")
                if vote_avg
                else Text("\u2014", style="#404040")
            )
            status_cell = (
                Text("\u2713", style="#606060")
                if watched
                else Text("\u25cf", style="#404040")
            )
            dur_cell = Text(dur, style="#606060")
            key = f"s{self._current_season}_e{ep_num}"
            dt.add_row(
                num_cell, title_cell, rating_cell, status_cell, dur_cell, key=key
            )
            self._episode_data_map[key] = ep

        if prev_cursor is not None and prev_cursor < dt.row_count:
            dt.move_cursor(row=prev_cursor)
        elif dt.row_count > 0:
            dt.move_cursor(row=0)
        dt.focus()

    def refresh_episode_statuses(self) -> None:
        """Re-query watch history DB and update the current season's episode
        Title/Status cells in-place. Called after sync completes so a just-watched
        episode flips to a checkmark without a season reload.
        """
        dt = self.query_one("#tvs-episodes", DataTable)
        if dt.row_count == 0 or not self._episode_data_map:
            return
        self._watched_eps = watch_db.get_episode_status(f"tmdb_{self._tmdb_id}")
        for key, ep in self._episode_data_map.items():
            ep_num = ep.get("episode_number", 0)
            watched = (self._current_season, ep_num) in self._watched_eps
            status_symbol = "\u2713" if watched else "\u25cf"
            status_style = "#606060" if watched else "#404040"
            title_style = "#808080" if watched else "#edecee"
            try:
                dt.update_cell(
                    key, "title", Text(ep.get("name", ""), style=title_style)
                )
                dt.update_cell(key, "status", Text(status_symbol, style=status_style))
            except Exception as e:
                logger.debug(
                    "refresh_episode_statuses: update_cell FAILED key=%s: %s", key, e
                )

    def _switch_season(self, direction: str) -> None:
        if self._season_count == 0:
            return
        if direction == "left":
            self._current_season -= 1
            if self._current_season < 1:
                self._current_season = self._season_count
        elif direction == "right":
            self._current_season += 1
            if self._current_season > self._season_count:
                self._current_season = 1
        else:
            return
        self._load_season_episodes(self._current_season)

    def _update_chip_focus(self) -> None:
        if self._compact_selector:
            return
        try:
            row = self.query_one("#tvs-chip-row", Horizontal)
            chips = list(row.query(Button))
            for chip in chips:
                sn = getattr(chip, "_season_number", None)
                if sn == self._current_season:
                    chip.add_class("active")
                else:
                    chip.remove_class("active")
        except Exception:
            logger.debug(
                "_update_chip_focus: #tvs-chip-row not found, skipping active class"
            )

    def _toggle_episode_watched(self) -> None:
        dt = self.query_one("#tvs-episodes", DataTable)
        if dt.cursor_coordinate is None:
            return
        cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
        if not cell_key or not cell_key.row_key.value:
            return
        ep = self._episode_data_map.get(cell_key.row_key.value)
        if not ep:
            return
        ep_num = ep.get("episode_number", 0)
        yt_id = f"tmdb_{self._tmdb_id}"
        key = (self._current_season, ep_num)
        if key in self._watched_eps:
            watch_db.unmark_watched(
                yt_id, season_number=self._current_season, episode_number=ep_num
            )
        else:
            watch_db.mark_watched(
                yt_id, "tv", season_number=self._current_season, episode_number=ep_num
            )
        ep_statuses = watch_db.get_episode_status(yt_id)
        now_watched = (self._current_season, ep_num) in ep_statuses
        if now_watched:
            self._watched_eps.add(key)
            status_symbol = "\u2713"
            status_style = "#606060"
            title_style = "#808080"
        else:
            self._watched_eps.discard(key)
            status_symbol = "\u25cf"
            status_style = "#404040"
            title_style = "#edecee"
        dt.update_cell(
            cell_key.row_key.value, "title", Text(ep.get("name", ""), style=title_style)
        )
        dt.update_cell(
            cell_key.row_key.value, "status", Text(status_symbol, style=status_style)
        )

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.screen.focused

        if isinstance(focused, Button) and focused.has_class("season-chip"):
            parent = focused.parent
            if parent is None:
                return
            siblings = list(parent.query(Button))
            if not siblings:
                return
            idx = siblings.index(focused)
            if key == "right":
                if idx < len(siblings) - 1:
                    siblings[idx + 1].focus()
                event.stop()
                return
            if key == "left":
                if idx > 0:
                    siblings[idx - 1].focus()
                event.stop()
                return
            if key == "down":
                self.query_one("#tvs-episodes", DataTable).focus()
                event.stop()
                return
            if key == "up":
                event.stop()
                return
            if key == "enter":
                sn = getattr(focused, "_season_number", None)
                if sn and sn != self._current_season:
                    self._current_season = sn
                    self._load_season_episodes(sn)
                event.stop()
                return

        if isinstance(focused, DataTable) and focused.id == "tvs-episodes":
            if key == "w":
                self._toggle_episode_watched()
                event.stop()
                return
            if key == "left":
                self._switch_season("left")
                event.stop()
                return
            if key == "right":
                self._switch_season("right")
                event.stop()
                return
            if key == "up":
                if focused.cursor_coordinate is not None:
                    row, _ = focused.cursor_coordinate
                    if row == 0 and not self._compact_selector:
                        try:
                            chip_row = self.query_one("#tvs-chip-row", Horizontal)
                            chips = list(chip_row.query(Button))
                            target = next(
                                (
                                    c
                                    for c in chips
                                    if getattr(c, "_season_number", None)
                                    == self._current_season
                                ),
                                chips[0] if chips else None,
                            )
                            if target:
                                target.focus()
                                event.stop()
                                return
                        except Exception:
                            logger.debug(
                                "on_key: up-navigate to chip row failed, not found or compact mode"
                            )

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        if event.data_table.id != "tvs-episodes":
            return
        episode = self._episode_data_map.get(require_key(event.row_key.value))
        if not episode:
            self._hide_episode_desc()
            return
        overview = episode.get("overview", "")
        if not overview:
            self.query_one("#tvs-episode-desc", Static).update(
                "[dim]Synopsis not available[/dim]"
            )
            return
        capped = overview[:269] + ("..." if len(overview) > 269 else "")
        self.query_one("#tvs-episode-desc", Static).update(capped)

    def _hide_episode_desc(self) -> None:
        try:
            self.query_one("#tvs-episode-desc", Static).update(
                "[dim]Synopsis not available[/dim]"
            )
        except Exception:
            logger.debug("_hide_episode_desc: #tvs-episode-desc widget not found")

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        if event.data_table.id != "tvs-episodes":
            return
        episode = self._episode_data_map.get(require_key(event.row_key.value))
        if not episode:
            return
        ep_rating = (
            episode.get("vote_average") or self._series_data.get("rating", 0) or 0
        )
        play_data = {
            "yt_id": f"tmdb_{self._tmdb_id}",
            "title": episode.get(
                "name", f"S{self._current_season} E{episode.get('episode_number', '?')}"
            ),
            "channel": "",
            "duration": (episode.get("runtime") or 0) * 60,
            "source": "tv_movies",
            "media_type": "tv",
            "tmdb_id": self._tmdb_id,
            "season_number": self._current_season,
            "episode_number": episode.get("episode_number", 1),
            "series_title": self._series_data.get("title", ""),
            "rating": ep_rating,
            "vote_count": self._series_data.get("vote_count", 0),
            "year": self._series_data.get("year", ""),
            "poster_path": self._series_data.get("poster_path", ""),
        }
        cast("MediaAppProtocol", self.app)._play(
            MediaRequest.from_dict(play_data, kind=MediaKind.EPISODE)
        )
