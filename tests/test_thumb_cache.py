# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``sources/tv_movies/thumb_cache.py``: poster caching.

Locks the ``w342`` size (BUG-3 resolution) and the empty-path guard /
cached short-circuit so bookmark rendering never re-downloads every row.
"""

from __future__ import annotations

from unittest.mock import patch

from nyrx.sources.tv_movies.thumb_cache import cache_tv_poster


class _Resp:
    def __init__(self, content=b"jpg-data", ok=True):
        self._content = content
        self.ok = ok

    @property
    def content(self):
        return self._content

    def raise_for_status(self):
        if not self.ok:
            raise Exception("http error")


class TestCacheTvPoster:
    def test_empty_poster_path_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "nyrx.sources.tv_movies.thumb_cache.TV_THUMBS_DIR", tmp_path
        )
        with patch("nyrx.sources.tv_movies.thumb_cache.requests.get") as m_get:
            assert cache_tv_poster(5, "") is None
        m_get.assert_not_called()

    def test_cached_file_returned_without_download(self, tmp_path, monkeypatch) -> None:
        cached = tmp_path / "5.jpg"
        cached.write_bytes(b"old")
        monkeypatch.setattr(
            "nyrx.sources.tv_movies.thumb_cache.TV_THUMBS_DIR", tmp_path
        )
        with patch("nyrx.sources.tv_movies.thumb_cache.requests.get") as m_get:
            out = cache_tv_poster(5, "/post.jpg")
        assert out == cached
        m_get.assert_not_called()

    def test_downloads_and_writes_w342(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "nyrx.sources.tv_movies.thumb_cache.TV_THUMBS_DIR", tmp_path
        )
        with patch(
            "nyrx.sources.tv_movies.thumb_cache.requests.get",
            return_value=_Resp(b"jpg"),
        ) as m_get:
            out = cache_tv_poster(5, "/post.jpg")
        assert out == tmp_path / "5.jpg"
        assert (tmp_path / "5.jpg").read_bytes() == b"jpg"
        m_get.assert_called_once_with(
            "https://image.tmdb.org/t/p/w342/post.jpg", timeout=10
        )

    def test_request_failure_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "nyrx.sources.tv_movies.thumb_cache.TV_THUMBS_DIR", tmp_path
        )
        with patch(
            "nyrx.sources.tv_movies.thumb_cache.requests.get",
            side_effect=Exception("boom"),
        ):
            assert cache_tv_poster(5, "/post.jpg") is None

    def test_http_error_status_returns_none(self, tmp_path, monkeypatch) -> None:
        monkeypatch.setattr(
            "nyrx.sources.tv_movies.thumb_cache.TV_THUMBS_DIR", tmp_path
        )
        with patch(
            "nyrx.sources.tv_movies.thumb_cache.requests.get",
            return_value=_Resp(b"x", ok=False),
        ):
            assert cache_tv_poster(5, "/post.jpg") is None
