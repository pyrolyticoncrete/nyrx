# SPDX-License-Identifier: AGPL-3.0-only

"""Thread-safe playback queue and download item types.

Provides the data structures used by the TUI to manage the
playback queue (FIFO with move-to-front reorder) and the
pending download backlog.
"""

from __future__ import annotations

import logging
import threading
import uuid
from dataclasses import dataclass, field

from nyrx.models import MediaRequest

logger = logging.getLogger(__name__)


class DownloadCancelled(Exception):  # noqa: N818
    """Raised inside yt-dlp progress hooks to abort an in-progress download."""

    pass


@dataclass
class QueueItem:
    """A single entry in the playback queue.

    Holds a typed ``MediaRequest`` directly so dequeuing is lossless.
    Passthrough properties preserve the old ``item.yt_id`` / ``item.title`` /
    ``item.audio_only`` access pattern for ``remove_by_id()`` and
    ``_make_queue_label()``.
    """

    request: MediaRequest
    uid: str = field(default_factory=lambda: str(uuid.uuid4()))

    @property
    def yt_id(self) -> str:
        return self.request.yt_id

    @property
    def title(self) -> str:
        return self.request.title

    @property
    def audio_only(self) -> bool:
        return self.request.audio_only


class PlaybackQueue:
    """Thread-safe FIFO queue for upcoming video playback."""

    def __init__(self) -> None:
        self._items: list[QueueItem] = []
        self._lock = threading.Lock()
        logger.debug("PlaybackQueue: created")

    def add(self, item: QueueItem) -> None:
        """Append an item to the end of the queue."""
        with self._lock:
            self._items.append(item)
            logger.debug(
                "add: yt_id=%s title=%s qlen=%d",
                item.yt_id,
                item.title,
                len(self._items),
            )

    def next(self) -> QueueItem | None:
        """Pop and return the first item, or None if empty."""
        with self._lock:
            if self._items:
                item = self._items.pop(0)
                logger.debug(
                    "next: yt_id=%s title=%s qlen=%d",
                    item.yt_id,
                    item.title,
                    len(self._items),
                )
                return item
            logger.debug("next: empty")
            return None

    def peek(self) -> QueueItem | None:
        """Return the first item without removing it."""
        with self._lock:
            if self._items:
                return self._items[0]
            return None

    def remove_by_id(self, yt_id: str) -> bool:
        """Remove the first item matching *yt_id*. Returns True if found."""
        with self._lock:
            for i, item in enumerate(self._items):
                if item.yt_id == yt_id:
                    self._items.pop(i)
                    logger.debug(
                        "remove_by_id: yt_id=%s found=True qlen=%d",
                        yt_id,
                        len(self._items),
                    )
                    return True
            logger.debug("remove_by_id: yt_id=%s found=False", yt_id)
        return False

    def remove_by_uid(self, uid: str) -> bool:
        """Remove the item with the given *uid*. Returns True if found."""
        with self._lock:
            for i, item in enumerate(self._items):
                if item.uid == uid:
                    self._items.pop(i)
                    logger.debug(
                        "remove_by_uid: uid=%s found=True qlen=%d",
                        uid,
                        len(self._items),
                    )
                    return True
            logger.debug("remove_by_uid: uid=%s found=False", uid)
        return False

    def move_to_front(self, idx: int) -> bool:
        """Move item at *idx* to position 0. Returns True if moved."""
        with self._lock:
            if 0 < idx < len(self._items):
                item = self._items.pop(idx)
                self._items.insert(0, item)
                logger.debug("move_to_front: idx=%d qlen=%d", idx, len(self._items))
                return True
        return False

    def clear(self) -> None:
        """Remove all items."""
        with self._lock:
            n = len(self._items)
            self._items.clear()
            logger.debug("clear: removed=%d", n)

    @property
    def items(self) -> list[QueueItem]:
        """Return a snapshot of all queued items."""
        with self._lock:
            return list(self._items)

    def __len__(self) -> int:
        with self._lock:
            return len(self._items)
