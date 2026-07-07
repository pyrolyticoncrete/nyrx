# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``sources/radio_source.py``: the ``RadioSource`` adapter.

Covers the fallback chains in ``play_params`` (``stationuuid`` /
``url_resolved`` win over ``yt_id``/``url``), the hard-forced ``audio_only``,
and the ``StationIndex`` load-once wiring in ``ensure_index_loaded``. The
constant-return stubs (``handles_url``/``search``/``fetch_metadata``/
``download_params``/``get_commands``) are doc-excluded echo.
"""

from __future__ import annotations

from unittest.mock import patch

from nyrx.sources.radio_index import StationIndex
from nyrx.sources.radio_source import RadioSource


def make_source() -> RadioSource:
    return RadioSource()


# ---------------------------------------------------------------------------
# play_params
# ---------------------------------------------------------------------------


class TestPlayParams:
    def test_resolved_fields_win(self) -> None:
        src = make_source()
        out = src.play_params(
            {
                "stationuuid": "s1",
                "yt_id": "y1",
                "name": "Station One",
                "title": "Fallback Title",
                "url_resolved": "http://resolved",
                "url": "http://direct",
                "channel": "chan",
                "uploader_id": "uid",
                "permalink": "http://p",
            }
        )
        assert out["yt_id"] == "s1"
        assert out["title"] == "Station One"
        assert out["url"] == "http://resolved"
        assert out["audio_only"] is True
        assert out["channel"] == "chan"
        assert out["uploader_id"] == "uid"
        assert out["permalink"] == "http://p"
        assert out["source"] == "radio"

    def test_falls_back_without_stationuuid(self) -> None:
        src = make_source()
        out = src.play_params({"yt_id": "y1", "title": "T", "url": "http://u"})
        assert out["yt_id"] == "y1"
        assert out["title"] == "T"
        assert out["url"] == "http://u"

    def test_missing_title_and_url_default_to_empty(self) -> None:
        src = make_source()
        out = src.play_params({"yt_id": "y1"})
        assert out["title"] == ""
        assert out["url"] == ""

    def test_audio_only_always_true(self) -> None:
        src = make_source()
        out = src.play_params({"yt_id": "y1"}, audio_only=False)
        assert out["audio_only"] is True


# ---------------------------------------------------------------------------
# play
# ---------------------------------------------------------------------------


class TestPlay:
    def test_forces_audio_only_and_returns_ipc(self) -> None:
        src = make_source()
        data = {"yt_id": "y1", "title": "T", "url": "http://u"}
        with patch(
            "nyrx.sources.radio_source.play_video_async", return_value="IPC"
        ) as m_play:
            ipc = src.play(data, audio_only=False)
        assert ipc == "IPC"
        m_play.assert_called_once_with(
            yt_id="y1",
            title="T",
            url="http://u",
            audio_only=True,
            channel="",
            uploader_id="",
            permalink="",
            source="radio",
        )


# ---------------------------------------------------------------------------
# ensure_index_loaded
# ---------------------------------------------------------------------------


class TestEnsureIndexLoaded:
    def test_calls_load_once_and_returns_index(self) -> None:
        src = make_source()
        with patch.object(StationIndex, "load") as m_load:
            idx = src.ensure_index_loaded()
        m_load.assert_called_once()
        assert idx is src._station_index

    def test_returns_the_same_index_each_call(self) -> None:
        src = make_source()
        with patch.object(StationIndex, "load"):
            first = src.ensure_index_loaded()
            second = src.ensure_index_loaded()
        assert first is second
