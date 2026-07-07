# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/radio.py`` (5B.4: station index, play, likes, list).

Bare ``object.__new__(RadioActions)`` stubs resolve sibling methods through
the class so real orchestration chains run, while every side-effect caller
(``_play``, ``notify``, ``_populate_radio_list``...) is a recorded mock.
``@work`` methods are invoked via ``__wrapped__``.  The ``DataTable`` used
as ``self._w_radio_list`` is a plain fake; only ``self.focused`` needs to be
a real ``DataTable`` instance to satisfy the ``isinstance`` gate.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from textual.coordinate import Coordinate
from textual.widgets import DataTable

from nyrx.actions.radio import RadioActions
from nyrx.config import SEVERITY_ERROR, TIMEOUT_ERROR, TIMEOUT_INFO
from nyrx.models import MediaRequest
from nyrx.modes import Source
from tests.fakes import stub_self


class _FakeIndex:
    """Stand-in for ``StationIndex`` (no isinstance gate in radio.py)."""

    def __init__(self, stations=None, liked=None, last_fetched=0.0, filtered=None):
        self.stations = stations if stations is not None else []
        self._liked = set(liked or [])
        self.last_fetched = last_fetched
        self.filtered = filtered
        self.get_filtered_calls = []
        self.refresh_calls = 0
        self.toggle_calls = []

    def get_filtered(self, name="", tags=None, countries=None):
        self.get_filtered_calls.append((name, tags, countries))
        return self.filtered if self.filtered is not None else self.stations

    def toggle_like(self, uuid):
        self.toggle_calls.append(uuid)
        if uuid in self._liked:
            self._liked.discard(uuid)
            return False
        self._liked.add(uuid)
        return True

    def popular_tags(self, station):
        raw = station.get("tags", "").strip()
        if not raw:
            return []
        return [t.strip() for t in raw.split(",") if t.strip()][:3]

    def ensure_index_loaded(self):
        return self

    def refresh_from_api(self):
        self.refresh_calls += 1


class _RowKey:
    """Hashable stand-in for Textual's ``RowKey`` (has ``.value``)."""

    def __init__(self, value):
        self.value = value

    def __hash__(self):
        return hash(self.value)

    def __eq__(self, other):
        return isinstance(other, _RowKey) and other.value == self.value


class _FakeDataTable:
    """Stand-in for the radio stations ``DataTable``."""

    def __init__(self):
        self.columns = []
        self.rows = {}
        self.row_count = 0
        self.focus_calls = 0
        self.clear_calls = 0
        self.move_cursor_rows = []
        self.update_cell_calls = []
        self.add_row_calls = []

    def add_column(self, label, key=None, width=None):
        self.columns.append(key)

    def clear(self):
        self.clear_calls += 1
        self.rows = {}
        self.row_count = 0

    def add_row(self, *cells, key=None):
        self.add_row_calls.append((cells, key))
        self.rows[_RowKey(key)] = None
        self.row_count = len(self.rows)

    def move_cursor(self, row=None, **kwargs):
        self.move_cursor_rows.append(row)

    def update_cell(self, key, column, value):
        self.update_cell_calls.append((key, column, value))

    def focus(self):
        self.focus_calls += 1


def _make_stub(**overrides):
    """Bare ``RadioActions`` with sane defaults + side-effect mocks."""
    defaults = {
        "_station_index": None,
        "_sources": {},
        "_source": "youtube",
        "_w_radio_list": None,
        "_w_radio_filter_hint": None,
        "_radio_row_positions": {},
        "_radio_row_stations": {},
        "_radio_total_filtered": 0,
        "_radio_display_count": 0,
        "_radio_page": 0,
        "_radio_gen": 0,
        "_radio_filter_name": "",
        "_radio_filter_tags": None,
        "_radio_filter_countries": None,
        "_now_playing_data": None,
        "focused": None,
        "notify": MagicMock(),
        "call_from_thread": MagicMock(),
        "call_after_refresh": MagicMock(),
        "log": MagicMock(),
        "_play": MagicMock(),
        "_do_refresh_radio": MagicMock(),
        "_populate_radio_list": MagicMock(),
        "_update_sidebar_context": MagicMock(),
        "_update_radio_playing_indicator": MagicMock(),
        "_refresh_radio_item": MagicMock(),
        "_maybe_refresh_radio": MagicMock(),
        "_on_index_loaded": MagicMock(),
    }
    defaults.update(overrides)
    return stub_self(RadioActions, **defaults)


def _make_stub_sync(**overrides):
    """Stub whose ``call_from_thread`` runs the callback inline.

    ``_populate_radio_list`` now dispatches to ``_populate_radio_list_async``
    (worker) which delivers rows through ``call_from_thread(_on_radio_rows_ready,
    ...)``.  Inline dispatch lets these tests exercise the real rendering path
    synchronously, preserving the original DataTable-level assertions.
    """
    overrides.setdefault("call_from_thread", lambda fn, *a, **kw: fn(*a, **kw))
    return _make_stub(**overrides)


def _call(cls_method, stub, *args, **kwargs):
    return cls_method(stub, *args, **kwargs)


def _station(**kw):
    data = {
        "stationuuid": "u1",
        "name": "Station",
        "url_resolved": "http://stream",
        "tags": "rock,pop",
        "country": "USA",
        "countrycode": "US",
        "bitrate": 128,
        "codec": "MP3",
        "clickcount": 10,
    }
    data.update(kw)
    return data


class TestMaybeRefreshRadio:
    def test_no_index_noop(self, patch_clock):
        stub = _make_stub(_station_index=None)
        _call(RadioActions._maybe_refresh_radio, stub)
        stub._do_refresh_radio.assert_not_called()

    def test_empty_stations_noop(self, patch_clock):
        stub = _make_stub(_station_index=_FakeIndex(stations=[]))
        _call(RadioActions._maybe_refresh_radio, stub)
        stub._do_refresh_radio.assert_not_called()

    def test_fresh_index_no_refresh(self, patch_clock):
        patch_clock.set_time(1_000_000.0)
        idx = _FakeIndex(stations=[_station()], last_fetched=1_000_000.0 - 5.99 * 86400)
        stub = _make_stub(_station_index=idx)
        with patch("nyrx.config.RADIO_CACHE_DAYS", 6):
            _call(RadioActions._maybe_refresh_radio, stub)
        stub._do_refresh_radio.assert_not_called()

    def test_stale_index_triggers_refresh(self, patch_clock):
        patch_clock.set_time(1_000_000.0)
        idx = _FakeIndex(stations=[_station()], last_fetched=1_000_000.0 - 6.01 * 86400)
        stub = _make_stub(_station_index=idx)
        with patch("nyrx.config.RADIO_CACHE_DAYS", 6):
            _call(RadioActions._maybe_refresh_radio, stub)
        stub._do_refresh_radio.assert_called_once()

    def test_exact_age_not_refreshed(self, patch_clock):
        patch_clock.set_time(1_000_000.0)
        idx = _FakeIndex(stations=[_station()], last_fetched=1_000_000.0 - 6.0 * 86400)
        stub = _make_stub(_station_index=idx)
        with patch("nyrx.config.RADIO_CACHE_DAYS", 6):
            _call(RadioActions._maybe_refresh_radio, stub)
        stub._do_refresh_radio.assert_not_called()


class TestDeferredLoadIndex:
    def test_loads_and_reports_loaded(self, wrapped):
        idx = _FakeIndex(stations=[_station()])
        stub = _make_stub(_sources={"radio": idx})
        wrapped(RadioActions._deferred_load_index)(stub)
        stub.call_from_thread.assert_called_once_with(stub._on_index_loaded, idx)


class TestOnIndexLoaded:
    def test_radio_source_populates(self):
        idx = _FakeIndex(stations=[_station()])
        stub = _make_stub(_source="radio")
        _call(RadioActions._on_index_loaded, stub, idx)
        assert stub._station_index is idx
        stub._maybe_refresh_radio.assert_called_once()
        stub._populate_radio_list.assert_called_once()
        stub._update_sidebar_context.assert_called_once()

    def test_non_radio_source_skips_populate(self):
        idx = _FakeIndex(stations=[_station()])
        stub = _make_stub(_source="youtube")
        _call(RadioActions._on_index_loaded, stub, idx)
        stub._populate_radio_list.assert_not_called()
        stub._update_sidebar_context.assert_not_called()


class TestDoRefreshRadio:
    def test_success_notifies(self, wrapped):
        idx = _FakeIndex(stations=[_station()])
        stub = _make_stub(_sources={"radio": idx})
        wrapped(RadioActions._do_refresh_radio)(stub)
        assert idx.refresh_calls == 1
        stub.notify.assert_called_once_with(
            "Radio station index refreshed", timeout=TIMEOUT_INFO
        )

    def test_exception_notifies_error(self, wrapped):
        idx = _FakeIndex(stations=[_station()])
        idx.refresh_from_api = MagicMock(side_effect=RuntimeError("boom"))
        stub = _make_stub(_sources={"radio": idx})
        wrapped(RadioActions._do_refresh_radio)(stub)
        stub.log.warning.assert_called_once()
        stub.notify.assert_called_once_with(
            "Radio refresh failed",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )


class TestPlayRadio:
    def _play_req(self, stub):
        return stub._play.call_args[0][0]

    def test_plays_media_request(self):
        stub = _make_stub()
        _call(
            RadioActions._play_radio,
            stub,
            _station(
                url_resolved="http://stream",
                stationuuid="u1",
                name="S",
                countrycode="US",
            ),
        )
        req = self._play_req(stub)
        assert isinstance(req, MediaRequest)
        assert req.source == "radio"
        assert req.yt_id == "u1"
        assert req.data["url"] == "http://stream"
        assert req.title == "S"
        assert req.channel == "S"
        assert req.payload.countrycode == "US"
        stub.call_after_refresh.assert_called_once_with(
            stub._update_radio_playing_indicator
        )

    def test_yt_id_fallback_when_no_uuid(self):
        stub = _make_stub()
        station = _station(yt_id="ytid1")
        del station["stationuuid"]
        _call(RadioActions._play_radio, stub, station)
        assert self._play_req(stub).yt_id == "ytid1"

    def test_url_fallback_when_no_ids(self):
        stub = _make_stub()
        station = _station()
        del station["stationuuid"]
        station.pop("yt_id", None)
        _call(RadioActions._play_radio, stub, station)
        assert self._play_req(stub).data["yt_id"] == "http://stream"

    def test_default_title(self):
        stub = _make_stub()
        station = _station(name=None)
        del station["name"]
        _call(RadioActions._play_radio, stub, station)
        assert self._play_req(stub).title == "Radio Station"

    def test_bug7_empty_url_resolved_falls_back_to_url(self):
        stub = _make_stub()
        _call(
            RadioActions._play_radio,
            stub,
            _station(url_resolved="", url="http://ok", stationuuid="u1"),
        )
        assert self._play_req(stub).data["url"] == "http://ok"

    def test_no_url_notifies_error(self):
        stub = _make_stub()
        _call(RadioActions._play_radio, stub, _station(url_resolved="", url=""))
        stub.notify.assert_called_once_with(
            "No stream URL found for this station",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub._play.assert_not_called()


class TestToggleRadioLike:
    def test_no_index_returns(self):
        stub = _make_stub(_station_index=None)
        _call(RadioActions._toggle_radio_like, stub, _station())
        stub._refresh_radio_item.assert_not_called()

    def test_no_uuid_returns(self):
        stub = _make_stub(_station_index=_FakeIndex())
        _call(RadioActions._toggle_radio_like, stub, _station(stationuuid=None))
        stub._refresh_radio_item.assert_not_called()

    def test_toggles_and_refreshes(self):
        idx = _FakeIndex(stations=[_station()], liked=set())
        stub = _make_stub_sync(_station_index=idx)
        _call(RadioActions._toggle_radio_like, stub, _station(stationuuid="u1"))
        assert idx.toggle_calls == ["u1"]
        assert "u1" in idx._liked
        stub._refresh_radio_item.assert_called_once_with("u1")


class TestPopulateRadioList:
    """Dispatcher → worker → ``_on_radio_rows_ready`` flow.

    ``_populate_radio_list`` bumps ``_radio_gen`` and hands a snapshot to the
    ``@work`` ``_populate_radio_list_async`` worker, which builds rows and
    delivers them through ``call_from_thread(_on_radio_rows_ready, ...)``.
    Worker/callback tests invoke the worker via ``__wrapped__`` (bypassing the
    Textual decorator) with an inline-dispatching ``call_from_thread`` so the
    real rendering path runs synchronously.
    """

    def _snapshot(self, **over):
        snap = {
            "name": "",
            "tags": None,
            "countries": None,
            "page": 0,
            "gen": 0,
        }
        snap.update(over)
        return (
            snap["name"],
            snap["tags"],
            snap["countries"],
            snap["page"],
            snap["gen"],
        )

    def _populate(self, stub, snapshot=None):
        _call(
            RadioActions._populate_radio_list_async.__wrapped__,
            stub,
            self._snapshot() if snapshot is None else snapshot,
        )

    def test_dt_none_returns(self):
        stub = _make_stub(
            _w_radio_list=None,
            _station_index=_FakeIndex(),
            _populate_radio_list_async=MagicMock(),
        )
        _call(RadioActions._populate_radio_list, stub)
        stub._populate_radio_list_async.assert_called_once()

    def test_no_index_returns(self):
        stub = _make_stub(
            _w_radio_list=_FakeDataTable(),
            _station_index=None,
            _populate_radio_list_async=MagicMock(),
        )
        _call(RadioActions._populate_radio_list, stub)
        stub._populate_radio_list_async.assert_not_called()

    def test_adds_columns_when_empty(self):
        dt = _FakeDataTable()
        stub = _make_stub_sync(
            _w_radio_list=dt, _station_index=_FakeIndex(stations=[_station()])
        )
        self._populate(stub)
        assert dt.columns == ["pos", "name", "tags", "country", "bitrate", "clicks"]
        assert dt.clear_calls == 1

    def test_skips_add_columns_when_present(self):
        dt = _FakeDataTable()
        dt.columns = ["pos", "name", "tags", "country", "bitrate", "clicks"]
        stub = _make_stub_sync(
            _w_radio_list=dt, _station_index=_FakeIndex(stations=[_station()])
        )
        self._populate(stub)
        assert len(dt.add_row_calls) == 1

    def test_filter_branch_uses_get_filtered(self):
        dt = _FakeDataTable()
        idx = _FakeIndex(stations=[_station()], filtered=[_station()])
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx)
        self._populate(
            stub, self._snapshot(name="rock", tags=["tag"], countries=["US"])
        )
        assert idx.get_filtered_calls == [("rock", ["tag"], ["US"])]

    def test_liked_sorted_first_and_desc(self):
        dt = _FakeDataTable()
        idx = _FakeIndex(
            stations=[
                _station(stationuuid="a", clickcount=5),
                _station(stationuuid="b", clickcount=50),
                _station(stationuuid="c", clickcount=20),
            ],
            liked={"b"},
        )
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx)
        self._populate(stub)
        keys = [c[1] for c in dt.add_row_calls]
        assert keys == ["b", "c", "a"]

    def test_pagination_slice_boundary(self):
        dt = _FakeDataTable()
        stations = [_station(stationuuid=f"u{i}") for i in range(101)]
        idx = _FakeIndex(stations=stations)
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx)
        self._populate(stub, self._snapshot(page=1))
        assert stub._radio_total_filtered == 101
        assert stub._radio_display_count == 1
        assert len(dt.add_row_calls) == 1
        assert dt.add_row_calls[0][1] == "u100"

    def test_empty_display_returns_after_totals(self):
        dt = _FakeDataTable()
        idx = _FakeIndex(stations=[_station(stationuuid="u1")])
        hint = SimpleNamespace(display=False, update=MagicMock())
        stub = _make_stub_sync(
            _w_radio_list=dt, _station_index=idx, _w_radio_filter_hint=hint
        )
        self._populate(stub, self._snapshot(page=1))
        assert stub._radio_total_filtered == 1
        assert stub._radio_display_count == 0
        assert hint.display is True
        hint.update.assert_not_called()
        assert dt.focus_calls == 0

    def test_hint_shows_keybind_bar(self):
        dt = _FakeDataTable()
        hint = SimpleNamespace(display=False, update=MagicMock())
        stub = _make_stub_sync(
            _w_radio_list=dt,
            _station_index=_FakeIndex(stations=[_station()]),
            _w_radio_filter_hint=hint,
        )
        self._populate(stub)
        assert hint.display is True
        hint.update.assert_not_called()

    def test_cursor_restored_to_previous_uuid(self):
        dt = _FakeDataTable()
        focused = DataTable()
        focused.id = "radio-list"
        focused.cursor_coordinate = Coordinate(0, 0)
        focused.coordinate_to_cell_key = lambda c: SimpleNamespace(
            row_key=SimpleNamespace(value="u1")
        )
        idx = _FakeIndex(stations=[_station(stationuuid="u1")])
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx, focused=focused)
        self._populate(stub)
        assert dt.move_cursor_rows == [0]
        assert dt.focus_calls == 1

    def test_cursor_resets_to_zero_when_previous_gone(self):
        dt = _FakeDataTable()
        idx = _FakeIndex(stations=[_station(stationuuid="u1")])
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx, focused=None)
        self._populate(stub)
        assert dt.move_cursor_rows == [0]

    def test_fmt_clicks_boundaries(self):
        dt = _FakeDataTable()
        idx = _FakeIndex(
            stations=[
                _station(stationuuid="a", clickcount=999),
                _station(stationuuid="b", clickcount=1000),
                _station(stationuuid="c", clickcount=999999),
                _station(stationuuid="d", clickcount=1000000),
                _station(stationuuid="e", clickcount=0),
            ]
        )
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx)
        self._populate(stub)
        plains = [c[0][5].plain for c in dt.add_row_calls]
        assert plains == ["1.0M", "1000k", "  1k", " 999", "   \u2014"]

    def test_liked_cell_shows_heart(self):
        dt = _FakeDataTable()
        idx = _FakeIndex(stations=[_station(stationuuid="u1")], liked={"u1"})
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx)
        self._populate(stub)
        assert dt.add_row_calls[0][0][0].plain == "  \u2764\ufe0e"

    def test_long_name_truncated_with_ellipsis(self):
        dt = _FakeDataTable()
        long_name = "X" * 80
        idx = _FakeIndex(stations=[_station(name=long_name)])
        stub = _make_stub_sync(_w_radio_list=dt, _station_index=idx)
        self._populate(stub)
        name_cell = dt.add_row_calls[0][0][1]
        assert "\u2026" in name_cell

    def test_playing_indicator_refreshed(self):
        dt = _FakeDataTable()
        stub = _make_stub_sync(
            _w_radio_list=dt, _station_index=_FakeIndex(stations=[_station()])
        )
        self._populate(stub)
        stub._update_radio_playing_indicator.assert_called_once()


class TestUpdateRadioPlayingIndicator:
    def test_dt_none_returns(self):
        stub = _make_stub(_w_radio_list=None)
        _call(RadioActions._update_radio_playing_indicator, stub)

    def test_playing_liked_and_pos_cells(self):
        dt = _FakeDataTable()
        dt.add_row(*([""] * 6), key="u1")
        dt.add_row(*([""] * 6), key="u2")
        dt.add_row(*([""] * 6), key="u3")
        idx = _FakeIndex(liked={"u2"})
        stub = _make_stub(
            _w_radio_list=dt,
            _station_index=idx,
            _source="radio",
            _now_playing_data={"yt_id": "u1"},
            _radio_row_positions={"u1": 1, "u2": 2, "u3": 3},
        )
        _call(RadioActions._update_radio_playing_indicator, stub)
        by_key = {k: (col, v.plain) for k, col, v in dt.update_cell_calls}
        assert by_key["u1"] == ("pos", " \u25b6")
        assert by_key["u2"] == ("pos", "  \u2764\ufe0e")
        assert by_key["u3"] == ("pos", "  3")

    def test_not_playing_when_source_not_radio(self):
        dt = _FakeDataTable()
        dt.add_row(*([""] * 6), key="u1")
        idx = _FakeIndex(liked=set())
        stub = _make_stub(
            _w_radio_list=dt,
            _station_index=idx,
            _source="youtube",
            _now_playing_data={"yt_id": "u1"},
            _radio_row_positions={"u1": 1},
        )
        _call(RadioActions._update_radio_playing_indicator, stub)
        assert dt.update_cell_calls[0][2].plain == "  1"

    def test_no_now_playing_data_all_pos(self):
        dt = _FakeDataTable()
        dt.add_row(*([""] * 6), key="u1")
        stub = _make_stub(
            _w_radio_list=dt,
            _station_index=_FakeIndex(liked=set()),
            _source="radio",
            _now_playing_data=None,
            _radio_row_positions={"u1": 7},
        )
        _call(RadioActions._update_radio_playing_indicator, stub)
        assert dt.update_cell_calls[0][2].plain == "  7"


class TestRadioIsActive:
    def test_radio_source_active(self):
        stub = _make_stub(_source=Source.RADIO)
        assert RadioActions._radio_is_active.__get__(stub) is True

    def test_other_source_inactive(self):
        stub = _make_stub(_source="youtube")
        assert RadioActions._radio_is_active.__get__(stub) is False


class TestRefreshRadioItem:
    def test_uuid_not_in_rows_returns(self):
        stub = _make_stub(_radio_row_stations={}, _w_radio_list=_FakeDataTable())
        _call(RadioActions._refresh_radio_item, stub, "u1")
        assert stub._w_radio_list.update_cell_calls == []

    def test_dt_none_returns(self):
        stub = _make_stub(_radio_row_stations={"u1": _station()}, _w_radio_list=None)
        _call(RadioActions._refresh_radio_item, stub, "u1")

    def test_playing_cell(self):
        dt = _FakeDataTable()
        stub = _make_stub(
            _radio_row_stations={"u1": _station()},
            _radio_row_positions={"u1": 3},
            _w_radio_list=dt,
            _source="radio",
            _now_playing_data={"yt_id": "u1"},
            _station_index=_FakeIndex(liked=set()),
        )
        _call(RadioActions._refresh_radio_item, stub, "u1")
        assert dt.update_cell_calls[0][2].plain == "\u25b6"

    def test_liked_cell(self):
        dt = _FakeDataTable()
        stub = _make_stub(
            _radio_row_stations={"u1": _station()},
            _radio_row_positions={"u1": 3},
            _w_radio_list=dt,
            _source="youtube",
            _now_playing_data=None,
            _station_index=_FakeIndex(liked={"u1"}),
        )
        _call(RadioActions._refresh_radio_item, stub, "u1")
        assert dt.update_cell_calls[0][2].plain == "  \u2764\ufe0e"

    def test_pos_cell(self):
        dt = _FakeDataTable()
        stub = _make_stub(
            _radio_row_stations={"u1": _station()},
            _radio_row_positions={"u1": 3},
            _w_radio_list=dt,
            _source="youtube",
            _now_playing_data=None,
            _station_index=_FakeIndex(liked=set()),
        )
        _call(RadioActions._refresh_radio_item, stub, "u1")
        assert dt.update_cell_calls[0][2].plain == "  3"
