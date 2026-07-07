# SPDX-License-Identifier: AGPL-3.0-only

"""Two-panel TV/movie thumbnail modal: poster left, metadata right."""

from __future__ import annotations

import logging

from rich.text import Text
from textual import events, work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal, Vertical
from textual.widgets import Label, Static
from textual_image.widget import Image as ThumbImage

from nyrx.config import TEMP_THUMBS, TV_THUMBS_DIR
from nyrx.player import estimate_raw_height
from nyrx.screens.base_modal import BaseModal
from nyrx.widgets.base import BrailleSpinner

logger = logging.getLogger(__name__)

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


class TVThumbnailModal(BaseModal):
    """Two-panel TV/movie thumbnail modal: poster left, metadata right."""

    def __init__(self, data: dict) -> None:
        super().__init__()
        self._data = data
        self._tmdb_id = data.get("tmdb_id", "")

    def compose(self) -> ComposeResult:
        with Container(id="tv-thumb-box"):
            with Horizontal(id="tv-thumb-panels"):
                with Vertical(id="tv-thumb-poster-col"):
                    yield ThumbImage(id="tv-thumb-img")
                    with Container(id="tv-thumb-loading", classes="hidden"):
                        with Horizontal(id="tv-thumb-loading-row"):
                            yield BrailleSpinner(id="tv-thumb-spinner")
                            yield Label(
                                " fetching poster...", id="tv-thumb-loading-label"
                            )
                with Vertical(id="tv-thumb-meta-col"):
                    yield Static(id="tv-thumb-title")
                    yield Static(id="tv-thumb-tagline")
                    yield Static(id="tv-thumb-rating")
                    yield Static(id="tv-thumb-genres")
                    yield Static(id="tv-thumb-overview")
            yield Static("[white]z[/white] [dim]close[/dim]", id="tv-thumb-hint")

    def _reflow(self) -> None:
        w = min(int(self.app.size.width * 0.50), self.app.size.width - 4, 72)
        self.query_one("#tv-thumb-box").styles.width = w
        poster_w = min(int(self.app.size.width * 0.22), 30)
        avail_h = self.app.size.height - 4
        raw_h = estimate_raw_height(poster_w, 278 / 185)
        if raw_h + 1 > avail_h:
            poster_w = int(poster_w * (avail_h - 1) / raw_h)
        self.query_one("#tv-thumb-poster-col").styles.width = poster_w

    def on_mount(self) -> None:
        super().on_mount()
        self._reflow()
        self._render_metadata()

        tmdb_id = self._tmdb_id
        if not tmdb_id:
            return

        tv_path = TV_THUMBS_DIR / f"{tmdb_id}.jpg"
        tmp_path = TEMP_THUMBS / f"{tmdb_id}.jpg"

        if tv_path.exists():
            self._set_poster(str(tv_path))
        elif tmp_path.exists():
            self._set_poster(str(tmp_path))
        else:
            self.query_one("#tv-thumb-img", ThumbImage).display = False
            self.query_one("#tv-thumb-loading").remove_class("hidden")
            self._fetch_poster()

    def on_resize(self, event: events.Resize) -> None:
        self._reflow()

    def _render_metadata(self) -> None:
        data = self._data
        media_type = data.get("media_type", "")

        title = data.get("title", "")
        self.query_one("#tv-thumb-title", Static).update(
            Text(title, style="bold white")
        )

        if media_type == "movie":
            tagline = data.get("tagline") or ""
            if tagline:
                self.query_one("#tv-thumb-tagline", Static).update(
                    Text(tagline, style="italic #808080")
                )
            else:
                self.query_one("#tv-thumb-tagline", Static).display = False
        else:
            self.query_one("#tv-thumb-tagline", Static).display = False

        rating = data.get("rating", 0) or 0
        vote_count = data.get("vote_count", 0) or 0
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
            vote_str = f"({vote_count})" if vote_count else ""
            self.query_one("#tv-thumb-rating", Static).update(
                f"[#b0b0b0]\u2605 {rating:.1f}{vote_str} \u00b7 {year}{runtime_str}[/]"
            )
        else:
            vote_str = f"({vote_count})" if vote_count else ""
            self.query_one("#tv-thumb-rating", Static).update(
                f"[#b0b0b0]\u2605 {rating:.1f}{vote_str} \u00b7 {year}[/]"
            )

        genre_ids = data.get("genre_ids", [])
        if genre_ids:
            from nyrx.sources.tv_movies.tmdb_cache import genre_names

            names = genre_names(genre_ids)
            if names:
                self.query_one("#tv-thumb-genres", Static).update(
                    " \u00b7 ".join(names[:3])
                )
            else:
                self.query_one("#tv-thumb-genres", Static).display = False
        else:
            self.query_one("#tv-thumb-genres", Static).display = False

        raw = data.get("overview", "")
        overview = raw[:370] + ("..." if len(raw) > 370 else "")
        if overview:
            self.query_one("#tv-thumb-overview", Static).update(Text(overview))
        else:
            self.query_one("#tv-thumb-overview", Static).display = False

    @work(thread=True)
    def _fetch_poster(self) -> None:
        poster_path = self._data.get("poster", "") or self._data.get("poster_path", "")
        if not poster_path:
            self.app.call_from_thread(self._hide_loader)
            return
        import requests

        url = f"{TMDB_IMAGE_BASE}/w342{poster_path}"
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            TEMP_THUMBS.mkdir(parents=True, exist_ok=True)
            cached = TEMP_THUMBS / f"{self._tmdb_id}.jpg"
            cached.write_bytes(resp.content)
            self.app.call_from_thread(self._set_poster, str(cached))
        except Exception:
            logger.debug(
                "TVThumbnailModal: poster fetch failed for tmdb_id=%s", self._tmdb_id
            )
            self.app.call_from_thread(self._hide_loader)

    def _set_poster(self, path: str) -> None:
        img = self.query_one("#tv-thumb-img", ThumbImage)
        from PIL import Image as PILImage

        pil_img: PILImage.Image = PILImage.open(path)
        if pil_img.mode == "CMYK":
            pil_img = pil_img.convert("RGB")
            pil_img.save(path, quality=90)
        img.image = pil_img
        img.display = True
        self._hide_loader()

    def _hide_loader(self) -> None:
        self.query_one("#tv-thumb-loading").add_class("hidden")

    def key_z(self, event: events.Key) -> None:
        event.stop()
        logger.debug("TVThumbnailModal: key_z dismiss")
        self.dismiss(None)
