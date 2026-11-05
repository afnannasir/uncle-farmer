# pyright: reportPrivateUsage=false
# White-box tests: they read the beast module's helpers and the stand-in bot's fields directly.
"""Fusion beasts go on only after QUIET seconds with no attack on us or our pet, and a refusal
from the game is waited out (longer after each one in a row, said once) instead of being pressed
again and again. Getting off a Swift beast for a fight is never held back."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot, Clock, stand_in

from test_beasts import Bot as BeastBot, row
from unclefarmer.clock import CLOCK
from unclefarmer.engine import beasts


class Bot(BeastBot):
    """The beast tests' bot, with an attack record and a clock its sleeps move."""

    def __init__(self, rows: list[Any], clock: Clock) -> None:
        super().__init__(rows)
        self.clock = clock
        self.last_attacked = 0.0
        self.strikes: list[float] = []      # times something will strike us, as the clock passes them

    def attackers(self) -> set[int]:
        hit = [t for t in self.strikes if t <= self.clock.now]
        if hit:
            self.last_attacked = max(self.last_attacked, max(hit))
        return {-1} if hit and self.clock.now - max(hit) < 4 else set()

    def sleep(self, s: float) -> None:
        super().sleep(s)
        self.clock.now += s


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c)
    monkeypatch.setattr(beasts, "known", stand_in(beasts.known, lambda: {}))
    return c


def fierce(clock: Clock) -> Bot:
    b = Bot([row(1, "Swift", "swift", speed=5.0), row(2, "Bear", "fierce", level=50, hp=900)], clock)
    b.p["fight_beast"] = "auto"
    b._me = {"points": {"fusion": 100, "age_ms": 1}}
    return b


def active(b: Bot) -> list[str]:
    return [r["name"] for r in b.a.rows if r["active"]]


def test_no_beast_goes_on_within_six_seconds_of_an_attack(clock: Clock) -> None:
    b = fierce(clock)
    b.last_attacked = clock.now - 3.0
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == [] and b.keys == []
    assert sum("the fusion beast waits until 6 s without an attack" in line for line in b.lines) == 1
    beasts.ensure(as_bot(b), beasts.FIGHT)              # asked again: not said again, still nothing
    assert sum("waits until" in line for line in b.lines) == 1 and b.keys == []
    clock.now += 3.1                                     # 6.1 s since the attack
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == ["Bear"] and any("fighting as Bear" in line for line in b.lines)


def test_each_new_attack_starts_the_six_seconds_again(clock: Clock) -> None:
    b = fierce(clock)
    b.strikes = [clock.now - 5.0]
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == []
    clock.now += 2.0                                     # 7 s after that one, but struck again 1 s ago
    b.strikes.append(clock.now - 1.0)
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == []
    assert beasts.attacked_since(as_bot(b)) == pytest.approx(1.0)
    clock.now += 5.5
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == ["Bear"]


def test_travel_waits_out_an_attack_too(clock: Clock) -> None:
    b = fierce(clock)
    b.last_attacked = clock.now - 1.0
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert active(b) == []


def test_a_swift_beast_still_comes_off_for_a_fight_while_attacked(clock: Clock) -> None:
    b = fierce(clock)
    b.a.rows[0]["active"] = True
    b.last_attacked = clock.now - 0.5
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == []                               # off the Swift one, the Bear not yet on
    assert any("got off Swift for the fight" in line for line in b.lines)


def test_an_attack_during_the_servers_gap_stops_the_presses_and_is_no_refusal(clock: Clock) -> None:
    b = fierce(clock)
    b.last_beast_change = clock.now - 1.0                # ~5.4 s of the server's gap left to wait
    b.strikes = [clock.now + 2.0]                        # struck while waiting
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == [] and b.keys == []              # never pressed
    assert sum(b.sleeps) <= 2.6                          # gave up at the strike, not after the whole gap
    assert b.beasts_refused == {}                        # not the game's refusal: tried again once quiet
    assert any("waits until" in line for line in b.lines)


def test_refusals_back_off_and_are_said_once(clock: Clock) -> None:
    b = fierce(clock)
    b.a.accept = False
    waits: list[float] = []
    for _i in range(6):
        beasts.ensure(as_bot(b), beasts.FIGHT)
        waits.append(b.beasts_refused[2] - clock.now)
        clock.now = b.beasts_refused[2] + 0.1            # its wait is over: tried again
    assert waits == pytest.approx([60, 120, 240, 480, 600, 600])
    assert sum("wouldn't take Bear" in line for line in b.lines) == 1
    b.a.accept = True
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert active(b) == ["Bear"]
    assert any("Bear taken after 6 refusals" in line for line in b.lines)
    assert b.__dict__.get("beast_refusals") == {}        # the next refusal is said again, waits from 60


def test_a_refused_beast_is_not_pressed_again_until_its_wait_is_over(clock: Clock) -> None:
    b = fierce(clock)
    b.a.accept = False
    beasts.ensure(as_bot(b), beasts.FIGHT)
    presses = len(b.keys)
    for _i in range(20):                                 # the fight loop asks every look
        clock.now += 1.0
        beasts.ensure(as_bot(b), beasts.FIGHT)
    assert len(b.keys) == presses                        # nothing pressed while it waits
    assert presses <= beasts.TRIES


def test_the_walking_assistant_backs_off_from_its_shorter_wait(clock: Clock) -> None:
    b = Bot([row(1, "Swift", "swift", speed=5.0)], clock)
    b.walk_only = True
    b.a.accept = False
    got: list[float] = []
    for _i in range(4):
        beasts.ensure(as_bot(b), beasts.TRAVEL)
        got.append(b.beasts_refused[1] - clock.now)
        clock.now = b.beasts_refused[1] + 0.1
    assert got == pytest.approx([15, 30, 60, 60])


def test_attacked_since_counts_from_the_last_strike(clock: Clock) -> None:
    b = fierce(clock)
    assert beasts.attacked_since(as_bot(b)) >= beasts.QUIET            # never attacked
    b.strikes = [clock.now - 2.5]
    assert beasts.attacked_since(as_bot(b)) == pytest.approx(2.5)
