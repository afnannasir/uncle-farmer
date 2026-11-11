# pyright: reportPrivateUsage=false
# White-box tests: they read the map model's grids and the walking part's distance directly.
"""Lava: walkable on the map, but a character standing in it loses HP (the volcanic cave's lava
lake, 5242). The map model counts it as a wall wherever the safe ground still holds together, so
the bot walks, stands and fights on the islands; a monster out in the lava is fought from the
shore within reach, or left alone."""

from __future__ import annotations

import math

import numpy as np

from unclefarmer import atlas
from unclefarmer.atlaslib import MapModel
from unclefarmer.engine.botparts.walking import WalkingMixin

VOLCANIC_CAVE = 5242
LAVA_ISLAND = (73, 158)            # the spot the player named: safe ground
STONE_LADDER = (81, 174)


def test_the_volcanic_caves_lava_lake_is_not_walked_on() -> None:
    m = MapModel(VOLCANIC_CAVE)
    assert m.hazard.sum() == 10942                     # every lava lake cell of the map table
    assert not m.walk[m.hazard].any()
    assert m.walk[LAVA_ISLAND[1], LAVA_ISLAND[0]] and m.walk[STONE_LADDER[1], STONE_LADDER[0]]


def test_its_exits_and_islands_are_still_joined_without_crossing_lava() -> None:
    m = MapModel(VOLCANIC_CAVE)
    path = m.route((80, 177), (80, 4))                 # the stone ladder to the north exit
    assert path is not None
    assert not any(m.hazard[y, x] for x, y in path)
    to_island = m.route((80, 177), LAVA_ISLAND)
    assert to_island is not None and not any(m.hazard[y, x] for x, y in to_island)


def test_lava_stays_walkable_where_blocking_it_would_cut_the_map_apart() -> None:
    for mid in (1790, 1793):                           # Deep underground, Demon Castle
        m = MapModel(mid)
        assert m.hazard.any() and m.walk[m.hazard].all()


def test_ordinary_water_and_maps_without_lava_are_untouched() -> None:
    m = MapModel(1041)                                 # central plains
    assert not m.hazard.any()


class Reacher(WalkingMixin):
    def __init__(self, reach: float) -> None:
        self._dists = None
        self._reach = reach

    def reach(self) -> float:
        return self._reach


def snap_at(me: tuple[float, float], mob: tuple[float, float]) -> tuple[dict[str, object], dict[str, float]]:
    return ({"map": {"id": VOLCANIC_CAVE}, "self": {"x": me[0], "y": me[1]}, "entities": []},
            {"x": mob[0], "y": mob[1]})


def test_a_monster_in_the_lava_is_fought_from_the_shore_within_reach() -> None:
    atlas.model(VOLCANIC_CAVE)
    # 12 cells out into the lava from the nearest safe ground (61, 2)
    snap, mob = snap_at((70.0, 5.0), (49.0, 2.0))
    assert Reacher(19.5).walk_dist(snap, mob) < math.inf      # a Hunter shoots it from the shore
    assert Reacher(4.0).walk_dist(snap, mob) == math.inf      # a melee character can't reach it


def test_a_monster_deep_in_the_lava_is_left_alone() -> None:
    snap, mob = snap_at((70.0, 5.0), (2.0, 2.0))
    m = atlas.model(VOLCANIC_CAVE)
    shore = m.nearest_walkable(2, 2, 19)          # a square search: its corner is 27 tiles off
    assert m.hazard[2, 2] and (shore is None or math.hypot(shore[0] - 2, shore[1] - 2) > 19.5)
    assert Reacher(19.5).walk_dist(snap, mob) == math.inf
    assert np.isfinite(Reacher(19.5).walk_dist(*snap_at((70.0, 5.0), (65.0, 5.0))))   # on the island: as before
