# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import Label, ListItem

from nyrx.player import format_duration as fmt_duration

from .base import _short_views

logger = logging.getLogger(__name__)


class FeedTrackItem(ListItem):
    """A feed track item in the following area's center panel.

    Renders 3 lines: ``Title``, ``Artist``, ``▶ views • ♥ likes • duration``.
    The track dict is stored in ``self.data`` for access by ``app.py`` handlers.
    """

    def __init__(
        self, data: dict, following: bool = False, liked: bool = False
    ) -> None:
        self.data = data
        self.following = following
        self.liked = liked
        super().__init__()

    def set_following(self, following: bool) -> None:
        self.following = following
        logger.debug(
            "FeedTrackItem.set_following: following=%s title=%s",
            following,
            self.data.get("title", "")[:20],
        )
        try:
            lbl = self.query_one(".ft-channel", Label)
            channel = self.data.get("channel", "?")
            lbl.update(Text(channel, style="#A277FF") if following else Text(channel))
        except Exception:
            logger.debug("Failed to update following state in feed item")

    def set_liked(self, liked: bool) -> None:
        self.liked = liked
        logger.debug(
            "FeedTrackItem.set_liked: liked=%s title=%s",
            liked,
            self.data.get("title", "")[:20],
        )
        try:
            lbl = self.query_one(".ft-title", Label)
            title = self.data.get("title", "?")
            text = Text(title)
            if liked:
                text.append("  ")
                text.append("\u2764\ufe0e", style="#A277FF")
            lbl.update(text)
        except Exception:
            logger.debug("Failed to update liked state in feed item")

    def compose(self) -> ComposeResult:
        title = self.data.get("title", "?")
        title_text = Text(title)
        if self.liked:
            title_text.append("  ")
            title_text.append("\u2764\ufe0e", style="#A277FF")
        channel = self.data.get("channel", "?")
        duration = fmt_duration(self.data.get("duration", 0))
        views = _short_views(self.data.get("view_count", 0))
        likes = _short_views(self.data.get("like_count", 0))
        consumed = self.data.get("consumed", False)

        with Vertical():
            title_cls = "ft-title ft-consumed" if consumed else "ft-title"
            yield Label(title_text, classes=title_cls)
            channel_text = (
                Text(channel, style="#A277FF") if self.following else Text(channel)
            )
            yield Label(channel_text, classes="ft-channel")
            yield Label(
                f"\u25b6 {views} \u00b7 \u2764\ufe0e {likes} \u00b7 {duration}",
                classes="ft-stats",
            )
        if consumed:
            self.add_class("ft-consumed-item")
