# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem

from nyrx.player import format_duration as fmt_duration
from nyrx.player import format_views

from .base import _short_views

logger = logging.getLogger(__name__)


class ResultItem(ListItem):
    """A search result card in the results ListView."""

    def __init__(
        self,
        data: dict,
        liked: bool = False,
        watched: bool = False,
        following: bool = False,
    ) -> None:
        self.data = data
        self.liked = liked
        self.watched = watched
        self.following = following
        super().__init__()

    def set_liked(self, liked: bool) -> None:
        self.liked = liked
        logger.debug(
            "ResultItem.set_liked: liked=%s title=%s",
            liked,
            self.data.get("title", "")[:20],
        )
        try:
            lbl = self.query_one(".card-title", Label)
            title = self.data["title"]
            text = Text(title)
            if liked:
                text.append("  ")
                text.append("\u2764\ufe0e", style="#A277FF")
            lbl.update(text)
        except Exception:
            logger.debug("Failed to update liked state in result item")

    def set_following(self, following: bool) -> None:
        self.following = following
        logger.debug(
            "ResultItem.set_following: following=%s channel=%s",
            following,
            self.data.get("channel", "")[:20],
        )
        try:
            lbl = self.query_one(".card-channel", Label)
            channel = self.data["channel"]
            lbl.update(Text(channel, style="#A277FF") if following else Text(channel))
        except Exception:
            logger.debug("Failed to update following state in result item")

    def compose(self) -> ComposeResult:
        with Vertical(classes="card-meta"):
            title = self.data.get("title", "")
            title_text = Text(title)
            if self.liked and self.data.get("source") in ("soundcloud", "tv_movies"):
                title_text.append("  ")
                title_text.append("\u2764\ufe0e", style="#A277FF")
            if self.watched and self.data.get("source") != "soundcloud":
                title_text.append("  ")
                title_text.append("[\u2713]", style="#A277FF")
            yield Label(title_text, classes="card-title")
            if self.data.get("source") == "soundcloud":
                channel = self.data.get("channel", "")
                channel_text = (
                    Text(channel, style="#A277FF") if self.following else Text(channel)
                )
                yield Label(channel_text, classes="card-channel")
                views = self.data.get("views", 0)
                duration = fmt_duration(self.data.get("duration", 0))
                plays_str = _short_views(views) if views else ""
                right = (
                    f"\u25b6 {plays_str} \u2022 {duration}" if plays_str else duration
                )
                yield Label(right, classes="card-channel")
            elif self.data.get("source") == "tv_movies":
                year = self.data.get("year", "")
                rating = self.data.get("rating", 0) or 0
                mtype = self.data.get("media_type", "movie").title()
                star = "\u2605 " if rating >= 5 else ""
                if rating:
                    meta = f"{star}{rating:.1f} \u00b7 {year} \u00b7 {mtype}"
                else:
                    meta = f"{year} \u00b7 {mtype}" if year else mtype
                yield Label(meta, classes="card-channel")
                overview = self.data.get("overview", "")
                if overview:
                    text = (
                        (overview[:57] + "\u2026") if len(overview) > 60 else overview
                    )
                    yield Label(Text(text), classes="card-channel")
            else:
                channel = self.data.get("channel", "")
                views = self.data.get("views", 0)
                duration = fmt_duration(self.data.get("duration", 0))
                yield Label(
                    Text(f"{channel} \u2022 {format_views(views)} \u2022 {duration}"),
                    classes="card-channel",
                )
