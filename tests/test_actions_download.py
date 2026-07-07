# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/download.py`` (Phase 5B.1: download orchestration).

Covers the download state machine, queue management, the raw-stream downloader,
output-path sanitizing, the ffmpeg muxing argv grammar, and the speed/ETA
formatters. Also carries the BUG-5 regression for ``_enqueue_download``
protocol conformance.

No ``MediaApp``/screen is ever booted: every method is driven against a bare
``object.__new__(DownloadActions)`` stub holding only the attributes it
touches, following the Phase-5 stub strategy. Anything not set resolves through
the class, so orchestration chains stay reachable without booting ``MediaApp``.
"""

from __future__ import annotations

import threading
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest
import requests

from nyrx.actions.download import DownloadActions, _stream_download
from nyrx.config import (
    DEFAULT_DOWNLOAD_DIR,
    TIMEOUT_CONFIRM,
    TIMEOUT_INFO,
    YT_QUALITY_PRESETS,
)
from nyrx.queues import DownloadCancelled


def _make_stub() -> DownloadActions:
    """A ``DownloadActions`` instance without ``__init__``.

    Instance attributes are set explicitly on each stub; anything not set (a
    sibling method such as ``self._enqueue_download`` or ``self._clear_dl_state``)
    resolves through the class, so orchestration chains work without booting
    ``MediaApp``.
    """
    return object.__new__(DownloadActions)


def _stub() -> DownloadActions:
    stub = _make_stub()
    stub._download_pending = []
    stub._sync_np_widget = MagicMock()
    stub.notify = MagicMock()
    stub._download_dir = None
    return stub


def _dl_stub() -> DownloadActions:
    """Stub for the download/start paths (``_start_download`` & co.)."""
    stub = _stub()
    stub._online = True
    stub._download_state = None
    stub._pending_dl_data = None
    stub._download_cancel_flag = threading.Event()
    stub._download_running_flag = threading.Event()
    stub._download_running_flag.set()
    stub._current_dl_params = None
    stub._dl_spinner_timer = None
    stub.set_interval = MagicMock()
    stub._update_sidebar_context = MagicMock()
    stub.call_from_thread = MagicMock()
    return stub


def _terminal_stub() -> DownloadActions:
    """Stub for the terminal-state handlers (``_dl_finished`` & co.)."""
    stub = _dl_stub()
    stub._dl_spinner_timer = None
    stub._dl_cancel_watchdog = None
    stub._clear_dl_timer = None
    stub.set_timer = MagicMock()
    stub._check_download_queue = MagicMock()
    return stub


def _queue_stub() -> DownloadActions:
    stub = _stub()
    stub._download_state = None
    stub._pending_dl_data = None
    return stub


class _FakeTimer:
    def __init__(self) -> None:
        self.stopped = False

    def stop(self) -> None:
        self.stopped = True


@pytest.fixture(autouse=True)
def _capture_threads():
    """Never let ``threading.Thread`` start a real background download."""
    with patch("nyrx.actions.download.threading.Thread") as mock_thread:
        yield mock_thread


class TestEnqueueDownloadNotify:
    """BUG-5: ``_enqueue_download`` accepts the protocol's full kwarg set."""

    def test_full_protocol_kwargs_do_not_raise(self) -> None:
        stub = _stub()

        DownloadActions._enqueue_download(
            stub,
            yt_id="abc123",
            title="Test",
            audio_only=True,
            format_str="bestaudio",
            url="https://example.com",
            source="soundcloud",
            quality=None,
            extra={"season": 1, "episode": 2},
            notify=True,
        )

        assert len(stub._download_pending) == 1
        assert stub._download_pending[0]["yt_id"] == "abc123"
        assert stub._download_pending[0]["season"] == 1
        stub._sync_np_widget.assert_called_once()

    def test_notify_true_shows_queued_toast(self) -> None:
        stub = _stub()

        DownloadActions._enqueue_download(stub, yt_id="a", title="Song", notify=True)

        stub.notify.assert_called_once_with("Queued: Song", timeout=TIMEOUT_CONFIRM)

    def test_notify_false_suppresses_toast_but_still_queues(self) -> None:
        stub = _stub()

        DownloadActions._enqueue_download(stub, yt_id="a", title="Song", notify=False)

        stub.notify.assert_not_called()
        assert len(stub._download_pending) == 1
        stub._sync_np_widget.assert_called_once()

    def test_notify_defaults_to_true(self) -> None:
        """The protocol default is notify=True: omitting the kwarg notifies."""
        stub = _stub()

        DownloadActions._enqueue_download(stub, yt_id="a", title="Song")

        stub.notify.assert_called_once_with("Queued: Song", timeout=TIMEOUT_CONFIRM)

    def test_quality_stored_when_present(self) -> None:
        stub = _stub()

        DownloadActions._enqueue_download(
            stub, yt_id="a", title="Song", quality="1080p"
        )

        assert stub._download_pending[0]["quality"] == "1080p"


class TestStreamDownload:
    """``_stream_download``: raw streaming + cancel cleanup."""

    def _resp(self, chunks, content_length="6"):
        resp = MagicMock()
        resp.headers = {"content-length": content_length}
        resp.iter_content.return_value = chunks
        return resp

    def test_writes_all_chunks_and_reports_progress(self, tmp_path) -> None:
        out = tmp_path / "file.mp4"
        cancel = threading.Event()
        resp = self._resp(iter([b"abc", b"", b"def"]))
        progress: list = []

        with (
            patch("requests.get", return_value=resp) as mock_get,
            patch("time.monotonic", side_effect=[0.0, 0.5, 1.0]),
        ):
            _stream_download(
                "http://x/stream", {"Referer": "r"}, str(out), progress.append, cancel
            )

        assert out.read_bytes() == b"abcdef"
        mock_get.assert_called_once_with(
            "http://x/stream", headers={"Referer": "r"}, stream=True, timeout=(30, 120)
        )
        resp.raise_for_status.assert_called_once()
        # empty chunk skipped → only 2 progress calls
        assert len(progress) == 2
        last = progress[-1]
        assert last["status"] == "downloading"
        assert last["downloaded_bytes"] == 6
        assert last["total_bytes"] == 6
        assert last["speed"] == 6.0
        assert last["eta"] == 0

    def test_raise_for_status_propagates(self, tmp_path) -> None:
        resp = MagicMock()
        resp.raise_for_status.side_effect = requests.HTTPError("404")

        with patch("requests.get", return_value=resp):
            with pytest.raises(requests.HTTPError):
                _stream_download(
                    "http://x",
                    {},
                    str(tmp_path / "f"),
                    lambda d: None,
                    threading.Event(),
                )

    def test_cancel_mid_loop_removes_partial_file(self, tmp_path) -> None:
        out = tmp_path / "partial.mp4"
        cancel = threading.Event()

        def chunks():
            yield b"abc"
            cancel.set()
            yield b"def"

        resp = self._resp(chunks())

        with (
            patch("requests.get", return_value=resp),
            patch("os.remove") as mock_remove,
        ):
            with pytest.raises(DownloadCancelled):
                _stream_download("http://x", {}, str(out), lambda d: None, cancel)

        mock_remove.assert_called_once_with(str(out))

    def test_remove_failure_still_reraised(self, tmp_path) -> None:
        cancel = threading.Event()
        cancel.set()
        resp = self._resp(iter([b"a"]))

        with (
            patch("requests.get", return_value=resp),
            patch("os.remove", side_effect=OSError("busy")),
        ):
            with pytest.raises(DownloadCancelled):
                _stream_download(
                    "http://x", {}, str(tmp_path / "f"), lambda d: None, cancel
                )

    def test_zero_content_length_yields_none_total(self, tmp_path) -> None:
        resp = self._resp(iter([b"abc"]), content_length="0")
        progress: list = []

        with (
            patch("requests.get", return_value=resp),
            patch("time.monotonic", side_effect=[0.0, 1.0]),
        ):
            _stream_download(
                "http://x", {}, str(tmp_path / "f"), progress.append, threading.Event()
            )

        assert progress[0]["total_bytes"] is None
        assert progress[0]["eta"] == 0
        assert progress[0]["speed"] == 3.0

    def test_zero_elapsed_yields_zero_speed(self, tmp_path) -> None:
        resp = self._resp(iter([b"abc"]))
        progress: list = []

        with (
            patch("requests.get", return_value=resp),
            patch("time.monotonic", return_value=5.0),
        ):
            _stream_download(
                "http://x", {}, str(tmp_path / "f"), progress.append, threading.Event()
            )

        assert progress[0]["speed"] == 0
        assert progress[0]["eta"] == 0


class TestBuildTvOutputPath:
    """``_build_tv_output_path``: pure sanitize + naming."""

    @pytest.mark.parametrize(
        "kwargs, expected",
        [
            ({"title": "Attack on Titan", "year": 2013}, "Attack on Titan (2013).mkv"),
            ({"title": "Title"}, "Title.mkv"),
            (
                {
                    "title": "X",
                    "series_title": "Series",
                    "season": 1,
                    "episode": 2,
                    "media_type": "tv",
                },
                "Series S01E02.mkv",
            ),
            (
                {
                    "title": "X",
                    "series_title": "Series",
                    "season": "2",
                    "episode": "3",
                    "media_type": "tv",
                },
                "Series S02E03.mkv",
            ),
            (
                {
                    "title": "X",
                    "series_title": "Series",
                    "year": 2013,
                    "media_type": "tv",
                },
                "Series (2013).mkv",
            ),
            (
                {"title": "X", "series_title": "Series", "media_type": "tv"},
                "Series.mkv",
            ),
            ({"title": "Only", "media_type": "tv"}, "Only.mkv"),
            ({"title": "A/B:C*D"}, "A_B_C_D.mkv"),
        ],
    )
    def test_names(self, tmp_path, kwargs, expected) -> None:
        out = DownloadActions._build_tv_output_path(
            None, output_dir=str(tmp_path), **kwargs
        )
        assert out == str(tmp_path / expected)


class TestMuxTvDownload:
    """``_mux_tv_download``: ffmpeg argv grammar."""

    def test_no_subs_no_audio(self) -> None:
        with (
            patch("subprocess.run") as mock_run,
            patch("os.replace") as mock_replace,
            patch("nyrx.config.FFMPEG_BINARY", "/usr/bin/ffmpeg"),
        ):
            DownloadActions._mux_tv_download(None, "primary.mkv", [], [])

        mock_run.assert_called_once_with(
            [
                "/usr/bin/ffmpeg",
                "-i",
                "primary.mkv",
                "-c",
                "copy",
                "-map",
                "0:v",
                "-map",
                "0:a:0",
                "-map",
                "0:s?",
                "-threads",
                "1",
                "-f",
                "matroska",
                "primary.mkv.tmp",
            ],
            check=True,
            capture_output=True,
        )
        mock_replace.assert_called_once_with("primary.mkv.tmp", "primary.mkv")

    def test_one_sub_one_audio(self) -> None:
        with (
            patch("subprocess.run") as mock_run,
            patch("os.replace"),
            patch("nyrx.config.FFMPEG_BINARY", "/usr/bin/ffmpeg"),
        ):
            DownloadActions._mux_tv_download(
                None,
                "primary.mkv",
                [("subs1.vtt", "English")],
                [("audio1.m4a", "eng")],
            )

        mock_run.assert_called_once_with(
            [
                "/usr/bin/ffmpeg",
                "-i",
                "primary.mkv",
                "-i",
                "subs1.vtt",
                "-i",
                "audio1.m4a",
                "-c",
                "copy",
                "-map",
                "0:v",
                "-map",
                "0:a:0",
                "-map",
                "0:s?",
                "-map",
                "1:s",
                "-map",
                "2:a",
                "-metadata:s:a",
                "language=eng",
                "-metadata:s:s:0",
                "title=English",
                "-threads",
                "1",
                "-f",
                "matroska",
                "primary.mkv.tmp",
            ],
            check=True,
            capture_output=True,
        )

    def test_two_subs_two_audio_index_math(self) -> None:
        vtt = [("s1.vtt", "en"), ("s2.vtt", "fr")]
        audio = [("a1.m4a", "eng"), ("a2.m4a", "spa")]

        with (
            patch("subprocess.run") as mock_run,
            patch("os.replace"),
            patch("nyrx.config.FFMPEG_BINARY", "/usr/bin/ffmpeg"),
        ):
            DownloadActions._mux_tv_download(None, "primary.mkv", vtt, audio)

        mock_run.assert_called_once_with(
            [
                "/usr/bin/ffmpeg",
                "-i",
                "primary.mkv",
                "-i",
                "s1.vtt",
                "-i",
                "s2.vtt",
                "-i",
                "a1.m4a",
                "-i",
                "a2.m4a",
                "-c",
                "copy",
                "-map",
                "0:v",
                "-map",
                "0:a:0",
                "-map",
                "0:s?",
                "-map",
                "1:s",
                "-map",
                "2:s",
                "-map",
                "3:a",
                "-metadata:s:a",
                "language=eng",
                "-map",
                "4:a",
                "-metadata:s:a",
                "language=spa",
                "-metadata:s:s:0",
                "title=en",
                "-metadata:s:s:1",
                "title=fr",
                "-threads",
                "1",
                "-f",
                "matroska",
                "primary.mkv.tmp",
            ],
            check=True,
            capture_output=True,
        )

    def test_run_failure_propagates_without_replace(self) -> None:
        with (
            patch("subprocess.run", side_effect=RuntimeError("ffmpeg failed")),
            patch("os.replace") as mock_replace,
            patch("nyrx.config.FFMPEG_BINARY", "/usr/bin/ffmpeg"),
        ):
            with pytest.raises(RuntimeError):
                DownloadActions._mux_tv_download(None, "primary.mkv", [], [])

        mock_replace.assert_not_called()


class TestFmtSpeed:
    @pytest.mark.parametrize(
        "speed, expected",
        [
            (0, "?"),
            (-1, "?"),
            (1_500_000, "1.5 MB/s"),
            (1_000_000, "1.0 MB/s"),
            (2_000, "2.0 KB/s"),
            (1_000, "1.0 KB/s"),
            (500, "500 B/s"),
        ],
    )
    def test_boundaries(self, speed, expected) -> None:
        assert DownloadActions._fmt_speed(speed) == expected


class TestFmtEta:
    @pytest.mark.parametrize(
        "eta, expected",
        [
            (0, "?"),
            (-5, "?"),
            (59, "0:59"),
            (90, "1:30"),
            (3600, "1:00:00"),
            (3725, "1:02:05"),
        ],
    )
    def test_boundaries(self, eta, expected) -> None:
        assert DownloadActions._fmt_eta(eta) == expected


class TestDoDownload:
    """``_do_download``: offline/network-error retry + dedupe."""

    def _stub(self) -> DownloadActions:
        stub = _dl_stub()
        stub._failed_downloads = []
        stub._current_dl_params = {"yt_id": "x"}
        return stub

    def test_cancel_precheck_aborts(self) -> None:
        stub = self._stub()
        stub._download_cancel_flag.set()

        with patch("nyrx.actions.download.download_video") as mock_dl:
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        mock_dl.assert_not_called()
        stub.call_from_thread.assert_called_once()
        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_cancelled
        )

    def test_already_exists_dispatches(self) -> None:
        stub = self._stub()

        with patch(
            "nyrx.actions.download.download_video", return_value=("/media/F.mkv", True)
        ) as mock_dl:
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        call = mock_dl.call_args
        assert call.args == ("a", "T")
        assert call.kwargs["output_dir"] == str(DEFAULT_DOWNLOAD_DIR)
        assert call.kwargs["audio_only"] is False
        assert call.kwargs["format_str"] is None
        assert call.kwargs["url"] is None
        assert call.kwargs["postprocessor_callback"] is None
        assert callable(call.kwargs["progress_callback"])
        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_already_exists
        )
        assert stub.call_from_thread.call_args[0][1] == "F.mkv"
        assert stub._current_dl_params is None

    def test_finished_dispatches(self) -> None:
        stub = self._stub()

        with patch(
            "nyrx.actions.download.download_video", return_value=("/media/F.mkv", False)
        ):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_finished
        )
        assert stub.call_from_thread.call_args[0][1] == "F.mkv"

    def test_download_cancelled_dispatches(self) -> None:
        stub = self._stub()

        with patch(
            "nyrx.actions.download.download_video", side_effect=DownloadCancelled()
        ):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_cancelled
        )
        assert stub._current_dl_params is None

    def test_offline_pause_appends_once(self) -> None:
        stub = self._stub()
        stub._online = False
        stub._download_state = {"status": "downloading"}

        with patch(
            "nyrx.actions.download.download_video", side_effect=RuntimeError("boom")
        ):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        assert stub._failed_downloads == [
            {
                "source": "youtube",
                "yt_id": "a",
                "title": "T",
                "audio_only": False,
                "format_str": None,
                "url": "",
            }
        ]
        assert (
            stub.call_from_thread.call_args[0][0].__func__ is DownloadActions._dl_error
        )
        assert (
            stub.call_from_thread.call_args[0][1]
            == "Download paused (offline), will retry: T..."
        )

    @pytest.mark.parametrize(
        "msg",
        [
            "timeout",
            "connection",
            "reset",
            "eof",
            "name or service not known",
            "network is unreachable",
            "connection refused",
        ],
    )
    def test_network_errors_pause(self, msg) -> None:
        stub = self._stub()
        stub._download_state = None

        with patch(
            "nyrx.actions.download.download_video", side_effect=RuntimeError(msg)
        ):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        assert len(stub._failed_downloads) == 1
        assert (
            stub.call_from_thread.call_args[0][0].__func__ is DownloadActions._dl_error
        )

    def test_other_error_reports_message(self) -> None:
        stub = self._stub()

        with patch(
            "nyrx.actions.download.download_video",
            side_effect=RuntimeError("disk full"),
        ):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        assert stub._failed_downloads == []
        assert (
            stub.call_from_thread.call_args[0][0].__func__ is DownloadActions._dl_error
        )
        assert stub.call_from_thread.call_args[0][1] == "disk full"

    def test_online_with_downloading_state_silently_returns(self) -> None:
        stub = self._stub()
        stub._download_state = {"status": "downloading"}

        with patch(
            "nyrx.actions.download.download_video", side_effect=RuntimeError("x")
        ):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        stub.call_from_thread.assert_not_called()
        assert stub._failed_downloads == []

    def test_cancel_during_exception_aborts(self) -> None:
        stub = self._stub()
        stub._download_state = {"status": "downloading"}

        def boom(*args, **kwargs):
            stub._download_cancel_flag.set()
            raise RuntimeError("x")

        with patch("nyrx.actions.download.download_video", side_effect=boom):
            DownloadActions._do_download(stub, "a", "T", lambda d: None)

        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_cancelled
        )
        assert stub._failed_downloads == []


class TestCheckDownloadQueue:
    """``_check_download_queue``: pop-order + tv_movies payload."""

    def test_busy_downloading_noop(self) -> None:
        stub = _queue_stub()
        stub._download_state = {"status": "downloading"}
        stub._download_pending = [{"source": "youtube", "yt_id": "a", "title": "T"}]

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download") as mock_tv,
        ):
            DownloadActions._check_download_queue(stub)

        mock_start.assert_not_called()
        mock_tv.assert_not_called()
        assert len(stub._download_pending) == 1

    def test_busy_processing_noop(self) -> None:
        stub = _queue_stub()
        stub._download_state = {"status": "processing"}
        stub._download_pending = [{"source": "youtube", "yt_id": "a", "title": "T"}]

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download"),
        ):
            DownloadActions._check_download_queue(stub)

        mock_start.assert_not_called()
        assert len(stub._download_pending) == 1

    def test_empty_queue_returns(self) -> None:
        stub = _queue_stub()

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download") as mock_tv,
        ):
            DownloadActions._check_download_queue(stub)

        mock_start.assert_not_called()
        mock_tv.assert_not_called()

    def test_pops_in_fifo_order(self) -> None:
        stub = _queue_stub()
        stub._download_pending = [
            {
                "source": "youtube",
                "yt_id": "a",
                "title": "First",
                "audio_only": False,
                "format_str": None,
                "url": "u1",
            },
            {
                "source": "youtube",
                "yt_id": "b",
                "title": "Second",
                "audio_only": True,
                "format_str": "best",
                "url": "u2",
            },
        ]

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download") as mock_tv,
        ):
            DownloadActions._check_download_queue(stub)
            DownloadActions._check_download_queue(stub)

        assert mock_start.call_count == 2
        assert mock_start.call_args_list[0].kwargs == {
            "yt_id": "a",
            "title": "First",
            "audio_only": False,
            "format_str": None,
            "url": "u1",
        }
        assert mock_start.call_args_list[1].kwargs == {
            "yt_id": "b",
            "title": "Second",
            "audio_only": True,
            "format_str": "best",
            "url": "u2",
        }
        mock_tv.assert_not_called()
        assert stub._download_pending == []

    def test_tv_movies_default_quality(self) -> None:
        stub = _queue_stub()
        stub._download_pending = [
            {
                "source": "tv_movies",
                "yt_id": "t1",
                "title": "Show S01E02",
                "tmdb_id": 9,
                "media_type": "tv",
                "season": 1,
                "episode": 2,
                "series_title": "Show",
                "year": 2020,
                "_queued_server_mode": "auto",
            }
        ]

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download") as mock_tv,
        ):
            DownloadActions._check_download_queue(stub)

        mock_start.assert_not_called()
        mock_tv.assert_called_once_with(quality="1080p")
        assert stub._pending_dl_data == {
            "yt_id": "t1",
            "title": "Show S01E02",
            "source": "tv_movies",
            "tmdb_id": 9,
            "media_type": "tv",
            "season": 1,
            "episode": 2,
            "series_title": "Show",
            "year": 2020,
            "_queued_server_mode": "auto",
        }

    def test_tv_movies_respects_quality(self) -> None:
        stub = _queue_stub()
        stub._download_pending = [
            {"source": "tv_movies", "yt_id": "t1", "title": "T", "quality": "720p"}
        ]

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download") as mock_tv,
        ):
            DownloadActions._check_download_queue(stub)

        mock_start.assert_not_called()
        mock_tv.assert_called_once_with(quality="720p")

    def test_tv_movies_sparse_payload(self) -> None:
        stub = _queue_stub()
        stub._download_pending = [{"source": "tv_movies", "yt_id": "t1", "title": "T"}]

        with (
            patch.object(DownloadActions, "_start_download") as mock_start,
            patch.object(DownloadActions, "_start_tv_movies_download") as mock_tv,
        ):
            DownloadActions._check_download_queue(stub)

        mock_start.assert_not_called()
        mock_tv.assert_called_once_with(quality="1080p")
        assert stub._pending_dl_data == {
            "yt_id": "t1",
            "title": "T",
            "source": "tv_movies",
            "tmdb_id": None,
            "media_type": None,
            "season": None,
            "episode": None,
            "series_title": None,
            "year": None,
            "_queued_server_mode": None,
        }


class TestEnqueueEpisodeRange:
    """``_enqueue_episode_range``: iterate_episode_range wiring."""

    @staticmethod
    def _result(**over) -> dict:
        result = {
            "start_season": 1,
            "start_episode": 1,
            "end_season": 1,
            "end_episode": 3,
            "quality": "1080p",
            "tmdb_id": 12345,
            "series_title": "Show",
            "seasons": [{"season_number": 1, "episode_count": 10}],
        }
        result.update(over)
        return result

    @staticmethod
    def _exists_only(name: str):
        def side_effect(path):
            return Path(path).name == name

        return side_effect

    def test_on_episode_range_result_none(self) -> None:
        stub = _stub()

        with patch.object(DownloadActions, "_enqueue_episode_range") as mock_enq:
            DownloadActions._on_episode_range_result(stub, None)

        mock_enq.assert_not_called()

    def test_on_episode_range_result_forwards(self) -> None:
        stub = _stub()
        result = self._result()

        with patch.object(DownloadActions, "_enqueue_episode_range") as mock_enq:
            DownloadActions._on_episode_range_result(stub, result)

        mock_enq.assert_called_once_with(result)

    def test_enqueues_range_in_order(self, tmp_path) -> None:
        stub = _stub()
        stub._download_dir = str(tmp_path)

        with (
            patch("os.path.exists", return_value=False),
            patch.object(DownloadActions, "_check_download_queue") as mock_check,
        ):
            DownloadActions._enqueue_episode_range(stub, self._result())

        assert [i["title"] for i in stub._download_pending] == [
            "Show S01E01",
            "Show S01E02",
            "Show S01E03",
        ]
        item = stub._download_pending[1]
        assert item["yt_id"] == "tmdb_12345"
        assert item["source"] == "tv_movies"
        assert item["quality"] == "1080p"
        assert item["season"] == 1
        assert item["episode"] == 2
        assert item["media_type"] == "tv"
        assert item["series_title"] == "Show"
        assert item["tmdb_id"] == 12345
        stub.notify.assert_called_once_with(
            "Queued 3 episodes", timeout=TIMEOUT_CONFIRM
        )
        mock_check.assert_called_once()

    def test_skips_existing(self, tmp_path) -> None:
        stub = _stub()
        stub._download_dir = str(tmp_path)

        with (
            patch("os.path.exists", side_effect=self._exists_only("Show S01E02.mkv")),
            patch.object(DownloadActions, "_check_download_queue") as mock_check,
        ):
            DownloadActions._enqueue_episode_range(stub, self._result())

        assert [i["episode"] for i in stub._download_pending] == [1, 3]
        stub.notify.assert_called_once_with(
            "Queued 2 episodes  (1 already exist, skipped)", timeout=TIMEOUT_CONFIRM
        )
        mock_check.assert_called_once()

    def test_all_exist_notifies(self, tmp_path) -> None:
        stub = _stub()
        stub._download_dir = str(tmp_path)

        with (
            patch("os.path.exists", return_value=True),
            patch.object(DownloadActions, "_check_download_queue") as mock_check,
        ):
            DownloadActions._enqueue_episode_range(stub, self._result())

        assert stub._download_pending == []
        stub.notify.assert_called_once_with(
            "All 3 episodes already exist", timeout=TIMEOUT_INFO
        )
        mock_check.assert_called_once()

    def test_single_episode_singular(self, tmp_path) -> None:
        stub = _stub()
        stub._download_dir = str(tmp_path)
        result = self._result(
            start_season=2,
            start_episode=5,
            end_season=2,
            end_episode=5,
            seasons=[{"season_number": 2, "episode_count": 10}],
        )

        with (
            patch("os.path.exists", return_value=False),
            patch.object(DownloadActions, "_check_download_queue") as mock_check,
        ):
            DownloadActions._enqueue_episode_range(stub, result)

        assert [i["title"] for i in stub._download_pending] == ["Show S02E05"]
        stub.notify.assert_called_once_with("Queued 1 episode", timeout=TIMEOUT_CONFIRM)
        mock_check.assert_called_once()

    def test_cross_season_range(self, tmp_path) -> None:
        stub = _stub()
        stub._download_dir = str(tmp_path)
        result = self._result(
            start_season=1,
            start_episode=9,
            end_season=2,
            end_episode=1,
            seasons=[
                {"season_number": 1, "episode_count": 10},
                {"season_number": 2, "episode_count": 10},
            ],
        )

        with (
            patch("os.path.exists", return_value=False),
            patch.object(DownloadActions, "_check_download_queue") as mock_check,
        ):
            DownloadActions._enqueue_episode_range(stub, result)

        assert [(i["season"], i["episode"]) for i in stub._download_pending] == [
            (1, 9),
            (1, 10),
            (2, 1),
        ]
        stub.notify.assert_called_once_with(
            "Queued 3 episodes", timeout=TIMEOUT_CONFIRM
        )
        mock_check.assert_called_once()


class TestStartDownload:
    """``_start_download``: gating + thread spawn (capture, don't start)."""

    @staticmethod
    def _spawn(_capture_threads):
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "yt123", "title": "Song", "url": "https://x"}
        DownloadActions._start_download(
            stub, audio_only=False, quality="1080p", _user_initiated=True
        )
        args = _capture_threads.call_args.kwargs["args"]
        return stub, args[2], args[6]

    def test_no_data_returns(self, _capture_threads) -> None:
        stub = _dl_stub()

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_download(stub)

        mock_enqueue.assert_not_called()
        _capture_threads.assert_not_called()

    def test_not_online_enqueues(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._online = False
        stub._pending_dl_data = {"yt_id": "a", "title": "T", "url": "u"}

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_download(
                stub, audio_only=True, format_str="best", _user_initiated=True
            )

        mock_enqueue.assert_called_once_with(
            yt_id="a", title="T", audio_only=True, format_str="best", url="u"
        )
        _capture_threads.assert_not_called()

    def test_user_initiated_queues_when_state_exists(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "a", "title": "T"}
        stub._download_state = {"status": "complete"}

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_download(stub, _user_initiated=True)

        mock_enqueue.assert_called_once_with(
            yt_id="a", title="T", audio_only=False, format_str=None, url=""
        )
        _capture_threads.assert_not_called()

    def test_auto_queues_when_downloading(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "a", "title": "T"}
        stub._download_state = {"status": "downloading"}

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_download(stub)

        mock_enqueue.assert_called_once()
        _capture_threads.assert_not_called()

    def test_auto_proceeds_when_state_terminal(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "a", "title": "T", "url": "u"}
        stub._download_state = {"status": "complete"}

        DownloadActions._start_download(stub)

        assert (
            _capture_threads.call_args.kwargs["target"].__func__
            is DownloadActions._do_download
        )
        _capture_threads.return_value.start.assert_called_once()

    def test_kwargs_data_used_when_no_pending(self, _capture_threads) -> None:
        stub = _dl_stub()

        DownloadActions._start_download(
            stub, yt_id="a", title="T", url="u", _user_initiated=True
        )

        assert stub._current_dl_params["yt_id"] == "a"
        assert stub._current_dl_params["title"] == "T"
        _capture_threads.return_value.start.assert_called_once()

    def test_spawns_daemon_thread_with_quality_format(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "yt123", "title": "Song", "url": "https://x"}
        fmt_1080p = YT_QUALITY_PRESETS[2][2]

        DownloadActions._start_download(stub, quality="1080p", _user_initiated=True)

        call = _capture_threads.call_args
        assert call.kwargs["daemon"] is True
        assert call.kwargs["target"].__func__ is DownloadActions._do_download
        args = call.kwargs["args"]
        assert args[0] == "yt123"
        assert args[1] == "Song"
        assert callable(args[2])
        assert args[3] is False
        assert args[4] == fmt_1080p
        assert args[5] == "https://x"
        assert callable(args[6])
        _capture_threads.return_value.start.assert_called_once()
        assert stub._current_dl_params == {
            "yt_id": "yt123",
            "title": "Song",
            "audio_only": False,
            "format_str": fmt_1080p,
        }
        assert stub._download_state == {
            "status": "downloading",
            "title": "Song",
            "pct": 0,
            "speed": 0,
            "eta": 0,
        }
        call = stub.set_interval.call_args
        assert call[0][0] == 0.04
        assert call[0][1].__func__ is DownloadActions._tick_dl_spinner
        assert stub._dl_spinner_timer is stub.set_interval.return_value
        assert not stub._download_cancel_flag.is_set()
        stub._update_sidebar_context.assert_called_once()
        stub._sync_np_widget.assert_called_once()
        assert stub._pending_dl_data is None

    def test_audio_only_skips_quality_lookup(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "a", "title": "T"}

        DownloadActions._start_download(stub, audio_only=True, quality="1080p")

        assert stub._current_dl_params["audio_only"] is True
        assert stub._current_dl_params["format_str"] is None

    def test_unknown_quality_keeps_none_format(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = {"yt_id": "a", "title": "T"}

        DownloadActions._start_download(stub, quality="4k")

        assert stub._current_dl_params["format_str"] is None

    def test_on_progress_updates_then_throttles(self, _capture_threads) -> None:
        stub, on_progress, _ = self._spawn(_capture_threads)

        on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 1000,
                "eta": 5,
            }
        )
        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._update_dl_progress
        )
        assert stub.call_from_thread.call_args[0][1:] == (50.0, 1000, 5, "yt123")

        stub.call_from_thread.reset_mock()
        on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 60,
                "total_bytes": 100,
                "speed": 1000,
                "eta": 5,
            }
        )
        stub.call_from_thread.assert_not_called()

    def test_on_progress_zero_total_proceeds(self, _capture_threads) -> None:
        stub, on_progress, _ = self._spawn(_capture_threads)

        on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 10,
                "total_bytes": 0,
                "speed": 0,
                "eta": 0,
            }
        )

        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._update_dl_progress
        )
        assert stub.call_from_thread.call_args[0][1] == 0.0

    def test_on_progress_finished_schedules_processing(self, _capture_threads) -> None:
        stub, on_progress, _ = self._spawn(_capture_threads)

        on_progress({"status": "finished"})

        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_processing
        )
        assert stub.call_from_thread.call_args[0][1] == "Song"

    def test_on_progress_cancel_raises(self, _capture_threads) -> None:
        stub, on_progress, _ = self._spawn(_capture_threads)
        stub._download_cancel_flag.set()

        with pytest.raises(DownloadCancelled):
            on_progress(
                {
                    "status": "downloading",
                    "downloaded_bytes": 10,
                    "total_bytes": 100,
                    "speed": 0,
                    "eta": 0,
                }
            )

    def test_on_postprocessor_stages(self, _capture_threads) -> None:
        stub, _, on_postprocessor = self._spawn(_capture_threads)

        on_postprocessor({"status": "started", "postprocessor": "Muxer"})
        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_processing
        )
        assert stub.call_from_thread.call_args[0][1] == "Song"
        assert stub.call_from_thread.call_args[0][2] == "muxing"

        stub.call_from_thread.reset_mock()
        on_postprocessor({"status": "started", "postprocessor": "Merging"})
        assert stub.call_from_thread.call_args[0][2] == "processing"

        stub.call_from_thread.reset_mock()
        on_postprocessor({"status": "other"})
        stub.call_from_thread.assert_not_called()

    def test_on_postprocessor_cancel_returns(self, _capture_threads) -> None:
        stub, _, on_postprocessor = self._spawn(_capture_threads)
        stub._download_cancel_flag.set()

        on_postprocessor({"status": "started", "postprocessor": "Muxer"})

        stub.call_from_thread.assert_not_called()


class TestStartTvMoviesDownload:
    """``_start_tv_movies_download``: re-enqueue + spawn."""

    @staticmethod
    def _data() -> dict:
        return {
            "yt_id": "tvid1",
            "title": "Show S01E02",
            "source": "tv_movies",
            "tmdb_id": 99,
            "media_type": "tv",
            "season": 1,
            "episode": 2,
            "series_title": "Show",
            "year": 2020,
            "_queued_server_mode": "auto",
        }

    @staticmethod
    def _spawn(_capture_threads):
        stub = _dl_stub()
        stub._pending_dl_data = TestStartTvMoviesDownload._data()
        DownloadActions._start_tv_movies_download(stub, "1080p")
        args = _capture_threads.call_args.kwargs["args"]
        return stub, args[2], args[3]

    def test_no_data_returns(self, _capture_threads) -> None:
        stub = _dl_stub()

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_tv_movies_download(stub, "1080p")

        mock_enqueue.assert_not_called()
        _capture_threads.assert_not_called()

    def test_offline_reenqueues(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._online = False
        stub._pending_dl_data = self._data()

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_tv_movies_download(stub, "1080p")

        mock_enqueue.assert_called_once_with(
            source="tv_movies",
            quality="1080p",
            yt_id="tvid1",
            title="Show S01E02",
            extra=self._data(),
        )
        _capture_threads.assert_not_called()

    def test_busy_reenqueues(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._download_state = {"status": "processing"}
        stub._pending_dl_data = self._data()

        with patch.object(DownloadActions, "_enqueue_download") as mock_enqueue:
            DownloadActions._start_tv_movies_download(stub, "720p")

        mock_enqueue.assert_called_once_with(
            source="tv_movies",
            quality="720p",
            yt_id="tvid1",
            title="Show S01E02",
            extra=self._data(),
        )
        _capture_threads.assert_not_called()

    def test_starts_thread_and_sets_params(self, _capture_threads) -> None:
        stub = _dl_stub()
        stub._pending_dl_data = self._data()

        DownloadActions._start_tv_movies_download(stub, "1080p")

        call = _capture_threads.call_args
        assert call.kwargs["daemon"] is True
        assert call.kwargs["target"].__func__ is DownloadActions._do_tv_movies_download
        args = call.kwargs["args"]
        assert args[0] == self._data()
        assert args[1] == "1080p"
        assert callable(args[2])
        assert callable(args[3])
        _capture_threads.return_value.start.assert_called_once()
        expected = dict(self._data())
        expected["quality"] = "1080p"
        assert stub._current_dl_params == expected
        assert stub._download_state["status"] == "downloading"
        stub.set_interval.assert_called_once()
        assert not stub._download_cancel_flag.is_set()

    def test_kwargs_data_overrides(self, _capture_threads) -> None:
        stub = _dl_stub()

        DownloadActions._start_tv_movies_download(stub, "480p", yt_id="x", title="Y")

        assert stub._current_dl_params["yt_id"] == "x"
        assert stub._current_dl_params["quality"] == "480p"
        _capture_threads.return_value.start.assert_called_once()

    def test_on_progress_updates_then_throttles(self, _capture_threads) -> None:
        stub, on_progress, _ = self._spawn(_capture_threads)

        on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 50,
                "total_bytes": 100,
                "speed": 1000,
                "eta": 5,
            }
        )
        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._update_dl_progress
        )
        assert stub.call_from_thread.call_args[0][1:] == (50.0, 1000, 5, "tvid1")

        stub.call_from_thread.reset_mock()
        on_progress(
            {
                "status": "downloading",
                "downloaded_bytes": 60,
                "total_bytes": 100,
                "speed": 1000,
                "eta": 5,
            }
        )
        stub.call_from_thread.assert_not_called()

    def test_on_progress_cancel_raises(self, _capture_threads) -> None:
        stub, on_progress, _ = self._spawn(_capture_threads)
        stub._download_cancel_flag.set()

        with pytest.raises(DownloadCancelled):
            on_progress(
                {
                    "status": "downloading",
                    "downloaded_bytes": 10,
                    "total_bytes": 100,
                    "speed": 0,
                    "eta": 0,
                }
            )

    def test_on_postprocessor_stages(self, _capture_threads) -> None:
        stub, _, on_postprocessor = self._spawn(_capture_threads)

        on_postprocessor({"status": "started", "postprocessor": "Muxing"})
        assert (
            stub.call_from_thread.call_args[0][0].__func__
            is DownloadActions._dl_processing
        )
        assert stub.call_from_thread.call_args[0][1] == "Show S01E02"
        assert stub.call_from_thread.call_args[0][2] == "muxing"

        stub.call_from_thread.reset_mock()
        on_postprocessor({"status": "other"})
        stub.call_from_thread.assert_not_called()

    def test_on_postprocessor_cancel_returns(self, _capture_threads) -> None:
        stub, _, on_postprocessor = self._spawn(_capture_threads)
        stub._download_cancel_flag.set()

        on_postprocessor({"status": "started", "postprocessor": "Muxer"})

        stub.call_from_thread.assert_not_called()


class TestDlStateHandlers:
    """Terminal-state handlers: ``_dl_processing`` .. ``_force_cancel_download``."""

    def test_processing_updates_state(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "downloading", "pct": 50}
        stub._w_download = MagicMock()

        DownloadActions._dl_processing(stub, "Title", stage="muxing")

        assert stub._download_state == {
            "status": "processing",
            "pct": 100,
            "speed": 0,
            "eta": 0,
            "stage": "muxing",
        }
        stub._w_download.update_progress.assert_called_once_with(stub._download_state)
        stub._sync_np_widget.assert_called_once()

    def test_processing_skips_when_not_downloading(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "complete"}

        DownloadActions._dl_processing(stub, "Title")

        assert stub._download_state == {"status": "complete"}
        stub._sync_np_widget.assert_not_called()

    def test_processing_widget_error_swallowed(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "downloading"}
        stub._w_download = MagicMock()
        stub._w_download.update_progress.side_effect = RuntimeError("boom")

        DownloadActions._dl_processing(stub, "Title")

        stub._sync_np_widget.assert_called_once()

    def test_finished_sets_complete_and_advances(self) -> None:
        stub = _terminal_stub()
        spinner = _FakeTimer()
        watchdog = _FakeTimer()
        clear_timer = _FakeTimer()
        stub._dl_spinner_timer = spinner
        stub._dl_cancel_watchdog = watchdog
        stub._clear_dl_timer = clear_timer

        DownloadActions._dl_finished(stub, "/media/Episode 01.mkv")

        assert stub._download_state == {
            "status": "complete",
            "filename": "Episode 01.mkv",
        }
        assert spinner.stopped
        assert watchdog.stopped
        assert clear_timer.stopped
        assert stub._dl_spinner_timer is None
        call = stub.set_timer.call_args
        assert call[0][0] == 5
        assert call[0][1].__func__ is DownloadActions._clear_dl_state
        stub._update_sidebar_context.assert_called_once()
        stub._check_download_queue.assert_called_once()

    def test_finished_without_timers(self) -> None:
        stub = _terminal_stub()

        DownloadActions._dl_finished(stub, "File.mkv")

        assert stub._download_state == {"status": "complete", "filename": "File.mkv"}
        stub.set_timer.assert_called_once()

    def test_already_exists_sets_state(self) -> None:
        stub = _terminal_stub()
        spinner = _FakeTimer()
        watchdog = _FakeTimer()
        clear_timer = _FakeTimer()
        stub._dl_spinner_timer = spinner
        stub._dl_cancel_watchdog = watchdog
        stub._clear_dl_timer = clear_timer

        DownloadActions._dl_already_exists(stub, "File.mkv")

        assert stub._download_state == {
            "status": "already_exists",
            "filename": "File.mkv",
        }
        assert spinner.stopped
        assert watchdog.stopped
        assert clear_timer.stopped
        assert stub._dl_spinner_timer is None
        assert stub._dl_cancel_watchdog is None
        call = stub.set_timer.call_args
        assert call[0][0] == 3
        assert call[0][1].__func__ is DownloadActions._clear_dl_state
        stub._check_download_queue.assert_called_once()

    def test_error_sets_state(self) -> None:
        stub = _terminal_stub()
        spinner = _FakeTimer()
        watchdog = _FakeTimer()
        clear_timer = _FakeTimer()
        stub._dl_spinner_timer = spinner
        stub._dl_cancel_watchdog = watchdog
        stub._clear_dl_timer = clear_timer

        DownloadActions._dl_error(stub, "boom")

        assert stub._download_state == {"status": "error", "msg": "boom"}
        assert spinner.stopped
        assert watchdog.stopped
        assert clear_timer.stopped
        assert stub._dl_spinner_timer is None
        assert stub._dl_cancel_watchdog is None
        call = stub.set_timer.call_args
        assert call[0][0] == 7
        assert call[0][1].__func__ is DownloadActions._clear_dl_state
        stub._check_download_queue.assert_called_once()

    def test_cancelled_sets_state_and_cleans_parts(self) -> None:
        stub = _terminal_stub()
        spinner = _FakeTimer()
        watchdog = _FakeTimer()
        clear_timer = _FakeTimer()
        stub._dl_spinner_timer = spinner
        stub._dl_cancel_watchdog = watchdog
        stub._clear_dl_timer = clear_timer

        with patch("nyrx.actions.download.clean_part_files") as mock_clean:
            DownloadActions._dl_cancelled(stub)

        assert stub._download_state == {
            "status": "cancelled",
            "msg": "Download cancelled",
        }
        assert spinner.stopped
        assert watchdog.stopped
        assert clear_timer.stopped
        mock_clean.assert_called_once_with(str(DEFAULT_DOWNLOAD_DIR))
        stub.set_timer.assert_called_once()
        stub._check_download_queue.assert_called_once()

    def test_cancelled_when_already_cancelled_is_noop(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "cancelled", "msg": "Download cancelled"}

        with patch("nyrx.actions.download.clean_part_files") as mock_clean:
            DownloadActions._dl_cancelled(stub)

        mock_clean.assert_not_called()
        stub.set_timer.assert_not_called()
        stub._check_download_queue.assert_not_called()

    def test_clear_state_skips_while_downloading(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "downloading", "pct": 50}

        DownloadActions._clear_dl_state(stub)

        assert stub._download_state == {"status": "downloading", "pct": 50}
        stub._update_sidebar_context.assert_not_called()
        stub._check_download_queue.assert_not_called()

    def test_clear_state_clears_terminal(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "complete", "filename": "F.mkv"}

        DownloadActions._clear_dl_state(stub)

        assert stub._download_state is None
        stub._update_sidebar_context.assert_called_once()
        stub._check_download_queue.assert_called_once()

    def test_clear_state_stops_spinner_without_state(self) -> None:
        stub = _terminal_stub()
        stub._dl_spinner_timer = _FakeTimer()
        stub._download_state = None

        DownloadActions._clear_dl_state(stub)

        assert stub._dl_spinner_timer is None
        stub._update_sidebar_context.assert_not_called()
        stub._check_download_queue.assert_not_called()

    def test_update_progress_applies(self) -> None:
        stub = _dl_stub()
        stub._current_dl_params = {"yt_id": "yt123"}
        stub._download_state = {"status": "downloading", "pct": 10}
        stub._w_download = MagicMock()

        DownloadActions._update_dl_progress(stub, 50.0, 1000, 5, "yt123")

        assert stub._download_state["pct"] == 50.0
        assert stub._download_state["speed"] == 1000
        assert stub._download_state["eta"] == 5
        stub._w_download.update_progress.assert_called_once_with(stub._download_state)
        stub._sync_np_widget.assert_called_once()

    def test_update_progress_no_params_returns(self) -> None:
        stub = _dl_stub()
        stub._current_dl_params = None

        DownloadActions._update_dl_progress(stub, 1, 1, 1, "x")

        stub._sync_np_widget.assert_not_called()

    def test_update_progress_yt_id_mismatch_returns(self) -> None:
        stub = _dl_stub()
        stub._current_dl_params = {"yt_id": "other"}
        stub._download_state = {"status": "downloading"}

        DownloadActions._update_dl_progress(stub, 1, 1, 1, "yt123")

        stub._sync_np_widget.assert_not_called()

    def test_update_progress_widget_none_no_crash(self) -> None:
        stub = _dl_stub()
        stub._current_dl_params = {"yt_id": "yt123"}
        stub._download_state = {"status": "downloading"}
        stub._w_download = None

        DownloadActions._update_dl_progress(stub, 1, 1, 1, "yt123")

        stub._sync_np_widget.assert_called_once()

    def test_update_progress_widget_error_swallowed(self) -> None:
        stub = _dl_stub()
        stub._current_dl_params = {"yt_id": "yt123"}
        stub._download_state = {"status": "downloading"}
        stub._w_download = MagicMock()
        stub._w_download.update_progress.side_effect = RuntimeError("boom")

        DownloadActions._update_dl_progress(stub, 1, 1, 1, "yt123")

        stub._sync_np_widget.assert_called_once()

    def test_update_progress_skips_widget_when_not_downloading(self) -> None:
        stub = _dl_stub()
        stub._current_dl_params = {"yt_id": "yt123"}
        stub._download_state = {"status": "complete"}
        stub._w_download = MagicMock()

        DownloadActions._update_dl_progress(stub, 1, 1, 1, "yt123")

        stub._w_download.update_progress.assert_not_called()
        stub._sync_np_widget.assert_called_once()

    def test_tick_cancel_stops_spinner_and_arms_watchdog(self) -> None:
        stub = _dl_stub()
        stub._download_cancel_flag.set()
        stub._download_state = {"status": "downloading"}
        spinner = _FakeTimer()
        stub._dl_spinner_timer = spinner
        stub._dl_cancel_watchdog = None
        stub.set_timer = MagicMock()
        stub._w_download = MagicMock()

        DownloadActions._tick_dl_spinner(stub)

        assert spinner.stopped
        assert stub._dl_spinner_timer is None
        assert stub._download_state["stage"] == "cancelling"
        stub._w_download.update_progress.assert_called_once_with(stub._download_state)
        call = stub.set_timer.call_args
        assert call[0][0] == 5
        assert call[0][1].__func__ is DownloadActions._force_cancel_download
        assert stub._dl_cancel_watchdog is stub.set_timer.return_value

    def test_tick_cancel_with_watchdog_does_not_rearm(self) -> None:
        stub = _dl_stub()
        stub._download_cancel_flag.set()
        stub._download_state = {"status": "downloading"}
        stub._dl_spinner_timer = _FakeTimer()
        stub._dl_cancel_watchdog = MagicMock()
        stub.set_timer = MagicMock()
        stub._w_download = MagicMock()

        DownloadActions._tick_dl_spinner(stub)

        stub.set_timer.assert_not_called()
        assert stub._dl_cancel_watchdog is not None

    def test_tick_cancel_without_downloading_state(self) -> None:
        stub = _dl_stub()
        stub._download_cancel_flag.set()
        stub._download_state = {"status": "complete"}
        stub._dl_spinner_timer = _FakeTimer()
        stub._dl_cancel_watchdog = None
        stub.set_timer = MagicMock()

        DownloadActions._tick_dl_spinner(stub)

        assert stub._dl_spinner_timer is None
        stub.set_timer.assert_not_called()

    def test_tick_no_widget_returns(self) -> None:
        stub = _dl_stub()
        stub._download_state = {"status": "downloading"}
        stub._w_download = None

        DownloadActions._tick_dl_spinner(stub)

    def test_tick_normal_updates_spinner(self) -> None:
        stub = _dl_stub()
        stub._download_state = {"status": "downloading"}
        stub._w_download = MagicMock()
        stub._w_download.display = True

        DownloadActions._tick_dl_spinner(stub)

        stub._w_download.update_spinner_frame.assert_called_once()
        stub._w_download.update_progress.assert_called_once_with(stub._download_state)

    def test_tick_paused_for_offline_skips_frame(self) -> None:
        stub = _dl_stub()
        stub._download_state = {"status": "downloading"}
        stub._w_download = MagicMock()
        stub._w_download.display = True
        stub._download_paused_for_offline = True

        DownloadActions._tick_dl_spinner(stub)

        stub._w_download.update_spinner_frame.assert_not_called()
        stub._w_download.update_progress.assert_called_once()

    def test_tick_display_false_noop(self) -> None:
        stub = _dl_stub()
        stub._download_state = {"status": "downloading"}
        stub._w_download = MagicMock()
        stub._w_download.display = False

        DownloadActions._tick_dl_spinner(stub)

        stub._w_download.update_spinner_frame.assert_not_called()
        stub._w_download.update_progress.assert_not_called()

    def test_tick_exception_swallowed(self) -> None:
        stub = _dl_stub()
        stub._download_state = {"status": "downloading"}
        stub._w_download = MagicMock()
        stub._w_download.display = True
        stub._w_download.update_progress.side_effect = RuntimeError("boom")

        DownloadActions._tick_dl_spinner(stub)

    def test_cancel_sets_flag(self) -> None:
        stub = _dl_stub()
        stub._download_cancel_flag = threading.Event()

        DownloadActions._cancel_download(stub)

        assert stub._download_cancel_flag.is_set()

    def test_cancel_without_flag_noop(self) -> None:
        stub = _dl_stub()
        stub._download_cancel_flag = None

        DownloadActions._cancel_download(stub)

    def test_force_cancel_downloading_state(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "downloading"}
        stub._dl_spinner_timer = _FakeTimer()

        with patch("nyrx.actions.download.clean_part_files"):
            DownloadActions._force_cancel_download(stub)

        assert stub._dl_cancel_watchdog is None
        assert stub._download_state["status"] == "cancelled"

    def test_force_cancel_other_state_noop(self) -> None:
        stub = _terminal_stub()
        stub._download_state = {"status": "complete"}

        DownloadActions._force_cancel_download(stub)

        assert stub._dl_cancel_watchdog is None
        assert stub._download_state["status"] == "complete"
