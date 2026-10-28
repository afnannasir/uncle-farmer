# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Walking on a map: step directions, arrival/stuck/map detection, retries, blocking and distances.

The source (engine/botparts/walking.py) is a Bot mixin: every method reaches back into `self` for
the game and for the other parts of the bot. So each test builds a small stand-in bot that owns
only the handful of methods the walking code calls, and a FakeModel standing in for `atlas.model`.
No game, no network, no threads: the clock is moved by hand and the "sleep" is a no-op.
"""

from __future__ import annotations

import copy
import math
import random
import threading

import numpy as np
import pytest

from unclefarmer.clock import CLOCK, Waitable
from unclefarmer import atlas
from unclefarmer.engine.botparts.base import NPC_HP, Respawned, Stopped
from unclefarmer.engine.botparts.parts import Rect
from unclefarmer.engine.botparts.walking import WalkingMixin
from collections.abc import Callable, Iterable, Sequence
from typing import Any
from conftest import as_kind, stand_in
from unclefarmer.atlaslib import Cell, MapModel

MOVE_KEYS = {"up": "w", "down": "s", "left": "a", "right": "d"}


# ---------------------------------------------------------------- shared fakes
class Clock:
    """time.time() for the code under test: moves only when a test moves it."""

    def __init__(self, start: float = 1000.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Waitable, timeout: float | None=None) -> bool:
        if event.is_set():
            return True
        if hasattr(event, "clock"):
            return bool(event.wait(timeout))
        if timeout:
            self.now += timeout
        return False

    def advance(self, seconds: float) -> None:
        self.now += seconds


class FakeEvent:
    """Stands in for a threading.Event; wait() advances the clock instead of blocking."""

    def __init__(self, clock: Clock, dt: float = 0.04) -> None:
        self.clock = clock
        self.dt = dt

    def wait(self, timeout: float | None = None) -> bool:
        self.clock.advance(self.dt)
        return False

    def is_set(self) -> bool:
        return False

    def set(self) -> None:
        pass                     # nothing here presses Stop


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


class Map:
    """A pretend walkable grid: every method walk_to/walk_dist/escape_cell call, all steered by
    attributes a test sets. W is the width; cells are 1..W in both axes."""

    def __init__(self, W: int=60, walkable: bool=True) -> None:
        self.id = 1
        self.W = W
        self.walk = np.full((W + 2, W + 2), bool(walkable))
        self.directional = np.zeros((W + 2, W + 2), bool)
        self.portal = np.zeros((W + 2, W + 2), bool)
        self.nearest: Cell | None = None             # what nearest_walkable returns
        self.nearest_floor_cell: Cell | None = None
        self.diag = True                # diag_ok
        self.line = True                # line_ok
        self.route_paths: list[list[Cell]] = []           # route() pops one per call
        self.route_calls: list[tuple[tuple[int, int], tuple[int, int], bool]] = []
        self.hops: list[Cell] | None = None                # diag_hops result
        self.waypoint_list = None       # waypoints result
        self.without_calls: list[set[tuple[int, int]]] = []
        self.two_way_result: frozenset[int] | None = None      # two_way()
        self.steps_map: dict[int, int] = {}
        self.step_calls = 0

    def ok(self, x: int, y: int) -> bool:
        return 1 <= x <= self.W and 1 <= y <= self.W and bool(self.walk[y, x])

    def nearest_walkable(self, x: int, y: int, radius: int=40, comp: int | None=None) -> Cell | None:
        return self.nearest

    def nearest_floor(self, x: int, y: int, radius: int=3, comp: int | None=None) -> Cell | None:
        return self.nearest_floor_cell

    def diag_ok(self, a: tuple[int, int], b: tuple[int, int]) -> bool:
        return self.diag

    def line_ok(self, a: Cell, b: Cell, min_clear: int=1, allow_directional: bool=False) -> bool:
        return self.line

    def route(self, a: Cell, b: Cell, avoid_directional: bool=False) -> list[Cell] | None:
        self.route_calls.append((a, b, avoid_directional))
        return self.route_paths.pop(0) if self.route_paths else None

    def diag_hops(self, path: Sequence[Cell], reach: int=300, stride: int=4) -> list[Cell]:
        return list(self.hops) if self.hops is not None else [path[-1]]

    def waypoints(self, path: Sequence[Cell], max_hop: int | None=None) -> list[Cell]:
        return list(self.waypoint_list) if self.waypoint_list is not None else [path[-1]]

    def without(self, cells: Iterable[Cell]) -> Map:
        self.without_calls.append(set(cells))
        mm = copy.copy(self)
        mm.walk = self.walk.copy()
        for x, y in cells:
            if 1 <= x <= self.W and 1 <= y <= self.W:
                mm.walk[y, x] = False
        return mm

    def two_way(self, x: int, y: int) -> frozenset[int] | None:
        return self.two_way_result

    def steps(self, src: Cell, limit: float | None=None) -> dict[int, int]:
        self.step_calls += 1
        return dict(self.steps_map)

    def comp_of(self, x: int, y: int) -> int:
        return 1


# ---------------------------------------------------------------- npc_cells
class CellBot(WalkingMixin):
    def __init__(self, entities: list[dict[str, int | str | float]] | None=None, raises: bool=False) -> None:
        self.entities = entities or []
        self.raises = raises

    def snap(self) -> dict[str, Any]:
        if self.raises:
            raise RuntimeError("the game can't be read")
        return {"entities": self.entities}


def test_npc_cells_are_the_cells_around_town_npcs() -> None:
    b = CellBot([
        {"kind": "npc", "id": 7, "x": 10.0, "y": 10.0, "maxhp": 100},
        {"kind": "monster", "id": -1, "x": 20.0, "y": 20.0, "maxhp": 100},
        {"kind": "monster", "id": -2, "x": 30.0, "y": 30.0, "maxhp": NPC_HP},
    ])
    cells = b.npc_cells((50, 50))
    assert (9, 9) in cells and (11, 11) in cells          # round the NPC at 10,10
    assert (19, 19) not in cells                          # a low-HP negative-id monster isn't one
    assert (29, 29) in cells and (31, 31) in cells        # high tooltip HP and a negative id: an NPC


def test_npc_cells_leave_out_the_npc_we_are_walking_up_to() -> None:
    b = CellBot([{"kind": "npc", "id": 7, "x": 10.0, "y": 10.0, "maxhp": 100}])
    assert b.npc_cells((10, 10)) == set()       # dest on the NPC: it is the one we want
    assert b.npc_cells((12, 10)) == set()       # still within keep (2.5) of it
    assert b.npc_cells((13, 10)) != set()       # now the NPC counts again


def test_npc_cells_empty_when_the_game_cannot_be_read(allow_ignored_failures: None) -> None:
    assert CellBot(raises=True).npc_cells((0, 0)) == set()


# ---------------------------------------------------------------- blocking
class BlockBot(WalkingMixin):
    pass


def test_blocking_is_a_monster_right_against_us() -> None:
    b = BlockBot()
    me = {"x": 0.0, "y": 0.0}
    assert b.blocking(me, {"x": 2.0, "y": 0.0})                       # 2 <= BLOCKING (2.5)
    assert not b.blocking(me, {"x": 3.0, "y": 0.0})


def test_blocking_is_a_monster_ahead_within_the_cone() -> None:
    b = BlockBot()
    me = {"x": 0.0, "y": 0.0}
    assert b.blocking(me, {"x": 5.0, "y": 0.0}, toward=(10.0, 0.0))   # straight ahead
    assert b.blocking(me, {"x": 4.0, "y": 4.0}, toward=(10.0, 0.0))   # 45 degrees: within 60


def test_blocking_ignores_what_is_behind_or_to_the_side() -> None:
    b = BlockBot()
    me = {"x": 0.0, "y": 0.0}
    assert not b.blocking(me, {"x": -5.0, "y": 0.0}, toward=(10.0, 0.0))   # behind us
    assert not b.blocking(me, {"x": 0.0, "y": 5.0}, toward=(10.0, 0.0))    # square to the side
    assert not b.blocking(me, {"x": 10.0, "y": 0.0}, toward=(10.0, 0.0))   # too far ahead (> AHEAD)


def test_blocking_without_a_direction_needs_the_monster_on_top_of_us() -> None:
    b = BlockBot()
    me = {"x": 0.0, "y": 0.0}
    assert not b.blocking(me, {"x": 3.0, "y": 0.0})


# ---------------------------------------------------------------- walk_axis
class Walker(WalkingMixin):
    """A bot whose every reading the test scripts: a queue of positions `look_after` walks through
    (repeating the last), a fixed map id, and switches for hit/popups."""

    def __init__(self, clock: Clock, looks: Sequence[Sequence[float]] = (), pos: Sequence[float] = (0.0, 0.0), map_id: int = 1,
                 hit: bool = False, popups: bool = False, dt: float = 0.04) -> None:
        self.p = {"move_keys": dict(MOVE_KEYS), "walk_diagonal": True, "stuck_limit": 3}
        self.stop_event = FakeEvent(clock, dt)
        self.resumed_at = 0.0
        self._looks = list(looks)
        self._pos = pos
        self._map = map_id
        self._hit = hit
        self._popups = popups
        self.held: set[str] = set()
        self.keys: list[tuple[str, str | None]] = []
        self.logs: list[str] = []
        self.last_hp = None

    def look_after(self, me: dict[str, Any] | None=None) -> dict[str, Any]:
        if self._looks:
            self._pos = self._looks.pop(0)
        return {"x": self._pos[0], "y": self._pos[1], "hp": 100}

    def map_id(self) -> int:
        return self._map

    def hit(self, me: dict[str, Any]) -> bool:
        return self._hit

    def clear_popups(self, force: bool=False) -> bool:
        return self._popups

    def move_key(self, direction: str) -> str:
        return self.p["move_keys"][direction]

    def hold(self, k: str) -> None:
        self.keys.append(("hold", k))
        self.held.add(k)

    def let_go(self, k: str | None = None) -> None:
        self.keys.append(("let", k))
        if k in self.held:
            self.held.discard(k)

    def log(self, line: str, speak: bool=False):
        self.logs.append(line)


def test_walk_axis_holds_the_key_and_arrives(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (10, 0)])
    assert b.walk_axis(0, 10.0) == "arrived"
    assert ("hold", "d") in b.keys
    assert ("let", "d") in b.keys
    assert b.held == set()


def test_walk_axis_already_at_the_target_only_arrives(clock: Clock) -> None:
    b = Walker(clock, pos=(5.0, 0.0))
    assert b.walk_axis(0, 5.0) == "arrived"
    assert b.keys == []


@pytest.mark.parametrize("axis,start,target,key", [
    (0, (5.0, 0.0), 0.0, "a"),     # west
    (1, (0.0, 5.0), 0.0, "w"),     # north (y grows south)
    (1, (0.0, 0.0), 5.0, "s"),     # south
])
def test_walk_axis_picks_the_key_for_the_direction(clock: Clock, axis: int, start: tuple[float, float], target: float, key: str) -> None:
    end = list(start)
    end[axis] = target
    b = Walker(clock, looks=[start, tuple(end)])
    assert b.walk_axis(axis, target) == "arrived"
    assert ("hold", key) in b.keys


def test_walk_axis_reports_a_map_change(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (1, 0)], map_id=2)
    assert b.walk_axis(0, 10.0, map_before=1) == "map"
    assert ("let", "d") in b.keys


def test_walk_axis_returns_attacked_and_lets_go(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (1, 0)], hit=True)
    assert b.walk_axis(0, 10.0) == "attacked"
    assert ("let", "d") in b.keys


def test_walk_axis_stops_when_near_says_so(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (1, 0)])
    assert b.walk_axis(0, 10.0, near=lambda me: True) == "near"
    assert ("let", "d") in b.keys


def test_walk_axis_stuck_when_it_does_not_move(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0)], pos=(0, 0), dt=1.0)
    assert b.walk_axis(0, 10.0) == "stuck"
    assert ("let", "d") in b.keys


def test_walk_axis_timeout_returns_stuck(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0)])
    assert b.walk_axis(0, 10.0, timeout=0.0) == "stuck"
    assert ("let", "d") in b.keys       # the finally always lets go


# ---------------------------------------------------------------- walk_diag
def test_walk_diag_holds_both_keys_and_arrives(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (10, 10)])
    assert b.walk_diag((10.0, 10.0)) == "arrived"
    assert [k for kind, k in b.keys if kind == "hold"] == ["d", "s"]
    assert [k for kind, k in b.keys if kind == "let"] == ["d", "s"]


def test_walk_diag_already_there_only_arrives(clock: Clock) -> None:
    b = Walker(clock, pos=(3.0, 3.0))
    assert b.walk_diag((3.0, 3.0)) == "arrived"
    assert b.keys == []


def test_walk_diag_releases_each_axis_as_it_is_reached(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (10, 5), (10, 10)])
    assert b.walk_diag((10.0, 10.0)) == "arrived"
    assert ("let", "d") in b.keys        # east-west reached first
    assert ("let", "s") in b.keys        # then north-south
    assert b.keys.index(("let", "d")) < b.keys.index(("let", "s"))


def test_walk_diag_lets_go_on_a_map_change(clock: Clock) -> None:
    b = Walker(clock, looks=[(0, 0), (1, 1)], map_id=2)
    assert b.walk_diag((10.0, 10.0), map_before=1) == "map"
    assert ("let", "d") in b.keys and ("let", "s") in b.keys


# ---------------------------------------------------------------- walk_to
class MapBot(WalkingMixin):
    """The bot half of walk_to: a scripted position, a fixed map id, and counters for the parts
    walk_to delegates (fighting back, getting unstuck, clearing the way)."""

    def __init__(self, m: Map, pos: tuple[int, int]=(1, 1), map_id: int=1) -> None:
        self.p = {"move_keys": dict(MOVE_KEYS), "walk_diagonal": True, "stuck_limit": 3,
                  "fight_back": True}
        self._m = m
        self._mid = map_id
        self.pos = pos
        self.walk_only = False
        self.in_fight = False
        self.traveling = False
        self._unsticking = False
        self.stuck_in_a_row = 0
        self.logs: list[str] = []
        self.keys: list[str] = []
        self.fight_count = 0
        self.unstuck_calls: list[str] = []
        self.crossed: list[dict[str, Any]] = []

    def me(self) -> dict[str, Any]:
        return {"x": self.pos[0], "y": self.pos[1], "hp": 100}

    def map_id(self) -> int:
        return self._mid

    def check_game(self, force: bool=False) -> None:
        pass

    def move_key(self, direction: str) -> str:
        return self.p["move_keys"][direction]

    def key(self, k: str, ms: int=60) -> None:
        self.keys.append(k)

    def fight_back(self) -> None:
        self.fight_count += 1

    def clear_popups(self, force: bool=False) -> bool:
        return False

    text_boxes = 0       # chat lines that open (one per stuck walk) until this many are closed

    def close_text_box(self) -> bool:
        if self.text_boxes:
            self.text_boxes -= 1
            return True
        return False

    def cross_exit(self, leg: dict[str, Any]) -> None:
        self.crossed.append(leg)

    def snap(self) -> dict[str, Any]:
        return {"entities": []}

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Map:
    m = Map()

    def mapper(mid: int) -> MapModel:
        return as_kind(MapModel, m)

    monkeypatch.setattr(atlas, "model", mapper)
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "map %s" % mid))
    return m


def test_walk_to_crosses_open_ground_on_the_diagonal(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    calls: list[tuple[tuple[float, float], int | None, object]] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: calls.append((target, map_before, near)) or "arrived"
    assert b.walk_to((10, 10)) == "arrived"
    assert calls == [((10, 10), None, None)]


def test_walk_to_snaps_an_unwalkable_target_to_the_nearest_cell(model: Map) -> None:
    model.walk[1, 3] = False                    # (3,1) is a wall
    model.nearest = (7, 7)
    b = MapBot(model, pos=(1, 1))
    seen: list[tuple[float, float]] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: seen.append(target) or "arrived"
    assert b.walk_to((3, 1)) == "arrived"
    assert seen == [(7, 7)]


def test_walk_to_stops_when_the_target_cannot_be_reached(model: Map) -> None:
    model.walk[1, 3] = False                    # (3,1) is a wall with no walkable cell near it
    model.nearest = None
    b = MapBot(model, pos=(1, 1))
    with pytest.raises(Stopped):
        b.walk_to((3, 1))


def test_walk_to_never_ends_on_a_ledge(model: Map) -> None:
    model.directional[10, 10] = True            # the target is a cliff-edge cell
    model.nearest_floor_cell = (9, 9)
    b = MapBot(model, pos=(1, 1))
    seen: list[tuple[float, float]] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: seen.append(target) or "arrived"
    assert b.walk_to((10, 10)) == "arrived"
    assert seen == [(9, 9)]


def test_walk_to_follows_a_route_when_the_slant_is_blocked(model: Map) -> None:
    model.diag = False
    model.line = False
    model.route_paths = [[(1, 1), (5, 1), (5, 5), (10, 10)]]
    model.hops = [(10, 10)]
    b = MapBot(model, pos=(1, 1))
    seen: list[tuple[float, float]] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: seen.append(target) or "arrived"
    assert b.walk_to((10, 10)) == "arrived"
    assert model.route_calls[0] == ((1, 1), (10, 10), True)     # avoids one-way ledges first
    assert seen == [(10, 10)]


def test_walk_to_stops_when_there_is_no_route(model: Map) -> None:
    model.diag = False
    model.line = False
    model.route_paths = []
    b = MapBot(model, pos=(1, 1))
    with pytest.raises(Stopped) as e:
        b.walk_to((10, 10))
    assert e.value.final is False


def test_walk_to_walks_one_axis_at_a_time_when_diagonal_is_off(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    b.p["walk_diagonal"] = False
    axes: list[tuple[int, float]] = []
    b.walk_axis = lambda axis, target, map_before=None, tol=0.5, timeout=60.0, near=None: axes.append((axis, target)) or "arrived"
    assert b.walk_to((10, 20)) == "arrived"
    assert axes == [(0, 10), (1, 20)]


def test_walk_to_snapshots_the_map_before_crossing(model: Map) -> None:
    b = MapBot(model, pos=(1, 1), map_id=42)
    seen: list[int | None] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: seen.append(map_before) or "map"
    assert b.walk_to((10, 10), map_change=True) == "map"
    assert seen == [42]


def test_walk_to_returns_near_without_fighting(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: "near"
    assert b.walk_to((10, 10), near=lambda me: True) == "near"
    assert b.fight_count == 0


def test_walk_to_fights_back_and_replans_when_attacked(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    results = iter(["attacked", "arrived"])
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: next(results)
    assert b.walk_to((10, 10)) == "arrived"
    assert b.fight_count == 1


def test_walk_to_attacked_repeats_without_spending_an_attempt(model: Map) -> None:
    # A monster that keeps us walking off the mark ("attacked") re-plans without ever counting
    # against the four-attempt limit: the fight_back() is expected to end it. Durable behavior:
    # a fixture that always reports "attacked" would loop here forever. Keep the test finite.
    b = MapBot(model, pos=(1, 1))
    results = iter(["attacked"] * 5 + ["arrived"])
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: next(results)
    assert b.walk_to((10, 10)) == "arrived"
    assert b.fight_count == 5


def test_walk_to_gives_up_after_too_many_hits(model: Map) -> None:
    # A monster we cannot fight off kept break off the walk; re-planning forever meant walk_to
    # never returned. After ATTACK_LIMIT fights one walk gives up and the caller plans again.
    b = MapBot(model, pos=(1, 1))
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: "attacked"
    assert b.walk_to((10, 10)) == "stuck"
    assert b.fight_count == b.ATTACK_LIMIT
    assert any("giving up this walk" in line for line in b.logs)


def test_walk_to_after_a_stuck_walks_one_axis_and_plans_around_npcs(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    diag_calls: list[tuple[float, float]] = []
    axis_calls: list[tuple[int, float]] = []
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: diag_calls.append(target) or "stuck"
    b.walk_axis = lambda axis, target, map_before=None, tol=0.5, timeout=60.0, near=None: axis_calls.append((axis, target)) or "arrived"
    assert b.walk_to((10, 10)) == "arrived"
    assert len(diag_calls) == 1                                  # only the first attempt is diagonal
    assert axis_calls                                            # the retry walks an axis at a time
    assert model.without_calls and model.without_calls[-1] == set()   # no NPCs about here


def test_a_chat_line_that_stopped_the_walk_is_closed_and_no_stuck(model: Map) -> None:
    # the chat line opened while the keys were held: no wall, so no stuck, no step aside
    b = MapBot(model, pos=(1, 1))
    b.text_boxes = 1
    walks = ["stuck"]                         # the first walk stops dead; every one after arrives
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: walks.pop() if walks else "arrived"
    b.walk_axis = lambda axis, target, map_before=None, tol=0.5, timeout=60.0, near=None: walks.pop() if walks else "arrived"
    b.get_unstuck = lambda at: b.unstuck_calls.append(at) or False
    assert b.walk_to((10, 10)) == "arrived"
    assert b.unstuck_calls == [] and b.stuck_in_a_row == 0
    assert not any("stuck" in line for line in b.logs)


def test_walk_to_gives_up_after_the_stuck_limit(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    b.p["stuck_limit"] = 1
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: "stuck"
    b.get_unstuck = lambda at: b.unstuck_calls.append(at) or False
    with pytest.raises(Stopped):
        b.walk_to((10, 10))
    assert b.unstuck_calls and b.fight_count == 1


def test_walk_to_hands_a_stuck_walk_to_get_unstuck(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    b.p["stuck_limit"] = 1
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: "stuck"
    b.get_unstuck = lambda at: b.unstuck_calls.append(at) or True
    assert b.walk_to((10, 10)) == "stuck"
    assert b.unstuck_calls == ["1,1"]


def test_walk_to_in_a_fight_returns_stuck_without_unsticking(model: Map) -> None:
    b = MapBot(model, pos=(1, 1))
    b.p["stuck_limit"] = 1
    b.in_fight = True
    b.walk_diag = lambda target, map_before=None, tol=0.5, timeout=60.0, near=None: "stuck"
    assert b.walk_to((10, 10)) == "stuck"
    assert not b.unstuck_calls and b.fight_count == 0


# ---------------------------------------------------------------- walk_dist
class DistBot(WalkingMixin):
    def __init__(self) -> None:
        self._dists = None


def snap(x: float, y: float, mid: int=1) -> dict[str, dict[str, int | float]]:
    return {"map": {"id": mid}, "self": {"x": x, "y": y, "hp": 100}}


def test_walk_dist_over_open_ground_is_the_straight_line(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    b = DistBot()
    assert b.walk_dist(snap(1.0, 1.0), {"x": 4.0, "y": 5.0}) == pytest.approx(5.0)


def test_walk_dist_over_the_limit_is_infinite(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    b = DistBot()
    assert b.walk_dist(snap(1.0, 1.0), {"x": 1.0, "y": 10.0}, limit=5) == math.inf


def test_walk_dist_round_a_wall_uses_the_steps_search(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    m.line = False
    m.steps_map = {10 * m.W + 10: 42}
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    b = DistBot()
    assert b.walk_dist(snap(1.0, 1.0), {"x": 10.0, "y": 10.0}) == 42
    assert m.step_calls == 1


def test_walk_dist_reuses_the_search_and_reruns_it_after_moving(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    m.line = False
    m.steps_map = {10 * m.W + 10: 42}
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    b = DistBot()
    b.walk_dist(snap(1.0, 1.0), {"x": 10.0, "y": 10.0})
    b.walk_dist(snap(1.0, 1.0), {"x": 10.0, "y": 10.0})
    assert m.step_calls == 1                       # same cell: the search is reused
    b.walk_dist(snap(5.0, 1.0), {"x": 10.0, "y": 10.0})
    assert m.step_calls == 2                       # moved more than two steps: searched again


def test_walk_dist_falls_back_to_the_straight_line_without_a_map(monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    def boom(mid: int):
        raise KeyError(mid)

    monkeypatch.setattr(atlas, "model", boom)
    b = DistBot()
    assert b.walk_dist(snap(1.0, 1.0), {"x": 4.0, "y": 5.0}) == pytest.approx(5.0)


def test_walk_dist_snaps_an_unwalkable_monster_to_the_map(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    m.walk[5, 4] = False                        # the monster stands where the map says wall
    m.nearest = (4, 4)
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: m))
    b = DistBot()
    assert b.walk_dist(snap(1.0, 1.0), {"x": 4.0, "y": 5.0}) == pytest.approx(5.0)


# ---------------------------------------------------------------- escape_cell / leave_by_exit
def test_escape_cell_picks_a_walkable_cell_away_from_here(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map(W=100)
    monkeypatch.setattr(random, "randint", stand_in(random.randint, lambda a, b: 10))
    b = MapBot(m, pos=(50, 50))
    cell = b.escape_cell(m, {"x": 50.0, "y": 50.0})
    assert cell == (60, 60)


def test_escape_cell_gives_up_when_nowhere_qualifies(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map(W=100, walkable=False)
    b = MapBot(m, pos=(50, 50))
    assert b.escape_cell(m, {"x": 50.0, "y": 50.0}) is None


def test_leave_by_exit_takes_the_shortest_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    m.id = 1
    m.route_paths = [[(1, 1), (2, 1)], [(1, 1), (2, 1), (3, 1), (4, 1), (5, 1)]]   # two steps, then five
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {1: {"name": "Test", "exits": [
        {"dest": 2, "stand": (5, 5), "rect": [4, 4, 6, 6]},
        {"dest": 3, "stand": (8, 8), "rect": [7, 7, 9, 9]},
    ]}}))
    b = MapBot(m, pos=(1, 1))
    assert b.leave_by_exit(m, {"x": 1.0, "y": 1.0}) is True
    assert b.crossed == [{"map": 1, "stand": (5, 5), "rect": [4, 4, 6, 6], "dest": 2}]


def test_leave_by_exit_is_false_without_a_usable_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    m = Map()
    m.id = 1
    monkeypatch.setattr(atlas, "maps", stand_in(atlas.maps, lambda: {1: {"name": "Test", "exits": [{"dest": None, "stand": (5, 5)}]}}))
    b = MapBot(m, pos=(1, 1))
    assert b.leave_by_exit(m, {"x": 1.0, "y": 1.0}) is False
    assert b.crossed == []


# ---------------------------------------------------------------- get_unstuck
class AlwaysRunning(threading.Event):
    """A stop flag that is never set."""

    def is_set(self) -> bool:
        return False


class UnstuckBot(MapBot):
    def __init__(self, m: Map, moved: bool=False) -> None:
        super().__init__(m, pos=(1, 1))
        self.moved = moved
        self.home = False
        self.leave_result = False
        self.stop_event = AlwaysRunning()

    def me(self) -> dict[str, Any]:
        return {"x": 20.0 if self.moved else 1.0, "y": 20.0 if self.moved else 1.0, "hp": 100}

    def escape_cell(self, m: Any, me: dict[str, Any], near: int = 10, far: int = 30) -> tuple[int, int] | None:
        return None

    def leave_by_exit(self, m: Any, me: dict[str, Any], why: str | None=None) -> bool:
        if self.leave_result:
            self.moved = True
        return self.leave_result

    def has_hearthstone(self) -> bool:
        return self.home

    def go_home(self, tries: int=1) -> bool:
        self.moved = True
        return True


def test_get_unstuck_returns_once_a_push_moves_us(model: Map) -> None:
    b = UnstuckBot(model)

    def press(k: str, ms: int = 60) -> None:
        b.moved = True                      # the key pushed us away

    b.key = press
    assert b.get_unstuck("1,1") is True
    assert b.stuck_in_a_row == 0
    assert not b._unsticking
    assert any("got out by pushing" in line for line in b.logs)


def test_get_unstuck_falls_through_to_walking_elsewhere(model: Map) -> None:
    b = UnstuckBot(model)
    walked: list[tuple[float, float]] = []
    b.escape_cell = lambda m, me, near=10, far=30: (30, 30)

    def walk_to(cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        walked.append(cell)
        b.moved = True
        return "arrived"

    b.walk_to = walk_to
    assert b.get_unstuck("1,1") is True
    assert walked == [(30, 30)]
    assert any("walked to" in line for line in b.logs)


def test_get_unstuck_falls_through_to_leaving_the_map(model: Map) -> None:
    b = UnstuckBot(model)
    b.leave_result = True
    assert b.get_unstuck("1,1") is True
    assert b.moved                               # the walk off the map moved us away


def test_get_unstuck_falls_through_to_the_hearthstone(model: Map) -> None:
    b = UnstuckBot(model)
    b.home = True
    assert b.get_unstuck("1,1") is True


def test_get_unstuck_returns_false_when_every_way_out_fails(model: Map) -> None:
    b = UnstuckBot(model)
    b.stuck_in_a_row = 5
    assert b.get_unstuck("1,1") is False
    assert b.stuck_in_a_row == 0                # reset in the finally
    assert not b._unsticking
    assert b.fight_count >= 4                   # fights near before each way out


def test_get_unstuck_reraises_a_stop_the_player_pressed(model: Map) -> None:
    b = UnstuckBot(model)
    b.stop_event.is_set = lambda: True

    def stop_here(m: Any, me: dict[str, Any], near: int = 10, far: int = 30) -> tuple[int, int] | None:
        raise Stopped("keys refused")

    b.escape_cell = stop_here
    with pytest.raises(Stopped):
        b.get_unstuck("1,1")
    assert not b._unsticking


def test_get_unstuck_reraises_respawned(model: Map) -> None:
    b = UnstuckBot(model)

    def respawn(k: str, ms: int = 60) -> None:
        raise Respawned()

    b.key = respawn
    with pytest.raises(Respawned):
        b.get_unstuck("1,1")
    assert not b._unsticking


# ---------------------------------------------------------------- clear_way
class ClearBot(WalkingMixin):
    """Owns just enough for clear_way: a snapshot, a target list, and a fight that kills the target
    (so the walk can go on)."""

    def __init__(self, m: Map, targets: list[dict[str, Any]]) -> None:
        self.p = {"move_keys": dict(MOVE_KEYS)}
        self._m = m
        self._targets = list(targets)
        self.walk_only = False
        self.modes_done = False
        self.fight_count = 0
        self.logs: list[str] = []
        self.set_modes_count = 0

    def me(self) -> dict[str, Any]:
        return {"x": 0.0, "y": 0.0, "hp": 100}

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def snap(self) -> dict[str, Any]:
        return {"self": self.me(), "entities": self._targets}

    def targets(self, snap: dict[str, Any], area: Sequence[float] | None = None, only: str | None = None,
                skip: str | None = None, avoid_rects: Sequence[Rect] = (),
                limit: float | None = None) -> list[dict[str, Any]]:
        return [e for e in self._targets if e.get("alive", True)]

    def fight(self, mob: dict[str, Any]) -> None:
        self.fight_count += 1
        mob["alive"] = False

    def set_modes(self) -> None:
        self.set_modes_count += 1


def test_clear_way_fights_only_the_monster_in_the_way() -> None:
    blocking = {"id": -1, "name": "Zombie", "x": 2.0, "y": 0.0, "alive": True}
    far_off = {"id": -2, "name": "Ghoul", "x": 20.0, "y": 20.0, "alive": True}
    b = ClearBot(Map(), [far_off, blocking])
    b.walk_only = True
    b.clear_way((10.0, 0.0))
    assert blocking["alive"] is False            # the one in the way was fought
    assert far_off["alive"] is True              # the far one was left alone
    assert b.fight_count == 1
    assert b.walk_only is True                   # the walking-assistant flag is put back


def test_clear_way_does_nothing_when_the_path_is_clear() -> None:
    b = ClearBot(Map(), [{"id": -1, "name": "Ghoul", "x": 20.0, "y": 20.0, "alive": True}])
    b.clear_way((10.0, 0.0))
    assert b.fight_count == 0


def test_a_hung_game_counts_as_no_stuck(clock: Clock) -> None:
    # a walk round whose readings took longer than GAME_STALL sat out the game hanging: the stuck
    # count starts again instead of a 1.5 s stuck firing for a wall that isn't there
    class W(WalkingMixin):
        resumed_at = 0.0

    w = W()
    w.clock = clock
    best_at = clock.now
    tick = clock.now
    clock.advance(w.GAME_STALL + 2)                 # the game hung during this round
    best_at, stuck = w._no_progress(tick, best_at)
    assert not stuck and best_at == clock.now
    tick = clock.now
    clock.advance(1.6)                              # ...but standing still after that is a stuck
    tick = clock.now - 0.05
    assert w._no_progress(tick, best_at) == (best_at, True)
