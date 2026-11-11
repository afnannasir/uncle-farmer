"""Tools, Store items now (town.store_items): what the bag manager's storage list says goes into
the storehouse through the same deposit the bag trips use - nothing that isn't on the list, never
more than the rule leaves to keep, and every item stored, kept, missing or turned away said."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot, stand_in
from test_town import TownBot

from unclefarmer import settings
from unclefarmer.engine import town
from unclefarmer.engine.bot import Stopped


def store_rules(**rules: tuple[str, int]) -> dict[str, dict[str, Any]]:
    return {name.replace("_", " ").lower(): {"action": action, "keep": keep} for name, (action, keep) in rules.items()}


class Storehouse:
    """The storehouse's deposit menu: takes what is asked, up to `room` items in all (then full)."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tb: TownBot, room: int = 10 ** 6) -> None:
        self.room = room
        self.asked: list[tuple[str, int | None]] = []
        self.visits = 0
        monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))

        def pick(b: Any, pattern: str, quantity: int | None = None) -> bool:
            name = next(r["name"] for r in tb.bag if town.re.search(pattern, r["name"], town.re.I))
            self.asked.append((name, quantity))
            n = min(quantity or 0, self.room)
            self.room -= n
            tb.remove(name, n)
            return n > 0
        monkeypatch.setattr(town, "pick", stand_in(town.pick, pick))

        def visit(b: Any, role: str | None, name: str | None = None, candidates: Any = None) -> str:
            assert role == "storehouse"
            self.visits += 1
            return "Storehouse Keeper"
        monkeypatch.setattr(town, "visit", stand_in(town.visit, visit))


BAG = [{"name": "Ruby Shard", "count": 12}, {"name": "Wolf Fur", "count": 30}, {"name": "Old Boots", "count": 2},
       {"name": "Large Red Potion", "count": 50}]


def bot(rules: dict[str, dict[str, Any]], bag: list[dict[str, Any]] | None = None) -> TownBot:
    return TownBot(bag=bag if bag is not None else BAG, profile={"bag_rules": rules})


def count(tb: TownBot, name: str) -> int:
    return sum(int(r["count"]) for r in tb.bag if r["name"] == name)


def test_store_list_is_only_the_store_rules_the_player_set() -> None:
    p = {"bag_rules": store_rules(Wolf_Fur=("store", 5), Old_Boots=("throw", 0), Ruby_Shard=("store", 0),
                                  Potion=("keep", 10))}
    assert town.store_list(p) == [("ruby shard", 0), ("wolf fur", 5)]
    assert town.store_list({}) == [] and town.store_list({"bag_rules": {"x": "not a rule"}}) == []


def test_only_listed_items_go_in_keeping_what_each_rule_keeps(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 5), Ruby_Shard=("store", 0), Old_Boots=("throw", 0)))
    house = Storehouse(monkeypatch, tb)
    assert town.store_items(as_bot(tb)) == 2
    assert sorted(house.asked) == [("Ruby Shard", 12), ("Wolf Fur", 25)]
    assert count(tb, "Wolf Fur") == 5 and count(tb, "Ruby Shard") == 0
    assert count(tb, "Old Boots") == 2 and count(tb, "Large Red Potion") == 50      # untouched
    assert any("stored 12 Ruby Shard, 25 Wolf Fur" in line for line in tb.spoken)
    assert any(line.startswith("put 25 Wolf Fur into the storehouse") for line in tb.lines)


def test_the_list_is_read_when_the_job_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 0)))
    house = Storehouse(monkeypatch, tb)
    tb.p["bag_rules"] = store_rules(Ruby_Shard=("store", 2))       # the player changed it meanwhile
    assert town.store_items(as_bot(tb)) == 1
    assert house.asked == [("Ruby Shard", 10)]


def test_an_empty_list_stores_nothing_and_says_so(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Old_Boots=("throw", 0)))
    house = Storehouse(monkeypatch, tb)
    assert town.store_items(as_bot(tb)) == 0
    assert house.visits == 0 and any("the storage list is empty" in line for line in tb.spoken)


def test_listed_items_missing_from_the_bag_are_said(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 0), Dragon_Scale=("store", 0)))
    Storehouse(monkeypatch, tb)
    assert town.store_items(as_bot(tb)) == 1
    assert "not in the bag: dragon scale" in tb.lines


def test_items_only_at_their_keep_count_stay(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 30), Dragon_Scale=("store", 0)))
    house = Storehouse(monkeypatch, tb)
    assert town.store_items(as_bot(tb)) == 0
    assert house.visits == 0
    assert "kept in the bag, as their rules say: wolf fur" in tb.lines
    assert any("nothing in the bag is on the storage list" in line for line in tb.spoken)


def test_a_full_storehouse_is_said_and_nothing_is_lost(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 0), Ruby_Shard=("store", 0)))
    house = Storehouse(monkeypatch, tb, room=4)                      # room for 4 items only
    assert town.store_items(as_bot(tb)) == 1
    assert count(tb, "Ruby Shard") + count(tb, "Wolf Fur") == 42 - 4   # what didn't fit stays in the bag
    assert house.room == 0
    said = " ".join(tb.spoken)
    assert "not stored (the storehouse may be full)" in said
    assert "Ruby Shard (4 of 12: the storehouse took no more)" in said and "Wolf Fur" in said


def test_a_storehouse_that_cant_be_reached_is_said_per_item(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 0)))
    Storehouse(monkeypatch, tb)

    def walled(b: Any, role: str | None, name: str | None = None, candidates: Any = None) -> str:
        raise Stopped("no way to the storehouse")
    monkeypatch.setattr(town, "visit", stand_in(town.visit, walled))
    assert town.store_items(as_bot(tb)) == 0
    assert "couldn't store Wolf Fur: no way to the storehouse" in tb.lines
    assert count(tb, "Wolf Fur") == 30


def test_stop_ends_it(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = bot(store_rules(Wolf_Fur=("store", 0), Ruby_Shard=("store", 0)))
    Storehouse(monkeypatch, tb)
    tb.stop_event.set()
    with pytest.raises(Stopped):
        town.store_items(as_bot(tb))


def test_storing_by_hand_changes_no_setting_or_list(monkeypatch: pytest.MonkeyPatch) -> None:
    p = settings.load_profile("Hero")
    p["bag_rules"] = store_rules(Wolf_Fur=("store", 5))
    p["store_items_after_farming"] = True
    settings.save_profile("Hero", p)
    tb = bot(settings.load_profile("Hero")["bag_rules"])
    Storehouse(monkeypatch, tb)
    town.store_items(as_bot(tb))
    again = settings.load_profile("Hero")
    assert again["bag_rules"] == store_rules(Wolf_Fur=("store", 5))
    assert again["store_items_after_farming"] is True and again["store_items_before_farming"] is False


def test_the_tools_menu_runs_it_by_hand(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import editing
    p = settings.load_profile("Hero")
    p["bag_rules"] = store_rules(Wolf_Fur=("store", 0))
    settings.save_profile("Hero", p)
    errands: list[tuple[str, str]] = []
    lines: list[str] = []

    class Window:
        runs: dict[int, Any] = {}

        def run_errand(self, text: str, title: str, walk: bool = False) -> None:
            errands.append((text, title))

        def log(self, line: str, speak: bool = False, who: str | None = None, private: bool = False) -> None:
            lines.append(line)

        def snapshot(self) -> dict[str, Any]:
            return {"self": {"name": "Hero"}}

        def selected_pid(self) -> int:
            return 1

        def say(self, text: str) -> None:
            lines.append(text)
    w: Any = Window()
    editing.EditingMixin.store_items_now(w)
    assert errands == [("store_items\n", "Storing items")]
    assert "Store items now: started by hand" in lines
