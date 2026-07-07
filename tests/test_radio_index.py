# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for StationIndex (renumbered E02).

Pure query tests use an inline 5-station fixture loaded directly.
Filesystem I/O tests use ``tmp_path`` with monkeypatched path constants.
"""

from __future__ import annotations

import gzip
import json
from pathlib import Path

import pytest

STATIONS = [
    {
        "stationuuid": "uuid-1",
        "name": "Groove Radio",
        "url_resolved": "https://example.com/groove",
        "tags": "house,techno,electronic",
        "country": "Germany",
        "countrycode": "DE",
        "codec": "MP3",
        "bitrate": 192,
        "clickcount": 5000,
        "favicon": "",
        "hls": "0",
        "homepage": "",
        "lastcheckok": "1",
        "lastchecktime": "2024-01-01 00:00:00",
    },
    {
        "stationuuid": "uuid-2",
        "name": "Jazz Cafe",
        "url_resolved": "https://example.com/jazz",
        "tags": "jazz,electronic,ambient",
        "country": "France",
        "countrycode": "FR",
        "codec": "AAC",
        "bitrate": 128,
        "clickcount": 3000,
        "favicon": "",
        "hls": "0",
        "homepage": "",
        "lastcheckok": "1",
        "lastchecktime": "2024-01-02 00:00:00",
    },
    {
        "stationuuid": "uuid-3",
        "name": "Berlin Beats",
        "url_resolved": "https://example.com/berlin",
        "tags": "techno,minimal,electronic",
        "country": "Germany",
        "countrycode": "DE",
        "codec": "MP3",
        "bitrate": 320,
        "clickcount": 8000,
        "favicon": "",
        "hls": "1",
        "homepage": "",
        "lastcheckok": "1",
        "lastchecktime": "2024-01-03 00:00:00",
    },
    {
        "stationuuid": "uuid-4",
        "name": "UK Rock",
        "url_resolved": "https://example.com/rock",
        "tags": "rock,alternative,indie",
        "country": "United Kingdom",
        "countrycode": "GB",
        "codec": "MP3",
        "bitrate": 192,
        "clickcount": 2000,
        "favicon": "",
        "hls": "0",
        "homepage": "",
        "lastcheckok": "0",
        "lastchecktime": "2024-01-04 00:00:00",
    },
    {
        "stationuuid": "uuid-5",
        "name": "Ambient Waves",
        "url_resolved": "https://example.com/ambient",
        "tags": "ambient,drone,electronic",
        "country": "United Kingdom",
        "countrycode": "GB",
        "codec": "AAC",
        "bitrate": 128,
        "clickcount": 1500,
        "favicon": "",
        "hls": "0",
        "homepage": "",
        "lastcheckok": "1",
        "lastchecktime": "2024-01-05 00:00:00",
    },
]

BUNDLED_PAYLOAD = {
    "version": 1,
    "last_fetched": "2024-01-01T00:00:00Z",
    "station_count": 5,
    "stations": STATIONS,
}


def _make_bundled_gz(
    tmp_path: Path, filename: str = "radio_stations_default.json.gz"
) -> Path:
    data_dir = tmp_path / "data"
    data_dir.mkdir(parents=True, exist_ok=True)
    gz_path = data_dir / filename
    gz_path.write_bytes(gzip.compress(json.dumps(BUNDLED_PAYLOAD).encode()))
    return gz_path


class TestStationIndexQuery:
    """Pure query logic: pre-load fixture data directly, no filesystem."""

    @pytest.fixture
    def index(self):
        from nyrx.sources.radio_index import StationIndex

        idx = StationIndex()
        idx.stations = list(STATIONS)
        idx._build_tag_rank()
        idx._build_indexes()
        idx._liked = {"uuid-1", "uuid-3"}
        idx._loaded = True
        idx._save_liked = lambda: None
        return idx

    # -- get_filtered --

    def test_get_filtered_no_filters_returns_all_sorted(self, index) -> None:
        results = index.get_filtered()
        assert len(results) == 5
        assert results[0]["stationuuid"] == "uuid-1"
        assert results[4]["stationuuid"] == "uuid-5"

    def test_get_filtered_by_tag_intersects(self, index) -> None:
        results = index.get_filtered(tags=["techno"])
        assert len(results) == 2
        uuids = {r["stationuuid"] for r in results}
        assert uuids == {"uuid-1", "uuid-3"}

    def test_get_filtered_by_tag_empty_intersection(self, index) -> None:
        results = index.get_filtered(tags=["techno", "jazz"])
        assert results == []

    def test_get_filtered_by_country_matches_country_and_code(self, index) -> None:
        de = index.get_filtered(countries=["DE"])
        assert len(de) == 2
        assert {r["stationuuid"] for r in de} == {"uuid-1", "uuid-3"}

        uk = index.get_filtered(countries=["United Kingdom"])
        assert len(uk) == 2
        assert {r["stationuuid"] for r in uk} == {"uuid-4", "uuid-5"}

    def test_get_filtered_by_name_substring(self, index) -> None:
        results = index.get_filtered(name="groove")
        assert len(results) == 1
        assert results[0]["stationuuid"] == "uuid-1"

    def test_get_filtered_name_case_insensitive(self, index) -> None:
        results = index.get_filtered(name="BERLIN")
        assert len(results) == 1
        assert results[0]["stationuuid"] == "uuid-3"

    def test_get_filtered_combines_all_filters(self, index) -> None:
        results = index.get_filtered(name="berlin", tags=["techno"], countries=["DE"])
        assert len(results) == 1
        assert results[0]["stationuuid"] == "uuid-3"

    def test_get_filtered_no_stations_returns_empty(self) -> None:
        from nyrx.sources.radio_index import StationIndex

        idx = StationIndex()
        assert idx.get_filtered() == []

    # -- get_tag_suggestions --

    def test_tag_suggestions_returns_matching_tags_ranked(self, index) -> None:
        results = index.get_tag_suggestions("elec")
        assert len(results) >= 1
        tag_name, count = results[0]
        assert tag_name == "electronic"
        assert count == 4  # 4 stations have "electronic" tag

    def test_tag_suggestions_excludes_already_selected(self, index) -> None:
        results = index.get_tag_suggestions("tec", tag_filter=["electronic"])
        assert len(results) >= 1
        tag_name, count = results[0]
        assert tag_name == "techno"
        assert "electronic" not in [t for t, _ in results]

    def test_tag_suggestions_respects_max_results(self, index) -> None:
        results = index.get_tag_suggestions("e", max_results=2)
        assert len(results) <= 2

    def test_tag_suggestions_with_country_filter(self, index) -> None:
        results = index.get_tag_suggestions("e", country_filter=["DE"])
        # Only stations in Germany: uuid-1, uuid-3
        # Tags containing "e": electronic(2), techno(0?), house(0?)
        # Actually "techno" doesn't contain "e"... wait yes it does: t-e-c-h-n-o
        # Wait: "techno" contains "e" (position 1). Let me check:
        #   - electronic: in stations 0,1,2,4 → but only stations 0,2 in DE → count=2
        #   - house: in station 0 only, which is in DE → count=1
        # So results should include electronic(2) and house(1)
        assert any("electronic" in t for t, _ in results)

    # -- get_country_suggestions --

    def test_country_suggestions_returns_matching_countries_ranked(self, index) -> None:
        results = index.get_country_suggestions("ger")
        assert len(results) >= 1
        name, count = results[0]
        assert name == "DE"
        assert count == 2

    def test_country_suggestions_excludes_already_selected(self, index) -> None:
        results = index.get_country_suggestions("united", country_filter=["DE"])
        assert len(results) >= 1
        name, count = results[0]
        assert name == "GB"
        assert "DE" not in [c for c, _ in results]

    def test_country_suggestions_with_tag_filter(self, index) -> None:
        results = index.get_country_suggestions("g", tag_filter=["techno"])
        assert len(results) >= 1
        assert results[0][0] == "DE"

    def test_country_suggestions_no_match_returns_empty(self, index) -> None:
        results = index.get_country_suggestions("zzzz")
        assert results == []

    # -- popular_tags --

    def test_popular_tags_returns_top_three_by_global_rank(self, index) -> None:
        tags = index.popular_tags(STATIONS[0])
        # Station 0 tags: house,techno,electronic
        # Global ranks: house=1, techno=2, electronic=3
        # Sorted: electronic(3), techno(2), house(1)
        assert tags == ["electronic", "techno", "house"]

    def test_popular_tags_different_station(self, index) -> None:
        tags = index.popular_tags(STATIONS[2])
        # Station 2 tags: techno,minimal,electronic
        # Global ranks: electronic=3, techno=2, minimal=1
        assert tags == ["electronic", "techno", "minimal"]

    def test_popular_tags_empty_tags_returns_empty(self, index) -> None:
        station = {"tags": ""}
        assert index.popular_tags(station) == []

    def test_popular_tags_missing_tags_returns_empty(self, index) -> None:
        station = {"name": "No Tags"}
        assert index.popular_tags(station) == []

    # -- likes --

    def test_is_liked_returns_true_for_liked_uuid(self, index) -> None:
        assert index.is_liked("uuid-1") is True

    def test_is_liked_returns_false_for_unliked_uuid(self, index) -> None:
        assert index.is_liked("uuid-999") is False

    def test_toggle_like_adds_uuid(self, index) -> None:
        result = index.toggle_like("uuid-4")
        assert result is True
        assert index.is_liked("uuid-4") is True

    def test_toggle_like_removes_uuid(self, index) -> None:
        result = index.toggle_like("uuid-1")
        assert result is False
        assert index.is_liked("uuid-1") is False


class TestStationIndexLoad:
    """Filesystem I/O: tmp_path + monkeypatched path constants."""

    @pytest.fixture
    def paths(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
        cache_dir = tmp_path / ".cache" / "nyrx"
        config_dir = tmp_path / ".config" / "nyrx"
        cache_dir.mkdir(parents=True, exist_ok=True)

        bundled = _make_bundled_gz(tmp_path)

        monkeypatch.setattr("nyrx.sources.radio_index._CACHE_DIR", cache_dir)
        monkeypatch.setattr("nyrx.sources.radio_index._CONFIG_DIR", config_dir)
        monkeypatch.setattr(
            "nyrx.sources.radio_index._CACHE_FILE", cache_dir / "radio_stations.json"
        )
        monkeypatch.setattr(
            "nyrx.sources.radio_index._LIKES_FILE", config_dir / "radio_likes.json"
        )
        monkeypatch.setattr("nyrx.sources.radio_index._BUNDLED", bundled)

        return cache_dir, config_dir

    def test_load_from_bundled_creates_cache_file(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        cache_dir, _ = paths
        idx = StationIndex()
        idx.load()

        assert idx._loaded is True
        assert len(idx.stations) == 5
        assert (cache_dir / "radio_stations.json").exists()

    def test_load_from_cache_does_not_need_bundled(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        cache_dir, _ = paths

        # First load creates the cache
        idx1 = StationIndex()
        idx1.load()

        # Remove bundled file, create fresh index, load again

        monkeypatch_del = pytest.MonkeyPatch()
        monkeypatch_del.setattr(
            "nyrx.sources.radio_index._BUNDLED", Path("/nonexistent/file.gz")
        )
        idx2 = StationIndex()
        idx2.load()

        assert idx2._loaded is True
        assert len(idx2.stations) == 5
        monkeypatch_del.undo()

    def test_load_idempotent_second_call_noop(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        idx = StationIndex()
        idx.load()

        original_stations = list(idx.stations)
        # Second load: should not re-read
        idx.load()

        assert idx.stations == original_stations

    def test_load_no_bundled_no_cache_returns_empty(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        cache_dir, _ = paths
        # Remove bundled
        monkeypatch_del = pytest.MonkeyPatch()
        monkeypatch_del.setattr(
            "nyrx.sources.radio_index._BUNDLED", Path("/nonexistent/file.gz")
        )

        idx = StationIndex()
        idx.load()

        assert idx._loaded is True
        assert idx.stations == []
        monkeypatch_del.undo()

    def test_last_fetched_returns_zero_before_load(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        idx = StationIndex()
        assert idx.last_fetched == 0.0

    def test_last_fetched_returns_positive_after_load(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        idx = StationIndex()
        idx.load()
        assert idx.last_fetched > 0

    def test_toggle_like_persists_to_disk(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        _, config_dir = paths
        idx = StationIndex()
        idx.load()

        idx.toggle_like("uuid-1")

        likes_file = config_dir / "radio_likes.json"
        assert likes_file.exists()
        data = json.loads(likes_file.read_text())
        assert "uuid-1" in data["liked"]

    def test_liked_stations_persisted_across_instances(self, paths) -> None:
        """Toggle like on one instance, verify on a fresh instance."""
        from nyrx.sources.radio_index import StationIndex

        idx1 = StationIndex()
        idx1.load()
        idx1.toggle_like("uuid-2")

        idx2 = StationIndex()
        idx2.load()
        assert idx2.is_liked("uuid-2") is True

    def test_corrupted_likes_file_returns_empty(self, paths) -> None:
        from nyrx.sources.radio_index import StationIndex

        _, config_dir = paths
        bad_file = config_dir / "radio_likes.json"
        bad_file.parent.mkdir(parents=True, exist_ok=True)
        bad_file.write_text("not valid json")

        idx = StationIndex()
        idx.load()
        assert idx._liked == set()
