# SPDX-License-Identifier: AGPL-3.0-only

"""Unified data models for the nyrx TUI.

Defines shared ``PlaybackState`` and the Phase 0 typed model: ``MediaRequest``
(discriminated by ``MediaKind``) with typed payloads ``MovieInfo`` / ``EpisodeInfo``
/ ``RadioInfo``.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


# ── Phase 0: Canonical typed model ────────────────────────────────────────


class MediaKind(Enum):
    MOVIE = "movie"
    EPISODE = "episode"
    AUDIO_TRACK = "audio_track"
    RADIO_STATION = "radio_station"


@dataclass
class EpisodeInfo:
    tmdb_id: int
    season_number: int
    episode_number: int
    series_title: str
    episode_title: str
    rating: float = 0.0
    vote_count: int = 0
    year: str = ""
    poster_path: str = ""
    overview: str = ""


@dataclass
class MovieInfo:
    tmdb_id: int
    title: str
    tagline: str = ""
    rating: float = 0.0
    vote_count: int = 0
    year: str = ""
    poster_path: str = ""
    overview: str = ""
    runtime: int = 0
    genres: list[str] = field(default_factory=list)


@dataclass
class RadioInfo:
    countrycode: str = ""


MediaPayload = MovieInfo | EpisodeInfo | RadioInfo | None


@dataclass
class MediaRequest:
    yt_id: str
    title: str
    channel: str = ""
    source: str = "youtube"
    kind: MediaKind = MediaKind.AUDIO_TRACK
    payload: MediaPayload = None
    audio_only: bool = False
    start_pos: float | None = None
    data: dict | None = None

    @classmethod
    def from_dict(
        cls,
        data: dict,
        *,
        source: str | None = None,
        audio_only: bool | None = None,
        kind: MediaKind | None = None,
    ) -> MediaRequest:
        source = source or data.get("source", "youtube")
        tmdb_id: int | None = data.get("tmdb_id") or (
            int(yt_id[5:])
            if (yt_id := data.get("yt_id", "")).startswith("tmdb_")
            else None
        )
        yt_id = data.get("yt_id") or (f"tmdb_{tmdb_id}" if tmdb_id else "")
        if not data.get("yt_id") and (tmdb_id or yt_id):
            logger.debug(
                "from_dict: synthesized yt_id=%r (not in data) source=%s tmdb_id=%s",
                yt_id,
                source,
                tmdb_id,
            )

        if kind is None:
            media_type = data.get("media_type", "")
            season = (
                data.get("season_number")
                if "season_number" in data
                else data.get("season")
            )
            if source == "tv_movies" and media_type == "tv" and season is not None:
                kind = MediaKind.EPISODE
            elif source == "tv_movies":
                kind = MediaKind.MOVIE
            elif source == "soundcloud":
                kind = MediaKind.AUDIO_TRACK
            elif source == "radio":
                kind = MediaKind.RADIO_STATION
            else:
                kind = MediaKind.AUDIO_TRACK
            logger.debug(
                "MediaRequest.from_dict: heuristic kind=%s (no explicit kind given)",
                kind,
            )

        payload: MediaPayload = None
        if kind == MediaKind.EPISODE:
            if tmdb_id is None:
                raise ValueError("EPISODE request missing tmdb_id")
            payload = EpisodeInfo(
                tmdb_id=tmdb_id,
                season_number=int(data.get("season_number", data.get("season", 0))),
                episode_number=data.get("episode_number", data.get("episode", 1)),
                series_title=data.get("series_title", ""),
                episode_title=data.get("title", ""),
                rating=data.get("rating") or 0.0,
                vote_count=data.get("vote_count") or 0,
                year=data.get("year", ""),
                poster_path=data.get("poster_path") or data.get("poster", ""),
                overview=data.get("overview", ""),
            )
        elif kind == MediaKind.MOVIE:
            if tmdb_id is None:
                raise ValueError("MOVIE request missing tmdb_id")
            genres = data.get("genres", [])
            if isinstance(genres, str):
                try:
                    genres = json.loads(genres)
                except (json.JSONDecodeError, TypeError):
                    genres = []
            payload = MovieInfo(
                tmdb_id=tmdb_id,
                title=data.get("title", ""),
                tagline=data.get("tagline") or "",
                rating=data.get("rating") or 0.0,
                vote_count=data.get("vote_count") or 0,
                year=data.get("year", ""),
                poster_path=data.get("poster_path") or data.get("poster", ""),
                overview=data.get("overview", ""),
                runtime=data.get("runtime") or 0,
                genres=genres,
            )
        elif kind == MediaKind.RADIO_STATION:
            payload = RadioInfo(countrycode=data.get("countrycode", ""))
        else:
            payload = None

        return cls(
            yt_id=yt_id,
            title=data.get("title", ""),
            channel=data.get("channel", ""),
            source=source,
            kind=kind,
            payload=payload,
            audio_only=audio_only
            if audio_only is not None
            else (
                True
                if source in ("soundcloud", "radio")
                else data.get("audio_only", False)
            ),
            start_pos=data.get("start_pos"),
            data=data,
        )


@dataclass
class PlaybackState:
    """Snapshot of mpv playback state pushed to now-playing widgets."""

    position: float = 0.0
    duration: float = 0.0
    paused: bool = False
    buffering: bool = False
