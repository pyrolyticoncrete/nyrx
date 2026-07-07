# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for entering a hotswap manifest URL with live validation."""

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


class ManifestUrlModal(BaseModal[str | None]):
    """Modal for entering a hotswap manifest URL with live validation.

    Validates the URL by fetching it and checking for a valid manifest
    structure (``version`` int + ``files`` list).  Dismisses with the
    URL on success, or stays open with an error on failure.
    """

    SPINNER_FRAMES = list(BRAILLE_SPINNER)

    def __init__(self, current_url: str = "") -> None:
        super().__init__()
        self._current_url = current_url
        self._spinner_idx = 0
        self._spinner_timer: Timer | None = None

    def compose(self) -> ComposeResult:
        with Container(id="url-box"):
            yield Static("CONFIGURE MANIFEST URL", id="url-heading")
            yield Static(
                "[yellow]Server plugins run third-party Lua code in a sandbox. "
                "Only configure URLs from sources you trust. nyrx does not review "
                "or endorse community plugins.[/yellow]",
                id="url-warning",
            )
            with Horizontal(id="url-input-row"):
                yield Input(
                    placeholder="Paste manifest URL here",
                    value=self._current_url,
                    id="url-input",
                )
                yield Static("", id="url-spinner")
            yield Static("", id="url-status")
            yield Label(
                "enter [dim]confirm[/dim]  \u2022  esc [dim]cancel[/dim]",
                id="url-hint",
            )

    def on_mount(self) -> None:
        self.query_one("#url-input", Input).focus()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        url = event.value.strip()
        if not url:
            self.dismiss("")
            return
        if not url.startswith(("http://", "https://")):
            self.query_one("#url-status", Static).update(
                "[red]URL must start with http:// or https://[/red]"
            )
            return
        inp = self.query_one("#url-input", Input)
        inp.disabled = True
        self._start_spinner()
        self._validate_url(url)

    def _start_spinner(self) -> None:
        self._spinner_idx = 0
        self.query_one("#url-spinner", Static).update(self.SPINNER_FRAMES[0])
        self._spinner_timer = self.set_interval(0.08, self._tick_spinner)

    def _tick_spinner(self) -> None:
        self._spinner_idx = (self._spinner_idx + 1) % len(self.SPINNER_FRAMES)
        self.query_one("#url-spinner", Static).update(
            self.SPINNER_FRAMES[self._spinner_idx]
        )

    def _stop_spinner(self) -> None:
        if self._spinner_timer:
            self._spinner_timer.stop()
            self._spinner_timer = None
        self.query_one("#url-spinner", Static).update("")

    def _show_status(self, text: str, is_error: bool = False) -> None:
        colour = "red" if is_error else "green"
        self.query_one("#url-status", Static).update(f"[{colour}]{text}[/{colour}]")

    @work(thread=True)
    def _validate_url(self, url: str) -> None:
        import requests

        try:
            resp = requests.get(url, timeout=15)
            resp.raise_for_status()
            data = resp.json()
            is_valid = isinstance(data.get("version"), int) and isinstance(
                data.get("files"), list
            )
        except requests.exceptions.HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else "?"
            reason = exc.response.reason if exc.response is not None else "error"
            self.app.call_from_thread(
                self._on_validation_error,
                f"Manifest URL returned {status} ({reason}).",
            )
            return
        except requests.exceptions.ConnectionError:
            self.app.call_from_thread(
                self._on_validation_error,
                "Could not reach the manifest URL. Check your connection.",
            )
            return
        except requests.exceptions.Timeout:
            self.app.call_from_thread(
                self._on_validation_error, "Manifest URL timed out. Try again."
            )
            return
        except ValueError:
            self.app.call_from_thread(
                self._on_validation_error,
                "Manifest URL did not return valid JSON.",
            )
            return
        except Exception as exc:
            self.app.call_from_thread(
                self._on_validation_error, f"Could not validate manifest: {exc}"
            )
            return
        self.app.call_from_thread(self._on_validation_result, url, is_valid)

    def _on_validation_result(self, url: str, is_valid: bool) -> None:
        if self.app.screen_stack[-1] is not self:
            logger.debug(
                "_on_validation_result: screen already dismissed, dropping result"
            )
            return
        self._stop_spinner()
        if is_valid:
            self._show_status("Manifest validated")
            self.dismiss(url)
        else:
            inp = self.query_one("#url-input", Input)
            inp.disabled = False
            inp.focus()
            self._show_status(
                "Invalid manifest: must have 'version' (int) and 'files' (list)",
                is_error=True,
            )

    def _on_validation_error(self, msg: str) -> None:
        if self.app.screen_stack[-1] is not self:
            logger.debug(
                "_on_validation_error: screen already dismissed, dropping error"
            )
            return
        self._stop_spinner()
        inp = self.query_one("#url-input", Input)
        inp.disabled = False
        inp.focus()
        self._show_status(msg, is_error=True)
