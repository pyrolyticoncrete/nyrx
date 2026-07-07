# SPDX-License-Identifier: AGPL-3.0-only

"""Python helpers registered into the Lua sandbox.

Extracted from probe_providers.py. These functions handle the heavy
lifting (HTTP, HLS parsing, PoW, base64, crypto) that Lua scripts
call through the sandbox bridge.
"""

from __future__ import annotations

import hashlib
import logging
import re
import time
from typing import Any

import requests

logger = logging.getLogger(__name__)

HTTP_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/150 Safari/537.36"
    ),
    "Accept": "application/json, text/javascript, */*; q=0.01",
    "Accept-Language": "en-US,en;q=0.9",
}

PLAYWRIGHT_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/148.0.0.0 Safari/537.36"
)

# ---------------------------------------------------------------------------
# HTTP helpers
# ---------------------------------------------------------------------------


def http_get(url: str, headers: dict | None = None, timeout: int = 15) -> dict:
    try:
        r = requests.get(url, headers=headers or HTTP_HEADERS, timeout=timeout)
        return {
            "status": r.status_code,
            "headers": dict(r.headers),
            "body": r.text,
            "ok": r.ok,
            "url": r.url,
        }
    except Exception as e:
        return {"status": 0, "error": str(e), "ok": False, "url": url}


def http_post(
    url: str,
    json_data: dict | None = None,
    headers: dict | None = None,
    timeout: int = 15,
) -> dict:
    try:
        r = requests.post(
            url, json=json_data or {}, headers=headers or HTTP_HEADERS, timeout=timeout
        )
        return {
            "status": r.status_code,
            "headers": dict(r.headers),
            "body": r.text,
            "ok": r.ok,
            "url": r.url,
        }
    except Exception as e:
        return {"status": 0, "error": str(e), "ok": False, "url": url}


# ---------------------------------------------------------------------------
# HLS parsing
# ---------------------------------------------------------------------------


def _fetch_text(url: str, headers: dict | None = None, timeout: int = 10) -> str | None:
    try:
        resp = requests.get(url, headers=headers or HTTP_HEADERS, timeout=timeout)
        resp.raise_for_status()
        return resp.text
    except Exception:
        logger.debug("_fetch_text: request failed for url=%s", url)
        return None


def _parse_hls_master(text: str) -> dict[str, Any]:
    renditions: list[dict[str, Any]] = []
    audio_langs = list(
        dict.fromkeys(
            m.group(1) for m in re.finditer(r'TYPE=AUDIO.*?LANGUAGE="([^"]+)"', text)
        )
    )
    sub_langs = list(
        dict.fromkeys(
            m.group(1)
            for m in re.finditer(r'TYPE=SUBTITLES.*?LANGUAGE="([^"]+)"', text)
        )
    )

    lines = text.strip().split("\n")
    i = 0
    while i < len(lines):
        line = lines[i]
        if line.startswith("#EXT-X-STREAM-INF:"):
            bw = 0
            resolution = None
            codec = None
            for attr in line.split(","):
                if "BANDWIDTH=" in attr:
                    try:
                        bw = int(attr.split("=")[1])
                    except ValueError:
                        pass
                elif "RESOLUTION=" in attr:
                    resolution = attr.split("=")[1]
                elif "CODECS=" in attr:
                    codecs_val = attr.split("=", 1)[1].strip('"')
                    for c in codecs_val.split(","):
                        c = c.strip()
                        if c.startswith("avc"):
                            codec = "h264"
                        elif c.startswith("hev"):
                            codec = "h265"
            i += 1
            if i < len(lines) and lines[i] and not lines[i].startswith("#"):
                renditions.append(
                    {
                        "bandwidth": bw,
                        "resolution": resolution,
                        "codec": codec,
                    }
                )
        i += 1

    renditions.sort(key=lambda x: int(x["bandwidth"]), reverse=True)
    best = renditions[0] if renditions else {}
    return {
        "format": "hls",
        "resolution": best.get("resolution"),
        "video_codec": best.get("codec"),
        "audio_languages": audio_langs,
        "subtitle_languages": sub_langs,
        "stream_count": len(renditions),
    }


def hls_get_variants(
    stream_url: str, headers: dict | None = None
) -> list[dict[str, Any]]:
    """Fetch a master m3u8 and return list of {url, resolution (int), bandwidth}.

    Returns empty list if the URL isn't a master playlist.
    """
    from urllib.parse import urljoin

    body = _fetch_text(stream_url, headers=headers)
    if not body or not body.startswith("#EXTM3U") or "#EXT-X-STREAM-INF:" not in body:
        return []

    variants: list[dict[str, Any]] = []
    lines = body.strip().split("\n")
    for i, line in enumerate(lines):
        line = line.strip()
        if not line.startswith("#EXT-X-STREAM-INF:"):
            continue

        bw = 0
        resolution: Any = None
        attrs = line[len("#EXT-X-STREAM-INF:") :]
        for attr in attrs.split(","):
            attr = attr.strip()
            if attr.startswith("BANDWIDTH="):
                try:
                    bw = int(attr.split("=", 1)[1])
                except ValueError:
                    pass
            elif attr.startswith("RESOLUTION="):
                res_str = attr.split("=", 1)[1]
                parts = res_str.lower().split("x")
                if len(parts) == 2:
                    try:
                        resolution = int(parts[1])
                    except ValueError:
                        resolution = res_str
                else:
                    resolution = res_str

        if i + 1 >= len(lines):
            continue
        variant_url = lines[i + 1].strip()
        if not variant_url or variant_url.startswith("#"):
            continue
        if not variant_url.startswith("http"):
            variant_url = urljoin(stream_url, variant_url)

        variants.append(
            {
                "url": variant_url,
                "resolution": resolution,
                "bandwidth": bw,
            }
        )

    return variants


def hls_analyze(stream_url: str, headers: dict | None = None) -> dict[str, Any]:
    result: dict[str, Any] = {
        "format": None,
        "resolution": None,
        "video_codec": None,
        "audio_languages": [],
        "subtitle_languages": [],
        "notes": None,
    }

    ext = (
        stream_url.rsplit(".", 1)[-1].lower().split("?")[0].split("#")[0]
        if "." in stream_url
        else ""
    )
    if ext in ("m3u8",):
        result["format"] = "hls"
        body = _fetch_text(stream_url, headers=headers)
        if body and body.startswith("#EXTM3U"):
            if "#EXT-X-STREAM-INF:" in body:
                info = _parse_hls_master(body)
                result.update(info)
                return result
            elif "#EXTINF" in body:
                result["notes"] = "media playlist (no master)"
                return result
        result["notes"] = "could not fetch playlist"
    elif ext in ("mp4", "mkv", "webm", "avi"):
        result["format"] = ext
    else:
        body = _fetch_text(stream_url, headers=headers)
        if body and body.startswith("#EXTM3U"):
            result["format"] = "hls"
            if "#EXT-X-STREAM-INF:" in body:
                info = _parse_hls_master(body)
                result.update(info)
            elif "#EXTINF" in body:
                result["notes"] = "media playlist (no master)"
            return result
        result["format"] = ext if ext else "?"
        if ext not in ("", "?"):
            result["notes"] = f"unrecognised .{ext}"

    return result


# ---------------------------------------------------------------------------
# Mapple PoW
# ---------------------------------------------------------------------------


def pow_find_nonce(
    input_prefix: str, difficulty: int, max_attempts: int = 0
) -> int | None:
    nb, nr = difficulty // 8, difficulty % 8
    nonce = 0
    while max_attempts == 0 or nonce < max_attempts:
        h = hashlib.sha256((input_prefix + str(nonce)).encode()).digest()
        ok = all(h[i] == 0 for i in range(nb))
        if ok and nr:
            ok = (h[nb] & (0xFF << (8 - nr))) == 0
        if ok:
            return nonce
        nonce += 1
    return None


# ---------------------------------------------------------------------------
# Custom base64 decoder (alphabet provided by Lua)
# ---------------------------------------------------------------------------


def custom_b64decode(input_str: str, alphabet: str, pad_char: str = "=") -> str:
    reverse = {c: i for i, c in enumerate(alphabet)}
    padded = input_str
    mod = len(padded) % 4
    if mod != 0:
        padded += pad_char * (4 - mod)
    bytes_out = []
    for i in range(0, len(padded), 4):
        chunk = padded[i : i + 4]
        c0 = reverse.get(chunk[0], 64)
        c1 = reverse.get(chunk[1], 64)
        c2 = 64 if chunk[2] == pad_char else reverse.get(chunk[2], 64)
        c3 = 64 if chunk[3] == pad_char else reverse.get(chunk[3], 64)
        bytes_out.append(((c0 << 2) | (c1 >> 4)) & 0xFF)
        if c2 != 64:
            bytes_out.append((((c1 & 0x0F) << 4) | (c2 >> 2)) & 0xFF)
        if c3 != 64:
            bytes_out.append((((c2 & 0x03) << 6) | c3) & 0xFF)
    return bytes(bytes_out).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# Crypto primitives
# ---------------------------------------------------------------------------


def sha256(input_str: str) -> str:
    return hashlib.sha256(input_str.encode()).hexdigest()


def hmac_sha256(key: str, message: str) -> str:
    # PIN: this is deliberately NOT RFC-2104 HMAC. It is a plain
    # sha256(key + message) concatenation, which is the contract the
    # Lua probe scripts and sandbox rely on for challenge-response.
    # Do NOT "fix" it to hmac.new()
    # That would silently break server auth.
    return hashlib.sha256((key + message).encode()).hexdigest()


def md5(input_str: str) -> str:
    return hashlib.md5(input_str.encode()).hexdigest()


def random_bytes(n: int) -> str:
    return hashlib.sha256(str(time.time_ns()).encode()).hexdigest()[:n]


def aes_decrypt(ciphertext_b64: str, key_hex: str, iv_hex: str) -> str:
    import base64

    from Cryptodome.Cipher import AES

    ciphertext = base64.b64decode(ciphertext_b64)
    key = bytes.fromhex(key_hex)
    iv = bytes.fromhex(iv_hex)
    cipher = AES.new(key, AES.MODE_CBC, iv=iv)
    return cipher.decrypt(ciphertext).decode("utf-8", errors="replace")


# ---------------------------------------------------------------------------
# URL helpers
# ---------------------------------------------------------------------------


def url_parse(url_str: str) -> dict:
    from urllib.parse import parse_qs, urlparse

    parsed = urlparse(url_str)
    return {
        "scheme": parsed.scheme,
        "netloc": parsed.netloc,
        "path": parsed.path,
        "params": parsed.params,
        "query": parsed.query,
        "fragment": parsed.fragment,
        "query_dict": {
            k: v[0] if len(v) == 1 else v for k, v in parse_qs(parsed.query).items()
        },
    }


def url_build(
    scheme: str = "",
    netloc: str = "",
    path: str = "",
    query: str = "",
    fragment: str = "",
) -> str:
    from urllib.parse import urlunparse

    return urlunparse((scheme, netloc, path, "", query, fragment))


def url_encode_params(params: dict) -> str:
    from urllib.parse import urlencode

    return urlencode(params)
