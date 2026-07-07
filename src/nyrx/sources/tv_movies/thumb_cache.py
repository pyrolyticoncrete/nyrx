# SPDX-License-Identifier: AGPL-3.0-only

"""Poster download and caching for TV/Movies bookmarks.

Caches to ``~/.cache/nyrx/tv_thumbs/{tmdb_id}.jpg`` at bookmark time.
"""

from __future__ import annotations

import logging
from pathlib import Path

import requests

from nyrx.config import TV_THUMBS_DIR

logger = logging.getLogger(__name__)

TMDB_IMAGE_BASE = "https://image.tmdb.org/t/p"


def cache_tv_poster(tmdb_id: int, poster_path: str) -> Path | None:
    """Download poster from TMDB to local cache.

    Uses ``w342`` size per decision #6.
    Returns the cached file path, or None on failure.
    """
    if not poster_path:
        return None
    TV_THUMBS_DIR.mkdir(parents=True, exist_ok=True)
    cached = TV_THUMBS_DIR / f"{tmdb_id}.jpg"
    if cached.exists():
        return cached
    url = f"{TMDB_IMAGE_BASE}/w342{poster_path}"
    try:
        resp = requests.get(url, timeout=10)
        resp.raise_for_status()
        cached.write_bytes(resp.content)
        logger.debug("cache_tv_poster: cached tmdb_id=%s", tmdb_id)
        return cached
    except Exception:
        logger.debug("cache_tv_poster: failed for tmdb_id=%s", tmdb_id)
        return None
