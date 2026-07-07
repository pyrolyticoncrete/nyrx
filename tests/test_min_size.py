# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the terminal-size-floor lock (MinSizeModal + SizefloorHandlers).

The hard floor (``MIN_TERMINAL_WIDTH`` × ``MIN_TERMINAL_HEIGHT``) blocks
usage when the terminal is too small.  Tests cover the pure predicate,
the push/pop guard logic, the modal key handlers, and the CSS layout.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from textual.app import App
from textual.geometry import Size
from textual.widgets import Static

from nyrx.handlers.sizefloor import SizefloorHandlers
from nyrx.screens.min_size import MinSizeModal, below_floor
from nyrx.widgets.base import SEEK_INTERVAL

# ---------------------------------------------------------------------------
# Pure predicate
# ---------------------------------------------------------------------------


class TestBelowFloor:
    """``below_floor`` returns True when *either* dimension is below the floor."""

    def test_usable_at_floor(self) -> None:
        assert not below_floor(165, 23)

    def test_usable_above_floor(self) -> None:
        assert not below_floor(200, 40)

    def test_blocked_below_width(self) -> None:
        assert below_floor(164, 23)

    def test_blocked_below_height(self) -> None:
        assert below_floor(165, 22)

    def test_blocked_both(self) -> None:
        assert below_floor(100, 10)

    def test_blocked_below_both(self) -> None:
        assert below_floor(164, 22)


# ---------------------------------------------------------------------------
# SizefloorHandlers: stub helpers
# ---------------------------------------------------------------------------


class _Stub(SizefloorHandlers):
    """Minimal app-like object that exposes the mixin's real methods."""

    def __init__(self, w: int, h: int, *, locked: bool = False) -> None:
        self.size = Size(w, h)
        self._min_size_locked = locked
        self.call_after_refresh = MagicMock()
        self.push_screen = MagicMock()
        self.pop_screen = MagicMock()
        self.screen: object = object()  # default: not MinSizeModal


class TestCheckSizeFloor:
    """``_check_size_floor`` schedules push/pop via ``call_after_refresh``."""

    def test_push_when_below_floor(self) -> None:
        stub = _Stub(100, 10)
        stub._check_size_floor()
        assert stub._min_size_locked is True
        stub.call_after_refresh.assert_called_once()

    def test_no_push_when_above_floor(self) -> None:
        stub = _Stub(200, 40)
        stub._check_size_floor()
        assert stub._min_size_locked is False
        stub.call_after_refresh.assert_not_called()

    def test_no_double_push_when_already_locked(self) -> None:
        stub = _Stub(100, 10, locked=True)
        stub._check_size_floor()
        stub.call_after_refresh.assert_not_called()

    def test_pop_when_above_floor_and_locked(self) -> None:
        stub = _Stub(200, 40, locked=True)
        stub.screen = MinSizeModal()
        stub._check_size_floor()
        assert stub._min_size_locked is False
        stub.call_after_refresh.assert_called_once()

    def test_pop_only_if_screen_is_min_size_modal(self) -> None:
        stub = _Stub(200, 40, locked=True)
        stub.screen = object()  # not MinSizeModal
        stub._check_size_floor()
        assert stub._min_size_locked is False
        stub.call_after_refresh.assert_called_once()

    def test_relock_aborts_deferred_pop(self) -> None:
        stub = _Stub(200, 40, locked=False)
        stub.screen = MinSizeModal()
        # pop_lock runs: screen is MinSizeModal, _min_size_locked is False → pop
        stub._pop_lock()
        stub.pop_screen.assert_called_once()
        # now simulate re-lock between check and pop: _min_size_locked set True
        stub._min_size_locked = True
        stub.pop_screen.reset_mock()
        stub._pop_lock()
        stub.pop_screen.assert_not_called()


class TestPushLock:
    """``_push_lock`` pushes a MinSizeModal when still locked."""

    def test_push_modal(self) -> None:
        stub = _Stub(100, 10, locked=True)
        stub._push_lock()
        stub.push_screen.assert_called_once()
        assert isinstance(stub.push_screen.call_args[0][0], MinSizeModal)

    def test_no_push_when_not_locked(self) -> None:
        stub = _Stub(100, 10, locked=False)
        stub._push_lock()
        stub.push_screen.assert_not_called()


class TestPopLock:
    """``_pop_lock`` pops only when MinSizeModal is the active screen."""

    def test_pop_when_modal_on_top(self) -> None:
        stub = _Stub(200, 40, locked=False)
        stub.screen = MinSizeModal()
        stub._pop_lock()
        stub.pop_screen.assert_called_once()

    def test_no_pop_when_modal_not_on_top(self) -> None:
        stub = _Stub(200, 40, locked=False)
        stub.screen = object()
        stub._pop_lock()
        stub.pop_screen.assert_not_called()

    def test_no_pop_when_still_locked(self) -> None:
        stub = _Stub(200, 40, locked=True)
        stub.screen = MinSizeModal()
        stub._pop_lock()
        stub.pop_screen.assert_not_called()


# ---------------------------------------------------------------------------
# MinSizeModal: key handler tests
# ---------------------------------------------------------------------------


class TestMinSizeModalKeys:
    """Transport keys forward to mpv IPC; escape and q are handled directly."""

    def test_escape_noop(self) -> None:
        event = MagicMock()
        stub = MagicMock()
        MinSizeModal.key_escape(stub, event)
        stub.dismiss.assert_not_called()

    def test_q_calls_quit(self) -> None:
        stub = MagicMock()
        MinSizeModal.action_quit(stub)
        stub.app.action_quit.assert_called_once()

    def test_key_space_toggles_pause(self) -> None:
        stub = MagicMock()
        stub.app._mpv_ipc = MagicMock()
        MinSizeModal.key_space(stub)
        stub.app._mpv_ipc.toggle_pause.assert_called_once()

    def test_key_space_no_ipc(self) -> None:
        stub = MagicMock()
        stub.app._mpv_ipc = None
        MinSizeModal.key_space(stub)  # should not raise

    def test_key_left_seeks_backward(self) -> None:
        stub = MagicMock()
        stub.app._mpv_ipc = MagicMock()
        MinSizeModal.key_left(stub)
        stub.app._mpv_ipc.seek.assert_called_once_with(-SEEK_INTERVAL)

    def test_key_right_seeks_forward(self) -> None:
        stub = MagicMock()
        stub.app._mpv_ipc = MagicMock()
        MinSizeModal.key_right(stub)
        stub.app._mpv_ipc.seek.assert_called_once_with(SEEK_INTERVAL)

    def test_key_left_no_ipc(self) -> None:
        stub = MagicMock()
        stub.app._mpv_ipc = None
        MinSizeModal.key_left(stub)  # should not raise

    def test_key_right_no_ipc(self) -> None:
        stub = MagicMock()
        stub.app._mpv_ipc = None
        MinSizeModal.key_right(stub)  # should not raise


# ---------------------------------------------------------------------------
# CSS regression: hint-line clipping
# ---------------------------------------------------------------------------


class _ModalHost(App):
    """Minimal host so ``run_test`` can mount :class:`MinSizeModal`."""

    def compose(self):
        yield Static("base")


class TestMinSizeModalCSS:
    """Verify the hint row isn't clipped by stale width:100% rules."""

    @pytest.mark.asyncio
    async def test_hint_not_clipped_at_below_floor_size(self) -> None:
        """At a realistic below-floor terminal the hint must not clip."""
        app = _ModalHost()
        async with app.run_test(size=(100, 23)) as pilot:
            app.push_screen(MinSizeModal())
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, MinSizeModal):
                    break
            hint = app.screen.query_one("#min-size-hint", Static)
            # hint content is 39 cells wide; must not be narrower
            assert hint.size.width >= 39

    @pytest.mark.asyncio
    async def test_hint_not_clipped_at_smaller_width(self) -> None:
        """Even at 45 columns the hint fits without clipping."""
        app = _ModalHost()
        async with app.run_test(size=(45, 23)) as pilot:
            app.push_screen(MinSizeModal())
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, MinSizeModal):
                    break
            hint = app.screen.query_one("#min-size-hint", Static)
            assert hint.size.width >= 39

    @pytest.mark.asyncio
    async def test_box_hugs_widest_child(self) -> None:
        """The box auto-sizes to the hint (widest line), not to 1fr."""
        app = _ModalHost()
        async with app.run_test(size=(100, 23)) as pilot:
            app.push_screen(MinSizeModal())
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, MinSizeModal):
                    break
            box = app.screen.query_one("#min-size-box")
            hint = app.screen.query_one("#min-size-hint", Static)
            # box must be at least as wide as the hint
            assert box.size.width >= hint.size.width
