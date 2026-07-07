# SPDX-License-Identifier: AGPL-3.0-only

"""Full-size thumbnail viewer with play/download actions bound to p/d keys."""

from __future__ import annotations

import logging

from textual import events, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Label
from textual_image.widget import Image as ThumbImage

from nyrx.config import SC_THUMBS_DIR, TEMP_THUMBS
from nyrx.player import (
    estimate_raw_height,
    format_views,
    get_thumbnail_path,
)
from nyrx.player import (
    format_duration as fmt_duration,
)
from nyrx.screens.base_modal import BaseModal
from nyrx.widgets.base import BrailleSpinner

logger = logging.getLogger(__name__)


class ThumbnailModal(BaseModal):
    """Full-size thumbnail viewer with play/download actions bound to p/d keys."""

    def __init__(self, data: dict) -> None:
        super().__init__()
        self._data = data
        logger.debug(
            "ThumbnailModal: title=%s yt_id=%s",
            data.get("title", "")[:30],
            data.get("yt_id", ""),
        )

    def compose(self) -> ComposeResult:
        with Container(id="thumb-box"):
            yield ThumbImage(id="thumb-img")
            with Container(id="thumb-loading", classes="hidden"):
                with Horizontal(id="thumb-loading-row"):
                    yield BrailleSpinner(id="thumb-spinner")
                    yield Label(" fetching thumbnail...", id="thumb-loading-label")
            yield Label(self._data["title"], id="thumb-title")
            yield Label(
                f"{self._data['channel']} \u2022 {format_views(self._data['views'])} \u2022 {fmt_duration(self._data['duration'])}",
                id="thumb-meta",
            )
            yield Label(
                "[white]z[/white] [dim]close[/dim]  \u2502  [white]p[/white] [dim]play[/dim]  \u2502  [white]d[/white] [dim]download[/dim]",
                id="thumb-hint",
            )

    def _reflow(self) -> None:
        is_sc = self._data.get("source") == "soundcloud"
        aspect = 1.0 if is_sc else 9 / 16
        avail_h = self.app.size.height - 5
        target_w = min(int(self.app.size.width * 0.50), self.app.size.width - 4, 66)
        raw_h = estimate_raw_height(target_w, aspect)
        if raw_h + 1 > avail_h:
            target_w = int(target_w * (avail_h - 1) / raw_h)
        self.query_one("#thumb-box").styles.width = target_w

    def on_mount(self) -> None:
        super().on_mount()
        self._reflow()

        yt_id = self._data["yt_id"]
        if self._data.get("source") == "soundcloud":
            sc_path = SC_THUMBS_DIR / f"{yt_id}.jpg"
            if sc_path.exists():
                self._set_thumb(str(sc_path))
                return

        cache_path = TEMP_THUMBS / f"{yt_id}.jpg"
        if cache_path.exists():
            self._set_thumb(str(cache_path))
        else:
            self.query_one("#thumb-img", ThumbImage).display = False
            self.query_one("#thumb-loading").remove_class("hidden")
            self._fetch_thumbnail()

    def on_resize(self, event: events.Resize) -> None:
        self._reflow()

    @work(thread=True)
    def _fetch_thumbnail(self) -> None:
        path = get_thumbnail_path(
            self._data["yt_id"],
            self._data.get("thumbnail_url", ""),
            self._data.get("source", ""),
        )
        if path and path.exists():
            self.app.call_from_thread(self._set_thumb, str(path))
        else:
            self.app.call_from_thread(self._hide_loader)

    def _hide_loader(self) -> None:
        self.query_one("#thumb-loading").add_class("hidden")

    def _set_thumb(self, path: str) -> None:
        img = self.query_one("#thumb-img", ThumbImage)
        from PIL import Image as PILImage

        pil_img: PILImage.Image = PILImage.open(path)
        if pil_img.mode == "CMYK":
            pil_img = pil_img.convert("RGB")
            pil_img.save(path, quality=90)
        img.image = pil_img
        img.display = True
        self._hide_loader()

    def key_z(self, event: events.Key) -> None:
        event.stop()
        logger.debug("key_z: dismiss")
        self.dismiss(None)

    def key_escape(self, event: events.Key) -> None:
        # Do not also handle escape in on_key or BINDINGS: see queue modal escape bug.
        # Doing so calls dismiss() on an already-popped screen, causing a swallowed
        # ScreenStackError on the second dismiss. All escape handling lives here.
        event.stop()
        logger.debug("key_escape: dismiss")
        self.dismiss({"action": None})

    def key_p(self, event: events.Key) -> None:
        event.stop()
        logger.debug("key_p: play")
        self.dismiss({"action": "play", "data": self._data})

    def key_d(self, event: events.Key) -> None:
        event.stop()
        logger.debug("key_d: download")
        self.dismiss({"action": "download", "data": self._data})
