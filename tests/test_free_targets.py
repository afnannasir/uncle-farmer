"""A target another player stands nearer to comes after a free one about as far (RIVAL_STEPS).

Measured live (Underwater World, an hour): a target with another player nearer to it than we were
was lost to someone else 23% of the time, the others 10%. Such a target is never left out - most
are still ours - only put after a free one up to RIVAL_STEPS further away. Pets and summons are no
players, and a monster attacking us is fought first whoever stands near it.
"""

from __future__ import annotations

from typing import Any

from conftest import ME
from test_bosses import mob
from test_wrong_target import Fighter

from unclefarmer.engine.botparts.fighting import FightingMixin


def player(pid: int, x: float, y: float = 100.0, name: str = "Bob") -> dict[str, Any]:
    return {"id": pid, "kind": "player", "name": name, "x": x, "y": y, "hp": 1000, "maxhp": 1000}


def fighter(*ents: dict[str, Any]) -> Fighter:
    b = Fighter(list(ents), {"id": ME, "x": 100.0, "y": 100.0})
    b.walk_dist = lambda snap, e, limit=None: abs(e["x"] - 100.0)
    return b


def order(b: Fighter) -> list[int]:
    return [e["id"] for e in b.targets(b.snap())]


def test_a_free_target_about_as_far_comes_first() -> None:
    # the wolf is 30 steps east with Bob 5 from it; the boar 40 steps west, nobody near it
    b = fighter(mob(-1, "Wolf", 130.0), mob(-2, "Boar", 60.0), player(7, 135.0))
    assert order(b) == [-2, -1]
    assert b.contested == {-1}


def test_a_free_target_much_further_doesnt() -> None:
    # the free boar is west, further than the wolf by more than RIVAL_STEPS
    b = fighter(mob(-1, "Wolf", 130.0), mob(-2, "Boar", 100.0 - (30 + FightingMixin.RIVAL_STEPS + 5)), player(7, 135.0))
    assert order(b) == [-1, -2]                  # never left out, and nearer by more than the steps
    assert b.contested == {-1}


def test_a_player_further_from_the_target_than_we_are_changes_nothing() -> None:
    b = fighter(mob(-1, "Wolf", 130.0), mob(-2, "Boar", 140.0), player(7, 200.0))
    assert order(b) == [-1, -2] and b.contested == set()


def test_pets_and_ourselves_are_no_players() -> None:
    b = fighter(mob(-1, "Wolf", 130.0), mob(-2, "Boar", 140.0),
                player(8, 131.0, name="Sprite Bubby belongs to Bob"),
                player(ME, 129.0, name="Hero"))
    assert order(b) == [-1, -2]


def test_one_attacking_us_comes_first_whoever_is_near_it() -> None:
    b = fighter(mob(-1, "Wolf", 130.0), mob(-2, "Boar", 140.0), player(7, 135.0))
    b.attackers = lambda: {-1}
    assert order(b)[0] == -1 and -1 not in b.contested
