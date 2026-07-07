# SPDX-License-Identifier: AGPL-3.0-only

"""Main Textual application for the nyrx TUI.

Provides the ``MediaApp`` class that manages YouTube / SoundCloud / Radio
browsing, the playback queue, background downloads, connectivity monitoring,
and the sidebar now-playing widget.
"""

from __future__ import annotations

import json
import logging
import os
import threading
import time
from collections.abc import Callable
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import TYPE_CHECKING, ClassVar, cast

from textual import events, work
from textual.app import App, ComposeResult
from textual.command import Provider
from textual.containers import Container, Horizontal, Vertical
from textual.screen import Screen
from textual.theme import Theme
from textual.timer import Timer
from textual.widgets import Button, ContentSwitcher, DataTable, ListView, Static

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

from platformdirs import user_log_dir

from nyrx import config
from nyrx.actions.download import DownloadActions
from nyrx.actions.navigation import NavigationActions
from nyrx.actions.playback import PlaybackActions
from nyrx.actions.radio import RadioActions
from nyrx.actions.search import SearchActions
from nyrx.actions.soundcloud import SoundCloudActions
from nyrx.actions.tv_movies import TVMoviesActions
from nyrx.commands import MediaProvider
from nyrx.config import (
    SETTINGS_PATH,
    SEVERITY_WARNING,
    TEMP_THUMBS,
    TIMEOUT_INFO,
    TIMEOUT_WARNING,
    WIDE_BREAKPOINT_WIDTH,
)
from nyrx.handlers.connectivity import ConnectivityHandlers
from nyrx.handlers.focus import FocusHandlers
from nyrx.handlers.keys import KeyHandlers
from nyrx.handlers.sidebar import SidebarHandlers
from nyrx.handlers.sizefloor import SizefloorHandlers
from nyrx.modes import Source, View
from nyrx.player import MPVIPC
from nyrx.queues import PlaybackQueue
from nyrx.sources import Source as SourceABC
from nyrx.sources.radio_index import StationIndex
from nyrx.sources.radio_source import RadioSource
from nyrx.sources.soundcloud import SoundCloudSource, load_sc_followed, load_sc_likes
from nyrx.sources.tv_movies import TVMoviesSource
from nyrx.sources.tv_movies.db import load_bookmarks
from nyrx.sources.youtube import YouTubeSource
from nyrx.watch_db import init_watch_db
from nyrx.widgets import (
    ArtistProfileView,
    BaseNowPlaying,
    BrailleSpinner,
    DownloadWidget,
    HistoryItem,
    LikedScreen,
    ModeSwitcher,
    RadioNowPlaying,
    SCHomeView,
    SidebarNowPlaying,
    SoundCloudNowPlaying,
    TVHomeView,
    TVNowPlaying,
    TVSeriesView,
    WatchlistScreen,
)

logger = logging.getLogger(__name__)

_perf_logger = logging.getLogger("nyrx.perf")


class MediaApp(  # type: ignore[misc]
    PlaybackActions,
    SearchActions,
    SoundCloudActions,
    TVMoviesActions,
    RadioActions,
    DownloadActions,
    NavigationActions,
    FocusHandlers,
    SizefloorHandlers,
    KeyHandlers,
    SidebarHandlers,
    ConnectivityHandlers,
    App,
):
    """Main Textual application for the nyrx TUI.

    Manages YouTube search results, playback queue, background downloads,
    connectivity monitoring, and the sidebar now-playing widget.  Integrates
    with mpv via IPC for remote control (seek, pause, position polling).
    """

    HORIZONTAL_BREAKPOINTS = [(WIDE_BREAKPOINT_WIDTH, "wide")]

    CSS_PATH = ["css/app.tcss"]

    BINDINGS = [
        ("q", "quit", "Quit"),
        ("`", "open_queue", "Queue"),
        ("ctrl+p", "open_commands", "Commands"),
        ("/", "open_search", "Search"),
        ("enter", "play", "Play"),
        ("left", "prev_page", "prev"),
        ("right", "next_page", "next"),
        ("m", "toggle_audio", "Mode"),
        ("d", "download", "Download"),
        ("b", "open_browser", "Browser"),
        ("z", "view_thumbnail", "View"),
        ("f1", "switch_source_1", "F1 YouTube"),
        ("f2", "switch_source_2", "F2 Audio"),
        ("f3", "switch_source_3", "F3 Radio"),
        ("f4", "switch_source_4", "F4 TV/Movies"),
        ("f", "follow", "Follow"),
        ("l", "like_toggle", "Like"),
        ("ctrl+l", "show_liked", "Liked"),
        ("ctrl+w", "show_watchlist", "Watchlist"),
        ("ctrl+d", "delete_bookmark", "Delete"),
        ("ctrl+f", "show_following", "Following"),
        ("escape", "go_home", "Home"),
        ("r", "regen_feed", "Regen Feed"),
        ("s", "station", "Station"),
        ("p", "browse_collections", "Collections"),
        ("ctrl+a", "queue_all_feed", "Queue All"),
    ]

    COMMANDS: ClassVar[set[type[Provider] | Callable[[], type[Provider]]]] = {
        MediaProvider
    }

    TITLE = "NYRX"

    def __init__(self, debug: bool = False, profile: bool = False) -> None:
        """Initialise app state: empty results, no playback, default settings."""
        self._debug = debug
        self._profile = profile
        self._all_results: list[dict] = []
        self._page = 0
        self._page_size = 20
        self._exhausted = False
        self._query = ""
        self._audio_only = False
        self._mpv_ipc: MPVIPC | None = None
        self._np_side: BaseNowPlaying | None = None
        self._poll_timer: Timer | None = None
        self._notify_timer: Timer | None = None
        self._download_state: dict | None = None
        self._last_quit_press: float | None = None
        self._stopping = False
        self._quality = "1080p"
        self._trending_region: str = "us"
        self._download_dir: str | None = None
        self._search_histories: dict[str, list[str]] = {
            "youtube": [],
            "soundcloud": [],
            "tv_movies": [],
        }
        self._playback_queue = PlaybackQueue()
        self._download_pending: list[dict] = []
        self._now_playing_data: dict | None = None
        self._tv_bookmarks: list[dict] = []
        self._online: bool = True
        self._consecutive_failures: int = 0
        self._offline_since: float | None = None
        self._queue_frozen: bool = False
        self._syncing: bool = False
        self._download_paused_for_offline: bool = False
        self._download_running_flag: threading.Event = threading.Event()
        self._download_running_flag.set()
        self._download_cancel_flag: threading.Event = threading.Event()
        self._download_cancel_flag.set()
        self._current_dl_params: dict | None = None
        self._clear_dl_timer: Timer | None = None
        self._play_spinner_timer: Timer | None = None
        self._dl_spinner_timer: Timer | None = None
        self._dl_cancel_watchdog: Timer | None = None
        self._last_failed_query: str | None = None
        self._connectivity_timer: Timer | None = None
        self._cancel_active = False
        self._cancel_target_idx = 0
        self._last_known_pos: float = 0
        self._last_playing_data: dict | None = None
        self._show_back_online = False
        self._back_online_timer: Timer | None = None
        self._last_playback_pos: float = 0
        self._stalled_count = 0
        self._stream_ready = False
        self._play_start_time = 0.0
        self._play_token: int = 0
        self._resolve_token: int = 0
        self._search_token: int = 0
        self._sc_resolving = False
        self._tv_probing: bool = False
        self._subs_tmpdir: str | None = None
        self._failed_downloads: list[dict] = []
        self._spinning_history_item: HistoryItem | None = None
        self._spinning_chip: Button | None = None
        self._chip_spinner_timer: Timer | None = None
        self._chip_spinner_idx: int = 0
        self._loading_artists: dict[str, str] = {}
        self._fs_spinner_frame: int = 0
        self._fs_spinner_timer: Timer | None = None
        self._load_settings()
        self._source_states: dict = {}
        self._pending_dl_data: dict | None = None
        self._sources: dict[str, SourceABC] = {
            "youtube": YouTubeSource(),
            "soundcloud": SoundCloudSource(),
            "radio": RadioSource(),
            "tv_movies": TVMoviesSource(),
        }
        self._source: Source | None = None
        self._view: View = View.LANDING
        self._station_index: StationIndex | None = None
        self._sc_liked: list[dict] = []
        self._sc_followed: list[dict] = []
        self._tv_trending: list[dict] = []
        self._tv_popular: list[dict] = []
        self._tv_recs: list[dict] = []
        self._unlike_buffer: dict[str, dict] = {}
        self._station_in_progress = False
        self._trending_in_progress = False
        self._regen_in_progress = False
        self._feed_populated = False
        self._in_following = False
        self._in_artist_profile = False
        self._min_size_locked: bool = False
        self._min_size_watchdog = None
        self._feed: list[dict] = []
        self._radio_filter_name = ""
        self._radio_filter_tags: list[str] = []
        self._radio_filter_countries: list[str] = []
        self._radio_row_positions: dict[str, int] = {}
        self._radio_row_stations: dict[str, dict] = {}
        self._radio_page: int = 0
        self._radio_display_count: int = 0
        self._radio_total_filtered: int = 0
        self._radio_gen: int = 0
        self._switch_gen: int = 0
        self._w_results_focus: Static | None = None
        self._w_sidebar_focus: Static | None = None
        self._w_sidebar_context: Static | None = None
        self._w_sidebar_queue: Static | None = None

        self._np_widgets: dict[str, BaseNowPlaying] = {}
        self._w_download: DownloadWidget | None = None
        self._w_radio_list: DataTable | None = None
        self._w_radio_filter_hint: Static | None = None
        self._w_results_list: ListView | None = None
        self._w_empty_state: Container | None = None
        self._w_empty_offline_banner: Static | None = None
        self._w_empty_hint: Static | None = None
        self._w_sc_offline_banner: Static | None = None
        self._w_tv_offline_banner: Static | None = None
        self._w_main_content: Container | None = None
        self._w_history_list: ListView | None = None
        self._w_radio_area: Vertical | None = None
        self._w_empty_heading: Static | None = None
        self._w_sc_home: SCHomeView | None = None
        self._w_following_area: Horizontal | None = None
        self._w_fs_left_list: DataTable | None = None
        self._w_fs_center_list: ListView | None = None
        self._w_fs_center_header: Static | None = None
        self._w_welcome_topright: Static | None = None
        self._w_artist_profile: ArtistProfileView | None = None
        self._w_liked_screen: LikedScreen | None = None
        self._w_watchlist_screen: WatchlistScreen | None = None
        self._w_tv_home: TVHomeView | None = None
        self._w_tv_series: TVSeriesView | None = None
        self._in_watchlist = False
        self._in_tv_series = False
        self._tv_nav_stack: list[Callable[[], None]] = []
        self._pending_delete_tmdb: int | None = None
        self._pending_unfollow_artist: str | None = None
        self._in_liked = False
        self._sc_home_focus = None
        self._tv_home_focus = None
        super().__init__()

    def _load_settings(self) -> None:
        """Load persisted settings from config.json."""
        if not SETTINGS_PATH.exists():
            logger.debug("_load_settings: no config.json, first run, using defaults")
        else:
            try:
                data = json.loads(SETTINGS_PATH.read_text())
            except Exception as exc:
                logger.warning("_load_settings: failed to parse config.json: %s", exc)
                data = {}
            self._download_dir = data.get("download_dir")
            self._quality = data.get("quality", "1080p")
            self._trending_region = data.get("trending_region", "us")
            loaded = data.get("search_histories", {})
            if isinstance(loaded, dict):
                self._search_histories = loaded
            old = data.get("search_history", [])
            if old and "youtube" not in self._search_histories:
                self._search_histories["youtube"] = old
        self._search_histories.setdefault("youtube", [])
        self._search_histories.setdefault("soundcloud", [])
        self._search_histories.setdefault("tv_movies", [])
        try:
            self._tv_bookmarks = load_bookmarks()
        except Exception:
            self._tv_bookmarks = []

    def _init_logging(self: MediaAppProtocol) -> None:
        if self._debug:
            level = logging.DEBUG
            level_name = "DEBUG"
        else:
            level_name = os.environ.get("NYRX_LOG_LEVEL", "WARNING").upper()
            level = getattr(logging, level_name, logging.WARNING)
        log_path = Path(user_log_dir("nyrx")) / "app.log"
        log_path.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            str(log_path),
            maxBytes=5_242_880,
            backupCount=1,
        )
        handler.setLevel(level)
        formatter = logging.Formatter(
            "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
            datefmt="%H:%M:%S",
        )
        handler.setFormatter(formatter)
        logging.basicConfig(level=level, handlers=[handler], force=True)
        logging.info("Log level: %s", level_name)

    async def on_event(self, event: events.Event) -> None:
        """Profile-mode latency hook: time user-input/window event processing.

        Pure pass-through when ``--profile`` is off. Timer-driven re-renders
        never post a ``Message`` and bypass this hook by design. Their cost
        is sized externally by py-spy instead.
        """
        if not self._profile:
            await super().on_event(event)
            return
        t0 = time.perf_counter()
        await super().on_event(event)
        self.call_after_refresh(self._log_latency, type(event).__name__, t0)

    def _log_latency(self, label: str, t0: float) -> None:
        dt_ms = (time.perf_counter() - t0) * 1000
        if dt_ms > 50:  # filters noise only; every event is still timed
            _perf_logger.info(
                "[latency] %s: %.1fms @ epoch=%d", label, dt_ms, time.time()
            )

    def _configure_perf_logging(self: MediaAppProtocol) -> None:
        """Attach the ``nyrx.perf`` INFO handler when ``--profile`` is set.

        Runs after ``_init_logging``: ``basicConfig(force=True)`` resets root
        handlers, so the perf handler must be attached afterward or it gets
        wiped. A separate file (``app.perf.log``) avoids two ``RotatingFileHandler``
        instances fighting over the same path during rollover.
        """
        if not self._profile:
            return
        handler = RotatingFileHandler(
            str(Path(user_log_dir("nyrx")) / "app.perf.log"),
            maxBytes=10_485_760,  # 10MB: long sessions must not rotate the start out
            backupCount=1,
        )
        handler.setLevel(logging.INFO)
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)-8s %(name)s: %(message)s",
                datefmt="%H:%M:%S",
            )
        )
        _perf_logger.setLevel(logging.INFO)
        _perf_logger.addHandler(handler)
        _perf_logger.propagate = False
        _perf_logger.info("perf logging enabled (nyrx.perf -> app.perf.log)")

    async def _replace_screen(self, screen: Screen) -> Screen:
        result = await super()._replace_screen(screen)
        if self._screen_stack:
            self.screen._repaint_required = True
        return result

    def compose(self: MediaAppProtocol) -> ComposeResult:
        """Build the app layout: results area + sidebar."""
        with Container(id="main-content"):
            with Container(id="results-wrapper"):
                yield Static(id="results-focus")
                yield Static(id="welcome-topright")
                yield Static(id="welcome-bottomright")
                with Container(id="empty-state"):
                    with Vertical(id="empty-center"):
                        yield Static("", id="empty-offline-banner")
                        yield Static("/ to search", id="empty-hint")
                        yield Static("", id="empty-sep")
                        yield Static("RECENT SEARCHES", id="empty-heading")
                        yield ListView(id="history-list")
                yield SCHomeView(id="sc-home")
                yield TVHomeView(id="tv-home")
                with ContentSwitcher(initial="rs-empty", id="rs-switcher"):
                    yield ListView(id="results-list")
                    with Vertical(id="rs-loading"):
                        yield BrailleSpinner()
                        yield Static("loading results")
                    with Vertical(id="rs-empty"):
                        yield Static("[dim]No results to display[/dim]")
                yield Static(id="radio-filter-hint")
                with Vertical(id="radio-area"):
                    with ContentSwitcher(initial="rx-loading", id="radio-switcher"):
                        with Vertical(id="rx-loading"):
                            yield BrailleSpinner()
                            yield Static("loading stations")
                        with Vertical(id="rx-empty"):
                            yield Static("[dim]No stations match your filter[/dim]")
                        with Vertical(id="rx-content"):
                            yield DataTable(
                                id="radio-list",
                                show_header=True,
                                header_height=1,
                                cursor_type="row",
                            )
            with Container(id="sidebar"):
                yield Static(id="sidebar-focus")
                yield Static(id="sidebar-context")
                yield SidebarNowPlaying(id="np-youtube")
                yield SoundCloudNowPlaying(id="np-soundcloud")
                yield RadioNowPlaying(id="np-radio")
                yield TVNowPlaying(id="np-tv")
                yield DownloadWidget(id="download-widget")
                yield Static(id="sidebar-queue")
        yield ModeSwitcher(id="mode-switcher")

    def on_mount(self: MediaAppProtocol) -> None:
        """Register the custom theme, load history, start connectivity polling."""
        self.register_theme(
            Theme(
                name="aura",
                primary="#a277ff",
                secondary="#f694ff",
                accent="#a277ff",
                error="#ff6767",
                warning="#ffca85",
                success="#61ffca",
                background="#0f0f0f",
                surface="#15141b",
                panel="#15141b",
                foreground="#edecee",
                dark=True,
            )
        )
        self.theme = "aura"
        self._init_logging()
        self._configure_perf_logging()
        init_watch_db()
        self._warm_sc_client()
        self._w_main_content = self.query_one("#main-content", Container)
        self._w_results_focus = self.query_one("#results-focus", Static)
        self._w_sidebar_focus = self.query_one("#sidebar-focus", Static)
        self._w_sidebar_context = self.query_one("#sidebar-context", Static)
        self._w_sidebar_queue = self.query_one("#sidebar-queue", Static)
        self._np_widgets = {
            "youtube": self.query_one("#np-youtube", SidebarNowPlaying),
            "soundcloud": self.query_one("#np-soundcloud", SoundCloudNowPlaying),
            "radio": self.query_one("#np-radio", RadioNowPlaying),
            "tv_movies": self.query_one("#np-tv", TVNowPlaying),
        }
        self._w_download = self.query_one("#download-widget", DownloadWidget)
        self._w_radio_list = self.query_one("#radio-list", DataTable)
        self._w_radio_filter_hint = self.query_one("#radio-filter-hint", Static)
        self._w_results_list = self.query_one("#results-list", ListView)
        self._w_empty_state = self.query_one("#empty-state", Container)
        self._w_empty_offline_banner = self.query_one("#empty-offline-banner", Static)
        self._w_empty_hint = self.query_one("#empty-hint", Static)
        self._w_sc_offline_banner = self.query_one("#sc-offline-banner", Static)
        self._w_tv_offline_banner = self.query_one("#tv-offline-banner", Static)
        self._w_history_list = self.query_one("#history-list", ListView)
        self._w_radio_area = self.query_one("#radio-area", Vertical)
        self._w_empty_heading = self.query_one("#empty-heading", Static)
        self._w_sc_home = self.query_one("#sc-home", SCHomeView)
        self._w_following_area = self.query_one("#following-area", Horizontal)
        self._w_fs_left_list = self.query_one("#fs-left-list", DataTable)
        self._w_fs_center_list = self.query_one("#feed-list", ListView)
        self._w_fs_center_header = self.query_one("#fs-center-header", Static)
        self._w_welcome_topright = self.query_one("#welcome-topright", Static)
        self._w_artist_profile = self.query_one("#artist-profile", ArtistProfileView)
        self._w_liked_screen = self.query_one("#liked-area", LikedScreen)
        self._w_watchlist_screen = self.query_one("#watchlist-screen", WatchlistScreen)
        self._w_tv_home = self.query_one("#tv-home", TVHomeView)
        self.watch(self.screen, "focused", self._on_screen_focus_changed)
        self.watch(self, "_screen", self._on_app_screen_changed)
        from nyrx.screens.home import HomeScreen

        self.push_screen(HomeScreen(), self._on_home_selected)
        if TEMP_THUMBS.exists():
            for f in TEMP_THUMBS.iterdir():
                try:
                    f.unlink()
                except Exception:
                    logger.debug("on_mount: failed to unlink thumbnail %s", f.name)
        self.set_timer(1.0, self._tick_connectivity)
        self._connectivity_timer = self.set_interval(5.0, self._tick_connectivity)
        self._sc_liked = load_sc_likes()
        self._sc_followed = load_sc_followed()
        self.set_interval(3600, self._check_stale_caches)
        self.call_after_refresh(self._check_stale_caches)
        self._deferred_load_index()
        self._preheat_tmdb_cache()
        _hotswap_enabled = config.get_config().get("hotswap_enabled", True)
        if config.HOTSWAP_MANIFEST_URL and _hotswap_enabled:
            self._check_hotswap()
        self.call_after_refresh(self._check_size_floor)
        self._min_size_watchdog = self.set_interval(0.5, self._check_size_floor)
        logger.debug("on_mount: complete")

    @work(thread=True, group="hotswap", exclusive=True)
    def _check_hotswap(self: MediaAppProtocol, manual: bool = False) -> None:
        """Fetch remote Lua config bundle and apply if disk differs.

        Set *manual* to ``True`` when invoked from the command palette
        so that the user gets appropriate feedback (up-to-date, updated,
        or failure).  Startup calls leave *manual* as ``False`` to keep
        boot quiet.
        """
        from nyrx.config import LUA_CACHE_DIR
        from nyrx.sources.hotswap import apply_bundle, fetch_manifest

        manifest = fetch_manifest(config.HOTSWAP_MANIFEST_URL)
        if manifest is None:
            if manual:
                self.call_from_thread(
                    self.notify,
                    "Could not check for server config updates, "
                    "check your internet connection",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
            else:
                self.call_from_thread(
                    self.notify,
                    "Could not check for server config updates (offline?). "
                    "Retrying in 30 min.",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
                self.call_from_thread(self.set_timer, 1800, self._check_hotswap)
            return

        result = apply_bundle(
            manifest,
            lua_cache_dir=LUA_CACHE_DIR,
            dispatcher=cast(TVMoviesSource, self._sources["tv_movies"])._dispatcher,
        )

        if manual:
            if result.written and not result.errors:
                self.call_from_thread(
                    self.notify,
                    f"{len(result.written)} server config(s) updated",
                    timeout=TIMEOUT_INFO,
                )
            elif result.errors and not result.written:
                for err in result.errors:
                    self.call_from_thread(
                        self.notify,
                        err,
                        severity=SEVERITY_WARNING,
                        timeout=TIMEOUT_WARNING,
                        title="Warning",
                    )
            elif result.errors:
                self.call_from_thread(
                    self.notify,
                    f"{len(result.written)} server config(s) updated, "
                    f"{len(result.errors)} failed",
                    severity=SEVERITY_WARNING,
                    timeout=TIMEOUT_WARNING,
                    title="Warning",
                )
            else:
                self.call_from_thread(
                    self.notify,
                    "Server configs are up to date",
                    timeout=TIMEOUT_INFO,
                )
        else:
            if not result.success:
                for err in result.errors:
                    self.call_from_thread(
                        self.notify,
                        err,
                        severity=SEVERITY_WARNING,
                        timeout=TIMEOUT_WARNING,
                        title="Warning",
                    )

    def action_like_toggle(self: MediaAppProtocol) -> None:
        """Toggle like/bookmark on the currently focused item (l key)."""
        if self._np_focused:
            super().action_like_toggle()  # type: ignore[misc]
            return
        if self._source == Source.TV_MOVIES:
            self._toggle_tv_bookmark()
            return
        if self._source == Source.RADIO:
            dt = self._w_radio_list
            if dt is not None and dt.cursor_coordinate is not None:
                cell_key = dt.coordinate_to_cell_key(dt.cursor_coordinate)
                uuid = (
                    cell_key.row_key.value
                    if cell_key and cell_key.row_key.value
                    else ""
                )
                station = self._radio_row_stations.get(uuid)
                if station:
                    self._toggle_radio_like(station)
            return
        super().action_like_toggle()  # type: ignore[misc]


def main(debug: bool = False, profile: bool = False) -> None:
    """Launch the Textual TUI application."""
    app = MediaApp(debug=debug, profile=profile)
    app.run(mouse=False)


if __name__ == "__main__":
    main()
