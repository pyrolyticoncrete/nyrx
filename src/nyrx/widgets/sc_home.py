# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import (
    Button,
    ContentSwitcher,
    DataTable,
    Input,
    ListItem,
    ListView,
    Static,
)

from nyrx.config import COMPACT_MAX_HEIGHT, COMPACT_MIN_WIDTH, SCH_NARROW_MAX_WIDTH
from nyrx.player import format_duration as fmt_duration

from .artist_profile import ArtistProfileView
from .base import BrailleSpinner, _short_views
from .history_item import HistoryItem
from .liked_screen import LikedScreen

logger = logging.getLogger(__name__)


class SCHomeView(Vertical):
    """SoundCloud home screen with trending genres, recent searches, following, liked tracks.

    Composes four sections in a scrollable vertical layout.  Call
    ``populate()`` after mounting to fill in dynamic content.
    """

    can_focus_children = True

    def __init__(self, **kwargs: Any) -> None:
        self._fs_all_artists: list[dict] = []
        self._debounce_timer: Timer | None = None
        super().__init__(**kwargs)

    SEP = "\u2500" * 84

    def compose(self) -> ComposeResult:
        with Vertical(id="sch-center"):
            yield Static("", id="sc-offline-banner")
            with Horizontal(id="sch-split"):
                with Vertical(id="sch-left"):
                    with Horizontal(id="sch-trending-header"):
                        yield Static("TRENDING\n", id="sch-trending-label")
                        yield Static("", id="chip-spinner")
                    yield Horizontal(id="sch-genre-row-1", classes="sch-genre-row")
                    yield Horizontal(id="sch-genre-row-2", classes="sch-genre-row")

                    yield Static(self.SEP, classes="sch-sep")
                    with Horizontal(classes="sch-header-row"):
                        yield Static(
                            "RECENT SEARCHES",
                            id="sch-recent-label",
                            classes="sch-header-left",
                        )
                        yield Static(
                            "", id="sch-recent-hint", classes="sch-header-right"
                        )
                    yield ListView(id="sch-recent-list")

                with Vertical(id="sch-right"):
                    yield Static(self.SEP, id="sch-first-sep", classes="sch-sep")

                    with Horizontal(classes="sch-header-row"):
                        yield Static(
                            "FOLLOWING",
                            id="sch-following-label",
                            classes="sch-header-left",
                        )
                        yield Static(
                            "ctrl+f to expand",
                            id="sch-following-hint",
                            classes="sch-header-right",
                        )
                    yield Static("", id="sch-following-content")

                    yield Static(self.SEP, classes="sch-sep")

                    with Horizontal(classes="sch-header-row"):
                        yield Static(
                            "LIKED TRACKS",
                            id="sch-liked-label",
                            classes="sch-header-left",
                        )
                        yield Static(
                            "ctrl+l to expand",
                            id="sch-liked-hint",
                            classes="sch-header-right",
                        )
                    yield Static("", id="sch-liked-content")

            yield Static("", id="sch-hint-row", classes="sch-hint")

        with Horizontal(id="following-area"):
            with Vertical(id="fs-left"):
                yield Static("FOLLOWING", id="fs-left-header")
                with Horizontal(id="fs-search-row"):
                    yield Static("/ ", id="fs-search-prefix")
                    yield Input(id="fs-search", placeholder="filter artists...")
                yield DataTable(id="fs-left-list", show_header=False, cursor_type="row")
            with Vertical(id="fs-center"):
                with Horizontal(id="fs-center-header-row"):
                    yield Static("FEED", id="fs-center-header")
                with ContentSwitcher(initial="feed-list", id="fs-center-switcher"):
                    yield ListView(id="feed-list")
                    with Vertical(id="feed-loading"):
                        yield BrailleSpinner()
                        yield Static("generating feed")
                    with Vertical(id="feed-empty"):
                        yield Static(
                            "Follow artists and like tracks to populate your feed"
                        )
            yield ArtistProfileView(id="artist-profile")
        yield LikedScreen(id="liked-area")

    def update_chip_spinner(self, frame: str) -> None:
        """Render the next trending-spinner frame into the trending header."""
        self.query_one("#chip-spinner", Static).update(frame)

    def clear_chip_spinner(self) -> None:
        """Clear the trending-header spinner."""
        self.query_one("#chip-spinner", Static).update("")

    def _update_trending_label(self) -> None:
        """Refresh the trending-region label based on client_id availability."""
        region = getattr(self.app, "_trending_region", "us")
        from nyrx.sources.soundcloud.api import client_id_available

        if not client_id_available():
            self.query_one("#sch-trending-label", Static).update(
                "TRENDING  [#b0b0b0](unavailable)[/]\n"
            )
        elif region:
            self.query_one("#sch-trending-label", Static).update(
                f"TRENDING  [#b0b0b0]({region})[/]\n"
            )

    def on_mount(self) -> None:
        self.query_one("#following-area").display = False
        dt = self.query_one("#fs-left-list", DataTable)
        dt.add_column("name", key="name")
        self._build_genre_chips()
        self._update_trending_label()

    def on_resize(self) -> None:
        w, h = self.screen.size
        self.set_class(w >= COMPACT_MIN_WIDTH and h < COMPACT_MAX_HEIGHT, "sch-wide")
        self.set_class(
            w <= SCH_NARROW_MAX_WIDTH and h < COMPACT_MAX_HEIGHT, "sch-narrow"
        )
        self._update_sidebar_class()
        try:
            self.query_one("#artist-profile", ArtistProfileView)._apply_compact()
        except Exception:
            logger.debug("Failed to apply compact on artist profile from SCHomeView")

    def _update_sidebar_class(self) -> None:
        try:
            sidebar = self.app.query_one("#sidebar")
            self.set_class(sidebar.display, "sch-sidebar-active")
        except Exception:
            logger.debug(
                "_update_sidebar_class: #sidebar not found, skipping class update"
            )

    def _build_genre_chips(self) -> None:
        from nyrx.config import SC_TRENDING_SLUGS

        rows = [
            ["electronic", "techno", "house", "pop", "soul", "r-b", "jazz", "country"],
            ["hip-hop-rap", "latin", "folk", "indie", "reggae", "rock-metal-punk"],
        ]
        for i, slugs in enumerate(rows):
            row = self.query_one(f"#sch-genre-row-{i + 1}", Horizontal)
            for slug in slugs:
                label = SC_TRENDING_SLUGS.get(slug, slug)
                btn = Button(label, id=f"sc-genre-{slug}", classes="sch-chip")
                row.mount(btn)

    def populate(
        self, searches: list[str], liked: list[dict], following: list | None = None
    ) -> None:
        """Fill dynamic sections with current data."""
        logger.debug(
            "SCHomeView.populate: searches=%s liked=%s following=%s",
            len(searches),
            len(liked),
            len(following or []),
        )
        self._populate_recent(searches)
        self._populate_following(following or [])
        self._populate_liked(liked)
        self._update_sidebar_class()
        self._update_trending_label()

    def _populate_recent(self, searches: list[str]) -> None:
        rl = self.query_one("#sch-recent-list", ListView)
        rl.clear()
        logger.debug("_populate_recent: count=%s", len(searches))
        if not searches:
            rl.mount(ListItem(Static("[dim]No recent searches[/dim]")))
            return
        for q in searches[:10]:
            rl.mount(HistoryItem(q))
        rl.index = 0
        self._apply_recent_gradient()

    def _populate_following(self, following: list) -> None:
        logger.debug("_populate_following: count=%s", len(following))
        label = self.query_one("#sch-following-label", Static)
        hint = self.query_one("#sch-following-hint", Static)
        content = self.query_one("#sch-following-content", Static)

        count = len(following)
        if not following:
            label.update("FOLLOWING")
            hint.update("")
            content.update("[dim]No artists followed yet[/dim]")
            return

        label.update(f"FOLLOWING  [#b0b0b0]({count})[/]")
        hint.update("ctrl+f to expand")

        sorted_following = sorted(
            following, key=lambda a: a.get("followed_at", ""), reverse=True
        )

        t = Text()
        names = sorted_following[:3]
        remaining = count - 3
        for i, artist in enumerate(names):
            name = artist.get("name", "?")
            name_fit = name[:30].ljust(30)
            t.append(name_fit, style="white")
            if i < len(names) - 1 or remaining > 0:
                t.append("\n")

        if remaining > 0:
            t.append(f"+ {remaining} more", style="dim")

        content.update(t)

    def _populate_liked(self, liked: list[dict]) -> None:
        logger.debug("_populate_liked: count=%s", len(liked))
        label = self.query_one("#sch-liked-label", Static)
        hint = self.query_one("#sch-liked-hint", Static)
        content = self.query_one("#sch-liked-content", Static)

        count = len(liked)
        if not liked:
            label.update("LIKED TRACKS")
            hint.update("")
            content.update("[dim]No liked tracks yet[/dim]")
            return

        label.update(f"LIKED TRACKS  [#b0b0b0]({count})[/]")
        hint.update("ctrl+l to expand")

        t = Text()

        scored = []
        for track in liked:
            title = track.get("title", "?")
            duration = fmt_duration(track.get("duration", 0))
            plays = track.get("views", 0)
            plays_str = _short_views(plays) if plays else ""
            right = f"\u25b6 {plays_str} \u00b7 {duration}" if plays_str else duration
            w = 3 + len(title) + 2 + len(right)
            scored.append((w, title, right))

        scored.sort(key=lambda x: x[0])
        items = [(s[1], s[2]) for s in scored[:3]]
        remaining = count - 3
        for i, (title, right) in enumerate(items):
            pad = max(1, 84 - 3 - len(title) - len(right))

            t.append("\u2764\ufe0e ", style="#A277FF")
            t.append(title, style="white")
            t.append(" " * pad)
            t.append(right, style="dim")
            if i < len(items) - 1 or remaining > 0:
                t.append("\n")

        if remaining > 0:
            t.append(f"+ {remaining} more", style="dim")

        content.update(t)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        if event.list_view.id == "sch-recent-list":
            self._apply_recent_gradient()

    def _apply_recent_gradient(self) -> None:
        lv = self.query_one("#sch-recent-list", ListView)
        if lv.index is None or not lv.children:
            return
        items = list(lv.children)
        for i, item in enumerate(items):
            for cls in ("-hl0", "-hl1", "-hl2", "-hl3", "-hl4"):
                item.remove_class(cls, update=False)
            dist = abs(i - lv.index)
            item.add_class(f"-hl{min(dist, 4)}", update=False)
        lv.update_node_styles()

    def set_following_artists(self, artists: list[dict]) -> None:
        self._fs_all_artists = list(artists)

    def focus_filter(self) -> None:
        self.query_one("#fs-search", Input).focus()

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "fs-search":
            if self._debounce_timer:
                self._debounce_timer.stop()
                self._debounce_timer = None
            self._debounce_timer = self.set_timer(
                0.15, lambda: self._apply_filter(event.value)
            )

    def _apply_filter(self, query: str) -> None:
        self._debounce_timer = None
        q = query.strip().lower()
        dt = self.query_one("#fs-left-list", DataTable)
        saved_key: str | None = None
        if dt.cursor_coordinate is not None and dt.row_count > 0:
            cell = dt.coordinate_to_cell_key(dt.cursor_coordinate)
            saved_key = cell.row_key.value
        dt.clear()
        new_keys: list[str] = []
        for a in self._fs_all_artists:
            name = a.get("name", a.get("permalink", "?")).lower()
            if not q or self._fuzzy_match(q, name):
                artist_id = a.get("id", "")
                display = a.get("name", a.get("permalink", "?"))
                dt.add_row(Text(display), key=artist_id)
                new_keys.append(artist_id)
        if saved_key and saved_key in new_keys:
            dt.move_cursor(row=new_keys.index(saved_key), animate=False)
        elif dt.row_count > 0:
            dt.move_cursor(row=0, animate=False)

    @staticmethod
    def _fuzzy_match(query: str, target: str) -> bool:
        it = iter(target)
        return all(c in it for c in query)

    def _focused_chip(self) -> Button | None:
        focused = self.screen.focused
        if focused is None:
            return None
        if isinstance(focused, Button) and focused.has_class("sch-chip"):
            return focused
        if focused.parent and isinstance(focused.parent, Button):
            if focused.parent.has_class("sch-chip"):
                return focused.parent
        return None

    def _focused_is_recent(self) -> bool:
        from textual.dom import DOMNode

        focused = self.screen.focused
        if focused is None:
            return False
        rl = self.query_one("#sch-recent-list", ListView)
        node: DOMNode | None = focused
        while node is not None:
            if node is rl:
                return True
            node = node.parent
        return False

    def on_key(self, event: events.Key) -> None:
        key = event.key
        chip = self._focused_chip()
        is_recent = self._focused_is_recent()

        row1 = list(self.query_one("#sch-genre-row-1").query(Button))
        row2 = list(self.query_one("#sch-genre-row-2").query(Button))
        rl = self.query_one("#sch-recent-list", ListView)

        if chip is not None:
            if key == "right":
                if chip in row1:
                    i = row1.index(chip)
                    if i < len(row1) - 1:
                        row1[i + 1].focus()
                elif chip in row2:
                    i = row2.index(chip)
                    if i < len(row2) - 1:
                        row2[i + 1].focus()
                event.stop()
                return

            if key == "left":
                if chip in row1:
                    i = row1.index(chip)
                    if i > 0:
                        row1[i - 1].focus()
                elif chip in row2:
                    i = row2.index(chip)
                    if i > 0:
                        row2[i - 1].focus()
                event.stop()
                return

            if key == "down":
                if chip in row1:
                    i = row1.index(chip)
                    target = min(i, len(row2) - 1)
                    row2[target].focus()
                elif chip in row2:
                    if rl.children:
                        rl.focus()
                event.stop()
                return

            if key == "up":
                if chip in row2:
                    i = row2.index(chip)
                    target = min(i, len(row1) - 1)
                    row1[target].focus()
                event.stop()
                return

        if is_recent:
            if key == "up":
                if rl.index is not None and rl.index > 0:
                    return
                if row2:
                    row2[0].focus()
                event.stop()
                return

        if self.query_one("#following-area").display:
            focused = self.screen.focused
            fs_input = self.query_one("#fs-search", Input)
            if focused is fs_input:
                if key == "escape":
                    if fs_input.value:
                        fs_input.value = ""
                        fs_input.focus()
                    else:
                        self.query_one("#fs-left-list", DataTable).focus()
                    event.stop()
                    return
                if key == "down":
                    self.query_one("#fs-left-list", DataTable).focus()
                    event.stop()
                    return
            lst = self.query_one("#fs-left-list", DataTable)
            if (
                focused is lst
                and getattr(self.app, "_pending_unfollow_artist", None) is not None
                and key != "ctrl+d"
            ):
                getattr(self.app, "clear_pending_unfollow", lambda: None)()
                event.stop()
                return
            if focused is lst and key == "up" and lst.cursor_row == 0:
                fs_input.focus()
                event.stop()
                return
