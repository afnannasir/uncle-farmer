"""A game that ran out of memory freezes the character where it stands: the bot must stop for good
with that reason (GameBroken), not read it as a stuck walk, and it keeps saying so once it knows."""

from __future__ import annotations

import threading

import pytest

from unclefarmer.engine.botparts.base import GameBroken, Stopped
from unclefarmer.engine.botparts.reading import ReadingMixin
from typing import Any


class Agent:
    def __init__(self, oom: int=0) -> None:
        self.oom = oom
        self.asked = 0

    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> dict[str, Any]:
        assert line == "health"
        self.asked += 1
        return {"ok": True, "oom": self.oom, "oom_where": "thread Timer-1: unable to create new native thread",
                "threads": 101, "heap_mb": 300, "heap_max_mb": 1004}


class Bot(ReadingMixin):
    def __init__(self, agent: Agent, version: int=36) -> None:
        self.agent = agent              # the pretend game, under its own type
        self.a = agent
        self.agent_version = version
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.daily_stop_at = 0.0               # no daily stop
        self.daily_done = False
        self.lines: list[str] = []

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)


def test_a_sound_game_runs_on() -> None:
    b = Bot(Agent())
    b.check()
    b.check_game(force=True)
    assert b.broken is None


def test_out_of_memory_stops_the_run_for_good_and_says_why() -> None:
    agent = Agent(oom=1)
    b = Bot(agent)
    with pytest.raises(GameBroken) as e:
        b.check()
    assert isinstance(e.value, Stopped) and e.value.final
    assert "out of memory" in str(e.value) and "Timer-1" in str(e.value)
    asked = agent.asked
    with pytest.raises(GameBroken):       # every later check too, without asking the game again
        b.check()
    assert agent.asked == asked


def test_a_stuck_asks_at_once_not_after_the_usual_wait() -> None:
    b = Bot(Agent())
    b.check()                              # read just now
    b.agent.oom = 1
    b.check()                              # within the wait: not asked again
    with pytest.raises(GameBroken):
        b.check_game(force=True)           # what a stuck walk does


def test_an_older_agent_is_never_asked() -> None:
    b = Bot(Agent(oom=1), version=35)
    b.check_game(force=True)
    assert b.agent.asked == 0 and b.broken is None


# ---------------------------------------------------------------- a frozen world
class World:
    """`mem state` readings, one after another (the last one again once they run out)."""

    def __init__(self, *readings: dict[str, Any]) -> None:
        self.readings = list(readings)

    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> dict[str, Any]:
        return self.readings.pop(0) if len(self.readings) > 1 else self.readings[0]


class Looker(Bot):
    def sleep(self, seconds: float) -> None:
        pass


def reading(monster_x: float = 10.0, effect: int | None = None) -> dict[str, Any]:
    rows: list[dict[str, Any]] = [{"id": 5, "x": monster_x, "y": 3.0, "hp": 100}]
    if effect is not None:
        rows.append({"id": effect, "x": 1.0, "y": 1.0, "hp": 1})
    return {"ok": True, "self": {"x": 1.0, "y": 1.0, "hp": 50}, "entities": rows}


def frozen(world: World) -> bool:
    b = Looker(Agent())
    b.a = world                          # pyright: ignore[reportAttributeAccessIssue]
    return b.world_frozen()


def test_a_world_where_nothing_moves_is_frozen() -> None:
    assert frozen(World(reading()))


def test_a_monster_that_moves_or_an_effect_that_comes_means_the_game_lives() -> None:
    assert not frozen(World(reading(), reading(), reading(monster_x=11.0)))
    assert not frozen(World(reading(), reading(effect=900)))


def test_with_nothing_in_sight_a_freeze_is_not_assumed() -> None:
    assert not frozen(World({"ok": True, "self": {"x": 1.0, "y": 1.0, "hp": 50}, "entities": []}))
    assert not frozen(World({"error": "noworld: entity map"}))
