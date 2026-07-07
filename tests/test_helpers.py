# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for helpers.py (require_key).

require_key() is used at 11 call sites across 4 mixin files to narrow
StringKey.value (str | None) -> str.  Tests verify the contract
("raises on None, returns str otherwise") without assuming the
implementation (bare assert vs raise ValueError).
"""

from __future__ import annotations

import pytest

from nyrx.helpers import iterate_episode_range, require_key


class TestRequireKey:
    def test_raises_on_none(self) -> None:
        with pytest.raises((AssertionError, ValueError)):
            require_key(None)

    def test_returns_value_on_str(self) -> None:
        result = require_key("abc")
        assert result == "abc"

    def test_returns_value_on_empty_str(self) -> None:
        # Catches a falsy-guard bug: `if not value:` instead of
        # `if value is None:`.  Empty-string keys are theoretically
        # possible (StringKey("")) but rare in practice: this is a
        # defensive-boundary test.
        result = require_key("")
        assert result == ""


class TestIterateEpisodeRange:
    """Boundary suite for ``iterate_episode_range`` (the bulk "mark S1E1→S2E2
    watched" flow).  Off-by-one here silently marks the wrong episode set or
    loops forever, so every wrap/inclusion edge is pinned explicitly.
    """

    def test_same_season_single(self) -> None:
        assert iterate_episode_range(1, 1, 1, 1, {1: 10}) == [(1, 1)]

    def test_same_season_run(self) -> None:
        assert iterate_episode_range(1, 1, 1, 3, {1: 10}) == [
            (1, 1),
            (1, 2),
            (1, 3),
        ]

    def test_inclusive_end_at_season_finale_does_not_wrap(self) -> None:
        """Ending exactly on the season finale must NOT emit a false next-season
        episode (the classic `>=` wrap bug skips the finale itself)."""
        result = iterate_episode_range(1, 1, 1, 10, {1: 10})
        assert result == [(1, e) for e in range(1, 11)]

    def test_wrap_exactly_at_season_end(self) -> None:
        assert iterate_episode_range(1, 10, 2, 1, {1: 10, 2: 10}) == [
            (1, 10),
            (2, 1),
        ]

    def test_multi_season_crossing(self) -> None:
        assert iterate_episode_range(1, 9, 2, 2, {1: 10, 2: 10}) == [
            (1, 9),
            (1, 10),
            (2, 1),
            (2, 2),
        ]

    def test_season_missing_from_map_wraps_anyway(self) -> None:
        """A season absent from ``season_map`` still advances to its next
        season (no map entry means "unknown length": treat as finished)."""
        assert iterate_episode_range(1, 3, 2, 1, {2: 10}) == [(1, 3), (2, 1)]

    def test_missing_season_map_does_not_loop_forever(self) -> None:
        """Crossing into a season missing from the map must terminate once the
        end bound is reached (regression guard against an infinite loop)."""
        result = iterate_episode_range(1, 1, 2, 2, {1: 10})
        assert result == [(1, e) for e in range(1, 11)] + [(2, 1)]
