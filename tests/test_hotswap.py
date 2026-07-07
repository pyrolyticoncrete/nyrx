# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the hotswap Lua config updater.

Every test here targets a pure decision point: version ordering, path
traversal guards, atomic writes, manifest validation, hash verification,
and the per-branch behaviour of ``apply_bundle``.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from nyrx.sources.hotswap import (
    _atomic_write,
    _check_min_version,
    _cmp_ver,
    _sanitize_filename,
    apply_bundle,
    fetch_file,
    fetch_manifest,
)


class _FakeResp:
    def __init__(self, data=None, content=b"", status_code=200) -> None:
        self._data = data
        self.content = content
        self.status_code = status_code
        self.ok = 200 <= status_code < 400
        self.url = "http://fake"
        self.headers = {}

    def raise_for_status(self) -> None:
        if not self.ok:
            raise AssertionError(f"HTTP {self.status_code}")

    def json(self):
        if self._data is None:
            raise ValueError("no json")
        return self._data


def _entry(name: str, content: bytes = b"x") -> dict:
    return {
        "path": name,
        "url": f"http://localhost/{name}",
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _manifest(*entries: dict, version: int = 1, **extra) -> dict:
    m = {"version": version, "files": list(entries)}
    m.update(extra)
    return m


# ---------------------------------------------------------------------------
# 1.1.1 _cmp_ver: numeric-not-lexicographic ordering
# ---------------------------------------------------------------------------


class TestCmpVer:
    def test_two_digit_segment_orders_numerically(self) -> None:
        assert _cmp_ver("1.2.3", "1.10.0") == -1

    def test_equal_returns_zero(self) -> None:
        assert _cmp_ver("1.2.3", "1.2.3") == 0

    def test_higher_major_wins(self) -> None:
        assert _cmp_ver("2.0.0", "1.99.99") == 1


# ---------------------------------------------------------------------------
# 1.1.2 _sanitize_filename: path-traversal guards
# ---------------------------------------------------------------------------


class TestSanitizeFilename:
    @pytest.mark.parametrize("name", ["a/b", "..\\evil.lua", "..", "x/../y"])
    def test_rejects_traversal_names(self, name: str) -> None:
        with pytest.raises(ValueError):
            _sanitize_filename(name)

    def test_returns_safe_name_unchanged(self) -> None:
        assert _sanitize_filename("01.lua") == "01.lua"


# ---------------------------------------------------------------------------
# 1.1.3 _atomic_write: temp-file protocol
# ---------------------------------------------------------------------------


class TestAtomicWrite:
    def test_target_appears_and_tmp_removed(self, tmp_path: Path) -> None:
        p = tmp_path / "01.lua"
        _atomic_write(p, b"x")
        assert p.read_bytes() == b"x"
        assert not p.with_suffix(p.suffix + ".tmp").exists()

    def test_overwrite_twice_works(self, tmp_path: Path) -> None:
        p = tmp_path / "01.lua"
        _atomic_write(p, b"one")
        _atomic_write(p, b"two")
        assert p.read_bytes() == b"two"


# ---------------------------------------------------------------------------
# 1.1.4 fetch_manifest: type-guard contract
# ---------------------------------------------------------------------------


class TestFetchManifest:
    def test_valid_manifest_returned(self) -> None:
        m = _manifest(_entry("01.lua"))
        with patch(
            "nyrx.sources.hotswap.requests.get", return_value=_FakeResp(data=m)
        ) as mock_get:
            assert fetch_manifest("http://x/manifest.json") == m
        mock_get.assert_called_once_with("http://x/manifest.json", timeout=15)

    def test_missing_version_returns_none(self) -> None:
        with patch(
            "nyrx.sources.hotswap.requests.get",
            return_value=_FakeResp(data={"files": []}),
        ):
            assert fetch_manifest("http://x") is None

    def test_string_version_returns_none(self) -> None:
        with patch(
            "nyrx.sources.hotswap.requests.get",
            return_value=_FakeResp(data={"version": "3", "files": []}),
        ):
            assert fetch_manifest("http://x") is None

    def test_files_not_a_list_returns_none(self) -> None:
        with patch(
            "nyrx.sources.hotswap.requests.get",
            return_value=_FakeResp(data={"version": 1, "files": {}}),
        ):
            assert fetch_manifest("http://x") is None

    def test_http_error_returns_none(self) -> None:
        with patch(
            "nyrx.sources.hotswap.requests.get", return_value=_FakeResp(status_code=500)
        ):
            assert fetch_manifest("http://x") is None

    def test_request_exception_returns_none(self) -> None:
        with patch("nyrx.sources.hotswap.requests.get", side_effect=OSError("boom")):
            assert fetch_manifest("http://x") is None


# ---------------------------------------------------------------------------
# 1.1.5 fetch_file: hash verification (the core integrity check)
# ---------------------------------------------------------------------------


class TestFetchFile:
    def test_matching_hash_returns_body(self) -> None:
        body = b"config lua"
        digest = hashlib.sha256(body).hexdigest()
        with patch(
            "nyrx.sources.hotswap.requests.get", return_value=_FakeResp(content=body)
        ) as mock_get:
            assert fetch_file("http://x/01.lua", digest) == body
        mock_get.assert_called_once_with("http://x/01.lua", timeout=30)

    def test_mismatched_hash_returns_none(self) -> None:
        with patch(
            "nyrx.sources.hotswap.requests.get",
            return_value=_FakeResp(content=b"tampered"),
        ):
            assert fetch_file("http://x/01.lua", "0" * 64) is None

    def test_http_error_returns_none(self) -> None:
        with patch(
            "nyrx.sources.hotswap.requests.get",
            return_value=_FakeResp(content=b"x", status_code=500),
        ):
            assert fetch_file("http://x/01.lua", "0" * 64) is None

    def test_request_exception_returns_none(self) -> None:
        with patch("nyrx.sources.hotswap.requests.get", side_effect=OSError("boom")):
            assert fetch_file("http://x/01.lua", "0" * 64) is None


# ---------------------------------------------------------------------------
# 1.1.6 _check_min_version: comparison direction + message
# ---------------------------------------------------------------------------


class TestCheckMinVersion:
    @pytest.fixture(autouse=True)
    def _v(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr("nyrx.config.APP_VERSION", "1.0.0")

    def test_older_required_passes(self) -> None:
        assert _check_min_version({"min_app_version": "0.9.0"}) is None

    def test_equal_required_passes(self) -> None:
        assert _check_min_version({"min_app_version": "1.0.0"}) is None

    def test_newer_required_returns_error(self) -> None:
        err = _check_min_version({"min_app_version": "1.0.1"})
        assert err is not None
        assert "require app v1.0.1" in err
        assert "1.0.0" in err

    def test_missing_key_means_zero(self) -> None:
        assert _check_min_version({}) is None


# ---------------------------------------------------------------------------
# 1.1.7–1.1.13 apply_bundle: branch behaviour
# ---------------------------------------------------------------------------


class TestApplyBundleMinVersionReject:
    def test_reject_short_circuits_everything(self, tmp_path: Path) -> None:
        manifest = _manifest(_entry("01.lua"))
        with (
            patch(
                "nyrx.sources.hotswap._check_min_version", return_value="too old"
            ) as mock_check,
            patch("nyrx.sources.hotswap.fetch_file") as mock_fetch,
            patch("nyrx.sources.hotswap._atomic_write") as mock_write,
        ):
            result = apply_bundle(manifest, tmp_path)
        assert result.success is False
        assert result.errors == ["too old"]
        assert result.written == [] and result.skipped == [] and result.deleted == []
        mock_check.assert_called_once_with(manifest)
        mock_fetch.assert_not_called()
        mock_write.assert_not_called()


class TestApplyBundleSkip:
    def test_sha256_equal_file_skipped(self, tmp_path: Path) -> None:
        body = b"same content"
        entry = _entry("01.lua", content=body)
        (tmp_path / "01.lua").write_bytes(body)
        with patch("nyrx.sources.hotswap.fetch_file") as mock_fetch:
            result = apply_bundle(_manifest(entry), tmp_path)
        assert result.skipped == ["01.lua"]
        assert result.written == []
        assert result.success is True
        mock_fetch.assert_not_called()

    def test_hash_mismatch_downloads_and_writes(self, tmp_path: Path) -> None:
        entry = _entry("01.lua", content=b"new")
        (tmp_path / "01.lua").write_bytes(b"old")
        with patch(
            "nyrx.sources.hotswap.fetch_file", return_value=b"new"
        ) as mock_fetch:
            result = apply_bundle(_manifest(entry), tmp_path)
        assert result.written == ["01.lua"]
        assert (tmp_path / "01.lua").read_bytes() == b"new"
        mock_fetch.assert_called_once_with(entry["url"], entry["sha256"])

    def test_download_failure_keeps_old_and_records_error(self, tmp_path: Path) -> None:
        good = _entry("01.lua", content=b"good")
        bad = _entry("02.lua", content=b"bad")
        (tmp_path / "02.lua").write_bytes(b"old2")

        def _fetch(url, expected_hash):
            if url == good["url"]:
                return b"good"
            return None

        with patch("nyrx.sources.hotswap.fetch_file", side_effect=_fetch):
            result = apply_bundle(_manifest(good, bad), tmp_path)
        assert result.success is False
        assert any("failed to download" in e for e in result.errors)
        assert result.written == ["01.lua"]
        assert (tmp_path / "02.lua").read_bytes() == b"old2"

    def test_invalid_path_entry_errors_but_others_apply(self, tmp_path: Path) -> None:
        good = _entry("01.lua", content=b"good")
        bad = {"path": "sub/..\\evil.lua", "url": "http://x", "sha256": "0" * 64}
        with patch("nyrx.sources.hotswap.fetch_file", return_value=b"good"):
            result = apply_bundle(_manifest(good, bad), tmp_path)
        assert result.success is False
        assert any("invalid entries" in e for e in result.errors)
        assert result.written == ["01.lua"]
        assert (tmp_path / "01.lua").read_bytes() == b"good"


class TestApplyBundleOrphans:
    def test_deletion_scoped_to_lua(self, tmp_path: Path) -> None:
        entry = _entry("01.lua", content=b"x")
        (tmp_path / "01.lua").write_bytes(b"x")
        (tmp_path / "orphan.lua").write_bytes(b"x")
        (tmp_path / "README.txt").write_text("readme")
        with patch("nyrx.sources.hotswap.fetch_file", return_value=b"x"):
            result = apply_bundle(_manifest(entry), tmp_path)
        assert result.deleted == ["orphan.lua"]
        assert not (tmp_path / "orphan.lua").exists()
        assert (tmp_path / "README.txt").exists()
        assert (tmp_path / "01.lua").exists()


class TestApplyBundleTmdbKeys:
    @pytest.fixture(autouse=True)
    def _keys_path(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
        keys = tmp_path / "keys.json"
        monkeypatch.setattr("nyrx.sources.hotswap._keys_json_path", lambda: keys)
        return keys

    def test_fresh_file_writes_and_reloads(
        self, tmp_path: Path, _keys_path: Path
    ) -> None:
        manifest = _manifest(version=2, tmdb_keys=["k1"])
        with patch("nyrx.sources.tv_movies.tmdb_cache.load_keys") as mock_load:
            result = apply_bundle(manifest, tmp_path)
        assert "tmdb_keys" in result.written
        assert json.loads(_keys_path.read_text())["tmdb_keys"] == ["k1"]
        mock_load.assert_called_once()

    def test_identical_keys_skipped(self, tmp_path: Path, _keys_path: Path) -> None:
        _keys_path.write_text(json.dumps({"tmdb_keys": ["k1"]}))
        manifest = _manifest(version=2, tmdb_keys=["k1"])
        with patch("nyrx.sources.tv_movies.tmdb_cache.load_keys") as mock_load:
            result = apply_bundle(manifest, tmp_path)
        assert "tmdb_keys" in result.skipped
        assert "tmdb_keys" not in result.written
        mock_load.assert_not_called()


class TestApplyBundleDispatcherReload:
    def test_reload_called_when_provided(self, tmp_path: Path) -> None:
        dispatcher = MagicMock()
        with patch("nyrx.sources.hotswap.fetch_file", return_value=b"x"):
            apply_bundle(_manifest(_entry("01.lua")), tmp_path, dispatcher)
        dispatcher.reload_configs.assert_called_once()

    def test_none_dispatcher_does_not_crash(self, tmp_path: Path) -> None:
        with patch("nyrx.sources.hotswap.fetch_file", return_value=b"x"):
            result = apply_bundle(_manifest(_entry("01.lua")), tmp_path)
        assert result.success is True
