# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""The game's "out of range" sound (engine/refusals.py, sound 1023, measured live 2026-11-26): heard
while farming, the bot stops pressing at that monster and the farm loop takes the nearest one."""

from __future__ import annotations

import threading
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import Clock, as_bot, stand_in

from unclefarmer.engine import refusals
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.link import AgentError


class Sounds:
    """The agent's sound answers: `sounds <after>` and `soundlisten <ids>`."""

    def __init__(self, hooked: bool = True) -> None:
        self.events: list[int] = []          # sound ids, in order
        self.listening = ""
        self.hooked = hooked

    def json(self, line: str) -> dict[str, Any]:
        verb, _sp, rest = line.partition(" ")
        if verb == "sounds":
            after = int(rest)
            rows = [{"seq": i + 1, "id": sid, "x": None, "y": None, "t": 0, "muted": False}
                    for i, sid in enumerate(self.events) if i + 1 > after]
            return {"now": len(self.events), "events": rows, "lost": False}
        if verb == "soundlisten":
            self.listening = rest
            return {"hooked": self.hooked, "watch": "off", "mute": "off", "reason": "", "observed": 0, "dropped": 0,
                    "live_muted": 0, "unsupported": 0}
        raise AssertionError(line)


class Bot:
    def __init__(self, link: Any) -> None:
        self.a = link
        self.lines: list[str] = []

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)


def test_the_farm_listens_for_the_out_of_range_sound_only() -> None:
    s = Sounds()
    b = Bot(s)
    refusals.start(as_bot(b))
    assert s.listening == "1023"


def test_a_sound_heard_before_the_farm_began_never_counts() -> None:
    s = Sounds()
    s.events = [1023, 1023]
    b = Bot(s)
    refusals.start(as_bot(b))
    assert refusals.heard(as_bot(b)) is False
    s.events.append(1023)
    assert refusals.heard(as_bot(b)) is True
    assert refusals.heard(as_bot(b)) is False             # each refusal counted once


def test_other_sounds_are_not_refusals() -> None:
    s = Sounds()
    b = Bot(s)
    refusals.start(as_bot(b))
    s.events += [1073, 10413, 1031]
    assert refusals.heard(as_bot(b)) is False


def test_an_agent_without_sounds_changes_nothing() -> None:
    class Old:
        def json(self, line: str) -> dict[str, Any]:
            raise AgentError("bad reply")
    b = Bot(Old())
    refusals.start(as_bot(b))
    assert refusals.heard(as_bot(b)) is False


def test_the_monster_is_named_once_but_every_refusal_is_counted() -> None:
    b = Bot(Sounds())
    refusals.note(as_bot(b), -7, "Thunder Tiger")
    refusals.note(as_bot(b), -7, "Thunder Tiger")
    assert len(b.lines) == 1 and "Thunder Tiger" in b.lines[0]
    assert as_bot(b).__dict__["refusals"] == 2


# ---------------------------------------------------------------- in a fight
class FightBot:
    """Just what _fight_can_press reads before it would press."""

    def __init__(self) -> None:
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.left: list[tuple[int, str, float | None]] = []
        self.lines: list[str] = []

    def leave(self, mid: int, why: str, seconds: float | None = None) -> float:
        self.left.append((mid, why, seconds))
        return 0.0

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)


def test_a_refusal_in_a_fight_stops_the_presses_and_leaves_the_monster_a_moment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(refusals, "heard", stand_in(refusals.heard, lambda b: True))
    b = FightBot()
    f = SimpleNamespace(mid=-87, mob={"name": "Thunder Tiger"}, press_block=None)
    assert FightingMixin._fight_can_press(as_bot(b), f) is False        # type: ignore[arg-type]
    assert f.press_block == "return"
    assert b.left == [(-87, "too far", refusals.REFUSED_FOR)]
    assert any("out of range" in line for line in b.lines)
