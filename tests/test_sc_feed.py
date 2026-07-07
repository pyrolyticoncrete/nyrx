# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SoundCloud feed module (D02).

``get_watched_secs`` and ``get_listened_ids`` read from the SQLite watch
history DB via ``watch_db._get_db()``: testable by pointing that function
at a real temp SQLite table.

``generate_feed`` requires heavy mocking of DB + API calls; we test
the outermost contract (empty feeds, no seeds) with minimal mocking.
"""

from __future__ import annotations

import sqlite3
from unittest.mock import MagicMock, patch

import pytest

from nyrx.sources.soundcloud.db import (
    _get_db,
    get_cached_artist_uploads,
    init_sc_db,
    save_artist_uploads,
    save_sc_likes,
)

_SCHEMA = """
CREATE TABLE watch_history (
    source         TEXT,
    yt_id          TEXT,
    watched_secs   INTEGER NOT NULL DEFAULT 0,
    duration_secs  INTEGER NOT NULL DEFAULT 0
)
"""


def _seed(conn: sqlite3.Connection, rows: list[tuple]) -> None:
    conn.executemany(
        "INSERT INTO watch_history (source, yt_id, watched_secs, duration_secs)"
        " VALUES (?, ?, ?, ?)",
        rows,
    )


def _liked(yt_id: str) -> dict:
    return {
        "yt_id": yt_id,
        "title": yt_id,
        "channel": "A",
        "duration": 300,
        "views": 1,
        "likes_count": 1,
        "thumbnail_url": "",
        "url": "",
        "uploader_id": "u",
        "permalink": yt_id,
    }


def _upload(track_id: str, like_count: int = 100) -> dict:
    return {
        "track_id": track_id,
        "title": track_id,
        "channel": "A",
        "like_count": like_count,
    }


def _follow(db: sqlite3.Connection, artist_id: str) -> None:
    db.execute(
        "INSERT INTO followed_artists (artist_id, permalink, name, url, followed_at)"
        " VALUES (?, ?, ?, ?, ?)",
        (
            artist_id,
            artist_id,
            artist_id,
            f"https://sc.com/{artist_id}",
            "2024-01-01T00:00:00",
        ),
    )


@pytest.fixture
def watch_conn(tmp_path) -> sqlite3.Connection:
    """Real temp ``watch_history`` table behind a patched ``watch_db._get_db``.

    The feed functions resolve the DB via ``watch_db._get_db()`` at call time,
    so patching that name means the real SQL aggregation runs against this
    hermetic table (no ``init_watch_db()``, no user state).
    """
    conn = sqlite3.connect(str(tmp_path / "watch_history.db"))
    conn.executescript(_SCHEMA)
    with patch("nyrx.watch_db._get_db", return_value=conn):
        yield conn
    conn.close()


@pytest.fixture
def sc_db(tmp_path, monkeypatch) -> None:
    """Real hermetic SoundCloud DB behind a patched ``SC_DB_PATH``."""
    monkeypatch.setattr("nyrx.sources.soundcloud.db.SC_DB_PATH", tmp_path / "sc.db")
    init_sc_db()


class TestGetWatchedSecs:
    def test_aggregates_watched_secs_per_id(self, watch_conn) -> None:
        _seed(
            watch_conn,
            [
                ("youtube", "a", 30, 100),
                ("youtube", "a", 45, 100),
                ("youtube", "b", 120, 300),
            ],
        )

        from nyrx.sources.soundcloud.feed import get_watched_secs

        assert get_watched_secs() == {"a": 75, "b": 120}

    def test_zero_duration_rows_are_filtered(self, watch_conn) -> None:
        _seed(watch_conn, [("youtube", "a", 50, 0)])

        from nyrx.sources.soundcloud.feed import get_watched_secs

        assert get_watched_secs() == {}

    def test_non_youtube_rows_are_excluded(self, watch_conn) -> None:
        _seed(watch_conn, [("soundcloud", "a", 999, 100)])

        from nyrx.sources.soundcloud.feed import get_watched_secs

        assert get_watched_secs() == {}

    def test_empty_table_returns_empty(self, watch_conn) -> None:
        from nyrx.sources.soundcloud.feed import get_watched_secs

        assert get_watched_secs() == {}


class TestGetListenedIds:
    def test_complete_flag_included(self, watch_conn) -> None:
        _seed(watch_conn, [("soundcloud", "done", 100, 100)])

        from nyrx.sources.soundcloud.feed import get_listened_ids

        assert "done" in get_listened_ids()

    def test_eighty_percent_ratio_included(self, watch_conn) -> None:
        _seed(watch_conn, [("soundcloud", "partial", 80, 100)])

        from nyrx.sources.soundcloud.feed import get_listened_ids

        assert "partial" in get_listened_ids()

    def test_below_eighty_percent_excluded(self, watch_conn) -> None:
        _seed(watch_conn, [("soundcloud", "partial", 79, 100)])

        from nyrx.sources.soundcloud.feed import get_listened_ids

        assert "partial" not in get_listened_ids()

    def test_non_soundcloud_rows_are_excluded(self, watch_conn) -> None:
        _seed(watch_conn, [("youtube", "yt", 80, 100)])

        from nyrx.sources.soundcloud.feed import get_listened_ids

        assert get_listened_ids() == set()

    def test_zero_duration_does_not_crash(self, watch_conn) -> None:
        _seed(watch_conn, [("soundcloud", "zero_dur", 0, 0)])

        from nyrx.sources.soundcloud.feed import get_listened_ids

        assert "zero_dur" not in get_listened_ids()

    def test_empty_table_returns_empty_set(self, watch_conn) -> None:
        from nyrx.sources.soundcloud.feed import get_listened_ids

        assert get_listened_ids() == set()


class TestGenerateFeed:
    def test_empty_seeds_returns_empty_list(self) -> None:
        """When no seeds exist (no liked tracks, no followed artists),
        the feed should be empty."""
        db = MagicMock()
        # All queries return empty results
        db.execute.return_value.fetchall.return_value = []
        db.execute.return_value.fetchone.return_value = None

        with patch("nyrx.sources.soundcloud.feed._get_db", return_value=db):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result == []

    def test_previously_seeded_upload_is_not_reselected(
        self, sc_db, watch_conn
    ) -> None:
        """BUG-4 regression: the merge refresh keeps ``last_seeded_at`` on the
        old upload, so rotation picks the new (unseeded) upload: a wiped
        stamp would re-seed the old track every run."""
        # Old upload seeded in a previous run (stamp intact)
        save_artist_uploads(
            "artist_1",
            [
                {"track_id": "old", "title": "Old", "channel": "A", "like_count": 500},
            ],
        )
        db = _get_db()
        db.execute(
            "UPDATE artist_uploads SET last_seeded_at = ? WHERE track_id = ?",
            ("2024-01-01T00:00:00", "old"),
        )
        _follow(db, "artist_1")
        db.commit()
        db.close()

        # Delta refresh delivers only the new track (merge keeps old + stamp)
        save_artist_uploads(
            "artist_1",
            [
                {"track_id": "new", "title": "New", "channel": "A", "like_count": 100},
            ],
        )

        new_track = {
            "yt_id": "new",
            "title": "New",
            "channel": "A",
            "duration": 300,
            "like_count": 100,
            "view_count": 10,
        }
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                return_value=[new_track],
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result and result[0]["yt_id"] == "new"

        cached = get_cached_artist_uploads("artist_1")
        old_row = next(t for t in cached if t["track_id"] == "old")
        new_row = next(t for t in cached if t["track_id"] == "new")
        # Old was not re-seeded (stamp intact); new got stamped by rotation
        assert old_row["last_seeded_at"] == "2024-01-01T00:00:00"
        assert new_row["last_seeded_at"] is not None

    def _seed_artist_upload(self, artist_id: str, track_id: str) -> None:
        """One followed artist with one eligible upload (the artist seed)."""
        save_artist_uploads(artist_id, [_upload(track_id)])
        db = _get_db()
        _follow(db, artist_id)
        db.commit()
        db.close()

    def test_filter_pipeline_excludes_and_dedups(self, sc_db, watch_conn) -> None:
        """Expansion dedup, 900s cap, and liked/listened exclusions."""
        self._seed_artist_upload("artist_1", "seedA")
        _seed(watch_conn, [("soundcloud", "listened1", 100, 100)])

        expansion = [
            {
                "yt_id": "clean1",
                "title": "Clean 1",
                "duration": 300,
                "like_count": 10,
                "view_count": 5,
            },
            {
                "yt_id": "clean1",
                "title": "Clean 1",
                "duration": 300,
                "like_count": 10,
                "view_count": 5,
            },
            {
                "yt_id": "toolong",
                "title": "Long",
                "duration": 901,
                "like_count": 5,
                "view_count": 5,
            },
            {
                "yt_id": "listened1",
                "title": "Heard",
                "duration": 300,
                "like_count": 5,
                "view_count": 5,
            },
            {
                "yt_id": "liked1",
                "title": "Liked",
                "duration": 300,
                "like_count": 5,
                "view_count": 5,
            },
            {
                "yt_id": "clean2",
                "title": "Clean 2",
                "duration": 300,
                "like_count": 8,
                "view_count": 5,
            },
        ]
        with (
            patch(
                "nyrx.sources.soundcloud.feed.load_sc_likes",
                return_value=[{"yt_id": "liked1"}],
            ),
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                return_value=expansion,
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert [t["yt_id"] for t in result] == ["clean1", "clean2"]

    def test_scoring_and_top_30_cap(self, sc_db, watch_conn) -> None:
        """like_count desc, view_count tiebreak, exactly 30 returned."""
        self._seed_artist_upload("artist_1", "seedA")

        expansion = [
            {
                "yt_id": f"t{i}",
                "title": f"T{i}",
                "duration": 300,
                "like_count": i % 3,
                "view_count": i,
            }
            for i in range(35)
        ]
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                return_value=expansion,
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert len(result) == 30
        pairs = [(t["like_count"], t["view_count"]) for t in result]
        assert pairs == sorted(pairs, reverse=True)
        # Highest like_count is 2; within that group the largest view is t32
        assert result[0]["yt_id"] == "t32"

    def test_seed_rotation_persists_last_seeded_at(self, sc_db, watch_conn) -> None:
        """Liked + artist seeds are stamped so rotation advances."""
        save_sc_likes([_liked("Lseed")])
        save_artist_uploads("artist_1", [_upload("u1"), _upload("u2", like_count=90)])
        db = _get_db()
        _follow(db, "artist_1")
        _follow(db, "artist_2")
        db.commit()
        db.close()

        clean = {
            "yt_id": "clean",
            "title": "Clean",
            "channel": "A",
            "duration": 300,
            "like_count": 5,
            "view_count": 5,
        }
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                return_value=[clean],
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result and result[0]["yt_id"] == "clean"

        db = _get_db()
        liked = db.execute(
            "SELECT last_seeded_at FROM liked_tracks WHERE track_id = 'Lseed'"
        ).fetchone()
        artist = db.execute(
            "SELECT last_seeded_at FROM followed_artists WHERE artist_id = 'artist_1'"
        ).fetchone()
        upload = db.execute(
            "SELECT last_seeded_at FROM artist_uploads WHERE track_id = 'u1'"
        ).fetchone()
        db.close()
        assert liked["last_seeded_at"] is not None
        assert artist["last_seeded_at"] is not None
        assert upload["last_seeded_at"] is not None

    def test_artist_top_up_when_no_liked_tracks(self, sc_db, watch_conn) -> None:
        """No liked tracks → feed tops up with more followed artists.

        artist_2 has no cached uploads, so rotation must skip it and draw a
        second seed (artist_3) from the artist top-up path.
        """
        save_artist_uploads("artist_1", [_upload("u1")])
        save_artist_uploads("artist_3", [_upload("u3", like_count=80)])
        db = _get_db()
        _follow(db, "artist_1")
        _follow(db, "artist_2")
        _follow(db, "artist_3")
        db.commit()
        db.close()

        clean = {
            "yt_id": "clean",
            "title": "Clean",
            "channel": "A",
            "duration": 300,
            "like_count": 5,
            "view_count": 5,
        }
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                return_value=[clean],
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result and result[0]["yt_id"] == "clean"

        db = _get_db()
        u1 = db.execute(
            "SELECT last_seeded_at FROM artist_uploads WHERE track_id = 'u1'"
        ).fetchone()
        u3 = db.execute(
            "SELECT last_seeded_at FROM artist_uploads WHERE track_id = 'u3'"
        ).fetchone()
        db.close()
        # Both the first-pass artist and the topped-up artist were seeded
        assert u1["last_seeded_at"] is not None
        assert u3["last_seeded_at"] is not None

    def test_station_fetch_failure_skips_only_that_seed(
        self, sc_db, watch_conn
    ) -> None:
        """fetch_station_ids raising must not abort the whole feed."""
        save_sc_likes([_liked("L1"), _liked("L2")])
        clean = {
            "yt_id": "clean",
            "title": "Clean",
            "channel": "A",
            "duration": 300,
            "like_count": 5,
            "view_count": 5,
        }
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids",
                side_effect=[RuntimeError("boom"), ["st1"]],
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                return_value=[clean],
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result and result[0]["yt_id"] == "clean"

    def test_all_seeds_fail_returns_empty(self, sc_db, watch_conn) -> None:
        """Every seed's station fetch empty → no expanded tracks → []."""
        save_sc_likes([_liked("L1")])
        with patch("nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=[]):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result == []

    def test_batch_client_id_failure_falls_back_to_ytdlp(
        self, sc_db, watch_conn
    ) -> None:
        """RuntimeError from the client_id batch falls back to yt-dlp."""
        save_sc_likes([_liked("L1")])
        clean = {
            "yt_id": "clean",
            "title": "Clean",
            "channel": "A",
            "duration": 300,
            "like_count": 5,
            "view_count": 5,
        }
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_ytdlp",
                return_value=[clean],
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result and result[0]["yt_id"] == "clean"

    def test_all_expansion_failures_return_empty(self, sc_db, watch_conn) -> None:
        """Both batch paths failing → seed skipped → []."""
        save_sc_likes([_liked("L1")])
        with (
            patch(
                "nyrx.sources.soundcloud.feed.fetch_station_ids", return_value=["st1"]
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_client_id",
                side_effect=RuntimeError("boom"),
            ),
            patch(
                "nyrx.sources.soundcloud.feed.batch_resolve_via_ytdlp",
                side_effect=RuntimeError("boom"),
            ),
        ):
            from nyrx.sources.soundcloud.feed import generate_feed

            result = generate_feed()

        assert result == []
