"""Pausing a run: keys let go, the player's own modifiers and the game's speech back in the game,
and on going on the walk's keys down again with its clocks counting from then, and the game muted
again if the run had muted it."""

from __future__ import annotations

import threading
import time
from typing import Any

from unclefarmer.clock import Waitable
from unclefarmer.engine.botparts import reading

from unclefarmer.engine.botparts.keys import KeysMixin
from unclefarmer.engine.botparts.reading import ReadingMixin


class Agent:
    def __init__(self, speech: str="on") -> None:
        self.sent: list[str] = []
        self.speech = speech
        self.map, self.x, self.y = 1503, 40.0, 50.0

    def send(self, line: str) -> str:
        self.sent.append(line)
        if line == "speech":
            return self.speech
        if line in ("speech on", "speech off"):
            self.speech = line.split()[1]
            return self.speech
        if line.startswith("keydown "):
            return "down " + line.split()[1]
        if line.startswith("guardmods "):
            return line.split()[1]
        return "ok"

    def json(self, line: str) -> dict[str, Any]:
        self.sent.append(line)
        if line in ("mem", "mem state"):
            return {"ok": True, "map": {"id": self.map}, "self": {"x": self.x, "y": self.y}, "entities": []}
        return {"ok": True, "open": False}


class Bot(ReadingMixin, KeysMixin):
    def __init__(self) -> None:
        self.link = Agent()
        self.a = self.link
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.daily_stop_at = 0.0               # no daily stop
        self.daily_done = False
        self.held = {"d", "s"}
        self.guarding = True
        self.lines: list[str] = []

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)


def test_a_pause_lets_go_and_going_on_holds_the_walk_again() -> None:
    b = Bot()
    b.paused.set()
    threading.Timer(0.4, b.paused.clear).start()
    before = time.time()
    b.check()
    sent = b.link.sent
    assert sent[:2] == ["keyup d", "keyup s"]      # this bot's keys only, never the game's keysup
    assert sent.index("guardmods off") < sent.index("guardmods on")
    assert sorted(line for line in sent if line.startswith("keydown")) == ["keydown d", "keydown s"]
    assert sent.index("guardmods on") < sent.index("keydown d")
    assert b.held == {"d", "s"}
    assert b.resumed_at >= before + 0.3


def test_a_run_without_the_guard_leaves_it_alone_when_paused() -> None:
    b = Bot()
    b.guarding = False
    b.held = set()
    b.paused.set()
    threading.Timer(0.2, b.paused.clear).start()
    b.check()
    assert not any(line.startswith("guardmods") for line in b.link.sent)


def test_a_muted_game_talks_while_paused_and_is_muted_again_after() -> None:
    b = Bot()
    b.link.speech = "off"                                   # the run muted it when it started
    heard: list[str] = []
    b.paused.set()

    def look():
        heard.append(b.link.speech)
        b.paused.clear()
    threading.Timer(0.3, look).start()
    b.check()
    assert heard == ["on"]
    assert b.link.speech == "off"


def test_a_game_that_was_talking_is_left_talking() -> None:
    b = Bot()
    b.paused.set()
    threading.Timer(0.2, b.paused.clear).start()
    b.check()
    assert b.link.speech == "on"
    assert "speech off" not in b.link.sent and "speech on" not in b.link.sent


def test_a_game_the_player_muted_while_paused_stays_muted() -> None:
    b = Bot()
    b.link.speech = "off"
    b.paused.set()

    def mute_by_hand():
        b.link.speech = "off"
        b.paused.clear()
    threading.Timer(0.3, mute_by_hand).start()
    b.check()
    assert b.link.speech == "off" and b.link.sent.count("speech off") == 0


class GameClock:
    """Game time that moves only as the pause waits; the pause ends after `paused_for` seconds."""

    def __init__(self, bot: Bot, paused_for: float, then: Any=None) -> None:
        self.now, self.bot, self.until, self.then = 1000.0, bot, 1000.0 + paused_for, then

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Waitable, timeout: float | None = None) -> bool:
        self.now += timeout or 0.0
        if self.now >= self.until and self.bot.paused.is_set():
            if self.then:
                self.then()
            self.bot.paused.clear()
        return event.is_set()


def test_a_long_pause_keeps_the_link_to_the_game_alive() -> None:
    # The agent drops a connection that asks nothing for 120 s: a 10-minute pause ended the run
    # as "lost the connection to the game" the moment it went on.
    b = Bot()
    b.clock = GameClock(b, 600.0)
    b.paused.set()
    b.check()
    asks = b.link.sent.count("version")
    assert 600 / reading.PAUSE_KEEPALIVE - 1 <= asks <= 600 / reading.PAUSE_KEEPALIVE + 1
    assert reading.PAUSE_KEEPALIVE < 120 / 2      # well inside the agent's idle limit
    assert b.lines[-1] == "going on"


def test_a_short_pause_asks_nothing_extra() -> None:
    b = Bot()
    b.clock = GameClock(b, 5.0)
    b.paused.set()
    b.check()
    assert "version" not in b.link.sent


def test_moved_by_hand_while_paused_the_walk_plans_again_from_there() -> None:
    b = Bot()

    def walk_two_tiles() -> None:
        b.link.x += 2
    b.clock = GameClock(b, 20.0, walk_two_tiles)
    b.paused.set()
    b.check()
    assert b.pause_moves == 1
    assert "moved 2 tiles while paused - going on from here" in b.lines
    assert b.lines[-1] == "going on"
    assert b.held == {"d", "s"}                  # the walk lets go itself when it sees the move


def test_paused_and_left_standing_the_walk_just_goes_on() -> None:
    b = Bot()

    def shuffle() -> None:
        b.link.x += 0.3                          # the game's own drift, not a move
    b.clock = GameClock(b, 20.0, shuffle)
    b.paused.set()
    b.check()
    assert b.pause_moves == 0
    assert not any("while paused" in line for line in b.lines)


def test_taken_to_another_map_while_paused_goes_on_from_there() -> None:
    b = Bot()

    def walk_out() -> None:
        b.link.map = 1081
    b.clock = GameClock(b, 20.0, walk_out)
    b.paused.set()
    b.check()
    assert b.pause_moves == 1
    assert "on another map than when paused - going on from here" in b.lines


def test_a_game_that_cant_say_where_we_are_still_goes_on() -> None:
    b = Bot()
    b.link.json = lambda line: {"ok": False, "error": "busy: map loading"}     # type: ignore[method-assign]
    b.clock = GameClock(b, 20.0)
    b.paused.set()
    b.check()
    assert b.pause_moves == 0 and b.lines[-1] == "going on"
