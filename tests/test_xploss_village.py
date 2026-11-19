"""Tools, XP loser, dying at a village (engine/xploss.py, where "village"): the game brings a dead
character back at the last town it walked into, so the bot walks in first; a village's gate guards
are a few steps from there - so each death is quicker than at a city, whose guards stand far out past its streets."""

from __future__ import annotations

from collections.abc import Collection
from typing import Any

import pytest

from test_xploss import CITY, REAL_SAFE_ZONES, Game, guard, lose
from test_xploss import isolated as isolated   # the same pretend world (autouse)
from unclefarmer.engine import xploss

VILLAGE: xploss.Village = (CITY, "Hill Village", 90, 90, 110, 110)
GATE = (100, 113)                 # just outside its south side
MIDDLE = (100, 100)               # its middle (no map data in the tests)
RIDES: list[Any] = []             # beasts.ensure() calls


@pytest.fixture
def one_village(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """One village on CITY, its south gate at GATE; the nearest of the candidates is the first."""
    asked: list[Any] = []
    def villages(skip: Collection[tuple[int, int, int]] = ()) -> list[xploss.Village]:
        return [v for v in [VILLAGE] if (v[0], v[2], v[3]) not in skip]

    def edges(mid: int, zone: tuple[int, int, int, int], me: tuple[float, float],
              comp: int | None = None) -> list[tuple[int, int]]:
        return [GATE]
    monkeypatch.setattr(xploss, "villages", villages)
    monkeypatch.setattr(xploss, "_edges", edges)

    def nearest(b: Any, role: str | None, candidates: Any = None, back_to: Any = None) -> Any:
        asked.append(candidates)
        return candidates[0] if candidates else None
    monkeypatch.setattr(xploss.town, "nearest", nearest)

    def ensure(b: Any, purpose: Any = None) -> None:
        RIDES.append(purpose)
    monkeypatch.setattr(xploss.beasts, "ensure", ensure)
    RIDES.clear()
    return asked


def far_away(g: Game) -> None:
    """Start on another map, with the village's gate guard in sight once we get there."""
    g.mid, g.x, g.y = 1561, 10.0, 10.0
    g.entities = []
    orig = g.travel

    def travel(dst: int, cell: Any = None) -> None:
        orig(dst, cell)
        if dst == CITY:
            g.entities = [guard(-40, x=101.0, y=114.0)]
    g.travel = travel


def test_dies_at_the_nearest_village_and_stays_there(one_village: list[Any]) -> None:
    g = Game(xp=1000, loss=500)
    far_away(g)
    assert lose(g, until_needed=2500, where="village") == "done"
    assert g.deaths == 3
    assert g.travelled == [(CITY, MIDDLE)]             # one trip there, into it: each respawn is there
    assert "dying at Hill Village" in g.text()
    assert g.text().count("walking into Hill Village on ") == 1
    assert one_village and one_village[0] == [(CITY, "Village", 100.0, 100.0)]


def test_a_respawn_somewhere_else_goes_back_to_the_nearest_guard(one_village: list[Any]) -> None:
    g = Game(xp=1000, loss=500)
    far_away(g)
    orig = g.handle_death

    def handle_death() -> None:
        g.x, g.y = 300.0, 300.0                        # where the game puts us is the game's choice
        try:
            orig()
        finally:
            g.x, g.y = 300.0, 300.0
    g.handle_death = handle_death
    g.entities.append(guard(-41, x=301.0, y=300.0))
    lose(g, until_needed=2500, where="village")
    assert "not at Hill Village on " in g.text() and "dying at the nearest guard instead" in g.text()
    assert g.deaths == 3


def test_a_village_without_guards_falls_back_to_the_nearest_guard(one_village: list[Any]) -> None:
    g = Game()
    g.mid, g.entities = 1561, [guard(-41, x=110.0, y=100.0)]   # a guard here, none at the village
    orig = g.travel

    def travel(dst: int, cell: Any = None) -> None:
        orig(dst, cell)
        g.entities = []
    g.travel = travel
    seen_here = list(g.entities)

    def back(dst: int, cell: Any = None) -> None:
        travel(dst, cell)
        if dst == 1561:
            g.entities = seen_here
    g.travel = back
    xploss.note(1561, seen_here)
    assert lose(g, until_needed=1500, where="village") == "done"
    text = g.text()
    assert "no guard to fight at Hill Village on " in text and "trying another village" in text
    assert "no village with a guard to fight nearby - dying at the nearest guard instead" in text
    assert g.deaths == 1


def test_the_village_is_the_default_and_the_guard_still_a_choice() -> None:
    assert xploss.DEFAULTS["where"] == "village"
    g = Game()
    assert lose(g, until_needed=1500) == "done"
    assert "dying at" not in g.text()


def test_villages_from_the_map_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xploss.atlas, "safe_zones", REAL_SAFE_ZONES)
    found = xploss.villages()
    names = {(v[0], v[1]) for v in found}
    assert (1081, "Aria Village") in names and (1081, "Tucana Village") in names
    assert (1631, "Orchid woods Village") in names            # 89 x 90: the biggest village
    assert not any(v[1] == "Tulip City" for v in found)       # a city, not a village
    assert not any(v[0] == 1081 and v[2] == 381 for v in found)   # a square inside Tulip City
    assert not any(v[0] == 1001 for v in found)               # Karon Village's walls: strips, no village
    assert all(xploss.VILLAGE_MIN <= v[4] - v[2] <= xploss.VILLAGE_MAX for v in found)
    aria = next(v for v in found if v[1] == "Aria Village")
    assert (aria[2], aria[3]) not in {(v[2], v[3]) for v in xploss.villages({(1081, aria[2], aria[3])}) if v[0] == 1081}


def test_village_names_and_distances() -> None:
    assert xploss.village_name(VILLAGE) == "Hill Village on %s" % xploss.atlas.label(CITY)
    assert xploss.village_name((CITY, "", 90, 90, 110, 110)).startswith("the village at 100,100 on ")
    assert xploss.off_village(VILLAGE, 100, 100) == 0
    assert xploss.off_village(VILLAGE, 100, 113) == 3
    assert xploss.off_village(VILLAGE, 113, 114) == 5


def test_the_window_choice_is_saved_in_the_settings() -> None:
    assert xploss.config({"xp_loser": {"where": "village"}})["where"] == "village"
    assert xploss.config({})["where"] == "village"
    assert xploss.config({"xp_loser": {"where": "guard"}})["where"] == "guard"   # a saved choice stays
    assert len(xploss.WHERE) == len(xploss.WHERE_NAMES)
    g = Game()
    assert lose(g, until_needed=1500, where="somewhere") == "done"   # an unknown choice: the guard
    assert "dying at" not in g.text()


def test_walks_into_the_village_before_hitting_a_gate_guard(one_village: list[Any]) -> None:
    """The game brings us back at the last town we walked into: a guard at the gates, in sight on
    the way, is struck only once we have been inside (seen live: dying on the way in brought the
    character back at the city it came from)."""
    g = Game(xp=1000, loss=500)
    g.x, g.y = 100.0, 140.0                            # on the village's map, south of it
    g.entities = [guard(-40, x=101.0, y=114.0)]
    walks: list[tuple[float, float]] = []
    orig = g.walk_to

    def walk_to(cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        walks.append(cell)
        return orig(cell, map_change, near)
    g.walk_to = walk_to
    assert lose(g, until_needed=1500, where="village") == "done"
    assert walks and walks[0] == MIDDLE
    assert g.hit_from and xploss.off_village(VILLAGE, *g.hit_from[0]) > 0
    assert g.travelled == []
    assert RIDES == []                                 # 27 tiles away: walked, not ridden


def test_a_long_way_to_the_village_is_ridden(one_village: list[Any]) -> None:
    g = Game(xp=1000, loss=500)
    g.x, g.y = 100.0, 250.0                            # 140 tiles south of it
    g.entities = [guard(-40, x=101.0, y=114.0)]
    assert lose(g, until_needed=1500, where="village") == "done"
    assert RIDES == [xploss.beasts.TRAVEL]


def test_a_blocked_walk_in_is_tried_again(one_village: list[Any]) -> None:
    """A player or a guard in the doorway: the next walk gets in."""
    g = Game(xp=1000, loss=500)
    g.x, g.y = 100.0, 140.0
    g.entities = [guard(-40, x=101.0, y=114.0)]
    orig = g.walk_to
    blocked = [2]

    def walk_to(cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        if cell == MIDDLE and blocked[0]:
            blocked[0] -= 1
            return "stuck"
        return orig(cell, map_change, near)
    g.walk_to = walk_to
    assert lose(g, until_needed=1500, where="village") == "done"
    assert "couldn't walk into" not in g.text()


def test_a_village_it_cannot_walk_into_is_left(one_village: list[Any]) -> None:
    g = Game()
    g.x, g.y = 100.0, 140.0
    g.entities = [guard(-40, x=101.0, y=114.0)]

    orig = g.walk_to

    def stuck(cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        if cell == MIDDLE:
            return "stuck"                             # a wall in the way, every time
        return orig(cell, map_change, near)
    g.walk_to = stuck
    assert lose(g, until_needed=1500, where="village") == "done"
    text = g.text()
    assert "couldn't walk into Hill Village on " in text and "trying another village" in text
    assert "dying at the nearest guard instead" in text


def test_the_middle_of_a_village() -> None:
    assert xploss.inside_cell(VILLAGE) == MIDDLE      # no map data: its middle
    assert xploss.well_inside(VILLAGE, 100, 100) and xploss.well_inside(VILLAGE, 93, 107)
    assert not xploss.well_inside(VILLAGE, 91, 100) and not xploss.well_inside(VILLAGE, 100, 112)
    aria = (1081, "Aria Village", 31, 31, 66, 66)
    x, y = xploss.inside_cell(aria)                   # the real map: walkable ground inside it
    assert xploss.off_village(aria, x, y) == 0
