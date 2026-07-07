# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import json
import logging
import re
import subprocess
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from typing import Any

from nyrx.config import (
    SC_CLIENT_ID_CACHE,
    SC_MAX_LIKED_TRACKS,
    SC_THUMBS_DIR,
    SC_TRENDING_SUFFIX_1_COUNTRIES,
    SC_TRENDING_SUFFIX_1_GENRES,
    SOUNDCLOUD_SEARCH_LIMIT,
)
from nyrx.helpers import db_scope

from .db import _get_db


class ProfileResolveError(Exception):
    """Raised when a SoundCloud profile URL cannot be resolved."""


logger = logging.getLogger(__name__)

_client_id: str | None = None


def _scrape_client_id() -> str | None:
    """Return a SoundCloud API client_id from disk cache or homepage scrape.

    1. Check module-level ``_client_id`` (fastest).
    2. Read ``SC_CLIENT_ID_CACHE`` from disk.
    3. Scrape the SoundCloud homepage JS bundle.
    4. On success, write to disk for future sessions.
    """
    global _client_id
    if _client_id:
        logger.debug("_scrape_client_id: cached_hit")
        return _client_id
    if SC_CLIENT_ID_CACHE.exists():
        try:
            _client_id = SC_CLIENT_ID_CACHE.read_text().strip()
            if _client_id:
                logger.debug("_scrape_client_id: disk_cache_hit")
                return _client_id
        except Exception:
            logger.debug("_scrape_client_id: failed to read disk cache")
    try:
        resp = urllib.request.urlopen("https://soundcloud.com", timeout=15)
        html = resp.read().decode()
        scripts = re.findall(r'<script[^>]+src="([^"]*sndcdn[^"]*)"', html)
        for src in reversed(scripts):
            try:
                js = urllib.request.urlopen(src, timeout=15).read().decode()
                m = re.search(r'client_id\s*:\s*"([0-9a-zA-Z]{32})"', js)
                if m:
                    _client_id = m.group(1)
                    logger.debug(
                        "_scrape_client_id: found client_id=%s", _client_id[:8]
                    )
                    SC_CLIENT_ID_CACHE.parent.mkdir(parents=True, exist_ok=True)
                    SC_CLIENT_ID_CACHE.write_text(_client_id)
                    logger.debug("_scrape_client_id: persisted to disk")
                    return _client_id
            except Exception:
                logger.debug(
                    "_scrape_client_id: failed to fetch JS bundle %s", src[:80]
                )
                continue
    except Exception:
        logger.warning("_scrape_client_id: failed to fetch SoundCloud homepage")
        pass
    logger.debug("_scrape_client_id: not_found")
    return None


def refresh_client_id() -> str | None:
    """Invalidate all caches and re-scrape. Returns the new client_id or None."""
    global _client_id
    _client_id = None
    if SC_CLIENT_ID_CACHE.exists():
        try:
            SC_CLIENT_ID_CACHE.unlink()
        except Exception:
            logger.debug("refresh_client_id: failed to delete disk cache")
    return _scrape_client_id()


def ensure_client_id() -> bool:
    """Pre-warm the client_id cache and validate with a lightweight API call.

    Called once at startup.  Returns True if a valid client_id is available,
    False otherwise (a warning is logged but the app continues).
    """
    cid = _scrape_client_id()
    if not cid:
        logger.warning(
            "ensure_client_id: no client_id available, SoundCloud features will be degraded"
        )
        return False
    test_url = f"https://api-v2.soundcloud.com/tracks?ids=294324164&client_id={cid}"
    try:
        resp = urllib.request.urlopen(test_url, timeout=10)
        if resp.status == 200:
            logger.info("ensure_client_id: validated successfully")
            return True
    except urllib.error.HTTPError as e:
        if e.code == 403:
            logger.info("ensure_client_id: cached client_id invalid, refreshing")
            cid = refresh_client_id()
            if cid:
                test_url = f"https://api-v2.soundcloud.com/tracks?ids=294324164&client_id={cid}"
                try:
                    resp = urllib.request.urlopen(test_url, timeout=10)
                    if resp.status == 200:
                        logger.info(
                            "ensure_client_id: refreshed and validated successfully"
                        )
                        return True
                except Exception:
                    logger.debug(
                        "ensure_client_id: refreshed validation request failed"
                    )
    except Exception:
        logger.debug("ensure_client_id: validation request failed")
    logger.warning(
        "ensure_client_id: validation failed, SoundCloud features may be degraded"
    )
    return False


def client_id_available() -> bool:
    """Lightweight check: no network I/O."""
    return _client_id is not None or SC_CLIENT_ID_CACHE.is_file()


def _redact_url(url: str) -> str:
    """Mask the client_id query param so debug logs don't leak the secret."""
    return re.sub(r"client_id=[^&\s]+", "client_id=***", url)


def _api_call(
    url: str,
    retries: int = 3,
    timeout: float = 15,
    deadline: float | None = None,
) -> dict | list | None:
    """Make an API call with retry + exponential backoff (1s, 2s, 4s).
    On 403, refreshes the client_id once and retries all remaining attempts.
    When *deadline* (a ``time.monotonic()`` timestamp) is set, the whole call
    is bounded by it: per-attempt urlopen timeouts are clamped to the
    remaining budget and backoff is skipped once it would overshoot.
    Returns parsed JSON on success, None if all retries exhausted."""
    for attempt in range(retries):
        if deadline is not None and time.monotonic() >= deadline:
            return None
        try:
            attempt_timeout = timeout
            if deadline is not None:
                attempt_timeout = min(timeout, deadline - time.monotonic())
            resp = urllib.request.urlopen(url, timeout=max(attempt_timeout, 0.1))
            return json.loads(resp.read())
        except urllib.error.HTTPError as e:
            if e.code == 403:
                logger.info(
                    "_api_call: 403 detected, refreshing client_id and retrying"
                )
                new_cid = refresh_client_id()
                if new_cid:
                    m = re.search(r"client_id=([^&]+)", url)
                    if m:
                        url = url.replace(m.group(0), f"client_id={new_cid}")
                        logger.debug("_api_call: retrying with refreshed client_id")
                        continue
            logger.debug(
                "_api_call attempt %d/%d failed: %s",
                attempt + 1,
                retries,
                _redact_url(url)[:80],
            )
            if attempt < retries - 1:
                if deadline is None or time.monotonic() + 2**attempt < deadline:
                    time.sleep(2**attempt)
            else:
                return None
        except Exception:
            logger.debug(
                "_api_call attempt %d/%d failed: %s",
                attempt + 1,
                retries,
                _redact_url(url)[:80],
            )
            if attempt < retries - 1:
                if deadline is None or time.monotonic() + 2**attempt < deadline:
                    time.sleep(2**attempt)
            else:
                return None
    return None


_THUMB_CACHE = SC_THUMBS_DIR

_SC_THUMB_SIZE_RE = re.compile(r"-(large|t\d+x\d+|crop)(?=\.)")


def _highres_sc_thumb(url: str) -> str:
    """Upgrade SoundCloud artwork URL from default 'large' to 'original'."""
    if not url:
        return url
    return _SC_THUMB_SIZE_RE.sub("-original", url)


def _cache_thumbnail(track_id: str, url: str) -> None:
    if not url:
        return
    threading.Thread(
        target=_do_cache_thumbnail, args=(track_id, url), daemon=True
    ).start()


def _do_cache_thumbnail(track_id: str, url: str) -> None:
    _THUMB_CACHE.mkdir(parents=True, exist_ok=True)
    local = _THUMB_CACHE / f"{track_id}.jpg"
    if local.exists():
        return
    try:
        resp = urllib.request.urlopen(url, timeout=10)
        local.write_bytes(resp.read())
    except Exception:
        logger.debug("_do_cache_thumbnail: failed for %s", url)


def fetch_trending_playlist(slug: str, country_code: str = "us") -> list[dict]:
    """Fetch trending tracks for a genre with full metadata.

    Two-step pipeline:
    1. ``yt-dlp --flat-playlist --dump-json`` on the trending playlist URL
       to get 50 track IDs.
    2. Batch-query ``api-v2.soundcloud.com/tracks?ids={...}&client_id={...}``
       for full metadata (title, channel, duration, views).

    Returns results in the same format as :func:`search_soundcloud`.
    """
    if (
        country_code in SC_TRENDING_SUFFIX_1_COUNTRIES
        and slug in SC_TRENDING_SUFFIX_1_GENRES
    ):
        slug = f"{slug}-1"
    playlist_url = f"https://soundcloud.com/trending-music-{country_code}/sets/{slug}"
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        playlist_url,
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"yt-dlp exited {proc.returncode}")

    track_ids: list[str] = []
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        item = json.loads(line)
        track_ids.append(str(item.get("id", "")))

    if not track_ids:
        logger.debug(
            "fetch_trending_playlist: no_ids slug=%s country=%s", slug, country_code
        )
        return []

    cid = _scrape_client_id()
    if not cid:
        raise RuntimeError("Could not obtain SoundCloud client_id for metadata fetch")

    ids_param = ",".join(track_ids)
    api_url = f"https://api-v2.soundcloud.com/tracks?ids={ids_param}&client_id={cid}"
    try:
        api_resp = urllib.request.urlopen(api_url, timeout=15)
        api_data: list[dict] = json.loads(api_resp.read())
    except urllib.error.HTTPError as e:
        if e.code != 403:
            raise RuntimeError(f"Failed to fetch track metadata: {e}") from e
        cid = refresh_client_id()
        if not cid:
            raise RuntimeError(
                "Failed to fetch track metadata after client_id refresh"
            ) from e
        api_url = (
            f"https://api-v2.soundcloud.com/tracks?ids={ids_param}&client_id={cid}"
        )
        api_resp = urllib.request.urlopen(api_url, timeout=15)
        api_data = json.loads(api_resp.read())
    except Exception as e:
        raise RuntimeError(f"Failed to fetch track metadata: {e}") from e

    results = []
    for t in api_data:
        if not isinstance(t, dict):
            continue
        user = t.get("user") or {}
        results.append(
            {
                "yt_id": str(t.get("id", "")),
                "title": t.get("title", ""),
                "channel": user.get("username", ""),
                "duration": (t.get("duration", 0) or 0) / 1000,
                "views": t.get("playback_count", 0) or 0,
                "likes_count": t.get("likes_count", 0) or 0,
                "thumbnail_url": _highres_sc_thumb(t.get("artwork_url", "")) or "",
                "url": t.get("permalink_url", "") or "",
                "description": t.get("description", "") or "",
                "genre": t.get("genre", "") or "",
                "uploader_id": str(user.get("id", "")),
                "permalink": user.get("permalink", ""),
                "source": "soundcloud",
            }
        )
    logger.debug(
        "fetch_trending_playlist: slug=%s country=%s ytdlp_ids=%s api_results=%s",
        slug,
        country_code,
        len(track_ids),
        len(results),
    )
    return results


def search_soundcloud(query: str, limit: int | None = None) -> list[dict]:
    n = limit or SOUNDCLOUD_SEARCH_LIMIT
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        f"scsearch{n}:{query}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"yt-dlp exited {proc.returncode}")

    results = []
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        item = json.loads(line)
        results.append(
            {
                "yt_id": str(item.get("id", "")),
                "title": item.get("title", ""),
                "channel": item.get("uploader", item.get("channel", "")),
                "duration": item.get("duration", 0),
                "views": item.get("view_count", 0),
                "likes_count": item.get("like_count", 0),
                "thumbnail_url": max(
                    (t for t in item.get("thumbnails", []) if t.get("url")),
                    key=lambda t: t.get("preference", 0) or 0,
                ).get("url", "")
                if item.get("thumbnails")
                else item.get("thumbnail", ""),
                "url": item.get("webpage_url", item.get("url", "")),
                "description": item.get("description", ""),
                "uploader_id": str(item.get("uploader_id", "")),
                "uploader_url": item.get("uploader_url", ""),
                "source": "soundcloud",
            }
        )
    logger.debug(
        "search_soundcloud: query=%s limit=%s results=%s",
        query[:30] if len(query) > 30 else query,
        n,
        len(results),
    )
    return results


def _format_location(city: str, country: str) -> str:
    if city and country:
        return f"{city}, {country}"
    return city or country


def fetch_artist_profile(artist_id: str) -> dict | None:
    """Fetch artist profile from SoundCloud API v2. Single call, no pagination.
    Returns dict with keys: artist_id, name, description, location, avatar_url,
    permalink, followers_count, track_count, playlist_count.
    Returns None on failure after all retries."""
    cid = _scrape_client_id()
    if not cid:
        logger.debug("fetch_artist_profile: no_cid artist_id=%s", artist_id)
        return None
    url = f"https://api-v2.soundcloud.com/users/{artist_id}?client_id={cid}"
    data = _api_call(url)
    if not isinstance(data, dict):
        logger.debug("fetch_artist_profile: api_failed artist_id=%s", artist_id)
        return None
    logger.debug("fetch_artist_profile: artist_id=%s ok", artist_id)
    return {
        "artist_id": str(data.get("id", "")),
        "name": data.get("username", ""),
        "description": data.get("description", "") or "",
        "location": _format_location(
            data.get("city", "") or "", data.get("country", "") or ""
        ),
        "avatar_url": _highres_sc_thumb(data.get("avatar_url", "")) or "",
        "permalink": data.get("permalink", ""),
        "followers_count": data.get("followers_count", 0) or 0,
        "track_count": data.get("track_count", 0) or 0,
        "playlist_count": data.get("playlist_count", 0) or 0,
    }


def fetch_artist_collections(artist_id: str) -> list[dict] | None:
    """Fetch all collections (albums + playlists) for an artist via /playlists.
    Albums are playlists with is_album=True: set type accordingly.
    Always fetches all pages (cheap 2-3 calls). Returns None on failure."""
    cid = _scrape_client_id()
    if not cid:
        logger.debug("fetch_artist_collections: no_cid artist_id=%s", artist_id)
        return None
    results: list[dict] = []
    offset = 0
    while True:
        url = f"https://api-v2.soundcloud.com/users/{artist_id}/playlists?client_id={cid}&limit=50&offset={offset}"
        data = _api_call(url)
        if not isinstance(data, dict):
            logger.debug(
                "fetch_artist_collections: api_failed artist_id=%s offset=%s collected=%s",
                artist_id,
                offset,
                len(results),
            )
            return None if not results else results
        col = data.get("collection", [])
        if not col:
            break
        for item in col:
            results.append(
                {
                    "collection_id": str(item.get("id", "")),
                    "title": item.get("title", ""),
                    "artwork_url": item.get("artwork_url", "") or "",
                    "track_count": item.get("track_count", 0) or 0,
                    "type": "album" if item.get("is_album") else "playlist",
                    "permalink_url": item.get("permalink_url", "") or "",
                }
            )
        offset += 50
    logger.debug(
        "fetch_artist_collections: artist_id=%s collections=%s", artist_id, len(results)
    )
    return results


def fetch_playlist_tracks(collection_id: str) -> list[dict] | None:
    cid = _scrape_client_id()
    if not cid:
        logger.debug("fetch_playlist_tracks: no_cid collection_id=%s", collection_id)
        return None
    url = f"https://api-v2.soundcloud.com/playlists/{collection_id}?client_id={cid}&limit=200"
    data = _api_call(url)
    if not isinstance(data, dict):
        return None
    raw_tracks = data.get("tracks", [])
    if not raw_tracks:
        return []

    ids = [str(t["id"]) for t in raw_tracks if isinstance(t, dict) and t.get("id")]
    if not ids:
        return []

    results = []
    for i in range(0, len(ids), 50):
        chunk = ids[i : i + 50]
        resolved = batch_resolve_via_client_id(chunk)
        if resolved:
            results.extend(resolved)
    logger.debug(
        "fetch_playlist_tracks: collection_id=%s ids=%s results=%s",
        collection_id,
        len(ids),
        len(results),
    )
    return results


def fetch_artist_uploads(artist_id: str, skip_dedup: bool = False) -> list[dict] | None:
    """Fetch all track uploads for an artist with cursor-based pagination.
    Uses next_href from the API response for pagination.
    When skip_dedup=True, bypasses the known-ID dedup and fetches all pages
    (used for initial fill after interrupted cache).
    When skip_dedup=False, uses 3-consecutive-dup dedup to stop early on refetches.
    Returns None on failure (no cid or API error)."""
    cid = _scrape_client_id()
    if not cid:
        logger.debug("fetch_artist_uploads: no_cid artist_id=%s", artist_id)
        return None

    if not skip_dedup:
        with db_scope(_get_db) as db:
            known = {
                r["track_id"]
                for r in db.execute(
                    "SELECT track_id FROM artist_uploads WHERE artist_id = ?",
                    (artist_id,),
                ).fetchall()
            }
    else:
        known = set()

    seen: set[str] = set()
    results: list[dict] = []
    consecutive_dup = 0
    url = f"https://api-v2.soundcloud.com/users/{artist_id}/tracks?client_id={cid}&limit=50"

    while url:
        data = _api_call(url)
        if not isinstance(data, dict):
            return None if not results else results
        col = data.get("collection", [])
        for item in col:
            tid = str(item.get("id", ""))
            if not skip_dedup:
                if tid in known:
                    consecutive_dup += 1
                    continue
                consecutive_dup = 0
            elif tid in seen:
                continue
            seen.add(tid)
            user = item.get("user") or {}
            results.append(
                {
                    "track_id": tid,
                    "title": item.get("title", ""),
                    "channel": user.get("username", ""),
                    "duration": (item.get("duration", 0) or 0) / 1000,
                    "like_count": item.get("likes_count", 0) or 0,
                    "view_count": item.get("playback_count", 0) or 0,
                    "repost_count": item.get("reposts_count", 0) or 0,
                    "genre": item.get("genre", "") or "",
                    "url": item.get("permalink_url", "") or "",
                    "uploader_id": str(user.get("id", "")),
                    "permalink": user.get("permalink", ""),
                    "thumbnail_url": _highres_sc_thumb(item.get("artwork_url", ""))
                    or "",
                    "source": "soundcloud",
                }
            )
        if not skip_dedup and consecutive_dup >= 3:
            break
        next_href = data.get("next_href") or ""
        if next_href:
            url = next_href
            if "client_id=" not in url:
                url += f"&client_id={cid}"
        else:
            url = ""
    logger.debug(
        "fetch_artist_uploads: artist_id=%s skip_dedup=%s results=%s known=%s consecutive_dup=%s",
        artist_id,
        skip_dedup,
        len(results),
        len(known) if not skip_dedup else 0,
        consecutive_dup,
    )
    return results


def fetch_artist_likes(
    artist_id: str, max_tracks: int | None = None
) -> list[dict] | None:
    """Fetch liked tracks for an artist with cursor-based pagination.
    Filters to track items only (skips playlist likes).
    Caps results at *max_tracks* (defaults to :data:`SC_MAX_LIKED_TRACKS`;
    pass ``None`` for no limit).
    Always fetches all pages (limit=200). Returns None on failure."""
    if max_tracks is None:
        max_tracks = SC_MAX_LIKED_TRACKS
    cid = _scrape_client_id()
    if not cid:
        logger.debug("fetch_artist_likes: no_cid artist_id=%s", artist_id)
        return None

    results: list[dict] = []
    seen: set[str] = set()
    url = f"https://api-v2.soundcloud.com/users/{artist_id}/likes?client_id={cid}&limit=200"

    while url:
        data = _api_call(url)
        if not isinstance(data, dict):
            return None if not results else results
        col = data.get("collection", [])
        for item in col:
            if "track" not in item:
                continue
            t = item["track"]
            tid = str(t.get("id", ""))
            if tid in seen:
                continue
            seen.add(tid)
            user = t.get("user") or {}
            results.append(
                {
                    "track_id": tid,
                    "title": t.get("title", ""),
                    "channel": user.get("username", ""),
                    "duration": (t.get("duration", 0) or 0) / 1000,
                    "like_count": t.get("likes_count", 0) or 0,
                    "view_count": t.get("playback_count", 0) or 0,
                    "url": t.get("permalink_url", "") or "",
                    "uploader_id": str(user.get("id", "")),
                    "permalink": user.get("permalink", ""),
                    "thumbnail_url": _highres_sc_thumb(t.get("artwork_url", "")) or "",
                    "source": "soundcloud",
                    "liked_at": item.get("created_at", ""),
                }
            )
            if max_tracks and len(results) >= max_tracks:
                return results[:max_tracks]
        next_href = data.get("next_href") or ""
        if next_href:
            url = next_href
            if "client_id=" not in url:
                url += f"&client_id={cid}"
        else:
            url = ""
    logger.debug(
        "fetch_artist_likes: artist_id=%s max_tracks=%s results=%s",
        artist_id,
        max_tracks,
        len(results),
    )
    return results


def resolve_sc_user(url: str) -> dict | None:
    """Resolve a SoundCloud profile URL to a user object via the resolve endpoint.

    Returns the full user dict (with ``id``, ``username``, etc.) or None if
    the URL is invalid, points to a non-user resource, or the API call fails.
    """
    cid = _scrape_client_id()
    if not cid:
        logger.debug("resolve_sc_user: no_cid url=%s", url[:60])
        return None
    encoded = urllib.parse.quote(url, safe="")
    data = _api_call(
        f"https://api-v2.soundcloud.com/resolve?url={encoded}&client_id={cid}"
    )
    if not isinstance(data, dict) or "username" not in data:
        logger.debug("resolve_sc_user: not_found url=%s", url[:60])
        return None
    logger.debug(
        "resolve_sc_user: ok url=%s user_id=%s name=%s",
        url[:60],
        data.get("id", ""),
        data.get("username", "")[:20],
    )
    return data


def fetch_station_ids(track_id: str) -> list[str]:
    """Fetch related track IDs from the SoundCloud station endpoint.

    Runs ``yt-dlp --flat-playlist`` on the station set URL for a track and
    returns up to 49 track ID strings.  Used as the first step of feed
    generation (``generate_feed``).
    """
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        f"https://soundcloud.com/discover/sets/track-stations:{track_id}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"yt-dlp exited {proc.returncode}")

    ids: list[str] = []
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        item = json.loads(line)
        tid = str(item.get("id", ""))
        if tid:
            ids.append(tid)
    logger.debug("fetch_station_ids: track_id=%s ids=%s", track_id, len(ids))
    return ids


def get_station_tracks(track_id: str) -> list[dict]:
    """Fetch station (related) tracks for a SoundCloud track.

    Chains :func:`fetch_station_ids` → :func:`batch_resolve_via_client_id`.
    Returns up to 50 full-metadata dicts matching the
    ``batch_resolve_via_client_id`` format, or an empty list on failure.
    """
    try:
        ids = fetch_station_ids(track_id)
    except Exception:
        logger.debug("get_station_tracks: fetch_station_ids failed for %s", track_id)
        return []
    if not ids:
        return []
    ids = ids[:50]
    try:
        result = batch_resolve_via_client_id(ids)
        logger.debug(
            "get_station_tracks: track_id=%s ids=%s results=%s",
            track_id,
            len(ids),
            len(result),
        )
        return result
    except RuntimeError:
        return []


def _resolve_sc_track(track_id: str) -> dict[str, Any] | None:
    """Fetch full metadata for a single track from the SC API.

    Returns ``{"views": N, "likes_count": N, "thumbnail_url": str}``
    or ``None`` on failure.
    """
    cid = _scrape_client_id()
    if not cid:
        return None
    try:
        api_resp = urllib.request.urlopen(
            f"https://api-v2.soundcloud.com/tracks?ids={track_id}&client_id={cid}",
            timeout=15,
        )
        data: list[dict] = json.loads(api_resp.read())
    except Exception:
        logger.debug("_resolve_sc_track: API call failed for track %s", track_id)
        return None
    if not isinstance(data, list) or not data:
        return None
    t = data[0]
    if not isinstance(t, dict):
        return None
    user = t.get("user") or {}
    return {
        "views": t.get("playback_count", 0) or 0,
        "likes_count": t.get("likes_count", 0) or 0,
        "thumbnail_url": _highres_sc_thumb(t.get("artwork_url", "")) or "",
        "uploader_id": str(user.get("id", "")),
        "permalink": user.get("permalink", ""),
    }


def batch_resolve_via_client_id(track_ids: list[str]) -> list[dict]:
    """Fetch full track metadata from the SoundCloud API v2.

    Queries ``api-v2.soundcloud.com/tracks?ids=...&client_id=...`` with up
    to ~150 comma-separated IDs.  Returns a list of dicts with keys:
    ``yt_id``, ``title``, ``channel``, ``duration`` (seconds),
    ``like_count``, ``view_count``, ``thumbnail_url``, ``url``, ``genre``,
    ``uploader_id``, ``permalink``, ``source``.

    Raises ``RuntimeError`` if the client_id cannot be obtained or the
    metadata fetch fails after all retries.
    """
    cid = _scrape_client_id()
    if not cid:
        raise RuntimeError("Could not obtain SoundCloud client_id")

    ids_param = ",".join(track_ids)
    api_url = f"https://api-v2.soundcloud.com/tracks?ids={ids_param}&client_id={cid}"
    api_data = _api_call(api_url)
    if api_data is None:
        raise RuntimeError("Failed to fetch track metadata")

    if not isinstance(api_data, list):
        return []

    results: list[dict] = []
    for t in api_data:
        if not isinstance(t, dict):
            continue
        user = t.get("user") or {}
        results.append(
            {
                "yt_id": str(t.get("id", "")),
                "title": t.get("title", ""),
                "channel": user.get("username", ""),
                "duration": (t.get("duration", 0) or 0) / 1000,
                "like_count": t.get("likes_count", 0) or 0,
                "view_count": t.get("playback_count", 0) or 0,
                "thumbnail_url": _highres_sc_thumb(t.get("artwork_url", "")) or "",
                "url": t.get("permalink_url", "") or "",
                "genre": t.get("genre", "") or "",
                "uploader_id": str(user.get("id", "")),
                "permalink": user.get("permalink", ""),
                "waveform_url": t.get("waveform_url", "") or "",
                "source": "soundcloud",
            }
        )
    logger.debug(
        "batch_resolve_via_client_id: requested=%s results=%s",
        len(track_ids),
        len(results),
    )
    return results


def batch_resolve_via_ytdlp(track_id: str, count: int = 30) -> list[dict]:
    """Fallback: resolve station tracks via full yt-dlp without ``--flat-playlist``."""
    cmd = [
        "yt-dlp",
        "-j",
        "--playlist-items",
        f"1-{count}",
        "--no-warnings",
        f"https://soundcloud.com/discover/sets/track-stations:{track_id}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=180)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"yt-dlp exited {proc.returncode}")

    results: list[dict] = []
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        item = json.loads(line)
        permalink = ""
        webpage_url = item.get("webpage_url", "") or ""
        m = re.search(r"soundcloud\.com/([^/]+)/", webpage_url)
        if m:
            permalink = m.group(1)
        results.append(
            {
                "yt_id": str(item.get("id", "")),
                "title": item.get("title", ""),
                "channel": item.get("uploader", item.get("channel", "")),
                "duration": item.get("duration", 0) or 0,
                "like_count": item.get("like_count", 0) or 0,
                "view_count": item.get("view_count", 0) or 0,
                "thumbnail_url": item.get("thumbnail", ""),
                "url": webpage_url,
                "genre": item.get("genre", item.get("track_genre", "")) or "",
                "uploader_id": str(item.get("uploader_id", "")),
                "permalink": permalink,
                "source": "soundcloud",
            }
        )
    logger.debug(
        "batch_resolve_via_ytdlp: track_id=%s count=%s results=%s",
        track_id,
        count,
        len(results),
    )
    return results


def fetch_waveform(url: str, timeout: float = 10) -> dict | None:
    """Fetch SoundCloud waveform JSON data from a waveform_url.

    Returns ``{"width": int, "height": int, "samples": [int, ...]}``
    or ``None`` on failure.
    """
    if not url:
        return None
    try:
        req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode())
    except Exception:
        logger.debug("fetch_waveform: failed for url %s", url[:80])
        return None


def enrich_sc_track(track_data: dict) -> dict:
    """Enrich a SoundCloud track dict with waveform data and full metadata.

    Three dispatch branches based on available data:
    1. ``waveform_url`` present → direct waveform fetch (feed / station).
    2. ``permalink_url`` present → resolve endpoint for full metadata + waveform.
    3. numeric ``yt_id`` only → ``/tracks/{id}`` endpoint.

    Wall-clock budget of 2.0 s.  Returns a dict with keys:
    ``waveform_samples`` (list | None), ``like_count``, ``view_count``,
    ``waveform_url``.
    """
    deadline = time.monotonic() + 2.0

    like_count = track_data.get("like_count", track_data.get("likes_count", 0)) or 0
    view_count = track_data.get("view_count", track_data.get("views", 0)) or 0
    waveform_url = track_data.get("waveform_url", "") or ""
    yt_id = track_data.get("yt_id", "")

    result: dict = {
        "waveform_samples": None,
        "like_count": like_count,
        "view_count": view_count,
        "waveform_url": waveform_url,
    }

    # -- Branch 1: waveform_url already known (feed / station tracks) ----------
    if waveform_url:
        samples = _try_fetch_waveform_samples(
            waveform_url, timeout=min(1.5, max(0.1, deadline - time.monotonic()))
        )
        if samples is not None:
            result["waveform_samples"] = samples
        logger.debug(
            "enrich_sc_track: branch1 (known_waveform) yt_id=%s has_samples=%s",
            yt_id,
            samples is not None,
        )
        return result

    remaining = deadline - time.monotonic()
    if remaining <= 0:
        logger.debug("enrich_sc_track: deadline_exceeded yt_id=%s", yt_id)
        return result

    # -- Branch 2: permalink_url → /resolve -----------------------------------
    if track_data.get("permalink_url"):
        metadata = _resolve_via_permalink(
            track_data["permalink_url"], timeout=remaining
        )
        logger.debug(
            "enrich_sc_track: branch2 (permalink) yt_id=%s resolved=%s",
            yt_id,
            metadata is not None,
        )
    # -- Branch 3: numeric yt_id → /tracks/{id} --------------------------------
    else:
        yt_id_local = track_data.get("yt_id", "")
        if not yt_id_local:
            logger.debug("enrich_sc_track: branch3 no_yt_id")
            return result
        metadata = _resolve_via_track_id(yt_id_local, timeout=remaining)
        logger.debug(
            "enrich_sc_track: branch3 (track_id) yt_id=%s resolved=%s",
            yt_id_local,
            metadata is not None,
        )

    if metadata:
        result.update(metadata)
        wf_url = metadata.get("waveform_url", "")
        if wf_url:
            remaining = deadline - time.monotonic()
            if remaining > 0.1:
                samples = _try_fetch_waveform_samples(
                    wf_url, timeout=min(1.0, remaining)
                )
                if samples is not None:
                    result["waveform_samples"] = samples
    return result


def _try_fetch_waveform_samples(
    waveform_url: str, timeout: float = 1.5
) -> list[int] | None:
    """Fetch waveform JSON and return the samples list, or None."""
    data = fetch_waveform(waveform_url, timeout=timeout)
    if data and isinstance(data.get("samples"), list):
        return data["samples"]
    return None


def _resolve_via_permalink(permalink_url: str, timeout: float = 2.0) -> dict | None:
    """Resolve a permalink URL to full track metadata (likes, views, waveform_url)."""
    cid = _scrape_client_id()
    if not cid:
        return None
    encoded = urllib.parse.quote(permalink_url, safe="")
    data = _api_call(
        f"https://api-v2.soundcloud.com/resolve?url={encoded}&client_id={cid}",
        timeout=timeout,
        deadline=time.monotonic() + timeout,
    )
    if not isinstance(data, dict):
        logger.debug(
            "_resolve_via_permalink: API call failed for %s", permalink_url[:80]
        )
        return None
    user = data.get("user") or {}
    return {
        "like_count": data.get("likes_count", 0) or 0,
        "view_count": data.get("playback_count", 0) or 0,
        "waveform_url": data.get("waveform_url", "") or "",
        "uploader_id": str(user.get("id", "")),
        "permalink": user.get("permalink", ""),
    }


def _resolve_via_track_id(track_id: str, timeout: float = 2.0) -> dict | None:
    """Fetch full track metadata via the /tracks endpoint."""
    cid = _scrape_client_id()
    if not cid:
        return None
    data_list = _api_call(
        f"https://api-v2.soundcloud.com/tracks?ids={track_id}&client_id={cid}",
        timeout=timeout,
        deadline=time.monotonic() + timeout,
    )
    if not isinstance(data_list, list) or not data_list:
        logger.debug("_resolve_via_track_id: API call failed for %s", track_id)
        return None
    t = data_list[0]
    if not isinstance(t, dict):
        return None
    user = t.get("user") or {}
    return {
        "like_count": t.get("likes_count", 0) or 0,
        "view_count": t.get("playback_count", 0) or 0,
        "waveform_url": t.get("waveform_url", "") or "",
        "uploader_id": str(user.get("id", "")),
        "permalink": user.get("permalink", ""),
    }
