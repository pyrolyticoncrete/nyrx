<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/a.png" alt="nyrxmain" width="800">
</p>

<p align="center">
  <a href="https://pypi.org/project/nyrx/"><img src="https://img.shields.io/pypi/v/nyrx?color=a277ff&label=PyPI" alt="PyPI"></a>
  <a href="https://github.com/pyrolyticoncrete/nyrx/blob/main/LICENSE.md"><img src="https://img.shields.io/github/license/pyrolyticoncrete/nyrx?color=a277ff" alt="License"></a>
</p>

## Overview

**nyrx** is a terminal media center. It brings Youtube, Soundcloud, internet radio, and TV/Movies into a single keyboard-driven interface.
Search, play, queue, download, and browse across four media sources from one terminal window. No browser, no Electron app, no accounts.

**Who is this for?**
If your media diet is Youtube, Soundcloud, radio, and TV/Movies, and you generally know what you want to watch or listen to, nyrx is built for that.

- **Youtube mode** is search-based: no algorithm, no related videos, no channel browsing. You search, you play, it remembers what you've watched. Audio and video modes both supported.
- **Soundcloud mode** is built for discovery: follow artists, build a feed, browse trending by genre, like tracks, start stations.
- **Radio mode** has 62k stations worldwide, filter by name, tags, or country, like stations to pin them.
- **TV/Movies mode** lets you search TMDb, bookmark media, browse seasons and episodes, watch and track what you've watched. Requires user-configured server plugins (Lua scripts) to find streaming sources.
- **Privacy-first:** no accounts, no logins, no tracking. The app doesn't know who you are.

**Not for you if:**
- You need deep Youtube browsing with algorithms, playlists, or channel pages
- You want to connect accounts or sync with official services
- You don't use Youtube, Soundcloud, radio, and don't care to know what TV/Movies mode is all about.
- You need mouse support (nyrx is keyboard-only)
- You are on Windows (not supported)

New to terminal apps? nyrx is approachable and built with UX in mind. **Keep scrolling for screenshots.**

*For channel browsing, playlist management, subscriptions, and deeper Youtube features, check out [youtube-tui](https://github.com/Siriusmart/youtube-tui).*

---

## Requirements

- Python 3.12 or higher
- mpv installed and on your PATH
- Linux or macOS (Windows is not supported due to mpv Unix socket dependency)
- Internet connection (required for streaming and searching)

Everything else is installed automatically by pip or dealt with on your first start-up.

nyrx has been tested on Linux via Kitty, Ghostty and Konsole. It should work on macOS, but has not been tested there.

**Kitty is recommended**. Terminals using Sixel (WezTerm, foot) may have sizing issues due to how textual-image handles Sixel rendering. Terminals without image support will render images using colored Unicode characters (last resort). 

> [!TIP]
> Minimum terminal size is 165x23. The app is fully functional at that size, but bigger is better. If you can, dedicate a virtual desktop to nyrx and switch back and forth to control the app while you do other things.

> [!NOTE]
> If you lose internet connection, the queue pauses and an offline banner appears. When the connection returns, everything resumes automatically.

---

## Quick Start

```
pip install nyrx
```

You will also need **mpv (a very capable media player)** installed on your system:

```
# Linux (Debian/Ubuntu)
sudo apt install mpv

# Linux (Fedora)
sudo dnf install mpv

# Linux (Arch)
sudo pacman -S mpv

# macOS
brew install mpv
```

Then just run it:

```
nyrx
```

nyrx handles everything else on first launch. It checks for mpv, installs Playwright Chromium if it is missing (for TV/Movies server probing), and bundles ffmpeg so you do not have to worry about that. You will get a clean pre-flight summary before the app opens.

<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/b.png" alt="Pre-flight dependency check" width="500">
</p>

> [!TIP]
> On first launch, nyrx prompts you to enter a manifest URL for TV/Movies server plugins. You can skip this and configure it later via the command palette ("Configure Lua plugin source"). No plugins are fetched without your input.

---

## What Can nyrx Do

Press F1 through F4 to switch between sources. Each source has its own landing view, and features. The queue is source-agnostic, so you can mix Youtube, Soundcloud, radio, and TV/Movies in a single playback session.

<p align="center">
	  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/c.gif" alt="Mode switcher" width="800">
</p>

### Youtube
- Search videos and paste URLs directly to play media
- Play video or audio (toggle with m)
- Download with quality presets (480p to 2160p)
- Persistent search history (last 10 queries)
- Open in browser (b), copy media URL to clipboard
- Watched indicators on search results

> [!NOTE]
> **DRM and mature content.** nyrx cannot play DRM-protected tracks from Soundcloud or mature-rated content from Youtube. This is a limitation of the scraping approach, not something that can be worked around.

<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/d.png" alt="Youtube search" width="800">
</p>

### Soundcloud
- Search tracks and browse artist's profiles
- Trending playlists by 14 genres and 31 regions
- Follow artists, like tracks, build a personalized feed
- Start a station from any track (auto-queues similar music)
- Sync liked tracks from a public profile
- Collection browser for artist playlists and albums
- Download audio

<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/e.jpg" alt="Soundcloud homepage" width="700">
</p>

### Radio
- 62,000+ internet radio stations from around the world
- Filter by name, tags, or country with autocomplete
- Like stations to pin them to the top of the list
- Live ICY metadata (now-playing title) during playback
- Station index auto-refreshes every 6 days

<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/f.gif" alt="Radio station table" width="800">
</p>

### TV and Movies
- Search TMDb for movies and TV shows with filter chips (All/Movies/TV)
- Bookmark shows and build a watchlist
- Get recommendations based on your watchlist
- Browse TV series by season and episode
- Track what you have watched (per-episode and per-movie status)
- Poster art, ratings, genres, and overview text
- Batch episode downloads with deduplication
- Subtitle and multi-audio track muxing into MKV
- Server plugins fetched from a user-configured manifest URL (hotswap)
- Write your own server plugins via the Lua plugin system ([scroll to the end to read more](#server-plugins-optional))
- Cycle through specific streaming server plugins or use auto mode

> [!NOTE]
> TV/Movies metadata is fetched through a third-party TMDb proxy, so **no API key is required** to use the mode. For extra reliability (or to avoid the third-party proxy), you can plug in your own free TMDb API key via the command palette ("Connect TMDb API key"). A maintained, open-source nyrx-operated proxy **could be added in a future release**.

> [!NOTE]
> TV/Movies server plugins must be configured manually. Use the command palette ("Configure Lua plugin source") to enter a manifest URL.

> [!WARNING] 
> **Rare server probe stall**
>
> A server probe from the sandbox can occasionally fail and the now-playing widget does not clean up or advance to the next queue item. This is rare and hard to reproduce. Workaround: press play on something on-screen to release the stuck widget, and the queue will move forward.

<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/g.png" alt="TV/Movies watchlist" width="800">
</p>

---

## Why Not Just Use a Browser?

| Browser                  | nyrx                        |
| ------------------------ | --------------------------- |
| 10 tabs, each eating RAM | One terminal window         |
| Ads, popups, tracking    | Clean, no ads, no telemetry |
| Juggling multiple sites  | Four sources, one queue     |

---

## Features

**Queue**

Source-agnostic queue with management features like batch deletion, and move-to-front reorder.
<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/h.jpg" alt="Playback queue modal" width="550">
</p>

**Downloads and Playback**

Background downloads show speed and ETA with a live animation. Existing files are skipped automatically. Both download and playback widgets live in the sidebar: Soundcloud shows a braille waveform, Youtube displays progress and metadata, Radio shows the station name and live ICY titles, and TV displays episode details. Queue indicators sit below. When both downloads and playback are active, navigation keys let you move between widgets and cancel as needed.
<p align="left">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/i.gif" alt="Now-playing widget and download progress" width="800">
</p>

**Thumbnails and Info**

Press z to view thumbnails, posters, and metadata. TV/Movies shows ratings, genres, and overview. Soundcloud shows track covers as such:

> [!NOTE] 
> **Konsole sixel rendering** 
>
> KDE Konsole has a sixel implementation issue that causes image rendering issues. nyrx switches to halfcell image rendering on Konsole as a workaround.

<p align="center">
  <img src="https://raw.githubusercontent.com/pyrolyticoncrete/nyrx/main/screenshots/j.jpg" alt="Thumbnail modal" width="400">
</p>

**Command Palette**

Press Ctrl+p to open the command palette. Source-specific commands in a searchable list: configure Lua plugin source, toggle plugin auto-updates, set download directory, sync liked tracks, and more.

**Help**

Press ? at any time for context-sensitive keybindings. The help changes based on which screen you are on. Focus indicators are unambiguous and the keybind hints update as you navigate around.

**Watch Tracking**

Every playback session is logged locally (mpv handles that), what you watched, how long, and whether you finished. This data stays on your machine.

This tracking could potentially become a digital wellbeing feature, letting you generate a local report showing your weekly viewing and listening habits, from data already stored on your machine.

---

## Tech Stack

- **Python 3.12+** with a mixin-based architecture
- **Textual** for the terminal UI framework
- **mpv** for media playback, controlled via Unix socket IPC
- **yt-dlp** for Youtube and Soundcloud stream extraction
- **ffmpeg** bundled via static-ffmpeg for downloads and muxing
- **Playwright** with headless Chromium for TV/Movies server probing
- **Lua** (via lupa) for the TV/Movies sandbox and mpv tracker script
- **SQLite** for watch history, Soundcloud data, and TMDb cache
- **TMDb** for movie and TV show metadata
- **radio-browser.info** for the global radio station index
- **pyfiglet** for the ASCII banner on the home screen

The app uses a custom dark color scheme with a lavender primary. The palette is designed for extended use in a terminal without eye strain.

---

## Known Quirks

These are not bugs that block anything, just things worth knowing about.

**Notification persistence.** Sometimes a notification toast stays on screen past its timeout if the full screen has not refreshed. Workaround: open any modal or press Tab to force a screen refresh. Hopefully this will be fixed as Textual gets updated.

**Navigation keys not responding.** If none of the focus indicators are lit up and navigation keys stop responding (this can happen after canceling a now-playing or download widget), press Tab to refocus the active element.

---

## Server Plugins (Optional)

TV/Movies works through server plugins: small Lua scripts that hunt down streaming sources. nyrx does not bundle these. They are third-party, community-maintained code that lives in a separate repo under its own MIT license, and you configure a manifest URL yourself.

> [!WARNING] 
> **Read this before adding a plugin source:**
>
> Server plugins run third-party Lua code on your behalf, inside a sandbox that blocks filesystem and process access. The sandbox limits but does not eliminate risk: a plugin can still make network requests and use system CPU. Only configure manifests from sources you trust, and skim the Lua source before use. nyrx does not review or endorse any community plugin.
 
- Read the documentation in [manifestinglua](https://github.com/pyrolyticoncrete/manifestinglua)
- Full API and examples: [HOW-TO-WRITE-SERVERS.md](https://github.com/pyrolyticoncrete/manifestinglua/blob/main/HOW-TO-WRITE-SERVERS.md)

---

## License

nyrx is licensed under the [GNU Affero General Public License v3.0](https://github.com/pyrolyticoncrete/nyrx/blob/main/LICENSE.md).

This is free software, and you are welcome to modify and distribute it under the terms of the AGPLv3.

---

## Privacy

nyrx does not require accounts and does not collect telemetry. nyrx itself runs no first-party server, but TV/Movies metadata is fetched through a third-party TMDb proxy, so your search and browsing queries are sent to that proxy rather than to nyrx. All your data, watch history, bookmarks, and search history stay on your machine. Network requests happen only when you search, play, stream content, or have caching jobs queued. TV/Movies server plugins are fetched only from a URL you explicitly configure. For full details, see the [Privacy Policy](https://github.com/pyrolyticoncrete/nyrx/blob/main/PRIVACY.md).

---

## Legal

nyrx does not host, upload, store, or distribute any media files or copyrighted content. It aggregates URLs to content already hosted on third-party public websites. TV/Movies server plugins must be configured manually by the user. You are solely responsible for complying with all applicable laws and terms of service. For full details, see the [Legal Notice](https://github.com/pyrolyticoncrete/nyrx/blob/main/LEGAL.md).
