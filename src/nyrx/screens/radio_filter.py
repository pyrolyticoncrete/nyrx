# SPDX-License-Identifier: AGPL-3.0-only

"""Two-panel filter modal for radio stations."""

from __future__ import annotations

import logging

from textual import events
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical
from textual.timer import Timer
from textual.widgets import Input, Label, ListItem, ListView, Static

from nyrx.screens.base_modal import BaseModal
from nyrx.sources.radio_index import StationIndex
from nyrx.widgets import ChipButton, ChipInput

logger = logging.getLogger(__name__)


class SuggestionItem(ListItem):
    """ListItem that carries a filter value for autocomplete suggestions."""

    _value: str = ""


class RadioFilterModal(BaseModal):
    """Two-panel filter modal for radio stations.

    Left:  name / tags / country inputs + inline suggestion dropdowns
    Right: active filters (chips) browsable with up/down, removable with x
    Tab cycle: fi-name -> fi-tags -> fi-country -> active-list -> fi-name ...
    """

    BINDINGS = [
        ("ctrl+p", "ignore", ""),
        ("/", "ignore", ""),
        ("left", "ignore", ""),
        ("right", "ignore", ""),
        ("m", "ignore", ""),
        ("d", "ignore", ""),
        ("b", "ignore", ""),
        ("z", "ignore", ""),
        ("l", "ignore", ""),
        ("ctrl+l", "ignore", ""),
        ("f", "ignore", ""),
        ("?", "ignore", ""),
    ]

    def action_ignore(self) -> None:
        pass

    _INPUT_FIELDS = ["fi-name", "fi-tags", "fi-country"]

    def __init__(
        self,
        station_index: StationIndex,
        initial_name: str = "",
        initial_tags: list[str] | None = None,
        initial_countries: list[str] | None = None,
        initial_country: str = "",
    ) -> None:
        self._index = station_index
        self._name = initial_name
        self._tags: list[str] = list(initial_tags or [])
        self._countries: list[str] = list(initial_countries or [])
        if initial_country and initial_country not in self._countries:
            self._countries.append(initial_country)
        self._zone = "input"
        self._suggest_timer: Timer | None = None
        self._pending_suggest: str | None = None
        self._pending_query = ""
        self._suggest_rebuilding = False
        super().__init__()

    def compose(self) -> ComposeResult:
        with Vertical(id="fi-wrapper"):
            with Horizontal(id="fi-box"):
                # left panel
                with Vertical(id="fi-left"):
                    with Horizontal(id="fi-heading"):
                        yield Static("[white]FILTER[/white]", id="fi-hd-left")
                        yield Static(id="fi-hd-right")

                    yield Static("station name", classes="fi-field-label")
                    yield Input(
                        value=self._name, placeholder="search name\u2026", id="fi-name"
                    )

                    yield Static("tags", classes="fi-field-label")
                    yield ChipInput(
                        chips=self._tags,
                        placeholder="add tags\u2026",
                        chip_color="#A277FF",
                        input_id="fi-tags",
                        id="fi-tags-wrap",
                        classes="chip-input-wrap",
                        render_chips=False,
                    )
                    yield ListView(id="fi-tag-list", classes="fi-suggest")

                    yield Static("country", classes="fi-field-label")
                    yield ChipInput(
                        chips=self._countries,
                        placeholder="add country\u2026",
                        chip_color="#E07B39",
                        input_id="fi-country",
                        id="fi-country-wrap",
                        classes="chip-input-wrap",
                        render_chips=False,
                    )
                    yield ListView(id="fi-country-list", classes="fi-suggest")

                # right panel
                with Vertical(id="fi-right"):
                    with Horizontal(id="fi-active-heading"):
                        yield Static("[white]ACTIVE[/white]", id="fi-active-label")
                        yield Static(id="fi-active-count")

                    yield Static("tags", classes="fi-section-label")
                    yield Horizontal(id="fi-tag-chips", classes="fi-chip-row")
                    yield Static("country", classes="fi-section-label")
                    yield Horizontal(id="fi-country-chips", classes="fi-chip-row")

            yield Static(id="fi-footer")

    def on_mount(self) -> None:
        super().on_mount()
        total = len(self._index.stations)
        self.query_one("#fi-hd-right", Static).update(f"[dim]{total:,} stations[/dim]")
        self._rebuild_active_panel()
        self._update_footer("input")
        self.query_one("#fi-name", Input).focus()

    def _update_footer(self, zone: str = "input") -> None:
        if zone == "active":
            text = (
                "[white]\u2191\u2193[/white] [dim]browse[/dim]  \u00b7  "
                "[white]\u2190\u2192[/white] [dim]chip[/dim]  \u00b7  "
                "[white]x[/white] [dim]remove[/dim]  \u00b7  "
                "[white]tab[/white] [dim]back[/dim]  \u00b7  "
                "[white]esc[/white] [dim]cancel[/dim]"
            )
        else:
            text = (
                "[white]tab[/white] [dim]next[/dim]  \u00b7  "
                "[white]\u2191\u2193[/white] [dim]browse[/dim]  \u00b7  "
                "[white]enter[/white] [dim]select / apply[/dim]  \u00b7  "
                "[white]backspace[/white] [dim]remove[/dim]"
            )
        self.query_one("#fi-footer", Static).update(text)

    def _submit(self) -> None:
        name = self.query_one("#fi-name", Input).value.strip()
        logger.debug(
            "_submit: name=%s tags=%s countries=%s", name, self._tags, self._countries
        )
        self.dismiss(
            {
                "name": name,
                "tags": list(self._tags),
                "countries": list(self._countries),
                "country": self._countries[0] if self._countries else "",
            }
        )

    # active filter panel (right panel)

    def _rebuild_active_panel(self) -> None:
        tag_row = self.query_one("#fi-tag-chips", Horizontal)
        tag_row.remove_children()
        tag_row.mount(*[ChipButton(t, "tag", t) for t in self._tags])

        country_row = self.query_one("#fi-country-chips", Horizontal)
        country_row.remove_children()
        country_row.mount(*[ChipButton(c, "country", c) for c in self._countries])

        self._update_active_count()

    def _update_active_count(self) -> None:
        try:
            filtered = self._index.get_filtered(
                name=self.query_one("#fi-name", Input).value.strip(),
                tags=self._tags,
                countries=self._countries if self._countries else None,
            )
            count = len(filtered)
            self.query_one("#fi-active-count", Static).update(
                f"[dim]{count:,} stations[/dim]"
            )
        except Exception:
            logger.debug("Failed to update active filter count")

    # tab cycle

    def _current_input_field(self) -> str | None:
        focused = self.focused
        if focused is None:
            return None
        fid = getattr(focused, "id", "") or ""
        return fid if fid in self._INPUT_FIELDS else None

    def _focus_input_field(self, field_id: str) -> None:
        self._hide_suggestions()
        if field_id == "fi-name":
            self.query_one("#fi-name", Input).focus()
        elif field_id == "fi-tags":
            self.query_one("#fi-tags-wrap", ChipInput).focus_input()
        elif field_id == "fi-country":
            self.query_one("#fi-country-wrap", ChipInput).focus_input()

    def _focus_active_panel(self) -> None:
        self._hide_suggestions()
        self._zone = "active"
        tag_row = self.query_one("#fi-tag-chips", Horizontal)
        country_row = self.query_one("#fi-country-chips", Horizontal)
        chips = list(tag_row.children) + list(country_row.children)
        if chips:
            chips[0].focus()
        else:
            self._zone = "input"
            self._focus_input_field("fi-name")
        self._update_footer(self._zone)

    def key_tab(self) -> None:
        if self._zone == "active":
            self._zone = "input"
            self._focus_input_field("fi-name")
            self._update_footer("input")
            return

        current = self._current_input_field()
        if current is None:
            self._focus_input_field(self._INPUT_FIELDS[0])
            return

        idx = self._INPUT_FIELDS.index(current)
        if idx < len(self._INPUT_FIELDS) - 1:
            self._focus_input_field(self._INPUT_FIELDS[idx + 1])
        else:
            self._focus_active_panel()

    # keyboard

    def on_key(self, event: events.Key) -> None:
        key = event.key

        if key == "tab":
            event.stop()
            return

        if self._zone == "active":
            if key == "up":
                self._active_row_up()
                event.stop()
            elif key == "down":
                self._active_row_down()
                event.stop()
            elif key == "left":
                self._active_chip_left()
                event.stop()
            elif key == "right":
                self._active_chip_right()
                event.stop()
            elif key == "x":
                self._active_remove_focused()
                event.stop()
            return

        # input zone
        if key == "up":
            if self._nav_suggestions(-1):
                event.stop()
        elif key == "down":
            if self._nav_suggestions(1):
                event.stop()
        elif key == "backspace":
            self._handle_backspace()

    # active panel chip navigation

    def _active_current_chip(self) -> ChipButton | None:
        focused = self.focused
        if isinstance(focused, ChipButton):
            return focused
        return None

    def _active_chips(self) -> list[ChipButton]:
        tag_row = self.query_one("#fi-tag-chips", Horizontal)
        country_row = self.query_one("#fi-country-chips", Horizontal)
        return list(tag_row.query(ChipButton)) + list(country_row.query(ChipButton))

    def _active_row_up(self) -> None:
        cur = self._active_current_chip()
        if cur is None:
            return
        tag_chips = list(self.query_one("#fi-tag-chips", Horizontal).children)
        country_chips = list(self.query_one("#fi-country-chips", Horizontal).children)
        if cur in tag_chips:
            return
        all_chips = tag_chips + country_chips
        target = tag_chips[-1] if tag_chips else all_chips[0]
        if target:
            target.focus()

    def _active_row_down(self) -> None:
        cur = self._active_current_chip()
        if cur is None:
            return
        tag_chips = list(self.query_one("#fi-tag-chips", Horizontal).children)
        country_chips = list(self.query_one("#fi-country-chips", Horizontal).children)
        if cur in country_chips:
            return
        all_chips = tag_chips + country_chips
        target = country_chips[0] if country_chips else all_chips[-1]
        if target:
            target.focus()

    def _active_chip_left(self) -> None:
        all_chips = self._active_chips()
        if not all_chips:
            return
        focused = self.focused
        if focused not in all_chips:
            all_chips[-1].focus()
            return
        idx = all_chips.index(focused)
        if idx > 0:
            all_chips[idx - 1].focus()

    def _active_chip_right(self) -> None:
        all_chips = self._active_chips()
        if not all_chips:
            return
        focused = self.focused
        if focused not in all_chips:
            all_chips[0].focus()
            return
        idx = all_chips.index(focused)
        if idx < len(all_chips) - 1:
            all_chips[idx + 1].focus()

    def _active_remove_focused(self) -> None:
        chip = self._active_current_chip()
        if chip is None:
            return
        val = chip._chip_value
        ctype = chip._chip_type
        all_chips = self._active_chips()
        idx = all_chips.index(chip)
        logger.debug("_active_remove_focused: type=%s val=%s idx=%s", ctype, val, idx)
        if ctype == "tag" and val in self._tags:
            self._tags.remove(val)
            self.query_one("#fi-tags-wrap", ChipInput).remove_chip(val)
        elif ctype == "country" and val in self._countries:
            self._countries.remove(val)
            self.query_one("#fi-country-wrap", ChipInput).remove_chip(val)
        self._rebuild_active_panel()
        self.call_after_refresh(self._refocus_after_remove, idx)

    def _refocus_after_remove(self, removed_idx: int) -> None:
        remaining = self._active_chips()
        if remaining:
            focus_idx = min(removed_idx, len(remaining) - 1)
            logger.debug(
                "_refocus_after_remove: remaining=%s focus_idx=%s",
                len(remaining),
                focus_idx,
            )
            remaining[focus_idx].focus()
        else:
            logger.debug("_refocus_after_remove: no_remaining, back_to_input")
            self._zone = "input"
            self._focus_input_field("fi-name")
            self._update_footer("input")

    def _handle_backspace(self) -> None:
        fid = self._current_input_field()
        if fid == "fi-tags":
            wrap = self.query_one("#fi-tags-wrap", ChipInput)
            if not wrap.value and self._tags:
                chip = wrap.remove_last_chip()
                logger.debug("_handle_backspace: tags removed=%s", chip)
                if chip and chip in self._tags:
                    self._tags.remove(chip)
                self._rebuild_active_panel()
        elif fid == "fi-country":
            wrap = self.query_one("#fi-country-wrap", ChipInput)
            if not wrap.value and self._countries:
                chip = wrap.remove_last_chip()
                logger.debug("_handle_backspace: country removed=%s", chip)
                if chip and chip in self._countries:
                    self._countries.remove(chip)
                self._rebuild_active_panel()

    # suggestions (input zone)

    def _active_suggest_list(self) -> ListView | None:
        fid = self._current_input_field()
        if fid == "fi-tags":
            lst = self.query_one("#fi-tag-list", ListView)
        elif fid == "fi-country":
            lst = self.query_one("#fi-country-list", ListView)
        else:
            return None
        return lst if lst.styles.display != "none" else None

    def _nav_suggestions(self, direction: int) -> bool:
        if self._suggest_rebuilding:
            return False
        lst = self._active_suggest_list()
        if lst is None:
            return False
        children = list(lst.children)
        if not children:
            return False
        current = lst.index if lst.index is not None else -1
        lst.index = max(0, min(current + direction, len(children) - 1))
        return True

    # ChipInput / Input events

    def on_chip_input_changed(self, event: ChipInput.Changed) -> None:
        iid = event.input._input_id
        if iid == "fi-tags":
            self._update_tag_suggestions(event.value)
        elif iid == "fi-country":
            self._update_country_suggestions(event.value)

    def on_chip_input_submitted(self, event: ChipInput.Submitted) -> None:
        iid = event.input._input_id
        if iid == "fi-tags":
            if not self._select_highlighted("fi-tag-list", "fi-tags-wrap", self._tags):
                if not event.value.strip():
                    self._submit()
        elif iid == "fi-country":
            if not self._select_highlighted(
                "fi-country-list", "fi-country-wrap", self._countries
            ):
                if not event.value.strip():
                    self._submit()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if getattr(event.input, "id", "") == "fi-name":
            self._submit()

    def on_input_changed(self, event: Input.Changed) -> None:
        if getattr(event.input, "id", "") == "fi-name":
            self._update_active_count()

    # chip selection from suggestion list

    def _select_highlighted(
        self, list_id: str, wrap_id: str, target_list: list[str]
    ) -> bool:
        if self._suggest_rebuilding:
            return False
        lst = self.query_one(f"#{list_id}", ListView)
        if lst.styles.display == "none" or not lst.children:
            return False
        idx = lst.index if lst.index is not None else 0
        items = list(lst.children)
        if idx >= len(items):
            return False
        val = getattr(items[idx], "_value", None)
        if not val or val in target_list:
            return False
        target_list.append(val)
        wrap = self.query_one(f"#{wrap_id}", ChipInput)
        wrap.add_chip(val)
        wrap.value = ""
        self._hide_suggestions()
        self._rebuild_active_panel()
        return True

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        event.stop()
        val = getattr(event.item, "_value", None)
        if val is None:
            return
        logger.debug("on_list_view_selected: list=%s val=%s", event.list_view.id, val)
        if event.list_view.id == "fi-tag-list" and val not in self._tags:
            self._tags.append(val)
            wrap = self.query_one("#fi-tags-wrap", ChipInput)
            wrap.add_chip(val)
            wrap.value = ""
            self._hide_suggestions()
            self._rebuild_active_panel()
            self._zone = "input"
            wrap.focus_input()
        elif event.list_view.id == "fi-country-list" and val not in self._countries:
            self._countries.append(val)
            wrap = self.query_one("#fi-country-wrap", ChipInput)
            wrap.add_chip(val)
            wrap.value = ""
            self._hide_suggestions()
            self._rebuild_active_panel()
            self._zone = "input"
            wrap.focus_input()

    # suggestion dropdowns

    async def _populate_list(
        self, list_id: str, suggestions: list[tuple[str, int]]
    ) -> None:
        self._suggest_rebuilding = True
        lst = self.query_one(f"#{list_id}", ListView)
        existing = list(lst.query(SuggestionItem))

        for i, (name, count) in enumerate(suggestions):
            label = f"{name}  [dim]({count:,})[/dim]"
            if i < len(existing):
                existing[i].query_one(Label).update(label)
                existing[i]._value = name
            else:
                item = SuggestionItem(Label(label))
                item._value = name
                await lst.mount(item)

        for child in existing[len(suggestions) :]:
            await child.remove()

        lst.styles.display = "block" if suggestions else "none"
        self._suggest_rebuilding = False
        if suggestions:
            # toggle None→0 to force reactive watcher even when
            # index was already 0 (newly mounted ListItems at
            # position 0 need -highlight CSS re-applied)
            lst.index = None
            lst.index = 0

    def _cancel_suggest(self) -> None:
        if self._suggest_timer is not None:
            self._suggest_timer.stop()
            self._suggest_timer = None

    async def _on_suggest_timeout(self) -> None:
        self._suggest_timer = None
        kind = self._pending_suggest
        query = self._pending_query
        self._pending_suggest = None
        self._pending_query = ""
        if kind == "tag":
            await self._do_update_tag_suggestions(query)
        elif kind == "country":
            await self._do_update_country_suggestions(query)

    def _update_tag_suggestions(self, query: str) -> None:
        self._cancel_suggest()
        self._pending_suggest = "tag"
        self._pending_query = query
        if query.strip():
            self._suggest_timer = self.set_timer(0.15, self._on_suggest_timeout)
        else:
            self.call_after_refresh(self._on_suggest_timeout)

    async def _do_update_tag_suggestions(self, query: str) -> None:
        suggestions = self._index.get_tag_suggestions(
            query,
            tag_filter=self._tags,
            country_filter=self._countries if self._countries else None,
        )
        logger.debug(
            "_do_update_tag_suggestions: query=%s count=%s", query, len(suggestions)
        )
        await self._populate_list("fi-tag-list", suggestions)

    def _update_country_suggestions(self, query: str) -> None:
        self._cancel_suggest()
        self._pending_suggest = "country"
        self._pending_query = query
        if query.strip():
            self._suggest_timer = self.set_timer(0.15, self._on_suggest_timeout)
        else:
            self.call_after_refresh(self._on_suggest_timeout)

    async def _do_update_country_suggestions(self, query: str) -> None:
        suggestions = self._index.get_country_suggestions(
            query,
            tag_filter=self._tags,
            country_filter=self._countries,
        )
        logger.debug(
            "_do_update_country_suggestions: query=%s count=%s", query, len(suggestions)
        )
        await self._populate_list("fi-country-list", suggestions)

    def _hide_suggestions(self) -> None:
        for lid in ("#fi-tag-list", "#fi-country-list"):
            try:
                lst = self.query_one(lid, ListView)
                lst.clear()
                lst.styles.display = "none"
            except Exception:
                logger.debug("Failed to hide suggestion list")
