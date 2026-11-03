# pyright: reportPrivateUsage=false
"""Airship travel, simulated: boarding an airship moves the player onto the airship's own flight
hall (a 5x5 "Central Kingdom" / "Loki Union" / "Garth Empire" map whose one exit is the whole
map), and the game moves the player on to the destination map by itself on a timer.

The bot used to treat the hall as a walking map: it planned a route there and walked at its
"exit" every 4-5 s until the timer fired (real logs: "now on Central Kingdom (1054)", then the
same route five times). Here a scripted world plays the ride on a pretend clock, with the real
map data (atlas.is_ride) and the bot's real travel loop, and records every plan, key and walk
made while the player is aboard."""

from __future__ import annotations

from typing import Any, cast

import pytest
from conftest import Clock, stand_in

from test_travel_part import Bot, entry
from unclefarmer import atlas
from unclefarmer import travel as travel_mod
from unclefarmer.engine import beasts
from unclefarmer.engine.botparts.base import Stopped

FROM, HALL, DEST, OTHER = 1061, 1069, 1041, 5121   # Fertile Wilds -> its flight hall -> central plains
BOARD_AT = (71.0, 83.0)                             # beside the Fertile Wilds administrator
LAND_AT = (262.0, 194.0)                            # where the flight drops us on central plains
GOAL = (200, 205)


def airship_leg() -> dict[str, Any]:
    return {"do": "npc", "map": FROM, "npc": "Airship Administrator", "kind": "airship", "dest": DEST,
            "id": 12, "choose": ["go to giant rock"], "at": [72, 83], "stand": [71, 83]}


class Sky:
    """The game's side of a flight: boarding puts us in the hall, and the timer (`flight`
    seconds later) moves us to `lands` - or nowhere, when `lands` is None."""

    def __init__(self, clock: Clock, flight: float = 25.0, lands: int | None = DEST,
                 land_at: tuple[float, float] = LAND_AT) -> None:
        self.clock = clock
        self.map, self.pos = FROM, BOARD_AT
        self.flight, self.lands, self.land_at = flight, lands, land_at
        self.boarded_at: float | None = None

    def board(self) -> None:
        self.map, self.pos = HALL, (3.0, 3.0)
        self.boarded_at = self.clock.time()

    def tick(self) -> None:
        if (self.map == HALL and self.lands is not None and self.boarded_at is not None
                and self.clock.time() - self.boarded_at >= self.flight):
            self.map, self.pos = self.lands, self.land_at


class Flyer(Bot):
    """The travel stand-in on a scripted world: what it reads comes from the Sky, and every plan,
    key, held key and walk is logged with the map it was made on."""

    def __init__(self, clock: Clock, sky: Sky) -> None:
        super().__init__(clock)
        self.sky = sky
        self.on_hall: list[str] = []          # anything done while aboard
        self.plans: list[tuple[int, tuple[float, float]]] = []
        self.p.update(use_airships=True, use_transport=True)

    def map_id(self) -> int:
        self.sky.tick()
        return self.sky.map

    def settled_map(self, seconds: float = 15) -> int:
        return self.map_id()

    def me(self) -> dict[str, Any]:
        self.sky.tick()
        return {"x": self.sky.pos[0], "y": self.sky.pos[1], "hp": 100}

    def _aboard(self, what: str) -> None:
        if self.sky.map == HALL:
            self.on_hall.append(what)

    def key(self, k: str, ms: int | None = None) -> None:
        self._aboard("key " + k)
        super().key(k, ms)

    def hold(self, k: str) -> None:
        self._aboard("hold " + k)
        super().hold(k)

    def walk_to(self, cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        self._aboard("walk %s" % (cell,))
        return super().walk_to(cell, map_change, near)

    def set_target_mode(self, want: int) -> bool:
        self.target_modes.append(want)
        self.game_mode = want
        return True

    def cross_exit(self, leg: dict[str, Any]) -> None:
        self._aboard("exit %s" % leg["map"])
        self.sky.map, self.sky.pos = leg["dest"], (100.0, 100.0)    # walked out to the next map

    def choose(self, pattern: str) -> dict[str, Any] | None:
        self.chosen.append(pattern)
        self.sky.board()                       # the administrator flies us at once
        return {"options": [pattern]}


@pytest.fixture(autouse=True)
def quiet(monkeypatch: pytest.MonkeyPatch) -> None:
    """No mounting, and short map names (the map data itself stays real: atlas.is_ride)."""
    monkeypatch.setattr(beasts, "ensure", stand_in(beasts.ensure, lambda b, purpose: None))
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "map%d" % int(mid)))


@pytest.fixture
def planner(monkeypatch: pytest.MonkeyPatch) -> list[tuple[int, tuple[float, float]]]:
    """travel.plan, recorded: the airship from Fertile Wilds, a walk on the goal map, a walk out of
    any other map towards it."""
    calls: list[tuple[int, tuple[float, float]]] = []

    def plan(here_map: int, here_cell: Any, goal_map: int, goal_cell: Any = None, **_kw: Any) -> list[dict[str, Any]]:
        calls.append((int(here_map), (float(here_cell[0]), float(here_cell[1]))))
        if here_map == FROM:
            return [airship_leg(), {"do": "walk", "map": DEST, "cell": list(goal_cell)}]
        if here_map == goal_map:
            return [{"do": "walk", "map": goal_map, "cell": list(goal_cell)}]
        return [{"do": "exit", "map": here_map, "exit": 0, "stand": [5, 5], "rect": [1, 1, 9, 9], "dest": goal_map},
                {"do": "walk", "map": goal_map, "cell": list(goal_cell)}]
    monkeypatch.setattr(travel_mod, "plan", plan)
    return calls


def fly(clock: Clock, sky: Sky, planner: list[tuple[int, tuple[float, float]]]) -> Flyer:
    b = Flyer(clock, sky)
    b.dialog_value = {"open": 0, "top": None}
    b.travel(DEST, GOAL)
    return b


def test_the_flight_halls_are_ride_maps_from_the_map_data() -> None:
    halls = [1024, 1053, 1054, 1069, 1085, 1086, 1106, 1126, 1127, 1142, 1162, 1182]
    assert all(atlas.is_ride(m) for m in halls)
    assert all(atlas.is_ride(m) for m in (1055, 1056, 1087, 1128))          # cable cars
    assert not any(atlas.is_ride(m) for m in (1041, 1042, 1061, 1081, 5302, 1104, 1124))


def test_boarding_moves_us_to_the_hall_and_nothing_is_planned_or_pressed_there(
        clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock)
    b = fly(clock, sky, planner)
    assert sky.boarded_at is not None
    assert b.on_hall == []                                     # no key, no held key, no walk aboard
    assert all(m != HALL for m, _cell in planner)              # no route planned aboard
    assert not any("stuck" in line for line in b.lines)
    assert any("riding - waiting to arrive" in line for line in b.lines)


def test_the_bot_waits_for_the_real_transition_then_plans_from_where_it_landed(
        clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock, flight=25.0)
    b = fly(clock, sky, planner)
    assert sky.boarded_at is not None and clock.time() - sky.boarded_at >= 25.0
    # the plan after the ride starts from the landing, not from where we boarded
    assert planner[-1] == (DEST, LAND_AT)
    assert b.walked[-1] == GOAL                                # the original destination, kept
    assert any(line.startswith("arrived on map%d at 262,194" % DEST) for line in b.lines)
    assert b.target_modes == [3, 2]                            # NPC mode to board, then back


def test_a_late_transition_is_still_waited_for(clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock, flight=150.0)
    b = fly(clock, sky, planner)
    assert b.on_hall == [] and planner[-1] == (DEST, LAND_AT)


def test_a_ride_that_never_moves_us_on_stops_without_walking_or_claiming_arrival(
        clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock, lands=None)
    b = Flyer(clock, sky)
    b.dialog_value = {"open": 0, "top": None}
    with pytest.raises(Stopped) as e:
        b.travel(DEST, GOAL)
    assert "never arrived" in str(e.value)
    assert b.on_hall == [] and all(m != HALL for m, _cell in planner)
    assert not any(line.startswith("arrived on") for line in b.lines)
    assert any("didn't move us on" in line for line in b.lines)
    assert clock.time() - (sky.boarded_at or 0) >= b.RIDE_WAIT


def test_an_unexpected_landing_is_planned_from_afresh(clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock, lands=OTHER, land_at=(40.0, 41.0))
    b = fly(clock, sky, planner)
    assert (OTHER, (40.0, 41.0)) in planner                    # from where the game really put us
    assert b.on_hall == [] and b.walked[-1] == GOAL


def test_starting_aboard_waits_too(clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock)
    sky.board()                                               # the run started mid-flight
    b = fly(clock, sky, planner)
    assert b.on_hall == [] and planner[0] == (DEST, LAND_AT)


def test_a_read_taken_mid_change_does_not_end_the_wait(clock: Clock, planner: list[tuple[int, tuple[float, float]]],
                                                       monkeypatch: pytest.MonkeyPatch) -> None:
    sky = Sky(clock)
    b = Flyer(clock, sky)
    b.dialog_value = {"open": 0, "top": None}
    reads = {"n": 0}

    def settled_map(seconds: float = 15) -> int:
        reads["n"] += 1
        return HALL if reads["n"] == 1 else b.map_id()       # the first settle still says the hall
    b.settled_map = settled_map
    b.travel(DEST, GOAL)
    assert reads["n"] >= 2 and b.on_hall == [] and planner[-1] == (DEST, LAND_AT)


@pytest.mark.parametrize("before", [0, 1, 2, 3])
def test_boarding_from_any_target_mode_uses_npc_mode_and_puts_the_old_one_back(
        before: int, clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock)
    b = Flyer(clock, sky)
    b.game_mode = before
    b.dialog_value = {"open": 0, "top": None}
    b.travel(DEST, GOAL)
    assert b.target_modes == [3, before]


def test_a_failed_boarding_puts_the_mode_back_too(clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock)
    b = Flyer(clock, sky)
    b.game_mode = 1
    b.talk_value = False                                      # the administrator never answered
    assert b.ride(airship_leg()) is False
    assert b.target_modes == [3, 1] and sky.boarded_at is None


def test_a_refused_menu_puts_the_mode_back(clock: Clock) -> None:
    sky = Sky(clock)
    b = Flyer(clock, sky)
    b.game_mode = 2
    b.wait_dialog_value = {"open": 1, "top": {}}
    b.dialog_value = {"open": 1, "top": {"rows": [[entry("no chance")]]}}
    b.find_entry = lambda top, pattern: None

    def no_flight(pattern: str) -> dict[str, Any]:
        b.chosen.append(pattern)
        return {"options": [pattern]}
    b.choose = no_flight
    assert b.ride(airship_leg()) is False
    assert b.target_modes == [3, 2]


def test_putting_the_mode_back_aboard_presses_no_walking_key(clock: Clock) -> None:
    sky = Sky(clock)
    b = Flyer(clock, sky)
    b.dialog_value = {"open": 0, "top": None}
    assert b.ride(airship_leg()) is True
    assert b.on_hall == [] and b.held == set()


def test_a_trip_with_more_maps_after_the_landing_goes_on(clock: Clock, planner: list[tuple[int, tuple[float, float]]]) -> None:
    sky = Sky(clock, lands=OTHER, land_at=(10.0, 10.0))
    b = Flyer(clock, sky)
    b.dialog_value = {"open": 0, "top": None}
    b.travel(DEST, GOAL)
    assert [m for m, _cell in planner] == [FROM, OTHER, DEST] and planner[-1] == (DEST, (100.0, 100.0)) and b.walked[-1] == GOAL


def test_the_real_plan_from_a_hall_is_never_asked_for(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    """With the real planner: a run that finds itself in a flight hall never plans there."""
    asked: list[int] = []
    real = travel_mod.plan

    def plan(here_map: int, *a: Any, **kw: Any) -> Any:
        asked.append(int(here_map))
        return cast(Any, real)(here_map, *a, **kw)
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, plan))
    sky = Sky(clock, lands=DEST, land_at=(200.0, 200.0))
    sky.board()
    b = Flyer(clock, sky)
    b.travel(DEST, GOAL)
    assert HALL not in asked and asked[0] == DEST
