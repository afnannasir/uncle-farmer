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
        assert set(d.trigger_editors) == set(triggers.STAGES)
        before, after = d.trigger_editors["before"], d.trigger_editors["after"]
        assert [row["action"] for row in after.rows if row["enabled"]] == ["sell_shards", "clean_bag"]
        for row in before.rows:
            row["enabled"] = row["action"] in ("sell_shards", "store_items")
        for row in after.rows:
            row["enabled"] = row["action"] in ("sell_shards", "store_items")
        before.rows[0].update(delay=600, deterministic=True)
        assert after.rows[0]["enabled"] and not after.rows[0]["deterministic"]
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    saved = settings.load_profile("Hero")
    assert triggers.ticked(saved, "before") == ["sell_shards", "store_items"]
    assert triggers.ticked(saved, "after") == ["sell_shards", "store_items"]
    assert triggers.sequence(saved, "before")["steps"][0]["delay"] == 600
    assert triggers.sequence(saved, "before")["steps"][0]["deterministic"] is True


def test_travel_and_pets_keeps_only_travel_and_pets(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    d = ProfileDialog(frame, "Hero")
    try:
        travel = labels(tab(d, "Travel and pets"))
        assert not any("farming" in t.lower() or "Tools," in t for t in travel)
        assert "End the run with the hearthstone" in travel
        assert all(editor.list.GetCount() == len(triggers.JOBS) for editor in d.trigger_editors.values())
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


def test_experience_cards_are_one_tick_per_kind(frame: Any) -> None:
    """One tick per kind, weakest first, each with what the last scan saw in the bag. A ticked kind
    that ran out stays ticked."""
    from unclefarmer.gui.profile import ProfileDialog
    p = settings.load_profile("Hero")
    p["scan"] = {"bag": [{"name": "Red Potion", "count": 9},
                         {"name": "3x experience acceleration card", "count": 2},
                         {"name": "1.3x experience acceleration card", "count": 41}]}
    p["xp_cards"] = [3.0]
    settings.save_profile("Hero", p)
    d = ProfileDialog(frame, "Hero")
    try:
        assert [k for k, _box in d.xp_cards] == [1.3, 1.5, 1.75, 2.0, 2.5, 3.0]
        labels = {k: box.GetLabel() for k, box in d.xp_cards}
        assert labels[1.3] == "1.3x experience acceleration card (41 in the bag)"
        assert labels[2.0] == "2x experience acceleration card (none in the bag)"
        assert [k for k, box in d.xp_cards if box.GetValue()] == [3.0]
        dict(d.xp_cards)[1.3].SetValue(True)
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    assert settings.load_profile("Hero")["xp_cards"] == [1.3, 3.0]

    p = settings.load_profile("Hero")
    p["scan"] = {"bag": [{"name": "3x experience acceleration card", "count": 1}]}   # the 1.3x ran out
    settings.save_profile("Hero", p)
    d = ProfileDialog(frame, "Hero")
    try:
        assert dict(d.xp_cards)[1.3].GetValue()       # still ticked: the next one looted is used
        assert dict(d.xp_cards)[1.3].GetLabel() == "1.3x experience acceleration card (none in the bag)"
    finally:
        d.Destroy()


def test_the_old_experience_card_tick_names_no_card() -> None:
    p = settings.load_profile("Hero")
    p["xp_card"] = True                    # saved before 2026-11-12: "the strongest in the bag"
    settings.save_profile("Hero", p)
    got = settings.load_profile("Hero")
    assert "xp_card" not in got and got["xp_cards"] == []


def test_the_one_chosen_experience_card_becomes_its_tick() -> None:
    import json
    with open(settings.profile_path("Hero"), "w", encoding="utf-8") as f:   # saved before 2026-11-17: one kind only
        json.dump({"xp_card_name": "2x experience acceleration card"}, f)
    got = settings.load_profile("Hero")
    assert "xp_card_name" not in got and got["xp_cards"] == [2.0]
    got["xp_cards"] = []                                     # the player unticks it: it stays unticked
    settings.save_profile("Hero", got)
    assert settings.load_profile("Hero")["xp_cards"] == []
