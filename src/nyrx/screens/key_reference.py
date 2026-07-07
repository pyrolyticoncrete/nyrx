# SPDX-License-Identifier: AGPL-3.0-only

"""Help modal showing all keybindings for the current screen context."""

from __future__ import annotations

import logging

from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import Static

from nyrx.bindings import CONTEXT_LABELS, KEY_REFERENCE
from nyrx.screens.base_modal import BaseModal

logger = logging.getLogger(__name__)

_PAD = 12


def _section_lines(
    name: str,
    bindings: list[tuple[str, str]],
    pad: int,
    *,
    prepend_blank: bool = False,
) -> list[str]:
    """Lines for a single section: optional blank + header + bindings."""
    lines: list[str] = []
    if prepend_blank:
        lines.append("")
    lines.append(f"[dim]{name}[/dim]")
    for key, desc in bindings:
        lines.append(f"  [#edecee]{key:<{pad}}[/]  [dim]{desc}[/]")
    return lines


def _split_sections(
    sections: list[tuple[str, list[tuple[str, str]]]],
) -> tuple[
    list[tuple[str, list[tuple[str, str]]]],
    list[tuple[str, list[tuple[str, str]]]],
]:
    """Balance sections across two columns (anchored NAVIGATION top-left).

    NAVIGATION is always the first section in the left column.  The remaining
    sections are placed greedily: tallest first, assigned to the currently
    shorter column: so the two columns are as equal in row-count as possible.
    Within each column the original section order is preserved.
    """
    order = [name for name, _ in sections]

    nav_items = [s for s in sections if s[0] == "NAVIGATION"]
    rest = [s for s in sections if s[0] != "NAVIGATION"]

    def col_height(cols: list[tuple[str, list[tuple[str, str]]]]) -> int:
        return sum(1 + len(b) for _, b in cols) + max(0, len(cols) - 1)

    left: list[tuple[str, list[tuple[str, str]]]] = list(nav_items)
    right: list[tuple[str, list[tuple[str, str]]]] = []

    for name, bindings in sorted(rest, key=lambda s: len(s[1]), reverse=True):
        item = (name, bindings)
        if col_height(left) <= col_height(right):
            left.append(item)
        else:
            right.append(item)

    left.sort(key=lambda s: order.index(s[0]))
    right.sort(key=lambda s: order.index(s[0]))
    return left, right


class KeyReferenceModal(BaseModal):
    """Help modal showing all keybindings for the current screen context."""

    def __init__(self, kr_context: str) -> None:
        super().__init__()
        self._kr_context = kr_context
        logger.debug("KeyReferenceModal: context=%s", kr_context)

    def compose(self) -> ComposeResult:
        with Container(id="kr-box"):
            yield Static(id="kr-title")
            with Horizontal(id="kr-panels"):
                yield Static(id="kr-col-left")
                yield Static(id="kr-col-right")
            yield Static("esc [dim]close[/dim]", id="kr-footer")

    def on_mount(self) -> None:
        super().on_mount()
        self._reflow()

    def _reflow(self) -> None:
        sections = KEY_REFERENCE.get(self._kr_context, [])
        label = CONTEXT_LABELS.get(
            self._kr_context, self._kr_context.upper().replace("-", " ")
        )
        box = self.query_one("#kr-box")
        title = self.query_one("#kr-title", Static)
        left = self.query_one("#kr-col-left", Static)
        right = self.query_one("#kr-col-right", Static)

        box.styles.width = 106
        title.update(f"[#edecee]KEY REFERENCE[/]  [dim]\u00b7  {label}[/dim]")

        left_secs, right_secs = _split_sections(sections)
        left_lines: list[str] = []
        for i, (name, bindings) in enumerate(left_secs):
            left_lines.extend(_section_lines(name, bindings, _PAD, prepend_blank=i > 0))
        right_lines: list[str] = []
        for i, (name, bindings) in enumerate(right_secs):
            right_lines.extend(
                _section_lines(name, bindings, _PAD, prepend_blank=i > 0)
            )
        left.update("\n".join(left_lines))
        right.update("\n".join(right_lines))
