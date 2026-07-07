# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for player.py utility functions (C01).

Pure functions with no mocking needed, except for ``get_thumbnail_path``
(HTTP) and ``clean_part_files`` (filesystem).
"""

from __future__ import annotations

import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from nyrx.player import (
    THUMB_CACHE,
    _cleanup_orphaned_sockets,
    _select_thumbnail,
    clean_part_files,
    estimate_raw_height,
    format_duration,
    format_seconds,
    format_views,
    get_thumbnail_path,
)


class TestFormatSeconds:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "0:00"),
            (-1, "0:00"),
            (1, "0:01"),
            (59, "0:59"),
            (60, "1:00"),
            (3661, "1:01:01"),
            (3600, "1:00:00"),
            (3661.5, "1:01:01"),  # float truncated
            (None, "0:00"),
        ],
    )
    def test_format_seconds(self, seconds: float | None, expected: str) -> None:
        assert format_seconds(seconds) == expected


class TestFormatDuration:
    @pytest.mark.parametrize(
        ("seconds", "expected"),
        [
            (0, "?"),
            (None, "?"),
            (1, "0:01"),
            (59, "0:59"),
            (60, "1:00"),
            (3661, "1:01:01"),
            (3600, "1:00:00"),
            (3661.5, "1:01:01"),  # float truncated
        ],
    )
    def test_format_duration(self, seconds: int | None, expected: str) -> None:
        assert format_duration(seconds) == expected


class TestFormatViews:
    @pytest.mark.parametrize(
        ("count", "expected"),
        [
            (0, ""),
            (None, ""),
            (1, "1 views"),
            (999, "999 views"),
            (1000, "1.0K views"),
            (1500, "1.5K views"),
            (999_999, "1000.0K views"),  # rounds up from 999.999
            (1_000_000, "1.0M views"),
            (1_200_000, "1.2M views"),
            (1_234_567, "1.2M views"),
        ],
    )
    def test_format_views(self, count: int | None, expected: str) -> None:
        assert format_views(count) == expected


class TestThumbCache:
    def test_thumb_cache_is_absolute_path(self) -> None:
        assert isinstance(THUMB_CACHE, Path)
        assert THUMB_CACHE.is_absolute()


class TestGetThumbnailPath:
    def test_returns_cached_path_when_file_exists(self, tmp_path: Path) -> None:
        cached = tmp_path / "abc123.jpg"
        cached.write_text("fake-image-data")

        with patch("nyrx.player.THUMB_CACHE", tmp_path):
            result = get_thumbnail_path("abc123")

        assert result == cached

    def test_returns_none_when_no_cache_and_no_network(self, tmp_path: Path) -> None:
        with patch("nyrx.player.THUMB_CACHE", tmp_path):
            with patch("nyrx.player.requests.get") as mock_get:
                mock_get.side_effect = OSError("no network")
                result = get_thumbnail_path("nonexistent")

        assert result is None

    def test_soundcloud_uses_sc_thumbs_dir_when_present(self, tmp_path: Path) -> None:
        sc = tmp_path / "sc"
        sc.mkdir()
        sc_thumb = sc / "scid.jpg"
        sc_thumb.write_text("x")

        with (
            patch("nyrx.player.THUMB_CACHE", tmp_path / "tmp"),
            patch("nyrx.player.SC_THUMBS_DIR", sc),
        ):
            result = get_thumbnail_path("scid", source="soundcloud")

        assert result == sc_thumb

    def test_soundcloud_falls_back_to_download_from_thumb_url(
        self, tmp_path: Path
    ) -> None:
        cache = tmp_path / "tmp"
        cache.mkdir()
        thumb_url = "https://example.com/sc.jpg"

        with (
            patch("nyrx.player.THUMB_CACHE", cache),
            patch("nyrx.player.SC_THUMBS_DIR", tmp_path / "sc"),
            patch("nyrx.player.requests.get") as mock_get,
        ):
            resp = mock_get.return_value
            resp.raise_for_status.return_value = None
            resp.content = b"img"
            result = get_thumbnail_path(
                "scid", thumb_url=thumb_url, source="soundcloud"
            )

        dst = cache / "scid.jpg"
        assert result == dst
        assert dst.read_bytes() == b"img"
        # sc thumbs dir missing and only the provided thumb_url is tried
        mock_get.assert_called_once_with(thumb_url, timeout=10)

    def test_youtube_downloads_maxres_to_cache(self, tmp_path: Path) -> None:
        cache = tmp_path / "tmp"
        cache.mkdir()

        with (
            patch("nyrx.player.THUMB_CACHE", cache),
            patch("nyrx.player.requests.get") as mock_get,
        ):
            resp = mock_get.return_value
            resp.raise_for_status.return_value = None
            resp.content = b"img"
            result = get_thumbnail_path("ytid")

        dst = cache / "ytid.jpg"
        assert result == dst
        assert dst.read_bytes() == b"img"
        # youtube branch tries maxresdefault first
        mock_get.assert_called_once_with(
            "https://img.youtube.com/vi/ytid/maxresdefault.jpg", timeout=10
        )


class TestSelectThumbnail:
    def test_empty_list_returns_empty_string(self) -> None:
        assert _select_thumbnail([]) == ""

    def test_picks_largest_by_area(self) -> None:
        thumbs = [
            {"url": "small", "width": 10, "height": 10},
            {"url": "big", "width": 100, "height": 200},
            {"url": "medium", "width": 20, "height": 20},
        ]
        assert _select_thumbnail(thumbs) == "big"

    def test_missing_width_or_height_treated_as_zero(self) -> None:
        thumbs = [
            {"url": "nodims"},
            {"url": "dim", "width": 5, "height": 5},
        ]
        assert _select_thumbnail(thumbs) == "dim"


class TestEstimateRawHeight:
    def test_fallback_when_cell_size_unavailable(self) -> None:
        with patch(
            "textual_image._terminal.get_cell_size",
            return_value=SimpleNamespace(width=0, height=0),
        ):
            assert estimate_raw_height(40, 0.5) == int(40 * 0.5 * 0.5)

    def test_real_cells_gives_nonzero(self) -> None:
        assert estimate_raw_height(40, 0.5) > 0


class TestCleanPartFiles:
    def test_removes_part_files(self, tmp_path: Path) -> None:
        (tmp_path / "a.part").write_text("x")
        (tmp_path / "b.part").write_text("x")
        (tmp_path / "ok.mp4").write_text("x")

        count = clean_part_files(str(tmp_path))

        assert count == 2
        assert not (tmp_path / "a.part").exists()
        assert (tmp_path / "ok.mp4").exists()

    def test_no_part_files_returns_zero(self, tmp_path: Path) -> None:
        (tmp_path / "ok.mp4").write_text("x")
        assert clean_part_files(str(tmp_path)) == 0

    def test_empty_directory_returns_zero(self, tmp_path: Path) -> None:
        assert clean_part_files(str(tmp_path)) == 0

    def test_missing_directory_returns_zero(self, tmp_path: Path) -> None:
        assert clean_part_files(str(tmp_path / "nonexistent")) == 0

    def test_glob_failure_returns_zero(self, tmp_path: Path) -> None:
        with patch("nyrx.player.Path") as mock_path:
            mock_path.return_value.glob.side_effect = PermissionError("denied")
            assert clean_part_files(str(tmp_path)) == 0

    def test_unlink_failure_is_skipped(self) -> None:
        """A single unlink raising must not abort the whole sweep."""
        bad = MagicMock()
        bad.unlink.side_effect = PermissionError("denied")
        with patch(
            "nyrx.player.Path", return_value=SimpleNamespace(glob=lambda p: [bad])
        ):
            assert clean_part_files("/some/dir") == 0


class TestCleanupOrphanedSockets:
    def test_removes_old_sock_files(self, tmp_path: Path) -> None:
        sock = tmp_path / "dead.sock"
        sock.write_text("x")
        os.utime(sock, (0, 0))  # mtime = epoch → age huge

        _cleanup_orphaned_sockets(sock_dir=tmp_path)

        assert not sock.exists()

    def test_preserves_fresh_sock_files(self, tmp_path: Path) -> None:
        sock = tmp_path / "live.sock"
        sock.write_text("x")

        _cleanup_orphaned_sockets(sock_dir=tmp_path)

        assert sock.exists()

    def test_skips_non_sock_files(self, tmp_path: Path) -> None:
        txt = tmp_path / "note.txt"
        txt.write_text("x")
        os.utime(txt, (0, 0))  # old, but not .sock

        _cleanup_orphaned_sockets(sock_dir=tmp_path)

        assert txt.exists()

    def test_missing_directory_is_noop(self, tmp_path: Path) -> None:
        _cleanup_orphaned_sockets(sock_dir=tmp_path / "nonexistent")

    def test_permission_error_on_unlink_handled(self, tmp_path: Path) -> None:
        locked_dir = tmp_path / "locked"
        locked_dir.mkdir()
        sock = locked_dir / "locked.sock"
        sock.write_text("x")
        os.utime(sock, (0, 0))  # old enough to trigger unlink
        os.chmod(locked_dir, 0o555)  # remove write → unlink raises PermissionError
        try:
            _cleanup_orphaned_sockets(sock_dir=locked_dir)
        finally:
            os.chmod(locked_dir, 0o755)  # restore so tmp_path cleanup works

    def test_age_threshold_boundary(self, tmp_path: Path) -> None:
        """Verify max_age boundary: old files removed, young files kept."""
        old = tmp_path / "old.sock"
        old.write_text("x")
        os.utime(old, (0, 0))  # mtime = epoch → age huge

        young = tmp_path / "young.sock"
        young.write_text("x")  # mtime ≈ now → age ≈ 0

        _cleanup_orphaned_sockets(sock_dir=tmp_path, max_age=100_000_000)

        assert not old.exists()
        assert young.exists()
