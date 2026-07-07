# SPDX-License-Identifier: AGPL-3.0-only

"""Playback queue modal showing current video and upcoming items."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from rich.text import Text
from textual import events
from textual.app import ComposeResult
from textual.containers import Container, Horizontal
from textual.widgets import DataTable, Static

from nyrx.queues import QueueItem
from nyrx.screens.base_modal import BaseModal

if TYPE_CHECKING:
    from nyrx.protocols import MediaAppProtocol

logger = logging.getLogger(__name__)


class QueueModal(BaseModal):
    """Playback queue modal showing current video and upcoming items."""

    BINDINGS = [
        ("ctrl+p", "ignore", ""),
        ("/", "ignore", ""),
        ("enter", "ignore", ""),
        ("left", "ignore", ""),
        ("right", "ignore", ""),
        ("m", "ignore", ""),
        ("d", "ignore", ""),
        ("b", "ignore", ""),
        ("z", "ignore", ""),
        ("tab", "ignore", ""),
        ("ctrl+d", "ignore", ""),
        ("?", "ignore", ""),
    ]

    def action_ignore(self) -> None:
        pass

    def __init__(self, app: MediaAppProtocol) -> None:
        super().__init__()
        self._app = app
        self._delete_mode: bool = False
        self._marked_keys: set[str] = set()

    def compose(self) -> ComposeResult:
        with Container(id="qm-box"):
            with Horizontal(id="qm-heading"):
                yield Static("[white]QUEUE[/white]", id="qm-hd-left")
                yield Static(id="qm-hd-right")
            yield Static("PLAYBACK", id="qm-pb-heading")
            yield DataTable(
                id="qm-playback-list",
                cursor_type="row",
                show_header=False,
                show_row_labels=False,
            )
            yield Static(id="qm-footer")

    def on_mount(self) -> None:
        logger.debug("QueueModal.on_mount")
        dt = self.query_one("#qm-playback-list", DataTable)
        dt.add_column("content", key="content")
        self._rebuild_lists()
        self._update_footer()
        if dt.row_count > 1:
            dt.move_cursor(row=1)
        dt.focus()

    def _update_footer(self) -> None:
        if self._delete_mode:
            self.query_one("#qm-footer", Static).update(
                "\n[white]ctrl+d[/white] clear all  \u2022  [white]space[/white] mark  \u2022  [white]enter[/white] delete confirm  \u2022  [white]x[/white] cancel"
            )
        else:
            self.query_one("#qm-footer", Static).update(
                "\n[white]\u2191\u2193[/white] navigate  \u2022  [white]n[/white] play next  \u2022  [white]x[/white] delete mode  \u2022  [white]esc[/white] close"
            )

    def _rebuild_lists(self) -> None:
        app = self._app
        dt = self.query_one("#qm-playback-list", DataTable)
        np_data = app._now_playing_data
        queue_items = app._playback_queue.items
        logger.debug(
            "_rebuild_lists: np_data=%s queue_len=%s", bool(np_data), len(queue_items)
        )

        cursor_qidx = None
        if dt.cursor_coordinate and dt.cursor_coordinate.row > 0:
            cursor_qidx = dt.cursor_coordinate.row - 1

        dt.clear()

        if np_data:
            dt.add_row(Text("\u25b6  ") + Text(np_data["title"]), key="__np__")
        elif queue_items:
            dt.add_row(
                Text("\u23f9  waiting for connection", style="dim"), key="__np__"
            )

        for item in queue_items:
            dt.add_row(self._make_queue_label(item), key=item.uid)

        pb_count = (1 if np_data or queue_items else 0) + len(queue_items)
        self.query_one("#qm-pb-heading", Static).update(
            f"PLAYBACK  \u00b7  {pb_count} item{'s' if pb_count != 1 else ''}"
            if pb_count
            else "PLAYBACK"
        )

        self.query_one("#qm-hd-right", Static).update(
            "[dim]Queue is empty. Add items from search results.[/dim]"
            if pb_count == 0
            else ""
        )
        self.query_one("#qm-hd-left", Static).update("[white]QUEUE[/white]")

        dt.set_class(dt.row_count <= 1, "qm-sole")

        if dt.row_count > 1:
            restore_row = max(
                1,
                min(
                    cursor_qidx + 1 if cursor_qidx is not None else 1, dt.row_count - 1
                ),
            )
            dt.move_cursor(row=restore_row)

    def _make_queue_label(self, item: QueueItem) -> Text:
        is_marked = item.uid in self._marked_keys

        t = Text()
        if is_marked:
            t.append("\u2713 ")
        else:
            t.append("  ")

        label = "[audio]" if item.audio_only else "[video]"
        t.append(f"{label}  ", style="dim")
        t.append(Text(item.title, overflow="ellipsis"))

        return t

    def on_key(self, event: events.Key) -> None:
        if event.key in ("left", "right"):
            event.prevent_default()
            event.stop()
            return
        if event.key not in (
            "up",
            "down",
            "pageup",
            "pagedown",
            "home",
            "end",
            "ctrl+home",
            "ctrl+end",
        ):
            return
        event.prevent_default()
        event.stop()
        dt = self.query_one("#qm-playback-list", DataTable)
        page = max(1, dt.size.height - 2)

        if event.key in ("down", "pagedown"):
            step = 1 if event.key == "down" else page
            new_row = min(dt.row_count - 1, dt.cursor_coordinate.row + step)
        elif event.key in ("up", "pageup"):
            step = 1 if event.key == "up" else page
            new_row = max(1, dt.cursor_coordinate.row - step)
        elif event.key in ("home", "ctrl+home"):
            new_row = 1
        else:
            new_row = max(1, dt.row_count - 1)
        dt.move_cursor(row=new_row)
        if new_row == 1:
            dt.scroll_home(animate=False)

    def key_n(self, event: events.Key) -> None:
        """Move selected item to front of queue."""
        if self._delete_mode:
            return
        event.stop()
        dt = self.query_one("#qm-playback-list", DataTable)
        if dt.cursor_coordinate is None or dt.cursor_coordinate.row < 1:
            return
        idx = dt.cursor_coordinate.row - 1
        app = self._app
        if app._playback_queue.move_to_front(idx):
            app._sync_np_widget()
            self._rebuild_lists()
            if dt.row_count > 1:
                dt.move_cursor(row=1)
            dt.focus()

    def key_x(self) -> None:
        self._delete_mode = not self._delete_mode
        logger.debug("key_x: delete_mode=%s", self._delete_mode)
        if not self._delete_mode:
            dt = self.query_one("#qm-playback-list", DataTable)
            marked = list(self._marked_keys)
            self._marked_keys.clear()
            for uid in marked:
                item = next(
                    (i for i in self._app._playback_queue.items if i.uid == uid), None
                )
                if item:
                    dt.update_cell(uid, "content", self._make_queue_label(item))
        self._update_footer()
        self.query_one("#qm-hd-left", Static).update(
            "[white]DELETE MODE[/white]"
            if self._delete_mode
            else "[white]QUEUE[/white]"
        )

    def key_space(self) -> None:
        if not self._delete_mode:
            return
        dt = self.query_one("#qm-playback-list", DataTable)
        if dt.cursor_coordinate is None or dt.cursor_coordinate.row < 1:
            return
        row_key = dt.coordinate_to_cell_key(dt.cursor_coordinate).row_key
        uid = row_key.value
        if not uid:
            return
        if uid in self._marked_keys:
            self._marked_keys.discard(uid)
            logger.debug("key_space: unmark uid=%s", uid[:8])
        else:
            self._marked_keys.add(uid)
            logger.debug("key_space: mark uid=%s", uid[:8])
        item = next((i for i in self._app._playback_queue.items if i.uid == uid), None)
        if item:
            dt.update_cell(row_key, "content", self._make_queue_label(item))

    def key_ctrl_d(self) -> None:
        if not self._delete_mode:
            return
        app = self._app
        if not app._playback_queue:
            return
        logger.debug("key_ctrl_d: clearing queue")
        app._playback_queue.clear()
        app._sync_np_widget()
        self._delete_mode = False
        self._marked_keys.clear()
        self._rebuild_lists()
        self._update_footer()
        self.query_one("#qm-hd-left", Static).update("[white]QUEUE[/white]")
        dt = self.query_one("#qm-playback-list", DataTable)
        dt.set_class(dt.row_count <= 1, "qm-sole")
        if dt.row_count > 1:
            dt.move_cursor(row=1)
        dt.focus()

    def _delete_marked(self) -> None:
        app = self._app
        if not self._marked_keys:
            return
        logger.debug("_delete_marked: count=%s", len(self._marked_keys))
        for uid in self._marked_keys:
            app._playback_queue.remove_by_uid(uid)
        app._sync_np_widget()
        self._delete_mode = False
        self._marked_keys.clear()
        self._rebuild_lists()
        self._update_footer()
        dt = self.query_one("#qm-playback-list", DataTable)
        dt.set_class(dt.row_count <= 1, "qm-sole")
        if dt.row_count > 1:
            dt.move_cursor(row=1)
        dt.focus()

    def key_enter(self, event: events.Key) -> None:
        event.stop()
        if self._delete_mode:
            self._delete_marked()
            return
        logger.debug("key_enter: no-op")

    def key_escape(self, event: events.Key) -> None:
        event.stop()
        if self._delete_mode:
            logger.debug("key_escape: exiting delete mode")
            dt = self.query_one("#qm-playback-list", DataTable)
            marked = list(self._marked_keys)
            self._marked_keys.clear()
            for uid in marked:
                item = next(
                    (i for i in self._app._playback_queue.items if i.uid == uid), None
                )
                if item:
                    dt.update_cell(uid, "content", self._make_queue_label(item))
            self._delete_mode = False
            self._update_footer()
            self.query_one("#qm-hd-left", Static).update("[white]QUEUE[/white]")
            return
        logger.debug("key_escape: dismissing")
        self.dismiss(None)
