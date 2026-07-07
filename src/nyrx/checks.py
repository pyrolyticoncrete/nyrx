# SPDX-License-Identifier: AGPL-3.0-only

"""Pre-flight dependency checks: runs before Textual app launches.

Verifies that ``mpv``, ``ffmpeg``, and the Playwright Chromium browser
binary are all present.  Chromium is auto-installed via
``playwright install chromium`` if missing; ``mpv`` must be on the
system ``PATH`` and ``ffmpeg`` is bundled via the ``static-ffmpeg``
package.
"""

from __future__ import annotations

import platform
import shutil
import subprocess
import sys
from pathlib import Path

from rich.console import Console

console = Console()

_PAD = "  "
_RULE = "\u2500" * 66


def run() -> None:
    """Run all pre-flight checks. Exits on fatal errors."""
    _check_windows()
    console.print(f"\n{_PAD}  nyrx")
    console.print(f"{_PAD}{_RULE}")
    _check_mpv()
    _ensure_ffmpeg()
    _ensure_chromium()
    _prompt_manifest_url()
    console.print(f"\n{_PAD}[green]All dependencies ready.[/]\n")


# ---------------------------------------------------------------------------
# Windows block
# ---------------------------------------------------------------------------


def _check_windows() -> None:
    if platform.system() != "Windows":
        return
    console.print(f"{_PAD}[red]nyrx uses mpv with Unix sockets.[/]")
    console.print(f"{_PAD}Windows is not supported. Please use Linux or macOS.")
    sys.exit(1)


# ---------------------------------------------------------------------------
# mpv
# ---------------------------------------------------------------------------


def _check_mpv() -> None:
    console.print(f"\n{_PAD}Checking dependencies..\n")
    if shutil.which("mpv"):
        console.print(f"{_PAD}  [green]\u2713[/] mpv")
        return
    console.print(f"{_PAD}  [red]\u00d7[/] mpv not found\n")
    console.print(f"{_PAD}nyrx requires mpv to play media.\n")
    console.print(f"{_PAD}  Linux:   sudo apt install mpv   (Debian/Ubuntu)")
    console.print(f"{_PAD}           sudo dnf install mpv   (Fedora)")
    console.print(f"{_PAD}           sudo pacman -S mpv     (Arch)")
    console.print(f"\n{_PAD}  macOS:   brew install mpv\n")
    sys.exit(1)


# ---------------------------------------------------------------------------
# ffmpeg (from static-ffmpeg or system)
# ---------------------------------------------------------------------------


def _ensure_ffmpeg() -> None:
    from nyrx.config import FFMPEG_BINARY

    if not FFMPEG_BINARY or not Path(FFMPEG_BINARY).exists():
        console.print(f"{_PAD}  [red]\u00d7[/] ffmpeg not found")
        console.print(f"\n{_PAD}    Run: pip install nyrx (includes ffmpeg)\n")
        sys.exit(1)
    try:
        result = subprocess.run(
            [FFMPEG_BINARY, "-version"],
            capture_output=True,
            timeout=5,
        )
    except Exception as exc:
        console.print(f"{_PAD}  [red]\u00d7[/] ffmpeg test failed: {exc}")
        sys.exit(1)
    if result.returncode == 0:
        source = "static" if "static_ffmpeg" in FFMPEG_BINARY else "system"
        console.print(f"{_PAD}  [green]\u2713[/] ffmpeg ({source})")
    else:
        console.print(f"{_PAD}  [red]\u00d7[/] ffmpeg binary found but failed test")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Playwright Chromium (browser binary: separate from pip package)
# ---------------------------------------------------------------------------


def _ensure_chromium() -> None:
    if _chromium_works():
        console.print(f"{_PAD}  [green]\u2713[/] Playwright Chromium")
        return
    _install_chromium()
    if _chromium_works():
        console.print(f"{_PAD}  [green]\u2713[/] Playwright Chromium (installed)")
    else:
        console.print(f"{_PAD}  [red]\u00d7[/] Chromium installed but test failed")
        sys.exit(1)


def _chromium_works() -> bool:
    """Try to launch and close Chromium. Returns True if it works."""
    try:
        from playwright.sync_api import sync_playwright

        pw = sync_playwright().start()
        browser = pw.chromium.launch(headless=True)
        browser.close()
        pw.stop()
        return True
    except Exception:
        return False


def _install_chromium() -> None:
    with console.status(
        "[dim]Installing Playwright Chromium (~150MB)...[/]",
        spinner="dots",
        spinner_style="dim",
    ):
        result = subprocess.run(
            [sys.executable, "-m", "playwright", "install", "chromium"],
            capture_output=True,
            timeout=300,
        )
    if result.returncode != 0:
        stderr = result.stderr.decode(errors="replace").strip()
        console.print(f"{_PAD}  [red]\u00d7[/] Chromium install failed: {stderr}")
        sys.exit(1)


# ---------------------------------------------------------------------------
# Manifest URL prompt (only if config field missing)
# ---------------------------------------------------------------------------


def _prompt_manifest_url() -> None:
    from nyrx.config import get_config, update_config

    cfg = get_config()
    if "hotswap_url" in cfg:
        return
    console.print()
    console.print(f"{_PAD}[bold]Would you like nyrx to fetch TV/Movies server[/bold]")
    console.print(f"{_PAD}[bold]configurations from a remote manifest?[/bold]\n")
    console.print(f"{_PAD}These are the probing scripts that find working")
    console.print(f"{_PAD}video sources. A manifest URL is required for")
    console.print(f"{_PAD}TV/Movies playback.\n")
    console.print(f"{_PAD}(You can configure this later via command palette)\n")

    while True:
        try:
            answer = input(
                f"{_PAD}Enter manifest URL (or press Enter to skip): "
            ).strip()
        except (EOFError, KeyboardInterrupt):
            answer = ""

        if not answer:
            update_config(hotswap_url="")
            console.print(
                f"{_PAD}  [dim]Skipped (TV/Movies playback won't be available)"
            )
            break

        if not answer.startswith(("http://", "https://")):
            console.print(
                f"{_PAD}  [yellow]URL must start with http:// or https://[/yellow]"
            )
            continue

        try:
            import requests

            with console.status(
                "[dim]Validating manifest URL...[/]",
                spinner="dots",
                spinner_style="dim",
            ):
                resp = requests.get(answer, timeout=15)
                resp.raise_for_status()
                data = resp.json()
            if isinstance(data.get("version"), int) and isinstance(
                data.get("files"), list
            ):
                update_config(hotswap_url=answer)
                console.print(f"{_PAD}  [green]\u2713[/] Manifest URL saved")
                break
            console.print(
                f"{_PAD}  [yellow]Invalid manifest: must have 'version' (int) "
                "and 'files' (list)[/yellow]"
            )
        except Exception as exc:
            console.print(f"{_PAD}  [yellow]Could not validate: {exc}[/yellow]")
