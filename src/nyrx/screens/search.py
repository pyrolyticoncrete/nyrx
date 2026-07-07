# SPDX-License-Identifier: AGPL-3.0-only

"""Overlay search modal with input field and URL resolution support."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from textual import work
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Button, Input, Label, LoadingIndicator

from nyrx.config import SEVERITY_ERROR, SEVERITY_WARNING, TIMEOUT_ERROR, TIMEOUT_WARNING
from nyrx.modes import Source
from nyrx.screens.base_modal import BaseModal

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


class SearchModal(BaseModal):
    """Overlay search modal with input field and URL resolution support."""

    BINDINGS = [
        ("f1", "switch_source_1", ""),
        ("f2", "switch_source_2", ""),
        ("f4", "switch_source_4", ""),
    ]

    def __init__(self) -> None:
        super().__init__()
        self._input_text = ""
        self._matching_source_key: str | None = None
        self._chip_idx = 0
        self._chip_values: list[str] = []

    def compose(self) -> ComposeResult:
        with Container(id="search-box"):
            yield Label("SEARCH", classes="search-heading")
            yield Input(id="search-input", placeholder="Search or enter URL")
            yield Horizontal(id="search-chips")
            yield Label("", id="search-hint")
            yield LoadingIndicator(id="search-loader", classes="hidden")

    def on_mount(self) -> None:
        inp = self.query_one("#search-input", Input)
        app = cast("MediaAppProtocol", self.app)
        if getattr(app, "_source", None) == Source.TV_MOVIES:
            inp.placeholder = "Enter text"
        inp.focus()
        self._build_chips()
        self._update_hint()

    def _build_chips(self) -> None:
        """Build filter chips for the current source."""
        chips = self.query_one("#search-chips", Horizontal)
        chips.remove_children()
        app = cast("MediaAppProtocol", self.app)
        source = getattr(app, "_source", None)

        if source == Source.TV_MOVIES:
            labels = ["All", "Movies", "TV Series"]
            values = ["all", "movie", "tv"]
        elif source == Source.YOUTUBE:
            labels = ["Video", "Audio"]
            values = ["video", "audio"]
        else:
            chips.display = False
            self._chip_values = []
            return

        chips.display = True
        self._chip_values = values
        for label in labels:
            chip = Button(label, classes="search-chip")
            chip.can_focus = False
            chips.mount(chip)

        if source == Source.TV_MOVIES:
            src = app._sources.get("tv_movies")
            filter_val = getattr(src, "_search_filter", "all") if src else "all"
            try:
                self._chip_idx = values.index(filter_val)
            except ValueError:
                self._chip_idx = 0
        elif source == Source.YOUTUBE:
            self._chip_idx = 1 if getattr(app, "_audio_only", False) else 0

        self._update_chip_styles()

    def _update_chip_styles(self) -> None:
        """Apply active/inactive CSS classes to chip buttons."""
        chips = list(self.query_one("#search-chips", Horizontal).children)
        for i, chip in enumerate(chips):
            if i == self._chip_idx:
                chip.add_class("active")
            else:
                chip.remove_class("active")

    def _apply_chip(self) -> None:
        """Set the app/source state to match the active chip."""
        app = cast("MediaAppProtocol", self.app)
        source = getattr(app, "_source", None)
        value = self._chip_values[self._chip_idx]
        if source == Source.TV_MOVIES:
            src = app._sources.get("tv_movies")
            if src:
                cast(Any, src)._search_filter = value
        elif source == Source.YOUTUBE:
            app._audio_only = value == "audio"

    def _update_hint(self) -> None:
        """Update the hint bar to reflect current source mode."""
        source = getattr(self.app, "_source", None)
        if source in (Source.SOUNDCLOUD, Source.RADIO):
            self.query_one("#search-hint", Label).update(
                "enter [dim]submit[/dim]  \u2022  esc [dim]cancel[/dim]"
            )
        else:
            self.query_one("#search-hint", Label).update(
                "enter [dim]submit[/dim]  \u2022  tab [dim]switch[/dim]  \u2022  esc [dim]cancel[/dim]"
            )

    def on_button_pressed(self, event: Button.Pressed) -> None:
        """Handle chip click: select the clicked chip."""
        if not event.button.has_class("search-chip"):
            return
        chips = list(self.query_one("#search-chips", Horizontal).children)
        for i, chip in enumerate(chips):
            if chip is event.button:
                self._chip_idx = i
                self._apply_chip()
                self._update_chip_styles()
                self._update_hint()
                return

    def _switch_source(self, n: int) -> None:
        app = cast("MediaAppProtocol", self.app)
        self.dismiss(None)
        getattr(app, f"action_switch_source_{n}")()

    def action_switch_source_1(self) -> None:
        self._switch_source(1)

    def action_switch_source_2(self) -> None:
        self._switch_source(2)

    def action_switch_source_4(self) -> None:
        self._switch_source(4)

    def key_tab(self) -> None:
        """Cycle through filter chips from the search modal."""
        if not self.query_one("#search-input", Input).display:
            return
        if not self._chip_values:
            return
        self._chip_idx = (self._chip_idx + 1) % len(self._chip_values)
        self._apply_chip()
        self._update_chip_styles()
        self._update_hint()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Handle search submission: route URLs to source fetch_metadata, else dismiss with query."""
        val = event.value.strip()
        if not val:
            return
        self._input_text = val
        is_url = val.startswith(("http://", "https://"))
        if is_url:
            app = cast("MediaAppProtocol", self.app)
            for key, src in app._sources.items():
                if src.handles_url(val):
                    self._matching_source_key = key
                    logger.debug(
                        "on_input_submitted: url=%s matched_source=%s", val, key
                    )
                    self.query_one("#search-input", Input).display = False
                    self.query_one("#search-chips", Horizontal).display = False
                    self.query_one("#search-hint", Label).display = False
                    self.query_one("#search-loader", LoadingIndicator).remove_class(
                        "hidden"
                    )
                    self._resolve_url()
                    return
            logger.debug("on_input_submitted: url=%s no_matching_source", val)
            try:
                app.notify(
                    "Enter a search query to continue",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
            except Exception:
                self.log.warning("notify() failed in on_input_submitted")
            return
        logger.debug("on_input_submitted: query=%s", val)
        self.dismiss(val)

    @work(thread=True)
    def _resolve_url(self) -> None:
        """Fetch metadata for a pasted URL on a background thread."""
        app = cast("MediaAppProtocol", self.app)
        assert self._matching_source_key is not None
        src = app._sources[self._matching_source_key]
        logger.debug(
            "_resolve_url: key=%s url=%s", self._matching_source_key, self._input_text
        )
        data = src.fetch_metadata(self._input_text)
        self.app.call_from_thread(self._done, data)

    def _done(self, data: dict | None) -> None:
        """Called on the main thread after URL resolution completes."""
        if self.app.screen_stack[-1] is not self:
            logger.debug("_done: screen already dismissed, dropping result")
            return
        logger.debug("_done: resolved=%s", bool(data))
        if not data:
            try:
                self.app.notify(
                    "Failed to resolve video from URL",
                    severity=SEVERITY_ERROR,
                    timeout=TIMEOUT_ERROR,
                    title="Error",
                )
            except Exception:
                self.log.warning("notify() failed in _done")
        self.dismiss(data)
