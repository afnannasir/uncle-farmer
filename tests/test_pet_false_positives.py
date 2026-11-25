# pyright: reportPrivateUsage=false
"""Regressions for pet false positives in kill tracking, doomed checks, and 'taken by someone else' logs.

Monsters our pet is fighting or that are locked on to our pet (with no other player claiming
them) must stay tracked across hits, out-of-sight snapshots, approach, and fight completion.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import ME, as_bot
from test_fighting import Bot as FightBot, Clock as FightClock, Link as FightLink, monster
from test_pet_defence import PET, bite, caster_bot, pet_entity
from test_wrong_target import Clock as WrongClock, Fighter as WrongFighter
from unclefarmer.clock import CLOCK
from unclefarmer.engine import pets
from unclefarmer.engine.kills import KillTracker


def test_pet_kill_via_hits_or_after_ten_seconds_is_counted() -> None:
    a = FightLink()
    now_ms = 0
    casts: list[dict[str, Any]] = []
    deaths: list[list[int]] = []

    def json_reply(line: str) -> Any:
        since = int(line.split()[1])
        return {"now": now_ms, "casts": [row for row in casts if row["t"] > since],
                "deaths": [d for d in deaths if d[1] > since]}

    a.json = json_reply
    k = KillTracker(a)
    k.own = {PET}
    assert k.update(ME) == []                     # prime KillTracker baseline at t=0
    now_ms = 12000
    casts.append({"t": 1000, "caster": PET, "seq": 1, "skill": "Claw", "hits": [[-8, 3, 250]]})
    assert k.update(ME) == []
    assert k.pets_alone(-8)                      # recorded from pet's hits even without targets

    # 15 seconds later (> STRUCK_KEEP 10 s, still well within KEEP 60 s), monster dies to pet's hit
    deaths.append([-8, 16000])
    assert k.update(ME) == [-8]


def test_ours_keeps_seen_pet_when_temporarily_out_of_sight() -> None:
    b = FightBot()
    b.last_me = {"id": ME, "name": "Jorvanthor"}
    b.self_ = {"id": ME, "name": "Jorvanthor", "x": 100.0, "y": 100.0}
    pet = pet_entity("Beetle belongs to Jorvanthor")
    snap_with_pet: dict[str, Any] = {"map": {"id": 5141}, "self": dict(b.self_), "entities": [pet]}
    snap_without_pet: dict[str, Any] = {"map": {"id": 5141}, "self": dict(b.self_), "entities": []}
    assert pets.ours(as_bot(b), snap_with_pet) == {PET}
    # Unticked pet walks out of sight for a moment: must still be recognized as ours
    assert PET in pets.ours(as_bot(b), snap_without_pet)


def test_approach_does_not_call_pet_attacked_or_attacker_doomed(monkeypatch: pytest.MonkeyPatch) -> None:
    c = WrongClock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    monkeypatch.setattr(CLOCK, "sleep", c.sleep)
    monkeypatch.setattr(CLOCK, "wait", c.wait)
    frog = {"id": -5, "kind": "monster", "side": -2, "name": "green bullfrog",
            "x": 130.0, "y": 100.0, "hp": 600, "maxhp": 1000}
    pet = pet_entity("Beetle belongs to Jorvanthor")
    b = WrongFighter([frog, pet], {"id": ME, "name": "Jorvanthor", "x": 100.0, "y": 100.0})
    b.count_kills = lambda snap=None: []         # type: ignore[assignment]
    for hp in (1000, 800, 600):
        frog["hp"] = hp
        b.watch_hp(b.snap())
        c.now += 0.5
    b._attackers = {-5}
    b._pet_attackers = {-5}
    assert b.doomed(frog, b.self_) is False


def test_taken_does_not_blame_someone_else_when_our_pet_was_fighting_or_locked_on(
        monkeypatch: pytest.MonkeyPatch) -> None:
    c = FightClock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    frog = monster(-5, x=120.0, name="green bullfrog")
    b = caster_bot([bite(-5)], [pet_entity("Beetle belongs to Jorvanthor")])
    b.agent_version = 44
    b.pet_out = {"id": PET, "name": "Beetle", "usable_at": -1}
    b.kill_tracker = KillTracker(b.a)
    b.kill_tracker.live = True
    b.kill_tracker.own = {PET}
    b.kill_tracker.pet_struck[-5] = int(c.now * 1000)
    b._pet_attackers = {-5}

    snap = {"map": {"id": 5141}, "self": dict(b.self_), "entities": [pet_entity("Beetle belongs to Jorvanthor")]}
    b._taken(snap, frog)
    assert not any("taken by someone else" in line for line in b.lines)


def test_fight_over_does_not_blame_someone_else_when_our_pet_was_fighting_or_locked_on(
        monkeypatch: pytest.MonkeyPatch) -> None:
    c = FightClock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    frog = monster(-5, x=105.0, name="green bullfrog")
    b = caster_bot([bite(-5)], [])
    b.agent_version = 44
    b.kill_tracker = KillTracker(b.a)
    b.kill_tracker.live = True
    b.kill_tracker.own = {PET}
    b.kill_tracker.pet_struck[-5] = int(c.now * 1000)
    b._pet_attackers = {-5}
    b.snap = lambda: {"map": {"id": 5141}, "self": dict(b.self_), "entities": []}
    b._hit_until_down(frog)
    assert not any("taken by someone else" in line for line in b.lines)
