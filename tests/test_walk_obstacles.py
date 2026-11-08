# pyright: reportPrivateUsage=false
"""NPCs and other players are solid: walk_to plans round them from the first try, on the real
map data. The map knows walls but not who stands where; the bot used to plan round NPCs only after
a walk had already stuck on one, and never round players.

The ground is a real open stretch of central plains (1041) with a wall drawn across it (a gate
with two gaps), so the searches are the real ones (C or Python)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import stand_in

from test_walking import MapBot
from unclefarmer import atlas
from unclefarmer.atlaslib import MapModel

MID = 1041
X0, Y0 = 68, 20                     # a 24 x 16 open patch (checked below)
WALL_X = X0 + 12
GAP_A, GAP_B = (WALL_X, Y0 + 3), (WALL_X, Y0 + 12)
START, GOAL = (X0 + 2, Y0 + 3), (X0 + 22, Y0 + 3)


@pytest.fixture
def ground(monkeypatch: pytest.MonkeyPatch) -> MapModel:
    real = atlas.model(MID)
    assert real.walk[Y0:Y0 + 16, X0:X0 + 24].all(), "the open patch moved: pick another"
    wall = {(WALL_X, y) for y in range(0, real.h + 2)} - {GAP_A, GAP_B}     # all the way across
    m = real.without(wall)
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "map %s" % mid))
    return m


class Crowd(MapBot):
    """MapBot on the real ground, with people standing about."""

    def __init__(self, m: Any, people: list[dict[str, Any]]) -> None:
        super().__init__(m, pos=START, map_id=MID)
        self.people = people
        self.walks: list[tuple[float, float]] = []

    def snap(self) -> dict[str, Any]:
        return {"self": {"id": 7, "x": self.pos[0], "y": self.pos[1], "name": "Hero"}, "entities": self.people}


def npc(x: int, y: int) -> dict[str, Any]:
    return {"id": -5, "kind": "npc", "name": "Guard", "x": float(x), "y": float(y), "maxhp": 20000000, "hp": 20000000}


def player(x: int, y: int, pid: int = 900, name: str = "Someone") -> dict[str, Any]:
    return {"id": pid, "kind": "player", "name": name, "x": float(x), "y": float(y), "hp": 100, "maxhp": 100}


def cells_walked(m: MapModel, start: tuple[int, int], hops: list[tuple[int, int]]) -> set[tuple[int, int]]:
    out: set[tuple[int, int]] = set()
    at = start
    for hop in hops:
        for _a, b in m.diag_steps(at, hop):
            out.add(b)
        at = hop
    return out


def plan(b: Crowd, m: MapModel) -> tuple[MapModel, list[tuple[int, int]]]:
    p = b._obstacle_map(m, GOAL, START)
    hops = b._walk_hops(m, p, MID, START, GOAL, True)
    assert hops is not None
    return p, hops


def test_open_ground_with_nobody_about_goes_straight(ground: MapModel) -> None:
    b = Crowd(ground, [])
    p, hops = plan(b, ground)
    assert p is ground and hops == [GOAL]


def test_an_npc_in_the_gap_sends_the_walk_through_the_other_one(ground: MapModel) -> None:
    b = Crowd(ground, [npc(*GAP_A)])
    _p, hops = plan(b, ground)
    walked = cells_walked(ground, START, hops)
    assert GAP_A not in walked and GAP_B in walked


def test_a_player_standing_in_the_way_is_walked_round(ground: MapModel) -> None:
    b = Crowd(ground, [player(*GAP_A)])
    _p, hops = plan(b, ground)
    walked = cells_walked(ground, START, hops)
    assert GAP_A not in walked and GAP_B in walked


def test_several_people_blocking_a_narrow_passage(ground: MapModel) -> None:
    b = Crowd(ground, [player(*GAP_A, pid=901), player(WALL_X - 1, Y0 + 3, pid=902), npc(WALL_X + 1, Y0 + 3)])
    _p, hops = plan(b, ground)
    walked = cells_walked(ground, START, hops)
    assert not walked & {GAP_A, (WALL_X - 1, Y0 + 3), (WALL_X + 1, Y0 + 3)}


def test_with_no_way_round_the_plain_map_is_used_instead_of_no_way(ground: MapModel) -> None:
    # both gaps taken: there is no way round, so the walk goes the plain way (people move on)
    # rather than stopping the run with "no way"
    b = Crowd(ground, [player(*GAP_A, pid=901), player(*GAP_B, pid=902)])
    p, hops = plan(b, ground)
    assert p is not ground and hops
    assert GAP_A in cells_walked(ground, START, hops)


def test_our_own_pet_and_ourselves_are_no_walls(ground: MapModel) -> None:
    pet = player(*GAP_A, pid=10001, name="Wild Eagle Sky belongs to Hero")
    me_too = player(*START, pid=7, name="Hero")
    b = Crowd(ground, [pet, me_too])
    p, hops = plan(b, ground)
    assert p is ground and hops == [GOAL]


def test_the_npc_walked_up_to_is_left_open(ground: MapModel) -> None:
    b = Crowd(ground, [npc(GOAL[0] + 1, GOAL[1])])
    cells = b.npc_cells(GOAL)
    assert cells == set()                       # near the goal: that is who we are going to see


def test_walk_to_plans_round_people_on_its_first_try(ground: MapModel) -> None:
    b = Crowd(ground, [player(*GAP_A)])
    first: list[tuple[float, float]] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=None, near=None: first.append(target) or "arrived"
    assert b.walk_to(GOAL) == "arrived"
    assert first and first[0] != GOAL            # not straight through the player in the gap
    assert GAP_A not in cells_walked(ground, START, [(int(x), int(y)) for x, y in first])


def test_a_stuck_walk_plans_again_with_whoever_stands_there_now(ground: MapModel) -> None:
    people: list[dict[str, Any]] = []
    b = Crowd(ground, people)
    tries: list[list[tuple[float, float]]] = [[]]

    def walk_axis(axis: int, target: float, map_before: Any = None, tol: float = 0.5, timeout: Any = None, near: Any = None) -> str:
        return "arrived"

    def walk_diag(target: tuple[float, float], map_before: Any = None, tol: float = 0.5, timeout: Any = None, near: Any = None) -> str:
        tries[-1].append(target)
        if len(tries) == 1:
            people.append(player(*GAP_A))       # someone stepped into the gap: stuck
            tries.append([])
            return "stuck"
        return "arrived"
    b.walk_diag = walk_diag
    b.walk_axis = walk_axis
    assert b.walk_to(GOAL) == "arrived"
    assert b.fight_count == 1                    # the stuck was looked at once (fight first)
