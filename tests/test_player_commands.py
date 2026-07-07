# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for player.py command construction (C02 / C03).

``play_video_async`` builds an mpv ``cmd`` list and ``download_video`` builds
a yt-dlp ``ydl_opts`` dict.  Neither is testable without mocking the
heavy machinery (subprocess / yt-dlp), but the parameter-construction
logic itself is what we're after.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest


class TestResolveMpvFlags:
    """C01: mpv flag resolution per source."""

    def test_tv_movies_gets_hwdec_no(self) -> None:
        from nyrx.player import resolve_mpv_flags

        flags = resolve_mpv_flags("tv_movies")
        assert "--hwdec=no" in flags
        assert "--profile=fast" in flags

    def test_youtube_gets_hwdec_auto(self) -> None:
        from nyrx.player import resolve_mpv_flags

        flags = resolve_mpv_flags("youtube")
        assert "--hwdec=auto" in flags
        assert "--profile=fast" in flags

    def test_unknown_source_defaults_to_youtube_flags(self) -> None:
        from nyrx.player import resolve_mpv_flags

        flags = resolve_mpv_flags("unknown_source")
        assert "--hwdec=auto" in flags
        assert "--profile=fast" in flags


class TestPlayVideoAsyncCmd:
    """C02: mpv command construction in ``play_video_async``."""

    @pytest.fixture(autouse=True)
    def _no_poll_thread(self):
        """Patch ``player.MPVIPC`` so no real ``MpvIPCThread`` is started.

        ``play_video_async`` constructs ``MPVIPC(socket_path, proc)`` on the
        found path; the real constructor spawns a background poll thread that
        would otherwise leak per test (daemon, but noisy).
        """
        with patch("nyrx.player.MPVIPC"):
            yield

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_audio_only_adds_no_video_flag(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="test123", audio_only=True)

        cmd = mock_popen.call_args[0][0]
        assert "--no-video" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_default_does_not_add_no_video(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="test123", audio_only=False)

        cmd = mock_popen.call_args[0][0]
        assert "--no-video" not in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_start_pos_added_when_positive(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="test123", start_pos=30.0)

        cmd = mock_popen.call_args[0][0]
        assert "--start=30.0" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_start_pos_omitted_when_zero(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="test123", start_pos=0)

        cmd = mock_popen.call_args[0][0]
        assert all("--start" not in arg for arg in cmd)

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_referrer_passed_through(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="test123", referrer="https://example.com")

        cmd = mock_popen.call_args[0][0]
        assert "--referrer=https://example.com" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_url_built_from_yt_id_when_not_given(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123")

        cmd = mock_popen.call_args[0][0]
        assert "https://www.youtube.com/watch?v=abc123" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_url_passed_directly(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(url="https://example.com/stream")

        cmd = mock_popen.call_args[0][0]
        assert "https://example.com/stream" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_ytdl_format_added_when_not_tv_movies(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123", ytdl_format="best")

        cmd = mock_popen.call_args[0][0]
        assert "--ytdl-format=best" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_ytdl_format_omitted_for_tv_movies(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        """Documented exclusion: tv_movies streams must not get --ytdl-format."""
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123", ytdl_format="best", source="tv_movies")

        cmd = mock_popen.call_args[0][0]
        assert all("--ytdl-format" not in arg for arg in cmd)

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_non_referer_stream_headers_become_http_header_fields(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123", stream_headers={"Authorization": "Bearer x"})

        cmd = mock_popen.call_args[0][0]
        assert "--http-header-fields=Authorization: Bearer x" in cmd
        assert all("--referrer" not in arg for arg in cmd)

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_referer_in_stream_headers_sets_referrer(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        """A ``Referer`` header must become ``--referrer``, not an http-header field."""
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123", stream_headers={"referer": "https://ref"})

        cmd = mock_popen.call_args[0][0]
        assert "--referrer=https://ref" in cmd
        assert all("--http-header-fields=referer" not in arg for arg in cmd)

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_explicit_referrer_beats_stream_header(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(
            yt_id="abc123",
            referrer="https://explicit",
            stream_headers={"Referer": "https://header"},
        )

        cmd = mock_popen.call_args[0][0]
        assert "--referrer=https://explicit" in cmd
        assert all("https://header" not in arg for arg in cmd)

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_subs_become_sub_file_flags(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123", subs=["a.vtt", "b.vtt"])

        cmd = mock_popen.call_args[0][0]
        assert "--sub-file=a.vtt" in cmd
        assert "--sub-file=b.vtt" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_audio_urls_become_audio_file_flags(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(yt_id="abc123", audio_urls=["a.m4a", "b.m4a"])

        cmd = mock_popen.call_args[0][0]
        assert "--audio-file=a.m4a" in cmd
        assert "--audio-file=b.m4a" in cmd

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_tracker_opts_appended(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = None
        mock_popen.return_value = mock_proc

        play_video_async(
            yt_id="abc123",
            title="Some Title",
            channel="A Channel",
            source="youtube",
            tracker_media_type="tv",
            tracker_season_number=2,
            tracker_episode_number=3,
        )

        cmd = mock_popen.call_args[0][0]
        assert any(
            arg.startswith("--script-opts=tracker_yt_id=abc123,")
            and "tracker_title=Some Title," in arg
            and "tracker_channel=A Channel," in arg
            and "tracker_source=youtube," in arg
            and "tracker_media_type=tv," in arg
            and "tracker_season_number=2," in arg
            and "tracker_episode_number=3" in arg
            for arg in cmd
        )

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_socket_appeared_but_process_died_returns_none(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.poll.return_value = 1  # process already dead
        mock_popen.return_value = mock_proc

        result = play_video_async(yt_id="abc123")

        assert result is None
        mock_proc.terminate.assert_called_once()

    @patch("nyrx.player.os.path.exists", return_value=False)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_socket_timeout_terminates_and_returns_none(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_popen.return_value = mock_proc

        result = play_video_async(yt_id="abc123")

        assert result is None
        mock_proc.terminate.assert_called_once()
        mock_proc.wait.assert_called_once_with(timeout=3)

    @patch("nyrx.player.os.path.exists", return_value=False)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_tv_movies_timeout_reads_mpv_stderr(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        import io

        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.stderr = io.BytesIO(b"mpv err line 1\nmpv err line 2\n")
        mock_popen.return_value = mock_proc

        with patch("nyrx.player._start_mpv_stderr_drain") as mock_drain:
            result = play_video_async(yt_id="abc123", source="tv_movies")

        assert result is None
        mock_drain.assert_called_once_with(mock_proc)

    @patch("nyrx.player.os.path.exists", return_value=False)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_non_tv_source_uses_devnull_stderr(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        """Non-tv sources must NOT set stderr=PIPE (no drain thread needed)."""
        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.stderr = None  # DEVNULL exposes no stream
        mock_popen.return_value = mock_proc

        with patch("nyrx.player._start_mpv_stderr_drain") as mock_drain:
            result = play_video_async(yt_id="abc123", source="youtube")

        assert result is None
        mock_drain.assert_not_called()

    def test_drain_mpv_stderr_logs_lines(self, caplog) -> None:
        """_drain_mpv_stderr logs each non-empty stderr line."""
        import io

        from nyrx.player import _drain_mpv_stderr

        proc = MagicMock()
        proc.stderr = io.BytesIO(b"line one\nline two\n\n")

        with caplog.at_level("DEBUG", logger="nyrx.player"):
            _drain_mpv_stderr(proc)

        assert "mpv stderr: line one" in caplog.text
        assert "mpv stderr: line two" in caplog.text

    def test_drain_mpv_stderr_handles_none_stream(self) -> None:
        """_drain_mpv_stderr is a no-op when the stream is None (DEVNULL)."""
        from nyrx.player import _drain_mpv_stderr

        proc = MagicMock()
        proc.stderr = None

        assert _drain_mpv_stderr(proc) is None

    @patch("nyrx.player.os.path.exists", return_value=False)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen")
    def test_timeout_then_kill_when_wait_times_out(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        import subprocess

        from nyrx.player import play_video_async

        mock_proc = MagicMock()
        mock_proc.wait.side_effect = [subprocess.TimeoutExpired("mpv", 3)]
        mock_popen.return_value = mock_proc

        result = play_video_async(yt_id="abc123")

        assert result is None
        mock_proc.kill.assert_called_once()

    @patch("nyrx.player.os.path.exists", return_value=True)
    @patch("nyrx.player.time.sleep")
    @patch("nyrx.player.subprocess.Popen", side_effect=OSError("no mpv"))
    def test_popen_failure_returns_none(
        self, mock_popen: MagicMock, _mock_sleep: MagicMock, _mock_exists: MagicMock
    ) -> None:
        from nyrx.player import play_video_async

        assert play_video_async(yt_id="abc123") is None


class TestBuildTrackerOpts:
    """C02b: ``--script-opts=`` tracker string assembly + sanitization."""

    def test_all_fields_assembled(self) -> None:
        from nyrx.player import _build_tracker_opts

        opts = _build_tracker_opts(
            yt_id="id1",
            title="Title",
            channel="Channel",
            uploader_id="uid",
            permalink="perm",
            source="soundcloud",
            media_type="movie",
            season_number=2,
            episode_number=3,
        )
        assert opts.startswith("--script-opts=tracker_yt_id=id1,tracker_dir=")
        assert "tracker_title=Title," in opts
        assert "tracker_channel=Channel," in opts
        assert "tracker_uploader_id=uid," in opts
        assert "tracker_permalink=perm," in opts
        assert "tracker_source=soundcloud," in opts
        assert "tracker_media_type=movie," in opts
        assert "tracker_season_number=2," in opts
        assert "tracker_episode_number=3" in opts

    def test_title_and_channel_sanitized(self) -> None:
        """The ``,=[\\]\"`` chars in user-supplied strings must be stripped:
        they break mpv's ``--script-opts=`` parser (watch-history silently stops)."""
        from nyrx.player import _build_tracker_opts

        opts = _build_tracker_opts(yt_id="id1", title='a,b=c[d]"e\\f', channel="x]y")
        assert "tracker_title=abcdef" in opts
        assert "tracker_channel=xy" in opts

    def test_optional_fields_omitted_when_none(self) -> None:
        from nyrx.player import _build_tracker_opts

        opts = _build_tracker_opts(yt_id="id1")
        assert opts.startswith("--script-opts=tracker_yt_id=id1,tracker_dir=")

    def test_zero_season_number_still_included(self) -> None:
        """season/episode guard is `is not None` (not truthiness): 0 is valid."""
        from nyrx.player import _build_tracker_opts

        opts = _build_tracker_opts(yt_id="id1", season_number=0, episode_number=0)
        assert "tracker_season_number=0" in opts
        assert "tracker_episode_number=0" in opts


class TestSearchYoutube:
    """C04: ``search_youtube``: yt-dlp JSON dump parsing + RuntimeError."""

    def _run(self, stdout: str, returncode: int = 0, stderr: str = ""):
        from types import SimpleNamespace

        with patch(
            "nyrx.player.subprocess.run",
            return_value=SimpleNamespace(
                returncode=returncode, stdout=stdout, stderr=stderr
            ),
        ) as mock_run:
            from nyrx.player import search_youtube

            result = search_youtube("hello")
            return result, mock_run

    def test_parses_two_json_lines_and_picks_largest_thumbnail(self) -> None:
        stdout = "\n".join(
            [
                "{"
                '"id":"vid1","title":"Title One","channel":"Chan","uploader":"Up",'
                '"duration":301,"view_count":1234,"release_year":2023,'
                '"thumbnails":['
                '{"url":"small","width":1,"height":1},'
                '{"url":"big","width":120,"height":90}]'
                "}",
                "{"
                '"id":"vid2","title":"Title Two","duration":0,"view_count":0,'
                '"thumbnails":[]'
                "}",
            ]
        )
        result, mock_run = self._run(stdout)

        assert result == [
            {
                "yt_id": "vid1",
                "title": "Title One",
                "channel": "Chan",
                "duration": 301,
                "views": 1234,
                "published": 2023,
                "thumbnail_url": "big",
            },
            {
                "yt_id": "vid2",
                "title": "Title Two",
                "channel": "",
                "duration": 0,
                "views": 0,
                "published": "",
                "thumbnail_url": "",
            },
        ]
        # channel falls back to uploader when channel missing
        cmd = mock_run.call_args[0][0]
        assert any("ytsearch" in arg for arg in cmd)

    def test_channel_falls_back_to_uploader(self) -> None:
        stdout = '{"id":"vid1","title":"T","uploader":"Up"}'
        result, _ = self._run(stdout)
        assert result[0]["channel"] == "Up"

    def test_blank_stdout_returns_empty_list(self) -> None:
        result, _ = self._run("")
        assert result == []

    def test_nonzero_returncode_raises_runtime_error_with_stderr(self) -> None:
        with pytest.raises(RuntimeError, match="yt-dlp failed"):
            self._run("", returncode=1, stderr="yt-dlp failed\n")

    def test_nonzero_returncode_with_empty_stderr_uses_default_message(self) -> None:
        with pytest.raises(RuntimeError, match="yt-dlp exited 2"):
            self._run("", returncode=2, stderr="")


class TestFetchVideoMetadata:
    """C05: ``fetch_video_metadata``: yt-dlp extract_info mapping."""

    def _extract(self, info):
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.return_value = info
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            from nyrx.player import fetch_video_metadata

            return fetch_video_metadata("https://www.youtube.com/watch?v=vid1")

    def test_happy_path_maps_fields(self) -> None:
        result = self._extract(
            {
                "id": "vid1",
                "title": "T",
                "channel": "C",
                "view_count": 42,
                "duration": 99,
                "thumbnail": "https://th/1.jpg",
            }
        )
        assert result == {
            "yt_id": "vid1",
            "title": "T",
            "channel": "C",
            "views": 42,
            "duration": 99,
            "thumbnail_url": "https://th/1.jpg",
        }

    def test_missing_id_returns_none(self) -> None:
        assert self._extract({"title": "No id"}) is None

    def test_extraction_failure_returns_none(self) -> None:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = RuntimeError("boom")
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl):
            from nyrx.player import fetch_video_metadata

            assert fetch_video_metadata("https://www.youtube.com/watch?v=x") is None


class TestDownloadVideoOpts:
    """C03: yt-dlp options dict construction in ``download_video``."""

    _mock_ydl: MagicMock
    _mock_ydl_cls: MagicMock

    @pytest.fixture(autouse=True)
    def _mock_ytdlp(self) -> None:
        mock_ydl = MagicMock()
        mock_ydl.__enter__.return_value = mock_ydl
        mock_ydl.extract_info.side_effect = [
            {"title": "Test", "id": "abc123"},
            {"title": "Test", "id": "abc123"},
        ]
        mock_ydl.prepare_filename.return_value = "/tmp/Test.mp4"
        with patch("yt_dlp.YoutubeDL", return_value=mock_ydl) as mock_cls:
            self._mock_ydl = mock_ydl
            self._mock_ydl_cls = mock_cls
            yield

    def test_default_format_string(self) -> None:
        from nyrx.player import download_video

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp")

        opts = self._mock_ydl_cls.call_args[0][0]
        assert (
            opts["format"] == "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
        )
        assert opts["ffmpeg_args"] == ["-threads", "1"]

    def test_audio_only_format(self) -> None:
        from nyrx.player import download_video

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp", audio_only=True)

        opts = self._mock_ydl_cls.call_args[0][0]
        assert opts["format"] == "bestaudio/best"
        assert opts["postprocessors"] == [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }
        ]

    def test_custom_format_override(self) -> None:
        from nyrx.player import download_video

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp", format_str="worstvideo")

        opts = self._mock_ydl_cls.call_args[0][0]
        assert opts["format"] == "worstvideo"
        assert opts.get("merge_output_format") == "mp4"

    def test_progress_hook_wired(self) -> None:
        from nyrx.player import download_video

        hook = MagicMock()

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp", progress_callback=hook)

        opts = self._mock_ydl_cls.call_args[0][0]
        assert opts.get("progress_hooks") == [hook]

    def test_output_template_includes_title_and_ext(self) -> None:
        from nyrx.player import download_video

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp")

        opts = self._mock_ydl_cls.call_args[0][0]
        assert "%(title)s" in opts["outtmpl"]
        assert "%(ext)s" in opts["outtmpl"]

    def test_already_exists_short_circuits_before_download(self) -> None:
        """Pre-existing predicted file → (path, True) with NO download call
        (extract_info only runs with download=False)."""
        from nyrx.player import download_video

        self._mock_ydl.extract_info.side_effect = [{"title": "Test", "id": "abc123"}]

        with patch("nyrx.player.os.path.exists", return_value=True):
            path, existed = download_video("abc123", "Test", output_dir="/tmp")

        assert path == "/tmp/Test.mp4"
        assert existed is True
        # exactly one extract_info call, and it was the dry-run (download=False)
        assert self._mock_ydl.extract_info.call_count == 1
        assert self._mock_ydl.extract_info.call_args.kwargs["download"] is False

    def test_postprocessor_callback_wired(self) -> None:
        from nyrx.player import download_video

        callback = MagicMock()

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video(
                "abc123", "Test", output_dir="/tmp", postprocessor_callback=callback
            )

        opts = self._mock_ydl_cls.call_args[0][0]
        hooks = opts.get("postprocessor_hooks")
        assert hooks is not None
        assert hooks[-1] is callback  # user callback appended after the recorder

    # ------------------------------------------------------------------
    # Tier 0.1: keepvideo: False prevents duplicate files on disk
    # ------------------------------------------------------------------

    def test_keepvideo_is_false(self) -> None:
        """download_video sets keepvideo=False so yt-dlp deletes the original after post-processing.

        Without this flag, yt-dlp keeps the pre-processor format (e.g. .m4a)
        alongside the post-processor output (.mp3), creating silent duplicates.
        """
        from nyrx.player import download_video

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp", audio_only=True)

        opts = self._mock_ydl_cls.call_args[0][0]
        assert opts.get("keepvideo") is False

    # ------------------------------------------------------------------
    # Tier 0.2: return path matches the actual file on disk
    # ------------------------------------------------------------------

    def test_post_download_returns_actual_file(self) -> None:
        """download_video returns the authoritative post-processed file path.

        yt-dlp's prepare_filename returns the pre-processor extension (.m4a).
        FFmpegExtractAudio produces .mp3. The function must return the .mp3
        path: taken from requested_downloads[].filepath, not the .m4a path
        that prepare_filename returns.

        Call sequence under test:
          1. os.path.exists(/tmp/Test.mp3): exists check before download → False
          2. os.path.exists(/tmp/Test.mp3): resolver candidate → True
        """
        from nyrx.player import download_video

        self._mock_ydl.prepare_filename.return_value = "/tmp/Test.m4a"
        self._mock_ydl.extract_info.side_effect = [
            {"title": "Test", "id": "abc123"},
            {
                "title": "Test",
                "id": "abc123",
                "requested_downloads": [{"filepath": "/tmp/Test.mp3"}],
            },
        ]

        with patch("nyrx.player.os.path.exists", side_effect=[False, True]):
            path, existed = download_video(
                "abc123",
                "Test",
                output_dir="/tmp",
                audio_only=True,
            )

        assert path == "/tmp/Test.mp3"
        assert existed is False

    def test_resolve_output_path_prefers_recorded_pp_path(self) -> None:
        """_resolve_output_path prioritizes the pp-hook captured filepath.

        Priority: recorded (post-processed) path → requested_downloads →
        prepare_filename. The first existing candidate wins.
        """
        from nyrx.player import _resolve_output_path

        ydl = MagicMock()
        ydl.prepare_filename.return_value = "/tmp/Test.prepared"

        info = {"requested_downloads": [{"filepath": "/tmp/Test.rd"}]}

        # recorded path exists → it wins even though others do not
        with patch(
            "nyrx.player.os.path.exists", side_effect=lambda p: p == "/tmp/Test.pp"
        ):
            resolved = _resolve_output_path(
                ydl, info, finalized={"filepath": "/tmp/Test.pp"}
            )

        assert resolved == "/tmp/Test.pp"

    def test_resolve_output_path_uses_requested_downloads_when_no_pp(self) -> None:
        """Without a recorded path, requested_downloads[].filepath is used."""
        from nyrx.player import _resolve_output_path

        ydl = MagicMock()
        ydl.prepare_filename.return_value = "/tmp/Test.prepared"

        info = {"requested_downloads": [{"filepath": "/tmp/Test.rd"}]}

        with patch(
            "nyrx.player.os.path.exists", side_effect=lambda p: p == "/tmp/Test.rd"
        ):
            resolved = _resolve_output_path(ydl, info, finalized={})

        assert resolved == "/tmp/Test.rd"

    def test_resolve_output_path_falls_back_to_prepare_filename(self) -> None:
        """With no recorded path and no requested_downloads, prepare_filename wins."""
        from nyrx.player import _resolve_output_path

        ydl = MagicMock()
        ydl.prepare_filename.return_value = "/tmp/Test.prepared"

        with patch(
            "nyrx.player.os.path.exists",
            side_effect=lambda p: p == "/tmp/Test.prepared",
        ):
            resolved = _resolve_output_path(ydl, {"title": "Test"}, finalized={})

        assert resolved == "/tmp/Test.prepared"

    def test_resolve_output_path_best_effort_warns_when_nothing_exists(
        self, caplog
    ) -> None:
        """No candidate on disk → warn and return the highest-priority candidate."""
        from nyrx.player import _resolve_output_path

        ydl = MagicMock()
        ydl.prepare_filename.return_value = "/tmp/Test.prepared"

        with (
            patch("nyrx.player.os.path.exists", return_value=False),
            caplog.at_level("WARNING", logger="nyrx.player"),
        ):
            resolved = _resolve_output_path(
                ydl,
                {"requested_downloads": [{"filepath": "/tmp/Test.rd"}]},
                finalized={},
            )

        assert resolved == "/tmp/Test.rd"
        assert "no candidate exists" in caplog.text

    def test_download_video_wires_final_path_recorder(self) -> None:
        """download_video always registers the final-path recorder hook.

        The recorder must be first so the user's postprocessor callback still
        runs (and the recorder captures the post-processed filepath).
        """
        from nyrx.player import download_video

        with patch("nyrx.player.os.path.exists", return_value=False):
            download_video("abc123", "Test", output_dir="/tmp")

        hooks = self._mock_ydl_cls.call_args_list[0].args[0].get("postprocessor_hooks")
        assert hooks is not None and len(hooks) == 1

    def test_existing_mp3_does_not_block_video_download(self) -> None:
        """Having Title.mp3 on disk must NOT short-circuit a video download.

        The skip-if-exists check uses the extension derived from audio_only,
        so an mp3 present from an earlier audio download does not collide with
        the mp4 request: the video is still downloaded and its path returned.
        """
        from nyrx.player import download_video

        self._mock_ydl.prepare_filename.return_value = "/tmp/Test.mp4"
        self._mock_ydl.extract_info.side_effect = [
            {"title": "Test", "id": "abc123"},
            {
                "title": "Test",
                "id": "abc123",
                "requested_downloads": [{"filepath": "/tmp/Test.mp4"}],
            },
        ]

        # exists(/tmp/Test.mp4) → False (only .mp3 is on disk), then resolver → True
        with patch("nyrx.player.os.path.exists", side_effect=[False, True]):
            path, existed = download_video("abc123", "Test", output_dir="/tmp")

        assert existed is False
        assert path == "/tmp/Test.mp4"
        assert self._mock_ydl.extract_info.call_count == 2
