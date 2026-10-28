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
