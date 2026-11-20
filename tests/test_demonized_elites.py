"""Leave demonized elites alone (skip_demonized_elites, off by default). The game's "demonized"
elites (demonized Boar, black bear, Water Elf...) are much stronger than the monsters around them;
one the same level as the character can kill it, depending on its gear. Ticked, none is ever picked
as a target, but one that attacks us is still fought. The demonized field bosses are bosses."""

from __future__ import annotations

from typing import Any

from conftest import ME
from test_bosses import mob
from test_wrong_target import Fighter

from unclefarmer import settings
from unclefarmer.engine import bosses


def fighter(*mobs: dict[str, Any], skip: bool = True) -> Fighter:
    b = Fighter(list(mobs), {"id": ME, "x": 100.0, "y": 100.0})
    b.p["skip_demonized_elites"] = skip
    b.walk_dist = lambda snap, e, limit=None: abs(e["x"] - 100.0)
    return b


def test_a_demonized_elite_is_not_a_boss() -> None:
    assert bosses.is_demonized_elite(mob(-1, "demonized  Water Elf", 0))
    assert bosses.is_demonized_elite(mob(-1, "Demonized Cat", 0))
    assert not bosses.is_demonized_elite(mob(-1, "Demonized Barbarian Tyrant", 0))    # a field boss
    assert not bosses.is_demonized_elite(mob(-1, "Water Elf", 0))
    assert not bosses.is_demonized_elite({"kind": "player", "name": "demonized Boar"})


def test_ticked_they_are_left_alone_and_said_why() -> None:
    b = fighter(mob(-1, "demonized black bear", 105.0), mob(-2, "Wolf", 150.0))
    assert [e["id"] for e in b.targets(b.snap())] == [-2]
    assert b.left_out == {"elite": 1}


def test_ticked_what_is_close_to_one_is_left_alone_too() -> None:
    """A monster within ELITE_CLEAR steps of the elite: fighting it would pull the elite in."""
    b = fighter(mob(-1, "demonized black bear", 105.0), mob(-2, "Wolf", 130.0), mob(-3, "Boar", 150.0))
    assert [e["id"] for e in b.targets(b.snap())] == [-3]
    assert b.left_out == {"elite": 2}
    b.attackers = lambda: {-2}                     # the wolf strikes us: it is fought
    assert [e["id"] for e in b.targets(b.snap())] == [-2, -3]


def test_unticked_by_default_they_are_fought() -> None:
    b = fighter(mob(-1, "demonized black bear", 105.0), mob(-2, "Wolf", 130.0), skip=False)
    assert [e["id"] for e in b.targets(b.snap())] == [-1, -2]
    assert settings.PROFILE_DEFAULTS["skip_demonized_elites"] is False


def test_one_that_attacks_us_is_still_fought() -> None:
    b = fighter(mob(-1, "demonized black bear", 105.0), mob(-2, "Wolf", 130.0))
    b.attackers = lambda: {-1}
    assert [e["id"] for e in b.targets(b.snap())][0] == -1
