# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""A game that ran out of memory freezes the character where it stands: the bot must stop for good
with that reason (GameBroken), not read it as a stuck walk, and it keeps saying so once it knows."""

import threading

import pytest

from unclefarmer.engine.botparts.base import GameBroken, Stopped
from unclefarmer.engine.botparts.reading import ReadingMixin


class Agent:
    def __init__(self, oom=0):
        self.oom = oom
        self.asked = 0

    def send(self, line):
        return "ok"

    def json(self, line):
        assert line == "health"
        self.asked += 1
        return {"ok": True, "oom": self.oom, "oom_where": "thread Timer-1: unable to create new native thread",
                "threads": 101, "heap_mb": 300, "heap_max_mb": 1004}


class Bot(ReadingMixin):
    def __init__(self, agent, version=36):
        self.a = agent
        self.agent_version = version
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.daily_stop_at = None
        self.daily_done = False
        self.lines = []

    def log(self, line, speak=False):
        self.lines.append(line)


def test_a_sound_game_runs_on():
    b = Bot(Agent())
    b.check()
    b.check_game(force=True)
    assert b.broken is None


def test_out_of_memory_stops_the_run_for_good_and_says_why():
    b = Bot(Agent(oom=1))
    with pytest.raises(GameBroken) as e:
        b.check()
    assert isinstance(e.value, Stopped) and e.value.final
    assert "out of memory" in str(e.value) and "Timer-1" in str(e.value)
    asked = b.a.asked
    with pytest.raises(GameBroken):       # every later check too, without asking the game again
        b.check()
    assert b.a.asked == asked


def test_a_stuck_asks_at_once_not_after_the_usual_wait():
    b = Bot(Agent())
    b.check()                              # read just now
    b.a.oom = 1
    b.check()                              # within the wait: not asked again
    with pytest.raises(GameBroken):
        b.check_game(force=True)           # what a stuck walk does


def test_an_older_agent_is_never_asked():
    b = Bot(Agent(oom=1), version=35)
    b.check_game(force=True)
    assert b.a.asked == 0 and b.broken is None
