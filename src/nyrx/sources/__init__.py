# SPDX-License-Identifier: AGPL-3.0-only

"""Abstract base interfaces for media sources.

Defines the `Source` plugin contract that all media backends (YouTube,
TV/movie providers) must implement, and the `ResultItem` dataclass shared
across sources.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class Source(ABC):
    """Abstract plugin interface for media sources (YouTube, TV/movie providers, etc.)."""

    @property
    @abstractmethod
    def name(self) -> str:
        """Human-readable source name displayed in the UI."""

    @property
    @abstractmethod
    def icon(self) -> str:
        """Single-character icon rendered next to the source name."""

    @abstractmethod
    def handles_url(self, url: str) -> bool:
        """Return True if this source can handle the given URL."""

    @abstractmethod
    def search(self, query: str, limit: int = 20) -> list[dict]:
        """Search the source for the given query string.

        Args:
            query: Free-text search string.
            limit: Maximum number of results to return.

        Returns:
            List of result dicts with keys matching Result fields.
        """

    @abstractmethod
    def fetch_metadata(self, url: str) -> dict | None:
        """Fetch metadata for a specific URL (e.g. YouTube video page).

        Args:
            url: Full URL to resolve.

        Returns:
            Dict with keys matching Result fields, or None if resolution failed.
        """

    @abstractmethod
    def download_params(self, data: dict) -> dict | None:
        """Return download params dict or None if download not supported.

        Args:
            data: Result dict from search() or fetch_metadata().

        Returns:
            Dict with at least ``yt_id``, ``title``, and ``url`` keys.
            Return ``None`` to indicate this source does not support
            downloads at all.
        """

    @abstractmethod
    def play_params(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> dict[str, Any]:
        """Build keyword arguments for youtube.play_video_async from a result dict.

        Args:
            data: Result dict from search() or fetch_metadata().
            audio_only: If True, request audio-only playback.
            ytdl_format: yt-dlp format string override.
            start_pos: Seek position in seconds to start playback from.

        Returns:
            Kwargs dict suitable for passing to play_video_async(**...).
        """

    @abstractmethod
    def play(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> Any:
        """Start playback for the given track data.

        Synchronous sources (YouTube, Radio) return an mpv IPC object
        (or None on failure).  Asynchronous sources (SoundCloud) return
        a ``(ipc, enrichment_dict)`` tuple consumed by the caller's
        async pipeline.
        """

    def get_commands(self, **context: Any) -> list[tuple[str, str, str | None]]:
        """Return list of (label, action_name, key_hint) tuples for the command palette.

        Override in subclasses to expose source-specific commands.
        """
        return []

    def current_server_display(self) -> str:
        """Return a human-readable display name for the current server/mode.

        Override in subclasses that have server/mode selection (e.g. TVMoviesSource).
        """
        return "Auto"

    def ensure_index_loaded(self) -> Any:
        """Ensure the source's index/data is loaded, returning it.

        Override in subclasses that need lazy index loading (e.g. RadioSource).
        """
        return None

    def cycle_server(self) -> str:
        """Cycle to the next server/mode, returning the new mode name.

        Override in subclasses that have server/mode selection (e.g. TVMoviesSource).
        """
        return "auto"
