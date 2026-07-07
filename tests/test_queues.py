# SPDX-License-Identifier: AGPL-3.0-only

from __future__ import annotations

import threading

import pytest

from nyrx.models import MediaRequest
from nyrx.queues import PlaybackQueue, QueueItem


class TestPlaybackQueue:
    def make_item(self, yt_id: str = "id1", **overrides: object) -> QueueItem:
        data: dict[str, object] = {
            "yt_id": yt_id,
            "title": f"Title {yt_id}",
            "channel": "Chan",
            "duration": 120,
            "url": f"https://example.com/{yt_id}",
        }
        data.update(overrides)
        return QueueItem(
            request=MediaRequest.from_dict(data),  # type: ignore[arg-type]
        )

    # --- empty state ---

    @pytest.mark.parametrize("action", ["clear", "items", "len"])
    def test_empty_state_behavior(self, action: str) -> None:
        q = PlaybackQueue()
        if action == "clear":
            q.clear()
            assert len(q) == 0
        elif action == "items":
            assert q.items == []
        else:
            assert len(q) == 0

    # --- add / FIFO order ---

    def test_add_appends_item(self) -> None:
        q = PlaybackQueue()
        item = self.make_item()
        q.add(item)
        assert len(q) == 1

    def test_add_respects_fifo_order(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        q.add(self.make_item("c"))
        first = q.next()
        assert first is not None and first.yt_id == "a"
        second = q.next()
        assert second is not None and second.yt_id == "b"
        third = q.next()
        assert third is not None and third.yt_id == "c"
        assert q.next() is None

    # --- next ---

    def test_next_returns_none_on_empty(self) -> None:
        q = PlaybackQueue()
        assert q.next() is None

    # --- peek ---

    def test_peek_returns_first_item_without_removal(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        first = q.peek()
        assert first is not None and first.yt_id == "a"
        assert len(q) == 2

    def test_peek_returns_none_on_empty(self) -> None:
        q = PlaybackQueue()
        assert q.peek() is None

    # --- remove by index ---

    def test_remove_by_uid_found(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        q.add(self.make_item("c"))
        target = q.items[1].uid
        assert q.remove_by_uid(target) is True
        assert len(q) == 2
        ids = [item.yt_id for item in q.items]
        assert ids == ["a", "c"]

    def test_remove_by_uid_unknown_noop(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        assert q.remove_by_uid("no-such-uid") is False
        assert len(q) == 1

    def test_remove_by_uid_from_empty_noop(self) -> None:
        q = PlaybackQueue()
        assert q.remove_by_uid("u") is False
        assert len(q) == 0

    # --- remove_by_id ---

    def test_remove_by_id_found(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        result = q.remove_by_id("a")
        assert result is True
        assert len(q) == 1
        assert q.items[0].yt_id == "b"

    def test_remove_by_id_missing(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        result = q.remove_by_id("nonexistent")
        assert result is False
        assert len(q) == 1

    def test_remove_by_id_removes_first_match_only(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item(yt_id="dup", title="A"))
        q.add(self.make_item(yt_id="unique", title="B"))
        q.add(self.make_item(yt_id="dup", title="C"))
        result = q.remove_by_id("dup")
        assert result is True
        ids = [item.yt_id for item in q.items]
        assert ids == ["unique", "dup"]

    # --- move_to_front ---

    def test_move_to_front_moves_item_to_head(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        q.add(self.make_item("c"))
        assert q.move_to_front(2) is True
        ids = [item.yt_id for item in q.items]
        assert ids == ["c", "a", "b"]

    def test_move_to_front_second_item_becomes_head(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        assert q.move_to_front(1) is True
        ids = [item.yt_id for item in q.items]
        assert ids == ["b", "a"]

    def test_move_to_front_head_is_noop(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        assert q.move_to_front(0) is False
        ids = [item.yt_id for item in q.items]
        assert ids == ["a", "b"]

    def test_move_to_front_out_of_range_noop(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        assert q.move_to_front(5) is False
        ids = [item.yt_id for item in q.items]
        assert ids == ["a", "b"]

    # --- clear ---

    def test_clear_removes_all_items(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        q.add(self.make_item("b"))
        q.clear()
        assert len(q) == 0
        assert q.next() is None

    # --- items property (snapshot semantics) ---

    def test_items_returns_snapshot(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        snapshot = q.items
        q.add(self.make_item("b"))
        assert len(snapshot) == 1

    def test_items_mutation_does_not_affect_queue(self) -> None:
        q = PlaybackQueue()
        q.add(self.make_item("a"))
        snapshot = q.items
        snapshot.clear()
        assert len(q) == 1

    # --- thread safety ---

    def test_concurrent_add_and_next(self) -> None:
        q = PlaybackQueue()
        n = 100
        errors: list[Exception] = []
        lock = threading.Lock()

        def add_items() -> None:
            for i in range(n):
                q.add(self.make_item(f"t{i}"))

        def consume_items() -> None:
            consumed = 0
            while consumed < n:
                item = q.next()
                if item is not None:
                    consumed += 1
            with lock:
                errors.append(consumed)

        adder = threading.Thread(target=add_items)
        consumer = threading.Thread(target=consume_items)
        adder.start()
        consumer.start()
        adder.join()
        consumer.join(timeout=5)

        assert len(errors) > 0
        assert q.next() is None

    def test_concurrent_remove_by_id(self) -> None:
        q = PlaybackQueue()
        for i in range(50):
            q.add(self.make_item(f"id{i}"))

        def remove_evens() -> None:
            for i in range(0, 50, 2):
                q.remove_by_id(f"id{i}")

        def remove_odds() -> None:
            for i in range(1, 50, 2):
                q.remove_by_id(f"id{i}")

        t1 = threading.Thread(target=remove_evens)
        t2 = threading.Thread(target=remove_odds)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        assert len(q) == 0

    def test_concurrent_add_and_clear(self) -> None:
        q = PlaybackQueue()

        def add_forever() -> None:
            for i in range(1000):
                q.add(self.make_item(f"id{i}"))

        def clear_often() -> None:
            for _ in range(20):
                q.clear()

        t1 = threading.Thread(target=add_forever)
        t2 = threading.Thread(target=clear_often)
        t1.start()
        t2.start()
        t1.join()
        t2.join()

        remaining = len(q)
        assert isinstance(remaining, int) and remaining >= 0
