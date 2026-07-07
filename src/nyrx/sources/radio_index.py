# SPDX-License-Identifier: AGPL-3.0-only

"""Radio station database index.

Provides the ``StationIndex`` class which manages an in-memory database
of internet radio stations loaded from a bundled JSON dump (or a cached
copy on disk).  Supports full-text name search, tag/country set-intersection
filtering, suggestion dropdowns, and liked-station persistence.
"""

from __future__ import annotations

import gzip
import json
import logging
import time
from collections import Counter
from pathlib import Path

from nyrx.config import CACHE_DIR, CONFIG_DIR

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Paths
# ---------------------------------------------------------------------------
_CACHE_DIR = CACHE_DIR
_CONFIG_DIR = CONFIG_DIR
_CACHE_FILE = _CACHE_DIR / "radio_stations.json"
_LIKES_FILE = _CONFIG_DIR / "radio_likes.json"
_BUNDLED = Path(__file__).parent.parent / "data" / "radio_stations_default.json.gz"


class StationIndex:
    """Manages the local radio station database.

    On first access the bundled ``.gz`` is decompressed into the cache
    directory.  A background refresh (triggered externally) can re-fetch
    from the live API every *RADIO_CACHE_DAYS* days.
    """

    def __init__(self) -> None:
        self.stations: list[dict] = []
        self._tag_rank: dict[str, int] = {}
        self._liked: set[str] = set()
        self._loaded = False
        self._tag_index: dict[str, set[int]] = {}
        self._country_index: dict[str, set[int]] = {}
        self._countrycode_index: dict[str, set[int]] = {}
        self._country_to_code: dict[str, str] = {}
        self._name_lower: list[str] = []

    # ---- load / save -------------------------------------------------------

    def load(self) -> None:
        if self._loaded:
            return
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CONFIG_DIR.mkdir(parents=True, exist_ok=True)

        data = None
        if _CACHE_FILE.exists():
            with open(_CACHE_FILE, encoding="utf-8") as f:
                data = json.load(f)
        if _BUNDLED.exists() and (data is None or not data.get("stations")):
            with gzip.open(_BUNDLED, "rt", encoding="utf-8") as f:
                data = json.load(f)
            with open(_CACHE_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False)
        if data is None or not data.get("stations"):
            self.stations = []
            self._loaded = True
            logger.debug("StationIndex.load: no_stations")
            return

        self.stations = data["stations"]
        self._build_tag_rank()
        self._load_liked()
        self._build_indexes()
        self._loaded = True
        logger.debug(
            "StationIndex.load: count=%s liked=%s tags=%s",
            len(self.stations),
            len(self._liked),
            len(self._tag_rank),
        )

    def _build_tag_rank(self) -> None:
        rank: Counter = Counter()
        for s in self.stations:
            raw = s.get("tags", "")
            if not raw:
                continue
            for t in raw.split(","):
                t = t.strip()
                if t:
                    rank[t] += 1
        self._tag_rank = dict(rank)

    def _load_liked(self) -> None:
        if _LIKES_FILE.exists():
            try:
                data = json.loads(_LIKES_FILE.read_text())
                self._liked = set(data.get("liked", []))
            except Exception:
                self._liked = set()

    def _save_liked(self) -> None:
        _LIKES_FILE.parent.mkdir(parents=True, exist_ok=True)
        _LIKES_FILE.write_text(
            json.dumps({"liked": list(self._liked)}, ensure_ascii=False, indent=2)
        )

    @property
    def last_fetched(self) -> float:
        try:
            st = _CACHE_FILE.stat()
            return st.st_mtime
        except OSError:
            return 0

    # ---- index -------------------------------------------------------------

    def _build_indexes(self) -> None:
        """Build inverted indexes for fast tag/country/name filtering."""
        self._tag_index.clear()
        self._country_index.clear()
        self._countrycode_index.clear()
        self._country_to_code.clear()
        self._name_lower = []

        for i, s in enumerate(self.stations):
            self._name_lower.append(s.get("name", "").lower())
            raw = s.get("tags", "")
            if raw:
                for tag in raw.split(","):
                    t = tag.strip().lower()
                    if t:
                        self._tag_index.setdefault(t, set()).add(i)
            cc = s.get("countrycode", "").strip().lower()
            if cc:
                self._countrycode_index.setdefault(cc, set()).add(i)
            c = s.get("country", "").strip().lower()
            if c:
                self._country_index.setdefault(c, set()).add(i)
            if c and cc:
                self._country_to_code.setdefault(c, cc.upper())

        # ---- fuzzy scorer -------------------------------------------------------

    @staticmethod
    def _fuzzy_score(query: str, target: str) -> float:
        q = query.lower()
        t = target.lower()
        if not q:
            return 0.0
        if q == t:
            return 100.0
        if t.startswith(q):
            return 80.0 + min(len(q) / len(t) * 10.0, 10.0)
        if q in t:
            idx = t.find(q)
            is_boundary = idx == 0 or t[idx - 1] in (" ", "/", "-", "_", "&")
            return 60.0 if is_boundary else 50.0
        it = iter(t)
        if all(c in it for c in q):
            return 20.0
        return 0.0

    # ---- filtering / suggestions -------------------------------------------

    def get_filtered(
        self,
        name: str = "",
        tags: list[str] | None = None,
        countries: list[str] | None = None,
    ) -> list[dict]:
        if not self.stations:
            return []
        candidates = set(range(len(self.stations)))
        before = len(candidates)
        if tags:
            for t in tags:
                tl = t.strip().lower()
                if tl:
                    candidates &= self._tag_index.get(tl, set())
        if countries:
            cc_set: set[int] = set()
            for c in countries:
                cl = c.lower()
                cc_set |= self._countrycode_index.get(cl, set())
                cc_set |= self._country_index.get(cl, set())
            candidates &= cc_set
        if name:
            nl = name.lower()
            candidates = {i for i in candidates if nl in self._name_lower[i]}
        result = [self.stations[i] for i in sorted(candidates)]
        logger.debug(
            "StationIndex.get_filtered: name=%s tags=%s countries=%s before=%s after=%s",
            name[:20] if name else "",
            tags,
            countries,
            before,
            len(result),
        )
        return result

    def get_tag_suggestions(
        self,
        query: str,
        country_filter: list[str] | None = None,
        tag_filter: list[str] | None = None,
        max_results: int = 3,
    ) -> list[tuple[str, int]]:
        if not query.strip():
            return []

        candidates = set(range(len(self.stations)))
        if tag_filter:
            for t in tag_filter:
                tl = t.strip().lower()
                if tl:
                    candidates &= self._tag_index.get(tl, set())
        if country_filter:
            cc_set: set[int] = set()
            for c in country_filter:
                cl = c.lower()
                cc_set |= self._countrycode_index.get(cl, set())
                cc_set |= self._country_index.get(cl, set())
            candidates &= cc_set

        scored: list[tuple[str, int, float]] = []
        for tag, station_set in self._tag_index.items():
            score = self._fuzzy_score(query, tag)
            if score <= 0:
                continue
            inter = station_set & candidates
            if inter:
                scored.append((tag, len(inter), score))

        if tag_filter:
            exclude = {tf.lower() for tf in tag_filter}
            scored = [(t, c, s) for t, c, s in scored if t.lower() not in exclude]

        scored.sort(key=lambda x: (-x[2], -x[1]))
        result = [(t, c) for t, c, _ in scored][:max_results]
        logger.debug(
            "StationIndex.get_tag_suggestions: query=%s result=%s", query, len(result)
        )
        return result

    def get_country_suggestions(
        self,
        query: str,
        tag_filter: list[str] | None = None,
        country_filter: list[str] | None = None,
        max_results: int = 3,
    ) -> list[tuple[str, int]]:
        if not query.strip():
            return []

        candidates = set(range(len(self.stations)))
        if tag_filter:
            for t in tag_filter:
                tl = t.strip().lower()
                if tl:
                    candidates &= self._tag_index.get(tl, set())

        scored: dict[str, tuple[int, float]] = {}

        for code, station_set in self._countrycode_index.items():
            score = self._fuzzy_score(query, code)
            if score > 0:
                inter = station_set & candidates
                if inter:
                    key = code.upper()
                    if key not in scored or score > scored[key][1]:
                        scored[key] = (len(inter), score)

        for country, station_set in self._country_index.items():
            score = self._fuzzy_score(query, country)
            if score > 0:
                code = self._country_to_code.get(country, country).upper()
                inter = station_set & candidates
                if inter:
                    if code not in scored or score > scored[code][1]:
                        scored[code] = (len(inter), score)

        exclude = {cf.lower() for cf in (country_filter or [])}
        scored = {code: v for code, v in scored.items() if code.lower() not in exclude}

        ranked = sorted(scored.items(), key=lambda x: (-x[1][1], -x[1][0]))
        result = [(code, count) for code, (count, _) in ranked][:max_results]
        logger.debug(
            "StationIndex.get_country_suggestions: query=%s result=%s",
            query,
            len(result),
        )
        return result

    # ---- popular tags for display ------------------------------------------

    def popular_tags(self, station: dict) -> list[str]:
        raw = station.get("tags", "").strip()
        if not raw:
            return []
        tags = [t.strip() for t in raw.split(",") if t.strip()]
        tags.sort(key=lambda t: self._tag_rank.get(t, 0), reverse=True)
        return tags[:3]

    # ---- likes --------------------------------------------------------------

    def is_liked(self, uuid: str) -> bool:
        return uuid in self._liked

    def toggle_like(self, uuid: str) -> bool:
        if uuid in self._liked:
            self._liked.discard(uuid)
            self._save_liked()
            logger.debug("StationIndex.toggle_like: unlike uuid=%s", uuid)
            return False
        self._liked.add(uuid)
        self._save_liked()
        logger.debug("StationIndex.toggle_like: like uuid=%s", uuid)
        return True

    # ---- refresh ------------------------------------------------------------

    def refresh_from_api(self) -> None:
        """Re-fetch ALL stations from the API (including broken) and merge."""
        import urllib.request as ureq

        logger.debug("StationIndex.refresh_from_api: start")
        api_base = "http://de1.api.radio-browser.info"
        page_size = 1000
        ua = "nyrx-refresh/1.0"
        essential = [
            "stationuuid",
            "name",
            "url_resolved",
            "tags",
            "country",
            "countrycode",
            "codec",
            "bitrate",
            "clickcount",
            "favicon",
            "hls",
            "homepage",
            "lastcheckok",
            "lastchecktime",
        ]

        fresh: list[dict] = []
        offset = 0
        while True:
            url = (
                f"{api_base}/json/stations/search"
                f"?hidebroken=false&limit={page_size}&offset={offset}"
            )
            req = ureq.Request(url, headers={"User-Agent": ua})
            try:
                with ureq.urlopen(req, timeout=30) as resp:
                    page = json.loads(resp.read().decode())
            except Exception:
                logger.warning("Radio API page fetch failed at offset %d", offset)
                break
            if not page:
                break
            for rec in page:
                fresh.append({k: rec.get(k, "") for k in essential})
            offset += page_size
            if len(page) < page_size:
                break
            time.sleep(0.2)

        logger.debug(
            "StationIndex.refresh_from_api: fetched=%s existing=%s",
            len(fresh),
            len(self.stations),
        )

        # merge by uuid
        by_uuid = {s["stationuuid"]: s for s in self.stations}
        for s in fresh:
            by_uuid[s["stationuuid"]] = s
        merged = list(by_uuid.values())

        payload = {
            "version": 1,
            "last_fetched": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            "station_count": len(merged),
            "stations": merged,
        }
        _CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _CACHE_FILE.write_text(json.dumps(payload, ensure_ascii=False, indent=2))

        self.stations = merged
        self._build_tag_rank()
        self._build_indexes()
        logger.debug("StationIndex.refresh_from_api: done merged=%s", len(merged))
