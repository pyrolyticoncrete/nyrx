# SPDX-License-Identifier: AGPL-3.0-only

"""Fidelity harness for the item-51 defensive-rendering pass.

Every widget/action site that fed *external* strings through Textual markup
(``Static.update``, ``DataTable.add_row``, ``Button`` labels) was converted to
rich ``Text`` objects, because Textual's markup parser silently DROPS unknown
``[tag]`` segments (``"[PREMIERE]"`` renders as ``""``).

This module proves the conversion is 1:1 for the normal (bracket-free) corpus
and pins the intended divergence: bracketed text that used to vanish now
renders verbatim.

Method: the OLD expression (markup string) and the NEW expression (rich
``Text``) are routed through the exact conversions the widgets use:
``Content.from_markup`` for the old string path and ``Content.from_rich_text``
for the new ``Text`` path: then compared on ``.plain`` plus each span's
normalized visual style (fg/bg colour triplet-or-index and
bold/italic/dim/underline/reverse/blink). The two paths canonicalise style
strings differently (``#A277FF`` vs ``rgb(162,119,255)``, ``red`` vs
``ansi_red``), so both are re-parsed through ``rich.style.Style`` before
comparison.
"""

from __future__ import annotations

from rich.color import Color
from rich.style import Style
from rich.text import Text
from textual.content import Content

_CORPUS = [
    "Some plain name",
    "A B C   trailing   ",
    "R\u00e9sum\u00e9  \u201cQuotes\u201d",
    "Sci-Fi & Fantasy",
]

_BRACKET_CASES = [
    "[PREMIERE]",
    "Armin van Buuren [PREMIERE]",
    "Alan Walker - Faded [Official Video]",
    "Lo-fi Beats [no copyright]",
    "[bold]x[/bold]",
]


def _color_key(color: Color | None) -> tuple | None:
    if color is None:
        return None
    return (color.type, color.triplet if color.triplet is not None else color.number)


def _style_key(style) -> tuple | None:
    if style is None:
        return None
    text = style if isinstance(style, str) else str(style)
    try:
        parsed = Style.parse(text)
    except Exception:
        if text.startswith("ansi_"):
            parsed = Style.parse(text[len("ansi_") :])
        else:
            raise
    return (
        _color_key(parsed.color),
        _color_key(parsed.bgcolor),
        parsed.bold,
        parsed.italic,
        parsed.dim,
        parsed.underline,
        parsed.reverse,
        parsed.blink,
    )


def _sig(content: Content) -> tuple:
    return (
        content.plain,
        [(_style_key(s.style), s.start, s.end) for s in content.spans],
    )


def _old_plain(s: str) -> Content:
    return Content.from_markup(s)


def _new_plain(s: str) -> Content:
    return Content.from_rich_text(Text(s))


def _old_radio_name(s: str) -> Content:
    return Content.from_markup(f"[#edecee]{s}[/]")


def _new_radio_name(s: str) -> Content:
    return Content.from_rich_text(Text(s, style="#edecee"))


def _old_radio_meta(s: str) -> Content:
    return Content.from_markup(f"[#606060]{s}[/]")


def _new_radio_meta(s: str) -> Content:
    return Content.from_rich_text(Text(s, style="#606060"))


def _old_artist_dim(s: str) -> Content:
    return Content.from_markup(f"[dim]{s}[/dim]")


def _new_artist_dim(s: str) -> Content:
    return Content.from_rich_text(Text(s, style="dim"))


def _old_tv_title(s: str) -> Content:
    return Content.from_markup(f"[bold white]{s}[/]")


def _new_tv_title(s: str) -> Content:
    return Content.from_rich_text(Text(s, style="bold white"))


def _old_tv_tagline(s: str) -> Content:
    return Content.from_markup(f"[italic #808080]{s}[/]")


def _new_tv_tagline(s: str) -> Content:
    return Content.from_rich_text(Text(s, style="italic #808080"))


def _old_channel(s: str) -> Content:
    return Content.from_markup(f"[#A277FF]{s}[/#A277FF]")


def _new_channel(s: str) -> Content:
    return Content.from_rich_text(Text(s, style="#A277FF"))


def _old_series(s: str) -> Content:
    return Content.from_markup(f"[#b0b0b0]{s} \u2022 S01E01[/]")


def _new_series(s: str) -> Content:
    return Content.from_rich_text(Text(f"{s} \u2022 S01E01", style="#b0b0b0"))


def _old_heart(s: str) -> Content:
    return Content.from_markup(f"{s}  [#A277FF]\u2764\ufe0e[/]")


def _new_heart(s: str) -> Content:
    t = Text(s)
    t.append("  ")
    t.append("\u2764\ufe0e", style="#A277FF")
    return Content.from_rich_text(t)


def _old_check(s: str) -> Content:
    return Content.from_markup(f"{s}  [#A277FF]\\[\u2713][/]")


def _new_check(s: str) -> Content:
    t = Text(s)
    t.append("  ")
    t.append("[\u2713]", style="#A277FF")
    return Content.from_rich_text(t)


def _old_collection(s: str) -> Content:
    return Content.from_markup(f" {s} [#404040](3)[/]")


def _new_collection(s: str) -> Content:
    t = Text(f" {s}")
    t.append(" ")
    t.append("(3)", style="#404040")
    return Content.from_rich_text(t)


def _old_chip(s: str) -> Content:
    return Content.from_text(s)


def _new_chip(s: str) -> Content:
    return Content.from_text(Text(s))


_PAIRS = [
    ("data-cell", _old_plain, _new_plain),
    ("radio-name", _old_radio_name, _new_radio_name),
    ("radio-meta", _old_radio_meta, _new_radio_meta),
    ("artist-dim", _old_artist_dim, _new_artist_dim),
    ("tv-title", _old_tv_title, _new_tv_title),
    ("tv-tagline", _old_tv_tagline, _new_tv_tagline),
    ("channel", _old_channel, _new_channel),
    ("series", _old_series, _new_series),
    ("heart", _old_heart, _new_heart),
    ("check", _old_check, _new_check),
    ("collection", _old_collection, _new_collection),
    ("chip", _old_chip, _new_chip),
]


def _assert_fidelity(old: Content, new: Content) -> None:
    assert _sig(new) == _sig(old)


class TestFidelityBracketFree:
    """Old markup path and new Text path render identically on normal input."""

    def test_plain_data_cell(self) -> None:
        for s in _CORPUS:
            _assert_fidelity(_old_plain(s), _new_plain(s))

    def test_styled_pairs(self) -> None:
        for name, old, new in _PAIRS:
            for s in _CORPUS:
                _assert_fidelity(old(s), new(s)), f"{name} diverged for {s!r}"

    def test_playback_banner(self) -> None:
        old_lines = [
            "[red]\u258c ! No internet connection. retrying...[/red]",
            "\u258c \u2191 Next: Some plain name",
            "[green]\u258c \u2713 Back online![/green]",
        ]
        old = Content.from_markup("\n".join(old_lines))
        new_lines = [
            Text("\u258c ! No internet connection. retrying...", style="red"),
            Text("\u258c \u2191 Next: Some plain name"),
            Text("\u258c \u2713 Back online!", style="green"),
        ]
        new = Content.from_rich_text(Text("\n").join(new_lines))
        assert new.plain == old.plain
        assert _sig(new) == _sig(old)


class TestBracketPreservation:
    """Bracketed external text now renders verbatim instead of vanishing."""

    def test_styled_pairs_preserve_brackets(self) -> None:
        for name, old, new in _PAIRS:
            for s in _BRACKET_CASES:
                rendered = new(s)
                assert s in rendered.plain, f"{name} lost {s!r}: {rendered.plain!r}"

    def test_plain_data_cell_preserves_brackets(self) -> None:
        for s in _BRACKET_CASES:
            assert _new_plain(s).plain == s

    def test_chip_preserves_brackets(self) -> None:
        assert _new_chip(" [PREMIERE] ").plain == " [PREMIERE] "
        assert _new_chip("[bold]x[/bold]").plain == "[bold]x[/bold]"

    def test_heart_and_check_suffixes(self) -> None:
        assert (
            _new_heart("Armin van Buuren [PREMIERE]").plain
            == "Armin van Buuren [PREMIERE]  \u2764\ufe0e"
        )
        assert (
            _new_check("Armin van Buuren [PREMIERE]").plain
            == "Armin van Buuren [PREMIERE]  [\u2713]"
        )

    def test_playback_banner_preserves_brackets(self) -> None:
        new = Content.from_rich_text(
            Text("\n").join(
                [Text("\u258c \u2191 Next: Alan Walker - Faded [Official Video]")]
            )
        )
        assert new.plain == "\u258c \u2191 Next: Alan Walker - Faded [Official Video]"


class TestOldPathDropsBrackets:
    """Pins the bug being fixed: the OLD markup path silently lost text."""

    def test_premiere_chip_was_empty(self) -> None:
        assert _old_chip(" [PREMIERE] ").plain == "  "

    def test_premiere_cell_was_truncated(self) -> None:
        assert _old_plain("Armin van Buuren [PREMIERE]").plain == "Armin van Buuren "
        assert _old_plain("[PREMIERE]").plain == ""

    def test_official_video_suffix_was_dropped(self) -> None:
        assert (
            _old_plain("Alan Walker - Faded [Official Video]").plain
            == "Alan Walker - Faded "
        )
