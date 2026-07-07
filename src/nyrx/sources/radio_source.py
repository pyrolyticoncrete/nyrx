# SPDX-License-Identifier: AGPL-3.0-only

"""Radio station media source plugin."""

from __future__ import annotations

import logging
from typing import Any

from nyrx.player import play_video_async
from nyrx.sources import Source
from nyrx.sources.radio_index import StationIndex

logger = logging.getLogger(__name__)


class RadioSource(Source):
    """Source backend for radio station streaming."""

    def __init__(self) -> None:
        self._station_index = StationIndex()

    @property
    def name(self) -> str:
        return "Radio"

    @property
    def icon(self) -> str:
        return "\u266b"

    def handles_url(self, url: str) -> bool:
        return False

    def search(self, query: str, limit: int = 20) -> list[dict]:
        return []

    def fetch_metadata(self, url: str) -> dict | None:
        return None

    def download_params(self, data: dict) -> dict | None:
        return None

    def play_params(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> dict[str, Any]:
        _ = audio_only, ytdl_format, start_pos
        params = {
            "yt_id": data.get("stationuuid", data["yt_id"]),
            "title": data.get("name", data.get("title", "")),
            "url": data.get("url_resolved", data.get("url", "")),
            "audio_only": True,
            "channel": data.get("channel", ""),
            "uploader_id": data.get("uploader_id", ""),
            "permalink": data.get("permalink", ""),
            "source": "radio",
        }
        logger.debug(
            "play_params: id=%s title=%s url=%s",
            params["yt_id"],
            params["title"],
            params["url"],
        )
        return params

    def get_commands(self, **context: Any) -> list[tuple[str, str, str | None]]:
        return []

    def play(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> Any:
        """Play a radio stream synchronously. Returns mpv IPC or None."""
        params = self.play_params(data, audio_only=True)
        ipc = play_video_async(**params)
        logger.debug("play: id=%s ipc=%s", data.get("yt_id", ""), bool(ipc))
        return ipc

    # ---- convenience helpers for the TUI -----------------------------------

    def ensure_index_loaded(self) -> StationIndex:
        self._station_index.load()
        logger.debug(
            "ensure_index_loaded: stations=%d", len(self._station_index.stations)
        )
        return self._station_index
