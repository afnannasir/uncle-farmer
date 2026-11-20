"""Chests gone back for and looked for (engine/chests.py memory, engine/chesthunt.py): a chest seen
in a fight and out of sight after it is walked back to; our map's announced chest starts a sweep
of the farm area, which ends when the chest is opened, the area seen, or the minutes run out."""

from __future__ import annotations

import math
from typing import Any

from conftest import as_bot
from test_chests import Bot, chest

from unclefarmer.engine import chesthunt, chests

APPEARS = "There's a Shining Small Chest in Margit Rainforest. Go find it and see what's inside!"
OPENED = "The lucky adventurer has opened the treasure chest in Margit Rainforest."


class Link:
    """The agent's chat: lines numbered as it numbers them."""

    def __init__(self) -> None:
        self.lines: list[dict[str, Any]] = []

    def say(self, text: str, channel: int = 37, name: str = "") -> None:
        self.lines.append({"n": len(self.lines) + 1, "channel": channel, "name": name, "text": text})

    def json(self, line: str) -> dict[str, Any]:
        after = int(line.split()[1])
        return {"ok": True, "last": len(self.lines), "chat": [m for m in self.lines if m["n"] > after]}


class Hunter(Bot):
    def __init__(self, entities: list[dict[str, Any]] | None = None, /, **p: Any) -> None:
        super().__init__(entities or [], p)
        self.a = Link()
        self.walks: list[tuple[float, float]] = []
        self.at = (100.0, 100.0)
        self.traveling = False
        self.rode: list[bool] = []

    def snap(self) -> dict[str, Any]:
        s = super().snap()
        s["self"] = {"x": self.at[0], "y": self.at[1]}
        return s

    def walk_to(self, cell: tuple[float, float], near: Any = None) -> str:
        self.walks.append(cell)
        self.rode.append(self.traveling)
        self.at = cell
        return "near" if near is not None and near({}) else "arrived"


def hunt(b: Hunter) -> bool:
    return chesthunt.hunt(as_bot(b), b.snap(), 5281, None)


def started(b: Hunter) -> None:
    hunt(b)                       # the first read only marks where the chat is
    b.a.say(APPEARS)
    b.clock.advance(chesthunt.LISTEN_EVERY)


# ---------------------------------------------------------------- the chest seen mid-fight
def test_a_chest_seen_then_out_of_sight_is_gone_back_for() -> None:
    b = Bot([chest(x=130.0, y=100.0)])
    b.chests.note(b.snap(), b.clock.time())     # seen while a fight went on
    b.entities = []                              # out of sight once it ended
    b.clock.advance(60)
    assert chests.open_nearby(as_bot(b), b.snap())
    assert b.talks == [("Chest", (130.0, 100.0))]
    assert b.lines[0][0] == "going back to the chest seen 30 steps away: going to open it"
    assert b.chests.seen == {}                  # gone: never gone back for twice


def test_a_remembered_chest_is_forgotten_after_a_while_or_on_another_map() -> None:
    b = Bot([chest(x=130.0, y=100.0)])
    b.chests.note(b.snap(), b.clock.time())
    b.entities = []
    assert b.chests.remembered(5000, b.clock.time(), set()) == []
    b.clock.advance(chests.REMEMBER + 1)
    assert not chests.open_nearby(as_bot(b), b.snap()) and b.talks == []


# ---------------------------------------------------------------- the announcement
def test_our_maps_announcement_starts_a_sweep_on_the_beast() -> None:
    b = Hunter()
    started(b)
    assert hunt(b)
    assert b.lines[0] == ("a chest turned up on Margit Rainforest (5281) - looking for it (up to 5 minutes)", True)
    assert len(b.walks) == 1 and b.rode == [True] and b.traveling is False


def test_another_maps_or_a_players_line_starts_nothing() -> None:
    b = Hunter()
    hunt(b)
    b.a.say(APPEARS.replace("Margit Rainforest", "Wolf Prairie"))
    b.a.say(APPEARS, channel=0, name="Bob")
    b.clock.advance(chesthunt.LISTEN_EVERY)
    assert not hunt(b) and b.walks == []


def test_the_sweep_never_walks_to_the_same_ground_twice() -> None:
    b = Hunter()
    started(b)
    for _i in range(6):
        b.clock.advance(5)
        assert hunt(b)
    assert len(set(b.walks)) == 6
    for i, (x, y) in enumerate(b.walks):
        assert all(math.hypot(x - u, y - v) > chesthunt.VIEW for u, v in b.walks[:i])


def test_the_opened_announcement_ends_it() -> None:
    b = Hunter()
    started(b)
    assert hunt(b)
    b.a.say(OPENED)
    b.clock.advance(chesthunt.LISTEN_EVERY)
    assert not hunt(b) and len(b.walks) == 1


def test_opening_it_ourselves_ends_it() -> None:
    b = Hunter()
    started(b)
    assert hunt(b)
    b.chests.opened += 1
    b.clock.advance(5)
    assert not hunt(b) and len(b.walks) == 1


def test_the_minutes_run_out() -> None:
    b = Hunter(chest_hunt_minutes=2)
    started(b)
    assert hunt(b)
    b.clock.advance(2 * 60 + 1)
    assert not hunt(b)
    assert b.lines[-1] == ("no chest found in 2 minutes - back to farming", False)
    b.clock.advance(5)
    assert not hunt(b) and len(b.walks) == 1


def test_a_chest_in_sight_or_an_attacker_comes_first() -> None:
    b = Hunter([chest()])
    started(b)
    assert not hunt(b)                         # chests.open_nearby takes it
    b.entities = []
    b.hitting = {-1}
    assert not hunt(b)                         # the fight first
    b.hitting = set()
    assert hunt(b)


def test_off_at_zero_with_chests_off_or_in_a_dungeon() -> None:
    for p in ({"chest_hunt_minutes": 0}, {"open_chests": False}, {}):
        b = Hunter(**p)
        b.in_instance = not p
        started(b)
        assert not hunt(b) and b.walks == []


def test_an_agent_without_the_chat_never_hunts() -> None:
    b = Hunter()

    class Broken:
        def json(self, line: str) -> dict[str, Any]:
            raise RuntimeError("no chat")
    b.a = Broken()  # type: ignore[assignment]
    for _i in range(3):
        b.clock.advance(chesthunt.LISTEN_EVERY)
        assert not hunt(b)


def test_the_sweep_points_cover_the_area_in_lanes() -> None:
    pts = chesthunt.sweep_points(5281, (11, 11, 390, 390), {"x": 0, "y": 0})
    assert len(pts) >= 9
    ys = sorted({round(y, -1) for _x, y in pts})
    assert len(ys) >= 3                         # several lanes, LANE apart
