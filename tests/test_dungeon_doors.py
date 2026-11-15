# pyright: reportPrivateUsage=false
"""Dungeon doors the map table draws as walls, on the real map data.

fully mountain (1563): the mountain gate is an in-map teleport pad - a z=-1 marker at x 202-203
that moves the player to marker 3, the cave mouth at 219,230-231. Walls stand between the two, so
the follower stopped with "no way from 200,233 to 219,231" while the leader was already through.

Demon's Prison (1741): "九门" (gate 9) is a z=-2 marker with a wall tile's id painted over the
door at x 332. It opens once its stage is cleared, so a walk with no other way plans through it."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from conftest import stand_in

from test_walking import MapBot
from unclefarmer import atlas
from unclefarmer.atlaslib import MapModel

MOUNTAIN, PRISON = 1563, 1741
LANDING = (219, 230)


class Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Any, timeout: float | None = None) -> bool:
        self.now += timeout or 0.0
        return False


class DoorBot(MapBot):
    """MapBot on a real map whose walks arrive where they aim, and whose pads move it on (unless
    shut)."""

    def __init__(self, m: MapModel, pos: tuple[int, int], mid: int, pads_open: bool = True) -> None:
        super().__init__(m, pos=pos, map_id=mid)   # type: ignore[arg-type]
        self.real = m
        self.pads_open = pads_open
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.stucks = 0

    def _arrive(self, x: float, y: float) -> None:
        self.pos = (int(x), int(y))
        j = self.real.jump_at(self.pos)
        if j is not None and self.pads_open:
            self.pos = j["to"]

    def walk_diag(self, target: tuple[float, float], map_before: int | None = None, tol: float = 0.5,
                  timeout: float | None = None, near: Any = None) -> str:
        self._arrive(target[0], target[1])
        return "arrived"

    def walk_axis(self, axis: int, target: float, map_before: int | None = None, tol: float = 0.5,
                  timeout: float | None = None, near: Any = None) -> str:
        self._arrive(target if axis == 0 else self.pos[0], target if axis == 1 else self.pos[1])
        return "arrived"

    def _walk_stuck(self, cell: tuple[int, int]) -> bool:
        self.stucks += 1
        return False


@pytest.fixture
def real_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "map %s" % mid))


def test_the_mountain_gate_is_a_teleport_pad() -> None:
    m = atlas.model(MOUNTAIN)
    assert m.route((200, 233), (219, 231)) is None          # walls all the way, as drawn
    [j] = m.jumps
    assert (j["x1"], j["y1"], j["x2"], j["y2"]) == (202, 227, 203, 232)
    assert j["to"] == LANDING
    pad = m.jump_leg((200, 233), (219, 231))
    assert pad is not None and m.jump_at(pad) is j
    # stepping on a pad moves us: routes may end on one, never cross it
    assert all(m.portal[y, x] for x in (202, 203) for y in range(227, 233))


def test_a_pad_with_two_landings_is_left_out() -> None:
    # thousand miles of rivers: pad 2 lands on either of two "in-map teleport points"
    assert atlas.model(1538).jumps == []


def test_ghost_hotel_floors_chain_through_their_stairs() -> None:
    m = atlas.model(1664)
    assert len(m.jumps) == 8
    ground, top = (141, 305), (21, 6)        # the first floor's stairs, the fifth floor's
    assert m.route(ground, top) is None
    pad = m.jump_leg(ground, top)
    assert pad is not None
    j = m.jump_at(pad)
    assert j is not None and j["to"] == (141, 248)       # up the east stairs first


def test_gate_nine_stays_a_wall_until_nothing_else_gets_there() -> None:
    m = atlas.model(PRISON)
    assert not m.walk[98, 332] and m.barrier[98, 332]
    assert m.route((329, 97), (335, 98)) is None
    opened = m.barriers_open()
    assert opened is not None
    path = opened.route((329, 97), (335, 98))
    assert path is not None and (332, 98) in path
    assert not m.walk[98, 332]                               # the map itself is left as it was


def test_barrier_markers_over_rock_never_open() -> None:
    # Nether Cave's stage-barrier column stands on rock: opening it would walk into the wall
    assert atlas.model(1836).barriers_open() is None


def test_maps_without_barriers_or_pads_are_unchanged() -> None:
    m = atlas.model(1041)
    assert m.jumps == [] and m.barriers_open() is None and m.jump_leg((252, 183), (260, 190)) is None


def test_the_follower_walks_through_the_mountain_gate(real_maps: None) -> None:
    m = atlas.model(MOUNTAIN)
    b = DoorBot(m, (200, 233), MOUNTAIN)
    assert b.walk_to((230, 231)) == "arrived"
    assert b.pos == (230, 231)
    assert "through the teleport at 202,232" in b.logs       # the pad's nearest cell
    assert b.stucks == 0


def test_a_shut_gate_is_a_stuck_walk_not_an_arrival(real_maps: None) -> None:
    m = atlas.model(MOUNTAIN)
    b = DoorBot(m, (200, 233), MOUNTAIN, pads_open=False)
    assert b.walk_to((230, 231)) == "stuck"
    assert b.stucks >= 1
    assert any("still shut" in line for line in b.logs)


def test_the_walk_goes_through_gate_nine(real_maps: None) -> None:
    m = atlas.model(PRISON)
    b = DoorBot(m, (329, 97), PRISON)
    assert b.walk_to((335, 98)) == "arrived"
    assert b.pos == (335, 98)
