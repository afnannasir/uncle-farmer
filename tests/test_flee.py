"""Fleeing a lost fight (flee_on_panic, "When HP stays low: go to town with the hearthstone, heal
and come back"). Only while farming on the farm's own map, with a hearthstone; a hit interrupts
the hearthstone's cast, so one that doesn't take us home leaves us fighting on for a while. The
FLEE_MOST-th flee within FLEE_WINDOW ends the run in town: the map is too hard."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Clock, returning, stand_in

from test_farming import Farm
from unclefarmer.clock import CLOCK
from unclefarmer.engine.botparts.base import LosingFight, Stopped
from unclefarmer.engine.botparts import health
from unclefarmer.engine.botparts.health import HealthMixin

FLEE = {"flee_on_panic": True, "panic_below": 30, "panic_after": 3.0}


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c)
    return c


class Fleer(Farm):
    """A farm bot whose HP readings and town are scripted."""

    def __init__(self, hp: list[int], profile: dict[str, Any] | None = None) -> None:
        super().__init__(dict(FLEE, **(profile or {})))
        self.in_instance = False
        self.hearthstone = True
        self.go_home_result = True
        self.go_home_calls: list[int] = []
        self.hp = list(hp)
        self.farming_on = 5281
        self.map = 5281
        self.mode_sets = 0

    def go_home(self, tries: int = 3) -> bool:
        self.go_home_calls.append(tries)
        if self.go_home_result:
            self.map = 1081
        return self.go_home_result

    def look_after(self, me: dict[str, Any] | None = None) -> dict[str, Any]:
        return {"hp": self.hp.pop(0) if len(self.hp) > 1 else self.hp[0], "maxhp": 100}

    def restock_now(self, start: bool = False) -> bool:
        self.restock_calls.append(start)
        return False

    def set_modes(self) -> None:
        self.mode_sets += 1

    may_flee = HealthMixin.may_flee


def test_it_may_flee_only_farming_on_the_farm_map_with_a_hearthstone() -> None:
    b = Fleer([100])
    assert b.may_flee() is True
    b.map = 1081
    assert b.may_flee() is False                     # in town (or on a trip): no
    b.map, b.farming_on = 5281, None
    assert b.may_flee() is False                     # not farming
    b.farming_on, b.in_instance = 5281, True
    assert b.may_flee() is False                     # a dungeon: its visit can't be left
    b.in_instance, b.p["flee_on_panic"] = False, False
    assert b.may_flee() is False


def test_without_a_hearthstone_it_fights_on_and_says_so_once() -> None:
    b = Fleer([100])
    b.hearthstone = False
    assert b.may_flee() is False and b.may_flee() is False
    assert sum("no hearthstone" in line for line in b.spoken) == 1


def test_a_flee_goes_home_heals_restocks_and_goes_back(clock: Clock) -> None:
    b = Fleer([40, 60, 80, 95])
    b._flee(5281)                                    # pyright: ignore[reportPrivateUsage]
    assert b.go_home_calls == [3]
    assert b.restock_calls == [False]
    assert b.travels == [(5281, None)] and b.map == 5281
    assert any("leaving the fight: hearthstone to town" in line for line in b.spoken)
    assert b.mode_sets == 1


def test_healing_in_town_gives_up_after_a_while(clock: Clock) -> None:
    b = Fleer([40])
    b.sleep = lambda s: clock.advance(s)              # type: ignore[method-assign]
    b._flee(5281)                                    # pyright: ignore[reportPrivateUsage]
    assert b.travels == [(5281, None)]               # went back anyway, after FLEE_HEAL_MOST


def test_a_hearthstone_that_fails_leaves_us_fighting_for_a_while(clock: Clock) -> None:
    b = Fleer([20])
    b.go_home_result = False
    b._flee(5281)                                    # pyright: ignore[reportPrivateUsage]
    assert any("couldn't get away - fighting on" in line for line in b.spoken)
    assert b.travels == [] and b.may_flee() is False
    clock.advance(b.FLEE_RETRY + 1)
    assert b.may_flee() is True


def test_the_third_flee_in_half_an_hour_stops_in_town(clock: Clock) -> None:
    b = Fleer([95])
    for _i in range(2):
        b._flee(5281)                                # pyright: ignore[reportPrivateUsage]
        clock.advance(10 * 60)
    with pytest.raises(Stopped) as e:
        b._flee(5281)                                # pyright: ignore[reportPrivateUsage]
    assert e.value.stay is True and b.map == 1081
    assert any("fled 3 times in 30 minutes" in line for line in b.spoken)


def test_flees_further_apart_never_stop(clock: Clock) -> None:
    b = Fleer([95])
    for _i in range(4):
        b._flee(5281)                                # pyright: ignore[reportPrivateUsage]
        clock.advance(20 * 60)


def test_the_farm_loop_turns_a_lost_fight_into_a_flee(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Fleer([95])
    fled: list[int] = []

    def flee(self: Fleer, map_id: int) -> None:
        fled.append(map_id)
    monkeypatch.setattr(Fleer, "_flee", flee)
    assert b._set_back(5281, LosingFight()) is False  # pyright: ignore[reportPrivateUsage]
    assert fled == [5281]


@pytest.fixture
def no_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(health.repair, "check", stand_in(health.repair.check, returning(False)))


@pytest.mark.usefixtures("no_repair")
def test_hp_that_stays_low_while_farming_raises_a_lost_fight(clock: Clock) -> None:
    from test_health import H, low_red
    h = low_red(H(dict(FLEE)), hp=200)
    h.may_flee = lambda: True                         # type: ignore[method-assign]
    h.low_since = clock.now - 5
    with pytest.raises(LosingFight):
        h.look_after()
    assert h.low_since is None                        # the next low counts from scratch


@pytest.mark.usefixtures("no_repair")
def test_off_the_farm_the_stop_choice_still_stops(clock: Clock) -> None:
    from test_health import H, low_red
    h = low_red(H({"stop_on_panic": True, "panic_below": 30, "panic_after": 3}), hp=200)
    h.low_since = clock.now - 5
    with pytest.raises(Stopped):
        h.look_after()


def test_no_second_flee_while_the_hearthstone_is_being_used(clock: Clock) -> None:
    """go_home fights back when a hit stops the cast, and that fight's look_after must not flee
    again: nothing would catch the second LosingFight."""
    b = Fleer([40, 95])
    during: list[bool] = []

    def go_home(tries: int = 3) -> bool:
        during.append(b.may_flee())                   # still on the farm map, HP low
        b.map = 1081
        return True
    b.go_home = go_home                               # type: ignore[method-assign]
    b._flee(5281)                                    # pyright: ignore[reportPrivateUsage]
    assert during == [False]
    assert b.may_flee() is True                       # and allowed again afterwards


@pytest.mark.usefixtures("no_repair")
def test_with_hp_fine_the_hearthstone_is_not_looked_for(clock: Clock) -> None:
    from test_health import H, low_red
    h = low_red(H(dict(FLEE)), hp=1000)               # full HP
    asked: list[int] = []

    def may_flee() -> bool:
        asked.append(1)
        return True
    h.may_flee = may_flee                             # type: ignore[method-assign]
    h.look_after()
    assert asked == []                                # it can read the bag: only once HP is low
