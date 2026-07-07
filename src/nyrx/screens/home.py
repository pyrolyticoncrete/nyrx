# SPDX-License-Identifier: AGPL-3.0-only

"""Full-screen NYRX welcome screen: banner, tagline, and source chips.

Shown on launch before any source is active.  The user picks a source with
F1-F4 (or quits with ``q``).  Being a ``ModalScreen``, the App-level bindings
(``/``, ``enter``, ``?`` etc.) are blocked for free by Textual's binding-chain
truncation, so no key can leak into the underlying app.
"""

from __future__ import annotations

import logging

import pyfiglet
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.timer import Timer
from textual.widgets import Static

from nyrx.config import APP_VERSION
from nyrx.modes import MODES, Source

logger = logging.getLogger(__name__)

SEP = "\u2500" * 70
FONT = "ansi_shadow"

# Per-row gradient: desaturated lavender → deep indigo (btop-style color bands)
_BAND: list[str] = ["#B7A4F0", "#A98FF0", "#9A7BF0", "#8A67E8", "#7A57C8", "#5A4A8E"]
_SHADE: list[int] = [0x6E, 0x62, 0x56, 0x4A, 0x3E, 0x32]
_BLOCKS = set("\u2588")


def _banner_markup(text: str) -> str:
    """Convert a pyfiglet string to Rich markup with per-row color bands."""
    rows = text.splitlines()
    lines: list[str] = []
    for z, row in enumerate(rows):
        band = _BAND[z]
        grey = f"#{_SHADE[z]:02x}{_SHADE[z]:02x}{_SHADE[z]:02x}"
        out: list[str] = []
        i = 0
        while i < len(row):
            ch = row[i]
            if ch in _BLOCKS:
                color = band
            elif ch != " ":
                color = grey
            else:
                out.append(" ")
                i += 1
                continue
            run = ch
            i += 1
            while i < len(row):
                next_ch = row[i]
                if next_ch in _BLOCKS:
                    next_color = band
                elif next_ch != " ":
                    next_color = grey
                else:
                    break
                if next_color != color:
                    break
                run += next_ch
                i += 1
            out.append(f"[{color}]{run}[/]")
        lines.append("".join(out))
    return "\n".join(lines)


try:
    _BANNER = _banner_markup(pyfiglet.Figlet(font=FONT).renderText("NYRX").rstrip())
except Exception:
    logger.debug("pyfiglet font %r unavailable, using fallback banner", FONT)
    _BANNER = "N  Y  R  X"


class HomeScreen(ModalScreen):
    """NYRX welcome screen shown before any source is selected."""

    BINDINGS = [
        ("f1", "select_source('youtube')", ""),
        ("f2", "select_source('soundcloud')", ""),
        ("f3", "select_source('radio')", ""),
        ("f4", "select_source('tv_movies')", ""),
        ("q", "quit", ""),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._selecting = False
        self._select_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Vertical(id="home-center"):
            with Horizontal(id="home-topline"):
                yield Static("", id="home-offline-banner")
                yield Static("q [dim]quit[/dim]", id="qtoquit")
            yield Static(_BANNER, id="home-banner")
            yield Static("Terminal Media Aggregator", id="home-tagline")
            yield Static(SEP, id="home-sep")
            with Horizontal(id="home-chips"):
                for source, meta in MODES.items():
                    yield Static(
                        f"{meta.label}\n[dim]\\[{meta.keybind.upper()}][/dim]",
                        id=f"home-chip-{source.value}",
                        classes="home-chip",
                    )
        with Horizontal(id="home-bottom"):
            yield Static(f"nyrx {APP_VERSION}", id="version-label")

    def update_offline(self, online: bool, show_back_online: bool) -> None:
        """Update the offline status marker in the top bar."""
        try:
            banner = self.query_one("#home-offline-banner", Static)
            if not online:
                banner.styles.display = "block"
                banner.update("[red]\u2717 offline \u00b7 reconnecting[/red]")
            elif show_back_online:
                banner.styles.display = "block"
                banner.update("[green]\u2713 back online[/green]")
            else:
                banner.styles.display = "none"
                banner.update("")
        except Exception:
            logger.debug("HomeScreen.update_offline: banner not mounted")

    def action_select_source(self, source_value: str) -> None:
        """Highlight the chosen chip, then switch sources after a brief arm."""
        if self._selecting:
            return
        self._selecting = True
        source = Source(source_value)
        self.query_one(f"#home-chip-{source.value}", Static).set_class(True, "active")
        self._select_timer = self.set_timer(0.1, lambda: self._finish_select(source))

    def _finish_select(self, source: Source) -> None:
        """Dismiss with the chosen source (must return None: see timer invoke)."""
        self.dismiss(source)

    def action_quit(self) -> None:
        """Quit via the app's double-press confirm; cancel any pending switch."""
        self._selecting = False
        if self._select_timer is not None:
            self._select_timer.stop()
            self._select_timer = None
        self.app.action_quit()  # type: ignore[unused-coroutine]  # Textual actions are fire-and-forget
