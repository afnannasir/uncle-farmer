"""Copying parts of one character's bag settings to another (Bag and supplies, Copy from another
character): sorting the throw-away list item by item for each character was a lot of work."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import pytest

from conftest import returning

from unclefarmer import settings
from unclefarmer.engine import bagcopy

THEIRS = {"wolf fur": {"action": "throw", "keep": 0}, "bear pelt": {"action": "throw", "keep": 2},
          "ruby": {"action": "sell", "keep": 0}}


def test_adding_a_list_puts_their_items_in_and_keeps_what_is_only_here() -> None:
    mine = {"wolf fur": {"action": "throw", "keep": 0}, "goblin ear": {"action": "throw", "keep": 0},
            "bear pelt": {"action": "keep", "keep": 0}}
    out, added, same = bagcopy.copy_rules(mine, THEIRS, ["throw"], replace=False)
    assert out["bear pelt"] == {"action": "throw", "keep": 2}         # with what it leaves in the bag
    assert out["goblin ear"]["action"] == "throw"                     # only here: stays
    assert "ruby" not in out                                           # the sell list wasn't asked for
    assert added == {"throw": 1} and same == {"throw": 1}


def test_replacing_a_list_makes_it_theirs() -> None:
    mine = {"goblin ear": {"action": "throw", "keep": 0}, "potion": {"action": "store", "keep": 5}}
    out, added, _same = bagcopy.copy_rules(mine, THEIRS, ["throw"], replace=True)
    assert "goblin ear" not in out                                     # back to the bot's default
    assert out["potion"] == {"action": "store", "keep": 5}             # another list: untouched
    assert {k for k, v in out.items() if v["action"] == "throw"} == {"wolf fur", "bear pelt"}
    assert added == {"throw": 2}


def test_supplies_add_keeps_our_own_numbers_and_replace_takes_theirs() -> None:
    mine = [{"item": "Medium Red Potion", "min": 50, "stock": 300}]
    theirs = [{"item": "medium red potion", "min": 10, "stock": 100}, {"item": "Toolbox", "min": 1, "stock": 3}]
    out, added, same = bagcopy.copy_supplies(mine, theirs, replace=False)
    assert [r["item"] for r in out] == ["Medium Red Potion", "Toolbox"] and out[0]["min"] == 50
    assert (added, same) == (1, 1)
    out, added, _same = bagcopy.copy_supplies(mine, theirs, replace=True)
    assert out == theirs and out is not theirs and added == 2


def test_the_full_bag_page_is_copied_as_it_is() -> None:
    theirs = {"bag_full_at": 90, "when_bag_full": "unload", "gear_throw": ["crude"], "other": 1}
    assert bagcopy.full_settings(theirs) == {"bag_full_at": 90, "when_bag_full": "unload", "gear_throw": ["crude"]}


wx = pytest.importorskip("wx")


@pytest.fixture
def frame() -> Iterator[Any]:
    app = wx.GetApp() or wx.App(False)
    window = wx.Frame(None)
    yield window
    window.Destroy()
    app.ProcessPendingEvents()


def test_the_bag_window_copies_their_throw_away_list_and_says_what_changed(frame: Any) -> None:
    from unclefarmer.gui.bagmanager import BagManagerDialog
    theirs = {"bag_rules": dict(THEIRS), "restock": [{"item": "Toolbox", "min": 1, "stock": 3}],
              "scan": {"bag": [{"name": "Wolf Fur", "count": 4}]}, "bag_full_at": 80, "when_bag_full": "unload",
              "gear_throw": ["crude", "common"]}
    mine = settings.load_profile("Hero")
    d = BagManagerDialog(frame, "Hero", mine)
    try:
        lines = d.copy_parts(theirs, ["throw", "supplies", "full"], replace=False)
        assert lines[0].startswith("Throw-away list: 2 put in, 0 already there")
        assert d.rules["wolf fur"]["action"] == "throw" and "Wolf Fur" in d.others   # their bag's spelling
        assert "Wolf Fur" in d.item_lists.members("throw")
        assert any(r["item"] == "Toolbox" for r in d.supplies)
        assert d.full_at.GetValue() == 80 and d.gear["crude"].GetValue() and not d.gear["rare"].GetValue()
        assert mine.get("bag_rules", {}).get("wolf fur") is None        # nothing saved until Save
    finally:
        d.Destroy()


def test_copy_from_lists_the_other_characters_only(frame: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui import bagmanager
    hero = settings.character_profile("Hero", 7, "S1")
    bob = settings.character_profile("Bob", 9, "S1")
    monkeypatch.setattr(bagmanager.settings, "profiles", returning([hero, bob]))
    shown: list[list[str]] = []

    class Picker:
        def __init__(self, _parent: Any, others: list[str], _parts: Any = ()) -> None:
            shown.append(others)

        def __enter__(self) -> "Picker":
            return self

        def __exit__(self, *_a: Any) -> None: ...

        def ShowModal(self) -> int:
            return wx.ID_CANCEL
    monkeypatch.setattr(bagmanager, "CopyFromDialog", Picker)
    d = bagmanager.BagManagerDialog(frame, hero, settings.load_profile(hero))
    try:
        d.on_copy()
    finally:
        d.Destroy()
    assert shown == [[bob]]
