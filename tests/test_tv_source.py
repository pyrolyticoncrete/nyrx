# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``sources/tv_movies/__init__.py``: the ``TVMoviesSource`` adapter.

Instantiated via ``TVMoviesSource.__new__(TVMoviesSource)`` (never the real
constructor: it spawns a proxy-refresh thread and calls ``load_keys``) with a
``FakeDispatcher`` and ``_server_mode``/``_search_filter`` set directly.
Covers download params, probe-param assembly, server-mode routing, result
mapping, subtitle download/merge, search pagination, and server cycling.
"""

from __future__ import annotations

from unittest.mock import patch

from nyrx.sources.tv_movies import TVMoviesSource, _download_one, _download_subtitles
from tests.fakes import FakeDispatcher


def make_source(dispatcher=None, server_mode="auto", search_filter="all"):
    src = TVMoviesSource.__new__(TVMoviesSource)
    src._dispatcher = dispatcher or FakeDispatcher(server_names=["a", "b"])
    src._server_mode = server_mode
    src._search_filter = search_filter
    return src


def _fake_resp(payload, ok=True):
    class _Resp:
        def __init__(self, body):
            self.body = body
            self.ok = ok

        @property
        def content(self):
            return self.body

        @property
        def text(self):
            return self.body.decode() if isinstance(self.body, bytes) else self.body

    return _Resp(payload)


# ---------------------------------------------------------------------------
# download_params
# ---------------------------------------------------------------------------


class TestDownloadParams:
    def test_direct_tmdb_id(self) -> None:
        src = make_source()
        out = src.download_params(
            {
                "tmdb_id": 5,
                "title": "T",
                "media_type": "tv",
                "season": 2,
                "episode": 3,
                "year": 2020,
            }
        )
        assert out["tmdb_id"] == 5
        assert out["yt_id"] == "tmdb_5"
        assert out["source"] == "tv_movies"
        assert out["season"] == 2
        assert out["episode"] == 3
        assert out["year"] == 2020

    def test_tmdb_prefix_yt_id(self) -> None:
        src = make_source()
        out = src.download_params({"yt_id": "tmdb_42"})
        assert out["tmdb_id"] == 42
        assert out["media_type"] == "movie"

    def test_season_number_fallback(self) -> None:
        src = make_source()
        out = src.download_params(
            {
                "tmdb_id": 5,
                "season_number": 7,
                "episode_number": 8,
            }
        )
        assert out["season"] == 7
        assert out["episode"] == 8

    def test_neither_id_returns_none(self) -> None:
        src = make_source()
        assert src.download_params({"yt_id": "abc"}) is None
        assert src.download_params({}) is None


# ---------------------------------------------------------------------------
# play_params: probe params
# ---------------------------------------------------------------------------


class TestPlayParamsProbeParams:
    def test_tv_defaults_season_episode_to_one(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        src.play_params({"tmdb_id": 5, "media_type": "tv"})
        params, _ = dispatcher.probe_calls[-1]
        assert params["season"] == 1
        assert params["episode"] == 1
        assert params["media_type"] == "tv"
        assert params["tmdb_id"] == 5

    def test_tv_uses_provided_season_episode(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        src.play_params({"tmdb_id": 5, "media_type": "tv", "season": 3, "episode": 4})
        params, _ = dispatcher.probe_calls[-1]
        assert params["season"] == 3
        assert params["episode"] == 4

    def test_movie_omits_season_episode_even_when_passed(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        src.play_params({"tmdb_id": 5, "media_type": "movie", "season": 9})
        params, _ = dispatcher.probe_calls[-1]
        assert "season" not in params
        assert "episode" not in params

    def test_quality_passthrough(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        src.play_params({"tmdb_id": 5, "media_type": "movie"}, quality_height=720)
        params, _ = dispatcher.probe_calls[-1]
        assert params["quality"] == 720

    def test_no_quality_when_omitted(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        src.play_params({"tmdb_id": 5, "media_type": "movie"})
        params, _ = dispatcher.probe_calls[-1]
        assert "quality" not in params


class TestPlayParamsServerMode:
    def test_auto_mode_passes_none(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher, server_mode="auto")
        src.play_params({"tmdb_id": 5, "media_type": "movie"})
        _, server_name = dispatcher.probe_calls[-1]
        assert server_name is None

    def test_manual_mode_passes_server_name(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher, server_mode="srv2")
        src.play_params({"tmdb_id": 5, "media_type": "movie"})
        _, server_name = dispatcher.probe_calls[-1]
        assert server_name == "srv2"

    def test_queued_server_mode_overrides(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher, server_mode="auto")
        src.play_params(
            {"tmdb_id": 5, "media_type": "movie", "_queued_server_mode": "srv3"}
        )
        _, server_name = dispatcher.probe_calls[-1]
        assert server_name == "srv3"

    def test_queued_auto_resets_to_none(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher, server_mode="srv2")
        src.play_params(
            {"tmdb_id": 5, "media_type": "movie", "_queued_server_mode": "auto"}
        )
        _, server_name = dispatcher.probe_calls[-1]
        assert server_name is None


# ---------------------------------------------------------------------------
# play_params: result mapping
# ---------------------------------------------------------------------------


class TestPlayParamsResultMapping:
    def test_full_result_mapping(self) -> None:
        dispatcher = FakeDispatcher(
            probe_result={
                "stream_url": "http://stream",
                "stream_headers": {"Referer": "http://ref"},
                "audio_urls": ["http://audio"],
                "subs": [{"url": "http://sub.vtt", "lang": "en"}],
            }
        )
        src = make_source(dispatcher)
        with patch(
            "nyrx.sources.tv_movies._download_subtitles",
            return_value=("/tmp/x", [("/tmp/x/a.vtt", "en")]),
        ):
            out = src.play_params(
                {"tmdb_id": 5, "media_type": "movie"},
                start_pos=1.5,
            )
        assert out["url"] == "http://stream"
        assert out["referrer"] == "http://ref"
        assert out["subs"] == ["/tmp/x/a.vtt"]
        assert out["_subs_tmpdir"] == "/tmp/x"
        assert out["audio_urls"] == ["http://audio"]
        assert out["stream_headers"] == {"Referer": "http://ref"}
        assert out["start_pos"] == 1.5
        assert out["source"] == "tv_movies"
        assert out["tracker_media_type"] == "movie"

    def test_bug2_no_yt_id_does_not_raise(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://stream"})
        src = make_source(dispatcher)
        with patch(
            "nyrx.sources.tv_movies._download_subtitles", return_value=(None, [])
        ):
            out = src.play_params({"tmdb_id": 5, "media_type": "movie"})
        assert out["yt_id"] == ""
        assert out["url"] == "http://stream"

    def test_no_stream_headers_yields_none_referrer(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://stream"})
        src = make_source(dispatcher)
        with patch(
            "nyrx.sources.tv_movies._download_subtitles", return_value=(None, [])
        ):
            out = src.play_params({"tmdb_id": 5, "media_type": "movie"})
        assert out["referrer"] is None
        assert out["stream_headers"] is None

    def test_probe_none_returns_fallback(self) -> None:
        dispatcher = FakeDispatcher(probe_result=None)
        src = make_source(dispatcher)
        out = src.play_params({"tmdb_id": 5, "media_type": "movie", "title": "T"})
        assert out == {"yt_id": "", "title": "T"}

    def test_no_tmdb_id_returns_fallback(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        out = src.play_params({"yt_id": "plain", "title": "T"})
        assert out == {"yt_id": "plain", "title": "T"}
        assert dispatcher.probe_calls == []

    def test_tmdb_prefix_yt_id_resolves_tmdb_id(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        src.play_params({"yt_id": "tmdb_9", "media_type": "movie"})
        params, _ = dispatcher.probe_calls[-1]
        assert params["tmdb_id"] == 9

    def test_download_subtitles_exception_degrades(self) -> None:
        dispatcher = FakeDispatcher(
            probe_result={
                "stream_url": "http://stream",
                "subs": [{"url": "http://sub.vtt"}],
            }
        )
        src = make_source(dispatcher)
        with patch(
            "nyrx.sources.tv_movies._download_subtitles",
            side_effect=RuntimeError("boom"),
        ):
            out = src.play_params({"tmdb_id": 5, "media_type": "movie"})
        assert out["url"] == "http://stream"
        assert out["subs"] == []
        assert out["_subs_tmpdir"] is None

    def test_empty_subs_calls_downloader_with_empty_list(self) -> None:
        dispatcher = FakeDispatcher(
            probe_result={
                "stream_url": "http://stream",
            }
        )
        src = make_source(dispatcher)
        with patch(
            "nyrx.sources.tv_movies._download_subtitles", return_value=(None, [])
        ) as dl:
            src.play_params({"tmdb_id": 5, "media_type": "movie"})
        assert dl.call_args.args[0] == []

    def test_tracker_fields_for_tv(self) -> None:
        dispatcher = FakeDispatcher(probe_result={"stream_url": "http://s"})
        src = make_source(dispatcher)
        with patch(
            "nyrx.sources.tv_movies._download_subtitles", return_value=(None, [])
        ):
            out = src.play_params(
                {"tmdb_id": 5, "media_type": "tv", "season": 3, "episode": 4}
            )
        assert out["tracker_media_type"] == "tv"
        assert out["tracker_season_number"] == 3
        assert out["tracker_episode_number"] == 4


# ---------------------------------------------------------------------------
# search
# ---------------------------------------------------------------------------


def _search_page(ids):
    return [
        {
            "tmdb_id": i,
            "title": f"T{i}",
            "media_type": "movie",
            "poster": f"/p{i}.jpg",
            "year": "2020",
            "rating": 7.0,
            "vote_count": 10,
            "release_date": "2020-01-01",
            "genre_ids": [1],
            "overview": "o",
        }
        for i in ids
    ]


class TestSearchPagination:
    def test_limit_20_one_page(self) -> None:
        src = make_source(search_filter="all")
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search",
            return_value=_search_page([1, 2]),
        ) as search:
            src.search("q", limit=20)
        assert search.call_count == 1
        assert search.call_args.kwargs["page"] == 1

    def test_limit_40_two_pages(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search", return_value=_search_page([1])
        ) as search:
            src.search("q", limit=40)
        assert search.call_count == 2
        assert [c.kwargs["page"] for c in search.call_args_list] == [1, 2]

    def test_limit_500_capped_at_five_pages(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search", return_value=_search_page([1])
        ) as search:
            src.search("q", limit=500)
        assert search.call_count == 5

    def test_exact_multiple_of_twenty(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search", return_value=_search_page([1])
        ) as search:
            src.search("q", limit=40)
        assert search.call_count == 2

    def test_empty_page_stops_early(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search",
            side_effect=[_search_page([1]), []],
        ) as search:
            src.search("q", limit=100)
        assert search.call_count == 2


class TestSearchMapping:
    def test_result_mapping_and_truncation(self) -> None:
        src = make_source()
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search",
            return_value=_search_page([1, 2]),
        ):
            out = src.search("q", limit=1)
        assert len(out) == 1
        r = out[0]
        assert r["yt_id"] == "tmdb_1"
        assert r["thumbnail_url"] == "https://image.tmdb.org/t/p/w342/p1.jpg"
        assert r["source"] == "tv_movies"
        assert r["media_type"] == "movie"
        assert r["tmdb_id"] == 1
        assert r["duration"] == 0

    def test_empty_poster_yields_empty_thumbnail(self) -> None:
        src = make_source()
        page = _search_page([1])
        page[0]["poster"] = ""
        with patch("nyrx.sources.tv_movies.tmdb_cache.search", return_value=page):
            out = src.search("q", limit=1)
        assert out[0]["thumbnail_url"] == ""

    def test_filter_routing(self) -> None:
        src = make_source(search_filter="tv")
        with patch(
            "nyrx.sources.tv_movies.tmdb_cache.search", return_value=[]
        ) as search:
            src.search("q")
        assert search.call_args.kwargs["media_type"] == "tv"


# ---------------------------------------------------------------------------
# cycle_server / current_server_display
# ---------------------------------------------------------------------------


class TestCycleServer:
    def test_wraps_through_names(self) -> None:
        src = make_source(FakeDispatcher(server_names=["a", "b"]), server_mode="auto")
        assert src.cycle_server() == "a"
        assert src.cycle_server() == "b"
        assert src.cycle_server() == "auto"

    def test_unknown_mode_resets_to_auto(self) -> None:
        src = make_source(FakeDispatcher(server_names=["a", "b"]), server_mode="zzz")
        assert src.cycle_server() == "auto"

    def test_empty_list_returns_no_configs(self) -> None:
        src = make_source(FakeDispatcher(server_names=[]), server_mode="auto")
        assert src.cycle_server() == "__no_configs__"
        assert src._server_mode == "auto"


class TestCurrentServerDisplay:
    def test_no_server(self) -> None:
        src = make_source(FakeDispatcher(server_names=[]))
        assert src.current_server_display() == "No Server"

    def test_auto(self) -> None:
        src = make_source(server_mode="auto")
        assert src.current_server_display() == "Auto"

    def test_server_names_property(self) -> None:
        src = make_source(FakeDispatcher(server_names=["a", "b"]))
        assert src.server_names == ["a", "b"]

    def test_named_server_uses_display_name(self) -> None:
        dispatcher = FakeDispatcher(
            server_names=["srv"], get_server_map={"srv": {"display_name": "Custom"}}
        )
        src = make_source(dispatcher, server_mode="srv")
        assert src.current_server_display() == "Custom"

    def test_unknown_mode_capitalized(self) -> None:
        dispatcher = FakeDispatcher(server_names=["a"])
        src = make_source(dispatcher, server_mode="srv")
        assert src.current_server_display() == "Srv"


# ---------------------------------------------------------------------------
# _download_one
# ---------------------------------------------------------------------------


class TestDownloadOne:
    def test_webvtt_saves_raw_bytes(self, tmp_path) -> None:
        with patch(
            "nyrx.sources.tv_movies.requests.get",
            return_value=_fake_resp(b"WEBVTT\ncue"),
        ) as get:
            out = _download_one(
                {"url": "http://sub.vtt", "lang": "en"}, {}, str(tmp_path), 0
            )
        assert out == (str(tmp_path / "subs_en_0.vtt"), "en")
        assert (tmp_path / "subs_en_0.vtt").read_bytes() == b"WEBVTT\ncue"
        get.assert_called_once_with("http://sub.vtt", headers={}, timeout=15)

    def test_plain_text_saved_as_bytes(self, tmp_path) -> None:
        with patch(
            "nyrx.sources.tv_movies.requests.get", return_value=_fake_resp(b"hello")
        ):
            out = _download_one("http://sub.srt", {}, str(tmp_path), 1)
        assert out == (str(tmp_path / "subs_sub_1.vtt"), "sub")
        assert (tmp_path / "subs_sub_1.vtt").read_bytes() == b"hello"

    def test_hls_two_segments_strips_headers_on_second(self, tmp_path) -> None:
        seg1 = b"WEBVTT\n1\n00:00.000 --> 00:01.000\nA\n"
        seg2 = b"WEBVTT\nX-TIMESTAMP-MAP=LOCAL:00:00:00.000\n2\n00:01.000 --> 00:02.000\nB\n"
        responses = [
            _fake_resp("#EXTM3U\nhttp://seg1\nhttp://seg2\n"),
            _fake_resp(seg1),
            _fake_resp(seg2),
        ]
        with patch("nyrx.sources.tv_movies.requests.get", side_effect=responses):
            out = _download_one({"url": "http://playlist.m3u8"}, {}, str(tmp_path), 2)
        assert out == (str(tmp_path / "subs_sub_2.vtt"), "sub")
        merged = (tmp_path / "subs_sub_2.vtt").read_text()
        assert "A" in merged and "B" in merged
        assert merged.count("WEBVTT") == 1

    def test_hls_no_segment_urls_returns_none(self, tmp_path) -> None:
        with patch(
            "nyrx.sources.tv_movies.requests.get",
            return_value=_fake_resp("#EXTM3U\n#EXT-X-ENDLIST\n"),
        ):
            assert _download_one("http://p.m3u8", {}, str(tmp_path), 0) is None

    def test_hls_all_segments_fail_returns_none(self, tmp_path) -> None:
        with patch(
            "nyrx.sources.tv_movies.requests.get",
            side_effect=[
                _fake_resp("#EXTM3U\nhttp://seg1\n"),
                _fake_resp(None, ok=False),
            ],
        ):
            assert _download_one("http://p.m3u8", {}, str(tmp_path), 0) is None

    def test_hls_segment_without_trailing_newline(self, tmp_path) -> None:
        seg1 = b"WEBVTT\nA"
        with patch(
            "nyrx.sources.tv_movies.requests.get",
            side_effect=[_fake_resp("#EXTM3U\nhttp://seg1\n"), _fake_resp(seg1)],
        ):
            _download_one("http://p.m3u8", {}, str(tmp_path), 0)
        assert (tmp_path / "subs_sub_0.vtt").read_text() == "WEBVTT\nA\n"

    def test_hls_segment_request_exception_continues(self, tmp_path) -> None:
        seg2 = b"B\n"
        responses = [
            _fake_resp("#EXTM3U\nhttp://seg1\nhttp://seg2\n"),
            ConnectionError("boom"),
            _fake_resp(seg2),
        ]
        with patch("nyrx.sources.tv_movies.requests.get", side_effect=responses):
            out = _download_one("http://p.m3u8", {}, str(tmp_path), 0)
        assert out == (str(tmp_path / "subs_sub_0.vtt"), "sub")
        assert (tmp_path / "subs_sub_0.vtt").read_text() == "B\n"

    def test_invalid_entry_forms_return_none(self, tmp_path) -> None:
        assert _download_one(42, {}, str(tmp_path), 0) is None
        assert _download_one({"lang": "en"}, {}, str(tmp_path), 0) is None

    def test_response_not_ok_returns_none(self, tmp_path) -> None:
        with patch(
            "nyrx.sources.tv_movies.requests.get",
            return_value=_fake_resp(b"x", ok=False),
        ):
            assert _download_one("http://sub.vtt", {}, str(tmp_path), 0) is None

    def test_exception_returns_none(self, tmp_path) -> None:
        with patch(
            "nyrx.sources.tv_movies.requests.get", side_effect=ConnectionError("boom")
        ):
            assert _download_one("http://sub.vtt", {}, str(tmp_path), 0) is None


# ---------------------------------------------------------------------------
# _download_subtitles
# ---------------------------------------------------------------------------


class TestDownloadSubtitles:
    def test_empty_entries_returns_none(self) -> None:
        assert _download_subtitles([]) == (None, [])

    def test_downloads_into_prefixed_tmpdir(self) -> None:
        with patch(
            "nyrx.sources.tv_movies._download_one",
            return_value=("/tmp/x/sub.vtt", "en"),
        ):
            tmpdir, paths = _download_subtitles([{"url": "http://s", "lang": "en"}])
        assert paths == [("/tmp/x/sub.vtt", "en")]
        assert tmpdir.startswith("/tmp/subs_")

    def test_builds_headers_from_referrer_and_extra(self) -> None:
        with patch(
            "nyrx.sources.tv_movies._download_one",
            return_value=("/tmp/x/sub.vtt", "en"),
        ) as one:
            _download_subtitles(
                [{"url": "http://s"}],
                referrer="http://ref",
                extra_headers={"X-Extra": "1"},
            )
        headers = one.call_args.args[1]
        assert headers["Referer"] == "http://ref"
        assert headers["X-Extra"] == "1"
        assert "User-Agent" in headers
