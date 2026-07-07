# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for selecting a streaming quality preset from YT_QUALITY_PRESETS."""

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from nyrx.config import YT_QUALITY_PRESETS
from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)


class QualitySelector(BaseModal[str | None]):
    """Modal for selecting a streaming quality preset from YT_QUALITY_PRESETS."""

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Container(id="qs-box"):
            yield Static("[white]SELECT STREAMING QUALITY[/white]", id="qs-heading")
            yield OptionList(id="qs-list")
            yield Label(
                "[white]enter[/white] [dim]select[/dim]  [white]esc[/white] [dim]cancel[/dim]",
                id="qs-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        logger.debug("QualitySelector.on_mount: current=%s", self._current)
        ol = self.query_one("#qs-list", OptionList)
        labels = [p[0] for p in YT_QUALITY_PRESETS]
        for label in labels:
            t = Text()
            t.append(" \u25cf " if label == self._current else "   ")
            t.append(label)
            ol.add_option(Option(t, id=label))
        if self._current in labels:
            ol.highlighted = labels.index(self._current)

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        logger.debug("on_option_list_option_selected: quality=%s", event.option.id)
        self.dismiss(str(event.option.id))
