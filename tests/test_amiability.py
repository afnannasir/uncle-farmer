"""The pet's amiability as the bot reads it, from the points key's real answers.

Recorded from the game (mwagent packet logs): every class answers the points key (T) with
"points;fusion[;amiability]" - 43,670 replies with a pet out and 12,599 without, to the one-byte
request; a Priest's two-byte request gets the same layout with its two counts first ("s0 e1;
79.3"). So the amiability is the THIRD field for a Priest and for every other class alike; the
second is always the fusion power (a Priest's reply's 81.3 matched its character sheet's fusion
power of 82.7 read seconds later). Reading the second field as the amiability would take a fusion
power of 100 for a contented pet and never feed it. These pin that, per class, through the agent's
`mem self` points as Points.java writes them, and the Rice Roll that follows."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_pets import Bot, snap
from unclefarmer.engine import pets


def points(reply: str) -> dict[str, Any]:
    """`mem self` points for a reply, as Points.json writes them (raw, n, fusion, pet, age_ms)."""
    fields = [f.strip() for f in reply.split(";")]
    out: dict[str, Any] = {"raw": fields[0], "fusion": float(fields[1]), "age_ms": 100}
    if fields[0].isdigit():
        out["n"] = int(fields[0])
    if len(fields) > 2:
        out["pet"] = float(fields[2])
    return out


class Book:
    ok = True

    def __init__(self, cls: str) -> None:
        self.cls = cls

    def healer(self) -> None:
        return None


@pytest.mark.parametrize("cls,reply,want", [
    ("Priest", "s0 e1; 79.3; 64.5", 64.5),
    ("Witcher", "0;100.0;55.0", 55.0),
    ("Warrior", "300;61.0;69.9", 69.9),
    ("Hunter", "2;100.0;8.5", 8.5),
    ("Mage", "0;100.0;71.2", 71.2),
])
def test_the_amiability_is_the_third_field_for_every_class(cls: str, reply: str, want: float) -> None:
    b = Bot()
    b.__dict__["skillbook"] = Book(cls)
    s = snap(hp=100000)
    s["self"]["points"] = points(reply)
    pet = pets.entity(as_bot(b), s)
    assert pet is not None
    n = pets.numbers(as_bot(b), s, pet)
    assert n["amiability"] == want
    assert n["amiability"] != s["self"]["points"]["fusion"]


@pytest.mark.parametrize("reply", ["s0 e0; 81.3", "0;100.0"])
def test_no_pet_value_means_no_amiability_not_the_fusion_power(reply: str) -> None:
    b = Bot()
    s = snap(hp=100000)
    s["self"]["points"] = points(reply)
    pet = pets.entity(as_bot(b), s)
    assert pet is not None
    assert pets.numbers(as_bot(b), s, pet)["amiability"] is None


@pytest.mark.parametrize("cls,reply", [("Priest", "s0 e1; 79.3; 64.5"), ("Warrior", "300;61.0;64.5")])
def test_a_low_amiability_feeds_a_rice_roll_for_a_priest_and_the_others(cls: str, reply: str) -> None:
    b = Bot(hotkeys={"Pet Rice Roll": "7"})
    b.__dict__["skillbook"] = Book(cls)
    b.p["pet_food_below"] = 70
    s = snap(hp=126000)                     # full HP: nothing else to do first
    s["self"]["points"] = points(reply)
    pets.look_after(as_bot(b), s)
    assert "7" in b.pressed
    assert any("pet amiability 64.5" in line for line in b.lines)
