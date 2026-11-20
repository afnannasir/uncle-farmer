"""The no-kill watchdog (engine/watchdog.py): no kill for no_kill_minutes -> another part of the
map, twice; the third time the run ends. A kill starts everything again; a trip away or a long
pause starts the minutes again; never in a dungeon nor on the walking assistant; 0 is off."""

from __future__ import annotations

import contextlib
from collections.abc import Sequence
from typing import Any

import pytest
from conftest import as_bot, Clock, ME

from unclefarmer.engine import watchdog
from unclefarmer.engine.botparts.base import Stopped


class Farmer:
    def __init__(self, minutes: int = 10) -> None:
        self.p: dict[str, Any] = {"no_kill_minutes": minutes}
        self.clock = Clock()
        self.kills = 0
        self.in_instance = False
        self.walk_only = False
        self.lines: list[str] = []
        self.walked: list[tuple[float, float]] = []
        self.cells: list[tuple[int, int]] = [(10, 10), (300, 300), (50, 50)]
        self.finished = 0
        self.drawn = 0

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def snap(self) -> dict[str, Any]:
        return {"self": {"id": ME, "x": 20.0, "y": 20.0}, "entities": []}

    def random_cell(self, map_id: int, area: Sequence[float] | None, me: dict[str, Any], radius: int = 40,
                    **_kw: Any) -> tuple[int, int] | None:
        self.drawn += 1
        return self.cells[self.drawn % len(self.cells)] if self.cells else None

    def walk_to(self, cell: tuple[float, float]) -> str:
        self.walked.append(cell)
        return "arrived"

    def timed(self, phase: str) -> contextlib.nullcontext[None]:
        return contextlib.nullcontext()

    def finish(self) -> None:
        self.finished += 1


def run(b: Farmer, seconds: float, step: float = 30.0, map_id: int = 5361) -> list[bool]:
    """The farm loop going round every `step` seconds for `seconds`."""
    out: list[bool] = []
    end = b.clock.time() + seconds
    while b.clock.time() < end:
        b.clock.advance(step)
        out.append(watchdog.check(as_bot(b), map_id, None))
    return out


def test_ten_minutes_without_a_kill_tries_the_furthest_part_of_the_map() -> None:
    b = Farmer()
    watchdog.start(as_bot(b), 5361)
    assert not any(run(b, 9 * 60))
    assert run(b, 60) == [False, True]
    assert b.walked == [(300, 300)]                 # the furthest of the cells drawn
    assert b.lines == ["no kill for 10 minutes - trying another part of the map"]


def test_the_third_time_with_still_no_kill_the_run_ends() -> None:
    b = Farmer()
    watchdog.start(as_bot(b), 5361)
    run(b, 20 * 60)
    assert len(b.walked) == 2
    assert b.lines[-1] == "no kill for 20 minutes - trying another part of the map"
    with pytest.raises(Stopped):
        run(b, 10 * 60)
    assert b.finished == 1 and b.lines[-1] == "no kill for 30 minutes - stopping the run"


def test_a_kill_starts_everything_again() -> None:
    b = Farmer()
    watchdog.start(as_bot(b), 5361)
    run(b, 20 * 60)                                  # two warnings
    b.kills += 1
    run(b, 9 * 60)
    assert len(b.walked) == 2                       # the minutes start again from the kill
    run(b, 2 * 60)
    assert len(b.walked) == 3 and b.lines[-1] == "no kill for 10 minutes - trying another part of the map"
    assert b.finished == 0                          # the count of warnings started again too


def test_a_trip_away_or_a_long_pause_starts_the_minutes_again() -> None:
    b = Farmer()
    watchdog.start(as_bot(b), 5361)
    run(b, 8 * 60)
    b.clock.advance(watchdog.GAP + 1)               # a town trip inside one turn of the loop
    run(b, 8 * 60)
    assert b.walked == []
    run(b, 8 * 60, map_id=5362)                     # another map: the minutes start again
    assert b.walked == []


def test_off_in_a_dungeon_on_the_walking_assistant_or_at_zero() -> None:
    for setup in ("dungeon", "walk", "zero"):
        b = Farmer(0 if setup == "zero" else 10)
        b.in_instance = setup == "dungeon"
        b.walk_only = setup == "walk"
        watchdog.start(as_bot(b), 5361)
        assert not any(run(b, 60 * 60))
        assert b.walked == [] and b.finished == 0


def test_nowhere_to_walk_only_says_so() -> None:
    b = Farmer()
    b.cells = []
    watchdog.start(as_bot(b), 5361)
    assert not any(run(b, 10 * 60))
    assert b.lines == ["no kill for 10 minutes - trying another part of the map"]


def test_a_farm_of_only_some_names_never_stops_for_it() -> None:
    b = Farmer()
    watchdog.start(as_bot(b), 5361)
    for _i in range(5 * 20):
        b.clock.advance(30)
        watchdog.check(as_bot(b), 5361, None, hunting=True)
    assert len(b.walked) == 5 and b.finished == 0
    assert b.lines[-1] == "no kill for 50 minutes - trying another part of the map"
