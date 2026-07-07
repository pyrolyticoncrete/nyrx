# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for KeyReferenceModal two-column layout."""

from __future__ import annotations

import pytest
from textual.app import App
from textual.widgets import Static

from nyrx.bindings import KEY_REFERENCE
from nyrx.screens.key_reference import KeyReferenceModal, _split_sections

# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def _col_height(cols):
    """Row count for a column (header + keys + blanks between sections)."""
    return sum(1 + len(b) for _, b in cols) + max(0, len(cols) - 1)


class TestSplitSections:
    def test_union_matches_original(self):
        for ctx, sections in KEY_REFERENCE.items():
            left, right = _split_sections(sections)
            combined = left + right
            assert len(combined) == len(sections), f"context={ctx}"
            assert {name for name, _ in combined} == {name for name, _ in sections}, (
                f"context={ctx}"
            )

    def test_balanced_heights(self):
        for ctx, sections in KEY_REFERENCE.items():
            left, right = _split_sections(sections)
            h_left, h_right = _col_height(left), _col_height(right)
            assert abs(h_left - h_right) <= 5, (
                f"context={ctx} left={h_left} right={h_right}"
            )

    def test_preserves_relative_order(self):
        for ctx, sections in KEY_REFERENCE.items():
            left, right = _split_sections(sections)
            order = [name for name, _ in sections]
            for col in (left, right):
                names = [name for name, _ in col]
                assert names == sorted(names, key=lambda n: order.index(n)), (
                    f"context={ctx} col={names}"
                )


# ---------------------------------------------------------------------------
# Render tests
# ---------------------------------------------------------------------------


class _ModalHost(App):
    def compose(self):
        yield Static("base")


@pytest.mark.asyncio
class TestKeyRefModalCSS:
    @pytest.mark.parametrize(
        "ctx",
        ["feed", "sc-search", "liked", "artist-profile"],
    )
    async def test_two_col_at_h23(self, ctx: str) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 23)) as pilot:
            app.push_screen(KeyReferenceModal(ctx))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            left = app.screen.query_one("#kr-col-left", Static)
            right = app.screen.query_one("#kr-col-right", Static)
            assert right.display is True, f"context={ctx}"
            assert left.size.height > 0
            assert right.size.height > 0

    async def test_box_width_always_106(self) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 40)) as pilot:
            app.push_screen(KeyReferenceModal("feed"))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            box = app.screen.query_one("#kr-box")
            assert box.size.width == 106

    async def test_title_spans_full_width(self) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 23)) as pilot:
            app.push_screen(KeyReferenceModal("feed"))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            title = app.screen.query_one("#kr-title", Static)
            assert title.size.width > 68

    async def test_title_appears_exactly_once(self) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 40)) as pilot:
            app.push_screen(KeyReferenceModal("yt-home"))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            title = app.screen.query_one("#kr-title", Static)
            assert "KEY REFERENCE" in str(title.render())
            left = app.screen.query_one("#kr-col-left", Static)
            assert "KEY REFERENCE" not in str(left.render())

    async def test_footer_spans_full_width(self) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 23)) as pilot:
            app.push_screen(KeyReferenceModal("feed"))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            footer = app.screen.query_one("#kr-footer", Static)
            footer_text = str(footer.render())
            assert "esc" in footer_text
            assert "close" in footer_text
            assert footer.size.width > 0

    async def test_footer_is_lowest_item(self) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 23)) as pilot:
            app.push_screen(KeyReferenceModal("feed"))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            footer = app.screen.query_one("#kr-footer", Static)
            right = app.screen.query_one("#kr-col-right", Static)
            assert footer.region.y >= right.region.y + right.size.height

    async def test_right_not_empty_for_all_contexts(self) -> None:
        for ctx, sections in KEY_REFERENCE.items():
            app = _ModalHost()
            async with app.run_test(size=(165, 23)) as pilot:
                app.push_screen(KeyReferenceModal(ctx))
                for _ in range(10):
                    await pilot.pause()
                    if isinstance(app.screen, KeyReferenceModal):
                        break
                right = app.screen.query_one("#kr-col-right", Static)
                assert right.display is True, f"context={ctx}"
                assert right.size.height > 0, f"context={ctx}"

    async def test_no_longest_line_overflows_column(self) -> None:
        app = _ModalHost()
        async with app.run_test(size=(165, 40)) as pilot:
            app.push_screen(KeyReferenceModal("liked"))
            for _ in range(10):
                await pilot.pause()
                if isinstance(app.screen, KeyReferenceModal):
                    break
            left = app.screen.query_one("#kr-col-left", Static)
            assert left.size.width >= 51
