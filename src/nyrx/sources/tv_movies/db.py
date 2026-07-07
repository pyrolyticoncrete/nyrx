# SPDX-License-Identifier: AGPL-3.0-only

"""TV/Movies SQLite storage: bookmarks, seasons, episodes.

Mirrors ``sources/soundcloud/db.py`` patterns:
  - Module-level ``_get_db()`` that inits + returns connection
  - ``_row_to_*`` converters
  - Per-operation functions: save, load, delete

Schema follows the locked decisions from ``documentation/Todo:Watch.md``:
  - ``bookmarks`` has ``vote_count`` and ``number_of_episodes``, no ``imdb_id`` or ``status`` (#17, #21, #25)
  - ``tv_seasons`` + ``tv_episodes`` with ``ON DELETE CASCADE`` FKs
  - No ``watched`` column in ``tv_episodes`` (#22: source of truth is ``watch_history.db``)
"""

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime

from nyrx.config import TV_DB_PATH, TV_THUMBS_DIR
from nyrx.helpers import db_scope

logger = logging.getLogger(__name__)


_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;
PRAGMA synchronous=NORMAL;

CREATE TABLE IF NOT EXISTS bookmarks (
    tmdb_id INTEGER PRIMARY KEY,
    title TEXT,
    media_type TEXT,              -- 'movie' | 'tv'
    year TEXT,
    rating REAL,
    vote_count INT,               -- for "★ 7.9(6)" display
    poster_path TEXT,
    tagline TEXT,                  -- movies only, NULL for tv
    overview TEXT,
    genres TEXT,                   -- JSON array of genre names
    runtime INT,                   -- movies only, NULL for tv
    season_count INT,              -- tv only, NULL for movies
    number_of_episodes INT,        -- tv only, NULL for movies
    enriched_at TEXT,
    bookmarked_at TEXT
);

CREATE TABLE IF NOT EXISTS tv_seasons (
    tmdb_id INTEGER,
    season_number INT,
    episode_count INT,
    name TEXT,
    poster_path TEXT,
    air_date TEXT,
    PRIMARY KEY (tmdb_id, season_number),
    FOREIGN KEY(tmdb_id) REFERENCES bookmarks(tmdb_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS tv_episodes (
    tmdb_id INTEGER,
    season_number INT,
    episode_number INT,
    name TEXT,
    still_path TEXT,
    overview TEXT,
    runtime INT,
    air_date TEXT,
    vote_average REAL,
    cached_at TEXT,
    PRIMARY KEY (tmdb_id, season_number, episode_number),
    FOREIGN KEY(tmdb_id, season_number) REFERENCES tv_seasons(tmdb_id, season_number) ON DELETE CASCADE
);
"""


def init_tv_db() -> None:
    """Create or migrate ``tv_data.db``.

    Idempotent: safe to call repeatedly.
    """
    TV_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_scope(lambda: sqlite3.connect(str(TV_DB_PATH))) as db:
        db.executescript(_SCHEMA_SQL)


def _get_db() -> sqlite3.Connection:
    init_tv_db()
    conn = sqlite3.connect(str(TV_DB_PATH))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


# ---------------------------------------------------------------------------
# Row converters
# ---------------------------------------------------------------------------


def _row_to_bookmark(r: sqlite3.Row) -> dict:
    return {
        "tmdb_id": r["tmdb_id"],
        "title": r["title"] or "",
        "media_type": r["media_type"] or "",
        "year": r["year"] or "",
        "rating": r["rating"] or 0.0,
        "vote_count": r["vote_count"] or 0,
        "poster_path": r["poster_path"] or "",
        "tagline": r["tagline"],
        "overview": r["overview"] or "",
        "genres": r["genres"] or "",
        "runtime": r["runtime"],
        "season_count": r["season_count"],
        "number_of_episodes": r["number_of_episodes"],
        "enriched_at": r["enriched_at"],
        "bookmarked_at": r["bookmarked_at"],
        "source": "tv_movies",
    }


# ---------------------------------------------------------------------------
# Bookmarks
# ---------------------------------------------------------------------------


def save_bookmark(data: dict) -> None:
    """Insert or replace a single bookmark row.

    ``data`` must contain at minimum ``tmdb_id``.
    All other fields default to sensible empty/None values.
    """
    now = datetime.now(UTC).isoformat()
    with db_scope(_get_db) as db:
        db.execute(
            """INSERT OR REPLACE INTO bookmarks
               (tmdb_id, title, media_type, year, rating, vote_count,
                poster_path, tagline, overview, genres, runtime,
                season_count, number_of_episodes, enriched_at, bookmarked_at)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                data["tmdb_id"],
                data.get("title", ""),
                data.get("media_type", ""),
                data.get("year", ""),
                data.get("rating", 0.0),
                data.get("vote_count", 0),
                data.get("poster_path", ""),
                data.get("tagline"),
                data.get("overview", ""),
                data.get("genres", ""),
                data.get("runtime"),
                data.get("season_count"),
                data.get("number_of_episodes"),
                data.get("enriched_at", now),
                data.get("bookmarked_at", now),
            ),
        )
        db.commit()
    logger.debug(
        "save_bookmark: tmdb_id=%s title=%s",
        data["tmdb_id"],
        data.get("title", "")[:40],
    )


def delete_bookmark(tmdb_id: int) -> None:
    """Delete a bookmark, cascaded seasons/episodes, and cached poster."""
    thumb = TV_THUMBS_DIR / f"{tmdb_id}.jpg"
    if thumb.exists():
        thumb.unlink()
    with db_scope(_get_db) as db:
        db.execute("DELETE FROM bookmarks WHERE tmdb_id = ?", (tmdb_id,))
        db.commit()
    logger.debug("delete_bookmark: tmdb_id=%s", tmdb_id)


def load_bookmarks() -> list[dict]:
    """Return all bookmarks ordered by most recently bookmarked first."""
    with db_scope(_get_db) as db:
        rows = db.execute(
            "SELECT * FROM bookmarks ORDER BY bookmarked_at DESC"
        ).fetchall()
        return [_row_to_bookmark(r) for r in rows]


def load_bookmark(tmdb_id: int) -> dict | None:
    """Return a single bookmark by tmdb_id, or None."""
    with db_scope(_get_db) as db:
        row = db.execute(
            "SELECT * FROM bookmarks WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        return _row_to_bookmark(row) if row else None


def bookmark_exists(tmdb_id: int) -> bool:
    """Return True if a bookmark with the given tmdb_id exists."""
    with db_scope(_get_db) as db:
        row = db.execute(
            "SELECT 1 FROM bookmarks WHERE tmdb_id = ?", (tmdb_id,)
        ).fetchone()
        return row is not None


# ---------------------------------------------------------------------------
# Seasons
# ---------------------------------------------------------------------------


def save_seasons(tmdb_id: int, seasons: list[dict]) -> None:
    """Replace all season rows for a given bookmark.

    Each dict in ``seasons`` must have ``season_number``.
    Optional keys: ``episode_count``, ``name``, ``poster_path``, ``air_date``.
    """
    with db_scope(_get_db) as db:
        db.execute("DELETE FROM tv_seasons WHERE tmdb_id = ?", (tmdb_id,))
        for s in seasons:
            db.execute(
                """INSERT INTO tv_seasons
                   (tmdb_id, season_number, episode_count, name, poster_path, air_date)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                (
                    tmdb_id,
                    s["season_number"],
                    s.get("episode_count", 0),
                    s.get("name", ""),
                    s.get("poster_path", ""),
                    s.get("air_date", ""),
                ),
            )
        db.commit()
    logger.debug("save_seasons: tmdb_id=%s count=%s", tmdb_id, len(seasons))


def load_seasons(tmdb_id: int) -> list[dict]:
    """Return all seasons for a bookmark, ordered by season_number."""
    with db_scope(_get_db) as db:
        rows = db.execute(
            "SELECT * FROM tv_seasons WHERE tmdb_id = ? ORDER BY season_number",
            (tmdb_id,),
        ).fetchall()
        return [dict(r) for r in rows]


# ---------------------------------------------------------------------------
# Episodes
# ---------------------------------------------------------------------------


def save_episodes(tmdb_id: int, season_number: int, episodes: list[dict]) -> None:
    """Replace all episode rows for a given season.

    Each dict in ``episodes`` must have ``episode_number``.
    Optional keys: ``name``, ``still_path``, ``overview``, ``runtime``, ``air_date``.
    Sets ``cached_at`` to the current time.
    """
    now = datetime.now(UTC).isoformat()
    with db_scope(_get_db) as db:
        db.execute(
            "DELETE FROM tv_episodes WHERE tmdb_id = ? AND season_number = ?",
            (tmdb_id, season_number),
        )
        for ep in episodes:
            db.execute(
                """INSERT INTO tv_episodes
                   (tmdb_id, season_number, episode_number, name, still_path,
                    overview, runtime, air_date, vote_average, cached_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    tmdb_id,
                    season_number,
                    ep["episode_number"],
                    ep.get("name", ""),
                    ep.get("still_path", ""),
                    ep.get("overview", ""),
                    ep.get("runtime"),
                    ep.get("air_date", ""),
                    ep.get("vote_average", 0),
                    now,
                ),
            )
        db.commit()
    logger.debug(
        "save_episodes: tmdb_id=%s season=%s count=%s",
        tmdb_id,
        season_number,
        len(episodes),
    )


def load_episodes(tmdb_id: int, season_number: int) -> list[dict]:
    """Return all episodes for a given season, ordered by episode_number."""
    with db_scope(_get_db) as db:
        rows = db.execute(
            """SELECT * FROM tv_episodes
               WHERE tmdb_id = ? AND season_number = ?
               ORDER BY episode_number""",
            (tmdb_id, season_number),
        ).fetchall()
        return [dict(r) for r in rows]
