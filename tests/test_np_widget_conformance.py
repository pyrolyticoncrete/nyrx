# SPDX-License-Identifier: AGPL-3.0-only

"""Conformance tests: every now-playing widget implements the NP interface.

``BaseNowPlaying`` defines 6 interface methods (``start_playback`` plus 5 that
``raise NotImplementedError``).  Each concrete subclass *must* override all 6
Otherwise it inherits the error-raising stub.
"""

from __future__ import annotations

import pytest

from nyrx.widgets.sidebar import (
    RadioNowPlaying,
    SidebarNowPlaying,
    SoundCloudNowPlaying,
    TVNowPlaying,
)

NP_INTERFACE = frozenset(
    {
        "start_playback",
        "stop_playback",
        "_refresh",
        "clear",
        "update_state",
        "should_show_spinner",
    }
)

NP_WIDGETS = [
    SidebarNowPlaying,
    SoundCloudNowPlaying,
    RadioNowPlaying,
    TVNowPlaying,
]

# Key handlers defined on BaseNowPlaying: SidebarNowPlaying inherits them.
KEY_HANDLERS = frozenset(
    {
        "key_left",
        "key_right",
        "key_space",
        "key_x",
        "key_up",
        "key_down",
        "key_enter",
        "key_escape",
    }
)


class TestNPWidgetConformance:
    @pytest.mark.parametrize("cls", NP_WIDGETS, ids=lambda c: c.__name__)
    def test_all_interface_methods_overridden(self, cls: type) -> None:
        missing = {m for m in NP_INTERFACE if cls.__dict__.get(m) is None}
        assert not missing, (
            f"{cls.__name__} inherits NotImplementedError stubs for: {sorted(missing)}"
        )

    @pytest.mark.parametrize("cls", NP_WIDGETS, ids=lambda c: c.__name__)
    def test_can_focus_is_true(self, cls: type) -> None:
        assert cls.can_focus is True

    @pytest.mark.parametrize("handler", sorted(KEY_HANDLERS))
    def test_sidebar_inherits_key_handlers(self, handler: str) -> None:
        assert hasattr(SidebarNowPlaying, handler), (
            f"SidebarNowPlaying missing key handler {handler!r}"
        )
