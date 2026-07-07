# SPDX-License-Identifier: AGPL-3.0-only

"""Connectivity monitoring handlers: periodic checks, offline/online transitions."""

from __future__ import annotations

import logging
import socket
import time
from typing import TYPE_CHECKING

from textual import work

from nyrx.models import MediaRequest

if TYPE_CHECKING:
    from textual.timer import Timer

    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


def compute_connectivity_transition(was_online: bool, now_online: bool) -> str | None:
    """Determine the connectivity state transition, if any.

    Returns ``"went_offline"``, ``"came_back_online"``, or ``None`` for
    no transition.
    """
    if was_online and not now_online:
        return "went_offline"
    if not was_online and now_online:
        return "came_back_online"
    return None


class ConnectivityHandlers:
    _offline_since: float | None
    _back_online_timer: Timer | None
    _pending_dl_data: dict | None
    _download_state: dict | None
    _current_dl_params: dict | None
    _clear_dl_timer: Timer | None
    _play_spinner_timer: Timer | None
    _dl_spinner_timer: Timer | None
    _last_failed_query: str | None
    _last_playing_data: dict | None
    _last_playback_pos: float
    _consecutive_failures: int

    @staticmethod
    def _check_connectivity() -> bool:
        """Check internet reachability by connecting to well-known DNS servers."""
        for host, port in [("1.1.1.1", 53), ("8.8.8.8", 53)]:
            try:
                socket.create_connection((host, port), timeout=2).close()
                return True
            except OSError:
                continue
        return False

    @work(thread=True)
    def _connectivity_worker(self: MediaAppProtocol) -> None:
        """Background thread that checks connectivity and reports the result."""
        now_online = self._check_connectivity()
        logger.debug("_connectivity_worker: online=%s", now_online)
        self.call_from_thread(self._handle_connectivity_result, now_online)

    def _tick_connectivity(self: MediaAppProtocol) -> None:
        """Periodic connectivity check invoked by the interval timer."""
        self._connectivity_worker()

    def _handle_connectivity_result(self: MediaAppProtocol, now_online: bool) -> None:
        """React to connectivity state transitions (online/offline)."""
        was_online = self._online
        if now_online:
            self._consecutive_failures = 0
            if not was_online:
                self._online = True
                logger.debug("_handle_connectivity_result: came back online")
                self._on_came_back_online()
            else:
                logger.debug("_handle_connectivity_result: still online")
        else:
            self._consecutive_failures += 1
            logger.debug(
                "_handle_connectivity_result: check failed (%d consecutive)",
                self._consecutive_failures,
            )
            if was_online and self._consecutive_failures >= 3:
                self._online = False
                logger.debug(
                    "_handle_connectivity_result: went offline after %d failures",
                    self._consecutive_failures,
                )
                self._on_went_offline()
            elif not was_online:
                logger.debug("_handle_connectivity_result: still offline")

    def _on_went_offline(self: MediaAppProtocol) -> None:
        """Handle connectivity loss: pause downloads, stop playback spinner, update UI."""
        logger.warning("Connectivity lost, pausing downloads and freezing queue")
        self._offline_since = time.monotonic()
        self._download_running_flag.clear()
        self._download_paused_for_offline = True
        self._queue_frozen = True
        if self._download_state and self._download_state.get("status") == "downloading":
            self._download_cancel_flag.set()
            if self._current_dl_params:
                if not any(
                    f["yt_id"] == self._current_dl_params["yt_id"]
                    for f in self._failed_downloads
                ):
                    self._failed_downloads.append(self._current_dl_params)
                self._current_dl_params = None
        if self._play_spinner_timer:
            self._play_spinner_timer.stop()
            self._play_spinner_timer = None
        self._update_landing_chrome()
        self._sync_np_widget()

    def _on_came_back_online(self: MediaAppProtocol) -> None:
        """Handle reconnection: resume downloads, retry failed items, resume playback."""
        logger.warning(
            "Connectivity restored, resuming playback and retrying failed downloads"
        )
        self._offline_since = None
        self._download_running_flag.set()
        self._download_paused_for_offline = False
        self._queue_frozen = False
        if self._dl_spinner_timer:
            self._dl_spinner_timer.stop()
            self._dl_spinner_timer = None
        if self._clear_dl_timer:
            self._clear_dl_timer.stop()
            self._clear_dl_timer = None
        self._resume_after_reconnect()
        self._download_state = None
        self._current_dl_params = None
        while self._failed_downloads:
            item = self._failed_downloads.pop(0)
            logger.debug(
                "_on_came_back_online: retrying download yt_id=%s source=%s",
                item["yt_id"],
                item.get("source", "youtube"),
            )
            if item.get("source") == "tv_movies":
                self._pending_dl_data = {
                    "yt_id": item["yt_id"],
                    "title": item["title"],
                    "source": "tv_movies",
                    "tmdb_id": item.get("tmdb_id"),
                    "media_type": item.get("media_type"),
                    "season": item.get("season"),
                    "episode": item.get("episode"),
                    "series_title": item.get("series_title"),
                    "year": item.get("year"),
                    "_queued_server_mode": item.get("_queued_server_mode"),
                }
                self._start_tv_movies_download(
                    quality=item.get("quality", "1080p"),
                )
            else:
                self._start_download(
                    yt_id=item["yt_id"],
                    title=item["title"],
                    audio_only=item.get("audio_only", False),
                    format_str=item.get("format_str"),
                    url=item.get("url", ""),
                )
        self._check_download_queue()
        self._show_back_online = True
        self._update_landing_chrome()
        self._update_queue_indicator()
        self._sync_np_widget()
        if self._back_online_timer:
            self._back_online_timer.reset()
        else:
            self._back_online_timer = self.set_timer(3, self._clear_back_online)

    def _clear_back_online(self: MediaAppProtocol) -> None:
        """Clear the 'back online' banner after a short display period."""
        logger.debug("_clear_back_online")
        self._show_back_online = False
        self._back_online_timer = None
        self._update_landing_chrome()
        self._update_queue_indicator()

    def _resume_after_reconnect(self: MediaAppProtocol) -> None:
        """Restore playback from the last known position or play the next queued item."""
        if self._is_playing:
            logger.debug("_resume_after_reconnect: already playing, skipping")
            return
        next_request = None
        pos = None
        if self._last_playing_data:
            ld = self._last_playing_data
            pos = self._last_playback_pos
            dur = ld.get("duration", 0)
            if dur and pos >= dur * 0.98:
                logger.debug(
                    "_resume_after_reconnect: last track finished, not resuming"
                )
                self._last_playing_data = None
                self._last_playback_pos = 0
                pos = None
        if self._last_playing_data:
            ld = self._last_playing_data
            logger.debug(
                "_resume_after_reconnect: resuming yt_id=%s pos=%s", ld["yt_id"], pos
            )
            request = MediaRequest.from_dict(ld)
            request.start_pos = pos
            next_request = request
            self._last_playing_data = None
            self._last_playback_pos = 0
        elif self._playback_queue.peek():
            logger.debug("_resume_after_reconnect: playing next from queue")
            next_item = self._playback_queue.next()
            if next_item is not None:
                next_request = next_item.request
        if next_request:
            self._play(next_request)
            if self._np_side:
                self._np_side._offline_mode = False
        self._sync_np_widget()
        if self._last_failed_query:
            self._show_info(
                f"Back online. Press / to retry '{self._last_failed_query}'"
            )
            self._last_failed_query = None

    def _stop_trending(self: MediaAppProtocol) -> None:
        self._trending_in_progress = False
        self._stop_chip_spinner()
