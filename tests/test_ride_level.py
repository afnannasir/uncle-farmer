# pyright: reportPrivateUsage=false
# White-box tests: they check the travel mixin's internal helpers (leading-underscore names) directly.
"""Rides that need a level (data/transports.json "level"). Below it the transport NPC opens no
menu: Tuneful Wetland's airship to Violet City only says "Go to Violet City; requires Level 76 or
above, Go to Violet City when you're stronger." (logged 2026-11-14). The bot used to wait for a
menu, say "didn't answer", try once more and walk: about 20 seconds lost on every trip."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from unclefarmer import travel
from unclefarmer.engine.botparts import travel as bottravel
from unclefarmer.engine.botparts.travel import TravelMixin

TULIP = 1081                    # Tuneful Wetland, Tulip City
VIOLET = 1161                   # where its south airship flies, from level 76
GIANT_ROCK = 1041
SUNSET = 1021                   # where Giant Rock City's west airship flies, from level 26
HOLY_BLOOD = 1121
DARK_NIGHT = 1181               # from Holy Blood City from level 86, back from its south gate
GOUGING = 1141                  # gouging knife city: from Holy Blood City from level 66
LOKI_HALL = 1162                # where the flight back from Violet City comes down, by Tulip City
REFUSAL = "Go to Violet City; requires Level 76 or above, Go to Violet City when you're stronger."


@pytest.fixture(autouse=True)
def fresh_plans() -> None:
    travel._plan_cache.clear()


def airship(start: int, dest: int) -> dict[str, Any]:
    return next(t for t in travel.transports() if t["kind"] == "airship" and t["map"] == start and t["dest"] == dest)


@pytest.mark.parametrize("start, dest, level", [(GIANT_ROCK, SUNSET, 26), (GIANT_ROCK, 1061, 36),
                                                (TULIP, 1101, 56), (HOLY_BLOOD, GOUGING, 66),
                                                (TULIP, VIOLET, 76), (HOLY_BLOOD, DARK_NIGHT, 86)])
def test_each_airship_out_of_a_capital_carries_its_level(start: int, dest: int, level: int) -> None:
    assert travel.ride_level(airship(start, dest)) == level


def cable_car(start: int, dest: int) -> dict[str, Any]:
    return next(t for t in travel.transports() if t["kind"] == "cable_car" and t["map"] == start and t["dest"] == dest)


def test_the_cable_cars_out_of_giant_rock_city_carry_their_level_and_none_back() -> None:
    assert travel.ride_level(cable_car(1042, 1082)) == 45          # to Tulip City's underground
    assert travel.ride_level(cable_car(1042, 1122)) == 60          # to Holy Blood City's
    others = [t for t in travel.transports() if t["kind"] == "cable_car" and t["dest"] not in (1082, 1122)
              or t["kind"] == "cable_car" and t["map"] != 1042]
    assert len(others) == 8 and all(travel.ride_level(t) == 0 for t in others)


def test_every_lifting_platform_ride_is_free_and_every_fare_known() -> None:
    platforms = (1702, 1703, 1704)
    rides = [t for t in travel.transports() if t["kind"] in ("cable_car", "airship")]
    assert all(isinstance(t["cost"], int) for t in rides)
    assert all(t["cost"] == 0 for t in rides if t["map"] in platforms or t["dest"] in platforms)
    assert all(t["cost"] == 1000 for t in rides if t["kind"] == "airship")


def test_the_flights_back_to_a_capital_need_no_level() -> None:
    back = [t for t in travel.transports() if t["kind"] == "airship" and t["dest"] in (GIANT_ROCK, TULIP, HOLY_BLOOD, LOKI_HALL)]
    assert len(back) == 6 and all(travel.ride_level(t) == 0 for t in back)


@pytest.mark.parametrize("start, cell", [(DARK_NIGHT, (220, 274)), (GOUGING, (83, 32))])
def test_dark_night_and_gouging_knife_city_fly_back_to_holy_blood_city(start: int, cell: tuple[int, int]) -> None:
    # both were missing: from dark night City the bot walked out through skeleton basin
    _rides, off = travel.ride_rules({}, 1)
    legs = travel.plan(start, cell, HOLY_BLOOD, avoid=off, items=False)
    assert legs and [leg["kind"] for leg in legs if leg["do"] == "npc"] == ["airship"]
    assert airship(start, HOLY_BLOOD)["lands"]["map"] == HOLY_BLOOD


@pytest.mark.parametrize("level, flies", [(75, False), (76, True), (100, True), (0, True)])
def test_a_ride_above_the_characters_level_is_left_out(level: int, flies: bool) -> None:
    _rides, off = travel.ride_rules({}, level)
    assert (airship(TULIP, VIOLET)["id"] not in off) is flies    # 0: the level isn't known
    assert airship(TULIP, 1101)["id"] not in off


class Traveller:
    """What TravelMixin._plan_legs reads: where we are, the bag, the profile."""

    def __init__(self, level: int) -> None:
        self.p: dict[str, Any] = {"use_transport": True}
        self.level = level
        port = travel._ports(TULIP)[0]
        self.at = (float(port["x"]), float(port["y"]))

    def me(self) -> dict[str, Any]:
        return {"x": self.at[0], "y": self.at[1], "level": self.level}

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": "Arnebia", "count": 100000}]


def ride_ids(legs: list[dict[str, Any]]) -> list[int]:
    return [int(leg["id"]) for leg in legs if leg["do"] == "npc"]


@pytest.mark.parametrize("level, flies", [(50, False), (80, True)])
def test_the_bot_plans_the_violet_city_airship_only_from_its_level(level: int, flies: bool) -> None:
    plan_legs: Any = TravelMixin._plan_legs
    legs: list[dict[str, Any]] = plan_legs(as_bot(Traveller(level)), TULIP, VIOLET, None, set())
    assert legs                                                   # a way there either way
    assert (airship(TULIP, VIOLET)["id"] in ride_ids(legs)) is flies


class Agent:
    """The agent's `said`: what the game said after the talk."""

    def __init__(self, said: list[str]) -> None:
        self.said = said

    def json(self, line: str) -> Any:
        return {"now": 5, "said": [{"text": t} for t in self.said]}


class Rider:
    """What TravelMixin.ride and _take_leg use; the NPC opens no menu (_ride fails)."""

    _level_refusal = TravelMixin._level_refusal
    NPC_MODE = TravelMixin.NPC_MODE
    RIDE_TRIES = TravelMixin.RIDE_TRIES

    def __init__(self, said: list[str]) -> None:
        self.p: dict[str, Any] = {}
        self.a = Agent(said)
        self.lines: list[str] = []
        self.rides = 0

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def target_mode_now(self) -> int:
        return 2

    def set_target_mode(self, mode: int) -> bool:
        return True

    def map_id(self) -> int:
        return TULIP

    def close_dialogs(self) -> None:
        pass

    def _ride(self, leg: dict[str, Any], mid: int, keys: bool = False) -> bool:
        self.rides += 1
        return False

    def ride(self, leg: dict[str, Any], keys: bool = False) -> bool:
        return TravelMixin.ride(as_bot(self), leg, keys)


def leg_of(t: dict[str, Any]) -> dict[str, Any]:
    return {"do": "npc", "id": t["id"], "map": t["map"], "npc": t["npc"], "dest": t["dest"]}


def test_the_games_level_refusal_leaves_the_ride_out_at_once_and_for_the_run() -> None:
    who = Rider([REFUSAL])
    leg = leg_of(airship(TULIP, VIOLET))
    avoid: set[Any] = set()
    failed: dict[Any, int] = {}
    assert TravelMixin._take_leg(as_bot(who), leg, avoid, failed) is False
    assert who.rides == 1 and leg["id"] in avoid                  # no second go: same answer
    assert bottravel.rides_too_high(who) == {leg["id"]: 76}
    assert any("takes only level 76 and above" in line for line in who.lines)


def test_a_ride_that_fails_for_another_reason_is_tried_once_more_as_before() -> None:
    who = Rider(["The airship is full"])
    leg = leg_of(airship(TULIP, VIOLET))
    avoid: set[Any] = set()
    failed: dict[Any, int] = {}
    TravelMixin._take_leg(as_bot(who), leg, avoid, failed)
    assert avoid == set() and failed == {leg["id"]: 1}            # once more before leaving it out
    assert bottravel.rides_too_high(who) == {}
    TravelMixin._take_leg(as_bot(who), leg, avoid, failed)
    assert leg["id"] in avoid


def test_a_ride_left_out_for_the_level_stays_out_of_the_next_plans() -> None:
    who = Traveller(0)                                            # the sheet's level not read yet
    bottravel.rides_too_high(who)[airship(TULIP, VIOLET)["id"]] = 76
    plan_legs: Any = TravelMixin._plan_legs
    legs: list[dict[str, Any]] = plan_legs(as_bot(who), TULIP, VIOLET, None, set())
    assert airship(TULIP, VIOLET)["id"] not in ride_ids(legs)
