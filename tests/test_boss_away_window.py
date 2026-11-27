"""The character window's Bosses tab shows and saves the boss choices, greys out the leaving boxes
while bosses aren't kept away from (and the wait's while the map isn't left), shows what the chat
gets for the player's own line, and every shortcut letter on it is used once. They were a group at
the bottom of Advanced, Fighting, where players didn't think of looking."""

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


def click(box: Any, value: bool) -> None:
    """Tick or untick a box as the player does: its value, then its event."""
    box.SetValue(value)
    evt = wx.CommandEvent(wx.wxEVT_CHECKBOX, box.GetId())
    evt.SetEventObject(box)
    evt.SetInt(int(value))
    box.GetEventHandler().ProcessEvent(evt)


def dialog(frame: Any, **values: Any) -> tuple[Any, dict[str, Any]]:
    from unclefarmer.gui import profile as gui
    p = settings.load_profile("Hero")
    p.update(values)
    settings.save_profile("Hero", p)
    d = gui.ProfileDialog(frame, "Hero")
    d.EndModal = returning(None)                # type: ignore[method-assign]
    return d, d.p


def page_labels(d: Any, title: str) -> list[str]:
    nb = next(c for c in d.GetChildren() if isinstance(c, wx.Notebook))
    return labelled(next(nb.GetPage(i) for i in range(nb.GetPageCount()) if nb.GetPageText(i) == title))


def letters(texts: list[str]) -> list[str]:
    out: list[str] = []
    for text in texts:
        at = text.replace("&&", "").find("&")
        if at >= 0:
            out.append(text.replace("&&", "")[at + 1].lower())
    return out


def test_the_bosses_group_shows_and_saves_the_choices(frame: Any) -> None:
    d, p = dialog(frame, boss_distance=50, boss_leave=True, boss_walk_offs=1, boss_away_first=90,
                  boss_away_most=600, boss_away_farm=False, boss_notice_rooms=[3])
    try:
        rooms = dict(d.boss_rooms)
        assert list(rooms) == [4, 3, 5, 0]
        assert [r for r, box in d.boss_rooms if box.GetValue()] == [3]
        rooms[0].SetValue(True)
        rooms[4].SetValue(True)
        assert d.boss_on.GetValue() and d.boss_keep.GetValue() == 50
        assert d.boss_leave.GetValue() and d.boss_walk_offs.GetValue() == 1
        assert d.boss_away_first.GetValue() == 90 and d.boss_away_most.GetValue() == 600
        assert not d.boss_away_farm.GetValue()
        d.boss_walk_offs.SetValue(0)
        d.boss_away_first.SetValue(120)
        d.boss_away_most.SetValue(60)           # under the first: saved as the first
        d.boss_away_farm.SetValue(True)
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    assert (p["boss_walk_offs"], p["boss_away_first"], p["boss_away_most"], p["boss_away_farm"]) == (0, 120, 120, True)
    assert p["boss_leave"] is True and p["boss_distance"] == 50
    assert p["boss_notice_rooms"] == [4, 3, 0]


def test_new_profiles_keep_the_old_behaviour_and_farm_the_wait() -> None:
    d = settings.PROFILE_DEFAULTS
    assert (d["boss_leave"], d["boss_walk_offs"], d["boss_away_first"], d["boss_away_most"]) == (True, 3, 60, 300)
    assert d["boss_away_farm"] is True and d["boss_notice_rooms"] == []


def test_the_leaving_boxes_follow_the_ticks(frame: Any) -> None:
    d, p = dialog(frame, boss_distance=40, boss_leave=False)
    try:
        assert d.boss_leave.IsEnabled()
        assert not d.boss_walk_offs.IsEnabled() and not d.boss_away_first.IsEnabled()
        assert not d.boss_away_most.IsEnabled() and not d.boss_away_farm.IsEnabled()
        click(d.boss_leave, True)
        assert d.boss_walk_offs.IsEnabled() and d.boss_away_first.IsEnabled()
        assert d.boss_away_most.IsEnabled() and d.boss_away_farm.IsEnabled()
        click(d.boss_on, False)
        assert not d.boss_keep.IsEnabled() and not d.boss_leave.IsEnabled()
        assert not d.boss_walk_offs.IsEnabled() and not d.boss_away_farm.IsEnabled()
        click(d.boss_on, True)
        assert d.boss_keep.IsEnabled() and d.boss_leave.IsEnabled() and d.boss_walk_offs.IsEnabled()
        click(d.boss_on, False)
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    assert p["boss_distance"] == 0 and p["boss_leave"] is True


def labelled(window: Any) -> list[str]:
    out: list[str] = []
    for c in window.GetChildren():
        if isinstance(c, (wx.CheckBox, wx.StaticText, wx.Button)):
            out.append(c.GetLabel())
        out.extend(labelled(c))
    return out


def test_every_shortcut_letter_of_the_bosses_tab_is_used_once(frame: Any) -> None:
    d, _p = dialog(frame)
    try:
        got = letters(page_labels(d, "Bosses"))
        assert len(got) == len(set(got)), sorted(got)
        assert {"b", "l", "w", "h", "m", "i", "t", "g", "n", "o", "y"} <= set(got)    # every box and field has one
    finally:
        d.Destroy()


def test_advanced_fighting_keeps_no_boss_box_and_its_letters_stay_unique(frame: Any) -> None:
    from unclefarmer.gui import profile as gui
    d = gui.AdvancedDialog(frame, "Hero", settings.load_profile("Hero"))
    try:
        texts = page_labels(d, "Fighting")
        got = letters(texts)
        assert len(got) == len(set(got)), sorted(got)
        assert not any("boss" in t.lower() or "demon" in t.lower() for t in texts)
    finally:
        d.Destroy()


def test_the_player_s_own_line_is_shown_as_the_chat_gets_it_and_saved(frame: Any) -> None:
    d, p = dialog(frame)
    try:
        assert d.boss_example.GetValue() == "Death Worm (a boss) on Wolf Prairie at 105, 100"   # empty: the bot's line
        d.boss_text.SetValue("  %BOSS% is up on %map% (%pos%)\n come help ")
        assert d.boss_example.GetValue() == "Death Worm is up on Wolf Prairie (105, 100) come help"
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    assert p["boss_notice_text"] == "%BOSS% is up on %map% (%pos%) come help"
    assert settings.load_profile("Hero")["boss_notice_text"] == p["boss_notice_text"]
