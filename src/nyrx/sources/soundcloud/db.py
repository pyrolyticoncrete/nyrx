# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from nyrx.config import SC_DB_PATH
from nyrx.helpers import db_scope

logger = logging.getLogger(__name__)

_sc_db_initialized_path: str | None = None


_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

DROP TABLE IF EXISTS artist_tracks;

CREATE TABLE IF NOT EXISTS followed_artists (
    artist_id TEXT PRIMARY KEY,
    permalink TEXT,
    name TEXT,
    url TEXT,
    followed_at TEXT,
    tracks_cached_at TEXT,
    last_seeded_at TEXT
);

CREATE TABLE IF NOT EXISTS artist_uploads (
    id INTEGER PRIMARY KEY,
    artist_id TEXT NOT NULL,
    track_id TEXT,
    title TEXT,
    channel TEXT,
    duration REAL,
    like_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    repost_count INTEGER DEFAULT 0,
    genre TEXT,
    url TEXT,
    uploader_id TEXT,
    permalink TEXT,
    thumbnail_url TEXT,
    cached_at TEXT,
    last_seeded_at TEXT,
    UNIQUE(artist_id, track_id)
);

CREATE TABLE IF NOT EXISTS liked_tracks (
    track_id TEXT PRIMARY KEY,
    title TEXT,
    channel TEXT,
    duration REAL,
    url TEXT,
    uploader_id TEXT,
    permalink TEXT,
    view_count INTEGER DEFAULT 0,
    like_count INTEGER DEFAULT 0,
    thumbnail_url TEXT,
    liked_at TEXT,
    last_seeded_at TEXT
);

CREATE TABLE IF NOT EXISTS feed_tracks (
    id INTEGER PRIMARY KEY,
    track_id TEXT UNIQUE,
    title TEXT,
    channel TEXT,
    duration REAL,
    like_count INTEGER,
    repost_count INTEGER,
    view_count INTEGER,
    genre TEXT,
    url TEXT,
    uploader_id TEXT,
    permalink TEXT,
    thumbnail_url TEXT,
    score REAL,
    seed_track_id TEXT,
    consumed INTEGER DEFAULT 0,
    consumed_at TEXT,
    added_at TEXT
);

CREATE TABLE IF NOT EXISTS listened_tracks (
    track_id TEXT,
    source TEXT,
    listened_at TEXT,
    duration_watched REAL,
    duration_total REAL,
    complete INTEGER
);
CREATE INDEX IF NOT EXISTS idx_listened_track ON listened_tracks(track_id, source);

CREATE TABLE IF NOT EXISTS artist_profile (
    artist_id TEXT PRIMARY KEY,
    name TEXT,
    description TEXT,
    location TEXT,
    avatar_url TEXT,
    permalink TEXT,
    followers_count INTEGER DEFAULT 0,
    track_count INTEGER DEFAULT 0,
    playlist_count INTEGER DEFAULT 0,
    cached_at TEXT
);

CREATE TABLE IF NOT EXISTS artist_collections (
    id INTEGER PRIMARY KEY,
    artist_id TEXT,
    collection_id TEXT,
    title TEXT,
    type TEXT,
    artwork_url TEXT,
    track_count INTEGER DEFAULT 0,
    cached_at TEXT,
    UNIQUE(artist_id, collection_id)
);

CREATE TABLE IF NOT EXISTS artist_liked_tracks (
    id INTEGER PRIMARY KEY,
    artist_id TEXT,
    track_id TEXT,
    title TEXT,
    channel TEXT,
    duration REAL,
    like_count INTEGER DEFAULT 0,
    view_count INTEGER DEFAULT 0,
    url TEXT,
    uploader_id TEXT,
    permalink TEXT,
    thumbnail_url TEXT,
    cached_at TEXT,
    UNIQUE(artist_id, track_id)
);

CREATE TABLE IF NOT EXISTS artist_cache_status (
    artist_id TEXT NOT NULL,
    category  TEXT NOT NULL,
    cached_at TEXT NOT NULL,
    PRIMARY KEY (artist_id, category)
);
"""


def init_sc_db() -> None:
    global _sc_db_initialized_path
    if _sc_db_initialized_path == str(SC_DB_PATH):
        return
    SC_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_scope(lambda: sqlite3.connect(str(SC_DB_PATH))) as db:
        db.executescript(_SCHEMA_SQL)
        for col in ("view_count", "like_count", "thumbnail_url"):
            try:
                db.execute(f"ALTER TABLE liked_tracks ADD COLUMN {col}")
            except sqlite3.OperationalError:
                pass
    _sc_db_initialized_path = str(SC_DB_PATH)


def _get_db() -> sqlite3.Connection:
    init_sc_db()
    conn = sqlite3.connect(str(SC_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA busy_timeout = 5000")
    return conn


def _row_to_liked(r: sqlite3.Row) -> dict:
    return {
        "yt_id": r["track_id"],
        "title": r["title"] or "",
        "channel": r["channel"] or "",
        "duration": r["duration"] or 0,
        "views": r["view_count"] or 0,
        "likes_count": r["like_count"] or 0,
        "thumbnail_url": r["thumbnail_url"] or "",
        "url": r["url"] or "",
        "uploader_id": r["uploader_id"] or "",
        "permalink": r["permalink"] or "",
        "source": "soundcloud",
        "liked_at": r["liked_at"],
        "last_seeded_at": r["last_seeded_at"],
    }


def _row_to_followed(r: sqlite3.Row) -> dict:
    return {
        "id": r["artist_id"],
        "permalink": r["permalink"] or "",
        "name": r["name"] or "",
        "url": r["url"] or "",
        "followed_at": r["followed_at"] or "",
    }


def load_sc_likes() -> list[dict]:
    try:
        with db_scope(_get_db) as db:
            rows = db.execute(
                "SELECT track_id, title, channel, duration, url, uploader_id, permalink, view_count, like_count, thumbnail_url, liked_at, last_seeded_at "
                "FROM liked_tracks ORDER BY liked_at DESC"
            ).fetchall()
        logger.debug("load_sc_likes: row_count=%s", len(rows))
        return [_row_to_liked(r) for r in rows]
    except Exception:
        logger.exception("load_sc_likes: failed to read likes")
        return []


def save_sc_likes(liked: list[dict]) -> None:
    try:
        with db_scope(_get_db) as db:
            db.execute("DELETE FROM liked_tracks")
            now = datetime.now(UTC).isoformat()
            for item in liked:
                db.execute(
                    "INSERT INTO liked_tracks "
                    "(track_id, title, channel, duration, url, uploader_id, permalink, view_count, like_count, thumbnail_url, liked_at, last_seeded_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
                        item.get("liked_at", now),
                        item.get("last_seeded_at"),
                    ),
                )
            db.commit()
        logger.debug("save_sc_likes: saved_count=%s", len(liked))
    except Exception:
        logger.exception("save_sc_likes: failed to save likes")


def load_sc_followed() -> list[dict]:
    try:
        with db_scope(_get_db) as db:
            rows = db.execute(
                "SELECT artist_id, permalink, name, url, followed_at, tracks_cached_at, last_seeded_at "
                "FROM followed_artists ORDER BY followed_at DESC"
            ).fetchall()
        logger.debug("load_sc_followed: row_count=%s", len(rows))
        return [_row_to_followed(r) for r in rows]
    except Exception:
        logger.exception("load_sc_followed: failed to read followed artists")
        return []


def save_sc_followed(followed: list[dict]) -> None:
    try:
        with db_scope(_get_db) as db:
            db.execute("DELETE FROM followed_artists")
            for item in followed:
                db.execute(
                    "INSERT INTO followed_artists "
                    "(artist_id, permalink, name, url, followed_at) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (
                        item.get("id", ""),
                        item.get("permalink", ""),
                        item.get("name", ""),
                        item.get("url", ""),
                        item.get("followed_at", ""),
                    ),
                )
            db.commit()
        logger.debug("save_sc_followed: saved_count=%s", len(followed))
    except Exception:
        logger.exception("save_sc_followed: failed to save followed artists")


def save_artist_profile(artist_id: str, profile: dict) -> None:
    """INSERT OR REPLACE into artist_profile."""
    now = datetime.now(UTC).isoformat()
    with db_scope(_get_db) as db:
        db.execute(
            """INSERT OR REPLACE INTO artist_profile
               (artist_id, name, description, location, avatar_url, permalink,
                followers_count, track_count, playlist_count, cached_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                artist_id,
                profile.get("name", ""),
                profile.get("description", ""),
                profile.get("location", ""),
                profile.get("avatar_url", ""),
                profile.get("permalink", ""),
                profile.get("followers_count", 0),
                profile.get("track_count", 0),
                profile.get("playlist_count", 0),
                now,
            ),
        )
        db.commit()
    logger.debug(
        "save_artist_profile: artist_id=%s name=%s",
        artist_id,
        profile.get("name", "")[:20],
    )


def save_artist_collections(artist_id: str, collections: list[dict]) -> None:
    """DELETE + INSERT artist_collections for this artist."""
    now = datetime.now(UTC).isoformat()
    with db_scope(_get_db) as db:
        db.execute("DELETE FROM artist_collections WHERE artist_id = ?", (artist_id,))
        for c in collections:
            db.execute(
                """INSERT INTO artist_collections
                   (artist_id, collection_id, title, type, artwork_url, track_count, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    artist_id,
                    c.get("collection_id", ""),
                    c.get("title", ""),
                    c.get("type", ""),
                    c.get("artwork_url", ""),
                    c.get("track_count", 0),
                    now,
                ),
            )
        db.commit()
    logger.debug(
        "save_artist_collections: artist_id=%s count=%s", artist_id, len(collections)
    )


def save_artist_uploads(artist_id: str, uploads: list[dict]) -> None:
    """Merge INSERTS into artist_uploads (cumulative history).

    Unlike the stateless DELETE+INSERT snapshots used for collections and
    likes, uploads are fetched as a delta on refresh and carry durable
    ``last_seeded_at`` on each row.  A DELETE here would wipe the full
    upload history every time a new track is posted; ``INSERT OR IGNORE``
    plus the ``UNIQUE(artist_id, track_id)`` constraint merges new rows in
    without touching existing ones.
    """
    now = datetime.now(UTC).isoformat()
    with db_scope(_get_db) as db:
        for t in uploads:
            db.execute(
                """INSERT OR IGNORE INTO artist_uploads
                   (artist_id, track_id, title, channel, duration, like_count,
                    view_count, repost_count, genre, url, uploader_id, permalink,
                    thumbnail_url, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artist_id,
                    t.get("track_id", ""),
                    t.get("title", ""),
                    t.get("channel", ""),
                    t.get("duration", 0),
                    t.get("like_count", 0),
                    t.get("view_count", 0),
                    t.get("repost_count", 0),
                    t.get("genre", ""),
                    t.get("url", ""),
                    t.get("uploader_id", ""),
                    t.get("permalink", ""),
                    t.get("thumbnail_url", ""),
                    now,
                ),
            )
        db.commit()
    logger.debug("save_artist_uploads: artist_id=%s count=%s", artist_id, len(uploads))


def save_artist_likes(artist_id: str, likes: list[dict]) -> None:
    """DELETE + INSERT artist_liked_tracks for this artist."""
    now = datetime.now(UTC).isoformat()
    with db_scope(_get_db) as db:
        db.execute("DELETE FROM artist_liked_tracks WHERE artist_id = ?", (artist_id,))
        for t in likes:
            db.execute(
                """INSERT OR IGNORE INTO artist_liked_tracks
                   (artist_id, track_id, title, channel, duration, like_count,
                    view_count, url, uploader_id, permalink, thumbnail_url, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    artist_id,
                    t.get("track_id", ""),
                    t.get("title", ""),
                    t.get("channel", ""),
                    t.get("duration", 0),
                    t.get("like_count", 0),
                    t.get("view_count", 0),
                    t.get("url", ""),
                    t.get("uploader_id", ""),
                    t.get("permalink", ""),
                    t.get("thumbnail_url", ""),
                    now,
                ),
            )
        db.commit()
    logger.debug("save_artist_likes: artist_id=%s count=%s", artist_id, len(likes))


def delete_artist_cache(artist_id: str) -> None:
    """Delete all cached data for an artist."""
    with db_scope(_get_db) as db:
        db.execute("DELETE FROM artist_profile WHERE artist_id = ?", (artist_id,))
        db.execute("DELETE FROM artist_collections WHERE artist_id = ?", (artist_id,))
        db.execute("DELETE FROM artist_uploads WHERE artist_id = ?", (artist_id,))
        db.execute("DELETE FROM artist_liked_tracks WHERE artist_id = ?", (artist_id,))
        db.execute("DELETE FROM artist_cache_status WHERE artist_id = ?", (artist_id,))
        db.commit()
    logger.debug("delete_artist_cache: artist_id=%s", artist_id)


def get_cached_artist_profile(artist_id: str) -> dict | None:
    with db_scope(_get_db) as db:
        row = db.execute(
            "SELECT * FROM artist_profile WHERE artist_id = ?", (artist_id,)
        ).fetchone()
    if not row:
        logger.debug("get_cached_artist_profile: miss artist_id=%s", artist_id)
        return None
    logger.debug("get_cached_artist_profile: hit artist_id=%s", artist_id)
    return dict(row)


def get_cached_artist_collections(artist_id: str) -> list[dict]:
    with db_scope(_get_db) as db:
        rows = db.execute(
            "SELECT * FROM artist_collections WHERE artist_id = ? ORDER BY cached_at DESC",
            (artist_id,),
        ).fetchall()
    result = [dict(r) for r in rows]
    logger.debug(
        "get_cached_artist_collections: artist_id=%s count=%s", artist_id, len(result)
    )
    return result


def get_cached_artist_uploads(artist_id: str) -> list[dict]:
    with db_scope(_get_db) as db:
        rows = db.execute(
            "SELECT * FROM artist_uploads WHERE artist_id = ? ORDER BY cached_at DESC",
            (artist_id,),
        ).fetchall()
    result = [dict(r) for r in rows]
    for t in result:
        t["source"] = "soundcloud"
    logger.debug(
        "get_cached_artist_uploads: artist_id=%s count=%s", artist_id, len(result)
    )
    return result


def get_cached_artist_likes(artist_id: str) -> list[dict]:
    with db_scope(_get_db) as db:
        rows = db.execute(
            "SELECT * FROM artist_liked_tracks WHERE artist_id = ? ORDER BY cached_at DESC",
            (artist_id,),
        ).fetchall()
    result = [dict(r) for r in rows]
    for t in result:
        t["source"] = "soundcloud"
    logger.debug(
        "get_cached_artist_likes: artist_id=%s count=%s", artist_id, len(result)
    )
    return result


TTL_HOURS: dict[str, int] = {
    "profile": 168,
    "uploads": 48,
    "collections": 48,
    "likes": 48,
}


def _mark_cached(
    db: sqlite3.Connection, artist_id: str, category: str, cached_at: str
) -> None:
    """Record a successful fetch timestamp. Caller is responsible for commit()."""
    db.execute(
        "INSERT OR REPLACE INTO artist_cache_status (artist_id, category, cached_at) VALUES (?, ?, ?)",
        (artist_id, category, cached_at),
    )


def needs_artist_refresh(artist_id: str, category: str) -> bool:
    """Return True if this category is stale or missing for the given artist."""
    ttl_hours = TTL_HOURS.get(category, 48)
    if category not in TTL_HOURS:
        logger.warning("needs_artist_refresh: unknown category %s", category)
        return False
    with db_scope(_get_db) as db:
        row = db.execute(
            "SELECT cached_at FROM artist_cache_status WHERE artist_id = ? AND category = ?",
            (artist_id, category),
        ).fetchone()
    if row is None:
        logger.debug(
            "needs_artist_refresh: miss artist_id=%s category=%s", artist_id, category
        )
        return True
    cached_at = row["cached_at"]
    if not cached_at:
        logger.debug(
            "needs_artist_refresh: no_ts artist_id=%s category=%s", artist_id, category
        )
        return True
    try:
        cached_dt = datetime.fromisoformat(cached_at)
        age = datetime.now(UTC) - cached_dt
        needs = age.total_seconds() > ttl_hours * 3600
        logger.debug(
            "needs_artist_refresh: artist_id=%s category=%s age=%.1fh needs=%s",
            artist_id,
            category,
            age.total_seconds() / 3600,
            needs,
        )
        return needs
    except Exception:
        logger.exception(
            "needs_artist_refresh: failed to parse cached_at for %s", artist_id
        )
        return True


def save_feed(tracks: list[dict]) -> None:
    """Replace the stored feed with a new batch of tracks."""
    with db_scope(_get_db) as db:
        now = datetime.now(UTC).isoformat()
        try:
            db.execute("BEGIN")
            db.execute("DELETE FROM feed_tracks")
            for t in tracks:
                db.execute(
                    """
                    INSERT INTO feed_tracks
                        (track_id, title, channel, duration, like_count, view_count,
                         genre, url, uploader_id, permalink, thumbnail_url, score,
                         added_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        t.get("yt_id", ""),
                        t.get("title", ""),
                        t.get("channel", ""),
                        t.get("duration", 0),
                        t.get("like_count", 0),
                        t.get("view_count", 0),
                        t.get("genre", ""),
                        t.get("url", ""),
                        t.get("uploader_id", ""),
                        t.get("permalink", ""),
                        t.get("thumbnail_url", ""),
                        t.get("like_count", 0) or 0,
                        now,
                    ),
                )
            db.commit()
            logger.debug("save_feed: saved_count=%s", len(tracks))
        except Exception:
            db.rollback()
            raise


def load_feed() -> list[dict]:
    """Return all feed tracks ordered by score descending."""
    with db_scope(_get_db) as db:
        rows = db.execute(
            """
            SELECT track_id, title, channel, duration, like_count, view_count,
                   genre, url, uploader_id, permalink, thumbnail_url, score,
                   consumed, consumed_at, added_at
            FROM feed_tracks
            ORDER BY score DESC
            """,
        ).fetchall()
        logger.debug("load_feed: row_count=%s", len(rows))
        return [
            {
                "yt_id": r["track_id"],
                "title": r["title"],
                "channel": r["channel"],
                "duration": r["duration"],
                "like_count": r["like_count"],
                "view_count": r["view_count"],
                "genre": r["genre"],
                "url": r["url"],
                "uploader_id": r["uploader_id"],
                "permalink": r["permalink"],
                "thumbnail_url": r["thumbnail_url"],
                "source": "soundcloud",
                "score": r["score"],
                "consumed": bool(r["consumed"]),
                "consumed_at": r["consumed_at"],
                "added_at": r["added_at"],
            }
            for r in rows
        ]


def get_feed_age() -> float:
    """Return hours since the feed was last generated, or infinity if empty."""
    with db_scope(_get_db) as db:
        row = db.execute(
            "SELECT MAX(added_at) AS max_added FROM feed_tracks"
        ).fetchone()
        if not row or not row["max_added"]:
            return float("inf")
        try:
            added = datetime.fromisoformat(row["max_added"])
            age = datetime.now(UTC) - added
            age_h = age.total_seconds() / 3600
            logger.debug("get_feed_age: age=%.1fh", age_h)
            return age_h
        except Exception:
            logger.exception("get_feed_age: failed to parse max_added timestamp")
            return float("inf")
