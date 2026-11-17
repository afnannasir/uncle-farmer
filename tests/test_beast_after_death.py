# pyright: reportPrivateUsage=false
# White-box tests: they read the stand-in bot's fields directly.
"""Just after a death the server still counts us in combat and refuses a fusion beast without a
word; a refused press then costs the wait before the next one (live, XP loser: the beast came 10 s
after the respawn). So the beast waits until AFTER_DEATH seconds after the death, and goes on at
the first press."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot, Clock

from test_beast_quiet_and_refusals import Bot, clock, fierce
from test_beasts import row
from unclefarmer.engine import beasts

assert clock is not None      # the fixture, used by name
REFUSED_FOR = 6.0             # what the server did live: refused 4 s after the death, took it at 6


class Game(Bot):
    """A server that refuses every beast change for REFUSED_FOR seconds after our death."""

    def __init__(self, rows: list[Any], clock: Clock) -> None:
        super().__init__(rows, clock)
        self.last_death = 0.0
        self.presses: list[float] = []

    def key(self, k: str, ms: int | None = None) -> None:
        self.presses.append(self.clock.now)
        self.a.accept = self.clock.now >= getattr(self, "last_death", 0.0) + REFUSED_FOR
        super().key(k, ms)


def swift(clock: Clock) -> Game:
    g = Game([row(1, "Swift", "swift", speed=5.0)], clock)
    g.p["speed_beast"] = "auto"
    g._me = {"points": {"fusion": 100, "age_ms": 1}}
    return g


def riding(g: Game) -> bool:
    return any(r["active"] for r in g.a.rows)


def test_on_at_the_first_press_once_the_seconds_after_a_death_are_over(clock: Clock) -> None:
    g = swift(clock)
    g.last_death = clock.now - 4.0                      # respawned, 4 s after the death
    beasts.ensure(as_bot(g), beasts.TRAVEL)
    assert riding(g) and len(g.presses) == 1             # no refused press, so no GAP to wait out
    assert g.presses[0] >= g.last_death + beasts.AFTER_DEATH - 1e-9
    assert sum("refuses one this soon after a death" in line for line in g.lines) == 1


def test_said_once_a_run(clock: Clock) -> None:
    g = swift(clock)
    for _i in range(2):
        g.a.rows = [dict(r, active=False) for r in g.a.rows]
        clock.now += 60.0
        g.last_death = clock.now - 2.0
        beasts.ensure(as_bot(g), beasts.TRAVEL)
        assert riding(g)
    assert sum("soon after a death" in line for line in g.lines) == 1


@pytest.mark.parametrize("ago", [30.0, 0.0])
def test_no_wait_long_after_a_death_or_with_none(clock: Clock, ago: float) -> None:
    g = swift(clock)
    if ago:
        g.last_death = clock.now - ago
    else:
        del g.last_death                                 # a bot that never died has none
        g.presses = []
    start = clock.now
    beasts.ensure(as_bot(g), beasts.TRAVEL)
    assert riding(g)
    assert clock.now - start < 1.0
    assert not any("after a death" in line for line in g.lines)


def test_fighting_beasts_wait_too(clock: Clock) -> None:
    b = fierce(clock)
    b.last_death = clock.now - 1.0                       # type: ignore[attr-defined]
    start = clock.now
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert clock.now - start >= beasts.AFTER_DEATH - 1.0 - 1e-9
