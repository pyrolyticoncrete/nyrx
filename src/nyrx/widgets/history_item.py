# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

from typing import TYPE_CHECKING

from rich.text import Text
from textual.app import ComposeResult
from textual.events import Click
from textual.timer import Timer
from textual.widgets import ListItem, Static

from nyrx.helpers import BRAILLE_SPINNER

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol


class HistoryItem(ListItem):
    """A recent search query shown in the landing-mode history list.

    Displays a spinner animation while a new search is running.
    """

    SPINNER_FRAMES = list(BRAILLE_SPINNER)

    def __init__(self, query: str) -> None:
        self._query = query
        self._spinner_timer: Timer | None = None
        self._spinning = False
        self._spinner_idx = 0
        super().__init__()

    def compose(self) -> ComposeResult:
        self._label = Static(Text(self._query))
        yield self._label

    def _on_click(self, event: Click) -> None:  # type: ignore[override]
        event.stop()

    def _on_list_item__child_clicked(self, event: ListItem._ChildClicked) -> None:
        event.stop()

    def start_spinner(self, app: MediaAppProtocol) -> None:
        """Begin animating a busy spinner on this history item."""
        self._spinning = True
        self._spinner_idx = 0
        self._update_spinner()
        self._spinner_timer = app.set_interval(0.08, self._advance_spinner)

    def stop_spinner(self) -> None:
        """Stop the spinner and restore the plain query text."""
        self._spinning = False
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self._label.update(Text(self._query))

    def _advance_spinner(self) -> None:
        if not self._spinning:
            return
        self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNER_FRAMES)
        self._update_spinner()

    def _update_spinner(self) -> None:
        frame = self.SPINNER_FRAMES[self._spinner_idx]
        self._label.update(Text(f"{self._query} {frame}"))
        self._label.refresh()
