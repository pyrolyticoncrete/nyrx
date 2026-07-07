# SPDX-License-Identifier: AGPL-3.0-only

"""Two-panel episode thumbnail modal: 16:9 still left, metadata right."""

from __future__ import annotations

import logging
from datetime import datetime

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Label, Static
from textual_image.widget import Image as ThumbImage

from nyrx.config import TEMP_THUMBS
from nyrx.player import estimate_raw_height
from nyrx.screens.base_modal import BaseModal
from nyrx.widgets.base import BrailleSpinner

logger = logging.getLogger(__name__)

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


class EpisodeThumbnailModal(BaseModal):
    """Two-panel episode thumbnail modal: 16:9 still left, metadata right."""

    def __init__(self, data: dict) -> None:
        super().__init__()
        self._data = data
        self._cache_key = self._build_cache_key(data)

    @staticmethod
    def _build_cache_key(data: dict) -> str:
        tmdb_id = data.get("tmdb_id", "")
        season = data.get("season_number", 0)
        episode = data.get("episode_number", 0)
        return f"{tmdb_id}_s{season:02d}_e{episode:02d}"

    def compose(self) -> ComposeResult:
        with Container(id="ep-thumb-box"):
            with Horizontal(id="ep-thumb-panels"):
                with Vertical(id="ep-thumb-img-col"):
                    yield ThumbImage(id="ep-thumb-img")
                    with Container(id="ep-thumb-loading", classes="hidden"):
                        with Horizontal(id="ep-thumb-loading-row"):
                            yield BrailleSpinner(id="ep-thumb-spinner")
                            yield Label(
                                " fetching still...", id="ep-thumb-loading-label"
                            )
                with Vertical(id="ep-thumb-meta-col"):
                    yield Static(id="ep-thumb-title")
                    yield Static(id="ep-thumb-series")
                    yield Static(id="ep-thumb-meta")
                    yield Static(id="ep-thumb-overview")
            yield Static("[white]z[/white] [dim]close[/dim]", id="ep-thumb-hint")

    def _reflow(self) -> None:
        w = min(int(self.app.size.width * 0.50), self.app.size.width - 4, 100)
        self.query_one("#ep-thumb-box").styles.width = w
        img_col_w = min(int(self.app.size.width * 0.40), 40)
        avail_h = self.app.size.height - 4
        raw_h = estimate_raw_height(img_col_w, 9 / 16)
        if raw_h + 1 > avail_h:
            img_col_w = int(img_col_w * (avail_h - 1) / raw_h)
        self.query_one("#ep-thumb-img-col").styles.width = img_col_w

    def on_mount(self) -> None:
        super().on_mount()
        self._reflow()
        self._render_metadata()

        cache_path = TEMP_THUMBS / f"{self._cache_key}.jpg"
        if cache_path.exists():
            self._set_still(str(cache_path))
        else:
            still_path = self._data.get("still_path", "")
            if still_path:
                self.query_one("#ep-thumb-img", ThumbImage).display = False
                self.query_one("#ep-thumb-loading").remove_class("hidden")
                self._fetch_still()

    def on_resize(self, event: events.Resize) -> None:
        self._reflow()

    def _render_metadata(self) -> None:
        data = self._data

        title = data.get("title", "")
        self.query_one("#ep-thumb-title", Static).update(
            Text(title, style="bold white")
        )

        series = data.get("series_title", "")
        season = data.get("season_number", 0)
        episode = data.get("episode_number", 0)
        self.query_one("#ep-thumb-series", Static).update(
            Text(f"{series} \u2022 S{season:02d}E{episode:02d}", style="#b0b0b0")
        )

        rating = data.get("vote_average", 0) or 0
        air_date = data.get("air_date", "")

        date_str = ""
        if air_date:
            try:
                dt = datetime.strptime(air_date, "%Y-%m-%d")
                date_str = dt.strftime("%B %d, %Y")
            except (ValueError, TypeError):
                date_str = air_date

        runtime = data.get("runtime")
        runtime_str = ""
        if runtime:
            h, m = divmod(runtime, 60)
            if h and m:
                runtime_str = f"{h}h{m:02d}min"
            elif h:
                runtime_str = f"{h}h"
            elif m:
                runtime_str = f"{m}min"

        parts = []
        if rating:
            parts.append(f"\u2605 {rating:.1f}")
        if date_str:
            parts.append(date_str)
        if runtime_str:
            parts.append(runtime_str)

        sep = " \u2022 "
        self.query_one("#ep-thumb-meta", Static).update(
            f"[#b0b0b0]{sep.join(parts)}[/]" if parts else ""
        )

        raw = data.get("overview", "")
        overview = raw[:370] + ("..." if len(raw) > 370 else "")
        if overview:
            self.query_one("#ep-thumb-overview", Static).update(Text(overview))
        else:
            self.query_one("#ep-thumb-overview", Static).display = False

    @work(thread=True)
    def _fetch_still(self) -> None:
        still_path = self._data.get("still_path", "")
        if not still_path:
            self.app.call_from_thread(self._hide_loader)
            return
        import requests

        url = f"{TMDB_IMAGE_BASE}/w300{still_path}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            TEMP_THUMBS.mkdir(parents=True, exist_ok=True)
            cached = TEMP_THUMBS / f"{self._cache_key}.jpg"
            cached.write_bytes(resp.content)
            self.app.call_from_thread(self._set_still, str(cached))
        except Exception:
            logger.debug(
                "EpisodeThumbnailModal: still fetch failed for key=%s",
                self._cache_key,
            )
            self.app.call_from_thread(self._hide_loader)

    def _set_still(self, path: str) -> None:
        img = self.query_one("#ep-thumb-img", ThumbImage)
        from PIL import Image as PILImage

        pil_img: PILImage.Image = PILImage.open(path)
        if pil_img.mode == "CMYK":
            pil_img = pil_img.convert("RGB")
            pil_img.save(path, quality=90)
        img.image = pil_img
        img.display = True
        self._hide_loader()

    def _hide_loader(self) -> None:
        self.query_one("#ep-thumb-loading").add_class("hidden")

    def key_z(self, event: events.Key) -> None:
        event.stop()
        logger.debug("EpisodeThumbnailModal: key_z dismiss")
        self.dismiss(None)
