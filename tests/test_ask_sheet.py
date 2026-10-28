# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The character sheet without the key (agent 43): the bot asks the agent, which sends the sheet
key's request itself and keeps the answer off the screen. So it is read every few seconds -
after every kill - without anyone waiting for it; a forced reading waits for the fresh sheet,
and an agent that can't send it hands the job back to the key for the rest of the run."""

from __future__ import annotations

from test_ask_vitals import AskLink, contract
from test_health import H

from unclefarmer.engine.botparts import health
from conftest import Clock
from typing import Any

FRESH = {"contribution": 1200, "xp_needed": 500, "drop_seconds": 7000, "level": 50, "age": 0,
         "load": 100, "maxload": 4000}
OLD = dict(FRESH, contribution=1000, age=6)


class SheetAsker(H):
    def __init__(self, profile: dict[str, Any] | None = None) -> None:
        super().__init__(profile)
        self.closed = 0

    def close_dialogs(self) -> None:
        self.closed += 1                      # the key opens a window, which the old way closes


def sheet_asker(profile: dict[str, Any] | None=None, replies: list[str] | None=None,
                sheet: dict[str, Any] | None=None) -> SheetAsker:
    h = SheetAsker(dict({"sheet_key": "`"}, **(profile or {})))
    h.link = AskLink(replies)
    h.a = h.link
    h.agent_sheet = True
    h._me = {"sheet": dict(sheet or OLD)}
    return h


def test_the_sheet_key_request_is_in_the_contract() -> None:
    assert contract()["sheet_packet"].split()[:6] == ["37", "13", "0", "0", "0", "1"]


def test_asks_without_the_key_and_waits_for_nothing(clock: Clock) -> None:
    h = sheet_asker()
    got = h.refresh_sheet()
    assert h.link.sent == ["ask sheet"]
    assert h.keys_pressed == [] and h.sleeps == []       # no key, no waiting
    assert h.closed == 0                                # and no window to close
    assert got == OLD                                   # the last reading; the new one lands later


def test_asks_again_after_every_few_seconds_not_sooner(clock: Clock) -> None:
    h = sheet_asker()
    h.refresh_sheet()
    clock.advance(health.SHEET_ASK_EVERY - 1)
    h.refresh_sheet()
    assert h.link.sent == ["ask sheet"]
    clock.advance(1)
    h.refresh_sheet()
    assert h.link.sent == ["ask sheet"] * 2


def test_ignores_the_ten_minute_key_spacing(clock: Clock) -> None:
    # sheet_every was for the key (it opens a window); the keyless request comes much more often
    h = sheet_asker({"sheet_every": 600})
    h.refresh_sheet()
    clock.advance(health.SHEET_ASK_EVERY)
    h.refresh_sheet()
    assert h.link.sent == ["ask sheet"] * 2 and h.keys_pressed == []


def test_a_forced_reading_waits_for_the_fresh_sheet(clock: Clock) -> None:
    h = sheet_asker()
    readings = [OLD, OLD, FRESH]
    h.me = lambda: {"sheet": dict(readings.pop(0) if len(readings) > 1 else readings[0])}
    h.sleep = lambda seconds: clock.advance(seconds)
    assert h.refresh_sheet(force=True) == FRESH
    assert h.keys_pressed == []


def test_a_forced_reading_gives_up_after_a_moment(clock: Clock) -> None:
    h = sheet_asker()
    h.sleep = lambda seconds: clock.advance(seconds)
    start = clock.now
    assert h.refresh_sheet(force=True) == OLD
    assert clock.now - start <= health.SHEET_WAIT + 0.2


def test_too_soon_answers_with_the_last_reading(clock: Clock) -> None:
    h = sheet_asker(replies=["error: too soon"])
    assert h.refresh_sheet() == OLD
    assert h.agent_sheet is True and h.keys_pressed == []


def test_an_agent_that_cant_send_it_hands_back_to_the_key(clock: Clock) -> None:
    h = sheet_asker(replies=["error: the game's connection wasn't found"])
    h.refresh_sheet(force=True)
    assert h.agent_sheet is False
    assert h.keys_pressed == ["`"]                     # read the old way, window and all
    assert any("read with the key again" in line for line in h.lines)
    h.link.sent.clear()
    clock.advance(health.SHEET_ASK_EVERY)
    h.refresh_sheet(force=True)
    assert "ask sheet" not in h.link.sent                 # the rest of the run uses the key


def test_an_unknown_id_is_only_a_moment(clock: Clock) -> None:
    h = sheet_asker(replies=["error: our id isn't known yet"])
    h.refresh_sheet(force=True)
    assert h.agent_sheet is True                       # asked again next time
    assert h.keys_pressed == ["`"]


def test_an_old_link_without_send_uses_the_key(clock: Clock) -> None:
    h = sheet_asker()

    class Broken(AskLink):
        def send(self, line: str) -> str:
            raise OSError("closed")

    h.link = Broken()
    h.a = h.link
    h.refresh_sheet(force=True)
    assert h.keys_pressed == ["`"]


def test_without_agent_43_the_key_is_used(clock: Clock) -> None:
    h = sheet_asker()
    h.agent_sheet = False
    h.refresh_sheet(force=True)
    assert h.link.sent == [] and h.keys_pressed == ["`"]
