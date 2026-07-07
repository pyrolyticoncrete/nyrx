# SPDX-License-Identifier: AGPL-3.0-only

"""CLI entry point: launches the TUI with optional preflight flags."""

from __future__ import annotations

import argparse
import json
import sys
import urllib.request
from importlib.metadata import version as pkg_version


def _check_update() -> None:
    """Check PyPI for a newer version and print the result."""
    from nyrx.config import APP_VERSION

    try:
        req = urllib.request.Request(
            "https://pypi.org/pypi/nyrx/json",
            headers={"Accept": "application/json"},
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read())
        latest = data["info"]["version"]
    except Exception:
        print("Unable to check for updates.")
        return

    if latest == APP_VERSION:
        print(f"nyrx is up to date ({APP_VERSION})")
    else:
        print(f"Update available: {latest} (installed: {APP_VERSION})")
        print("Run: pip install --upgrade nyrx")


def main() -> None:
    parser = argparse.ArgumentParser(description="nyrx TUI.")
    parser.add_argument(
        "--version",
        action="version",
        version=f"nyrx {pkg_version('nyrx')}",
    )
    parser.add_argument(
        "--debug",
        action="store_true",
        help="Enable verbose debug logging across all modules",
    )
    parser.add_argument(
        "--profile",
        action="store_true",
        help="Enable latency instrumentation logging",
    )
    parser.add_argument(
        "--update",
        action="store_true",
        help="Check for a newer version on PyPI",
    )
    args = parser.parse_args()

    if args.update:
        _check_update()
        sys.exit(0)

    from nyrx import patches  # noqa: F401, applies third-party library patches
    from nyrx.app import main as tui_main
    from nyrx.checks import run as preflight

    preflight()
    tui_main(debug=args.debug, profile=args.profile)


if __name__ == "__main__":
    main()
