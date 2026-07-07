# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SoundCloud artist cache module (D01).

``enqueue_artist_cache`` is pure (module-level deque state).
``process_artist_cache`` needs mocking for API/DB/time dependencies.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nyrx.sources.soundcloud.cache import (
    CACHE_QUEUE,
    enqueue_artist_cache,
    process_artist_cache,
)
from nyrx.sources.soundcloud.db import (
    get_cached_artist_uploads,
    init_sc_db,
    save_artist_uploads,
)


@pytest.fixture(autouse=True)
def _clear_queue() -> None:
    CACHE_QUEUE.clear()


class TestEnqueueArtistCache:
    def test_adds_artist_to_queue(self) -> None:
        enqueue_artist_cache("artist_1")
        assert list(CACHE_QUEUE) == ["artist_1"]

    def test_deduplicates(self) -> None:
        enqueue_artist_cache("artist_1")
        enqueue_artist_cache("artist_1")
        assert len(CACHE_QUEUE) == 1

    def test_preserves_order_for_multiple_artists(self) -> None:
        enqueue_artist_cache("a")
        enqueue_artist_cache("b")
        enqueue_artist_cache("c")
        assert list(CACHE_QUEUE) == ["a", "b", "c"]

    def test_mixed_unique_and_duplicate(self) -> None:
        enqueue_artist_cache("a")
        enqueue_artist_cache("b")
        enqueue_artist_cache("a")  # duplicate: should be ignored
        enqueue_artist_cache("c")
        assert list(CACHE_QUEUE) == ["a", "b", "c"]


class TestProcessArtistCache:
    """process_artist_cache(artist_id): mocked API/DB/time."""

    def _happy_db(self) -> MagicMock:
        """Return a DB mock that reports 0 cached uploads (triggers skip_dedup)."""
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (0,)
        return db

    def test_all_apis_succeed(self) -> None:
        profile = {"track_count": 5}
        collections = [{"id": "c1"}]
        uploads = [{"id": "u1"}]
        likes = [{"id": "l1"}]

        db = self._happy_db()

        with (
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_profile",
                return_value=profile,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_profile"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_collections",
                return_value=collections,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_collections"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_uploads",
                return_value=uploads,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_uploads"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_likes", return_value=likes
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_likes"),
            patch("nyrx.sources.soundcloud.cache._get_db", return_value=db),
            patch("nyrx.sources.soundcloud.cache.time.sleep"),
        ):
            result = process_artist_cache("artist_1")

        assert result == {
            "profile": True,
            "collections": True,
            "uploads": True,
            "likes": True,
        }

    def test_all_apis_fail_returns_all_false(self) -> None:
        db = self._happy_db()

        with (
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_profile", return_value=None
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_profile"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_collections",
                return_value=None,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_collections"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_uploads", return_value=None
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_uploads"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_likes", return_value=None
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_likes"),
            patch("nyrx.sources.soundcloud.cache._get_db", return_value=db),
            patch("nyrx.sources.soundcloud.cache.time.sleep"),
        ):
            result = process_artist_cache("artist_1")

        assert result == {
            "profile": False,
            "collections": False,
            "uploads": False,
            "likes": False,
        }

    def test_partial_cache_detection_triggers_skip_dedup(self) -> None:
        """When profile has track_count > cached uploads, skip_dedup=True."""
        profile = {"track_count": 10}
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (3,)  # only 3 of 10 cached

        with (
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_profile",
                return_value=profile,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_profile"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_collections",
                return_value=[],
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_collections"),
            patch("nyrx.sources.soundcloud.cache.fetch_artist_uploads") as mock_uploads,
            patch("nyrx.sources.soundcloud.cache.save_artist_uploads"),
            patch("nyrx.sources.soundcloud.cache.fetch_artist_likes", return_value=[]),
            patch("nyrx.sources.soundcloud.cache.save_artist_likes"),
            patch("nyrx.sources.soundcloud.cache._get_db", return_value=db),
            patch("nyrx.sources.soundcloud.cache.time.sleep"),
        ):
            process_artist_cache("artist_1")

        mock_uploads.assert_called_once_with("artist_1", skip_dedup=True)

    def test_partial_cache_not_triggered_when_fully_cached(self) -> None:
        """When cached_count >= track_count * 0.8, skip_dedup=False."""
        profile = {"track_count": 10}
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (9,)  # 9 of 10 cached (>80%)

        with (
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_profile",
                return_value=profile,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_profile"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_collections",
                return_value=[],
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_collections"),
            patch("nyrx.sources.soundcloud.cache.fetch_artist_uploads") as mock_uploads,
            patch("nyrx.sources.soundcloud.cache.save_artist_uploads"),
            patch("nyrx.sources.soundcloud.cache.fetch_artist_likes", return_value=[]),
            patch("nyrx.sources.soundcloud.cache.save_artist_likes"),
            patch("nyrx.sources.soundcloud.cache._get_db", return_value=db),
            patch("nyrx.sources.soundcloud.cache.time.sleep"),
        ):
            process_artist_cache("artist_1")

        mock_uploads.assert_called_once_with("artist_1", skip_dedup=False)

    def test_empty_collections_touches_cached_at(self) -> None:
        profile = {"track_count": 0}
        db = MagicMock()
        db.execute.return_value.fetchone.return_value = (0,)

        with (
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_profile",
                return_value=profile,
            ),
            patch(
                "nyrx.sources.soundcloud.cache.save_artist_profile"
            ) as mock_save_profile,
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_collections",
                return_value=[],
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_collections"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_uploads", return_value=[]
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_uploads"),
            patch("nyrx.sources.soundcloud.cache.fetch_artist_likes", return_value=[]),
            patch("nyrx.sources.soundcloud.cache.save_artist_likes"),
            patch("nyrx.sources.soundcloud.cache._get_db", return_value=db),
            patch("nyrx.sources.soundcloud.cache.time.sleep"),
        ):
            process_artist_cache("artist_1")

        # Pipeline ran to completion even with empty data
        mock_save_profile.assert_called_once()
        # _mark_cached was called for all four categories (profile + 3 empty)
        mark_cached_calls = [
            c
            for c in db.execute.call_args_list
            if c.args[0] and "artist_cache_status" in c.args[0]
        ]
        assert len(mark_cached_calls) == 4

    def test_delta_refresh_does_not_wipe_existing_uploads(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """BUG-4 regression: a delta refresh merges, it never deletes.

        Fully cached artist (9/10 >= 80%) → skip_dedup=False → the API
        returns only the new track.  The real ``save_artist_uploads`` must
        keep the 9 old rows (with the old code the DELETE wiped them).
        """
        monkeypatch.setattr("nyrx.sources.soundcloud.db.SC_DB_PATH", tmp_path / "sc.db")
        init_sc_db()

        old = [
            {"track_id": f"old{i}", "title": f"Old {i}", "channel": "A"}
            for i in range(9)
        ]
        save_artist_uploads("artist_1", old)

        profile = {"track_count": 10}
        new_track = [{"track_id": "new1", "title": "New", "channel": "A"}]

        with (
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_profile",
                return_value=profile,
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_profile"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_collections",
                return_value=[],
            ),
            patch("nyrx.sources.soundcloud.cache.save_artist_collections"),
            patch(
                "nyrx.sources.soundcloud.cache.fetch_artist_uploads",
                return_value=new_track,
            ) as mock_uploads,
            patch("nyrx.sources.soundcloud.cache.fetch_artist_likes", return_value=[]),
            patch("nyrx.sources.soundcloud.cache.save_artist_likes"),
            patch("nyrx.sources.soundcloud.cache.time.sleep"),
        ):
            result = process_artist_cache("artist_1")

        cached = get_cached_artist_uploads("artist_1")
        assert {t["track_id"] for t in cached} == {f"old{i}" for i in range(9)} | {
            "new1"
        }
        assert len(cached) == 10
        assert result["uploads"] is True
        mock_uploads.assert_called_once_with("artist_1", skip_dedup=False)
