# pyright: reportPrivateUsage=false
"""A monster attacking our pet is an attacker of ours, and the player fights it.

Our pet's id used to come only from its name, "<Kind> <name> belongs to <us>", matched exactly
(the game builds the name from parts and spaces it oddly; a translated game words it its own way):
when the match failed, a monster biting the pet was nobody's attacker and the player farmed on
beside it. Now the pet list's id counts too, and a fight on a monster that attacks neither us nor
the pet gives way to the pet's attacker (the farm loop takes attackers first)."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_fighting import Bot, Clock, Link, monster, straight_walk
from unclefarmer.clock import CLOCK
from unclefarmer.engine import pets
from unclefarmer.engine.botparts.fighting import _Fight

ME = 4242
PET = 10001

@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The fighting tests' pretend clock (test_fighting.clock), here as this module's own fixture."""
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c



def pet_entity(name: str = "Wild Eagle Sky belongs to Hero") -> dict[str, Any]:
    return {"id": PET, "kind": "player", "name": name, "x": 101.0, "y": 100.0, "hp": 5000, "maxhp": 9000}


def caster_bot(casts: list[dict[str, Any]], entities: list[dict[str, Any]] | None = None) -> Bot:
    b = Bot(link=Link(json=lambda line: {"casts": casts} if line.startswith("casts") else {}),
            entities=entities or [], me={"id": ME, "name": "Hero", "x": 100.0, "y": 100.0, "hp": 100, "maxhp": 100})
    b.__dict__["_snap_self"] = b.self_
    b._attackers_at = 0.0
    return b


def bite(caster: int, on: int = PET) -> dict[str, Any]:
    return {"t": 1, "caster": caster, "seq": 1, "skill": "Level 1 Bite", "hits": [[on, 3, 400]],
            "targets": [[on, 4600]]}


def test_our_pets_are_known_by_the_pet_list_id_whatever_their_name() -> None:
    b = Bot()
    b.pet_out = {"id": PET, "name": "Wild Eagle>><< Sky", "usable_at": -1}
    snap = {"self": {"name": "Hero"}, "entities": [pet_entity("Wild Eagle Sky ka malik Hero")]}
    assert pets.ours(as_bot(b), snap) == {PET}                     # (a translated "belongs to")


def test_an_oddly_spaced_name_is_ours_too() -> None:
    b = Bot()
    snap = {"self": {"name": "Hero"}, "entities": [pet_entity("Wild Eagle  Sky belongs to  Hero")]}
    assert pets.ours(as_bot(b), snap) == {PET}


def test_someone_elses_pet_is_not_ours() -> None:
    b = Bot()
    snap = {"self": {"name": "Hero"}, "entities": [pet_entity("Wild Eagle Sky belongs to Heroine")]}
    assert pets.ours(as_bot(b), snap) == set()


def test_a_monster_biting_the_pet_is_an_attacker_once_kills_are_counted(clock: Clock) -> None:
    b = caster_bot([bite(-7)], [pet_entity("Wild Eagle Sky ka malik Hero")])
    b.pet_out = {"id": PET, "name": "Wild Eagle>><< Sky", "usable_at": -1}
    b.agent_version = 44
    b.count_kills(b.snap())                                 # (the farm loop does this every look)
    assert b.attackers() == {-7} and b.pet_attackers() == {-7}


def test_a_bite_on_the_pet_counts_from_its_hit_report_too(clock: Clock) -> None:
    cast = bite(-7)
    del cast["targets"]                                     # an agent before 44: hits only
    b = caster_bot([cast], [pet_entity()])
    b.agent_version = 40
    b.count_kills(b.snap())
    assert -7 in b.attackers() and b.pet_attackers() == {-7}


def test_a_monster_busy_with_someone_else_is_no_attacker(clock: Clock) -> None:
    b = caster_bot([bite(-7, on=5555)], [pet_entity()])
    b.agent_version = 44
    b.count_kills(b.snap())
    assert b.attackers() == set() and b.pet_attackers() == set()


def fight_on(b: Bot, mob: dict[str, Any]) -> _Fight:
    f = _Fight(b, mob)
    f.look = b.snap()
    f.seen = next(e for e in f.look["entities"] if e["id"] == mob["id"])
    return f


def test_a_fight_on_a_bystander_gives_way_to_the_pets_attacker(clock: Clock) -> None:
    calm, biter = monster(-3, x=110.0, hp=800, maxhp=900), monster(-7, x=103.0, name="Hyena")
    b = caster_bot([bite(-7)], [calm, biter, pet_entity()])
    b.agent_version = 44
    straight_walk(b)
    b.count_kills(b.snap())
    assert b._pet_needs_us(fight_on(b, calm)) is True
    assert any("Hyena is attacking our pet - fighting it first" in line for line in b.lines)
    # and the farm loop's next pick is that one (attackers first)
    assert b.targets(b.snap())[0]["id"] == -7


def test_the_fight_goes_on_when_its_monster_attacks_us_or_is_nearly_dead(clock: Clock) -> None:
    calm, biter = monster(-3, x=110.0, hp=800, maxhp=900), monster(-7, x=103.0, name="Hyena")
    b = caster_bot([bite(-7), bite(-3, on=ME)], [calm, biter, pet_entity()])
    b.agent_version = 44
    straight_walk(b)
    b.count_kills(b.snap())
    assert b._pet_needs_us(fight_on(b, calm)) is False      # it attacks us: finish it
    low = monster(-3, x=110.0, hp=100, maxhp=900)
    b2 = caster_bot([bite(-7)], [low, biter, pet_entity()])
    b2.agent_version = 44
    straight_walk(b2)
    b2.count_kills(b2.snap())
    assert b2._pet_needs_us(fight_on(b2, low)) is False     # nearly dead: finish it first


def test_no_pet_no_change(clock: Clock) -> None:
    calm, other = monster(-3, x=110.0), monster(-7, x=103.0)
    b = caster_bot([bite(-7, on=5555)], [calm, other])
    b.agent_version = 44
    straight_walk(b)
    b.count_kills(b.snap())
    assert b._pet_needs_us(fight_on(b, calm)) is False
