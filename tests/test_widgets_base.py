# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for widgets/base.py: utility functions (Tier G target 5).

``_short_views`` is a pure formatting function (int → str) used in
widget display strings.  Tested identically to ``format_views`` in C01
but without the ``" views"`` suffix and with ``int``-only typing.

Known bug: ``_short_views(0)`` returns ``""`` where some callers
expect ``"0"``: documented in UnitTestSuite.md Tier G entry.
"""

from __future__ import annotations

import pytest

from nyrx.widgets.base import _short_views


class TestShortViews:
    @pytest.mark.parametrize(
        ("n", "expected"),
        [
            (0, ""),
            (1, "1"),
            (999, "999"),
            (1000, "1.0K"),
            (1500, "1.5K"),
            (999_999, "1000.0K"),
            (1_000_000, "1.0M"),
            (1_200_000, "1.2M"),
            (1_234_567, "1.2M"),
        ],
    )
    def test_short_views(self, n: int, expected: str) -> None:
        assert _short_views(n) == expected
