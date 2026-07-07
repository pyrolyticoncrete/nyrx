# SPDX-License-Identifier: AGPL-3.0-only

"""TV/Movies source: TMDB search + server probing for stream playback."""

from __future__ import annotations

import logging
import os
import tempfile
from typing import Any

import requests

from nyrx.config import LUA_CACHE_DIR, LUA_CONFIG_DIR
from nyrx.sources import Source

from . import tmdb_cache
from .dispatcher import Dispatcher

logger = logging.getLogger(__name__)

MAX_SUBS = 30


def _download_one(
    entry: Any, headers: dict, tmpdir: str, idx: int
) -> tuple[str, str] | None:
    if isinstance(entry, dict):
        lang = entry.get("lang", "sub")
        url = entry.get("url")
    elif isinstance(entry, str):
        lang = "sub"
        url = entry
    else:
        return None
    if not url:
        return None
    try:
        resp = requests.get(url, headers=headers, timeout=15)
        if not resp.ok:
            return None
        text = resp.text
        lines = text.split("\n")
        first_line = next((line.strip() for line in lines if line.strip()), "")
        fpath = os.path.join(tmpdir, f"subs_{lang}_{idx}.vtt")

        if first_line.startswith("WEBVTT"):
            with open(fpath, "wb") as f:
                f.write(resp.content)
            return fpath, lang

        if first_line.startswith("#EXT"):
            segment_urls = [
                line.strip()
                for line in lines
                if line.strip() and not line.strip().startswith("#")
            ]
            if not segment_urls:
                return None
            first = True
            with open(fpath, "w") as f:
                for seg_url in segment_urls:
                    try:
                        seg = requests.get(seg_url, headers=headers, timeout=15)
                        if not seg.ok:
                            continue
                        content = seg.text
                        if not first:
                            content = "\n".join(
                                line
                                for line in content.split("\n")
                                if not line.strip().startswith("WEBVTT")
                                and not line.strip().startswith("X-TIMESTAMP-MAP")
                            )
                        f.write(content)
                        if not content.endswith("\n"):
                            f.write("\n")
                        first = False
                    except Exception:
                        logger.debug(
                            "_download_one: subtitle segment download failed for %s",
                            seg_url,
                        )
                        continue
            return (fpath, lang) if not first else None

        with open(fpath, "wb") as f:
            f.write(resp.content)
        return fpath, lang
    except Exception:
        logger.debug(
            "_download_one: failed to download subtitle entry lang=%s idx=%d", lang, idx
        )
        return None


def _download_subtitles(
    entries: list, referrer: str = "", extra_headers: dict | None = None
) -> tuple[str | None, list[tuple[str, str]]]:
    if not entries:
        return None, []
    headers = {
        "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    }
    if referrer:
        headers["Referer"] = referrer
    if extra_headers:
        headers.update(extra_headers)

    tmpdir = tempfile.mkdtemp(prefix="subs_")
    from concurrent.futures import ThreadPoolExecutor, as_completed

    paths: list[tuple[str, str]] = []
    with ThreadPoolExecutor(max_workers=8) as pool:
        futures = {
            pool.submit(_download_one, entry, headers, tmpdir, i): entry
            for i, entry in enumerate(entries[:MAX_SUBS])
        }
        for f in as_completed(futures):
            result = f.result()
            if result:
                paths.append(result)
    return tmpdir, paths


class TVMoviesSource(Source):
    def __init__(self) -> None:
        tmdb_cache.load_keys()
        import threading

        threading.Thread(target=tmdb_cache.refresh_proxy, daemon=True).start()
        self._dispatcher = Dispatcher(
            lua_config_dir=LUA_CONFIG_DIR,
            lua_cache_dir=LUA_CACHE_DIR,
        )
        self._server_mode: str = "auto"
        self._search_filter: str = "all"

    @property
    def name(self) -> str:
        return "TV/Movies"

    @property
    def icon(self) -> str:
        return "\u25b6"

    def handles_url(self, url: str) -> bool:
        return False

    @property
    def server_names(self) -> list[str]:
        return self._dispatcher.server_names

    def reload_configs(self) -> None:
        """Re-discover Lua configs from cache + user override dirs."""
        self._dispatcher.reload_configs()

    @property
    def server_mode(self) -> str:
        return self._server_mode

    def search(self, query: str, limit: int = 20) -> list[dict]:
        media_type = None if self._search_filter == "all" else self._search_filter
        pages_needed = (limit + 19) // 20
        pages_needed = min(pages_needed, 5)

        all_results: list[dict] = []
        for p in range(1, pages_needed + 1):
            page_results = tmdb_cache.search(query, media_type=media_type, page=p)
            if not page_results:
                break
            all_results.extend(page_results)

        out = []
        for r in all_results[:limit]:
            tmdb_id = r["tmdb_id"]
            poster = r.get("poster", "")
            thumb = f"https://image.tmdb.org/t/p/w342{poster}" if poster else ""
            out.append(
                {
                    "yt_id": f"tmdb_{tmdb_id}",
                    "title": r["title"],
                    "channel": "",
                    "duration": 0,
                    "views": 0,
                    "thumbnail_url": thumb,
                    "url": "",
                    "tmdb_id": tmdb_id,
                    "media_type": r["media_type"],
                    "year": r.get("year", ""),
                    "rating": r.get("rating", 0),
                    "vote_count": r.get("vote_count", 0),
                    "release_date": r.get("release_date", ""),
                    "genre_ids": r.get("genre_ids", []),
                    "overview": r.get("overview", ""),
                    "poster": poster,
                    "source": "tv_movies",
                }
            )
        return out

    def fetch_metadata(self, url: str) -> dict | None:
        return None

    def download_params(self, data: dict) -> dict | None:
        tmdb_id = data.get("tmdb_id")
        if not tmdb_id:
            yt_id = data.get("yt_id", "")
            if yt_id.startswith("tmdb_"):
                tmdb_id = int(yt_id[5:])
        if not tmdb_id:
            return None
        return {
            "yt_id": data.get("yt_id", f"tmdb_{tmdb_id}"),
            "title": data.get("title", ""),
            "source": "tv_movies",
            "tmdb_id": tmdb_id,
            "media_type": data.get("media_type", "movie"),
            "season": data.get("season") or data.get("season_number"),
            "episode": data.get("episode") or data.get("episode_number"),
            "series_title": data.get("series_title"),
            "year": data.get("year"),
            "channel": data.get("channel", ""),
        }

    def play_params(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
        quality_height: int | None = None,
    ) -> dict[str, Any]:
        _ = audio_only, ytdl_format
        tmdb_id = data.get("tmdb_id")
        if not tmdb_id:
            yt_id = data.get("yt_id", "")
            if yt_id.startswith("tmdb_"):
                tmdb_id = int(yt_id[5:])
        if not tmdb_id:
            return {"yt_id": data.get("yt_id", ""), "title": data.get("title", "")}

        media_type = data.get("media_type", "movie")
        quality = quality_height
        season = data.get("season") or data.get("season_number")
        episode = data.get("episode") or data.get("episode_number")

        probe_params: dict[str, Any] = {
            "tmdb_id": tmdb_id,
            "media_type": media_type,
        }
        if quality is not None:
            probe_params["quality"] = quality
        if media_type == "tv":
            probe_params["season"] = season or 1
            probe_params["episode"] = episode or 1

        queued = data.get("_queued_server_mode")
        if queued is not None:
            server_name = None if queued == "auto" else queued
        else:
            server_name = None if self._server_mode == "auto" else self._server_mode

        if not self._dispatcher.server_names:
            return {
                "yt_id": data.get("yt_id", ""),
                "title": data.get("title", ""),
                "_no_configs": True,
            }

        result = self._dispatcher.probe(probe_params, server_name=server_name)
        if not result:
            return {"yt_id": data.get("yt_id", ""), "title": data.get("title", "")}

        stream_url = result["stream_url"]
        stream_headers = result.get("stream_headers") or {}
        sub_headers = result.get("sub_headers") or {}
        referrer = stream_headers.get("Referer", "")

        audio_urls = result.get("audio_urls") or []

        subs = result.get("subs") or []
        tmpdir: str | None = None
        vtt_paths: list[str] = []
        try:
            tmpdir, sub_tuples = _download_subtitles(subs, referrer, sub_headers)
            vtt_paths = [p for p, _ in sub_tuples]
        except Exception:
            tmpdir = None
            vtt_paths = []

        if "yt_id" not in data:
            logger.debug(
                "play_params: yt_id MISSING in data before return! keys=%s",
                list(data.keys()),
            )
        return {
            "yt_id": data.get("yt_id", ""),
            "title": data.get("title", ""),
            "url": stream_url,
            "subs": vtt_paths,
            "audio_urls": audio_urls,
            "_subs_tmpdir": tmpdir,
            "referrer": referrer or None,
            "stream_headers": stream_headers or None,
            "start_pos": start_pos,
            "source": "tv_movies",
            "tracker_media_type": media_type,
            "tracker_season_number": season,
            "tracker_episode_number": episode,
            "channel": "",
        }

    def play(
        self,
        data: dict,
        audio_only: bool = False,
        ytdl_format: str | None = None,
        start_pos: float | None = None,
    ) -> Any:
        return None

    def get_commands(self, **context: Any) -> list[tuple[str, str, str | None]]:
        quality = context.get("quality", "1080p")
        return [
            ("Set default streaming quality", "action_set_quality", quality),
            ("Connect TMDB API key", "action_set_tmdb_key", None),
            ("Refresh Lua plugins", "action_check_updates", None),
        ]

    def current_server_display(self) -> str:
        if not self._dispatcher.server_names:
            return "No Server"
        if self._server_mode == "auto":
            return "Auto"
        srv = self._dispatcher.get_server(self._server_mode)
        return srv["display_name"] if srv else self._server_mode.capitalize()

    def cycle_server(self) -> str:
        names = self._dispatcher.server_names
        if not names:
            self._server_mode = "auto"
            return "__no_configs__"
        if self._server_mode == "auto":
            self._server_mode = names[0]
        else:
            try:
                idx = names.index(self._server_mode)
                if idx + 1 >= len(names):
                    self._server_mode = "auto"
                else:
                    self._server_mode = names[idx + 1]
            except ValueError:
                self._server_mode = "auto"
        return self._server_mode
