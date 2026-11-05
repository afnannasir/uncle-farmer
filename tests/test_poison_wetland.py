# pyright: reportPrivateUsage=false
# White-box tests: they replace the travel part's own leg-taking (cross_exit) and read its lines.
"""Temporary: no trip to Polson Wetland (5141), or through it, unless the Poison Immunity talent
is activated - refused before anything moves, with one clear log line. Every other trip is as
before, and asks the game nothing more."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import JsonOnly, failing, returning, stand_in

from test_travel_part import Bot, quiet  # quiet: the travel tests' autouse fixture, used here too
from unclefarmer import travel as travel_mod
from unclefarmer.engine.botparts import travel as part
from unclefarmer.engine.botparts.base import Stopped

MESSAGE = "Travel to Polson Wetland (5141) refused: Poison Immunity Talent is not activated."
FERTILE_WILDS, POLSON, WATER_CAVE = 1061, 5141, 5142
__all__ = ["quiet"]


def traveller(talents: list[dict[str, Any]] | None, here: int = FERTILE_WILDS) -> tuple[Bot, list[str], list[Any]]:
    """A bot on map `here` whose agent reports these talents (None: no `mem skills` answer),
    with every leg it takes recorded instead of walked."""
    b = Bot()
    b._map = here
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


ACTIVE = [{"id": 1, "b": 1, "name": "Poison Immunity"}]
INACTIVE = [{"id": 1, "b": 0, "name": "Poison Immunity"}, {"id": 2, "b": 1, "name": "Anti-Corrosion"}]


def exit_to(here: int, dest: int) -> dict[str, Any]:
    return {"do": "exit", "map": here, "exit": 0, "stand": [3, 3], "rect": [1, 1, 5, 5], "dest": dest}


@pytest.mark.parametrize("talents", [INACTIVE, [], None], ids=["not activated", "no talents", "no answer"])
def test_travel_to_polson_wetland_is_refused_before_anything_moves(monkeypatch: pytest.MonkeyPatch,
                                                                   talents: list[dict[str, Any]] | None) -> None:
    b, _asked, crossed = traveller(talents)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, failing("no route is planned once refused")))
    with pytest.raises(Stopped) as e:
        b.travel(POLSON)
    assert str(e.value) == MESSAGE
    assert b.lines == [MESSAGE]                              # said once, exactly
    assert crossed == [] and b.walked == [] and b.keys == [] and b.used == []
    assert b.traveling is False and "restock looked at" not in b.lines


def test_with_the_talent_activated_the_trip_goes_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    b, asked, crossed = traveller(ACTIVE)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(b._map, POLSON)]))
    b.travel(POLSON)
    assert [leg["dest"] for leg in crossed] == [POLSON] and b._map == POLSON
    assert MESSAGE not in b.lines and asked.count("mem skills") == 1


def test_a_trip_through_polson_wetland_is_refused_too(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(INACTIVE)
    route = [exit_to(FERTILE_WILDS, POLSON), exit_to(POLSON, WATER_CAVE)]
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: route))
    with pytest.raises(Stopped, match="Poison Immunity Talent is not activated"):
        b.travel(WATER_CAVE)
    assert crossed == [] and b.lines.count(MESSAGE) == 1


def test_a_trip_through_it_goes_with_the_talent(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(ACTIVE)
    routes = {FERTILE_WILDS: [exit_to(FERTILE_WILDS, POLSON), exit_to(POLSON, WATER_CAVE)],
              POLSON: [exit_to(POLSON, WATER_CAVE)]}
    def plan(here_map: int, *a: object, **k: object) -> list[dict[str, Any]]:
        return routes.get(here_map, [])
    monkeypatch.setattr(travel_mod, "plan", plan)
    b.travel(WATER_CAVE)
    assert [leg["dest"] for leg in crossed] == [POLSON, WATER_CAVE]


def test_other_trips_are_untouched_and_ask_nothing_more(monkeypatch: pytest.MonkeyPatch) -> None:
    b, asked, crossed = traveller(INACTIVE, here=1041)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(1041, FERTILE_WILDS)]))
    b.travel(FERTILE_WILDS)
    assert [leg["dest"] for leg in crossed] == [FERTILE_WILDS]
    assert "mem skills" not in asked and MESSAGE not in b.lines


def test_already_on_polson_wetland_the_bot_may_move_about_and_leave(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, _crossed = traveller(INACTIVE, here=POLSON)
    monkeypatch.setattr(travel_mod, "plan",
                        stand_in(travel_mod.plan, lambda *a, **k: [{"do": "walk", "map": POLSON, "cell": [7, 8]}]))
    b.travel(POLSON, (7, 8))                                 # a cell on the map it already stands on
    assert b.walked == [(7, 8)]
    b2, _asked2, crossed2 = traveller(INACTIVE, here=POLSON)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(POLSON, FERTILE_WILDS)]))
    b2.travel(FERTILE_WILDS)                                 # getting out of it is never refused
    assert [leg["dest"] for leg in crossed2] == [FERTILE_WILDS] and MESSAGE not in b2.lines


@pytest.mark.usefixtures("allow_ignored_failures")
def test_a_talent_reading_that_fails_refuses_rather_than_risks(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(ACTIVE)

    def broken(line: str) -> Any:
        raise OSError("the agent didn't answer")
    b.a = JsonOnly(broken)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, failing("no route once refused")))
    with pytest.raises(Stopped, match="refused"):
        b.travel(POLSON)
    assert crossed == []


def test_the_walking_assistant_is_refused_the_same_way(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, crossed = traveller(INACTIVE)
    no_beasts: list[dict[str, Any]] = []
    monkeypatch.setattr(part.beasts, "owned", returning(no_beasts))
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, failing("no route once refused")))
    with pytest.raises(Stopped, match="refused"):
        b.go(POLSON)
    assert crossed == [] and b.walk_only is False


def test_every_trip_in_the_game_data_to_the_cave_or_port_crosses_polson_wetland() -> None:
    # why a trip THROUGH it is refused too: these are only reached across its poisoned ground
    for dest in (WATER_CAVE, 5143):
        legs = travel_mod.plan(5121, (100, 100), dest, None, transport=True)
        assert legs is not None and any(leg.get("map") == POLSON for leg in legs)
