# SPDX-License-Identifier: AGPL-3.0-only

"""SoundCloud media source plugin."""

from __future__ import annotations

from typing import Any

from nyrx.player import fetch_video_metadata, play_video_async
from nyrx.sources import Source
from nyrx.sources.soundcloud import search_soundcloud
from nyrx.sources.soundcloud.api import enrich_sc_track


class SoundCloudSource(Source):
    """Source backend for SoundCloud search and playback."""

    @property
    def name(self) -> str:
        return "Soundcloud"

    @property
    def icon(self) -> str:
        return "\u266b"

    def handles_url(self, url: str) -> bool:
        return "soundcloud.com" in url

    def search(self, query: str, limit: int = 20) -> list[dict]:
        return search_soundcloud(query, limit)

    def fetch_metadata(self, url: str) -> dict | None:
        if "soundcloud.com" in url:
            info = fetch_video_metadata(url)
            if info:
                info["source"] = "soundcloud"
                info["url"] = url
            return info
        return None

    def download_params(self, data: dict) -> dict | None:
        return {
            "yt_id": data["yt_id"],
            "title": data.get("title", ""),
            "url": data.get("url", ""),
            "source": "soundcloud",
            "audio_only": True,
        }

    def play_params(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> dict[str, Any]:
        _ = audio_only  # always audio
        return {
            "yt_id": data["yt_id"],
            "title": data.get("title", ""),
            "url": data.get("url", f"https://soundcloud.com/tracks/{data['yt_id']}"),
            "audio_only": True,
            "ytdl_format": ytdl_format,
            "start_pos": start_pos,
            "waveform_url": data.get("waveform_url", ""),
            "channel": data.get("channel", ""),
            "uploader_id": data.get("uploader_id", ""),
            "permalink": data.get("permalink", ""),
            "source": "soundcloud",
        }

    def get_commands(self, **context: Any) -> list[tuple[str, str, str | None]]:
        return [
            (
                "Set trending region",
                "action_set_trending_region",
                context.get("trending_region", "us"),
            ),
        ]

    def play(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> tuple[Any, dict]:
        """Play a SoundCloud track.

        This is a synchronous worker meant to be called from a background
        thread.  Returns ``(mpv_ipc, enrichment_dict)`` for the caller to
        dispatch on the main thread.
        """
        params = self.play_params(
            data,
            audio_only=True,
            ytdl_format=ytdl_format,
            start_pos=start_pos,
        )
        params.pop("waveform_url", None)
        resolved = enrich_sc_track(data)
        ipc = play_video_async(**params)
        return ipc, resolved
