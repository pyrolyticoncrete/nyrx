# SPDX-License-Identifier: AGPL-3.0-only

"""TMDB API client with key rotation, proxy fallback, and disk caching.

Replaces the old OMDB-backed ``series_cache.py``. Uses the key pool
from ``keys.json`` (runtime copy at ``~/.config/nyrx/keys.json``,
seeded from bundled ``data/keys.json`` on first access), falling back to
the videasy proxy for read-only queries (search, find, details).

Cache is stored at ``~/.cache/nyrx/tmdb_cache.json`` with
per-entry TTL (7 days for series info, 30 days for search results).
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from datetime import date
from pathlib import Path
from typing import Any

import requests

from nyrx.config import CACHE_DIR, KEYS_PATH
from nyrx.sources.tv_movies.helpers import HTTP_HEADERS

logger = logging.getLogger(__name__)

CACHE_FILE = CACHE_DIR / "tmdb_cache.json"

SERIES_TTL = 7 * 86400
MOVIE_TTL = 30 * 86400
TRENDING_TTL = 24 * 3600
POPULAR_TTL = 24 * 3600
GENRE_TTL = 30 * 86400

_CACHE_LOCK = threading.Lock()

_KEYS: list[str] = []
_PROXY: str | None = None

_KEYS_PATH = KEYS_PATH
_BUNDLED_KEYS = Path(__file__).parent.parent.parent / "data" / "keys.json"


def _seed_keys(path: Path) -> None:
    """Ensure runtime keys.json has bundled keys, without overwriting user data.

    Handles three scenarios:
    1. Runtime file missing → copy bundled to runtime path (one-time seed).
    2. Runtime file exists with keys → nothing to do.
    3. Runtime file exists but has no keys (created by proxy discovery) → merge
       bundled keys in so the app can function until hotswap provides OTA keys.
    """
    if not _BUNDLED_KEYS.is_file():
        return
    try:
        if not path.is_file():
            path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(_BUNDLED_KEYS, path)
            return
        existing = json.loads(path.read_text())
        if existing.get("tmdb_keys") or existing.get("user_tmdb_keys"):
            return
        bundled = json.loads(_BUNDLED_KEYS.read_text())
        if bundled.get("tmdb_keys"):
            existing["tmdb_keys"] = bundled["tmdb_keys"]
            path.write_text(json.dumps(existing, indent=2))
    except Exception:
        logger.debug("_seed_keys: failed to seed keys from bundled data")


def load_keys(keys_path: str | Path | None = None) -> None:
    """Load TMDB keys and proxy from ``~/.config/nyrx/keys.json``.

    If the runtime config path doesn't exist yet it is seeded from the
    bundled ``data/keys.json`` (one-time copy).  User-provided keys in
    ``user_tmdb_keys`` are prepended before OTA keys so they take priority.
    """
    global _KEYS, _PROXY
    if keys_path is None:
        keys_path = _KEYS_PATH
        _seed_keys(keys_path)
    try:
        with open(keys_path) as f:
            data = json.load(f)
        user_keys = data.get("user_tmdb_keys", [])
        ota_keys = data.get("tmdb_keys", [])
        _KEYS = user_keys + ota_keys
        _PROXY = data.get("tmdb_proxy")
    except Exception:
        logger.debug("Failed to load TMDB keys, using empty defaults")
        _KEYS = []
        _PROXY = None


def _ensure_keys() -> None:
    if not _KEYS:
        load_keys()


def refresh_proxy() -> None:
    """Discover current proxy URL and update in-memory ``_PROXY`` + config.

    Meant to be called once from a background thread at startup.  Skips
    if checked within the last 7 days (``proxy_last_checked`` in keys.json).
    """
    global _PROXY

    keys_path = _KEYS_PATH
    try:
        cfg = json.loads(keys_path.read_text())
        last = cfg.get("proxy_last_checked", 0)
        if time.time() - last < 7 * 86400:
            logger.debug(
                "proxy_discovery: skipping (checked %.1fh ago)",
                (time.time() - last) / 3600,
            )
            return
    except Exception:
        logger.debug(
            "refresh_proxy: failed to read proxy_last_checked from keys.json, will re-discover"
        )

    from .proxy_discovery import discover_proxy, update_proxy_config

    found = discover_proxy()
    if not found:
        return
    if found == _PROXY:
        update_proxy_config()
        return
    _PROXY = found
    update_proxy_config()
    logger.info("proxy_discovery: switched proxy to %s", found)


# ---------------------------------------------------------------------------
# Disk cache helpers
# ---------------------------------------------------------------------------


def _read_cache() -> dict[str, Any]:
    try:
        if CACHE_FILE.exists():
            return json.loads(CACHE_FILE.read_text())
    except Exception:
        logger.debug("Failed to read TMDB cache file")
    return {}


def _write_cache(cache: dict) -> None:
    """Persist the cache atomically (temp file + rename).

    The temp file lives in the same directory so ``os.replace`` is atomic
    on POSIX: readers either see the old file or the new one, never a
    partially-written JSON blob.  Callers must hold ``_CACHE_LOCK``.
    """
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    tmp = CACHE_FILE.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cache, indent=2))
    os.replace(tmp, CACHE_FILE)


def _cache_get(key: str, max_age: int) -> Any | None:
    with _CACHE_LOCK:
        cache = _read_cache()
        entry = cache.get(key)
        if entry and (time.time() - entry.get("ts", 0)) < max_age:
            return entry.get("data")
    return None


def _cache_set(key: str, data: Any) -> None:
    with _CACHE_LOCK:
        cache = _read_cache()
        cache[key] = {"ts": time.time(), "data": data}
        _write_cache(cache)


# ---------------------------------------------------------------------------
# Low-level request with key rotation + proxy
# ---------------------------------------------------------------------------

_KEY_INDEX = 0


def _request(path: str, params: dict | None = None) -> dict | None:
    """Make a TMDB API request.

    Tries the proxy first (if available), then falls back to direct
    API calls with key rotation.
    """
    global _KEY_INDEX
    _ensure_keys()

    if _PROXY:
        url = f"{_PROXY}{path}"
        try:
            r = requests.get(url, params=params, timeout=10, headers=HTTP_HEADERS)
            if r.ok:
                return r.json()
        except Exception:
            logger.debug("TMDB proxy request failed, falling back to direct API")

    for _ in range(len(_KEYS)):
        key = _KEYS[_KEY_INDEX % len(_KEYS)]
        _KEY_INDEX += 1
        url = f"https://api.themoviedb.org/3{path}"
        p = dict(params or {})
        p["api_key"] = key
        try:
            r = requests.get(url, params=p, timeout=10, headers=HTTP_HEADERS)
            if r.ok:
                return r.json()
        except Exception:
            logger.debug("TMDB direct API request failed, trying next key")
            continue
    return None


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def search(query: str, media_type: str | None = None, page: int = 1) -> list[dict]:
    """Search TMDB for movies and/or TV shows with pagination.

    Uses ``/search/multi`` (mixed), ``/search/movie``, or ``/search/tv``
    depending on *media_type*.

    Args:
        query: Free-text search string.
        media_type: ``"movie"``, ``"tv"``, or ``None`` for mixed results.
        page: TMDB page number (20 results per page).

    Returns:
        List of result dicts with keys: tmdb_id, title, media_type, year,
        poster, overview, rating, vote_count, release_date, genre_ids.
    """
    if media_type == "movie":
        path = "/search/movie"
    elif media_type == "tv":
        path = "/search/tv"
    else:
        path = "/search/multi"

    data = _request(path, {"query": query, "page": str(page), "language": "en"})
    if not data:
        return []

    results: list[dict] = []
    for r in data.get("results", []):
        if path == "/search/multi" and r.get("media_type") == "person":
            continue
        raw_date = r.get("release_date") or r.get("first_air_date") or ""
        results.append(
            {
                "tmdb_id": r["id"],
                "title": r.get("title") or r.get("name", ""),
                "media_type": r.get("media_type") or media_type or "movie",
                "year": raw_date[:4],
                "poster": r.get("poster_path") or "",
                "overview": r.get("overview", ""),
                "rating": r.get("vote_average", 0) or 0,
                "vote_count": r.get("vote_count", 0),
                "release_date": raw_date,
                "genre_ids": r.get("genre_ids", []),
            }
        )
    return results


def movie_details(tmdb_id: int) -> dict | None:
    """Fetch full movie details from TMDB.

    Cached locally for 30 days.
    """
    cache_key = f"movie:{tmdb_id}"
    cached = _cache_get(cache_key, MOVIE_TTL)
    if cached:
        return cached
    data = _request(f"/movie/{tmdb_id}", {"language": "en"})
    if data:
        _cache_set(cache_key, data)
    return data


def tv_details(tmdb_id: int) -> dict | None:
    """Fetch full TV series details from TMDB.

    Cached locally for 7 days.
    """
    cache_key = f"tv:{tmdb_id}"
    cached = _cache_get(cache_key, SERIES_TTL)
    if cached:
        return cached
    data = _request(f"/tv/{tmdb_id}", {"language": "en"})
    if data:
        _cache_set(cache_key, data)
    return data


def season_details(tmdb_id: int, season_number: int) -> dict | None:
    """Fetch a single season's episode list from TMDB.

    Cached locally for 7 days.
    """
    cache_key = f"tv:{tmdb_id}:season:{season_number}"
    cached = _cache_get(cache_key, SERIES_TTL)
    if cached:
        return cached
    data = _request(f"/tv/{tmdb_id}/season/{season_number}", {"language": "en"})
    if data:
        _cache_set(cache_key, data)
    return data


def _normalize_results(data: dict | None) -> list[dict]:
    """Convert TMDB API response results to standardized item dicts."""
    if not data:
        return []
    out = []
    for r in data.get("results", []):
        raw_date = r.get("release_date") or r.get("first_air_date") or ""
        out.append(
            {
                "tmdb_id": r["id"],
                "title": r.get("title") or r.get("name", ""),
                "media_type": r.get("media_type", "movie"),
                "year": raw_date[:4],
                "release_date": raw_date,
                "rating": r.get("vote_average", 0) or 0,
                "poster": r.get("poster_path") or "",
                "overview": r.get("overview", ""),
            }
        )
    return out


_genre_map: dict[int, str] | None = None


def genre_names(genre_ids: list[int]) -> list[str]:
    """Resolve TMDB genre IDs to names, cached in memory + on disk.

    Fetches ``/genre/movie/list`` and ``/genre/tv/list`` on first use
    (two API calls, ~20 entries each), then persists to disk cache with
    a 30-day TTL so subsequent sessions don't re-fetch.
    """
    global _genre_map
    if _genre_map is None:
        cached = _cache_get("genre_map", GENRE_TTL)
        if cached is not None:
            _genre_map = {int(k): v for k, v in cached.items()}
        else:
            _genre_map = {}
            for mt in ("movie", "tv"):
                data = _request(f"/genre/{mt}/list", {"language": "en"})
                if data:
                    for g in data.get("genres", []):
                        _genre_map[g["id"]] = g["name"]
            _cache_set("genre_map", _genre_map)
    return [_genre_map[gid] for gid in genre_ids if gid in _genre_map]


def _filter_released(items: list[dict]) -> list[dict]:
    """Filter out titles with release dates in the future."""
    today = date.today().isoformat()
    return [i for i in items if not i.get("release_date") or i["release_date"] <= today]


def trending(page: int = 1) -> list[dict]:
    """Fetch trending all-week from TMDB.

    Cached locally for 24 hours.
    """
    cache_key = f"trending:{page}"
    cached = _cache_get(cache_key, TRENDING_TTL)
    if cached is not None:
        return cached
    data = _request("/trending/all/week", {"language": "en", "page": str(page)})
    results = _normalize_results(data)
    results = _filter_released(results)
    _cache_set(cache_key, results)
    return results


def popular(page: int = 1) -> list[dict]:
    """Fetch popular movies and TV from TMDB (mixed).

    Cached locally for 24 hours.
    """
    cache_key = f"popular:{page}"
    cached = _cache_get(cache_key, POPULAR_TTL)
    if cached is not None:
        return cached
    movies = _normalize_results(
        _request("/movie/popular", {"language": "en", "page": str(page)})
    )
    tv = _normalize_results(
        _request("/tv/popular", {"language": "en", "page": str(page)})
    )
    for m in movies:
        m["media_type"] = "movie"
    for t in tv:
        t["media_type"] = "tv"
    merged = movies + tv
    merged = _filter_released(merged)
    merged.sort(key=lambda x: x.get("rating", 0), reverse=True)
    _cache_set(cache_key, merged)
    return merged


def recommendations_from_seeds(bookmarks: list[dict], limit: int = 20) -> list[dict]:
    """Fetch TMDB recommendations seeded from bookmarked items.

    Uses the most recent bookmark's tmdb_id + media_type.
    Not cached because the seed changes as the user bookmarks more.
    """
    if not bookmarks:
        return []
    seed = bookmarks[0]
    tmdb_id = seed["tmdb_id"]
    media_type = seed.get("media_type", "movie")
    data = _request(f"/{media_type}/{tmdb_id}/recommendations", {"language": "en"})
    return _normalize_results(data)[:limit]
