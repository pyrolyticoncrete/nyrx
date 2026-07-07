# SPDX-License-Identifier: AGPL-3.0-only

"""Keybinding reference data and help text for the TUI."""

KEYBIND_BAR_TEXT: dict[str, str] = {
    "tv-movies-home": (
        "/ [dim]search[/dim]  \u2022  "
        "l [dim]bookmark[/dim]  \u2022  "
        "z [dim]info[/dim]  \u2022  "
        "m [dim]server[/dim]  \u2022  "
        "ctrl+p [dim]commands[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "tv-movies-search": (
        "enter [dim]open/play[/dim]  \u2022  "
        "l [dim]bookmark[/dim]  \u2022  "
        "d [dim]download[/dim]  \u2022  "
        "z [dim]info[/dim]  \u2022  "
        "m [dim]server[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "yt-home": (
        "enter [dim]select[/dim]  \u2022  "
        "m [dim]mode[/dim]  \u2022  "
        "ctrl+p [dim]commands[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "yt-search": (
        "\u2190 \u2192 [dim]page[/dim]  \u2022  "
        "tab [dim]focus[/dim]  \u2022  "
        "d [dim]download[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "sc-home": (
        "/ [dim]search[/dim]  \u2022  "
        "enter [dim]select[/dim]  \u2022  "
        "ctrl+p [dim]commands[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "sc-search": (
        "\u2190 \u2192 [dim]page[/dim]  \u2022  "
        "s [dim]station[/dim]  \u2022  "
        "tab [dim]focus[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "liked": (
        "l [dim]unlike[/dim]  \u2022  "
        "f [dim]follow[/dim]  \u2022  "
        "/ [dim]filter[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "? [dim]keys[/dim]"
    ),
    "following": (
        "ctrl+d [dim]unfollow[/dim]  \u2022  "
        "enter [dim]profile[/dim]  \u2022  "
        "\u2190 \u2192 [dim]panel[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "feed": (
        "ctrl+a [dim]queue all[/dim]  \u2022  "
        "r [dim]regen[/dim]  \u2022  "
        "s [dim]station[/dim]  \u2022  "
        "l [dim]like[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "artist-profile": (
        "s [dim]station[/dim]  \u2022  "
        "l [dim]like[/dim]  \u2022  "
        "f [dim]follow[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "watchlist": (
        "w [dim]toggle watched[/dim]  \u2022  "
        "/ [dim]filter[/dim]  \u2022  "
        "ctrl+d [dim]delete[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "radio": "[white]/[/white] [dim]filter[/dim]  \u2022  [white]l[/white] [dim]like[/dim]  \u2022  [white]enter[/white] [dim]play[/dim]  \u2022  [dim]\\[?][/dim]",
    "tv-movies-series": (
        "w [dim]toggle watched[/dim]  \u2022  "
        "/ [dim]jump to[/dim]  \u2022  "
        "enter [dim]play[/dim]  \u2022  "
        "d [dim]download[/dim]  \u2022  "
        "z [dim]info[/dim]  \u2022  "
        "esc [dim]back[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
}

CONTEXT_LABELS: dict[str, str] = {
    "yt-home": "Youtube",
    "yt-search": "Youtube Search",
    "sc-home": "Soundcloud",
    "sc-search": "Soundcloud Search",
    "liked": "Liked",
    "watchlist": "Watchlist",
    "following": "Following",
    "feed": "Feed",
    "artist-profile": "Artist Profile",
    "radio": "Radio",
    "np": "Now Playing",
    "np-sc": "Now Playing \u00b7 Soundcloud",
    "tv-movies-home": "TV & Movies",
    "tv-movies-search": "TV & Movies Search",
    "tv-movies-series": "TV Series",
}

SIDEBAR_KEYBIND_TEXT: dict[str, str] = {
    "np": (
        "space [dim]play/pause[/dim]  \u2022  "
        "\u2190 \u2192 [dim]seek[/dim]  \u2022  "
        "x [dim]stop[/dim]  \u2022  "
        "tab [dim]focus[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
    "np-sc": (
        "space [dim]play/pause[/dim]  \u2022  "
        "l [dim]like[/dim]  \u2022  "
        "s [dim]station[/dim]  \u2022  "
        "x [dim]stop[/dim]  \u2022  "
        "tab [dim]focus[/dim]  \u2022  "
        "[dim]\\[?][/dim]"
    ),
}

KEY_REFERENCE: dict[str, list[tuple[str, list[tuple[str, str]]]]] = {
    "yt-home": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("tab", "cycle focus"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("m", "toggle audio / video mode"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("enter", "select"),
                ("/", "search"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "yt-search": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "previous / next page"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play"),
                ("m", "toggle audio / video mode"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("/", "search"),
                ("d", "download"),
                ("z", "view thumbnail"),
                ("b", "open in browser"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "sc-home": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193 \u2190 \u2192", "navigate genre chips + recents"),
                ("tab", "cycle focus"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play / select"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("/", "search"),
                ("ctrl+l", "liked tracks"),
                ("ctrl+f", "following + feed"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "sc-search": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "previous / next page"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("/", "search"),
                ("l", "like / unlike"),
                ("f", "follow / unfollow artist"),
                ("s", "queue track station"),
                ("d", "download"),
                ("z", "view thumbnail"),
                ("b", "open in browser"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "liked": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("/", "filter"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play"),
                ("space", "pause / resume"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("l", "unlike / like (buffered until exit)"),
                ("f", "follow / unfollow artist"),
                ("s", "track station"),
                ("d", "download"),
                ("z", "view thumbnail"),
                ("b", "open in browser"),
                ("ctrl+p", "command palette (sync liked)"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "watchlist": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("/", "filter"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play / open / queue"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("d", "download"),
                ("b", "open in browser"),
                ("w", "toggle watched"),
                ("ctrl+p", "command palette"),
                ("ctrl+d", "delete bookmark (twice to confirm)"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "following": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "switch between panels"),
                ("enter", "open artist profile"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("ctrl+d", "unfollow artist"),
                ("/", "filter"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "feed": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "switch between panels"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play"),
                ("`", "open queue"),
                ("ctrl+a", "queue all tracks"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("r", "regenerate feed"),
                ("s", "queue track station"),
                ("l", "like / unlike"),
                ("f", "follow / unfollow artist"),
                ("d", "download"),
                ("z", "view thumbnail"),
                ("b", "open in browser"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "artist-profile": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "switch uploads / liked tab"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play / open"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("s", "queue track station"),
                ("l", "like / unlike"),
                ("f", "follow / unfollow"),
                ("d", "download track"),
                ("z", "view thumbnail"),
                ("b", "open in browser"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "radio": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "previous / next page"),
                ("/", "filter"),
                ("tab", "cycle focus"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play / queue"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("l", "like / pin to top"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "np": [
        (
            "PLAYBACK",
            [
                ("\u2190 \u2192", "seek"),
                ("space", "play / pause"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("x", "stop / cancel mode"),
                ("enter", "confirm cancel"),
            ],
        ),
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("tab", "switch focus"),
                ("esc", "back"),
            ],
        ),
    ],
    "np-sc": [
        (
            "PLAYBACK",
            [
                ("\u2190 \u2192", "seek"),
                ("space", "play / pause"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("l", "like / unlike"),
                ("f", "follow / unfollow"),
                ("s", "queue track station"),
                ("x", "stop / cancel mode"),
                ("enter", "confirm cancel"),
            ],
        ),
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("tab", "switch focus"),
                ("esc", "back"),
            ],
        ),
    ],
    "tv-movies-home": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("tab", "cycle focus"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "open / play / queue"),
                ("m", "cycle server"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("/", "search"),
                ("l", "bookmark"),
                ("d", "download"),
                ("z", "info"),
                ("ctrl+w", "watchlist"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "tv-movies-search": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate"),
                ("\u2190 \u2192", "previous / next page"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "open / play / queue"),
                ("m", "cycle server"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("/", "search"),
                ("l", "bookmark"),
                ("d", "download"),
                ("z", "info"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
    "tv-movies-series": [
        (
            "NAVIGATION",
            [
                ("\u2191 \u2193", "navigate episodes"),
                ("\u2190 \u2192", "switch season"),
                ("tab", "cycle focus"),
                ("esc", "back"),
            ],
        ),
        (
            "PLAYBACK",
            [
                ("enter", "play episode"),
                ("`", "open queue"),
            ],
        ),
        (
            "ACTIONS",
            [
                ("w", "toggle watched"),
                ("/", "jump to season"),
                ("d", "download"),
                ("z", "info"),
                ("ctrl+p", "command palette"),
            ],
        ),
        (
            "SYSTEM",
            [
                ("f1", "Youtube"),
                ("f2", "Soundcloud"),
                ("f3", "Radio"),
                ("f4", "TV/Movies"),
                ("q", "quit (twice to confirm)"),
            ],
        ),
    ],
}
