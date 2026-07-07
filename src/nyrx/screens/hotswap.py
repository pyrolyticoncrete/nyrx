# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for toggling TV/Movies server auto-updates (hotswap)."""

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)


class HotswapToggle(BaseModal[bool | None]):
    """Modal for toggling TV/Movies server auto-updates."""

    def __init__(self, enabled: bool) -> None:
        super().__init__()
        self._enabled = enabled

    def compose(self) -> ComposeResult:
        with Container(id="hs-box"):
            yield Static("[white]TV/MOVIES SERVER UPDATES[/white]", id="hs-heading")
            yield OptionList(id="hs-list")
            yield Label(
                "[white]enter[/white] [dim]select[/dim]  "
                "[white]esc[/white] [dim]cancel[/dim]",
                id="hs-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        ol = self.query_one("#hs-list", OptionList)
        options = [("Enabled", True), ("Disabled", False)]
        for label, value in options:
            t = Text()
            t.append(" \u25cf " if value == self._enabled else "   ")
            t.append(label)
            ol.add_option(Option(t, id=str(value)))
        idx = 0 if self._enabled else 1
        ol.highlighted = idx

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        selected = event.option.id == "True"
        logger.debug("HotswapToggle: selected=%s", selected)
        self.dismiss(selected)
