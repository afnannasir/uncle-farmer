# pyright: reportPrivateUsage=false
"""A full storehouse (seen in the game 2026-11-18): it says "item storage is full" and takes nothing
of an item it holds no stack of yet; an item it already holds still goes in. The menu closes after
every deposit, either way.

  - The refused item isn't taken to the storehouse again this run (every bag trip walked it there
    and got "put 0"), and the trip goes on with the others.
  - The log says the storehouse is full, not that it "may be".
  - Something taken out of the storehouse makes room: the refusals are forgotten.
  - A deposit that took nothing without the game saying "full" isn't held against the item."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot, returning, stand_in
from test_store_items import store_rules
from test_town import Link, TownBot, _unload_bot

from unclefarmer.engine import bagrules, town


class SaidLink(Link):
    """The agent: what the game said (IN op=60), each line with its time."""

    def __init__(self) -> None:
        super().__init__()
        self.now = 1000
        self.lines: list[tuple[int, str]] = []

    def say(self, text: str) -> None:
        self.now += 10
        self.lines.append((self.now, text))

    def json(self, line: str) -> Any:
        if line.startswith("said "):
            since = int(line.split()[1])
            return {"now": self.now, "said": [{"t": t, "text": x} for t, x in self.lines if t > since]}
        return super().json(line)


class FullHouse:
    """A full storehouse holding stacks of `holds`: those go in, anything else is refused."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tb: TownBot, holds: set[str],
                 says_full: bool = True) -> None:
        self.visits: list[str] = []
        link = SaidLink()
        tb.link = tb.a = link
        monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))

        def visit(b: Any, role: str | None, name: str | None = None, candidates: Any = None) -> str:
            self.visits.append(role or "")
            return "Ansible Space-Time Storehouse"
        monkeypatch.setattr(town, "visit", stand_in(town.visit, visit))

        def pick(b: Any, pattern: str, quantity: int | None = None) -> bool:
            name = next(r["name"] for r in tb.bag if town.re.search(pattern, r["name"], town.re.I))
            if name in holds:
                tb.remove(name, quantity or 0)
                link.say("deposited")
            elif says_full:
                link.say("item storage is full")
            return True
        monkeypatch.setattr(town, "pick", stand_in(town.pick, pick))


BAG = [{"name": "Level 2 Obsidian Shard", "count": 3}, {"name": "Level 1 Blond Jade Shard", "count": 5}]


def test_an_item_the_full_storehouse_refuses_isn_t_taken_there_again(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[dict(r) for r in BAG])
    house = FullHouse(monkeypatch, tb, holds={"Level 1 Blond Jade Shard"})
    assert town.deposit(as_bot(tb), "Level 2 Obsidian Shard") == 0
    assert any("the storehouse is full: Level 2 Obsidian Shard has no place in it" in line for line in tb.lines)
    assert town.deposit(as_bot(tb), "Level 2 Obsidian Shard") == 0
    assert house.visits == ["storehouse"]                          # not walked there a second time
    assert any("it is full and holds none yet" in line for line in tb.lines)


def test_an_item_it_holds_a_stack_of_still_goes_in(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[dict(r) for r in BAG])
    FullHouse(monkeypatch, tb, holds={"Level 1 Blond Jade Shard"})
    town.deposit(as_bot(tb), "Level 2 Obsidian Shard")
    assert town.deposit(as_bot(tb), "Level 1 Blond Jade Shard") == 5


def test_nothing_taken_without_the_game_saying_full_is_tried_again(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[dict(r) for r in BAG])
    house = FullHouse(monkeypatch, tb, holds=set(), says_full=False)
    assert town.deposit(as_bot(tb), "Level 2 Obsidian Shard") == 0
    assert town.deposit(as_bot(tb), "Level 2 Obsidian Shard") == 0
    assert len(house.visits) == 2 and town.refused(as_bot(tb)) == set()


def test_the_full_message_is_waited_for_a_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[dict(r) for r in BAG])
    FullHouse(monkeypatch, tb, holds=set(), says_full=False)
    link: SaidLink = tb.link       # type: ignore[assignment]
    sleeps = [0]

    def sleep(seconds: float) -> None:
        sleeps[0] += 1
        tb.clock.advance(seconds)
        if sleeps[0] == 2:
            link.say("item storage is full")             # half a second after the menu closed
    tb.sleep = sleep
    town.deposit(as_bot(tb), "Level 2 Obsidian Shard")
    assert town.refused(as_bot(tb)) == {"level 2 obsidian shard"}


def test_taking_something_out_makes_room_again(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[dict(r) for r in BAG])
    town.refused(as_bot(tb)).add("level 2 obsidian shard")
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Storehouse")))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))
    tb.dialog = lambda: {"open": 1, "top": {"rows": [[{"options": ["1; 50 Large Red Potion"]}]], "row": 0, "col": 0}}
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity=None:
                                               tb.add("Large Red Potion", quantity or 0) or True))
    monkeypatch.setattr(town.knowledge, "service_npcs", stand_in(town.knowledge.service_npcs,
                                                                 lambda role: [(1081, "Storehouse", 344, 160)]))
    assert town.withdraw(as_bot(tb), "Large Red Potion", 10) == 10
    assert town.refused(as_bot(tb)) == set()


def test_store_items_says_the_storehouse_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[dict(r) for r in BAG], profile={"bag_rules": store_rules(
        Level_2_Obsidian_Shard=("store", 0), Level_1_Blond_Jade_Shard=("store", 0))})
    FullHouse(monkeypatch, tb, holds={"Level 1 Blond Jade Shard"})
    assert town.store_items(as_bot(tb)) == 1
    full = [line for line in tb.spoken if "the storehouse is full - these stay in the bag" in line]
    assert len(full) == 1 and "Level 2 Obsidian Shard" in full[0]
    assert not any("may be full" in line for line in tb.spoken)


def test_a_bag_trip_says_the_storehouse_is_full(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = _unload_bot([dict(r) for r in BAG])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning(
        [("Level 2 Obsidian Shard", "store", 3), ("Level 1 Blond Jade Shard", "store", 5)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(None)))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    FullHouse(monkeypatch, tb, holds={"Level 1 Blond Jade Shard"})
    assert town.unload(as_bot(tb)) == 1
    assert any("the storehouse is full - these stay in the bag until there is room in it: Level 2 Obsidian Shard"
               in line for line in tb.spoken)
