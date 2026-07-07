# SPDX-License-Identifier: AGPL-3.0-only

"""Lua sandbox for executing server probe scripts.

Wraps lupa with locked-down globals and registered helper modules.
Each server is a .lua file defining ``probe(params)``. Python calls
``probe()`` through the sandbox bridge.

Locked-down (set to nil): os, io, require, debug, package, python,
loadfile, dofile.

Each config runs in its own fresh environment (``_build_env``) built as a
copy of the shared runtime globals, so no config can see or mutate another
config's globals or module tables.

Registered modules:
  - ``http``: get / post
   - ``playwright``: navigate / on_response / wait_for / wait / wait_for_selector / click / click_text / hover / evaluate / close / start / stop
   - ``crypto``: sha256 / hmac_sha256 / md5 / random_bytes / aes_decrypt (aes_gcm stubbed)
   - ``hls``: analyze
   - ``base64``: decode / encode / decode_custom
   - ``pow``: solve
  - ``json``: decode / encode
  - ``url``: parse / build / encode_params
  - ``log``: info / warn / error

"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from lupa import LuaRuntime

from nyrx.config import CONFIG_DIR

from . import helpers

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Thread-safe LuaRuntime factory
# ---------------------------------------------------------------------------

_lua_local = threading.local()

# Name of the config currently being probed on this thread, used to scope
# secrets access to keys under ``<server_name>_``.
_current_server = threading.local()


def _init_runtime(lua: LuaRuntime) -> None:
    g = lua.globals()
    for name in (
        "os",
        "io",
        "require",
        "debug",
        "package",
        "python",
        "loadfile",
        "dofile",
    ):
        g[name] = None
    _register_http(lua)
    _register_playwright(lua)
    _register_crypto(lua)
    _register_hls(lua)
    _register_base64(lua)
    _register_pow(lua)
    _register_json(lua)
    _register_url(lua)
    _register_log(lua)
    _register_secrets(lua)
    _register_quality(lua)


def _get_lua() -> LuaRuntime:
    if not hasattr(_lua_local, "lua"):
        _lua_local.lua = LuaRuntime(unpack_returned_tuples=True)
        _init_runtime(_lua_local.lua)
    return _lua_local.lua


# ---------------------------------------------------------------------------
# Deep conversion helpers (Lua table -> Python)
# ---------------------------------------------------------------------------


def _deep_from_lua(val: Any) -> Any:
    """Recursively convert Lua tables to Python dicts/lists."""
    if val is None:
        return None
    try:
        d = dict(val)
        if not d:
            return d
        keys = list(d.keys())
        if (
            all(isinstance(k, int) for k in keys)
            and min(keys) == 1
            and sorted(keys) == list(range(1, len(keys) + 1))
        ):
            return [_deep_from_lua(d[i]) for i in range(1, len(keys) + 1)]
        return {_deep_from_lua(k): _deep_from_lua(v) for k, v in d.items()}
    except (TypeError, ValueError):
        return val


# ---------------------------------------------------------------------------
# Per-config environment helpers
# ---------------------------------------------------------------------------

# Mutable module/stdlib tables get a fresh shallow copy per config so one
# config cannot rebind e.g. http.get or string.find for another.
_FRESH_COPY_TABLES = (
    "http",
    "playwright",
    "crypto",
    "hls",
    "base64",
    "pow",
    "json",
    "url",
    "log",
    "secrets",
    "quality",
    "string",
    "table",
    "math",
)


def _build_env(lua: LuaRuntime) -> Any:
    """Build a fresh per-config environment from the runtime globals.

    Copies every global by reference (so the full stdlib stays available),
    except:

    - ``_G`` -> the env itself, so ``_G`` writes stay config-scoped.
    - ``load`` -> compiling against the shared globals from inside a config
      would defeat isolation.
    - ``__nyrx_*`` -> the temp slots used to shuttle values in/out.

    The mutable module/stdlib tables in ``_FRESH_COPY_TABLES`` are copied
    into a brand-new table so config A rebinding ``http.get`` cannot affect
    config B.
    """
    g = lua.globals()
    env = lua.table()
    fresh = lua.table()
    for name in _FRESH_COPY_TABLES:
        fresh[name] = True
    g["__nyrx_src"] = g
    g["__nyrx_env"] = env
    g["__nyrx_fresh"] = fresh
    try:
        lua.execute(
            "for k, v in pairs(__nyrx_src) do\n"
            "  if k ~= '_G'\n"
            "     and k ~= 'load'\n"
            "     and string.sub(tostring(k), 1, 7) ~= '__nyrx' then\n"
            "    if __nyrx_fresh[k] then\n"
            "      local t = {}\n"
            "      for kk, vv in pairs(v) do t[kk] = vv end\n"
            "      __nyrx_env[k] = t\n"
            "    else\n"
            "      __nyrx_env[k] = v\n"
            "    end\n"
            "  end\n"
            "end\n"
        )
    finally:
        g["__nyrx_src"] = None
        g["__nyrx_env"] = None
        g["__nyrx_fresh"] = None
    env["_G"] = env
    return env


def _run_script(lua: LuaRuntime, code: str, env: Any) -> None:
    """Compile *code* with *env* as its environment and execute it."""
    g = lua.globals()
    g["__nyrx_code"] = code
    g["__nyrx_env"] = env
    try:
        lua.execute("assert(load(__nyrx_code, 'cfg', 't', __nyrx_env))()")
    finally:
        g["__nyrx_code"] = None
        g["__nyrx_env"] = None


# ---------------------------------------------------------------------------
# Sandbox class
# ---------------------------------------------------------------------------


class Sandbox:
    """Sandbox for loading and calling Lua server probe scripts.

    ``Sandbox`` does not cache a ``LuaRuntime``: every access delegates
    to the current thread's runtime via ``_get_lua()``.
    """

    def load_script(self, filepath: str | Path) -> Any:
        """Load a Lua file into a fresh isolated env and return it."""
        lua = _get_lua()
        with open(filepath) as f:
            code = f.read()
        env = _build_env(lua)
        _run_script(lua, code, env)
        return env

    def call_probe(self, script_globals: Any, params: dict) -> dict | None:
        """Call ``probe(params)`` and return the result dict or None."""
        lua = _get_lua()
        lua_params = lua.table()
        for k, v in params.items():
            lua_params[k] = v
        try:
            fn = script_globals["probe"]
            if fn is None:
                return None
            self._set_current_server(script_globals)
            try:
                result = fn(lua_params)
            finally:
                _current_server.name = None
            if result is None:
                return None
            return _deep_from_lua(result)
        except Exception:
            logger.warning("Sandbox.call_probe: probe failed")
            return None

    @staticmethod
    def _set_current_server(script_globals: Any) -> None:
        """Scope secrets access to the probed config's ``SERVER.name``."""
        name = None
        try:
            server = script_globals["SERVER"]
            if server is not None:
                name = dict(server).get("name")
        except Exception:
            name = None
        _current_server.name = name

    @staticmethod
    def create() -> Sandbox:
        return Sandbox()


# ---------------------------------------------------------------------------
# Module registration helpers
# ---------------------------------------------------------------------------


def _make_table(lua: LuaRuntime) -> Any:
    return lua.table()


def _register_http(lua: LuaRuntime) -> None:
    def _to_lua_table(val: Any) -> Any:
        if isinstance(val, dict):
            t = lua.table()
            for k, v in val.items():
                t[k] = _to_lua_table(v)
            return t
        if isinstance(val, list):
            t = lua.table()
            for i, v in enumerate(val, 1):
                t[i] = _to_lua_table(v)
            return t
        return val

    def http_get_wrapped(
        url: str,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ) -> Any:
        return _to_lua_table(helpers.http_get(url, headers, timeout))

    def _from_lua(val: Any) -> Any:
        if isinstance(val, dict):
            return {_from_lua(k): _from_lua(v) for k, v in val.items()}
        if isinstance(val, list):
            return [_from_lua(v) for v in val]
        try:
            d = dict(val)
            if not d:
                return d
            keys = list(d.keys())
            if (
                all(isinstance(k, int) for k in keys)
                and min(keys) == 1
                and sorted(keys) == list(range(1, len(keys) + 1))
            ):
                return [_from_lua(d[i]) for i in range(1, len(keys) + 1)]
            return {_from_lua(k): _from_lua(v) for k, v in d.items()}
        except (TypeError, ValueError):
            return val

    def http_post_wrapped(
        url: str,
        json_data: Any = None,
        headers: dict[str, str] | None = None,
        timeout: int = 15,
    ) -> Any:
        py_data = _from_lua(json_data) if json_data is not None else None
        return _to_lua_table(helpers.http_post(url, py_data, headers, timeout))

    tbl = _make_table(lua)
    tbl["get"] = http_get_wrapped
    tbl["post"] = http_post_wrapped
    lua.globals()["http"] = tbl


def _register_playwright(lua: LuaRuntime) -> None:
    page_data: dict[str, Any] = {"_page": [None], "_cb": [None], "_done": [False]}

    def _handler(response: Any) -> None:
        cb = page_data["_cb"][0]
        done = page_data["_done"]
        if cb is None:
            return
        try:
            body = response.text()
            headers = dict(response.headers)
            r = cb(response.url, response.status, headers, body)
            if r:
                done[0] = True
        except Exception:
            logger.debug("playwright _handler: response callback failed")

    def pw_start() -> None:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        ctx = browser.new_context(
            viewport={"width": 1280, "height": 720},
        )
        page_data["_pw"] = pw
        page_data["_browser"] = browser
        page_data["_context"] = ctx

    def pw_new_page() -> None:
        ctx = page_data.get("_context")
        if ctx is None:
            pw_start()
            ctx = page_data["_context"]
        old = page_data["_page"][0]
        if old is not None:
            try:
                old.close()
            except Exception:
                logger.debug("playwright pw_new_page: old.close failed")
        page = ctx.new_page()
        page.on("response", _handler)
        page_data["_page"][0] = page
        page_data["_done"][0] = False

    def pw_goto(url: str, timeout_ms: int = 30000) -> None:
        if urlparse(url).scheme not in ("http", "https"):
            raise ValueError(
                f"playwright.navigate: only http/https URLs allowed: {url!r}"
            )
        page = page_data["_page"][0]
        if page is None:
            pw_new_page()
            page = page_data["_page"][0]
        page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

    def pw_on_response(callback: Any) -> None:
        page_data["_cb"][0] = callback

    def pw_wait_for_result(max_seconds: int = 30, interval_ms: int = 1000) -> bool:
        page = page_data["_page"][0]
        for _ in range(max_seconds * 1000 // interval_ms):
            if page_data["_done"][0]:
                page_data["_done"][0] = False
                return True
            page.wait_for_timeout(interval_ms)
        return False

    def pw_wait(ms: int) -> None:
        page = page_data["_page"][0]
        if page:
            page.wait_for_timeout(ms)

    def pw_wait_for_selector(selector: str, timeout: int = 5000) -> bool:
        page = page_data["_page"][0]
        if page:
            try:
                page.wait_for_selector(selector, timeout=timeout)
                return True
            except Exception:
                logger.debug("playwright wait_for_selector failed: %s", selector)
                return False
        return False

    def pw_click(selector: str, timeout: int = 5000) -> bool:
        page = page_data["_page"][0]
        if page:
            try:
                page.wait_for_selector(selector, timeout=timeout)
                page.evaluate(f"document.querySelector('{selector}')?.click()")
                return True
            except Exception:
                logger.debug("playwright click failed: %s", selector)
                return False
        return False

    def pw_click_text(text: str, timeout: int = 5000) -> bool:
        page = page_data["_page"][0]
        if page:
            safe = text.replace("'", "\\'")
            try:
                page.wait_for_function(
                    f"[...document.querySelectorAll('button, a, [role=button], [role=menuitem]')]"
                    f".find(el => el.textContent.trim() === '{safe}')",
                    timeout=timeout,
                )
                page.evaluate(
                    f"[...document.querySelectorAll('button, a, [role=button], [role=menuitem]')]"
                    f".find(el => el.textContent.trim() === '{safe}')?.click()"
                )
                return True
            except Exception:
                logger.debug("playwright click_text failed: %s", text)
                return False
        return False

    def pw_hover(selector: str, timeout: int = 5000) -> bool:
        page = page_data["_page"][0]
        if page:
            try:
                page.wait_for_selector(selector, timeout=timeout)
                page.hover(selector)
                return True
            except Exception:
                logger.debug("playwright hover failed: %s", selector)
                return False
        return False

    def pw_evaluate(js: str) -> Any:
        page = page_data["_page"][0]
        if page:
            return page.evaluate(js)
        return None

    def pw_get_cookies(domain: str | None = None) -> Any:
        ctx = page_data.get("_context")
        if ctx is None:
            return lua.table()
        try:
            urls = [domain] if domain else None
            raw = ctx.cookies(urls)
            t = lua.table()
            for i, c in enumerate(raw, 1):
                entry = lua.table()
                entry["name"] = c["name"]
                entry["value"] = c["value"]
                t[i] = entry
            return t
        except Exception:
            logger.debug("playwright get_cookies failed")
            return lua.table()

    def pw_close() -> None:
        page = page_data["_page"][0]
        if page:
            try:
                page.close()
            except Exception:
                logger.debug("playwright pw_close failed")
        page_data["_page"][0] = None
        page_data["_cb"][0] = None
        page_data["_done"][0] = False

    def pw_stop() -> None:
        pw_close()
        browser = page_data.get("_browser")
        if browser:
            try:
                browser.close()
            except Exception:
                logger.debug("playwright pw_stop: browser.close failed")
        pw = page_data.get("_pw")
        if pw:
            try:
                pw.stop()
            except Exception:
                logger.debug("playwright pw_stop: pw.stop failed")
        page_data["_browser"] = None
        page_data["_context"] = None
        page_data["_pw"] = None

    tbl = _make_table(lua)
    tbl["start"] = pw_start
    tbl["new_page"] = pw_new_page
    tbl["navigate"] = pw_goto
    tbl["on_response"] = pw_on_response
    tbl["wait_for"] = pw_wait_for_result
    tbl["wait"] = pw_wait
    tbl["wait_for_selector"] = pw_wait_for_selector
    tbl["click"] = pw_click
    tbl["click_text"] = pw_click_text
    tbl["hover"] = pw_hover
    tbl["evaluate"] = pw_evaluate
    tbl["get_cookies"] = pw_get_cookies
    tbl["close"] = pw_close
    tbl["stop"] = pw_stop
    lua.globals()["playwright"] = tbl


def _register_crypto(lua: LuaRuntime) -> None:
    tbl = _make_table(lua)
    tbl["sha256"] = helpers.sha256
    tbl["hmac_sha256"] = helpers.hmac_sha256
    tbl["md5"] = helpers.md5
    tbl["random_bytes"] = helpers.random_bytes
    tbl["aes_decrypt"] = helpers.aes_decrypt

    def aes_gcm_stub(*args: Any, **kwargs: Any) -> Any:
        raise RuntimeError(
            "crypto.aes_gcm requires pycryptodomex. Install: pip install pycryptodomex"
        )

    tbl["aes_gcm"] = aes_gcm_stub
    lua.globals()["crypto"] = tbl


def _register_hls(lua: LuaRuntime) -> None:

    def _to_lua(val: Any) -> Any:
        if isinstance(val, dict):
            t = lua.table()
            for k, v in val.items():
                t[k] = _to_lua(v)
            return t
        if isinstance(val, list):
            t = lua.table()
            for i, v in enumerate(val, 1):
                t[i] = _to_lua(v)
            return t
        return val

    tbl = _make_table(lua)
    tbl["analyze"] = lambda *a, **kw: _to_lua(helpers.hls_analyze(*a, **kw))
    tbl["get_variants"] = lambda *a, **kw: _to_lua(helpers.hls_get_variants(*a, **kw))
    lua.globals()["hls"] = tbl


def _register_base64(lua: LuaRuntime) -> None:
    import base64

    def b64decode(s: str) -> str:
        try:
            return base64.b64decode(s).decode("utf-8", errors="replace")
        except Exception:
            logger.debug("base64 decode failed for input of length %d", len(s))
            return ""

    def b64encode(s: str) -> str:
        try:
            return base64.b64encode(s.encode()).decode()
        except Exception:
            logger.debug("base64 encode failed for input of length %d", len(s))
            return ""

    tbl = _make_table(lua)
    tbl["decode"] = b64decode
    tbl["encode"] = b64encode
    tbl["decode_custom"] = helpers.custom_b64decode
    lua.globals()["base64"] = tbl


def _register_pow(lua: LuaRuntime) -> None:
    tbl = _make_table(lua)
    tbl["find_nonce"] = helpers.pow_find_nonce
    lua.globals()["pow"] = tbl


def _register_json(lua: LuaRuntime) -> None:
    import json as _json

    def _to_lua(val: Any) -> Any:
        if isinstance(val, dict):
            t = lua.table()
            for k, v in val.items():
                t[k] = _to_lua(v)
            return t
        if isinstance(val, list):
            t = lua.table()
            for i, v in enumerate(val, 1):
                t[i] = _to_lua(v)
            return t
        return val

    def json_decode(s: str) -> Any:
        return _to_lua(_json.loads(s))

    tbl = _make_table(lua)
    tbl["decode"] = json_decode

    def json_encode(val: Any) -> str:
        val = _deep_from_lua(val)
        return _json.dumps(val)

    tbl["encode"] = json_encode
    lua.globals()["json"] = tbl


def _register_url(lua: LuaRuntime) -> None:
    tbl = _make_table(lua)
    tbl["parse"] = helpers.url_parse
    tbl["build"] = helpers.url_build
    tbl["encode_params"] = helpers.url_encode_params
    lua.globals()["url"] = tbl


def _register_log(lua: LuaRuntime) -> None:
    import logging

    sandbox_logger = logging.getLogger("nyrx.lua.sandbox")

    tbl = _make_table(lua)
    tbl["info"] = lambda msg: sandbox_logger.info(msg)
    tbl["warn"] = lambda msg: sandbox_logger.warning(msg)
    tbl["error"] = lambda msg: sandbox_logger.error(msg)
    lua.globals()["log"] = tbl


def _register_secrets(lua: LuaRuntime, secrets_path: str | Path | None = None) -> None:
    import json

    secrets_path = (
        Path(secrets_path) if secrets_path is not None else CONFIG_DIR / "secrets.json"
    )
    lock = __import__("threading").Lock()

    def _read() -> dict:
        try:
            return json.loads(secrets_path.read_text()) if secrets_path.exists() else {}
        except Exception:
            logger.warning("secrets _read: failed to read %s", secrets_path)
            return {}

    def _write(data: dict) -> None:
        secrets_path.parent.mkdir(parents=True, exist_ok=True)
        secrets_path.write_text(json.dumps(data, indent=2))

    def _scoped(key: str) -> bool:
        name = getattr(_current_server, "name", None)
        if not name or not isinstance(key, str):
            return False
        return key.startswith(name + "_")

    def secrets_load(key: str) -> str | None:
        if not _scoped(key):
            logger.debug("secrets.load denied for key %r", key)
            return None
        with lock:
            return _read().get(key)

    def secrets_store(key: str, value: str) -> None:
        if not _scoped(key):
            logger.debug("secrets.store denied for key %r", key)
            return
        with lock:
            d = _read()
            d[key] = value
            _write(d)

    def secrets_clear(key: str) -> None:
        if not _scoped(key):
            logger.debug("secrets.clear denied for key %r", key)
            return
        with lock:
            d = _read()
            d.pop(key, None)
            _write(d)

    tbl = _make_table(lua)
    tbl["load"] = secrets_load
    tbl["store"] = secrets_store
    tbl["clear"] = secrets_clear
    lua.globals()["secrets"] = tbl


def _register_quality(lua: LuaRuntime) -> None:

    def _to_lua_table(val: Any) -> Any:
        if isinstance(val, dict):
            t = lua.table()
            for k, v in val.items():
                t[k] = _to_lua_table(v)
            return t
        if isinstance(val, list):
            t = lua.table()
            for i, v in enumerate(val, 1):
                t[i] = _to_lua_table(v)
            return t
        return val

    def _from_lua(val: Any) -> Any:
        return _deep_from_lua(val)

    def select_variant(
        variants: Any,
        preferred: Any = None,
    ) -> Any:
        py_variants = _from_lua(variants) if variants is not None else []
        py_preferred = int(preferred) if preferred is not None else None

        if not py_variants:
            return None

        if py_preferred is None:
            best = py_variants[0]
            for v in py_variants:
                v_res = v.get("resolution") or 0
                best_res = best.get("resolution") or 0
                if isinstance(v_res, int) and isinstance(best_res, int):
                    if v_res > best_res:
                        best = v
                    elif v_res == best_res and (v.get("bandwidth") or 0) > (
                        best.get("bandwidth") or 0
                    ):
                        best = v
                elif not isinstance(best_res, int) and isinstance(v_res, int):
                    best = v
            return _to_lua_table(best)

        # Exact match
        for v in py_variants:
            if v.get("resolution") == py_preferred:
                return _to_lua_table(v)

        # Best below preferred
        best, best_res = None, 0
        for v in py_variants:
            res = v.get("resolution")
            if isinstance(res, int) and 0 < res < py_preferred and res > best_res:
                best = v
                best_res = res
        if best:
            return _to_lua_table(best)

        # Everything exceeds preferred, take lowest
        best, best_res = None, float("inf")
        for v in py_variants:
            res = v.get("resolution")
            if isinstance(res, int) and res < best_res:
                best = v
                best_res = res
        if best:
            return _to_lua_table(best)

        # No resolution info, return first
        return _to_lua_table(py_variants[0]) if py_variants else None

    tbl = _make_table(lua)
    tbl["select_variant"] = select_variant
    lua.globals()["quality"] = tbl
