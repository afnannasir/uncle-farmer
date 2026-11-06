# pyright: reportPrivateUsage=false
# White-box tests: they read the fight's helpers (_pet_needs_us, _attackers_at) directly.
"""The pet's HP falling fast is an attack, even with no cast report naming the pet: the monsters
right next to it become attackers (fought first) until its HP has stopped falling for a while.
Missing, late or odd readings never make up a threat."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_fighting import Bot, Clock, Link, monster, straight_walk
from test_pet_defence import ME, PET, fight_on, pet_entity
from unclefarmer.clock import CLOCK
from unclefarmer.engine import pets


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


def pet(hp: float, x: float = 101.0, pid: int = PET) -> dict[str, Any]:
    e = pet_entity()
    e.update(hp=hp, x=x, id=pid)
    return e


def world(*entities: dict[str, Any]) -> dict[str, Any]:
    return {"self": {"id": ME, "name": "Hero", "x": 100.0, "y": 100.0}, "entities": list(entities)}


def bot() -> Bot:
    """A fighting bot whose agent reports no casts at all: only the pet's HP can tell."""
    b = Bot(link=Link(json=lambda line: {"casts": []} if line.startswith("casts") else {}),
            me={"id": ME, "name": "Hero", "x": 100.0, "y": 100.0, "hp": 100, "maxhp": 100})
    b.__dict__["_snap_self"] = b.self_
    b._attackers_at = 0.0
    b.pet_out = {"id": PET, "name": "Wild Eagle>><< Sky", "usable_at": -1}
    return b


def feed(b: Bot, clock: Clock, readings: list[tuple[float, float]], *others: dict[str, Any]) -> None:
    """The pet's HP at each of those seconds after now, with these monsters about."""
    start = clock.now
    for at, hp in readings:
        clock.now = start + at
        pets.watch(as_bot(b), world(pet(hp), *others), pet(hp), clock.now)


def test_a_fast_fall_makes_the_monster_next_to_the_pet_an_attacker(clock: Clock) -> None:
    b = bot()
    near, far = monster(-7, x=102.5, name="Hyena"), monster(-9, x=115.0)
    feed(b, clock, [(0, 9000), (1, 8700), (2, 8200)], near, far)          # 800 of 9000 in 2 s
    assert pets.suspects(as_bot(b)) == {-7}
    assert -7 in b.attackers() and -7 in b.pet_attackers() and -9 not in b.attackers()
    assert sum("is losing HP fast" in line for line in b.lines) == 1


def test_a_slow_fall_is_no_attack(clock: Clock) -> None:
    b = bot()
    # 50 of 9000 a second: 150 (1.7%) in the 3-second window, under the 2% mark
    feed(b, clock, [(0, 9000), (1, 8950), (2, 8900), (3, 8850), (4, 8800)], monster(-7, x=102.0))
    assert pets.suspects(as_bot(b)) == set() and b.attackers() == set()


def test_two_percent_lost_is_already_an_attack(clock: Clock) -> None:
    b = bot()
    feed(b, clock, [(0, 9000), (1, 8820)], monster(-7, x=102.0))         # 180 of 9000: 2%
    assert pets.suspects(as_bot(b)) == {-7}
    b2 = bot()
    feed(b2, clock, [(0, 9000), (1, 8830)], monster(-7, x=102.0))        # 170: just under
    assert pets.suspects(as_bot(b2)) == set()


def test_the_threat_lasts_while_hp_falls_and_ends_after_the_hold(clock: Clock) -> None:
    b = bot()
    biter = monster(-7, x=102.0, name="Hyena")
    feed(b, clock, [(0, 9000), (1, 8000)], biter)
    assert pets.suspects(as_bot(b)) == {-7}
    feed(b, clock, [(2, 7000)], biter)                                    # still falling: held on
    feed(b, clock, [(pets.THREAT_HOLD - 0.5, 7000)], biter)
    assert pets.suspects(as_bot(b)) == {-7}
    feed(b, clock, [(1.0, 7000)], biter)                                  # quiet for longer than the hold
    assert pets.suspects(as_bot(b)) == set()
    assert any("no longer losing HP" in line for line in b.lines)
    feed(b, clock, [(1, 7000), (2, 6000)], biter)                         # a new attack is said again
    assert sum("is losing HP fast" in line for line in b.lines) == 2


def test_a_monster_that_walks_away_from_the_pet_stops_being_a_suspect(clock: Clock) -> None:
    b = bot()
    feed(b, clock, [(0, 9000), (1, 8000)], monster(-7, x=102.0))
    feed(b, clock, [(0.5, 7900)], monster(-7, x=110.0))
    assert pets.suspects(as_bot(b)) == set()


@pytest.mark.parametrize("gap", ["out of sight", "no hp", "late", "another pet"])
def test_missing_or_late_readings_never_make_a_threat(clock: Clock, gap: str) -> None:
    b = bot()
    near = monster(-7, x=102.0)
    feed(b, clock, [(0, 9000)], near)
    clock.now += 1
    if gap == "out of sight":
        pets.watch(as_bot(b), world(near), None, clock.now)
    elif gap == "no hp":
        odd = pet(9000)
        del odd["hp"]
        pets.watch(as_bot(b), world(odd, near), odd, clock.now)
    elif gap == "late":
        clock.now += pets.READING_GAP + 1
    else:
        other = pet(1000, pid=PET + 1)                                    # a fresh pet with less HP
        pets.watch(as_bot(b), world(other, near), other, clock.now)
    clock.now += 1
    low = pet(5000, pid=PET + 1 if gap == "another pet" else PET)
    pets.watch(as_bot(b), world(low, near), low, clock.now)
    assert pets.suspects(as_bot(b)) == set()      # the readings started afresh: nothing to compare


def test_a_dead_pet_starts_no_new_threat(clock: Clock) -> None:
    b = bot()
    feed(b, clock, [(0, 9000), (1, 0)], monster(-7, x=102.0))
    assert pets.suspects(as_bot(b)) == set()


def test_no_pet_chosen_means_no_watch(clock: Clock) -> None:
    b = bot()
    b.p["pets"] = []
    snap = world(pet(9000), monster(-7, x=102.0))
    pets.look_after(as_bot(b), snap)
    assert getattr(b, "pet_hp_log", []) == []


def test_the_fight_on_a_bystander_gives_way_to_the_pets_unseen_attacker(clock: Clock) -> None:
    calm, biter = monster(-3, x=110.0, hp=800, maxhp=900), monster(-7, x=102.5, name="Hyena")
    b = bot()
    b.entities = [calm, biter, pet(9000)]
    b.agent_version = 44
    straight_walk(b)
    feed(b, clock, [(0, 9000), (1, 8000)], calm, biter)
    b._attackers_at = 0.0
    assert b._pet_needs_us(fight_on(b, calm)) is True
    assert any("Hyena is attacking our pet - fighting it first" in line for line in b.lines)
    assert b.targets(b.snap())[0]["id"] == -7                             # the farm loop's next pick


def test_a_monster_hitting_us_is_finished_before_the_pets_unseen_attacker(clock: Clock) -> None:
    b = bot()
    feed(b, clock, [(0, 9000), (1, 8000)], monster(-7, x=102.0))
    calm = monster(-7, x=102.0, hp=800, maxhp=900)
    b.entities = [calm, pet(8000)]
    straight_walk(b)
    assert b._pet_needs_us(fight_on(b, calm)) is False                    # it is the pet's attacker itself


def test_a_strike_on_us_marks_when_we_were_last_attacked(clock: Clock) -> None:
    struck = {"t": int((clock.now - 1.5) * 1000), "caster": -7, "seq": 1, "skill": "Level 1 Bite",
              "hits": [[ME, 3, 400]], "targets": [[ME, 60]]}
    b = Bot(link=Link(json=lambda line: {"casts": [struck]} if line.startswith("casts") else {}),
            me={"id": ME, "name": "Hero", "x": 100.0, "y": 100.0, "hp": 100, "maxhp": 100})
    b.__dict__["_snap_self"] = b.self_
    b._attackers_at = 0.0
    b.agent_version = 44
    assert b.attackers() == {-7}
    assert b.last_attacked == pytest.approx(clock.now - 1.5)     # the cast's own time, not the reading's
