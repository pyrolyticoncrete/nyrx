# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SoundCloud likes module (D04).

``is_sc_liked`` is pure (no mocking).
``toggle_sc_like`` and ``sync_liked_from_profile`` need mocking for
API/DB/thumbnail dependencies.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nyrx.sources.soundcloud.likes import (
    is_sc_liked,
    sync_liked_from_profile,
    toggle_sc_like,
)


class TestIsScLiked:
    @pytest.mark.parametrize(
        ("yt_id", "liked", "expected"),
        [
            ("abc", [{"yt_id": "abc"}], True),
            ("abc", [{"yt_id": "xyz"}], False),
            ("abc", [], False),
            ("abc", [{"yt_id": "abc"}, {"yt_id": "xyz"}], True),
        ],
    )
    def test_is_sc_liked(self, yt_id: str, liked: list[dict], expected: bool) -> None:
        assert is_sc_liked(yt_id, liked) is expected


class TestToggleScLike:
    def test_unlike_existing_track(self) -> None:
        liked = [{"yt_id": "abc", "title": "Test"}]

        with (
            patch("nyrx.sources.soundcloud.likes.save_sc_likes") as mock_save,
            patch("nyrx.sources.soundcloud.likes._THUMB_CACHE", Path("/tmp")),
            patch("pathlib.Path.exists", return_value=True),
            patch("pathlib.Path.unlink") as mock_unlink,
        ):
            result = toggle_sc_like("abc", {}, liked)

        assert result is False
        assert liked == []  # item removed from list
        mock_save.assert_called_once_with(liked)
        mock_unlink.assert_called_once_with()

    def test_unlike_skips_missing_thumbnail(self) -> None:
        liked = [{"yt_id": "abc"}]

        with (
            patch("nyrx.sources.soundcloud.likes.save_sc_likes") as mock_save,
            patch("nyrx.sources.soundcloud.likes._THUMB_CACHE", Path("/tmp")),
            patch("pathlib.Path.exists", return_value=False),
            patch("pathlib.Path.unlink") as mock_unlink,
        ):
            result = toggle_sc_like("abc", {}, liked)

        assert result is False
        mock_save.assert_called_once()
        mock_unlink.assert_not_called()

    def test_like_new_track_no_resolve(self) -> None:
        liked: list[dict] = []
        data = {
            "title": "New Track",
            "channel": "Artist",
            "duration": 180,
            "views": 1000,
            "likes_count": 50,
            "thumbnail_url": "https://example.com/thumb.jpg",
            "url": "https://soundcloud.com/track",
            "uploader_id": "123",
            "permalink": "new-track",
        }

        with (
            patch("nyrx.sources.soundcloud.likes.save_sc_likes") as mock_save,
            patch("nyrx.sources.soundcloud.likes._cache_thumbnail") as mock_cache,
            patch("nyrx.sources.soundcloud.likes._resolve_sc_track") as mock_resolve,
        ):
            result = toggle_sc_like("new_id", data, liked)

        assert result is True
        assert len(liked) == 1
        assert liked[0]["yt_id"] == "new_id"
        assert liked[0]["title"] == "New Track"
        assert liked[0]["channel"] == "Artist"
        mock_save.assert_called_once_with(liked)
        mock_cache.assert_called_once_with("new_id", data["thumbnail_url"])
        mock_resolve.assert_not_called()

    def test_like_triggers_resolve_when_data_sparse(self) -> None:
        liked: list[dict] = []
        data = {"title": "Sparse Track"}  # no views, likes, or thumb

        resolved = {
            "views": "500",
            "likes_count": "25",
            "thumbnail_url": "https://example.com/thumb.jpg",
            "uploader_id": "456",
            "permalink": "sparse-track",
        }

        with (
            patch("nyrx.sources.soundcloud.likes.save_sc_likes"),
            patch("nyrx.sources.soundcloud.likes._cache_thumbnail"),
            patch(
                "nyrx.sources.soundcloud.likes._resolve_sc_track",
                return_value=resolved,
            ) as mock_resolve,
        ):
            result = toggle_sc_like("sparse", data, liked)

        assert result is True
        assert len(liked) == 1
        mock_resolve.assert_called_once_with("sparse")
        # Resolved fields merged into data
        assert data.get("views") == "500"
        assert data.get("likes_count") == "25"
        assert data.get("uploader_id") == "456"

    def test_like_resolve_failure_still_succeeds(self) -> None:
        liked: list[dict] = []
        data = {"title": "Minimal"}

        with (
            patch("nyrx.sources.soundcloud.likes.save_sc_likes"),
            patch("nyrx.sources.soundcloud.likes._cache_thumbnail"),
            patch(
                "nyrx.sources.soundcloud.likes._resolve_sc_track",
                return_value=None,
            ),
        ):
            result = toggle_sc_like("minimal", data, liked)

        assert result is True
        assert len(liked) == 1
        # Track saved even without resolved data


class TestSyncLikedFromProfile:
    @patch("nyrx.sources.soundcloud.likes.resolve_sc_user", return_value=None)
    def test_unknown_user_raises_resolve_error(self, mock_resolve: MagicMock) -> None:
        from nyrx.sources.soundcloud.likes import ProfileResolveError

        local = [{"yt_id": "existing"}]
        with pytest.raises(ProfileResolveError):
            sync_liked_from_profile("https://sc.com/user", local)

    def test_imports_new_tracks_and_returns_count(self) -> None:
        user_data = {"id": 42}
        remote_tracks = [
            {"track_id": "new1", "title": "New 1", "view_count": 100, "like_count": 10},
            {"track_id": "new2", "title": "New 2", "view_count": 200, "like_count": 20},
        ]
        local = [{"yt_id": "existing"}]

        mock_db = MagicMock()

        with (
            patch(
                "nyrx.sources.soundcloud.likes.resolve_sc_user", return_value=user_data
            ),
            patch(
                "nyrx.sources.soundcloud.likes.fetch_artist_likes",
                return_value=remote_tracks,
            ),
            patch("nyrx.sources.soundcloud.likes._cache_thumbnail"),
            patch("nyrx.sources.soundcloud.likes._get_db", return_value=mock_db),
        ):
            merged, count = sync_liked_from_profile("https://sc.com/user", local)

        assert count == 2
        assert len(merged) == 3
        assert merged[0] is local[0]  # original list unchanged

    def test_no_new_tracks_returns_zero(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.likes.resolve_sc_user", return_value={"id": 1}
            ),
            patch("nyrx.sources.soundcloud.likes.fetch_artist_likes", return_value=[]),
            patch("nyrx.sources.soundcloud.likes._cache_thumbnail"),
            patch("nyrx.sources.soundcloud.likes._get_db"),
        ):
            merged, count = sync_liked_from_profile("https://sc.com/user", [])

        assert count == 0
        assert merged == []

    def test_db_error_returns_unchanged(self) -> None:
        local = [{"yt_id": "existing"}]
        with (
            patch(
                "nyrx.sources.soundcloud.likes.resolve_sc_user", return_value={"id": 1}
            ),
            patch(
                "nyrx.sources.soundcloud.likes.fetch_artist_likes",
                return_value=[{"track_id": "n1"}],
            ),
            patch("nyrx.sources.soundcloud.likes._cache_thumbnail"),
            patch(
                "nyrx.sources.soundcloud.likes._get_db",
                side_effect=RuntimeError("locked"),
            ),
        ):
            merged, count = sync_liked_from_profile("https://sc.com/user", local)

        assert merged is local
        assert count == 0
