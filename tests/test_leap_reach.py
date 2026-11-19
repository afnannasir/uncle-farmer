"""Leaps to reach a monster (Phase 1b item 5): Leap Slash was only pressed with the Powerful Leap
Attack talent, and then in reach; without it the Warrior never used it. Now a monster past melee
reach but inside the leap's 10 yards is jumped to instead of walked to."""
from __future__ import annotations

from typing import Any

import pytest
from conftest import Bot as BookBot
from conftest import Game, as_parts
from test_fighting import Bot, Clock, monster, straight_walk

from unclefarmer.engine import combat
from unclefarmer.engine.botparts import fighting


def book(clock: Clock, skills: list[str], profile: dict[str, Any] | None = None) -> combat.SkillBook:
    return combat.SkillBook(as_parts(BookBot(Game(clock, skills), profile)))


def test_which_skills_are_leaps_to_the_target() -> None:
    info = {x["name"]: x for cls in combat.knowledge().values() for x in cls}
    assert combat.SkillBook.leap_skill(info["Leap Slash"])
    assert not combat.SkillBook.leap_skill(info["Leap Gouge"])    # a 2.5 s channel: not without a recording
    assert not combat.SkillBook.leap_skill(info["Escape"])        # jumps backward
    assert not combat.SkillBook.leap_skill(info["Wind Step"])


def test_leap_for_needs_the_monster_inside_its_range_and_the_skill_ready(clock: Clock) -> None:
    sb = book(clock, ["Bash", "Leap Slash"])
    leap = sb.leap_for(8.0, clock.now)
    assert leap is not None and leap["name"] == "Leap Slash"
    assert sb.leap_for(9.8, clock.now) is None                    # past range less the margin
    press = sb.press_leap(leap, clock.now)
    assert press is not None and press[1] == "Leap Slash"
    sb.used["Leap Slash"] = clock.now                              # the game cast it
    assert sb.leap_for(8.0, clock.now + 20) is None                # 30 s cooldown
    assert sb.leap_for(8.0, clock.now + 31) is not None


def test_never_keeps_it_off(clock: Clock) -> None:
    assert book(clock, ["Bash", "Leap Slash"], {"skill_use": {"Leap Slash": "never"}}).leap_for(8.0, clock.now) is None
    assert book(clock, ["Bash"]).leap_for(8.0, clock.now) is None


class LeapBook:
    """A skill book with one ready Leap Slash (range 10)."""
    ok = True

    def __init__(self) -> None:
        self.pressed: list[str] = []

    def leap_for(self, dist: float, now: float) -> dict[str, Any] | None:
        if self.pressed or dist > 10 - combat.LEAP_MARGIN:
            return None
        return {"name": "Leap Slash", "key": "5", "page": 1}

    def press_leap(self, x: dict[str, Any], now: float) -> tuple[str, str]:
        self.pressed.append(x["name"])
        return x["key"], x["name"]


def leap_bot(x: float, monkeypatch: pytest.MonkeyPatch) -> Bot:
    b = Bot(entities=[monster(-1, x=x)])
    straight_walk(b)
    b.skillbook = LeapBook()
    b.clear_shot = lambda me, mob, mid=None: True
    b.dialog = lambda: {}
    monkeypatch.setattr(fighting.beasts, "owned", lambda bot: [])
    return b


def test_a_monster_a_few_steps_past_reach_is_leapt_to(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    b = leap_bot(108.0, monkeypatch)                   # 8 yards off, reach 5

    def land(k: str, ms: int | None = None) -> None:
        b.keys_pressed.append(k)
        b.self_["x"] = 107.0                            # Leap Slash lands 1.0 tile from the target
    b.key = land
    got = b._leap_in(b.snap()["entities"][0], 5.0)
    assert b.keys_pressed == ["5"] and got["id"] == -1
    assert any("Leap Slash to reach Wolf" in line for line in b.lines)


def test_no_leap_in_reach_past_range_or_on_a_beast(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    for x in (105.5, 110.0):                            # within reach + a step; past 10 less the margin
        b = leap_bot(x, monkeypatch)
        b._leap_in(b.snap()["entities"][0], 5.0)
        assert b.keys_pressed == [], x
    b = leap_bot(108.0, monkeypatch)
    monkeypatch.setattr(fighting.beasts, "owned", lambda bot: [{"active": True}])
    b._leap_in(b.snap()["entities"][0], 5.0)
    assert b.keys_pressed == []
    b = leap_bot(108.0, monkeypatch)
    b.walk_only = True                                 # the walking assistant never fights
    assert not b.leap_ready(b.me(), b.snap()["entities"][0], 5.0)


def test_the_walk_up_stops_where_the_leap_reaches(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    b = leap_bot(108.0, monkeypatch)
    b.shoots_moving = lambda: False
    b.downed = lambda mid, since: False
    b.doomed = lambda m, me, steps=None, rng=None: False
    b.watch_hp = lambda snap: None
    way = fighting._Approach(b, -1, 5.0, 5.0, 0.0)
    assert way.stop(b.me()) and way.leap
    assert b._approach(b.snap()["entities"][0], fighting._Approach(b, -1, 5.0, 5.0, 0.0))["id"] == -1
    assert b.walked == []                              # no walk: the fight leaps from here
