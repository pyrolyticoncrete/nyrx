# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for SoundCloud API parser functions (D01 supplement).

Every function that extracts fields from a raw API response is tested
against a static JSON fixture.  Network calls are mocked; parsing
logic is exercised with real field values.
"""

from __future__ import annotations

import json
import logging
import time
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError

import pytest

FIXTURES = Path(__file__).parent / "fixtures" / "sc_api"


def _load(name: str) -> dict | list:
    return json.loads((FIXTURES / name).read_text())


class TestFetchArtistProfile:
    def test_parses_all_profile_fields(self) -> None:
        fixture = _load("artist_profile.json")

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=fixture),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_profile

            result = fetch_artist_profile("123456")

        assert result is not None
        assert result["artist_id"] == "123456"
        assert result["name"] == "Test Artist"
        assert result["description"] == "A test artist profile for unit testing"
        assert result["location"] == "Berlin, Germany"
        assert result["permalink"] == "test-artist"
        assert result["followers_count"] == 1500
        assert result["track_count"] == 42
        assert result["playlist_count"] == 3

    def test_returns_none_on_missing_client_id(self) -> None:
        with patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value=None):
            from nyrx.sources.soundcloud.api import fetch_artist_profile

            assert fetch_artist_profile("123456") is None


class TestFetchArtistUploads:
    def test_parses_all_uploads(self) -> None:
        fixture = _load("artist_uploads.json")

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=fixture),
            patch("nyrx.sources.soundcloud.api._get_db") as mock_db,
        ):
            mock_db.return_value.execute.return_value.fetchall.return_value = []
            from nyrx.sources.soundcloud.api import fetch_artist_uploads

            results = fetch_artist_uploads("123456", skip_dedup=True)

        assert len(results) == 2
        assert results[0]["track_id"] == "78901"
        assert results[0]["title"] == "First Upload"
        assert results[0]["channel"] == "Test Artist"
        assert results[0]["duration"] == 240.0  # ms → seconds
        assert results[0]["like_count"] == 120
        assert results[0]["view_count"] == 5000
        assert results[0]["repost_count"] == 15
        assert results[0]["genre"] == "electronic"
        assert results[0]["source"] == "soundcloud"

    def test_returns_none_on_api_failure(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=None),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_uploads

            assert fetch_artist_uploads("123456", skip_dedup=True) is None


class TestFetchArtistLikes:
    def test_parses_liked_tracks(self) -> None:
        fixture = _load("artist_likes.json")

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=fixture),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_likes

            results = fetch_artist_likes("654321", max_tracks=None)

        assert len(results) == 2
        assert results[0]["track_id"] == "11111"
        assert results[0]["title"] == "Liked Track One"
        assert results[0]["channel"] == "Other Artist"
        assert results[0]["duration"] == 200.0  # ms → seconds
        assert results[0]["like_count"] == 300
        assert results[0]["view_count"] == 15000
        assert results[0]["source"] == "soundcloud"
        assert results[0]["liked_at"] == "2024-06-15T10:00:00Z"

    def test_respects_max_tracks_limit(self) -> None:
        fixture = _load("artist_likes.json")

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=fixture),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_likes

            results = fetch_artist_likes("654321", max_tracks=1)

        assert len(results) == 1


class TestBatchResolveViaClientId:
    def test_parses_tracks_from_api_response(self) -> None:
        fixture = _load("track_batch.json")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fixture).encode()

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id",
                return_value="fake_cid",
            ),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                return_value=mock_resp,
            ) as mock_urlopen,
        ):
            from nyrx.sources.soundcloud.api import batch_resolve_via_client_id

            results = batch_resolve_via_client_id(["33333", "44444"])

        mock_urlopen.assert_called_once_with(
            "https://api-v2.soundcloud.com/tracks?ids=33333,44444&client_id=fake_cid",
            timeout=15,
        )
        assert len(results) == 2
        assert results[0]["yt_id"] == "33333"
        assert results[0]["title"] == "Batch Track One"
        assert results[0]["channel"] == "Batch Artist"
        assert results[0]["duration"] == 210.0
        assert results[0]["like_count"] == 250
        assert results[0]["view_count"] == 12000
        assert results[0]["genre"] == "house"
        assert results[0]["source"] == "soundcloud"

    def test_empty_response_returns_empty_list(self) -> None:
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps([]).encode()

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id",
                return_value="fake_cid",
            ),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                return_value=mock_resp,
            ) as mock_urlopen,
        ):
            from nyrx.sources.soundcloud.api import batch_resolve_via_client_id

            assert batch_resolve_via_client_id(["nonexistent"]) == []

        mock_urlopen.assert_called_once()
        # URL should contain the nonexistent ID
        url_arg: str = mock_urlopen.call_args[0][0]
        assert "nonexistent" in url_arg

    def test_raises_runtime_error_on_api_failure(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=None),
        ):
            from nyrx.sources.soundcloud.api import batch_resolve_via_client_id

            with pytest.raises(RuntimeError, match="Failed to fetch track metadata"):
                batch_resolve_via_client_id(["33333"])

    def test_raises_runtime_error_when_no_client_id(self) -> None:
        with patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value=None):
            from nyrx.sources.soundcloud.api import batch_resolve_via_client_id

            with pytest.raises(
                RuntimeError, match="Could not obtain SoundCloud client_id"
            ):
                batch_resolve_via_client_id(["33333"])


class TestResolveViaApiCallRouting:
    """Item 25: permalink/track_id resolvers now route through _api_call."""

    def test_permalink_resolves_via_api_call_with_deadline(self) -> None:
        fixture = {
            "likes_count": 10,
            "playback_count": 20,
            "waveform_url": "https://wf/1.json",
            "user": {"id": "u1", "permalink": "artist"},
        }
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call", return_value=fixture
            ) as mock_api,
        ):
            from nyrx.sources.soundcloud.api import _resolve_via_permalink

            result = _resolve_via_permalink("https://soundcloud.com/a/b", timeout=2.0)

        mock_api.assert_called_once()
        url_arg: str = mock_api.call_args[0][0]
        assert "/resolve?url=" in url_arg
        assert "client_id=cid" in url_arg
        assert mock_api.call_args.kwargs["timeout"] == 2.0
        deadline: float = mock_api.call_args.kwargs["deadline"]
        assert 1.0 < deadline - time.monotonic() <= 2.0
        assert result == {
            "like_count": 10,
            "view_count": 20,
            "waveform_url": "https://wf/1.json",
            "uploader_id": "u1",
            "permalink": "artist",
        }

    def test_track_id_resolves_via_api_call_with_deadline(self) -> None:
        fixture = [
            {
                "likes_count": 5,
                "playback_count": 7,
                "waveform_url": "",
                "user": {"id": "u2", "permalink": "p2"},
            }
        ]
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call", return_value=fixture
            ) as mock_api,
        ):
            from nyrx.sources.soundcloud.api import _resolve_via_track_id

            result = _resolve_via_track_id("77", timeout=2.0)

        mock_api.assert_called_once()
        url_arg: str = mock_api.call_args[0][0]
        assert "/tracks?ids=77&client_id=cid" in url_arg
        assert mock_api.call_args.kwargs["timeout"] == 2.0
        deadline: float = mock_api.call_args.kwargs["deadline"]
        assert 1.0 < deadline - time.monotonic() <= 2.0
        assert result == {
            "like_count": 5,
            "view_count": 7,
            "waveform_url": "",
            "uploader_id": "u2",
            "permalink": "p2",
        }


class TestSearchSoundcloud:
    def test_parses_ytdlp_output(self) -> None:
        fixture = _load("search_result.json")
        lines = "\n".join(json.dumps(item) for item in fixture)
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = lines

        with patch(
            "nyrx.sources.soundcloud.api.subprocess.run",
            return_value=mock_proc,
        ) as mock_subprocess:
            from nyrx.sources.soundcloud.api import search_soundcloud

            results = search_soundcloud("test query", limit=2)

        mock_subprocess.assert_called_once_with(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--no-warnings",
                "scsearch2:test query",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        assert len(results) == 2
        assert results[0]["yt_id"] == "55555"
        assert results[0]["title"] == "Search Result One"
        assert results[0]["channel"] == "Search Artist"
        assert results[0]["duration"] == 220
        assert results[0]["views"] == 7500
        assert results[0]["likes_count"] == 400
        assert results[0]["source"] == "soundcloud"

    def test_raises_on_ytdlp_failure(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 1
        mock_proc.stderr = "yt-dlp error"

        with patch(
            "nyrx.sources.soundcloud.api.subprocess.run",
            return_value=mock_proc,
        ) as mock_subprocess:
            from nyrx.sources.soundcloud.api import search_soundcloud

            with pytest.raises(RuntimeError, match="yt-dlp error"):
                search_soundcloud("test")

        mock_subprocess.assert_called_once()
        cmd_arg: list = mock_subprocess.call_args[0][0]
        assert "scsearch" in " ".join(cmd_arg)


class TestFetchWaveform:
    def test_parses_waveform_data(self) -> None:
        fixture = _load("waveform.json")
        mock_resp = MagicMock()
        mock_resp.read.return_value = json.dumps(fixture).encode()
        mock_resp.__enter__.return_value = mock_resp

        with (
            patch("nyrx.sources.soundcloud.api.urllib.request.Request") as mock_request,
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                return_value=mock_resp,
            ),
        ):
            from nyrx.sources.soundcloud.api import fetch_waveform

            result = fetch_waveform("https://wave1.sndcdn.com/test.json")

        mock_request.assert_called_once_with(
            "https://wave1.sndcdn.com/test.json",
            headers={"User-Agent": "Mozilla/5.0"},
        )
        assert result is not None
        assert result["width"] == 1800
        assert result["height"] == 280
        assert len(result["samples"]) == 40
        assert result["samples"][0] == 0
        assert result["samples"][10] == 168

    def test_returns_none_on_empty_url(self) -> None:
        from nyrx.sources.soundcloud.api import fetch_waveform

        assert fetch_waveform("") is None


class TestResolveScUser:
    def test_parses_user_data(self) -> None:
        fixture = _load("resolve_user.json")

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=fixture),
        ):
            from nyrx.sources.soundcloud.api import resolve_sc_user

            result = resolve_sc_user("https://soundcloud.com/resolved-user")

        assert result is not None
        assert result.get("username") == "Resolved User"
        assert result.get("id") == 123456
        assert result.get("permalink") == "resolved-user"

    def test_returns_none_on_missing_username(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id", return_value="fake_cid"
            ),
            patch(
                "nyrx.sources.soundcloud.api._api_call",
                return_value={"id": 1},
            ),  # no "username" key
        ):
            from nyrx.sources.soundcloud.api import resolve_sc_user

            assert resolve_sc_user("https://soundcloud.com/test") is None


class TestFetchTrendingPlaylist:
    def test_parses_full_pipeline(self) -> None:
        """yt-dlp flat-playlist → API batch resolve → parsed results."""
        track_batch = _load("track_batch.json")
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            json.dumps({"id": 33333, "title": "Batch Track One"})
            + "\n"
            + json.dumps({"id": 44444, "title": "Batch Track Two"})
            + "\n"
        )

        mock_api_resp = MagicMock()
        mock_api_resp.read.return_value = json.dumps(track_batch).encode()

        with (
            patch(
                "nyrx.sources.soundcloud.api._scrape_client_id",
                return_value="fake_cid",
            ),
            patch(
                "nyrx.sources.soundcloud.api.subprocess.run",
                return_value=mock_proc,
            ) as mock_subprocess,
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                return_value=mock_api_resp,
            ) as mock_urlopen,
        ):
            from nyrx.sources.soundcloud.api import fetch_trending_playlist

            results = fetch_trending_playlist("techno")

        mock_subprocess.assert_called_once_with(
            [
                "yt-dlp",
                "--flat-playlist",
                "--dump-json",
                "--no-warnings",
                "https://soundcloud.com/trending-music-us/sets/techno",
            ],
            capture_output=True,
            text=True,
            timeout=30,
        )
        mock_urlopen.assert_called_once_with(
            "https://api-v2.soundcloud.com/tracks?ids=33333,44444&client_id=fake_cid",
            timeout=15,
        )
        assert len(results) == 2
        assert results[0]["yt_id"] == "33333"
        assert results[0]["title"] == "Batch Track One"
        assert results[0]["genre"] == "house"
        assert results[0]["source"] == "soundcloud"


def _http_err(code: int = 403) -> HTTPError:
    return HTTPError("http://x", code, "Forbidden", {}, None)


class TestHighresScThumb:
    def test_large_suffix_upgraded(self) -> None:
        from nyrx.sources.soundcloud.api import _highres_sc_thumb

        assert (
            _highres_sc_thumb("https://i1.sndcdn.com/artworks/abc-large.jpg")
            == "https://i1.sndcdn.com/artworks/abc-original.jpg"
        )

    def test_resized_suffix_upgraded(self) -> None:
        from nyrx.sources.soundcloud.api import _highres_sc_thumb

        assert (
            _highres_sc_thumb("https://x/a-t500x500.jpg") == "https://x/a-original.jpg"
        )

    def test_crop_suffix_upgraded(self) -> None:
        from nyrx.sources.soundcloud.api import _highres_sc_thumb

        assert _highres_sc_thumb("https://x/a-crop.jpg") == "https://x/a-original.jpg"

    def test_no_suffix_unchanged(self) -> None:
        from nyrx.sources.soundcloud.api import _highres_sc_thumb

        url = "https://x/a.jpg"
        assert _highres_sc_thumb(url) == url

    def test_lookahead_guard_keeps_similar_prefix(self) -> None:
        from nyrx.sources.soundcloud.api import _highres_sc_thumb

        assert _highres_sc_thumb("https://x/a-largex.jpg") == "https://x/a-largex.jpg"

    def test_empty_url_unchanged(self) -> None:
        from nyrx.sources.soundcloud.api import _highres_sc_thumb

        assert _highres_sc_thumb("") == ""


class TestApiCall:
    def test_403_refreshes_and_retries(self) -> None:
        ok = MagicMock()
        ok.read.return_value = json.dumps({"ok": True}).encode()

        with (
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=[_http_err(403), ok],
            ) as mock_urlopen,
            patch(
                "nyrx.sources.soundcloud.api.refresh_client_id", return_value="newcid"
            ) as mock_refresh,
            patch("nyrx.sources.soundcloud.api.time.sleep") as mock_sleep,
        ):
            from nyrx.sources.soundcloud.api import _api_call

            result = _api_call(
                "https://api-v2.soundcloud.com/tracks?ids=1&client_id=dead"
            )

        assert result == {"ok": True}
        assert mock_urlopen.call_count == 2
        assert mock_urlopen.call_args_list[0][0][0].endswith("client_id=dead")
        assert mock_urlopen.call_args_list[1][0][0].endswith("client_id=newcid")
        mock_refresh.assert_called_once()
        mock_sleep.assert_not_called()

    def test_persistent_403_returns_none(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=_http_err(403),
            ) as mock_urlopen,
            patch(
                "nyrx.sources.soundcloud.api.refresh_client_id", return_value="newcid"
            ) as mock_refresh,
        ):
            from nyrx.sources.soundcloud.api import _api_call

            assert _api_call("https://x/tracks?ids=1&client_id=dead") is None

        assert mock_urlopen.call_count == 3
        assert mock_refresh.call_count == 3

    def test_403_without_refresh_sleeps_then_none(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=_http_err(403),
            ),
            patch(
                "nyrx.sources.soundcloud.api.refresh_client_id", return_value=None
            ) as mock_refresh,
            patch("nyrx.sources.soundcloud.api.time.sleep") as mock_sleep,
        ):
            from nyrx.sources.soundcloud.api import _api_call

            assert _api_call("https://x/tracks?ids=1&client_id=dead") is None

        mock_refresh.assert_called()
        assert mock_sleep.call_args_list[0][0][0] == 1
        assert mock_sleep.call_args_list[1][0][0] == 2

    def test_non_403_http_error_no_refresh(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=_http_err(500),
            ),
            patch("nyrx.sources.soundcloud.api.refresh_client_id") as mock_refresh,
            patch("nyrx.sources.soundcloud.api.time.sleep") as mock_sleep,
        ):
            from nyrx.sources.soundcloud.api import _api_call

            assert _api_call("https://x/tracks?ids=1&client_id=dead") is None

        mock_refresh.assert_not_called()
        assert mock_sleep.call_count == 2

    def test_generic_exception_backoff_then_none(self) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=OSError("boom"),
            ),
            patch("nyrx.sources.soundcloud.api.time.sleep") as mock_sleep,
        ):
            from nyrx.sources.soundcloud.api import _api_call

            assert _api_call("https://x/tracks?ids=1&client_id=dead") is None

        assert mock_sleep.call_count == 2

    def test_failure_logs_redact_client_id(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with (
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=OSError("boom"),
            ),
            patch("nyrx.sources.soundcloud.api.time.sleep"),
            caplog.at_level(logging.DEBUG, logger="nyrx.sources.soundcloud.api"),
        ):
            from nyrx.sources.soundcloud.api import _api_call

            assert _api_call("https://x/tracks?ids=1&client_id=secret123") is None

        assert "client_id=***" in caplog.text
        assert "secret123" not in caplog.text


class TestEnsureClientId:
    def test_200_returns_true(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                return_value=MagicMock(status=200),
            ),
        ):
            from nyrx.sources.soundcloud.api import ensure_client_id

            assert ensure_client_id() is True

    def test_403_refreshes_and_retries(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=[_http_err(403), MagicMock(status=200)],
            ) as mock_urlopen,
            patch(
                "nyrx.sources.soundcloud.api.refresh_client_id", return_value="newcid"
            ) as mock_refresh,
        ):
            from nyrx.sources.soundcloud.api import ensure_client_id

            assert ensure_client_id() is True

        assert mock_urlopen.call_count == 2
        mock_refresh.assert_called_once()

    def test_non_403_error_returns_false(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=_http_err(500),
            ),
            patch("nyrx.sources.soundcloud.api.refresh_client_id") as mock_refresh,
        ):
            from nyrx.sources.soundcloud.api import ensure_client_id

            assert ensure_client_id() is False

        mock_refresh.assert_not_called()

    def test_no_cid_returns_false_without_network(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value=None),
            patch("nyrx.sources.soundcloud.api.urllib.request.urlopen") as mock_urlopen,
        ):
            from nyrx.sources.soundcloud.api import ensure_client_id

            assert ensure_client_id() is False

        mock_urlopen.assert_not_called()

    def test_always_raises_returns_false(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=OSError("boom"),
            ),
        ):
            from nyrx.sources.soundcloud.api import ensure_client_id

            assert ensure_client_id() is False

    def test_network_error_logs_debug(self, caplog: pytest.LogCaptureFixture) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api.urllib.request.urlopen",
                side_effect=OSError("boom"),
            ),
            caplog.at_level(logging.DEBUG, logger="nyrx.sources.soundcloud.api"),
        ):
            from nyrx.sources.soundcloud.api import ensure_client_id

            assert ensure_client_id() is False

        assert "ensure_client_id: validation request failed" in caplog.text


class TestScrapeClientId:
    @pytest.fixture(autouse=True)
    def _reset(self, monkeypatch, tmp_path) -> None:
        monkeypatch.setattr("nyrx.sources.soundcloud.api._client_id", None)
        monkeypatch.setattr(
            "nyrx.sources.soundcloud.api.SC_CLIENT_ID_CACHE",
            tmp_path / "client_id_cache",
        )

    def test_module_cache_hit(self, monkeypatch) -> None:
        monkeypatch.setattr("nyrx.sources.soundcloud.api._client_id", "cached_id")
        with patch(
            "nyrx.sources.soundcloud.api.urllib.request.urlopen"
        ) as mock_urlopen:
            from nyrx.sources.soundcloud.api import _scrape_client_id

            assert _scrape_client_id() == "cached_id"

        mock_urlopen.assert_not_called()

    def test_disk_cache_hit(self) -> None:
        from nyrx.sources.soundcloud.api import SC_CLIENT_ID_CACHE, _scrape_client_id

        SC_CLIENT_ID_CACHE.parent.mkdir(parents=True, exist_ok=True)
        SC_CLIENT_ID_CACHE.write_text("disk_id")
        assert _scrape_client_id() == "disk_id"

    def test_scrape_success_persists(self) -> None:
        from nyrx.sources.soundcloud.api import SC_CLIENT_ID_CACHE, _scrape_client_id

        home = MagicMock()
        home.read.return_value = b'<html><script src="https://a-v2.sndcdn.com/assets/app.js"></script></html>'
        bundle = MagicMock()
        bundle.read.return_value = (
            b'var x; client_id : "abc123def456abc123def456abc123de";'
        )
        with patch(
            "nyrx.sources.soundcloud.api.urllib.request.urlopen",
            side_effect=[home, bundle],
        ):
            assert _scrape_client_id() == "abc123def456abc123def456abc123de"
        assert SC_CLIENT_ID_CACHE.read_text() == "abc123def456abc123def456abc123de"

    def test_scrape_no_match_returns_none(self) -> None:
        home = MagicMock()
        home.read.return_value = b'<html><script src="https://a-v2.sndcdn.com/assets/app.js"></script></html>'
        bundle = MagicMock()
        bundle.read.return_value = b"var x = 1;"
        with patch(
            "nyrx.sources.soundcloud.api.urllib.request.urlopen",
            side_effect=[home, bundle],
        ):
            from nyrx.sources.soundcloud.api import _scrape_client_id

            assert _scrape_client_id() is None

    def test_homepage_fetch_raises_returns_none(self) -> None:
        with patch(
            "nyrx.sources.soundcloud.api.urllib.request.urlopen",
            side_effect=OSError("net down"),
        ):
            from nyrx.sources.soundcloud.api import _scrape_client_id

            assert _scrape_client_id() is None


class TestFetchArtistUploadsDedup:
    def test_three_consecutive_dups_early_stop(self) -> None:
        page1 = {
            "collection": [
                {"id": "known1", "title": "K1", "user": {}},
                {"id": "known2", "title": "K2", "user": {}},
                {
                    "id": "new1",
                    "title": "New",
                    "user": {},
                    "duration": 120000,
                    "likes_count": 5,
                    "playback_count": 3,
                    "reposts_count": 1,
                    "genre": "pop",
                    "permalink_url": "https://soundcloud.com/a/new1",
                },
            ],
            "next_href": "https://api-v2.soundcloud.com/users/1/tracks?limit=50&offset=50",
        }
        page2 = {
            "collection": [
                {"id": "known3", "title": "K3", "user": {}},
                {"id": "known4", "title": "K4", "user": {}},
                {"id": "known5", "title": "K5", "user": {}},
            ],
            "next_href": "",
        }
        mock_db = MagicMock()
        mock_db.execute.return_value.fetchall.return_value = [
            {"track_id": t} for t in ("known1", "known2", "known3", "known4", "known5")
        ]
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call", side_effect=[page1, page2]
            ) as mock_api,
            patch("nyrx.sources.soundcloud.api._get_db", return_value=mock_db),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_uploads

            results = fetch_artist_uploads("1")

        assert [r["track_id"] for r in results] == ["new1"]
        assert mock_api.call_count == 2

    def test_skip_dedup_bypasses_db(self) -> None:
        page1 = {
            "collection": [{"id": "known1", "title": "K1", "user": {}}],
            "next_href": "",
        }
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=page1),
            patch(
                "nyrx.sources.soundcloud.api._get_db", return_value=MagicMock()
            ) as mock_db_get,
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_uploads

            results = fetch_artist_uploads("1", skip_dedup=True)

        mock_db_get.assert_not_called()
        assert [r["track_id"] for r in results] == ["known1"]


class TestFetchArtistUploadsPagination:
    def test_next_href_gets_client_id_appended(self) -> None:
        page1 = {
            "collection": [{"id": "a", "title": "A", "user": {}}],
            "next_href": "https://api-v2.soundcloud.com/users/1/tracks?limit=50&offset=50",
        }
        page2 = {"collection": [{"id": "b", "title": "B", "user": {}}], "next_href": ""}
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call", side_effect=[page1, page2]
            ) as mock_api,
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_uploads

            results = fetch_artist_uploads("1", skip_dedup=True)

        assert [r["track_id"] for r in results] == ["a", "b"]
        assert mock_api.call_count == 2
        assert mock_api.call_args_list[0][0][0] == (
            "https://api-v2.soundcloud.com/users/1/tracks?client_id=cid&limit=50"
        )
        assert mock_api.call_args_list[1][0][0] == (
            "https://api-v2.soundcloud.com/users/1/tracks?limit=50&offset=50&client_id=cid"
        )


class TestFetchArtistLikesExtras:
    def test_skips_non_track_items(self) -> None:
        collection = [
            {"kind": "playlist", "playlist": {"id": "pl"}},
            {"track": {"id": "t1", "title": "T1", "user": {}}},
        ]
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call",
                return_value={"collection": collection, "next_href": ""},
            ),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_likes

            results = fetch_artist_likes("1", max_tracks=None)

        assert [r["track_id"] for r in results] == ["t1"]

    def test_caps_mid_collection(self) -> None:
        collection = [
            {"track": {"id": f"t{i}", "title": f"T{i}", "user": {}}} for i in range(3)
        ]
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call",
                return_value={"collection": collection, "next_href": ""},
            ),
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_likes

            results = fetch_artist_likes("1", max_tracks=2)

        assert [r["track_id"] for r in results] == ["t0", "t1"]

    def test_paginates_and_appends_client_id(self) -> None:
        page1 = {
            "collection": [{"track": {"id": "t1", "title": "T1", "user": {}}}],
            "next_href": "https://api-v2.soundcloud.com/users/1/likes?limit=200&offset=200",
        }
        page2 = {
            "collection": [{"track": {"id": "t2", "title": "T2", "user": {}}}],
            "next_href": "",
        }
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call", side_effect=[page1, page2]
            ) as mock_api,
        ):
            from nyrx.sources.soundcloud.api import fetch_artist_likes

            results = fetch_artist_likes("1", max_tracks=None)

        assert [r["track_id"] for r in results] == ["t1", "t2"]
        assert mock_api.call_args_list[1][0][0].endswith("&client_id=cid")


class TestEnrichScTrack:
    def test_branch1_waveform_fetch_only(self) -> None:
        from nyrx.sources.soundcloud.api import enrich_sc_track

        with (
            patch(
                "nyrx.sources.soundcloud.api._try_fetch_waveform_samples",
                return_value=[0, 1],
            ) as mock_samples,
            patch("nyrx.sources.soundcloud.api._resolve_via_permalink") as mock_pl,
            patch("nyrx.sources.soundcloud.api._resolve_via_track_id") as mock_tid,
        ):
            result = enrich_sc_track(
                {
                    "yt_id": "5",
                    "waveform_url": "https://wf/1.json",
                    "like_count": 4,
                    "view_count": 9,
                }
            )

        assert result["waveform_samples"] == [0, 1]
        assert result["like_count"] == 4
        assert result["view_count"] == 9
        mock_samples.assert_called_once()
        mock_pl.assert_not_called()
        mock_tid.assert_not_called()

    def test_branch2_permalink_resolve(self) -> None:
        from nyrx.sources.soundcloud.api import enrich_sc_track

        with (
            patch(
                "nyrx.sources.soundcloud.api._resolve_via_permalink",
                return_value={"like_count": 10, "view_count": 20, "waveform_url": ""},
            ) as mock_pl,
            patch(
                "nyrx.sources.soundcloud.api._try_fetch_waveform_samples"
            ) as mock_samples,
        ):
            result = enrich_sc_track(
                {"yt_id": "5", "permalink_url": "https://soundcloud.com/artist/track"}
            )

        mock_pl.assert_called_once()
        assert mock_pl.call_args[0][0] == "https://soundcloud.com/artist/track"
        assert result["like_count"] == 10
        assert result["view_count"] == 20
        mock_samples.assert_not_called()

    def test_branch3_track_id_resolve(self) -> None:
        from nyrx.sources.soundcloud.api import enrich_sc_track

        with (
            patch(
                "nyrx.sources.soundcloud.api._resolve_via_track_id",
                return_value={"like_count": 1, "view_count": 2, "waveform_url": ""},
            ) as mock_tid,
            patch(
                "nyrx.sources.soundcloud.api._try_fetch_waveform_samples"
            ) as mock_samples,
        ):
            result = enrich_sc_track({"yt_id": "77"})

        mock_tid.assert_called_once()
        assert mock_tid.call_args[0][0] == "77"
        assert result["like_count"] == 1
        mock_samples.assert_not_called()

    def test_branch3_missing_yt_id_returns_baseline(self) -> None:
        from nyrx.sources.soundcloud.api import enrich_sc_track

        with (
            patch("nyrx.sources.soundcloud.api._resolve_via_permalink") as mock_pl,
            patch("nyrx.sources.soundcloud.api._resolve_via_track_id") as mock_tid,
        ):
            result = enrich_sc_track({"yt_id": ""})

        assert result["waveform_samples"] is None
        assert result["like_count"] == 0
        assert result["view_count"] == 0
        mock_pl.assert_not_called()
        mock_tid.assert_not_called()

    def test_deadline_expired_returns_baseline(self) -> None:
        from nyrx.sources.soundcloud.api import enrich_sc_track

        with (
            patch(
                "nyrx.sources.soundcloud.api.time.monotonic", side_effect=[0.0, 100.0]
            ),
            patch("nyrx.sources.soundcloud.api._resolve_via_permalink") as mock_pl,
        ):
            result = enrich_sc_track(
                {"yt_id": "5", "permalink_url": "https://soundcloud.com/a/b"}
            )

        assert result["waveform_samples"] is None
        mock_pl.assert_not_called()


class TestFetchPlaylistTracks:
    def test_batches_ids_by_50(self) -> None:
        raw = [{"id": i} for i in range(1, 121)]

        def fake_batch(ids):
            return [{"yt_id": tid} for tid in ids]

        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch(
                "nyrx.sources.soundcloud.api._api_call", return_value={"tracks": raw}
            ),
            patch(
                "nyrx.sources.soundcloud.api.batch_resolve_via_client_id",
                side_effect=fake_batch,
            ) as mock_batch,
        ):
            from nyrx.sources.soundcloud.api import fetch_playlist_tracks

            results = fetch_playlist_tracks("col1")

        assert mock_batch.call_count == 3
        assert mock_batch.call_args_list[0][0][0] == [str(i) for i in range(1, 51)]
        assert mock_batch.call_args_list[1][0][0] == [str(i) for i in range(51, 101)]
        assert mock_batch.call_args_list[2][0][0] == [str(i) for i in range(101, 121)]
        assert len(results) == 120

    def test_no_tracks_returns_empty(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch("nyrx.sources.soundcloud.api._api_call", return_value={"tracks": []}),
            patch(
                "nyrx.sources.soundcloud.api.batch_resolve_via_client_id"
            ) as mock_batch,
        ):
            from nyrx.sources.soundcloud.api import fetch_playlist_tracks

            assert fetch_playlist_tracks("col1") == []

        mock_batch.assert_not_called()

    def test_api_failure_returns_none(self) -> None:
        with (
            patch("nyrx.sources.soundcloud.api._scrape_client_id", return_value="cid"),
            patch("nyrx.sources.soundcloud.api._api_call", return_value=None),
        ):
            from nyrx.sources.soundcloud.api import fetch_playlist_tracks

            assert fetch_playlist_tracks("col1") is None


class TestBatchResolveViaYtdlp:
    def test_permalink_extracted_from_webpage_url(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            json.dumps(
                {
                    "id": "1",
                    "webpage_url": "https://soundcloud.com/my-artist/track",
                    "title": "T",
                    "uploader": "Artist",
                }
            )
            + "\n"
        )

        with patch(
            "nyrx.sources.soundcloud.api.subprocess.run", return_value=mock_proc
        ):
            from nyrx.sources.soundcloud.api import batch_resolve_via_ytdlp

            results = batch_resolve_via_ytdlp("123")

        assert results[0]["permalink"] == "my-artist"
        assert results[0]["url"] == "https://soundcloud.com/my-artist/track"

    def test_no_match_permalink_empty(self) -> None:
        mock_proc = MagicMock()
        mock_proc.returncode = 0
        mock_proc.stdout = (
            json.dumps(
                {
                    "id": "1",
                    "webpage_url": "https://example.com/foo/bar",
                    "title": "T",
                    "uploader": "A",
                }
            )
            + "\n"
        )

        with patch(
            "nyrx.sources.soundcloud.api.subprocess.run", return_value=mock_proc
        ):
            from nyrx.sources.soundcloud.api import batch_resolve_via_ytdlp

            results = batch_resolve_via_ytdlp("123")

        assert results[0]["permalink"] == ""


class TestTryFetchWaveformSamples:
    def test_returns_samples_list(self) -> None:
        from nyrx.sources.soundcloud.api import _try_fetch_waveform_samples

        with patch(
            "nyrx.sources.soundcloud.api.fetch_waveform",
            return_value={"samples": [0, 1, 2]},
        ):
            assert _try_fetch_waveform_samples("https://wf/x.json") == [0, 1, 2]

    def test_returns_none_when_samples_missing(self) -> None:
        from nyrx.sources.soundcloud.api import _try_fetch_waveform_samples

        with patch(
            "nyrx.sources.soundcloud.api.fetch_waveform", return_value={"width": 1}
        ):
            assert _try_fetch_waveform_samples("https://wf/x.json") is None

    def test_returns_none_on_fetch_failure(self) -> None:
        from nyrx.sources.soundcloud.api import _try_fetch_waveform_samples

        with patch("nyrx.sources.soundcloud.api.fetch_waveform", return_value=None):
            assert _try_fetch_waveform_samples("https://wf/x.json") is None
