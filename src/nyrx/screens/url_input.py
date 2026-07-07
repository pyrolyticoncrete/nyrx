# SPDX-License-Identifier: AGPL-3.0-only

"""Modal prompting the user for a SoundCloud profile URL."""

from __future__ import annotations

import logging
import re

from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Input, Label, Static

from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)

_SC_PROFILE_RE = re.compile(r"^https?://(www\.)?soundcloud\.com/[A-Za-z0-9\-_.]+/?$")


class URLInputModal(BaseModal[str | None]):
    """Modal prompting the user for a SoundCloud profile URL.

    Dismisses with the entered URL string, or ``None`` on cancel.
    """

    def compose(self) -> ComposeResult:
        with Container(id="url-box"):
            yield Static("[white]SOUNDCLOUD PROFILE URL[/white]", id="url-heading")
            yield Input(
                placeholder="https://soundcloud.com/your-username",
                id="url-input",
            )
            yield Static("", id="url-status")
            yield Label(
                "[white]enter[/white] [dim]confirm[/dim]  \u2022  [white]esc[/white] [dim]cancel[/dim]",
                id="url-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#url-input", Input).focus()
        logger.debug("URLInputModal.on_mount")

    def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            return
        if not _SC_PROFILE_RE.match(url):
            self.query_one("#url-status", Static).update(
                "[red]Not a SoundCloud profile URL[/red]"
            )
            return
        logger.debug("on_input_submitted: url=%s", url)
        self.dismiss(url)
