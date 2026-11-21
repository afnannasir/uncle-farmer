"""Help, About shows this copy's own version: a dev build's number, as the update check does."""

from __future__ import annotations

import pytest

pytest.importorskip("wx")

from unclefarmer import __version__, updater  # noqa: E402
from unclefarmer.gui import main  # noqa: E402
from conftest import returning  # noqa: E402


def test_about_names_the_dev_build(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "installed_version", returning(__version__ + "-dev.436"))
    assert main.about_text().startswith("Uncle Farmer %s-dev.436\n" % __version__)


def test_about_names_a_release(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updater, "installed_version", returning(__version__))
    assert main.about_text().splitlines()[0] == "Uncle Farmer " + __version__
