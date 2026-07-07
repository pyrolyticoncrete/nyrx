# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any, cast

from textual.events import Key
from textual.widgets import Static

from nyrx.helpers import BRAILLE_SPINNER
from nyrx.models import MediaRequest, PlaybackState

from .base import SEEK_INTERVAL, SIDEBAR_BAR_W, SIDEBAR_TEXT_W

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


class BaseNowPlaying(Static):
    can_focus = True
    _spinner_frames = BRAILLE_SPINNER
    _track: MediaRequest | None

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._cancel_selected = False
        self._loading = False
        self._offline_mode = False

    # ------------------------------------------------------------------
    # Shared key handlers
    # ------------------------------------------------------------------

    def key_left(self) -> None:
        app = self.app
        if hasattr(app, "_mpv_ipc") and app._mpv_ipc:
            logger.debug("BaseNowPlaying.key_left: seek %s", -SEEK_INTERVAL)
            app._mpv_ipc.seek(-SEEK_INTERVAL)

    def key_right(self) -> None:
        app = self.app
        if hasattr(app, "_mpv_ipc") and app._mpv_ipc:
            logger.debug("BaseNowPlaying.key_right: seek %s", SEEK_INTERVAL)
            app._mpv_ipc.seek(SEEK_INTERVAL)

    def key_space(self) -> None:
        app = self.app
        if hasattr(app, "_mpv_ipc") and app._mpv_ipc:
            logger.debug("BaseNowPlaying.key_space: toggle_pause")
            app._mpv_ipc.toggle_pause()

    def key_x(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if app._cancel_active:
            logger.debug("BaseNowPlaying.key_x: exit_cancel_mode")
            app._exit_cancel_mode()
        else:
            targets = app._cancel_targets
            if not targets:
                logger.debug("BaseNowPlaying.key_x: no_cancel_targets")
                return
            if len(targets) == 1:
                target = targets[0]
                logger.debug("BaseNowPlaying.key_x: single_target=%s", target)
                if target == "playback":
                    app._skip_playback()
                elif target == "download":
                    app._cancel_download()
                return
            logger.debug(
                "BaseNowPlaying.key_x: enter_cancel_mode targets=%s", len(targets)
            )
            app._enter_cancel_mode()

    def key_up(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if app._cancel_active:
            logger.debug("BaseNowPlaying.key_up: cycle_cancel -1")
            app._cycle_cancel_target(-1)

    def key_down(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if app._cancel_active:
            logger.debug("BaseNowPlaying.key_down: cycle_cancel +1")
            app._cycle_cancel_target(1)

    def key_enter(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if app._cancel_active:
            logger.debug("BaseNowPlaying.key_enter: confirm_cancel")
            app._confirm_cancel()

    def key_escape(self, event: Key) -> None:
        app = cast("MediaAppProtocol", self.app)
        if app._cancel_active:
            logger.debug("BaseNowPlaying.key_escape: exit_cancel_mode")
            app._exit_cancel_mode()
        else:
            logger.debug("BaseNowPlaying.key_escape: focus_main_panel")
            app._focus_main_panel()
        event.stop()

    # ------------------------------------------------------------------
    # Focus / highlight
    # ------------------------------------------------------------------

    def set_cancel_highlight(self, selected: bool) -> None:
        self._cancel_selected = selected
        self._refresh()

    def watch_has_focus(self, value: bool) -> None:
        app = cast("MediaAppProtocol", self.app)
        try:
            app._update_mode_indicator()
        except Exception:
            logger.debug("Failed to update mode indicator on focus change")

    # ------------------------------------------------------------------
    # Helper
    # ------------------------------------------------------------------

    def _current_spinner_frame(self) -> str:
        return self._spinner_frames[
            int(time.monotonic() * 12.5) % len(self._spinner_frames)
        ]

    def _cancel_preamble(self) -> tuple[bool, str, str]:
        cancel_active = getattr(self.app, "_cancel_active", False)
        play_accent = "rgb(242,140,229)"
        accent = play_accent if not cancel_active or self._cancel_selected else "gray50"
        x_st = "bold red" if self._cancel_selected else "gray50"
        return cancel_active, accent, x_st

    @staticmethod
    def _trunc(text: str, max_len: int) -> str:
        if len(text) > max_len:
            return text[: max_len - 1] + "\u2026"
        return text

    @staticmethod
    def _progress_bar(frac: float, time_len: int, suffix_len: int = 0) -> str:
        bar_w = min(SIDEBAR_BAR_W, max(4, SIDEBAR_TEXT_W - 2 - time_len - suffix_len))
        filled = max(0, min(bar_w, int(bar_w * frac)))
        return "\u2588" * filled + "\u2591" * (bar_w - filled)

    # ------------------------------------------------------------------
    # Unified interface
    # ------------------------------------------------------------------

    def start_playback(self, request: MediaRequest) -> None:
        self._track = request
        self._cancel_selected = False
        self.display = True
        logger.debug("BaseNowPlaying.start_playback: title=%s", request.title[:30])

    def stop_playback(self) -> None:
        raise NotImplementedError

    def _refresh(self) -> None:
        raise NotImplementedError

    def clear(self) -> None:
        raise NotImplementedError

    def update_state(self, state: PlaybackState) -> None:
        raise NotImplementedError

    def should_show_spinner(self) -> bool:
        raise NotImplementedError
