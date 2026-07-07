# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual.events import Key
from textual.widgets import ListView, Static

from nyrx.config import YT_QUALITY_PRESETS

from .base import SIDEBAR_TEXT_W

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


class DownloadWidget(Static):
    """Sidebar download widget: progress scanner, format/quality selector, cancel mode."""

    can_focus = True

    # --- Knight Rider scanner constants ---
    _SCANNER_WIDTH = 8
    _SCANNER_HOLD_START = 30
    _SCANNER_HOLD_END = 9
    _SCANNER_TOTAL_FRAMES = (
        _SCANNER_WIDTH + _SCANNER_HOLD_END + (_SCANNER_WIDTH - 1) + _SCANNER_HOLD_START
    )  # 54
    _SCANNER_TRAIL_STEPS = 6

    _COLOR_CONNECTING = "#ffca85"
    _COLOR_DOWNLOADING = "#a277ff"
    _COLOR_PROCESSING = "#61ffca"

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._download_state: dict | None = None
        self._dl_scanner_idx = 0
        self._frames: list[Text] = []
        self._dl_scanner_color: str | None = None
        self._dl_select_mode: str | None = None
        self._dl_select_idx = 0
        self._dl_select_choices: list[str] | None = None
        self._dl_select_caption = ""
        self._cancel_selected = False

    def update_progress(self, state: dict | None) -> None:
        self._download_state = state
        if state:
            logger.debug(
                "update_progress: status=%s title=%s pct=%s",
                state.get("status"),
                state.get("title", "")[:30],
                state.get("pct"),
            )
        self._refresh()

    def show_format_selector(self, choices: list[str]) -> None:
        logger.debug("show_format_selector: choices=%s", choices)
        self._dl_select_mode = "type"
        self._dl_select_choices = choices
        self._dl_select_idx = 0
        self._dl_select_caption = "Select format:"
        self.display = True
        self._refresh()

    def show_tv_quality_selector(self, choices: list[str]) -> None:
        self._dl_select_mode = "tv_quality"
        self._dl_select_choices = choices
        self._dl_select_idx = 0
        self._dl_select_caption = "Select quality:"
        self.display = True
        self._refresh()

    def hide_format_selector(self) -> None:
        logger.debug("hide_format_selector")
        self._dl_select_mode = None
        self._dl_select_idx = 0
        self._dl_select_choices = None
        self._refresh()

    def set_cancel_highlight(self, selected: bool) -> None:
        self._cancel_selected = selected
        self._refresh()

    def clear(self) -> None:
        logger.debug("clear: had_state=%s", bool(self._download_state))
        self._download_state = None
        self._dl_scanner_idx = 0
        self._frames = []
        self._dl_scanner_color = None
        self._dl_select_mode = None
        self._dl_select_idx = 0
        self._dl_select_choices = None
        self._cancel_selected = False
        self.display = False

    def watch_has_focus(self, value: bool) -> None:
        app = cast("MediaAppProtocol", self.app)
        try:
            app._update_mode_indicator()
        except Exception:
            logger.debug("Failed to update mode indicator on focus change")

    def on_mount(self) -> None:
        self._refresh()

    def update_spinner_frame(self) -> None:
        self._dl_scanner_idx += 1

    @staticmethod
    def _scanner_position(
        frame: int, width: int = 8
    ) -> tuple[int, bool, int, int | None]:
        """Return (head_position, is_moving_forward, hold_progress, hold_total).

        Phase 1: forward scan  (width steps)
        Phase 2: hold at end   (HOLD_END = 9 steps)
        Phase 3: backward scan (width - 1 steps)
        Phase 4: hold at start (HOLD_START = 30 steps)
        """
        f = frame % (
            width
            + DownloadWidget._SCANNER_HOLD_END
            + (width - 1)
            + DownloadWidget._SCANNER_HOLD_START
        )
        if f < width:
            return (f, True, 0, None)
        f -= width
        if f < DownloadWidget._SCANNER_HOLD_END:
            return (width - 1, True, f, DownloadWidget._SCANNER_HOLD_END)
        f -= DownloadWidget._SCANNER_HOLD_END
        if f < width - 1:
            return (width - 2 - f, False, 0, None)
        f -= width - 1
        return (0, False, f, DownloadWidget._SCANNER_HOLD_START)

    @staticmethod
    def _scanner_trail_colors(hex_color: str, steps: int = 6) -> list[str]:
        """Derive trail gradient hex strings (head → bloom → exponential decay)."""
        r, g, b = (
            int(hex_color[1:3], 16),
            int(hex_color[3:5], 16),
            int(hex_color[5:7], 16),
        )
        colors = []
        for i in range(steps):
            if i == 0:
                fr = fg = fb = 1.0
            elif i == 1:
                fr = fg = fb = 1.15
            else:
                decay = 0.65 ** (i - 1)
                fr = fg = fb = decay
            colors.append(
                f"#{min(255, int(r * fr)):02x}{min(255, int(g * fg)):02x}{min(255, int(b * fb)):02x}"
            )
        return colors

    @staticmethod
    def _build_scanner_frames(color: str, width: int = 8) -> list[Text]:
        """Pre-compute one full scanner cycle (54 frames) with per-character colors."""
        total = (
            width
            + DownloadWidget._SCANNER_HOLD_END
            + (width - 1)
            + DownloadWidget._SCANNER_HOLD_START
        )
        trail = DownloadWidget._scanner_trail_colors(color)
        frames: list[Text] = []
        for frame_idx in range(total):
            head_pos, moving_fwd, hold_prog, hold_total = (
                DownloadWidget._scanner_position(frame_idx, width)
            )
            text = Text()
            for char_idx in range(width):
                dist = (head_pos - char_idx) if moving_fwd else (char_idx - head_pos)
                if dist == 0:
                    cidx = 0
                elif 0 < dist < len(trail):
                    cidx = dist + (hold_prog if hold_total is not None else 0)
                else:
                    cidx = -1

                if cidx >= 0 and cidx < len(trail):
                    text.append(
                        "\u25a0", style=f"bold {trail[min(cidx, len(trail) - 1)]}"
                    )
                else:
                    fade = 1.0
                    if hold_total is not None and hold_total > 0:
                        prog = min(hold_prog / hold_total, 1)
                        fade = max(0.3, 1 - prog * 0.7)
                    r, g, b = (
                        int(color[1:3], 16),
                        int(color[3:5], 16),
                        int(color[5:7], 16),
                    )
                    inactive = f"#{int(r * 0.6 * fade):02x}{int(g * 0.6 * fade):02x}{int(b * 0.6 * fade):02x}"
                    text.append("\u2b1d", style=f"bold {inactive}")
            frames.append(text)
        return frames

    def _ensure_scanner_frames(self, desired_color: str) -> None:
        """Regenerate scanner frames if color changed since last render."""
        if desired_color != self._dl_scanner_color:
            self._frames = self._build_scanner_frames(desired_color)
            self._dl_scanner_color = desired_color

    def _refresh(self) -> None:
        """Re-render the widget's Rich Text content based on current download state."""
        app = cast("MediaAppProtocol", self.app)
        sections: list[Text] = []
        cancel_active = getattr(app, "_cancel_active", False)
        sidebar_w = SIDEBAR_TEXT_W + 2
        dl_accent = "#1A396F"

        if self._download_state:
            st = self._download_state
            status = st.get("status", "")
            s = Text()
            if status == "downloading":
                paused = getattr(app, "_download_paused_for_offline", False)
                accent = (
                    "gray50"
                    if paused
                    else (
                        dl_accent
                        if not cancel_active or self._cancel_selected
                        else "gray50"
                    )
                )
                x_st = (
                    "bold red" if (self._cancel_selected and not paused) else "gray50"
                )

                # Phase colour for scanner
                stage = st.get("stage", "")
                pct = st.get("pct", 0)
                is_cancelling = stage == "cancelling"
                if not is_cancelling and not paused:
                    desired_color = (
                        self._COLOR_CONNECTING if pct == 0 else self._COLOR_DOWNLOADING
                    )
                elif paused:
                    desired_color = self._dl_scanner_color or self._COLOR_DOWNLOADING
                else:
                    desired_color = self._COLOR_CONNECTING
                if not is_cancelling:
                    self._ensure_scanner_frames(desired_color)

                scanner = (
                    self._frames[self._dl_scanner_idx % len(self._frames)]
                    if self._frames
                    else Text()
                )

                # Line 1: caption
                s.append("\u258c", style=accent)
                s.append(" ")
                if paused:
                    s.append("download paused  ", style="dim")
                    offline = getattr(app, "_download_paused_for_offline", False)
                    if offline:
                        s.append("[offline]", style="red")
                else:
                    cap = "downloading [\u2193]"
                    s.append(cap, style="dim")
                    if cancel_active:
                        pads = sidebar_w - 2 - len(cap) - 4
                        if pads > 0:
                            s.append(" " * pads)
                        s.append(" [X]", style=x_st)
                s.append("\n")

                # Line 2: title
                s.append("\u258c", style=accent)
                s.append(" ")
                dl_title = st.get("title", "")
                dl_w = sidebar_w - 2
                if len(dl_title) > dl_w:
                    s.append(dl_title[: dl_w - 1] + "\u2026")
                else:
                    s.append(dl_title)
                s.append("\n")

                # Line 3: scanner + phase info
                s.append("\u258c", style=accent)
                s.append(" ")
                if is_cancelling:
                    s.append("cancelling...", style="bold red")
                elif paused:
                    s.append_text(scanner)
                    s.append("  \u00b7 paused")
                elif pct == 0:
                    s.append_text(scanner)
                    s.append("  \u00b7 connecting")
                else:
                    s.append_text(scanner)
                    speed = st.get("speed", 0)
                    eta = st.get("eta", 0)
                    if speed > 0 and eta > 0:
                        try:
                            fmt_speed = app._fmt_speed(speed)
                            fmt_eta = app._fmt_eta(eta)
                            s.append(f"  \u00b7 {fmt_speed} \u00b7 ETA {fmt_eta}")
                        except Exception:
                            logger.debug("Failed to format speed/ETA")
                sections.append(s)

            elif status == "processing":
                self._ensure_scanner_frames(self._COLOR_PROCESSING)
                scanner = (
                    self._frames[self._dl_scanner_idx % len(self._frames)]
                    if self._frames
                    else Text()
                )
                accent = dl_accent

                # Line 1: caption
                s.append("\u258c", style=accent)
                s.append(" ")
                s.append("downloading [\u2193]", style="dim")
                s.append("\n")

                # Line 2: title
                s.append("\u258c", style=accent)
                s.append(" ")
                dl_title = st.get("title", "")
                dl_w = sidebar_w - 2
                if len(dl_title) > dl_w:
                    s.append(dl_title[: dl_w - 1] + "\u2026")
                else:
                    s.append(dl_title)
                s.append("\n")

                # Line 3: scanner + stage
                s.append("\u258c", style=accent)
                s.append(" ")
                s.append_text(scanner)
                stage = st.get("stage", "processing")
                s.append(f"  \u00b7 {stage}")
                sections.append(s)

            elif status == "complete":
                s.append("\u258c", style=dl_accent)
                s.append(" ")
                s.append("\u2713 ", style="bold green")
                s.append("Download complete")
                sections.append(s)
            elif status == "error":
                s.append("\u258c", style=dl_accent)
                s.append(" ")
                s.append("\u2717 ", style="bold red")
                s.append(f"Failed: {st.get('msg', '')}")
                sections.append(s)
            elif status == "cancelled":
                s.append("\u258c", style=dl_accent)
                s.append(" ")
                s.append("\u2717 ", style="bold red")
                s.append(f"{st.get('msg', '')}")
                sections.append(s)
            elif status == "already_exists":
                s.append("\u258c", style=dl_accent)
                s.append(" ")
                s.append("\u26a0 ", style="bold yellow")
                s.append("already exists")
                sections.append(s)

        if self._dl_select_mode:
            s = Text()
            self._render_dl_selector(s)
            sections.append(s)

        t = Text()
        for i, section in enumerate(sections):
            t.append_text(section)
            if i < len(sections) - 1:
                t.append("\n\n")
        self.update(t)

    def _render_dl_selector(self, t: Text) -> None:
        cap = "download  [\u2193]"
        accent = "#1A396F"
        t.append("\u258c", style=accent)
        t.append(" ")
        t.append(cap, style="dim")
        t.append("\n")
        t.append("\u258c", style=accent)
        t.append(" ")
        t.append(self._dl_select_caption)
        t.append("\n")
        t.append("\u258c", style=accent)
        t.append(" ")
        for i, choice in enumerate(self._dl_select_choices or []):
            if i == self._dl_select_idx:
                t.append(f"  [{choice}]", style="bold #F28CE5")
            else:
                t.append(f"  [{choice}]", style="dim")

    def _confirm_dl_selection(self) -> None:
        """Handle Enter in download format/quality selector."""
        app = cast("MediaAppProtocol", self.app)
        if self._dl_select_mode == "type":
            choice = (
                self._dl_select_choices[self._dl_select_idx]
                if self._dl_select_choices
                else ""
            )
            logger.debug("_confirm_dl_selection: type=%s", choice)
            if choice == "Audio":
                self._dl_select_mode = None
                app._start_download(audio_only=True, _user_initiated=True)
                self._refresh()
                try:
                    app.query_one("#results-list", ListView).focus()
                except Exception:
                    logger.debug("Failed to focus results list after audio selection")
            else:
                self._dl_select_mode = "quality"
                self._dl_select_choices = [p[0] for p in YT_QUALITY_PRESETS]
                self._dl_select_idx = 0
                self._dl_select_caption = "Select quality:"
                self._refresh()
        elif self._dl_select_mode == "quality":
            quality_label = (
                self._dl_select_choices[self._dl_select_idx]
                if self._dl_select_choices
                else ""
            )
            logger.debug("_confirm_dl_selection: quality=%s", quality_label)
            self._dl_select_mode = None
            app._start_download(
                audio_only=False, quality=quality_label, _user_initiated=True
            )
            self._refresh()
            try:
                app.query_one("#results-list", ListView).focus()
            except Exception:
                logger.debug("Failed to focus results list after quality selection")

    def _confirm_tv_selection(self) -> None:
        """Handle Enter in TV quality selector: start the TV/Movies download."""
        app = cast("MediaAppProtocol", self.app)
        quality_label = (
            self._dl_select_choices[self._dl_select_idx]
            if self._dl_select_choices
            else "1080p"
        )
        self._dl_select_mode = None
        app._start_tv_movies_download(quality=quality_label)
        self._refresh()
        try:
            app.query_one("#results-list", ListView).focus()
        except Exception:
            logger.debug(
                "_confirm_tv_selection: #results-list not found, skipping focus restore"
            )

    def _cancel_dl_selection(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        self._dl_select_mode = None
        self._dl_select_idx = 0
        self._dl_select_choices = None
        app._pending_dl_data = None
        if self._download_state is None:
            self.display = False
        self._refresh()
        try:
            app.query_one("#results-list", ListView).focus()
        except Exception:
            logger.debug("Failed to focus results list after cancel selection")
        if hasattr(app, "_update_mode_indicator"):
            app._update_mode_indicator()

    def key_left(self) -> None:
        if self._dl_select_mode:
            self._dl_select_idx = (self._dl_select_idx - 1) % len(
                self._dl_select_choices or [1]
            )
            self._refresh()

    def key_right(self) -> None:
        if self._dl_select_mode:
            self._dl_select_idx = (self._dl_select_idx + 1) % len(
                self._dl_select_choices or [1]
            )
            self._refresh()

    def key_x(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if app._cancel_active:
            app._exit_cancel_mode()
        else:
            targets = app._cancel_targets
            if not targets:
                return
            if len(targets) == 1:
                target = targets[0]
                if target == "playback":
                    app._skip_playback()
                elif target == "download":
                    app._cancel_download()
                return
            app._enter_cancel_mode()

    def key_up(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if not self._dl_select_mode and app._cancel_active:
            app._cycle_cancel_target(-1)

    def key_down(self) -> None:
        app = cast("MediaAppProtocol", self.app)
        if not self._dl_select_mode and app._cancel_active:
            app._cycle_cancel_target(1)

    def key_enter(self, event: Key) -> None:
        app = cast("MediaAppProtocol", self.app)
        if self._dl_select_mode == "tv_quality":
            self._confirm_tv_selection()
            event.stop()
            return
        if self._dl_select_mode:
            self._confirm_dl_selection()
            event.stop()
        elif hasattr(app, "_cancel_active") and app._cancel_active:
            app._confirm_cancel()

    def key_tab(self) -> None:
        if self._dl_select_mode:
            self._cancel_dl_selection()

    def key_escape(self, event: Key) -> None:
        app = cast("MediaAppProtocol", self.app)
        if self._dl_select_mode:
            self._cancel_dl_selection()
            event.stop()
        elif hasattr(app, "_cancel_active") and app._cancel_active:
            app._exit_cancel_mode()
            event.stop()
        else:
            app._focus_main_panel()
            event.stop()

    @staticmethod
    def _trunc(text: str, max_len: int) -> str:
        if len(text) > max_len:
            return text[: max_len - 1] + "\u2026"
        return text
