# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for jumping to a specific season in the series browser."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.widgets import Label, Static

from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)


class SeasonPicker(Static):
    """Focusable widget for selecting a season via \u2190 \u2192 navigation."""

    can_focus = True

    def __init__(self, season_count: int, current: int, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._season_count = season_count
        self._current = current
        self._update_display()

    def _update_display(self) -> None:
        self.update(f"S{self._current:02d}")

    @property
    def value(self) -> int:
        return self._current

    def key_left(self) -> None:
        self._current = self._current - 1 if self._current > 1 else self._season_count
        self._update_display()

    def key_right(self) -> None:
        self._current = self._current + 1 if self._current < self._season_count else 1
        self._update_display()


class SeasonJumpModal(BaseModal[int | None]):
    """Modal for jumping to a specific season."""

    BINDINGS = [
        Binding("left", "navigate_left", "", priority=True),
        Binding("right", "navigate_right", "", priority=True),
        Binding("enter", "submit", "", priority=True),
    ]

    def __init__(self, season_count: int, current_season: int) -> None:
        super().__init__()
        self._season_count = season_count
        self._current_season = current_season

    def compose(self) -> ComposeResult:
        with Container(id="sj-box"):
            with Horizontal(id="sj-row"):
                yield Static("jump to ", id="sj-label")
                yield SeasonPicker(
                    self._season_count,
                    self._current_season,
                    id="sj-picker",
                )
            yield Label(
                "[white]\u2190 \u2192[/white] [dim]navigate[/dim]  \u00b7  "
                "[white]enter[/white] [dim]select[/dim]  \u00b7  "
                "[white]esc[/white] [dim]exit[/dim]",
                id="sj-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#sj-picker", SeasonPicker).focus()

    def action_navigate_left(self) -> None:
        self.query_one("#sj-picker", SeasonPicker).key_left()

    def action_navigate_right(self) -> None:
        self.query_one("#sj-picker", SeasonPicker).key_right()

    def action_submit(self) -> None:
        picker = self.query_one("#sj-picker", SeasonPicker)
        self.dismiss(picker.value)
