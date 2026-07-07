# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for selecting a default SoundCloud trending region from SC_COUNTRY_MAP."""

from __future__ import annotations

import logging

from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Container
from textual.widgets import Label, OptionList, Static
from textual.widgets.option_list import Option

from nyrx.config import SC_COUNTRY_MAP
from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)

# Fixed vertical rows: box padding (2) + heading (2) + hint (2) + list margin (1).
_CHROME = 7


class _CenteringOptionList(OptionList):
    """OptionList that centers the highlighted row in the viewport (fzf-style)."""

    def scroll_to_highlight(self, top: bool = False) -> None:
        highlighted = self.highlighted
        if highlighted is None or not self.is_mounted:
            return
        self._update_lines()
        try:
            y = self._index_to_line[highlighted]
        except KeyError:
            return
        h = self._heights[highlighted]
        vp = self.scrollable_content_region.height
        target = y - max(0, (vp - h) // 2)
        self.scroll_to(
            y=max(0, min(target, self.max_scroll_y)),
            animate=False,
            immediate=True,
        )


class TrendingRegionSelector(BaseModal[str | None]):
    """Modal for selecting a default SoundCloud trending region from SC_COUNTRY_MAP."""

    def __init__(self, current: str) -> None:
        super().__init__()
        self._current = current

    def compose(self) -> ComposeResult:
        with Container(id="trs-box"):
            yield Static("[white]SELECT TRENDING REGION[/white]", id="trs-heading")
            yield _CenteringOptionList(id="trs-list")
            yield Label(
                "[white]enter[/white] [dim]select[/dim]  [white]esc[/white] [dim]cancel[/dim]",
                id="trs-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        ol = self.query_one("#trs-list", _CenteringOptionList)
        sorted_codes = sorted(SC_COUNTRY_MAP, key=lambda c: SC_COUNTRY_MAP[c])
        display_names = [SC_COUNTRY_MAP[c] for c in sorted_codes]
        logger.debug(
            "TrendingRegionSelector.on_mount: current=%s options=%s",
            self._current,
            len(sorted_codes),
        )
        for code, name in zip(sorted_codes, display_names):
            t = Text()
            t.append(" \u25cf " if code == self._current else "   ")
            t.append(name)
            ol.add_option(Option(t, id=code))
        if self._current in sorted_codes:
            ol.highlighted = sorted_codes.index(self._current)
        self._reflow()

    def on_resize(self) -> None:
        self._reflow()

    def _reflow(self) -> None:
        ol = self.query_one("#trs-list", _CenteringOptionList)
        box_max = int(self.size.height * 0.8)
        ol.styles.height = max(1, min(len(ol._options), box_max - _CHROME))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        logger.debug("on_option_list_option_selected: code=%s", event.option.id)
        self.dismiss(str(event.option.id))
