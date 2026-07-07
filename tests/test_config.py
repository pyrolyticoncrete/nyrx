# SPDX-License-Identifier: AGPL-3.0-only

"""Contract tests for config.py.

Only behavioral paths are tested: the env-var gated DEFAULT_DOWNLOAD_DIR
construction.  Pure declarations (dicts, sets, integer constants) are
skipped per the same reasoning as A03: they can't fail for a reason that
matters (wrong value → wrong behavior is visible immediately, and the
test's "expected" is copied from the source, not independently derived).

Sources of the construction pattern:
  - DEFAULT_DOWNLOAD_DIR: config.py:13-14
"""

from __future__ import annotations

import importlib
import os
from pathlib import Path

import pytest

from nyrx import config
from nyrx.config import DEFAULT_DOWNLOAD_DIR


class TestDefaultDownloadDir:
    def test_is_path_ending_in_media(self) -> None:
        assert isinstance(DEFAULT_DOWNLOAD_DIR, Path)
        assert DEFAULT_DOWNLOAD_DIR.name == "Media"

    def test_fallback_is_platformdirs_media(self) -> None:
        if os.getenv("NYRX_DOWNLOAD_DIR"):
            pytest.skip("NYRX_DOWNLOAD_DIR is set in this environment")
        from platformdirs import user_downloads_dir

        assert DEFAULT_DOWNLOAD_DIR == Path(user_downloads_dir()) / "Media"

    def test_env_var_override_uses_custom_path(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setenv("NYRX_DOWNLOAD_DIR", "/tmp/test_media_dir")
        importlib.reload(config)
        assert str(config.DEFAULT_DOWNLOAD_DIR) == "/tmp/test_media_dir"
