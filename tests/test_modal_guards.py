# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for the modal worker dismissal guards (P2 item 42).

When a modal is dismissed (e.g. via esc) while a ``@work(thread=True)``
resolution is still in flight, the completion callback runs against the
popped screen.  Calling ``dismiss()`` there would pop whatever screen is now
on top (or raise ``ScreenStackError``), so the callbacks must bail out when
the screen is no longer the active one.

Only the two callbacks that call ``dismiss()`` need the guard:
``SearchModal._done`` and ``TMDbKeyInputModal._on_validation_result``.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock

from nyrx.screens.search import SearchModal
from nyrx.screens.tmdb_key import TMDbKeyInputModal


class TestSearchModalDone:
    """``_done`` must not notify/dismiss when the screen was already popped."""

    def _stub(self, screen_stack_top):
        app = SimpleNamespace(screen_stack=[screen_stack_top], notify=MagicMock())
        return SimpleNamespace(
            app=app,
            log=MagicMock(),
            dismiss=MagicMock(),
        )

    def test_guard_fires_when_screen_not_on_top(self):
        other = object()
        stub = self._stub(other)
        SearchModal._done(stub, {"yt_id": "x"})
        stub.dismiss.assert_not_called()
        stub.app.notify.assert_not_called()

    def test_screen_on_top_delivers_data(self):
        stub = self._stub(None)
        stub.app.screen_stack = [stub]
        SearchModal._done(stub, {"yt_id": "x"})
        stub.dismiss.assert_called_once_with({"yt_id": "x"})
        stub.app.notify.assert_not_called()

    def test_screen_on_top_empty_data_notifies_and_dismisses(self):
        stub = self._stub(None)
        stub.app.screen_stack = [stub]
        SearchModal._done(stub, None)
        stub.dismiss.assert_called_once_with(None)
        stub.app.notify.assert_called_once()


class TestTMDbKeyValidationResult:
    """``_on_validation_result`` must not dismiss when the modal is gone."""

    def _stub(self, screen_stack_top):
        return SimpleNamespace(
            app=SimpleNamespace(screen_stack=[screen_stack_top]),
            _stop_spinner=MagicMock(),
            _show_status=MagicMock(),
            query_one=MagicMock(),
            dismiss=MagicMock(),
        )

    def test_guard_fires_when_screen_not_on_top(self):
        other = object()
        stub = self._stub(other)
        TMDbKeyInputModal._on_validation_result(stub, "k" * 32, True)
        stub._stop_spinner.assert_not_called()
        stub.dismiss.assert_not_called()
        stub._show_status.assert_not_called()

    def test_valid_key_dismisses_with_key(self):
        stub = self._stub(None)
        stub.app.screen_stack = [stub]
        key = "k" * 32
        TMDbKeyInputModal._on_validation_result(stub, key, True)
        stub._stop_spinner.assert_called_once_with()
        stub._show_status.assert_called_once_with("Connected")
        stub.dismiss.assert_called_once_with(key)

    def test_invalid_key_shows_error_and_keeps_open(self):
        stub = self._stub(None)
        stub.app.screen_stack = [stub]
        TMDbKeyInputModal._on_validation_result(stub, "k" * 32, False)
        stub._stop_spinner.assert_called_once_with()
        stub._show_status.assert_called_once_with("Invalid API key", is_error=True)
        stub.dismiss.assert_not_called()
        inp = stub.query_one("#tmdb-input", MagicMock)
        inp.focus.assert_called_once()
