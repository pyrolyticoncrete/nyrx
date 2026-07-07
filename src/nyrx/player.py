# SPDX-License-Identifier: AGPL-3.0-only

"""YouTube search, download, and playback via yt-dlp + mpv IPC.

Provides the core engine powering the YouTube TUI: searching with
yt-dlp flat playlist dumps, downloading with yt-dlp + optional progress
hooks, and playing via mpv with Unix-socket IPC for seek/pause/position
polling.
"""

import json
import logging
import os
import socket
import subprocess
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import requests

from nyrx.config import CONFIG_DIR, SC_THUMBS_DIR, TEMP_THUMBS, YT_SEARCH_LIMIT

logger = logging.getLogger(__name__)

THUMB_CACHE = TEMP_THUMBS
IPC_SOCKET_DIR = Path("/tmp") / "nyrx-mpv"
_TRACKER_SCRIPT = str(Path(__file__).parent / "data" / "tracker.lua")


def _cleanup_orphaned_sockets(
    sock_dir: Path | None = None,
    max_age: int = 86400,
) -> None:
    """Remove IPC socket files from a previous session (background, non-blocking).

    Parameters are exposed for testing; production callers rely on defaults.
    """
    target = sock_dir if sock_dir is not None else IPC_SOCKET_DIR
    try:
        if not target.exists():
            return
        now = time.time()
        for entry in target.iterdir():
            if entry.suffix == ".sock":
                age = now - entry.stat().st_mtime
                # NOTE: age-only check; 24h threshold means no movie triggers false
                # deletion (longest film ~4h). Stale sockets from crashes still get
                # cleaned next day.
                if age > max_age:
                    entry.unlink(missing_ok=True)
    except Exception:
        logger.debug("_cleanup_orphaned_sockets: cleanup failed")


_cleanup_thread = threading.Thread(target=_cleanup_orphaned_sockets, daemon=True)
_cleanup_thread.start()


def _mpv_read_reply(sock: socket.socket) -> dict | None:
    """Read the next non-event reply line from an mpv IPC socket.

    mpv broadcasts asynchronous ``event`` messages (``end-file``, ``pause``,
    ``file-loaded``, ...) as unsolicited JSON lines that may be interleaved
    with command replies.  Event lines are skipped so the actual command reply
    is returned, or None on EOF/communication failure.
    """
    buf = b""
    while True:
        while b"\n" not in buf:
            chunk = sock.recv(4096)
            if not chunk:
                return None
            buf += chunk
        line, buf = buf.split(b"\n", 1)
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line.decode())
        except json.JSONDecodeError:
            logger.debug("_mpv_read_reply: invalid JSON line discarded")
            continue
        if message.get("event") is None:
            return message


class MpvIPCThread(threading.Thread):
    """Background thread that polls mpv state on its own socket.

    Runs ``_poll_once()`` then ``_stop_event.wait(0.08)`` each cycle, so the
    effective interval is roughly 0.08s plus socket round-trip time, not the
    0.5s an earlier docstring claimed.  That is intentional and not an issue:
    the loop is bounded by the 0.08s sleep, and the main thread reads a cached
    snapshot via :meth:`get_state` with no blocking I/O.

    Stores cached values for time_pos, duration, paused, and paused-for-cache.
    """

    def __init__(self, socket_path: str, process: subprocess.Popen) -> None:
        super().__init__(daemon=True)
        self._socket_path = socket_path
        self._process = process
        self._state: dict = {
            "running": True,
            "time_pos": None,
            "duration": None,
            "paused": False,
            "paused_for_cache": False,
            "metadata": None,
        }
        self._lock = threading.Lock()
        self._stop_event = threading.Event()

    def run(self) -> None:
        while not self._stop_event.is_set():
            try:
                self._poll_once()
            except Exception:
                logger.exception("MpvIPCThread: poll_once failed")
            self._stop_event.wait(0.08)

    def _poll_once(self) -> None:
        running = self._process.poll() is None
        time_pos = None
        duration = None
        paused = False
        paused_for_cache = False
        metadata = None

        if not running:
            logger.debug(
                "MpvIPCThread._poll_once: process exited (rc=%s)",
                self._process.returncode,
            )

        if running:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            try:
                sock.connect(self._socket_path)

                def _extract(data: dict | None) -> Any:
                    if data and data.get("error") == "success":
                        return data.get("data")
                    return None

                time_pos = _extract(
                    self._send_on_socket(
                        sock, {"command": ["get_property", "time-pos"]}
                    )
                )
                duration = _extract(
                    self._send_on_socket(
                        sock, {"command": ["get_property", "duration"]}
                    )
                )
                pause_data = _extract(
                    self._send_on_socket(sock, {"command": ["get_property", "pause"]})
                )
                if pause_data is not None:
                    paused = bool(pause_data)
                cache_data = _extract(
                    self._send_on_socket(
                        sock, {"command": ["get_property", "paused-for-cache"]}
                    )
                )
                if cache_data is not None:
                    paused_for_cache = bool(cache_data)
                metadata = _extract(
                    self._send_on_socket(
                        sock, {"command": ["get_property", "metadata"]}
                    )
                )
            except Exception:
                logger.debug("MpvIPCThread._poll_once: socket communication failed")
            finally:
                try:
                    sock.close()
                except Exception:
                    logger.debug("MpvIPCThread._poll_once: sock.close failed")

        with self._lock:
            self._state["running"] = running
            self._state["time_pos"] = time_pos
            self._state["duration"] = duration
            self._state["paused"] = paused
            self._state["paused_for_cache"] = paused_for_cache
            self._state["metadata"] = metadata

    @staticmethod
    def _send_on_socket(sock: socket.socket, data: dict) -> dict | None:
        """Send one JSON command on an already-connected socket and read its reply.

        mpv may interleave asynchronous ``event`` messages with command
        replies; ``_mpv_read_reply`` skips those event lines so the true
        command reply is returned.
        """
        try:
            sock.send(json.dumps(data).encode() + b"\n")
            return _mpv_read_reply(sock)
        except Exception:
            logger.debug("MpvIPCThread._send_on_socket: send/recv failed")
        return None

    def get_state(self) -> dict:
        """Return a snapshot of the latest cached mpv state (non-blocking)."""
        with self._lock:
            return dict(self._state)

    def stop(self) -> None:
        """Signal the thread to exit its poll loop."""
        self._stop_event.set()


class MPVIPC:
    """Controls an mpv process via a Unix-domain IPC socket.

    Sends JSON-formatted mpv commands over the socket to query playback
    position/duration, seek, pause, or stop playback gracefully.
    """

    def __init__(self, socket_path: str, process: subprocess.Popen) -> None:
        self.socket_path = socket_path
        self.process = process
        self._poll_thread = MpvIPCThread(socket_path, process)
        self._poll_thread.start()

    def _send(self, data: dict) -> dict | None:
        """Send a JSON command to mpv via the IPC socket and read the response.

        Args:
            data: Dict representing an mpv command (e.g. {"command": ["seek", 10, "relative"]}).

        Returns:
            Parsed response dict, or None if communication failed.  Any
            asynchronous ``event`` lines mpv interleaves with the reply are
            skipped (see ``_mpv_read_reply``).
        """
        try:
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.settimeout(2)
            sock.connect(self.socket_path)
            sock.send(json.dumps(data).encode() + b"\n")
            resp = _mpv_read_reply(sock)
            sock.close()
            return resp
        except Exception:
            logger.debug("MPVIPC._send: IPC command failed")
        return None

    def seek(self, seconds: float, absolute: bool = False) -> None:
        """Seek forward/backward by *seconds*, or to an absolute position."""
        mode = "absolute" if absolute else "relative"
        self._send({"command": ["seek", seconds, mode]})

    def toggle_pause(self) -> None:
        """Toggle play/pause state."""
        self._send({"command": ["cycle", "pause"]})

    def get_state(self) -> dict:
        """Return the latest cached mpv state (non-blocking, from background thread)."""
        return self._poll_thread.get_state()

    def stop(self) -> None:
        """Stop mpv gracefully: stop poll thread first, then IPC quit.

        Stops the background polling thread before sending the quit
        command so the thread never tries to poll a dead socket.
        """
        self._poll_thread.stop()
        self._poll_thread.join(timeout=0.5)
        try:
            self._send({"command": ["quit"]})
        except Exception:
            logger.debug("MPVIPC.stop: quit command failed")
        try:
            self.process.wait(timeout=3)
        except Exception:
            logger.debug("MPVIPC.stop: process.wait failed")
        if self.process.poll() is None:
            try:
                self.process.terminate()
                self.process.wait(timeout=2)
            except Exception:
                logger.debug("MPVIPC.stop: terminate failed")
                try:
                    self.process.kill()
                except Exception:
                    logger.debug("MPVIPC.stop: kill failed")
        self._cleanup()

    def is_running(self) -> bool:
        """True if the mpv process is still alive."""
        return self.process.poll() is None

    def _cleanup(self) -> None:
        """Remove the stale IPC socket file from /tmp."""
        try:
            if self.socket_path and os.path.exists(self.socket_path):
                os.unlink(self.socket_path)
        except Exception:
            logger.debug("MPVIPC._cleanup: unlink failed")


def _ensure_cache() -> None:
    """Ensure the thumbnail cache directory exists."""
    THUMB_CACHE.mkdir(parents=True, exist_ok=True)


def estimate_raw_height(width_chars: int, aspect: float) -> int:
    """Predict image height in terminal rows: matches ThumbImage's internal sizing.

    Uses textual-image's own ``get_cell_size()`` so the predictor and
    the renderer compute from identical pixel-per-cell values.

    Parameters
    ----------
    width_chars:
        Target image width in character columns.
    aspect:
        Source image aspect as **height / width**.
        SC 1:1 square → 1.0, YT 16:9 → 9/16, TMDB poster → 278/185.
    """
    from textual_image._terminal import get_cell_size

    cell = get_cell_size()
    if cell.width and cell.height:
        return round(width_chars * cell.width * aspect / cell.height)
    return int(width_chars * 0.5 * aspect)


def _select_thumbnail(thumbnails: list[dict]) -> str:
    """Pick the largest thumbnail URL from a list of thumbnail dicts."""
    if not thumbnails:
        return ""
    best = max(thumbnails, key=lambda t: t.get("width", 0) * t.get("height", 0))
    return best["url"]


def search_youtube(query: str, limit: int | None = None) -> list[dict]:
    """Search YouTube via yt-dlp's flat-playlist JSON dump mode.

    Args:
        query: Free-text search string.
        limit: Max results (defaults to YT_SEARCH_LIMIT from config).

    Returns:
        List of result dicts with keys: yt_id, title, channel, duration,
        views, published, thumbnail_url.

    Raises:
        RuntimeError: If yt-dlp search fails.
    """
    n = limit or YT_SEARCH_LIMIT
    cmd = [
        "yt-dlp",
        "--flat-playlist",
        "--dump-json",
        "--no-warnings",
        f"ytsearch{n}:{query}",
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        raise RuntimeError(proc.stderr.strip() or f"yt-dlp exited {proc.returncode}")

    results = []
    for line in proc.stdout.strip().split("\n"):
        if not line.strip():
            continue
        item = json.loads(line)
        thumb = _select_thumbnail(item.get("thumbnails", []) or [])
        results.append(
            {
                "yt_id": item["id"],
                "title": item.get("title", ""),
                "channel": item.get("channel", item.get("uploader", "")),
                "duration": item.get("duration", 0),
                "views": item.get("view_count", 0),
                "published": item.get("release_year", ""),
                "thumbnail_url": thumb,
            }
        )
    return results


def fetch_video_metadata(url: str) -> dict | None:
    """Fetch metadata for a single video URL via yt-dlp's extract_info.

    Returns the same dict shape as ``search_youtube`` so callers can treat
    both code paths uniformly.

    Args:
        url: Full YouTube URL (e.g. https://www.youtube.com/watch?v=...).

    Returns:
        Result dict or None if extraction failed.
    """
    import yt_dlp

    try:
        with yt_dlp.YoutubeDL(
            {
                "quiet": True,
                "no_warnings": True,
            }
        ) as ydl:
            info = ydl.extract_info(url, download=False)
            if info and info.get("id"):
                return {
                    "yt_id": info["id"],
                    "title": info.get("title", "Untitled"),
                    "channel": info.get("channel", info.get("uploader", "Unknown")),
                    "views": info.get("view_count", 0),
                    "duration": info.get("duration", 0),
                    "thumbnail_url": info.get("thumbnail", ""),
                }
            return None
    except Exception:
        logger.warning("fetch_video_metadata: yt-dlp extraction failed for %s", url)
        return None


def get_thumbnail_path(
    yt_id: str, thumb_url: str = "", source: str = ""
) -> Path | None:
    """Return a cached thumbnail path for the given video/audio ID.

    Lookup order depends on source:
    - ``soundcloud``: checks ``sc_thumbnails/`` first, then ``tmp_thumbs/``.
      Downloads to ``tmp_thumbs/`` on cache miss, using only the provided
      ``thumb_url``: no YouTube URL fallback.
    - ``youtube`` / default: checks ``tmp_thumbs/``, then tries
      ``maxresdefault`` → ``thumb_url`` → ``hqdefault``.

    Args:
        yt_id: Video/audio ID.
        thumb_url: Optional known thumbnail URL (e.g. from search results).
        source: ``"soundcloud"`` or ``"youtube"`` (default).

    Returns:
        Path to the cached JPG file, or None if all sources failed.
    """
    _ensure_cache()
    dst = THUMB_CACHE / f"{yt_id}.jpg"
    if dst.exists():
        return dst

    if source == "soundcloud":
        sc_path = SC_THUMBS_DIR / f"{yt_id}.jpg"
        if sc_path.exists():
            return sc_path
        urls = [thumb_url] if thumb_url else []
    else:
        urls = [
            f"https://img.youtube.com/vi/{yt_id}/maxresdefault.jpg",
            thumb_url,
            f"https://img.youtube.com/vi/{yt_id}/hqdefault.jpg",
        ]

    for url in urls:
        if not url:
            continue
        try:
            resp = requests.get(url, timeout=10)
            resp.raise_for_status()
            if url == thumb_url or not source or source != "soundcloud":
                dst.write_bytes(resp.content)
                return dst
        except Exception:
            logger.debug("get_thumbnail_path: download failed for %s", url)
            continue
    return None


def format_duration(seconds: int) -> str:
    """Format a duration in seconds as ``M:SS`` or ``H:MM:SS``."""
    if not seconds:
        return "?"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_seconds(seconds: float) -> str:
    """Format a float seconds value as ``M:SS`` or ``H:MM:SS``."""
    if not seconds or seconds < 0:
        return "0:00"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}:{m:02d}:{s:02d}"
    return f"{m}:{s:02d}"


def format_views(count: int) -> str:
    """Format a view count as a human-readable string (e.g. "1.2M views")."""
    if not count:
        return ""
    if count >= 1_000_000:
        return f"{count / 1_000_000:.1f}M views"
    if count >= 1_000:
        return f"{count / 1_000:.1f}K views"
    return f"{count} views"


def make_final_path_recorder(finalized: dict[str, str]) -> Callable[[dict], None]:
    """Return a yt-dlp postprocessor hook that records the final output filepath.

    FFmpegExtractAudio/Muxer rename the file during post-processing; the
    authoritative post-processed path is exposed to postprocessor hooks via
    ``info_dict["filepath"]`` when the hook fires with status ``finished``.
    """

    def _record(d: dict) -> None:
        if d.get("status") == "finished":
            filepath = (d.get("info_dict") or {}).get("filepath")
            if filepath:
                finalized["filepath"] = str(filepath)

    return _record


def _resolve_output_path(ydl: Any, info: dict, *, finalized: dict[str, str]) -> str:
    """Resolve the actual on-disk output path for a completed download.

    Priority:
      1. filepath captured by ``make_final_path_recorder`` (post-processed path,
         e.g. the .mp3 produced by FFmpegExtractAudio).
      2. ``info["requested_downloads"][0]["filepath"]`` (yt-dlp's own record of
         what was written for this download).
      3. ``ydl.prepare_filename(info)``.

    The first candidate that exists on disk wins; otherwise the highest-priority
    non-empty candidate is returned with a warning (best-effort, no crash).
    """
    candidates: list[str] = []
    if finalized.get("filepath"):
        candidates.append(finalized["filepath"])
    req_downloads = info.get("requested_downloads")
    if isinstance(req_downloads, list) and req_downloads:
        first = req_downloads[0]
        if isinstance(first, dict):
            fp = first.get("filepath")
            if fp:
                candidates.append(str(fp))
    try:
        candidates.append(str(ydl.prepare_filename(info)))
    except Exception:
        logger.debug("_resolve_output_path: prepare_filename failed")
    for cand in candidates:
        if cand and os.path.exists(cand):
            return cand
    if candidates:
        logger.warning(
            "_resolve_output_path: no candidate exists on disk; returning best-effort %s",
            candidates[0],
        )
        return candidates[0]
    return ""


def download_video(
    yt_id: str,
    title: str,
    output_dir: str = "~/Videos",
    progress_callback: Callable | None = None,
    audio_only: bool = False,
    format_str: str | None = None,
    url: str | None = None,
    postprocessor_callback: Callable | None = None,
) -> tuple[str, bool]:
    """Download a video/audio via yt-dlp.

    Args:
        yt_id: Video ID (used for cache path / fallback URL).
        title: Video title (used in output path and UI).
        output_dir: Download directory (supports ~ expansion).
        progress_callback: Function to call with yt-dlp progress dicts.
        audio_only: If True, extract audio as MP3.
        format_str: yt-dlp format string override.
        url: Explicit URL to download. If omitted, built from yt_id as YouTube URL.
        postprocessor_callback: Function to call with yt-dlp postprocessor dicts.

    Returns:
        Tuple of (file_path, already_existed).
    """
    import os

    import yt_dlp

    from nyrx.config import FFMPEG_BINARY

    output_dir = os.path.expanduser(output_dir)
    output_template = os.path.join(output_dir, "%(title)s.%(ext)s")

    ydl_opts = {
        "outtmpl": output_template,
        "quiet": True,
        "no_warnings": True,
        "socket_timeout": 30,
        "extractor_retries": 3,
        "retries": 3,
        "keepvideo": False,
        "ffmpeg_args": ["-threads", "1"],
    }
    if FFMPEG_BINARY:
        ydl_opts["ffmpeg_location"] = os.path.dirname(FFMPEG_BINARY)
    if audio_only:
        ydl_opts["format"] = "bestaudio/best"
        ydl_opts["postprocessors"] = [
            {
                "key": "FFmpegExtractAudio",
                "preferredcodec": "mp3",
            }
        ]
    elif format_str:
        ydl_opts["format"] = format_str
        ydl_opts["merge_output_format"] = "mp4"
    else:
        ydl_opts["format"] = "bestvideo[ext=mp4]+bestaudio[ext=m4a]/best[ext=mp4]/best"
    if progress_callback:
        ydl_opts["progress_hooks"] = [progress_callback]
    finalized: dict[str, str] = {}
    pp_hooks: list[Callable[[dict], None]] = [make_final_path_recorder(finalized)]
    if postprocessor_callback:
        pp_hooks.append(postprocessor_callback)
    ydl_opts["postprocessor_hooks"] = pp_hooks

    download_url = url or f"https://www.youtube.com/watch?v={yt_id}"
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(download_url, download=False)
        pred_path = ydl.prepare_filename(info)
    expected_ext = ".mp3" if audio_only else ".mp4"
    pred_path = os.path.splitext(pred_path)[0] + expected_ext
    if os.path.exists(pred_path):
        return (pred_path, True)
    with yt_dlp.YoutubeDL(ydl_opts) as ydl:
        info = ydl.extract_info(download_url, download=True)
    return (_resolve_output_path(ydl, info, finalized=finalized), False)


def _sanitize_script_opt(val: str) -> str:
    """Strip characters that would break mpv's --script-opts= parsing."""
    return (
        val.replace(",", "")
        .replace("=", "")
        .replace("[", "")
        .replace("]", "")
        .replace('"', "")
        .replace("\\", "")
    )


def _build_tracker_opts(
    *,
    yt_id: str,
    title: str | None = None,
    channel: str | None = None,
    uploader_id: str | None = None,
    permalink: str | None = None,
    source: str | None = None,
    media_type: str | None = None,
    season_number: int | None = None,
    episode_number: int | None = None,
) -> str | None:
    """Build the tracker_* --script-opts= value from whichever fields are present.

    Only *title* and *channel* are user-supplied strings and are sanitized
    against characters that break mpv's ``--script-opts=`` parser (``,``,
    ``=``, ``[``, ``]``, ``"``). All other keys are structured IDs or
    fixed enums and are passed through verbatim.
    """
    parts = []
    parts.append(f"tracker_yt_id={yt_id}")
    parts.append(f"tracker_dir={CONFIG_DIR}")
    if title:
        parts.append(f"tracker_title={_sanitize_script_opt(title)}")
    if channel:
        parts.append(f"tracker_channel={_sanitize_script_opt(channel)}")
    if uploader_id:
        parts.append(f"tracker_uploader_id={uploader_id}")
    if permalink:
        parts.append(f"tracker_permalink={permalink}")
    if source:
        parts.append(f"tracker_source={source}")
    if media_type:
        parts.append(f"tracker_media_type={media_type}")
    if season_number is not None:
        parts.append(f"tracker_season_number={season_number}")
    if episode_number is not None:
        parts.append(f"tracker_episode_number={episode_number}")
    if not parts:
        return None
    return "--script-opts=" + ",".join(parts)


def resolve_mpv_flags(source: str) -> list[str]:
    """Return base mpv flags for the given content source.

    ``--hwdec`` is the main discriminant: HLS-heavy sources (tv_movies)
    break with ``--hwdec=auto``, while YouTube benefits from GPU decoding.
    ``--profile=fast`` is per-source tunable but currently universal.

    Audio-only sources (soundcloud, radio) silently ignore ``--hwdec``.
    The caller appends ``--no-video`` when ``audio_only`` is set.
    """
    if source == "tv_movies":
        return ["--hwdec=no", "--profile=fast"]
    return ["--hwdec=auto", "--profile=fast"]


def _drain_mpv_stderr(proc: subprocess.Popen) -> None:
    """Read and log every line of mpv's stderr until EOF (no-op on None/DEVNULL).

    Runs on a daemon thread for long-lived processes so ``stderr=PIPE`` is
    consumed continuously and can never fill its buffer.
    """
    stream = getattr(proc, "stderr", None)
    if stream is None:
        return
    try:
        for line in iter(stream.readline, b""):
            stripped = line.decode("utf-8", errors="replace").rstrip()
            if stripped:
                logger.debug("mpv stderr: %s", stripped)
    except Exception:
        logger.debug("_drain_mpv_stderr: failed to read stderr")


def _start_mpv_stderr_drain(proc: subprocess.Popen) -> threading.Thread:
    """Spawn a daemon thread that drains ``proc.stderr`` into the debug log."""
    thread = threading.Thread(
        target=_drain_mpv_stderr, args=(proc,), daemon=True, name="mpv-stderr-drain"
    )
    thread.start()
    return thread


def play_video_async(
    yt_id: str = "",
    title: str = "",
    audio_only: bool = False,
    ytdl_format: str | None = None,
    start_pos: float | None = None,
    url: str | None = None,
    channel: str = "",
    uploader_id: str = "",
    permalink: str = "",
    source: str = "youtube",
    subs: list[str] | None = None,
    audio_urls: list[str] | None = None,
    referrer: str | None = None,
    stream_headers: dict | None = None,
    tracker_media_type: str | None = None,
    tracker_season_number: int | None = None,
    tracker_episode_number: int | None = None,
) -> MPVIPC | None:
    """Launch mpv in the background with an IPC socket for remote control.

    Creates a Unix-domain socket in /tmp/nyrx-mpv/ (UUID-named)
    and returns an MPVIPC handle that the TUI uses to poll position,
    seek, pause, etc.

    Args:
        yt_id: YouTube video ID.
        title: Video title (passed to tracker script).
        audio_only: Pass --no-video to mpv.
        ytdl_format: yt-dlp format string forwarded via --ytdl-format.
        start_pos: Seek to this position (seconds) on start.
        url: Stream URL; if omitted, built from yt_id as YouTube URL.
        channel: Channel/artist name (passed to tracker script).
        uploader_id: SoundCloud uploader ID (passed to tracker script).
        permalink: SoundCloud permalink (passed to tracker script).
        source: Content source (youtube, soundcloud, radio, tv_movies).
        subs: List of downloaded VTT file paths (passed as --sub-file).
        referrer: HTTP Referer header value.
        stream_headers: Extra HTTP headers for the stream.
        tracker_media_type: 'movie' or 'tv' for tv_movies source.
        tracker_season_number: Season number for series tracking.
        tracker_episode_number: Episode number for series tracking.

    Returns:
        MPVIPC controller instance, or None if the socket didn't appear.
    """
    if url is None:
        url = f"https://www.youtube.com/watch?v={yt_id}"
    IPC_SOCKET_DIR.mkdir(parents=True, exist_ok=True)
    socket_path = str(IPC_SOCKET_DIR / f"{uuid.uuid4().hex}.sock")

    cmd = [
        "mpv",
        url,
        f"--script={_TRACKER_SCRIPT}",
        *resolve_mpv_flags(source),
        f"--input-ipc-server={socket_path}",
        "--no-terminal",
    ]
    if audio_only:
        cmd.append("--no-video")
    if ytdl_format and source != "tv_movies":
        cmd.append(f"--ytdl-format={ytdl_format}")
    if start_pos is not None and start_pos > 0:
        cmd.append(f"--start={start_pos}")
    effective_referrer = referrer
    if stream_headers:
        for key, val in stream_headers.items():
            if key.lower() == "referer":
                if not effective_referrer:
                    effective_referrer = val
            else:
                cmd.append(f"--http-header-fields={key}: {val}")
    if effective_referrer:
        cmd.append(f"--referrer={effective_referrer}")
    if subs:
        for sub_path in subs:
            cmd.append(f"--sub-file={sub_path}")
    if audio_urls:
        for audio_url in audio_urls:
            cmd.append(f"--audio-file={audio_url}")

    opts = _build_tracker_opts(
        yt_id=yt_id,
        title=title or None,
        channel=channel or None,
        uploader_id=uploader_id or None,
        permalink=permalink or None,
        source=source or None,
        media_type=tracker_media_type,
        season_number=tracker_season_number,
        episode_number=tracker_episode_number,
    )
    if opts:
        cmd.append(opts)

    stderr_target = subprocess.PIPE if source == "tv_movies" else subprocess.DEVNULL
    try:
        proc = subprocess.Popen(
            cmd,
            stdin=subprocess.DEVNULL if audio_only else None,
            stdout=subprocess.DEVNULL,
            stderr=stderr_target,
        )
        if stderr_target == subprocess.PIPE:
            _start_mpv_stderr_drain(proc)
        found = False
        for _ in range(80):
            if os.path.exists(socket_path):
                time.sleep(0.3)
                if proc.poll() is not None:
                    logger.debug(
                        "play_video_async: socket appeared but process died url=%s", url
                    )
                    break
                found = True
                logger.debug("play_video_async: socket found url=%s", url)
                return MPVIPC(socket_path, proc)
            time.sleep(0.1)
        if not found:
            logger.debug("play_video_async: socket timeout url=%s", url)
        proc.terminate()
        try:
            proc.wait(timeout=3)
        except subprocess.TimeoutExpired:
            proc.kill()
            proc.wait()
        return None
    except Exception:
        logger.exception("play_video_async: mpv launch failed")
        return None


def clean_part_files(directory: str) -> int:
    """Remove stale ``.part`` files left behind by interrupted yt-dlp downloads.

    Args:
        directory: Directory to scan for .part files.

    Returns:
        Number of files cleaned.
    """
    count = 0
    try:
        for p in Path(directory).glob("*.part"):
            try:
                p.unlink()
                count += 1
            except Exception:
                logger.debug("clean_part_files: failed to unlink %s", p)
    except Exception:
        logger.exception("clean_part_files: glob failed for %s", directory)
    return count
