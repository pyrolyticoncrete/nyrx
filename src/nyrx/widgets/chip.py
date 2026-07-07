# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from typing import Any

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.message import Message
from textual.widget import Widget
from textual.widgets import Input, Static

logger = logging.getLogger(__name__)


class ChipInput(Widget):
    """Inline chip + text input in a single line."""

    class Changed(Message):
        def __init__(self, sender: ChipInput, value: str) -> None:
            super().__init__()
            self.input = sender
            self.value = value

    class Submitted(Message):
        def __init__(self, sender: ChipInput, value: str) -> None:
            super().__init__()
            self.input = sender
            self.value = value

    def __init__(
        self,
        chips: list[str] | None = None,
        placeholder: str = "",
        chip_color: str = "#A277FF",
        input_id: str | None = None,
        render_chips: bool = True,
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._chips: list[str] = list(chips or [])
        self._placeholder = placeholder
        self._chip_color = chip_color
        self._input_id = input_id
        self._render_chips_enabled = render_chips

    def compose(self) -> ComposeResult:
        yield Static("", id=f"{self._input_id}-chip-row", classes="chip-row")
        yield Input(placeholder=self._placeholder, id=self._input_id)

    def on_mount(self) -> None:
        self._render_chips()

    @property
    def value(self) -> str:
        try:
            return self.query_one(f"#{self._input_id}", Input).value
        except Exception:
            logger.debug("Failed to read chip input value")
            return ""

    @value.setter
    def value(self, v: str) -> None:
        try:
            self.query_one(f"#{self._input_id}", Input).value = v
        except Exception:
            logger.debug("Failed to set chip input value")

    def add_chip(self, chip: str) -> None:
        logger.debug(
            "ChipInput.add_chip: chip=%s total=%s",
            chip,
            len(self._chips) + (0 if chip in self._chips else 1),
        )
        if chip not in self._chips:
            self._chips.append(chip)
            self._render_chips()

    def remove_last_chip(self) -> str | None:
        if not self._chips:
            return None
        chip = self._chips.pop()
        logger.debug(
            "ChipInput.remove_last_chip: chip=%s remaining=%s", chip, len(self._chips)
        )
        self._render_chips()
        return chip

    def remove_chip(self, chip: str) -> None:
        logger.debug("ChipInput.remove_chip: chip=%s", chip)
        if chip in self._chips:
            self._chips.remove(chip)
            self._render_chips()

    def focus_input(self) -> None:
        try:
            self.query_one(f"#{self._input_id}", Input).focus()
        except Exception:
            logger.debug("Failed to focus chip input")

    def _render_chips(self) -> None:
        if not self._render_chips_enabled:
            return
        try:
            row = self.query_one(f"#{self._input_id}-chip-row", Static)
        except Exception:
            logger.debug("Failed to query chip row for rendering")
            return
        if not self._chips:
            row.update("")
            row.styles.width = 0
            return
        t = Text()
        for chip in self._chips:
            t.append(f" {chip} ", style=f"white on {self._chip_color}")
            t.append(" ", style="dim")
        row.update(t)
        chip_text_len = sum(len(c) + 3 for c in self._chips)
        row.styles.width = chip_text_len

    def on_focus(self, event: events.Focus) -> None:
        self.focus_input()

    def on_input_changed(self, event: Input.Changed) -> None:
        event.stop()
        self.post_message(self.Changed(self, event.value))

    def on_input_submitted(self, event: Input.Submitted) -> None:
        event.stop()
        self.post_message(self.Submitted(self, event.value))


class ChipButton(Static):
    """A single focusable chip in the active filter panel."""

    can_focus = True

    def __init__(self, label: str, chip_type: str, value: str) -> None:
        self._chip_type = chip_type
        self._chip_value = value
        color = "#A277FF" if chip_type == "tag" else "#E07B39"
        t = Text()
        t.append(" x ", style="bold red")
        t.append(f" {label} ", style=f"black on {color}")
        super().__init__(t)
