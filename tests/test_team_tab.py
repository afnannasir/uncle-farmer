"""The character window's Team tab: every leader's choice off by default, saved as typed, refused
when a ticked choice lacks what it needs; teammates' healing moved here from the pets' block."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

wx = pytest.importorskip("wx")

from unclefarmer import settings  # noqa: E402
from unclefarmer.gui.teamtab import names_list, names_text  # noqa: E402


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


def page_texts(dlg: Any, title: str) -> list[str]:
    nb = next(c for c in dlg.GetChildren() if isinstance(c, wx.Notebook))
    page = next(nb.GetPage(i) for i in range(nb.GetPageCount()) if nb.GetPageText(i) == title)
    out: list[str] = []

    def walk(w: Any) -> None:
        for c in w.GetChildren():
            if isinstance(c, (wx.CheckBox, wx.StaticText, wx.StaticBox)):
                out.append(c.GetLabel())
            walk(c)
    walk(page)
    return out


def test_names_and_ids_round_trip() -> None:
    assert names_list(" Bob, 1234; Alice ,, ") == ["Bob", 1234, "Alice"]
    assert names_text(["Bob", 1234]) == "Bob, 1234"
    assert names_text(None) == ""


def test_every_leader_choice_is_off_by_default() -> None:
    p = settings.load_profile("Hero")
    for key in ("team_accept", "team_remove_offline", "team_offline_town_only", "team_hand_over", "team_create",
                "team_remake", "team_leave_others", "team_stay_in_town"):
        assert p[key] is False, key
    assert p["team_accept_who"] == "trusted" and p["team_name"] == ""


def test_team_hunting_settings_are_opt_in_and_saved(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    start = settings.load_profile("Hero")
    assert start["team_hunt"] is False and start["team_hunt_subscribe"] is False
    window = ProfileDialog(frame, "Hero")
    try:
        ui = window.team_ui
        ui["hunt"].SetValue(True)
        ui["hunt_threshold"].SetValue(7)
        ui["hunt_message"].SetValue("Bring the team here")
        ui["hunt_return"].SetValue(True)
        ui["hunt_subscribe"].SetValue(True)
        window.on_ok(wx.CommandEvent())
    finally:
        window.Destroy()
    saved = settings.load_profile("Hero")
    assert saved["team_hunt"] is True and saved["team_hunt_subscribe"] is True
    assert saved["team_hunt_threshold"] == 7 and saved["team_hunt_return"] is True
    assert saved["team_hunt_message"] == "Bring the team here"


def test_hunting_requires_a_configured_encounter_message(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    from unclefarmer.gui.teamtab import save_team
    window = ProfileDialog(frame, "Hero")
    try:
        window.team_ui["hunt"].SetValue(True)
        window.team_ui["hunt_message"].SetValue(" ")
        profile: dict[str, Any] = {"team_hunt": False}
        with pytest.raises(ValueError, match="encounter message"):
            save_team(profile, window.team_ui)
        assert profile == {"team_hunt": False}
    finally:
        window.Destroy()


def test_the_tab_comes_after_travel_with_its_two_groups(frame: Any) -> None:
    from unclefarmer.gui.profile import AdvancedDialog, ProfileDialog
    d = ProfileDialog(frame, "Hero")
    try:
        nb: Any = next(c for c in d.GetChildren() if isinstance(c, wx.Notebook))
        titles: list[str] = [nb.GetPageText(i) for i in range(nb.GetPageCount())]
        assert titles.index("Team") == titles.index("Travel and pets") + 1
        team = page_texts(d, "Team")
        assert "Leading a team" in team and "Following a leader" in team
        shortcuts = [t[t.index("&") + 1].lower() for t in team if "&" in t]
        assert len(shortcuts) == len(set(shortcuts))          # one key per choice on the page
        assert not any("teammates" in t for t in page_texts(d, "Travel and pets"))
    finally:
        d.Destroy()
    a = AdvancedDialog(frame, "Hero", settings.load_profile("Hero"))
    try:
        assert not any("teammates" in t for t in page_texts(a, "Pets"))
    finally:
        a.Destroy()


def test_the_tab_saves_what_was_typed(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    d = ProfileDialog(frame, "Hero")
    try:
        ui = d.team_ui
        ui["accept"].SetValue(True)
        ui["who"].SetSelection(2)
        ui["accept_list"].ChangeValue("Bob, 1234")
        ui["remove_offline"].SetValue(True)
        ui["offline_every"].SetValue(5)
        ui["hand_over"].SetValue(True)
        ui["hand_to"].ChangeValue("Alice")
        ui["create"].SetValue(True)
        ui["name"].ChangeValue("Farmers1")
        ui["heal"].SetValue(False)
        ui["heal_below"].SetValue(55)
        d.on_ok(wx.CommandEvent())
    finally:
        d.Destroy()
    p = settings.load_profile("Hero")
    assert p["team_accept"] and p["team_accept_who"] == "list" and p["team_accept_list"] == ["Bob", 1234]
    assert p["team_remove_offline"] and p["team_offline_every"] == 5
    assert p["team_hand_over"] and p["team_hand_to"] == ["Alice"]
    assert p["team_create"] and p["team_name"] == "Farmers1"
    assert p["team_heal"] is False and p["team_heal_below"] == 55


@pytest.mark.parametrize("change", [
    {"create": True, "name": "two words"},          # the game takes letters and numbers only
    {"remake": True, "name": ""},
    {"accept": True, "who": 2, "accept_list": ""},   # nobody to let in
    {"hand_over": True, "hand_to": " , "},           # nobody to hand it to
])
def test_a_ticked_choice_missing_what_it_needs_is_not_saved(frame: Any, change: dict[str, Any]) -> None:
    from unclefarmer.gui import profile
    from unclefarmer.gui.teamtab import save_team
    d = profile.ProfileDialog(frame, "Hero")
    try:
        ui: Any = d.team_ui
        for key, value in change.items():
            if key == "who":
                ui["who"].SetSelection(value)
            elif isinstance(value, bool):
                ui[key].SetValue(value)
            else:
                ui[key].ChangeValue(value)
        p: dict[str, Any] = {"team_create": False}
        with pytest.raises(ValueError):
            save_team(p, ui)
        assert p == {"team_create": False}           # nothing changed
    finally:
        d.Destroy()
