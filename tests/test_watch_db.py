# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the SQLite watch-history mirror (watch_db).

Queries resolve the DB via ``watch_db._get_db()`` at call time, so pointing
``WATCH_HISTORY_DB_PATH`` at ``tmp_path`` and calling ``init_watch_db()``
gives a hermetic full-schema table.  All expected values are hand-computed
from the ≥80% / manual-escape / ``IS`` semantics in the documented contract.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from nyrx import watch_db


@pytest.fixture(autouse=True)
def _hermetic_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Redirect all watch_db paths to tmp_path and init a fresh DB."""
    db_path = tmp_path / "watch_history.db"
    tracker = tmp_path / "tracker_v4.jsonl"
    offset = tmp_path / "tracker_offset"
    monkeypatch.setattr("nyrx.watch_db.WATCH_HISTORY_DB_PATH", db_path)
    monkeypatch.setattr("nyrx.watch_db.TRACKER_V4_PATH", tracker)
    monkeypatch.setattr("nyrx.watch_db.TRACKER_OFFSET_PATH", offset)
    watch_db.init_watch_db()
    return tracker


def _insert(db_path: Path, rows: list[dict]) -> None:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        for r in rows:
            cols = ", ".join(r.keys())
            marks = ", ".join("?" * len(r))
            conn.execute(
                f"INSERT INTO watch_history ({cols}) VALUES ({marks})",
                tuple(r.values()),
            )
        conn.commit()
    finally:
        conn.close()


def _row_count(db_path: Path) -> int:
    import sqlite3

    conn = sqlite3.connect(str(db_path))
    try:
        return conn.execute("SELECT COUNT(*) FROM watch_history").fetchone()[0]
    finally:
        conn.close()


def _movie_row(yt_id: str, watched: int, duration: int, **extra) -> dict:
    return {
        "_v": 4,
        "ts": 0,
        "source": "tv_movies",
        "yt_id": yt_id,
        "media_type": "movie",
        "watched_secs": watched,
        "duration_secs": duration,
        **extra,
    }


def _tv_row(
    yt_id: str, season: int, episode: int, watched: int, duration: int, **extra
) -> dict:
    return {
        "_v": 4,
        "ts": 0,
        "source": "tv_movies",
        "yt_id": yt_id,
        "media_type": "tv",
        "season_number": season,
        "episode_number": episode,
        "watched_secs": watched,
        "duration_secs": duration,
        **extra,
    }


# ---------------------------------------------------------------------------
# 1.2.1 get_movie_watched: 80% boundary (the watched badge)
# ---------------------------------------------------------------------------


class TestGetMovieWatched:
    def test_exactly_80_percent_included(self, _hermetic_db: Path) -> None:
        _insert(_hermetic_db.parent / "watch_history.db", [_movie_row("a", 80, 100)])
        assert watch_db.get_movie_watched(["a"]) == {"a"}

    def test_79_percent_excluded(self, _hermetic_db: Path) -> None:
        _insert(_hermetic_db.parent / "watch_history.db", [_movie_row("a", 79, 100)])
        assert watch_db.get_movie_watched(["a"]) == set()

    def test_100_percent_included(self, _hermetic_db: Path) -> None:
        _insert(_hermetic_db.parent / "watch_history.db", [_movie_row("a", 100, 100)])
        assert watch_db.get_movie_watched(["a"]) == {"a"}

    def test_aggregation_across_rows(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _movie_row("a", 30, 100),
                _movie_row("a", 50, 100),
            ],
        )
        assert watch_db.get_movie_watched(["a"]) == {"a"}

    def test_zero_duration_row_excluded(self, _hermetic_db: Path) -> None:
        _insert(_hermetic_db.parent / "watch_history.db", [_movie_row("a", 100, 0)])
        assert watch_db.get_movie_watched(["a"]) == set()

    def test_empty_list_returns_empty_set(self, _hermetic_db: Path) -> None:
        assert watch_db.get_movie_watched([]) == set()

    def test_tv_rows_with_same_id_not_counted(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _tv_row("a", 1, 1, 100, 100),
            ],
        )
        assert watch_db.get_movie_watched(["a"]) == set()

    def test_manual_mark_escape_hatch(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _movie_row("a", 1, 100, reason="manual"),
            ],
        )
        assert watch_db.get_movie_watched(["a"]) == {"a"}


# ---------------------------------------------------------------------------
# 1.2.3 get_episode_status: tv-scoped (season, episode) set + boundary
# ---------------------------------------------------------------------------


class TestGetEpisodeStatus:
    def test_100_percent_in_set(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db", [_tv_row("s", 2, 3, 100, 100)]
        )
        assert watch_db.get_episode_status("s") == {(2, 3)}

    def test_79_percent_not_in_set(self, _hermetic_db: Path) -> None:
        _insert(_hermetic_db.parent / "watch_history.db", [_tv_row("s", 2, 4, 79, 100)])
        assert watch_db.get_episode_status("s") == set()

    def test_manual_row_in_set(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _tv_row("s", 2, 3, 1, 100, reason="manual"),
            ],
        )
        assert watch_db.get_episode_status("s") == {(2, 3)}

    def test_movie_row_same_yt_id_not_returned(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _movie_row("s", 100, 100),
            ],
        )
        assert watch_db.get_episode_status("s") == set()

    def test_empty_returns_empty_set(self, _hermetic_db: Path) -> None:
        assert watch_db.get_episode_status("s") == set()


# ---------------------------------------------------------------------------
# 1.2.4 get_last_watched_season: max, tv-only, None on empty
# ---------------------------------------------------------------------------


class TestGetLastWatchedSeason:
    def test_highest_season_returned(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _tv_row("s", 2, 1, 100, 100),
                _tv_row("s", 5, 1, 100, 100),
            ],
        )
        assert watch_db.get_last_watched_season("s") == 5

    def test_no_rows_returns_none(self, _hermetic_db: Path) -> None:
        assert watch_db.get_last_watched_season("s") is None

    def test_movie_rows_ignored(self, _hermetic_db: Path) -> None:
        _insert(
            _hermetic_db.parent / "watch_history.db",
            [
                _movie_row("s", 100, 100),
            ],
        )
        assert watch_db.get_last_watched_season("s") is None


# ---------------------------------------------------------------------------
# 1.2.5 mark_watched / unmark_watched: round-trip incl. NULL semantics
# ---------------------------------------------------------------------------


class TestMarkUnmark:
    def test_mark_then_unmark_movie(self, _hermetic_db: Path) -> None:
        watch_db.mark_watched("m", "movie")
        assert watch_db.get_movie_watched(["m"]) == {"m"}
        watch_db.unmark_watched("m")
        assert watch_db.get_movie_watched(["m"]) == set()

    def test_mark_then_unmark_episode(self, _hermetic_db: Path) -> None:
        watch_db.mark_watched("s", "tv", season_number=2, episode_number=3)
        assert watch_db.get_episode_status("s") == {(2, 3)}
        watch_db.unmark_watched("s", season_number=2, episode_number=3)
        assert watch_db.get_episode_status("s") == set()

    def test_unmark_other_episode_keeps_this_one(self, _hermetic_db: Path) -> None:
        watch_db.mark_watched("s", "tv", season_number=2, episode_number=3)
        watch_db.unmark_watched("s", season_number=2, episode_number=4)
        assert watch_db.get_episode_status("s") == {(2, 3)}

    def test_reunmark_is_idempotent(self, _hermetic_db: Path) -> None:
        watch_db.mark_watched("m", "movie")
        watch_db.unmark_watched("m")
        watch_db.unmark_watched("m")
        assert watch_db.get_movie_watched(["m"]) == set()


# ---------------------------------------------------------------------------
# 1.2.6–1.2.8 sync_from_tracker: ingestion, radio/blank skip, malformed
# ---------------------------------------------------------------------------


class TestSyncFromTracker:
    def _write_lines(self, path: Path, lines: list[str]) -> None:
        path.write_text("\n".join(lines) + "\n")

    def test_full_sync_and_offset_persisted(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        self._write_lines(
            tracker,
            [
                json.dumps(
                    {
                        "source": "youtube",
                        "yt_id": "a",
                        "watched_secs": 10,
                        "duration_secs": 100,
                    }
                ),
                json.dumps(
                    {
                        "source": "tv_movies",
                        "yt_id": "b",
                        "media_type": "movie",
                        "watched_secs": 80,
                        "duration_secs": 100,
                    }
                ),
            ],
        )
        watch_db.sync_from_tracker()
        assert _row_count(_hermetic_db.parent / "watch_history.db") == 2
        assert watch_db.get_movie_watched(["b"]) == {"b"}
        assert watch_db.TRACKER_OFFSET_PATH.read_text() == str(tracker.stat().st_size)

    def test_second_sync_is_idempotent(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        self._write_lines(
            tracker,
            [
                json.dumps(
                    {
                        "source": "youtube",
                        "yt_id": "a",
                        "watched_secs": 80,
                        "duration_secs": 100,
                    }
                ),
            ],
        )
        watch_db.sync_from_tracker()
        watch_db.sync_from_tracker()
        assert _row_count(_hermetic_db.parent / "watch_history.db") == 1

    def test_radio_rows_and_blank_lines_skipped(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        self._write_lines(
            tracker,
            [
                json.dumps(
                    {
                        "source": "youtube",
                        "yt_id": "a",
                        "watched_secs": 80,
                        "duration_secs": 100,
                    }
                ),
                json.dumps({"source": "radio", "yt_id": "r"}),
                "",
            ],
        )
        watch_db.sync_from_tracker()
        assert _row_count(_hermetic_db.parent / "watch_history.db") == 1

    def test_malformed_line_stops_and_retries(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        good = json.dumps(
            {
                "source": "youtube",
                "yt_id": "a",
                "watched_secs": 80,
                "duration_secs": 100,
            }
        )
        self._write_lines(
            tracker,
            [
                good,
                "{BROKEN-JSON",
                json.dumps(
                    {
                        "source": "youtube",
                        "yt_id": "c",
                        "watched_secs": 80,
                        "duration_secs": 100,
                    }
                ),
            ],
        )
        watch_db.sync_from_tracker()
        assert _row_count(_hermetic_db.parent / "watch_history.db") == 1
        assert watch_db.TRACKER_OFFSET_PATH.read_text() == str(len(good) + 1)

    def test_missing_tracker_is_noop(self, _hermetic_db: Path) -> None:
        watch_db.sync_from_tracker()
        assert _row_count(_hermetic_db.parent / "watch_history.db") == 0

    def test_corrupt_offset_treated_as_zero(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        self._write_lines(
            tracker,
            [
                json.dumps(
                    {
                        "source": "tv_movies",
                        "yt_id": "a",
                        "media_type": "movie",
                        "watched_secs": 80,
                        "duration_secs": 100,
                    }
                ),
            ],
        )
        watch_db.TRACKER_OFFSET_PATH.write_text("abc")
        watch_db.sync_from_tracker()
        assert watch_db.get_movie_watched(["a"]) == {"a"}


# ---------------------------------------------------------------------------
# 1.2.9 _insert_entry: None/missing field coercion
# ---------------------------------------------------------------------------


class TestInsertEntry:
    def test_none_fields_coerce_to_zero(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        tracker.write_text(
            json.dumps(
                {
                    "source": "youtube",
                    "yt_id": "a",
                    "watched_secs": None,
                    "duration_secs": None,
                }
            )
            + "\n"
        )
        watch_db.sync_from_tracker()
        assert watch_db.get_movie_watched(["a"]) == set()

    def test_missing_keys_use_defaults(self, _hermetic_db: Path) -> None:
        tracker = _hermetic_db
        tracker.write_text(json.dumps({"source": "youtube", "yt_id": "a"}) + "\n")
        watch_db.sync_from_tracker()
        assert watch_db.get_movie_watched(["a"]) == set()
