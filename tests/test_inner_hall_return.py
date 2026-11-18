"""The end of a run that started in a gang base it can't walk back to: the way in is the Gang
Entrance, which only leads to the character's own gang (a one-way door shuts the inner hall)."""

from __future__ import annotations

import re
import threading
from typing import Any

import pytest

from conftest import Clock, as_bot, returning
from unclefarmer.engine import chores, crafting
from unclefarmer.engine.bot import Stopped

BASE = 4001
START = (41, 49)


class Game:
    def __init__(self) -> None:
        self.stop_event = threading.Event()
        self.lines: list[str] = []
        self.trips: list[tuple[int, tuple[int, int]]] = []
        self.mid = BASE
        self.name = "Our Gang encampment"
        self.closed = 0

    def map_id(self) -> int:
        return self.mid

    def snap(self) -> dict[str, Any]:
        return {"map": {"id": self.mid, "name": self.name}}

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def travel(self, mid: int, cell: tuple[int, int]) -> None:
        self.trips.append((mid, cell))

    def close_dialogs(self) -> None:
        self.closed += 1


@pytest.fixture
def outside(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """We stand where the start can't be walked to; the Entrance works unless a test says not."""
    entered: list[int] = []
    monkeypatch.setattr(chores, "_in_the_hall", returning(True))
    monkeypatch.setattr(crafting, "reachable_cell", returning(False))

    def enter(b: Any) -> None:
        entered.append(1)
    monkeypatch.setattr(chores, "inner_hall", enter)
    return entered


def back(g: Game) -> bool:
    return chores.back_to_gang_base(as_bot(g), BASE, START, "Our Gang encampment")


def test_it_goes_round_by_the_entrance_and_walks_to_the_start(outside: list[int]) -> None:
    g = Game()
    assert back(g) is True
    assert outside == [1] and g.trips == [(BASE, START)]


def test_the_entrance_refusing_leaves_it_where_it_is(monkeypatch: pytest.MonkeyPatch, outside: list[int]) -> None:
    def no_way(b: Any) -> None:
        raise Stopped("the Gang Entrance has no way into a gang's inner hall")
    monkeypatch.setattr(chores, "inner_hall", no_way)
    g = Game()
    assert back(g) is True
    assert g.trips == [] and any("staying here" in line for line in g.lines)


def test_a_stop_by_the_player_stays_a_stop(monkeypatch: pytest.MonkeyPatch, outside: list[int]) -> None:
    def stopped(b: Any) -> None:
        raise Stopped("stopped")
    monkeypatch.setattr(chores, "inner_hall", stopped)
    g = Game()
    g.stop_event.set()
    with pytest.raises(Stopped):
        back(g)


def test_another_gangs_hall_is_not_walked_in(outside: list[int]) -> None:
    g = Game()
    g.name = "Other Gang encampment"
    assert back(g) is True
    assert g.trips == [] and any("led to Other Gang encampment" in line for line in g.lines)


def test_a_start_we_can_still_walk_to_is_an_ordinary_trip(monkeypatch: pytest.MonkeyPatch, outside: list[int]) -> None:
    monkeypatch.setattr(crafting, "reachable_cell", returning(True))
    assert back(Game()) is False
    assert outside == []


def test_the_outer_hall_is_entered_by_its_own_way(monkeypatch: pytest.MonkeyPatch, outside: list[int]) -> None:
    monkeypatch.setattr(chores, "_in_the_hall", returning(False))
    monkeypatch.setattr(crafting, "enter_gang", returning(True))
    g = Game()
    assert back(g) is True
    assert outside == [] and g.trips == [(BASE, START)]


def test_an_entrance_that_wont_open_leaves_it_there(monkeypatch: pytest.MonkeyPatch, outside: list[int]) -> None:
    monkeypatch.setattr(chores, "_in_the_hall", returning(False))
    monkeypatch.setattr(crafting, "enter_gang", returning(False))
    g = Game()
    assert back(g) is True
    assert g.trips == [] and any("staying here" in line for line in g.lines)


def test_any_other_place_is_an_ordinary_trip(monkeypatch: pytest.MonkeyPatch, outside: list[int]) -> None:
    monkeypatch.setattr(crafting, "gang_map", returning(False))
    assert back(Game()) is False
    assert outside == []


def test_the_real_map_knows_the_hall_from_the_outer_hall() -> None:
    assert chores._in_the_hall(BASE, START)  # pyright: ignore[reportPrivateUsage]
    assert not chores._in_the_hall(BASE, (59, 93))  # pyright: ignore[reportPrivateUsage]
    assert not chores._in_the_hall(1081, START)  # pyright: ignore[reportPrivateUsage]


class Door(Game):
    """In the outer hall, next to the Inner hall Guard."""

    def __init__(self, opens: bool = True, offers: bool = True) -> None:
        super().__init__()
        self.opens, self.offers = opens, offers
        self.inside = False
        self.talked: list[str] = []
        self.chosen: list[str] = []
        self.visited: list[Any] = []
        self.clock = Clock()

    def talk(self, name: str, cell: Any = None) -> bool:
        self.talked.append(name)
        return True

    def wait_dialog(self, seconds: float = 3.0) -> dict[str, Any]:
        return {"open": 1}

    def choose(self, pattern: str) -> dict[str, Any] | None:
        self.chosen.append(pattern)
        if not self.offers:
            return None
        self.inside = self.opens
        return {"text": "Enter Inner hall"}

    def sleep(self, seconds: float) -> None:
        self.clock.sleep(seconds)


@pytest.fixture
def door(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """The hall can be walked to once the guard let us in; the Gang Entrance is noted when used."""
    entrance: list[Any] = []

    def reachable(b: Any, cell: tuple[int, int]) -> bool:
        return cell == chores.HALL_GUARD[1] or b.inside

    def visit(b: Any, role: Any, candidates: Any = None) -> None:
        entrance.append(role)
        raise Stopped("no such entrance here")
    monkeypatch.setattr(crafting, "in_gang", returning(True))
    monkeypatch.setattr(crafting, "reachable_cell", reachable)
    monkeypatch.setattr(chores.town, "visit", visit)
    return entrance


def test_the_guard_lets_us_in_from_the_outer_hall(door: list[Any]) -> None:
    g = Door()
    chores.inner_hall(as_bot(g))
    assert g.inside and g.talked == ["Inner hall Guard"] and door == []
    assert re.search(g.chosen[0], "Enter Inner hall", re.I)


@pytest.mark.parametrize("opens, offers", [(False, True), (True, False)])
def test_a_guard_that_doesnt_let_us_in_leaves_the_gang_entrance(door: list[Any], opens: bool, offers: bool) -> None:
    g = Door(opens, offers)
    with pytest.raises(Stopped):
        chores.inner_hall(as_bot(g))
    assert door == ["gang_entrance"]


def test_the_real_map_reaches_the_guard_from_the_outer_hall_only() -> None:
    assert crafting.walks_between(BASE, (59, 93), chores.HALL_GUARD[1])
    assert crafting.walks_between(BASE, START, chores.HALL_GUARD[1])       # out through the one-way door
    assert not crafting.walks_between(BASE, chores.HALL_GUARD[1], START)
