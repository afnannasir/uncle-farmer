"""Pausing a run: keys let go, the player's own modifiers and the game's speech back in the game,
and on going on the walk's keys down again with its clocks counting from then, and the game muted
again if the run had muted it."""

from __future__ import annotations

import threading
import time

from unclefarmer.engine.botparts.keys import KeysMixin
from unclefarmer.engine.botparts.reading import ReadingMixin


class Agent:
    def __init__(self, speech: str="on") -> None:
        self.sent: list[str] = []
        self.speech = speech

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

    def json(self, line: str) -> dict[str, bool]:
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
