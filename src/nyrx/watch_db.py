# SPDX-License-Identifier: AGPL-3.0-only

"""SQLite mirror of the tracker v4 JSONL event log.

Provides a read-optimised ``watch_history`` table queried by all four sources
(YouTube, SoundCloud, Radio excluded, TV/Movies) instead of scanning JSONL.

  - ``init_watch_db()`` / ``_get_db()``: schema creation, connection factory
  - 6 public query/mutation functions
  - ``sync_from_tracker()``: incremental offset-cursor sync worker

"""

from __future__ import annotations

import json
import logging
import sqlite3
import time

from nyrx.config import TRACKER_OFFSET_PATH, TRACKER_V4_PATH, WATCH_HISTORY_DB_PATH
from nyrx.helpers import db_scope

logger = logging.getLogger(__name__)

_SCHEMA_SQL = """
PRAGMA journal_mode=WAL;

CREATE TABLE IF NOT EXISTS watch_history (
    id             INTEGER PRIMARY KEY AUTOINCREMENT,
    _v             INTEGER NOT NULL DEFAULT 4,
    ts             INTEGER NOT NULL,
    source         TEXT,
    yt_id          TEXT,
    media_type     TEXT,
    season_number  INTEGER,
    episode_number INTEGER,
    title          TEXT,
    channel        TEXT,
    yt_channel_url TEXT,
    uploader_id    TEXT,
    permalink      TEXT,
    watched_secs   INTEGER NOT NULL DEFAULT 0,
    duration_secs  INTEGER NOT NULL DEFAULT 0,
    reason         TEXT
);

CREATE INDEX IF NOT EXISTS idx_wh_lookup
    ON watch_history(source, yt_id, season_number, episode_number);
"""


def init_watch_db() -> None:
    """Create or migrate ``watch_history.db``.  Idempotent."""
    WATCH_HISTORY_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    with db_scope(lambda: sqlite3.connect(str(WATCH_HISTORY_DB_PATH))) as db:
        db.executescript(_SCHEMA_SQL)


def _get_db() -> sqlite3.Connection:
    init_watch_db()
    conn = sqlite3.connect(str(WATCH_HISTORY_DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _insert_entry(conn: sqlite3.Connection, entry: dict) -> None:
    """Map a single v4 JSONL entry dict to a ``watch_history`` row and INSERT.

    ``station_host`` / ``station_mount`` from the raw log are intentionally
    dropped: they're radio-only columns and radio rows never reach the DB.
    """
    conn.execute(
        """INSERT INTO watch_history
               (_v, ts, source, yt_id, media_type,
                season_number, episode_number,
                title, channel, yt_channel_url,
                uploader_id, permalink,
                watched_secs, duration_secs, reason)
           VALUES (?, ?, ?, ?, ?,
                   ?, ?,
                   ?, ?, ?,
                   ?, ?,
                   ?, ?, ?)""",
        (
            int(entry.get("_v", 4)),
            int(entry.get("ts", 0)),
            entry.get("source"),
            entry.get("yt_id"),
            entry.get("media_type"),
            entry.get("season_number"),
            entry.get("episode_number"),
            entry.get("title"),
            entry.get("channel"),
            entry.get("yt_channel_url"),
            entry.get("uploader_id"),
            entry.get("permalink"),
            int(entry.get("watched_secs", 0) or 0),
            int(entry.get("duration_secs", 0) or 0),
            entry.get("reason"),
        ),
    )


# ---------------------------------------------------------------------------
# Query functions
# ---------------------------------------------------------------------------


def get_movie_watched(yt_ids: list[str]) -> set[str]:
    """Return the subset of ``yt_ids`` that have been watched (≥80 %).

    Scoped to ``media_type='movie'``: TV episodes are checked via
    :func:`get_episode_status` instead.
    """
    if not yt_ids:
        return set()
    with db_scope(_get_db) as db:
        placeholders = ",".join("?" * len(yt_ids))
        rows = db.execute(
            f"""SELECT yt_id FROM watch_history
                 WHERE media_type='movie'
                   AND yt_id IN ({placeholders})
                   AND duration_secs > 0
                 GROUP BY yt_id
                 HAVING SUM(watched_secs) * 1.0 / MAX(duration_secs) >= 0.8
                    OR SUM(CASE WHEN reason='manual' THEN 1 ELSE 0 END) > 0""",
            yt_ids,
        )
        return {r[0] for r in rows}


def get_episode_status(yt_id: str) -> set[tuple[int, int]]:
    """Return ``{(season, episode), ...}`` for watched episodes of a series.

    Only ``media_type='tv'`` rows are considered.
    """
    with db_scope(_get_db) as db:
        rows = db.execute(
            """SELECT season_number, episode_number FROM watch_history
                 WHERE yt_id = ?
                   AND media_type = 'tv'
                   AND duration_secs > 0
                 GROUP BY season_number, episode_number
                 HAVING SUM(watched_secs) * 1.0 / MAX(duration_secs) >= 0.8
                    OR SUM(CASE WHEN reason='manual' THEN 1 ELSE 0 END) > 0""",
            (yt_id,),
        )
        return {(r["season_number"], r["episode_number"]) for r in rows}


def get_last_watched_season(yt_id: str) -> int | None:
    """Return the highest season number with any watch_history row, or ``None``.

    ``MAX()`` over an empty set returns ``NULL`` natively: no ``COALESCE``
    needed.
    """
    with db_scope(_get_db) as db:
        row = db.execute(
            """SELECT MAX(season_number) FROM watch_history
                 WHERE yt_id = ? AND media_type = 'tv'""",
            (yt_id,),
        ).fetchone()
        return row[0]  # None when no rows match


# ---------------------------------------------------------------------------
# Mutation functions  (mark / unmark via the ``w`` key)
# ---------------------------------------------------------------------------


def mark_watched(
    yt_id: str,
    media_type: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> None:
    """Insert a synthetic 'manual' row to mark an item as watched.

    ``watched_secs = duration_secs = 1``: meaningless placeholders, not used
    in any SUM/MAX ratio (``manual`` rows are only ever ``DELETE`` d by
    :func:`unmark_watched`, never aggregated with real playback rows).
    """
    with db_scope(_get_db) as db:
        db.execute(
            """INSERT INTO watch_history
                   (_v, ts, source, yt_id, media_type,
                    season_number, episode_number,
                    watched_secs, duration_secs, reason)
               VALUES (4, ?, 'tv_movies', ?, ?, ?, ?, 1, 1, 'manual')""",
            (int(time.time()), yt_id, media_type, season_number, episode_number),
        )
        db.commit()


def unmark_watched(
    yt_id: str,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> None:
    """Remove the synthetic 'manual' row for a previously marked item.

    Uses ``IS`` not ``=`` so that ``NULL`` (movie rows) binds and matches
    correctly: provably safe because ``manual`` is never emitted by mpv/Lua.
    """
    with db_scope(_get_db) as db:
        db.execute(
            """DELETE FROM watch_history
                 WHERE yt_id = ?
                   AND reason = 'manual'
                   AND season_number IS ?
                   AND episode_number IS ?""",
            (yt_id, season_number, episode_number),
        )
        db.commit()


# ---------------------------------------------------------------------------
# Sync worker  (incremental offset-cursor over tracker_v4.jsonl)
# ---------------------------------------------------------------------------


def sync_from_tracker() -> None:
    """Tail new lines from ``tracker_v4.jsonl`` and INSERT them into the DB.

    Algorithm
      - Read the byte offset from ``TRACKER_OFFSET_PATH`` (plain text, 0 if
        missing / empty).
      - Seek to that offset and iterate remaining lines.
      - Radio entries are skipped (they have no consumption semantics).
      - A malformed JSON line (= mpv caught mid-write) stops iteration:
        ``break``, not ``continue``: so the offset stays before the partial
        write and the line is retried on the next sync.
      - Rows are ``INSERT`` ed immediately per line; ``commit()`` runs
        **before** the offset is persisted so that a crash between the two
        causes at most harmless duplicate rows instead of silent data loss.

    Guarded by ``self._syncing`` in ``actions/playback.py`` (Phase 5): this
    function itself stays pure DB/IO logic.
    """
    if not TRACKER_V4_PATH.exists():
        return

    offset = 0
    if TRACKER_OFFSET_PATH.exists():
        try:
            offset = int(TRACKER_OFFSET_PATH.read_text().strip())
        except (ValueError, OSError):
            offset = 0

    conn = _get_db()
    last_good = offset

    try:
        with open(TRACKER_V4_PATH, "rb") as f:
            f.seek(offset)
            for line in f:
                if not line.strip():
                    last_good = f.tell()
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    break
                if entry.get("source") == "radio":
                    last_good = f.tell()
                    continue
                _insert_entry(conn, entry)
                last_good = f.tell()
        conn.commit()
    except Exception:
        logger.exception("sync_from_tracker: failed")
        conn.close()
        return

    if last_good != offset:
        try:
            TRACKER_OFFSET_PATH.write_text(str(last_good))
        except OSError:
            logger.exception("sync_from_tracker: failed to persist offset")

    conn.close()
