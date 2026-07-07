# SPDX-License-Identifier: AGPL-3.0-only

"""Sidebar context and source-change handlers."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text as RichText

from nyrx.config import RADIO_INDEX_PAGE
from nyrx.modes import Source
from nyrx.widgets.base import SIDEBAR_TEXT_W

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol


def _truncate_markup(markup: str, width: int) -> str:
    """Truncate a Rich-markup string to `width` cells per line, keeping markup intact."""
    lines = []
    for line in markup.split("\n"):
        text = RichText.from_markup(line)
        if len(text.plain) > width:
            text.truncate(width, overflow="ellipsis")
        lines.append(text.markup)
    return "\n".join(lines)


class SidebarHandlers:
    def _update_sidebar_context(self: MediaAppProtocol) -> None:
        """Update the sidebar context area with source name, query, and page info."""
        ctx = self._w_sidebar_context
        if ctx is None:
            logger.debug("_update_sidebar_context: _w_sidebar_context is None")
            return
        if self._in_tv_series:
            ctx.update("")
            ctx.styles.display = "none"
            self._sync_np_widget()
            return
        if self._source is Source.RADIO:
            total_filtered = self._radio_total_filtered
            total_pages = (
                max(1, (total_filtered + RADIO_INDEX_PAGE - 1) // RADIO_INDEX_PAGE)
                if total_filtered
                else 1
            )
            filter_parts = []
            if self._radio_filter_name:
                filter_parts.append(self._radio_filter_name)
            if self._radio_filter_tags:
                filter_parts.append(", ".join(self._radio_filter_tags))
            if self._radio_filter_countries:
                filter_parts.append(", ".join(self._radio_filter_countries))
            filter_line = ""
            if filter_parts:
                sep = " \u00b7 "
                filter_line = f"\nfiltering: [#a277ff]{sep.join(filter_parts)}[/]"
            ctx.styles.display = "block"
            ctx.update(
                _truncate_markup(
                    f"[dim]{total_filtered:,} stations[/]  \u2022  Page {self._radio_page + 1}[dim]/{total_pages}[/dim]{filter_line}",
                    SIDEBAR_TEXT_W,
                )
            )
        elif self._source is Source.SOUNDCLOUD:
            if self._all_results:
                ctx.styles.display = "block"
                ctx.update(
                    _truncate_markup(
                        f"[dim]{self._query}[/dim]\n"
                        f"page {self._page + 1}[dim]/{max(1, (len(self._all_results) + self._page_size - 1) // self._page_size)}[/dim] \u2022 "
                        f"{len(self._all_results)} [dim]results[/dim]",
                        SIDEBAR_TEXT_W,
                    )
                )
            elif self._query:
                ctx.styles.display = "block"
                ctx.update(
                    _truncate_markup(f"[dim]{self._query}[/dim]", SIDEBAR_TEXT_W)
                )
            else:
                ctx.update("")
                ctx.styles.display = "none"
        elif not self._all_results:
            if self._query:
                ctx.styles.display = "block"
                ctx.update(
                    _truncate_markup(f"[dim]{self._query}[/dim]", SIDEBAR_TEXT_W)
                )
            else:
                ctx.update("")
                ctx.styles.display = "none"
        else:
            ctx.styles.display = "block"
            ctx.update(
                _truncate_markup(
                    f"[dim]{self._query}[/dim]\n"
                    f"page {self._page + 1}[dim]/{max(1, (len(self._all_results) + self._page_size - 1) // self._page_size)}[/dim] \u2022 "
                    f"{len(self._all_results)} [dim]results[/dim]",
                    SIDEBAR_TEXT_W,
                )
            )
        self._sync_np_widget()

    def _on_source_changed(self: MediaAppProtocol) -> None:
        """Call after any source/mode switch that doesn't trigger a focus event."""
        self._render_focus_indicators()
