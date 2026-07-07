# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for entering or editing the download directory path."""

from __future__ import annotations

import logging
import os
from pathlib import Path

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Label, Static

from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)


class DirInputModal(BaseModal[str | None]):
    """Modal for entering or editing the download directory path."""

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Container(id="di-box"):
            yield Static("[white]DOWNLOAD DIRECTORY[/white]", id="di-heading")
            yield Input(value=self._current, id="di-input")
            yield Static("", id="di-status")
            yield Label(
                "[white]enter[/white] [dim]confirm[/dim]  \u2022  [white]esc[/white] [dim]cancel[/dim]",
                id="di-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        inp = self.query_one("#di-input", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)
        logger.debug("DirInputModal.on_mount: current=%s", self._current)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        path = event.value.strip()
        if not path:
            return
        expanded = str(Path(path).expanduser())
        try:
            os.makedirs(expanded, exist_ok=True)
            if not os.access(expanded, os.W_OK):
                raise OSError("directory not writable")
        except OSError as exc:
            self.query_one("#di-status", Static).update(f"[red]{path}: {exc}[/red]")
            return
        logger.debug("on_input_submitted: path=%s", path)
        self.dismiss(path)
