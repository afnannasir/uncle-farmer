# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Out of a cable car through its door as soon as it arrives. A scripted cabin plays what was measured
live (2026-11-26): sound 1152 about 12 s after boarding, the door refusing a step until then, the game
putting us out 22 s after boarding. The real map data gives the doors."""

from __future__ import annotations

import threading
from typing import Any

import pytest
from conftest import Clock, JsonOnly, stand_in

from test_travel_part import Bot
from unclefarmer import atlas
from unclefarmer.engine import ridedoor
from unclefarmer.engine.botparts.base import Stopped

CAB, FROM, TO = 1706, 1703, 1082       # Cable Car No. 2: Lifting Platform No. 2 -> Underground of Tulip City


def sound_row(seq: int, sid: int) -> dict[str, Any]:
    return {"seq": seq, "id": sid, "x": None, "y": None, "t": seq * 100, "muted": False}


class Cabin:
    """The game's side of the ride: the clock, the sounds played so far, where we are."""

    def __init__(self, clock: Clock, sounds_work: bool = True, arrives: float = 12.0, put_out: float = 22.0) -> None:
        self.clock, self.start = clock, clock.time()
        self.sounds_work, self.arrives, self.put_out = sounds_work, arrives, put_out
        self.map, self.pos = CAB, (8.0, 13.0)
        self.listens: list[str] = []

    def since(self) -> float:
        return self.clock.time() - self.start

    def tick(self) -> None:
        if self.map == CAB and self.since() >= self.put_out:
            self.map, self.pos = TO, (31.0, 57.0)          # the game puts us out

    def answer(self, line: str) -> Any:
        if not self.sounds_work:
            return {}
        if line.startswith("soundlisten "):
            self.listens.append(line.split(" ", 1)[1])
            return {"hooked": True, "watch": "", "mute": "", "reason": "", "observed": 0, "dropped": 0,
                    "live_muted": 0, "unsupported": 0}
        if line.startswith("sounds "):
            after = int(line.split()[1])
            rows = [sound_row(1, 3333)] if self.since() >= 2 else []
            if self.since() >= self.arrives:
                rows.append(sound_row(2, ridedoor.ARRIVED))
            now = rows[-1]["seq"] if rows else 0
            return {"now": max(now, after), "events": [r for r in rows if r["seq"] > after], "lost": False}
        return {}


class Rider(Bot):
    def __init__(self, cabin: Cabin) -> None:
        super().__init__(cabin.clock)
        self.cabin = cabin
        self.a = JsonOnly(cabin.answer)
        self.walks: list[tuple[tuple[float, float], bool, float]] = []
        self.walk_error: Exception | None = None

    def map_id(self) -> int:
        self.cabin.tick()
        return self.cabin.map

    def settled_map(self, seconds: float = 15) -> int:
        return self.map_id()

    def me(self) -> dict[str, Any]:
        self.cabin.tick()
        return {"x": self.cabin.pos[0], "y": self.cabin.pos[1], "hp": 100}

    def walk_to(self, cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        self.walks.append((cell, map_change, self.cabin.since()))
        if self.walk_error is not None:
            raise self.walk_error
        self.test_clock.advance(2.5)
        if map_change and self.cabin.since() >= self.cabin.arrives:
            self.cabin.map, self.cabin.pos = TO, (31.0, 57.0)    # through the door
            return "map"
        if map_change:
            return "stuck"                                       # the door refuses until the arrival
        self.cabin.pos = cell
        return "arrived"


@pytest.fixture(autouse=True)
def short_names(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "label", stand_in(atlas.label, lambda mid: "map%d" % int(mid)))


def test_every_cable_car_has_its_door_and_no_flight_hall_has_one() -> None:
    cars = [m for m in atlas.maps() if "cable car" in str(atlas.maps()[m].get("name", "")).lower()]
    assert len(cars) == 10 and all(ridedoor.door(m) is not None for m in cars)
    assert ridedoor.door(1706) == ((8, 1), (8, 4))      # to Underground of Tulip City (measured: stopped at 8,4)
    assert ridedoor.door(1083) == ((8, 15), (8, 12))    # the other way, to Lifting Platform No. 2
    assert all(ridedoor.door(h) is None for h in (1024, 1053, 1069, 1085, 1126))   # airships: no door
    assert ridedoor.door(1082) is None                  # not a ride at all


def test_the_car_is_left_through_its_door_at_the_arrival_sound() -> None:
    cabin = Cabin(Clock())
    b = Rider(cabin)
    assert b.wait_ride(CAB) == TO
    assert b.walks[0][0] == (8.0, 4.0) and b.walks[0][1] is False     # walked up first, inside the cabin
    door = [w for w in b.walks if w[1]]
    assert len(door) == 1 and door[0][0] == (8.0, 1.0) and door[0][2] >= cabin.arrives
    assert cabin.since() < cabin.put_out                              # out before the game would put us out
    assert "the cable car has arrived - out through its door" in b.lines
    assert cabin.listens == ["1152", "off"]                           # listened for the ride only


def test_the_runs_other_sounds_are_still_listened_for() -> None:
    """The farm's out-of-range sound and the team's request sound stay listened for through the ride
    (the agent keeps one list a connection: the ride's must not replace theirs)."""
    from conftest import as_bot
    from unclefarmer.engine import listening
    cabin = Cabin(Clock())
    b = Rider(cabin)
    listening.want(as_bot(b), "out of range", "1023")
    listening.want(as_bot(b), "team requests", "1057")
    b.wait_ride(CAB)
    assert cabin.listens == ["1023", "1023,1057", "1023,1057,1152", "1023,1057"]


def test_the_listening_list_is_the_runs_parts_together() -> None:
    from conftest import as_bot
    from unclefarmer.engine import listening
    cabin = Cabin(Clock())
    b = Rider(cabin)
    assert listening.wanted(as_bot(b)) == "off"
    listening.want(as_bot(b), "out of range", "1023")
    listening.want(as_bot(b), "out of range", "1023")             # twice: once in the list
    listening.want(as_bot(b), "team requests", "1057")
    assert listening.with_ours(as_bot(b), "1000-1010") == "1000-1010,1023,1057"   # a script's wait on top
    listening.want(as_bot(b), "out of range", None)
    assert listening.wanted(as_bot(b)) == "1057"
    listening.want(as_bot(b), "team requests", None)
    assert cabin.listens[-1] == "off"


def test_a_script_waiting_for_every_sound_or_none_still_works() -> None:
    """ "all" and "off" are whole lists, not ids to add: the run's 1023 with a wait for all of them is
    all of them, and with "off" only the run's own."""
    from conftest import as_bot
    from unclefarmer.engine import listening
    b = Rider(Cabin(Clock()))
    listening.want(as_bot(b), "out of range", "1023")
    assert listening.with_ours(as_bot(b), "all") == "all"
    assert listening.with_ours(as_bot(b), "off") == "1023"
    assert listening.with_ours(as_bot(b), "1020-1030") == "1020-1030"


def test_a_door_that_keeps_refusing_never_sets_off_the_ways_out() -> None:
    """Walks in the cabin are no place to be stuck at: the stuck count starts over after them, and
    while they last get_unstuck (another door back, the hearthstone) is never tried."""
    cabin = Cabin(Clock())
    b = Rider(cabin)
    seen: list[bool] = []
    b.stuck_in_a_row = 5           # type: ignore[attr-defined]
    real = Rider.walk_to

    def walk(cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        seen.append(bool(getattr(b, "_unsticking", False)))
        return real(b, cell, map_change, near)
    b.walk_to = walk               # type: ignore[method-assign]
    assert b.wait_ride(CAB) == TO
    assert seen and all(seen)
    assert b.stuck_in_a_row == 0 and getattr(b, "_unsticking", False) is False   # type: ignore[attr-defined]


def test_with_no_sound_the_door_is_tried_once_after_15_seconds() -> None:
    cabin = Cabin(Clock(), sounds_work=False)
    b = Rider(cabin)
    assert b.wait_ride(CAB) == TO
    door = [w for w in b.walks if w[1]]
    assert len(door) == 1 and door[0][2] >= ridedoor.DOOR_AFTER


def test_a_door_that_still_refuses_leaves_us_to_the_game() -> None:
    """With no sound and a slow car, the one try at 15 s is refused: the game puts us out as before."""
    cabin = Cabin(Clock(), sounds_work=False, arrives=18.0)
    b = Rider(cabin)
    assert b.wait_ride(CAB) == TO
    assert len([w for w in b.walks if w[1]]) == 1
    assert cabin.since() >= cabin.put_out


def test_a_walk_that_fails_gives_the_door_up_and_waits() -> None:
    cabin = Cabin(Clock())
    b = Rider(cabin)
    b.walk_error = Stopped("stuck near 8,4 and every way out failed - stopping")
    assert b.wait_ride(CAB) == TO
    assert len(b.walks) == 1 and cabin.since() >= cabin.put_out
    assert any("couldn't walk to the cable car's door" in line for line in b.lines)


def test_the_players_stop_is_never_swallowed() -> None:
    cabin = Cabin(Clock())
    b = Rider(cabin)
    b.stop_event = threading.Event()
    b.stop_event.set()
    b.walk_error = Stopped("stopped")
    with pytest.raises(Stopped):
        b.wait_ride(CAB)
    assert cabin.listens[-1] == "off"                                 # listening put back all the same


def test_a_flight_hall_is_waited_out_as_before() -> None:
    clock = Clock()
    cabin = Cabin(clock)
    cabin.map = 1069                                                  # an airship's hall: no door
    b = Rider(cabin)

    def land() -> None:
        if cabin.map == 1069 and cabin.since() >= 25:
            cabin.map = 1041
    b.cabin.tick = land               # type: ignore[method-assign]
    assert b.wait_ride(1069) == 1041
    assert b.walks == [] and cabin.listens == []


def test_heard_and_listen_survive_an_agent_without_sounds() -> None:
    cabin = Cabin(Clock(), sounds_work=False)
    b = Rider(cabin)
    from conftest import as_bot
    assert ridedoor.listen(as_bot(b)) is None
    assert ridedoor.heard(as_bot(b), 5) == (False, 5)
    ridedoor.done(as_bot(b))                                          # no error
