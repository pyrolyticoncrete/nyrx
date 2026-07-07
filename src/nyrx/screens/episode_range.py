# SPDX-License-Identifier: AGPL-3.0-only

"""Modal for selecting a from-till episode range for batch download.

Uses custom EpisodePicker widgets (← → navigation) instead of text
Inputs: zero string parsing, zero invalid states.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal
from textual.message import Message
from textual.widgets import Button, Label, Static

from nyrx.helpers import iterate_episode_range
from nyrx.screens.base_modal import BaseModal

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

QUALITY_CHOICES = ["Best", "1080p", "720p", "480p"]


class EpisodePicker(Static):
    """Focusable widget for selecting an episode via ← → navigation.

    Displays ``S01E01`` and wraps across seasons.
    """

    can_focus = True

    class Changed(Message):
        """Posted when the user changes the selected episode."""

    def __init__(
        self,
        episodes: list[tuple[int, int]],
        initial: tuple[int, int],
        **kwargs: Any,
    ) -> None:
        super().__init__(**kwargs)
        self._episodes = episodes
        self._index = episodes.index(initial)
        self._update_display()

    def _update_display(self) -> None:
        s, e = self._episodes[self._index]
        self.update(f"S{s:02d}E{e:02d}")

    @property
    def value(self) -> tuple[int, int]:
        return self._episodes[self._index]

    def key_left(self) -> None:
        self._index = (self._index - 1) % len(self._episodes)
        self._update_display()
        self.post_message(self.Changed())

    def key_right(self) -> None:
        self._index = (self._index + 1) % len(self._episodes)
        self._update_display()
        self.post_message(self.Changed())


class EpisodeRangeModal(BaseModal[dict | None]):
    """Modal for selecting a from-till episode range to batch download."""

    BINDINGS = [
        Binding("tab", "focus_next", "Next", priority=True),
        Binding("shift+tab", "focus_previous", "Previous", priority=True),
        Binding("left", "navigate_left", "", priority=True),
        Binding("right", "navigate_right", "", priority=True),
        Binding("enter", "submit", "", priority=True),
    ]

    def __init__(self, payload: dict) -> None:
        super().__init__()
        self._zone = "pickers"
        self._tmdb_id = payload["tmdb_id"]
        self._series_title = payload["series_title"]
        self._seasons = payload["seasons"]
        self._season_map: dict[int, int] = {
            s["season_number"]: s["episode_count"] for s in self._seasons
        }

        self._episodes: list[tuple[int, int]] = []
        for s in sorted(self._season_map):
            for e in range(1, self._season_map[s] + 1):
                self._episodes.append((s, e))

        initial = (
            payload["current_season"],
            payload["current_episode"],
        )
        self._initial = initial if initial in self._episodes else self._episodes[0]

        self._quality_idx = 0

    def compose(self) -> ComposeResult:
        with Container(id="er-box"):
            yield Static("[white]BATCH DOWNLOAD[/white]", id="er-heading")

            with Horizontal(id="er-pickers"):
                yield Label("from: ", id="er-from-label")
                yield EpisodePicker(
                    self._episodes,
                    self._initial,
                    id="er-from-picker",
                )
                yield Label(" till: ", id="er-till-label")
                yield EpisodePicker(
                    self._episodes,
                    self._initial,
                    id="er-till-picker",
                )

            yield Label("QUALITY", id="er-quality-label")
            with Horizontal(id="er-quality-chips"):
                for label in QUALITY_CHOICES:
                    chip = Button(label, classes="quality-chip")
                    chip.can_focus = False
                    yield chip

            yield Label("", id="er-status")

            yield Label(
                "[white]\u2190 \u2192[/white] [dim]navigate[/dim]  \u00b7  "
                "[white]enter[/white] [dim]confirm[/dim]  \u00b7  "
                "[white]tab[/white] [dim]next[/dim]  \u00b7  "
                "[white]esc[/white] [dim]cancel[/dim]",
                id="er-hint",
            )

    def on_mount(self) -> None:
        super().on_mount()
        self.query_one("#er-till-picker", EpisodePicker).focus()
        self._update_quality_styles()
        self._update_zone_class()
        self._update_status()

    # ── Quality chips ────────────────────────────────────────────

    def _update_quality_styles(self) -> None:
        chips = list(self.query_one("#er-quality-chips", Horizontal).children)
        for i, chip in enumerate(chips):
            if i == self._quality_idx:
                chip.add_class("active")
            else:
                chip.remove_class("active")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if not event.button.has_class("quality-chip"):
            return
        chips = list(self.query_one("#er-quality-chips", Horizontal).children)
        for i, chip in enumerate(chips):
            if chip is event.button:
                self._quality_idx = i
                self._update_quality_styles()
                return

    def _set_zone(self, zone: str) -> None:
        self._zone = zone
        self._update_zone_class()
        self._update_status()

    def _update_zone_class(self) -> None:
        in_pickers = self._zone == "pickers"
        self.query_one("#er-box").set_class(in_pickers, "zone-pickers")
        self.query_one("#er-quality-chips").set_class(not in_pickers, "zone-active")

    # ── Status line ──────────────────────────────────────────────

    def _update_status(self) -> None:
        from_picker = self.query_one("#er-from-picker", EpisodePicker)
        till_picker = self.query_one("#er-till-picker", EpisodePicker)
        from_s, from_e = from_picker.value
        till_s, till_e = till_picker.value

        if (from_s, from_e) > (till_s, till_e):
            from_s, from_e, till_s, till_e = till_s, till_e, from_s, from_e

        n = len(
            iterate_episode_range(
                from_s,
                from_e,
                till_s,
                till_e,
                self._season_map,
            )
        )
        label = "episode" if n == 1 else "episodes"
        self.query_one("#er-status", Label).update(f"[dim]{n} {label}[/dim]")

    def on_episode_picker_changed(self, event: EpisodePicker.Changed) -> None:
        self._update_status()

    # ── Keyboard navigation ──────────────────────────────────────

    def action_focus_next(self) -> None:
        focused = self.focused
        if (
            focused is self.query_one("#er-till-picker", EpisodePicker)
            and self._zone != "quality"
        ):
            self._set_zone("quality")
        elif self._zone == "quality":
            self._set_zone("pickers")
            self.query_one("#er-from-picker", EpisodePicker).focus()
        elif focused is self.query_one("#er-from-picker", EpisodePicker):
            self.query_one("#er-till-picker", EpisodePicker).focus()
        else:
            self._set_zone("pickers")
            self.query_one("#er-from-picker", EpisodePicker).focus()

    def action_focus_previous(self) -> None:
        focused = self.focused
        if (
            focused is self.query_one("#er-from-picker", EpisodePicker)
            and self._zone != "quality"
        ):
            self._set_zone("quality")
        elif self._zone == "quality":
            self._set_zone("pickers")
            self.query_one("#er-till-picker", EpisodePicker).focus()
        elif focused is self.query_one("#er-till-picker", EpisodePicker):
            self.query_one("#er-from-picker", EpisodePicker).focus()
        else:
            self._set_zone("pickers")
            self.query_one("#er-till-picker", EpisodePicker).focus()

    def action_navigate_left(self) -> None:
        if self._zone == "quality":
            self._quality_prev()
        elif isinstance(self.focused, EpisodePicker):
            self.focused.key_left()
        self._update_status()

    def action_navigate_right(self) -> None:
        if self._zone == "quality":
            self._quality_next()
        elif isinstance(self.focused, EpisodePicker):
            self.focused.key_right()
        self._update_status()

    def action_submit(self) -> None:
        self._submit()

    def _quality_prev(self) -> None:
        n = len(QUALITY_CHOICES)
        self._quality_idx = (self._quality_idx - 1) % n
        self._update_quality_styles()

    def _quality_next(self) -> None:
        n = len(QUALITY_CHOICES)
        self._quality_idx = (self._quality_idx + 1) % n
        self._update_quality_styles()

    # ── Submit ───────────────────────────────────────────────────

    def _submit(self) -> None:
        from_picker = self.query_one("#er-from-picker", EpisodePicker)
        till_picker = self.query_one("#er-till-picker", EpisodePicker)

        from_s, from_e = from_picker.value
        till_s, till_e = till_picker.value

        if (from_s, from_e) > (till_s, till_e):
            from_s, from_e, till_s, till_e = till_s, till_e, from_s, from_e

        quality = QUALITY_CHOICES[self._quality_idx]

        result = {
            "start_season": from_s,
            "start_episode": from_e,
            "end_season": till_s,
            "end_episode": till_e,
            "quality": quality,
            "tmdb_id": self._tmdb_id,
            "series_title": self._series_title,
            "seasons": self._seasons,
        }
        logger.debug("EpisodeRangeModal._submit: result=%s", result)
        self.dismiss(result)
