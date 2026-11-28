"""Never out of a dungeon by the bot's own doing: a trip that starts in one farms it until the game moves
us out (its timer, its end chest, the time it allows), then goes on; a wedding hall stops the run
where it is (leaving it loses the wedding's gift); two hours inside and the run stops there, still
inside. The walking assistant, following and coming to a player who called leave as before."""

from __future__ import annotations

from typing import Any, Callable

import pytest
from conftest import as_bot, Clock

from unclefarmer.engine import dungeonhold, triggers
from unclefarmer.engine.botparts.base import Stopped
from unclefarmer.engine.script import parse

NETHER = 1831          # a "dungeon" in data/map_kinds.json
BONE = 1792            # White Bone Cave: an "instance" the module knows is a dungeon
WORMHOLE = 1603        # a "dungeon?"
CHURCH = 4401          # a wedding hall
SECRET = 206           # a secret room (left as before)
TULIP = 1081


class Inside:
    """A character in a dungeon: farm() runs until its until() says so, a step of 30 s at a time;
    the game moves it out at out_at (None: never)."""

    def __init__(self, mid: int, out_at: float | None = None, land: int = TULIP) -> None:
        self.clock = Clock()
        self.where, self.out_at, self.land = mid, out_at, land
        self.lines: list[tuple[str, bool]] = []
        self.farms: list[tuple[int, bool]] = []
        self.chest = False
        self.leaves = 0
        self.leave_works = True
        self.walk_only = False
        self.following: str | None = None
        self.coming = False

    def map_id(self) -> int:
        return self.where

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append((line, speak))

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def snap(self) -> dict[str, Any]:
        rows: list[dict[str, Any]] = [{"name": "Treasure chest belongs to Hero"}] if self.chest else []
        return {"self": {"name": "Hero"}, "entities": rows}

    def farm(self, mid: int, until: Callable[[], bool] | None = None, instance: bool = False, **_k: Any) -> None:
        self.farms.append((mid, instance))
        assert until is not None
        while not until():
            self.clock.advance(30)
            if self.out_at is not None and self.clock.time() >= self.out_at:
                self.where = self.land

    def leave_instance(self) -> bool:
        self.leaves += 1
        if self.leave_works:
            self.where = self.land
        return self.leave_works


# ------------------------------------------------------------------ which maps
def test_the_maps_kept_are_the_dungeons_and_the_wedding_halls() -> None:
    assert dungeonhold.kept(NETHER) == "dungeon"
    assert dungeonhold.kept(BONE) == "dungeon"
    assert dungeonhold.kept(WORMHOLE) == "dungeon"
    assert dungeonhold.kept(1568) == "dungeon"          # Beast Mountain, recognised by a player
    assert dungeonhold.kept(1538) is None                # thousand miles of rivers: a map, not a dungeon
    assert dungeonhold.kept(CHURCH) == "wedding"
    assert dungeonhold.kept(SECRET) is None              # a secret room: left as before
    assert dungeonhold.kept(4201) is None                # the gang's mine valley: gathering leaves it
    assert dungeonhold.kept(1002) is None                # an "instance" nobody has said is a dungeon
    assert dungeonhold.kept(TULIP) is None
    assert dungeonhold.kept(999999) is None              # a map not in the table


def test_the_walking_assistant_following_and_coming_still_leave() -> None:
    b = Inside(NETHER)
    assert dungeonhold.applies(as_bot(b))
    for flag, value in (("walk_only", True), ("following", "Bob"), ("coming", True)):
        b = Inside(NETHER)
        setattr(b, flag, value)
        assert not dungeonhold.applies(as_bot(b)), flag


# ------------------------------------------------------------------ the hold
def test_a_dungeon_is_farmed_until_the_game_moves_us_out() -> None:
    b = Inside(NETHER, out_at=1000 + 900)
    dungeonhold.hold(as_bot(b), NETHER, TULIP)
    assert b.where == TULIP and b.leaves == 0               # never left by ourselves
    assert b.farms == [(NETHER, True)]                     # farmed as a dungeon: no trip out
    said, speak = b.lines[0]
    assert speak and "not leaving the dungeon by ourselves" in said and "on to Tuneful Wetland" in said


def test_the_trip_s_own_state_is_put_back_after_the_farm_inside() -> None:
    """The farm inside a trip clears what a farm sets for itself; the trip (perhaps the way to another
    farm) gets it back as it was, and the farm inside isn't walked as a trip."""
    b = Inside(NETHER, out_at=1000 + 300)
    b.traveling, b.on_farm_run, b.farm_filter = True, True, ("area", "only", "", [])  # type: ignore[attr-defined]
    seen: list[bool] = []
    real = b.farm

    def farm(mid: int, until: Callable[[], bool] | None = None, instance: bool = False, **k: Any) -> None:
        seen.append(b.traveling)  # type: ignore[attr-defined]
        b.on_farm_run, b.farm_filter = False, None  # type: ignore[attr-defined]
        real(mid, until=until, instance=instance)
    b.farm = farm  # type: ignore[method-assign]
    dungeonhold.hold(as_bot(b), NETHER, TULIP)
    assert seen == [False]
    assert b.traveling and b.on_farm_run and b.farm_filter == ("area", "only", "", [])  # type: ignore[attr-defined]


def test_our_end_chest_takes_us_out_when_the_dungeon_is_done() -> None:
    b = Inside(NETHER)
    b.chest = True
    dungeonhold.hold(as_bot(b), NETHER, TULIP)
    assert b.leaves == 1 and b.where == TULIP


def test_an_end_chest_that_doesn_t_work_is_tried_a_few_times_then_farmed_past() -> None:
    b = Inside(NETHER, out_at=1000 + 600)
    b.chest, b.leave_works = True, False
    dungeonhold.hold(as_bot(b), NETHER, TULIP)
    assert b.leaves == dungeonhold.CHEST_TRIES and b.where == TULIP


def test_two_hours_inside_stops_the_run_there_without_leaving() -> None:
    b = Inside(NETHER, out_at=None)
    with pytest.raises(Stopped) as e:
        dungeonhold.hold(as_bot(b), NETHER, TULIP)
    assert getattr(e.value, "stay", False)                  # no end place, no closing the game
    assert "still in" in str(e.value) and "2 hours" in str(e.value)
    assert b.leaves == 0 and b.where == NETHER
    assert b.clock.time() - 1000 >= dungeonhold.HOLD_MOST


def test_a_farm_that_gives_back_early_is_taken_up_again() -> None:
    """farm() can end on its own (nothing left to fight): the wait goes on, it isn't the way out."""
    b = Inside(NETHER, out_at=1000 + 300)
    calls = {"n": 0}
    real = b.farm

    def short_farm(mid: int, until: Callable[[], bool] | None = None, instance: bool = False, **k: Any) -> None:
        calls["n"] += 1
        if calls["n"] < 3:
            b.farms.append((mid, instance))
            return                                         # gave back at once
        real(mid, until=until, instance=instance)
    b.farm = short_farm  # type: ignore[method-assign]
    dungeonhold.hold(as_bot(b), NETHER, TULIP)
    assert calls["n"] == 3 and b.where == TULIP and b.leaves == 0


def test_a_wedding_hall_stops_the_run_where_it_is() -> None:
    b = Inside(CHURCH)
    with pytest.raises(Stopped) as e:
        dungeonhold.hold(as_bot(b), CHURCH, TULIP)
    assert getattr(e.value, "stay", False) and "gift" in str(e.value)
    assert b.farms == [] and b.leaves == 0


# ------------------------------------------------------------------ the travel loop
class Trip(Inside):
    """The travel mixin's loop around the hold: _plan_legs is never reached while we are kept."""

    def __init__(self, mid: int, out_at: float | None) -> None:
        super().__init__(mid, out_at)
        self.planned: list[int] = []
        self.p: dict[str, Any] = {}
        self.walk_left = self.walk_target = None

    def restock_on_the_way(self) -> None:
        pass

    def _plan_legs(self, mid: int, dst: int, cell: Any, avoid: set[Any]) -> list[dict[str, Any]]:
        self.planned.append(mid)
        self.where = dst
        return []


def no_ride(_mid: int) -> bool:
    return False


def test_a_trip_from_a_dungeon_waits_for_the_game_then_plans_from_outside(monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.engine.botparts import travel as travel_part
    b = Trip(NETHER, out_at=1000 + 600)
    monkeypatch.setattr(travel_part.atlas, "is_ride", no_ride)
    travel_part.TravelMixin._travel(as_bot(b), 5121, None, set())   # type: ignore[arg-type]
    assert b.planned == [TULIP]                 # planned only once outside, from where the game put us
    assert b.leaves == 0 and b.where == 5121


def test_a_trip_from_a_secret_room_plans_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.engine.botparts import travel as travel_part
    b = Trip(SECRET, out_at=None)
    monkeypatch.setattr(travel_part.atlas, "is_ride", no_ride)
    travel_part.TravelMixin._travel(as_bot(b), 5121, None, set())   # type: ignore[arg-type]
    assert b.planned == [SECRET] and b.farms == []


# ------------------------------------------------------------------ the drop time
def test_only_a_farm_line_spends_drop_time() -> None:
    for text, public in (("farm map=5121", True), ("dungeon", False), ("gather", False), ("gem_maker", False),
                         ("if kills > 1\n  farm map=5121\nend", True)):
        main, subs, _stop = parse(text)
        assert triggers.farms_public(main, subs) is public, text
    assert not triggers.step_farms(as_bot(Inside(TULIP)), "dungeon")


# ------------------------------------------------------------------ the end of a run
def off(_b: Any) -> None:
    pass


def test_the_end_of_a_run_in_a_dungeon_waits_for_the_game_before_its_end_place(monkeypatch: pytest.MonkeyPatch) -> None:
    """The end place's way (and its hearthstone) only once the game has moved us out."""
    from test_farming import Farm, labels
    from unclefarmer.engine.botparts import farming
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", off)
    b = Farm()
    b.map = NETHER
    b.start_place = (NETHER, 10, 20)
    b.sn = {"self": {"id": 1, "x": 0, "y": 0}}
    held: list[tuple[int, int | None]] = []

    def hold(bot: Any, mid: int, dst: int | None) -> None:
        held.append((mid, dst))
        b.map = TULIP                     # the game moved us out
    monkeypatch.setattr(dungeonhold, "hold", hold)
    b.finish()
    assert held == [(NETHER, None)]
    # the start is inside the dungeon: no way back in - the nearest map without monsters, from outside
    assert b.travels and all(dst != NETHER for dst, _cell in b.travels)


def test_a_stop_while_waiting_at_the_end_is_passed_on(monkeypatch: pytest.MonkeyPatch) -> None:
    """The wedding hall, or two hours inside: the run waits there for its player - the end's own
    "couldn't get there, the nearest map without monsters" never runs."""
    from test_farming import Farm, labels
    from unclefarmer.engine.botparts import farming
    labels(monkeypatch)
    monkeypatch.setattr(farming.beasts, "off", off)
    b = Farm()
    b.map = CHURCH
    b.start_place = (TULIP, 10, 20)
    b.sn = {"self": {"id": 1, "x": 0, "y": 0}}
    b.leave_monsters = lambda: b.leave_calls.append(True)
    with pytest.raises(Stopped) as e:
        b.finish()
    assert getattr(e.value, "stay", False) and b.held_inside  # type: ignore[attr-defined]
    assert b.travels == [] and b.leave_calls == []


def test_a_farm_on_the_dungeon_we_stand_in_restocks_nothing_and_is_a_dungeon_s(monkeypatch: pytest.MonkeyPatch) -> None:
    from test_farming import Farm
    from unclefarmer.engine.botparts import farming
    b = Farm()
    b.map = NETHER
    b.restocked_once = False
    restocks: list[bool] = []
    b.restock_now = lambda start=False: restocks.append(True) or False  # type: ignore[method-assign]
    seen: list[bool] = []

    def loop(_self: Any, *_a: Any, **_k: Any) -> None:
        seen.append(b.in_instance)
    monkeypatch.setattr(farming.FarmingMixin, "_farm_loop", loop)
    b.farm(NETHER, until=lambda: True)
    assert seen == [True] and restocks == [] and b.travels == []
