# SPDX-License-Identifier: AGPL-3.0-only

"""Radio mixin: station index loading, radio playback, like, list population."""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING, Any

from rich.text import Text
from textual import work
from textual.widgets import ContentSwitcher, DataTable

from nyrx.config import (
    RADIO_INDEX_PAGE,
    SEVERITY_ERROR,
    TIMEOUT_ERROR,
    TIMEOUT_INFO,
)
from nyrx.helpers import require_key
from nyrx.models import MediaRequest
from nyrx.modes import Source

logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol
    from nyrx.sources.radio_index import StationIndex


def _fit(s_: str, w: int) -> str:
    if len(s_) <= w:
        return s_.ljust(w)
    return s_[: w - 1] + "\u2026"


def _fmt_clicks(n: int) -> str:
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1_000:
        return f"{n / 1_000:.0f}k"
    if n == 0:
        return "\u2014"
    return str(n)


class RadioActions:
    _station_index: StationIndex | None

    def _switch_radio_to(self: MediaAppProtocol, state: str) -> None:
        try:
            sw = self.query_one("#radio-switcher", ContentSwitcher)
            sw.current = state
        except Exception:
            logger.debug("_switch_radio_to: ContentSwitcher not mounted yet")

    def _show_radio_loading(self: MediaAppProtocol) -> None:
        self._switch_radio_to("rx-loading")

    def _maybe_refresh_radio(self: MediaAppProtocol) -> None:
        if not self._station_index or not self._station_index.stations:
            return
        age = time.time() - self._station_index.last_fetched
        from nyrx.config import RADIO_CACHE_DAYS

        if age > RADIO_CACHE_DAYS * 86400:
            self._do_refresh_radio()

    @work(thread=True, group="radio-init")
    def _deferred_load_index(self: MediaAppProtocol) -> None:
        """Load radio station index in the background so startup is not blocked."""
        logger.debug("_deferred_load_index: loading station index")
        idx = self._sources["radio"].ensure_index_loaded()
        self.call_from_thread(self._on_index_loaded, idx)

    def _on_index_loaded(self: MediaAppProtocol, idx: StationIndex) -> None:
        """Called on the main thread after the radio index finishes loading."""
        self._station_index = idx
        logger.debug("_on_index_loaded: stations=%d", len(idx.stations))
        self._maybe_refresh_radio()
        if self._source == Source.RADIO:
            self._populate_radio_list()
            self._update_sidebar_context()

    @work(thread=True, group="radio-refresh")
    def _do_refresh_radio(self: MediaAppProtocol) -> None:
        try:
            idx = self._sources["radio"].ensure_index_loaded()
            idx.refresh_from_api()
            self.notify("Radio station index refreshed", timeout=TIMEOUT_INFO)
        except Exception:
            self.log.warning("Radio refresh failed")
            self.notify(
                "Radio refresh failed",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )

    def _play_radio(self: MediaAppProtocol, station: dict) -> None:
        """Play a radio station stream."""
        url = station.get("url_resolved") or station.get("url") or ""
        if not url:
            self.notify(
                "No stream URL found for this station",
                severity=SEVERITY_ERROR,
                timeout=TIMEOUT_ERROR,
                title="Error",
            )
            return
        yt_id = station.get("stationuuid", station.get("yt_id", url))
        title = station.get("name", "Radio Station")
        logger.debug("_play_radio: name=%s url=%s", title, url)
        self._play(
            MediaRequest.from_dict(
                {
                    "yt_id": yt_id,
                    "title": title,
                    "url": url,
                    "source": "radio",
                    "channel": station.get("name", ""),
                    "countrycode": station.get("countrycode", ""),
                }
            )
        )
        self.call_after_refresh(self._update_radio_playing_indicator)

    def _toggle_radio_like(self: MediaAppProtocol, station: dict) -> None:
        if not self._station_index:
            return
        uuid = station.get("stationuuid")
        if not uuid:
            return
        self._station_index.toggle_like(uuid)
        liked = uuid in self._station_index._liked
        logger.debug("_toggle_radio_like: uuid=%s liked=%s", uuid, liked)
        self._refresh_radio_item(uuid)

    def _populate_radio_list(self: MediaAppProtocol) -> None:
        """Dispatcher: bump gen, show loading, fire async worker."""
        if not self._station_index:
            return
        self._radio_gen += 1
        self._show_radio_loading()

        snapshot = (
            self._radio_filter_name,
            self._radio_filter_tags,
            self._radio_filter_countries,
            self._radio_page,
            self._radio_gen,
        )
        self._populate_radio_list_async(snapshot)

    @work(thread=True, exclusive=True, group="radio-pop")
    def _populate_radio_list_async(
        self: MediaAppProtocol, snapshot: tuple[Any, ...]
    ) -> None:
        """Worker: filter + sort + slice + format off the main thread."""
        filter_name, filter_tags, filter_countries, page, gen = snapshot
        try:
            idx = self._station_index
            if idx is None:
                return

            stations = idx.stations
            if filter_name or filter_tags or filter_countries:
                stations = idx.get_filtered(
                    name=filter_name,
                    tags=filter_tags,
                    countries=filter_countries,
                )

            liked_uuids = idx._liked
            liked_stations = [
                s for s in stations if s.get("stationuuid") in liked_uuids
            ]
            other_stations = [
                s for s in stations if s.get("stationuuid") not in liked_uuids
            ]

            liked_stations.sort(key=lambda s: s.get("clickcount", 0), reverse=True)
            other_stations.sort(key=lambda s: s.get("clickcount", 0), reverse=True)

            all_stations = liked_stations + other_stations
            total_filtered = len(all_stations)
            start = page * RADIO_INDEX_PAGE
            display = all_stations[start : start + RADIO_INDEX_PAGE]

            rows: list[dict] = []
            for pos, s in enumerate(display, page * RADIO_INDEX_PAGE + 1):
                uuid = s.get("stationuuid", "")
                liked = uuid in liked_uuids
                name_raw = s.get("name", "").split("|")[0].strip()
                tags_raw = s.get("tags", "").strip()
                popular = idx.popular_tags(s)
                tags = (
                    ", ".join(popular[:2])
                    if popular
                    else (tags_raw[:22] if tags_raw else "")
                )
                cc = s.get("countrycode", "").strip()
                bitrate = s.get("bitrate", 0)
                codec = s.get("codec", "")
                tech = f"{bitrate}k {codec}" if bitrate and codec else ""
                clickcount = s.get("clickcount", 0) or 0

                rows.append(
                    {
                        "uuid": uuid,
                        "pos": pos,
                        "liked": liked,
                        "name_raw": name_raw,
                        "tags": tags,
                        "cc": cc,
                        "tech": tech,
                        "clickcount": clickcount,
                        "station": s,
                    }
                )

            self.call_from_thread(
                self._on_radio_rows_ready,
                rows,
                {"total_filtered": total_filtered, "page": page, "gen": gen},
            )
        except Exception:
            logger.exception("_populate_radio_list_async failed")
            self.call_from_thread(self._switch_radio_to, "rx-empty")

    def _on_radio_rows_ready(
        self: MediaAppProtocol, rows: list[dict], meta: dict
    ) -> None:
        """Main-thread callback: render rows into the DataTable."""
        if meta["gen"] != self._radio_gen:
            return

        dt = self._w_radio_list
        if dt is None:
            return

        previous_uuid = None
        focused = self.focused
        if (
            isinstance(focused, DataTable)
            and focused.id == "radio-list"
            and focused.cursor_coordinate is not None
        ):
            cell_key = focused.coordinate_to_cell_key(focused.cursor_coordinate)
            previous_uuid = cell_key.row_key.value

        dt.clear()
        self._radio_row_positions.clear()
        self._radio_row_stations.clear()

        if not dt.columns:
            dt.add_column("  #", key="pos", width=4)
            dt.add_column("Name", key="name", width=40)
            dt.add_column("Tags", key="tags", width=16)
            dt.add_column("Country", key="country", width=7)
            dt.add_column("Bitrate", key="bitrate", width=10)
            dt.add_column("Clicks", key="clicks", width=5)

        self._radio_total_filtered = meta["total_filtered"]
        self._radio_display_count = len(rows)
        self._update_sidebar_context()

        if hint := self._w_radio_filter_hint:
            hint.display = True

        for row in rows:
            uuid = row["uuid"]
            pos = row["pos"]
            pos_cell = (
                Text("  \u2764\ufe0e", style="bold #A277FF")
                if row["liked"]
                else Text(f"{pos:>3}", style="dim")
            )
            name_cell = _fit(row["name_raw"], 40)
            tags_cell = _fit(row["tags"], 16)
            clicks_cell = Text(_fmt_clicks(row["clickcount"]).rjust(4), style="#404040")

            dt.add_row(
                pos_cell,
                Text(name_cell, style="#edecee"),
                Text(tags_cell, style="#606060"),
                Text(row["cc"], style="#606060"),
                Text(row["tech"], style="#606060"),
                clicks_cell,
                key=uuid,
            )
            self._radio_row_positions[uuid] = pos
            self._radio_row_stations[uuid] = row["station"]

        self._update_radio_playing_indicator()

        if previous_uuid and previous_uuid in self._radio_row_stations:
            for idx, row_key in enumerate(dt.rows):
                if row_key.value == previous_uuid:
                    dt.move_cursor(row=idx)
                    break
        elif dt.row_count:
            dt.move_cursor(row=0)

        if rows:
            dt.focus()
            self._switch_radio_to("rx-content")
        else:
            self._switch_radio_to("rx-empty")

    def _update_radio_playing_indicator(self: MediaAppProtocol) -> None:
        """Update the pos cell to show \u25b6 for the currently playing station."""
        dt = self._w_radio_list
        if dt is None:
            logger.debug("_update_radio_playing_indicator: _w_radio_list is None")
            return
        playing_uuid = (
            self._now_playing_data.get("yt_id")
            if self._now_playing_data and self._radio_is_active
            else None
        )
        for row_key in list(dt.rows.keys()):
            key = require_key(row_key.value)
            liked = key in self._station_index._liked if self._station_index else False
            pos = self._radio_row_positions.get(key, 0)
            if key == playing_uuid:
                dt.update_cell(key, "pos", Text(" \u25b6", style="bold #F3AE67"))
            elif liked:
                dt.update_cell(key, "pos", Text("  \u2764\ufe0e", style="bold #A277FF"))
            else:
                dt.update_cell(key, "pos", Text(f"{pos:>3}", style="dim"))

    @property
    def _radio_is_active(self: MediaAppProtocol) -> bool:
        return self._source == Source.RADIO

    def _refresh_radio_item(self: MediaAppProtocol, uuid: str) -> None:
        """Update a single station's like state in-place (no full rebuild)."""
        if uuid not in self._radio_row_stations:
            return
        liked_uuids = self._station_index._liked if self._station_index else frozenset()
        liked = uuid in liked_uuids
        pos = self._radio_row_positions.get(uuid, 0)
        dt = self._w_radio_list
        if dt is None:
            logger.debug("_refresh_radio_item: _w_radio_list is None")
            return
        is_playing = (
            self._now_playing_data
            and self._radio_is_active
            and self._now_playing_data.get("yt_id") == uuid
        )
        if is_playing:
            dt.update_cell(uuid, "pos", Text("\u25b6", style="bold #F3AE67"))
        elif liked:
            dt.update_cell(uuid, "pos", Text("  \u2764\ufe0e", style="bold #A277FF"))
        else:
            dt.update_cell(uuid, "pos", Text(f"{pos:>3}", style="dim"))
