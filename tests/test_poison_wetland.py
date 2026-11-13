# pyright: reportPrivateUsage=false
# White-box tests: they replace the travel part's own leg-taking (cross_exit) and read its lines.
"""Polson Wetland (5141): its ground poisons whoever stands on it, unless Poison Immunity is
activated or the character is on a fusion beast. A trip THROUGH it gets on a beast before the step
in (refused only when none will go on); a trip that ends there goes, with one spoken warning;
leaving it is never refused. Every other trip is as before, and asks the game nothing more."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import JsonOnly, stand_in

from test_travel_part import Bot, quiet  # quiet: the travel tests' autouse fixture, used here too
from unclefarmer import travel as travel_mod
from unclefarmer.engine.botparts import travel as part
from unclefarmer.engine.botparts.base import Stopped

FERTILE_WILDS, POLSON, WATER_CAVE = 1061, 5141, 5142
WARNING = "Polson Wetland: Poison Immunity isn't activated"
CANT_CROSS = "can't cross Polson Wetland"
__all__ = ["quiet"]


def traveller(talents: list[dict[str, Any]] | None, here: int = FERTILE_WILDS) -> tuple[Bot, list[str], list[Any]]:
    """A bot on map `here` whose agent reports these talents (None: no `mem skills` answer),
    with every leg it takes recorded instead of walked."""
    b = Bot()
    b._map = here
    b.p["use_teleport_skills"] = False    # these count the talent's `mem skills` reads alone
    asked: list[str] = []

    def answer(line: str) -> Any:
        asked.append(line)
        if line == "mem skills":
            return {} if talents is None else {"talents": talents}
        return {}
    b.a = JsonOnly(answer)
    crossed: list[Any] = []

    def cross(leg: dict[str, Any]) -> None:
        crossed.append(leg)
        b._map = leg["dest"]
    b.cross_exit = cross
    return b, asked, crossed


class Rider:
    """Stands in for beasts.ride_any: records each try, on the map it was asked from."""

    def __init__(self, b: Bot, takes: bool = True) -> None:
        self.b, self.takes = b, takes
        self.asked_on: list[int] = []

    def __call__(self, bot: object) -> bool:
        self.asked_on.append(self.b._map)
        return self.takes


def riding(monkeypatch: pytest.MonkeyPatch, b: Bot, takes: bool = True) -> Rider:
    r = Rider(b, takes)
    monkeypatch.setattr(part.beasts, "ride_any", r)
    return r


ACTIVE = [{"id": 1, "b": 1, "name": "Poison Immunity"}]
INACTIVE = [{"id": 1, "b": 0, "name": "Poison Immunity"}, {"id": 2, "b": 1, "name": "Anti-Corrosion"}]


def exit_to(here: int, dest: int) -> dict[str, Any]:
    return {"do": "exit", "map": here, "exit": 0, "stand": [3, 3], "rect": [1, 1, 5, 5], "dest": dest}


def through(monkeypatch: pytest.MonkeyPatch, b: Bot) -> None:
    routes = {FERTILE_WILDS: [exit_to(FERTILE_WILDS, POLSON), exit_to(POLSON, WATER_CAVE)],
              POLSON: [exit_to(POLSON, WATER_CAVE)]}

    def plan(here_map: int, *a: object, **k: object) -> list[dict[str, Any]]:
        return routes.get(here_map, [])
    monkeypatch.setattr(travel_mod, "plan", plan)


@pytest.mark.parametrize("talents", [INACTIVE, [], None], ids=["not activated", "no talents", "no answer"])
def test_a_trip_to_polson_wetland_goes_with_one_spoken_warning(monkeypatch: pytest.MonkeyPatch,
                                                              talents: list[dict[str, Any]] | None) -> None:
    b, _asked, crossed = traveller(talents)
    rider = riding(monkeypatch, b)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(b._map, POLSON)]))
    b.travel(POLSON)
    assert [leg["dest"] for leg in crossed] == [POLSON]
    assert len([line for line in b.lines if line.startswith(WARNING)]) == 1
    assert rider.asked_on == []           # farming there is on foot anyway: no beast forced on
    b._map = FERTILE_WILDS
    b.travel(POLSON)                      # the warning is once a run
    assert len([line for line in b.lines if line.startswith(WARNING)]) == 1


def test_with_the_talent_activated_the_trip_goes_without_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    b, asked, crossed = traveller(ACTIVE)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(b._map, POLSON)]))
    b.travel(POLSON)
    assert [leg["dest"] for leg in crossed] == [POLSON] and b._map == POLSON
    assert not any(line.startswith(WARNING) for line in b.lines) and asked.count("mem skills") == 1


def test_a_trip_through_it_gets_on_a_beast_before_the_step_in(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(INACTIVE)
    rider = riding(monkeypatch, b)
    through(monkeypatch, b)
    b.travel(WATER_CAVE)
    assert [leg["dest"] for leg in crossed] == [POLSON, WATER_CAVE]
    assert rider.asked_on == [FERTILE_WILDS, POLSON]     # before the step in, and again on it
    assert not any(line.startswith(WARNING) or CANT_CROSS in line for line in b.lines)
    assert b.crossing_poison is False                    # only while a leg is being taken


def test_a_trip_through_it_with_no_beast_going_on_is_refused_before_the_step_in(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(INACTIVE)
    riding(monkeypatch, b, takes=False)
    through(monkeypatch, b)
    with pytest.raises(Stopped, match=CANT_CROSS):
        b.travel(WATER_CAVE)
    assert crossed == [] and sum(b.sleeps) >= part.POISON_MOUNT_WAIT     # waited out a fight first
    assert len([line for line in b.lines if CANT_CROSS in line]) == 1


def test_a_trip_through_it_with_the_talent_needs_no_beast(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(ACTIVE)
    rider = riding(monkeypatch, b, takes=False)
    through(monkeypatch, b)
    b.travel(WATER_CAVE)
    assert [leg["dest"] for leg in crossed] == [POLSON, WATER_CAVE] and rider.asked_on == []


def test_leaving_polson_wetland_is_never_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(INACTIVE, here=POLSON)
    rider = riding(monkeypatch, b, takes=False)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(POLSON, FERTILE_WILDS)]))
    b.travel(FERTILE_WILDS)
    assert [leg["dest"] for leg in crossed] == [FERTILE_WILDS]
    assert rider.asked_on == [POLSON] and b.sleeps == []     # one try, no waiting in the poison


def test_moving_about_on_polson_wetland_is_untouched(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, _crossed = traveller(INACTIVE, here=POLSON)
    rider = riding(monkeypatch, b)
    monkeypatch.setattr(travel_mod, "plan",
                        stand_in(travel_mod.plan, lambda *a, **k: [{"do": "walk", "map": POLSON, "cell": [7, 8]}]))
    b.travel(POLSON, (7, 8))                                 # a cell on the map it already stands on
    assert b.walked == [(7, 8)] and rider.asked_on == []
    assert not any(line.startswith(WARNING) for line in b.lines)


def test_other_trips_are_untouched_and_ask_nothing_more(monkeypatch: pytest.MonkeyPatch) -> None:
    b, asked, crossed = traveller(INACTIVE, here=1041)
    rider = riding(monkeypatch, b)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(1041, FERTILE_WILDS)]))
    b.travel(FERTILE_WILDS)
    assert [leg["dest"] for leg in crossed] == [FERTILE_WILDS]
    assert "mem skills" not in asked and rider.asked_on == [] and b.lines.count("restock looked at") == 1


@pytest.mark.usefixtures("allow_ignored_failures")
def test_a_talent_reading_that_fails_counts_as_no_talent(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(ACTIVE)

    def broken(line: str) -> Any:
        raise OSError("the agent didn't answer")
    b.a = JsonOnly(broken)
    rider = riding(monkeypatch, b)
    through(monkeypatch, b)
    b.travel(WATER_CAVE)
    assert [leg["dest"] for leg in crossed] == [POLSON, WATER_CAVE] and rider.asked_on


def test_every_trip_in_the_game_data_to_the_cave_or_port_crosses_polson_wetland() -> None:
    # why a trip THROUGH it rides: these are only reached across its poisoned ground
    for dest in (WATER_CAVE, 5143):
        legs = travel_mod.plan(5121, (100, 100), dest, None, transport=True)
        assert legs is not None and any(leg.get("map") == POLSON for leg in legs)
