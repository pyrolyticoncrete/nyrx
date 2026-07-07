# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``widgets/tv_home.py`` compact layout and nav safety.

Covers ``TVHomeView._apply_compact`` toggling ``.compact`` class at the
config threshold, and the ``on_key`` row filter excluding hidden-section
rows so arrow nav can't land inside a collapsed POPULAR section.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from nyrx.widgets.tv_home import TVChip, TVHomeView


def _stub(**attrs) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


class TestApplyCompact:
    """``_apply_compact`` toggles ``.compact`` class below ``TV_HOME_COMPACT_MAX_HEIGHT``."""

    def _make_stub(self, height: int) -> tuple[SimpleNamespace, MagicMock]:
        set_class = MagicMock()
        return _stub(
            screen=SimpleNamespace(size=SimpleNamespace(height=height)),
            set_class=set_class,
        ), set_class

    def test_compact_at_28(self) -> None:
        stub, set_class = self._make_stub(height=28)
        TVHomeView._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")

    def test_not_compact_at_29(self) -> None:
        stub, set_class = self._make_stub(height=29)
        TVHomeView._apply_compact(stub)
        set_class.assert_called_once_with(False, "compact")

    def test_compact_at_27(self) -> None:
        stub, set_class = self._make_stub(height=27)
        TVHomeView._apply_compact(stub)
        set_class.assert_called_once_with(True, "compact")


class TestOnKeyNavFilter:
    """``on_key`` up/down must not land inside a hidden parent section."""

    def _make_focused_chip(self) -> TVChip:
        chip = TVChip.__new__(TVChip)
        chip.data = {"title": "X"}
        chip.bookmarked = False
        return chip

    def _make_row(self, *, parent_display: bool = True) -> SimpleNamespace:
        return _stub(
            display=True,
            parent=_stub(display=parent_display),
            query=lambda cls: [],
        )

    def test_hidden_parent_row_excluded(self) -> None:
        visible_row = self._make_row(parent_display=True)
        hidden_row = self._make_row(parent_display=False)

        focused = self._make_focused_chip()
        with patch.object(
            type(focused), "parent", new_callable=PropertyMock, return_value=visible_row
        ):
            stub = _stub(
                screen=SimpleNamespace(focused=focused),
                query=lambda cls: (
                    [visible_row, hidden_row] if cls == ".tv-chip-row" else []
                ),
            )
            chip = self._make_focused_chip()
            visible_row.query = lambda cls: [chip]

            event = _stub(key="down", stop=MagicMock())
            TVHomeView.on_key(stub, event)

        event.stop.assert_called()

    def test_all_parents_hidden_nav_stops(self) -> None:
        focused = self._make_focused_chip()
        hidden_row = self._make_row(parent_display=False)
        with patch.object(
            type(focused), "parent", new_callable=PropertyMock, return_value=hidden_row
        ):
            stub = _stub(
                screen=SimpleNamespace(focused=focused),
                query=lambda cls: [hidden_row] if cls == ".tv-chip-row" else [],
            )

            event = _stub(key="up", stop=MagicMock())
            TVHomeView.on_key(stub, event)

        event.stop.assert_not_called()
