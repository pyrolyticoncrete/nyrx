# SPDX-License-Identifier: AGPL-3.0-only

"""Discover the current TMDB proxy URL from player.videasy.to JS bundles.

The proxy domain changes periodically (e.g. db.videasy.to -> db.speedracelight.com).
This module dynamically extracts the current base URL from the Next.js movie page
chunk, keeping ``~/.config/nyrx/keys.json`` in sync without hardcoding a
single domain.
"""

from __future__ import annotations

import json
import logging
import re
import threading
import time
from typing import Any

import requests

from nyrx.config import KEYS_PATH

logger = logging.getLogger(__name__)

_HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36",
    "Referer": "https://player.videasy.to/",
}

_LOCK = threading.Lock()


def discover_proxy() -> str | None:
    """Fetch the current TMDB proxy URL from player.videasy.to's JS bundle.

    Returns the base URL (e.g. ``https://db.speedracelight.com/3``) or
    ``None`` if discovery fails for any reason.
    """
    try:
        html = requests.get(
            "https://player.videasy.to/movie/550",
            headers=_HEADERS,
            timeout=15,
        ).text

        m = re.search(
            r'<script[^>]+src="(/[^"]+/pages/movie/[^"]+\.js)"',
            html,
        )
        if not m:
            logger.debug("proxy_discovery: movie chunk not found in HTML")
            return None

        chunk_url = f"https://player.videasy.to{m.group(1)}"
        js = requests.get(chunk_url, headers=_HEADERS, timeout=15).text

        m = re.search(r'baseURL:"([^"]+)"', js)
        if not m:
            logger.debug("proxy_discovery: baseURL not found in JS chunk")
            return None

        return m.group(1)
    except Exception as exc:
        logger.debug(f"proxy_discovery: failed, {exc}")
        return None


def update_proxy_config() -> bool:
    """Discover proxy URL and persist to ``keys.json`` if changed.

    Returns ``True`` if the config was updated, ``False`` otherwise.
    Thread-safe via a module-level lock.
    """
    discovered = discover_proxy()
    if not discovered:
        return False

    with _LOCK:
        try:
            KEYS_PATH.parent.mkdir(parents=True, exist_ok=True)
            config: dict[str, Any] = (
                json.loads(KEYS_PATH.read_text()) if KEYS_PATH.is_file() else {}
            )
            config["proxy_last_checked"] = time.time()
            changed = config.get("tmdb_proxy") != discovered
            if changed:
                config["tmdb_proxy"] = discovered
                logger.info(f"proxy_discovery: updated tmdb_proxy to {discovered}")
            KEYS_PATH.write_text(json.dumps(config, indent=2) + "\n")
            return changed
        except Exception as exc:
            logger.debug(f"proxy_discovery: failed to write config, {exc}")
            return False
