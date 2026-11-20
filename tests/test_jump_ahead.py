# pyright: reportPrivateUsage=false
"""Escape to jump ahead (Phase 1b item 6, the Hunter's part): Escape jumps 12 tiles away from where
we face, free of the global cooldown. The player turns first (a 0.1 s tap of the opposite key) so it
carries them forward. The bot does that on a long walk on foot, only along a clear straight row that
follows its planned way, and only once agent 76 shows the turn took."""
from __future__ import annotations

from typing import Any

import pytest
from conftest import as_kind, stand_in
from test_walking import MOVE_KEYS, Map

from unclefarmer import atlas
from unclefarmer.atlaslib import MapModel
from unclefarmer.clock import Clock as RealClock
from unclefarmer.engine import combat
from unclefarmer.engine.botparts import walking
from unclefarmer.engine.botparts.walking import WalkingMixin

FACING = {"left": 1, "up": 2, "right": 4, "down": 8}       # the facing a tap of each key gives


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now


class JumpBook:
    ok = True

    def __init__(self, ready: bool = True) -> None:
        self.ready = ready
        self.pressed: list[str] = []

    def jump_back_for(self, now: float) -> dict[str, Any] | None:
        return {"name": "Escape", "key": "e", "page": 1} if self.ready and not self.pressed else None

    def press_support(self, x: dict[str, Any], now: float) -> tuple[str, str]:
        self.pressed.append(x["name"])
        return x["key"], x["name"]


class JumpBot(WalkingMixin):
    def __init__(self, pos: tuple[float, float] = (10.0, 10.0), compass: int | None = 0, turns: bool = True) -> None:
        self.p = {"move_keys": dict(MOVE_KEYS)}
        self.book = JumpBook()
        self.skillbook = as_kind(combat.SkillBook, self.book)
        self.ticks = Clock()
        self.clock = as_kind(RealClock, self.ticks)
        self.in_fight = False
        self.walk_only = False
        self.pos = pos
        self.compass = compass
        self.turns = turns           # a tap turns us (else a cast or a window kept the old facing)
        self.keys: list[tuple[str, int]] = []
        self.logs: list[str] = []

    def me(self) -> dict[str, Any]:
        me: dict[str, Any] = {"x": self.pos[0], "y": self.pos[1], "hp": 100}
        if self.compass is not None:
            me["compass"] = self.compass
        return me

    def map_id(self) -> int:
        return 1

    def dialog(self) -> dict[str, Any]:
        return {}

    def move_key(self, direction: str) -> str:
        return MOVE_KEYS[direction]

    def key(self, k: str, ms: int = 60) -> None:
        self.keys.append((k, ms))
        turn = next((d for d, key in MOVE_KEYS.items() if key == k), None)
        if turn and self.turns and self.compass is not None:
            self.compass = FACING[turn]
        if k == "e":                 # Escape: 12 tiles away from the facing
            step = {1: (1, 0), 4: (-1, 0), 2: (0, 1), 8: (0, -1)}[self.compass or 1]
            self.pos = (self.pos[0] + 12 * step[0], self.pos[1] + 12 * step[1])

    def sleep(self, seconds: float) -> None:
        self.ticks.now += seconds

    def log(self, line: str, speak: bool = False) -> None:
        self.logs.append(line)


@pytest.fixture
def grid(monkeypatch: pytest.MonkeyPatch) -> Map:
    m = Map()
    def model(mid: int | str) -> MapModel:
        return as_kind(MapModel, m)

    def owned(b: Any) -> list[dict[str, Any]]:
        return []
    monkeypatch.setattr(atlas, "model", model)
    monkeypatch.setattr(walking.beasts, "owned", stand_in(walking.beasts.owned, owned))
    return m


def test_which_skill_is_a_jump_backward() -> None:
    info = {x["name"]: x for cls in combat.knowledge().values() for x in cls}
    assert combat.SkillBook.jump_back_skill(info["Escape"])
    assert not combat.SkillBook.jump_back_skill(info["Leap Slash"])
    assert not combat.SkillBook.jump_back_skill(info["Wind Step"])


def test_a_long_walk_east_turns_west_then_jumps(grid: Map) -> None:
    b = JumpBot()
    assert b._jump_ahead([(40, 10)])
    assert b.keys == [("a", b.JUMP_TAP_MS), ("e", 60)]          # tap left (face x-), then Escape
    assert b.pos == (22.0, 10.0)
    assert any("Escape to jump ahead" in line for line in b.logs)


def test_a_walk_south_turns_north(grid: Map) -> None:
    b = JumpBot()
    assert b._jump_ahead([(11, 40)])
    assert b.keys[0][0] == "w" and b.pos == (10.0, 22.0)


def test_no_jump_when_the_turn_didnt_take_or_the_agent_cant_tell(grid: Map) -> None:
    b = JumpBot(turns=False)
    assert not b._jump_ahead([(40, 10)])
    assert [k for k, _ms in b.keys] == ["a"] and b.book.pressed == []      # turned, never jumped
    b = JumpBot(compass=None)                                   # an agent before 74
    assert not b._jump_ahead([(40, 10)]) and b.keys == []


def test_no_jump_with_a_wall_in_the_row_or_off_the_planned_way(grid: Map) -> None:
    grid.walk[10, 18] = False                                   # a wall 8 tiles east
    b = JumpBot()
    assert not b._jump_ahead([(40, 10)]) and b.keys == []
    grid.walk[10, 18] = True
    b = JumpBot()
    assert not b._jump_ahead([(14, 10), (14, 40)]) and b.keys == []   # the way turns south after 4


def test_no_jump_on_a_short_walk_in_a_fight_or_with_escape_not_ready(grid: Map) -> None:
    b = JumpBot()
    assert not b._jump_ahead([(20, 10)])                        # 10 steps: shorter than the jump + 3
    b = JumpBot()
    b.in_fight = True
    assert not b._jump_ahead([(40, 10)])
    b = JumpBot()
    b.book = JumpBook(ready=False)
    b.skillbook = as_kind(combat.SkillBook, b.book)
    assert not b._jump_ahead([(40, 10)])
    assert b.keys == []


def test_a_jump_makes_the_walk_plan_again() -> None:
    b = JumpBot()
    assert b._pad_outcome(Map(), [(40, 10)], (10, 10), (40, 10), "leapt") == "jumped"
