# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""HP and MP without the R key (agent 39): the bot asks the agent, which sends the own-HP key's
request itself. A request whose answer is still due waits a tick; an agent that can't send it
hands the job back to the key for the rest of the run."""

from __future__ import annotations

from pathlib import Path

from test_health import H, Link

from unclefarmer.engine import pets
from unclefarmer.engine.botparts import health
from conftest import as_bot, Clock
from typing import Any

CONTRACT = Path(__file__).resolve().parents[1] / "agent" / "test" / "contract.txt"


class AskLink(Link):
    def __init__(self, replies: list[str] | None=None) -> None:
        super().__init__()
        self.sent: list[str] = []
        self.replies = list(replies or [])

    def send(self, line: str) -> str:
        self.sent.append(line)
        return self.replies.pop(0) if self.replies else "asked"


def asker(profile: dict[str, Any] | None=None, replies: list[str] | None=None) -> H:
    h = H(profile)
    h.link = AskLink(replies)
    h.a = h.link
    h.agent_ask = True
    return h


def contract() -> dict[str, str]:
    out: dict[str, str] = {}
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_asks_without_a_key_every_few_seconds(clock: Clock) -> None:
    h = asker({"vitals_key": "r"})
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.link.sent == ["ask vitals"] and h.keys_pressed == []
    h.check_mp(clock.now + 1)
    assert h.link.sent == ["ask vitals"]                       # too soon for us
    h.check_mp(clock.now + health.ASK_EVERY)
    assert h.link.sent == ["ask vitals"] * 2


def test_asks_even_with_no_key_in_the_profile(clock: Clock) -> None:
    h = asker()
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.link.sent == ["ask vitals"] and h.keys_pressed == []


def test_asks_less_often_while_mp_is_high(clock: Clock) -> None:
    h = asker()
    h.mp_share = 95.0
    h.last_vitals_key = clock.now - health.ASK_EVERY
    h.check_mp(clock.now)
    assert h.link.sent == []
    h.check_mp(clock.now + health.ASK_QUIET)
    assert h.link.sent == ["ask vitals"]


def test_an_answer_still_due_waits_a_tick_without_the_key(clock: Clock) -> None:
    h = asker({"vitals_key": "r"}, replies=["error: too soon"])
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == [] and h.last_vitals_key == 0.0
    h.check_mp(clock.now)                                    # asked again on the next tick
    assert h.link.sent == ["ask vitals"] * 2


def test_an_agent_that_cant_ask_hands_it_back_to_the_key(clock: Clock) -> None:
    h = asker({"vitals_key": "r"}, replies=["error: the game's connection wasn't found"])
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == ["r"] and h.agent_ask is False
    assert any("with the key again" in line for line in h.lines)
    h.check_mp(clock.now + 30)
    assert h.link.sent == ["ask vitals"]                       # never asked again this run


def test_an_unknown_id_uses_the_key_this_time_only(clock: Clock) -> None:
    h = asker({"vitals_key": "r"}, replies=["error: our id isn't known yet"])
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == ["r"] and h.agent_ask is True


def test_the_agents_vitals_reach_the_mp_reading(clock: Clock) -> None:
    """contract.txt's vitals line is what AgentTests.java checks the agent writes."""
    v = __import__("json").loads(contract()["vitals"])
    h = asker()
    h.maxmp = 100000
    got = h.mp_now({"vitals": v}, clock.now)
    assert got == (82000, 100000)
    assert h.mp_seen == clock.now - 1.5                     # age_ms says when the answer came


def test_the_pet_reading_asks_too(clock: Clock) -> None:
    class B:
        p = {}
        agent_ask = True
        pet_vitals_at = 0.0

        def __init__(self) -> None:
            self.asked: list[str] = []
            self.keys: list[str] = []

        def ask_vitals(self, what: str) -> bool | None:
            self.asked.append(what)
            return True

        def key(self, k: str, ms: int | None=None) -> None:
            self.keys.append(k)

    b = B()
    pets._read(as_bot(b), {}, clock.now)
    assert b.asked == ["petvitals"] and b.keys == [] and b.pet_vitals_at == clock.now
