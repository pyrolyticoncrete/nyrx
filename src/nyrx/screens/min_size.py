# SPDX-License-Identifier: AGPL-3.0-only

"""Lock overlay shown when the terminal is below the usable floor.

Provides :func:`below_floor` (pure predicate) and :class:`MinSizeModal`
(a :class:`~textual.screen.ModalScreen` that blocks all input except
transport keys and quit until the terminal returns to usable dimensions).
"""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from nyrx.config import MIN_TERMINAL_HEIGHT, MIN_TERMINAL_WIDTH
from nyrx.widgets.base import SEEK_INTERVAL

logger = logging.getLogger(__name__)


def below_floor(width: int, height: int) -> bool:
    """Return ``True`` when the terminal is smaller than the hard floor."""
    return width < MIN_TERMINAL_WIDTH or height < MIN_TERMINAL_HEIGHT


class MinSizeModal(ModalScreen[None]):
    """Full-screen lock shown when the terminal is below usable dimensions.

    The overlay is modal so all underlying bindings are truncated.
    Only ``q`` (quit), ``space`` (pause), and ``←``/``→`` (seek) are
    forwarded to the mpv IPC controller.  ``escape`` is swallowed so the
    user cannot dismiss the lock while still below the floor.
    """

    BINDINGS = [("q", "quit", "")]

    def compose(self) -> ComposeResult:
        with Vertical(id="min-size-box"):
            yield Static("Terminal size too small", id="min-size-title")
            yield Static(self._current_text(), id="min-size-current")
            yield Static("", id="min-size-spacer")
            yield Static("Minimum Required:", id="min-size-req-label")
            yield Static(
                f"Width = {MIN_TERMINAL_WIDTH}  Height = {MIN_TERMINAL_HEIGHT}",
                id="min-size-req",
            )
            yield Static("", id="min-size-spacer2")
            yield Static(
                "[dim]q quit  ·  space play/pause  ·  ←→ seek[/dim]",
                id="min-size-hint",
            )

    def _current_text(self) -> str:
        w, h = self.app.size
        w_ok = w >= MIN_TERMINAL_WIDTH
        h_ok = h >= MIN_TERMINAL_HEIGHT
        w_str = f"[red]{w}[/red]" if not w_ok else str(w)
        h_str = f"[red]{h}[/red]" if not h_ok else str(h)
        return f"Width = {w_str}  Height = {h_str}"

    def on_resize(self) -> None:
        try:
            self.query_one("#min-size-current", Static).update(self._current_text())
        except Exception:
            logger.debug("on_resize: #min-size-current not found, skipping size update")

    def action_quit(self) -> None:
        self.app.action_quit()  # type: ignore[union-attr,unused-coroutine]

    def key_escape(self, event: object = None) -> None:
        pass

    def key_space(self) -> None:
        app = self.app
        if hasattr(app, "_mpv_ipc") and app._mpv_ipc:
            app._mpv_ipc.toggle_pause()

    def key_left(self) -> None:
        app = self.app
        if hasattr(app, "_mpv_ipc") and app._mpv_ipc:
            app._mpv_ipc.seek(-SEEK_INTERVAL)

    def key_right(self) -> None:
        app = self.app
        if hasattr(app, "_mpv_ipc") and app._mpv_ipc:
            app._mpv_ipc.seek(SEEK_INTERVAL)
