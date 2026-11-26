"""Character settings, Fighting: the "Use only the skills of page 1" box shows and saves its tick, and
Reset the fighting settings puts it back to unticked."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from conftest import returning

from unclefarmer import settings

wx = pytest.importorskip("wx")


@pytest.fixture
def frame() -> Iterator[Any]:
    app = wx.GetApp() or wx.App(False)
    try:
        window = wx.Frame(None)
    except Exception as e:                      # no desktop to make windows on
        pytest.skip("no windows here: %s" % e)
    yield window
    window.Destroy()
    app.ProcessPendingEvents()


def test_the_page_one_box_shows_and_saves_its_tick(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    p = settings.load_profile("Hero")
    p["skill_page_one"] = False
    settings.save_profile("Hero", p)
    d = ProfileDialog(frame, "Hero")
    try:
        assert d.page_one.GetValue() is False
        assert d.page_one.GetLabel() == "Use only the skills of page &1 (never turn to skill page 2)"
        d.page_one.SetValue(True)
        d.EndModal = returning(None)            # type: ignore[method-assign]
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    assert settings.load_profile("Hero")["skill_page_one"] is True
    d = ProfileDialog(frame, "Hero")
    try:
        assert d.page_one.GetValue() is True
        d.p["skill_page_one"] = settings.PROFILE_DEFAULTS["skill_page_one"]   # as the reset leaves it
        d.show_fighting()
        assert d.page_one.GetValue() is False
    finally:
        d.Destroy()
