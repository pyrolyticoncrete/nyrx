# SPDX-License-Identifier: AGPL-3.0-only

"""Terminal-size-floor mixin: locks the app below usable dimensions.

``SizefloorHandlers`` is mixed into :class:`~app.MediaApp` alongside
the other handler mixins.  ``_check_size_floor`` is called from
``FocusHandlers.on_resize`` and on a 0.5 s watchdog so that a missed
resize event never leaves the lock stale.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from nyrx.screens.min_size import MinSizeModal, below_floor

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


class SizefloorHandlers:
    """Mixin that enforces the minimum-terminal-size lock."""

    def _check_size_floor(self: MediaAppProtocol) -> None:
        w, h = self.size
        too_small = below_floor(w, h)

        if not too_small:
            if self._min_size_locked:
                self._min_size_locked = False
                logger.debug("_check_size_floor: unlocked, scheduling pop")
                self.call_after_refresh(self._pop_lock)
        elif not self._min_size_locked:
            self._min_size_locked = True
            logger.debug("_check_size_floor: locked, scheduling push")
            self.call_after_refresh(self._push_lock)

    def _push_lock(self: MediaAppProtocol) -> None:
        if not self._min_size_locked:
            return
        try:
            self.push_screen(MinSizeModal())
            logger.debug("_push_lock: MinSizeModal pushed")
        except Exception:
            logger.exception("_push_lock: push_screen failed")
            self._min_size_locked = False

    def _pop_lock(self: MediaAppProtocol) -> None:
        if self._min_size_locked:
            return
        if not isinstance(self.screen, MinSizeModal):
            return
        try:
            self.pop_screen()
            logger.debug("_pop_lock: MinSizeModal popped")
        except Exception:
            logger.exception("_pop_lock: pop_screen failed")
