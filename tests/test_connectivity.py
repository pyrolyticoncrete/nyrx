# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for handlers/connectivity.py: state transition logic (Tier G target 1).

``compute_connectivity_transition`` is a pure function that determines
the connectivity transition type from a pair of (was_online, now_online)
booleans.  Extracted from ``ConnectivityHandlers._handle_connectivity_result``
so the decision logic is testable without a ``MediaAppProtocol`` instance.
"""

from __future__ import annotations

import pytest

from nyrx.handlers.connectivity import compute_connectivity_transition


class TestComputeConnectivityTransition:
    @pytest.mark.parametrize(
        ("was_online", "now_online", "expected"),
        [
            (True, False, "went_offline"),
            (False, True, "came_back_online"),
            (True, True, None),
            (False, False, None),
        ],
    )
    def test_transition(
        self, was_online: bool, now_online: bool, expected: str | None
    ) -> None:
        assert compute_connectivity_transition(was_online, now_online) == expected
