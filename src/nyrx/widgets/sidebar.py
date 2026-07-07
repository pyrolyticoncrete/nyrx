# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import logging
from enum import Enum, auto
from typing import Any

from rich.text import Text

from nyrx.helpers import build_waveform
from nyrx.models import MediaKind, MediaRequest, PlaybackState
from nyrx.player import format_seconds

from .base import SIDEBAR_BAR_W, SIDEBAR_TEXT_W
from .base_now_playing import BaseNowPlaying

logger = logging.getLogger(__name__)

_PAUSE_MARKER = "  [PAUSED]"  # 10 cells
_LIVE_BLINK_HOLDS = (
    16  # 0.08s ticks per blink half-phase → ~1.3s half-phase (~2.6s full cycle)
)


class ScState(Enum):
    IDLE = auto()
    CONNECTING = auto()
    VISUALIZER = auto()
    FALLBACK = auto()


class SidebarNowPlaying(BaseNowPlaying):
    """Right-sidebar widget showing now-playing status.

    Renders a card for the currently playing video (with progress bar).
    Supports a cancel mode (triggered by ``x``) to stop playback.
    """

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._title = ""
        self._yt_id = ""
        self._audio_only = False
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._offline_mode = False
        self._loading = False
        self._buffering = False

    def start_playback(self, request: MediaRequest) -> None:
        super().start_playback(request)
        self._title = request.title
        self._yt_id = request.yt_id
        self._audio_only = request.audio_only
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._loading = True
        self._buffering = False
        logger.debug(
            "SidebarNowPlaying.start_playback: title=%s yt_id=%s audio=%s",
            request.title[:30],
            request.yt_id,
            request.audio_only,
        )
        self._refresh()

    def stop_playback(self) -> None:
        logger.debug("SidebarNowPlaying.stop_playback")
        self._title = ""
        self._yt_id = ""
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._offline_mode = False
        self._loading = False
        self._buffering = False
        self._cancel_selected = False
        self.display = False

    def clear(self) -> None:
        self.stop_playback()

    def update_state(self, state: PlaybackState) -> None:
        self._position = (
            state.position if state.position and state.position >= 0 else 0.0
        )
        self._duration = (
            state.duration if state.duration and state.duration > 0 else 1.0
        )
        self._paused = state.paused
        self._buffering = state.buffering
        if self._loading and state.duration and state.duration > 1:
            self._loading = False
        self._refresh()

    def should_show_spinner(self) -> bool:
        return self._loading or self._buffering

    def on_mount(self) -> None:
        logger.debug("SidebarNowPlaying.on_mount")
        self._refresh()

    def _refresh(self) -> None:
        cancel_active, accent, x_st = self._cancel_preamble()
        sidebar_w = SIDEBAR_TEXT_W + 2
        if self._title:
            s = Text()
            if self._offline_mode:
                cap = f"now playing {'[audio]' if self._audio_only else '[video]'} "
                s.append("\u258c", style="gray50")
                s.append(" ")
                s.append(cap, style="dim")
                s.append("[offline]", style="red")
                s.append("\n")
                s.append("\u258c", style="gray50")
                s.append(" ")
                title_w = self._title_available_width
                if len(self._title) > title_w:
                    s.append(self._title[: title_w - 1] + "\u2026")
                else:
                    s.append(self._title)
                s.append("\n")
                s.append("\u258c", style="gray50")
                s.append(" ")
                frac = (
                    min(1.0, self._position / self._duration)
                    if self._duration > 0
                    else 0.0
                )
                time_str = f"{format_seconds(self._position)} / {format_seconds(self._duration)}"
                bar = self._progress_bar(frac, len(time_str))
                s.append(f"{bar}  {time_str}")
                self.update(s)
            else:
                cap = f"now playing {'[audio]' if self._audio_only else '[video]'}"
                s = Text()
                s.append("\u258c", style=accent)
                s.append(" ")
                s.append(cap, style="dim")
                if cancel_active:
                    pads = sidebar_w - 2 - len(cap) - 4
                    if pads > 0:
                        s.append(" " * pads)
                    s.append(" [X]", style=x_st)
                s.append("\n")
                s.append("\u258c", style=accent)
                s.append(" ")
                title_w = self._title_available_width
                if len(self._title) > title_w:
                    s.append(self._title[: title_w - 1] + "\u2026")
                else:
                    s.append(self._title)
                s.append("\n")
                s.append("\u258c", style=accent)
                s.append(" ")
                if self._loading:
                    spinner = self._current_spinner_frame()
                    s.append(f"{spinner} connecting...")
                elif self._buffering:
                    frac = (
                        min(1.0, self._position / self._duration)
                        if self._duration > 0
                        else 0.0
                    )
                    time_str = f"{format_seconds(self._position)} / {format_seconds(self._duration)}"
                    bar = self._progress_bar(frac, len(time_str), 2)
                    s.append(f"{bar}  {time_str}")
                    s.append(" ")
                    spinner = self._current_spinner_frame()
                    s.append(spinner, style="dim")
                else:
                    frac = (
                        min(1.0, self._position / self._duration)
                        if self._duration > 0
                        else 0.0
                    )
                    time_str = f"{format_seconds(self._position)} / {format_seconds(self._duration)}"
                    bar = self._progress_bar(
                        frac, len(time_str), 10 if self._paused else 0
                    )
                    s.append(f"{bar}  {time_str}")
                    if self._paused:
                        s.append(_PAUSE_MARKER, style="bold yellow")
                self.update(s)

    @property
    def _title_available_width(self) -> int:
        return SIDEBAR_TEXT_W


class SoundCloudNowPlaying(BaseNowPlaying):
    """SoundCloud now-playing widget with braille waveform visualization.

    State machine:
        IDLE → CONNECTING → VISUALIZER | FALLBACK (final)
    """

    # Braille lookup: left_level (0-4) * 5 + right_level (0-4)
    DATA = [
        " ",
        "⢀",
        "⢠",
        "⢰",
        "⢸",
        "⡀",
        "⣀",
        "⣠",
        "⣰",
        "⣸",
        "⡄",
        "⣄",
        "⣤",
        "⣴",
        "⣼",
        "⡆",
        "⣆",
        "⣦",
        "⣶",
        "⣾",
        "⡇",
        "⣇",
        "⣧",
        "⣷",
        "⣿",
    ]
    ROW_HEIGHT = 4  # braille rows (16 levels)
    PLAYHEAD_PIN_RATIO = 0.50  # cursor pinned at centre of viewport

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._display_w = SIDEBAR_TEXT_W
        self._state: ScState = ScState.IDLE
        self._title = ""
        self._yt_id = ""
        self._artist = ""
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._liked = False
        self._followed = False
        self._like_count = 0
        self._play_count = 0
        self._buffering = False
        self._stream_ready = True
        self._waveform_samples: list[float] = []
        self._waveform_rendered: list[list[str]] = []

    # ------------------------------------------------------------------
    # Public API called from PlaybackActions
    # ------------------------------------------------------------------

    def start_playback(self, request: MediaRequest) -> None:
        super().start_playback(request)
        self._state = ScState.CONNECTING
        self._title = request.title
        self._yt_id = request.yt_id
        self._artist = request.channel or ""
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._liked = False
        self._followed = False
        self._like_count = 0
        self._play_count = 0
        self._buffering = False
        self._stream_ready = False
        self._waveform_samples = []
        self._waveform_rendered = []
        logger.debug(
            "SoundCloudNowPlaying.start_playback: title=%s yt_id=%s",
            request.title[:30],
            request.yt_id,
        )
        self._refresh()

    def show_visualizer(
        self,
        data: dict,
        resolved: dict,
        rendered: tuple[list[float], list[list[str]]] | None = None,
    ) -> None:
        self._state = ScState.VISUALIZER
        self._artist = data.get("channel", self._artist)
        self._play_count = (
            resolved.get("view_count", data.get("view_count", data.get("views", 0)))
            or 0
        )
        self._like_count = (
            resolved.get(
                "like_count", data.get("like_count", data.get("likes_count", 0))
            )
            or 0
        )
        if rendered is not None:
            self._waveform_samples, self._waveform_rendered = rendered
        else:
            samples = resolved.get("waveform_samples")
            if samples:
                self._prepare_waveform(samples)
        logger.debug(
            "SoundCloudNowPlaying.show_visualizer: has_samples=%s",
            bool(self._waveform_samples),
        )
        self._refresh()

    def show_fallback(self, data: dict, resolved: dict) -> None:
        self._state = ScState.FALLBACK
        self._artist = data.get("channel", self._artist)
        self._play_count = (
            resolved.get("view_count", data.get("view_count", data.get("views", 0)))
            or 0
        )
        self._like_count = (
            resolved.get(
                "like_count", data.get("like_count", data.get("likes_count", 0))
            )
            or 0
        )
        logger.debug("SoundCloudNowPlaying.show_fallback")
        self._refresh()

    def stop_playback(self) -> None:
        logger.debug("SoundCloudNowPlaying.stop_playback")
        self._state = ScState.IDLE
        self._title = ""
        self._yt_id = ""
        self._artist = ""
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._cancel_selected = False
        self._liked = False
        self._followed = False
        self._like_count = 0
        self._play_count = 0
        self._buffering = False
        self._stream_ready = True
        self._waveform_samples = []
        self._waveform_rendered = []
        self.display = False

    def clear(self) -> None:
        self.stop_playback()

    def update_state(self, state: PlaybackState) -> None:
        if self._state == ScState.CONNECTING:
            return
        self._position = (
            state.position if state.position and state.position >= 0 else 0.0
        )
        self._duration = (
            state.duration if state.duration and state.duration > 0 else 1.0
        )
        self._paused = state.paused
        self._buffering = state.buffering
        if state.duration and state.duration > 1:
            self._stream_ready = True
        self._refresh()

    def should_show_spinner(self) -> bool:
        return (
            self._state == ScState.CONNECTING
            or not self._stream_ready
            or self._buffering
        )

    def update_metadata(
        self, liked: bool, followed: bool, like_count: int, play_count: int
    ) -> None:
        logger.debug(
            "SoundCloudNowPlaying.update_metadata: liked=%s followed=%s",
            liked,
            followed,
        )
        self._liked = liked
        self._followed = followed
        self._like_count = like_count
        self._play_count = play_count
        self._refresh()

    def update_artist(self, artist: str) -> None:
        logger.debug("SoundCloudNowPlaying.update_artist: artist=%s", artist[:30])
        self._artist = artist
        self._refresh()

    def on_mount(self) -> None:
        logger.debug("SoundCloudNowPlaying.on_mount")
        self._refresh()

    # ------------------------------------------------------------------
    # Waveform
    # ------------------------------------------------------------------

    def _prepare_waveform(self, samples: list[int]) -> None:
        if not samples:
            return
        self._waveform_samples, self._waveform_rendered = build_waveform(
            samples, self.ROW_HEIGHT, self.DATA
        )

    def _get_visible_waveform(self) -> tuple[list[str], int]:
        if not self._waveform_rendered or not self._waveform_rendered[0]:
            return [], 0
        total = len(self._waveform_rendered[0])
        frac = min(1.0, self._position / self._duration) if self._duration > 0 else 0.0
        playhead_col = frac * total
        if total <= self._display_w:
            visible = ["".join(row) for row in self._waveform_rendered]
            split_col = max(0, min(int(playhead_col), self._display_w))
            return visible, split_col
        pin_offset = self._display_w * self.PLAYHEAD_PIN_RATIO
        start_col = int(playhead_col - pin_offset)
        start_col = max(0, min(start_col, total - self._display_w))
        split_col = int(playhead_col) - start_col
        split_col = max(0, min(split_col, self._display_w))
        visible = []
        for row in self._waveform_rendered:
            visible.append("".join(row[start_col : start_col + self._display_w]))
        return visible, split_col

    # ------------------------------------------------------------------
    # Render
    # ------------------------------------------------------------------

    def _refresh(self) -> None:
        cancel_active, accent, x_st = self._cancel_preamble()

        if not self._title:
            self.update("")
            return

        from nyrx.widgets.base import _short_views

        s = Text()
        cap = "now playing"
        s.append("\u258c", style=accent)
        s.append(" ")
        s.append(cap, style="dim")
        if self._paused:
            s.append(_PAUSE_MARKER, style="bold yellow")
        if cancel_active:
            sidebar_w = SIDEBAR_TEXT_W + 2
            used = 2 + len(cap) + (len(_PAUSE_MARKER) if self._paused else 0)
            pads = sidebar_w - used - 4
            if pads > 0:
                s.append(" " * pads)
            s.append(" [X]", style=x_st)
        s.append("\n")

        s.append("\u258c", style=accent)
        s.append(" ")
        title_w = SIDEBAR_TEXT_W
        if len(self._title) > title_w:
            s.append(self._title[: title_w - 1] + "\u2026")
        else:
            s.append(self._title)
        s.append("\n")

        if self._artist:
            s.append("\u258c", style=accent)
            s.append(" ")
            artist_w = SIDEBAR_TEXT_W
            artist_style = "bold #A277FF" if self._followed else "dim"
            if len(self._artist) > artist_w:
                s.append(self._artist[: artist_w - 1] + "\u2026", style=artist_style)
            else:
                s.append(self._artist, style=artist_style)
            s.append("\n")

        s.append("\u258c", style=accent)
        s.append(" ")
        if self._state == ScState.CONNECTING:
            spinner = self._current_spinner_frame()
            s.append(f"{spinner} connecting...")
            s.append("\n")
        elif self._state == ScState.VISUALIZER and self._waveform_rendered:
            visible, split_col = self._get_visible_waveform()
            for idx, row_text in enumerate(visible):
                if idx > 0:
                    s.append("\n")
                    s.append("\u258c", style=accent)
                    s.append(" ")
                played = row_text[:split_col]
                unplayed = row_text[split_col:]
                if played:
                    s.append(played, style="white")
                if unplayed:
                    s.append(unplayed, style="grey27")
            s.append("\n")
        else:
            frac = (
                min(1.0, self._position / self._duration) if self._duration > 0 else 0.0
            )
            bar_w = SIDEBAR_BAR_W
            filled = int(bar_w * frac)
            bar = "\u2588" * filled + "\u2591" * (bar_w - filled)
            s.append(bar)
            s.append("\n")

        s.append("\u258c", style=accent)
        s.append(" ")
        if self._state == ScState.CONNECTING:
            pass
        else:
            has_prev = False
            if self._like_count:
                heart = "\u2764\ufe0e"
                like_style = "bold #A277FF" if self._liked else "dim"
                s.append(f"{heart} {_short_views(self._like_count)}", style=like_style)
                has_prev = True
            if self._play_count:
                if has_prev:
                    s.append(" \u2022 ", style="dim")
                s.append(f"\u25b6 {_short_views(self._play_count)}", style="dim")
                has_prev = True
            if has_prev:
                s.append(" \u2022 ", style="dim")
            if not self._stream_ready:
                spinner = self._current_spinner_frame()
                s.append(spinner, style="dim")
            elif self._buffering:
                s.append(
                    f"{format_seconds(self._position)} / {format_seconds(self._duration)}",
                    style="dim",
                )
                s.append(" ")
                spinner = self._current_spinner_frame()
                s.append(spinner, style="dim")
            else:
                s.append(
                    f"{format_seconds(self._position)} / {format_seconds(self._duration)}",
                    style="dim",
                )

        self.update(s)


class RadioNowPlaying(BaseNowPlaying):
    can_focus = True

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self._station_name = ""
        self._station_code = ""
        self._icy_title = ""
        self._paused = False
        self._buffering = False
        self._live_idx = 0

    def start_playback(self, request: MediaRequest) -> None:
        super().start_playback(request)
        self._station_name = request.channel or request.title
        self._station_code = getattr(request.payload, "countrycode", "")
        self._icy_title = ""
        self._paused = False
        self._buffering = False
        self._live_idx = 0
        logger.debug(
            "RadioNowPlaying.start_playback: station=%s code=%s",
            self._station_name[:30],
            self._station_code,
        )
        self._refresh()

    def stop_playback(self) -> None:
        logger.debug("RadioNowPlaying.stop_playback")
        self.display = False

    def clear(self) -> None:
        logger.debug("RadioNowPlaying.clear")
        self.display = False

    def set_icy_title(self, title: str) -> None:
        if title != self._icy_title:
            logger.debug("RadioNowPlaying.set_icy_title: title=%s", title[:40])
            self._icy_title = title
            self._refresh()

    def should_show_spinner(self) -> bool:
        return self.display

    def update_state(self, state: PlaybackState) -> None:
        self._paused = state.paused
        self._buffering = state.buffering
        self._refresh()

    def _refresh(self) -> None:
        cancel_active, accent, x_st = self._cancel_preamble()

        s = Text()

        s.append("\u258c", style=accent)
        s.append(" ")
        line1 = self._station_name
        if self._station_code:
            line1 += f" \u2022 {self._station_code}"
        line1 = BaseNowPlaying._trunc(line1, SIDEBAR_TEXT_W)
        s.append(line1, style="bold" if not cancel_active else "")
        if cancel_active:
            pads = SIDEBAR_TEXT_W - len(line1) - 4
            if pads > 0:
                s.append(" " * pads)
            s.append(" [X]", style=x_st)
        s.append("\n")

        if self._icy_title:
            s.append("\u258c", style=accent)
            s.append(" ")
            s.append("Now Playing: ", style="dim")
            title_w = SIDEBAR_TEXT_W - len("Now Playing: ")
            if len(self._icy_title) > title_w:
                s.append(self._icy_title[: title_w - 1] + "\u2026")
            else:
                s.append(self._icy_title)
            s.append("\n")

        s.append("\u258c", style=accent)
        s.append(" ")
        if self._paused and self._buffering:
            spinner = self._current_spinner_frame()
            s.append(spinner, style="dim")
        elif self._paused:
            s.append("\u25a3" + _PAUSE_MARKER, style="bold yellow")
        else:
            self._live_idx += 1
            dot_visible = (self._live_idx // _LIVE_BLINK_HOLDS) % 2 == 0
            s.append(
                ("\u25a3" if dot_visible else " ") + "  [LIVE]", style="bold green"
            )

        self.update(s)


class TVNowPlaying(BaseNowPlaying):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._status = ""
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        self._track: MediaRequest | None = None

    def start_playback(self, request: MediaRequest) -> None:
        super().start_playback(request)
        self._loading = True
        self._status = "probing\u2026"
        self._position = 0.0
        self._duration = 1.0
        self._paused = False
        logger.debug(
            "TVNowPlaying.start_playback: title=%s kind=%s",
            request.title[:30],
            request.kind,
        )
        self._refresh()

    def set_status(self, text: str) -> None:
        self._status = text
        self._refresh()

    def stop_playback(self) -> None:
        self.display = False
        self._loading = False
        self._track = None

    def clear(self) -> None:
        self.display = False

    def update_state(self, state: PlaybackState) -> None:
        self._position = (
            state.position if state.position and state.position >= 0 else 0.0
        )
        self._duration = (
            state.duration if state.duration and state.duration > 0 else 1.0
        )
        self._paused = state.paused
        if self._loading and state.duration and state.duration > 1:
            self._loading = False
        self._refresh()

    def should_show_spinner(self) -> bool:
        return self._loading

    def _refresh(self) -> None:
        cancel_active, accent, x_st = self._cancel_preamble()
        max_w = SIDEBAR_TEXT_W
        s = Text()

        # Header
        s.append("\u258c", style=accent)
        s.append(" now playing", style="dim")
        if cancel_active:
            s.append(" " * (SIDEBAR_TEXT_W + 2 - 13 - 4))
            s.append(" [X]", style=x_st)
        s.append("\n")

        request = self._track
        if request is None or not request.title:
            self.update(s)
            return

        is_episode = request.kind == MediaKind.EPISODE

        # Title / Episode name
        s.append("\u258c", style=accent)
        s.append(" ")
        if is_episode:
            ep_name = request.title
            s.append(BaseNowPlaying._trunc(ep_name, max_w))
        else:
            s.append(BaseNowPlaying._trunc(request.title, max_w), style="bold")
        s.append("\n")

        # Series title (episodes only)
        if is_episode:
            s.append("\u258c", style=accent)
            s.append(" ")
            series = (
                getattr(request.payload, "series_title", "") if request.payload else ""
            )
            s.append(BaseNowPlaying._trunc(series, max_w), style="bold")
            s.append("\n")

        # Metadata: rating (+ year for movies, S01E01 prefix for episodes)
        s.append("\u258c", style=accent)
        s.append(" ")
        if request.payload:
            rating = getattr(request.payload, "rating", 0.0) if request.payload else 0.0
            year = getattr(request.payload, "year", "")
            season = getattr(request.payload, "season_number", None)
            episode = getattr(request.payload, "episode_number", None)
        else:
            rating = 0.0
            year = ""
            season = None
            episode = None
        if is_episode and season is not None and episode is not None:
            meta = f"S{season:02d}E{episode:02d}"
            if rating:
                meta += f" \u2022 \u2605 {rating:.1f}"
        else:
            meta = ""
            if rating:
                meta = f"\u2605 {rating:.1f}"
            if year:
                meta += f" \u00b7 {year}" if meta else year
        s.append(BaseNowPlaying._trunc(meta, max_w), style="dim")
        s.append("\n")

        # Blank spacer
        s.append("\u258c", style=accent)
        s.append("\n")

        # Status / Timestamp
        s.append("\u258c", style=accent)
        s.append(" ")
        if self._loading:
            spinner = self._current_spinner_frame()
            s.append(f"{spinner} {self._status}")
        else:
            s.append(
                f"{format_seconds(self._position)} / {format_seconds(self._duration)}",
                style="dim",
            )
            if self._paused:
                s.append(_PAUSE_MARKER, style="bold yellow")

        self.update(s)
