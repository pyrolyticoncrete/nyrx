# Credits & Attributions

nyrx stands on the work of many open source projects, services, and designers.
We are grateful to them and list what we adapted, what inspired us, and what
we depend on. nyrx itself is licensed under the GNU Affero General Public
License v3.0 (see LICENSE.md).

## Direct adaptations (ported code, attribution required)

### opencode (aura theme and Knight Rider scanner)

- Source: https://github.com/anomalyco/opencode (package @opencode-ai/tui)
- Author: opencode
- License: MIT

nyrx ports two pieces from opencode:

- The `aura` theme palette defined in `src/nyrx/app.py` is a direct port of
  opencode's `aura.json` theme (same hex values for primary, secondary,
  accent, error, warning, success, background, surface, and foreground).
- The download widget's Knight Rider scanner in
  `src/nyrx/widgets/download.py` is a Python port of opencode's Knight Rider
  animation in `tui/src/ui/spinner.ts`. It uses the same 8 cell width, the
  same start and end hold frame counts, 54 total frames, a 6 step trail, and
  the same exponential `0.65 ** (i - 1)` trail decay with a brightness bloom
  on the second step. The block glyphs are the same; only the colors differ

Because this is a substantial port of MIT licensed code, the MIT license and
copyright notice are preserved below.

MIT License

Copyright (c) 2025 opencode

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

## Inspiration (visual style, courtesy credits)

### btop (color band banner)

- Author: Aristocratos (jakob@qvantnet.com)
- License: Apache-2.0

nyrx's pyfiglet welcome banner in `src/nyrx/screens/home.py` colors each text
row with its own band (a per-row gradient, light lavender at the top fading to
deep indigo at the bottom). That approach is in the spirit of btop's gradient
meters. The per row color band technique is a generic terminal idiom, so this
is a courtesy credit rather than a license requirement.

### SoundCloud (waveform visualization)

Inspiration, not a port. nyrx's SoundCloud now-playing view draws the track's
waveform as braille bars in the terminal. Both nyrx and SoundCloud's own web
player read the same public data: SoundCloud's `waveform_url` JSON `samples`
array, fetched by `fetch_waveform`. The visual designs differ substantially:

- SoundCloud renders a mirrored canvas waveform: a solid upper shard
  plus a faded lower reflection about a 70% centerline, using a gamma sample
  transform and orange (played) versus gray (unplayed) coloring.
- nyrx renders a single-sided, bottom-anchored braille waveform (4 dot rows, 
  two samples per braille cell) with its own 2nd-98th percentile normalization
  and error-diffusion dithering (`src/nyrx/helpers.py`, `build_waveform`), 
  and a center-pinned 44 column playhead (played = white, unplayed = grey).

The look is inspired by SoundCloud's player UI. SoundCloud is a trademark of
SoundCloud Global Limited and Co. KG. nyrx is not affiliated with or endorsed by
SoundCloud. See the SoundCloud entry under Services below for the data and
client id details.

## Services & APIs

### TMDb

This product uses the TMDb API but is not endorsed or certified by TMDb.

### SoundCloud

- Waveform and track metadata are sourced from SoundCloud's API
  (`waveform_url` and `api-v2.soundcloud.com`).
- SoundCloud API access uses a `client_id` extracted from SoundCloud's public
  web JavaScript. This is the same technique used by yt-dlp and the wider
  SoundCloud reverse engineering community, since SoundCloud does not hand 
  out open public API keys.
- SoundCloud is a trademark of SoundCloud Global Limited and Co. KG. nyrx is
  not affiliated with or endorsed by SoundCloud.

### radio-browser.info

The radio station index is powered by the radio-browser.info community API.

### YouTube

Video and music extraction is performed through yt-dlp. YouTube is a trademark
of Google LLC. nyrx is not affiliated with YouTube.

## Third party dependencies

nyrx is built on these open source Python packages (licenses listed as
commonly published; confirm exact SPDX identifiers in each project):

| Project         | License                     |
|-----------------|-----------------------------|
| textual         | MIT                         |
| lupa            | MIT                         |
| yt-dlp          | Unlicense                   |
| static-ffmpeg   | MIT                         |
| playwright       | Apache-2.0                  |
| pyfiglet        | MIT                         |
| pycryptodomex    | BSD-2-Clause / public domain (pycryptodome)    |
| requests        | Apache-2.0                  |
| rich            | MIT                         |
| pillow          | MIT (PIL Software License)           |
| platformdirs    | MIT                         |

Bundled with the above: ffmpeg (shipped via static-ffmpeg) and Chromium
(shipped via Playwright) are used at runtime.

Thank you to everyone who builds and maintains these projects.
