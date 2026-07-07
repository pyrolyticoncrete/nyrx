# SPDX-License-Identifier: AGPL-3.0-only

"""Hotswap: fetch, validate, and apply remote Lua config bundles.

Debug logging is available at each decision point; enable with
``logger.setLevel(logging.DEBUG)`` or the app's ``--debug`` flag.
"""

from __future__ import annotations

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import requests

logger = logging.getLogger(__name__)


@dataclass
class ApplyResult:
    success: bool
    written: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _cmp_ver(a: str, b: str) -> int:
    ta = tuple(int(x) for x in a.split("."))
    tb = tuple(int(x) for x in b.split("."))
    return (ta > tb) - (ta < tb)


def _sanitize_filename(name: str) -> str:
    if "/" in name or "\\" in name or ".." in name:
        raise ValueError(f"Invalid path component: {name!r}")
    return name


def _atomic_write(path: Path, data: bytes) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_bytes(data)
    tmp.replace(path)


# ---------------------------------------------------------------------------
# Fetch
# ---------------------------------------------------------------------------


def fetch_manifest(url: str) -> dict | None:
    try:
        resp = requests.get(url, timeout=15)
        resp.raise_for_status()
        data = resp.json()
        if not isinstance(data.get("version"), int):
            logger.debug("hotswap: manifest missing version int at %s", url)
            return None
        if not isinstance(data.get("files"), list):
            logger.debug("hotswap: manifest missing files list at %s", url)
            return None
        logger.debug(
            "hotswap: fetched manifest v%s (%d files) from %s",
            data["version"],
            len(data["files"]),
            url,
        )
        return data
    except Exception:
        logger.debug("hotswap: failed to fetch manifest from %s", url)
        return None


def fetch_file(url: str, expected_hash: str) -> bytes | None:
    try:
        resp = requests.get(url, timeout=30)
        resp.raise_for_status()
        actual = _sha256(resp.content)
        if actual != expected_hash:
            logger.error(
                "hotswap: hash mismatch for %s (expected=%s, actual=%s)",
                url,
                expected_hash,
                actual,
            )
            return None
        logger.debug("hotswap: downloaded %s (sha256=%s)", url, actual[:12])
        return resp.content
    except Exception:
        logger.debug("hotswap: failed to fetch %s", url)
        return None


# ---------------------------------------------------------------------------
# Version check
# ---------------------------------------------------------------------------


def _check_min_version(manifest: dict) -> str | None:
    from nyrx.config import APP_VERSION

    required = manifest.get("min_app_version", "0")
    if _cmp_ver(required, APP_VERSION) > 0:
        logger.debug(
            "hotswap: app v%s < required v%s, rejecting bundle",
            APP_VERSION,
            required,
        )
        return (
            f"Server configs require app v{required}, "
            f"please update nyrx (have v{APP_VERSION})"
        )
    logger.debug("hotswap: app v%s >= required v%s, ok", APP_VERSION, required)
    return None


# ---------------------------------------------------------------------------
# Apply bundle
# ---------------------------------------------------------------------------


def _manifest_file_name(entry: dict) -> str:
    raw = entry.get("path", "")
    return _sanitize_filename(Path(raw).name)


def _keys_json_path() -> Path:
    from nyrx.config import KEYS_PATH

    return KEYS_PATH


def apply_bundle(
    manifest: dict,
    lua_cache_dir: Path,
    dispatcher: Any = None,
) -> ApplyResult:
    result = ApplyResult(success=True)

    logger.debug(
        "hotswap: applying bundle v%s (%d files)",
        manifest.get("version"),
        len(manifest.get("files", [])),
    )

    # collectors for batched errors
    download_failures: list[str] = []
    invalid_entries: list[str] = []

    # 1) Min version check: reject early
    err = _check_min_version(manifest)
    if err:
        result.errors.append(err)
        result.success = False
        return result

    # 2) Build name→entry index, validate paths
    manifest_files: dict[str, dict] = {}
    for entry in manifest.get("files", []):
        try:
            name = _manifest_file_name(entry)
        except ValueError as exc:
            logger.debug("hotswap: invalid entry %s: %s", entry.get("path"), exc)
            invalid_entries.append(entry.get("path", "?"))
            continue
        manifest_files[name] = entry
    manifest_names = set(manifest_files.keys())

    # 3) Download loop: best-effort (continue on per-file failure)
    lua_cache_dir.mkdir(parents=True, exist_ok=True)
    for name, entry in manifest_files.items():
        dest = lua_cache_dir / name
        try:
            current_hash = _sha256(dest.read_bytes()) if dest.is_file() else ""
        except OSError:
            current_hash = ""

        if current_hash == entry["sha256"]:
            logger.debug("hotswap: %s: sha256 match, skipped", name)
            result.skipped.append(name)
            continue

        logger.debug(
            "hotswap: %s: sha256 mismatch (disk=%s, remote=%s), downloading",
            name,
            current_hash[:12],
            entry["sha256"][:12],
        )
        content = fetch_file(entry["url"], entry["sha256"])
        if content is None:
            download_failures.append(name)
            continue

        _atomic_write(dest, content)
        result.written.append(name)

    # 4) Delete orphans: any .lua on disk not in the manifest
    for f in sorted(lua_cache_dir.glob("*.lua")):
        if f.name not in manifest_names:
            logger.debug("hotswap: deleting orphan %s", f.name)
            f.unlink()
            result.deleted.append(f.name)

    # 5) TMDb keys: compare directly, no hash needed (keys are already strings)
    if "tmdb_keys" in manifest:
        keys_path = _keys_json_path()
        try:
            keys_path.parent.mkdir(parents=True, exist_ok=True)
            existing = json.loads(keys_path.read_text()) if keys_path.is_file() else {}
            if existing.get("tmdb_keys") != manifest["tmdb_keys"]:
                logger.debug(
                    "hotswap: tmdb_keys differ, updating (%d keys)",
                    len(manifest["tmdb_keys"]),
                )
                existing["tmdb_keys"] = manifest["tmdb_keys"]
                _atomic_write(keys_path, json.dumps(existing, indent=2).encode())
                from nyrx.sources.tv_movies.tmdb_cache import load_keys

                load_keys()
                result.written.append("tmdb_keys")
            else:
                logger.debug("hotswap: tmdb_keys unchanged, skipped")
                result.skipped.append("tmdb_keys")
        except Exception as exc:
            logger.debug("hotswap: failed to update TMDB keys: %s", exc)
            result.errors.append(f"Failed to update TMDB keys: {exc}")

    # 6) Batch errors
    if invalid_entries:
        result.errors.append(
            "Config manifest contains invalid entries: contact maintainer"
        )
    if download_failures:
        result.errors.append(
            f"{len(download_failures)} config file(s) failed to download, "
            "old versions kept"
        )

    # 7) Reload dispatcher metadata
    if dispatcher is not None:
        logger.debug("hotswap: reloading dispatcher configs")
        dispatcher.reload_configs()

    result.success = len(result.errors) == 0
    logger.debug(
        "hotswap: apply_bundle complete: written=%d, skipped=%d, errors=%d, deleted=%d",
        len(result.written),
        len(result.skipped),
        len(result.errors),
        len(result.deleted),
    )
    return result
