"""Who is near us and how our pet is doing, for the status and the "players" / "pet" commands."""

from unclefarmer import nearby
from typing import Any


def world() -> dict[str, Any]:
    me = {"id": 4242, "name": "Hero", "x": 100.0, "y": 100.0,
          "pet_vitals": {"hp": 100000, "mp": 55000, "age_ms": 3000},
          "pet_info": {"name": "Wild Eagle Sky", "maxmp": 110000, "amiability": 80.0}}
    ents = [
        {"id": 2001, "kind": "player", "name": "Sara", "x": 112.0, "y": 100.0},
        {"id": 2002, "kind": "player", "name": "Ali", "x": 103.0, "y": 104.0},
        {"id": 10000001, "kind": "player", "name": "Wild Eagle Sky belongs to Hero", "x": 101.0, "y": 100.0,
         "hp": 60000, "maxhp": 120000},
        {"id": 10000002, "kind": "player", "name": "Shepard Dog Rex belongs to Sara", "x": 113.0, "y": 100.0,
         "hp": 1, "maxhp": 2},
        {"id": -5, "kind": "monster", "name": "Wolf", "x": 101.0, "y": 101.0, "hp": 10, "maxhp": 10},
    ]
    return {"self": me, "entities": ents, "map": {"id": 5121, "name": "Wolf Prairie"}}


def test_players_are_listed_nearest_first_without_pets_or_monsters() -> None:
    assert nearby.players(world()) == [("Ali", 5.0), ("Sara", 12.0)]
    assert nearby.players_text(world()) == "2: Ali 5, Sara 12"
    assert nearby.players_text(world(), most=1) == "2: Ali 5, 1 more"


def test_nobody_in_sight() -> None:
    w = world()
    w["entities"] = []
    assert nearby.players_text(w) == "Nobody in sight"


def test_our_pet_with_hp_and_the_mp_the_bot_read() -> None:
    assert nearby.pet_text(world()) == "Wild Eagle Sky, HP 50%, MP 50%"
    w = world()
    del w["self"]["pet_vitals"]
    assert nearby.pet_text(w) == "Wild Eagle Sky, HP 50%"
    w["entities"] = [e for e in w["entities"] if "Hero" not in e["name"]]
    assert nearby.pet_text(w) == "No pet out"
