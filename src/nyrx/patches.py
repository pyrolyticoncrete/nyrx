# SPDX-License-Identifier: AGPL-3.0-only

"""Monkey-patches for third-party library bugs / perf issues not yet upstream.

Currently patches:
- ``textual-image==0.13.2``: Konsole sixel rendering is broken.
  Konsole reports sixel support via DA1 response but renders images
  incorrectly. The ``KONSOLE_VERSION`` env var is used to detect Konsole
  and force halfcell rendering instead, which works correctly.

  When Konsole fixes sixel support, remove this patch.

- ``textual-image==0.13.2``: sixel stale-line artifact (cursor off-by-one).
  The ``_get_sixel_segments()`` method positions the cursor at
  ``visible_region.bottom`` (one row past the widget) instead of
  ``y + height - 1`` (last row of the widget).  During Textual's
  ``LayoutUpdate`` path (full-screen redraw triggered by modal
  push/pop), this off-by-one causes a stale horizontal line at the
  bottom row of the sixel widget.

  When upstream releases a fix, remove this file, update
  ``pyproject.toml`` to ``textual-image>=<fixed_version>``, and
  delete the ``import patches`` line from ``main.py``.

- ``textual==8.2.8``: DataTable per-cell Style object construction.
  ``_get_row_style()`` calls ``get_component_styles(name).rich_style``
  which creates a fresh ``RenderStyles`` and recomputes the full
  ancestor chain on every call, bypassing ``_rich_style_cache``.  The
  patch replaces these with ``get_component_rich_style(name)`` which
  returns a cached ``Style`` object.

  When upstream fixes this, remove the patch below.

- ``textual==8.2.8``: ToastRack ghost cells after toast expiry.
  ``Toast._expire()`` calls ``_unnotify(refresh=False)`` then removes
  the ToastHolder.  The ``_toastrack`` layer is never fully repainted,
  leaving stale ghost cells.  Patching ``ToastRack.show()`` to call
  ``self.refresh()`` forces a full repaint after every mount/removal.

  When upstream fixes this, remove the patch below.
"""

from __future__ import annotations

import logging
import os

import textual_image.widget as _widget
from rich.control import Control
from rich.segment import ControlType, Segment
from rich.style import Style
from textual_image.widget.sixel import _ImageSixelImpl

logger = logging.getLogger(__name__)

# --- Konsole sixel fix ---------------------------------------------------
# Konsole reports sixel support via DA1, but renders images incorrectly.
# Force halfcell rendering when KONSOLE_VERSION is set.
if os.environ.get("KONSOLE_VERSION"):
    _widget.Image = _widget.HalfcellImage  # type: ignore[assignment]  # intentional monkey-patch
    logger.debug("Konsole detected: forcing halfcell image rendering")

_original = _ImageSixelImpl._get_sixel_segments


def _patched_get_sixel_segments(
    self: _ImageSixelImpl, sixel_data: str
) -> list[Segment]:
    vr = self.screen.find_widget(self).visible_region
    return [
        Segment(Control.move_to(vr.x, vr.y).segment.text, style=Style()),
        Segment(
            sixel_data,
            style=Style(),
            control=((ControlType.CURSOR_FORWARD, 0),),
        ),
        Segment(
            Control.move_to(vr.right, vr.y + vr.height - 1).segment.text,
            style=Style(),
        ),
    ]


_ImageSixelImpl._get_sixel_segments = _patched_get_sixel_segments  # type: ignore[method-assign]  # intentional monkey-patch
logger.debug(
    "Applied sixel cursor-position monkey-patch "
    "(visible_region.bottom → y + height - 1)"
)


# --- DataTable Style cache optimization -----------------------------------
# _get_row_style() calls get_component_styles(name).rich_style which creates
# a fresh RenderStyles + recomputes the full ancestor chain on every call,
# bypassing _rich_style_cache.  Replace with get_component_rich_style() which
# returns a cached Style object.

from textual.widgets._data_table import DataTable  # noqa: E402


def _patched_get_row_style(self: DataTable, row_index: int, base_style: Style) -> Style:
    if row_index == -1:
        row_style = self.get_component_rich_style("datatable--header")
    elif row_index < self.fixed_rows:
        row_style = self.get_component_rich_style("datatable--fixed")
    else:
        if self.zebra_stripes:
            component_row_style = (
                "datatable--odd-row" if row_index % 2 else "datatable--even-row"
            )
            row_style = self.get_component_rich_style(component_row_style)
        else:
            row_style = base_style
    return row_style


DataTable._get_row_style = _patched_get_row_style  # type: ignore[assignment]  # intentional monkey-patch
logger.debug(
    "Applied DataTable _get_row_style monkey-patch "
    "(get_component_styles → get_component_rich_style)"
)


# --- ToastRack ghost-cell fix ----------------------------------------------
# When toasts expire, _expire() calls _unnotify(refresh=False) then removes
# the ToastHolder.  The _toastrack layer is never fully repainted, leaving
# stale ghost cells.  Patching show() to call self.refresh() after every
# mount/removal forces a full repaint of the ToastRack area.

from textual.notifications import Notifications  # noqa: E402
from textual.widgets._toast import ToastRack as _OrigToastRack  # noqa: E402

_orig_show = _OrigToastRack.show


def _patched_show(self: _OrigToastRack, notifications: Notifications) -> None:
    _orig_show(self, notifications)
    self.refresh()


_OrigToastRack.show = _patched_show  # type: ignore[assignment]  # intentional monkey-patch
logger.debug(
    "Applied ToastRack ghost-cell monkey-patch (show → refresh after mount/removal)"
)
