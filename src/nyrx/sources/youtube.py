# SPDX-License-Identifier: AGPL-3.0-only

"""YouTube source plugin implementing the Source interface.

Wraps player.py search, metadata, and playback functions behind
the abstract Source contract for use by the Textual TUI.
"""

from __future__ import annotations

import logging
from typing import Any

from nyrx.player import fetch_video_metadata, play_video_async, search_youtube
from nyrx.sources import Source

logger = logging.getLogger(__name__)


class YouTubeSource(Source):
    """Source backend for YouTube search and playback."""

    @property
    def name(self) -> str:
        return "Youtube"

    @property
    def icon(self) -> str:
        return "\u25b6"

    def handles_url(self, url: str) -> bool:
        return "youtube.com" in url or "youtu.be" in url

    def search(self, query: str, limit: int = 20) -> list[dict]:
        results = search_youtube(query, limit=limit)
        logger.debug("search: query=%s limit=%d results=%d", query, limit, len(results))
        return results

    def fetch_metadata(self, url: str) -> dict | None:
        result = fetch_video_metadata(url)
        logger.debug("fetch_metadata: url=%s found=%s", url, bool(result))
        return result

    def download_params(self, data: dict) -> dict | None:
        return {
            "yt_id": data["yt_id"],
            "title": data.get("title", ""),
            "url": data.get("url", ""),
            "source": "youtube",
        }

    def play_params(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> dict[str, Any]:
        """Build youtube.play_video_async kwargs from search result data.

        Constructs the YouTube watch URL and passes through playback options.
        """
        logger.debug(
            "play_params: yt_id=%s audio_only=%s fmt=%s start_pos=%s",
            data.get("yt_id", ""),
            audio_only,
            ytdl_format,
            start_pos,
        )
        return {
            "yt_id": data["yt_id"],
            "title": data.get("title", ""),
            "url": f"https://www.youtube.com/watch?v={data['yt_id']}",
            "audio_only": audio_only,
            "ytdl_format": ytdl_format,
            "start_pos": start_pos,
        }

    def play(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> Any:
        """Play a YouTube video synchronously. Returns mpv IPC or None."""
        params = self.play_params(
            data,
            audio_only=audio_only,
            ytdl_format=ytdl_format,
            start_pos=start_pos,
        )
        params["channel"] = data.get("channel", "")
        params["uploader_id"] = data.get("uploader_id", "")
        params["permalink"] = data.get("permalink", "")
        params["source"] = data.get("source", "youtube")
        ipc = play_video_async(**params)
        logger.debug("play: yt_id=%s ipc=%s", data.get("yt_id", ""), bool(ipc))
        return ipc

    def get_commands(self, **context: Any) -> list[tuple[str, str, str | None]]:
        quality = context.get("quality", "1080p")
        return [
            ("Open in browser", "action_open_browser", "b"),
            ("Copy video URL", "action_copy_url", None),
            ("Set default streaming quality", "action_set_quality", quality),
        ]
