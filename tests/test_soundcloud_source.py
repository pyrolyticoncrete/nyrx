# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``sources/soundcloud/source.py``: the ``SoundCloudSource`` adapter.

Covers the URL-fallback template, the soundcloud-only guard in
``fetch_metadata`` (with ``source``/``url`` injection), the ``waveform_url``
strip before mpv, and the ``(ipc, enriched)`` play contract. ``get_commands``
is doc-excluded echo.
"""

from __future__ import annotations

from unittest.mock import patch

from nyrx.sources.soundcloud import SoundCloudSource


def make_source() -> SoundCloudSource:
    return SoundCloudSource()


# ---------------------------------------------------------------------------
# handles_url
# ---------------------------------------------------------------------------


class TestHandlesUrl:
    def test_substring_contract(self) -> None:
        src = make_source()
        assert src.handles_url("https://soundcloud.com/track/1")
        assert not src.handles_url("https://example.com")


# ---------------------------------------------------------------------------
# download_params
# ---------------------------------------------------------------------------


class TestDownloadParams:
    def test_keys_audio_only_and_source(self) -> None:
        src = make_source()
        out = src.download_params({"yt_id": "x", "title": "T", "url": "u"})
        assert out == {
            "yt_id": "x",
            "title": "T",
            "url": "u",
            "source": "soundcloud",
            "audio_only": True,
        }


# ---------------------------------------------------------------------------
# play_params
# ---------------------------------------------------------------------------


class TestPlayParams:
    def test_url_fallback_template(self) -> None:
        src = make_source()
        out = src.play_params({"yt_id": "abc"})
        assert out["url"] == "https://soundcloud.com/tracks/abc"
        assert out["yt_id"] == "abc"
        assert out["title"] == ""

    def test_uses_provided_url_and_forwards_fields(self) -> None:
        src = make_source()
        out = src.play_params(
            {
                "yt_id": "abc",
                "title": "T",
                "url": "http://sc/t",
                "waveform_url": "http://w",
                "channel": "chan",
                "uploader_id": "uid",
                "permalink": "http://p",
            },
            ytdl_format="best",
            start_pos=3.0,
        )
        assert out["url"] == "http://sc/t"
        assert out["audio_only"] is True
        assert out["ytdl_format"] == "best"
        assert out["start_pos"] == 3.0
        assert out["waveform_url"] == "http://w"
        assert out["channel"] == "chan"
        assert out["uploader_id"] == "uid"
        assert out["permalink"] == "http://p"
        assert out["source"] == "soundcloud"


# ---------------------------------------------------------------------------
# fetch_metadata
# ---------------------------------------------------------------------------


class TestFetchMetadata:
    def test_sc_url_injects_source_and_url(self) -> None:
        src = make_source()
        info = {"title": "T"}
        with patch(
            "nyrx.sources.soundcloud.source.fetch_video_metadata",
            return_value=info,
        ) as m_fetch:
            out = src.fetch_metadata("https://soundcloud.com/x")
        assert out is info
        assert out["source"] == "soundcloud"
        assert out["url"] == "https://soundcloud.com/x"
        m_fetch.assert_called_once_with("https://soundcloud.com/x")

    def test_non_sc_url_returns_none_without_fetch(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.soundcloud.source.fetch_video_metadata",
        ) as m_fetch:
            out = src.fetch_metadata("https://youtube.com/watch?v=1")
        assert out is None
        m_fetch.assert_not_called()

    def test_sc_url_with_none_result_returns_none(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.soundcloud.source.fetch_video_metadata",
            return_value=None,
        ) as m_fetch:
            out = src.fetch_metadata("https://soundcloud.com/x")
        assert out is None
        m_fetch.assert_called_once()


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


class TestSearch:
    def test_forwards_query_and_limit(self) -> None:
        src = make_source()
        results = [{"id": 1}]
        with patch(
            "nyrx.sources.soundcloud.source.search_soundcloud",
            return_value=results,
        ) as m_search:
            assert src.search("q", 10) == results
        m_search.assert_called_once_with("q", 10)


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


class TestPlay:
    def test_strips_waveform_url_and_returns_tuple(self) -> None:
        src = make_source()
        data = {"yt_id": "abc", "waveform_url": "http://w"}
        with (
            patch(
                "nyrx.sources.soundcloud.source.play_video_async", return_value="IPC"
            ) as m_play,
            patch(
                "nyrx.sources.soundcloud.source.enrich_sc_track",
                return_value={"resolved": True},
            ) as m_enrich,
        ):
            result = src.play(data)
        assert result == ("IPC", {"resolved": True})
        _, kwargs = m_play.call_args
        assert "waveform_url" not in kwargs
        m_play.assert_called_once_with(
            yt_id="abc",
            title="",
            url="https://soundcloud.com/tracks/abc",
            audio_only=True,
            ytdl_format=None,
            start_pos=None,
            channel="",
            uploader_id="",
            permalink="",
            source="soundcloud",
        )
        m_enrich.assert_called_once_with(data)

    def test_returns_none_ipc_when_playback_fails(self) -> None:
        src = make_source()
        with (
            patch("nyrx.sources.soundcloud.source.play_video_async", return_value=None),
            patch(
                "nyrx.sources.soundcloud.source.enrich_sc_track",
                return_value={"resolved": True},
            ),
        ):
            ipc, resolved = src.play({"yt_id": "abc"})
        assert ipc is None
        assert resolved == {"resolved": True}
