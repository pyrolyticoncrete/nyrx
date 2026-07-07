# SPDX-License-Identifier: AGPL-3.0-only

"""Modal prompting the user for a TMDB API key with live validation."""

from __future__ import annotations

import logging

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.timer import Timer
from textual.widgets import Input, Label, Static

from nyrx.helpers import BRAILLE_SPINNER
from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)


class TMDbKeyInputModal(BaseModal[str | None]):
    """Modal prompting the user for a TMDB API key with live validation.

    Validates the key against the TMDB ``/3/configuration`` endpoint in a
    background thread.  Dismisses with the validated key on success, or
    stays open with an error message on failure.
    """

    SPINNER_FRAMES = list(BRAILLE_SPINNER)

    def __init__(self) -> None:
        super().__init__()
        self._spinner_idx = 0
        self._spinner_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Container(id="tmdb-box"):
            yield Static("CONNECT TMDB", id="tmdb-heading")
            with Horizontal(id="tmdb-input-row"):
                yield Input(
                    placeholder="Paste your API key here",
                    id="tmdb-input",
                )
                yield Static("", id="tmdb-spinner")
            yield Static("", id="tmdb-status")
            yield Label(
                "enter [dim]confirm[/dim]  \u2022  esc [dim]cancel[/dim]",
                id="tmdb-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#tmdb-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        key = event.value.strip()
        if not key or len(key) < 32:
            self.query_one("#tmdb-status", Static).update(
                "[red]Key too short: TMDB keys are 32 characters[/red]"
            )
            return
        inp = self.query_one("#tmdb-input", Input)
        inp.disabled = True
        self._start_spinner()
        self._validate_key(key)

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        self.query_one("#tmdb-spinner", Static).update(self.SPINNER_FRAMES[0])
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNER_FRAMES)
        self.query_one("#tmdb-spinner", Static).update(
            self.SPINNER_FRAMES[self._spinner_idx]
        )

    def _stop_spinner(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.query_one("#tmdb-spinner", Static).update("")

    def _show_status(self, text: str, is_error: bool = False) -> None:
        colour = "red" if is_error else "green"
        self.query_one("#tmdb-status", Static).update(f"[{colour}]{text}[/{colour}]")

    @work(thread=True)
    def _validate_key(self, key: str) -> None:
        import requests

        try:
            resp = requests.get(
                f"https://api.themoviedb.org/3/configuration?api_key={key}",
                timeout=10,
            )
            is_valid = resp.status_code == 200
        except Exception as exc:
            self.app.call_from_thread(self._on_validation_error, str(exc))
            return
        self.app.call_from_thread(self._on_validation_result, key, is_valid)

    def _on_validation_result(self, key: str, is_valid: bool) -> None:
        if self.app.screen_stack[-1] is not self:
            logger.debug(
                "_on_validation_result: screen already dismissed, dropping result"
            )
            return
        self._stop_spinner()
        if is_valid:
            self._show_status("Connected")
            self.dismiss(key)
        else:
            inp = self.query_one("#tmdb-input", Input)
            inp.disabled = False
            inp.focus()
            self._show_status("Invalid API key", is_error=True)

    def _on_validation_error(self, msg: str) -> None:
        if self.app.screen_stack[-1] is not self:
            logger.debug(
                "_on_validation_error: screen already dismissed, dropping error"
            )
            return
        self._stop_spinner()
        inp = self.query_one("#tmdb-input", Input)
        inp.disabled = False
        inp.focus()
        self._show_status(f"Could not validate: {msg}", is_error=True)
