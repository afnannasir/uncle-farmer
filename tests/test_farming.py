# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""Farming a map: the profile's modes, the farm loop's decisions (restock, town, targets, walking
the patrol loop or wandering at random, deaths), the day's drop time, ending somewhere no monster
spawns, and picking a walkable random cell. The bot is a small stand-in that inherits only the
farming mixin; everything it calls in another part of the Bot is stubbed and recorded.
"""

import collections
import contextlib
import itertools
import threading

import pytest
from conftest import Clock

from unclefarmer.clock import CLOCK
from unclefarmer import settings
from unclefarmer.engine import town
from unclefarmer.engine.botparts import farming
from unclefarmer.engine.botparts.base import Respawned, Stopped
from unclefarmer.engine.botparts.farming import FarmingMixin


class Link:
    def __init__(self, modes=None):
        self.modes = dict(modes) if modes else {}
        self.answers = {}

    def json(self, line):
        if line == "mem modes":
            return dict(self.modes)
        return self.answers.get(line, {})


class Farm(FarmingMixin):
    """A stand-in bot: only the farming mixin is real; every other part of the Bot is a recorder."""

    def __init__(self, profile=None, modes=None):
        self.a = Link(modes)
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.p.update(profile or {})
        self.lines = []
        self.spoken = []
        self.keys = []
        self.sleeps = []
        self.kills = 0
        self.restocked_once = False
        self.restock_calls = []
        self.restock_result = False
        self.travels = []
        self.travel_error = None
        self.map = 1
        self.sn = {"self": {"id": 1, "x": 5, "y": 5}}
        self.bag_start = {}
        self.farm_filter = "unset"
        self.timing = None
        self.timing_reports = []
        self.farmed = False
        self.modes_done = False
        self.stop_event = threading.Event()
        self.start_place = None
        self.end_place = ("start",)
        self.targets_result = []
        self.targets_calls = []
        self.rest_up_result = False
        self.attackers_result = set()
        self.bag_check_result = False
        self.fights = []
        self.walk_calls = []
        self.random_cell_result = (7, 7)
        self.random_cell_calls = []
        self.timed_phases = []
        self.spawns = False
        self.hearthstone = False
        self.go_home_result = True
        self.go_home_calls = []
        self.leave_calls = []
        self.set_modes_calls = []
        self.count_kills_calls = []
        self.look_after_calls = []
        self.map_keys = {}

    def log(self, line, speak=False):
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def sleep(self, s):
        self.sleeps.append(s)

    def key(self, k, ms=None):
        self.keys.append(k)
        if k in self.map_keys:
            field, want = self.map_keys[k]
            self.a.modes[field] = want

    @contextlib.contextmanager
    def timed(self, phase):
        self.timed_phases.append(phase)
        yield

    def timing_report(self, final=False):
        self.timing_reports.append(final)

    def contribution_now(self):
        return -1

    def travel(self, dst, cell=None):
        self.travels.append((dst, cell))
        if self.travel_error is not None:
            raise self.travel_error
        self.map = dst

    def map_id(self):
        return self.map

    def snap(self):
        return self.sn

    def me(self):
        return self.sn["self"]

    def count_kills(self, snap=None):
        self.count_kills_calls.append(snap)

    def look_after(self, me=None):
        self.look_after_calls.append(me)

    def bag_check(self):
        return self.bag_check_result

    def rest_up(self, me):
        return self.rest_up_result

    def fight(self, mob):
        self.fights.append(mob)

    def targets(self, snap, area=None, only=None, skip=None, avoid_rects=(), limit=None):
        self.targets_calls.append((snap, area, only, skip, avoid_rects, limit))
        return list(self.targets_result)

    def next_target(self, snap, mobs):
        return mobs[0]

    def note_pick(self, snap, mob, mobs):
        pass

    def note_search(self, map_id, me, cell, start, result):
        pass

    def walk_to(self, cell, map_change=False, near=None):
        self.walk_calls.append(cell)
        return "arrived"

    def attackers(self):
        return set(self.attackers_result)

    def spawns_at(self, mid, cell):
        return self.spawns

    def has_hearthstone(self):
        return self.hearthstone

    def go_home(self, tries=3):
        self.go_home_calls.append(tries)
        return self.go_home_result


# ------------------------------------------------------------------ helpers
def labels(monkeypatch):
    monkeypatch.setattr(farming.atlas, "label", lambda mid: "Map %s (%s)" % (mid, mid))


def zone_stub(monkeypatch, elite=(), boss=(), all_zones=(1,)):
    def zones(mid, kinds=("monster", "elite", "boss")):
        if kinds == ("elite",):
            return [{"rect": r} for r in elite]
        if kinds == ("boss",):
            return [{"rect": r} for r in boss]
        return [{"rect": (0, 0, 1, 1)} for _ in all_zones]
    monkeypatch.setattr(farming.atlas, "zones", zones)


def farm_capture(bot, **kw):
    """Run farm() with a fake loop that captures what it was handed; return the capture."""
    cap = {}

    def loop(map_id, area, only, skip, avoid, loop_, chase, done, sighted, last_supply_check):
        cap.update(map_id=map_id, area=area, only=only, skip=skip, avoid=avoid, loop=loop_,
                   chase=chase, done=done, sighted=sighted, timing=bot.timing,
                   farm_filter=bot.farm_filter)

    bot._farm_loop = loop
    bot.restock_now = lambda start=False: (bot.restock_calls.append(start), bot.restock_result)[1]
    bot.set_modes = lambda: bot.set_modes_calls.append(True)
    bot.farm(**kw)
    return cap


def run_loop(bot, passes=1, map_id=1, area=None, only="", skip="", avoid=(), loop=None, chase=None,
             last_supply_check=None, sighted=None):
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
def test_set_modes_off_does_nothing():
    b = Farm(profile={"set_modes": False})
    b.set_modes()
    assert b.keys == []


def test_set_modes_link_error_is_reported():
    b = Farm()
    b.a.json = lambda line: {"error": "busy"}
    b.set_modes()
    assert any("can't read the game's modes (busy)" in line for line in b.lines)


def test_set_modes_already_right_presses_nothing():
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 1, "target": 2})
    b.set_modes()
    assert b.keys == []


def test_set_modes_presses_until_the_game_agrees():
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 0, "target": 0})
    b.map_keys = {"b": ("pk", 1), "v": ("target", 2)}
    b.set_modes()
    assert b.keys == ["b", "v"]
    assert "mode set: pacifist mode with counterattack" in b.lines
    assert "mode set: enemies" in b.lines


def test_set_modes_leaves_a_locked_pk_mode_alone():
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 0, "target": 2, "pk_locked": True})
    b.set_modes()
    assert b.keys == []
    assert any("PK mode is locked here" in line and "pacifist mode" in line for line in b.lines)


def test_set_modes_warns_when_the_game_refuses():
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 1, "target": 0})
    b.set_modes()
    assert b.keys == ["v", "v", "v", "v", "v"]          # len(TARGET_MODES) + 1 tries
    assert any("couldn't set the mode to enemies with the v key" in line for line in b.spoken)


def test_set_modes_skips_a_mode_the_game_does_not_report():
    b = Farm(profile={"pk_mode": 1, "target_mode": 2}, modes={"pk": 1})
    b.set_modes()
    assert b.keys == []


# ====================================================================== farm
def test_a_run_that_cannot_reach_its_map_has_not_farmed(monkeypatch):
    labels(monkeypatch)
    b = Farm()
    b.travel_error = Stopped("no way there")
    with pytest.raises(Stopped):
        b.farm(5121, minutes=1)
    assert b.farmed is False


def test_farm_sets_up_and_tears_down(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [10, 10, 20, 20])
    zone_stub(monkeypatch)
    monkeypatch.setattr(farming.atlas, "patrol_loop", lambda mid, variant: None)
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


def test_farm_keeps_the_area_it_was_given(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    monkeypatch.setattr(farming.atlas, "patrol_loop", lambda mid, variant: None)
    b = Farm()
    cap = farm_capture(b, map_id=5121, area=[1, 2, 3, 4])
    assert cap["area"] == [1, 2, 3, 4]


def test_farm_avoids_bosses_by_default_and_elites_on_request(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "patrol_loop", lambda mid, variant: None)
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


def test_farm_on_a_patrol_loop_uses_chase_range(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    monkeypatch.setattr(farming.atlas, "patrol_loop", lambda mid, variant: [(1, 1), (2, 2)])
    b = Farm(profile={"chase_range": 12})
    cap = farm_capture(b, map_id=1, patrol="full")
    assert cap["loop"] == [(1, 1), (2, 2)] and cap["chase"] == 12
    cap["sighted"](None)
    assert b.targets_calls[-1][-1] == 12                 # the limit targets() was given


def test_farm_wandering_has_no_chase_limit(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    b = Farm()
    cap = farm_capture(b, map_id=1, patrol="random")
    assert cap["loop"] is None and cap["chase"] is None
    b.targets_result = [{"id": -1}]
    assert cap["sighted"](None) is True
    assert b.targets_calls[-1][-1] is None


def test_farm_done_by_minutes(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Farm()
    cap = farm_capture(b, map_id=1, minutes=1)
    assert cap["done"]() is False
    clock.advance(60)
    assert cap["done"]() is True


def test_farm_done_by_kills(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    b = Farm()
    cap = farm_capture(b, map_id=1, kills=2)
    assert cap["done"]() is False
    b.kills = 2
    assert cap["done"]() is True


def test_farm_done_by_until(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    flag = {"stop": False}
    b = Farm()
    cap = farm_capture(b, map_id=1, until=lambda: flag["stop"])
    assert cap["done"]() is False
    flag["stop"] = True
    assert cap["done"]() is True


def test_farm_counts_the_bag_at_start(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    monkeypatch.setattr(town, "counts", lambda bot: {"sold": 1})
    b = Farm()
    del b.bag_start
    farm_capture(b, map_id=1)
    assert b.bag_start == {"sold": 1}


def test_farm_keeps_the_bag_count_it_already_made(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "monster_area", lambda mid: [0, 0, 1, 1])
    monkeypatch.setattr(farming.atlas, "zones", lambda mid, kinds=("monster", "elite", "boss"): [])
    monkeypatch.setattr(town, "counts", lambda bot: pytest.fail("counts should not run again"))
    b = Farm()
    b.bag_start = {"kept": 1}
    farm_capture(b, map_id=1)
    assert b.bag_start == {"kept": 1}


# ================================================================= _farm_loop
def test_loop_fights_a_target(monkeypatch):
    b = Farm()
    mob = {"id": -5, "name": "Wolf", "x": 6, "y": 5}
    b.targets_result = [mob]
    calls = []
    monkeypatch.setattr(farming.pets, "keep_out", lambda bot: calls.append("keep"))
    monkeypatch.setattr(farming.pets, "look_after", lambda bot, snap: calls.append("pet"))
    run_loop(b)
    assert b.fights == [mob]
    assert b.count_kills_calls == [b.sn]
    assert b.look_after_calls == [b.sn["self"]]
    assert calls == ["keep", "pet"]
    assert b.targets_calls[0][1:3] == (None, "")         # area, only
    assert b.timed_phases[:1] == ["town"]


def test_loop_rests_before_taking_a_target():
    b = Farm()
    b.targets_result = [{"id": -5, "name": "Wolf", "x": 6, "y": 5}]
    b.rest_up_result = True
    run_loop(b)
    assert b.fights == []
    assert b.walk_calls == []


def test_loop_goes_to_town_and_starts_over():
    b = Farm()
    b.bag_check_result = True
    b.targets_result = [{"id": -5, "hits": 1}]
    run_loop(b)
    assert b.fights == []
    assert b.targets_calls == []                          # the town trip happens before targets


def test_loop_refills_keys_and_starts_over(monkeypatch):
    b = Farm()
    monkeypatch.setattr(farming.keyslots, "refill", lambda bot: True)
    b.targets_result = [{"id": -5}]
    run_loop(b)
    assert b.fights == []
    assert b.targets_calls == []


def test_loop_restocks_and_comes_back(monkeypatch):
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


def test_loop_returns_after_leaving_the_map(monkeypatch):
    labels(monkeypatch)
    b = Farm()
    b.map = 2
    b.set_modes = lambda: b.set_modes_calls.append(True)
    run_loop(b, map_id=1, loop=[(5, 5)])
    assert b.travels == [(1, None)]
    assert b.set_modes_calls == [True]
    assert any("left Map 1 (1)" in line and "going back" in line for line in b.lines)


def test_loop_walks_the_patrol_loop():
    b = Farm()
    loop = [(1, 1), (9, 9)]
    run_loop(b, passes=2, loop=loop)
    assert b.walk_calls == [(1, 1), (9, 9)]


def test_loop_wanders_to_a_random_cell():
    b = Farm()
    calls = []

    def random_cell(map_id, area, me, radius=40):
        calls.append((map_id, area, me, radius))
        return (7, 7)

    b.random_cell = random_cell
    run_loop(b)
    assert b.walk_calls == [(7, 7)]
    assert calls == [(1, None, b.sn["self"], 40)]


def test_loop_sleeps_when_there_is_nowhere_to_walk():
    b = Farm()
    b.random_cell = lambda map_id, area, me, radius=40: None
    run_loop(b, passes=2)
    assert b.sleeps == [1.0, 1.0]
    assert b.walk_calls == []


def test_loop_goes_back_after_a_respawn(monkeypatch):
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


def test_loop_keeps_going_when_the_respawn_travel_fails():
    b = Farm()
    b.travel_error = Respawned()
    b.set_modes = lambda: b.set_modes_calls.append(True)

    def bag_check():
        raise Respawned()

    b.bag_check = bag_check
    run_loop(b)                                            # must not raise, must not set modes
    assert b.set_modes_calls == []


# ========================================================== drop_time_check
def test_drop_time_off_does_nothing():
    b = Farm(profile={"stop_on_drop_time": False})
    b.finish = lambda: pytest.fail("finish must not run")
    b.drop_time_check({"drop_seconds": 10})


def test_drop_time_missing_or_negative_returns():
    b = Farm()
    b.finish = lambda: pytest.fail("finish must not run")
    b.drop_time_check({})
    b.drop_time_check({"drop_seconds": None})
    b.drop_time_check({"drop_seconds": -1})


def test_drop_time_warns_once_when_plenty_is_left():
    b = Farm()
    b.drop_time_check({"drop_seconds": 1000, "age": 0})
    assert any("drop time:" in line and "farming stops in about 11 minutes" in line for line in b.lines)
    n = len(b.lines)
    b.drop_time_check({"drop_seconds": 1000, "age": 0})
    assert len(b.lines) == n and b.said_drop is True


def test_drop_time_stops_the_run_when_used_up():
    b = Farm()
    finished = []
    b.finish = lambda: finished.append(True)
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 300, "age": 0})
    assert finished == [True]
    assert any("drop time is nearly used up (5 min left)" in line for line in b.spoken)


def test_drop_time_subtracts_the_age():
    b = Farm()
    finished = []
    b.finish = lambda: finished.append(True)
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 400, "age": 200})   # 200 left: less than the tail
    assert finished == [True]
    assert any("nearly used up" in line for line in b.spoken)


# ============================================================ leave_monsters
def test_leave_monsters_on_a_map_without_zones_does_nothing(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", lambda mid: [])
    b = Farm()
    b.leave_monsters()
    assert b.travels == []


def test_leave_monsters_walks_to_the_nearest_safe_map(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", lambda mid: {1: [1], 2: [1], 3: []}.get(mid, []))
    monkeypatch.setattr(farming.atlas, "maps", lambda: {
        1: {"exits": [{"dest": 2}]},
        2: {"exits": [{"dest": 3}, {"dest": 1}, {"dest": None}]},
        3: {"exits": []}})
    b = Farm()
    b.leave_monsters()
    assert b.travels == [(3, None)]
    assert any("done here - going to Map 3 (3), where no monsters spawn" in line for line in b.lines)


def test_leave_monsters_says_when_there_is_none(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.atlas, "zones", lambda mid: [1])
    monkeypatch.setattr(farming.atlas, "maps", lambda: {
        1: {"exits": [{"dest": 2}]},
        2: {"exits": [{"dest": 1}]}})
    b = Farm()
    b.leave_monsters()
    assert b.travels == []
    assert any("no map without monsters near Map 1 (1)" in line for line in b.spoken)


# ==================================================================== finish
def test_finish_here_stays(monkeypatch):
    labels(monkeypatch)
    off = []
    monkeypatch.setattr(farming.beasts, "off", lambda bot: off.append(True))
    b = Farm()
    b.end_place = ("here",)
    b.finish()
    assert off == [True] and b.travels == []


def test_finish_start_without_a_start_place_goes_safe(monkeypatch):
    labels(monkeypatch)
    off = []
    monkeypatch.setattr(farming.beasts, "off", lambda bot: off.append(True))
    zone_stub(monkeypatch)
    b = Farm()
    b.start_place = None
    b.hearthstone = True
    b.finish()
    assert any("going home with the hearthstone" in line for line in b.lines)
    assert b.go_home_calls == [1] and b.travels == [] and off == [True]


def test_finish_starting_where_monsters_spawn_goes_safe(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    zone_stub(monkeypatch)
    b.start_place = (5, 10, 20)
    b.spawns = True
    b.hearthstone = False
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert any("started where monsters spawn" in line for line in b.lines)
    assert b.leave_calls == [True] and b.travels == []


def test_finish_start_walks_back_when_far(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.start_place = (5, 10, 20)
    b.spawns = False
    b.map = 1
    b.sn = {"self": {"id": 1, "x": 0, "y": 0}}
    b.finish()
    assert b.travels == [(5, (10, 20))]
    assert any("going back to Map 5 (5) (10,20)" in line for line in b.spoken)


def test_finish_start_does_not_walk_when_already_there(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.start_place = (5, 10, 20)
    b.spawns = False
    b.map = 5
    b.sn = {"self": {"id": 1, "x": 11, "y": 20}}
    b.finish()
    assert b.travels == []


def test_finish_place_walks_there(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("place", 7, [30, 40])
    b.map = 1
    b.finish()
    assert b.travels == [(7, (30, 40))]


def test_finish_place_without_a_cell_walks_to_the_map(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("place", 7, None)
    b.map = 1
    b.finish()
    assert b.travels == [(7, None)]
    assert any("going back to Map 7 (7)" in line and "(" not in line.split("Map 7 (7)", 1)[1]
               for line in b.spoken)


def test_finish_with_the_hearthstone_works(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("hearthstone",)
    b.hearthstone = True
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert b.leave_calls == [] and b.go_home_calls == [1] and b.travels == []


def test_finish_hearthstone_that_fails_leaves_the_monsters(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("hearthstone",)
    b.hearthstone = False
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert b.leave_calls == [True] and b.go_home_calls == []


def test_finish_unknown_kind_goes_safe(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    zone_stub(monkeypatch)
    b = Farm()
    b.end_place = ("nonsense",)
    b.hearthstone = True
    b.finish()
    assert any("going home with the hearthstone" in line for line in b.lines)


def test_finish_reraises_a_respawn(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("place", 7, [1, 1])
    b.travel_error = Respawned()
    with pytest.raises(Respawned):
        b.finish()


def test_finish_stopped_with_stop_event_reraises(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("place", 7, [1, 1])
    b.travel_error = Stopped("no way")
    b.stop_event.set()
    with pytest.raises(Stopped):
        b.finish()


def test_finish_stopped_without_stop_event_goes_to_a_safe_map(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    b = Farm()
    b.end_place = ("place", 7, [1, 1])
    b.travel_error = Stopped("no way")
    b.leave_monsters = lambda: b.leave_calls.append(True)
    b.finish()
    assert b.leave_calls == [True]
    assert any("couldn't get there - going to the nearest map without monsters" in line
               for line in b.spoken)


def test_finish_recalls_the_pet_at_the_end(monkeypatch):
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", lambda bot: None)
    recalled = []
    monkeypatch.setattr(farming.pets, "recall", lambda bot: recalled.append(True))
    b = Farm(profile={"pet_recall_at_end": True})
    b.end_place = ("here",)
    b.finish()
    assert recalled == [True]


# ================================================================ restock_now
def test_restock_now_without_rules_does_nothing(monkeypatch):
    monkeypatch.setattr(town, "restock", lambda *a, **k: pytest.fail("must not run"))
    b = Farm(profile={"restock": []})
    assert FarmingMixin.restock_now(b) is False


def test_restock_now_delegates_to_town(monkeypatch):
    calls = []
    monkeypatch.setattr(town, "restock", lambda bot, rules, start=False: calls.append((bot, rules, start)) or "R")
    b = Farm(profile={"restock": [{"item": "Large Red Potion"}]})
    assert FarmingMixin.restock_now(b) == "R"
    assert FarmingMixin.restock_now(b, start=True) == "R"
    assert calls == [(b, [{"item": "Large Red Potion"}], False), (b, [{"item": "Large Red Potion"}], True)]


# ================================================================= random_cell
class Gate:
    def __init__(self, on):
        self.on = on

    def any(self):
        return self.on


class Model:
    def __init__(self, floor, comps=None, portals=(), two_way=(), gate=False, W=10):
        self.W = W
        self.floor = set(floor)
        self.comps = dict(comps or {})
        self.portal = collections.defaultdict(bool)
        for p in portals:
            self.portal[p] = True
        self.gate = Gate(gate)
        self.way = set(two_way)
        self.two_way_calls = 0

    def ok(self, x, y):
        return (x, y) in self.floor

    def comp_of(self, x, y):
        return self.comps.get((x, y))

    def two_way(self, x, y):
        self.two_way_calls += 1
        return set(self.way)


def grid(x0=1, y0=1, x1=8, y1=9):
    return {(x, y) for x in range(x0, x1 + 1) for y in range(y0, y1 + 1)}


def randint_seq(values):
    it = itertools.chain(values, itertools.repeat(values[-1]))
    return lambda a, b: next(it)


def test_random_cell_returns_a_walkable_cell(monkeypatch):
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (8, 9)


def test_random_cell_skips_a_map_exit(monkeypatch):
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps, portals=[(9, 8)])       # portal key is (y, x) for cell (8, 9)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9, 2, 2]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (2, 2)


def test_random_cell_skips_a_walled_off_part(monkeypatch):
    b = Farm()
    comps = {c: 2 for c in grid()}
    comps[(5, 5)] = comps[(2, 2)] = 1
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9, 2, 2]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (2, 2)


def test_random_cell_respects_one_way_gates(monkeypatch):
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps, gate=True, two_way=[2 * 10 + 2])   # the two-way area holds (2, 2)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9, 2, 2]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) == (2, 2)
    assert m.two_way_calls == 1


def test_random_cell_gives_up_after_many_tries(monkeypatch):
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps, portals=[(9, 8)])
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 5, "y": 5}) is None


def test_random_cell_from_a_cell_the_map_calls_wall(monkeypatch):
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, (1, 1, 8, 9), {"x": 0, "y": 0}) == (8, 9)


def test_random_cell_stays_inside_the_area_when_we_stand_far_outside_it(monkeypatch):
    # The candidate range is the area narrowed to `radius` around us. Standing further outside the
    # area than that used to invert the range - randint raises on that and the run stopped with
    # "unexpected error: empty range for randrange()" - so it now falls back to the whole area.
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    asked = []

    def randint(low, high):
        asked.append((low, high))
        return low

    monkeypatch.setattr(farming.random, "randint", randint)
    c = b.random_cell(1, (1, 1, 8, 9), {"x": 100, "y": 5})
    assert asked and all(low <= high for low, high in asked)     # never an inverted range
    assert c is not None and 1 <= c[0] <= 8 and 1 <= c[1] <= 9


def test_random_cell_uses_the_map_size_without_an_area(monkeypatch):
    b = Farm()
    comps = {c: 1 for c in grid()}
    m = Model(grid(), comps=comps)
    monkeypatch.setattr(farming.atlas, "model", lambda mid: m)
    monkeypatch.setattr(farming.atlas, "maps", lambda: {1: {"w": 8, "h": 9}})
    monkeypatch.setattr(farming.random, "randint", randint_seq([8, 9]))
    assert b.random_cell(1, None, {"x": 5, "y": 5}) == (8, 9)


def test_a_plans_drop_time_to_save_beats_the_characters():
    b = Farm(profile={"stop_on_drop_time": False, "drop_keep_minutes": 0})
    b.drop_keep = 60                      # keep_drop 60 from the plan
    finished = []
    b.finish = lambda: finished.append(True)
    b.drop_time_check({"drop_seconds": 60 * 60 + 300 + 120, "age": 0})    # two minutes to spare
    assert finished == []
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 60 * 60 + 300 - 10, "age": 0})
    assert finished == [True]
