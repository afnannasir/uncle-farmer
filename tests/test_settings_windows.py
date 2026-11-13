"""The Character settings and Settings windows, built for real (skipped where wx can't make a window):
the Triggers tab saves its ten ticks independently and Travel and pets no longer holds any, the
Updater tab holds every update setting, and every tab scrolls instead of being cut off."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

wx = pytest.importorskip("wx")

from unclefarmer import settings  # noqa: E402
from unclefarmer.engine import triggers  # noqa: E402
from unclefarmer.gui import common  # noqa: E402


@pytest.fixture(scope="module")
def frame() -> Iterator[Any]:
    app = wx.App.Get() or wx.App(False)
    try:
        top = wx.Frame(None)
    except Exception as e:                      # no desktop to make windows on
        pytest.skip("no windows here: %s" % e)
    yield top
    top.Destroy()
    del app


def tabs_of(dlg: Any) -> Any:
    return next(c for c in dlg.GetChildren() if isinstance(c, wx.Notebook))


def tab(dlg: Any, title: str) -> Any:
    nb = tabs_of(dlg)
    return next(nb.GetPage(i) for i in range(nb.GetPageCount()) if nb.GetPageText(i) == title)


def checkboxes(window: Any) -> list[Any]:
    out: list[Any] = []
    for c in window.GetChildren():
        if isinstance(c, wx.CheckBox):
            out.append(c)
        out.extend(checkboxes(c))
    return out


def labels(window: Any) -> list[str]:
    return [c.GetLabel().replace("&", "") for c in checkboxes(window)]


def test_triggers_tab_saves_both_stages_independently(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    p = settings.load_profile("Hero")
    p.update(sell_shards_after_farming=True, clean_bag_after_farming=True)     # ticked on the old tab
    settings.save_profile("Hero", p)
    d = ProfileDialog(frame, "Hero")
    try:
        assert set(d.triggers) == set(triggers.SETTINGS)
        assert [k for k, box in d.triggers.items() if box.GetValue()] == ["sell_shards_after_farming",
                                                                        "clean_bag_after_farming"]
        d.triggers["sell_shards_before_farming"].SetValue(True)      # the same job before farming too
        d.triggers["store_items_before_farming"].SetValue(True)
        d.triggers["store_items_after_farming"].SetValue(True)
        d.triggers["clean_bag_after_farming"].SetValue(False)
        assert d.triggers["sell_shards_after_farming"].GetValue() is True     # nothing unticked the other
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    saved = settings.load_profile("Hero")
    assert triggers.ticked(saved, "before") == ["sell_shards", "store_items"]
    assert triggers.ticked(saved, "after") == ["sell_shards", "store_items"]


def test_travel_and_pets_keeps_only_travel_and_pets(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    d = ProfileDialog(frame, "Hero")
    try:
        travel = labels(tab(d, "Travel and pets"))
        assert not any("farming" in t.lower() or "Tools," in t for t in travel)
        assert "End the run with the hearthstone" in travel
        assert len(labels(tab(d, "Triggers"))) == 2 * len(triggers.JOBS)
    finally:
        d.Destroy()


def test_updater_tab_holds_every_update_setting(frame: Any) -> None:
    from unclefarmer import updater
    from unclefarmer.gui.setup import SetupDialog
    s = dict(settings.load_app(), check_updates=False, update_channel="dev", skip_version="9.9.9")
    d = SetupDialog(frame, s)
    try:
        nb = tabs_of(d)
        assert nb.GetPageText(nb.GetPageCount() - 1) == "Updater"
        speech = labels(tab(d, "Speech and language"))
        assert not any("version" in t.lower() for t in speech)       # moved, not copied
        if updater.REPO:
            assert d.updates is not None and d.channel is not None and d.unskip is not None
            assert d.updates.GetValue() is False and d.channel.GetSelection() == updater.CHANNELS.index("dev")
            d.updates.SetValue(True)
            d.channel.SetSelection(0)
            d.unskip.SetValue(True)
            d.apply()
            assert s["check_updates"] is True and s["update_channel"] == "master" and s["skip_version"] == ""
    finally:
        d.Destroy()


def test_every_tab_scrolls_and_windows_fit_the_screen(frame: Any) -> None:
    from unclefarmer.gui.profile import AdvancedDialog, ProfileDialog
    from unclefarmer.gui.setup import SetupDialog
    for d in (ProfileDialog(frame, "Hero"), AdvancedDialog(frame, "Hero", settings.load_profile("Hero")),
              SetupDialog(frame, dict(settings.load_app()))):
        try:
            area = common.screen_area(d)
            size = d.GetSize()
            assert size.width <= area.width and size.height <= area.height
            nb = tabs_of(d)
            for i in range(nb.GetPageCount()):
                assert isinstance(nb.GetPage(i), common.Page), nb.GetPageText(i)
        finally:
            d.Destroy()


def test_experience_card_choices_are_the_cards_in_the_bag(frame: Any) -> None:
    """Only the cards the last scan saw in the bag can be chosen, weakest first, and None: the bot
    uses the chosen kind only, never the strongest in the bag."""
    from unclefarmer.gui.profile import ProfileDialog
    p = settings.load_profile("Hero")
    p["scan"] = {"bag": [{"name": "Red Potion", "count": 9},
                         {"name": "3x experience acceleration card", "count": 2},
                         {"name": "1.3x experience acceleration card", "count": 41}]}
    p["xp_card_name"] = "3x experience acceleration card"
    settings.save_profile("Hero", p)
    d = ProfileDialog(frame, "Hero")
    try:
        assert d.xp_card.GetStrings() == ["None", "1.3x experience acceleration card (41 in the bag)",
                                        "3x experience acceleration card (2 in the bag)"]
        assert d.xp_card.GetSelection() == 2
        d.xp_card.SetSelection(1)
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    assert settings.load_profile("Hero")["xp_card_name"] == "1.3x experience acceleration card"

    p = settings.load_profile("Hero")
    p["scan"] = {"bag": [{"name": "3x experience acceleration card", "count": 1}]}   # the 1.3x ran out
    settings.save_profile("Hero", p)
    d = ProfileDialog(frame, "Hero")
    try:
        assert d.xp_card.GetStrings() == ["None", "3x experience acceleration card (1 in the bag)"]
        assert d.xp_card.GetSelection() == 0          # not in the bag: no choice of it
    finally:
        d.Destroy()


def test_the_old_experience_card_tick_names_no_card() -> None:
    p = settings.load_profile("Hero")
    p["xp_card"] = True                    # saved before 2026-11-12: "the strongest in the bag"
    settings.save_profile("Hero", p)
    got = settings.load_profile("Hero")
    assert "xp_card" not in got and got["xp_card_name"] == ""
