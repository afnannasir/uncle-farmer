# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""HP and MP without the R key (agent 39): the bot asks the agent, which sends the own-HP key's
request itself. A request whose answer is still due waits a tick; an agent that can't send it
hands the job back to the key for the rest of the run."""

from pathlib import Path

from test_health import H, Link

from unclefarmer.engine import pets
from unclefarmer.engine.botparts import health

CONTRACT = Path(__file__).resolve().parents[1] / "agent" / "test" / "contract.txt"


class AskLink(Link):
    def __init__(self, replies=None):
        super().__init__()
        self.sent = []
        self.replies = list(replies or [])

    def send(self, line):
        self.sent.append(line)
        return self.replies.pop(0) if self.replies else "asked"


def asker(profile=None, replies=None):
    h = H(profile)
    h.a = AskLink(replies)
    h.agent_ask = True
    return h


def contract():
    out = {}
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            out[k] = v
    return out


def test_asks_without_a_key_every_few_seconds(clock):
    h = asker({"vitals_key": "r"})
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.a.sent == ["ask vitals"] and h.keys_pressed == []
    h.check_mp(clock.now + 1)
    assert h.a.sent == ["ask vitals"]                       # too soon for us
    h.check_mp(clock.now + health.ASK_EVERY)
    assert h.a.sent == ["ask vitals"] * 2


def test_asks_even_with_no_key_in_the_profile(clock):
    h = asker()
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.a.sent == ["ask vitals"] and h.keys_pressed == []


def test_asks_less_often_while_mp_is_high(clock):
    h = asker()
    h.mp_share = 95.0
    h.last_vitals_key = clock.now - health.ASK_EVERY
    h.check_mp(clock.now)
    assert h.a.sent == []
    h.check_mp(clock.now + health.ASK_QUIET)
    assert h.a.sent == ["ask vitals"]


def test_an_answer_still_due_waits_a_tick_without_the_key(clock):
    h = asker({"vitals_key": "r"}, replies=["error: too soon"])
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == [] and h.last_vitals_key == 0.0
    h.check_mp(clock.now)                                    # asked again on the next tick
    assert h.a.sent == ["ask vitals"] * 2


def test_an_agent_that_cant_ask_hands_it_back_to_the_key(clock):
    h = asker({"vitals_key": "r"}, replies=["error: the game's connection wasn't found"])
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == ["r"] and h.agent_ask is False
    assert any("with the key again" in line for line in h.lines)
    h.check_mp(clock.now + 30)
    assert h.a.sent == ["ask vitals"]                       # never asked again this run


def test_an_unknown_id_uses_the_key_this_time_only(clock):
    h = asker({"vitals_key": "r"}, replies=["error: our id isn't known yet"])
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == ["r"] and h.agent_ask is True


def test_the_agents_vitals_reach_the_mp_reading(clock):
    """contract.txt's vitals line is what AgentTests.java checks the agent writes."""
    v = __import__("json").loads(contract()["vitals"])
    h = asker()
    h.maxmp = 100000
    got = h.mp_now({"vitals": v}, clock.now)
    assert got == (82000, 100000)
    assert h.mp_seen == clock.now - 1.5                     # age_ms says when the answer came


def test_the_pet_reading_asks_too(clock):
    class B:
        p = {}
        agent_ask = True
        pet_vitals_at = 0.0

        def __init__(self):
            self.asked, self.keys = [], []

        def ask_vitals(self, what):
            self.asked.append(what)
            return True

        def key(self, k, ms=None):
            self.keys.append(k)

    b = B()
    pets._read(b, {}, clock.now)
    assert b.asked == ["petvitals"] and b.keys == [] and b.pet_vitals_at == clock.now
