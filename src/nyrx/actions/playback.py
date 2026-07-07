# SPDX-License-Identifier: AGPL-3.0-only

"""Playback mixin: mpv lifecycle, queue management, browser/thumbnail actions."""

from __future__ import annotations

import logging
import shutil
import subprocess
import time
from typing import TYPE_CHECKING, Any, cast

from rich.text import Text
from textual import work
from textual.widgets import DataTable

from nyrx.config import (
    SEVERITY_ERROR,
    TIMEOUT_CONFIRM,
    TIMEOUT_ERROR,
    YT_QUALITY_PRESETS,
)
from nyrx.helpers import require_key
from nyrx.models import MediaRequest, PlaybackState
from nyrx.player import play_video_async
from nyrx.queues import QueueItem
from nyrx.screens import QueueModal, ThumbnailModal
from nyrx.sources.tv_movies import TVMoviesSource
from nyrx.watch_db import sync_from_tracker
from nyrx.widgets import (
    SIDEBAR_TEXT_W,
    BaseNowPlaying,
    FeedTrackItem,
    RadioNowPlaying,
    ResultItem,
    SCHomeView,
    TVChip,
    TVNowPlaying,
)
from nyrx.widgets.sidebar import ScState, SoundCloudNowPlaying

logger = logging.getLogger(__name__)

_QUEUE_NEXT_PREFIX = "\u258c \u2191 Next: "

if TYPE_CHECKING:
    from textual.timer import Timer

    from nyrx.player import MPVIPC
    from nyrx.protocols import MediaAppProtocol
    from nyrx.sources import Source as SourceABC


class PlaybackActions:
    _poll_timer: Timer | None
    _now_playing_data: dict | None
    _play_spinner_timer: Timer | None
    _last_known_pos: float
    _last_playing_data: dict | None
    _last_playback_pos: float

    @property
    def _np_focused(self: MediaAppProtocol) -> bool:
        try:
            for w in self._np_widgets.values():
                if w.display and self.focused is w:
                    return True
            dl = self._w_download
            return dl is not None and dl.display and self.focused is dl
        except (KeyError, AttributeError):
            logger.debug("_np_focused: query failed")
            return False

    @property
    def _sc_np_focused(self: MediaAppProtocol) -> bool:
        try:
            w = self._np_widgets.get("soundcloud")
            return w is not None and w.display and self.focused is w
        except (KeyError, AttributeError):
            logger.debug("_sc_np_focused: query failed")
            return False

    def _active_np_widget(self: MediaAppProtocol) -> BaseNowPlaying | None:
        """Return the now-playing widget for the source that's currently playing."""
        src = (self._now_playing_data or {}).get("source", "")
        return self._np_widgets.get(src, self._np_widgets.get("youtube"))

    def _displayed_np_widget(self: MediaAppProtocol) -> BaseNowPlaying | None:
        """Return the displayed now-playing widget, or None."""
        for w in self._np_widgets.values():
            if w.display:
                return w
        return None

    def _update_queue_indicator(self: MediaAppProtocol) -> None:
        q = self._w_sidebar_queue
        if q is None:
            logger.debug("_update_queue_indicator: _w_sidebar_queue is None")
            return
        lines: list[Text] = []
        if not self._online:
            lines.append(
                Text("\u258c ! No internet connection. retrying...", style="red")
            )
        elif self._show_back_online:
            lines.append(Text("\u258c \u2713 Back online!", style="green"))
        if self._playback_queue:
            first = self._playback_queue.peek()
            if first:
                lines.append(
                    Text(
                        f"{_QUEUE_NEXT_PREFIX}{BaseNowPlaying._trunc(first.title, SIDEBAR_TEXT_W - len(_QUEUE_NEXT_PREFIX))}"
                    )
                )
        if self._download_pending:
            lines.append(
                Text(f"\u258c \u2193 +{len(self._download_pending)} download queued")
            )
        if lines:
            q.update(Text("\n").join(lines))
            q.display = True
        else:
            q.update("")
            q.display = False

    def action_play(self: MediaAppProtocol) -> None:
        if self._np_focused or self._stopping:
            return
        focused = self.focused
        if isinstance(focused, TVChip):
            data = self._get_focused_track()
            if data:
                if data.get("source") == "tv_movies" and data.get("media_type") == "tv":
                    tmdb_id = data.get("tmdb_id")
                    if tmdb_id is not None:
                        self.action_view_tv_series(tmdb_id)
                else:
                    self._play(
                        MediaRequest.from_dict(data, audio_only=self._audio_only)
                    )
            return
        item = self._current_item()
        if item:
            self._play(MediaRequest.from_dict(item.data, audio_only=self._audio_only))

    def _get_quality_format(self: MediaAppProtocol) -> str | None:
        for label, _height, fmt in YT_QUALITY_PRESETS:
            if label == self._quality:
                return fmt
        return None

    def _get_quality_height(self: MediaAppProtocol) -> int | None:
        for label, height, _ in YT_QUALITY_PRESETS:
            if label == self._quality:
                return height
        return None

    @property
    def _is_playing(self: MediaAppProtocol) -> bool:
        return (
            (self._mpv_ipc is not None and self._mpv_ipc.is_running())
            or self._sc_resolving
            or self._tv_probing
        )

    def _update_sc_home_sidebar_class(self: MediaAppProtocol) -> None:
        try:
            self.query_one("#sc-home", SCHomeView)._update_sidebar_class()
        except Exception:
            logger.debug("_update_sc_home_sidebar_class: sc-home not found")

    def _play_next_queued(self: MediaAppProtocol) -> None:
        """Consume next item from the playback queue and start playing it.

        No-op if the queue is empty (sets ``_stopping`` flag so the
        ``_clear_stopping`` timer can clean up).  Safe to call from
        timer callbacks (``set_timer(0, self._play_next_queued)``).
        """
        next_item = self._playback_queue.next()
        if next_item:
            logger.debug(
                "_play_next_queued: request.source=%s request.audio_only=%s data_keys=%s data.get(source)=%r",
                next_item.request.source,
                next_item.request.audio_only,
                list(next_item.request.data.keys())
                if next_item.request.data
                else "EMPTY",
                next_item.request.data.get("source", "MISSING")
                if next_item.request.data
                else "NO_DATA",
            )
            self._stopping = False
            self._play(next_item.request, from_queue=True)
        else:
            self._stopping = True
            self.set_timer(1.5, self._clear_stopping)

    def _start_playback(
        self: MediaAppProtocol, data: dict, request: MediaRequest
    ) -> None:
        """Shared state reset before source-specific launch.

        Resets 8 state fields, bumps ``_play_token``, sets up the now-playing
        widget with the typed ``request``, and starts the spinner.
        """
        self._play_token += 1
        self._now_playing_data = data
        self._update_sidebar_content()
        self._refresh_queue_modal()
        self._last_playback_pos = 0
        self._stalled_count = 0
        self._stream_ready = False
        self._last_known_pos = -1
        self._play_start_time = time.monotonic()
        self._stopping = False

        np_side = self._active_np_widget()
        if np_side is not None:
            np_side.start_playback(request)
        else:
            logger.debug("_start_playback: np_side is None")
        self._np_side = np_side
        if self._play_spinner_timer:
            self._play_spinner_timer.stop()
        self._play_spinner_timer = self.set_interval(0.08, self._tick_play_spinner)

    def _finish_playback(self: MediaAppProtocol, ipc: MPVIPC | None) -> None:
        """Shared post-mpv setup.

        Sets ``_mpv_ipc``, syncs the widget, clears ``_stopping``, stops the
        spinner, and starts the mpv-poll timer.  Does NOT restart the spinner:
        that decision lives in per-source wrappers.
        """
        self._mpv_ipc = ipc
        self._sync_np_widget()
        self._stopping = False
        np_side = self._active_np_widget()
        if self._np_side is not None and self._np_side is not np_side:
            self._np_side.stop_playback()
        self._np_side = np_side
        self._update_sc_home_sidebar_class()
        self._apply_sidebar(self.screen.has_class("wide"))
        if self._play_spinner_timer:
            self._play_spinner_timer.stop()
            self._play_spinner_timer = None
        if self._poll_timer is None:
            self._poll_timer = self.set_interval(0.04, self._poll_mpv)

    def _sync_finish(self: MediaAppProtocol, ipc: MPVIPC | None) -> None:
        """Sync-source finish wrapper: YT, radio, and SC callbacks.

        Calls the shared ``_finish_playback()`` then restarts the spinner
        (sync sources show a buffering indicator via the spinner).
        """
        self._finish_playback(ipc)
        self._play_spinner_timer = self.set_interval(0.08, self._tick_play_spinner)

    def _tv_movies_finish(
        self: MediaAppProtocol, ipc: MPVIPC | None, subs_tmpdir: str | None, token: int
    ) -> None:
        """tv_movies finish wrapper: token check, cleanup, spinner until stream ready.

        Keeps the spinner running after mpv launch; ``TVNowPlaying.update_state``
        will transition ``_loading = False`` when mpv reports ``duration > 1``,
        replacing the spinner with the real timestamp.
        """
        if token != self._play_token:
            if ipc:
                ipc.stop()
            if subs_tmpdir:
                shutil.rmtree(subs_tmpdir, ignore_errors=True)
            self._tv_probing = False
            return
        self._subs_tmpdir = subs_tmpdir
        self._tv_probing = False
        self._sync_finish(ipc)

    def _play_raw(
        self: MediaAppProtocol, data: dict, start_pos: float | None = None
    ) -> None:
        """Dispatch playback to the correct source handler based on source key."""
        self._resolve_token += 1
        self._sc_resolving = False
        self._last_icy_title = ""

        src = data.get("source", "")
        logger.debug(
            "_play_raw: data_keys=%s data.get(source)=%r url=%s.. self._source=%s src_after_read=%r",
            list(data.keys()) if data else "EMPTY",
            data.get("source", "MISSING") if data else "NO_DATA",
            (data.get("url", "") or "")[:60] if data else "NO_DATA",
            self._source,
            src,
        )
        if not src:
            url = data.get("url", "")
            if "soundcloud.com" in url:
                src = "soundcloud"
            elif "youtube.com" in url or "youtu.be" in url:
                src = "youtube"
            else:
                src = self._source

        if src == "tv_movies":
            self._play_tv_movies(data, start_pos)
            return

        source = self._sources[src]
        np_side = self._active_np_widget()

        if src == "soundcloud":
            if self._np_side is not None and self._np_side is not np_side:
                self._np_side.stop_playback()
            self._np_side = np_side

            request = MediaRequest.from_dict(data, source="soundcloud", audio_only=True)
            self._start_playback(data, request)

            token = self._resolve_token
            self._sc_resolving = True
            if self._play_spinner_timer:
                self._play_spinner_timer.stop()
            self._play_spinner_timer = self.set_interval(0.08, self._tick_play_spinner)
            self._resolve_then_play(source, data, token)
            return

        # YouTube or Radio: synchronous play
        audio_only = src == "radio" or data.get("audio_only", self._audio_only)
        ytdl_fmt = self._get_quality_format() if src != "radio" else None

        request = MediaRequest.from_dict(data, source=src, audio_only=audio_only)
        self._start_playback(data, request)

        ipc = source.play(
            data, audio_only=audio_only, ytdl_format=ytdl_fmt, start_pos=start_pos
        )
        if ipc:
            self._sync_finish(ipc)
        else:
            self._now_playing_data = None
            self._update_sidebar_content()
            self._update_sidebar_context()
            self.notify(
                "Playback failed to start",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            self.call_later(self._play_next_queued)

    # ------------------------------------------------------------------
    # SC resolve pipeline
    # ------------------------------------------------------------------

    @work(thread=True, exclusive=True, group="sc-resolve")
    def _resolve_then_play(
        self: MediaAppProtocol, source: SourceABC, data: dict, token: int
    ) -> None:
        """Resolve SC track metadata in a thread, then launch mpv."""
        try:
            ipc, resolved = source.play(data, audio_only=True)
        except Exception:
            logger.exception(
                "_resolve_then_play: resolve failed for %s",
                data.get("yt_id", data.get("url", "?")),
            )
            self.call_from_thread(self._on_sc_resolved, data, None, {}, None, token)
            return
        rendered = None
        if resolved.get("waveform_samples"):
            try:
                from nyrx.helpers import build_waveform

                rendered = build_waveform(
                    resolved["waveform_samples"],
                    SoundCloudNowPlaying.ROW_HEIGHT,
                    SoundCloudNowPlaying.DATA,
                )
            except Exception:
                logger.warning(
                    "_resolve_then_play: waveform render failed, falling back"
                )
                rendered = None
        self.call_from_thread(
            self._on_sc_resolved, data, ipc, resolved, rendered, token
        )

    def _on_sc_resolved(
        self: MediaAppProtocol,
        data: dict,
        ipc: MPVIPC | None,
        resolved: dict,
        rendered: tuple[list[float], list[list[str]]] | None,
        token: int,
    ) -> None:
        """Callback: check staleness, show visualizer/fallback, launch mpv."""
        if token != self._resolve_token:
            if ipc:
                ipc.stop()
            return

        np_side = self._active_np_widget()
        if not isinstance(np_side, SoundCloudNowPlaying):
            if ipc:
                ipc.stop()
            return
        if np_side._state != ScState.CONNECTING:
            if ipc:
                ipc.stop()
            return

        if ipc:
            if resolved.get("waveform_samples"):
                np_side.show_visualizer(data, resolved, rendered)
            else:
                np_side.show_fallback(data, resolved)
            self._sc_resolving = False
            self._sync_finish(ipc)
            self._sync_sc_np_metadata()
        else:
            self._sc_resolving = False
            np_side.stop_playback()
            self._now_playing_data = None
            self._update_sidebar_content()
            self._update_sidebar_context()
            self.notify(
                "Playback failed to start",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            logger.debug("_on_sc_resolved: SC play failed, advancing queue")
            self._play_next_queued()

    # ------------------------------------------------------------------
    # TV / Movies: async worker (probe can take 5-30s)
    # ------------------------------------------------------------------

    def _play_tv_movies(
        self: MediaAppProtocol, data: dict, start_pos: float | None = None
    ) -> None:
        request = MediaRequest.from_dict(data, source="tv_movies")
        data.setdefault("yt_id", request.yt_id)
        self._start_playback(data, request)
        self._tv_probing = True
        self._sync_np_widget()
        self._render_focus_indicators()
        token = self._play_token
        self._play_tv_worker(data, start_pos, token)

    @work(thread=True, exclusive=True, group="tv-play")
    def _play_tv_worker(
        self: MediaAppProtocol, data: dict, start_pos: float | None, token: int
    ) -> None:
        subs_tmpdir = None
        try:
            if self._np_side is not None:
                self.call_from_thread(
                    cast(TVNowPlaying, self._np_side).set_status, "probing\u2026"
                )

            params = cast(Any, self._sources["tv_movies"]).play_params(
                data,
                audio_only=False,
                start_pos=start_pos,
                quality_height=self._get_quality_height(),
            )
            subs_tmpdir = params.pop("_subs_tmpdir", None)

            if params.pop("_no_configs", False):
                self.call_from_thread(
                    self._play_tv_failed,
                    "No server configs. Set a manifest URL to enable TV/Movies.",
                    subs_tmpdir,
                    token,
                )
                return

            if not params.get("url"):
                self.call_from_thread(
                    self._play_tv_failed,
                    "No stream URL returned",
                    subs_tmpdir,
                    token,
                )
                return

            if self._np_side is not None:
                self.call_from_thread(
                    cast(TVNowPlaying, self._np_side).set_status,
                    "starting stream\u2026",
                )

            params["source"] = "tv_movies"
            params["channel"] = data.get("channel", "")
            ipc = play_video_async(**params)
            self.call_from_thread(
                self._play_tv_ready,
                data,
                ipc,
                subs_tmpdir,
                token,
            )
        except Exception as e:
            self.call_from_thread(
                self._play_tv_failed,
                str(e),
                subs_tmpdir,
                token,
            )

    def _play_tv_ready(
        self: MediaAppProtocol,
        data: dict,
        ipc: MPVIPC | None,
        subs_tmpdir: str | None,
        token: int,
    ) -> None:
        if ipc:
            self._tv_movies_finish(ipc, subs_tmpdir, token)
        else:
            self._play_tv_failed("mpv failed to start", subs_tmpdir, token)

    def _play_tv_failed(
        self: MediaAppProtocol,
        error_msg: str,
        subs_tmpdir: str | None = None,
        token: int | None = None,
    ) -> None:
        if token is not None and token != self._play_token:
            if subs_tmpdir:
                shutil.rmtree(subs_tmpdir, ignore_errors=True)
            self._tv_probing = False
            return
        self._tv_probing = False
        self._now_playing_data = None
        self._subs_tmpdir = None
        if subs_tmpdir:
            shutil.rmtree(subs_tmpdir, ignore_errors=True)
        if self._np_side is not None:
            self._np_side.stop_playback()
            self._np_side = None
        if self._play_spinner_timer:
            self._play_spinner_timer.stop()
            self._play_spinner_timer = None
        self._update_sidebar_content()
        self._update_sidebar_context()
        self._sync_np_widget()
        self.notify(
            f"Playback failed: {error_msg}",
            severity=SEVERITY_ERROR,
            timeout=TIMEOUT_ERROR,
            title="Error",
        )
        self._play_next_queued()

    def _play(
        self: MediaAppProtocol, request: MediaRequest, from_queue: bool = False
    ) -> None:
        if self._stopping:
            return
        if not from_queue and request.source not in ("soundcloud", "radio"):
            request.audio_only = self._audio_only
            if request.source == "tv_movies":
                request.audio_only = False
        item = QueueItem(request=request)
        if self._is_playing or not self._online:
            request.data = request.data or {}
            request.data["audio_only"] = request.audio_only
            if request.source == "tv_movies":
                request.data["_queued_server_mode"] = cast(
                    TVMoviesSource, self._sources["tv_movies"]
                ).server_mode
            self._playback_queue.add(item)
            self._sync_np_widget()
            self._refresh_queue_modal()
            tag = " (offline)" if not self._online else ""
            self.notify(f"Queued{tag}: {request.title}", timeout=TIMEOUT_CONFIRM)
        else:
            raw_data = dict(request.data or {})
            raw_data.setdefault("source", request.source)
            logger.debug(
                "_play: from_queue=%s request.source=%s data_keys=%s data.get(source)=%r _is_playing=%s _source=%s",
                from_queue,
                request.source,
                list(raw_data.keys()) if raw_data else "EMPTY",
                raw_data.get("source", "MISSING"),
                self._is_playing,
                self._source,
            )
            self._play_raw(raw_data, start_pos=request.start_pos)

    def _cleanup_mpv(self: MediaAppProtocol) -> None:
        """Tear down mpv IPC, invalidate in-flight operations, and reset playback state."""
        self._play_token += 1  # invalidate any in-flight probe
        self._resolve_token += 1  # invalidate any in-flight SC resolve
        self._sc_resolving = False
        self._tv_probing = False
        self._last_icy_title = ""
        self._now_playing_data = None
        if self._subs_tmpdir:
            shutil.rmtree(self._subs_tmpdir, ignore_errors=True)
            self._subs_tmpdir = None
        self._update_sidebar_content()
        self._update_radio_playing_indicator()
        self._refresh_queue_modal()
        self._update_sc_home_sidebar_class()
        self._stalled_count = 0
        if self._mpv_ipc:
            self._mpv_ipc.stop()
            self._mpv_ipc = None
        if self._play_spinner_timer:
            self._play_spinner_timer.stop()
            self._play_spinner_timer = None
        if self._np_side:
            self._np_side.stop_playback()
            self._np_side = None
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None
        self._sync_np_widget()

    def _stop_playback(self: MediaAppProtocol) -> None:
        """Stop current playback and set a cooldown before next action."""
        self._last_playing_data = None
        self._last_playback_pos = 0
        self._stopping = True
        self._cleanup_mpv()
        self.set_timer(1.5, self._clear_stopping)

    def _skip_playback(self: MediaAppProtocol) -> None:
        """Skip current track and advance to next queued item."""
        self._last_playing_data = None
        self._last_playback_pos = 0
        self._stopping = True
        self._cleanup_mpv()
        if self._queue_frozen:
            self.set_timer(1.5, self._clear_stopping)
            return
        self._play_next_queued()
        if not self._syncing:
            self._syncing = True
            try:
                sync_from_tracker()
            finally:
                self._syncing = False
        self._refresh_watchlist_statuses()

    def _clear_stopping(self: MediaAppProtocol) -> None:
        self._stopping = False

    @property
    def _cancel_targets(self: MediaAppProtocol) -> list[str]:
        targets: list[str] = []
        if (
            self._last_playing_data
            or (self._mpv_ipc and self._mpv_ipc.is_running())
            or self._sc_resolving
            or self._tv_probing
        ):
            targets.append("playback")
        if self._download_state and self._download_state.get("status") == "downloading":
            targets.append("download")
        return targets

    def _enter_cancel_mode(self: MediaAppProtocol) -> None:
        self._cancel_active = True
        self._cancel_target_idx = 0
        self._sync_cancel_highlights()
        self._update_mode_indicator()

    def _exit_cancel_mode(self: MediaAppProtocol) -> None:
        self._cancel_active = False
        self._cancel_target_idx = 0
        np_widget = self._active_np_widget()
        if np_widget:
            np_widget.set_cancel_highlight(False)
        if self._w_download:
            self._w_download.set_cancel_highlight(False)
        self._update_mode_indicator()

    def _cycle_cancel_target(self: MediaAppProtocol, direction: int) -> None:
        targets = self._cancel_targets
        if len(targets) <= 1:
            self._exit_cancel_mode()
            return
        self._cancel_target_idx = (self._cancel_target_idx + direction) % len(targets)
        self._sync_cancel_highlights()

    def _sync_cancel_highlights(self: MediaAppProtocol) -> None:
        targets = self._cancel_targets
        np_widget = self._active_np_widget()
        if 0 <= self._cancel_target_idx < len(targets):
            target = targets[self._cancel_target_idx]
            if np_widget:
                np_widget.set_cancel_highlight(target == "playback")
            if self._w_download:
                self._w_download.set_cancel_highlight(target == "download")
        else:
            if np_widget:
                np_widget.set_cancel_highlight(False)
            if self._w_download:
                self._w_download.set_cancel_highlight(False)

    def _confirm_cancel(self: MediaAppProtocol) -> None:
        targets = self._cancel_targets
        if 0 <= self._cancel_target_idx < len(targets):
            target = targets[self._cancel_target_idx]
            if target == "playback":
                self._skip_playback()
            elif target == "download":
                self._cancel_download()
        self._exit_cancel_mode()

    def _refresh_watchlist_statuses(self: MediaAppProtocol) -> None:
        if self._in_watchlist and self._w_watchlist_screen:
            try:
                self._w_watchlist_screen.refresh_statuses()
            except Exception:
                logger.debug("_refresh_watchlist_statuses: failed")
        if self._w_tv_series:
            try:
                self._w_tv_series.refresh_episode_statuses()
            except Exception:
                logger.debug("_refresh_watchlist_statuses: tv_series update failed")

    def _sync_np_widget(self: MediaAppProtocol) -> None:
        try:
            dl = self._w_download
            if dl is not None:
                dl.update_progress(self._download_state)
                if not dl._dl_select_mode:
                    dl.display = bool(self._download_state)
            self._update_queue_indicator()
            self._update_mode_indicator()
            self._apply_sidebar(self.screen.has_class("wide"))
        except Exception:
            logger.debug("_sync_np_widget: sync failed")

    def _refresh_queue_modal(self: MediaAppProtocol) -> None:
        try:
            if isinstance(self.screen, QueueModal):
                self.screen._rebuild_lists()
        except Exception:
            logger.debug("_refresh_queue_modal: rebuild failed")

    def _poll_mpv(self: MediaAppProtocol) -> None:
        """Poll mpv IPC for playback state; advance queue on completion."""
        if not self._mpv_ipc:
            return
        state = self._mpv_ipc.get_state()
        running = state["running"]
        pos = state["time_pos"]
        dur = state["duration"]
        paused = state["paused"]
        paused_for_cache = state["paused_for_cache"]
        if not running:
            if self._queue_frozen:
                self._last_playing_data = None
                self._last_playback_pos = 0
                if self._mpv_ipc:
                    self._mpv_ipc.stop()
                    self._mpv_ipc = None
                if self._poll_timer is not None:
                    self._poll_timer.stop()
                    self._poll_timer = None
                if self._np_side:
                    self._np_side._offline_mode = False
                    self._np_side.display = False
                    self._np_side._refresh()
                return
            self._cleanup_mpv()
            if self._queue_frozen:
                return
            self._play_next_queued()
            if not self._syncing:
                self._syncing = True
                try:
                    sync_from_tracker()
                finally:
                    self._syncing = False
            self._refresh_watchlist_statuses()
            return
        if self._queue_frozen:
            is_user_paused = bool(paused) and not bool(paused_for_cache)
            if is_user_paused:
                self._stalled_count = 0
                if pos is not None:
                    self._last_known_pos = pos
            elif (
                self._stream_ready
                and self._last_known_pos >= 0
                and (time.monotonic() - self._play_start_time > 2.0)
            ):
                if pos is not None and abs(pos - self._last_known_pos) < 0.01:
                    self._stalled_count += 1
                else:
                    self._stalled_count = 0
                if pos is not None:
                    self._last_known_pos = pos
                if self._stalled_count >= 8:
                    dur = dur or 0
                    pos = self._last_known_pos or 0
                    is_finished = dur > 0 and pos >= dur * 0.98

                    if is_finished:
                        self._last_playing_data = None
                        self._last_playback_pos = 0
                    else:
                        self._last_playing_data = self._now_playing_data
                        self._last_playback_pos = self._last_known_pos

                    if self._np_side:
                        if is_finished:
                            self._np_side._offline_mode = False
                            self._np_side.display = False
                        else:
                            self._np_side._offline_mode = True
                        self._np_side._refresh()
                    if self._mpv_ipc:
                        self._mpv_ipc.stop()
                        self._mpv_ipc = None
                    if self._poll_timer is not None:
                        self._poll_timer.stop()
                        self._poll_timer = None
                    if self._play_spinner_timer:
                        self._play_spinner_timer.stop()
                        self._play_spinner_timer = None
                    return
            if self._np_side:
                self._np_side.update_state(
                    PlaybackState(
                        position=pos or 0.0,
                        duration=dur or 0.0,
                        paused=bool(paused),
                        buffering=bool(paused_for_cache),
                    )
                )
            return
        if not self._stream_ready:
            if dur and dur > 1:
                self._stream_ready = True
        if pos is not None and pos >= 0:
            self._last_known_pos = pos
        if self._np_side:
            self._np_side.update_state(
                PlaybackState(
                    position=pos or 0.0,
                    duration=dur or 0.0,
                    paused=bool(paused),
                    buffering=bool(paused_for_cache),
                )
            )

        if (self._now_playing_data or {}).get("source") == "radio":
            if self._mpv_ipc:
                meta = state.get("metadata")
                if meta and isinstance(meta, dict):
                    icy = meta.get("icy-title", "")
                    if icy and icy != self._last_icy_title:
                        self._last_icy_title = icy
                        if isinstance(self._np_side, RadioNowPlaying):
                            self._np_side.set_icy_title(icy)

    def action_open_browser(self: MediaAppProtocol) -> None:
        if self._np_focused:
            return
        data = self._get_focused_track()
        if data:
            self._open_browser_worker(data)

    @work(thread=True, group="open-browser")
    def _open_browser_worker(self: MediaAppProtocol, data: dict) -> None:
        url = data.get("url", "")
        if not url:
            yt_id = data.get("yt_id", "")
            if data.get("source") == "soundcloud":
                url = f"https://soundcloud.com/tracks/{yt_id}" if yt_id else ""
            elif data.get("source") == "tv_movies":
                tmdb_id = data.get("tmdb_id")
                media_type = data.get("media_type", "movie")
                if tmdb_id:
                    url = f"https://www.themoviedb.org/{media_type}/{tmdb_id}"
            else:
                url = f"https://www.youtube.com/watch?v={yt_id}" if yt_id else ""
        if url:
            try:
                import sys

                cmd = ["xdg-open", url] if sys.platform != "darwin" else ["open", url]
                subprocess.run(cmd, stderr=subprocess.DEVNULL)
            except Exception:
                logger.debug("_open_browser_worker: failed to open %s", url)

    def action_open_queue(self: MediaAppProtocol) -> None:
        if self.screen.__class__.__name__ == "QueueModal":
            self.screen.dismiss(None)
            return
        _t0 = time.perf_counter()
        self.push_screen(QueueModal(self), lambda _: self._sync_np_widget())
        logger.debug(
            "QueueModal: push+mount=%.1fms", (time.perf_counter() - _t0) * 1000
        )

    def action_view_thumbnail(self: MediaAppProtocol) -> None:
        if self._np_focused or self._in_watchlist:
            return
        focused = self.focused
        if isinstance(focused, DataTable) and focused.id == "tvs-episodes":
            if tv := self._w_tv_series:
                if focused.cursor_coordinate is not None and focused.row_count > 0:
                    cell_key = focused.coordinate_to_cell_key(focused.cursor_coordinate)
                    key = require_key(cell_key.row_key.value)
                    ep = tv._episode_data_map.get(key)
                    if ep:
                        ep_data = {
                            "title": ep.get("name", ""),
                            "series_title": tv._series_data.get("title", ""),
                            "season_number": tv._current_season,
                            "episode_number": ep.get("episode_number"),
                            "still_path": ep.get("still_path", ""),
                            "overview": ep.get("overview", ""),
                            "runtime": ep.get("runtime"),
                            "air_date": ep.get("air_date", ""),
                            "vote_average": ep.get("vote_average", 0),
                            "tmdb_id": tv._tmdb_id,
                            "source": "tv_movies",
                            "media_type": "episode",
                        }
                        from nyrx.screens.episode_thumbnail import EpisodeThumbnailModal

                        self.push_screen(
                            EpisodeThumbnailModal(ep_data), self._on_thumb_result
                        )
                        return
        data: dict[str, Any] | None = self._get_focused_track()
        if data:
            _t0 = time.perf_counter()
            if data.get("source") == "tv_movies":
                from nyrx.screens.tv_thumbnail import TVThumbnailModal

                self.push_screen(TVThumbnailModal(data), self._on_thumb_result)
                logger.debug(
                    "TVThumbnailModal: push+mount=%.1fms",
                    (time.perf_counter() - _t0) * 1000,
                )
            else:
                self.push_screen(ThumbnailModal(data), self._on_thumb_result)
                logger.debug(
                    "ThumbnailModal: push+mount=%.1fms",
                    (time.perf_counter() - _t0) * 1000,
                )

    def _on_thumb_result(self: MediaAppProtocol, result: dict | None) -> None:
        if not result or not isinstance(result, dict):
            return
        data = result.get("data") or self._get_focused_track()
        if not data:
            return
        if result.get("action") == "play":
            self._play(MediaRequest.from_dict(data))
        elif result.get("action") == "download":
            self.action_download(data=data)

    def _get_focused_track(self: MediaAppProtocol) -> dict | None:
        """Return the data dict for the currently focused track across all focus contexts."""
        if self._np_focused:
            return self._now_playing_data or None
        data = None
        focused = self.focused
        if isinstance(focused, TVChip):
            data = dict(focused.data)
            if "yt_id" not in data:
                data["yt_id"] = f"tmdb_{data.get('tmdb_id', '')}"
            if "source" not in data:
                data["source"] = "tv_movies"
            if "media_type" not in data:
                data["media_type"] = "tv"
            poster = data.get("poster", "")
            if poster and "thumbnail_url" not in data:
                data["thumbnail_url"] = f"https://image.tmdb.org/t/p/w342{poster}"
            return data
        if self._in_watchlist and (wl := self._w_watchlist_screen):
            data = wl.focused_bookmark()
            if data:
                data.setdefault("source", "tv_movies")
                data.setdefault("yt_id", f"tmdb_{data.get('tmdb_id', '')}")
                return data
        if self._in_artist_profile:
            if ap := self._w_artist_profile:
                dt = ap.query_one("#ap-track-list", DataTable)
                if (
                    self.focused is dt
                    and dt.cursor_coordinate is not None
                    and dt.row_count > 0
                ):
                    cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
                    data = ap._track_data_map.get(require_key(cell_key.row_key.value))
            else:
                logger.debug("_get_focused_track: _w_artist_profile is None")
        elif self._in_liked:
            if ls := self._w_liked_screen:
                data = ls.focused_track()
            else:
                logger.debug("_get_focused_track: _w_liked_screen is None")
        elif self._in_following:
            ll = getattr(self, "_w_fs_left_list", None)
            if ll is not None and getattr(ll, "has_focus", False):
                data = None
            elif (cl := self._w_fs_center_list) is not None:
                item = cl.highlighted_child
                if isinstance(item, FeedTrackItem):
                    data = item.data
            else:
                logger.debug("_get_focused_track: _w_fs_center_list is None")
        if not data:
            item = self._current_item()
            if item:
                data = item.data
        if data:
            if "yt_id" not in data and "track_id" in data:
                data = {**data, "yt_id": data["track_id"]}
            if "views" not in data and "view_count" in data:
                data = {**data, "views": data["view_count"]}
            if "likes_count" not in data and "like_count" in data:
                data = {**data, "likes_count": data["like_count"]}
        return data

    def _current_item(self: MediaAppProtocol) -> ResultItem | None:
        """Return the currently highlighted ResultItem from the results ListView, or None."""
        lv = self._w_results_list
        if lv is None:
            return None
        if lv.index is not None:
            children = list(lv.children)
            if 0 <= lv.index < len(children):
                item = children[lv.index]
                if isinstance(item, ResultItem):
                    return item
        return None

    def _tick_play_spinner(self: MediaAppProtocol) -> None:
        if self._np_side and self._np_side.should_show_spinner():
            self._np_side._refresh()
