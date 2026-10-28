# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Fusion beasts: the fastest Swift one for travel, the strongest Fierce one for a fight, the
server's ~6.4 s gap between changes, refusals, low fusion power and the walking assistant."""

from __future__ import annotations

import pytest
from conftest import as_bot, stand_in, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import settings
from unclefarmer.engine import beasts
from typing import Any


class Link:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.accept = True

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem beasts":
            return [dict(r) for r in self.rows]
        return {}


class Bot:
    def __init__(self, rows: list[Any]) -> None:
        self.a = Link(rows)
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.beasts_refused: dict[int, float] = {}
        self.lines: list[str] = []
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.cursor = None
        self.panel = None
        self.last_beast_change = 0.0
        self.last_combat = 0.0
        self.last_ride_check = 0.0
        self.last_points_key = 0.0
        self.said_low_power = False
        self.walk_only = False
        self._me: dict[str, Any] = {}

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def me(self) -> dict[str, Any]:
        return self._me

    def panel_cursor(self, panel: int, index: int, size: int) -> bool:
        self.cursor, self.panel = index, panel
        return 0 <= index < len(self.a.rows)

    def key(self, k: str, ms: int | None=None) -> None:
        self.keys.append(k)
        if self.a.accept and self.cursor is not None and 0 <= self.cursor < len(self.a.rows):
            r = self.a.rows[self.cursor]
            if r.get("active"):
                r["active"] = False
            else:
                for x in self.a.rows:
                    x["active"] = False
                r["active"] = True

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)


def row(id: int=1, name: str="Beast", kind: str="swift", active: bool=False, **kw: Any) -> dict[str, Any]:
    r: dict[str, Any] = {"id": id, "name": name, "kind": kind, "active": active, "usable_at": 0, "expires_at": 0}
    r.update(kw)
    return r


# ---------------------------------------------------------------- known / owned
def test_owned_merges_the_game_texts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(beasts, "known", stand_in(beasts.known, lambda: {"Nightmare Beast": {"kind": "swift", "speed": 5.8, "text": "x"}}))
    b = Bot([])
    b.a.rows = [{"id": 1, "name": "Nightmare Beast", "active": False}]
    got = beasts.owned(as_bot(b))
    assert got[0]["kind"] == "swift" and got[0]["speed"] == 5.8
    assert "text" not in got[0]                              # the long text is left out
    b.a.rows = [{"id": 2, "name": "Unknown", "active": False}]
    assert beasts.owned(as_bot(b))[0]["kind"] == "unknown"
    b.a.json = lambda line: None
    assert beasts.owned(as_bot(b)) == []


# ---------------------------------------------------------------- choose
def test_choose_travel_fastest_swift_breaks_ties_by_free() -> None:
    rows = [row(1, "A", "swift", speed=3.0), row(2, "B", "swift", speed=4.0, free=True),
            row(3, "C", "swift", speed=4.0, free=False), row(4, "D", "fierce", level=90)]
    fastest = beasts.choose(rows, beasts.TRAVEL, {})
    assert fastest is not None and fastest["id"] == 2
    assert beasts.choose([], beasts.TRAVEL, {}) is None


def test_choose_fight_strongest_fierce_by_level_then_hp() -> None:
    rows = [row(1, "A", "fierce", level=40, hp=5000), row(2, "B", "fierce", level=60, hp=3000),
            row(3, "C", "fierce", level=60, hp=9000), row(4, "D", "swift", speed=5)]
    strongest = beasts.choose(rows, beasts.FIGHT, {})
    assert strongest is not None and strongest["id"] == 3
    assert beasts.choose([row(1, "A", "swift")], beasts.FIGHT, {}) is None


def test_choose_works_without_a_refused_list() -> None:
    # its default used to be (), which has no .get: a call without the dict raised
    fast = row(1, "A", "swift", speed=3.0)
    assert beasts.choose([fast], beasts.TRAVEL) == fast


def test_choose_respects_refused_and_expired(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(1000.0)
    monkeypatch.setattr(CLOCK, "time", clock)
    now_ms = clock.now * 1000
    rows = [row(1, "A", "swift", speed=5, usable_at=now_ms + 1000),
            row(2, "B", "swift", speed=4, expires_at=now_ms - 1000),
            row(3, "C", "swift", speed=3)]
    chosen = beasts.choose(rows, beasts.TRAVEL, {})
    assert chosen is not None and chosen["id"] == 3
    assert beasts.choose(rows, beasts.TRAVEL, {3: clock.now + 10}) is None


# ---------------------------------------------------------------- usable / power
def test_usable_reads_both_clocks(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(1000.0)
    monkeypatch.setattr(CLOCK, "time", clock)
    now_ms = clock.now * 1000
    assert beasts.usable(row(1, "A", usable_at=-1), {}) is True     # negative means "out", still usable
    assert beasts.usable(row(1, "A", usable_at=now_ms + 1), {}) is False
    assert beasts.usable(row(1, "A", expires_at=now_ms - 1), {}) is False
    assert beasts.usable(row(1, "A"), {1: clock.now + 1}) is False


def test_power_from_points_then_sheet() -> None:
    assert beasts.power({"points": {"fusion": 55, "age_ms": 1000}}) == 55
    assert beasts.power({"points": {"fusion": 55, "age_ms": 999999},
                         "sheet": {"fusion": 42, "age": 50}}) == 42
    assert beasts.power({"points": {"fusion": -1, "age_ms": 1},
                         "sheet": {"fusion": 42, "age": 999}}) is None
    assert beasts.power({}) is None


# ---------------------------------------------------------------- ensure
def test_ensure_travel_auto_rides_the_fastest_swift(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    monkeypatch.setattr(beasts, "known", stand_in(beasts.known, lambda: {}))
    b = Bot([row(1, "Slow", "swift", speed=3.0), row(2, "Fast", "swift", speed=5.0)])
    b._me = {"points": {"fusion": 100, "age_ms": 1}}
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.a.rows[1]["active"] is True
    assert any("riding Fast" in line for line in b.lines)


def test_ensure_fight_auto_rides_the_strongest_fierce(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Weak", "fierce", level=10, hp=100), row(2, "Strong", "fierce", level=80, hp=9999)])
    b.p["fight_beast"] = "auto"
    b._me = {"points": {"fusion": 100, "age_ms": 1}}
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert b.a.rows[1]["active"] is True
    assert any("fighting as Strong" in line for line in b.lines)


def test_ensure_fight_gets_off_a_swift_beast(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0, active=True)])
    b.p["fight_beast"] = "none"
    beasts.ensure(as_bot(b), beasts.FIGHT)
    assert b.a.rows[0]["active"] is False
    assert any("got off Swift" in line for line in b.lines)


def test_ensure_travel_none_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.p["speed_beast"] = "none"
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.keys == []


def test_ensure_travel_waits_after_combat(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.last_combat = clock.now - 1                            # within AFTER_FIGHT
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.keys == []


def test_ensure_low_power_stays_on_foot(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0, free=False)])
    b._me = {"points": {"fusion": 5, "age_ms": 1}}
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.keys == []
    assert b.said_low_power is True
    assert any("on foot until it has come back" in line for line in b.lines)


def test_ensure_low_power_still_rides_a_free_beast(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Free", "swift", speed=5.0, free=True)])
    b._me = {"points": {"fusion": 5, "age_ms": 1}}
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.a.rows[0]["active"] is True


def test_ensure_power_recovered_clears_the_flag(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.said_low_power = True
    b._me = {"points": {"fusion": 80, "age_ms": 1}}
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.said_low_power is False


def test_ensure_already_riding_does_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0, active=True)])
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.keys == []


def test_ensure_by_name_and_unusable_name(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Nomad", "swift", speed=2.0), row(2, "Fast", "swift", speed=9.0)])
    b.p["speed_beast"] = "Nomad"
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.a.rows[0]["active"] is True
    # a named beast that is on cooldown does nothing
    b2 = Bot([row(1, "Nomad", "swift", usable_at=clock.now * 1000 + 5000)])
    b2.p["speed_beast"] = "Nomad"
    beasts.ensure(as_bot(b2), beasts.TRAVEL)
    assert b2.keys == []


def test_ensure_records_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.a.accept = False
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.beasts_refused[1] == clock.now + beasts.REFUSED_FOR
    assert any("wouldn't take Swift" in line for line in b.lines)


def test_ensure_walk_assistant_uses_the_shorter_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.a.accept = False
    b.walk_only = True
    beasts.ensure(as_bot(b), beasts.TRAVEL)
    assert b.beasts_refused[1] == clock.now + beasts.WALK_REFUSED_FOR


# ---------------------------------------------------------------- keep_riding / off
def test_keep_riding_reads_points_and_keeps_riding(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.p["points_key"] = "t"
    beasts.keep_riding(as_bot(b))
    assert "t" in b.keys
    assert b.a.rows[0]["active"] is True
    # a second call within RIDE_CHECK does nothing
    b.keys.clear()
    beasts.keep_riding(as_bot(b))
    assert b.keys == []


def test_keep_riding_points_key_every_power_every(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.p["points_key"] = "t"
    beasts.keep_riding(as_bot(b))
    clock.advance(beasts.RIDE_CHECK + 1)                     # still inside POWER_EVERY
    b.keys.clear()
    beasts.keep_riding(as_bot(b))
    assert "t" not in b.keys
    clock.advance(beasts.POWER_EVERY)
    beasts.keep_riding(as_bot(b))
    assert "t" in b.keys


def test_off_gets_off_the_active_beast(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0, active=True)])
    beasts.off(as_bot(b))
    assert b.a.rows[0]["active"] is False


def test_off_nothing_when_no_beast_is_active(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    beasts.off(as_bot(b))
    assert b.keys == []


# ---------------------------------------------------------------- _press
def test_press_waits_out_the_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.last_beast_change = clock.now                          # a change just happened
    rows = beasts.owned(as_bot(b))
    assert beasts._press(as_bot(b), rows, rows[0]) is True
    assert any(s == pytest.approx(beasts.GAP) for s in b.sleeps)
    assert b.last_beast_change == clock.now


def test_press_retries_and_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([row(1, "Swift", "swift", speed=5.0)])
    b.a.accept = False
    rows = beasts.owned(as_bot(b))
    assert beasts._press(as_bot(b), rows, rows[0]) is False
