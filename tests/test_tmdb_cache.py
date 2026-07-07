# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the TMDB client in ``sources/tv_movies/tmdb_cache.py``.

Covers key seeding/loading, proxy-first + key-rotation request path, the
disk cache TTL semantics, the details/search/genre/trending/popular public
API, and the proxy refresh 7-day gate.  All module globals and filesystem
paths are redirected to ``tmp_path`` so nothing touches the real
``~/.config/nyrx`` or ``~/.cache/nyrx`` trees.
"""

from __future__ import annotations

import json
import time
from unittest.mock import patch

import pytest

from nyrx.sources.tv_movies import tmdb_cache


class _JsonResp:
    """Minimal requests.Response stand-in exposing ``ok`` + ``json()``."""

    def __init__(self, payload, ok: bool = True):
        self._payload = payload
        self.ok = ok

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def hermetic_tmdb(tmp_path, monkeypatch):
    """Redirect every module global and path the client touches."""
    monkeypatch.setattr(tmdb_cache, "_KEYS", [])
    monkeypatch.setattr(tmdb_cache, "_PROXY", None)
    monkeypatch.setattr(tmdb_cache, "_KEY_INDEX", 0)
    monkeypatch.setattr(tmdb_cache, "_genre_map", None)
    monkeypatch.setattr(tmdb_cache, "CACHE_DIR", tmp_path / "cache")
    monkeypatch.setattr(
        tmdb_cache, "CACHE_FILE", tmp_path / "cache" / "tmdb_cache.json"
    )
    monkeypatch.setattr(tmdb_cache, "_KEYS_PATH", tmp_path / "keys.json")
    monkeypatch.setattr(tmdb_cache, "_BUNDLED_KEYS", tmp_path / "bundled_keys.json")
    yield
    monkeypatch.setattr(tmdb_cache, "_genre_map", None)


def _write_keys(data: dict) -> None:
    tmdb_cache._KEYS_PATH.write_text(json.dumps(data))


def _write_bundled(data: dict) -> None:
    tmdb_cache._BUNDLED_KEYS.write_text(json.dumps(data))


# ---------------------------------------------------------------------------
# _seed_keys
# ---------------------------------------------------------------------------


class TestSeedKeys:
    def test_missing_runtime_copies_bundled(self) -> None:
        _write_bundled({"tmdb_keys": ["k1"], "tmdb_proxy": "p"})
        runtime = tmdb_cache._KEYS_PATH
        tmdb_cache._seed_keys(runtime)
        assert json.loads(runtime.read_text()) == {
            "tmdb_keys": ["k1"],
            "tmdb_proxy": "p",
        }

    def test_existing_keys_left_untouched(self) -> None:
        _write_bundled({"tmdb_keys": ["k1"]})
        _write_keys({"tmdb_keys": ["custom"]})
        tmdb_cache._seed_keys(tmdb_cache._KEYS_PATH)
        assert json.loads(tmdb_cache._KEYS_PATH.read_text()) == {
            "tmdb_keys": ["custom"]
        }

    def test_missing_keys_merges_bundled_preserving_other_fields(self) -> None:
        _write_bundled({"tmdb_keys": ["k1"], "tmdb_proxy": "p"})
        _write_keys({"tmdb_proxy": "p"})
        tmdb_cache._seed_keys(tmdb_cache._KEYS_PATH)
        assert json.loads(tmdb_cache._KEYS_PATH.read_text()) == {
            "tmdb_keys": ["k1"],
            "tmdb_proxy": "p",
        }

    def test_user_keys_present_short_circuits_merge(self) -> None:
        _write_bundled({"tmdb_keys": ["k1"]})
        _write_keys({"user_tmdb_keys": ["u1"]})
        tmdb_cache._seed_keys(tmdb_cache._KEYS_PATH)
        assert json.loads(tmdb_cache._KEYS_PATH.read_text()) == {
            "user_tmdb_keys": ["u1"]
        }

    def test_missing_bundled_is_noop(self) -> None:
        runtime = tmdb_cache._KEYS_PATH
        tmdb_cache._seed_keys(runtime)
        assert not runtime.exists()

    def test_merged_file_is_valid_json(self) -> None:
        _write_bundled({"tmdb_keys": ["k1"]})
        _write_keys({"user_tmdb_keys": ["u1"]})
        tmdb_cache._seed_keys(tmdb_cache._KEYS_PATH)
        # user keys present → no merge; file still valid JSON.
        assert json.loads(tmdb_cache._KEYS_PATH.read_text())["user_tmdb_keys"] == ["u1"]

    def test_corrupt_bundled_json_swallowed(self) -> None:
        _write_keys({"tmdb_proxy": "p"})
        tmdb_cache._BUNDLED_KEYS.write_text("{corrupt")
        tmdb_cache._seed_keys(tmdb_cache._KEYS_PATH)
        assert json.loads(tmdb_cache._KEYS_PATH.read_text()) == {"tmdb_proxy": "p"}


# ---------------------------------------------------------------------------
# load_keys
# ---------------------------------------------------------------------------


class TestLoadKeys:
    def test_user_keys_prepend_ota_and_proxy_set(self) -> None:
        keys = tmdb_cache._KEYS_PATH.parent / "explicit.json"
        keys.write_text(
            json.dumps(
                {
                    "user_tmdb_keys": ["u1"],
                    "tmdb_keys": ["o1", "o2"],
                    "tmdb_proxy": "https://db.videasy.to/3",
                }
            )
        )
        tmdb_cache.load_keys(keys)
        assert tmdb_cache._KEYS == ["u1", "o1", "o2"]
        assert tmdb_cache._PROXY == "https://db.videasy.to/3"

    def test_default_path_seeds_then_loads(self) -> None:
        _write_bundled({"tmdb_keys": ["k1"]})
        tmdb_cache.load_keys()
        assert tmdb_cache._KEYS == ["k1"]

    def test_unreadable_file_falls_back_to_empty(self) -> None:
        tmdb_cache.load_keys(tmdb_cache._KEYS_PATH.parent / "nope.json")
        assert tmdb_cache._KEYS == []
        assert tmdb_cache._PROXY is None

    def test_invalid_json_falls_back_to_empty(self) -> None:
        bad = tmdb_cache._KEYS_PATH.parent / "bad.json"
        bad.write_text("{not json")
        tmdb_cache.load_keys(bad)
        assert tmdb_cache._KEYS == []
        assert tmdb_cache._PROXY is None


# ---------------------------------------------------------------------------
# _request
# ---------------------------------------------------------------------------


class TestRequestProxyFirst:
    def test_proxy_used_without_api_key_and_no_rotation(self) -> None:
        tmdb_cache._PROXY = "https://db.videasy.to/3"
        tmdb_cache._KEYS = ["k1"]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get",
            return_value=_JsonResp({"ok": True}),
        ) as get:
            result = tmdb_cache._request("/search/movie", {"query": "q"})
        assert result == {"ok": True}
        get.assert_called_once_with(
            "https://db.videasy.to/3/search/movie",
            params={"query": "q"},
            timeout=10,
            headers=tmdb_cache.HTTP_HEADERS,
        )
        assert tmdb_cache._KEY_INDEX == 0

    def test_proxy_failure_falls_back_to_direct_with_key(self) -> None:
        tmdb_cache._PROXY = "https://db.videasy.to/3"
        tmdb_cache._KEYS = ["k1"]
        resp = [_JsonResp(None, ok=False), _JsonResp({"ok": True})]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get", side_effect=resp
        ) as get:
            result = tmdb_cache._request("/search/movie", {"query": "q"})
        assert result == {"ok": True}
        assert get.call_count == 2
        direct_params = get.call_args_list[1].kwargs["params"]
        assert direct_params["api_key"] == "k1"

    def test_proxy_exception_falls_back_to_direct_with_key(self) -> None:
        tmdb_cache._PROXY = "https://db.videasy.to/3"
        tmdb_cache._KEYS = ["k1"]
        resp = [ConnectionError("boom"), _JsonResp({"ok": True})]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get", side_effect=resp
        ) as get:
            result = tmdb_cache._request("/search/movie", {"query": "q"})
        assert result == {"ok": True}
        assert get.call_count == 2
        assert get.call_args_list[1].kwargs["params"]["api_key"] == "k1"


class TestRequestRotation:
    def test_two_direct_calls_use_two_different_keys(self) -> None:
        tmdb_cache._KEYS = ["k1", "k2"]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get",
            return_value=_JsonResp({"ok": True}),
        ) as get:
            tmdb_cache._request("/x")
            tmdb_cache._request("/x")
        keys = [c.kwargs["params"]["api_key"] for c in get.call_args_list]
        assert keys == ["k1", "k2"]

    def test_index_wraps_with_single_key(self) -> None:
        tmdb_cache._KEYS = ["k1"]
        tmdb_cache._KEY_INDEX = 5
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get",
            return_value=_JsonResp({"ok": True}),
        ) as get:
            assert tmdb_cache._request("/x") == {"ok": True}
        assert get.call_args.kwargs["params"]["api_key"] == "k1"

    def test_all_keys_fail_returns_none(self) -> None:
        tmdb_cache._KEYS = ["k1", "k2"]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get",
            return_value=_JsonResp(None, ok=False),
        ):
            assert tmdb_cache._request("/x") is None
        assert tmdb_cache._KEY_INDEX == 2

    def test_no_keys_no_proxy_returns_none(self) -> None:
        assert tmdb_cache._KEYS == []
        assert tmdb_cache._PROXY is None
        with patch("nyrx.sources.tv_movies.tmdb_cache.requests.get") as get:
            assert tmdb_cache._request("/x") is None
        get.assert_not_called()

    def test_request_exception_tries_next_key(self) -> None:
        tmdb_cache._KEYS = ["k1", "k2"]
        resp = [_JsonResp({"ok": True})]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.requests.get",
            side_effect=[ConnectionError("boom")] + resp,
        ) as get:
            result = tmdb_cache._request("/x")
        assert result == {"ok": True}
        assert get.call_count == 2
        assert get.call_args_list[1].kwargs["params"]["api_key"] == "k2"


# ---------------------------------------------------------------------------
# Disk cache TTL
# ---------------------------------------------------------------------------


class TestCacheTtl:
    def test_fresh_entry_returned(self) -> None:
        tmdb_cache._cache_set("k", {"a": 1})
        assert tmdb_cache._cache_get("k", 100) == {"a": 1}

    def test_exactly_max_age_is_stale(self) -> None:
        tmdb_cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmdb_cache.CACHE_FILE.write_text(
            json.dumps(
                {
                    "k": {"ts": 1000.0, "data": {"a": 1}},
                }
            )
        )
        with patch("nyrx.sources.tv_movies.tmdb_cache.time.time", return_value=1100.0):
            assert tmdb_cache._cache_get("k", 100) is None

    def test_entry_just_under_max_age_is_fresh(self) -> None:
        tmdb_cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmdb_cache.CACHE_FILE.write_text(
            json.dumps(
                {
                    "k": {"ts": 1000.0, "data": {"a": 1}},
                }
            )
        )
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.time.time", return_value=1099.999
        ):
            assert tmdb_cache._cache_get("k", 100) == {"a": 1}

    def test_malformed_cache_file_returns_none(self) -> None:
        tmdb_cache.CACHE_DIR.mkdir(parents=True, exist_ok=True)
        tmdb_cache.CACHE_FILE.write_text("{not json")
        assert tmdb_cache._cache_get("k", 100) is None

    def test_missing_cache_file_returns_none(self) -> None:
        assert tmdb_cache._cache_get("k", 100) is None

    def test_set_overwrites_and_round_trips(self) -> None:
        tmdb_cache._cache_set("k", {"a": 1})
        tmdb_cache._cache_set("k", {"b": 2})
        assert tmdb_cache._cache_get("k", 100) == {"b": 2}

    def test_set_creates_cache_dir(self) -> None:
        tmdb_cache._cache_set("k", {"a": 1})
        assert tmdb_cache.CACHE_DIR.is_dir()


class TestCacheConcurrency:
    """Item 21: lock-guarded read-modify-write + atomic temp-file write."""

    def test_write_is_atomic_no_tmp_left_behind(self) -> None:
        tmdb_cache._cache_set("k", {"a": 1})
        assert (tmdb_cache.CACHE_DIR / "tmdb_cache.json.tmp").exists() is False
        assert tmdb_cache._cache_get("k", 100) == {"a": 1}

    def test_concurrent_sets_lose_no_entries(self) -> None:
        """Concurrent writers must not drop each other's entries.

        Without ``_CACHE_LOCK`` the read-modify-write of ``_cache_set``
        races: two threads can read the same snapshot and the last write
        silently discards the other's key.
        """
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def set_k(i: int) -> None:
            tmdb_cache._cache_set(f"key{i}", {"v": i})

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(set_k, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()  # propagate any error

        cache = json.loads(tmdb_cache.CACHE_FILE.read_text())
        assert len(cache) == 50
        for i in range(50):
            assert cache[f"key{i}"]["data"] == {"v": i}

    def test_lock_serializes_read_modify_write(self) -> None:
        """The same key written from many threads keeps the last value."""
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def set_same(i: int) -> None:
            tmdb_cache._cache_set("same", {"v": i})

        with ThreadPoolExecutor(max_workers=8) as pool:
            futures = [pool.submit(set_same, i) for i in range(50)]
            for f in as_completed(futures):
                f.result()

        final = tmdb_cache._cache_get("same", 100)
        assert final in ({"v": i} for i in range(50))


# ---------------------------------------------------------------------------
# movie_details / tv_details / season_details
# ---------------------------------------------------------------------------


class TestDetailsCache:
    def test_movie_details_cache_hit_avoids_request(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"a": 1}
        ) as req:
            assert tmdb_cache.movie_details(1) == {"a": 1}
            assert tmdb_cache.movie_details(1) == {"a": 1}
        req.assert_called_once_with("/movie/1", {"language": "en"})

    def test_tv_details_cache_hit_avoids_request(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"a": 1}
        ) as req:
            assert tmdb_cache.tv_details(7) == {"a": 1}
            assert tmdb_cache.tv_details(7) == {"a": 1}
        req.assert_called_once_with("/tv/7", {"language": "en"})

    def test_stale_entry_refetches(self) -> None:
        tmdb_cache._cache_set("movie:1", {"old": 1})
        tmdb_cache.CACHE_FILE.write_text(
            json.dumps(
                {
                    "movie:1": {
                        "ts": time.time() - tmdb_cache.MOVIE_TTL - 1,
                        "data": {"old": 1},
                    },
                }
            )
        )
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"new": 1}
        ) as req:
            assert tmdb_cache.movie_details(1) == {"new": 1}
        req.assert_called_once()

    def test_none_result_is_not_cached(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value=None
        ) as req:
            assert tmdb_cache.movie_details(5) is None
            assert tmdb_cache.movie_details(5) is None
        assert req.call_count == 2
        assert not tmdb_cache.CACHE_FILE.exists()

    def test_season_details_fetch_and_cache(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"episodes": []}
        ) as req:
            assert tmdb_cache.season_details(1, 2) == {"episodes": []}
            assert tmdb_cache.season_details(1, 2) == {"episodes": []}
        req.assert_called_once_with("/tv/1/season/2", {"language": "en"})


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _search_result(**overrides):
    base = {
        "id": 1,
        "title": "Movie",
        "name": None,
        "media_type": "movie",
        "release_date": "2019-03-01",
        "first_air_date": None,
        "vote_average": 7.5,
        "vote_count": 100,
        "poster_path": "/p.jpg",
        "overview": "ov",
        "genre_ids": [1, 2],
    }
    base.update(overrides)
    return base


class TestSearch:
    def test_movie_path_and_mapping(self) -> None:
        results = [_search_result(title="Mov", release_date="2019-03-01")]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request",
            return_value={"results": results},
        ) as req:
            out = tmdb_cache.search("q", media_type="movie")
        req.assert_called_once_with(
            "/search/movie",
            {"query": "q", "page": "1", "language": "en"},
        )
        assert out[0]["tmdb_id"] == 1
        assert out[0]["title"] == "Mov"
        assert out[0]["media_type"] == "movie"
        assert out[0]["year"] == "2019"
        assert out[0]["rating"] == 7.5
        assert out[0]["vote_count"] == 100
        assert out[0]["release_date"] == "2019-03-01"
        assert out[0]["genre_ids"] == [1, 2]

    def test_tv_path_uses_name_and_first_air_date(self) -> None:
        results = [
            _search_result(
                title=None,
                name="Show",
                media_type="tv",
                release_date=None,
                first_air_date="2021-06-01",
            )
        ]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request",
            return_value={"results": results},
        ):
            out = tmdb_cache.search("q", media_type="tv")
        assert out[0]["title"] == "Show"
        assert out[0]["year"] == "2021"
        assert out[0]["media_type"] == "tv"

    def test_multi_path_filters_person_and_maps_fields(self) -> None:
        results = [
            _search_result(id=99, title=None, name="Actor", media_type="person"),
            _search_result(id=2, title="Real", media_type="movie"),
        ]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request",
            return_value={"results": results},
        ):
            out = tmdb_cache.search("q")
        assert len(out) == 1
        assert out[0]["tmdb_id"] == 2

    def test_movie_path_result_without_media_type_defaults_to_movie(self) -> None:
        results = [_search_result(media_type=None)]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request",
            return_value={"results": results},
        ):
            out = tmdb_cache.search("q", media_type="movie")
        assert out[0]["media_type"] == "movie"

    def test_missing_data_returns_empty_list(self) -> None:
        with patch("nyrx.sources.tv_movies.tmdb_cache._request", return_value=None):
            assert tmdb_cache.search("q") == []

    def test_empty_results_returns_empty_list(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"results": []}
        ):
            assert tmdb_cache.search("q") == []

    def test_zero_rating_normalized(self) -> None:
        results = [_search_result(vote_average=0)]
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request",
            return_value={"results": results},
        ):
            out = tmdb_cache.search("q")
        assert out[0]["rating"] == 0


# ---------------------------------------------------------------------------
# genre_names
# ---------------------------------------------------------------------------


def _genre_response(path: str, params):
    if path == "/genre/movie/list":
        return {"genres": [{"id": 1, "name": "Action"}]}
    return {"genres": [{"id": 1, "name": "Action"}, {"id": 2, "name": "Drama"}]}


class TestGenreNames:
    def test_resolves_and_skips_unknown_ids(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", side_effect=_genre_response
        ):
            assert tmdb_cache.genre_names([1, 999]) == ["Action"]

    def test_second_call_uses_in_memory_cache(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", side_effect=_genre_response
        ) as req:
            tmdb_cache.genre_names([1])
            tmdb_cache.genre_names([2])
        assert req.call_count == 2

    def test_disk_cache_hit_avoids_fetch(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", side_effect=_genre_response
        ) as req:
            tmdb_cache.genre_names([1])
        tmdb_cache._genre_map = None
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", side_effect=_genre_response
        ) as req2:
            assert tmdb_cache.genre_names([1]) == ["Action"]
        assert req2.call_count == 0
        assert req.call_count == 2


# ---------------------------------------------------------------------------
# _filter_released
# ---------------------------------------------------------------------------


class _FixedDate:
    def __init__(self, iso: str):
        self._iso = iso

    def isoformat(self) -> str:
        return self._iso


class _FakeDate:
    @classmethod
    def today(cls) -> _FixedDate:
        return _FixedDate("2024-06-15")


class TestFilterReleased:
    def test_future_filtered_today_and_missing_kept(self, monkeypatch) -> None:
        monkeypatch.setattr(tmdb_cache, "date", _FakeDate)
        items = [
            {"release_date": "2024-07-01"},
            {"release_date": "2024-06-15"},
            {},
        ]
        out = tmdb_cache._filter_released(items)
        assert out == [{"release_date": "2024-06-15"}, {}]


# ---------------------------------------------------------------------------
# trending / popular
# ---------------------------------------------------------------------------


class TestTrending:
    def test_fetch_normalize_filter_and_cache(self) -> None:
        payload = {
            "results": [
                {
                    "id": 1,
                    "title": "Hot",
                    "media_type": "movie",
                    "release_date": "2024-01-01",
                    "vote_average": 8.0,
                }
            ]
        }
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value=payload
        ) as req:
            out = tmdb_cache.trending(1)
            out2 = tmdb_cache.trending(1)
        assert out == out2
        assert out[0]["tmdb_id"] == 1
        assert out[0]["title"] == "Hot"
        assert out[0]["release_date"] == "2024-01-01"
        req.assert_called_once_with(
            "/trending/all/week", {"language": "en", "page": "1"}
        )

    def test_future_title_filtered_out(self, monkeypatch) -> None:
        monkeypatch.setattr(tmdb_cache, "date", _FakeDate)
        payload = {
            "results": [
                {
                    "id": 1,
                    "title": "Now",
                    "media_type": "movie",
                    "release_date": "2024-01-01",
                },
                {
                    "id": 2,
                    "title": "Later",
                    "media_type": "movie",
                    "release_date": "2025-01-01",
                },
            ]
        }
        with patch("nyrx.sources.tv_movies.tmdb_cache._request", return_value=payload):
            out = tmdb_cache.trending(1)
        assert [i["tmdb_id"] for i in out] == [1]


class TestPopular:
    def test_merged_sorted_desc_with_media_types(self, monkeypatch) -> None:
        monkeypatch.setattr(tmdb_cache, "date", _FakeDate)
        movie_payload = {
            "results": [
                {
                    "id": 1,
                    "title": "A",
                    "release_date": "2020-01-01",
                    "vote_average": 5.0,
                },
                {
                    "id": 3,
                    "title": "C",
                    "release_date": "2021-01-01",
                    "vote_average": 7.0,
                },
            ]
        }
        tv_payload = {
            "results": [
                {
                    "id": 2,
                    "title": "B",
                    "release_date": "2024-05-01",
                    "vote_average": 9.0,
                },
            ]
        }
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request",
            side_effect=[movie_payload, tv_payload],
        ) as req:
            out = tmdb_cache.popular(1)
            out2 = tmdb_cache.popular(1)
        assert out == out2
        assert [i["tmdb_id"] for i in out] == [2, 3, 1]
        assert [i["media_type"] for i in out] == ["tv", "movie", "movie"]
        assert req.call_count == 2

    def test_empty_response_returns_empty_list(self) -> None:
        with patch("nyrx.sources.tv_movies.tmdb_cache._request", return_value=None):
            assert tmdb_cache.popular(1) == []


# ---------------------------------------------------------------------------
# recommendations_from_seeds
# ---------------------------------------------------------------------------


class TestRecommendationsFromSeeds:
    def test_empty_bookmarks_returns_empty(self) -> None:
        with patch("nyrx.sources.tv_movies.tmdb_cache._request") as req:
            assert tmdb_cache.recommendations_from_seeds([]) == []
        req.assert_not_called()

    def test_seed_routing_and_limit(self) -> None:
        payload = {
            "results": [
                {"id": i, "title": f"R{i}", "release_date": "2020-01-01"}
                for i in range(1, 4)
            ]
        }
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value=payload
        ) as req:
            out = tmdb_cache.recommendations_from_seeds(
                [{"tmdb_id": 1, "media_type": "movie"}], limit=2
            )
        assert len(out) == 2
        req.assert_called_once_with("/movie/1/recommendations", {"language": "en"})

    def test_media_type_defaults_to_movie(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"results": []}
        ) as req:
            tmdb_cache.recommendations_from_seeds([{"tmdb_id": 1}])
        req.assert_called_once_with("/movie/1/recommendations", {"language": "en"})

    def test_tv_seed_routes_to_tv(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache._request", return_value={"results": []}
        ) as req:
            tmdb_cache.recommendations_from_seeds([{"tmdb_id": 5, "media_type": "tv"}])
        req.assert_called_once_with("/tv/5/recommendations", {"language": "en"})


# ---------------------------------------------------------------------------
# refresh_proxy
# ---------------------------------------------------------------------------


class TestRefreshProxy:
    def test_skips_within_seven_days(self) -> None:
        _write_keys({"proxy_last_checked": time.time()})
        with (
            patch("nyrx.sources.tv_movies.proxy_discovery.discover_proxy") as disc,
            patch("nyrx.sources.tv_movies.proxy_discovery.update_proxy_config") as upd,
        ):
            tmdb_cache.refresh_proxy()
        disc.assert_not_called()
        upd.assert_not_called()

    def test_stale_discover_and_switch(self) -> None:
        _write_keys({"proxy_last_checked": time.time() - 8 * 86400})
        tmdb_cache._PROXY = "https://old.example/3"
        with (
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.discover_proxy",
                return_value="https://db.videasy.to/3",
            ) as disc,
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.update_proxy_config",
                return_value=True,
            ) as upd,
        ):
            tmdb_cache.refresh_proxy()
        disc.assert_called_once()
        upd.assert_called_once()
        assert tmdb_cache._PROXY == "https://db.videasy.to/3"

    def test_same_proxy_still_updates_config(self) -> None:
        _write_keys({"proxy_last_checked": time.time() - 8 * 86400})
        tmdb_cache._PROXY = "https://db.videasy.to/3"
        with (
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.discover_proxy",
                return_value="https://db.videasy.to/3",
            ) as disc,
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.update_proxy_config",
                return_value=True,
            ) as upd,
        ):
            tmdb_cache.refresh_proxy()
        disc.assert_called_once()
        upd.assert_called_once()
        assert tmdb_cache._PROXY == "https://db.videasy.to/3"

    def test_no_found_proxy_returns_without_update(self) -> None:
        _write_keys({"proxy_last_checked": time.time() - 8 * 86400})
        with (
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.discover_proxy",
                return_value=None,
            ) as disc,
            patch("nyrx.sources.tv_movies.proxy_discovery.update_proxy_config") as upd,
        ):
            tmdb_cache.refresh_proxy()
        disc.assert_called_once()
        upd.assert_not_called()

    def test_unreadable_keys_still_proceeds_to_discovery(self) -> None:
        with (
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.discover_proxy",
                return_value="https://db.videasy.to/3",
            ) as disc,
            patch(
                "nyrx.sources.tv_movies.proxy_discovery.update_proxy_config",
                return_value=True,
            ),
        ):
            tmdb_cache.refresh_proxy()
        disc.assert_called_once()
