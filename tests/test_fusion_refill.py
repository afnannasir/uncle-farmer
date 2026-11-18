"""Fusion power, measured 2026-11-18: a beast that is on costs 3 a minute, moving or not; off, it
comes back at 6 a minute. Put back on as soon as it was over MIN_POWER, it was off again within two
minutes, a change every two and a half. Once it has run low it now comes back to REFILL_POWER first."""

from __future__ import annotations

import pytest
from conftest import Clock, as_bot

from test_beasts import Bot, row
from unclefarmer.clock import CLOCK
from unclefarmer.engine import beasts


@pytest.fixture(autouse=True)
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c)
    return c


def at(b: Bot, power: float) -> None:
    b._me = {"points": {"fusion": power, "age_ms": 1}}      # pyright: ignore[reportPrivateUsage]
    beasts.ensure(as_bot(b), beasts.TRAVEL)


def test_after_running_low_it_waits_for_the_refill() -> None:
    b = Bot([row(1, "Swift", "swift", speed=5.0, free=False)])
    at(b, 12)
    for power in (15, 30, 49):                       # over MIN_POWER, but not back yet
        at(b, power)
        assert b.a.rows[0]["active"] is False and b.keys == []
    said = [line for line in b.lines if "on foot until it is back to 50" in line]
    assert len(said) == 1 and "fusion power 12" in said[0]
    at(b, 50)
    assert b.a.rows[0]["active"] is True


def test_a_beast_that_never_ran_low_rides_under_the_refill_mark() -> None:
    b = Bot([row(1, "Swift", "swift", speed=5.0, free=False)])
    at(b, 30)
    assert b.a.rows[0]["active"] is True


def test_a_free_beast_rides_while_the_power_comes_back() -> None:
    b = Bot([row(1, "Free", "swift", speed=5.0, free=True)])
    at(b, 12)
    at(b, 30)
    assert b.a.rows[0]["active"] is True


def test_the_next_low_says_so_again() -> None:
    b = Bot([row(1, "Swift", "swift", speed=5.0, free=False)])
    at(b, 12)
    at(b, 60)
    b.a.rows[0]["active"] = False
    at(b, 9)
    assert sum("on foot until it is back to 50" in line for line in b.lines) == 2
