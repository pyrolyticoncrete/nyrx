# SPDX-License-Identifier: AGPL-3.0-only

"""Download mixin: download workflow, progress tracking, and queue management."""

from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

from textual.widgets import DataTable

from nyrx.config import (
    DEFAULT_DOWNLOAD_DIR,
    FFMPEG_BINARY,
    SEVERITY_WARNING,
    TIMEOUT_CONFIRM,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
    YT_QUALITY_PRESETS,
    update_config,
)
from nyrx.helpers import iterate_episode_range, require_key
from nyrx.player import (
    _resolve_output_path,
    clean_part_files,
    download_video,
    make_final_path_recorder,
)
from nyrx.queues import DownloadCancelled
from nyrx.screens import DirInputModal

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from textual.timer import Timer

    from nyrx.protocols import MediaAppProtocol


# ── Standalone helpers ──────────────────────────────────


def _stream_download(
    stream_url: str,
    stream_headers: dict,
    output_path: str,
    on_progress: Callable,
    cancel_flag: threading.Event,
) -> None:
    """Download a stream via requests (non-yt-dlp path for direct MP4 URLs)."""
    import time

    import requests

    headers = stream_headers or {}
    resp = requests.get(stream_url, headers=headers, stream=True, timeout=(30, 120))
    resp.raise_for_status()

    total = int(resp.headers.get("content-length", 0))
    downloaded = 0
    start = time.monotonic()

    try:
        with open(output_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=8192):
                if cancel_flag.is_set():
                    raise DownloadCancelled()
                if chunk:
                    f.write(chunk)
                    downloaded += len(chunk)
                    elapsed = time.monotonic() - start
                    speed = downloaded / elapsed if elapsed > 0 else 0
                    eta = (total - downloaded) / speed if speed > 0 and total > 0 else 0
                    on_progress(
                        {
                            "status": "downloading",
                            "downloaded_bytes": downloaded,
                            "total_bytes": total or None,
                            "speed": speed,
                            "eta": eta,
                        }
                    )
    except DownloadCancelled:
        try:
            os.remove(output_path)
        except Exception:
            logger.debug(
                "_stream_download: failed to remove partial file on cancel: %s",
                output_path,
            )
        raise


class DownloadActions:
    _download_state: dict | None
    _download_dir: str | None
    _current_dl_params: dict | None
    _pending_dl_data: dict | None
    _clear_dl_timer: Timer | None
    _dl_spinner_timer: Timer | None
    _dl_cancel_watchdog: Timer | None

    # ── Download workflow ───────────────────────────────────

    def action_download(self: MediaAppProtocol, data: dict | None = None) -> None:
        """Start the download workflow: prompts for format (video/audio) then quality."""
        if self._np_focused:
            return

        if data is None:
            focused = self.focused
            if isinstance(focused, DataTable) and focused.id == "tvs-episodes":
                if tv := self._w_tv_series:
                    if focused.cursor_coordinate is not None and focused.row_count > 0:
                        cell_key = focused.coordinate_to_cell_key(
                            focused.cursor_coordinate
                        )
                        key = require_key(cell_key.row_key.value)
                        ep = tv._episode_data_map.get(key)
                        if ep:
                            payload = {
                                "current_season": tv._current_season,
                                "current_episode": ep["episode_number"],
                                "tmdb_id": tv._tmdb_id,
                                "series_title": tv._series_data.get("title", ""),
                                "seasons": tv._seasons,
                            }
                            from nyrx.screens.episode_range import EpisodeRangeModal

                            self.push_screen(
                                EpisodeRangeModal(payload),
                                self._on_episode_range_result,
                            )
                            return

        data = data or self._get_focused_track()
        if not data:
            return

        source_key = data.get("source", "youtube")
        source = self._sources.get(source_key, self._sources.get("youtube"))
        if source is None:
            self.notify(
                "Download not available for this source",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return
        params = source.download_params(data)
        if params is None:
            self.notify(
                "Download not available for this source",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return

        if (
            params.get("source") == "tv_movies"
            and params.get("media_type") == "tv"
            and not params.get("season")
            and not params.get("episode")
        ):
            self.notify(
                "Open a series first, then select episodes to download",
                severity=SEVERITY_WARNING,
                timeout=TIMEOUT_WARNING,
                title="Warning",
            )
            return

        self._pending_dl_data = params

        if params.get("audio_only"):
            self._start_download(audio_only=True, _user_initiated=True)
            return

        if params.get("source") == "tv_movies":
            side = self._w_download
            if side is None:
                self._pending_dl_data = None
                return
            side.show_tv_quality_selector(["1080p", "720p", "480p", "Best"])
            side.focus()
            self._update_mode_indicator()
            return

        side = self._w_download
        if side is None:
            logger.debug("action_download: _w_download is None")
            self._pending_dl_data = None
            return
        side.show_format_selector(["Video", "Audio"])
        side.focus()
        self._update_mode_indicator()

    def _on_episode_range_result(self: MediaAppProtocol, result: dict | None) -> None:
        """Callback from EpisodeRangeModal: enqueue the batch."""
        if not result:
            return
        self._enqueue_episode_range(result)

    def _enqueue_episode_range(self: MediaAppProtocol, result: dict) -> None:
        """Enqueue all episodes in the from-till range as individual queue items."""
        start_s, start_e = result["start_season"], result["start_episode"]
        end_s, end_e = result["end_season"], result["end_episode"]
        quality = result["quality"]
        tmdb_id = result["tmdb_id"]
        series_title = result["series_title"]
        season_map = {s["season_number"]: s["episode_count"] for s in result["seasons"]}
        output_dir = self._download_dir or str(DEFAULT_DOWNLOAD_DIR)

        queued = 0
        skipped = 0
        for s, e in iterate_episode_range(start_s, start_e, end_s, end_e, season_map):
            ep_title = f"{series_title} S{s:02d}E{e:02d}"
            output_path = self._build_tv_output_path(
                title=ep_title,
                series_title=series_title,
                season=s,
                episode=e,
                media_type="tv",
                output_dir=output_dir,
            )
            if os.path.exists(output_path):
                logger.debug("_enqueue_episode_range: skip exists %s", ep_title)
                skipped += 1
                continue
            logger.debug(
                "_enqueue_episode_range: queue S%02dE%02d  pending_before=%d",
                s,
                e,
                len(self._download_pending),
            )
            self._enqueue_download(
                yt_id=f"tmdb_{tmdb_id}",
                title=ep_title,
                source="tv_movies",
                quality=quality,
                notify=False,
                extra={
                    "tmdb_id": tmdb_id,
                    "media_type": "tv",
                    "season": s,
                    "episode": e,
                    "series_title": series_title,
                },
            )
            queued += 1

        if queued:
            parts = [f"Queued {queued} episode{'s' if queued != 1 else ''}"]
            if skipped:
                parts.append(f"({skipped} already exist, skipped)")
            self.notify("  ".join(parts), timeout=TIMEOUT_CONFIRM)
        elif skipped:
            self.notify(f"All {skipped} episodes already exist", timeout=TIMEOUT_INFO)

        logger.debug(
            "_enqueue_episode_range: done queued=%d skipped=%d total_pending=%d",
            queued,
            skipped,
            len(self._download_pending),
        )
        self._check_download_queue()

    # ── Generic download (yt-dlp) ───────────────────────────

    def _start_download(
        self: MediaAppProtocol,
        audio_only: bool = False,
        quality: str | None = None,
        yt_id: str | None = None,
        title: str | None = None,
        format_str: str | None = None,
        url: str | None = None,
        *,
        _user_initiated: bool = False,
    ) -> None:
        """Initiate a download with optional format/quality preferences."""
        data = self._pending_dl_data
        if yt_id and title:
            data = {"yt_id": yt_id, "title": title, "url": url or ""}
        self._pending_dl_data = None
        if not data:
            return

        if not self._online:
            self._enqueue_download(
                yt_id=data["yt_id"],
                title=data["title"],
                audio_only=audio_only,
                format_str=format_str,
                url=data.get("url", ""),
            )
            return

        if _user_initiated:
            if self._download_state is not None:
                self._enqueue_download(
                    yt_id=data["yt_id"],
                    title=data["title"],
                    audio_only=audio_only,
                    format_str=format_str,
                    url=data.get("url", ""),
                )
                return
        else:
            if self._download_state and self._download_state.get("status") in (
                "downloading",
                "processing",
            ):
                self._enqueue_download(
                    yt_id=data["yt_id"],
                    title=data["title"],
                    audio_only=audio_only,
                    format_str=format_str,
                    url=data.get("url", ""),
                )
                return

        if not format_str and not audio_only and quality:
            for label, _height, fmt in YT_QUALITY_PRESETS:
                if label == quality:
                    format_str = fmt
                    break

        self._current_dl_params = {
            "yt_id": data["yt_id"],
            "title": data["title"],
            "audio_only": audio_only,
            "format_str": format_str,
        }
        self._download_cancel_flag.clear()
        last_progress_time = 0.0
        last_pct = 0.0

        def on_progress(d: dict) -> None:
            while not self._download_running_flag.is_set():
                if self._download_cancel_flag and self._download_cancel_flag.is_set():
                    raise DownloadCancelled()
                time.sleep(0.5)
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                raise DownloadCancelled()
            if d["status"] == "downloading":
                nonlocal last_progress_time, last_pct
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = (d["downloaded_bytes"] / total * 100) if total > 0 else 0
                now = time.monotonic()
                if not (pct > 0 and last_pct == 0):
                    if now - last_progress_time < 2.0:
                        return
                last_progress_time = now
                last_pct = pct
                self.call_from_thread(
                    self._update_dl_progress,
                    pct,
                    d.get("speed") or 0,
                    d.get("eta") or 0,
                    data["yt_id"],
                )
            elif d["status"] == "finished":
                self.call_from_thread(self._dl_processing, data["title"])

        def on_postprocessor(d: dict) -> None:
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                return
            if d.get("status") == "started":
                _pp_map = {"Muxer": "muxing", "Muxing": "muxing"}
                stage = _pp_map.get(d.get("postprocessor", ""), "processing")
                self.call_from_thread(self._dl_processing, data["title"], stage)

        self._show_dl_progress(data["title"])
        if not self._dl_spinner_timer:
            self._dl_spinner_timer = self.set_interval(0.04, self._tick_dl_spinner)
        thread = threading.Thread(
            target=self._do_download,
            args=(
                data["yt_id"],
                data["title"],
                on_progress,
                audio_only,
                format_str,
                data.get("url", ""),
                on_postprocessor,
            ),
            daemon=True,
        )
        thread.start()

    # ── Progress UI ─────────────────────────────────────────

    def _show_dl_progress(self: MediaAppProtocol, title: str) -> None:
        """Display the initial download progress state in the sidebar."""
        self._download_state = {
            "status": "downloading",
            "title": title,
            "pct": 0,
            "speed": 0,
            "eta": 0,
        }
        self._update_sidebar_context()
        self._sync_np_widget()

    def _update_dl_progress(
        self: MediaAppProtocol, pct: float, speed: float, eta: int, yt_id: str
    ) -> None:
        """Update the download progress displayed in the sidebar."""
        if self._current_dl_params is None:
            return
        if self._current_dl_params.get("yt_id") != yt_id:
            return
        if self._download_state and self._download_state.get("status") == "downloading":
            self._download_state.update(pct=pct, speed=speed, eta=eta)
            try:
                if dw := self._w_download:
                    dw.update_progress(self._download_state)
            except Exception:
                logger.debug("_update_dl_progress: widget update failed")
        self._sync_np_widget()

    def _tick_dl_spinner(self: MediaAppProtocol) -> None:
        """Advance the download connecting spinner and refresh the widget."""
        try:
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                if self._dl_spinner_timer:
                    self._dl_spinner_timer.stop()
                    self._dl_spinner_timer = None
                if (
                    self._download_state
                    and self._download_state.get("status") == "downloading"
                ):
                    self._download_state["stage"] = "cancelling"
                    try:
                        if dw := self._w_download:
                            dw.update_progress(self._download_state)
                    except Exception:
                        logger.debug("_tick_dl_spinner: cancelling update failed")
                    if not self._dl_cancel_watchdog:
                        self._dl_cancel_watchdog = self.set_timer(
                            5, self._force_cancel_download
                        )
                return
            np = self._w_download
            if np is None:
                logger.debug("_tick_dl_spinner: _w_download is None")
                return
            if self._download_state and np.display:
                if not getattr(self, "_download_paused_for_offline", False):
                    np.update_spinner_frame()
                np.update_progress(self._download_state)
        except Exception:
            logger.debug("_tick_dl_spinner: main spinner update failed")

    def _dl_processing(
        self: MediaAppProtocol, title: str, stage: str = "processing"
    ) -> None:
        """Update the download state to show post-processing activity."""
        if self._download_state and self._download_state.get("status") == "downloading":
            self._download_state.update(
                status="processing",
                pct=100,
                speed=0,
                eta=0,
                stage=stage,
            )
            try:
                if dw := self._w_download:
                    dw.update_progress(self._download_state)
            except Exception:
                logger.debug("_dl_processing: widget update failed")
            self._sync_np_widget()

    def _dl_finished(self: MediaAppProtocol, filename: str) -> None:
        """Handle a completed download."""
        logger.debug("_dl_finished: filename=%s", filename)
        if self._dl_spinner_timer:
            self._dl_spinner_timer.stop()
            self._dl_spinner_timer = None
        if self._dl_cancel_watchdog:
            self._dl_cancel_watchdog.stop()
            self._dl_cancel_watchdog = None
        self._download_state = {
            "status": "complete",
            "filename": Path(filename).name,
        }
        self._update_sidebar_context()
        if self._clear_dl_timer:
            self._clear_dl_timer.stop()
        self._clear_dl_timer = self.set_timer(5, self._clear_dl_state)
        self._check_download_queue()

    def _dl_already_exists(self: MediaAppProtocol, filename: str) -> None:
        """Handle the case where the file already exists on disk."""
        logger.debug("_dl_already_exists: filename=%s", filename)
        if self._dl_spinner_timer:
            self._dl_spinner_timer.stop()
            self._dl_spinner_timer = None
        if self._dl_cancel_watchdog:
            self._dl_cancel_watchdog.stop()
            self._dl_cancel_watchdog = None
        self._download_state = {
            "status": "already_exists",
            "filename": filename,
        }
        self._update_sidebar_context()
        if self._clear_dl_timer:
            self._clear_dl_timer.stop()
        self._clear_dl_timer = self.set_timer(3, self._clear_dl_state)
        self._check_download_queue()

    def _dl_error(self: MediaAppProtocol, msg: str) -> None:
        """Handle a download error."""
        if self._dl_spinner_timer:
            self._dl_spinner_timer.stop()
            self._dl_spinner_timer = None
        if self._dl_cancel_watchdog:
            self._dl_cancel_watchdog.stop()
            self._dl_cancel_watchdog = None
        self._download_state = {
            "status": "error",
            "msg": msg,
        }
        self._update_sidebar_context()
        if self._clear_dl_timer:
            self._clear_dl_timer.stop()
        self._clear_dl_timer = self.set_timer(7, self._clear_dl_state)
        self._check_download_queue()

    def _cancel_download(self: MediaAppProtocol) -> None:
        """Signal the active download thread to cancel."""
        if self._download_cancel_flag:
            self._download_cancel_flag.set()

    def _force_cancel_download(self: MediaAppProtocol) -> None:
        """Force-cancel a stuck download after the watchdog timeout."""
        self._dl_cancel_watchdog = None
        if self._download_state and self._download_state.get("status") in (
            "downloading",
            "processing",
        ):
            self._dl_cancelled()

    def _dl_cancelled(self: MediaAppProtocol) -> None:
        """Handle a user-cancelled download."""
        if self._download_state and self._download_state.get("status") == "cancelled":
            return
        logger.debug("_dl_cancelled")
        if self._dl_spinner_timer:
            self._dl_spinner_timer.stop()
            self._dl_spinner_timer = None
        if self._dl_cancel_watchdog:
            self._dl_cancel_watchdog.stop()
            self._dl_cancel_watchdog = None
        self._download_state = {
            "status": "cancelled",
            "msg": "Download cancelled",
        }
        clean_part_files(self._download_dir or str(DEFAULT_DOWNLOAD_DIR))
        self._update_sidebar_context()
        if self._clear_dl_timer:
            self._clear_dl_timer.stop()
        self._clear_dl_timer = self.set_timer(3, self._clear_dl_state)
        self._check_download_queue()

    def _clear_dl_state(self: MediaAppProtocol) -> None:
        """Clear the download state from the sidebar after the display timeout."""
        self._clear_dl_timer = None
        prev_state = (
            self._download_state.get("status") if self._download_state else "None"
        )
        if self._download_state and self._download_state.get("status") == "downloading":
            return
        if self._dl_spinner_timer:
            self._dl_spinner_timer.stop()
            self._dl_spinner_timer = None
        if self._download_state and self._download_state.get("status") in (
            "complete",
            "already_exists",
            "error",
            "cancelled",
        ):
            logger.debug(
                "_clear_dl_state: clear state from %s, pending=%d",
                prev_state,
                len(self._download_pending),
            )
            self._download_state = None
            self._update_sidebar_context()
            self._check_download_queue()

    # ── Download queue ──────────────────────────────────────

    def _check_download_queue(self: MediaAppProtocol) -> None:
        """Start the next queued download if nothing is currently downloading."""
        if self._download_state and self._download_state.get("status") in (
            "downloading",
            "processing",
        ):
            logger.debug(
                "_check_download_queue: skip (state=%s)",
                self._download_state.get("status"),
            )
            return
        if not self._download_pending:
            logger.debug("_check_download_queue: queue empty")
            return
        next_item = self._download_pending.pop(0)
        title = next_item.get("title", "?")
        season = next_item.get("season")
        episode = next_item.get("episode")
        logger.debug(
            "_check_download_queue: dequeue title=%s S%02dE%02d  remaining=%d  state=%s",
            title,
            season or 0,
            episode or 0,
            len(self._download_pending),
            self._download_state.get("status") if self._download_state else "None",
        )
        if next_item.get("source") == "tv_movies":
            self._pending_dl_data = {
                "yt_id": next_item["yt_id"],
                "title": next_item["title"],
                "source": "tv_movies",
                "tmdb_id": next_item.get("tmdb_id"),
                "media_type": next_item.get("media_type"),
                "season": next_item.get("season"),
                "episode": next_item.get("episode"),
                "series_title": next_item.get("series_title"),
                "year": next_item.get("year"),
                "_queued_server_mode": next_item.get("_queued_server_mode"),
            }
            self._start_tv_movies_download(quality=next_item.get("quality", "1080p"))
        else:
            self._start_download(
                yt_id=next_item["yt_id"],
                title=next_item["title"],
                audio_only=next_item.get("audio_only", False),
                format_str=next_item.get("format_str"),
                url=next_item.get("url", ""),
            )

    def _enqueue_download(
        self: MediaAppProtocol,
        yt_id: str,
        title: str,
        audio_only: bool = False,
        format_str: str | None = None,
        url: str | None = None,
        source: str = "youtube",
        quality: str | None = None,
        extra: dict | None = None,
        notify: bool = True,
    ) -> None:
        """Add a download to the pending queue.

        ``notify`` matches the ``MediaAppProtocol`` contract: True shows a
        ``Queued:`` toast (False for callers that notify in bulk).
        """
        item: dict = {
            "source": source,
            "yt_id": yt_id,
            "title": title,
            "audio_only": audio_only,
            "format_str": format_str,
            "url": url or "",
        }
        if quality:
            item["quality"] = quality
        if extra:
            item.update(extra)
        self._download_pending.append(item)
        season = item.get("season")
        episode = item.get("episode")
        logger.debug(
            "_enqueue_download: append title=%s S%02dE%02d  pos=%d  source=%s",
            title,
            season or 0,
            episode or 0,
            len(self._download_pending) - 1,
            source,
        )
        self._sync_np_widget()
        if notify:
            self.notify(f"Queued: {title}", timeout=TIMEOUT_CONFIRM)

    # ── Background download (yt-dlp) ────────────────────────

    def _do_download(
        self: MediaAppProtocol,
        yt_id: str,
        title: str,
        on_progress: Callable,
        audio_only: bool = False,
        format_str: str | None = None,
        url: str | None = None,
        postprocessor_callback: Callable | None = None,
    ) -> None:
        """Run the actual download on a background thread."""
        if self._download_cancel_flag and self._download_cancel_flag.is_set():
            self.call_from_thread(self._dl_cancelled)
            return
        output_dir = self._download_dir or str(DEFAULT_DOWNLOAD_DIR)
        try:
            out_path, already_existed = download_video(
                yt_id,
                title,
                output_dir=output_dir,
                progress_callback=on_progress,
                audio_only=audio_only,
                format_str=format_str,
                url=url,
                postprocessor_callback=postprocessor_callback,
            )
            self._current_dl_params = None
            self.call_from_thread(
                self._dl_already_exists if already_existed else self._dl_finished,
                Path(out_path).name,
            )
        except DownloadCancelled:
            self._current_dl_params = None
            self.call_from_thread(self._dl_cancelled)
        except Exception as e:
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                self.call_from_thread(self._dl_cancelled)
                return
            if (
                self._download_state
                and self._download_state.get("status") == "downloading"
            ):
                if not self._online:
                    item = {
                        "source": "youtube",
                        "yt_id": yt_id,
                        "title": title,
                        "audio_only": audio_only,
                        "format_str": format_str,
                        "url": url or "",
                    }
                    if not any(f["yt_id"] == yt_id for f in self._failed_downloads):
                        self._failed_downloads.append(item)
                    self.call_from_thread(
                        self._dl_error,
                        f"Download paused (offline), will retry: {title[:30]}...",
                    )
                return
            err_str = str(e).lower()
            if any(
                x in err_str
                for x in [
                    "timeout",
                    "connection",
                    "reset",
                    "eof",
                    "name or service not known",
                    "network is unreachable",
                    "connection refused",
                ]
            ):
                item = {
                    "source": "youtube",
                    "yt_id": yt_id,
                    "title": title,
                    "audio_only": audio_only,
                    "format_str": format_str,
                    "url": url or "",
                }
                if not any(f["yt_id"] == yt_id for f in self._failed_downloads):
                    self._failed_downloads.append(item)
                self.call_from_thread(
                    self._dl_error,
                    f"Download paused (offline), will retry: {title[:30]}...",
                )
            else:
                self.call_from_thread(self._dl_error, str(e))

    # ── TV/Movies download ──────────────────────────────────

    def _start_tv_movies_download(
        self: MediaAppProtocol,
        quality: str,
        yt_id: str | None = None,
        title: str | None = None,
    ) -> None:
        """Launch a TV/Movies download after probing for a stream URL."""
        data = self._pending_dl_data
        if yt_id and title:
            data = {"yt_id": yt_id, "title": title}
        self._pending_dl_data = None
        if not data:
            return

        if not self._online:
            logger.debug(
                "_start_tv_movies_download: offline, re-enqueue %s", data.get("title")
            )
            self._enqueue_download(
                source="tv_movies",
                quality=quality,
                yt_id=data["yt_id"],
                title=data["title"],
                extra=data,
            )
            return

        if self._download_state and self._download_state.get("status") in (
            "downloading",
            "processing",
        ):
            logger.debug(
                "_start_tv_movies_download: state=%s re-enqueue %s",
                self._download_state.get("status"),
                data.get("title"),
            )
            self._enqueue_download(
                source="tv_movies",
                quality=quality,
                yt_id=data["yt_id"],
                title=data["title"],
                extra=data,
            )
            return

        logger.debug(
            "_start_tv_movies_download: start S%02dE%02d %s",
            data.get("season", 0),
            data.get("episode", 0),
            data.get("title"),
        )
        self._current_dl_params = {
            "yt_id": data["yt_id"],
            "title": data["title"],
            "source": "tv_movies",
            "quality": quality,
            "tmdb_id": data.get("tmdb_id"),
            "media_type": data.get("media_type"),
            "season": data.get("season"),
            "episode": data.get("episode"),
            "series_title": data.get("series_title"),
            "year": data.get("year"),
            "_queued_server_mode": data.get("_queued_server_mode"),
        }
        self._download_cancel_flag.clear()
        last_progress_time = 0.0
        last_pct = 0.0

        def on_progress(d: dict) -> None:
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                raise DownloadCancelled()
            if d["status"] == "downloading":
                nonlocal last_progress_time, last_pct
                total = d.get("total_bytes") or d.get("total_bytes_estimate") or 0
                pct = (d["downloaded_bytes"] / total * 100) if total > 0 else 0
                now = time.monotonic()
                if not (pct > 0 and last_pct == 0):
                    if now - last_progress_time < 2.0:
                        return
                last_progress_time = now
                last_pct = pct
                self.call_from_thread(
                    self._update_dl_progress,
                    pct,
                    d.get("speed") or 0,
                    d.get("eta") or 0,
                    data["yt_id"],
                )

        def on_postprocessor(d: dict) -> None:
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                return
            if d.get("status") == "started":
                _pp_map = {"Muxer": "muxing", "Muxing": "muxing"}
                stage = _pp_map.get(d.get("postprocessor", ""), "processing")
                self.call_from_thread(self._dl_processing, data["title"], stage)

        self._show_dl_progress(data["title"])
        if not self._dl_spinner_timer:
            self._dl_spinner_timer = self.set_interval(0.04, self._tick_dl_spinner)
        thread = threading.Thread(
            target=self._do_tv_movies_download,
            args=(data, quality, on_progress, on_postprocessor),
            daemon=True,
        )
        thread.start()

    def _do_tv_movies_download(
        self: MediaAppProtocol,
        data: dict,
        quality_label: str,
        on_progress: Callable[[dict], None],
        on_postprocessor: Callable[[dict], None],
    ) -> None:
        import os
        import shutil
        import tempfile
        from pathlib import Path

        import yt_dlp

        from nyrx.config import DEFAULT_DOWNLOAD_DIR
        from nyrx.sources.tv_movies import TVMoviesSource, _download_subtitles

        sub_tmpdir = None
        extra_audio_paths: list[tuple[str, str]] = []

        try:
            probe_quality = None
            for _label, height, _ in YT_QUALITY_PRESETS:
                if _label == quality_label:
                    probe_quality = height
                    break

            tv_source = self._sources["tv_movies"]
            if not isinstance(tv_source, TVMoviesSource):
                raise Exception("TV/Movies source not available")
            is_tv = data.get("media_type") == "tv"
            season = data.get("season") or 1 if is_tv else None
            episode = data.get("episode") or 1 if is_tv else None

            probe_params: dict = {
                "tmdb_id": data["tmdb_id"],
                "media_type": data["media_type"],
            }
            if probe_quality is not None:
                probe_params["quality"] = probe_quality
            if is_tv:
                probe_params["season"] = season
                probe_params["episode"] = episode

            queued = data.get("_queued_server_mode")
            if queued is not None:
                server_name: str | None = None if queued == "auto" else queued
            else:
                server_name = (
                    None if tv_source._server_mode == "auto" else tv_source._server_mode
                )

            logger.debug(
                "_do_tv_movies_download: probing %s quality=%s server=%s",
                data["title"],
                quality_label,
                server_name or "auto",
            )

            result = tv_source._dispatcher.probe(probe_params, server_name=server_name)
            if not result:
                raise Exception("No stream available from any server")

            stream_url = result["stream_url"]
            stream_headers = result.get("stream_headers") or {}
            referrer = stream_headers.get("Referer", "")

            sub_entries = result.get("subs") or []
            sub_headers = result.get("sub_headers") or {}
            try:
                sub_tmpdir, vtt_paths = _download_subtitles(
                    sub_entries,
                    referrer,
                    sub_headers,
                )
            except Exception:
                sub_tmpdir = None
                vtt_paths = []

            output_dir = self._download_dir or str(DEFAULT_DOWNLOAD_DIR)
            os.makedirs(output_dir, exist_ok=True)
            output_path = self._build_tv_output_path(
                title=data["title"],
                year=data.get("year"),
                media_type=data.get("media_type", "movie"),
                series_title=data.get("series_title"),
                season=season,
                episode=episode,
                output_dir=output_dir,
            )

            if os.path.exists(output_path):
                self._current_dl_params = None
                self.call_from_thread(self._dl_already_exists, Path(output_path).name)
                return

            if result.get("format") == "mp4":
                _stream_download(
                    stream_url=stream_url,
                    stream_headers=stream_headers,
                    output_path=output_path,
                    on_progress=on_progress,
                    cancel_flag=self._download_cancel_flag,
                )
                if vtt_paths or extra_audio_paths:
                    self._mux_tv_download(output_path, vtt_paths, extra_audio_paths)
                self._current_dl_params = None
                self.call_from_thread(self._dl_finished, Path(output_path).name)
                return

            ydl_opts: dict = {
                "outtmpl": output_path,
                "quiet": True,
                "no_warnings": True,
                "http_headers": stream_headers,
                "merge_output_format": "mkv",
                "socket_timeout": 30,
                "extractor_retries": 3,
                "retries": 3,
                "keepvideo": False,
                "ffmpeg_args": ["-threads", "1"],
            }
            if FFMPEG_BINARY:
                ydl_opts["ffmpeg_location"] = os.path.dirname(FFMPEG_BINARY)
            ydl_opts["progress_hooks"] = [on_progress]
            finalized: dict[str, str] = {}
            ydl_opts["postprocessor_hooks"] = [
                make_final_path_recorder(finalized),
                on_postprocessor,
            ]

            with yt_dlp.YoutubeDL(ydl_opts) as ydl:
                info = ydl.extract_info(stream_url, download=True)

            actual_path = _resolve_output_path(ydl, info, finalized=finalized)
            if not actual_path:
                actual_path = output_path

            audio_urls = result.get("audio_urls") or []
            audio_langs = result.get("audio_langs") or []
            for i, audio_url in enumerate(audio_urls):
                lang = audio_langs[i] if i < len(audio_langs) else f"track_{i}"
                safe_lang = "".join(c for c in lang if c.isalnum() or c in "_-")
                tmp_audio = os.path.join(
                    tempfile.gettempdir(),
                    f"mc_audio_{data['yt_id']}_{safe_lang}.%(ext)s",
                )
                audio_opts: dict = {
                    "outtmpl": tmp_audio,
                    "quiet": True,
                    "no_warnings": True,
                    "http_headers": stream_headers,
                    "format": "bestaudio/best",
                    "ffmpeg_args": ["-threads", "1"],
                }
                if FFMPEG_BINARY:
                    audio_opts["ffmpeg_location"] = os.path.dirname(FFMPEG_BINARY)
                try:
                    with yt_dlp.YoutubeDL(audio_opts) as ydl:
                        ydl.download([audio_url])
                    for f in os.listdir(tempfile.gettempdir()):
                        if f.startswith(f"mc_audio_{data['yt_id']}_{safe_lang}"):
                            full = os.path.join(tempfile.gettempdir(), f)
                            extra_audio_paths.append((full, lang))
                            break
                except Exception:
                    logger.debug("_do_tv_movies_download: audio track %s failed", lang)

            if vtt_paths or extra_audio_paths:
                self._mux_tv_download(actual_path, vtt_paths, extra_audio_paths)

            self._current_dl_params = None
            self.call_from_thread(self._dl_finished, Path(actual_path).name)

        except DownloadCancelled:
            self._current_dl_params = None
            self.call_from_thread(self._dl_cancelled)
        except Exception as e:
            self._current_dl_params = None
            if self._download_cancel_flag and self._download_cancel_flag.is_set():
                self.call_from_thread(self._dl_cancelled)
                return
            err_str = str(e).lower()
            if any(x in err_str for x in ["timeout", "connection", "reset"]):
                item = {
                    "source": "tv_movies",
                    "quality": quality_label,
                    "yt_id": data["yt_id"],
                    "title": data["title"],
                    "tmdb_id": data.get("tmdb_id"),
                    "media_type": data.get("media_type"),
                    "season": data.get("season"),
                    "episode": data.get("episode"),
                    "series_title": data.get("series_title"),
                    "year": data.get("year"),
                    "_queued_server_mode": data.get("_queued_server_mode"),
                }
                if not any(f["yt_id"] == data["yt_id"] for f in self._failed_downloads):
                    self._failed_downloads.append(item)
                self.call_from_thread(
                    self._dl_error,
                    f"Download paused (offline), will retry: {data['title'][:30]}...",
                )
            else:
                self.call_from_thread(self._dl_error, str(e))
        finally:
            if sub_tmpdir:
                shutil.rmtree(sub_tmpdir, ignore_errors=True)
            for path, _ in extra_audio_paths:
                try:
                    os.remove(path)
                except Exception:
                    logger.debug(
                        "_do_tv_movies_download: failed to remove temp audio track: %s",
                        path,
                    )

    def _build_tv_output_path(
        self,
        title: str,
        year: str | None = None,
        media_type: str = "movie",
        series_title: str | None = None,
        season: int | None = None,
        episode: int | None = None,
        output_dir: str = "~/Videos",
    ) -> str:
        """Build a sanitized output path for TV/Movies downloads."""
        import os
        import re

        def sanitize(s: Any) -> str:
            return re.sub(r'[\\/:*?"<>|]', "_", str(s)).strip()

        if media_type == "movie":
            parts = [sanitize(title)]
            if year:
                parts.append(f"({year})")
            filename = " ".join(parts) + ".mkv"
        else:
            base = sanitize(series_title or title)
            if season is not None and episode is not None:
                filename = f"{base} S{int(season):02d}E{int(episode):02d}.mkv"
            elif year:
                filename = f"{base} ({year}).mkv"
            else:
                filename = f"{base}.mkv"
        return os.path.join(os.path.expanduser(output_dir), filename)

    def _mux_tv_download(
        self,
        primary_path: str,
        vtt_paths: list[tuple[str, str]],
        extra_audio_paths: list[tuple[str, str]],
    ) -> None:
        """Mux subtitle and extra audio tracks into the primary video file via ffmpeg."""
        import os
        import subprocess

        from nyrx.config import FFMPEG_BINARY

        tmp_path = primary_path + ".tmp"
        cmd = [FFMPEG_BINARY, "-i", primary_path]

        for sp, _ in vtt_paths:
            cmd += ["-i", sp]
        for path, _ in extra_audio_paths:
            cmd += ["-i", path]

        n_subs = len(vtt_paths)
        n_audio = len(extra_audio_paths)

        cmd += ["-c", "copy", "-map", "0:v", "-map", "0:a:0", "-map", "0:s?"]
        for i in range(n_subs):
            cmd += ["-map", f"{i + 1}:s"]
        for j in range(n_audio):
            input_idx = 1 + n_subs + j
            cmd += ["-map", f"{input_idx}:a"]
            lang = extra_audio_paths[j][1]
            cmd += ["-metadata:s:a", f"language={lang}"]
        for i, (_, lang) in enumerate(vtt_paths):
            cmd.append(f"-metadata:s:s:{i}")
            cmd.append(f"title={lang}")
        cmd += ["-threads", "1", "-f", "matroska", tmp_path]
        subprocess.run(cmd, check=True, capture_output=True)
        os.replace(tmp_path, primary_path)

    # ── Format helpers ──────────────────────────────────────

    @staticmethod
    def _fmt_speed(speed: float) -> str:
        """Format download speed as a human-readable string."""
        if speed <= 0:
            return "?"
        if speed >= 1_000_000:
            return f"{speed / 1_000_000:.1f} MB/s"
        if speed >= 1_000:
            return f"{speed / 1_000:.1f} KB/s"
        return f"{speed:.0f} B/s"

    @staticmethod
    def _fmt_eta(eta: int) -> str:
        """Format download ETA as a human-readable string."""
        if eta <= 0:
            return "?"
        m, s = divmod(int(eta), 60)
        h, m = divmod(m, 60)
        if h:
            return f"{h}:{m:02d}:{s:02d}"
        return f"{m}:{s:02d}"

    # ── Download directory ──────────────────────────────────

    def action_change_download_dir(self: MediaAppProtocol) -> None:
        """Open the directory input modal to change the download path."""
        current = self._download_dir or str(DEFAULT_DOWNLOAD_DIR)
        self.push_screen(DirInputModal(current), self._on_dir_selected)

    def _on_dir_selected(self: MediaAppProtocol, path: str | None) -> None:
        """Handle directory selection from the DirInputModal."""
        if path:
            self._download_dir = path
            update_config(download_dir=path)
            clean_part_files(path)
            self.notify(f"Download directory: {path}", timeout=TIMEOUT_INFO)
