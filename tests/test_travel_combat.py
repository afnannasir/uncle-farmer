# pyright: reportPrivateUsage=false
"""Walking a trip while monsters attack, and long walks.

  - A trip's walk stops the moment something casts at us (not only once our HP has dropped), the
    fight is handled with no walking key held and nothing planned, and the walk is planned again
    to the same goal from wherever the fight ended. The pause and the way on are logged.
  - No fusion beast is mounted while we are attacked: the fight goes first, on foot.
  - A long straight walk is not called stuck at a fixed 60 s: its limit comes from its length,
    while a walk that gets nowhere is still caught in 1.5 s."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_kind, stand_in
from test_walking import Clock, MapBot, Map, Walker
from unclefarmer import atlas
from unclefarmer.atlaslib import MapModel
from unclefarmer.clock import CLOCK
from unclefarmer.engine import beasts


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """test_walking's pretend clock, as this module's own fixture."""
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


@pytest.fixture
def model(monkeypatch: pytest.MonkeyPatch) -> Map:
    """test_walking's pretend map, as this module's own fixture."""
    m = Map()
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: as_kind(MapModel, m)))
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "map %s" % mid))
    return m


class Mover(Walker):
    """A Walker that really moves: SPEED tiles a second along whatever axes are held."""

    SPEED = 4.0

    def __init__(self, clock: Clock, speed: float = 4.0, **kw: Any) -> None:
        super().__init__(clock, pos=(0.0, 0.0), **kw)
        self.clock = clock
        self.speed = speed
        self.t = clock.time()
        self.traveling = False
        self.in_fight = False
        self.walk_only = False
        self.casting: set[int] = set()

    def look_after(self, me: dict[str, Any] | None = None) -> dict[str, Any]:
        now = self.clock.time()
        dt, self.t = now - self.t, now
        x, y = self._pos
        step = self.speed * dt
        x += step * ((("d" in self.held) - ("a" in self.held)))
        y += step * ((("s" in self.held) - ("w" in self.held)))
        self._pos = (x, y)
        return {"x": x, "y": y, "hp": 100}

    def attackers(self) -> set[int]:
        return set(self.casting)


def test_a_long_straight_walk_is_not_called_stuck_at_60_seconds(clock: Clock) -> None:
    b = Mover(clock)
    start = clock.time()
    assert b.walk_axis(0, 400.0) == "arrived"            # 100 s on foot
    assert clock.time() - start > 60.0


def test_a_long_slanted_walk_too(clock: Clock) -> None:
    b = Mover(clock)
    assert b.walk_diag((380.0, 380.0)) == "arrived"


def test_the_limit_still_ends_a_walk_that_crawls_on_for_ever(clock: Clock) -> None:
    b = Mover(clock, speed=0.05)                         # moving, but hopelessly slowly
    assert b.walk_axis(0, 1000.0) == "stuck"
    assert b.walk_timeout(4000.0) == b.WALK_MAX


def test_a_walk_that_gets_nowhere_is_still_stuck_at_once(clock: Clock) -> None:
    b = Mover(clock, speed=0.0)
    start = clock.time()
    assert b.walk_axis(0, 400.0) == "stuck"
    assert clock.time() - start < 3.0


def test_the_limit_grows_with_the_distance() -> None:
    w = Mover.__new__(Mover)
    assert w.walk_timeout(10) == w.WALK_MIN
    assert w.walk_timeout(400) > 100
    assert w.walk_timeout(10_000) == w.WALK_MAX


def test_on_a_trip_a_monster_casting_at_us_stops_the_walk_before_any_hp_is_lost(clock: Clock) -> None:
    b = Mover(clock)
    b.p["fight_back"] = True
    b.traveling = True
    b.casting = {-41}
    assert b.walk_axis(0, 400.0) == "attacked"
    assert b.held == set()                                # no walking key left held


def test_off_a_trip_the_farm_loop_keeps_choosing_its_own_fights(clock: Clock) -> None:
    b = Mover(clock)
    b.p["fight_back"] = True
    b.casting = {-41}
    assert b.walk_axis(0, 40.0) == "arrived"


@pytest.mark.parametrize("why", ["walking assistant", "fighting back off", "already fighting"])
def test_no_stop_for_attacks_where_fights_are_not_wanted(clock: Clock, why: str) -> None:
    b = Mover(clock)
    b.p["fight_back"] = why != "fighting back off"
    b.traveling = True
    b.walk_only = why == "walking assistant"
    b.in_fight = why == "already fighting"
    b.casting = {-41}
    assert b.walk_axis(0, 40.0) == "arrived"


class Trip(MapBot):
    """walk_to on a trip: the first walk is broken off by an attack, the fight clears it."""

    def __init__(self, m: Map, attacks: int = 1, leftover: bool = False) -> None:
        super().__init__(m, pos=(1, 1))
        self.traveling = True
        self.kills = 0
        self.order: list[str] = []
        self.goals: list[tuple[float, float]] = []
        self.attacks = attacks
        self.leftover = leftover            # an attacker fight_back can't fight stays casting
        self.mounts: list[str] = []

    def attackers(self) -> set[int]:
        return {-41} if self.attacks > 0 or self.leftover else set()

    def fight_back(self) -> None:
        self.order.append("fight")
        if self.attacks > 0:
            self.attacks -= 1
            self.kills += 1

    def walk_diag(self, target: tuple[float, float], map_before: Any = None, tol: float = 0.5,
                  timeout: Any = None, near: Any = None) -> str:
        self.order.append("walk")
        self.goals.append(target)
        return "attacked" if self.attacks > 0 else "arrived"


def test_an_attack_on_a_trip_pauses_the_walk_fights_and_walks_on_to_the_same_goal(model: Map,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beasts, "ensure", stand_in(beasts.ensure, lambda b, purpose=None: None))
    b = Trip(model)
    assert b.walk_to((10, 10)) == "arrived"
    assert b.order == ["walk", "fight", "walk"]          # nothing walked while fighting
    assert b.goals == [(10, 10), (10, 10)]               # the destination kept
    assert any("attacked on the way - stopping to fight" in line for line in b.logs)
    assert any("walking on to 10,10" in line for line in b.logs)


def test_several_attackers_each_get_fought_before_the_walk_goes_on(model: Map, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beasts, "ensure", stand_in(beasts.ensure, lambda b, purpose=None: None))
    b = Trip(model, attacks=3)
    assert b.walk_to((10, 10)) == "arrived"
    assert b.order == ["walk", "fight"] * 3 + ["walk"]
    assert b.kills == 3


def test_an_attacker_that_cannot_be_fought_is_left_for_a_while(model: Map, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beasts, "ensure", stand_in(beasts.ensure, lambda b, purpose=None: None))
    b = Trip(model, attacks=0, leftover=True)
    b._attacks_ignored_until = 0.0
    b.clock = Clock()
    b._fight_on_the_way((10, 10))
    assert b._attacks_ignored_until > b.clock.time()     # no stop-start at every step


class Rider:
    """beasts.ensure's view of the bot."""

    def __init__(self, attacked: bool) -> None:
        self.p = {"speed_beast": "auto"}
        self.beasts_refused: dict[int, float] = {}
        self.last_combat = 0.0
        self.walk_only = False
        self.attacked = attacked
        self.lines: list[str] = []

    def attackers(self) -> set[int]:
        return {-41} if self.attacked else set()

    def me(self) -> dict[str, Any]:
        return {"x": 0, "y": 0}

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)


@pytest.mark.parametrize("attacked", [True, False])
def test_no_fusion_beast_is_mounted_while_attacked(attacked: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    swift = {"id": 1, "name": "Nightmare Beast", "kind": "swift", "active": False, "speed": 9, "free": True}
    monkeypatch.setattr(beasts, "owned", stand_in(beasts.owned, lambda b: [dict(swift)]))
    pressed: list[str] = []
    monkeypatch.setattr(beasts, "_press", stand_in(beasts._press, lambda b, rows, beast, until_attacked=False: pressed.append(beast["name"]) or True))
    r = Rider(attacked)
    beasts.ensure(r, beasts.TRAVEL)       # type: ignore[arg-type]
    if attacked:
        assert pressed == [] and any("staying on foot" in line for line in r.lines)
        beasts.ensure(r, beasts.TRAVEL)   # type: ignore[arg-type]
        assert sum("staying on foot" in line for line in r.lines) == 1      # said once
    else:
        assert pressed == ["Nightmare Beast"]


def test_getting_off_the_beast_for_a_fight_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    swift = {"id": 1, "name": "Nightmare Beast", "kind": "swift", "active": True, "speed": 9, "free": True}
    monkeypatch.setattr(beasts, "owned", stand_in(beasts.owned, lambda b: [dict(swift)]))
    pressed: list[str] = []
    monkeypatch.setattr(beasts, "_press", stand_in(beasts._press, lambda b, rows, beast, until_attacked=False: pressed.append(beast["name"]) or True))
    r = Rider(attacked=True)
    r.p["fight_beast"] = "none"
    beasts.ensure(r, beasts.FIGHT)        # type: ignore[arg-type]
    assert pressed == ["Nightmare Beast"]                 # off the Swift one, attacked or not
