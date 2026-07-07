# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SoundCloud SQLite storage layer (D03).

All tests use ``tmp_path`` for a hermetic database: no filesystem
interactions outside the temp directory.
"""

from __future__ import annotations

import sqlite3
from concurrent.futures import ThreadPoolExecutor, wait
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from nyrx.sources.soundcloud.db import (
    _get_db,
    _row_to_followed,
    _row_to_liked,
    delete_artist_cache,
    get_cached_artist_collections,
    get_cached_artist_likes,
    get_cached_artist_profile,
    get_cached_artist_uploads,
    get_feed_age,
    init_sc_db,
    load_feed,
    load_sc_followed,
    load_sc_likes,
    needs_artist_refresh,
    save_artist_collections,
    save_artist_likes,
    save_artist_profile,
    save_artist_uploads,
    save_feed,
    save_sc_followed,
    save_sc_likes,
)


@pytest.fixture(autouse=True)
def _hermetic_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect the SC DB file to tmp_path for every test."""
    monkeypatch.setattr("nyrx.sources.soundcloud.db.SC_DB_PATH", tmp_path / "sc.db")
    init_sc_db()


class TestSchema:
    def test_init_twice_is_idempotent(self) -> None:
        init_sc_db()  # second call should not raise
        init_sc_db()  # third call should not raise


class TestWalAndBusyTimeout:
    def test_wal_mode_persistent(self) -> None:
        db = _get_db()
        assert db.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
        db.close()

    def test_busy_timeout_set_per_connection(self) -> None:
        db = _get_db()
        assert db.execute("PRAGMA busy_timeout").fetchone()[0] == 5000
        db.close()


class TestRowConversion:
    def test_row_to_liked(self) -> None:
        class FakeRow:
            def __getitem__(self, key: str):
                mapping = {
                    "track_id": "abc123",
                    "title": "Test Track",
                    "channel": "Test Artist",
                    "duration": 180.0,
                    "view_count": 1500,
                    "like_count": 50,
                    "thumbnail_url": "https://example.com/thumb.jpg",
                    "url": "https://soundcloud.com/test",
                    "uploader_id": "user_1",
                    "permalink": "test-track",
                    "liked_at": "2024-01-01T00:00:00",
                    "last_seeded_at": None,
                }
                return mapping[key]

        result = _row_to_liked(FakeRow())
        assert result["yt_id"] == "abc123"
        assert result["title"] == "Test Track"
        assert result["channel"] == "Test Artist"
        assert result["duration"] == 180.0
        assert result["views"] == 1500
        assert result["likes_count"] == 50
        assert result["source"] == "soundcloud"

    def test_row_to_followed(self) -> None:
        class FakeRow:
            def __getitem__(self, key: str):
                mapping = {
                    "artist_id": "artist_1",
                    "permalink": "test-artist",
                    "name": "Test Artist",
                    "url": "https://soundcloud.com/test-artist",
                    "followed_at": "2024-01-01T00:00:00",
                }
                return mapping[key]

        result = _row_to_followed(FakeRow())
        assert result["id"] == "artist_1"
        assert result["name"] == "Test Artist"
        assert result["permalink"] == "test-artist"


class TestLikesRoundTrip:
    def test_save_then_load(self) -> None:
        liked = [
            {
                "yt_id": "abc",
                "title": "Track A",
                "channel": "Artist A",
                "duration": 200,
                "views": 1000,
                "likes_count": 50,
                "thumbnail_url": "",
                "url": "",
                "uploader_id": "ua",
                "permalink": "track-a",
                "liked_at": datetime.now(UTC).isoformat(),
            },
        ]
        save_sc_likes(liked)
        loaded = load_sc_likes()
        assert len(loaded) == 1
        assert loaded[0]["yt_id"] == "abc"

    def test_load_empty(self) -> None:
        loaded = load_sc_likes()
        assert loaded == []

    def test_overwrite_replaces_data(self) -> None:
        t1 = [{"yt_id": "a"}]
        t2 = [{"yt_id": "b"}]
        save_sc_likes(t1)
        save_sc_likes(t2)
        loaded = load_sc_likes()
        assert len(loaded) == 1
        assert loaded[0]["yt_id"] == "b"


class TestFollowedRoundTrip:
    def test_save_then_load(self) -> None:
        followed = [
            {
                "id": "artist_1",
                "permalink": "test-artist",
                "name": "Test Artist",
                "url": "https://sc.com/test-artist",
                "followed_at": "2024-01-01T00:00:00",
            },
        ]
        save_sc_followed(followed)
        loaded = load_sc_followed()
        assert len(loaded) == 1
        assert loaded[0]["id"] == "artist_1"

    def test_load_empty(self) -> None:
        assert load_sc_followed() == []


class TestFeedRoundTrip:
    def test_save_then_load(self) -> None:
        tracks = [
            {
                "yt_id": "feed_1",
                "title": "Feed Track",
                "channel": "Feed Artist",
                "duration": 300,
                "like_count": 100,
                "view_count": 5000,
                "genre": "electronic",
                "url": "",
                "uploader_id": "fu",
                "permalink": "feed-track",
                "thumbnail_url": "",
            },
        ]
        save_feed(tracks)
        loaded = load_feed()
        assert len(loaded) == 1
        assert loaded[0]["yt_id"] == "feed_1"
        assert loaded[0]["source"] == "soundcloud"

    def test_empty_feed(self) -> None:
        assert load_feed() == []

    def test_overwrite_replaces(self) -> None:
        save_feed([{"yt_id": "a"}])
        save_feed([{"yt_id": "b"}])
        loaded = load_feed()
        assert len(loaded) == 1
        assert loaded[0]["yt_id"] == "b"


class TestArtistUploadsMerge:
    """save_artist_uploads merges (BUG-4 regression).

    Refetch returns only newly-posted tracks, so the save must not DELETE
    the existing upload history.  ``last_seeded_at`` lives on these rows,
    so wiping them would re-seed already-seeded tracks.
    """

    def test_save_preserves_existing_rows(self) -> None:
        old = [
            {"track_id": "old1", "title": "Old 1", "channel": "A"},
            {"track_id": "old2", "title": "Old 2", "channel": "A"},
        ]
        save_artist_uploads("artist_1", old)
        save_artist_uploads(
            "artist_1", [{"track_id": "new1", "title": "New 1", "channel": "A"}]
        )

        cached = get_cached_artist_uploads("artist_1")
        assert {t["track_id"] for t in cached} == {"old1", "old2", "new1"}
        assert len(cached) == 3

    def test_save_preserves_last_seeded_at_on_old_rows(self) -> None:
        save_artist_uploads(
            "artist_1", [{"track_id": "old1", "title": "Old", "channel": "A"}]
        )
        db = _get_db()
        db.execute(
            "UPDATE artist_uploads SET last_seeded_at = ? WHERE track_id = ?",
            ("2024-01-01T00:00:00", "old1"),
        )
        db.commit()
        db.close()

        save_artist_uploads(
            "artist_1", [{"track_id": "new1", "title": "New", "channel": "A"}]
        )

        cached = get_cached_artist_uploads("artist_1")
        old_row = next(t for t in cached if t["track_id"] == "old1")
        assert old_row["last_seeded_at"] == "2024-01-01T00:00:00"

    def test_save_deduplicates_existing_track(self) -> None:
        save_artist_uploads(
            "artist_1", [{"track_id": "t1", "title": "A", "channel": "C"}]
        )
        save_artist_uploads(
            "artist_1",
            [
                {"track_id": "t1", "title": "A", "channel": "C"},
                {"track_id": "t2", "title": "B", "channel": "C"},
            ],
        )

        cached = get_cached_artist_uploads("artist_1")
        tids = [t["track_id"] for t in cached]
        assert tids.count("t1") == 1
        assert set(tids) == {"t1", "t2"}


class TestNeedsArtistRefresh:
    """TTL boundary + failure paths for needs_artist_refresh."""

    def _set_cached_at(self, artist_id: str, category: str, value: str) -> None:
        db = _get_db()
        db.execute(
            "INSERT OR REPLACE INTO artist_cache_status (artist_id, category, cached_at) VALUES (?, ?, ?)",
            (artist_id, category, value),
        )
        db.commit()
        db.close()

    def test_stale_uploads_need_refresh(self) -> None:
        self._set_cached_at(
            "artist_1", "uploads", (datetime.now(UTC) - timedelta(hours=50)).isoformat()
        )
        assert needs_artist_refresh("artist_1", "uploads") is True

    def test_fresh_uploads_do_not_need_refresh(self) -> None:
        self._set_cached_at(
            "artist_1", "uploads", (datetime.now(UTC) - timedelta(hours=46)).isoformat()
        )
        assert needs_artist_refresh("artist_1", "uploads") is False

    def test_no_row_returns_true(self) -> None:
        assert needs_artist_refresh("artist_1", "uploads") is True

    def test_empty_cached_at_returns_true(self) -> None:
        self._set_cached_at("artist_1", "uploads", "")
        assert needs_artist_refresh("artist_1", "uploads") is True

    def test_unknown_category_returns_false(self) -> None:
        assert needs_artist_refresh("artist_1", "watched") is False

    def test_corrupt_cached_at_returns_true(self) -> None:
        self._set_cached_at("artist_1", "uploads", "not-a-timestamp")
        assert needs_artist_refresh("artist_1", "uploads") is True


class TestArtistCacheRoundTrip:
    """save_*/get_cached_* for profile/collections/likes + delete_artist_cache.

    Uploads are already round-tripped in TestArtistUploadsMerge.
    """

    def test_profile_round_trip(self) -> None:
        save_artist_profile(
            "artist_1",
            {
                "name": "Alice",
                "description": "d",
                "location": "l",
                "avatar_url": "a",
                "permalink": "alice",
                "followers_count": 100,
                "track_count": 20,
                "playlist_count": 3,
            },
        )
        got = get_cached_artist_profile("artist_1")
        assert got is not None
        assert got["name"] == "Alice"
        assert got["permalink"] == "alice"
        assert got["track_count"] == 20

    def test_profile_miss_returns_none(self) -> None:
        assert get_cached_artist_profile("artist_1") is None

    def test_collections_round_trip(self) -> None:
        save_artist_collections(
            "artist_1",
            [
                {
                    "collection_id": "c1",
                    "title": "Playlist",
                    "type": "playlist",
                    "artwork_url": "a",
                    "track_count": 5,
                },
            ],
        )
        got = get_cached_artist_collections("artist_1")
        assert len(got) == 1
        assert got[0]["collection_id"] == "c1"
        assert got[0]["title"] == "Playlist"

    def test_collections_getter_has_no_source_key(self) -> None:
        save_artist_collections("artist_1", [{"collection_id": "c1"}])
        got = get_cached_artist_collections("artist_1")
        assert "source" not in got[0]

    def test_likes_round_trip_adds_source(self) -> None:
        save_artist_likes(
            "artist_1", [{"track_id": "l1", "title": "Liked", "channel": "C"}]
        )
        got = get_cached_artist_likes("artist_1")
        assert len(got) == 1
        assert got[0]["track_id"] == "l1"
        assert got[0]["source"] == "soundcloud"

    def test_delete_artist_cache_empties_all_categories(self) -> None:
        save_artist_profile("artist_1", {"name": "A"})
        save_artist_collections("artist_1", [{"collection_id": "c1"}])
        save_artist_uploads(
            "artist_1", [{"track_id": "u1", "title": "U", "channel": "C"}]
        )
        save_artist_likes(
            "artist_1", [{"track_id": "l1", "title": "L", "channel": "C"}]
        )

        delete_artist_cache("artist_1")

        assert get_cached_artist_profile("artist_1") is None
        assert get_cached_artist_collections("artist_1") == []
        assert get_cached_artist_uploads("artist_1") == []
        assert get_cached_artist_likes("artist_1") == []

    def test_delete_artist_cache_idempotent(self) -> None:
        delete_artist_cache("artist_1")
        delete_artist_cache("artist_1")  # second call must not raise


class TestGetFeedAge:
    """get_feed_age: empty/fresh/corrupt/future added_at."""

    def test_empty_feed_is_infinity(self) -> None:
        assert get_feed_age() == float("inf")

    def test_fresh_feed_is_small_non_negative(self) -> None:
        save_feed([{"yt_id": "f1", "title": "F", "channel": "C"}])
        assert get_feed_age() >= 0

    def test_future_timestamp_returns_negative(self) -> None:
        """Clock-skew-backwards corner: age is negative (documented).

        The caller (`get_feed_age() > 24`) treats a negative age as fresh,
        which is the accepted behaviour.
        """
        db = _get_db()
        db.execute(
            "INSERT INTO feed_tracks (track_id, title, added_at) VALUES (?, ?, ?)",
            ("f1", "F", (datetime.now(UTC) + timedelta(hours=1)).isoformat()),
        )
        db.commit()
        db.close()
        assert get_feed_age() < 0

    def test_corrupt_timestamp_is_infinity(self) -> None:
        db = _get_db()
        db.execute(
            "INSERT INTO feed_tracks (track_id, title, added_at) VALUES (?, ?, ?)",
            ("f1", "F", "not-a-timestamp"),
        )
        db.commit()
        db.close()
        assert get_feed_age() == float("inf")


class TestConcurrency:
    def test_concurrent_writes_different_tables(self) -> None:
        """Write to likes, followed, and feed tables concurrently.

        Each table has its own ``DELETE/INSERT`` cycle, so they should
        not interfere with each other when called from separate threads.
        """

        def write_likes() -> None:
            save_sc_likes(
                [
                    {
                        "yt_id": "cl_1",
                        "title": "Concurrent Like",
                        "channel": "Artist",
                        "duration": 200,
                        "views": 1000,
                        "likes_count": 50,
                        "thumbnail_url": "",
                        "url": "",
                        "uploader_id": "cl",
                        "permalink": "cl-1",
                        "liked_at": datetime.now(UTC).isoformat(),
                    },
                ]
            )

        def write_followed() -> None:
            save_sc_followed(
                [
                    {
                        "id": "cf_1",
                        "permalink": "cf-artist",
                        "name": "CF Artist",
                        "url": "https://sc.com/cf-artist",
                        "followed_at": datetime.now(UTC).isoformat(),
                    },
                ]
            )

        def write_feed() -> None:
            save_feed(
                [
                    {
                        "yt_id": "cfd_1",
                        "title": "Concurrent Feed",
                        "channel": "Feed Artist",
                        "duration": 300,
                        "like_count": 100,
                        "view_count": 5000,
                        "genre": "electronic",
                        "url": "",
                        "uploader_id": "cfd",
                        "permalink": "cfd-1",
                        "thumbnail_url": "",
                    },
                ]
            )

        with ThreadPoolExecutor(max_workers=3) as pool:
            futs = [
                pool.submit(write_likes),
                pool.submit(write_followed),
                pool.submit(write_feed),
            ]
            done, _ = wait(futs)
            for f in done:
                assert f.exception() is None

        assert len(load_sc_likes()) == 1
        assert len(load_sc_followed()) == 1
        assert len(load_feed()) == 1

    def test_concurrent_reads_and_writes(self) -> None:
        """Interleaved reads during writes do not error.

        Each ``_get_db()`` call creates a fresh connection, so
        thread-safety violations (``ProgrammingError``) should not occur.
        Also verifies the reader observed non-trivial data.
        """
        results: list = []

        # Seed one row so concurrent readers deterministically observe data:
        # each writer's DELETE+INSERT is a single transaction, so a reader on
        # a separate connection sees either the pre- or post-write snapshot
        # (never the in-progress DELETE).
        save_sc_likes(
            [
                {
                    "yt_id": "rw_seed",
                    "title": "Seed",
                    "channel": "Test",
                    "duration": 100,
                    "views": 500,
                    "likes_count": 25,
                    "thumbnail_url": "",
                    "url": "",
                    "uploader_id": "rw",
                    "permalink": "rw-seed",
                    "liked_at": datetime.now(UTC).isoformat(),
                },
            ]
        )

        def writer() -> None:
            for i in range(4):
                save_sc_likes(
                    [
                        {
                            "yt_id": f"rw_{i}",
                            "title": f"RW {i}",
                            "channel": "Test",
                            "duration": 100,
                            "views": 500,
                            "likes_count": 25,
                            "thumbnail_url": "",
                            "url": "",
                            "uploader_id": "rw",
                            "permalink": f"rw-{i}",
                            "liked_at": datetime.now(UTC).isoformat(),
                        },
                    ]
                )

        def reader() -> None:
            for _ in range(4):
                results.append(len(load_sc_likes()))

        with ThreadPoolExecutor(max_workers=2) as pool:
            w = pool.submit(writer)
            r = pool.submit(reader)
            wait([w, r])

        assert w.exception() is None
        assert r.exception() is None
        # Reader should have observed at least one non-empty snapshot
        assert any(r > 0 for r in results)
        # Sanity: writer only wrote at most 4 items at any point
        assert max(results) <= 4


class TestDbScopeExceptionCleanup:
    """Item 24: every DB path closes its connection on the exception path."""

    def _raising_db(self):
        from unittest.mock import MagicMock

        db = MagicMock()
        db.execute.side_effect = sqlite3.OperationalError("boom")
        return db

    def test_save_artist_profile_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            save_artist_profile("a1", {"name": "X"})
        db.close.assert_called_once()

    def test_load_sc_likes_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        assert load_sc_likes() == []
        db.close.assert_called_once()

    def test_load_sc_followed_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        assert load_sc_followed() == []
        db.close.assert_called_once()

    def test_save_artist_collections_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            save_artist_collections("a1", [])
        db.close.assert_called_once()

    def test_save_artist_likes_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            save_artist_likes("a1", [])
        db.close.assert_called_once()

    def test_get_cached_artist_profile_closes_on_execute_raise(
        self, monkeypatch
    ) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            get_cached_artist_profile("a1")
        db.close.assert_called_once()

    def test_get_cached_artist_uploads_closes_on_execute_raise(
        self, monkeypatch
    ) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            get_cached_artist_uploads("a1")
        db.close.assert_called_once()

    def test_get_cached_artist_likes_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            get_cached_artist_likes("a1")
        db.close.assert_called_once()

    def test_needs_artist_refresh_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            needs_artist_refresh("a1", "uploads")
        db.close.assert_called_once()

    def test_delete_artist_cache_closes_on_execute_raise(self, monkeypatch) -> None:
        db = self._raising_db()
        monkeypatch.setattr("nyrx.sources.soundcloud.db._get_db", lambda: db)
        with pytest.raises(sqlite3.OperationalError):
            delete_artist_cache("a1")
        db.close.assert_called_once()
