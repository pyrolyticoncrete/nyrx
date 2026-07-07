# SPDX-License-Identifier: AGPL-3.0-only

"""Tests for ``widgets/sc_home.py``: ``_update_trending_label`` and ``populate``."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import MagicMock, PropertyMock, patch

from nyrx.widgets.sc_home import SCHomeView


def _stub(**attrs) -> SimpleNamespace:
    return SimpleNamespace(**attrs)


def _make_label_stub(**app_attrs) -> tuple[SCHomeView, MagicMock]:
    """Build a bare SCHomeView with a stubbed ``query_one`` for the label."""
    label = MagicMock()
    app = SimpleNamespace(**app_attrs)
    w = object.__new__(SCHomeView)
    w.query_one = MagicMock(return_value=label)
    patcher = patch.object(
        SCHomeView, "app", new_callable=PropertyMock, return_value=app
    )
    patcher.start()
    return w, label


class TestUpdateTrendingLabel:
    """``_update_trending_label`` reflects client_id availability."""

    def test_unavailable_when_no_client_id(self):
        w, label = _make_label_stub(_trending_region="us")
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=False
        ):
            SCHomeView._update_trending_label(w)
        label.update.assert_called_once_with("TRENDING  [#b0b0b0](unavailable)[/]\n")

    def test_shows_region_when_available(self):
        w, label = _make_label_stub(_trending_region="de")
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SCHomeView._update_trending_label(w)
        label.update.assert_called_once_with("TRENDING  [#b0b0b0](de)[/]\n")

    def test_no_region_update_when_no_region(self):
        w, label = _make_label_stub(_trending_region="")
        with patch(
            "nyrx.sources.soundcloud.api.client_id_available", return_value=True
        ):
            SCHomeView._update_trending_label(w)
        label.update.assert_not_called()


class TestPopulate:
    """``populate`` refreshes the trending label on SC-home entry."""

    def test_calls_update_trending_label(self):
        w = object.__new__(SCHomeView)
        w._populate_recent = MagicMock()
        w._populate_following = MagicMock()
        w._populate_liked = MagicMock()
        w._update_sidebar_class = MagicMock()
        w._update_trending_label = MagicMock()
        SCHomeView.populate(w, ["q"], [], [])
        w._update_trending_label.assert_called_once_with()
