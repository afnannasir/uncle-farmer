"""Copying a part of one character's settings to another from the character window: its Before or
After farming list, its fighting settings, its team settings."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from conftest import returning

from unclefarmer import settings
from unclefarmer.engine import profilecopy, triggers


def listed(p: dict[str, Any], stage: str) -> list[tuple[str, bool]]:
    return [(s["action"], s["enabled"]) for s in triggers.sequence(p, stage)["steps"]]


def ticked(*actions: str) -> dict[str, Any]:
    return {"steps": [triggers.step(a) for a in actions]}


def test_adding_a_list_ticks_their_steps_here_and_keeps_ours() -> None:
    mine: dict[str, Any] = {"trigger_sequences": {"before": ticked("restock")}}
    theirs: dict[str, Any] = {"trigger_sequences": {"before": ticked("sell_shards", "restock")}}
    out, added, same = profilecopy.steps(mine, theirs, "before", replace=False)
    assert [(s["action"], s["enabled"]) for s in out] == [("restock", True), ("sell_shards", True)]
    assert (added, same) == (1, 1)


def test_a_list_never_edited_gets_their_ticked_steps_ticked_in_place() -> None:
    theirs: dict[str, Any] = {"trigger_sequences": {"after": ticked("sell_shards")}}
    out, added, same = profilecopy.steps({}, theirs, "after", replace=False)   # every job there, unticked
    assert [s["action"] for s in out if s["enabled"]] == ["sell_shards"] and (added, same) == (1, 0)
    assert len(out) == len(listed({}, "after"))                               # none twice


def test_replacing_a_list_makes_it_theirs() -> None:
    mine: dict[str, Any] = {"trigger_sequences": {"before": ticked("restock")}}
    theirs: dict[str, Any] = {"trigger_sequences": {"before": ticked("sell_shards")}}
    out, added, _same = profilecopy.steps(mine, theirs, "before", replace=True)
    assert [s["action"] for s in out] == ["sell_shards"] and added == 1


def test_fighting_settings_leave_the_skills_out_for_another_class() -> None:
    theirs = {"attack_gap": 0.5, "press_burst": False, "skill_use": {"Fireball": "never"},
              "skill_use_others": "never", "attack_keys": ["1"], "scan": {"class": "Mage"}}
    assert profilecopy.fighting(theirs, with_skills=True)["skill_use"] == {"Fireball": "never"}
    got = profilecopy.fighting(theirs, with_skills=False)
    assert got == {"attack_gap": 0.5, "press_burst": False}                    # never the keys
    assert not profilecopy.same_class({"scan": {"class": "Orc"}}, theirs)
    assert profilecopy.same_class({}, theirs)                                  # never scanned: nothing says


def test_team_settings_are_copied_as_they_are() -> None:
    theirs = {"team_accept": True, "team_accept_list": ["Ann", 2001], "team_name": "Wolves", "pk_mode": 1}
    assert profilecopy.team(theirs) == {"team_accept": True, "team_accept_list": ["Ann", 2001], "team_name": "Wolves"}


wx = pytest.importorskip("wx")


@pytest.fixture
def frame() -> Iterator[Any]:
    app = wx.GetApp() or wx.App(False)
    window = wx.Frame(None)
    yield window
    window.Destroy()
    app.ProcessPendingEvents()


def test_the_character_window_copies_and_shows_it_saving_nothing(frame: Any) -> None:
    from unclefarmer.gui.profile import ProfileDialog
    theirs: dict[str, Any] = {"trigger_sequences": {"before": ticked("sell_shards")}, "skip_before_on_farm": False,
                              "fight_locked": True, "attack_gap": 0.6, "skill_use": {"Fireball": "never"},
                              "team_accept": True, "team_name": "Wolves", "team_accept_list": ["Ann"]}
    d = ProfileDialog(frame, "Hero")
    try:
        lines = d.copy_parts(theirs, ["before", "fighting", "team"], replace=False, with_skills=False)
        assert lines[0].startswith("Before farming list: 1 put in")
        assert [s["action"] for s in d.trigger_editors["before"].value()["steps"] if s["enabled"]] == ["sell_shards"]
        assert d.skip_before.GetValue() is False
        assert d.fight_locked.GetValue() is True and d.p["attack_gap"] == 0.6
        assert "Fireball" not in (d.p.get("skill_use") or {})                # another class: not the skills
        assert d.team_ui["accept"].GetValue() and d.team_ui["name"].GetValue() == "Wolves"
        assert "team_name" not in settings.load_profile("Hero") or settings.load_profile("Hero")["team_name"] != "Wolves"
    finally:
        d.Destroy()


def test_another_class_asks_first(frame: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui import profile as gui
    hero = settings.character_profile("Hero", 7, "S1")
    bob = settings.character_profile("Bob", 9, "S1")
    settings.save_profile(bob, {"scan": {"class": "Mage"}, "attack_gap": 0.9})
    monkeypatch.setattr(gui, "other_characters", returning([bob]))
    asked: list[str] = []

    class Picker:
        def __init__(self, *_a: Any) -> None: ...

        def __enter__(self) -> "Picker":
            return self

        def __exit__(self, *_a: Any) -> None: ...

        def ShowModal(self) -> int:
            return wx.ID_OK

        def picked(self) -> tuple[str, list[str], bool]:
            return bob, ["fighting"], False

    def no(_parent: Any, text: str, _title: str = "") -> bool:
        asked.append(text)
        return False
    monkeypatch.setattr(gui, "CopyFromDialog", Picker)
    monkeypatch.setattr(gui, "ask", no)
    d = gui.ProfileDialog(frame, hero)
    try:
        d.p.setdefault("scan", {})["class"] = "Orc"
        d.on_copy()
        assert asked and "Mage" in asked[0] and d.p.get("attack_gap") != 0.9          # said no: nothing copied
    finally:
        d.Destroy()
