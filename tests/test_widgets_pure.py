# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the pure, no-DOM chunks embedded in ``screens/``/``widgets/`` (Phase 5C).

These methods are directly callable leaves: no App, no DOM, zero logic mocks:
reached via ``object.__new__`` / ``stub_self`` so ``__init__`` (which needs the
widget tree) never runs.  ``compose``/``on_mount``/key-handler bodies are
integration territory and deliberately excluded.

``_history_key`` is intentionally absent: it lives in ``actions/search.py`` and
is already fully covered by ``tests/test_actions_search.py`` (``TestHistoryKey``).
The EpisodePicker empty-``_episodes`` guard is also absent: ``__init__`` raises
``ValueError`` on ``episodes.index(initial)``, so an empty list is unreachable.

Expected values are hand-computed from the UI contract, not read from the code.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

import pytest

from nyrx.screens.collection_browser import CollectionBrowser
from nyrx.screens.episode_range import EpisodePicker
from nyrx.screens.queue import QueueModal
from nyrx.widgets.download import DownloadWidget
from nyrx.widgets.watchlist_screen import WatchlistScreen
from tests.fakes import stub_self


class TestWatchlistFormatMeta:
    """``WatchlistScreen._format_meta``: dict → Text detail block (pure, no self)."""

    def test_movie_with_tagline(self) -> None:
        data = {
            "title": "Inception",
            "media_type": "movie",
            "tagline": "Your mind is the scene of the crime",
            "rating": 8.8,
            "year": "2010",
            "runtime": 148,
            "genres": '["Sci-Fi", "Action", "Thriller"]',
            "overview": "A thief who steals corporate secrets.",
        }
        text = WatchlistScreen._format_meta(None, data)
        assert text.plain == (
            "Inception\n"
            "Your mind is the scene of the crime\n\n"
            "\u2605 8.8 \u00b7 2010 \u00b7 2h28min\n"
            "Sci-Fi \u00b7 Action \u00b7 Thriller\n\n"
            "A thief who steals corporate secrets."
        )

    def test_title_and_tagline_styles(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "Inception",
                "media_type": "movie",
                "tagline": "T",
                "rating": 8.0,
                "year": "2010",
                "runtime": 120,
                "genres": '["A"]',
                "overview": "",
            },
        )
        assert text.spans[0].style == "bold white"
        assert text.spans[1].style == "italic #808080"

    def test_movie_no_tagline(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "Memento",
                "media_type": "movie",
                "tagline": None,
                "rating": 8.4,
                "year": "2000",
                "runtime": 113,
                "genres": "",
                "overview": "",
            },
        )
        assert text.plain == "Memento\n\n\u2605 8.4 \u00b7 2000 \u00b7 1h53min\n\n"

    def test_movie_rating_below_five_no_star(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "Flop",
                "media_type": "movie",
                "tagline": "",
                "rating": 4.9,
                "year": 2021,
                "runtime": 0,
                "genres": "[]",
                "overview": "",
            },
        )
        assert text.plain == "Flop\n\n4.9 \u00b7 2021\n\n"

    @pytest.mark.parametrize(
        ("runtime", "suffix"),
        [
            (120, " \u00b7 2h"),
            (45, " \u00b7 45min"),
            (0, ""),
            (None, ""),
        ],
    )
    def test_movie_runtime_variants(self, runtime: int | None, suffix: str) -> None:
        # The " · " separator only appears when a runtime string exists; with
        # no runtime the meta line ends right after the year.
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "R",
                "media_type": "movie",
                "rating": 5.0,
                "year": "2021",
                "runtime": runtime,
                "genres": None,
                "overview": "",
            },
        )
        assert text.plain == f"R\n\n\u2605 5.0 \u00b7 2021{suffix}\n\n"

    def test_tv_singular_season(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "S",
                "media_type": "tv",
                "tagline": "ignored for tv",
                "rating": 9.0,
                "year": "2010",
                "season_count": 1,
                "genres": "",
                "overview": "",
            },
        )
        assert text.plain == "S\n\u2605 9.0 \u00b7 2010 \u00b7 1 Season\n\n"

    def test_tv_plural_seasons(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "BB",
                "media_type": "tv",
                "rating": 9.5,
                "year": "2008",
                "season_count": 5,
                "genres": '["Drama"]',
                "overview": "over",
            },
        )
        assert (
            text.plain == "BB\n\u2605 9.5 \u00b7 2008 \u00b7 5 Seasons\nDrama\n\nover"
        )

    def test_tv_falsy_season_count_trailing_dot(self) -> None:
        # PIN: season_count falsy -> empty season_str -> trailing " · " before \n.
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "Q",
                "media_type": "tv",
                "rating": 9.0,
                "year": "2010",
                "season_count": 0,
                "genres": "",
                "overview": "",
            },
        )
        assert text.plain == "Q\n\u2605 9.0 \u00b7 2010 \u00b7 \n\n"

    def test_default_media_type_is_movie(self) -> None:
        text = WatchlistScreen._format_meta(None, {"title": "X"})
        assert text.plain == "X\n\n0.0 \u00b7 \n\n"

    @pytest.mark.parametrize(
        "genres",
        [
            "",
            None,
            "not json",
            '{"a": 1}',
            "[]",
        ],
    )
    def test_genre_fallbacks_emit_blank_line(self, genres) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "G",
                "media_type": "movie",
                "rating": 5.0,
                "year": "",
                "runtime": 0,
                "genres": genres,
                "overview": "",
            },
        )
        assert text.plain == "G\n\n\u2605 5.0 \u00b7 \n\n"

    def test_genres_joins_first_three(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "G",
                "media_type": "movie",
                "rating": 5.0,
                "year": "",
                "runtime": 0,
                "genres": '["a", "b", "c", "d"]',
                "overview": "",
            },
        )
        assert text.plain == "G\n\n\u2605 5.0 \u00b7 \na \u00b7 b \u00b7 c\n\n"

    def test_genres_passthrough_list_input(self) -> None:
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "G",
                "media_type": "movie",
                "rating": 5.0,
                "year": "",
                "runtime": 0,
                "genres": ["a", "b", "c", "d"],
                "overview": "",
            },
        )
        assert text.plain == "G\n\n\u2605 5.0 \u00b7 \na \u00b7 b \u00b7 c\n\n"

    def test_overview_269_chars_unchanged(self) -> None:
        overview = "x" * 269
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "G",
                "media_type": "movie",
                "rating": 5.0,
                "year": "",
                "runtime": 0,
                "genres": "",
                "overview": overview,
            },
        )
        assert text.plain.endswith(overview)

    def test_overview_270_chars_truncated_with_ellipsis(self) -> None:
        overview = "x" * 270
        text = WatchlistScreen._format_meta(
            None,
            {
                "title": "G",
                "media_type": "movie",
                "rating": 5.0,
                "year": "",
                "runtime": 0,
                "genres": "",
                "overview": overview,
            },
        )
        assert text.plain.endswith("x" * 269 + "...")


class TestCollectionBrowserTruncateLabel:
    """``_truncate_label``: needs ``self._COL_WIDTH`` (class attr 45)."""

    @staticmethod
    def _call(label: str, count: int) -> str:
        return CollectionBrowser._truncate_label(
            object.__new__(CollectionBrowser), label, count
        )

    def test_short_unchanged(self) -> None:
        assert self._call("Short", 5) == "Short"

    def test_exact_boundary_unchanged(self) -> None:
        # max_label_len = 45 - len("(5)") - 2 = 40
        assert self._call("x" * 40, 5) == "x" * 40

    def test_long_no_space_hard_cut(self) -> None:
        # slice to max_label_len - 1 = 39, nothing to back off to
        assert self._call("x" * 50, 5) == "x" * 39

    def test_long_truncated_at_last_word(self) -> None:
        label = "abcdefghij " * 10
        assert self._call(label, 5) == "abcdefghij abcdefghij abcdefghij"

    def test_label_starting_with_space_keeps_hard_cut(self) -> None:
        # rfind(" ") == 0 does not trigger word back-off (guard is > 0)
        assert self._call(" " + "x" * 50, 5) == " " + "x" * 38

    def test_count_width_shrinks_max_len(self) -> None:
        # count=100 -> "(100)" len 5 -> max_label_len = 45 - 5 - 2 = 38
        browser = object.__new__(CollectionBrowser)
        assert CollectionBrowser._truncate_label(browser, "x" * 38, 100) == "x" * 38
        assert CollectionBrowser._truncate_label(browser, "x" * 40, 100) == "x" * 37


class TestCollectionBrowserBuildTrackCell:
    """``_build_track_cell``: pure (no self), module helpers stay real."""

    def test_full_cell(self) -> None:
        cell = CollectionBrowser._build_track_cell(
            None,
            {
                "title": "Track",
                "channel": "Artist",
                "duration": 150,
                "view_count": 1000,
                "like_count": 500,
            },
        )
        assert cell.plain == " Track\n Artist  \u25b6 1.0K  \u2764\ufe0e 500  2:30\n"

    def test_zero_views_likes_omit_segments(self) -> None:
        cell = CollectionBrowser._build_track_cell(
            None,
            {
                "title": "T",
                "channel": "C",
                "duration": 60,
                "view_count": 0,
                "like_count": 0,
            },
        )
        assert cell.plain == " T\n C  1:00\n"

    def test_likes_only(self) -> None:
        cell = CollectionBrowser._build_track_cell(
            None,
            {
                "title": "T",
                "channel": "C",
                "duration": 100,
                "view_count": 0,
                "like_count": 500,
            },
        )
        assert cell.plain == " T\n C  \u2764\ufe0e 500  1:40\n"

    def test_zero_duration_question_mark(self) -> None:
        cell = CollectionBrowser._build_track_cell(
            None,
            {
                "title": "T",
                "channel": "C",
                "duration": 0,
                "view_count": 0,
                "like_count": 0,
            },
        )
        assert cell.plain == " T\n C  ?\n"

    def test_missing_fields_default_question(self) -> None:
        cell = CollectionBrowser._build_track_cell(None, {})
        assert cell.plain == " ?\n ?  ?\n"


class TestQueueModalMakeQueueLabel:
    """``_make_queue_label``: marks queued items from ``_marked_keys``."""

    @staticmethod
    def _item(audio_only: bool = False, title: str = "T") -> SimpleNamespace:
        return SimpleNamespace(audio_only=audio_only, title=title, uid="u1")

    def test_unmarked_audio(self) -> None:
        qm = stub_self(QueueModal, _marked_keys=set())
        label = QueueModal._make_queue_label(
            qm, self._item(audio_only=True, title="Song")
        )
        assert label.plain == "  [audio]  Song"

    def test_unmarked_video(self) -> None:
        qm = stub_self(QueueModal, _marked_keys=set())
        label = QueueModal._make_queue_label(
            qm, self._item(audio_only=False, title="V")
        )
        assert label.plain == "  [video]  V"

    def test_marked_audio_prefixes_checkmark(self) -> None:
        qm = stub_self(QueueModal, _marked_keys={"u1"})
        label = QueueModal._make_queue_label(
            qm, self._item(audio_only=True, title="Song")
        )
        assert label.plain == "\u2713 [audio]  Song"

    def test_other_uid_not_marked(self) -> None:
        qm = stub_self(QueueModal, _marked_keys={"u0"})
        label = QueueModal._make_queue_label(
            qm, self._item(audio_only=False, title="V")
        )
        assert label.plain == "  [video]  V"

    def test_extra_args_ignored(self) -> None:
        qm = stub_self(QueueModal, _marked_keys=set())
        label = QueueModal._make_queue_label(
            qm, self._item(audio_only=False, title="V")
        )
        assert label.plain == "  [video]  V"


class TestQueueModalNavigation:
    """``QueueModal.on_key``: Home/End map to first/last playable rows."""

    @staticmethod
    def _dt(row_count: int = 5, cursor: int = 3) -> SimpleNamespace:
        return SimpleNamespace(
            size=SimpleNamespace(height=10),
            row_count=row_count,
            cursor_coordinate=SimpleNamespace(row=cursor),
            move_cursor=MagicMock(),
            scroll_home=MagicMock(),
        )

    @staticmethod
    def _event(key: str) -> MagicMock:
        ev = MagicMock()
        ev.key = key
        return ev

    def test_home_moves_to_first_playable_row(self) -> None:
        dt = self._dt()
        qm = stub_self(QueueModal, query_one=MagicMock(return_value=dt))
        QueueModal.on_key(qm, self._event("home"))
        dt.move_cursor.assert_called_once_with(row=1)
        dt.scroll_home.assert_called_once_with(animate=False)

    def test_ctrl_home_moves_to_first_playable_row(self) -> None:
        dt = self._dt()
        qm = stub_self(QueueModal, query_one=MagicMock(return_value=dt))
        QueueModal.on_key(qm, self._event("ctrl+home"))
        dt.move_cursor.assert_called_once_with(row=1)

    def test_end_moves_to_last_row(self) -> None:
        dt = self._dt(row_count=5)
        qm = stub_self(QueueModal, query_one=MagicMock(return_value=dt))
        QueueModal.on_key(qm, self._event("end"))
        dt.move_cursor.assert_called_once_with(row=4)
        dt.scroll_home.assert_not_called()

    def test_ctrl_end_moves_to_last_row(self) -> None:
        dt = self._dt(row_count=5)
        qm = stub_self(QueueModal, query_one=MagicMock(return_value=dt))
        QueueModal.on_key(qm, self._event("ctrl+end"))
        dt.move_cursor.assert_called_once_with(row=4)

    def test_up_still_skips_row_zero(self) -> None:
        dt = self._dt(cursor=1)
        qm = stub_self(QueueModal, query_one=MagicMock(return_value=dt))
        QueueModal.on_key(qm, self._event("up"))
        dt.move_cursor.assert_called_once_with(row=1)


class TestQueueModalDeleteModeKeyN:
    """``QueueModal.key_n``: disabled in delete mode."""

    def test_delete_mode_blocks_reorder(self) -> None:
        qm = stub_self(QueueModal, _delete_mode=True, query_one=MagicMock())
        ev = MagicMock()
        QueueModal.key_n(qm, ev)
        ev.stop.assert_not_called()
        qm.query_one.assert_not_called()

    def test_normal_mode_reorders(self) -> None:
        app = SimpleNamespace(
            _playback_queue=SimpleNamespace(move_to_front=MagicMock(return_value=True)),
            _sync_np_widget=MagicMock(),
        )
        dt = SimpleNamespace(
            cursor_coordinate=SimpleNamespace(row=2),
            row_count=3,
            move_cursor=MagicMock(),
            focus=MagicMock(),
        )
        qm = stub_self(
            QueueModal,
            _delete_mode=False,
            _app=app,
            query_one=MagicMock(return_value=dt),
            _rebuild_lists=MagicMock(),
        )
        QueueModal.key_n(qm, MagicMock())
        app._playback_queue.move_to_front.assert_called_once_with(1)
        qm._rebuild_lists.assert_called_once()


class TestEpisodePickerValue:
    """``EpisodePicker.value``: reads ``_episodes[_index]``."""

    @staticmethod
    def _picker(index: int) -> EpisodePicker:
        picker = object.__new__(EpisodePicker)
        picker._episodes = [(1, 1), (1, 2), (2, 1)]
        picker._index = index
        return picker

    def test_head(self) -> None:
        assert self._picker(0).value == (1, 1)

    def test_middle(self) -> None:
        assert self._picker(1).value == (1, 2)

    def test_second_season(self) -> None:
        assert self._picker(2).value == (2, 1)


class TestEpisodePickerWrap:
    """``key_left``/``key_right`` wrap via ``% len`` over the flat list."""

    FLAT = [(1, e) for e in range(1, 13)] + [(2, 1), (2, 2)]

    @staticmethod
    def _picker(index: int) -> EpisodePicker:
        picker = object.__new__(EpisodePicker)
        picker._episodes = list(TestEpisodePickerWrap.FLAT)
        picker._index = index
        picker._update_display = MagicMock()
        picker.post_message = MagicMock()
        return picker

    def test_right_wraps_across_season(self) -> None:
        # (1,12) at index 11 -> right -> (2,1)
        picker = self._picker(11)
        EpisodePicker.key_right(picker)
        assert picker.value == (2, 1)

    def test_right_last_element_wraps_to_head(self) -> None:
        # (2,2) is last -> right wraps to (1,1)
        picker = self._picker(len(self.FLAT) - 1)
        EpisodePicker.key_right(picker)
        assert picker.value == (1, 1)

    def test_left_from_head_wraps_to_tail(self) -> None:
        # (1,1) -> left -> last of the last season
        picker = self._picker(0)
        EpisodePicker.key_left(picker)
        assert picker.value == (2, 2)

    def test_plain_key_moves_within_list(self) -> None:
        picker = self._picker(5)
        EpisodePicker.key_right(picker)
        assert picker.value == self.FLAT[6]

    def test_wrap_updates_display_and_posts_changed(self) -> None:
        picker = self._picker(0)
        EpisodePicker.key_left(picker)
        picker._update_display.assert_called_once_with()
        assert picker.post_message.call_count == 1
        (msg,) = picker.post_message.call_args[0]
        assert isinstance(msg, EpisodePicker.Changed)


class TestDownloadScannerPosition:
    """``_scanner_position``: 4-phase 54-frame (width 8) / 46-frame (width 4) cycle."""

    @pytest.mark.parametrize(
        ("frame", "expected"),
        [
            (0, (0, True, 0, None)),
            (7, (7, True, 0, None)),
            (8, (7, True, 0, 9)),
            (16, (7, True, 8, 9)),
            (17, (6, False, 0, None)),
            (23, (0, False, 0, None)),
            (24, (0, False, 0, 30)),
            (53, (0, False, 29, 30)),
            (54, (0, True, 0, None)),
        ],
    )
    def test_width8_cycle_phases(self, frame: int, expected: tuple) -> None:
        assert DownloadWidget._scanner_position(frame) == expected

    @pytest.mark.parametrize(
        ("frame", "expected"),
        [
            (0, (0, True, 0, None)),
            (12, (3, True, 8, 9)),
            (13, (2, False, 0, None)),
            (15, (0, False, 0, None)),
            (16, (0, False, 0, 30)),
            (45, (0, False, 29, 30)),
        ],
    )
    def test_width4_cycle_phases(self, frame: int, expected: tuple) -> None:
        assert DownloadWidget._scanner_position(frame, width=4) == expected


class TestDownloadScannerTrailColors:
    """``_scanner_trail_colors``: head 1.0, bloom 1.15, then 0.65**(i-1) decay."""

    def test_default_six_steps(self) -> None:
        assert DownloadWidget._scanner_trail_colors("#ffca85") == [
            "#ffca85",
            "#ffe898",
            "#a58356",
            "#6b5538",
            "#463724",
            "#2d2417",
        ]

    def test_white_clamped(self) -> None:
        # 255 * 1.15 clamps back to 255 for the bloom step.
        assert DownloadWidget._scanner_trail_colors("#ffffff") == [
            "#ffffff",
            "#ffffff",
            "#a5a5a5",
            "#6b6b6b",
            "#464646",
            "#2d2d2d",
        ]

    def test_black(self) -> None:
        assert DownloadWidget._scanner_trail_colors("#000000") == ["#000000"] * 6

    def test_custom_steps(self) -> None:
        assert DownloadWidget._scanner_trail_colors("#ffca85", 3) == [
            "#ffca85",
            "#ffe898",
            "#a58356",
        ]


class TestDownloadBuildScannerFrames:
    """``_build_scanner_frames``: structural invariants + phase-boundary heads."""

    HEAD = "\u25a0"

    def test_width8_cycle(self) -> None:
        frames = DownloadWidget._build_scanner_frames("#ffca85")
        assert len(frames) == 54
        assert all(len(f.plain) == 8 for f in frames)

    def test_width4_cycle(self) -> None:
        frames = DownloadWidget._build_scanner_frames("#a277ff", width=4)
        assert len(frames) == 46
        assert all(len(f.plain) == 4 for f in frames)

    def test_every_frame_has_head(self) -> None:
        frames = DownloadWidget._build_scanner_frames("#ffca85")
        assert all(self.HEAD in f.plain for f in frames)

    def test_phase_boundary_head_positions(self) -> None:
        frames = DownloadWidget._build_scanner_frames("#ffca85")
        assert frames[0].plain[0] == self.HEAD  # forward, head at 0
        assert frames[7].plain[7] == self.HEAD  # forward, head at 7
        assert frames[17].plain[6] == self.HEAD  # backward, head at 6
        assert frames[53].plain[0] == self.HEAD  # start-hold, head at 0

    def test_width4_phase_boundary(self) -> None:
        frames = DownloadWidget._build_scanner_frames("#ffca85", width=4)
        assert frames[0].plain[0] == self.HEAD
        assert frames[3].plain[3] == self.HEAD


class TestDownloadTrunc:
    """``_trunc``: ``text[:max_len-1] + "…"`` only when the text is too long."""

    @pytest.mark.parametrize(
        ("text", "max_len", "expected"),
        [
            ("abc", 5, "abc"),
            ("abcdef", 6, "abcdef"),
            ("abcdef", 7, "abcdef"),
            ("", 5, ""),
            ("abcdef", 5, "abcd\u2026"),
            ("abcdef", 1, "\u2026"),
        ],
    )
    def test_trunc(self, text: str, max_len: int, expected: str) -> None:
        assert DownloadWidget._trunc(text, max_len) == expected


class TestDownloadWidgetKeyEscape:
    """``DownloadWidget.key_escape``: mirrors BaseNowPlaying routing."""

    @staticmethod
    def _stub(**attrs):
        app = SimpleNamespace(
            _cancel_active=False,
            _exit_cancel_mode=MagicMock(),
            _focus_main_panel=MagicMock(),
        )
        defaults = dict(
            _dl_select_mode=None,
            _cancel_dl_selection=MagicMock(),
        )
        defaults.update(attrs)
        w = object.__new__(DownloadWidget)
        for name, value in defaults.items():
            setattr(w, name, value)
        return w, app

    def test_selector_mode_cancels_and_stops(self) -> None:
        ev = MagicMock()
        w, app = self._stub(_dl_select_mode="type")
        with patch.object(
            DownloadWidget, "app", new_callable=PropertyMock, return_value=app
        ):
            DownloadWidget.key_escape(w, ev)
        w._cancel_dl_selection.assert_called_once()
        ev.stop.assert_called_once()
        app._focus_main_panel.assert_not_called()

    def test_cancel_active_exits_and_stops(self) -> None:
        ev = MagicMock()
        w, app = self._stub()
        app._cancel_active = True
        with patch.object(
            DownloadWidget, "app", new_callable=PropertyMock, return_value=app
        ):
            DownloadWidget.key_escape(w, ev)
        app._exit_cancel_mode.assert_called_once()
        ev.stop.assert_called_once()
        app._focus_main_panel.assert_not_called()

    def test_normal_case_focuses_main_and_stops(self) -> None:
        ev = MagicMock()
        w, app = self._stub()
        with patch.object(
            DownloadWidget, "app", new_callable=PropertyMock, return_value=app
        ):
            DownloadWidget.key_escape(w, ev)
        app._focus_main_panel.assert_called_once()
        ev.stop.assert_called_once()
        w._cancel_dl_selection.assert_not_called()
