# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Farming a map: the profile's modes, the farm loop's decisions (restock, town, targets, walking
the patrol loop or wandering at random, deaths), the day's drop time, ending somewhere no monster
spawns, and picking a walkable random cell. The bot is a small stand-in that inherits only the
farming mixin; everything it calls in another part of the Bot is stubbed and recorded.
"""

from __future__ import annotations

import collections
import contextlib
import itertools
import threading

import pytest
from conftest import failing, stand_in, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import settings
from unclefarmer.engine import town
from unclefarmer.engine.botparts import farming
from unclefarmer.engine.botparts.base import Respawned, Stopped
from unclefarmer.engine.botparts.farming import FarmingMixin
from unclefarmer.engine.botparts.parts import FarmFilter, Rect
from collections.abc import Callable, Sequence
from typing import Any
import typing


UNSET: FarmFilter = (None, "unset", "unset", [])     # farm() has not set the filter yet


class Link:
    def __init__(self, modes: dict[str, int] | dict[str, Any] | None=None) -> None:
        self.modes = dict(modes) if modes else {}
        self.answers: dict[str, Any] = {}

    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> dict[str, Any]:
        if line == "mem modes":
            return dict(self.modes)
        return self.answers.get(line, {})


class Farm(FarmingMixin):
    """A stand-in bot: only the farming mixin is real; every other part of the Bot is a recorder."""

    spawns = False          # what spawns_at answers (static on the real Bot); a test patches it

    def __init__(self, profile: dict[str, Any] | None=None, modes: dict[str, int] | dict[str, Any] | None=None) -> None:
        self.link = Link(modes)
        self.a = self.link
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.p.update(profile or {})
        self.lines: list[str] = []
        self.spoken: list[str] = []
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.kills = 0
        self.restocked_once = False
        self.restock_calls: list[bool] = []
        self.restock_result = False
        self.travels: list[tuple[int | str, tuple[float, float] | None]] = []
        self.travel_error: Exception | None = None
        self.map = 1
        self.sn: dict[str, Any] = {"self": {"id": 1, "x": 5, "y": 5}}
        self.bag_start: dict[str, int] | None = {}
        self.farm_filter: FarmFilter | None = UNSET
        self.timing = None
        self.timing_reports: list[bool] = []
        self.farmed = False
        self.modes_done = False
        self.stop_event = threading.Event()
        self.start_place = None
        self.end_place = ("start",)
        self.targets_result: list[dict[str, Any]] = []
        self.targets_calls: list[tuple[dict[str, Any], Sequence[float] | None, str | None, str | None, Sequence[tuple[float, float, float, float]], float | None]] = []
        self.rest_up_result = False
        self.attackers_result: set[int] = set()
        self.bag_check_result = False
        self.fights: list[dict[str, Any]] = []
        self.walk_calls: list[tuple[float, float]] = []
        self.random_cell_result = (7, 7)
        self.random_cell_calls: list[object] = []
        self.timed_phases: list[str] = []
        self.hearthstone = False
        self.go_home_result = True
        self.go_home_calls: list[int] = []
        self.leave_calls: list[bool] = []
        self.set_modes_calls: list[bool] = []
        self.count_kills_calls: list[dict[str, Any] | None] = []
        self.look_after_calls: list[dict[str, Any] | None] = []
        self.map_keys: dict[str, tuple[str, int]] = {}   # key -> (mode it switches, to what)

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def key(self, k: str, ms: int | None=None) -> None:
        self.keys.append(k)
        if k in self.map_keys:
            field, want = self.map_keys[k]
            self.link.modes[field] = want

    @contextlib.contextmanager
    def timed(self, phase: str) -> typing.Generator[None, None, None]:
        self.timed_phases.append(phase)
        yield

    def timing_report(self, final: bool=False) -> None:
        self.timing_reports.append(final)

    def contribution_now(self) -> int:
        return -1

    def travel(self, dst: int | str, cell: tuple[float, float] | None=None) -> None:
        self.travels.append((dst, cell))
        if self.travel_error is not None:
            raise self.travel_error
        assert isinstance(dst, int), "the farming tests travel by map id"
        self.map = dst

    def map_id(self) -> int:
        return self.map

    def snap(self) -> dict[str, Any]:
        return self.sn

    def me(self) -> dict[str, Any]:
        return self.sn["self"]

    def count_kills(self, snap: dict[str, Any] | None=None) -> list[int]:
        self.count_kills_calls.append(snap)
        return []

    def look_after(self, me: dict[str, Any] | None=None) -> dict[str, Any]:
        self.look_after_calls.append(me)
        return me if me is not None else self.me()

    def bag_check(self) -> bool:
        return self.bag_check_result

    def rest_up(self, me: dict[str, Any]) -> bool:
        return self.rest_up_result

    def fight(self, mob: dict[str, Any]) -> None:
        self.fights.append(mob)

    def targets(self, snap: dict[str, Any], area: Sequence[float] | None=None, only: str | None=None, skip: str | None=None, avoid_rects: Sequence[tuple[float, float, float, float]]=(), limit: float | None=None) -> list[dict[str, Any]]:
        self.targets_calls.append((snap, area, only, skip, avoid_rects, limit))
        return list(self.targets_result)

    def next_target(self, snap: dict[str, Any], mobs: list[dict[str, Any]]) -> dict[str, Any]:
        return mobs[0]

    def note_pick(self, snap: dict[str, Any], mob: dict[str, Any], mobs: list[dict[str, Any]]) -> None:
        pass

    def note_search(self, map_id: int, me: dict[str, Any], cell: tuple[float, float], start: float, result: str) -> None:
        pass

    def walk_to(self, cell: tuple[float, float], map_change: bool=False, near: Callable[[dict[str, Any]], bool] | None=None) -> str:
        self.walk_calls.append(cell)
        return "arrived"

    def attackers(self) -> set[int]:
        return set(self.attackers_result)

    @staticmethod
    def spawns_at(mid: int, cell: tuple[float, float]) -> bool:
        return Farm.spawns

    def has_hearthstone(self) -> bool:
        return self.hearthstone

    def go_home(self, tries: int=3) -> bool:
        self.go_home_calls.append(tries)
        return self.go_home_result


# ------------------------------------------------------------------ helpers
def labels(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(farming.atlas, "label", stand_in(farming.atlas.label, lambda mid: "Map %s (%s)" % (mid, mid)))


def zone_stub(monkeypatch: pytest.MonkeyPatch, elite: list[tuple[int, int, int, int]] | tuple[()]=(), boss: list[tuple[int, int, int, int]] | tuple[()]=(), all_zones: tuple[int]=(1,)) -> None:
    def zones(mid: int | str, kinds: tuple[str, ...] = ("monster", "elite", "boss")) -> list[dict[str, Any]]:
        if kinds == ("elite",):
            return [{"rect": r} for r in elite]
        if kinds == ("boss",):
            return [{"rect": r} for r in boss]
        return [{"rect": (0, 0, 1, 1)} for _ in all_zones]
    monkeypatch.setattr(farming.atlas, "zones", zones)


def farm_capture(bot: Farm, **kw: Any) -> dict[str, Any]:
    """Run farm() with a fake loop that captures what it was handed; return the capture."""
    cap: dict[str, Any] = {}

    def capture(map_id: int, area: Sequence[float] | None, only: str, skip: str, avoid: list[Rect],
                loop: list[tuple[int, int]] | None, chase: float | None, done: Callable[[], bool],
                sighted: Callable[[dict[str, Any]], bool], last_supply_check: float) -> None:
        cap.update(map_id=map_id, area=area, only=only, skip=skip, avoid=avoid, loop=loop,
                   chase=chase, done=done, sighted=sighted, timing=bot.timing,
                   farm_filter=bot.farm_filter)

    bot._farm_loop = capture
    bot.restock_now = lambda start=False: (bot.restock_calls.append(start), bot.restock_result)[1]
    bot.set_modes = lambda: bot.set_modes_calls.append(True)
    bot.farm(**kw)
    return cap


def run_loop(bot: Farm, passes: int=1, map_id: int=1, area: Sequence[float] | None=None, only: str="", skip: str="",
             avoid: Sequence[Rect]=(), loop: list[tuple[int, int]] | None=None, chase: float | None=None,
             last_supply_check: float | None=None, sighted: Callable[[dict[str, Any]], bool] | None=None) -> None:
    """Drive _farm_loop for `passes` body runs, then stop."""
    state = {"n": 0}

    def done():
        state["n"] += 1
        return state["n"] > passes

    if sighted is None:
        sighted = lambda me: False
    if last_supply_check is None:
        last_supply_check = CLOCK.time()
    bot._farm_loop(map_id, area, only, skip, list(avoid), loop, chase, done, sighted, last_supply_check)


# ================================================================== set_modes
def test_set_modes_off_does_nothing() -> None:
    b = Farm(profile={"set_modes": False})
    b.set_modes()
    assert b.keys == []


def test_set_modes_link_error_is_reported() -> None:
    b = Farm()
    b.a.json = lambda line: {"error": "busy"}
    b.set_modes()
    assert any("can't read the game's modes (busy)" in line for line in b.lines)


def test_set_modes_already_right_presses_nothing() -> None:
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 1, "target": 2})
    b.set_modes()
    assert b.keys == []


def test_set_modes_presses_until_the_game_agrees() -> None:
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 0, "target": 0})
    b.map_keys = {"b": ("pk", 1), "v": ("target", 2)}
    b.set_modes()
    assert b.keys == ["b", "v"]
    assert "mode set: pacifist mode with counterattack" in b.lines
    assert "mode set: enemies" in b.lines


def test_set_modes_leaves_a_locked_pk_mode_alone() -> None:
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 0, "target": 2, "pk_locked": True})
    b.set_modes()
    assert b.keys == []
    assert any("PK mode is locked here" in line and "pacifist mode" in line for line in b.lines)


def test_set_modes_warns_when_the_game_refuses() -> None:
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 1, "target": 0})
    b.set_modes()
    assert b.keys == ["v", "v", "v", "v", "v"]          # len(TARGET_MODES) + 1 tries
    assert any("couldn't set the mode to enemies with the v key" in line for line in b.spoken)


def test_set_modes_skips_a_mode_the_game_does_not_report() -> None:
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 1})
    b.set_modes()
    assert b.keys == []


# ====================================================================== farm
def test_a_run_that_cannot_reach_its_map_has_not_farmed(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    b = Farm()
    b.travel_error = Stopped("no way there")
    with pytest.raises(Stopped):
        b.farm(5121, minutes=1)
    assert b.farmed is False


def test_farm_sets_up_and_tears_down(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [10, 10, 20, 20]))
    zone_stub(monkeypatch)
    monkeypatch.setattr(farming.atlas, "patrol_loop", stand_in(farming.atlas.patrol_loop, lambda mid, variant: None))
    b = Farm()
    cap = farm_capture(b, map_id=5121)
    assert b.farmed is True
    assert b.restock_calls == [True] and b.restocked_once is True
    assert b.travels == [(5121, None)]
    assert b.set_modes_calls == [True]
    assert b.modes_done is True
    assert cap["area"] == [10, 10, 20, 20]
    assert cap["loop"] is None and cap["chase"] is None
    assert cap["farm_filter"] == ([10, 10, 20, 20], "", "", [])
    assert cap["timing"] is not None and b.timing is None
    assert b.timing_reports == [True]
    assert b.lines[0].startswith("farming ") and b.lines[-1].startswith("finished farming ")
    assert b.farm_filter is None


def test_farm_keeps_the_area_it_was_given(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    monkeypatch.setattr(farming.atlas, "patrol_loop", stand_in(farming.atlas.patrol_loop, lambda mid, variant: None))
    b = Farm()
    cap = farm_capture(b, map_id=5121, area=[1, 2, 3, 4])
    assert cap["area"] == [1, 2, 3, 4]


def test_farm_avoids_bosses_by_default_and_elites_on_request(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "patrol_loop", stand_in(farming.atlas.patrol_loop, lambda mid, variant: None))
    zone_stub(monkeypatch, elite=[(7, 7, 8, 8)], boss=[(2, 2, 3, 3)])
    b = Farm()
    cap = farm_capture(b, map_id=1)
    assert cap["avoid"] == [(2, 2, 3, 3)]

    b2 = Farm()
    cap2 = farm_capture(b2, map_id=1, avoid_elites=True)
    assert cap2["avoid"] == [(7, 7, 8, 8), (2, 2, 3, 3)]

    b3 = Farm()
    cap3 = farm_capture(b3, map_id=1, avoid_bosses=False)
    assert cap3["avoid"] == []


def test_farm_on_a_patrol_loop_uses_chase_range(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    monkeypatch.setattr(farming.atlas, "patrol_loop", stand_in(farming.atlas.patrol_loop, lambda mid, variant: [(1, 1), (2, 2)]))
    b = Farm(profile={"chase_range": 12})
    cap = farm_capture(b, map_id=1, patrol="full")
    assert cap["loop"] == [(1, 1), (2, 2)] and cap["chase"] == 12
    cap["sighted"](None)
    assert b.targets_calls[-1][-1] == 12                 # the limit targets() was given


def test_farm_wandering_has_no_chase_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    b = Farm()
    cap = farm_capture(b, map_id=1, patrol="random")
    assert cap["loop"] is None and cap["chase"] is None
    b.targets_result = [{"id": -1}]
    assert cap["sighted"](None) is True
    assert b.targets_calls[-1][-1] is None


def test_farm_done_by_minutes(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Farm()
    cap = farm_capture(b, map_id=1, minutes=1)
    assert cap["done"]() is False
    clock.advance(60)
    assert cap["done"]() is True


def test_farm_done_by_kills(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    b = Farm()
    cap = farm_capture(b, map_id=1, kills=2)
    assert cap["done"]() is False
    b.kills = 2
    assert cap["done"]() is True


def test_farm_done_by_until(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    flag = {"stop": False}
    b = Farm()
    cap = farm_capture(b, map_id=1, until=lambda: flag["stop"])
    assert cap["done"]() is False
    flag["stop"] = True
    assert cap["done"]() is True


def test_farm_counts_the_bag_at_start(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    monkeypatch.setattr(town, "counts", stand_in(town.counts, lambda b: {"sold": 1}))
    b = Farm()
    del b.bag_start
    farm_capture(b, map_id=1)
    assert b.bag_start == {"sold": 1}


def test_farm_keeps_the_bag_count_it_already_made(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: [0, 0, 1, 1]))
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid, kinds=("monster", "elite", "boss"): []))
    monkeypatch.setattr(town, "counts", stand_in(town.counts, lambda b: pytest.fail("counts should not run again")))
    b = Farm()
    b.bag_start = {"kept": 1}
    farm_capture(b, map_id=1)
    assert b.bag_start == {"kept": 1}


# ================================================================= _farm_loop
def test_loop_fights_a_target(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    mob = {"id": -5, "name": "Wolf", "x": 6, "y": 5}
    b.targets_result = [mob]
    calls: list[str] = []
    monkeypatch.setattr(farming.pets, "keep_out", stand_in(farming.pets.keep_out, lambda b: calls.append("keep")))
    monkeypatch.setattr(farming.pets, "look_after", stand_in(farming.pets.look_after, lambda b, snap: calls.append("pet")))
    run_loop(b)
    assert b.fights == [mob]
    assert b.count_kills_calls == [b.sn]
    assert b.look_after_calls == [b.sn["self"]]
    assert calls == ["keep", "pet"]
    assert b.targets_calls[0][1:3] == (None, "")         # area, only
    assert b.timed_phases[:1] == ["town"]


def test_loop_rests_before_taking_a_target() -> None:
    b = Farm()
    b.targets_result = [{"id": -5, "name": "Wolf", "x": 6, "y": 5}]
    b.rest_up_result = True
    run_loop(b)
    assert b.fights == []
    assert b.walk_calls == []


def test_loop_goes_to_town_and_starts_over() -> None:
    b = Farm()
    b.bag_check_result = True
    b.targets_result = [{"id": -5, "hits": 1}]
    run_loop(b)
    assert b.fights == []
    assert b.targets_calls == []                          # the town trip happens before targets


def test_loop_refills_keys_and_starts_over(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    monkeypatch.setattr(farming.keyslots, "refill", stand_in(farming.keyslots.refill, lambda bot: True))
    b.targets_result = [{"id": -5}]
    run_loop(b)
    assert b.fights == []
    assert b.targets_calls == []


def test_loop_restocks_and_comes_back(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Farm()
    b.restock_result = True
    b.restock_now = lambda start=False: (b.restock_calls.append(start), True)[1]
    b.set_modes = lambda: b.set_modes_calls.append(True)
    b.targets_result = [{"id": -5, "name": "Wolf", "x": 6, "y": 5}]
    run_loop(b, last_supply_check=clock.now - 100)
    assert b.restock_calls == [False]
    assert b.travels == [(1, None)]
    assert b.set_modes_calls == [True]
    assert any("back to Map 1 (1)" in line for line in b.lines)
    assert b.fights == [b.targets_result[0]]


def test_loop_returns_after_leaving_the_map(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    b = Farm()
    b.map = 2
    b.set_modes = lambda: b.set_modes_calls.append(True)
    run_loop(b, map_id=1, loop=[(5, 5)])
    assert b.travels == [(1, None)]
    assert b.set_modes_calls == [True]
    assert any("left Map 1 (1)" in line and "going back" in line for line in b.lines)


def test_loop_walks_the_patrol_loop() -> None:
    b = Farm()
    loop = [(1, 1), (9, 9)]
    run_loop(b, passes=2, loop=loop)
    assert b.walk_calls == [(1, 1), (9, 9)]


def test_loop_wanders_to_a_random_cell() -> None:
    b = Farm()
    calls: list[tuple[int, Sequence[float] | None, dict[str, Any], int]] = []

    def random_cell(map_id: int, area: Sequence[float] | None, me: dict[str, Any], radius: int = 40) -> tuple[int, int] | None:
        calls.append((map_id, area, me, radius))
        return (7, 7)

    b.random_cell = random_cell
    run_loop(b)
    assert b.walk_calls == [(7, 7)]
    assert calls == [(1, None, b.sn["self"], 40)]


def test_loop_sleeps_when_there_is_nowhere_to_walk() -> None:
    b = Farm()
    b.random_cell = lambda map_id, area, me, radius=40: None
    run_loop(b, passes=2)
    assert b.sleeps == [1.0, 1.0]
    assert b.walk_calls == []


def test_loop_goes_back_after_a_respawn(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    b = Farm()
    b.set_modes = lambda: b.set_modes_calls.append(True)
    seen = {"n": 0}

    def bag_check():
        seen["n"] += 1
        if seen["n"] == 1:
            raise Respawned()
        return False

    b.bag_check = bag_check
    run_loop(b, loop=[(5, 5)])
    assert b.travels == [(1, None)]
    assert b.set_modes_calls == [True]
    assert any("going back to Map 1 (1)" in line for line in b.lines)


def test_loop_keeps_going_when_the_respawn_travel_fails() -> None:
    b = Farm()
    b.travel_error = Respawned()
    b.set_modes = lambda: b.set_modes_calls.append(True)

    def bag_check():
        raise Respawned()

    b.bag_check = bag_check
    run_loop(b)                                            # must not raise, must not set modes
    assert b.set_modes_calls == []


# ========================================================== drop_time_check
def test_drop_time_off_does_nothing() -> None:
    b = Farm(profile={"stop_on_drop_time": False})
    b.finish = lambda: pytest.fail("finish must not run")
    b.drop_time_check({"drop_seconds": 10})


def test_drop_time_missing_or_negative_returns() -> None:
    b = Farm()
    b.finish = lambda: pytest.fail("finish must not run")
    b.drop_time_check({})
    b.drop_time_check({"drop_seconds": None})
    b.drop_time_check({"drop_seconds": -1})


def test_drop_time_warns_once_when_plenty_is_left() -> None:
    b = Farm()
    b.drop_time_check({"drop_seconds": 1000, "age": 0})
    assert any("drop time:" in line and "farming stops in about 11 minutes" in line for line in b.lines)
    n = len(b.lines)
    b.drop_time_check({"drop_seconds": 1000, "age": 0})
    assert len(b.lines) == n and b.said_drop is True


def test_drop_time_stops_the_run_when_used_up() -> None:
    b = Farm()
    finished: list[bool] = []
    b.finish = lambda: finished.append(True)
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 300, "age": 0})
    assert finished == [True]
    assert any("drop time is nearly used up (5 min left)" in line for line in b.spoken)


def test_drop_time_subtracts_the_age() -> None:
    b = Farm()
    finished: list[bool] = []
    b.finish = lambda: finished.append(True)
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 400, "age": 200})   # 200 left: less than the tail
    assert finished == [True]
    assert any("nearly used up" in line for line in b.spoken)


# ============================================================ leave_monsters
def test_leave_monsters_on_a_map_without_zones_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid: []))
    b = Farm()
    b.leave_monsters()
    assert b.travels == []


def test_leave_monsters_walks_to_the_nearest_safe_map(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid: [1] if mid in (1, 2) else []))
    monkeypatch.setattr(farming.atlas, "maps", stand_in(farming.atlas.maps, lambda: {
        1: {"exits": [{"dest": 2}]},
        2: {"exits": [{"dest": 3}, {"dest": 1}, {"dest": None}]},
        3: {"exits": []}}))
    b = Farm()
    b.leave_monsters()
    assert b.travels == [(3, None)]
    assert any("done here - going to Map 3 (3), where no monsters spawn" in line for line in b.lines)


def test_leave_monsters_says_when_there_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", stand_in(farming.atlas.zones, lambda mid: [1]))
    monkeypatch.setattr(farming.atlas, "maps", stand_in(farming.atlas.maps, lambda: {
        1: {"exits": [{"dest": 2}]},
        2: {"exits": [{"dest": 1}]}}))
    b = Farm()
    b.leave_monsters()
    assert b.travels == []
    assert any("no map without monsters near Map 1 (1)" in line for line in b.spoken)


# ==================================================================== finish
def test_finish_here_stays(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    off: list[bool] = []
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: off.append(True)))
    b = Farm()
    b.end_place = ("here",)
    b.finish()
    assert off == [True] and b.travels == []


def test_finish_start_without_a_start_place_goes_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    off: list[bool] = []
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: off.append(True)))
    zone_stub(monkeypatch)
    b = Farm()
    b.start_place = None
    b.hearthstone = True
    b.finish()
    assert any("going home with the hearthstone" in line for line in b.lines)
    assert b.go_home_calls == [1] and b.travels == [] and off == [True]


def test_finish_starting_where_monsters_spawn_goes_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    zone_stub(monkeypatch)
    b.start_place = (5, 10, 20)
    monkeypatch.setattr(Farm, "spawns", True)
    b.hearthstone = False
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert any("started where monsters spawn" in line for line in b.lines)
    assert b.leave_calls == [True] and b.travels == []


def test_finish_start_walks_back_when_far(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.start_place = (5, 10, 20)
    monkeypatch.setattr(Farm, "spawns", False)
    b.map = 1
    b.sn = {"self": {"id": 1, "x": 0, "y": 0}}
    b.finish()
    assert b.travels == [(5, (10, 20))]
    assert any("going back to Map 5 (5) (10,20)" in line for line in b.spoken)


def test_finish_start_does_not_walk_when_already_there(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.start_place = (5, 10, 20)
    monkeypatch.setattr(Farm, "spawns", False)
    b.map = 5
    b.sn = {"self": {"id": 1, "x": 11, "y": 20}}
    b.finish()
    assert b.travels == []


def test_finish_place_walks_there(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("place", 7, [30, 40])
    b.map = 1
    b.finish()
    assert b.travels == [(7, (30, 40))]


def test_finish_place_without_a_cell_walks_to_the_map(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("place", 7, None)
    b.map = 1
    b.finish()
    assert b.travels == [(7, None)]
    assert any("going back to Map 7 (7)" in line and "(" not in line.split("Map 7 (7)", 1)[1]
               for line in b.spoken)


def test_finish_with_the_hearthstone_works(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("hearthstone",)
    b.hearthstone = True
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert b.leave_calls == [] and b.go_home_calls == [1] and b.travels == []


def test_finish_hearthstone_that_fails_leaves_the_monsters(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("hearthstone",)
    b.hearthstone = False
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert b.leave_calls == [True] and b.go_home_calls == []


def test_finish_unknown_kind_goes_safe(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    zone_stub(monkeypatch)
    b = Farm()
    b.end_place = ("nonsense",)
    b.hearthstone = True
    b.finish()
    assert any("going home with the hearthstone" in line for line in b.lines)


def test_finish_reraises_a_respawn(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("place", 7, [1, 1])
    b.travel_error = Respawned()
    with pytest.raises(Respawned):
        b.finish()


def test_finish_stopped_with_stop_event_reraises(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("place", 7, [1, 1])
    b.travel_error = Stopped("no way")
    b.stop_event.set()
    with pytest.raises(Stopped):
        b.finish()


def test_finish_stopped_without_stop_event_goes_to_a_safe_map(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    b = Farm()
    b.end_place = ("place", 7, [1, 1])
    b.travel_error = Stopped("no way")
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert b.leave_calls == [True]
    assert any("couldn't get there - going to the nearest map without monsters" in line
               for line in b.spoken)


def test_finish_recalls_the_pet_at_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", stand_in(farming.beasts.off, lambda b: None))
    recalled: list[bool] = []
    monkeypatch.setattr(farming.pets, "recall", stand_in(farming.pets.recall, lambda b: recalled.append(True)))
    b = Farm(profile={"pet_recall_at_end": True})
    b.end_place = ("here",)
    b.finish()
    assert recalled == [True]


# ================================================================ restock_now
def test_restock_now_without_rules_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "restock", stand_in(town.restock, failing("must not run")))
    b = Farm(profile={"restock": []})
    assert FarmingMixin.restock_now(b) is False


def test_restock_now_delegates_to_town(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[object, object, bool]] = []
    monkeypatch.setattr(town, "restock", stand_in(town.restock, lambda b, rules, start=False: calls.append((b, rules, start)) or "R"))
    b = Farm(profile={"restock": [{"item": "Large Red Potion"}]})
    assert FarmingMixin.restock_now(b) == "R"
    assert FarmingMixin.restock_now(b, start=True) == "R"
    assert calls == [(b, [{"item": "Large Red Potion"}], False), (b, [{"item": "Large Red Potion"}], True)]


# ================================================================= random_cell
class Gate:
    def __init__(self, on: bool) -> None:
        self.on = on

    def any(self) -> bool:
        return self.on


class Model:
    def __init__(self, floor: set[tuple[int, int]], comps: dict[tuple[int, int], int] | None=None, portals: list[tuple[int, int]] | tuple[()]=(), two_way: list[int] | tuple[()]=(), gate: bool=False, W: int=10) -> None:
        self.W = W
        self.floor = set(floor)
        self.comps = dict(comps or {})
        self.portal: collections.defaultdict[tuple[int, int], bool] = collections.defaultdict(bool)
        for p in portals:
            self.portal[p] = True
        self.gate = Gate(gate)
        self.way = set(two_way)
        self.two_way_calls = 0

    def ok(self, x: int, y: int) -> bool:
        return (x, y) in self.floor

    def comp_of(self, x: int, y: int) -> int:
        return self.comps.get((x, y), -1)       # -1: a cell the test gave no component

    def two_way(self, x: int, y: int) -> set[int]:
        self.two_way_calls += 1
        return set(self.way)


def grid(x0: int=1, y0: int=1, x1: int=8, y1: int=9) -> set[tuple[int, int]]:
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def randint_seq(values: list[int]) -> typing.Callable[[int, int], int]:
    it = itertools.chain(values, itertools.repeat(values[-1]))
    return lambda a, b: next(it)


def test_random_cell_returns_a_walkable_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (8, 9)


def test_random_cell_skips_a_map_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps, portals=[(9, 8)])       # portal key is (y, x) for cell (8, 9)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9, 2, 2]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (2, 2)


def test_random_cell_skips_a_walled_off_part(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 2 for c in grid()}
    comps[(5, 5)] = comps[(2, 2)] = 1
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9, 2, 2]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (2, 2)


def test_random_cell_respects_one_way_gates(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps, gate=True, two_way=[2 * 10 + 2])   # the two-way area holds (2, 2)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9, 2, 2]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (2, 2)
    assert m.two_way_calls == 1


def test_random_cell_gives_up_after_many_tries(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps, portals=[(9, 8)])
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) is None


def test_random_cell_from_a_cell_the_map_calls_wall(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 0, "y": 0}) == (8, 9)


def test_random_cell_stays_inside_the_area_when_we_stand_far_outside_it(monkeypatch: pytest.MonkeyPatch) -> None:
    # The candidate range is the area narrowed to `radius` around us. Standing further outside the
    # area than that used to invert the range - randint raises on that and the run stopped with
    # "unexpected error: empty range for randrange()" - so it now falls back to the whole area.
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    asked: list[tuple[int, int]] = []

    def randint(low: int, high: int) -> int:
        asked.append((low, high))
        return low

    monkeypatch.setattr(farming.random, "randint", randint)
    c = b.random_cell(1, (1, 1, 8, 9), {"x": 100, "y": 5})
    assert asked and all(low <= high for low, high in asked)     # never an inverted range
    assert c is not None and 1 <= c[0] <= 8 and 1 <= c[1] <= 9


def test_random_cell_uses_the_map_size_without_an_area(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", stand_in(farming.atlas.model, lambda mid: m))
    monkeypatch.setattr(farming.atlas, "maps", stand_in(farming.atlas.maps, lambda: {1: {"w": 8, "h": 9}}))
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, None, {"x": 5, "y": 5}) == (8, 9)


def test_a_plans_drop_time_to_save_beats_the_characters() -> None:
    b = Farm(profile={"stop_on_drop_time": False, "drop_keep_minutes": 0})
    b.drop_keep = 60                      # keep_drop 60 from the plan
    finished: list[bool] = []
    b.finish = lambda: finished.append(True)
    b.drop_time_check({"drop_seconds": 60 * 60 + 300 + 120, "age": 0})    # two minutes to spare
    assert finished == []
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 60 * 60 + 300 - 10, "age": 0})
    assert finished == [True]


# ================================================================== a dungeon (instance=True)
def instance_loop(bot: Farm, passes: int=1, map_id: int=1, loop: list[tuple[int, int]] | None=None, last_supply_check: float | None=None) -> None:
    bot.in_instance = True
    try:
        run_loop(bot, passes=passes, map_id=map_id, loop=loop, last_supply_check=last_supply_check)
    finally:
        bot.in_instance = False


def test_in_a_dungeon_being_moved_out_ends_the_loop_without_going_back(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    b = Farm()
    b.map = 2
    instance_loop(b, passes=5, map_id=1)
    assert b.travels == []
    assert any("moved out of Map 1 (1)" in line for line in b.lines)


def test_in_a_dungeon_no_trip_out_and_no_resting(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Farm()
    b.restock_now = lambda start=False: (b.restock_calls.append(start), True)[1]
    b.bag_check = lambda: pytest.fail("bag_check would leave the dungeon")
    b.share_trip = lambda map_id: pytest.fail("the secret room would leave the dungeon")
    reads: list[bool] = []
    b.instance_reads = lambda: reads.append(True)
    b.rest_up_result = True
    mob = {"id": -5, "name": "Scorpion", "x": 6, "y": 5}
    b.targets_result = [mob]
    monkeypatch.setattr(farming.pets, "keep_out", stand_in(farming.pets.keep_out, lambda b: None))
    monkeypatch.setattr(farming.pets, "look_after", stand_in(farming.pets.look_after, lambda b, snap: None))
    monkeypatch.setattr(farming.keyslots, "refill", stand_in(farming.keyslots.refill, lambda bot: False))
    monkeypatch.setattr(farming.bosses, "keep_away", stand_in(farming.bosses.keep_away, lambda bot, snap, map_id, area: False))
    instance_loop(b, passes=1, last_supply_check=clock.now - 100)    # a restock check is due
    assert b.restock_calls == []
    assert reads == [True]
    assert b.fights == [mob]                    # rest_up said rest: in a dungeon it fights on


def test_in_a_dungeon_a_death_outside_ends_it_and_inside_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    for outside, ends in ((True, True), (False, False)):
        b = Farm()
        n = {"i": 0}

        def reads(b: Farm = b, n: dict[str, int] = n, outside: bool = outside) -> None:
            n["i"] += 1
            if n["i"] == 1:
                if outside:
                    b.map = 7
                raise Respawned()

        b.instance_reads = reads
        instance_loop(b, passes=3, loop=[(5, 5)])
        assert b.travels == []
        assert any("died and came back outside" in line for line in b.lines) == ends
        assert n["i"] == (1 if ends else 3)


def test_farm_in_a_dungeon_neither_restocks_nor_travels_and_sets_the_pack(monkeypatch: pytest.MonkeyPatch) -> None:
    zone_stub(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", stand_in(farming.atlas.monster_area, lambda mid: None))
    monkeypatch.setattr(farming.atlas, "patrol_loop", stand_in(farming.atlas.patrol_loop, lambda mid, variant: [(1, 1)]))
    monkeypatch.setattr(farming.atlas, "label", stand_in(farming.atlas.label, lambda mid: str(mid)))
    b = Farm()
    b.map = 1539
    seen: dict[str, object] = {}

    def loop(map_id: int, area: Sequence[float] | None, only: str, skip: str, avoid: list[Rect],
             loop: list[tuple[int, int]] | None, chase: float | None, done: Callable[[], bool],
             sighted: Callable[[dict[str, Any]], bool], last_supply_check: float) -> None:
        seen.update(instance=b.in_instance, pack=b.pack_min)

    b._farm_loop = loop
    b.restock_now = lambda start=False: pytest.fail("no restock inside a dungeon")
    b.set_modes = lambda: None
    b.farm(1539, patrol="quick", instance=True, pack=2)
    assert seen == {"instance": True, "pack": 2}
    assert b.travels == []
    assert (b.in_instance, b.pack_min) == (False, 3)        # back to farming's own afterwards
