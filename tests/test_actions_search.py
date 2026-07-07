# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``actions/search.py`` (5B.3: search/pagination/history).

Bare ``object.__new__(SearchActions)`` stubs resolve sibling methods through
the class so real orchestration chains run, while every side-effect caller
(``notify``, ``_show_page``, ``_perform_search``, ``call_from_thread``...)
is a recorded mock.  ``@work`` methods are invoked via ``__wrapped__``.
``_history_key`` runs real (it is the one method conveniently keepable).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from textual.widgets import ListItem

from nyrx.actions.search import SearchActions
from nyrx.config import SEVERITY_ERROR, TIMEOUT_ERROR
from nyrx.models import MediaRequest
from nyrx.modes import Source
from nyrx.widgets import HistoryItem, ResultItem
from tests.fakes import stub_self


class _FakeSource:
    """Stand-in for a ``Source``-backed search API."""

    def __init__(self, results=None, error=None):
        self.results = results
        self.error = error
        self.search_calls = []

    def search(self, query, limit=None):
        self.search_calls.append((query, limit))
        if self.error is not None:
            raise self.error
        return self.results


class _FakeListView:
    """Stand-in for the results/history ListView."""

    def __init__(self):
        self.items = []
        self.index = None
        self.display = True
        self.focus_calls = 0

    def clear(self):
        self.items = []

    def append(self, item):
        self.items.append(item)

    def focus(self):
        self.focus_calls += 1


class _Timer:
    def __init__(self):
        self.stops = 0

    def stop(self):
        self.stops += 1


def _make_stub(**overrides):
    """Bare ``SearchActions`` with sane defaults + side-effect mocks."""
    defaults = {
        "_page": 0,
        "_page_size": 30,
        "_all_results": [],
        "_search_token": 0,
        "_query": "",
        "_exhausted": False,
        "_online": True,
        "_source": "youtube",
        "_np_focused": False,
        "_in_tv_series": False,
        "_w_tv_series": None,
        "_in_liked": False,
        "_w_liked_screen": None,
        "_in_watchlist": False,
        "_w_watchlist_screen": None,
        "_in_following": False,
        "_w_sc_home": None,
        "_radio_total_filtered": 0,
        "_radio_page": 0,
        "_notify_timer": None,
        "_spinning_history_item": None,
        "_spinning_chip": False,
        "_chip_spinner_idx": 0,
        "_chip_spinner_timer": None,
        "_search_histories": {},
        "_sc_liked": [],
        "_sc_followed": [],
        "_tv_bookmarks": [],
        "_w_empty_state": None,
        "_w_results_list": None,
        "_w_history_list": None,
        "_w_empty_heading": None,
        "_sources": {},
        "notify": MagicMock(),
        "call_from_thread": MagicMock(),
        "call_after_refresh": MagicMock(),
        "set_timer": MagicMock(),
        "set_interval": MagicMock(),
        "query_one": MagicMock(
            return_value=SimpleNamespace(current="", children=[], update=MagicMock())
        ),
        "push_screen": MagicMock(),
        "log": MagicMock(),
        "_show_page": MagicMock(),
        "_show_info": MagicMock(),
        "_show_loading": MagicMock(),
        "_fetch_next_page": MagicMock(),
        "_perform_search": MagicMock(),
        "_play": MagicMock(),
        "_rebuild_history_list": MagicMock(),
        "_stop_history_spinner": MagicMock(),
        "_exit_landing_mode": MagicMock(),
        "_enter_landing_mode": MagicMock(),
        "_update_sidebar_context": MagicMock(),
        "_apply_history_gradient": MagicMock(),
        "_populate_radio_list": MagicMock(),
        "_notify_once": MagicMock(),
        "_switch_results_to": MagicMock(),
        "_handle_connectivity_result": MagicMock(),
        "_check_connectivity": MagicMock(),
        "action_open_filter": MagicMock(),
        "_on_season_jump_result": MagicMock(),
    }
    defaults.update(overrides)
    return stub_self(SearchActions, **defaults)


def _call(cls_method, stub, *args, **kwargs):
    return cls_method(stub, *args, **kwargs)


class TestOnInitialResults:
    """History-cap logic: insert-front, dedupe, 10 cap, exhausted flag."""

    def test_empty_query_skips_history(self):
        stub = _make_stub(_query="", _search_histories={"youtube": []})
        with patch("nyrx.actions.search.update_config") as mock_update:
            _call(SearchActions._on_initial_results, stub, [], "", "youtube", 0)
        mock_update.assert_not_called()
        stub._show_page.assert_called_once()
        assert stub._exhausted is True

    def test_existing_front_query_skips_save(self):
        stub = _make_stub(_query="q1", _search_histories={"youtube": ["q1"]})
        with patch("nyrx.actions.search.update_config") as mock_update:
            _call(SearchActions._on_initial_results, stub, [], "q1", "youtube", 0)
        mock_update.assert_not_called()
        stub._rebuild_history_list.assert_not_called()
        stub._show_page.assert_called_once()

    def test_new_query_inserts_front_and_saves(self):
        stub = _make_stub(_query="q1", _search_histories={"youtube": []})
        with patch("nyrx.actions.search.update_config") as mock_update:
            _call(SearchActions._on_initial_results, stub, [], "q1", "youtube", 0)
        assert stub._search_histories["youtube"] == ["q1"]
        mock_update.assert_called_once_with(search_histories={"youtube": ["q1"]})
        stub._rebuild_history_list.assert_called_once()
        stub._show_page.assert_called_once()

    def test_existing_query_moved_front(self):
        stub = _make_stub(_query="q1", _search_histories={"youtube": ["q2", "q1"]})
        _call(SearchActions._on_initial_results, stub, [], "q1", "youtube", 0)
        assert stub._search_histories["youtube"] == ["q1", "q2"]

    def test_eleventh_item_pushes_oldest_off(self):
        stub = _make_stub(
            _query="new", _search_histories={"youtube": [f"q{i}" for i in range(10)]}
        )
        _call(SearchActions._on_initial_results, stub, [], "new", "youtube", 0)
        h = stub._search_histories["youtube"]
        assert len(h) == 10
        assert h[0] == "new"
        assert h[-1] == "q8"

    def test_exhausted_boundary_equal_to_page_size_times_three(self):
        stub = _make_stub(_all_results=[{}] * 90)
        _call(SearchActions._on_initial_results, stub, [{}] * 90, "", "youtube", 0)
        assert stub._exhausted is False

    def test_exhausted_boundary_below_page_size_times_three(self):
        stub = _make_stub(_all_results=[{}] * 89)
        _call(SearchActions._on_initial_results, stub, [{}] * 89, "", "youtube", 0)
        assert stub._exhausted is True


class TestPerformSearch:
    """``@work`` search worker via ``__wrapped__``."""

    def test_results_delivered_to_initial_handler(self, wrapped):
        source = _FakeSource(results=[{"yt_id": "a"}])
        stub = _make_stub(_sources={"youtube": source})
        wrapped(SearchActions._perform_search)(stub, limit=90)
        assert stub._search_token == 1
        assert source.search_calls == [("", 90)]
        stub.call_from_thread.assert_called_once_with(
            stub._on_initial_results, [{"yt_id": "a"}], "", "youtube", 1
        )

    def test_empty_results_shows_no_results(self, wrapped):
        stub = _make_stub(_sources={"youtube": _FakeSource(results=[])})
        wrapped(SearchActions._perform_search)(stub, limit=90)
        stub.call_from_thread.assert_called_once_with(
            stub._show_info, "No results found."
        )

    def test_online_exception_reports_search_failed(self, wrapped):
        stub = _make_stub(
            _sources={"youtube": _FakeSource(error=RuntimeError("boom"))},
            _online=True,
        )
        wrapped(SearchActions._perform_search)(stub, limit=90)
        stub.call_from_thread.assert_called_once()
        args = stub.call_from_thread.call_args[0]
        assert args[0] == stub._show_info
        assert args[1] == "Search failed: boom"

    def test_offline_exception_records_failed_query_and_conn_result(self, wrapped):
        stub = _make_stub(
            _sources={"youtube": _FakeSource(error=RuntimeError("boom"))},
            _online=True,
            _check_connectivity=MagicMock(return_value=False),
        )
        wrapped(SearchActions._perform_search)(stub, limit=90)
        assert stub._last_failed_query == ""
        stub.call_from_thread.assert_any_call(stub._handle_connectivity_result, False)
        stub.call_from_thread.assert_any_call(
            stub._show_info, "No internet connection."
        )

    def test_offline_exception_skips_conn_result_when_already_offline(self, wrapped):
        stub = _make_stub(
            _sources={"youtube": _FakeSource(error=RuntimeError("boom"))},
            _online=False,
        )
        wrapped(SearchActions._perform_search)(stub, limit=90)
        assert stub._last_failed_query == ""
        stub.call_from_thread.assert_called_once_with(
            stub._show_info, "No internet connection."
        )


class TestFetchNextPage:
    """``@work`` next-page worker via ``__wrapped__``."""

    def test_success_calls_on_fetch_done(self, wrapped):
        source = _FakeSource(results=[{}] * 31)
        stub = _make_stub(_sources={"youtube": source})
        wrapped(SearchActions._fetch_next_page)(stub)
        assert source.search_calls == [("", 60)]
        stub.call_from_thread.assert_called_once_with(
            stub._on_fetch_done, [{}] * 31, 1, "", "youtube", 0
        )

    def test_online_exception_notifies_and_switches_empty(self, wrapped):
        stub = _make_stub(
            _sources={"youtube": _FakeSource(error=RuntimeError("boom"))},
            _online=True,
        )
        wrapped(SearchActions._fetch_next_page)(stub)
        stub.call_from_thread.assert_any_call(
            stub.notify, "Failed to load more: boom", severity="error", timeout=3
        )
        stub.call_from_thread.assert_any_call(stub._switch_results_to, "rs-empty")

    def test_offline_exception_notifies_offline_and_switches_empty(self, wrapped):
        stub = _make_stub(
            _sources={"youtube": _FakeSource(error=RuntimeError("boom"))},
            _online=False,
        )
        wrapped(SearchActions._fetch_next_page)(stub)
        stub.call_from_thread.assert_any_call(
            stub.notify,
            "No internet connection. Can't fetch more results.",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub.call_from_thread.assert_any_call(stub._switch_results_to, "rs-empty")

    def test_online_unreachable_also_reports_connectivity(self, wrapped):
        stub = _make_stub(
            _sources={"youtube": _FakeSource(error=RuntimeError("boom"))},
            _online=True,
            _check_connectivity=MagicMock(return_value=False),
        )
        wrapped(SearchActions._fetch_next_page)(stub)
        stub.call_from_thread.assert_any_call(stub._handle_connectivity_result, False)
        stub.call_from_thread.assert_any_call(
            stub.notify,
            "No internet connection. Can't fetch more results.",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )


class TestOnFetchDone:
    """Exhausted boundary: ``start < len`` shows, otherwise no-more-results."""

    def test_more_results_sets_page_and_shows(self):
        stub = _make_stub(_all_results=[{}] * 31)
        _call(SearchActions._on_fetch_done, stub, [{}] * 31, 1, "", "youtube", 0)
        assert stub._page == 1
        stub._show_page.assert_called_once()
        stub._notify_once.assert_not_called()

    def test_len_equal_start_is_exhausted(self):
        stub = _make_stub(_all_results=[{}] * 30)
        _call(SearchActions._on_fetch_done, stub, [{}] * 30, 1, "", "youtube", 0)
        assert stub._exhausted is True
        stub._notify_once.assert_called_once_with("No more results.")
        stub._show_page.assert_not_called()

    def test_len_start_plus_one_shows(self):
        stub = _make_stub(_all_results=[{}] * 31)
        _call(SearchActions._on_fetch_done, stub, [{}] * 31, 1, "", "youtube", 0)
        stub._show_page.assert_called_once()
        assert stub._page == 1


class TestSearchStaleness:
    """Token/query/source guards: late or cross-mode results are discarded."""

    def test_stale_token_discards_initial_results(self):
        stub = _make_stub(_query="dogs", _search_token=2)
        with patch("nyrx.actions.search.update_config") as mock_update:
            _call(
                SearchActions._on_initial_results,
                stub,
                [{"yt_id": "a"}],
                "cats",
                "youtube",
                1,
            )
        assert stub._all_results == []
        stub._show_page.assert_not_called()
        mock_update.assert_not_called()

    def test_stale_source_discards_initial_results(self):
        stub = _make_stub(_query="cats", _search_token=1, _source="soundcloud")
        _call(
            SearchActions._on_initial_results,
            stub,
            [{"yt_id": "a"}],
            "cats",
            "youtube",
            1,
        )
        stub._show_page.assert_not_called()

    def test_stale_query_discards_fetch_done(self):
        stub = _make_stub(_query="dogs", _search_token=1)
        _call(SearchActions._on_fetch_done, stub, [{}] * 31, 1, "cats", "youtube", 1)
        assert stub._all_results == []
        assert stub._exhausted is False
        stub._show_page.assert_not_called()

    def test_current_search_accepts_results(self):
        stub = _make_stub(
            _query="cats",
            _search_token=1,
            _source="youtube",
            _search_histories={"youtube": []},
        )
        _call(
            SearchActions._on_initial_results,
            stub,
            [{"yt_id": "a"}],
            "cats",
            "youtube",
            1,
        )
        assert stub._all_results == [{"yt_id": "a"}]
        stub._show_page.assert_called_once()

    def test_stale_worker_error_is_silent(self, wrapped):
        stub = _make_stub(_online=True)
        src = _FakeSource(results=[])

        def _boom(query, limit=None):
            src.search_calls.append((query, limit))
            stub._search_token = 99
            raise RuntimeError("boom")

        src.search = _boom
        stub._sources = {"youtube": src}
        wrapped(SearchActions._perform_search)(stub, limit=90)
        stub.call_from_thread.assert_not_called()


class TestShowPage:
    """Watched ratio, bookmarks/likes/following, empty page, BUG-6."""

    def _row(self, **kw):
        data = {"yt_id": "yt1", "source": "youtube", "title": "T", "duration": 100}
        data.update(kw)
        return data

    def test_results_list_none_returns(self):
        stub = _make_stub(_w_results_list=None)
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        stub._show_info.assert_not_called()

    def test_empty_page_shows_no_more_results(self):
        lv = _FakeListView()
        stub = _make_stub(_all_results=[], _w_results_list=lv)
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        stub._show_info.assert_called_once_with("No more results.")

    def test_watched_ratio_over_80_percent(self):
        lv = _FakeListView()
        stub = _make_stub(
            _all_results=[self._row()],
            _w_results_list=lv,
        )
        with patch("nyrx.actions.search.get_watched_secs", return_value={"yt1": 80}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].watched is True

    def test_watched_ratio_under_80_percent(self):
        lv = _FakeListView()
        stub = _make_stub(_all_results=[self._row()], _w_results_list=lv)
        with patch("nyrx.actions.search.get_watched_secs", return_value={"yt1": 79}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].watched is False

    def test_zero_duration_never_watched(self):
        lv = _FakeListView()
        stub = _make_stub(_all_results=[self._row(duration=0)], _w_results_list=lv)
        with patch("nyrx.actions.search.get_watched_secs", return_value={"yt1": 500}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].watched is False

    def test_soundcloud_row_never_watched(self):
        lv = _FakeListView()
        stub = _make_stub(
            _all_results=[self._row(source="soundcloud")],
            _w_results_list=lv,
        )
        with patch("nyrx.actions.search.get_watched_secs", return_value={"yt1": 999}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].watched is False

    def test_youtube_liked_from_sc_liked(self):
        lv = _FakeListView()
        stub = _make_stub(
            _all_results=[self._row()],
            _w_results_list=lv,
            _sc_liked=[{"yt_id": "yt1"}],
        )
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].liked is True

    def test_tv_movies_liked_from_bookmarks(self):
        lv = _FakeListView()
        stub = _make_stub(
            _all_results=[self._row(source="tv_movies", yt_id="tmdb_42")],
            _w_results_list=lv,
            _tv_bookmarks=[{"tmdb_id": 42}],
        )
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].liked is True

    def test_bug6_bookmark_without_tmdb_id_does_not_crash(self):
        lv = _FakeListView()
        stub = _make_stub(
            _all_results=[self._row(source="tv_movies", yt_id="tmdb_42")],
            _w_results_list=lv,
            _tv_bookmarks=[{"title": "malformed"}],
        )
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].liked is False

    def test_following_from_uploader_id(self):
        lv = _FakeListView()
        stub = _make_stub(
            _all_results=[self._row(uploader_id="chan1")],
            _w_results_list=lv,
            _sc_followed=[{"id": "chan1"}],
        )
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        assert lv.items[0].following is True

    def test_index_reset_focus_and_sidebar(self):
        lv = _FakeListView()
        stub = _make_stub(_all_results=[self._row()], _w_results_list=lv)
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        assert lv.index == 0
        assert lv.focus_calls == 1
        stub._switch_results_to.assert_called_once_with("results-list")
        stub._exit_landing_mode.assert_called_once_with()

    def test_constructed_items_are_result_items(self):
        lv = _FakeListView()
        stub = _make_stub(_all_results=[self._row()], _w_results_list=lv)
        with patch("nyrx.actions.search.get_watched_secs", return_value={}):
            _call(SearchActions._show_page, stub)
        assert isinstance(lv.items[0], ResultItem)


class TestActionNextPage:
    """Pagination state machine: radio boundary, exhausted, offline."""

    def test_np_focused_returns(self):
        stub = _make_stub(_np_focused=True)
        _call(SearchActions.action_next_page, stub)
        stub._show_page.assert_not_called()
        stub._populate_radio_list.assert_not_called()

    def test_in_tv_series_returns(self):
        stub = _make_stub(_in_tv_series=True)
        _call(SearchActions.action_next_page, stub)
        stub._show_page.assert_not_called()

    def test_radio_total_100_no_page(self):
        stub = _make_stub(_source="radio", _radio_total_filtered=100, _radio_page=0)
        _call(SearchActions.action_next_page, stub)
        assert stub._radio_page == 0
        stub._populate_radio_list.assert_not_called()

    def test_radio_total_101_advances(self):
        stub = _make_stub(_source="radio", _radio_total_filtered=101, _radio_page=0)
        _call(SearchActions.action_next_page, stub)
        assert stub._radio_page == 1
        stub._populate_radio_list.assert_called_once()
        stub._update_sidebar_context.assert_called_once()

    def test_radio_zero_total_no_page(self):
        stub = _make_stub(_source="radio", _radio_total_filtered=0, _radio_page=0)
        _call(SearchActions.action_next_page, stub)
        assert stub._radio_page == 0

    def test_no_all_results_returns(self):
        stub = _make_stub(_all_results=[])
        _call(SearchActions.action_next_page, stub)
        stub._show_page.assert_not_called()

    def test_next_start_within_results_advances(self):
        stub = _make_stub(_all_results=[{}] * 31)
        _call(SearchActions.action_next_page, stub)
        assert stub._page == 1
        stub._show_page.assert_called_once()
        stub._fetch_next_page.assert_not_called()

    def test_exhausted_notifies_no_more_results(self):
        stub = _make_stub(_all_results=[{}] * 30, _exhausted=True)
        _call(SearchActions.action_next_page, stub)
        stub._notify_once.assert_called_once_with("No more results.")
        stub._fetch_next_page.assert_not_called()

    def test_offline_notifies_no_internet(self):
        stub = _make_stub(_all_results=[{}] * 30, _exhausted=False, _online=False)
        _call(SearchActions.action_next_page, stub)
        stub._notify_once.assert_called_once_with(
            "Can't fetch more pages, no internet detected.", severity=SEVERITY_ERROR
        )
        stub._fetch_next_page.assert_not_called()

    def test_fetches_next_page_when_online(self):
        stub = _make_stub(_all_results=[{}] * 30, _exhausted=False, _online=True)
        _call(SearchActions.action_next_page, stub)
        stub._show_loading.assert_called_once()
        stub._fetch_next_page.assert_called_once()


class TestActionPrevPage:
    def test_np_focused_returns(self):
        stub = _make_stub(_np_focused=True)
        _call(SearchActions.action_prev_page, stub)
        stub._show_page.assert_not_called()

    def test_in_tv_series_returns(self):
        stub = _make_stub(_in_tv_series=True)
        _call(SearchActions.action_prev_page, stub)
        stub._show_page.assert_not_called()

    def test_radio_page_zero_no_decrement(self):
        stub = _make_stub(_source="radio", _radio_page=0)
        _call(SearchActions.action_prev_page, stub)
        assert stub._radio_page == 0
        stub._populate_radio_list.assert_not_called()

    def test_radio_page_one_decrements(self):
        stub = _make_stub(_source="radio", _radio_page=1)
        _call(SearchActions.action_prev_page, stub)
        assert stub._radio_page == 0
        stub._populate_radio_list.assert_called_once()
        stub._update_sidebar_context.assert_called_once()

    def test_page_zero_guarded(self):
        stub = _make_stub(_page=0, _all_results=[{}] * 10)
        _call(SearchActions.action_prev_page, stub)
        stub._show_page.assert_not_called()
        assert stub._page == 0

    def test_no_results_guarded(self):
        stub = _make_stub(_page=1, _all_results=[])
        _call(SearchActions.action_prev_page, stub)
        stub._show_page.assert_not_called()

    def test_page_one_decrements_and_shows(self):
        stub = _make_stub(_page=1, _all_results=[{}] * 10)
        _call(SearchActions.action_prev_page, stub)
        assert stub._page == 0
        stub._show_page.assert_called_once()


class TestShowLoading:
    def test_empty_state_hidden_and_switch_loading(self):
        stub = _make_stub(_w_empty_state=SimpleNamespace(display=True))
        _call(SearchActions._show_loading, stub)
        stub._exit_landing_mode.assert_called_once()
        assert stub._w_empty_state.display is False
        stub._switch_results_to.assert_called_once_with("rs-loading")

    def test_no_empty_state_still_switches(self):
        stub = _make_stub(_w_empty_state=None)
        _call(SearchActions._show_loading, stub)
        stub._switch_results_to.assert_called_once_with("rs-loading")


class TestShowInfo:
    def test_message_notifies_and_switches_empty(self):
        lv = _FakeListView()
        stub = _make_stub(_w_results_list=lv)
        _call(SearchActions._show_info, stub, "hello")
        stub._switch_results_to.assert_called_once_with("rs-empty")
        assert lv.items == []
        stub.notify.assert_called_once_with("hello", timeout=3)

    def test_empty_message_no_notify(self):
        stub = _make_stub(_w_results_list=_FakeListView())
        _call(SearchActions._show_info, stub)
        stub.notify.assert_not_called()

    def test_no_results_list_skips_clear(self):
        stub = _make_stub(_w_results_list=None)
        _call(SearchActions._show_info, stub, "x")
        stub._switch_results_to.assert_called_once_with("rs-empty")


class TestNotifyOnce:
    def test_timer_active_suppresses(self):
        stub = _make_stub(_notify_timer=object())
        _call(SearchActions._notify_once, stub, "hi")
        stub.notify.assert_not_called()

    def test_sets_timer_to_clear(self):
        stub = _make_stub(_notify_timer=None)
        _call(SearchActions._notify_once, stub, "hi", timeout=3)
        stub.notify.assert_called_once_with("hi", timeout=3)
        stub.set_timer.assert_called_once()
        assert stub.set_timer.call_args[0][0] == 3.1
        assert stub._notify_timer == stub.set_timer.return_value


class TestStopHistorySpinner:
    def test_active_spinner_stopped(self):
        item = SimpleNamespace(stop_spinner=MagicMock())
        stub = _make_stub(_spinning_history_item=item)
        _call(SearchActions._stop_history_spinner, stub)
        item.stop_spinner.assert_called_once()
        assert stub._spinning_history_item is None

    def test_no_spinner_noop(self):
        stub = _make_stub(_spinning_history_item=None)
        _call(SearchActions._stop_history_spinner, stub)


class TestOnSearchResult:
    def test_dict_plays_media_request(self):
        stub = _make_stub()
        _call(SearchActions._on_search_result, stub, {"yt_id": "x", "title": "t"})
        stub._enter_landing_mode.assert_called_once()
        req = stub._play.call_args[0][0]
        assert isinstance(req, MediaRequest)
        assert req.yt_id == "x"

    def test_dict_no_empty_state(self):
        stub = _make_stub(_w_empty_state=None)
        _call(SearchActions._on_search_result, stub, {"yt_id": "x"})
        stub._play.assert_called_once()

    def test_blank_string_returns(self):
        stub = _make_stub()
        _call(SearchActions._on_search_result, stub, "   ")
        stub._perform_search.assert_not_called()

    def test_string_runs_search_with_history(self):
        stub = _make_stub(_search_histories={"youtube": []})
        with patch("nyrx.actions.search.update_config") as mock_update:
            _call(SearchActions._on_search_result, stub, "q")
        assert stub._search_histories["youtube"] == ["q"]
        mock_update.assert_called_once_with(search_histories={"youtube": ["q"]})
        stub._rebuild_history_list.assert_called_once()
        assert stub._query == "q"
        assert stub._all_results == []
        assert stub._page == 0
        stub._show_loading.assert_called_once()
        stub._perform_search.assert_called_once_with(90)

    def test_string_existing_moved_front(self):
        stub = _make_stub(_search_histories={"youtube": ["q2", "q"]})
        _call(SearchActions._on_search_result, stub, "q")
        assert stub._search_histories["youtube"] == ["q", "q2"]

    def test_string_caps_history_at_ten(self):
        stub = _make_stub(_search_histories={"youtube": [f"q{i}" for i in range(10)]})
        _call(SearchActions._on_search_result, stub, "new")
        h = stub._search_histories["youtube"]
        assert len(h) == 10
        assert h[0] == "new"
        assert h[-1] == "q8"

    def test_none_ignored(self):
        stub = _make_stub()
        _call(SearchActions._on_search_result, stub, None)
        stub._play.assert_not_called()
        stub._perform_search.assert_not_called()


class TestSwitchResultsTo:
    def test_success_sets_switcher_current(self):
        sw = SimpleNamespace(current="", children=[])
        stub = _make_stub(query_one=MagicMock(return_value=sw))
        _call(SearchActions._switch_results_to, stub, "rs-loading")
        assert sw.current == "rs-loading"
        stub.log.assert_called_once()

    def test_exception_logged(self):
        stub = _make_stub(query_one=MagicMock(side_effect=RuntimeError("boom")))
        _call(SearchActions._switch_results_to, stub, "rs-loading")
        stub.log.assert_called_once()


class TestRebuildHistoryList:
    def test_no_history_list_returns(self):
        stub = _make_stub(_w_history_list=None)
        _call(SearchActions._rebuild_history_list, stub)

    def test_empty_history_shows_placeholder(self):
        lv = _FakeListView()
        eh = SimpleNamespace(display=True)
        stub = _make_stub(
            _w_history_list=lv, _w_empty_heading=eh, _search_histories={"youtube": []}
        )
        _call(SearchActions._rebuild_history_list, stub)
        assert lv.display is True
        assert eh.display is True
        assert len(lv.items) == 1
        assert isinstance(lv.items[0], ListItem)
        stub._apply_history_gradient.assert_not_called()

    def test_with_items_shows_and_appends(self):
        lv = _FakeListView()
        eh = SimpleNamespace(display=False)
        stub = _make_stub(
            _w_history_list=lv,
            _w_empty_heading=eh,
            _search_histories={"youtube": ["a", "b"]},
        )
        _call(SearchActions._rebuild_history_list, stub)
        assert lv.display is True
        assert eh.display is True
        assert len(lv.items) == 2
        assert isinstance(lv.items[0], HistoryItem)
        assert lv.index == 0
        stub._apply_history_gradient.assert_called_once()


class TestRunHistorySearch:
    def test_offline_notifies(self):
        item = SimpleNamespace(_query="q", start_spinner=MagicMock())
        stub = _make_stub(_online=False)
        _call(SearchActions._run_history_search, stub, item)
        stub.notify.assert_called_once_with(
            "No internet connection.",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub._perform_search.assert_not_called()

    def test_online_runs_search(self):
        item = SimpleNamespace(_query="q", start_spinner=MagicMock())
        stub = _make_stub(_online=True)
        _call(SearchActions._run_history_search, stub, item)
        assert stub._query == "q"
        assert stub._all_results == []
        assert stub._page == 0
        stub.call_after_refresh.assert_called_once()
        stub._perform_search.assert_called_once_with(90)

    def test_online_start_spinner_closure_runs(self):
        item = SimpleNamespace(_query="q", start_spinner=MagicMock())
        stub = _make_stub(_online=True)
        _call(SearchActions._run_history_search, stub, item)
        start_spinner = stub.call_after_refresh.call_args[0][0]
        start_spinner()
        assert stub._spinning_history_item is item
        item.start_spinner.assert_called_once_with(stub)


class TestHistoryKey:
    @pytest.mark.parametrize(
        "source,expected",
        [
            (Source.YOUTUBE, "youtube"),
            (Source.SOUNDCLOUD, "soundcloud"),
            (Source.TV_MOVIES, "tv_movies"),
            (Source.RADIO, "youtube"),
            ("youtube", "youtube"),
        ],
    )
    def test_returns_key_for_source(self, source, expected):
        stub = _make_stub(_source=source)
        assert SearchActions._history_key(stub) == expected


class TestChipSpinner:
    def test_advance_not_spinning_noop(self):
        stub = _make_stub(_spinning_chip=False, query_one=MagicMock())
        _call(SearchActions._advance_chip_spinner, stub)
        stub.query_one.assert_not_called()

    def test_advance_spinning_cycles_frame(self):
        static = SimpleNamespace(update_chip_spinner=MagicMock())
        stub = _make_stub(
            _spinning_chip=True,
            _chip_spinner_idx=0,
            query_one=MagicMock(return_value=static),
        )
        _call(SearchActions._advance_chip_spinner, stub)
        assert stub._chip_spinner_idx == 1
        static.update_chip_spinner.assert_called_once_with(" \u2819")

    def test_advance_exception_logged_but_index_advances(self):
        stub = _make_stub(
            _spinning_chip=True,
            _chip_spinner_idx=0,
            query_one=MagicMock(side_effect=RuntimeError("boom")),
        )
        _call(SearchActions._advance_chip_spinner, stub)
        assert stub._chip_spinner_idx == 1

    def test_start_registers_interval(self):
        stub = _make_stub()
        _call(SearchActions._start_chip_spinner, stub)
        stub.set_interval.assert_called_once()
        assert stub.set_interval.call_args[0][0] == 0.08

    def test_stop_stops_timer_and_clears(self):
        timer = _Timer()
        static = SimpleNamespace(clear_chip_spinner=MagicMock())
        stub = _make_stub(
            _chip_spinner_timer=timer,
            _spinning_chip=True,
            query_one=MagicMock(return_value=static),
        )
        _call(SearchActions._stop_chip_spinner, stub)
        assert timer.stops == 1
        assert stub._chip_spinner_timer is None
        assert stub._spinning_chip is None
        static.clear_chip_spinner.assert_called_once_with()

    def test_stop_no_timer_noop(self):
        stub = _make_stub(_chip_spinner_timer=None)
        _call(SearchActions._stop_chip_spinner, stub)


class TestActionOpenSearch:
    def test_np_focused_returns(self):
        stub = _make_stub(_np_focused=True)
        _call(SearchActions.action_open_search, stub)
        stub.push_screen.assert_not_called()

    def test_tv_series_without_widget_falls_through_to_search(self):
        stub = _make_stub(_in_tv_series=True, _w_tv_series=None)
        with patch("nyrx.actions.search.SearchModal") as modal:
            _call(SearchActions.action_open_search, stub)
        modal.assert_called_once_with()
        stub.push_screen.assert_called_once()

    def test_tv_series_pushes_season_jump(self):
        tv_series = SimpleNamespace(_season_count=3, _current_season=2)
        stub = _make_stub(_in_tv_series=True, _w_tv_series=tv_series)
        with patch("nyrx.screens.season_jump.SeasonJumpModal") as modal:
            _call(SearchActions.action_open_search, stub)
        modal.assert_called_once_with(season_count=3, current_season=2)
        stub.push_screen.assert_called_once()
        assert stub.push_screen.call_args[0][1] is stub._on_season_jump_result

    def test_in_liked_focuses_search(self):
        focus = SimpleNamespace(focus=MagicMock())
        ls = SimpleNamespace(query_one=MagicMock(return_value=focus))
        stub = _make_stub(_in_liked=True, _w_liked_screen=ls)
        _call(SearchActions.action_open_search, stub)
        assert ls.query_one.call_args[0][0] == "#ls-search"
        focus.focus.assert_called_once()

    def test_in_liked_without_screen_returns(self):
        stub = _make_stub(_in_liked=True, _w_liked_screen=None)
        _call(SearchActions.action_open_search, stub)
        stub.push_screen.assert_not_called()

    def test_in_watchlist_focuses_search(self):
        focus = SimpleNamespace(focus=MagicMock())
        wl = SimpleNamespace(query_one=MagicMock(return_value=focus))
        stub = _make_stub(_in_watchlist=True, _w_watchlist_screen=wl)
        _call(SearchActions.action_open_search, stub)
        assert wl.query_one.call_args[0][0] == "#wl-search"
        focus.focus.assert_called_once()

    def test_in_following_focuses_filter(self):
        sc = SimpleNamespace(focus_filter=MagicMock())
        stub = _make_stub(_in_following=True, _w_sc_home=sc)
        _call(SearchActions.action_open_search, stub)
        sc.focus_filter.assert_called_once()

    def test_radio_opens_filter(self):
        stub = _make_stub(_source="radio")
        _call(SearchActions.action_open_search, stub)
        stub.action_open_filter.assert_called_once()

    def test_offline_notifies(self):
        stub = _make_stub(_online=False)
        _call(SearchActions.action_open_search, stub)
        stub.notify.assert_called_once_with(
            "No internet connection. Search unavailable.",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        stub.push_screen.assert_not_called()

    def test_online_pushes_search_modal(self):
        stub = _make_stub(_online=True)
        with patch("nyrx.actions.search.SearchModal") as modal:
            _call(SearchActions.action_open_search, stub)
        modal.assert_called_once_with()
        stub.push_screen.assert_called_once()
