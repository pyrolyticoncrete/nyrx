# SPDX-License-Identifier: AGPL-3.0-only

"""Server probe dispatcher.

Discovers Lua server configs, loads them through the sandbox, and
probes each server for a stream URL. Supports both auto mode (try
all servers, return first success) and manual mode (try a specific
server by name).

Configs are discovered from runtime directories in priority order:

  1. Cache dir (LUA_CACHE_DIR):
     managed by hotswap OTA updates
  2. User override dir (LUA_CONFIG_DIR):
     same filename overrides cache; extra filename = new server
  3. User disabled dir (``.../lua_configs/disabled/``): excluded
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from nyrx.config import LUA_CACHE_DIR, LUA_CONFIG_DIR

from .sandbox import Sandbox

logger = logging.getLogger(__name__)


def _write_if_missing(path: Path, content: str) -> None:
    if not path.exists():
        path.write_text(content)


CACHE_README = f"""\
DO NOT EDIT FILES IN THIS DIRECTORY

These Lua files are managed automatically by the app's hotswap
updater. They are overwritten every time an update is applied,
so any changes you make here will be silently lost.

If you want to customize or override one of these servers,
add your own copy to:

    {LUA_CONFIG_DIR}/

A file with the same name there will take priority over the one
in this folder.
"""

CONFIG_README = """\
YOUR LUA OVERRIDES FOLDER

Files you add here take priority over the app's built-in servers.

- To override a built-in server: add a file with the exact same
  name as the one you want to replace (e.g. 01.lua). Your version
  will be used instead of the default.
- To add a new server: add a file with any other name
  (e.g. 06-my-server.lua). It will never be touched or removed
  by app updates.
- To disable a server without deleting it: move its file into
  the "disabled" subfolder.

Nothing in this folder is ever modified or deleted automatically.
"""


class Dispatcher:
    """Discovers and orchestrates Lua server probes."""

    def __init__(
        self,
        lua_config_dir: str | Path | None = None,
        lua_cache_dir: str | Path | None = None,
    ) -> None:
        self._lua_config_dir = Path(lua_config_dir) if lua_config_dir else None
        self._lua_cache_dir = Path(lua_cache_dir) if lua_cache_dir else LUA_CACHE_DIR
        self._sandbox: Sandbox | None = None
        self._servers: list[dict[str, Any]] = []
        self._ensure_runtime_dirs()
        self._discover()

    # ------------------------------------------------------------------
    # Discovery
    # ------------------------------------------------------------------

    def _get_sandbox(self) -> Sandbox:
        if self._sandbox is None:
            self._sandbox = Sandbox.create()
        return self._sandbox

    def _ensure_runtime_dirs(self) -> None:
        """Create runtime directories and READMEs if missing.

        Runs unconditionally on every ``__init__`` (not gated by seeding).
        """
        self._lua_cache_dir.mkdir(parents=True, exist_ok=True)
        if self._lua_config_dir:
            self._lua_config_dir.mkdir(parents=True, exist_ok=True)
            (self._lua_config_dir / "disabled").mkdir(exist_ok=True)
        _write_if_missing(self._lua_cache_dir / "README.txt", CACHE_README)
        if self._lua_config_dir:
            _write_if_missing(self._lua_config_dir / "README.txt", CONFIG_README)

    def _load_one(self, sandbox: Sandbox, path: Path, loaded: dict[str, int]) -> None:
        """Load a single Lua config file and add/override in ``self._servers``."""
        try:
            g = sandbox.load_script(str(path))
            raw = g["SERVER"]
            if raw is None:
                return
            meta = {k: raw[k] for k in raw} if raw else {}
            name = meta.get("name", path.stem)
            entry = {
                "name": name,
                "display_name": meta.get("display_name", name.capitalize()),
                "requires_playwright": meta.get("requires_playwright", False),
                "has_subs": meta.get("has_subs", True),
                "has_audio": meta.get("has_audio", True),
                "notes": meta.get("notes", ""),
                "filepath": str(path),
            }
            if name in loaded:
                self._servers[loaded[name]] = entry
            else:
                loaded[name] = len(self._servers)
                self._servers.append(entry)
        except Exception as exc:
            logger.warning("dispatcher: failed to load Lua config %s: %s", path, exc)

    def _discover(self) -> None:
        self._servers = []
        sandbox = self._get_sandbox()
        loaded: dict[str, int] = {}

        # 1) Load hotswap-managed files (cache dir)
        for f in sorted(self._lua_cache_dir.glob("*.lua")):
            self._load_one(sandbox, f, loaded)

        # 2) Load user overrides (same name = overrides cache)
        if self._lua_config_dir and self._lua_config_dir.is_dir():
            for f in sorted(self._lua_config_dir.glob("*.lua")):
                self._load_one(sandbox, f, loaded)

        # 3) Remove disabled servers
        if self._lua_config_dir:
            disabled_dir = self._lua_config_dir / "disabled"
            if disabled_dir.is_dir():
                disabled_names: set[str] = set()
                for f in disabled_dir.glob("*.lua"):
                    try:
                        g = sandbox.load_script(str(f))
                        raw = g["SERVER"]
                        if raw is not None:
                            meta = {k: raw[k] for k in raw} if raw else {}
                            disabled_names.add(meta.get("name", f.stem))
                    except Exception:
                        disabled_names.add(f.stem)
                self._servers = [
                    s for s in self._servers if s["name"] not in disabled_names
                ]

    def reload_configs(self) -> None:
        """Re-scan all config dirs (cache + user override + disabled).

        Safe to call at any time: ``probe()`` reads Lua from disk per-call
        so the only cached state being refreshed is the server list metadata
        (names, display names, notes).
        """
        self._discover()

    # ------------------------------------------------------------------
    # Query
    # ------------------------------------------------------------------

    @property
    def server_names(self) -> list[str]:
        return [s["name"] for s in self._servers]

    def list_servers(self) -> list[dict[str, Any]]:
        return list(self._servers)

    def get_server(self, name: str) -> dict | None:
        for s in self._servers:
            if s["name"] == name:
                return dict(s)
        return None

    # ------------------------------------------------------------------
    # Probe
    # ------------------------------------------------------------------

    def probe(self, params: dict, server_name: str | None = None) -> dict | None:
        """Probe one or all servers for a stream URL.

        Args:
            params: Dict with keys ``tmdb_id``, ``media_type`` (movie/tv),
                    optionally ``season``, ``episode``.
            server_name: If given, try only this server. If None, try
                         all discovered servers in order and return the
                         first successful result.

        Returns:
            Result dict with ``stream_url``, ``format``, ``resolution``,
            ``server``, and optional ``subs``, ``audio``, or ``None``.
        """
        if server_name:
            candidates = [s for s in self._servers if s["name"] == server_name]
        else:
            candidates = self._servers

        sandbox = self._get_sandbox()

        for s in candidates:
            try:
                g = sandbox.load_script(s["filepath"])
                result = sandbox.call_probe(g, params)
                if result is None:
                    logger.debug("dispatcher: no stream from %s", s["name"])
                    continue
                stream_url = result.get("stream_url")
                if not stream_url:
                    logger.debug("dispatcher: no stream from %s", s["name"])
                    continue
                result["server"] = s["name"]
                result["server_display"] = s["display_name"]
                return result
            except Exception as exc:
                logger.warning(
                    "dispatcher: probe failed for server %s: %s", s["name"], exc
                )
                continue

        return None
