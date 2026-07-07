# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/playback.py`` (5B.2: the playback state machine).

These cover the mpv lifecycle orchestration without ever booting ``MediaApp``:
``object.__new__(PlaybackActions)`` stubs resolve sibling methods through the
class so full chains run, and every side-effect caller (``_cleanup_mpv``,
``_play_next_queued``, ``notify``, ``set_interval``...) is a recorded mock.
``@work`` methods are invoked via ``__wrapped__`` (the decorator asserts
``isinstance(self, DOMNode)`` when called directly).
"""

from __future__ import annotations

import subprocess
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from nyrx.actions.playback import PlaybackActions
from nyrx.config import SEVERITY_ERROR, TIMEOUT_CONFIRM, TIMEOUT_ERROR
from nyrx.models import MediaRequest, PlaybackState
from nyrx.queues import PlaybackQueue, QueueItem
from nyrx.screens.queue import QueueModal
from nyrx.widgets import FeedTrackItem, RadioNowPlaying, ResultItem, TVChip
from nyrx.widgets.sidebar import ScState, SoundCloudNowPlaying
from tests.fakes import stub_self


class _FakeIpc:
    """Stand-in for the mpv IPC used by ``_poll_mpv``."""

    def __init__(
        self,
        running=True,
        time_pos=0.0,
        duration=0.0,
        paused=False,
        paused_for_cache=False,
        metadata=None,
    ):
        self._state = {
            "running": running,
            "time_pos": time_pos,
            "duration": duration,
            "paused": paused,
            "paused_for_cache": paused_for_cache,
            "metadata": metadata,
        }
        self._metadata = metadata
        self.stop_calls = 0

    def get_state(self):
        return dict(self._state)

    def get_property(self, name):
        return self._metadata

    def is_running(self):
        return self._state["running"]

    def stop(self):
        self.stop_calls += 1


class _Timer:
    """Stand-in for a Textual timer whose ``stop`` is recorded."""

    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


class _NPFake:
    """Plain stand-in for a now-playing widget (not Radio/SoundCloud)."""

    def __init__(self):
        self.update_calls = []
        self._offline_mode = False
        self.display = True
        self._refresh = MagicMock()
        self.start_playback = MagicMock()
        self.stop_playback = MagicMock()

    def update_state(self, state):
        self.update_calls.append(state)


class _FakeSource:
    def __init__(self, play_result=None):
        self.play_result = play_result
        self.calls = []

    def play(self, data, audio_only=False, ytdl_format=None, start_pos=None):
        self.calls.append((audio_only, ytdl_format, start_pos))
        return self.play_result


def _radio_np():
    return stub_self(
        RadioNowPlaying,
        _icy_title="",
        _refresh=MagicMock(),
        _paused=False,
        _buffering=False,
    )


def _sc_np(state=ScState.CONNECTING):
    return stub_self(
        SoundCloudNowPlaying,
        _state=state,
        show_visualizer=MagicMock(),
        show_fallback=MagicMock(),
        stop_playback=MagicMock(),
        _refresh=MagicMock(),
    )


def _make_stub(**overrides):
    """Bare ``PlaybackActions`` with sane defaults + side-effect mocks."""
    defaults = {
        "_play_token": 0,
        "_resolve_token": 0,
        "_sc_resolving": False,
        "_tv_probing": False,
        "_last_icy_title": "",
        "_now_playing_data": None,
        "_last_playing_data": None,
        "_last_playback_pos": 0,
        "_stalled_count": 0,
        "_stream_ready": False,
        "_last_known_pos": -1,
        "_play_start_time": 0.0,
        "_stopping": False,
        "_queue_frozen": False,
        "_syncing": False,
        "_mpv_ipc": None,
        "_poll_timer": None,
        "_play_spinner_timer": None,
        "_np_side": None,
        "_subs_tmpdir": None,
        "_audio_only": False,
        "_online": True,
        "_show_back_online": False,
        "_source": "youtube",
        "_quality": "1080p",
        "_in_watchlist": False,
        "_in_artist_profile": False,
        "_in_liked": False,
        "_in_following": False,
        "_w_watchlist_screen": None,
        "_w_tv_series": None,
        "_w_artist_profile": None,
        "_w_liked_screen": None,
        "_w_fs_center_list": None,
        "_w_sidebar_queue": None,
        "_w_download": None,
        "_w_results_list": None,
        "_download_pending": [],
        "_sources": {},
        "_playback_queue": PlaybackQueue(),
        "focused": None,
        "screen": SimpleNamespace(has_class=MagicMock(return_value=True)),
        "set_interval": MagicMock(),
        "set_timer": MagicMock(),
        "call_later": MagicMock(),
        "call_from_thread": MagicMock(),
        "notify": MagicMock(),
        "_active_np_widget": MagicMock(return_value=None),
        "_update_sidebar_content": MagicMock(),
        "_update_sidebar_context": MagicMock(),
        "_update_radio_playing_indicator": MagicMock(),
        "_update_sc_home_sidebar_class": MagicMock(),
        "_update_queue_indicator": MagicMock(),
        "_update_mode_indicator": MagicMock(),
        "_apply_sidebar": MagicMock(),
        "_sync_np_widget": MagicMock(),
        "_refresh_queue_modal": MagicMock(),
        "_refresh_watchlist_statuses": MagicMock(),
        "_render_focus_indicators": MagicMock(),
        "_get_quality_format": MagicMock(return_value="FMT"),
        "_get_quality_height": MagicMock(return_value=1080),
        "_play": MagicMock(),
        "_play_raw": MagicMock(),
        "_start_playback": MagicMock(),
        "_finish_playback": MagicMock(),
        "_sync_finish": MagicMock(),
        "_cleanup_mpv": MagicMock(),
        "_play_next_queued": MagicMock(),
        "_resolve_then_play": MagicMock(),
        "_play_tv_movies": MagicMock(),
        "_play_tv_worker": MagicMock(),
        "_sync_sc_np_metadata": MagicMock(),
        "_play_tv_ready": MagicMock(),
        "_play_tv_failed": MagicMock(),
        "_on_sc_resolved": MagicMock(),
        "_tv_movies_finish": MagicMock(),
    }
    defaults.update(overrides)
    return stub_self(PlaybackActions, **defaults)


def _call(cls_method, stub, *args, **kwargs):
    return cls_method(stub, *args, **kwargs)


class TestPollMpv:
    """The crown jewel: full stall / finish / offline / radio ICY machine."""

    def test_no_ipc_is_noop(self):
        stub = _make_stub(_mpv_ipc=None)
        _call(PlaybackActions._poll_mpv, stub)

    def test_not_running_frozen_stops_ipc_and_hides_np(self):
        ipc = _FakeIpc(running=False)
        poll = _Timer()
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=ipc,
            _queue_frozen=True,
            _poll_timer=poll,
            _np_side=np_side,
            _last_playing_data={"x": 1},
            _last_playback_pos=5.0,
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._last_playing_data is None
        assert stub._last_playback_pos == 0
        assert ipc.stop_calls == 1
        assert stub._mpv_ipc is None
        assert poll.stops == 1
        assert stub._poll_timer is None
        assert np_side._offline_mode is False
        assert np_side.display is False
        np_side._refresh.assert_called_once()
        stub._cleanup_mpv.assert_not_called()

    def test_not_running_advances_queue_and_syncs(self):
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=False), _queue_frozen=False, _syncing=False
        )
        with patch("nyrx.actions.playback.sync_from_tracker") as mock_sync:
            _call(PlaybackActions._poll_mpv, stub)
        stub._cleanup_mpv.assert_called_once()
        stub._play_next_queued.assert_called_once()
        mock_sync.assert_called_once()
        stub._refresh_watchlist_statuses.assert_called_once()
        assert stub._syncing is False

    def test_not_running_skips_sync_when_already_syncing(self):
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=False), _queue_frozen=False, _syncing=True
        )
        with patch("nyrx.actions.playback.sync_from_tracker") as mock_sync:
            _call(PlaybackActions._poll_mpv, stub)
        stub._play_next_queued.assert_called_once()
        mock_sync.assert_not_called()

    def test_frozen_user_paused_resets_stall(self, patch_clock):
        patch_clock.set_monotonic(5.0)
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=5.0, duration=100.0, paused=True),
            _queue_frozen=True,
            _stalled_count=7,
            _stream_ready=True,
            _last_known_pos=3.0,
            _play_start_time=0.0,
            _np_side=np_side,
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stalled_count == 0
        assert stub._last_known_pos == 5.0
        assert len(np_side.update_calls) == 1
        state = np_side.update_calls[0]
        assert isinstance(state, PlaybackState)
        assert state.paused is True
        assert state.position == 5.0

    def test_frozen_paused_for_cache_stalls(self, patch_clock):
        patch_clock.set_monotonic(5.0)
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(
                running=True,
                time_pos=3.0,
                duration=100.0,
                paused=True,
                paused_for_cache=True,
            ),
            _queue_frozen=True,
            _stalled_count=0,
            _stream_ready=True,
            _last_known_pos=3.0,
            _play_start_time=0.0,
            _np_side=np_side,
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stalled_count == 1
        assert stub._last_known_pos == 3.0
        state = np_side.update_calls[0]
        assert state.buffering is True

    def test_frozen_stall_resets_when_position_moves(self, patch_clock):
        patch_clock.set_monotonic(5.0)
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=5.0, duration=100.0),
            _queue_frozen=True,
            _stalled_count=3,
            _stream_ready=True,
            _last_known_pos=3.0,
            _play_start_time=0.0,
            _np_side=np_side,
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stalled_count == 0
        assert stub._last_known_pos == 5.0

    def test_frozen_stall_waits_for_warmup_grace(self, patch_clock):
        patch_clock.set_monotonic(1.0)
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=3.0, duration=100.0),
            _queue_frozen=True,
            _stalled_count=0,
            _stream_ready=True,
            _last_known_pos=3.0,
            _play_start_time=0.0,
            _np_side=np_side,
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stalled_count == 0

    def test_frozen_stall_threshold_triggers_offline(self, patch_clock):
        patch_clock.set_monotonic(5.0)
        np_side = _NPFake()
        poll = _Timer()
        spinner = _Timer()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=5.0, duration=100.0),
            _queue_frozen=True,
            _stalled_count=7,
            _stream_ready=True,
            _last_known_pos=5.0,
            _play_start_time=0.0,
            _np_side=np_side,
            _poll_timer=poll,
            _play_spinner_timer=spinner,
            _now_playing_data={"yt_id": "abc", "title": "T"},
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stalled_count == 8
        assert stub._last_playing_data == {"yt_id": "abc", "title": "T"}
        assert stub._last_playback_pos == 5.0
        assert np_side._offline_mode is True
        np_side._refresh.assert_called_once()
        assert stub._mpv_ipc is None
        assert poll.stops == 1
        assert spinner.stops == 1

    def test_frozen_stall_threshold_detects_finish(self, patch_clock):
        patch_clock.set_monotonic(5.0)
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=99.0, duration=100.0),
            _queue_frozen=True,
            _stalled_count=7,
            _stream_ready=True,
            _last_known_pos=99.0,
            _play_start_time=0.0,
            _np_side=np_side,
            _now_playing_data={"yt_id": "abc"},
            _poll_timer=_Timer(),
            _play_spinner_timer=_Timer(),
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._last_playing_data is None
        assert stub._last_playback_pos == 0
        assert np_side._offline_mode is False
        assert np_side.display is False
        np_side._refresh.assert_called_once()
        assert stub._mpv_ipc is None

    def test_frozen_stall_zero_duration_is_not_finished(self, patch_clock):
        patch_clock.set_monotonic(5.0)
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=5.0, duration=None),
            _queue_frozen=True,
            _stalled_count=7,
            _stream_ready=True,
            _last_known_pos=5.0,
            _play_start_time=0.0,
            _np_side=np_side,
            _now_playing_data={"yt_id": "abc"},
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._last_playing_data == {"yt_id": "abc"}
        assert np_side._offline_mode is True

    def test_stream_ready_transition_when_duration_exceeds_one(self):
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=0.5, duration=100.0),
            _stream_ready=False,
            _np_side=np_side,
            _now_playing_data={},
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stream_ready is True
        assert stub._last_known_pos == 0.5
        assert len(np_side.update_calls) == 1

    def test_stream_ready_not_set_for_short_duration(self):
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=0.5, duration=1.0),
            _stream_ready=False,
            _np_side=np_side,
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._stream_ready is False

    def test_radio_icy_metadata_sets_title(self):
        np_side = _radio_np()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(
                running=True,
                time_pos=10.0,
                duration=100.0,
                metadata={"icy-title": "New Song"},
            ),
            _stream_ready=True,
            _np_side=np_side,
            _now_playing_data={"source": "radio"},
            _last_icy_title="",
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._last_icy_title == "New Song"
        assert np_side._icy_title == "New Song"
        assert np_side._refresh.called

    def test_radio_icy_dedup_on_unchanged(self):
        np_side = _radio_np()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(
                running=True,
                time_pos=10.0,
                duration=100.0,
                metadata={"icy-title": "Same"},
            ),
            _stream_ready=True,
            _np_side=np_side,
            _now_playing_data={"source": "radio"},
            _last_icy_title="Same",
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert np_side._icy_title == ""
        assert stub._last_icy_title == "Same"

    def test_radio_ignores_non_dict_metadata(self):
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(
                running=True, time_pos=10.0, duration=100.0, metadata="garbage"
            ),
            _stream_ready=True,
            _np_side=np_side,
            _now_playing_data={"source": "radio"},
            _last_icy_title="",
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._last_icy_title == ""

    def test_radio_no_metadata(self):
        np_side = _NPFake()
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True, time_pos=10.0, duration=100.0),
            _stream_ready=True,
            _np_side=np_side,
            _now_playing_data={"source": "radio"},
            _last_icy_title="",
        )
        _call(PlaybackActions._poll_mpv, stub)
        assert stub._last_icy_title == ""


class TestPlay:
    def _req(self, source="youtube", audio_only=False, data=None):
        return MediaRequest(
            yt_id="abc", title="Track", source=source, audio_only=audio_only, data=data
        )

    def test_stopping_returns_early(self):
        stub = _make_stub(_stopping=True)
        _call(PlaybackActions._play, stub, self._req())
        stub._play_raw.assert_not_called()
        assert len(stub._playback_queue) == 0

    def test_queue_path_when_playing(self):
        stub = _make_stub(_mpv_ipc=_FakeIpc(running=True))
        req = self._req()
        _call(PlaybackActions._play, stub, req)
        assert len(stub._playback_queue) == 1
        assert stub._playback_queue.items[0].request is req
        stub._sync_np_widget.assert_called_once()
        stub._refresh_queue_modal.assert_called_once()
        stub.notify.assert_called_once_with("Queued: Track", timeout=TIMEOUT_CONFIRM)
        assert req.data["audio_only"] is False

    def test_queue_path_when_offline_with_tag(self):
        stub = _make_stub(_online=False)
        req = self._req()
        _call(PlaybackActions._play, stub, req)
        stub.notify.assert_called_once_with(
            "Queued (offline): Track", timeout=TIMEOUT_CONFIRM
        )
        assert len(stub._playback_queue) == 1

    def test_queue_path_tv_movies_injects_server_mode(self):
        stub = _make_stub(
            _mpv_ipc=_FakeIpc(running=True),
            _sources={"tv_movies": SimpleNamespace(server_mode="tv")},
        )
        req = self._req(source="tv_movies")
        _call(PlaybackActions._play, stub, req)
        assert req.data["_queued_server_mode"] == "tv"
        assert len(stub._playback_queue) == 1

    def test_immediate_path_calls_play_raw_with_copy(self):
        stub = _make_stub()
        req = self._req(data={"yt_id": "abc", "title": "Track"})
        _call(PlaybackActions._play, stub, req)
        stub._play_raw.assert_called_once()
        args, kwargs = stub._play_raw.call_args
        (raw,) = args
        assert raw is not req.data
        assert raw == {**req.data, "source": "youtube"}
        assert kwargs == {"start_pos": None}

    def test_from_queue_preserves_audio_only(self):
        stub = _make_stub(_audio_only=True)
        req = self._req(audio_only=False)
        _call(PlaybackActions._play, stub, req, from_queue=True)
        assert req.audio_only is False
        stub._play_raw.assert_called_once()

    def test_non_from_queue_overrides_audio_only(self):
        stub = _make_stub(_audio_only=True)
        req = self._req(audio_only=False)
        _call(PlaybackActions._play, stub, req)
        assert req.audio_only is True

    def test_soundcloud_not_overridden(self):
        stub = _make_stub(_audio_only=True)
        req = self._req(source="soundcloud", audio_only=False)
        _call(PlaybackActions._play, stub, req)
        assert req.audio_only is False


class TestPlayRaw:
    def test_youtube_happy_path(self):
        ipc = _FakeIpc()
        src = _FakeSource(play_result=ipc)
        stub = _make_stub(
            _sources={"youtube": src},
            _active_np_widget=MagicMock(return_value=_NPFake()),
        )
        _call(
            PlaybackActions._play_raw,
            stub,
            {"source": "youtube", "yt_id": "abc", "title": "T"},
        )
        assert stub._resolve_token == 1
        assert stub._sc_resolving is False
        assert stub._last_icy_title == ""
        stub._start_playback.assert_called_once()
        stub._sync_finish.assert_called_once_with(ipc)
        assert src.calls == [(False, "FMT", None)]

    def test_youtube_play_failure_advances_queue(self):
        src = _FakeSource(play_result=None)
        stub = _make_stub(
            _sources={"youtube": src},
            _now_playing_data={"x": 1},
            _active_np_widget=MagicMock(return_value=_NPFake()),
        )
        _call(PlaybackActions._play_raw, stub, {"source": "youtube", "yt_id": "abc"})
        assert stub._now_playing_data is None
        stub._update_sidebar_content.assert_called_once()
        stub._update_sidebar_context.assert_called_once()
        stub.notify.assert_called_once_with(
            "Playback failed to start",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub.call_later.assert_called_once_with(stub._play_next_queued)
        stub._sync_finish.assert_not_called()

    def test_radio_uses_no_ytdl_format(self):
        ipc = _FakeIpc()
        src = _FakeSource(play_result=ipc)
        stub = _make_stub(
            _sources={"radio": src}, _active_np_widget=MagicMock(return_value=_NPFake())
        )
        _call(
            PlaybackActions._play_raw,
            stub,
            {"source": "radio", "url": "http://radio", "title": "R"},
        )
        assert src.calls == [(True, None, None)]
        stub._get_quality_format.assert_not_called()

    def test_soundcloud_resolve_pipeline(self):
        src = _FakeSource()
        np_side = _NPFake()
        old_np = _NPFake()
        stub = _make_stub(
            _sources={"soundcloud": src},
            _np_side=old_np,
            _active_np_widget=MagicMock(return_value=np_side),
        )
        data = {"source": "soundcloud", "yt_id": "sc1", "title": "S"}
        _call(PlaybackActions._play_raw, stub, data)
        assert stub._resolve_token == 1
        assert stub._sc_resolving is True
        old_np.stop_playback.assert_called_once()
        stub._start_playback.assert_called_once()
        args = stub.set_interval.call_args
        assert args[0][0] == 0.08
        assert args[0][1].__func__ is PlaybackActions._tick_play_spinner
        stub._resolve_then_play.assert_called_once_with(src, data, 1)

    def test_soundcloud_stops_existing_spinner(self):
        src = _FakeSource()
        spinner = _Timer()
        np_side = _NPFake()
        stub = _make_stub(
            _sources={"soundcloud": src},
            _play_spinner_timer=spinner,
            _active_np_widget=MagicMock(return_value=np_side),
        )
        _call(
            PlaybackActions._play_raw,
            stub,
            {"source": "soundcloud", "yt_id": "sc1", "title": "S"},
        )
        assert spinner.stops == 1

    def test_empty_source_sniffs_soundcloud_url(self):
        src = _FakeSource()
        stub = _make_stub(
            _sources={"soundcloud": src},
            _active_np_widget=MagicMock(return_value=_NPFake()),
        )
        _call(
            PlaybackActions._play_raw,
            stub,
            {"url": "https://soundcloud.com/x/1", "title": "S"},
        )
        stub._resolve_then_play.assert_called_once()

    def test_empty_source_sniffs_youtube_url(self):
        ipc = _FakeIpc()
        src = _FakeSource(play_result=ipc)
        stub = _make_stub(
            _sources={"youtube": src},
            _active_np_widget=MagicMock(return_value=_NPFake()),
        )
        _call(PlaybackActions._play_raw, stub, {"url": "https://youtu.be/abc"})
        stub._sync_finish.assert_called_once_with(ipc)

    def test_empty_source_falls_back_to_self_source(self):
        ipc = _FakeIpc()
        src = _FakeSource(play_result=ipc)
        stub = _make_stub(
            _sources={"radio": src},
            _source="radio",
            _active_np_widget=MagicMock(return_value=_NPFake()),
        )
        _call(PlaybackActions._play_raw, stub, {"url": "http://odd", "title": "T"})
        assert src.calls == [(True, None, None)]

    def test_tv_movies_dispatch(self):
        stub = _make_stub()
        data = {"source": "tv_movies", "tmdb_id": 5}
        _call(PlaybackActions._play_raw, stub, data, start_pos=12.0)
        stub._play_tv_movies.assert_called_once_with(data, 12.0)


class TestPlayNextQueued:
    def test_empty_queue_sets_stopping_and_schedules_clear(self):
        stub = _make_stub()
        _call(PlaybackActions._play_next_queued, stub)
        assert stub._stopping is True
        stub.set_timer.assert_called_once()
        args = stub.set_timer.call_args
        assert args[0][0] == 1.5
        assert args[0][1].__func__ is PlaybackActions._clear_stopping

    def test_non_empty_pops_and_plays(self):
        stub = _make_stub()
        req = MediaRequest(yt_id="abc", title="T")
        stub._playback_queue.add(QueueItem(request=req))
        _call(PlaybackActions._play_next_queued, stub)
        assert stub._stopping is False
        stub._play.assert_called_once_with(req, from_queue=True)
        assert len(stub._playback_queue) == 0


class TestCleanupMpv:
    def test_full_cleanup(self):
        ipc = _FakeIpc()
        spinner = _Timer()
        poll = _Timer()
        np_side = _NPFake()
        stub = _make_stub(
            _play_token=5,
            _resolve_token=7,
            _sc_resolving=True,
            _tv_probing=True,
            _last_icy_title="x",
            _now_playing_data={"yt_id": "abc"},
            _subs_tmpdir="/tmp/subs",
            _stalled_count=9,
            _mpv_ipc=ipc,
            _play_spinner_timer=spinner,
            _np_side=np_side,
            _poll_timer=poll,
        )
        with patch("nyrx.actions.playback.shutil.rmtree") as mock_rmtree:
            _call(PlaybackActions._cleanup_mpv, stub)
        assert stub._play_token == 6
        assert stub._resolve_token == 8
        assert stub._sc_resolving is False
        assert stub._tv_probing is False
        assert stub._last_icy_title == ""
        assert stub._now_playing_data is None
        mock_rmtree.assert_called_once_with("/tmp/subs", ignore_errors=True)
        assert stub._subs_tmpdir is None
        assert ipc.stop_calls == 1
        assert stub._mpv_ipc is None
        assert spinner.stops == 1
        assert stub._play_spinner_timer is None
        np_side.stop_playback.assert_called_once()
        assert stub._np_side is None
        assert poll.stops == 1
        assert stub._poll_timer is None
        stub._sync_np_widget.assert_called_once()
        stub._update_sidebar_content.assert_called_once()
        stub._update_radio_playing_indicator.assert_called_once()
        stub._refresh_queue_modal.assert_called_once()
        stub._update_sc_home_sidebar_class.assert_called_once()

    def test_partial_state_no_crash(self):
        stub = _make_stub(_play_token=1, _resolve_token=1)
        with patch("nyrx.actions.playback.shutil.rmtree") as mock_rmtree:
            _call(PlaybackActions._cleanup_mpv, stub)
        assert stub._play_token == 2
        assert stub._resolve_token == 2
        mock_rmtree.assert_not_called()
        assert stub._np_side is None
        assert stub._poll_timer is None
        assert stub._mpv_ipc is None
        stub._sync_np_widget.assert_called_once()


class TestTvMoviesFinish:
    def test_matching_token_proceeds(self):
        ipc = _FakeIpc()
        stub = _make_stub(_play_token=3)
        _call(PlaybackActions._tv_movies_finish, stub, ipc, "/tmp/subs", 3)
        assert stub._subs_tmpdir == "/tmp/subs"
        assert stub._tv_probing is False
        stub._sync_finish.assert_called_once_with(ipc)
        assert ipc.stop_calls == 0

    def test_stale_token_stops_and_cleans(self):
        ipc = _FakeIpc()
        stub = _make_stub(_play_token=4, _tv_probing=True)
        with patch("nyrx.actions.playback.shutil.rmtree") as mock_rmtree:
            _call(PlaybackActions._tv_movies_finish, stub, ipc, "/tmp/subs", 2)
        assert ipc.stop_calls == 1
        mock_rmtree.assert_called_once_with("/tmp/subs", ignore_errors=True)
        assert stub._tv_probing is False
        stub._sync_finish.assert_not_called()

    def test_stale_token_with_none_ipc_and_subs(self):
        stub = _make_stub(_play_token=4, _tv_probing=True)
        _call(PlaybackActions._tv_movies_finish, stub, None, None, 2)
        assert stub._tv_probing is False
        stub._sync_finish.assert_not_called()


class TestOpenBrowserWorker:
    def test_uses_present_url(self, wrapped):
        worker = wrapped(PlaybackActions._open_browser_worker)
        with patch("nyrx.actions.playback.subprocess.run") as mock_run:
            worker(stub_self(PlaybackActions), {"url": "https://example.com/x"})
        mock_run.assert_called_once_with(
            ["xdg-open", "https://example.com/x"], stderr=subprocess.DEVNULL
        )

    def test_youtube_fallback_url(self, wrapped):
        worker = wrapped(PlaybackActions._open_browser_worker)
        with patch("nyrx.actions.playback.subprocess.run") as mock_run:
            worker(stub_self(PlaybackActions), {"source": "youtube", "yt_id": "abc"})
        mock_run.assert_called_once_with(
            ["xdg-open", "https://www.youtube.com/watch?v=abc"],
            stderr=subprocess.DEVNULL,
        )

    def test_soundcloud_fallback_url(self, wrapped):
        worker = wrapped(PlaybackActions._open_browser_worker)
        with patch("nyrx.actions.playback.subprocess.run") as mock_run:
            worker(stub_self(PlaybackActions), {"source": "soundcloud", "yt_id": "sc1"})
        mock_run.assert_called_once_with(
            ["xdg-open", "https://soundcloud.com/tracks/sc1"], stderr=subprocess.DEVNULL
        )

    def test_tv_movies_fallback_url(self, wrapped):
        worker = wrapped(PlaybackActions._open_browser_worker)
        with patch("nyrx.actions.playback.subprocess.run") as mock_run:
            worker(
                stub_self(PlaybackActions),
                {"source": "tv_movies", "tmdb_id": 42, "media_type": "tv"},
            )
        mock_run.assert_called_once_with(
            ["xdg-open", "https://www.themoviedb.org/tv/42"], stderr=subprocess.DEVNULL
        )

    def test_no_url_no_action(self, wrapped):
        worker = wrapped(PlaybackActions._open_browser_worker)
        with patch("nyrx.actions.playback.subprocess.run") as mock_run:
            worker(stub_self(PlaybackActions), {"source": "youtube"})
        mock_run.assert_not_called()

    def test_subprocess_failure_does_not_raise(self, wrapped):
        worker = wrapped(PlaybackActions._open_browser_worker)
        with (
            patch(
                "nyrx.actions.playback.subprocess.run", side_effect=FileNotFoundError
            ),
            patch("nyrx.actions.playback.logger.debug") as mock_log,
        ):
            worker(stub_self(PlaybackActions), {"url": "https://example.com/x"})
        mock_log.assert_called_once()


class TestGetFocusedTrack:
    def test_tv_chip_normalized(self):
        chip = stub_self(
            TVChip, data={"title": "Show", "tmdb_id": "42", "poster": "/abc.jpg"}
        )
        stub = _make_stub(focused=chip)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data["yt_id"] == "tmdb_42"
        assert data["source"] == "tv_movies"
        assert data["media_type"] == "tv"
        assert data["thumbnail_url"] == "https://image.tmdb.org/t/p/w342/abc.jpg"

    def test_tv_chip_existing_fields_kept(self):
        chip = stub_self(
            TVChip,
            data={
                "title": "S",
                "yt_id": "abc",
                "source": "youtube",
                "media_type": "movie",
                "thumbnail_url": "http://x",
            },
        )
        stub = _make_stub(focused=chip)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data["yt_id"] == "abc"
        assert data["source"] == "youtube"
        assert data["media_type"] == "movie"
        assert data["thumbnail_url"] == "http://x"

    def test_watchlist_bookmark(self):
        wl = SimpleNamespace(
            focused_bookmark=MagicMock(return_value={"title": "M", "tmdb_id": "7"})
        )
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=wl, focused=None)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data["source"] == "tv_movies"
        assert data["yt_id"] == "tmdb_7"

    def test_artist_profile_track(self):
        dt = SimpleNamespace(
            cursor_coordinate=(0, 0),
            row_count=1,
            coordinate_to_cell_key=MagicMock(
                return_value=SimpleNamespace(row_key=SimpleNamespace(value="r0"))
            ),
        )
        ap = SimpleNamespace(
            query_one=MagicMock(return_value=dt),
            _track_data_map={"r0": {"title": "AP Track"}},
        )
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap, focused=dt)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "AP Track"}

    def test_liked_track(self):
        ls = SimpleNamespace(
            focused_track=MagicMock(return_value={"title": "L", "yt_id": "l1"})
        )
        stub = _make_stub(_in_liked=True, _w_liked_screen=ls, focused=None)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "L", "yt_id": "l1"}

    def test_following_track(self):
        item = stub_self(FeedTrackItem, data={"title": "F", "yt_id": "f1"})
        cl = SimpleNamespace(highlighted_child=item)
        stub = _make_stub(_in_following=True, _w_fs_center_list=cl, focused=None)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "F", "yt_id": "f1"}

    def test_artist_profile_missing_screen_falls_through(self):
        ri = stub_self(ResultItem, data={"title": "R", "yt_id": "r1"})
        lv = SimpleNamespace(index=0, children=[ri])
        stub = _make_stub(
            _in_artist_profile=True,
            _w_artist_profile=None,
            focused=None,
            _w_results_list=lv,
        )
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "R", "yt_id": "r1"}

    def test_liked_missing_screen_falls_through(self):
        ri = stub_self(ResultItem, data={"title": "R", "yt_id": "r1"})
        lv = SimpleNamespace(index=0, children=[ri])
        stub = _make_stub(
            _in_liked=True, _w_liked_screen=None, focused=None, _w_results_list=lv
        )
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "R", "yt_id": "r1"}

    def test_following_missing_list_falls_through(self):
        ri = stub_self(ResultItem, data={"title": "R", "yt_id": "r1"})
        lv = SimpleNamespace(index=0, children=[ri])
        stub = _make_stub(
            _in_following=True, _w_fs_center_list=None, focused=None, _w_results_list=lv
        )
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "R", "yt_id": "r1"}

    def test_falls_back_to_current_item(self):
        ri = stub_self(ResultItem, data={"title": "R", "yt_id": "r1"})
        lv = SimpleNamespace(index=0, children=[ri])
        stub = _make_stub(_w_results_list=lv, focused=None)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "R", "yt_id": "r1"}

    def test_falls_back_to_now_playing_data(self):
        np_w = SimpleNamespace(display=True)
        stub = _make_stub(
            focused=np_w,
            _np_widgets={"youtube": np_w},
            _now_playing_data={"title": "NP", "yt_id": "n1"},
        )
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data == {"title": "NP", "yt_id": "n1"}

    def test_normalizes_legacy_field_names(self):
        ri = stub_self(
            ResultItem, data={"track_id": "t1", "view_count": "5", "like_count": "9"}
        )
        lv = SimpleNamespace(index=0, children=[ri])
        stub = _make_stub(focused=None, _w_results_list=lv)
        data = _call(PlaybackActions._get_focused_track, stub)
        assert data["yt_id"] == "t1"
        assert data["views"] == "5"
        assert data["likes_count"] == "9"

    def test_nothing_focused_returns_none(self):
        stub = _make_stub(focused=None, _now_playing_data=None)
        assert _call(PlaybackActions._get_focused_track, stub) is None


class TestCurrentItem:
    def test_no_list_returns_none(self):
        stub = _make_stub(_w_results_list=None)
        assert _call(PlaybackActions._current_item, stub) is None

    def test_valid_index_returns_item(self):
        ri = stub_self(ResultItem, data={})
        lv = SimpleNamespace(index=1, children=["junk", ri])
        stub = _make_stub(_w_results_list=lv)
        assert _call(PlaybackActions._current_item, stub) is ri

    def test_index_out_of_range_returns_none(self):
        lv = SimpleNamespace(index=5, children=[stub_self(ResultItem)])
        stub = _make_stub(_w_results_list=lv)
        assert _call(PlaybackActions._current_item, stub) is None

    def test_index_none_returns_none(self):
        lv = SimpleNamespace(index=None, children=[stub_self(ResultItem)])
        stub = _make_stub(_w_results_list=lv)
        assert _call(PlaybackActions._current_item, stub) is None

    def test_non_result_item_returns_none(self):
        lv = SimpleNamespace(index=0, children=["not-a-result"])
        stub = _make_stub(_w_results_list=lv)
        assert _call(PlaybackActions._current_item, stub) is None


class TestStartPlayback:
    def test_resets_state_and_starts_np(self, patch_clock):
        patch_clock.set_monotonic(42.0)
        np_side = _NPFake()
        spinner = _Timer()
        req = MediaRequest(yt_id="abc", title="T")
        stub = _make_stub(
            _play_token=1,
            _play_spinner_timer=spinner,
            _active_np_widget=MagicMock(return_value=np_side),
        )
        _call(PlaybackActions._start_playback, stub, {"yt_id": "abc"}, req)
        assert stub._play_token == 2
        assert stub._now_playing_data == {"yt_id": "abc"}
        assert stub._last_playback_pos == 0
        assert stub._stalled_count == 0
        assert stub._stream_ready is False
        assert stub._last_known_pos == -1
        assert stub._play_start_time == 42.0
        assert stub._stopping is False
        assert stub._np_side is np_side
        np_side.start_playback.assert_called_once_with(req)
        assert spinner.stops == 1
        args = stub.set_interval.call_args
        assert args[0][0] == 0.08
        assert args[0][1].__func__ is PlaybackActions._tick_play_spinner
        stub._update_sidebar_content.assert_called_once()
        stub._refresh_queue_modal.assert_called_once()

    def test_no_np_side_no_crash(self, patch_clock):
        patch_clock.set_monotonic(1.0)
        stub = _make_stub(_active_np_widget=MagicMock(return_value=None))
        _call(
            PlaybackActions._start_playback,
            stub,
            {},
            MediaRequest(yt_id="a", title="T"),
        )
        assert stub._np_side is None


class TestFinishPlayback:
    def test_switches_np_side_and_stops_old(self):
        old_np = _NPFake()
        new_np = _NPFake()
        stub = _make_stub(
            _np_side=old_np,
            _active_np_widget=MagicMock(return_value=new_np),
            _play_spinner_timer=_Timer(),
            _poll_timer=None,
        )
        _call(PlaybackActions._finish_playback, stub, _FakeIpc())
        assert stub._mpv_ipc is not None
        assert stub._np_side is new_np
        old_np.stop_playback.assert_called_once()
        stub._sync_np_widget.assert_called_once()
        stub._update_sc_home_sidebar_class.assert_called_once()
        stub._apply_sidebar.assert_called_once_with(True)

    def test_same_np_side_not_stopped(self):
        np_side = _NPFake()
        stub = _make_stub(
            _np_side=np_side, _active_np_widget=MagicMock(return_value=np_side)
        )
        _call(PlaybackActions._finish_playback, stub, _FakeIpc())
        np_side.stop_playback.assert_not_called()

    def test_stops_spinner_and_starts_poll_timer(self):
        spinner = _Timer()
        stub = _make_stub(_play_spinner_timer=spinner, _poll_timer=None)
        _call(PlaybackActions._finish_playback, stub, _FakeIpc())
        assert spinner.stops == 1
        assert stub._play_spinner_timer is None
        args = stub.set_interval.call_args
        assert args[0][0] == 0.04
        assert args[0][1].__func__ is PlaybackActions._poll_mpv

    def test_keeps_existing_poll_timer(self):
        poll = _Timer()
        stub = _make_stub(_poll_timer=poll)
        _call(PlaybackActions._finish_playback, stub, _FakeIpc())
        stub.set_interval.assert_not_called()


class TestSyncFinish:
    def test_restarts_spinner_after_finish(self):
        stub = _make_stub()
        ipc = _FakeIpc()
        _call(PlaybackActions._sync_finish, stub, ipc)
        stub._finish_playback.assert_called_once_with(ipc)
        args = stub.set_interval.call_args
        assert args[0][0] == 0.08
        assert args[0][1].__func__ is PlaybackActions._tick_play_spinner


class TestStopPlayback:
    def test_sets_stopping_and_cleans(self):
        stub = _make_stub()
        _call(PlaybackActions._stop_playback, stub)
        assert stub._last_playing_data is None
        assert stub._last_playback_pos == 0
        assert stub._stopping is True
        stub._cleanup_mpv.assert_called_once()
        stub.set_timer.assert_called_once()
        args = stub.set_timer.call_args
        assert args[0][0] == 1.5
        assert args[0][1].__func__ is PlaybackActions._clear_stopping


class TestSkipPlayback:
    def test_frozen_queue_returns_early(self):
        stub = _make_stub(_queue_frozen=True)
        _call(PlaybackActions._skip_playback, stub)
        assert stub._stopping is True
        stub.set_timer.assert_called_once()
        stub._play_next_queued.assert_not_called()

    def test_advances_and_syncs(self):
        stub = _make_stub(_queue_frozen=False, _syncing=False)
        with patch("nyrx.actions.playback.sync_from_tracker") as mock_sync:
            _call(PlaybackActions._skip_playback, stub)
        stub._play_next_queued.assert_called_once()
        mock_sync.assert_called_once()
        assert stub._syncing is False
        stub._refresh_watchlist_statuses.assert_called_once()

    def test_skips_sync_when_already_syncing(self):
        stub = _make_stub(_queue_frozen=False, _syncing=True)
        with patch("nyrx.actions.playback.sync_from_tracker") as mock_sync:
            _call(PlaybackActions._skip_playback, stub)
        stub._play_next_queued.assert_called_once()
        mock_sync.assert_not_called()


class TestClearStopping:
    def test_clears_stopping_flag(self):
        stub = _make_stub(_stopping=True)
        _call(PlaybackActions._clear_stopping, stub)
        assert stub._stopping is False


class TestRefreshWatchlistStatuses:
    def test_refresh_when_in_watchlist_and_screen(self):
        screen = SimpleNamespace(refresh_statuses=MagicMock())
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=screen)
        _call(PlaybackActions._refresh_watchlist_statuses, stub)
        screen.refresh_statuses.assert_called_once()

    def test_skips_when_not_in_watchlist(self):
        stub = _make_stub(_in_watchlist=False, _w_watchlist_screen=SimpleNamespace())
        _call(PlaybackActions._refresh_watchlist_statuses, stub)

    def test_swallows_refresh_error(self):
        screen = SimpleNamespace(
            refresh_statuses=MagicMock(side_effect=RuntimeError("boom"))
        )
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=screen)
        _call(PlaybackActions._refresh_watchlist_statuses, stub)

    def test_refreshes_tv_series_when_mounted(self):
        series = SimpleNamespace(refresh_episode_statuses=MagicMock())
        stub = _make_stub(_in_watchlist=False, _w_tv_series=series)
        _call(PlaybackActions._refresh_watchlist_statuses, stub)
        series.refresh_episode_statuses.assert_called_once()

    def test_swallows_tv_series_refresh_error(self):
        series = SimpleNamespace(
            refresh_episode_statuses=MagicMock(side_effect=RuntimeError("boom"))
        )
        stub = _make_stub(_in_watchlist=False, _w_tv_series=series)
        _call(PlaybackActions._refresh_watchlist_statuses, stub)


class TestPlayTvMovies:
    def test_launches_tv_worker(self):
        stub = _make_stub(_play_token=7)
        data = {"source": "tv_movies", "tmdb_id": 5, "title": "M"}
        _call(PlaybackActions._play_tv_movies, stub, data, start_pos=3.0)
        assert stub._tv_probing is True
        assert data["yt_id"] == "tmdb_5"
        stub._start_playback.assert_called_once()
        stub._sync_np_widget.assert_called_once()
        stub._render_focus_indicators.assert_called_once()
        stub._play_tv_worker.assert_called_once_with(data, 3.0, 7)


class TestPlayTvWorker:
    def test_success_launches_and_reports_ready(self, wrapped):
        worker = wrapped(PlaybackActions._play_tv_worker)
        np_side = SimpleNamespace(set_status=MagicMock())
        tv_src = SimpleNamespace(
            play_params=MagicMock(
                return_value={"url": "http://stream", "_subs_tmpdir": "/tmp/subs"}
            )
        )
        stub = _make_stub(_sources={"tv_movies": tv_src}, _np_side=np_side)
        ipc = _FakeIpc()
        data = {"tmdb_id": 5, "channel": "C"}
        with patch(
            "nyrx.actions.playback.play_video_async", return_value=ipc
        ) as mock_pva:
            worker(stub, data, 0.0, 3)
        mock_pva.assert_called_once_with(
            url="http://stream", source="tv_movies", channel="C"
        )
        stub.call_from_thread.assert_any_call(np_side.set_status, "probing…")
        stub.call_from_thread.assert_any_call(np_side.set_status, "starting stream…")
        stub.call_from_thread.assert_any_call(
            stub._play_tv_ready, data, ipc, "/tmp/subs", 3
        )

    def test_no_url_reports_failure(self, wrapped):
        worker = wrapped(PlaybackActions._play_tv_worker)
        np_side = SimpleNamespace(set_status=MagicMock())
        tv_src = SimpleNamespace(
            play_params=MagicMock(return_value={"_subs_tmpdir": "/tmp/subs"})
        )
        stub = _make_stub(_sources={"tv_movies": tv_src}, _np_side=np_side)
        data = {"tmdb_id": 5}
        with patch("nyrx.actions.playback.play_video_async") as mock_pva:
            worker(stub, data, None, 3)
        mock_pva.assert_not_called()
        stub.call_from_thread.assert_any_call(
            stub._play_tv_failed, "No stream URL returned", "/tmp/subs", 3
        )

    def test_exception_reports_failure(self, wrapped):
        worker = wrapped(PlaybackActions._play_tv_worker)
        np_side = SimpleNamespace(set_status=MagicMock())
        tv_src = SimpleNamespace(
            play_params=MagicMock(side_effect=RuntimeError("boom"))
        )
        stub = _make_stub(_sources={"tv_movies": tv_src}, _np_side=np_side)
        data = {"tmdb_id": 5}
        worker(stub, data, None, 3)
        stub.call_from_thread.assert_any_call(stub._play_tv_failed, "boom", None, 3)


class TestPlayTvReady:
    def test_ipc_routes_to_tv_movies_finish(self):
        stub = _make_stub()
        ipc = _FakeIpc()
        _call(PlaybackActions._play_tv_ready, stub, {"tmdb_id": 5}, ipc, "/tmp/subs", 3)
        stub._tv_movies_finish.assert_called_once_with(ipc, "/tmp/subs", 3)

    def test_no_ipc_reports_failure(self):
        stub = _make_stub()
        _call(
            PlaybackActions._play_tv_ready, stub, {"tmdb_id": 5}, None, "/tmp/subs", 3
        )
        stub._play_tv_failed.assert_called_once_with(
            "mpv failed to start", "/tmp/subs", 3
        )


class TestPlayTvFailed:
    def test_stale_token_returns_early(self):
        stub = _make_stub(_play_token=4, _tv_probing=True)
        with patch("nyrx.actions.playback.shutil.rmtree") as mock_rmtree:
            _call(PlaybackActions._play_tv_failed, stub, "err", "/tmp/subs", 2)
        mock_rmtree.assert_called_once_with("/tmp/subs", ignore_errors=True)
        assert stub._tv_probing is False
        stub.notify.assert_not_called()
        stub._play_next_queued.assert_not_called()

    def test_current_token_cleans_and_notifies(self):
        np_side = _NPFake()
        stub = _make_stub(
            _play_token=4,
            _tv_probing=True,
            _now_playing_data={"x": 1},
            _subs_tmpdir="/tmp/old",
            _play_spinner_timer=_Timer(),
            _np_side=np_side,
        )
        with patch("nyrx.actions.playback.shutil.rmtree") as mock_rmtree:
            _call(PlaybackActions._play_tv_failed, stub, "stream down", "/tmp/subs", 4)
        assert stub._tv_probing is False
        assert stub._now_playing_data is None
        assert stub._subs_tmpdir is None
        assert stub._np_side is None
        np_side.stop_playback.assert_called_once()
        mock_rmtree.assert_called_once_with("/tmp/subs", ignore_errors=True)
        stub._update_sidebar_content.assert_called_once()
        stub._update_sidebar_context.assert_called_once()
        stub._sync_np_widget.assert_called_once()
        stub.notify.assert_called_once_with(
            "Playback failed: stream down",
            timeout=TIMEOUT_ERROR,
            severity=SEVERITY_ERROR,
            title="Error",
        )
        stub._play_next_queued.assert_called_once()

    def test_no_subs_no_spinner_no_crash(self):
        stub = _make_stub(_play_token=4, _tv_probing=True)
        _call(PlaybackActions._play_tv_failed, stub, "boom", None, 4)
        stub.notify.assert_called_once_with(
            "Playback failed: boom",
            timeout=TIMEOUT_ERROR,
            severity=SEVERITY_ERROR,
            title="Error",
        )
        stub._play_next_queued.assert_called_once()


class TestResolveThenPlay:
    def test_forwards_resolved_result(self, wrapped):
        worker = wrapped(PlaybackActions._resolve_then_play)
        ipc = _FakeIpc()
        resolved = {"waveform_samples": [1]}
        src = _FakeSource(play_result=(ipc, resolved))
        stub = _make_stub()
        worker(stub, src, {"yt_id": "sc1"}, 1)
        stub.call_from_thread.assert_called_once()
        args = stub.call_from_thread.call_args
        assert args[0][0] is stub._on_sc_resolved
        assert args[0][1] == {"yt_id": "sc1"}
        assert args[0][2] is ipc
        assert args[0][3] == resolved
        assert args[0][4] is not None  # rendered waveform
        assert args[0][5] == 1

    def test_play_failure_routes_to_failure_handler(self, wrapped):
        worker = wrapped(PlaybackActions._resolve_then_play)

        class _RaisingSource:
            def play(self, data, audio_only=False, ytdl_format=None, start_pos=None):
                raise RuntimeError("boom")

        stub = _make_stub()
        worker(stub, _RaisingSource(), {"yt_id": "sc1"}, 1)
        args = stub.call_from_thread.call_args[0]
        assert args[0] is stub._on_sc_resolved
        assert args[1] == {"yt_id": "sc1"}
        assert args[2] is None
        assert args[3] == {}
        assert args[4] is None
        assert args[5] == 1

    def test_waveform_failure_falls_back_not_crash(self, wrapped):
        worker = wrapped(PlaybackActions._resolve_then_play)
        ipc = _FakeIpc()
        src = _FakeSource(play_result=(ipc, {"waveform_samples": [1]}))
        stub = _make_stub()
        with patch(
            "nyrx.helpers.build_waveform", side_effect=ValueError("bad samples")
        ):
            worker(stub, src, {"yt_id": "sc1"}, 1)
        args = stub.call_from_thread.call_args[0]
        assert args[0] is stub._on_sc_resolved
        assert args[2] is ipc
        assert args[4] is None


class TestOnScResolved:
    def test_stale_token_stops_ipc(self):
        ipc = _FakeIpc()
        stub = _make_stub(_resolve_token=3)
        _call(PlaybackActions._on_sc_resolved, stub, {}, ipc, {}, None, 1)
        assert ipc.stop_calls == 1

    def test_non_sc_np_stops_ipc(self):
        ipc = _FakeIpc()
        stub = _make_stub(
            _resolve_token=1, _active_np_widget=MagicMock(return_value=_NPFake())
        )
        _call(PlaybackActions._on_sc_resolved, stub, {}, ipc, {}, None, 1)
        assert ipc.stop_calls == 1

    def test_np_not_connecting_stops_ipc(self):
        ipc = _FakeIpc()
        np_side = _sc_np(state=ScState.IDLE)
        stub = _make_stub(
            _resolve_token=1, _active_np_widget=MagicMock(return_value=np_side)
        )
        _call(PlaybackActions._on_sc_resolved, stub, {}, ipc, {}, None, 1)
        assert ipc.stop_calls == 1

    def test_ipc_with_waveform_shows_visualizer(self):
        np_side = _sc_np()
        ipc = _FakeIpc()
        stub = _make_stub(
            _resolve_token=1, _active_np_widget=MagicMock(return_value=np_side)
        )
        data = {"yt_id": "sc1"}
        resolved = {"waveform_samples": [1]}
        rendered = ([0.0], [[], [], [], []])
        _call(PlaybackActions._on_sc_resolved, stub, data, ipc, resolved, rendered, 1)
        np_side.show_visualizer.assert_called_once_with(data, resolved, rendered)
        assert stub._sc_resolving is False
        stub._sync_finish.assert_called_once_with(ipc)
        stub._sync_sc_np_metadata.assert_called_once_with()

    def test_ipc_without_waveform_shows_fallback(self):
        np_side = _sc_np()
        ipc = _FakeIpc()
        stub = _make_stub(
            _resolve_token=1, _active_np_widget=MagicMock(return_value=np_side)
        )
        data = {"yt_id": "sc1"}
        _call(PlaybackActions._on_sc_resolved, stub, data, ipc, {}, None, 1)
        np_side.show_fallback.assert_called_once_with(data, {})
        stub._sync_sc_np_metadata.assert_called_once_with()

    def test_no_ipc_advances_queue(self):
        np_side = _sc_np()
        stub = _make_stub(
            _resolve_token=1, _active_np_widget=MagicMock(return_value=np_side)
        )
        _call(
            PlaybackActions._on_sc_resolved, stub, {"yt_id": "sc1"}, None, {}, None, 1
        )
        np_side.stop_playback.assert_called_once()
        assert stub._now_playing_data is None
        stub.notify.assert_called_once_with(
            "Playback failed to start",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub._play_next_queued.assert_called_once()
        stub._update_sidebar_content.assert_called_once()
        stub._update_sidebar_context.assert_called_once()
        stub._sync_sc_np_metadata.assert_not_called()


class TestIsPlaying:
    def test_mpv_running(self):
        stub = _make_stub(_mpv_ipc=_FakeIpc(running=True))
        assert PlaybackActions._is_playing.fget(stub) is True

    def test_mpv_stopped(self):
        stub = _make_stub(_mpv_ipc=_FakeIpc(running=False))
        assert PlaybackActions._is_playing.fget(stub) is False

    def test_sc_resolving(self):
        stub = _make_stub(_mpv_ipc=None, _sc_resolving=True)
        assert PlaybackActions._is_playing.fget(stub) is True

    def test_tv_probing(self):
        stub = _make_stub(_mpv_ipc=None, _tv_probing=True)
        assert PlaybackActions._is_playing.fget(stub) is True

    def test_not_playing(self):
        stub = _make_stub(_mpv_ipc=None)
        assert PlaybackActions._is_playing.fget(stub) is False


class TestGetQualityFormat:
    def test_known_label(self):
        stub = _make_stub(_quality="1080p")
        assert (
            PlaybackActions._get_quality_format(stub)
            == "bestvideo[height<=1080]+bestaudio/best"
        )

    def test_best_label(self):
        stub = _make_stub(_quality="Best")
        assert PlaybackActions._get_quality_format(stub) == "bestvideo+bestaudio/best"

    def test_unknown_label(self):
        stub = _make_stub(_quality="144p")
        assert PlaybackActions._get_quality_format(stub) is None


class TestGetQualityHeight:
    def test_known_label(self):
        stub = _make_stub(_quality="480p")
        assert PlaybackActions._get_quality_height(stub) == 480

    def test_unknown_label(self):
        stub = _make_stub(_quality="144p")
        assert PlaybackActions._get_quality_height(stub) is None


class TestUpdateQueueIndicator:
    def test_no_widget_returns(self):
        stub = _make_stub(_w_sidebar_queue=None)
        _call(PlaybackActions._update_queue_indicator, stub)

    def test_offline_line(self):
        q = SimpleNamespace(update=MagicMock(), display=True)
        stub = _make_stub(_w_sidebar_queue=q, _online=False)
        _call(PlaybackActions._update_queue_indicator, stub)
        text = q.update.call_args[0][0]
        assert text.plain == "\u258c ! No internet connection. retrying..."
        assert text.spans[0].style == "red"
        assert q.display is True

    def test_back_online_line(self):
        q = SimpleNamespace(update=MagicMock(), display=True)
        stub = _make_stub(_w_sidebar_queue=q, _online=True, _show_back_online=True)
        _call(PlaybackActions._update_queue_indicator, stub)
        text = q.update.call_args[0][0]
        assert text.plain == "\u258c \u2713 Back online!"
        assert text.spans[0].style == "green"

    def test_next_queued_line(self):
        q = SimpleNamespace(update=MagicMock(), display=True)
        stub = _make_stub(_w_sidebar_queue=q, _online=True)
        stub._playback_queue.add(
            QueueItem(request=MediaRequest(yt_id="a", title="Next One"))
        )
        _call(PlaybackActions._update_queue_indicator, stub)
        assert "Next: Next One" in q.update.call_args[0][0]

    def test_download_pending_line(self):
        q = SimpleNamespace(update=MagicMock(), display=True)
        stub = _make_stub(_w_sidebar_queue=q, _online=True, _download_pending=[1, 2, 3])
        _call(PlaybackActions._update_queue_indicator, stub)
        assert "+3 download queued" in q.update.call_args[0][0]

    def test_no_lines_hides_widget(self):
        q = SimpleNamespace(update=MagicMock(), display=True)
        stub = _make_stub(_w_sidebar_queue=q, _online=True, _show_back_online=False)
        _call(PlaybackActions._update_queue_indicator, stub)
        q.update.assert_called_once_with("")
        assert q.display is False


class TestSyncNpWidget:
    def test_updates_download_widget(self):
        dl = SimpleNamespace(
            update_progress=MagicMock(), _dl_select_mode=False, display=True
        )
        stub = _make_stub(_w_download=dl, _download_state={"x": 1})
        _call(PlaybackActions._sync_np_widget, stub)
        dl.update_progress.assert_called_once_with({"x": 1})
        assert dl.display is True

    def test_select_mode_keeps_display(self):
        dl = SimpleNamespace(
            update_progress=MagicMock(), _dl_select_mode=True, display=False
        )
        stub = _make_stub(_w_download=dl, _download_state={"x": 1})
        _call(PlaybackActions._sync_np_widget, stub)
        dl.update_progress.assert_called_once_with({"x": 1})
        assert dl.display is False

    def test_no_download_widget(self):
        stub = _make_stub(_w_download=None)
        _call(PlaybackActions._sync_np_widget, stub)
        stub._update_queue_indicator.assert_called_once()
        stub._update_mode_indicator.assert_called_once()
        stub._apply_sidebar.assert_called_once_with(True)

    def test_exception_swallowed(self):
        stub = _make_stub(
            _update_mode_indicator=MagicMock(side_effect=RuntimeError("boom"))
        )
        _call(PlaybackActions._sync_np_widget, stub)


class TestRefreshQueueModal:
    def test_queue_modal_rebuilds(self):
        screen = stub_self(QueueModal, _rebuild_lists=MagicMock())
        stub = _make_stub(screen=screen)
        _call(PlaybackActions._refresh_queue_modal, stub)
        screen._rebuild_lists.assert_called_once()

    def test_non_queue_modal_skipped(self):
        stub = _make_stub()
        _call(PlaybackActions._refresh_queue_modal, stub)

    def test_rebuild_error_swallowed(self):
        screen = stub_self(
            QueueModal, _rebuild_lists=MagicMock(side_effect=RuntimeError("boom"))
        )
        stub = _make_stub(screen=screen)
        _call(PlaybackActions._refresh_queue_modal, stub)


class TestTickPlaySpinner:
    def test_refreshes_when_spinner_shown(self):
        np_side = _NPFake()
        np_side.should_show_spinner = MagicMock(return_value=True)
        stub = _make_stub(_np_side=np_side)
        _call(PlaybackActions._tick_play_spinner, stub)
        np_side._refresh.assert_called_once()

    def test_skips_when_hidden(self):
        np_side = _NPFake()
        np_side.should_show_spinner = MagicMock(return_value=False)
        stub = _make_stub(_np_side=np_side)
        _call(PlaybackActions._tick_play_spinner, stub)
        np_side._refresh.assert_not_called()

    def test_no_np_side(self):
        stub = _make_stub(_np_side=None)
        _call(PlaybackActions._tick_play_spinner, stub)
