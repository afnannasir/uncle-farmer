"""Opening the treasure chests that turn up while farming: which chest is picked, the walk, Enter
and "Open the chest", and what a chest that wouldn't open or was taken by someone else leaves."""

from __future__ import annotations

import math
from contextlib import nullcontext
from typing import Any

from conftest import as_bot, Clock

from unclefarmer import settings, stats
from unclefarmer.engine import chests


def chest(cid: int = -129, x: float = 137.0, y: float = 238.0) -> dict[str, Any]:
    """A chest as the agent reported it live (Margit Rainforest, 2026-11-14)."""
    return {"id": cid, "kind": "monster", "side": -2, "name": "Chest", "x": x, "y": y, "type": 0,
            "facing": 7203, "hp": 19998239, "maxhp": 20000000}


def wolf(x: float = 105.0, y: float = 100.0) -> dict[str, Any]:
    return {"id": -1, "kind": "monster", "side": -2, "name": "Grey wolf", "x": x, "y": y, "hp": 30, "maxhp": 30}


class Bot:
    """The part of the Bot chests.py uses. talk() and choose() say how far the game goes along."""

    def __init__(self, entities: list[dict[str, Any]], p: dict[str, Any] | None = None) -> None:
        self.clock = Clock()
        self.p = dict(settings.PROFILE_DEFAULTS, **(p or {}))
        self.in_instance = False
        self.chests = chests.Chests()
        self.tally: Any = None
        self.entities = entities
        self.hitting: set[int] = set()
        self.walls: set[int] = set()        # chest ids no walk reaches
        self.talks: list[tuple[str, Any]] = []
        self.chosen: list[str] = []
        self.answers = True                  # the chest's menu opens on Enter
        self.menu_has_open = True
        self.opens = True                    # choosing the entry takes the chest away
        self.taken_on_the_way = False        # someone else opens it while we walk there
        self.lines: list[tuple[str, bool]] = []
        self.closed = 0
        self.modes_set = 0
        self.modes_done = False

    def snap(self) -> dict[str, Any]:
        return {"self": {"x": 100.0, "y": 100.0}, "map": {"id": 5281}, "entities": [dict(e) for e in self.entities]}

    def attackers(self) -> set[int]:
        return self.hitting

    def walk_dist(self, snap: dict[str, Any], e: dict[str, Any], limit: float | None = None) -> float:
        if e["id"] in self.walls:
            return math.inf
        d = math.hypot(e["x"] - snap["self"]["x"], e["y"] - snap["self"]["y"])
        return math.inf if limit is not None and d > limit else d

    def talk(self, name: str, at: Any = None, stand: Any = None) -> bool:
        self.talks.append((name, at))
        if self.taken_on_the_way:
            self.entities = [e for e in self.entities if not chests.is_chest(e)]
            return False
        return self.answers

    def choose(self, pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        self.chosen.append(pattern)
        if not self.menu_has_open:
            return None
        if self.opens:
            self.entities = [e for e in self.entities if not chests.is_chest(e)]
        return {"options": ["Open the chest"]}

    def close_dialogs(self) -> None:
        self.closed += 1

    def set_modes(self) -> None:
        self.modes_set += 1

    def timed(self, phase: str) -> Any:
        return nullcontext()

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append((line, speak))


def go(b: Bot) -> bool:
    return chests.open_nearby(as_bot(b), b.snap())


# ---------------------------------------------------------------- what counts as a chest
def test_a_chest_is_the_monster_named_chest() -> None:
    assert chests.is_chest(chest())
    assert chests.is_chest(dict(chest(), name=" chest "))
    assert not chests.is_chest(wolf())
    assert not chests.is_chest(dict(chest(), kind="npc"))       # never a town NPC of that name
    assert not chests.is_chest({k: v for k, v in chest().items() if k != "hp"})


def test_in_sight_lists_the_chests_or_one_of_them() -> None:
    snap = {"entities": [wolf(), chest(-5), chest(-6)]}
    assert [e["id"] for e in chests.in_sight(snap)] == [-5, -6]
    assert [e["id"] for e in chests.in_sight(snap, -6)] == [-6]


def test_targets_still_leave_the_chest_out() -> None:
    """The chest carries a town NPC's hp: the fight code must keep ignoring it (it doesn't die)."""
    from unclefarmer.engine.botparts.base import NPC_HP
    assert chest()["maxhp"] >= NPC_HP


# ---------------------------------------------------------------- picking one
def test_nothing_to_do_without_a_chest() -> None:
    b = Bot([wolf()])
    assert not go(b)
    assert b.talks == [] and b.closed == 0


def test_the_nearest_reachable_chest_is_picked() -> None:
    b = Bot([chest(-5, 150, 100), chest(-6, 120, 100), chest(-7, 110, 100)])
    b.walls = {-7}
    picked = chests.pick(as_bot(b), b.snap())
    assert picked is not None and picked["id"] == -6


def test_a_chest_too_far_to_walk_is_left() -> None:
    b = Bot([chest(x=100 + chests.MOST_STEPS + 5, y=100)])
    assert chests.pick(as_bot(b), b.snap()) is None


def test_no_chest_while_something_hits_us() -> None:
    b = Bot([chest(x=110, y=100), wolf()])
    b.hitting = {-1}
    assert chests.pick(as_bot(b), b.snap()) is None


def test_no_chest_when_turned_off_or_in_a_dungeon() -> None:
    b = Bot([chest(x=110, y=100)], {"open_chests": False})
    assert chests.pick(as_bot(b), b.snap()) is None
    b = Bot([chest(x=110, y=100)])
    b.in_instance = True
    assert chests.pick(as_bot(b), b.snap()) is None


def test_no_chest_beside_a_boss(monkeypatch: Any) -> None:
    boss = dict(wolf(112, 100), id=-50, name="King beetle", hp=79730, maxhp=79730)
    monkeypatch.setattr(chests.bosses, "names", lambda: {"king beetle"})
    b = Bot([chest(x=110, y=100), boss])
    assert chests.pick(as_bot(b), b.snap()) is None
    b = Bot([chest(x=110, y=100), boss], {"boss_distance": 0})       # keeping away is off
    assert chests.pick(as_bot(b), b.snap()) is not None


# ---------------------------------------------------------------- opening it
def test_a_chest_is_walked_to_and_opened() -> None:
    b = Bot([chest(x=110, y=104)])
    assert go(b)
    assert b.talks == [("Chest", (110, 104))]
    assert b.chosen == [chests.OPEN]
    assert b.chests.opened == 1 and b.chests.skip == {}
    assert any("opened a chest (1 this run)" in line and speak for line, speak in b.lines)
    # the menu is closed and the target key goes back to enemies, whatever happened
    assert b.closed == 1 and b.modes_set == 1 and b.modes_done


def test_an_opened_chest_is_counted_in_the_results() -> None:
    clock = Clock()
    tally = stats.Tally("Hero", "test", clock=clock)
    b = Bot([chest(x=110, y=104)])
    b.tally = tally
    go(b)
    assert tally.parts[stats.FARMING].chests == 1
    rec = tally.results()[0]
    assert rec["chests"] == 1
    assert "1 chest opened" in stats.short(rec)
    assert stats.add_up([rec, rec])["chests"] == 2


def test_a_chest_that_wont_answer_is_left_alone_a_while() -> None:
    b = Bot([chest(x=110, y=104)])
    b.answers = False
    assert go(b)
    assert b.chests.opened == 0
    assert b.chests.skip == {-129: b.clock.time() + chests.GIVE_UP}
    assert b.closed == 1 and b.modes_set == 1
    assert chests.pick(as_bot(b), b.snap()) is None           # not again straight away
    b.clock.advance(chests.GIVE_UP + 1)
    assert chests.pick(as_bot(b), b.snap()) is not None       # but later


def test_a_menu_without_the_entry_leaves_it_alone() -> None:
    b = Bot([chest(x=110, y=104)])
    b.menu_has_open = False
    assert go(b)
    assert b.chests.opened == 0 and -129 in b.chests.skip


def test_a_chest_still_there_after_choosing_is_not_counted() -> None:
    b = Bot([chest(x=110, y=104)])
    b.opens = False
    assert go(b)
    assert b.chests.opened == 0 and -129 in b.chests.skip
    assert any("still there" in line for line, _speak in b.lines)
    assert b.clock.time() >= 1000.0 + chests.GONE_WAIT          # it gave the game its time


def test_a_chest_someone_else_opened_is_not_held_against_it() -> None:
    b = Bot([chest(x=110, y=104)])
    b.taken_on_the_way = True
    assert go(b)
    assert b.chests.opened == 0 and b.chests.skip == {}
    assert any("someone else opened it" in line for line, _speak in b.lines)


# ---------------------------------------------------------------- what a chest gave
class LootBot(Bot):
    """A bag that gets the chest's items when it opens, and its arnebia a moment later."""

    def __init__(self, entities: list[dict[str, Any]]) -> None:
        super().__init__(entities)
        self.bag: list[dict[str, Any]] = [{"name": "arnebia", "count": 1000}, {"name": "Small Fish", "count": 2}]
        self.reads = 0

    def choose(self, pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        got = super().choose(pattern)
        self.bag = [{"name": "arnebia", "count": 1000}, {"name": "Small Fish", "count": 2},
                    {"name": "Level 1 Obsidian Shard", "count": 24}]
        return got

    def bag_rows(self) -> list[dict[str, Any]]:
        if any(r["name"] == "Level 1 Obsidian Shard" for r in self.bag):
            self.reads += 1
            if self.reads >= 3:              # the arnebia comes in after the items
                self.bag[0] = {"name": "arnebia", "count": 10190}
        return [dict(r) for r in self.bag]


def test_what_a_chest_gave_is_written_in_the_log() -> None:
    b = LootBot([chest(x=110, y=104)])
    assert go(b)
    assert ("the chest gave: +9,190 arnebia, +24 Level 1 Obsidian Shard", False) in b.lines


def test_a_bag_that_cannot_be_read_leaves_the_loot_line_out() -> None:
    b = Bot([chest(x=110, y=104)])           # no bag_rows at all
    assert go(b)
    assert not any("the chest gave" in line for line, _speak in b.lines)
