# SPDX-License-Identifier: AGPL-3.0-only

"""Shared base constants, helpers, and BrailleSpinner for the widgets package."""

from textual.widgets import Static

from nyrx.helpers import BRAILLE_SPINNER

SEEK_INTERVAL = 10

SIDEBAR_TEXT_W = 44
SIDEBAR_BAR_W = 18


def _short_views(n: int) -> str:
    if not n:
        return ""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.1f}K"
    return str(n)


class BrailleSpinner(Static, can_focus=False):
    CHARS = BRAILLE_SPINNER

    def on_mount(self) -> None:
        self._idx = 0
        self.set_interval(0.08, self._tick)

    def _tick(self) -> None:
        self._idx = (self._idx + 1) % len(self.CHARS)
        self.update(self.CHARS[self._idx])
