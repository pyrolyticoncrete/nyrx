# SPDX-License-Identifier: AGPL-3.0-only

"""Floating mode-switcher overlay: Alt+Tab style chip strip."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Horizontal
from textual.timer import Timer
from textual.widget import Widget
from textual.widgets import Static

from nyrx.modes import MODES, Source

logger = logging.getLogger(__name__)


class ModeSwitcher(Widget):
    """Transient, non-blocking overlay showing 4 mode chips.

    Current mode is highlighted; others stay dimmed. Auto-dismisses
    after 1.6s of inactivity, resetting on every switch.
    """

    DEFAULT_CSS = ""

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._hide_timer: Timer | None = None
        self._show_token: int = 0

    def compose(self) -> ComposeResult:
        with Horizontal(id="chip-row"):
            for source, meta in MODES.items():
                chip = Static(
                    f"{meta.label}\n[dim]\\[{meta.keybind.upper()}][/dim]",
                    id=f"chip-{source.value}",
                    classes="mode-chip",
                )
                yield chip

    def show(self, active: Source) -> None:
        """Light up *active* chip, dim the rest, start auto-dismiss timer."""
        self._show_token += 1
        token = self._show_token
        logger.debug("show: active=%s token=%s", active, token)

        for source in MODES:
            chip = self.query_one(f"#chip-{source.value}", Static)
            chip.set_class(source == active, "active")
            chip.refresh()

        self.add_class("visible")

        if self._hide_timer:
            self._hide_timer.stop()
        self._hide_timer = self.set_timer(1.6, lambda: self._dismiss(token))

    def _dismiss(self, token: int) -> None:
        """Hide immediately: force a full repaint so the overlay always clears."""
        if token != self._show_token:
            logger.debug(
                "_dismiss: suppressed token=%s current=%s", token, self._show_token
            )
            return
        self.remove_class("visible")
        logger.debug("_dismiss: forcing full refresh")
        self.app.refresh(layout=True)
