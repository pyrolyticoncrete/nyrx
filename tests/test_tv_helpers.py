# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the Lua-sandbox helper bridge in ``sources/tv_movies/helpers.py``.

Covers the HLS parsing, PoW, custom base64, crypto, URL, and HTTP helpers
that probe scripts call through the sandbox. Expected values are derived
from the HLS spec / stdlib oracles, not from reading the implementation.
"""

from __future__ import annotations

import base64
import hashlib
from unittest.mock import ANY, patch

from nyrx.sources.tv_movies.helpers import (
    _parse_hls_master,
    custom_b64decode,
    hls_analyze,
    hls_get_variants,
    hmac_sha256,
    http_get,
    http_post,
    md5,
    pow_find_nonce,
    sha256,
    url_parse,
)

_STD_ALPHABET = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+/"


def _master(
    renditions: list[tuple[int, str | None, str | None]],
    media: str = "",
) -> str:
    """Build a master playlist from ``(bandwidth, resolution, codecs)``."""
    lines = ["#EXTM3U", "#EXT-X-VERSION:6"]
    if media:
        lines.append(media)
    for bw, res, codecs in renditions:
        attrs = f"BANDWIDTH={bw}"
        if res:
            attrs += f",RESOLUTION={res}"
        if codecs:
            attrs += f',CODECS="{codecs}"'
        lines.append(f"#EXT-X-STREAM-INF:{attrs}")
        lines.append(f"v_{bw}.m3u8")
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------
# _parse_hls_master
# ---------------------------------------------------------------------------


class TestParseHlsMasterBestRendition:
    def test_highest_bandwidth_wins(self) -> None:
        text = _master(
            [
                (1000, "854x480", "avc1.66.30,mp4a.40.2"),
                (5000, "1920x1080", "avc1.640028,mp4a.40.2"),
                (2000, "1280x720", "avc1.4d4020,mp4a.40.2"),
            ]
        )
        info = _parse_hls_master(text)
        assert info["format"] == "hls"
        assert info["resolution"] == "1920x1080"
        assert info["stream_count"] == 3
        assert info["video_codec"] == "h264"

    def test_no_renditions(self) -> None:
        info = _parse_hls_master("#EXTM3U\n")
        assert info["format"] == "hls"
        assert info["stream_count"] == 0
        assert info["resolution"] is None
        assert info["video_codec"] is None

    def test_malformed_bandwidth_defaults_to_zero(self) -> None:
        info = _parse_hls_master(_master([("abc", "1920x1080", None)]))
        assert info["stream_count"] == 1
        assert info["resolution"] == "1920x1080"


class TestParseHlsMasterCodecMapping:
    def test_avc_maps_to_h264(self) -> None:
        info = _parse_hls_master(
            _master([(5000, "1920x1080", "avc1.640028,mp4a.40.2")])
        )
        assert info["video_codec"] == "h264"

    def test_hev_maps_to_h265(self) -> None:
        info = _parse_hls_master(_master([(5000, "1920x1080", "hev1.1.6.L120")]))
        assert info["video_codec"] == "h265"

    def test_codec_absent_is_none(self) -> None:
        info = _parse_hls_master(_master([(5000, "1920x1080", None)]))
        assert info["video_codec"] is None


class TestParseHlsMasterLanguageExtraction:
    _AUDIO_SUBS = (
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES,'
        'AUTOSELECT=YES,LANGUAGE="en",URI="a-en.m3u8"\n'
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English Alt",DEFAULT=NO,'
        'AUTOSELECT=YES,LANGUAGE="en",URI="a-en2.m3u8"\n'
        '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="Fran\u00e7ais",DEFAULT=NO,'
        'AUTOSELECT=YES,LANGUAGE="fr",URI="a-fr.m3u8"\n'
        '#EXT-X-MEDIA:TYPE=SUBTITLES,GROUP-ID="subs",NAME="English",DEFAULT=YES,'
        'AUTOSELECT=YES,LANGUAGE="en",URI="s-en.m3u8"'
    )

    def test_audio_deduped_and_ordered(self) -> None:
        info = _parse_hls_master(_master([(5000, "1920x1080", None)], self._AUDIO_SUBS))
        assert info["audio_languages"] == ["en", "fr"]
        assert info["subtitle_languages"] == ["en"]

    def test_subtitles_do_not_raise_and_are_captured(self) -> None:
        info = _parse_hls_master(_master([(5000, "1920x1080", None)], self._AUDIO_SUBS))
        assert info["subtitle_languages"] == ["en"]

    def test_no_subtitle_line_guards_missing_group(self) -> None:
        audio_only = (
            '#EXT-X-MEDIA:TYPE=AUDIO,GROUP-ID="aud",NAME="English",DEFAULT=YES,'
            'AUTOSELECT=YES,LANGUAGE="en",URI="a-en.m3u8"'
        )
        info = _parse_hls_master(_master([(5000, "1920x1080", None)], audio_only))
        assert info["subtitle_languages"] == []
        assert info["audio_languages"] == ["en"]


# ---------------------------------------------------------------------------
# hls_get_variants
# ---------------------------------------------------------------------------


class TestHlsGetVariantsGuard:
    def test_media_playlist_returns_empty(self) -> None:
        body = "#EXTM3U\n#EXTINF:10,\nseg.ts\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            assert hls_get_variants("http://host/master.m3u8") == []

    def test_fetch_failure_returns_empty(self) -> None:
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=None):
            assert hls_get_variants("http://host/master.m3u8") == []


class TestHlsGetVariantsParse:
    def test_relative_url_join_and_height_parse(self) -> None:
        body = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1920x1080\n"
            "stream_1080.m3u8\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=2500,RESOLUTION=1280x720\n"
            "stream_720.m3u8\n"
        )
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            variants = hls_get_variants("https://host/path/master.m3u8")
        assert variants == [
            {
                "url": "https://host/path/stream_1080.m3u8",
                "resolution": 1080,
                "bandwidth": 5000,
            },
            {
                "url": "https://host/path/stream_720.m3u8",
                "resolution": 720,
                "bandwidth": 2500,
            },
        ]

    def test_malformed_bandwidth_defaults_to_zero(self) -> None:
        body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=abc\nbroken.m3u8\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            variants = hls_get_variants("https://host/path/master.m3u8")
        assert variants[0]["bandwidth"] == 0

    def test_absolute_url_left_untouched(self) -> None:
        body = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1920x1080\n"
            "https://cdn.example.com/high.m3u8\n"
        )
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            variants = hls_get_variants("https://host/path/master.m3u8")
        assert variants[0]["url"] == "https://cdn.example.com/high.m3u8"

    def test_resolution_height_not_integer_keeps_raw(self) -> None:
        body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1920xabc\nv.m3u8\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            variants = hls_get_variants("https://host/path/master.m3u8")
        assert variants[0]["resolution"] == "1920xabc"

    def test_resolution_missing_width_separator_keeps_raw(self) -> None:
        body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000,RESOLUTION=1080\nv.m3u8\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            variants = hls_get_variants("https://host/path/master.m3u8")
        assert variants[0]["resolution"] == "1080"

    def test_stream_inf_without_following_variant_skipped(self) -> None:
        body = "#EXTM3U\n#EXT-X-STREAM-INF:BANDWIDTH=5000\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            assert hls_get_variants("https://host/path/master.m3u8") == []

    def test_comment_line_after_stream_inf_skipped(self) -> None:
        body = (
            "#EXTM3U\n"
            "#EXT-X-STREAM-INF:BANDWIDTH=5000\n"
            "#EXT-X-I-FRAME-STREAM-INF:BANDWIDTH=1000\n"
        )
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            assert hls_get_variants("https://host/path/master.m3u8") == []


# ---------------------------------------------------------------------------
# hls_analyze
# ---------------------------------------------------------------------------


class TestHlsAnalyze:
    def test_m3u8_with_query_string_parses_master(self) -> None:
        body = _master([(5000, "1920x1080", "avc1.640028")])
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            result = hls_analyze("https://host/master.m3u8?token=abc")
        assert result["format"] == "hls"
        assert result["resolution"] == "1920x1080"
        assert result["stream_count"] == 1
        assert result["video_codec"] == "h264"

    def test_mp4_does_not_fetch(self) -> None:
        with patch("nyrx.sources.tv_movies.helpers._fetch_text") as fetch:
            result = hls_analyze("https://host/movie.mp4")
        fetch.assert_not_called()
        assert result["format"] == "mp4"

    def test_unknown_extension_unrecognised(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.helpers._fetch_text", return_value="not-hls"
        ):
            result = hls_analyze("https://host/thing.xyz")
        assert result["format"] == "xyz"
        assert result["notes"] == "unrecognised .xyz"

    def test_m3u8_fetch_failure(self) -> None:
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=None):
            result = hls_analyze("https://host/master.m3u8")
        assert result["format"] == "hls"
        assert result["notes"] == "could not fetch playlist"

    def test_media_playlist_note(self) -> None:
        body = "#EXTM3U\n#EXTINF:10,\nseg.ts\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            result = hls_analyze("https://host/playlist.m3u8")
        assert result["format"] == "hls"
        assert result["notes"] == "media playlist (no master)"

    def test_unknown_extension_with_hls_body_parses_master(self) -> None:
        body = _master([(5000, "1920x1080", "avc1.640028")])
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            result = hls_analyze("https://host/stream.xhtml")
        assert result["format"] == "hls"
        assert result["resolution"] == "1920x1080"
        assert result["video_codec"] == "h264"

    def test_unknown_extension_with_media_playlist_body(self) -> None:
        body = "#EXTM3U\n#EXTINF:10,\nseg.ts\n"
        with patch("nyrx.sources.tv_movies.helpers._fetch_text", return_value=body):
            result = hls_analyze("https://host/stream.xhtml")
        assert result["format"] == "hls"
        assert result["notes"] == "media playlist (no master)"


# ---------------------------------------------------------------------------
# pow_find_nonce
# ---------------------------------------------------------------------------


def _pow_valid(prefix: str, difficulty: int, nonce: int) -> bool:
    h = hashlib.sha256((prefix + str(nonce)).encode()).digest()
    nb, nr = difficulty // 8, difficulty % 8
    if not all(h[i] == 0 for i in range(nb)):
        return False
    if nr:
        return (h[nb] & (0xFF << (8 - nr))) == 0
    return True


class TestPowFindNonce:
    def test_difficulty_8_zero_byte(self) -> None:
        nonce = pow_find_nonce("PoWtest", 8)
        assert nonce is not None
        assert hashlib.sha256(("PoWtest" + str(nonce)).encode()).hexdigest()[:2] == "00"
        assert _pow_valid("PoWtest", 8, nonce)

    def test_difficulty_1_top_bit(self) -> None:
        nonce = pow_find_nonce("PoWtest", 1)
        assert nonce is not None
        assert _pow_valid("PoWtest", 1, nonce)

    def test_difficulty_0_returns_zero_immediately(self) -> None:
        assert pow_find_nonce("PoWtest", 0) == 0

    def test_exhaustion_returns_none(self) -> None:
        assert pow_find_nonce("PoWtest", 8, max_attempts=3) is None


# ---------------------------------------------------------------------------
# custom_b64decode
# ---------------------------------------------------------------------------


class TestCustomB64Decode:
    def test_round_trip_via_stdlib_oracle(self) -> None:
        for raw in (b"hello", b"he", b"a", b"", b"\x00\xffmid"):
            encoded = base64.b64encode(raw).decode()
            assert custom_b64decode(encoded, _STD_ALPHABET) == raw.decode(
                "utf-8", "replace"
            )

    def test_padded_input(self) -> None:
        assert custom_b64decode("aGVsbG8=", _STD_ALPHABET) == "hello"

    def test_unpadded_input_gets_padding(self) -> None:
        assert custom_b64decode("aGVsbG8", _STD_ALPHABET) == "hello"

    def test_unknown_char_treated_as_pad(self) -> None:
        assert custom_b64decode("aGVsbG8!", _STD_ALPHABET) == "hello"


# ---------------------------------------------------------------------------
# Crypto KATs
# ---------------------------------------------------------------------------


class TestCryptoKnownAnswers:
    def test_sha256_empty_kat(self) -> None:
        assert (
            sha256("")
            == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
        )

    def test_md5_empty_kat(self) -> None:
        assert md5("") == "d41d8cd98f00b204e9800998ecf8427e"

    def test_hmac_sha256_concatenation_contract(self) -> None:
        assert hmac_sha256("k", "m") == hashlib.sha256(b"km").hexdigest()


# ---------------------------------------------------------------------------
# url_parse
# ---------------------------------------------------------------------------


class TestUrlParse:
    def test_query_dict_collapse(self) -> None:
        parsed = url_parse("https://x.com/p?a=1&b=2&b=3")
        assert parsed["scheme"] == "https"
        assert parsed["netloc"] == "x.com"
        assert parsed["path"] == "/p"
        assert parsed["query_dict"] == {"a": "1", "b": ["2", "3"]}

    def test_no_query(self) -> None:
        assert url_parse("https://x.com/p")["query_dict"] == {}


# ---------------------------------------------------------------------------
# http_get / http_post
# ---------------------------------------------------------------------------


class TestHttpHelpers:
    def test_http_get_success_envelope(self) -> None:
        class _Resp:
            status_code = 200
            ok = True
            text = "body"
            url = "http://final"
            headers = {"content-type": "text/plain"}

        with patch(
            "nyrx.sources.tv_movies.helpers.requests.get", return_value=_Resp()
        ) as get:
            result = http_get("http://host/x")
        get.assert_called_once_with("http://host/x", headers=ANY, timeout=15)
        assert result == {
            "status": 200,
            "headers": {"content-type": "text/plain"},
            "body": "body",
            "ok": True,
            "url": "http://final",
        }

    def test_http_get_exception_envelope(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.helpers.requests.get",
            side_effect=ConnectionError("boom"),
        ):
            result = http_get("http://host/x")
        assert result["status"] == 0
        assert result["ok"] is False
        assert "boom" in result["error"]
        assert result["url"] == "http://host/x"

    def test_http_post_exception_envelope(self) -> None:
        with patch(
            "nyrx.sources.tv_movies.helpers.requests.post",
            side_effect=ConnectionError("boom"),
        ):
            result = http_post("http://host/x")
        assert result["status"] == 0
        assert result["ok"] is False
        assert "boom" in result["error"]
        assert result["url"] == "http://host/x"

    def test_fetch_text_returns_none_on_http_error(self) -> None:
        from nyrx.sources.tv_movies import helpers as _h

        class _Resp:
            def raise_for_status(self):
                raise ConnectionError("404")

        with patch("nyrx.sources.tv_movies.helpers.requests.get", return_value=_Resp()):
            assert _h._fetch_text("http://host/x") is None

    def test_fetch_text_returns_body_on_success(self) -> None:
        from nyrx.sources.tv_movies import helpers as _h

        class _Resp:
            text = "EXTM3U"

            def raise_for_status(self):
                return None

        with patch("nyrx.sources.tv_movies.helpers.requests.get", return_value=_Resp()):
            assert _h._fetch_text("http://host/x") == "EXTM3U"

    def test_http_post_passes_json_body(self) -> None:
        class _Resp:
            status_code = 201
            ok = True
            text = "created"
            url = "http://final"
            headers = {}

        with patch(
            "nyrx.sources.tv_movies.helpers.requests.post", return_value=_Resp()
        ) as post:
            result = http_post("http://host/x", json_data={"a": 1})
        post.assert_called_once_with(
            "http://host/x", json={"a": 1}, headers=ANY, timeout=15
        )
        assert result["status"] == 201
        assert result["ok"] is True
