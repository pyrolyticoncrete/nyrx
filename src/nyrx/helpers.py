# SPDX-License-Identifier: AGPL-3.0-only

"""Small shared helpers used across the codebase."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import sqlite3

BRAILLE_SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"


def build_waveform(
    samples: list[int],
    row_height: int,
    data: list[str],
) -> tuple[list[float], list[list[str]]]:
    """Normalize SC waveform samples and render rows of braille glyphs.

    Pure computation: no widget/Textual state: so callers may run it on a
    worker thread and hand the finished result to the main thread.

    Returns ``(normalized_samples, rendered_rows)`` where ``rendered_rows``
    is ``row_height`` parallel rows (one glyph per sample-pair), matching the
    layout expected by the SC now-playing visualizer.
    """
    sorted_s = sorted(samples)
    n = len(sorted_s)
    lo = sorted_s[int(n * 0.02)]
    hi = sorted_s[int(n * 0.98)]
    span = hi - lo
    ds = (
        [max(0.0, min(1.0, (s - lo) / span)) for s in samples]
        if span > 0
        else [0.0] * len(samples)
    )

    rows: list[list[str]] = [[] for _ in range(row_height)]
    err_l = [0.0] * row_height
    err_r = [0.0] * row_height
    for i in range(0, len(ds) - 1, 2):
        l_val = ds[i] * 100
        r_val = ds[i + 1] * 100
        for row in range(row_height):
            band_top = (row_height - row) * 100 / row_height
            band_bot = (row_height - (row + 1)) * 100 / row_height
            span_b = band_top - band_bot

            def _continuous(pct: float) -> float:
                if pct >= band_top:
                    return 4.0
                if pct <= band_bot:
                    return 0.0
                return (pct - band_bot) * 4 / span_b

            l_target = _continuous(l_val) + err_l[row]
            r_target = _continuous(r_val) + err_r[row]
            l_level = max(0, min(4, round(l_target)))
            r_level = max(0, min(4, round(r_target)))
            err_l[row] = l_target - l_level
            err_r[row] = r_target - r_level
            rows[row].append(data[l_level * 5 + r_level])

    return ds, rows


@contextmanager
def db_scope(
    conn_factory: Callable[[], sqlite3.Connection],
) -> Iterator[sqlite3.Connection]:
    """Yield a connection from *conn_factory*, guaranteeing it is closed.

    Single source of truth for connection lifecycle so callers never leak
    an open ``sqlite3`` handle on any exit path (return, early return, or
    exception).
    """
    conn = conn_factory()
    try:
        yield conn
    finally:
        conn.close()


def iterate_episode_range(
    start_s: int,
    start_e: int,
    end_s: int,
    end_e: int,
    season_map: dict[int, int],
) -> list[tuple[int, int]]:
    """Return (season, episode) tuples for each step in the range.

    Wraps across seasons using season_map (season_number -> episode_count).
    Assumes input is pre-validated: all (s, e) pairs are in-bounds.
    """
    result: list[tuple[int, int]] = []
    s, e = start_s, start_e
    while (s, e) <= (end_s, end_e):
        result.append((s, e))
        e += 1
        if s in season_map and e > season_map[s]:
            s += 1
            e = 1
        elif s not in season_map:
            s += 1
            e = 1
    return result


def require_key(k: str | None) -> str:
    """Assert that a DataTable row key is not None at runtime.

    Textual's ``StringKey.value`` is typed ``str | None`` (a third-party
    library constraint), but in practice row keys are always provided and
    never None.  This helper narrows the type to ``str`` for mypy without
    repeating the assertion at every call site.
    """
    if k is None:
        raise ValueError("DataTable row key should not be None")
    return k
