# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SoundCloud following module (renumbered E01).

``is_sc_followed`` is pure (no mocking).
``follow_sc`` / ``unfollow_sc`` need mocking for ``save_sc_followed``.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest

from nyrx.sources.soundcloud.following import (
    follow_sc,
    is_sc_followed,
    unfollow_sc,
)


class TestIsScFollowed:
    @pytest.mark.parametrize(
        ("uploader_id", "followed", "expected"),
        [
            ("abc", [{"id": "abc"}], True),
            ("abc", [{"id": "xyz"}], False),
            ("abc", [], False),
            ("abc", [{"id": "abc"}, {"id": "xyz"}], True),
            ("abc", [{"id": "xyz"}, {"id": "abc"}], True),
        ],
    )
    def test_is_sc_followed(
        self, uploader_id: str, followed: list[dict], expected: bool
    ) -> None:
        assert is_sc_followed(uploader_id, followed) is expected


class TestFollowSc:
    def test_follow_appends_entry_and_calls_save(self) -> None:
        followed: list[dict] = []

        with patch("nyrx.sources.soundcloud.following.save_sc_followed") as mock_save:
            entry = follow_sc(
                "id_1", "artist-1", "Artist One", "https://sc.com/artist-1", followed
            )

        assert len(followed) == 1
        assert followed[0]["id"] == "id_1"
        assert followed[0]["permalink"] == "artist-1"
        assert followed[0]["name"] == "Artist One"
        assert followed[0]["url"] == "https://sc.com/artist-1"
        assert "followed_at" in followed[0]
        assert entry is followed[0]
        mock_save.assert_called_once_with(followed)


class TestUnfollowSc:
    def test_unfollow_removes_entry(self) -> None:
        followed = [{"id": "id_1"}, {"id": "id_2"}]

        with patch("nyrx.sources.soundcloud.following.save_sc_followed") as mock_save:
            unfollow_sc("id_1", followed)

        assert len(followed) == 1
        assert followed[0]["id"] == "id_2"
        mock_save.assert_called_once_with(followed)

    def test_unfollow_missing_is_noop(self) -> None:
        followed = [{"id": "id_1"}]

        with patch("nyrx.sources.soundcloud.following.save_sc_followed") as mock_save:
            unfollow_sc("nonexistent", followed)

        assert len(followed) == 1
        assert followed[0]["id"] == "id_1"
        mock_save.assert_called_once_with(followed)

    def test_unfollow_empty_list_is_noop(self) -> None:
        followed: list[dict] = []

        with patch("nyrx.sources.soundcloud.following.save_sc_followed") as mock_save:
            unfollow_sc("anything", followed)

        assert followed == []
        mock_save.assert_called_once_with(followed)
