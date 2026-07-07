# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``sources/youtube.py``: the ``YouTubeSource`` adapter.

Pure wrapper around ``player`` functions (``search_youtube``,
``fetch_video_metadata``, ``play_video_async``) plus dict-building param
helpers. The URL template and the ``source`` default are the two silent-
failure contracts: a wrong template makes mpv open a garbage page, and a
missing ``source`` default misclassifies the item for the tracker + mpv flags.
"""

from __future__ import annotations

from unittest.mock import patch

from nyrx.sources.youtube import YouTubeSource


def make_source() -> YouTubeSource:
    return YouTubeSource()


# ---------------------------------------------------------------------------
# handles_url
# ---------------------------------------------------------------------------


class TestHandlesUrl:
    def test_substring_contract(self) -> None:
        src = make_source()
        assert src.handles_url("https://youtube.com/watch?v=abc")
        assert src.handles_url("https://www.youtube.com/watch?v=abc")
        assert src.handles_url("https://youtu.be/abc")
        assert not src.handles_url("https://example.com/watch")


# ---------------------------------------------------------------------------
# play_params
# ---------------------------------------------------------------------------


class TestPlayParams:
    def test_builds_watch_url(self) -> None:
        src = make_source()
        out = src.play_params({"yt_id": "abc"})
        assert out["url"] == "https://www.youtube.com/watch?v=abc"
        assert out["yt_id"] == "abc"
        assert out["title"] == ""

    def test_title_and_option_passthrough(self) -> None:
        src = make_source()
        out = src.play_params(
            {"yt_id": "abc", "title": "T"},
            audio_only=True,
            ytdl_format="best",
            start_pos=5.0,
        )
        assert out["title"] == "T"
        assert out["audio_only"] is True
        assert out["ytdl_format"] == "best"
        assert out["start_pos"] == 5.0

    def test_default_options(self) -> None:
        src = make_source()
        out = src.play_params({"yt_id": "abc"})
        assert out["audio_only"] is False
        assert out["ytdl_format"] is None
        assert out["start_pos"] is None


# ---------------------------------------------------------------------------
# download_params
# ---------------------------------------------------------------------------


class TestDownloadParams:
    def test_keys_and_source(self) -> None:
        src = make_source()
        out = src.download_params({"yt_id": "x", "title": "T", "url": "u"})
        assert out == {
            "yt_id": "x",
            "title": "T",
            "url": "u",
            "source": "youtube",
        }


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


class TestPlay:
    def test_defaults_source_to_youtube(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.youtube.play_video_async", return_value="IPC"
        ) as m_play:
            ipc = src.play({"yt_id": "abc"})
        assert ipc == "IPC"
        m_play.assert_called_once_with(
            yt_id="abc",
            title="",
            url="https://www.youtube.com/watch?v=abc",
            audio_only=False,
            ytdl_format=None,
            start_pos=None,
            channel="",
            uploader_id="",
            permalink="",
            source="youtube",
        )

    def test_forwards_channel_fields_and_explicit_source(self) -> None:
        src = make_source()
        data = {
            "yt_id": "abc",
            "title": "T",
            "source": "custom",
            "channel": "chan",
            "uploader_id": "uid",
            "permalink": "http://p",
        }
        with patch(
            "nyrx.sources.youtube.play_video_async", return_value="IPC"
        ) as m_play:
            src.play(data, audio_only=True, ytdl_format="best", start_pos=2.0)
        m_play.assert_called_once_with(
            yt_id="abc",
            title="T",
            url="https://www.youtube.com/watch?v=abc",
            audio_only=True,
            ytdl_format="best",
            start_pos=2.0,
            channel="chan",
            uploader_id="uid",
            permalink="http://p",
            source="custom",
        )


# ---------------------------------------------------------------------------
# search / fetch_metadata
# ---------------------------------------------------------------------------


class TestSearch:
    def test_forwards_query_and_limit(self) -> None:
        src = make_source()
        results = [{"id": 1}]
        with patch(
            "nyrx.sources.youtube.search_youtube", return_value=results
        ) as m_search:
            assert src.search("q", 10) == results
        m_search.assert_called_once_with("q", limit=10)

    def test_default_limit_is_20(self) -> None:
        src = make_source()
        with patch("nyrx.sources.youtube.search_youtube", return_value=[]) as m_search:
            src.search("q")
        m_search.assert_called_once_with("q", limit=20)


class TestFetchMetadata:
    def test_returns_result(self) -> None:
        src = make_source()
        info = {"x": 1}
        with patch(
            "nyrx.sources.youtube.fetch_video_metadata", return_value=info
        ) as m_fetch:
            assert src.fetch_metadata("http://u") == info
        m_fetch.assert_called_once_with("http://u")
