# SPDX-License-Identifier: AGPL-3.0-only

"""Conformance tests: every source module implements the Source ABC.

These tests verify that source classes fulfil the abstract interface
contract: they don't test behavioral correctness (that's what the
per-source test files are for).

The primary check is Python's own ``__abstractmethods__``: any remaining
abstract method prevents instantiation with a ``TypeError``.  By checking
the frozenset is empty we prove the class is concretely instantiable
without actually calling a constructor (which may have side effects).
"""

from __future__ import annotations

import inspect

import pytest

from nyrx.sources import Source
from nyrx.sources.radio_source import RadioSource
from nyrx.sources.soundcloud import SoundCloudSource
from nyrx.sources.tv_movies import TVMoviesSource
from nyrx.sources.youtube import YouTubeSource

SOURCES: list[type[Source]] = [
    YouTubeSource,
    SoundCloudSource,
    RadioSource,
    TVMoviesSource,
]

ABSTRACT_NAMES = frozenset(
    {
        "name",
        "icon",
        "handles_url",
        "search",
        "fetch_metadata",
        "play_params",
        "play",
    }
)


class TestSourceConformance:
    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_all_abstract_methods_implemented(self, cls: type[Source]) -> None:
        remaining = cls.__abstractmethods__
        assert remaining == frozenset(), (
            f"{cls.__name__} still has abstract methods: {remaining}"
        )

    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_name_and_icon_are_properties(self, cls: type[Source]) -> None:
        for attr in ("name", "icon"):
            val = cls.__dict__.get(attr)
            assert isinstance(val, property), (
                f"{cls.__name__}.{attr} should be a @property, got {type(val).__name__}"
            )

    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_property_return_types_are_str(self, cls: type[Source]) -> None:
        for attr in ("name", "icon"):
            prop = cls.__dict__[attr]
            sig = inspect.signature(prop.fget)
            ret = sig.return_annotation
            assert ret is str or ret == "str", (
                f"{cls.__name__}.{attr} should return str, got {ret!r}"
            )

    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_handles_url_returns_bool(self, cls: type[Source]) -> None:
        sig = inspect.signature(cls.handles_url)
        assert "url" in sig.parameters
        ret = sig.return_annotation
        assert ret is bool or ret == "bool", (
            f"{cls.__name__}.handles_url should return bool, got {ret!r}"
        )

    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_search_accepts_query_and_limit(self, cls: type[Source]) -> None:
        sig = inspect.signature(cls.search)
        assert "query" in sig.parameters
        assert "limit" in sig.parameters

    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_play_params_signature(self, cls: type[Source]) -> None:
        sig = inspect.signature(cls.play_params)
        for param in ("data", "audio_only", "ytdl_format", "start_pos"):
            assert param in sig.parameters, (
                f"{cls.__name__}.play_params missing parameter {param!r}"
            )
        ret = sig.return_annotation
        if ret is not inspect.Parameter.empty:
            assert "dict" in str(ret) or "dict" in str(
                getattr(ret, "__origin__", "")
            ), f"{cls.__name__}.play_params should return dict-like, got {ret!r}"

    @pytest.mark.parametrize("cls", SOURCES, ids=lambda c: c.__name__)
    def test_play_accepts_standard_params(self, cls: type[Source]) -> None:
        sig = inspect.signature(cls.play)
        for param in ("data", "audio_only", "ytdl_format", "start_pos"):
            assert param in sig.parameters, (
                f"{cls.__name__}.play missing parameter {param!r}"
            )
