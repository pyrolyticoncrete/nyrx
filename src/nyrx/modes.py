# SPDX-License-Identifier: AGPL-3.0-only

"""Source / View enums and the MODES navigation registry."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class Source(StrEnum):
    YOUTUBE = "youtube"
    SOUNDCLOUD = "soundcloud"
    RADIO = "radio"
    TV_MOVIES = "tv_movies"


class View(StrEnum):
    LANDING = "landing"
    RESULTS = "results"


@dataclass(frozen=True)
class ModeSpec:
    key: Source
    label: str
    keybind: str
    welcome_widget_id: str


MODES: dict[Source, ModeSpec] = {
    Source.YOUTUBE: ModeSpec(Source.YOUTUBE, "Youtube", "f1", "#empty-state"),
    Source.SOUNDCLOUD: ModeSpec(Source.SOUNDCLOUD, "Soundcloud", "f2", "#sc-home"),
    Source.RADIO: ModeSpec(Source.RADIO, "Radio", "f3", "#radio-area"),
    Source.TV_MOVIES: ModeSpec(Source.TV_MOVIES, "TV & Movies", "f4", "#tv-home"),
}
