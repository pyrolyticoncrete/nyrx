# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import sqlite3
from datetime import UTC, datetime
from typing import Any

from nyrx import watch_db
from nyrx.helpers import db_scope

from .api import batch_resolve_via_client_id, batch_resolve_via_ytdlp, fetch_station_ids
from .db import _get_db, load_sc_likes

logger = logging.getLogger(__name__)


def _pick_artist_top_track(
    db: sqlite3.Connection, artist_id: int
) -> sqlite3.Row | None:
    """Pick the rotation-aware next track from an artist's top 10 uploads.

    Selects the top 10 uploads by ``like_count`` (capping at 15 min), then
    returns the least-recently-seeded one so repeated feed regens rotate.
    """
    return db.execute(
        """
        WITH top10 AS (
            SELECT track_id, last_seeded_at FROM artist_uploads
            WHERE artist_id = ? AND (duration IS NULL OR duration <= 900)
            ORDER BY like_count DESC NULLS LAST LIMIT 10
        )
        SELECT track_id FROM top10
        ORDER BY
            CASE WHEN last_seeded_at IS NULL THEN 0 ELSE 1 END,
            last_seeded_at ASC
        LIMIT 1
        """,
        (artist_id,),
    ).fetchone()


def get_watched_secs() -> dict[str, int]:
    """Sum ``watched_secs`` per YouTube yt_id from the watch history DB.

    Hardcoded to ``source='youtube'``: the only consumer (search checkbox)
    excludes SoundCloud and tv_movies at display time.
    """
    with db_scope(watch_db._get_db) as conn:
        rows = conn.execute(
            "SELECT yt_id, SUM(watched_secs) FROM watch_history "
            "WHERE source='youtube' AND duration_secs>0 GROUP BY yt_id"
        )
        result = {r[0]: r[1] for r in rows}
    logger.debug("get_watched_secs: tracks=%s", len(result))
    return result


def get_listened_ids() -> set[str]:
    """Return a set of SoundCloud track IDs that have been fully listened (≥80%).
    Reads from the watch history DB, hardcoded to ``source='soundcloud'``.
    """
    with db_scope(watch_db._get_db) as conn:
        rows = conn.execute(
            "SELECT yt_id FROM watch_history WHERE source='soundcloud' AND duration_secs>0 "
            "GROUP BY yt_id HAVING SUM(watched_secs)*1.0/MAX(duration_secs)>=0.8"
        )
        result = {r[0] for r in rows}
    logger.debug("get_listened_ids: tracks=%s", len(result))
    return result


def generate_feed() -> list[dict]:
    """Full feed generation pipeline.  Returns top 30 scored tracks.

    1. Pick up to 3 seeds from the rotation pool:
       - Up to 2 liked tracks (by ``last_seeded_at`` rotation).
       - Up to 2 followed-artist tracks (artist rotation, then top-10
         upload by ``like_count`` with track-level rotation).
       - Top up from liked tracks if room remains.
       - If no liked tracks exist, top up from more followed artists.
    2. Expand each seed via its station endpoint -> up to 49 IDs each.
    3. Fetch full metadata via client_id batch API (per seed, ≤50 IDs each).
    4. Filter pipeline: dedup, duration <= 900 s, exclude liked & listened.
    5. Score by ``like_count`` desc / ``view_count`` tiebreaker -> top 30.
    6. Update ``last_seeded_at`` on used seeds (artist + track level).
    """
    with db_scope(_get_db) as db:
        now = datetime.now(UTC).isoformat()
        seeds: list[dict[str, Any]] = []

        # --- Liked-track seeds (up to 2) ---
        liked_rows = db.execute(
            """
            SELECT track_id, liked_at, last_seeded_at
            FROM liked_tracks
            WHERE duration IS NULL OR duration <= 900
            ORDER BY
                CASE WHEN last_seeded_at IS NULL THEN 0 ELSE 1 END,
                CASE WHEN last_seeded_at IS NULL THEN liked_at ELSE NULL END DESC,
                last_seeded_at ASC
            LIMIT 2
            """,
        ).fetchall()
        for r in liked_rows:
            seeds.append(
                {"type": "liked", "track_id": r["track_id"], "artist_id": None}
            )

        # --- Artist seeds (up to 2) ---
        artist_rows = db.execute(
            """
            SELECT artist_id, followed_at, last_seeded_at
            FROM followed_artists
            ORDER BY
                CASE WHEN last_seeded_at IS NULL THEN 0 ELSE 1 END,
                CASE WHEN last_seeded_at IS NULL THEN followed_at ELSE NULL END DESC,
                last_seeded_at ASC
            LIMIT 2
            """,
        ).fetchall()
        for ar in artist_rows:
            if len(seeds) >= 3:
                break
            row = _pick_artist_top_track(db, ar["artist_id"])
            if not row:
                continue
            seeds.append(
                {
                    "type": "artist",
                    "track_id": row["track_id"],
                    "artist_id": ar["artist_id"],
                }
            )

        # --- Top up with more liked tracks if we still have room ---
        if len(seeds) < 3:
            liked_tids = [s["track_id"] for s in seeds if s["type"] == "liked"]
            remaining = 3 - len(seeds)
            if liked_tids:
                placeholders = ",".join("?" * len(liked_tids))
                more = db.execute(
                    f"SELECT track_id FROM liked_tracks "
                    f"WHERE (duration IS NULL OR duration <= 900) "
                    f"  AND track_id NOT IN ({placeholders}) "
                    f"ORDER BY liked_at DESC LIMIT ?",
                    liked_tids + [remaining],
                ).fetchall()
            else:
                more = db.execute(
                    "SELECT track_id FROM liked_tracks "
                    "WHERE duration IS NULL OR duration <= 900 "
                    "ORDER BY liked_at DESC LIMIT ?",
                    [remaining],
                ).fetchall()
            for r in more:
                seeds.append(
                    {"type": "liked", "track_id": r["track_id"], "artist_id": None}
                )

        # --- Artist top-up if still < 3 and no liked tracks to draw from ---
        if len(seeds) < 3 and not liked_rows:
            remaining = 3 - len(seeds)
            used_artist_ids = [
                s["artist_id"]
                for s in seeds
                if s.get("type") == "artist" and s.get("artist_id")
            ]
            if used_artist_ids:
                placeholders = ",".join("?" * len(used_artist_ids))
                more_artists = db.execute(
                    f"SELECT artist_id FROM followed_artists "
                    f"WHERE artist_id NOT IN ({placeholders}) "
                    f"ORDER BY "
                    f"    CASE WHEN last_seeded_at IS NULL THEN 0 ELSE 1 END, "
                    f"    CASE WHEN last_seeded_at IS NULL THEN followed_at ELSE NULL END DESC, "
                    f"    last_seeded_at ASC LIMIT ?",
                    used_artist_ids + [remaining],
                ).fetchall()
            else:
                more_artists = db.execute(
                    "SELECT artist_id FROM followed_artists "
                    "ORDER BY "
                    "    CASE WHEN last_seeded_at IS NULL THEN 0 ELSE 1 END, "
                    "    CASE WHEN last_seeded_at IS NULL THEN followed_at ELSE NULL END DESC, "
                    "    last_seeded_at ASC LIMIT ?",
                    [remaining],
                ).fetchall()
            for ar in more_artists:
                if len(seeds) >= 3:
                    break
                row = _pick_artist_top_track(db, ar["artist_id"])
                if not row:
                    continue
                seeds.append(
                    {
                        "type": "artist",
                        "track_id": row["track_id"],
                        "artist_id": ar["artist_id"],
                    }
                )

        if not seeds:
            logger.debug("generate_feed: no_seeds")
            return []

        # --- Expand each seed independently and collect metadata ---
        all_tracks: list[dict] = []
        seen: set[str] = set()
        logger.debug("generate_feed: seeds=%s", len(seeds))

        for s in seeds:
            try:
                station_ids = fetch_station_ids(s["track_id"])
            except Exception:
                logger.debug(
                    "generate_feed: fetch_station_ids failed for track_id=%s, skipping",
                    s["track_id"],
                )
                continue
            if not station_ids:
                continue
            try:
                seed_tracks = batch_resolve_via_client_id(station_ids)
            except RuntimeError:
                try:
                    seed_tracks = batch_resolve_via_ytdlp(s["track_id"], count=30)
                except RuntimeError:
                    continue
            for t in seed_tracks:
                tid = t.get("yt_id", "")
                if not tid or tid in seen:
                    continue
                seen.add(tid)
                all_tracks.append(t)

        if not all_tracks:
            logger.debug("generate_feed: no_expanded_tracks")
            return []

        # --- Filter pipeline ---
        liked_ids = {t.get("yt_id", "") for t in load_sc_likes()}
        listened_ids = get_listened_ids()

        filtered: list[dict] = []
        filtered_seen: set[str] = set()
        for t in all_tracks:
            tid = t.get("yt_id", "")
            if not tid or tid in filtered_seen:
                continue
            if t.get("duration", 0) > 900:
                continue
            if tid in liked_ids:
                continue
            if tid in listened_ids:
                continue
            filtered_seen.add(tid)
            filtered.append(t)

        if not filtered:
            logger.debug(
                "generate_feed: all_filtered_out total_before=%s", len(all_tracks)
            )
            return []

        # --- Score and top 30 ---
        filtered.sort(
            key=lambda t: ((t.get("like_count") or 0), (t.get("view_count") or 0)),
            reverse=True,
        )
        top30 = filtered[:30]

        # --- Update seed rotation ---
        for s in seeds:
            if s["type"] == "liked":
                db.execute(
                    "UPDATE liked_tracks SET last_seeded_at = ? WHERE track_id = ?",
                    (now, s["track_id"]),
                )
            elif s["type"] == "artist" and s["artist_id"]:
                db.execute(
                    "UPDATE followed_artists SET last_seeded_at = ? WHERE artist_id = ?",
                    (now, s["artist_id"]),
                )
                db.execute(
                    "UPDATE artist_uploads SET last_seeded_at = ? WHERE track_id = ?",
                    (now, s["track_id"]),
                )
        db.commit()

        logger.debug(
            "generate_feed: expanded=%s filtered=%s top30=%s",
            len(all_tracks),
            len(filtered),
            len(top30),
        )
        return top30
