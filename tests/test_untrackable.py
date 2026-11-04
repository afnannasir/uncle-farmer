# pyright: reportPrivateUsage=false
"""Monsters that are in view but can't be tracked: Skull Cave (5322) and other maps full of rock.

A monster standing inside a rock (or past a wall) shows in the world, and walk_dist counts it as
near (the map is trusted to be wrong before the monster is), but the game's target keys won't take
it: no clear line. The farm loop walked up to it, tried to select it, left it 10 s, and picked it
again - for as long as it stood there. Now each failure where it stands doubles... triples its
wait (10, 30, 90, 270, 300 s), the next nearest are farmed meanwhile, and a monster that moves off
the spot is a fresh try at once."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any

import pytest

import simgame
from test_fighting import Bot, Clock, monster, straight_walk
from unclefarmer.clock import CLOCK
from unclefarmer import atlas

@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The fighting tests' pretend clock (test_fighting.clock), here as this module's own fixture."""
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c



def rock_spot(world: simgame.World, near: tuple[float, float]) -> tuple[int, int]:
    """A cell inside rock in a monster zone, a few tiles from walkable ground, nearest to `near`."""
    m = world.model
    best: tuple[float, tuple[int, int]] | None = None
    for (x0, y0, x1, y1) in world.zones:
        for y in range(int(y0), int(y1) + 1):
            for x in range(int(x0), int(x1) + 1):
                if not m.ok(x, y) and m.nearest_walkable(x, y, 3) is None and m.nearest_walkable(x, y, 8):
                    d = math.hypot(x - near[0], y - near[1])
                    if best is None or d < best[0]:
                        best = (d, (x, y))
    assert best is not None
    return best[1]


@pytest.mark.slow
def test_skull_cave_a_monster_in_the_rock_is_not_walked_back_to_every_ten_seconds(
        sim_env: Callable[..., simgame.SimGame]) -> None:
    game = sim_env(map_id=5322, monsters=8)
    w = game.world
    w.walls_block_select = True
    x, y = rock_spot(w, (w.x, w.y))
    w.x, w.y = map(float, w.model.nearest_walkable(x, y, 8) or (w.x, w.y))   # start beside it
    rock = simgame.Monster(-99, float(x), float(y), (x, y, x, y), 55)
    rock.name = "Rock Wraith"
    w.monsters[rock.id] = rock
    s = simgame.Soak(game, "end_at here\nfarm minutes=15", watchdog=240).go()
    assert s.reason == "finished", list(s.lines)[-15:]
    assert not game.errors and not game.unknown
    tries = [line for line in s.lines if "select Rock Wraith" in line]
    assert 1 <= len(tries) <= 6, tries                     # 10, 30, 90, 270, 300 s waits: a handful
    assert w.truth["kills"] >= 5, w.truth                  # the others were farmed meanwhile (a big,
                                                           # sparse cave: 7-10 in 15 minutes)
    assert rock.hp == rock.maxhp                           # (it never could be hit)


def test_the_wait_grows_where_it_stands_and_resets_once_it_moves(clock: Clock) -> None:
    b = Bot()
    mob = monster(-5, x=110.0, y=100.0, name="Rock Wraith")
    waits: list[int] = []
    for _try in range(6):
        b.note_unselectable(mob)
        waits.append(round(b.unselectable[-5][2] - clock.now))
    assert waits == [10, 30, 90, 270, 300, 300]
    assert b.untrackable(mob, clock.now) is True
    moved = dict(mob, x=120.0)
    assert b.untrackable(moved, clock.now) is False        # off the spot: a fresh try
    assert -5 not in b.skip and -5 not in b.unselectable
    b.note_unselectable(moved)
    assert round(b.unselectable[-5][2] - clock.now) == 10


def test_a_new_monster_under_a_reused_id_elsewhere_is_not_left_alone(clock: Clock) -> None:
    b = Bot(entities=[monster(-5, x=110.0)])
    straight_walk(b)
    b._attackers_at, b._attackers = 1e12, set()
    b.note_unselectable(monster(-5, x=140.0, y=140.0))     # the old one, in the rock far off
    assert [e["id"] for e in b.targets(b.snap())] == [-5]


@pytest.mark.parametrize("pos", [{"x": None, "y": 100.0}, {"x": float("nan"), "y": 100.0},
                                 {"x": 100.0, "y": float("inf")}, {"x": 5000.0, "y": 100.0}])
def test_a_monster_with_no_real_position_is_not_a_target(clock: Clock, pos: dict[str, Any]) -> None:
    b = Bot(entities=[dict(monster(-5), **pos), monster(-6, x=105.0)])
    straight_walk(b)
    b._attackers_at, b._attackers = 1e12, set()
    assert atlas.maps()[5121]["w"] < 5000
    assert [e["id"] for e in b.targets(b.snap())] == [-6]
