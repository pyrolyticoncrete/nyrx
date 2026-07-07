# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``sources/tv_movies/proxy_discovery.py``.

The proxy domain rotates periodically; if discovery silently fails the TMDB
key path degrades with no user-visible signal. Locks the script-tag +
``baseURL`` extraction and the changed/unchanged persistence contract.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from nyrx.sources.tv_movies import proxy_discovery


class _TextResp:
    def __init__(self, text):
        self.text = text


_HTML = '<html><script src="/_next/static/chunks/pages/movie/550.js"></script></html>'
_JS = 'const cfg={baseURL:"https://db.speedracelight.com/3",foo:1};'


# ---------------------------------------------------------------------------
# discover_proxy
# ---------------------------------------------------------------------------


class TestDiscoverProxy:
    def test_happy_path(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.proxy_discovery.requests.get",
            side_effect=[_TextResp(_HTML), _TextResp(_JS)],
        ) as m_get:
            out = proxy_discovery.discover_proxy()
        assert out == "https://db.speedracelight.com/3"
        assert m_get.call_count == 2

    def test_html_without_script_tag_returns_none(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.proxy_discovery.requests.get",
            return_value=_TextResp("<html>no script</html>"),
        ) as m_get:
            out = proxy_discovery.discover_proxy()
        assert out is None
        assert m_get.call_count == 1

    def test_js_without_baseurl_returns_none(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.proxy_discovery.requests.get",
            side_effect=[_TextResp(_HTML), _TextResp("const x=1;")],
        ):
            assert proxy_discovery.discover_proxy() is None

    def test_request_raising_returns_none(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.proxy_discovery.requests.get",
            side_effect=Exception("boom"),
        ):
            assert proxy_discovery.discover_proxy() is None


# ---------------------------------------------------------------------------
# update_proxy_config
# ---------------------------------------------------------------------------


class TestUpdateProxyConfig:
    def test_new_proxy_writes_config(self, tmp_path, monkeypatch) -> None:
        keys = tmp_path / "keys.json"
        monkeypatch.setattr(proxy_discovery, "KEYS_PATH", keys)
        monkeypatch.setattr("time.time", lambda: 1234.5)
        with patch.object(
            proxy_discovery, "discover_proxy", return_value="https://db.x.com/3"
        ):
            assert proxy_discovery.update_proxy_config() is True
        cfg = json.loads(keys.read_text())
        assert cfg["tmdb_proxy"] == "https://db.x.com/3"
        assert cfg["proxy_last_checked"] == 1234.5

    def test_unchanged_proxy_rewrites_last_checked(self, tmp_path, monkeypatch) -> None:
        keys = tmp_path / "keys.json"
        keys.write_text(json.dumps({"tmdb_proxy": "https://db.x.com/3"}))
        monkeypatch.setattr(proxy_discovery, "KEYS_PATH", keys)
        monkeypatch.setattr("time.time", lambda: 99.0)
        with patch.object(
            proxy_discovery, "discover_proxy", return_value="https://db.x.com/3"
        ):
            assert proxy_discovery.update_proxy_config() is False
        cfg = json.loads(keys.read_text())
        assert cfg["tmdb_proxy"] == "https://db.x.com/3"
        assert cfg["proxy_last_checked"] == 99.0

    def test_discover_none_leaves_config_untouched(self, tmp_path, monkeypatch) -> None:
        keys = tmp_path / "keys.json"
        monkeypatch.setattr(proxy_discovery, "KEYS_PATH", keys)
        with patch.object(proxy_discovery, "discover_proxy", return_value=None):
            assert proxy_discovery.update_proxy_config() is False
        assert not keys.exists()

    def test_write_failure_returns_false(self, tmp_path, monkeypatch) -> None:
        # KEYS_PATH pointing at a directory: mkdir succeeds, write_text raises.
        monkeypatch.setattr(proxy_discovery, "KEYS_PATH", tmp_path)
        with patch.object(
            proxy_discovery, "discover_proxy", return_value="https://db.x.com/3"
        ):
            assert proxy_discovery.update_proxy_config() is False
