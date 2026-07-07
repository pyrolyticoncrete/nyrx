# SPDX-License-Identifier: AGPL-3.0-only

"""Application configuration constants.

Paths, API keys, quality presets, and app metadata used across the media aggregator.
"""

from __future__ import annotations

import json
import os
import shutil
import threading
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from typing import Literal

from platformdirs import user_cache_dir, user_config_dir, user_downloads_dir

CONFIG_DIR = Path(user_config_dir("nyrx"))
CACHE_DIR = Path(user_cache_dir("nyrx"))
KEYS_PATH = CONFIG_DIR / "keys.json"
SETTINGS_PATH = CONFIG_DIR / "config.json"

try:
    APP_VERSION = version("nyrx")
except PackageNotFoundError:
    APP_VERSION = "1.0.0"

# --- FFmpeg / FFprobe binaries ---
# Primary: static-ffmpeg (installed as project dependency via pyproject.toml).
# Fallback: system ffmpeg if static-ffmpeg import fails (broken install edge case).
try:
    from static_ffmpeg import run as _ffmpeg_run

    FFMPEG_BINARY: str = _ffmpeg_run.get_or_fetch_platform_executables_else_raise()[0]
except ImportError:
    FFMPEG_BINARY = shutil.which("ffmpeg") or ""

# --- Notification severity levels ---
SEVERITY_INFORMATION: Literal["information"] = "information"
SEVERITY_WARNING: Literal["warning"] = "warning"
SEVERITY_ERROR: Literal["error"] = "error"

# --- Notification timeouts (seconds) ---
TIMEOUT_CONFIRM = 2  # Queued, followed, unfollowed, synced
TIMEOUT_INFO = 3  # Standard informational messages
TIMEOUT_WARNING = 4  # Recoverable issues, missing data
TIMEOUT_ERROR = 8  # Hard failures, unrecoverable


_settings_lock = threading.Lock()


def get_config() -> dict:
    """Read config.json, return {} on missing or corrupt file."""
    if not SETTINGS_PATH.exists():
        return {}
    try:
        return json.loads(SETTINGS_PATH.read_text())
    except Exception:
        return {}


def update_config(**kwargs: object) -> None:
    """Thread-safe, atomic read-modify-write for config.json."""
    with _settings_lock:
        data = get_config()
        data.update(kwargs)
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        tmp = SETTINGS_PATH.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, indent=2))
        tmp.replace(SETTINGS_PATH)


def set_config(key: str, value: object) -> None:
    """Write a single key-value pair to config.json (thin wrapper)."""
    update_config(**{key: value})


SC_DB_PATH = CONFIG_DIR / "sc_data.db"

TV_DB_PATH = CONFIG_DIR / "tv_data.db"
WATCH_HISTORY_DB_PATH = CONFIG_DIR / "watch_history.db"
TRACKER_V4_PATH = CONFIG_DIR / "tracker_v4.jsonl"
TRACKER_OFFSET_PATH = CONFIG_DIR / "tracker_offset"
TV_THUMBS_DIR = CACHE_DIR / "tv_thumbs"
SC_THUMBS_DIR = CACHE_DIR / "sc_thumbnails"
TEMP_THUMBS = CACHE_DIR / "tmp_thumbs"

_download_env = os.getenv("NYRX_DOWNLOAD_DIR")
DEFAULT_DOWNLOAD_DIR = (
    Path(_download_env) if _download_env else Path(user_downloads_dir()) / "Media"
)
YT_SEARCH_LIMIT = 20

SOUNDCLOUD_SEARCH_LIMIT = 20

SC_CLIENT_ID_CACHE = CACHE_DIR / "sc_client_id"

LUA_CONFIG_DIR = CONFIG_DIR / "lua_configs"
LUA_CACHE_DIR = CACHE_DIR / "lua_configs"


def get_manifest_url() -> str:
    """Read the manifest URL from config.json (empty string if unset)."""
    return get_config().get("hotswap_url", "")


def set_manifest_url(url: str) -> None:
    """Write the manifest URL to config.json."""
    update_config(hotswap_url=url)


HOTSWAP_MANIFEST_URL = get_manifest_url()

SC_TRENDING_SLUGS = {
    "electronic": "Electronic",
    "techno": "Techno",
    "house": "House",
    "pop": "Pop",
    "soul": "Soul",
    "r-b": "R&B",
    "hip-hop-rap": "Hip Hop & Rap",
    "latin": "Latin",
    "folk": "Folk",
    "indie": "Indie",
    "reggae": "Reggae",
    "jazz": "Jazz",
    "rock-metal-punk": "Rock, Metal, Punk",
    "country": "Country",
}

SC_MAX_LIKED_TRACKS = 300

SC_TRENDING_SUFFIX_1_COUNTRIES = {"us", "gb"}
SC_TRENDING_SUFFIX_1_GENRES = {"electronic", "pop", "indie", "r-b"}

SC_COUNTRY_MAP = {
    "us": "United States",
    "de": "Germany",
    "gb": "United Kingdom",
    "fr": "France",
    "it": "Italy",
    "nl": "Netherlands",
    "be": "Belgium",
    "ie": "Ireland",
    "pl": "Poland",
    "ua": "Ukraine",
    "sa": "Saudi Arabia",
    "il": "Israel",
    "eg": "Egypt",
    "kr": "South Korea",
    "jp": "Japan",
    "in": "India",
    "pk": "Pakistan",
    "vn": "Vietnam",
    "id": "Indonesia",
    "au": "Australia",
    "br": "Brazil",
    "ca": "Canada",
    "amer": "Americas",
    "eunon": "Europe",
    "asse": "Southeast Asia",
    "aswe": "West Asia",
    "asce": "Central & East Asia",
    "ibe": "Spain & Portugal",
    "afrsu": "Sub-Saharan Africa",
    "afrno": "North Africa",
    "nord": "Nordics",
    "englobal": "Global",
}

# --- Responsive layout thresholds (cells) ---
# Screen flips to "wide" (sidebar visible) at/above this width.
WIDE_BREAKPOINT_WIDTH = 135
# SC home split + artist profile enter compact mode when BOTH apply:
COMPACT_MIN_WIDTH = 140
COMPACT_MAX_HEIGHT = 33  # strict-below (h < 33)
# TV series header hides at/below this height (no width gate: full-screen view).
TVS_COMPACT_MAX_HEIGHT = 29  # at-or-below (h <= 29)
# TV home hides POPULAR section + separator at/below this height.
TV_HOME_COMPACT_MAX_HEIGHT = 28  # at-or-below (h <= 28)
# Watchlist hides left poster panel at/below this height, shows compact strip.
WL_COMPACT_MAX_HEIGHT = 24  # at-or-below (h <= 24)
# Watchlist hides left poster panel at/below this width, shows compact strip.
WL_COMPACT_MAX_WIDTH = 179  # at-or-below (w <= 179)
# SC home hides FOLLOWING + LIKED (right column) at/below this width.
SCH_NARROW_MAX_WIDTH = 175
# Minimum usable terminal size ("hard floor"). Below either dimension the
# UI cuts data / has quirks; the app is locked until back at/above these.
MIN_TERMINAL_WIDTH = 165  # at-or-above (w >= 165)
MIN_TERMINAL_HEIGHT = 23  # at-or-above (h >= 23)

RADIO_CACHE_DAYS = 6
RADIO_INDEX_PAGE = 100
YT_QUALITY_PRESETS = [
    ("480p", 480, "bestvideo[height<=480]+bestaudio/best"),
    ("720p", 720, "bestvideo[height<=720]+bestaudio/best"),
    ("1080p", 1080, "bestvideo[height<=1080]+bestaudio/best"),
    ("2160p", 2160, "bestvideo[height<=2160]+bestaudio/best"),
    ("Best", None, "bestvideo+bestaudio/best"),
]
