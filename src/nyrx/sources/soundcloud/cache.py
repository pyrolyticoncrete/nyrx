# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import time
from collections import deque
from datetime import UTC, datetime

from nyrx.helpers import db_scope

from .api import (
    fetch_artist_collections,
    fetch_artist_likes,
    fetch_artist_profile,
    fetch_artist_uploads,
)
from .db import (
    _get_db,
    _mark_cached,
    save_artist_collections,
    save_artist_likes,
    save_artist_profile,
    save_artist_uploads,
)

logger = logging.getLogger(__name__)

CACHE_QUEUE: deque[str] = deque()


def enqueue_artist_cache(artist_id: str) -> None:
    """Add artist to cache queue if not already queued."""
    if artist_id not in CACHE_QUEUE:
        CACHE_QUEUE.append(artist_id)
        logger.debug(
            "enqueue_artist_cache: artist_id=%s queue_size=%s",
            artist_id,
            len(CACHE_QUEUE),
        )


def process_artist_cache(artist_id: str) -> dict:
    """Sequential per-artist cache fetch. Returns result dict:
    {profile: bool, collections: bool, uploads: bool, likes: bool}
    True = fetched OK, False = failed after retries.

    For uploads: detects partial initial caches (e.g. app closed mid-fetch)
    via profile track_count vs cached row count. When partial detected,
    skip_dedup=True forces a full fetch of all pages. Subsequent refetches
    use normal dedup (1-2 API calls)."""
    result = {"profile": False, "collections": False, "uploads": False, "likes": False}

    # 1. Profile
    profile = fetch_artist_profile(artist_id)
    if profile:
        save_artist_profile(artist_id, profile)
        result["profile"] = True
    time.sleep(0.5)

    # 2. Collections (always full fetch: cheap 2-3 pages)
    collections = fetch_artist_collections(artist_id)
    if collections is not None:
        result["collections"] = True
        if collections:
            save_artist_collections(artist_id, collections)
    time.sleep(0.5)

    # 3. Uploads: partial-cache detection
    skip_dedup = False
    if profile:
        track_count = profile.get("track_count", 0)
        if track_count > 0:
            with db_scope(_get_db) as db:
                cached_count = db.execute(
                    "SELECT COUNT(*) FROM artist_uploads WHERE artist_id = ?",
                    (artist_id,),
                ).fetchone()[0]
            skip_dedup = cached_count < track_count * 0.8
    uploads = fetch_artist_uploads(artist_id, skip_dedup=skip_dedup)
    if uploads is not None:
        result["uploads"] = True
        if uploads:
            save_artist_uploads(artist_id, uploads)
    time.sleep(0.5)

    # 4. Likes (always full fetch: max 2 pages)
    likes = fetch_artist_likes(artist_id)
    if likes is not None:
        result["likes"] = True
        if likes:
            save_artist_likes(artist_id, likes)

    # Record successful fetch timestamps in cache status table
    with db_scope(_get_db) as db:
        now = datetime.now(UTC).isoformat()
        if result["profile"]:
            _mark_cached(db, artist_id, "profile", now)
        if result["collections"]:
            _mark_cached(db, artist_id, "collections", now)
        if result["uploads"]:
            _mark_cached(db, artist_id, "uploads", now)
        if result["likes"]:
            _mark_cached(db, artist_id, "likes", now)
        db.commit()

    logger.debug(
        "process_artist_cache: artist_id=%s skip_dedup=%s result=%s",
        artist_id,
        skip_dedup,
        result,
    )
    return result
