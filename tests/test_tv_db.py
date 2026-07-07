# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for TV/Movies SQLite storage layer (Phase 1).

All tests use ``tmp_path`` for a hermetic database: no filesystem
interactions outside the temp directory.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from nyrx.sources.tv_movies.db import (
    bookmark_exists,
    delete_bookmark,
    init_tv_db,
    load_bookmark,
    load_bookmarks,
    load_episodes,
    load_seasons,
    save_bookmark,
    save_episodes,
    save_seasons,
)


@pytest.fixture(autouse=True)
def _hermetic_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Redirect TV_DB_PATH to tmp_path for every test."""
    db_path = tmp_path / "tv_data.db"
    monkeypatch.setattr("nyrx.sources.tv_movies.db.TV_DB_PATH", db_path)
    init_tv_db()


_MOVIE = {
    "tmdb_id": 1,
    "title": "Test Movie",
    "media_type": "movie",
    "year": "2024",
    "rating": 8.5,
    "vote_count": 100,
    "poster_path": "/poster.jpg",
    "tagline": "A test movie.",
    "overview": "This is a test movie used for verification.",
    "genres": '["Action", "Drama"]',
    "runtime": 120,
    "season_count": None,
    "number_of_episodes": None,
    "bookmarked_at": datetime.now(UTC).isoformat(),
}

_TV = {
    "tmdb_id": 2,
    "title": "Test Series",
    "media_type": "tv",
    "year": "2020",
    "rating": 7.5,
    "vote_count": 50,
    "poster_path": "/tv_poster.jpg",
    "tagline": None,
    "overview": "A test TV series.",
    "genres": '["Comedy"]',
    "runtime": None,
    "season_count": 3,
    "number_of_episodes": 24,
    "bookmarked_at": datetime.now(UTC).isoformat(),
}

_SEASONS = [
    {
        "season_number": 1,
        "episode_count": 10,
        "name": "Season 1",
        "poster_path": "/s1.jpg",
        "air_date": "2020-01-01",
    },
    {
        "season_number": 2,
        "episode_count": 8,
        "name": "Season 2",
        "poster_path": "/s2.jpg",
        "air_date": "2021-01-01",
    },
    {
        "season_number": 3,
        "episode_count": 6,
        "name": "Season 3",
        "poster_path": "",
        "air_date": "2022-01-01",
    },
]

_EPISODES = [
    {
        "episode_number": 1,
        "name": "Pilot",
        "still_path": "/still1.jpg",
        "overview": "First episode.",
        "runtime": 45,
        "air_date": "2020-01-01",
    },
    {
        "episode_number": 2,
        "name": "Second Episode",
        "still_path": "",
        "overview": "Second episode.",
        "runtime": 44,
        "air_date": "2020-01-08",
    },
]


class TestSchema:
    def test_init_twice_is_idempotent(self) -> None:
        init_tv_db()
        init_tv_db()


class TestBookmarkRoundTrip:
    def test_save_then_load_movie(self) -> None:
        save_bookmark(_MOVIE)
        loaded = load_bookmark(1)
        assert loaded is not None
        assert loaded["tmdb_id"] == 1
        assert loaded["title"] == "Test Movie"
        assert loaded["media_type"] == "movie"
        assert loaded["tagline"] == "A test movie."
        assert loaded["runtime"] == 120
        assert loaded["season_count"] is None
        assert loaded["number_of_episodes"] is None

    def test_save_then_load_tv(self) -> None:
        save_bookmark(_TV)
        loaded = load_bookmark(2)
        assert loaded is not None
        assert loaded["title"] == "Test Series"
        assert loaded["media_type"] == "tv"
        assert loaded["season_count"] == 3
        assert loaded["number_of_episodes"] == 24
        assert loaded["tagline"] is None
        assert loaded["runtime"] is None

    def test_load_all_bookmarks(self) -> None:
        save_bookmark(_MOVIE)
        save_bookmark(_TV)
        all_bm = load_bookmarks()
        assert len(all_bm) == 2
        tmdb_ids = {b["tmdb_id"] for b in all_bm}
        assert tmdb_ids == {1, 2}

    def test_load_empty(self) -> None:
        assert load_bookmarks() == []

    def test_bookmark_exists(self) -> None:
        assert not bookmark_exists(1)
        save_bookmark(_MOVIE)
        assert bookmark_exists(1)
        assert not bookmark_exists(999)

    def test_overwrite_replaces_data(self) -> None:
        save_bookmark(_MOVIE)
        updated = {**_MOVIE, "title": "Updated Movie", "rating": 9.0}
        save_bookmark(updated)
        loaded = load_bookmark(1)
        assert loaded["title"] == "Updated Movie"
        assert loaded["rating"] == 9.0

    def test_delete_removes_bookmark(self) -> None:
        save_bookmark(_MOVIE)
        assert bookmark_exists(1)
        delete_bookmark(1)
        assert not bookmark_exists(1)
        assert load_bookmarks() == []


class TestSeasonRoundTrip:
    def test_save_then_load(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS)
        seasons = load_seasons(2)
        assert len(seasons) == 3
        assert seasons[0]["season_number"] == 1
        assert seasons[0]["episode_count"] == 10
        assert seasons[1]["name"] == "Season 2"

    def test_load_empty_for_unbookmarked(self) -> None:
        assert load_seasons(999) == []

    def test_overwrite_replaces(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS)
        replacement = [
            {
                "season_number": 1,
                "episode_count": 5,
                "name": "New S1",
                "poster_path": "",
                "air_date": "",
            }
        ]
        save_seasons(2, replacement)
        seasons = load_seasons(2)
        assert len(seasons) == 1
        assert seasons[0]["episode_count"] == 5
        assert seasons[0]["name"] == "New S1"


class TestEpisodeRoundTrip:
    def test_save_then_load(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS[:1])
        save_episodes(2, 1, _EPISODES)
        episodes = load_episodes(2, 1)
        assert len(episodes) == 2
        assert episodes[0]["name"] == "Pilot"
        assert episodes[0]["runtime"] == 45
        assert episodes[0]["cached_at"] is not None

    def test_load_empty_for_unfetched_season(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS[:1])
        assert load_episodes(2, 1) == []

    def test_overwrite_replaces(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS[:1])
        save_episodes(2, 1, _EPISODES)
        new_eps = [
            {
                "episode_number": 1,
                "name": "Changed",
                "still_path": "",
                "overview": "",
                "runtime": 30,
                "air_date": "",
            }
        ]
        save_episodes(2, 1, new_eps)
        episodes = load_episodes(2, 1)
        assert len(episodes) == 1
        assert episodes[0]["name"] == "Changed"

    def test_episode_has_cached_at(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS[:1])
        save_episodes(2, 1, _EPISODES)
        episodes = load_episodes(2, 1)
        for ep in episodes:
            assert ep["cached_at"] is not None
            assert len(ep["cached_at"]) > 0


class TestCascadeDelete:
    def test_delete_bookmark_removes_seasons(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS)
        assert len(load_seasons(2)) == 3
        delete_bookmark(2)
        assert load_seasons(2) == []

    def test_delete_bookmark_removes_episodes(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS[:1])
        save_episodes(2, 1, _EPISODES)
        assert len(load_episodes(2, 1)) == 2
        delete_bookmark(2)
        assert load_episodes(2, 1) == []

    def test_cascade_removes_all_child_rows(self) -> None:
        save_bookmark(_TV)
        save_seasons(2, _SEASONS)
        save_episodes(2, 1, _EPISODES)
        save_episodes(
            2,
            2,
            [
                {
                    "episode_number": 1,
                    "name": "S2E1",
                    "still_path": "",
                    "overview": "",
                    "runtime": 40,
                    "air_date": "",
                },
                {
                    "episode_number": 2,
                    "name": "S2E2",
                    "still_path": "",
                    "overview": "",
                    "runtime": 41,
                    "air_date": "",
                },
            ],
        )
        delete_bookmark(2)
        assert load_bookmarks() == []
        assert load_seasons(2) == []
        assert load_episodes(2, 1) == []
        assert load_episodes(2, 2) == []


class TestDataIntegrity:
    def test_movie_tagline_round_trips_correctly(self) -> None:
        save_bookmark(_MOVIE)
        loaded = load_bookmark(1)
        assert loaded["tagline"] == "A test movie."

    def test_tv_tagline_is_null(self) -> None:
        save_bookmark(_TV)
        loaded = load_bookmark(2)
        assert loaded["tagline"] is None

    def test_vote_count_stored_correctly(self) -> None:
        save_bookmark(_MOVIE)
        loaded = load_bookmark(1)
        assert loaded["vote_count"] == 100

    def test_number_of_episodes_only_on_tv(self) -> None:
        save_bookmark(_MOVIE)
        save_bookmark(_TV)
        movie = load_bookmark(1)
        tv = load_bookmark(2)
        assert movie["number_of_episodes"] is None
        assert tv["number_of_episodes"] == 24
