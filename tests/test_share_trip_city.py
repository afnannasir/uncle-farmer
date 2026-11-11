"""Which instructor a farming run's secret room trip goes to (engine/chores.py, town.nearest), on
the real maps. Instructors with secret rooms stand in three capitals: Giant Rock (central plains),
Tulip City (Tuneful Wetland) and holy blood city (High climbing grassland).

The trip goes out from the farm and comes back to it. With a hearthstone in the bag (Use travel
items), the way there alone made the hearthstone's square look the cheapest from almost anywhere -
and runs end with the hearthstone, so that square is where the run started: the trip went back
to the starting city and then walked the whole way out to the farm again. The choice now counts
the way back to the farm too."""

from __future__ import annotations

import threading
from typing import Any

import pytest

from unclefarmer.clock import Waitable
from unclefarmer.engine import chores, town
from unclefarmer.engine.bot import Stopped
from unclefarmer.engine.botparts.farming import FarmingMixin
from unclefarmer.settings import PROFILE_DEFAULTS
from conftest import as_bot

GIANT_ROCK, TULIP, HOLY_BLOOD = 1041, 1081, 1121
GLORIOUS_GRASSLAND = 1101          # a farm next to Tulip City, far from Giant Rock
HALF = chores.SLOT


class Clock:
    def __init__(self, now: float) -> None:
        self.now = now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Waitable, timeout: float | None = None) -> bool:
        self.now += timeout or 0.0
        return False


class Farmer(FarmingMixin):
    """A farming run standing on a farm, as far as a share trip looks: where it is, its bag and
    profile; travel and talk only note where they would go. The instructor's menu offers nothing,
    so the visit stops there and the trip goes back to the farm."""

    def __init__(self, mid: int, cell: tuple[float, float], hearth_home: str = "giant rock square",
                 hearthstone: bool = True) -> None:
        self.p: dict[str, Any] = {**PROFILE_DEFAULTS, "use_travel_items": hearthstone,
                                  "hearth_home": hearth_home,
                                  "chores": {**chores.DEFAULTS, "secret_room": True, "trip_lead": 10}}
        self.mid, self.cell = mid, cell
        self.clock = Clock(1000 * HALF - 9 * 60)        # 9 minutes before a half hour: a trip is due
        self.character = "Hero"
        self.share_tried = -1
        self.stop_event = threading.Event()
        self.talked: list[str] = []
        self.trips: list[int] = []
        self.lines: list[str] = []
        self.rows: list[dict[str, Any]] = [{"name": "Arnebia", "count": 500000}]
        if hearthstone:
            self.rows.append({"name": "Hearthstone", "count": 5})

    def map_id(self) -> int:
        return self.mid

    def me(self) -> dict[str, Any]:
        return {"x": self.cell[0], "y": self.cell[1]}

    def bag_rows(self) -> list[dict[str, Any]]:
        return self.rows

    def travel(self, dst: int | str, cell: tuple[float, float] | None = None) -> None:
        self.trips.append(int(dst))

    def talk(self, name: str, at: tuple[float, float] | None = None, stand: tuple[float, float] | None = None) -> bool:
        self.talked.append(name)
        return True

    def choose(self, pattern: str) -> None:
        return None

    def close_dialogs(self) -> None:
        pass

    def set_modes(self) -> None:
        pass

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)


def instructor_map(name: str) -> int:
    return next(m for m, n, _x, _y in town.knowledge.service_npcs("instructor") if n == name)


def test_the_share_trip_from_a_farm_near_tulip_city_does_not_go_back_to_the_starting_city() -> None:
    """The reported bug: started in Giant Rock (the hearthstone's square), farming next to Tulip
    City, the share trip went back to Giant Rock's instructor."""
    f = Farmer(GLORIOUS_GRASSLAND, (173, 150))
    assert chores.trip_due(as_bot(f))                   # the option is on and a trip is due
    f.trip_checked = 0.0                                # (it looks once per TRIP_CHECK seconds)
    assert f.share_trip(GLORIOUS_GRASSLAND) is True
    assert len(f.talked) == 1
    assert instructor_map(f.talked[0]) == TULIP, f.talked
    assert f.trips == [TULIP, GLORIOUS_GRASSLAND]       # there, then back to the farm
    assert any("no arnebia shared this time" in ln for ln in f.lines)


def test_the_share_trip_from_holy_blood_city_s_own_farm_uses_its_instructor() -> None:
    f = Farmer(HOLY_BLOOD, (200, 160))
    f.share_trip(HOLY_BLOOD)
    assert [instructor_map(n) for n in f.talked] == [HOLY_BLOOD]


def test_the_share_trip_from_a_farm_near_giant_rock_still_goes_to_giant_rock() -> None:
    f = Farmer(1021, (73, 200), hearth_home="tulip square")    # Mario Basin; the hearthstone goes elsewhere
    f.share_trip(1021)
    assert [instructor_map(n) for n in f.talked] == [GIANT_ROCK]


def test_without_a_hearthstone_the_nearest_instructor_is_chosen_too() -> None:
    f = Farmer(GLORIOUS_GRASSLAND, (173, 150), hearthstone=False)
    f.share_trip(GLORIOUS_GRASSLAND)
    assert [instructor_map(n) for n in f.talked] == [TULIP]


def test_the_way_back_decides_between_two_instructors() -> None:
    """town.nearest with back_to: the cheapest there-and-back, not the cheapest way there."""
    f = Farmer(GLORIOUS_GRASSLAND, (173, 150))
    there_only = town.nearest(as_bot(f), "instructor")
    round_trip = town.nearest(as_bot(f), "instructor", back_to=(GLORIOUS_GRASSLAND, (173, 150)))
    assert there_only is not None and there_only[0] == GIANT_ROCK     # the hearthstone's square
    assert round_trip is not None and round_trip[0] == TULIP


def test_an_unreachable_instructor_is_never_chosen() -> None:
    f = Farmer(GLORIOUS_GRASSLAND, (173, 150))
    nowhere = (99999, "Instructor Nobody", 10.0, 10.0)       # not on our maps: no way there
    known = town.knowledge.service_npcs("instructor")
    pick = town.nearest(as_bot(f), "instructor", [nowhere, *known], back_to=(GLORIOUS_GRASSLAND, (173, 150)))
    assert pick is not None and pick[0] == TULIP
    with pytest.raises(Stopped):
        town.visit(as_bot(f), "instructor", candidates=[nowhere], back_to=(GLORIOUS_GRASSLAND, (173, 150)))


def test_a_daily_chores_run_with_no_farm_to_go_back_to_takes_the_nearest_way() -> None:
    """Tools, Daily chores (and the chores before farming) don't come back anywhere: the nearest
    instructor from where we stand, as before."""
    f = Farmer(GIANT_ROCK, (200, 205))
    pick = town.nearest(as_bot(f), "instructor")
    assert pick is not None and pick[0] == GIANT_ROCK
