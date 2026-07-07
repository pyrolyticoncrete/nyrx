# SPDX-License-Identifier: AGPL-3.0-only

"""Command palette provider and custom screen for the nyrx TUI."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from rich.text import Text
from textual.app import ComposeResult
from textual.command import (
    CommandInput,
    CommandList,
    CommandPalette,
    DiscoveryHit,
    Hit,
    Hits,
    Provider,
    SearchIcon,
)
from textual.containers import Horizontal, Vertical
from textual.widgets import LoadingIndicator, Static

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

from nyrx.config import get_config
from nyrx.sources.radio_source import RadioSource
from nyrx.sources.soundcloud import SoundCloudSource
from nyrx.sources.tv_movies import TVMoviesSource

_DISPLAY_W = 44


class MediaProvider(Provider):
    """Command palette provider exposing app actions and source commands.

    Subclasses Textual's ``CommandPalette.Provider``, overriding its
    ``search``/``discover`` hooks to build command entries from the active
    source's ``get_commands()``.  The near-identical bodies are deliberate:
    Textual calls each hook for a different palette phase (typed query vs.
    initial discovery), so both must emit the same command list.
    """

    @staticmethod
    def _row(name: str, key: str | None) -> Text:
        t = Text()
        if key:
            pad = _DISPLAY_W - len(name) - len(key) - 1
            t.append(name)
            if pad > 0:
                t.append(" " * pad)
            t.append(f" {key}", style="dim")
        else:
            t.append(name)
        return t

    async def search(self, query: str) -> Hits:
        matcher = self.matcher(query)
        app = cast("MediaAppProtocol", self.app)
        commands: list[tuple[str, str, str | None]] = [
            ("Open queue", "action_open_queue", "`"),
        ]
        source = app._sources.get(str(app._source))
        if source:
            if not isinstance(source, RadioSource):
                commands.append(
                    ("Set download directory", "action_change_download_dir", None)
                )
            quality = getattr(app, "_quality", "")
            trending_region = getattr(app, "_trending_region", "us")
            for name, action_name, key_hint in source.get_commands(
                quality=quality, trending_region=trending_region
            ):
                commands.append((name, action_name, key_hint))
            if isinstance(source, SoundCloudSource):
                commands.append(("Sync liked tracks", "action_sync_liked", None))
        if isinstance(source, TVMoviesSource):
            _hs_on = get_config().get("hotswap_enabled", True)
            commands.append(
                ("Configure Lua plugin source", "action_configure_manifest_url", None)
            )
            commands.append(
                (
                    "Toggle Lua plugin auto-updates",
                    "action_toggle_hotswap",
                    "on" if _hs_on else "off",
                )
            )
        for name, action, key in commands:
            if (match := matcher.match(name)) > 0:
                yield Hit(
                    match,
                    self._row(name, key),
                    getattr(app, action),
                    text=name,
                )

    async def discover(self) -> Hits:
        app = cast("MediaAppProtocol", self.app)
        commands: list[tuple[str, str, str | None]] = [
            ("Open queue", "action_open_queue", "`"),
        ]
        source = app._sources.get(str(app._source))
        if source:
            if not isinstance(source, RadioSource):
                commands.append(
                    ("Set download directory", "action_change_download_dir", None)
                )
            quality = getattr(app, "_quality", "")
            trending_region = getattr(app, "_trending_region", "us")
            for name, action_name, key_hint in source.get_commands(
                quality=quality, trending_region=trending_region
            ):
                commands.append((name, action_name, key_hint))
            if isinstance(source, SoundCloudSource):
                commands.append(("Sync liked tracks", "action_sync_liked", None))
        if isinstance(source, TVMoviesSource):
            _hs_on = get_config().get("hotswap_enabled", True)
            commands.append(
                ("Configure Lua plugin source", "action_configure_manifest_url", None)
            )
            commands.append(
                (
                    "Toggle Lua plugin auto-updates",
                    "action_toggle_hotswap",
                    "on" if _hs_on else "off",
                )
            )
        for name, action, key in commands:
            yield DiscoveryHit(
                self._row(name, key),
                getattr(app, action),
                text=name,
            )


class CommandScreen(CommandPalette):
    def compose(self) -> ComposeResult:
        with Vertical(id="--container"):
            yield Static("[white]Commands[/white]", id="--heading")
            with Horizontal(id="--input"):
                yield SearchIcon()
                yield CommandInput(select_on_focus=False)
            with Vertical(id="--results"):
                yield CommandList()
                yield LoadingIndicator()
