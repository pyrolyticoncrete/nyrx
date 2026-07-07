# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for MediaRequest.from_dict(): Phase 0 typed model factory.

Written before the implementation exists (TDD). Expected values derived from
the factory spec in Todo:NP-WidgetV4.md, not from reading models.py.

Audit against the 4+1 filter methodology:
1. Mutation test: each case exercises a different branch of the factory
   (source dispatch, kind heuristic, tmdb_id recovery, None/JSON sanitization)
2. Expected value from spec: every field mapping is specified in the plan doc
3. Refactor-survival: tests the public contract (kind, payload fields), not
   internal structure
4. Each case catches a real bug class that exists today or would be silently
   introduced: field drops, kind misclassification, None propagation,
   JSON-string-as-list, missing-tmdb_id silent MOVIE
5. Input-selection echo risk: inputs were chosen from real data shapes in the
   codebase (YT at playback.py:17-26, SC at api.py:634-648, radio at
   radio.py:70, movie/episode at tv_series.py:437-447, queue at
   playback.py:111-121, bookmark at db.py:97-115), NOT from reverse-engineering
   the factory's branching. Domain-derived, not implementation-derived.

   Tests 14, not 20. Five were dropped after audit: test_empty_dict_defaults
   (echo: restates dataclass defaults, no production call site),
   test_movie_data_as_episode / test_episode_data_as_movie / test_youtube_data_as_radio
   (speculative: no call site does these kind overrides; the ordering mutation
   is covered by test_radio_data_as_audio with realistic input), and
   test_audio_only_override (redundant: same "kwarg wins" pattern as
   test_explicit_source_override, no unique mutation).
"""

from __future__ import annotations

import pytest

from nyrx.models import EpisodeInfo, MediaKind, MediaRequest, MovieInfo, RadioInfo

# ---------------------------------------------------------------------------
# Realistic data shapes from each source
# ---------------------------------------------------------------------------

YT_DATA = {
    "yt_id": "dQw4w9WgXcQ",
    "title": "Rick Astley - Never Gonna Give You Up",
    "channel": "Rick Astley",
    "duration": 212,
    "url": "https://youtube.com/watch?v=dQw4w9WgXcQ",
    "uploader_id": "UCuAXFkgsw1L7xaCfnd5JJOw",
    "permalink": "/watch?v=dQw4w9WgXcQ",
}

SC_DATA = {
    "yt_id": "123456789",
    "title": "Some SoundCloud Track",
    "channel": "Some Artist",
    "duration": 180,
    "url": "https://soundcloud.com/some-artist/some-track",
    "uploader_id": "987654321",
    "permalink": "some-artist",
    "source": "soundcloud",
}

RADIO_DATA = {
    "title": "BBC Radio 4",
    "channel": "BBC Radio 4",
    "source": "radio",
    "countrycode": "GB",
}

TV_MOVIE_DATA = {
    "source": "tv_movies",
    "tmdb_id": 550,
    "title": "Fight Club",
    "tagline": "Mischief. Mayhem. Soap.",
    "rating": 8.4,
    "vote_count": 25000,
    "year": "1999",
    "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
    "overview": "A ticking time bomb of a movie.",
    "runtime": 139,
    "genres": ["Drama"],
    "media_type": "movie",
}

TV_EPISODE_DATA = {
    "source": "tv_movies",
    "tmdb_id": 1668,
    "series_title": "Friends",
    "title": "The One Where Monica Gets a Roommate",
    "season_number": 1,
    "episode_number": 1,
    "rating": 8.2,
    "vote_count": 500,
    "year": "1994",
    "poster_path": "/fMu2qKCZ6C9UQGTl9FI30pSrDEW.jpg",
    "overview": "Monica's new roommate moves in.",
    "media_type": "tv",
    "yt_id": "tmdb_1668",
}

QUEUE_RECONSTRUCTED_DATA = {
    "yt_id": "tmdb_550",
    "title": "Fight Club",
    "source": "tv_movies",
}

BOOKMARK_DATA = {
    "source": "tv_movies",
    "tmdb_id": 550,
    "title": "Fight Club",
    "media_type": "movie",
    "year": "1999",
    "rating": 8.4,
    "vote_count": 0,
    "poster_path": "/pB8BM7pdSp6B6Ih7QZ4DrQ3PmJK.jpg",
    "tagline": None,
    "overview": "A ticking time bomb of a movie.",
    "genres": '["Drama","Thriller"]',
    "runtime": None,
}


class TestFactoryRoundTrip:
    """MediaRequest.from_dict() produces the correct kind and payload per source.

    Mutation target per case:
    - YT: factory defaults source to "youtube" when absent, produces AUDIO_TRACK
    - SC: source="soundcloud" → AUDIO_TRACK, no payload
    - Radio: source="radio" → RADIO_STATION, countrycode in payload
    - Movie: source="tv_movies" + media_type="movie" → MOVIE, full MovieInfo
    - Episode: source="tv_movies" + media_type="tv" + season_number → EPISODE
    - Queue: yt_id="tmdb_N" without tmdb_id key → tmdb_id recovery from prefix
    - Bookmark: tagline=None/runtime=None/genres=JSON-string → sanitized
    """

    def test_youtube(self) -> None:
        request = MediaRequest.from_dict(YT_DATA)
        assert request.kind is MediaKind.AUDIO_TRACK
        assert request.payload is None
        assert request.yt_id == "dQw4w9WgXcQ"
        assert request.title == "Rick Astley - Never Gonna Give You Up"
        assert request.channel == "Rick Astley"
        assert request.source == "youtube"
        assert request.audio_only is False
        assert request.start_pos is None

    def test_soundcloud(self) -> None:
        request = MediaRequest.from_dict(SC_DATA)
        assert request.kind is MediaKind.AUDIO_TRACK
        assert request.payload is None
        assert request.yt_id == "123456789"
        assert request.channel == "Some Artist"
        assert request.source == "soundcloud"

    def test_radio(self) -> None:
        request = MediaRequest.from_dict(RADIO_DATA)
        assert request.kind is MediaKind.RADIO_STATION
        assert isinstance(request.payload, RadioInfo)
        assert request.payload.countrycode == "GB"
        assert request.channel == "BBC Radio 4"
        assert request.source == "radio"

    def test_movie(self) -> None:
        request = MediaRequest.from_dict(TV_MOVIE_DATA)
        assert request.kind is MediaKind.MOVIE
        assert isinstance(request.payload, MovieInfo)
        p = request.payload
        assert p.tmdb_id == 550
        assert p.title == "Fight Club"
        assert p.tagline == "Mischief. Mayhem. Soap."
        assert p.rating == 8.4
        assert p.vote_count == 25000
        assert p.year == "1999"
        assert p.runtime == 139
        assert p.genres == ["Drama"]
        assert p.overview == "A ticking time bomb of a movie."
        assert request.yt_id == "tmdb_550"
        assert request.source == "tv_movies"

    def test_episode(self) -> None:
        request = MediaRequest.from_dict(TV_EPISODE_DATA)
        assert request.kind is MediaKind.EPISODE
        assert isinstance(request.payload, EpisodeInfo)
        p = request.payload
        assert p.tmdb_id == 1668
        assert p.season_number == 1
        assert p.episode_number == 1
        assert p.series_title == "Friends"
        assert p.episode_title == "The One Where Monica Gets a Roommate"
        assert p.rating == 8.2
        assert p.year == "1994"
        assert request.yt_id == "tmdb_1668"
        assert request.source == "tv_movies"

    def test_queue_reconstructed(self) -> None:
        """yt_id='tmdb_550' without tmdb_id key → recovers tmdb_id via prefix."""
        request = MediaRequest.from_dict(QUEUE_RECONSTRUCTED_DATA)
        assert request.kind is MediaKind.MOVIE
        assert isinstance(request.payload, MovieInfo)
        assert request.payload.tmdb_id == 550
        assert request.title == "Fight Club"
        assert request.yt_id == "tmdb_550"

    def test_bookmark_shape(self) -> None:
        """tagline=None/runtime=None/genres as JSON string → sanitized."""
        request = MediaRequest.from_dict(BOOKMARK_DATA)
        assert request.kind is MediaKind.MOVIE
        assert isinstance(request.payload, MovieInfo)
        p = request.payload
        assert p.tagline == ""
        assert p.runtime == 0
        assert p.vote_count == 0
        assert p.genres == ["Drama", "Thriller"]
        assert p.tmdb_id == 550

    def test_explicit_source_override(self) -> None:
        """Explicit source= kwarg overrides data.get('source')."""
        request = MediaRequest.from_dict(YT_DATA, source="radio")
        assert request.source == "radio"
        assert request.kind is MediaKind.RADIO_STATION
        assert isinstance(request.payload, RadioInfo)

    def test_explicit_kind_bypasses_heuristic(self) -> None:
        """Explicit kind= runs before the source-based heuristic, not after.

        Radio data with kind=AUDIO_TRACK is the cleanest signal for this
        ordering test: the heuristic would produce RADIO_STATION, so if the
        factory returns AUDIO_TRACK we know the explicit kind was honored.
        """
        request = MediaRequest.from_dict(RADIO_DATA, kind=MediaKind.AUDIO_TRACK)
        assert request.kind is MediaKind.AUDIO_TRACK
        assert request.payload is None


class TestMissingTmdbId:
    """MOVIE/EPISODE without tmdb_id must raise ValueError.

    Mutation target: a silent fallback to MOVIE with tmdb_id=0 or None would
    propagate None through the entire pipeline. The factory must reject early.
    """

    def test_no_tmdb_id_on_tv_source(self) -> None:
        """source='tv_movies' with empty data → ValueError, not silent MOVIE."""
        with pytest.raises(ValueError, match="tmdb_id"):
            MediaRequest.from_dict({"source": "tv_movies"})

    def test_no_tmdb_id_explicit_movie(self) -> None:
        with pytest.raises(ValueError, match="tmdb_id"):
            MediaRequest.from_dict({}, kind=MediaKind.MOVIE)

    def test_no_tmdb_id_explicit_episode(self) -> None:
        with pytest.raises(ValueError, match="tmdb_id"):
            MediaRequest.from_dict({}, kind=MediaKind.EPISODE)

    def test_queue_missing_both_ids(self) -> None:
        """Queue dict with source='tv_movies' but no tmdb_id or yt_id → ValueError.

        A queue-reconstructed dict for tv_movies should always carry yt_id="tmdb_N".
        If both are missing, the data is corrupt: reject rather than guess.
        """
        with pytest.raises(ValueError, match="tmdb_id"):
            MediaRequest.from_dict({"source": "tv_movies", "title": "orphaned"})

    def test_audio_track_no_tmdb_id_silent(self) -> None:
        """AUDIO_TRACK does not require tmdb_id: no error, empty payload."""
        request = MediaRequest.from_dict({}, kind=MediaKind.AUDIO_TRACK)
        assert request.kind is MediaKind.AUDIO_TRACK
        assert request.payload is None

    def test_radio_no_tmdb_id_silent(self) -> None:
        """RADIO_STATION does not require tmdb_id: no error."""
        request = MediaRequest.from_dict({}, kind=MediaKind.RADIO_STATION)
        assert request.kind is MediaKind.RADIO_STATION
        assert isinstance(request.payload, RadioInfo)
