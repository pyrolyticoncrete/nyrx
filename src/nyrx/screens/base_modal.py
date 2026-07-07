# SPDX-License-Identifier: AGPL-3.0-only

"""Shared base class for all modal screens.

Provides consistent overlay styling, escape-to-dismiss, and auto-focus.
"""

from __future__ import annotations

import logging
from typing import ClassVar

from textual import events
from textual.binding import Binding
from textual.screen import ModalScreen
from textual.widgets import DataTable, Input, ListView, OptionList

logger = logging.getLogger(__name__)


class BaseModal[T](ModalScreen[T]):
    """Consistent modal screen with overlay, escape-dismiss, and auto-focus."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = []

    def key_escape(self, event: events.Key) -> None:
        # Do not also handle escape in on_key or BINDINGS: see queue modal escape bug.
        # Doing so calls dismiss() on an already-popped screen, causing a swallowed
        # ScreenStackError on the second dismiss. All escape handling lives here.
        event.stop()
        self.dismiss(None)

    def on_mount(self) -> None:
        logger.debug("BaseModal.on_mount: class=%s", type(self).__name__)
        self._focus_first()

    def _focus_first(self) -> None:
        for widget in self.query("*"):
            if isinstance(widget, (Input, ListView, DataTable, OptionList)):
                widget.focus()
                return
