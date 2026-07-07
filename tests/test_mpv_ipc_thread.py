# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for MpvIPCThread state machine (C04).

Tests ``_poll_once()`` as the state-transition function directly,
avoiding the real socket and subprocess.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from tests.fakes import FakeSocket


class TestMpvIPCThread:
    def _make_thread(self, poll_return: int | None = None) -> tuple:
        """Create an MpvIPCThread with mocked process."""
        mock_proc = MagicMock()
        mock_proc.poll.return_value = poll_return  # None = running

        from nyrx.player import MpvIPCThread

        thread = MpvIPCThread("/fake/socket", mock_proc)
        return thread, mock_proc

    def test_initial_state(self) -> None:
        thread, _ = self._make_thread()
        state = thread.get_state()
        assert state["running"] is True
        assert state["time_pos"] is None
        assert state["duration"] is None
        assert state["paused"] is False
        assert state["paused_for_cache"] is False

    def test_poll_once_process_running(self) -> None:
        thread, _ = self._make_thread(poll_return=None)
        responses = [
            {"error": "success", "data": 42.5},
            {"error": "success", "data": 180.0},
            {"error": "success", "data": False},
            {"error": "success", "data": False},
        ]

        with patch("socket.socket", return_value=FakeSocket(responses, chunk_size=20)):
            thread._poll_once()

        state = thread.get_state()
        assert state["running"] is True
        assert state["time_pos"] == 42.5
        assert state["duration"] == 180.0
        assert state["paused"] is False
        assert state["paused_for_cache"] is False

    def test_poll_once_process_stopped_resets_state(self) -> None:
        thread, mock_proc = self._make_thread(poll_return=0)
        mock_proc.poll.return_value = 0  # process exited

        with thread._lock:
            thread._state["time_pos"] = 99.9
            thread._state["duration"] = 200.0
            thread._state["paused"] = True

        thread._poll_once()

        state = thread.get_state()
        assert state["running"] is False
        assert state["time_pos"] is None
        assert state["duration"] is None
        assert state["paused"] is False
        assert state["paused_for_cache"] is False

    def test_poll_once_socket_creation_failure_propagates_to_run(self) -> None:
        """``socket.socket()`` is outside the try/except in
        ``_poll_once``, so the exception propagates up to ``run()``
        which logs and continues."""
        thread, _ = self._make_thread(poll_return=None)

        with patch("socket.socket", side_effect=OSError("no socket")):
            with pytest.raises(OSError, match="no socket"):
                thread._poll_once()

        # State remains at initial values (not written by _poll_once)
        state = thread.get_state()
        assert state["running"] is True
        assert state["time_pos"] is None

    def test_get_state_returns_snapshot(self) -> None:
        thread, _ = self._make_thread()
        state = thread.get_state()
        state["time_pos"] = 999
        original = thread.get_state()
        assert original["time_pos"] is None

    def test_stop_sets_stop_event(self) -> None:
        thread, _ = self._make_thread()
        assert thread._stop_event.is_set() is False
        thread.stop()
        assert thread._stop_event.is_set() is True

    def test_run_loop_calls_poll_once_repeatedly(self) -> None:
        thread, _ = self._make_thread(poll_return=None)
        call_count = 0

        def counting_poll() -> None:
            nonlocal call_count
            call_count += 1
            if call_count >= 3:
                thread.stop()

        thread._poll_once = counting_poll

        with patch("socket.socket") as mock_socket:
            mock_socket.return_value = MagicMock()
            thread.run()

        assert call_count == 3

    def test_run_loop_recovers_from_poll_exception(self) -> None:
        thread, _ = self._make_thread(poll_return=None)
        call_count = 0

        def failing_then_stop() -> None:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("first poll crash")
            if call_count >= 3:
                thread.stop()

        thread._poll_once = failing_then_stop
        thread.run()
        assert call_count == 3

    def test_send_on_socket_success(self) -> None:
        from nyrx.player import MpvIPCThread

        sock = FakeSocket([{"error": "success", "data": 42.0}], chunk_size=10)
        result = MpvIPCThread._send_on_socket(
            sock, {"command": ["get_property", "time-pos"]}
        )
        assert result == {"error": "success", "data": 42.0}
        assert sock._sent_data == [b'{"command": ["get_property", "time-pos"]}\n']

    def test_send_on_socket_failure_returns_none(self) -> None:
        from nyrx.player import MpvIPCThread

        sock = MagicMock()
        sock.send.side_effect = OSError("broken pipe")
        result = MpvIPCThread._send_on_socket(sock, {"command": ["test"]})
        assert result is None

    def test_poll_once_updates_paused_for_cache(self) -> None:
        thread, _ = self._make_thread(poll_return=None)
        responses = [
            {"error": "success", "data": 10.0},
            {"error": "success", "data": 300.0},
            {"error": "success", "data": True},
            {"error": "success", "data": True},
        ]

        with patch("socket.socket", return_value=FakeSocket(responses, chunk_size=20)):
            thread._poll_once()

        state = thread.get_state()
        assert state["paused"] is True
        assert state["paused_for_cache"] is True

    def test_send_on_socket_skips_event_before_reply(self) -> None:
        """An async mpv ``event`` line before the reply must not be misread."""
        from nyrx.player import MpvIPCThread

        sock = FakeSocket(
            [
                {"event": "end-file", "reason": "eof"},
                {"error": "success", "data": 42.0},
            ],
            chunk_size=10,
        )
        result = MpvIPCThread._send_on_socket(
            sock, {"command": ["get_property", "time-pos"]}
        )
        assert result == {"error": "success", "data": 42.0}

    def test_poll_once_skips_interleaved_events(self) -> None:
        """Events interleaved between property reads must not shift the replies."""
        thread, _ = self._make_thread(poll_return=None)
        responses = [
            {"event": "file-loaded"},
            {"error": "success", "data": 10.0},  # time-pos
            {"error": "success", "data": 300.0},  # duration
            {"event": "seek"},
            {"error": "success", "data": False},  # pause
            {"error": "success", "data": True},  # paused-for-cache
        ]

        with patch("socket.socket", return_value=FakeSocket(responses, chunk_size=20)):
            thread._poll_once()

        state = thread.get_state()
        assert state["time_pos"] == 10.0
        assert state["duration"] == 300.0
        assert state["paused"] is False
        assert state["paused_for_cache"] is True

    def test_mpv_ipc_send_skips_event_before_reply(self) -> None:
        """``MPVIPC._send`` (seek/pause/quit) must skip interleaved events too."""
        from unittest.mock import MagicMock

        from nyrx.player import MPVIPC

        ipc = object.__new__(MPVIPC)
        ipc.socket_path = "/fake/socket"
        ipc.process = MagicMock()

        sock = FakeSocket(
            [
                {"event": "pause"},
                {"error": "success", "data": None},
            ],
            chunk_size=10,
        )
        with patch("socket.socket", return_value=sock):
            result = ipc._send({"command": ["cycle", "pause"]})
        assert result == {"error": "success", "data": None}
        assert sock._closed is True
