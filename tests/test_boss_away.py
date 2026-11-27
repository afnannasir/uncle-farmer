# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""A boss that keeps coming back: the player's choices (how many walk-offs before the map is left,
whether it is left at all, how long the wait away lasts), and the wait spent farming - the walk to
the door fighting what it meets, the map waited on farmed near where we came in, nothing on it
stronger than what the farm fights."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot, Clock, ME, stand_in
from test_bosses import Walker, mob
from test_farming import Farm, labels, zone_stub

from unclefarmer.engine import bosses
from unclefarmer.engine.botparts import farming


def worm_snap() -> dict[str, Any]:
    return {"self": {"id": ME, "x": 100.0, "y": 100.0}, "entities": [mob(-1, "Death Worm", 105.0, hp=900000)]}


# ------------------------------------------------------------------ the player's choices
def test_the_choices_are_read_from_the_profile_with_safe_fallbacks() -> None:
    assert bosses.walk_offs({}) == 3
    assert bosses.walk_offs({"boss_walk_offs": 0}) == 0
    assert bosses.walk_offs({"boss_walk_offs": -2}) == 0
    assert bosses.walk_offs({"boss_walk_offs": "x"}) == 3
    assert bosses.away_times({}) == (60.0, 300.0)
    assert bosses.away_times({"boss_away_first": 30, "boss_away_most": 90}) == (30.0, 90.0)
    assert bosses.away_times({"boss_away_first": 120, "boss_away_most": 30}) == (120.0, 120.0)   # never under the first
    assert bosses.away_times({"boss_away_first": None}) == (60.0, 300.0)


def test_no_walk_offs_leaves_the_map_the_first_time_a_boss_comes_close() -> None:
    snap = worm_snap()
    w = Walker(snap, [])
    w.p["boss_walk_offs"] = 0
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.walked == [] and w.trips == [1041, 5121]


def test_one_walk_off_then_the_map_is_left() -> None:
    snap = worm_snap()
    w = Walker(snap, [])
    w.p["boss_walk_offs"] = 1
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.walked == [(40, 100)] and w.trips == []
    w.clock.advance(30)
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.walked == [(40, 100)] and w.trips == [1041, 5121]


def test_with_leaving_off_the_map_is_never_left() -> None:
    """Walk-offs as many as they come; with nowhere further off, the boss is farmed on, away from it,
    as an elite is - the farm loop goes on."""
    snap = worm_snap()
    w = Walker(snap, [])
    w.p["boss_leave"] = False
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    for _i in range(6):
        assert bosses.keep_away(as_bot(w), snap, 5121, None)
        w.clock.advance(10)
    assert w.trips == [] and len(w.walked) == 6
    w.random_cell = lambda *a, **k: None        # type: ignore[method-assign]
    assert not bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.trips == []


def test_the_chosen_waits_double_up_to_the_longest() -> None:
    snap = worm_snap()
    w = Walker(snap, [])
    w.p.update(boss_away_first=30, boss_away_most=100)
    waits: list[float] = []
    for _i in range(4):
        before = w.clock.time()
        assert bosses.keep_away(as_bot(w), snap, 5121, None)
        waits.append(w.clock.time() - before)
    assert waits == [30.0, 60.0, 100.0, 100.0]
    assert w.lines[1] == "Death Worm (a boss) keeps coming back - leaving for central plains (1041) for 30 s"


def test_a_long_wait_still_doubles_when_the_boss_is_there_on_the_way_back() -> None:
    """The wait starts over only AWAY_RESET after coming BACK: counted from the leaving, an hour's
    wait would always have started over."""
    snap = worm_snap()
    w = Walker(snap, [])
    w.p.update(boss_away_first=1800, boss_away_most=3600)
    waits: list[float] = []
    for _i in range(2):
        before = w.clock.time()
        assert bosses.keep_away(as_bot(w), snap, 5121, None)
        waits.append(w.clock.time() - before)
    assert waits == [1800.0, 3600.0]


def test_the_farm_ending_ends_the_wait_and_it_does_not_go_back() -> None:
    snap = worm_snap()
    w = Walker(snap, [])
    start = w.clock.time()
    assert bosses.keep_away(as_bot(w), snap, 5121, None, done=lambda: w.clock.time() - start >= 10)
    assert 10 <= w.clock.time() - start < 60
    assert w.trips == [1041] and "back to Wolf Prairie (5121)" not in w.lines


# ------------------------------------------------------------------ farming the wait
class FarmWalker(Walker):
    """The boss module's stand-in bot, with boss_away_farm on: records the fighting walk to the door
    and the farmed wait."""

    def __init__(self, snap: dict[str, Any]) -> None:
        super().__init__(snap, [])
        self.p["boss_away_farm"] = True
        self.farm_top_hp = 5000
        self.last_me: dict[str, Any] | None = None
        self.fought_walks: list[tuple[float, float]] = []
        self.farmed: list[tuple[int, float]] = []

    def leave_fighting(self, cell: tuple[float, float]) -> str:
        self.fought_walks.append(cell)
        self.where = 1041
        return "map"

    def farm_away(self, home: int, seconds: float, done: Any = None) -> None:
        self.farmed.append((home, seconds))
        self.clock.advance(seconds)


def test_the_wait_is_farmed_after_a_walk_out_that_fights() -> None:
    snap = worm_snap()
    w = FarmWalker(snap)
    w.random_cell = lambda *a, **k: None        # type: ignore[method-assign]
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert len(w.fought_walks) == 1
    assert w.doors == []                       # not the plain walk
    assert w.farmed == [(5121, 60.0)]
    assert w.trips == [5121] and w.modes == 1
    assert w.lines[-1] == "back to Wolf Prairie (5121)"


def test_nothing_fought_yet_waits_without_farming_and_says_why() -> None:
    snap = worm_snap()
    w = FarmWalker(snap)
    w.farm_top_hp = 0
    w.random_cell = lambda *a, **k: None        # type: ignore[method-assign]
    start = w.clock.time()
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.farmed == [] and w.clock.time() - start >= 60
    assert ("not farming central plains (1041) meanwhile: nothing fought on the farm yet to judge its "
            "monsters by") in w.lines


def test_a_map_it_may_not_farm_is_waited_on_without_farming(monkeypatch: pytest.MonkeyPatch) -> None:
    snap = worm_snap()
    w = FarmWalker(snap)
    w.random_cell = lambda *a, **k: None        # type: ignore[method-assign]
    monkeypatch.setattr(bosses, "may_farm", stand_in(bosses.may_farm, lambda bot, dest, home: False))
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.farmed == []
    assert ("not farming central plains (1041) meanwhile: it is a town, or the map data says its monsters "
            "are too strong") in w.lines


def test_may_farm_a_field_or_road_unless_the_data_says_it_is_stronger(monkeypatch: pytest.MonkeyPatch) -> None:
    kinds = {1: "field", 2: "town", 3: "transit", 4: "field", 9: "field"}
    tops = {1: 0, 3: 0, 4: 80, 9: 50}
    monkeypatch.setattr(bosses.atlas, "kind", stand_in(bosses.atlas.kind, lambda mid: kinds.get(int(mid))))
    monkeypatch.setattr(bosses, "_top_level", stand_in(bosses._top_level, lambda mid: tops.get(int(mid), 0)))
    w = FarmWalker(worm_snap())
    bot = as_bot(w)
    assert bosses.may_farm(bot, 1, 9)           # levels unknown: the wait judges by HP
    assert not bosses.may_farm(bot, 2, 9)       # a town
    assert bosses.may_farm(bot, 3, 9)           # a road
    assert not bosses.may_farm(bot, 4, 9)       # level 80 next to a level 50 farm
    w.last_me = {"sheet": {"level": 76}}
    assert bosses.may_farm(bot, 4, 9)           # ...fine for a level 76 (76 + 5)
    w.last_me = {"sheet": {"level": 70}}
    assert not bosses.may_farm(bot, 4, 9)


def test_the_door_to_a_map_it_may_farm_is_taken_when_hardly_further() -> None:
    """Wolf Prairie's east edge: Carter Plain's door (95 steps) is the nearest, wolf head mountain's
    100 steps off - a small detour for a wait that farms."""
    me: dict[str, Any] = {"x": 398.0, "y": 100.0}
    worm = mob(-1, "Death Worm", 200.0, 300.0)
    nearest = bosses.way_out(5121, me, worm, 40)
    assert nearest is not None and nearest["dest"] == 5181
    liked = bosses.way_out(5121, me, worm, 40, prefer=lambda d: d == 5122)
    assert liked is not None and liked["dest"] == 5122
    far = bosses.way_out(5121, me, worm, 40, prefer=lambda d: d == 1061)   # 300 steps off: too far
    assert far is not None and far["dest"] == 5181


# ------------------------------------------------------------------ the farming mixin
class AwayFarm(Farm):
    """The farming tests' stand-in, with a clock that fights and walks move on."""

    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        super().__init__(profile)
        self.ticks = Clock()
        self.clock = self.ticks
        self.fight_back_calls = 0
        self.off_map_calls: list[int] = []
        self.walk_results: list[str] = []
        self.nears: list[bool] = []

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.ticks.advance(seconds)

    def fight(self, mob: dict[str, Any]) -> None:
        super().fight(mob)
        self.ticks.advance(10)

    def walk_to(self, cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        self.walk_calls.append(cell)
        self.ticks.advance(5)
        if near is not None:
            self.nears.append(bool(near(self.me())))
        return self.walk_results.pop(0) if self.walk_results else "arrived"

    def _off_map(self, map_id: int) -> bool:
        self.off_map_calls.append(map_id)
        self.map = map_id
        return False


class Tally:
    def __init__(self) -> None:
        self.maps: list[int | None] = []

    def on_map(self, mid: int | None) -> None:
        self.maps.append(mid)


def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    labels(monkeypatch)
    zone_stub(monkeypatch)
    monkeypatch.setattr(farming.pets, "keep_out", stand_in(farming.pets.keep_out, lambda b: None))
    monkeypatch.setattr(farming.pets, "look_after", stand_in(farming.pets.look_after, lambda b, snap: None))
    monkeypatch.setattr(farming.chests, "open_nearby", stand_in(farming.chests.open_nearby, lambda bot, snap: False))
    monkeypatch.setattr(farming.corpses, "harvest", stand_in(farming.corpses.harvest, lambda b, snap: False))
    monkeypatch.setattr(farming.bosses, "no_go", stand_in(farming.bosses.no_go, lambda bot, snap: []))


def away_bot(monkeypatch: pytest.MonkeyPatch) -> AwayFarm:
    quiet(monkeypatch)
    b = AwayFarm()
    b.map = 1041
    b.sn = {"self": {"id": ME, "x": 10.0, "y": 10.0}, "entities": []}
    b.farm_filter = (None, "", "Rabbit", [])
    b.farm_top_hp = 1000
    b.spot_no_go = [(50.0, 50.0, 5.0)]
    b.tally = Tally()   # type: ignore[attr-defined]
    return b


def test_the_wait_farms_what_is_close_and_no_stronger(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    weak = {"id": -2, "name": "Wolf", "x": 20.0, "y": 10.0, "maxhp": 1400}     # 1.5 x 1000 at most
    strong = {"id": -3, "name": "Wolf King", "x": 15.0, "y": 10.0, "maxhp": 9000}
    b.targets_result = [strong, weak]
    b.farm_away(5121, 25.0)
    assert b.fights and all(m["id"] == -2 for m in b.fights)
    assert b.lines.count("leaving Wolf King alone on Map 1041 (1041): more HP than the monsters of the farm") == 1
    assert b.lines[0] == "farming Map 1041 (1041) meanwhile, near where we came in"
    assert b.tally.maps == [1041, 5121]          # type: ignore[attr-defined]
    assert b.spot_no_go == [(50.0, 50.0, 5.0)]   # the farm's circles back
    call = b.targets_calls[0]
    assert call[1] is None and call[3] == "Rabbit" and call[5] == b.AWAY_LEASH


def test_the_wait_keeps_near_where_it_came_in_but_fights_what_attacks(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    far = {"id": -4, "name": "Wolf", "x": 90.0, "y": 10.0, "maxhp": 100}
    b.targets_result = [far]
    b.farm_away(5121, 12.0)
    assert b.fights == []
    b.attackers_result = {-4}
    b.farm_away(5121, 12.0)
    assert b.fights and b.fights[0]["id"] == -4


def test_the_wait_rests_before_a_new_monster(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    b.targets_result = [{"id": -2, "name": "Wolf", "x": 20.0, "y": 10.0, "maxhp": 900}]
    b.rest_up_result = True
    original = b.rest_up

    def rest(me: dict[str, Any]) -> bool:
        b.ticks.advance(5)
        return original(me)
    b.rest_up = rest   # type: ignore[method-assign]
    b.farm_away(5121, 12.0)
    assert b.fights == []


def test_the_wait_patrols_round_where_it_came_in(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    seen: list[tuple[Any, ...]] = []

    def cell(map_id: int, area: Any, me: dict[str, Any], radius: int = 40, **kw: Any) -> tuple[int, int] | None:
        seen.append((map_id, area, dict(me), radius))
        return (12, 12)
    b.random_cell = cell   # type: ignore[method-assign]
    b.farm_away(5121, 9.0)
    assert seen[0] == (1041, None, {"x": 10.0, "y": 10.0}, b.AWAY_LEASH)
    assert b.walk_calls[0] == (12, 12) and b.nears[0] is False


def test_the_wait_ends_on_another_map_or_after_a_trip_to_town(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    b.bag_check_result = True
    b.farm_away(5121, 60.0)
    assert b.off_map_calls == [5121] and b.map == 5121
    assert b.tally.maps == [1041, 5121]          # type: ignore[attr-defined]
    b = away_bot(monkeypatch)
    b.targets_result = [{"id": -2, "name": "Wolf", "x": 20.0, "y": 10.0, "maxhp": 900}]

    def moved(mob: dict[str, Any]) -> None:       # died or pushed off the map in that fight
        b.fights.append(mob)
        b.map = 1061
    b.fight = moved   # type: ignore[method-assign]
    start = b.clock.time()
    b.farm_away(5121, 60.0)
    assert len(b.fights) == 1 and b.clock.time() - start < 60
    assert b.off_map_calls == [] and b.tally.maps == [1041, 5121]   # type: ignore[attr-defined]


def test_the_farm_ending_ends_the_farmed_wait(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    b.targets_result = [{"id": -2, "name": "Wolf", "x": 20.0, "y": 10.0, "maxhp": 900}]
    b.farm_away(5121, 600.0, done=lambda: len(b.fights) >= 2)
    assert len(b.fights) == 2


# ------------------------------------------------------------------ the walk out
def test_the_walk_out_fights_what_it_meets_then_goes_on_to_the_door(monkeypatch: pytest.MonkeyPatch) -> None:
    quiet(monkeypatch)
    b = AwayFarm()
    b.farm_filter = (None, "", "Rabbit", [(1, 1, 2, 2)])
    b.sn = {"self": {"id": ME, "x": 10.0, "y": 10.0}, "entities": []}
    wolf = {"id": -2, "name": "Wolf", "x": 14.0, "y": 10.0, "maxhp": 900}
    b.targets_result = [wolf]
    b.walk_results = ["near", "near", "map"]
    assert b.leave_fighting((300, 300)) == "map"
    assert b.fights == [wolf, wolf]
    assert b.walk_calls == [(300, 300)] * 3
    call = b.targets_calls[0]
    assert call[1] is None and call[3] == "Rabbit" and call[4] == [(1, 1, 2, 2)] and call[5] == b.WAY_CHASE


def test_the_walk_out_stops_fighting_near_a_boss_and_after_enough_fights(monkeypatch: pytest.MonkeyPatch) -> None:
    quiet(monkeypatch)
    b = AwayFarm()
    b.farm_filter = None
    b.sn = {"self": {"id": ME, "x": 10.0, "y": 10.0}, "entities": []}
    b.targets_result = [{"id": -2, "name": "Wolf", "x": 14.0, "y": 10.0, "maxhp": 900}]
    monkeypatch.setattr(farming.bosses, "threatened", stand_in(farming.bosses.threatened, lambda bot, snap: True))
    b.walk_results = ["arrived"]
    assert b.leave_fighting((300, 300)) == "arrived"
    assert b.fights == [] and b.nears == [False]
    monkeypatch.setattr(farming.bosses, "threatened", stand_in(farming.bosses.threatened, lambda bot, snap: False))
    b.walk_results = ["near"] * (b.WAY_FIGHTS + 3) + ["map"]
    b.leave_fighting((300, 300))
    assert len(b.fights) == 8               # 15 s a fight and its walk: WAY_FIGHT_FOR (120 s) is up
    b.fights = []
    b.fight = b.fights.append               # type: ignore[method-assign]   (fights that take no time)
    b.walk_to = lambda cell, map_change=False, near=None: (  # type: ignore[method-assign]
        b.walk_results.pop(0) if b.walk_results else "map")
    b.walk_results = ["near"] * (b.WAY_FIGHTS + 3) + ["map"]
    b.leave_fighting((300, 300))
    assert len(b.fights) == b.WAY_FIGHTS    # ...or WAY_FIGHTS fights


def test_a_monster_gone_before_the_fight_lets_the_walk_go_on(monkeypatch: pytest.MonkeyPatch) -> None:
    quiet(monkeypatch)
    b = AwayFarm()
    b.farm_filter = None
    b.walk_results = ["near", "map"]
    assert b.leave_fighting((300, 300)) == "map"
    assert b.fights == []


def test_threatened_is_a_boss_within_its_steps() -> None:
    snap = worm_snap()
    w = Walker(snap, [])
    assert bosses.threatened(as_bot(w), snap)
    snap["entities"][0]["x"] = 160.0
    assert not bosses.threatened(as_bot(w), snap)


# ------------------------------------------------------------------ what the farm fights
def test_the_farm_notes_the_most_hp_it_takes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    quiet(monkeypatch)
    monkeypatch.setattr(farming.teamhunt, "tick", stand_in(farming.teamhunt.tick, lambda b, snap, targets: False))
    b = AwayFarm()
    snap = b.snap()
    for hp in (800, 1200, 600):
        assert b._farm_fight(snap, [{"id": -2, "name": "Wolf", "x": 1.0, "y": 1.0, "maxhp": hp}])
    assert b.farm_top_hp == 1200
    assert b._farm_fight(snap, [{"id": -3, "name": "Odd", "x": 1.0, "y": 1.0}])   # no max HP read
    assert b.farm_top_hp == 1200


def test_an_elite_that_attacked_us_doesnt_raise_the_hp_measure(monkeypatch: pytest.MonkeyPatch) -> None:
    quiet(monkeypatch)
    monkeypatch.setattr(farming.teamhunt, "tick", stand_in(farming.teamhunt.tick, lambda b, snap, targets: False))
    b = AwayFarm()
    snap = b.snap()
    assert b._farm_fight(snap, [{"id": -2, "name": "Wolf", "x": 1.0, "y": 1.0, "maxhp": 800}])
    b.attackers_result = {-5}
    assert b._farm_fight(snap, [{"id": -5, "name": "Wolf Elite", "x": 1.0, "y": 1.0, "maxhp": 50000}])
    b.attackers_result = set()
    assert b._farm_fight(snap, [{"id": -6, "kind": "monster", "name": "Demonized Wolf", "x": 1.0, "y": 1.0,
                                 "maxhp": 60000}])
    assert b.farm_top_hp == 800


def test_a_boss_of_the_map_waited_on_is_walked_away_from_never_left_for(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    calls: list[tuple[int, bool]] = []

    def keep(bot: Any, snap: dict[str, Any], map_id: int, area: Any, done: Any = None, leave: bool = True) -> bool:
        calls.append((map_id, leave))
        b.ticks.advance(5)
        return True
    monkeypatch.setattr(farming.bosses, "keep_away", keep)
    b.targets_result = [{"id": -2, "name": "Wolf", "x": 20.0, "y": 10.0, "maxhp": 900}]
    b.farm_away(5121, 12.0)
    assert calls and set(calls) == {(1041, False)} and b.fights == []


def test_a_walk_off_on_the_map_waited_on_is_not_counted_for_leaving() -> None:
    snap = worm_snap()
    w = Walker(snap, [])
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    for _i in range(5):
        assert bosses.keep_away(as_bot(w), snap, 5121, None, leave=False)
    assert w.trips == [] and w.__dict__.get("boss_flees") == []
    w.random_cell = lambda *a, **k: None        # type: ignore[method-assign]
    assert not bosses.keep_away(as_bot(w), snap, 5121, None, leave=False)
    assert w.trips == []


def test_a_trip_that_ends_on_another_map_waits_there_unfarmed() -> None:
    snap = worm_snap()
    w = FarmWalker(snap)
    w.random_cell = lambda *a, **k: None        # type: ignore[method-assign]

    def elsewhere(cell: tuple[float, float]) -> str:
        w.fought_walks.append(cell)
        return "arrived"                          # not through the door...
    w.leave_fighting = elsewhere                  # type: ignore[method-assign]

    def lost(dst: int) -> None:                   # ...and the planned way ends on a third map
        w.trips.append(dst)
        w.where = 1061 if dst == 1041 else dst
    w.travel = lost                               # type: ignore[method-assign]
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.farmed == [] and not any("nothing fought" in line for line in w.lines)
    assert w.trips == [1041, 5121]


def test_a_setback_in_the_farmed_wait_puts_the_farm_back(monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.engine.botparts.base import Respawned
    b = away_bot(monkeypatch)
    b.targets_result = [{"id": -2, "name": "Wolf", "x": 20.0, "y": 10.0, "maxhp": 900}]

    def died(mob: dict[str, Any]) -> None:
        raise Respawned("died")
    b.fight = died   # type: ignore[method-assign]
    with pytest.raises(Respawned):
        b.farm_away(5121, 60.0)
    assert b.spot_no_go == [(50.0, 50.0, 5.0)] and b.tally.maps == [1041, 5121]   # type: ignore[attr-defined]


# ------------------------------------------------------------------ telling the chat rooms
class Sender:
    def __init__(self, answers: dict[int, str] | None = None) -> None:
        self.lines: list[str] = []
        self.answers = answers or {}

    def send(self, line: str) -> str:
        self.lines.append(line)
        return self.answers.get(int(line.split()[1]), "sent")


def told_walker(rooms: list[int], answers: dict[int, str] | None = None) -> tuple[Walker, Sender]:
    w = Walker(worm_snap(), [])
    w.p["boss_notice_rooms"] = rooms
    sender = Sender(answers)
    w.a = sender   # type: ignore[attr-defined]
    return w, sender


def test_no_room_ticked_tells_nobody() -> None:
    w, sender = told_walker([])
    bosses.announce(as_bot(w), worm_snap(), 5121)
    assert sender.lines == [] and w.lines == []


def test_a_boss_seen_is_told_once_to_every_ticked_room_in_english() -> None:
    w, sender = told_walker([0, 4, 7, 3])         # 7 isn't a room the bot writes to
    snap = worm_snap()
    bosses.announce(as_bot(w), snap, 5121)
    bosses.announce(as_bot(w), snap, 5121)        # the same boss: once
    text = "Death Worm (a boss) on Wolf Prairie at 105, 100"
    assert sender.lines == ["say 4 " + text, "say 3 " + text, "say 0 " + text]
    assert w.lines == ["told Team, Gang, Global: " + text]


def test_one_notice_a_map_every_ten_minutes_at_most() -> None:
    w, sender = told_walker([4])
    snap = worm_snap()
    bosses.announce(as_bot(w), snap, 5121)
    other = {"self": snap["self"], "entities": [mob(-9, "Death Worm", 300.0, hp=900000)]}
    w.clock.advance(60)
    bosses.announce(as_bot(w), other, 5121)       # came back as a new one, a minute later: not again
    assert len(sender.lines) == 1
    third = {"self": snap["self"], "entities": [mob(-10, "Death Worm", 200.0, hp=900000)]}
    w.clock.advance(bosses.NOTICE_EVERY)
    bosses.announce(as_bot(w), third, 5121)
    assert len(sender.lines) == 2
    bosses.announce(as_bot(w), {"self": snap["self"], "entities": [mob(-11, "Death Worm", 1.0)]}, 1041)
    assert len(sender.lines) == 3                 # another map has its own ten minutes


def test_a_room_the_game_refuses_is_said_and_the_others_still_get_it() -> None:
    w, sender = told_walker([4, 5], answers={5: "not in an international room"})
    bosses.announce(as_bot(w), worm_snap(), 5121)
    assert len(sender.lines) == 2
    assert w.lines[0] == "couldn't tell International of Death Worm: Join an international room in the game first."
    assert w.lines[1].startswith("told Team: ")


def test_a_character_in_no_gang_tells_the_gang_nothing() -> None:
    w, sender = told_walker([4, 3])
    snap = worm_snap()
    snap["self"]["side"] = 0                      # the game's own "no gang"
    bosses.announce(as_bot(w), snap, 5121)
    assert [line.split()[1] for line in sender.lines] == ["4"]
    assert w.lines[0] == "not telling Gang of Death Worm: the character is in no gang"
    w, sender = told_walker([3])
    snap["self"]["side"] = 1234                   # a gang's id
    bosses.announce(as_bot(w), snap, 5121)
    assert [line.split()[1] for line in sender.lines] == ["3"]


def test_the_same_monster_id_on_another_map_is_another_boss() -> None:
    w, sender = told_walker([4])
    snap = worm_snap()
    bosses.announce(as_bot(w), snap, 5121)
    bosses.announce(as_bot(w), snap, 1041)        # ids are the map's own
    assert len(sender.lines) == 2


def test_a_lost_link_is_said_not_raised() -> None:
    from unclefarmer.link import AgentError
    w, sender = told_walker([4])

    def broken(line: str) -> str:
        raise AgentError("no game")
    sender.send = broken   # type: ignore[method-assign]
    bosses.announce(as_bot(w), worm_snap(), 5121)
    assert w.lines == ["couldn't tell Team of Death Worm: The game didn't take it: no game"]


def test_the_room_choice_reads_safely() -> None:
    assert bosses.notice_rooms({}) == []
    assert bosses.notice_rooms({"boss_notice_rooms": "team"}) == []
    assert bosses.notice_rooms({"boss_notice_rooms": [0, 5, 4, 1]}) == [4, 5, 0]


def test_the_farm_and_the_wait_both_tell_of_the_bosses(monkeypatch: pytest.MonkeyPatch) -> None:
    b = away_bot(monkeypatch)
    seen: list[int] = []
    monkeypatch.setattr(farming.bosses, "announce",
                        stand_in(farming.bosses.announce, lambda bot, snap, map_id: seen.append(map_id)))
    b.farm_away(5121, 6.0)
    assert seen and set(seen) == {1041}


def test_the_farm_loop_tells_of_the_bosses_before_keeping_away(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_farming import run_loop
    quiet(monkeypatch)
    order: list[str] = []
    monkeypatch.setattr(farming.keyslots, "refill", stand_in(farming.keyslots.refill, lambda bot: False))
    monkeypatch.setattr(farming.bosses, "announce",
                        stand_in(farming.bosses.announce, lambda bot, snap, map_id: order.append("told %d" % map_id)))

    def keep(bot: Any, snap: dict[str, Any], map_id: int, area: Any, done: Any = None, leave: bool = True) -> bool:
        order.append("keep")
        return True
    monkeypatch.setattr(farming.bosses, "keep_away", keep)
    b = AwayFarm()
    b.sn = {"self": {"id": ME, "x": 10.0, "y": 10.0}, "entities": []}
    run_loop(b, passes=1, map_id=1)
    assert order == ["told 1", "keep"]


def test_a_tick_is_not_a_number_of_seconds() -> None:
    assert bosses.walk_offs({"boss_walk_offs": True}) == 3
    assert bosses.away_times({"boss_away_first": True, "boss_away_most": False}) == (60.0, 300.0)


def test_the_player_s_own_line_names_the_boss_the_map_and_where() -> None:
    w, sender = told_walker([3])
    w.p["boss_notice_text"] = "Boss alert: %Boss% on %MAP% at %pos%, %unknown% stays"
    bosses.announce(as_bot(w), worm_snap(), 5121)
    assert sender.lines == ["say 3 Boss alert: Death Worm on Wolf Prairie at 105, 100, %unknown% stays"]


def test_an_own_line_of_blanks_is_the_bot_s_line_and_one_of_several_lines_is_one() -> None:
    boss = {"name": "Death Worm", "x": 105.4, "y": 99.6}
    assert bosses.notice_text(boss, 5121, " \n\t ") == "Death Worm (a boss) on Wolf Prairie at 105, 99"
    assert bosses.notice_text(boss, 5121, "%boss%\nis\r\nhere") == "Death Worm is here"
    assert bosses.notice_text(boss, 99999, "%map%") == "99999"         # a map the bot has no name for
