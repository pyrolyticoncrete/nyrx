# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.widget import Widget

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

from textual.containers import Horizontal, Vertical
from textual.widgets import Button, Static

from nyrx.config import TV_HOME_COMPACT_MAX_HEIGHT

from .watchlist_screen import WatchlistScreen


class TVChip(Button):
    can_focus = True

    def __init__(self, data: dict, bookmarked: bool = False, **kwargs: Any) -> None:
        title = data.get("title", "")
        prefix = "\u2764\ufe0e " if bookmarked else "  "
        rating = data.get("rating", 0) or 0
        star = "\u2605 " if rating >= 5 else ""
        year = data.get("year", "")
        mtype = data.get("media_type", "movie")
        label_type = "Movie" if mtype == "movie" else "TV"
        second = (
            f"{star}{rating:.1f} \u00b7 {year} \u00b7 {label_type}"
            if rating
            else f"{year} \u00b7 {label_type}"
        )
        super().__init__(label=f"{prefix}{title}\n{second}", **kwargs)
        self.data = data
        self.bookmarked = bookmarked

    def render(self) -> Text:
        t = Text()
        focused = self.has_focus
        title_style = "bold black" if focused else "bold white"
        sub_style = "black" if focused else "dim #b0b0b0"
        title = self.data.get("title", "")
        if self.bookmarked:
            t.append("\u2764\ufe0e ", style=title_style)
        else:
            t.append("  ")
        t.append(f"{title}\n", style=title_style)
        rating = self.data.get("rating", 0) or 0
        star = "\u2605 " if rating >= 5 else ""
        year = self.data.get("year", "")
        mtype = self.data.get("media_type", "movie")
        label = "Movie" if mtype == "movie" else "TV"
        second = (
            f"{star}{rating:.1f} \u00b7 {year} \u00b7 {label}"
            if rating
            else f"{year} \u00b7 {label}"
        )
        t.append(second, style=sub_style)
        return t


class TVHomeView(Vertical):
    can_focus_children = True

    SEP = "\u2500" * 84

    def _apply_compact(self) -> None:
        compact = self.screen.size.height <= TV_HOME_COMPACT_MAX_HEIGHT
        self.set_class(compact, "compact")

    def on_mount(self) -> None:
        self._apply_compact()

    def on_resize(self) -> None:
        self._apply_compact()

    def on_show(self) -> None:
        self._apply_compact()

    def compose(self) -> ComposeResult:
        with Vertical(id="tv-center"):
            yield Static("", id="tv-offline-banner")
            with Vertical(id="tv-watchlist-section"):
                with Horizontal(classes="tv-section-header"):
                    yield Static("WATCHLIST", classes="tv-section-label")
                    yield Static("", id="tv-watchlist-hint", classes="tv-section-hint")
                yield Static("", id="tv-watchlist-content", classes="tv-watchlist-text")

            with Vertical(id="tv-rec-wrapper"):
                yield Static(self.SEP, classes="sch-sep")
                with Vertical(id="tv-rec-section", classes="tv-section-hidden"):
                    yield Static("RECOMMENDED FOR YOU", classes="tv-section-header")
                    yield Horizontal(id="tv-rec-content", classes="tv-chip-row")

            yield Static(self.SEP, classes="sch-sep")
            with Vertical(id="tv-trending-section"):
                yield Static("TRENDING THIS WEEK", classes="tv-section-header")
                yield Horizontal(id="tv-trending-content", classes="tv-chip-row")

            yield Static(self.SEP, id="tv-popular-sep", classes="sch-sep")
            with Vertical(id="tv-popular-section"):
                yield Static("POPULAR", classes="tv-section-header")
                yield Horizontal(id="tv-popular-content", classes="tv-chip-row")

        yield WatchlistScreen(id="watchlist-screen")

    # ---------- population ----------

    def populate_watchlist(self, bookmarks: list[dict]) -> None:
        container = self.query_one("#tv-watchlist-content", Static)
        hint = self.query_one("#tv-watchlist-hint", Static)
        if not bookmarks:
            container.update("[dim]Press l on any title to start your watchlist[/dim]")
            hint.update("")
            return
        hint.update("[dim]ctrl+w to expand[/dim]")
        t = Text()
        for i, bm in enumerate(bookmarks[:3]):
            title = bm.get("title", "?")
            year = bm.get("year", "")
            mtype = bm.get("media_type", "movie")
            rating = bm.get("rating", 0) or 0
            star = "\u2605 " if rating >= 5 else ""
            label = f"{star}{rating:.1f} \u00b7 {'Movie' if mtype == 'movie' else 'TV'}"
            left = f"\u2764\ufe0e {title} ({year})"
            pad = max(1, 84 - len(left) - 1 - len(label))
            t.append("\u2764\ufe0e ", style="#A277FF")
            t.append(f"{title} ({year})", style="white")
            t.append(" " * pad)
            t.append(label, style="dim")
            if i < min(len(bookmarks), 3) - 1:
                t.append("\n")
        remaining = len(bookmarks) - 3
        if remaining > 0:
            t.append(f"\n+ {remaining} more", style="dim")
        container.update(t)

    def populate_row(
        self,
        row_id: str,
        items: list[dict],
        bookmarked_ids: set[int],
        hide_section_if_empty: bool = False,
    ) -> None:
        row = self.query_one(row_id, Horizontal)
        row.remove_children()
        section = row.parent
        if section is None:
            return
        if not items:
            if hide_section_if_empty:
                target = (
                    section.parent
                    if section.parent and section.parent.id == "tv-rec-wrapper"
                    else section
                )
                target.add_class("tv-section-hidden")
            return
        section.remove_class("tv-section-hidden")
        if section.parent and section.parent.id == "tv-rec-wrapper":
            section.parent.remove_class("tv-section-hidden")
        for item in items:
            bm = (
                item.get("tmdb_id") in bookmarked_ids
                or item.get("id") in bookmarked_ids
            )
            chip = TVChip(item, bookmarked=bm, classes="tv-chip")
            row.mount(chip)

    def populate(
        self,
        bookmarks: list[dict],
        recs: list[dict],
        trending: list[dict],
        popular: list[dict],
    ) -> None:
        self.populate_watchlist(bookmarks)
        bm_ids = {b["tmdb_id"] for b in bookmarks}
        self.populate_row("#tv-rec-content", recs, bm_ids, hide_section_if_empty=True)
        self.populate_row(
            "#tv-trending-content", trending, bm_ids, hide_section_if_empty=False
        )
        self.populate_row(
            "#tv-popular-content", popular, bm_ids, hide_section_if_empty=False
        )

    # ---------- navigation (mirrors ArtistProfileView.on_key) ----------

    def on_key(self, event: events.Key) -> None:
        key = event.key
        focused = self.screen.focused
        if not isinstance(focused, TVChip):
            return

        parent = focused.parent
        if parent is None:
            return

        siblings = list(parent.query(TVChip))
        idx = siblings.index(focused) if focused in siblings else -1

        if key == "right" and idx != -1 and idx < len(siblings) - 1:
            siblings[idx + 1].focus()
            event.stop()
            return
        if key == "left" and idx != -1 and idx > 0:
            siblings[idx - 1].focus()
            event.stop()
            return

        if key in ("up", "down"):
            rows = [
                r
                for r in self.query(".tv-chip-row")
                if r.display
                and r.parent is not None
                and r.parent.display
                and list(r.query(TVChip))
            ]
            try:
                row_idx = rows.index(cast("Widget", parent))
            except ValueError:
                return
            target_row_idx = row_idx - 1 if key == "up" else row_idx + 1
            if 0 <= target_row_idx < len(rows):
                target_chips = list(rows[target_row_idx].query(TVChip))
                if target_chips:
                    target = min(idx, len(target_chips) - 1) if idx != -1 else 0
                    target_chips[target].focus()
            event.stop()
            return

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if isinstance(event.button, TVChip):
            event.stop()
            cast("MediaAppProtocol", self.app).action_play()
