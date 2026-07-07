# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from datetime import UTC, datetime

from nyrx.helpers import db_scope

from .api import (
    _THUMB_CACHE,
    ProfileResolveError,
    _cache_thumbnail,
    _resolve_sc_track,
    fetch_artist_likes,
    resolve_sc_user,
)
from .db import _get_db, save_sc_likes

logger = logging.getLogger(__name__)


def is_sc_liked(yt_id: str, liked: list[dict]) -> bool:
    return any(t.get("yt_id") == yt_id for t in liked)


def toggle_sc_like(yt_id: str, data: dict, liked: list[dict]) -> bool:
    for i, t in enumerate(liked):
        if t.get("yt_id") == yt_id:
            liked.pop(i)
            save_sc_likes(liked)
            thumb_path = _THUMB_CACHE / f"{yt_id}.jpg"
            if thumb_path.exists():
                thumb_path.unlink()
            logger.debug(
                "toggle_sc_like: unlike yt_id=%s title=%s",
                yt_id,
                t.get("title", "")[:20],
            )
            return False

    needs_resolve = (
        not (data.get("views") or data.get("view_count"))
        and not (data.get("likes_count") or data.get("like_count"))
        and not data.get("thumbnail_url")
    )
    if needs_resolve:
        resolved = _resolve_sc_track(yt_id)
        if resolved:
            for key in (
                "views",
                "likes_count",
                "thumbnail_url",
                "uploader_id",
                "permalink",
            ):
                val = resolved.get(key)
                if val:
                    data[key] = val

    _cache_thumbnail(yt_id, data.get("thumbnail_url", ""))

    liked.insert(
        0,
        {
            "yt_id": yt_id,
            "title": data.get("title", ""),
            "channel": data.get("channel", ""),
            "duration": data.get("duration", 0),
            "views": data.get("views") or data.get("view_count") or 0,
            "likes_count": data.get("likes_count") or data.get("like_count") or 0,
            "thumbnail_url": data.get("thumbnail_url", ""),
            "url": data.get("url", ""),
            "description": data.get("description", ""),
            "genre": data.get("genre", ""),
            "uploader_id": data.get("uploader_id", ""),
            "permalink": data.get("permalink", ""),
            "source": "soundcloud",
            "liked_at": datetime.now(UTC).isoformat(),
        },
    )
    save_sc_likes(liked)
    logger.debug(
        "toggle_sc_like: like yt_id=%s title=%s", yt_id, data.get("title", "")[:20]
    )
    return True


def sync_liked_from_profile(
    profile_url: str, local_liked: list[dict]
) -> tuple[list[dict], int]:
    """Fetch all liked tracks from a SoundCloud profile and merge add-only.

    Resolves the profile URL to a user ID, fetches all liked tracks via
    cursor pagination (reusing :func:`fetch_artist_likes`), then appends
    any remote tracks whose ``track_id`` is not already present in
    *local_liked* (matched against ``yt_id``).

    Thumbnails for new tracks are cached via ``_cache_thumbnail``.

    Returns ``(merged_list, count_of_new_tracks)``.  On any failure the
    original *local_liked* list is returned unchanged with count ``0``.

    Raises :class:`~sources.soundcloud.api.ProfileResolveError` if the
    profile URL cannot be resolved.
    """
    try:
        return _sync_liked_impl(profile_url, local_liked)
    except ProfileResolveError:
        raise
    except Exception:
        logger.exception("sync_liked_from_profile: failed for %s", profile_url)
        return local_liked, 0


def _sync_liked_impl(
    profile_url: str, local_liked: list[dict]
) -> tuple[list[dict], int]:
    user = resolve_sc_user(profile_url)
    if not user:
        raise ProfileResolveError(f"Could not resolve profile URL: {profile_url}")
    user_id = str(user.get("id", ""))
    if not user_id:
        raise ProfileResolveError(
            f"Profile URL resolved but missing user ID: {profile_url}"
        )

    remote = fetch_artist_likes(user_id, max_tracks=0)
    if remote is None:
        return local_liked, 0

    existing_ids = {t.get("yt_id", "") for t in local_liked}
    new_tracks: list[dict] = []
    for t in remote:
        tid = t.get("track_id", "")
        if tid and tid not in existing_ids:
            existing_ids.add(tid)
            _cache_thumbnail(tid, t.get("thumbnail_url", ""))
            liked_ts = t.get("liked_at") or datetime.now(UTC).isoformat()
            new_tracks.append(
                {
                    "yt_id": tid,
                    "title": t.get("title", ""),
                    "channel": t.get("channel", ""),
                    "duration": t.get("duration", 0),
                    "views": t.get("view_count", 0),
                    "likes_count": t.get("like_count", 0),
                    "thumbnail_url": t.get("thumbnail_url", ""),
                    "url": t.get("url", ""),
                    "uploader_id": t.get("uploader_id", ""),
                    "permalink": t.get("permalink", ""),
                    "source": "soundcloud",
                    "liked_at": liked_ts,
                }
            )

    if not new_tracks:
        return local_liked, 0

    # INSERT new tracks only: never DELETE existing rows
    with db_scope(_get_db) as db:
        for item in new_tracks:
            db.execute(
                "INSERT OR IGNORE INTO liked_tracks "
                "(track_id, title, channel, duration, url, uploader_id, permalink, view_count, like_count, thumbnail_url, liked_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    item.get("yt_id", ""),
                    item.get("title", ""),
                    item.get("channel", ""),
                    item.get("duration", 0),
                    item.get("url", ""),
                    item.get("uploader_id", ""),
                    item.get("permalink", ""),
                    item.get("views", 0),
                    item.get("likes_count", 0),
                    item.get("thumbnail_url", ""),
                    item.get("liked_at", ""),
                ),
            )
        db.commit()

    merged = list(local_liked) + new_tracks
    logger.debug(
        "sync_liked_from_profile: profile_url=%s user_id=%s remote=%s new=%s total=%s",
        profile_url,
        user_id,
        len(remote),
        len(new_tracks),
        len(merged),
    )
    return merged, len(new_tracks)
