# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/soundcloud.py`` (5B.7: full-surface pure-unit).

Covers the doc-listed 5B.7 families (``_check_stale_caches``,
``_is_artist_fully_cached``, ``_purge_unlike_buffer``) plus the BUG-9
``_cache_worker`` regression and the full following / feed / station / follow /
like / sync surface.  Pure unit: bare ``SimpleNamespace`` stubs + widget fakes,
``@work`` methods driven via ``__wrapped__``, never boots ``MediaApp``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, call, patch

import pytest
from textual.widgets import ContentSwitcher

from nyrx.actions.soundcloud import SoundCloudActions
from nyrx.config import (
    SEVERITY_ERROR,
    SEVERITY_WARNING,
    TIMEOUT_CONFIRM,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
)
from nyrx.modes import Source
from nyrx.sources.soundcloud.cache import CACHE_QUEUE


@pytest.fixture(autouse=True)
def _clear_queue() -> None:
    CACHE_QUEUE.clear()


# ── Widget fakes ────────────────────────────────────────────────────────────


class _FakeWidget:
    """Minimal lookalike for the bits of a Textual widget the actions touch."""

    def __init__(self) -> None:
        self.display = True
        self.disabled = False
        self.value = ""
        self.children: list = []
        self.focused = False
        self.updated: list = []
        self._classes: set = set()

    def focus(self) -> None:
        self.focused = True

    def clear(self) -> None:
        self.children = []

    def append(self, item: object) -> None:
        self.children.append(item)

    def update(self, text: str = "") -> None:
        self.updated.append(text)

    def add_class(self, name: str) -> None:
        self._classes.add(name)

    def remove_class(self, name: str) -> None:
        self._classes.discard(name)


class _FakeScHome:
    """``#sc-home`` lookalike: selector dispatch for the widgets it owns."""

    def __init__(self) -> None:
        self.display = True
        self._q = {
            "#sch-center": _FakeWidget(),
            "#fs-left": _FakeWidget(),
            "#fs-center": _FakeWidget(),
            "#fs-search": _FakeWidget(),
        }
        self.calls: list = []

    def query_one(self, selector: str, *_args):
        return self._q[selector]

    def set_following_artists(self, artists: list[dict]) -> None:
        self.calls.append(("set_following_artists", artists))

    def _apply_filter(self, text: str) -> None:
        self.calls.append(("apply_filter", text))

    def _populate_following(self, followed: list[dict]) -> None:
        self.calls.append(("populate_following", followed))

    def _populate_liked(self, liked: list[dict]) -> None:
        self.calls.append(("populate_liked", liked))


class _FakeList:
    """List-widget lookalike with ``children`` / ``clear`` / ``append`` / focus."""

    def __init__(self, children: list | None = None) -> None:
        self.children = children or []
        self.display = True
        self.focused = False
        self.has_focus = False
        self.index: object = None
        self.highlighted_child: object = None

    def focus(self) -> None:
        self.focused = True
        self.has_focus = True

    def clear(self) -> None:
        self.children = []

    def append(self, item: object) -> None:
        self.children.append(item)

    def query(self, *_args):
        return self.children


class _FakeListItem:
    def __init__(self, artist_id: str = "", label: str = "") -> None:
        self.artist_id = artist_id
        self._label = _FakeWidget()
        self._label.updated.append(label)

    def query_one(self, *_args):
        return self._label


class _FakeFeedTrack:
    """Substitute for the patched ``FeedTrackItem`` (covers isinstance gates)."""

    def __init__(
        self, data: dict | None = None, following: bool = False, liked: bool = False
    ) -> None:
        self.data = data or {}
        self.following = following
        self.liked = liked

    def set_liked(self, value: bool) -> None:
        self.liked = value

    def set_following(self, value: bool) -> None:
        self.following = value


class _FakeResultItem:
    def __init__(self, data: dict | None = None) -> None:
        self.data = data or {}
        self.following = False
        self.liked = False

    def set_liked(self, value: bool) -> None:
        self.liked = value

    def set_following(self, value: bool) -> None:
        self.following = value


class _FakeDataTable:
    """Lookalike for the DataTable the following list / track tables became."""

    def __init__(self) -> None:
        self.cursor_coordinate = None
        self.row_count = 0
        self.calls: list = []
        self.classes: set = set()
        self.rows: dict = {}
        self.row_keys: list = []
        self.focused = False
        self.has_focus = False

    def coordinate_to_cell_key(self, _coord):
        return SimpleNamespace(row_key=SimpleNamespace(value="key"))

    def update_cell(self, *args, **kwargs) -> None:
        self.calls.append(("update_cell", args, kwargs))

    def update(self, text: str) -> None:
        self.calls.append(("update", text))

    def add_class(self, name: str) -> None:
        self.classes.add(name)

    def remove_class(self, name: str) -> None:
        self.classes.discard(name)

    def add_row(self, *cells, key=None) -> None:
        self.calls.append(("add_row", key))
        self.row_keys.append(key)
        self.rows[key] = len(self.row_keys) - 1
        self.row_count = len(self.row_keys)

    def clear(self) -> None:
        self.row_keys = []
        self.rows = {}
        self.row_count = 0

    def focus(self) -> None:
        self.focused = True
        self.has_focus = True


class _FakeArtistProfile:
    def __init__(self) -> None:
        self.display = True
        self._collections: list = []
        self._profile: dict | None = None
        self._track_data_map: dict = {}
        self._followed_set: set = set()
        self._liked_ids: set = set()
        self.dt = _FakeDataTable()
        self.populate_args: object = None

    def query_one(self, _selector, *_args):
        return self.dt

    def populate(self, artist_id, liked_ids=None, followed_set=None) -> None:
        self.populate_args = (artist_id, liked_ids, followed_set)


class _FakeLikedScreen:
    def __init__(self) -> None:
        self.display = True
        self._buffer_ids: set = set()
        self._filtered: list = []
        self._followed_set: set = set()
        self.dt = _FakeDataTable()
        self._track: dict | None = None

    def query_one(self, _selector, *_args):
        return self.dt

    def focused_track(self):
        return self._track

    def populate(self, *_args) -> None:
        pass

    def update_tracks(self, *_args) -> None:
        pass

    def _rebuild_table(self, *_args) -> None:
        pass

    def set_loading(self) -> None:
        pass


# ── Stub builder ────────────────────────────────────────────────────────────


def _make_stub(**attrs) -> SimpleNamespace:
    stub = SimpleNamespace(
        _w_sc_home=None,
        _w_following_area=None,
        _w_artist_profile=None,
        _w_main_content=None,
        _w_fs_left_list=None,
        _w_fs_center_list=None,
        _w_fs_center_header=None,
        _w_liked_screen=None,
        _sc_followed=[],
        _sc_liked=[],
        _unlike_buffer={},
        _loading_artists={},
        _fs_spinner_timer=None,
        _fs_spinner_frame=0,
        _in_following=False,
        _in_artist_profile=False,
        _in_liked=False,
        _source=Source.YOUTUBE,
        _np_focused=False,
        _sc_np_focused=False,
        _online=True,
        _feed=[],
        _feed_populated=False,
        _regen_in_progress=False,
        _station_in_progress=False,
        _now_playing_data=None,
        _np_widgets={},
        _sc_api_blocked=False,
        screen=SimpleNamespace(has_class=lambda _cls: False),
        call_from_thread=MagicMock(),
        notify=MagicMock(),
        set_timer=MagicMock(),
        set_interval=MagicMock(),
        query_one=MagicMock(),
        push_screen=MagicMock(),
        _save_sc_home_focus=MagicMock(),
        _restore_sc_home_focus=MagicMock(),
        _apply_sidebar=MagicMock(),
        _update_sidebar_content=MagicMock(),
        _update_sidebar_context=MagicMock(),
        _render_focus_indicators=MagicMock(),
        _refresh_fs_spinners=MagicMock(),
        _set_feed_loading=MagicMock(),
        _regen_feed_worker=MagicMock(),
        _populate_center_feed=MagicMock(),
        _refresh_feed_liked_indicators=MagicMock(),
        _play=MagicMock(),
        _playback_queue=SimpleNamespace(add=MagicMock(), remove_by_id=MagicMock()),
        _sync_np_widget=MagicMock(),
        _refresh_queue_modal=MagicMock(),
        _get_focused_track=MagicMock(),
        _current_item=MagicMock(),
        _cache_worker=MagicMock(),
        _sync_liked_worker=MagicMock(),
        _station_worker=MagicMock(),
        _on_station_result=MagicMock(),
        _on_regen_complete=MagicMock(),
        _on_regen_error=MagicMock(),
        _finish_sync=MagicMock(),
        _finish_sync_resolve_error=MagicMock(),
        _on_sync_url=MagicMock(),
        _hide_liked=MagicMock(),
        _show_liked=MagicMock(),
        _stop_following_spinner=MagicMock(),
        _tick_following_spinner=MagicMock(),
        _is_artist_fully_cached=MagicMock(return_value=True),
        _start_following_spinner=MagicMock(),
        _populate_following_panels=MagicMock(),
        _purge_unlike_buffer=MagicMock(),
        _sync_sc_np_metadata=MagicMock(),
        _on_sc_client_warmed=MagicMock(),
    )
    for name, value in attrs.items():
        setattr(stub, name, value)
    return stub


# ── BUG-9 regression: _cache_worker ─────────────────────────────────────────


def _worker() -> MagicMock:
    return SoundCloudActions._cache_worker.__wrapped__


class TestCacheWorkerResilience:
    """BUG-9: an artist that raises must not drop the rest of the queue."""

    def test_raise_on_first_drains_remaining_items(self) -> None:
        stub = _make_stub()
        CACHE_QUEUE.extend(["artist_a", "artist_b"])

        def fake_process(artist_id: str) -> dict:
            if artist_id == "artist_a":
                raise RuntimeError("network down")
            return {
                "profile": True,
                "collections": True,
                "uploads": True,
                "likes": True,
            }

        with (
            patch(
                "nyrx.actions.soundcloud.process_artist_cache", side_effect=fake_process
            ) as mock_process,
            patch("nyrx.actions.soundcloud.time.sleep"),
        ):
            result = _worker()(stub)

        assert result is None  # worker survived, no exception propagated
        assert not CACHE_QUEUE  # both ids drained: nothing abandoned
        assert mock_process.call_count == 2
        stub.call_from_thread.assert_any_call(stub._stop_following_spinner, "artist_b")
        stub.call_from_thread.assert_any_call(
            stub.notify,
            "Artist cache failed for artist_a",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_empty_queue_terminates_immediately(self) -> None:
        stub = _make_stub()

        with (
            patch("nyrx.actions.soundcloud.process_artist_cache") as mock_process,
            patch("nyrx.actions.soundcloud.time.sleep"),
        ):
            result = _worker()(stub)

        assert result is None
        mock_process.assert_not_called()
        stub.call_from_thread.assert_not_called()

    def test_all_successful_drains_and_cleans_up(self) -> None:
        stub = _make_stub()
        CACHE_QUEUE.extend(["artist_a", "artist_b"])
        ok = {"profile": True, "collections": True, "uploads": True, "likes": True}

        with (
            patch("nyrx.actions.soundcloud.process_artist_cache", return_value=ok),
            patch("nyrx.actions.soundcloud.time.sleep"),
        ):
            result = _worker()(stub)

        assert result is None
        assert not CACHE_QUEUE
        stub.call_from_thread.assert_any_call(stub._stop_following_spinner, "artist_a")
        stub.call_from_thread.assert_any_call(stub._stop_following_spinner, "artist_b")
        assert not any(
            c.args[0] is stub.notify for c in stub.call_from_thread.call_args_list
        )

    def test_raise_on_all_artists_still_drains_queue(self) -> None:
        stub = _make_stub()
        CACHE_QUEUE.extend(["artist_a", "artist_b"])

        with (
            patch(
                "nyrx.actions.soundcloud.process_artist_cache",
                side_effect=RuntimeError("client id down"),
            ) as mock_process,
            patch("nyrx.actions.soundcloud.time.sleep"),
        ):
            result = _worker()(stub)

        assert result is None
        assert mock_process.call_count == 2
        assert not CACHE_QUEUE
        assert stub.call_from_thread.call_count == 2


# ── _check_stale_caches ─────────────────────────────────────────────────────


class TestCheckStaleCaches:
    def _call(self, stub: SimpleNamespace):
        return SoundCloudActions._check_stale_caches.__wrapped__(stub)

    def test_no_client_id_returns(self) -> None:
        stub = _make_stub(_sc_followed=[{"id": "1"}])
        with (
            patch(
                "nyrx.sources.soundcloud.api.client_id_available", return_value=False
            ),
            patch("nyrx.actions.soundcloud.needs_artist_refresh") as m_need,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
        ):
            self._call(stub)
        m_need.assert_not_called()
        m_enc.assert_not_called()
        stub.call_from_thread.assert_not_called()

    def test_empty_followed(self) -> None:
        stub = _make_stub()
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.needs_artist_refresh") as m_need,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
        ):
            self._call(stub)
        m_need.assert_not_called()
        m_enc.assert_not_called()
        stub.call_from_thread.assert_not_called()

    def test_artist_without_id_skipped(self) -> None:
        stub = _make_stub(_sc_followed=[{"name": "x"}])
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.needs_artist_refresh") as m_need,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
        ):
            self._call(stub)
        m_need.assert_not_called()
        m_enc.assert_not_called()

    def test_single_stale_category_enqueues_once(self) -> None:
        stub = _make_stub(_sc_followed=[{"id": "1"}])
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch(
                "nyrx.actions.soundcloud.needs_artist_refresh",
                side_effect=lambda _aid, cat: cat == "profile",
            ) as m_need,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
        ):
            self._call(stub)
        m_enc.assert_called_once_with("1")
        m_need.assert_called_once_with("1", "profile")

    def test_two_stale_categories_still_one_enqueue(self) -> None:
        stub = _make_stub(_sc_followed=[{"id": "1"}])
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch(
                "nyrx.actions.soundcloud.needs_artist_refresh",
                side_effect=lambda _aid, cat: cat in ("profile", "uploads"),
            ) as m_need,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
        ):
            self._call(stub)
        m_enc.assert_called_once_with("1")
        m_need.assert_called_once_with("1", "profile")

    def test_nothing_stale_no_enqueue(self) -> None:
        stub = _make_stub(_sc_followed=[{"id": "1"}])
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.needs_artist_refresh", return_value=False),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
        ):
            self._call(stub)
        m_enc.assert_not_called()
        stub.call_from_thread.assert_not_called()

    def test_enqueued_schedules_worker(self) -> None:
        stub = _make_stub(_sc_followed=[{"id": "1"}])
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.needs_artist_refresh", return_value=True),
            patch(
                "nyrx.actions.soundcloud.enqueue_artist_cache",
                side_effect=CACHE_QUEUE.append,
            ) as m_enc,
        ):
            self._call(stub)
        m_enc.assert_called_once_with("1")
        stub.call_from_thread.assert_called_once_with(stub._cache_worker)

    def test_multiple_artists_each_enqueued_once(self) -> None:
        stub = _make_stub(_sc_followed=[{"id": "1"}, {"id": "2"}])
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.needs_artist_refresh", return_value=True),
            patch(
                "nyrx.actions.soundcloud.enqueue_artist_cache",
                side_effect=CACHE_QUEUE.append,
            ) as m_enc,
        ):
            self._call(stub)
        m_enc.assert_has_calls([call("1"), call("2")])


# ── _is_artist_fully_cached ─────────────────────────────────────────────────


class TestIsArtistFullyCached:
    def test_profile_stale_short_circuits(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.actions.soundcloud.needs_artist_refresh",
            side_effect=lambda _aid, cat: cat == "profile",
        ) as m_need:
            assert SoundCloudActions._is_artist_fully_cached(stub, "1") is False
            m_need.assert_called_once_with("1", "profile")

    def test_collections_stale(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.actions.soundcloud.needs_artist_refresh",
            side_effect=lambda _aid, cat: cat == "collections",
        ) as m_need:
            assert SoundCloudActions._is_artist_fully_cached(stub, "1") is False
            m_need.assert_has_calls([call("1", "profile"), call("1", "collections")])

    def test_all_fresh(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.actions.soundcloud.needs_artist_refresh", return_value=False
        ) as m_need:
            assert SoundCloudActions._is_artist_fully_cached(stub, "1") is True
            assert m_need.call_count == 4


# ── _purge_unlike_buffer ────────────────────────────────────────────────────


class TestPurgeUnlikeBuffer:
    def test_empty_buffer_no_calls(self) -> None:
        stub = _make_stub(_unlike_buffer={}, _sc_liked=[])
        with patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle:
            SoundCloudActions._purge_unlike_buffer(stub)
        m_toggle.assert_not_called()

    def test_purges_all_and_clears(self) -> None:
        buffer = {"y1": {"a": 1}, "y2": {"b": 2}}
        stub = _make_stub(_unlike_buffer=buffer, _sc_liked=[{"yt_id": "y0"}])
        with patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle:
            SoundCloudActions._purge_unlike_buffer(stub)
        m_toggle.assert_has_calls(
            [call("y1", {"a": 1}, stub._sc_liked), call("y2", {"b": 2}, stub._sc_liked)]
        )
        assert stub._unlike_buffer == {}

    def test_raise_midway_partial_purge(self) -> None:
        stub = _make_stub(
            _unlike_buffer={"y1": {"a": 1}, "y2": {"b": 2}, "y3": {"c": 3}},
            _sc_liked=[],
        )

        def boom(_yt: str, _t: dict, _l: list) -> None:
            if _yt == "y2":
                raise RuntimeError("boom")

        with patch(
            "nyrx.actions.soundcloud.toggle_sc_like", side_effect=boom
        ) as m_toggle:
            with pytest.raises(RuntimeError):
                SoundCloudActions._purge_unlike_buffer(stub)
        # y1 processed, y2 raised: clear() never ran (partial purge pinned)
        assert m_toggle.call_count == 2
        assert list(stub._unlike_buffer) == ["y1", "y2", "y3"]


# ── Following spinner helpers ───────────────────────────────────────────────


class TestStartFollowingSpinner:
    def test_already_loading_returns(self) -> None:
        stub = _make_stub(_loading_artists={"1": "Alice"})
        SoundCloudActions._start_following_spinner(stub, "1", "Alice")
        stub.set_interval.assert_not_called()

    def test_starts_timer_and_updates_item(self) -> None:
        dt = _FakeDataTable()
        stub = _make_stub(
            _loading_artists={}, _w_fs_left_list=dt, _fs_spinner_timer=None
        )
        SoundCloudActions._start_following_spinner(stub, "1", "Alice")
        assert stub._loading_artists == {"1": "Alice"}
        assert dt.calls[-1][1][0] == "1"
        assert dt.calls[-1][1][1] == "name"
        assert dt.calls[-1][1][2].plain == "\u280b Alice"
        stub.set_interval.assert_called_once_with(0.08, stub._tick_following_spinner)

    def test_timer_exists_no_reschedule(self) -> None:
        stub = _make_stub(
            _loading_artists={},
            _w_fs_left_list=_FakeList(),
            _fs_spinner_timer=SimpleNamespace(),
        )
        SoundCloudActions._start_following_spinner(stub, "1", "Alice")
        stub.set_interval.assert_not_called()


class TestTickFollowingSpinner:
    def test_no_loading_returns(self) -> None:
        stub = _make_stub(_loading_artists={})
        SoundCloudActions._tick_following_spinner(stub)
        assert stub._fs_spinner_frame == 0

    def test_tick_updates_loading_items(self) -> None:
        dt = _FakeDataTable()
        stub = _make_stub(
            _loading_artists={"1": "Alice"}, _fs_spinner_frame=0, _w_fs_left_list=dt
        )
        SoundCloudActions._tick_following_spinner(stub)
        assert stub._fs_spinner_frame == 1
        assert dt.calls[-1][1][2].plain == "\u2819 Alice"

    def test_no_list_advances_frame(self) -> None:
        stub = _make_stub(
            _loading_artists={"1": "Alice"}, _fs_spinner_frame=0, _w_fs_left_list=None
        )
        SoundCloudActions._tick_following_spinner(stub)
        assert stub._fs_spinner_frame == 1


class TestStopFollowingSpinner:
    def test_stop_removes_and_stops_timer(self) -> None:
        item = _FakeListItem(artist_id="1", label="Alice")
        ll = _FakeList(children=[item])
        timer = SimpleNamespace(stop=MagicMock())
        stub = _make_stub(
            _loading_artists={"1": "Alice"}, _w_fs_left_list=ll, _fs_spinner_timer=timer
        )
        SoundCloudActions._stop_following_spinner(stub, "1")
        assert stub._loading_artists == {}
        assert item._label.updated[-1] == "Alice"
        timer.stop.assert_called_once_with()
        assert stub._fs_spinner_timer is None

    def test_unknown_id_keeps_timer(self) -> None:
        timer = SimpleNamespace(stop=MagicMock())
        stub = _make_stub(
            _loading_artists={"1": "Alice"},
            _w_fs_left_list=_FakeList(),
            _fs_spinner_timer=timer,
        )
        SoundCloudActions._stop_following_spinner(stub, "2")
        assert stub._loading_artists == {"1": "Alice"}
        timer.stop.assert_not_called()


class TestRefreshFsSpinners:
    def test_no_client_id_returns(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions._refresh_fs_spinners(stub)
        stub._is_artist_fully_cached.assert_not_called()
        stub._start_following_spinner.assert_not_called()

    def test_no_list_returns(self) -> None:
        stub = _make_stub(_w_fs_left_list=None)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions._refresh_fs_spinners(stub)
        stub._is_artist_fully_cached.assert_not_called()

    def test_starts_missing_artists(self) -> None:
        stub = _make_stub(
            _w_fs_left_list=_FakeList(),
            _sc_followed=[
                {"id": "1", "name": "Alice"},
                {"permalink": "bob"},
                {"id": "2"},
            ],
            _loading_artists={},
            _is_artist_fully_cached=MagicMock(return_value=False),
        )
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions._refresh_fs_spinners(stub)
        assert stub._start_following_spinner.call_count == 2
        stub._start_following_spinner.assert_any_call("1", "Alice")
        stub._start_following_spinner.assert_any_call("2", "?")

    def test_fully_cached_not_started(self) -> None:
        stub = _make_stub(
            _w_fs_left_list=_FakeList(),
            _sc_followed=[{"id": "1", "name": "Alice"}],
            _loading_artists={},
            _is_artist_fully_cached=MagicMock(return_value=True),
        )
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions._refresh_fs_spinners(stub)
        stub._start_following_spinner.assert_not_called()

    def test_loading_artist_skipped(self) -> None:
        stub = _make_stub(
            _w_fs_left_list=_FakeList(),
            _sc_followed=[{"id": "1", "name": "Alice"}],
            _loading_artists={"1": "Alice"},
            _is_artist_fully_cached=MagicMock(return_value=False),
        )
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions._refresh_fs_spinners(stub)
        stub._start_following_spinner.assert_not_called()


# ── Following navigation ────────────────────────────────────────────────────


class TestHandleFollowingNav:
    def test_not_in_following(self) -> None:
        stub = _make_stub(_in_following=False)
        assert SoundCloudActions._handle_following_nav(stub, "right") is False

    def test_in_artist_profile_returns_false(self) -> None:
        stub = _make_stub(_in_following=True, _in_artist_profile=True, _in_liked=False)
        assert SoundCloudActions._handle_following_nav(stub, "right") is False

    def test_in_liked_returns_false(self) -> None:
        stub = _make_stub(_in_following=True, _in_artist_profile=False, _in_liked=True)
        assert SoundCloudActions._handle_following_nav(stub, "right") is False

    def test_missing_left_list(self) -> None:
        stub = _make_stub(
            _in_following=True, _w_fs_left_list=None, _w_fs_center_list=_FakeList()
        )
        with patch("nyrx.actions.soundcloud.logger"):
            assert SoundCloudActions._handle_following_nav(stub, "right") is False

    def test_missing_center_list(self) -> None:
        stub = _make_stub(
            _in_following=True, _w_fs_left_list=_FakeList(), _w_fs_center_list=None
        )
        with patch("nyrx.actions.soundcloud.logger"):
            assert SoundCloudActions._handle_following_nav(stub, "right") is False

    def test_right_moves_to_center_and_sets_index(self) -> None:
        ll = _FakeList(children=[_FakeListItem("1")])
        ll.has_focus = True
        cl = _FakeList(children=[_FakeListItem("1")])
        stub = _make_stub(_in_following=True, _w_fs_left_list=ll, _w_fs_center_list=cl)
        assert SoundCloudActions._handle_following_nav(stub, "right") is True
        assert cl.focused is True
        assert cl.index == 0

    def test_right_preserves_existing_index(self) -> None:
        ll = _FakeList()
        ll.has_focus = True
        cl = _FakeList(children=[_FakeListItem("1")])
        cl.index = 2
        stub = _make_stub(_in_following=True, _w_fs_left_list=ll, _w_fs_center_list=cl)
        assert SoundCloudActions._handle_following_nav(stub, "right") is True
        assert cl.index == 2

    def test_left_moves_to_left(self) -> None:
        ll = _FakeList()
        cl = _FakeList()
        cl.has_focus = True
        stub = _make_stub(_in_following=True, _w_fs_left_list=ll, _w_fs_center_list=cl)
        assert SoundCloudActions._handle_following_nav(stub, "left") is True
        assert ll.focused is True

    def test_unhandled_key(self) -> None:
        ll = _FakeList()
        cl = _FakeList()
        stub = _make_stub(_in_following=True, _w_fs_left_list=ll, _w_fs_center_list=cl)
        assert SoundCloudActions._handle_following_nav(stub, "x") is False


class TestOnKey:
    def test_handled_stops_event(self) -> None:
        stub = _make_stub()
        stub._handle_following_nav = MagicMock(return_value=True)
        event = SimpleNamespace(key="right", stop=MagicMock())
        SoundCloudActions.on_key(stub, event)
        event.stop.assert_called_once_with()

    def test_unhandled_passes_through(self) -> None:
        stub = _make_stub()
        stub._handle_following_nav = MagicMock(return_value=False)
        event = SimpleNamespace(key="x", stop=MagicMock())
        SoundCloudActions.on_key(stub, event)
        event.stop.assert_not_called()


# ── Following show / hide ───────────────────────────────────────────────────


class TestShowFollowing:
    def _sc_stub(self, **over):
        sc = _FakeScHome()
        fa = _FakeWidget()
        ap = _FakeWidget()
        mc = _FakeWidget()
        ll = _FakeDataTable()
        base = {
            "_w_sc_home": sc,
            "_w_following_area": fa,
            "_w_artist_profile": ap,
            "_w_main_content": mc,
            "_w_fs_left_list": ll,
            "_sc_followed": [
                {"id": "1", "name": "Alice"},
                {"id": "2", "permalink": "bob"},
            ],
            "screen": SimpleNamespace(has_class=lambda _cls: True),
        }
        base.update(over)
        stub = _make_stub(**base)
        return stub, sc, fa, ap, mc, ll

    def test_empty_followed_sets_feed_empty(self) -> None:
        stub, sc, fa, ap, mc, ll = self._sc_stub(_sc_followed=[])
        SoundCloudActions._show_following(stub)
        assert stub._in_following is True
        assert stub._in_artist_profile is False
        assert fa.display is True
        assert ap.display is False
        assert sc._q["#sch-center"].display is False
        assert sc._q["#fs-left"].display is True
        assert "following-mode" in mc._classes
        assert ll.focused is True
        stub.query_one.assert_called_once_with("#fs-center-switcher", ContentSwitcher)
        stub._apply_sidebar.assert_called_once_with(True)
        stub._save_sc_home_focus.assert_called_once_with()
        stub._update_sidebar_context.assert_called_once_with()
        stub._refresh_fs_spinners.assert_called_once_with()
        stub._populate_center_feed.assert_not_called()
        stub._regen_feed_worker.assert_not_called()

    def test_regen_in_progress_sets_loading(self) -> None:
        stub, *_ = self._sc_stub(_regen_in_progress=True)
        SoundCloudActions._show_following(stub)
        stub._set_feed_loading.assert_called_once_with()
        stub._regen_feed_worker.assert_not_called()

    def test_stale_feed_starts_regen(self) -> None:
        stub, *_ = self._sc_stub(_regen_in_progress=False)
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=25):
            SoundCloudActions._show_following(stub)
        stub._set_feed_loading.assert_called_once_with()
        stub._regen_feed_worker.assert_called_once_with()

    def test_unpopulated_populates_center_feed(self) -> None:
        stub, *_ = self._sc_stub(_regen_in_progress=False, _feed_populated=False)
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=5):
            SoundCloudActions._show_following(stub)
        stub._populate_center_feed.assert_called_once_with()
        stub._set_feed_loading.assert_not_called()
        stub._regen_feed_worker.assert_not_called()

    def test_populated_feed_restores_list(self) -> None:
        stub, sc, fa, ap, mc, ll = self._sc_stub(
            _regen_in_progress=False, _feed_populated=True
        )
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=5):
            SoundCloudActions._show_following(stub)
        assert ll.row_keys == ["1", "2"]
        stub._refresh_feed_liked_indicators.assert_called_once_with()
        stub.query_one.return_value.current = "feed-list"
        assert stub.query_one.return_value.current == "feed-list"
        sc.calls == []
        assert ("set_following_artists", stub._sc_followed) in sc.calls
        assert ("apply_filter", "") in sc.calls
        assert sc._q["#fs-search"].value == ""

    def test_none_widgets_warn(self) -> None:
        stub = _make_stub(
            _sc_followed=[],
            _w_fs_left_list=None,
            _w_sc_home=None,
            _w_following_area=None,
            _w_artist_profile=None,
            _w_main_content=None,
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._show_following(stub)
        assert m_log.debug.call_count >= 5


class TestHideFollowing:
    def test_hide_following(self) -> None:
        sc = _FakeScHome()
        fa = _FakeWidget()
        mc = _FakeWidget()
        timer = SimpleNamespace(stop=MagicMock())
        stub = _make_stub(
            _w_sc_home=sc,
            _w_following_area=fa,
            _w_main_content=mc,
            _fs_spinner_timer=timer,
            _loading_artists={"1": "Alice"},
            _in_following=True,
            _sc_followed=[{"id": "1", "name": "Alice"}],
        )
        SoundCloudActions._hide_following(stub)
        assert fa.display is False
        assert sc._q["#sch-center"].display is True
        assert stub._fs_spinner_timer is None
        assert stub._loading_artists == {}
        assert stub._in_following is False
        assert stub._in_artist_profile is False
        assert "following-mode" not in mc._classes
        assert ("populate_following", stub._sc_followed) in sc.calls
        timer.stop.assert_called_once_with()
        stub._update_sidebar_content.assert_called_once_with()
        stub._apply_sidebar.assert_called_once()
        stub._restore_sc_home_focus.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_none_widgets_debug(self) -> None:
        stub = _make_stub(_w_following_area=None, _w_sc_home=None, _w_main_content=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._hide_following(stub)
        m_log.debug.assert_called()


class TestShowArtistProfile:
    def test_show_profile(self) -> None:
        sc = _FakeScHome()
        ap = _FakeArtistProfile()
        stub = _make_stub(
            _w_sc_home=sc,
            _w_artist_profile=ap,
            _sc_liked=[{"yt_id": "y1"}],
            _sc_followed=[{"id": "9"}],
        )
        SoundCloudActions._show_artist_profile(stub, "9")
        assert sc._q["#fs-left"].display is False
        assert sc._q["#fs-center"].display is False
        assert sc._q["#fs-left"].disabled is True
        assert sc._q["#fs-center"].disabled is True
        assert ap.display is True
        assert stub._in_following is False
        assert stub._in_artist_profile is True
        assert ap.populate_args == ("9", {"y1"}, {"9"})
        stub._update_sidebar_context.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_none_widgets_debug(self) -> None:
        stub = _make_stub(_w_sc_home=None, _w_artist_profile=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._show_artist_profile(stub, "9")
        m_log.debug.assert_called()
        assert stub._in_artist_profile is True


class TestHideArtistProfile:
    def test_hide_restores_list_and_feed(self) -> None:
        sc = _FakeScHome()
        ll = _FakeDataTable()
        stub = _make_stub(
            _w_sc_home=sc,
            _w_artist_profile=_FakeWidget(),
            _w_fs_left_list=ll,
            _sc_followed=[{"id": "1", "name": "Alice"}],
            _in_following=True,
            _in_artist_profile=True,
            _feed_populated=True,
        )
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=5):
            SoundCloudActions._hide_artist_profile(stub)
        assert stub._in_artist_profile is False
        assert stub._in_following is True
        assert sc._q["#fs-left"].display is True
        assert sc._q["#fs-left"].disabled is False
        assert ll.row_keys == ["1"]
        assert ll.focused is True
        stub._refresh_fs_spinners.assert_called_once_with()
        stub._refresh_feed_liked_indicators.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_stale_feed_starts_regen(self) -> None:
        ll = _FakeDataTable()
        stub = _make_stub(
            _w_fs_left_list=ll,
            _sc_followed=[{"id": "1"}],
            _in_artist_profile=True,
            _feed_populated=False,
        )
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=25):
            SoundCloudActions._hide_artist_profile(stub)
        stub._set_feed_loading.assert_called_once_with()
        stub._regen_feed_worker.assert_called_once_with()


# ── Feed regen helpers ──────────────────────────────────────────────────────


class TestFeedRegenHelpers:
    def test_set_feed_loading(self) -> None:
        ll = _FakeList()
        stub = _make_stub(_w_fs_left_list=ll)
        SoundCloudActions._set_feed_loading(stub)
        assert stub._regen_in_progress is True
        assert stub._feed_populated is False
        stub.query_one.assert_called_once_with("#fs-center-switcher", ContentSwitcher)
        assert ll.focused is True

    def test_set_feed_loading_no_list(self) -> None:
        stub = _make_stub(_w_fs_left_list=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._set_feed_loading(stub)
        assert stub._regen_in_progress is True
        m_log.debug.assert_called()

    def test_set_feed_loading_switcher_raise(self) -> None:
        stub = _make_stub(
            _w_fs_left_list=None, query_one=MagicMock(side_effect=RuntimeError("x"))
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._set_feed_loading(stub)
        m_log.debug.assert_called()

    def test_on_regen_complete(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_regen_complete(stub)
        assert stub._regen_in_progress is False
        stub._populate_center_feed.assert_called_once_with()

    def test_on_regen_error_updates_header(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_regen_error(stub, "boom")
        assert stub._regen_in_progress is False
        stub.query_one.return_value.update.assert_called_once_with("FEED: Error: boom")
        assert stub.query_one.return_value.current == "feed-list"
        assert stub.query_one.call_count == 2

    def test_on_regen_error_switcher_raise(self) -> None:
        stub = _make_stub(query_one=MagicMock(side_effect=RuntimeError("x")))
        with patch("nyrx.actions.soundcloud.logger"):
            SoundCloudActions._on_regen_error(stub, "boom")
        assert stub._regen_in_progress is False

    def test_regen_worker_success(self) -> None:
        stub = _make_stub()
        with (
            patch(
                "nyrx.actions.soundcloud.generate_feed", return_value=[{"yt_id": "y1"}]
            ) as m_gen,
            patch("nyrx.actions.soundcloud.save_feed") as m_save,
        ):
            SoundCloudActions._regen_feed_worker.__wrapped__(stub)
        m_gen.assert_called_once_with()
        m_save.assert_called_once_with([{"yt_id": "y1"}])
        stub.call_from_thread.assert_called_once_with(stub._on_regen_complete)

    def test_regen_worker_error(self) -> None:
        stub = _make_stub()
        with (
            patch(
                "nyrx.actions.soundcloud.generate_feed",
                side_effect=RuntimeError("down"),
            ),
            patch("nyrx.actions.soundcloud.save_feed") as m_save,
        ):
            SoundCloudActions._regen_feed_worker.__wrapped__(stub)
        m_save.assert_not_called()
        stub.call_from_thread.assert_called_once_with(stub._on_regen_error, "down")


class TestActionRegenFeed:
    def test_not_in_following_guard(self) -> None:
        stub = _make_stub(_in_following=False)
        SoundCloudActions.action_regen_feed(stub)
        stub._set_feed_loading.assert_not_called()

    def test_in_artist_profile_guard(self) -> None:
        stub = _make_stub(_in_following=True, _in_artist_profile=True)
        SoundCloudActions.action_regen_feed(stub)
        stub._set_feed_loading.assert_not_called()

    def test_regen_in_progress_guard(self) -> None:
        stub = _make_stub(_in_following=True, _regen_in_progress=True)
        SoundCloudActions.action_regen_feed(stub)
        stub._set_feed_loading.assert_not_called()

    def test_starts_regen(self) -> None:
        stub = _make_stub(_in_following=True)
        SoundCloudActions.action_regen_feed(stub)
        stub._set_feed_loading.assert_called_once_with()
        stub._regen_feed_worker.assert_called_once_with()


# ── Center feed population ──────────────────────────────────────────────────


class TestPopulateCenterFeed:
    def test_populates(self) -> None:
        ll = _FakeList()
        header = _FakeWidget()
        feed = [
            {"yt_id": "y1", "uploader_id": "9"},
            {"yt_id": "y2", "uploader_id": "x"},
        ]
        stub = _make_stub(
            _w_fs_center_header=header,
            _w_fs_center_list=ll,
            _sc_followed=[{"id": "9"}],
            _sc_liked=[{"yt_id": "y2"}],
        )
        with (
            patch("nyrx.actions.soundcloud.load_feed", return_value=feed),
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch("nyrx.sources.soundcloud.get_listened_ids", return_value={"y1"}),
        ):
            SoundCloudActions._populate_center_feed(stub)
        assert stub._feed == feed
        assert header.updated == ["FEED"]
        assert len(ll.children) == 2
        c0, c1 = ll.children
        assert c0.data["consumed"] is True
        assert c1.data["consumed"] is False
        assert c0.following is True
        assert c0.liked is False
        assert c1.following is False
        assert c1.liked is True
        assert stub._feed_populated is True
        stub.query_one.assert_called_once_with("#fs-center-switcher", ContentSwitcher)

    def test_no_center_list_debug(self) -> None:
        stub = _make_stub(_w_fs_center_list=None)
        with (
            patch("nyrx.actions.soundcloud.load_feed", return_value=[]),
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch("nyrx.sources.soundcloud.get_listened_ids", return_value=set()),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._populate_center_feed(stub)
        m_log.debug.assert_called()
        assert stub._feed_populated is True

    def test_get_listened_ids_raises(self) -> None:
        ll = _FakeList()
        stub = _make_stub(_w_fs_center_list=ll)
        with (
            patch("nyrx.actions.soundcloud.load_feed", return_value=[{"yt_id": "y1"}]),
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch(
                "nyrx.sources.soundcloud.get_listened_ids",
                side_effect=RuntimeError("x"),
            ),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._populate_center_feed(stub)
        m_log.debug.assert_called()
        assert ll.children[0].data["consumed"] is False


class TestRefreshFeedLikedIndicators:
    def test_updates_items(self) -> None:
        ll = _FakeList(
            children=[_FakeFeedTrack({"yt_id": "y1"}), _FakeFeedTrack({"yt_id": "y2"})]
        )
        stub = _make_stub(_w_fs_center_list=ll, _sc_liked=[{"yt_id": "y1"}])
        with patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack):
            SoundCloudActions._refresh_feed_liked_indicators(stub)
        assert ll.children[0].liked is True
        assert ll.children[1].liked is False

    def test_no_list_debug(self) -> None:
        stub = _make_stub(_w_fs_center_list=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._refresh_feed_liked_indicators(stub)
        m_log.debug.assert_called()


class TestPopulateFollowingPanels:
    def test_populates(self) -> None:
        timer = SimpleNamespace(stop=MagicMock())
        ll = _FakeDataTable()
        stub = _make_stub(
            _fs_spinner_timer=timer,
            _loading_artists={"1": "A"},
            _w_fs_left_list=ll,
            _sc_followed=[{"id": "1", "name": "Alice"}],
        )
        SoundCloudActions._populate_following_panels(stub)
        timer.stop.assert_called_once_with()
        assert stub._fs_spinner_timer is None
        assert stub._loading_artists == {}
        assert ll.row_keys == ["1"]
        stub._refresh_fs_spinners.assert_called_once_with()
        stub._populate_center_feed.assert_called_once_with()


class TestActionQueueAllFeed:
    def test_guards(self) -> None:
        for attrs in [
            {
                "_in_following": False,
                "_feed_populated": True,
                "_feed": [{"yt_id": "y1"}],
            },
            {
                "_in_following": True,
                "_feed_populated": False,
                "_feed": [{"yt_id": "y1"}],
            },
            {"_in_following": True, "_feed_populated": True, "_feed": []},
        ]:
            stub = _make_stub(**attrs)
            SoundCloudActions.action_queue_all_feed(stub)
            stub._play.assert_not_called()
            stub._playback_queue.add.assert_not_called()

    def test_queues_and_plays(self) -> None:
        feed = [
            {"yt_id": "y1", "title": "One"},
            {"yt_id": "y2", "title": "Two"},
            {"title": "no-id"},
        ]
        stub = _make_stub(_in_following=True, _feed_populated=True, _feed=feed)
        SoundCloudActions.action_queue_all_feed(stub)
        assert stub._playback_queue.add.call_count == 2
        stub._play.assert_called_once()
        req = stub._play.call_args[0][0]
        assert req.source == "soundcloud"
        assert req.audio_only is True
        stub._playback_queue.remove_by_id.assert_called_once_with("y1")
        stub._sync_np_widget.assert_called_once_with()
        stub._refresh_queue_modal.assert_called_once_with()
        stub.notify.assert_called_once_with(
            "Queued 2 tracks from feed.", timeout=TIMEOUT_CONFIRM
        )

    def test_single_track_singular(self) -> None:
        stub = _make_stub(
            _in_following=True,
            _feed_populated=True,
            _feed=[{"yt_id": "y1", "title": "One"}],
        )
        SoundCloudActions.action_queue_all_feed(stub)
        stub.notify.assert_called_once_with(
            "Queued 1 track from feed.", timeout=TIMEOUT_CONFIRM
        )


# ── Station ──────────────────────────────────────────────────────────────────


class TestActionStation:
    def test_no_client_id_blocked_rearm(self) -> None:
        stub = _make_stub(_sc_api_blocked=True)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_station(stub)
        stub.notify.assert_not_called()
        stub.set_timer.assert_not_called()

    def test_no_client_id_blocks(self) -> None:
        stub = _make_stub(_sc_api_blocked=False)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_station(stub)
        assert stub._sc_api_blocked is True
        stub.notify.assert_called_once_with(
            "Soundcloud station disabled: API key not available",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub.set_timer.assert_called_once()
        stub._station_worker.assert_not_called()

    def test_station_in_progress(self) -> None:
        stub = _make_stub(_station_in_progress=True)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_station(stub)
        stub.notify.assert_called_once_with(
            "Station already loading...", timeout=TIMEOUT_CONFIRM
        )
        stub._station_worker.assert_not_called()

    def test_no_track_focused(self) -> None:
        stub = _make_stub()
        stub._get_focused_track.return_value = None
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_station(stub)
        stub.notify.assert_called_once_with(
            "No SoundCloud track focused.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub._station_worker.assert_not_called()

    def test_wrong_source_track(self) -> None:
        stub = _make_stub()
        stub._get_focused_track.return_value = {"source": "youtube", "yt_id": "y"}
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_station(stub)
        stub.notify.assert_called_once_with(
            "No SoundCloud track focused.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_no_ytid(self) -> None:
        stub = _make_stub()
        stub._get_focused_track.return_value = {"source": "soundcloud"}
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_station(stub)
        stub.notify.assert_called_once_with(
            "Track ID not available.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_launches_station(self) -> None:
        stub = _make_stub()
        stub._get_focused_track.return_value = {
            "source": "soundcloud",
            "yt_id": "y1",
            "title": "Track One",
        }
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_station(stub)
        assert stub._station_in_progress is True
        stub.notify.assert_called_once_with("Fetching station...", timeout=TIMEOUT_INFO)
        stub._station_worker.assert_called_once_with("y1", "Track One")

    def test_track_id_fallback(self) -> None:
        stub = _make_stub()
        stub._get_focused_track.return_value = {
            "source": "soundcloud",
            "track_id": "tid1",
            "title": "T",
        }
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_station(stub)
        stub._station_worker.assert_called_once_with("tid1", "T")


class TestStationWorker:
    def test_success(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.sources.soundcloud.get_station_tracks", return_value=[{"yt_id": "r1"}]
        ) as m_get:
            SoundCloudActions._station_worker.__wrapped__(stub, "y1", "T")
        m_get.assert_called_once_with("y1")
        stub.call_from_thread.assert_called_once_with(
            stub._on_station_result, [{"yt_id": "r1"}], "T"
        )

    def test_error(self) -> None:
        stub = _make_stub()
        with (
            patch(
                "nyrx.sources.soundcloud.get_station_tracks",
                side_effect=RuntimeError("x"),
            ),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._station_worker.__wrapped__(stub, "y1", "T")
        m_log.warning.assert_called()
        stub.call_from_thread.assert_called_once_with(
            stub._on_station_result, None, "T"
        )


class TestOnStationResult:
    def test_none_results(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_station_result(stub, None, "T")
        stub.notify.assert_called_once_with(
            "No station tracks found for: T",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub._play.assert_not_called()

    def test_empty_results(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_station_result(stub, [], "T")
        stub.notify.assert_called_once_with(
            "No station tracks found for: T",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_queues_station_tracks(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_station_result(
            stub, [{"yt_id": "r1"}, {"yt_id": "r2"}], "T"
        )
        assert stub._station_in_progress is False
        stub._play.assert_called_once()
        assert stub._playback_queue.add.call_count == 1
        stub._sync_np_widget.assert_called_once_with()
        stub._refresh_queue_modal.assert_called_once_with()
        stub.notify.assert_called_once_with(
            "Queued 2 station tracks from: T", timeout=TIMEOUT_CONFIRM
        )


class TestActionBrowseCollections:
    def test_not_in_artist_profile(self) -> None:
        stub = _make_stub()
        SoundCloudActions.action_browse_collections(stub)
        stub.push_screen.assert_not_called()

    def test_no_client_id_blocked_rearm(self) -> None:
        stub = _make_stub(_in_artist_profile=True, _sc_api_blocked=True)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_browse_collections(stub)
        stub.push_screen.assert_not_called()

    def test_no_client_id_blocks(self) -> None:
        stub = _make_stub(_in_artist_profile=True)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_browse_collections(stub)
        stub.notify.assert_called_once_with(
            "Client id unavailable: playlists disabled",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        assert stub._sc_api_blocked is True
        stub.set_timer.assert_called_once()

    def test_no_profile_widget(self) -> None:
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=None)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_browse_collections(stub)
        stub.push_screen.assert_not_called()

    def test_no_collections(self) -> None:
        stub = _make_stub(
            _in_artist_profile=True, _w_artist_profile=_FakeArtistProfile()
        )
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_browse_collections(stub)
        stub.notify.assert_called_once_with(
            "No collections available for this artist.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub.push_screen.assert_not_called()

    def test_pushes_collection_browser(self) -> None:
        ap = _FakeArtistProfile()
        ap._collections = [{"id": "c1"}]
        ap._profile = {"name": "Alice"}
        stub = _make_stub(
            _in_artist_profile=True,
            _w_artist_profile=ap,
            _sc_liked=[{"yt_id": "y1"}],
            _sc_followed=[{"id": "9"}],
        )
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.CollectionBrowser") as m_cb,
        ):
            SoundCloudActions.action_browse_collections(stub, "c1")
        m_cb.assert_called_once_with([{"id": "c1"}], "Alice", "c1", {"y1"}, {"9"})
        stub.push_screen.assert_called_once_with(m_cb.return_value)

    def test_no_profile_name_fallback(self) -> None:
        ap = _FakeArtistProfile()
        ap._collections = [{"id": "c1"}]
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap)
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.CollectionBrowser") as m_cb,
        ):
            SoundCloudActions.action_browse_collections(stub)
        assert m_cb.call_args[0][1] == "Artist"


# ── action_follow ───────────────────────────────────────────────────────────


class TestActionFollow:
    def test_artist_profile_follow(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap.dt.row_count = 1
        ap._track_data_map = {
            "key": {
                "source": "soundcloud",
                "uploader_id": "7",
                "channel": "Art",
                "permalink": "art",
                "yt_id": "y7",
            }
        }
        stub = _make_stub(
            _in_artist_profile=True,
            _w_artist_profile=ap,
            _w_sc_home=_FakeScHome(),
            _sc_followed=[],
            focused=ap.dt,
        )
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=False),
            patch("nyrx.actions.soundcloud.follow_sc") as m_follow,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_follow.assert_called_once_with(
            "7", "art", "Art", "https://soundcloud.com/art", []
        )
        m_enc.assert_called_once_with("7")
        stub._cache_worker.assert_called_once_with()
        stub.notify.assert_called_once_with("Following: Art", timeout=TIMEOUT_CONFIRM)
        stub._populate_following_panels.assert_called_once_with()
        assert ap._followed_set == set()
        assert ap.dt.calls  # update_cell ran for the artist column

    def test_artist_profile_unfollow(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap.dt.row_count = 1
        ap._track_data_map = {
            "key": {
                "source": "soundcloud",
                "uploader_id": "7",
                "channel": "Art",
                "permalink": "art",
            }
        }
        stub = _make_stub(
            _in_artist_profile=True,
            _w_artist_profile=ap,
            _w_sc_home=_FakeScHome(),
            _sc_followed=[{"id": "7"}],
            focused=ap.dt,
        )
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=True),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.actions.soundcloud.delete_artist_cache") as m_del,
            patch("nyrx.actions.soundcloud.unfollow_sc") as m_unf,
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_del.assert_called_once_with("7")
        m_unf.assert_called_once_with("7", stub._sc_followed)
        stub._cache_worker.assert_not_called()
        stub.notify.assert_called_once_with("Unfollowed: Art", timeout=TIMEOUT_CONFIRM)

    def test_artist_profile_no_cursor(self) -> None:
        ap = _FakeArtistProfile()
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap, focused=ap.dt)
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed"),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
        ):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()
        stub.notify.assert_not_called()

    def test_artist_profile_no_track(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap.dt.row_count = 1
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap, focused=ap.dt)
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed"),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
        ):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()
        stub.notify.assert_not_called()

    def test_artist_profile_widget_none(self) -> None:
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=None)
        with patch("nyrx.actions.soundcloud.logger"):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()

    def test_liked_screen_follow(self) -> None:
        ls = _FakeLikedScreen()
        ls._track = {
            "source": "soundcloud",
            "uploader_id": "9",
            "channel": "Lee",
            "permalink": "lee",
        }
        stub = _make_stub(_in_liked=True, _w_liked_screen=ls, _sc_followed=[])
        with (
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=False),
            patch("nyrx.actions.soundcloud.follow_sc") as m_follow,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_follow.assert_called_once()
        m_enc.assert_called_once_with("9")
        stub._cache_worker.assert_called_once_with()
        stub.notify.assert_called_once_with("Following: Lee", timeout=TIMEOUT_CONFIRM)
        stub._populate_following_panels.assert_called_once_with()
        assert ls._followed_set == set()

    def test_liked_screen_unfollow(self) -> None:
        ls = _FakeLikedScreen()
        ls._track = {
            "source": "soundcloud",
            "uploader_id": "9",
            "channel": "Lee",
            "permalink": "lee",
        }
        stub = _make_stub(
            _in_liked=True, _w_liked_screen=ls, _sc_followed=[{"id": "9"}]
        )
        with (
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=True),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.actions.soundcloud.delete_artist_cache") as m_del,
            patch("nyrx.actions.soundcloud.unfollow_sc") as m_unf,
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_del.assert_called_once_with("9")
        m_unf.assert_called_once()
        stub._cache_worker.assert_not_called()
        stub.notify.assert_called_once_with("Unfollowed: Lee", timeout=TIMEOUT_CONFIRM)

    def test_liked_screen_no_track(self) -> None:
        ls = _FakeLikedScreen()
        stub = _make_stub(_in_liked=True, _w_liked_screen=ls, _sc_followed=[])
        with (
            patch("nyrx.actions.soundcloud.is_sc_followed"),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()
        stub.notify.assert_not_called()

    def test_liked_screen_widget_none(self) -> None:
        stub = _make_stub(_in_liked=True, _w_liked_screen=None)
        with patch("nyrx.actions.soundcloud.logger"):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()

    def test_following_left_focus_returns(self) -> None:
        ll = _FakeList()
        ll.has_focus = True
        stub = _make_stub(
            _in_following=True,
            _w_fs_left_list=ll,
            _w_fs_center_list=_FakeList(),
            _sc_followed=[{"id": "5", "name": "Five"}],
        )
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.delete_artist_cache") as m_del,
            patch("nyrx.actions.soundcloud.unfollow_sc") as m_unf,
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
        ):
            SoundCloudActions.action_follow(stub)
        m_del.assert_not_called()
        m_unf.assert_not_called()
        stub.notify.assert_not_called()
        stub._populate_following_panels.assert_not_called()

    def test_following_left_no_artist_id(self) -> None:
        ll = _FakeList()
        ll.has_focus = True
        ll.highlighted_child = SimpleNamespace()
        stub = _make_stub(
            _in_following=True, _w_fs_left_list=ll, _w_fs_center_list=_FakeList()
        )
        with (
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        stub._populate_following_panels.assert_not_called()

    def test_following_left_artist_not_found(self) -> None:
        ll = _FakeList()
        ll.has_focus = True
        ll.highlighted_child = SimpleNamespace(artist_id="99")
        stub = _make_stub(
            _in_following=True, _w_fs_left_list=ll, _w_fs_center_list=_FakeList()
        )
        with (
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        stub._populate_following_panels.assert_not_called()

    def test_following_center_follow(self) -> None:
        cl = _FakeList()
        cl.has_focus = True
        track_item = _FakeFeedTrack(
            {"uploader_id": "3", "channel": "Tri", "permalink": "tri"}
        )
        cl.highlighted_child = track_item
        stub = _make_stub(
            _in_following=True,
            _w_fs_left_list=_FakeList(),
            _w_fs_center_list=cl,
            _sc_followed=[],
        )
        with (
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=False),
            patch("nyrx.actions.soundcloud.follow_sc") as m_follow,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_follow.assert_called_once_with(
            "3", "tri", "Tri", "https://soundcloud.com/tri", []
        )
        m_enc.assert_called_once_with("3")
        assert track_item.following is True
        stub.notify.assert_called_once_with("Following: Tri", timeout=TIMEOUT_CONFIRM)
        stub._populate_following_panels.assert_called_once_with()

    def test_following_center_unfollow(self) -> None:
        cl = _FakeList()
        cl.has_focus = True
        track_item = _FakeFeedTrack(
            {"uploader_id": "3", "channel": "Tri", "permalink": "tri"}
        )
        cl.highlighted_child = track_item
        stub = _make_stub(
            _in_following=True,
            _w_fs_left_list=_FakeList(),
            _w_fs_center_list=cl,
            _sc_followed=[{"id": "3"}],
        )
        with (
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=True),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.actions.soundcloud.delete_artist_cache") as m_del,
            patch("nyrx.actions.soundcloud.unfollow_sc") as m_unf,
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_del.assert_called_once_with("3")
        m_unf.assert_called_once()
        assert track_item.following is False
        stub.notify.assert_called_once_with("Unfollowed: Tri", timeout=TIMEOUT_CONFIRM)

    def test_following_center_not_feed_item(self) -> None:
        cl = _FakeList()
        cl.has_focus = True
        cl.highlighted_child = SimpleNamespace()
        stub = _make_stub(
            _in_following=True, _w_fs_left_list=_FakeList(), _w_fs_center_list=cl
        )
        with (
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
        ):
            SoundCloudActions.action_follow(stub)
        stub._populate_following_panels.assert_not_called()

    def test_fallback_no_data(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            stub._current_item.return_value = None
            SoundCloudActions.action_follow(stub)
        stub.notify.assert_called_once_with(
            "No SoundCloud artist focused to follow.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_fallback_no_uploader(self) -> None:
        stub = _make_stub(
            _np_focused=True,
            _sc_np_focused=True,
            _now_playing_data={"source": "soundcloud"},
        )
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_follow(stub)
        stub.notify.assert_called_once_with(
            "Artist info not available for this track.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_fallback_follow_with_permalink_regex(self) -> None:
        data = {
            "source": "soundcloud",
            "uploader_id": "4",
            "channel": "Four",
            "uploader_url": "https://soundcloud.com/four",
            "yt_id": "y4",
        }
        stub = _make_stub(_now_playing_data=None, _np_widgets={})
        stub._current_item.return_value = _FakeResultItem(data)
        with (
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=False),
            patch("nyrx.actions.soundcloud.follow_sc") as m_follow,
            patch("nyrx.actions.soundcloud.enqueue_artist_cache") as m_enc,
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.ResultItem", _FakeResultItem),
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
        ):
            SoundCloudActions.action_follow(stub)
        m_follow.assert_called_once_with(
            "4", "four", "Four", "https://soundcloud.com/four", []
        )
        m_enc.assert_called_once_with("4")
        stub.notify.assert_called_once_with("Following: Four", timeout=TIMEOUT_CONFIRM)
        stub._populate_following_panels.assert_called_once_with()

    def test_fallback_now_playing_unfollow_syncs_np_metadata(self) -> None:
        data = {
            "source": "soundcloud",
            "uploader_id": "4",
            "channel": "Four",
            "permalink": "four",
        }
        stub = _make_stub(
            _np_focused=True,
            _sc_np_focused=True,
            _now_playing_data=data,
            _sc_followed=[{"id": "4"}],
        )
        stub._current_item.return_value = None
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=True),
            patch("nyrx.actions.soundcloud.unfollow_sc") as m_unf,
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.actions.soundcloud.ResultItem", _FakeResultItem),
        ):
            SoundCloudActions.action_follow(stub)
        m_unf.assert_called_once()
        stub.notify.assert_called_once_with("Unfollowed: Four", timeout=TIMEOUT_CONFIRM)
        stub._sync_sc_np_metadata.assert_called_once_with()


# ── action_like_toggle ──────────────────────────────────────────────────────


class TestActionLikeToggle:
    def test_artist_profile_like(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap._track_data_map = {
            "key": {
                "source": "soundcloud",
                "yt_id": "y1",
                "like_count": 500,
                "channel": "A",
            }
        }
        stub = _make_stub(
            _in_artist_profile=True,
            _w_artist_profile=ap,
            _sc_liked=[{"yt_id": "y0"}],
            focused=ap.dt,
        )
        with patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle:
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_called_once_with(
            "y1", ap._track_data_map["key"], stub._sc_liked
        )
        assert ap._liked_ids == {"y0"}
        assert ap.dt.calls  # update_cell on the likes column

    def test_artist_profile_no_cursor(self) -> None:
        ap = _FakeArtistProfile()
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap, focused=ap.dt)
        with patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle:
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_not_called()

    def test_artist_profile_track_id_fallback(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap._track_data_map = {
            "key": {"source": "soundcloud", "track_id": "tid", "likes_count": 3}
        }
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap, focused=ap.dt)
        with patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle:
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_called_once_with(
            "tid", ap._track_data_map["key"], stub._sc_liked
        )

    def test_liked_screen_arm(self) -> None:
        ls = _FakeLikedScreen()
        ls._track = {"source": "soundcloud", "yt_id": "y9", "likes_count": 30}
        stub = _make_stub(
            _in_liked=True,
            _w_liked_screen=ls,
            _unlike_buffer={},
            _sc_liked=[{"yt_id": "y8"}],
        )
        SoundCloudActions.action_like_toggle(stub)
        assert "y9" in stub._unlike_buffer
        assert ls._buffer_ids == {"y9"}
        assert "-buffer-cursor" in ls.dt.classes
        assert ls.dt.calls  # update_cell ran

    def test_liked_screen_disarm(self) -> None:
        ls = _FakeLikedScreen()
        ls._track = {"source": "soundcloud", "yt_id": "y9", "likes_count": 30}
        stub = _make_stub(
            _in_liked=True,
            _w_liked_screen=ls,
            _unlike_buffer={"y9": ls._track},
            _sc_liked=[{"yt_id": "y8"}, {"yt_id": "y9"}],
        )
        SoundCloudActions.action_like_toggle(stub)
        assert "y9" not in stub._unlike_buffer
        assert ls._buffer_ids == set()
        assert "-buffer-cursor" not in ls.dt.classes

    def test_liked_screen_no_track(self) -> None:
        ls = _FakeLikedScreen()
        stub = _make_stub(_in_liked=True, _w_liked_screen=ls)
        SoundCloudActions.action_like_toggle(stub)
        assert stub._unlike_buffer == {}

    def test_following_like(self) -> None:
        cl = _FakeList()
        cl.has_focus = True
        item = _FakeFeedTrack({"source": "soundcloud", "yt_id": "y3"})
        cl.highlighted_child = item
        stub = _make_stub(_in_following=True, _w_fs_center_list=cl, _sc_liked=[])
        with (
            patch("nyrx.actions.soundcloud.is_sc_liked", return_value=False),
            patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle,
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
        ):
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_called_once_with("y3", item.data, stub._sc_liked)
        assert item.liked is True

    def test_following_not_feed_item(self) -> None:
        cl = _FakeList()
        cl.has_focus = True
        cl.highlighted_child = SimpleNamespace()
        stub = _make_stub(_in_following=True, _w_fs_center_list=cl)
        with (
            patch("nyrx.actions.soundcloud.is_sc_liked"),
            patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle,
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
        ):
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_not_called()

    def test_fallback_current_item(self) -> None:
        item = _FakeResultItem({"source": "soundcloud", "yt_id": "y5"})
        stub = _make_stub(_now_playing_data=None, _np_widgets={})
        stub._current_item.return_value = item
        with (
            patch("nyrx.actions.soundcloud.is_sc_liked", return_value=True),
            patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle,
            patch("nyrx.actions.soundcloud.ResultItem", _FakeResultItem),
        ):
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_called_once_with("y5", item.data, stub._sc_liked)
        assert item.liked is False

    def test_fallback_now_playing(self) -> None:
        data = {"source": "soundcloud", "yt_id": "y7"}
        stub = _make_stub(
            _np_focused=True,
            _sc_np_focused=True,
            _now_playing_data=data,
        )
        stub._current_item.return_value = None
        with (
            patch("nyrx.actions.soundcloud.is_sc_liked", return_value=False),
            patch("nyrx.actions.soundcloud.toggle_sc_like") as m_toggle,
        ):
            SoundCloudActions.action_like_toggle(stub)
        m_toggle.assert_called_once_with("y7", data, stub._sc_liked)
        stub._sync_sc_np_metadata.assert_called_once_with()

    def test_no_playable_target(self) -> None:
        stub = _make_stub(_now_playing_data=None, _np_widgets={})
        stub._current_item.return_value = None
        SoundCloudActions.action_like_toggle(stub)
        assert stub._np_widgets == {}


# ── Liked sync ──────────────────────────────────────────────────────────────


class TestActionSyncLiked:
    def test_no_client_id_blocked_rearm(self) -> None:
        stub = _make_stub(_sc_api_blocked=True)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_sync_liked(stub)
        stub.push_screen.assert_not_called()

    def test_no_client_id_blocks(self) -> None:
        stub = _make_stub()
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_sync_liked(stub)
        assert stub._sc_api_blocked is True
        stub.notify.assert_called_once_with(
            "Soundcloud sync disabled: API key not available",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub.set_timer.assert_called_once()

    def test_offline(self) -> None:
        stub = _make_stub(_online=False)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SoundCloudActions.action_sync_liked(stub)
        stub.notify.assert_called_once_with(
            "No internet connection. Sync unavailable.",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub.push_screen.assert_not_called()

    def test_pushes_modal(self) -> None:
        stub = _make_stub()
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.URLInputModal") as m_modal,
        ):
            SoundCloudActions.action_sync_liked(stub)
        stub.push_screen.assert_called_once_with(
            m_modal.return_value, stub._on_sync_url
        )


class TestOnSyncUrl:
    def test_none_result(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_sync_url(stub, None)
        stub._sync_liked_worker.assert_not_called()

    def test_result_starts_sync(self) -> None:
        stub = _make_stub()
        SoundCloudActions._on_sync_url(stub, "https://soundcloud.com/u")
        stub._sync_liked_worker.assert_called_once_with("https://soundcloud.com/u")


class TestSyncLikedWorker:
    def test_sets_loading_and_finishes(self) -> None:
        ls = _FakeLikedScreen()
        stub = _make_stub(_w_liked_screen=ls)
        with patch(
            "nyrx.actions.soundcloud.sync_liked_from_profile",
            return_value=([{"yt_id": "y1"}], 1),
        ) as m_sync:
            SoundCloudActions._sync_liked_worker.__wrapped__(stub, "url")
        m_sync.assert_called_once_with("url", stub._sc_liked)
        stub.call_from_thread.assert_any_call(ls.set_loading)
        stub.call_from_thread.assert_any_call(stub._finish_sync, [{"yt_id": "y1"}], 1)

    def test_no_liked_screen_debug(self) -> None:
        stub = _make_stub(_w_liked_screen=None)
        with (
            patch(
                "nyrx.actions.soundcloud.sync_liked_from_profile", return_value=([], 0)
            ),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._sync_liked_worker.__wrapped__(stub, "url")
        m_log.debug.assert_called()

    def test_resolve_error_calls_resolve_handler(self) -> None:
        from nyrx.sources.soundcloud.api import ProfileResolveError

        stub = _make_stub()
        with patch(
            "nyrx.actions.soundcloud.sync_liked_from_profile",
            side_effect=ProfileResolveError("bad url"),
        ):
            SoundCloudActions._sync_liked_worker.__wrapped__(stub, "https://sc.com/bad")
        stub.call_from_thread.assert_any_call(
            stub._finish_sync_resolve_error, "https://sc.com/bad"
        )
        stub._finish_sync.assert_not_called()


class TestFinishSync:
    def test_populates_and_notifies(self) -> None:
        ls = _FakeLikedScreen()
        sc = _FakeScHome()
        stub = _make_stub(
            _in_liked=True,
            _w_liked_screen=ls,
            _w_sc_home=sc,
            _sc_followed=[{"id": "9"}],
        )
        SoundCloudActions._finish_sync(stub, [{"yt_id": "y1"}], 2)
        assert stub._sc_liked == [{"yt_id": "y1"}]
        assert ls._followed_set == {"9"}
        assert ("populate_liked", stub._sc_liked) in sc.calls
        stub.notify.assert_called_once_with(
            "Synced 2 new liked tracks from profile", timeout=TIMEOUT_INFO
        )

    def test_no_new(self) -> None:
        stub = _make_stub(
            _in_liked=False, _w_liked_screen=None, _w_sc_home=_FakeScHome()
        )
        SoundCloudActions._finish_sync(stub, [], 0)
        stub.notify.assert_called_once_with(
            "No new liked tracks found", timeout=TIMEOUT_INFO
        )

    def test_single_singular(self) -> None:
        stub = _make_stub(_in_liked=False, _w_sc_home=_FakeScHome())
        SoundCloudActions._finish_sync(stub, [{"yt_id": "y1"}], 1)
        stub.notify.assert_called_once_with(
            "Synced 1 new liked track from profile", timeout=TIMEOUT_INFO
        )

    def test_no_liked_screen_debug(self) -> None:
        stub = _make_stub(
            _in_liked=True, _w_liked_screen=None, _w_sc_home=_FakeScHome()
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._finish_sync(stub, [], 0)
        m_log.debug.assert_called()


class TestFinishSyncResolveError:
    def test_warns_about_resolve_failure(self) -> None:
        stub = _make_stub()
        SoundCloudActions._finish_sync_resolve_error(stub, "https://sc.com/bad")
        stub.notify.assert_called_once_with(
            "Failed to resolve Soundcloud profile URL",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )


# ── Liked screen show / hide ────────────────────────────────────────────────


class TestActionShowLiked:
    def test_radio_source_returns(self) -> None:
        stub = _make_stub(_source=Source.RADIO)
        SoundCloudActions.action_show_liked(stub)
        stub._show_liked.assert_not_called()

    def test_no_sc_home(self) -> None:
        stub = _make_stub(_w_sc_home=None)
        with patch("nyrx.actions.soundcloud.logger"):
            SoundCloudActions.action_show_liked(stub)
        stub._show_liked.assert_not_called()

    def test_sc_home_hidden(self) -> None:
        sc = _FakeScHome()
        sc.display = False
        stub = _make_stub(_w_sc_home=sc)
        SoundCloudActions.action_show_liked(stub)
        stub._show_liked.assert_not_called()

    def test_in_artist_profile(self) -> None:
        stub = _make_stub(_w_sc_home=_FakeScHome(), _in_artist_profile=True)
        SoundCloudActions.action_show_liked(stub)
        stub._show_liked.assert_not_called()

    def test_in_following(self) -> None:
        stub = _make_stub(_w_sc_home=_FakeScHome(), _in_following=True)
        SoundCloudActions.action_show_liked(stub)
        stub._show_liked.assert_not_called()

    def test_shows(self) -> None:
        stub = _make_stub(_w_sc_home=_FakeScHome(), _in_liked=False)
        SoundCloudActions.action_show_liked(stub)
        stub._show_liked.assert_called_once_with()

    def test_hides(self) -> None:
        stub = _make_stub(_w_sc_home=_FakeScHome(), _in_liked=True)
        SoundCloudActions.action_show_liked(stub)
        stub._hide_liked.assert_called_once_with()


class TestShowLiked:
    def test_show_liked(self) -> None:
        sc = _FakeScHome()
        fa = _FakeWidget()
        mc = _FakeWidget()
        ls = _FakeLikedScreen()
        stub = _make_stub(
            _w_sc_home=sc,
            _w_following_area=fa,
            _w_main_content=mc,
            _w_liked_screen=ls,
            _sc_followed=[{"id": "9"}],
            _sc_liked=[{"yt_id": "y1"}],
        )
        SoundCloudActions._show_liked(stub)
        assert stub._in_liked is True
        assert stub._in_artist_profile is False
        assert stub._in_following is False
        assert fa.display is False
        assert sc._q["#sch-center"].display is False
        assert ls.display is True
        assert ls._followed_set == {"9"}
        assert "following-mode" in mc._classes
        stub._save_sc_home_focus.assert_called_once_with()
        stub._apply_sidebar.assert_called_once()
        stub._update_sidebar_context.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_no_liked_screen_debug(self) -> None:
        stub = _make_stub(_w_liked_screen=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._show_liked(stub)
        m_log.debug.assert_called()


class TestHideLiked:
    def test_hide_liked(self) -> None:
        sc = _FakeScHome()
        mc = _FakeWidget()
        ls = _FakeLikedScreen()
        ls._buffer_ids = {"y9"}
        stub = _make_stub(
            _in_liked=True,
            _w_sc_home=sc,
            _w_main_content=mc,
            _w_liked_screen=ls,
            _sc_liked=[{"yt_id": "y1"}],
            _unlike_buffer={"y9": {"yt_id": "y9"}},
        )
        SoundCloudActions._hide_liked(stub)
        stub._purge_unlike_buffer.assert_called_once_with()
        assert stub._in_liked is False
        assert ls.display is False
        assert ls._buffer_ids == set()
        assert sc._q["#sch-center"].display is True
        assert "following-mode" not in mc._classes
        assert ("populate_liked", stub._sc_liked) in sc.calls
        stub._update_sidebar_content.assert_called_once_with()
        stub._apply_sidebar.assert_called_once()
        stub._restore_sc_home_focus.assert_called_once_with()
        stub._render_focus_indicators.assert_called_once_with()

    def test_keybind_bar_raise_debug(self) -> None:
        sc = _FakeScHome()
        ls = _FakeLikedScreen()

        def qone(sel: str, *_a):
            if sel == "#ls-keybind-bar":
                raise RuntimeError("x")
            return ls.dt

        ls.query_one = qone
        stub = _make_stub(_w_sc_home=sc, _w_liked_screen=ls)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._hide_liked(stub)
        m_log.debug.assert_called()
        stub._purge_unlike_buffer.assert_called_once_with()

    def test_all_widgets_none_debug(self) -> None:
        stub = _make_stub(
            _in_liked=True,
            _w_sc_home=None,
            _w_liked_screen=None,
            _w_main_content=None,
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._hide_liked(stub)
        assert m_log.debug.call_count >= 5
        assert stub._in_liked is False
        stub._purge_unlike_buffer.assert_called_once_with()


# ── Residual edge branches (full-surface tail) ──────────────────────────────


class _RaiseListItem:
    """Left-list item whose label lookup always raises."""

    def __init__(self, artist_id: str = "") -> None:
        self.artist_id = artist_id

    def query_one(self, *_args):
        raise RuntimeError("no label")


class TestActionShowFollowingGuards:
    def _sc(self, display: bool = True) -> _FakeScHome:
        sc = _FakeScHome()
        sc.display = display
        return sc

    def test_np_focused_returns(self) -> None:
        stub = _make_stub(
            _np_focused=True,
            _w_sc_home=self._sc(),
            _hide_following=MagicMock(),
            _show_following=MagicMock(),
            _hide_artist_profile=MagicMock(),
        )
        SoundCloudActions.action_show_following(stub)
        stub._hide_following.assert_not_called()
        stub._show_following.assert_not_called()

    def test_no_sc_home_debug(self) -> None:
        stub = _make_stub(
            _np_focused=False,
            _w_sc_home=None,
            _hide_following=MagicMock(),
            _show_following=MagicMock(),
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions.action_show_following(stub)
        m_log.debug.assert_called()
        stub._hide_following.assert_not_called()
        stub._show_following.assert_not_called()

    def test_sc_home_hidden_returns(self) -> None:
        stub = _make_stub(
            _np_focused=False,
            _w_sc_home=self._sc(display=False),
            _hide_following=MagicMock(),
            _show_following=MagicMock(),
        )
        SoundCloudActions.action_show_following(stub)
        stub._hide_following.assert_not_called()
        stub._show_following.assert_not_called()

    def test_in_liked_returns(self) -> None:
        stub = _make_stub(
            _np_focused=False,
            _w_sc_home=self._sc(),
            _in_liked=True,
            _hide_following=MagicMock(),
            _show_following=MagicMock(),
        )
        SoundCloudActions.action_show_following(stub)
        stub._hide_following.assert_not_called()
        stub._show_following.assert_not_called()

    def test_in_artist_profile_hides_profile(self) -> None:
        stub = _make_stub(
            _np_focused=False,
            _w_sc_home=self._sc(),
            _in_artist_profile=True,
            _hide_artist_profile=MagicMock(),
            _show_following=MagicMock(),
        )
        SoundCloudActions.action_show_following(stub)
        stub._hide_artist_profile.assert_called_once_with()
        stub._show_following.assert_not_called()

    def test_in_following_hides(self) -> None:
        stub = _make_stub(
            _np_focused=False,
            _w_sc_home=self._sc(),
            _in_following=True,
            _hide_following=MagicMock(),
            _show_following=MagicMock(),
        )
        SoundCloudActions.action_show_following(stub)
        stub._hide_following.assert_called_once_with()
        stub._show_following.assert_not_called()

    def test_else_shows(self) -> None:
        stub = _make_stub(
            _np_focused=False,
            _w_sc_home=self._sc(),
            _hide_following=MagicMock(),
            _show_following=MagicMock(),
        )
        SoundCloudActions.action_show_following(stub)
        stub._show_following.assert_called_once_with()
        stub._hide_following.assert_not_called()


class TestShowFollowingEdge:
    def _sc_stub(self, **over):
        base = {
            "_w_sc_home": _FakeScHome(),
            "_w_following_area": _FakeWidget(),
            "_w_artist_profile": _FakeWidget(),
            "_w_main_content": _FakeWidget(),
            "_w_fs_left_list": _FakeDataTable(),
            "_sc_followed": [
                {"id": "1", "name": "Alice"},
                {"id": "2", "permalink": "bob"},
            ],
        }
        base.update(over)
        return _make_stub(**base)

    def test_nonempty_followed_no_left_list_warns(self) -> None:
        stub = self._sc_stub(_w_fs_left_list=None)
        with (
            patch("nyrx.actions.soundcloud.get_feed_age", return_value=5),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._show_following(stub)
        m_log.debug.assert_called()

    def test_empty_followed_switcher_raise(self) -> None:
        stub = self._sc_stub(
            _sc_followed=[],
            query_one=MagicMock(side_effect=RuntimeError("x")),
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._show_following(stub)
        m_log.debug.assert_called()

    def test_populated_feed_switcher_raise(self) -> None:
        stub = self._sc_stub(
            _feed_populated=True,
            _regen_in_progress=False,
            query_one=MagicMock(side_effect=RuntimeError("x")),
        )
        with (
            patch("nyrx.actions.soundcloud.get_feed_age", return_value=5),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._show_following(stub)
        m_log.debug.assert_called()


class TestHideArtistProfileEdge:
    def _sc_stub(self, **over):
        base = {
            "_w_artist_profile": _FakeWidget(),
            "_w_sc_home": _FakeScHome(),
            "_w_fs_left_list": _FakeDataTable(),
            "_sc_followed": [{"id": "1", "name": "Alice"}],
            "_in_artist_profile": True,
        }
        base.update(over)
        return _make_stub(**base)

    def test_no_left_list_debug(self) -> None:
        stub = self._sc_stub(_w_fs_left_list=None)
        with (
            patch("nyrx.actions.soundcloud.get_feed_age", return_value=5),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._hide_artist_profile(stub)
        assert m_log.debug.called
        assert stub._in_artist_profile is False
        assert stub._in_following is True

    def test_empty_followed_switcher_raise(self) -> None:
        stub = self._sc_stub(
            _sc_followed=[],
            query_one=MagicMock(side_effect=RuntimeError("x")),
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._hide_artist_profile(stub)
        m_log.debug.assert_called()

    def test_regen_in_progress_sets_loading(self) -> None:
        stub = self._sc_stub(_regen_in_progress=True)
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=5):
            SoundCloudActions._hide_artist_profile(stub)
        stub._set_feed_loading.assert_called_once_with()
        stub._regen_feed_worker.assert_not_called()

    def test_unpopulated_populates_center_feed(self) -> None:
        stub = self._sc_stub(_regen_in_progress=False, _feed_populated=False)
        with patch("nyrx.actions.soundcloud.get_feed_age", return_value=5):
            SoundCloudActions._hide_artist_profile(stub)
        stub._populate_center_feed.assert_called_once_with()
        stub._set_feed_loading.assert_not_called()

    def test_populated_feed_switcher_raise(self) -> None:
        stub = self._sc_stub(
            _regen_in_progress=False,
            _feed_populated=True,
            query_one=MagicMock(side_effect=RuntimeError("x")),
        )
        with (
            patch("nyrx.actions.soundcloud.get_feed_age", return_value=5),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._hide_artist_profile(stub)
        m_log.debug.assert_called()


class TestSpinnerLabelRaise:
    def test_start_spinner_label_raise(self) -> None:
        ll = _FakeList(children=[_RaiseListItem("1")])
        stub = _make_stub(
            _loading_artists={}, _w_fs_left_list=ll, _fs_spinner_timer=None
        )
        SoundCloudActions._start_following_spinner(stub, "1", "Alice")
        assert stub._loading_artists == {"1": "Alice"}
        stub.set_interval.assert_called_once()

    def test_tick_spinner_label_raise(self) -> None:
        ll = _FakeList(children=[_RaiseListItem("1")])
        stub = _make_stub(
            _loading_artists={"1": "Alice"}, _fs_spinner_frame=0, _w_fs_left_list=ll
        )
        SoundCloudActions._tick_following_spinner(stub)
        assert stub._fs_spinner_frame == 1

    def test_stop_spinner_label_raise(self) -> None:
        ll = _FakeList(children=[_RaiseListItem("1")])
        timer = SimpleNamespace(stop=MagicMock())
        stub = _make_stub(
            _loading_artists={"1": "Alice"}, _w_fs_left_list=ll, _fs_spinner_timer=timer
        )
        SoundCloudActions._stop_following_spinner(stub, "1")
        assert stub._loading_artists == {}
        timer.stop.assert_called_once_with()


class TestCacheWorkerPartialFailure:
    def test_partial_failure_notifies_failed_artist(self) -> None:
        stub = _make_stub()
        CACHE_QUEUE.extend(["artist_a"])
        result = {"profile": True, "collections": False, "uploads": True, "likes": True}
        with (
            patch("nyrx.actions.soundcloud.process_artist_cache", return_value=result),
            patch("nyrx.actions.soundcloud.time.sleep"),
        ):
            _worker()(stub)
        stub.call_from_thread.assert_any_call(
            stub.notify,
            "Artist cache: collections failed for artist_a",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )


class TestPopulateCenterFeedEdge:
    def test_switcher_raise_debug(self) -> None:
        ll = _FakeList()
        stub = _make_stub(
            _w_fs_center_list=ll,
            _sc_followed=[],
            query_one=MagicMock(side_effect=RuntimeError("x")),
        )
        with (
            patch("nyrx.actions.soundcloud.load_feed", return_value=[]),
            patch("nyrx.actions.soundcloud.FeedTrackItem", _FakeFeedTrack),
            patch("nyrx.sources.soundcloud.get_listened_ids", return_value=set()),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions._populate_center_feed(stub)
        m_log.debug.assert_called()
        assert stub._feed_populated is True


class TestPopulateFollowingPanelsEdge:
    def test_no_left_list_empty_debug(self) -> None:
        stub = _make_stub(_w_fs_left_list=None, _sc_followed=[])
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._populate_following_panels(stub)
        m_log.debug.assert_called()

    def test_no_left_list_nonempty_append_debug(self) -> None:
        stub = _make_stub(
            _w_fs_left_list=None, _sc_followed=[{"id": "1", "name": "Alice"}]
        )
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._populate_following_panels(stub)
        assert m_log.debug.called


class TestActionFollowEdge:
    def test_no_client_id_blocked_rearm(self) -> None:
        stub = _make_stub(_sc_api_blocked=True)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_follow(stub)
        stub.notify.assert_not_called()
        stub.set_timer.assert_not_called()

    def test_no_client_id_blocks(self) -> None:
        stub = _make_stub(_sc_api_blocked=False)
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SoundCloudActions.action_follow(stub)
        assert stub._sc_api_blocked is True
        stub.notify.assert_called_once_with(
            "Client id unavailable: follow disabled",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )
        stub.set_timer.assert_called_once()

    def test_artist_profile_no_uploader(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap.dt.row_count = 1
        ap._track_data_map = {
            "key": {"source": "soundcloud", "channel": "Art", "permalink": "art"}
        }
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=ap, focused=ap.dt)
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed"),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
        ):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()
        stub.notify.assert_called_once_with(
            "Artist info not available.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_artist_profile_no_sc_home_debug(self) -> None:
        ap = _FakeArtistProfile()
        ap.dt.cursor_coordinate = (0,)
        ap.dt.row_count = 1
        ap._track_data_map = {
            "key": {
                "source": "soundcloud",
                "uploader_id": "7",
                "channel": "Art",
                "permalink": "art",
            }
        }
        stub = _make_stub(
            _in_artist_profile=True,
            _w_artist_profile=ap,
            _w_sc_home=None,
            focused=ap.dt,
        )
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=False),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.logger") as m_log,
        ):
            SoundCloudActions.action_follow(stub)
        m_log.debug.assert_called()

    def test_liked_screen_no_uploader(self) -> None:
        ls = _FakeLikedScreen()
        ls._track = {"source": "soundcloud", "channel": "Lee", "permalink": "lee"}
        stub = _make_stub(_in_liked=True, _w_liked_screen=ls)
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed"),
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.unfollow_sc"),
            patch("nyrx.actions.soundcloud.delete_artist_cache"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
        ):
            SoundCloudActions.action_follow(stub)
        stub._cache_worker.assert_not_called()
        stub.notify.assert_called_once_with(
            "Artist info not available.",
            severity=SEVERITY_WARNING,
            timeout=TIMEOUT_WARNING,
            title="Warning",
        )

    def test_fallback_unfollow_result_item(self) -> None:
        data = {
            "source": "soundcloud",
            "uploader_id": "4",
            "channel": "Four",
            "permalink": "four",
        }
        item = _FakeResultItem(data)
        stub = _make_stub(
            _now_playing_data=None, _np_widgets={}, _sc_followed=[{"id": "4"}]
        )
        stub._current_item.return_value = item
        with (
            patch("nyrx.sources.soundcloud.api.client_id_available", return_value=True),
            patch("nyrx.actions.soundcloud.is_sc_followed", return_value=True),
            patch("nyrx.actions.soundcloud.unfollow_sc") as m_unf,
            patch("nyrx.actions.soundcloud.delete_artist_cache") as m_del,
            patch("nyrx.actions.soundcloud.follow_sc"),
            patch("nyrx.actions.soundcloud.enqueue_artist_cache"),
            patch("nyrx.actions.soundcloud.ResultItem", _FakeResultItem),
        ):
            SoundCloudActions.action_follow(stub)
        m_del.assert_called_once_with("4")
        m_unf.assert_called_once()
        stub.notify.assert_called_once_with("Unfollowed: Four", timeout=TIMEOUT_CONFIRM)
        assert item.following is False
        stub._populate_following_panels.assert_called_once_with()


class TestActionLikeToggleEdge:
    def test_artist_profile_widget_none_debug(self) -> None:
        stub = _make_stub(_in_artist_profile=True, _w_artist_profile=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions.action_like_toggle(stub)
        m_log.debug.assert_called()

    def test_liked_screen_widget_none_debug(self) -> None:
        stub = _make_stub(_in_liked=True, _w_liked_screen=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions.action_like_toggle(stub)
        m_log.debug.assert_called()


class TestFinishSyncEdge:
    def test_no_sc_home_debug(self) -> None:
        stub = _make_stub(_in_liked=False, _w_liked_screen=None, _w_sc_home=None)
        with patch("nyrx.actions.soundcloud.logger") as m_log:
            SoundCloudActions._finish_sync(stub, [], 0)
        m_log.debug.assert_called()
        stub.notify.assert_called_once_with(
            "No new liked tracks found", timeout=TIMEOUT_INFO
        )


# ── _sync_sc_np_metadata ────────────────────────────────────────────────────


class TestSyncScNpMetadata:
    def _np_fake(self) -> SimpleNamespace:
        return SimpleNamespace(
            _like_count=7,
            _play_count=9,
            update_metadata=MagicMock(),
        )

    def test_updates_widget_with_liked_and_followed(self) -> None:
        np_side = self._np_fake()
        stub = _make_stub(
            _np_widgets={"soundcloud": np_side},
            _now_playing_data={
                "source": "soundcloud",
                "yt_id": "abc",
                "uploader_id": "u1",
            },
            _sc_liked=[{"yt_id": "abc"}],
            _sc_followed=[{"id": "u1"}],
        )
        SoundCloudActions._sync_sc_np_metadata(stub)
        np_side.update_metadata.assert_called_once_with(
            liked=True, followed=True, like_count=7, play_count=9
        )

    def test_followed_false_when_artist_not_followed(self) -> None:
        np_side = self._np_fake()
        stub = _make_stub(
            _np_widgets={"soundcloud": np_side},
            _now_playing_data={
                "source": "soundcloud",
                "yt_id": "abc",
                "uploader_id": "u1",
            },
            _sc_liked=[{"yt_id": "abc"}],
            _sc_followed=[],
        )
        SoundCloudActions._sync_sc_np_metadata(stub)
        np_side.update_metadata.assert_called_once_with(
            liked=True, followed=False, like_count=7, play_count=9
        )

    def test_uploader_id_fallback_from_liked_list(self) -> None:
        np_side = self._np_fake()
        stub = _make_stub(
            _np_widgets={"soundcloud": np_side},
            _now_playing_data={"source": "soundcloud", "yt_id": "abc"},
            _sc_liked=[{"yt_id": "abc", "uploader_id": "u1"}],
            _sc_followed=[{"id": "u1"}],
        )
        SoundCloudActions._sync_sc_np_metadata(stub)
        np_side.update_metadata.assert_called_once_with(
            liked=True, followed=True, like_count=7, play_count=9
        )

    def test_noop_non_soundcloud_track(self) -> None:
        np_side = self._np_fake()
        stub = _make_stub(
            _np_widgets={"soundcloud": np_side},
            _now_playing_data={"source": "youtube", "yt_id": "abc"},
        )
        SoundCloudActions._sync_sc_np_metadata(stub)
        np_side.update_metadata.assert_not_called()

    def test_noop_missing_widget(self) -> None:
        stub = _make_stub(
            _np_widgets={},
            _now_playing_data={"source": "soundcloud", "yt_id": "abc"},
        )
        SoundCloudActions._sync_sc_np_metadata(stub)

    def test_noop_empty_yt_id(self) -> None:
        np_side = self._np_fake()
        stub = _make_stub(
            _np_widgets={"soundcloud": np_side},
            _now_playing_data={"source": "soundcloud", "yt_id": ""},
        )
        SoundCloudActions._sync_sc_np_metadata(stub)
        np_side.update_metadata.assert_not_called()


# ── Warmup callback: _warm_sc_client + _on_sc_client_warmed ────────────────


class TestWarmScClient:
    """``_warm_sc_client`` resolves client_id then notifies via call_from_thread."""

    @staticmethod
    def _worker():
        return SoundCloudActions._warm_sc_client.__wrapped__

    def test_notifies_warmed_after_ensure(self) -> None:
        stub = _make_stub()
        with patch("nyrx.sources.soundcloud.api.ensure_client_id"):
            self._worker()(stub)
        stub.call_from_thread.assert_called_once_with(stub._on_sc_client_warmed)


class TestOnScClientWarmed:
    """``_on_sc_client_warmed`` refreshes the trending label when SC-home displayed."""

    def test_updates_label_when_displayed(self) -> None:
        sc = MagicMock(display=True)
        stub = _make_stub(_w_sc_home=sc)
        SoundCloudActions._on_sc_client_warmed(stub)
        sc._update_trending_label.assert_called_once_with()

    def test_noop_when_not_displayed(self) -> None:
        sc = MagicMock(display=False)
        stub = _make_stub(_w_sc_home=sc)
        SoundCloudActions._on_sc_client_warmed(stub)
        sc._update_trending_label.assert_not_called()

    def test_noop_when_sc_home_none(self) -> None:
        stub = _make_stub(_w_sc_home=None)
        SoundCloudActions._on_sc_client_warmed(stub)
