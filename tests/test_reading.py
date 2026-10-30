# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Reading the game: waiting, the stop flag, transient agent errors, read helpers, map settling,
private-message requests, phase timing and the UI read.

The bot here is a stand-in that inherits only ReadingMixin; the agent link answers the lines the
reader asks for, time is a clock the tests move by hand, and every other part of the Bot is a
recorder.
"""

from __future__ import annotations

import queue
import threading

import pytest

from unclefarmer.clock import CLOCK
from conftest import Clock, as_kind

from unclefarmer.engine.botparts.base import Stopped
from unclefarmer.engine.botparts.reading import ReadingMixin
from unclefarmer.stats import Tally
from collections.abc import Callable
from typing import Any, NoReturn

from unclefarmer import jsonio
from unclefarmer.engine.bot import Bot


class Seq:
    """A callable answer: each call gives the next item, the last one repeating forever."""

    def __init__(self, *items: object) -> None:
        self.items = list(items)

    def __call__(self) -> object:
        if len(self.items) > 1:
            return self.items.pop(0)
        return self.items[0]


class Link:
    """The agent: json() answers stored lines, send() records and answers stored lines."""

    def __init__(self) -> None:
        self.answers: dict[str, Any] = {}
        self.send_answers: dict[str, str | list[str] | Callable[[], object]] = {}
        self.sent: list[str] = []

    def json(self, line: str) -> Any:
        a = self.answers.get(line, {})
        return a() if callable(a) else a

    def send(self, line: str) -> str:
        self.sent.append(line)
        a = self.send_answers.get(line, "ok")
        if callable(a):
            return str(a())
        if isinstance(a, list):
            return a.pop(0) if a else "ok"
        return str(a)


class FakeEvent:
    """A stop flag whose wait() moves the test clock instead of really waiting."""

    def __init__(self, clock: Clock | None=None) -> None:
        self.clock = clock
        self.flag = False
        self.waited: list[float] = []

    def is_set(self) -> bool:
        return self.flag

    def set(self) -> None:
        self.flag = True

    def clear(self):
        self.flag = False

    def wait(self, timeout: float | None=None) -> bool:
        if self.flag:
            return True
        if timeout is not None:
            self.waited.append(timeout)
            if self.clock:
                self.clock.advance(timeout)
        return False


class OnceSet:
    """is_set() is True on the first call only: a pause that clears as soon as it is noticed."""

    def __init__(self) -> None:
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls == 1

    def set(self) -> None:
        pass

    def clear(self) -> None:
        pass


class Pause:
    """A pause the test sets and clears itself."""

    def __init__(self) -> None:
        self.flag = False

    def is_set(self) -> bool:
        return self.flag

    def set(self) -> None:
        self.flag = True

    def clear(self) -> None:
        self.flag = False


class StopMidPause:
    """A stop flag that only trips once check() is inside the pause loop."""

    def __init__(self) -> None:
        self.calls = 0

    def is_set(self) -> bool:
        self.calls += 1
        return self.calls > 1

    def wait(self, timeout: float | None=None) -> bool:
        return True

    def set(self) -> None:
        pass


class Read(ReadingMixin):
    """Only the reading mixin is real; every other part of the Bot is recorded or faked."""

    def __init__(self) -> None:
        self.link = Link()           # the tests read back what it recorded
        self.a = self.link
        self.stop_event = threading.Event()
        self.paused = Pause()
        self.daily_stop_at = 0.0
        self.daily_reset = "00:00"
        self.daily_done = False
        self.held = set()
        self.guarding = False
        self.lines: list[str] = []
        self.spoken: list[str] = []
        self.sleeps: list[float] = []
        self.held_calls: list[str] = []
        self.let_go_calls = 0
        self.resumed_at = 0.0
        self._speech_at = 5.0
        self.last_me = None
        self.started = 0.0
        self.doing_request = False
        self.requests = queue.Queue()
        self.timing = None
        self._phases = []
        self.kills = 0

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def let_go(self, k: str | None = None) -> None:
        self.let_go_calls += 1

    def hold(self, k: str) -> None:
        self.held_calls.append(k)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    monkeypatch.setattr(CLOCK, "sleep", c.sleep)
    monkeypatch.setattr(CLOCK, "wait", c.wait)
    return c


def ticking(bot: Read, clock: Clock) -> None:
    """Make self.sleep() move the clock (and record the waits) instead of really waiting."""
    def sleep(seconds: float):
        bot.sleeps.append(seconds)
        clock.advance(seconds)
    bot.sleep = sleep


# ==================================================================== check
def test_check_raises_when_stopped_by_user() -> None:
    b = Read()
    b.stop_event.set()
    with pytest.raises(Stopped) as e:
        b.check()
    assert e.value.final is True
    assert "stopped by you" in str(e.value)


def test_check_stop_flag_beats_the_daily_stop(clock: Clock) -> None:
    b = Read()
    b.stop_event.set()
    b.daily_stop_at = clock.now - 1
    with pytest.raises(Stopped) as e:
        b.check()
    assert "stopped by you" in str(e.value)
    assert b.daily_done is False


def test_check_daily_stop_fires_once(clock: Clock) -> None:
    b = Read()
    b.daily_stop_at = clock.now - 1
    b.daily_reset = "04:00"
    with pytest.raises(Stopped) as e:
        b.check()
    assert e.value.final is False
    assert "the day's farming is over" in str(e.value)
    assert "04:00" in str(e.value)
    assert b.daily_done is True
    assert b.check() is None                    # once, then the ending runs


def test_check_daily_stop_before_the_time_does_nothing(clock: Clock) -> None:
    b = Read()
    b.daily_stop_at = clock.now + 100
    assert b.check() is None
    assert b.daily_done is False


def test_daily_stop_can_cross_midnight_on_the_injected_clock(clock: Clock) -> None:
    b = Read()
    clock.now = 86_399.0                       # 23:59:59, one second before midnight
    b.daily_stop_at = clock.now + 1
    b.daily_reset = "00:00"
    with pytest.raises(Stopped) as e:
        ReadingMixin.sleep(b, 2.0)
    assert clock.now >= 86_400.0
    assert b.daily_done is True
    assert "00:00" in str(e.value)
    b.daily_done = False                       # the next day's run is armed again
    b.daily_stop_at = clock.now + 86_400.0
    assert b.check() is None


def test_check_daily_stop_already_done_does_nothing(clock: Clock) -> None:
    b = Read()
    b.daily_stop_at = clock.now - 1
    b.daily_done = True
    assert b.check() is None


def test_check_pause_lets_go_and_restores_everything(clock: Clock) -> None:
    b = Read()
    b.guarding = True
    b.held = {"d", "s"}
    b.paused = OnceSet()
    b.link.send_answers["guardmods off"] = "off"
    b.link.send_answers["guardmods on"] = "on"
    b.link.send_answers["speech"] = ["off", "on"]
    b.link.send_answers["speech on"] = "on"
    b.check()
    assert b.link.sent == ["guardmods off", "speech", "speech on", "guardmods on", "speech", "speech off"]
    assert b.let_go_calls == 1
    assert sorted(b.held_calls) == ["d", "s"]
    assert b.resumed_at == clock.now
    assert b._speech_at == 0.0
    assert b.lines == ["paused", "going on"]


def test_check_pause_without_the_guard_leaves_guardmods_alone(clock: Clock) -> None:
    b = Read()
    b.guarding = False
    b.paused = OnceSet()
    b.link.send_answers["speech"] = "on"
    b.check()
    assert b.link.sent == ["speech"]
    assert not any(line.startswith("guardmods") for line in b.link.sent)


def test_check_pause_leaves_a_talking_game_talking(clock: Clock) -> None:
    b = Read()
    b.paused = OnceSet()
    b.link.send_answers["speech"] = "on"
    b.check()
    assert b.link.sent == ["speech"]
    assert "speech off" not in b.link.sent and "speech on" not in b.link.sent


def test_check_pause_player_muting_it_during_the_pause_stays_muted(clock: Clock) -> None:
    b = Read()
    b.paused = OnceSet()
    b.link.send_answers["speech"] = ["off", "off"]
    b.link.send_answers["speech on"] = "on"
    b.check()
    assert b.link.sent == ["speech", "speech on", "speech"]
    assert "speech off" not in b.link.sent


def test_check_pause_stopped_midway_raises(clock: Clock) -> None:
    b = Read()
    b.paused.set()
    b.stop_event = StopMidPause()
    with pytest.raises(Stopped) as e:
        b.check()
    assert e.value.final is True
    assert "stopped by you" in str(e.value)
    assert b._speech_at == 0.0


# ==================================================================== wait_keys_free
def test_wait_keys_free_returns_when_no_box_is_open() -> None:
    b = Read()
    b.link.answers["input"] = {"ok": True, "open": 0}
    b.wait_keys_free()
    assert b.sleeps == [] and b.link.sent == []


def test_wait_keys_free_returns_when_the_agent_says_nothing() -> None:
    b = Read()
    b.link.answers["input"] = {"ok": False}
    b.wait_keys_free()
    assert b.link.sent == []


def test_wait_keys_free_ignores_our_own_number_box() -> None:
    b = Read()
    b.link.answers["input"] = {"ok": True, "open": 1, "mode": 3}
    b.wait_keys_free()
    assert b.link.sent == []


def test_wait_keys_free_escapes_a_few_times_then_warns() -> None:
    b = Read()
    b.link.answers["input"] = Seq({"ok": True, "open": 1}, {"ok": True, "open": 1}, {"ok": True, "open": 1},
                               {"ok": True, "open": 1}, {"ok": True, "open": 1}, {"ok": True, "open": 0})
    b.link.send_answers["typeesc"] = "escaped"
    b.wait_keys_free()
    assert b.link.sent == ["typeesc", "typeesc", "typeesc"]     # only the first three try
    assert b.sleeps == [0.2, 0.2, 0.2, 1.0, 1.0]
    assert len(b.spoken) == 1 and "text box is open" in b.spoken[0]
    assert b.lines == b.spoken


def test_close_text_box_closes_a_chat_line_and_says_so() -> None:
    b = Read()
    b.link.answers["input"] = Seq({"ok": True, "open": 1, "mode": 5}, {"ok": True, "open": 1, "mode": 5},
                               {"ok": True, "open": 0})
    b.link.send_answers["typeesc"] = "escaped"
    assert b.close_text_box() is True
    assert b.link.sent == ["typeesc"]


def test_close_text_box_leaves_our_own_box_and_a_closed_one() -> None:
    b = Read()
    for answer in ({"ok": True, "open": 0}, {"ok": True, "open": 1, "mode": 3}, {"ok": False}):
        b.link.answers["input"] = answer
        assert b.close_text_box() is False
    assert b.link.sent == []


def test_wait_keys_free_warns_when_the_agent_cannot_escape() -> None:
    b = Read()
    b.link.answers["input"] = Seq({"ok": True, "open": 1}, {"ok": True, "open": 1}, {"ok": True, "open": 0})
    b.link.send_answers["typeesc"] = "no"
    b.wait_keys_free()
    assert b.link.sent == ["typeesc", "typeesc"]     # tried each pass until the box shut
    assert b.sleeps == [1.0, 1.0]
    assert len(b.spoken) == 1


# ==================================================================== sleep
def test_sleep_waits_until_the_time_is_up(clock: Clock) -> None:
    b = Read()
    b.stop_event = FakeEvent(clock)
    ReadingMixin.sleep(b, 0.5)
    assert clock.now == 1000.5
    assert len(b.stop_event.waited) == 5
    assert [round(x, 6) for x in b.stop_event.waited] == [0.1] * 5


def test_sleep_with_zero_returns_at_once(clock: Clock) -> None:
    b = Read()
    b.stop_event = FakeEvent(clock)
    ReadingMixin.sleep(b, 0.0)
    assert clock.now == 1000.0
    assert b.stop_event.waited == []


def test_sleep_stops_when_the_flag_is_set(clock: Clock) -> None:
    b = Read()
    b.stop_event = FakeEvent(clock)
    b.stop_event.set()
    with pytest.raises(Stopped) as e:
        ReadingMixin.sleep(b, 5.0)
    assert e.value.final is True
    assert clock.now == 1000.0                    # nothing was waited at all


# ==================================================================== _read
def test_read_returns_when_the_reader_is_happy(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = {"ok": True}
    assert b._read("mem", lambda s: s.get("ok")) == {"ok": True}
    assert b.sleeps == []


def test_read_retries_the_transient_errors(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = Seq({"error": "busy: loading"}, {"error": "noplayer: x"}, {"ok": True})
    assert b._read("mem", lambda s: s.get("ok")) == {"ok": True}
    assert b.sleeps == [0.25, 0.25]


def test_read_gives_up_after_five_seconds(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = {"error": "noworld: entity map"}
    with pytest.raises(Stopped) as e:
        b._read("mem", lambda s: s.get("ok"))
    assert e.value.final is True
    assert "can't be read" in str(e.value) and "noworld" in str(e.value)
    assert clock.now > 1005.0


def test_read_waits_longer_for_our_character_to_come_back(clock: Clock) -> None:
    # the server can drop our own character from the entity map for a few seconds
    b = Read()
    ticking(b, clock)
    gone = {"error": "noplayer: id 4242 not in the entity map"}
    b.link.answers["mem self"] = Seq(*[gone] * 50, {"id": 4242})
    assert b._read("mem self", lambda s: "error" not in s) == {"id": 4242}
    assert clock.now > 1010.0


def test_read_gives_up_on_a_missing_character_in_the_end(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem self"] = {"error": "noplayer: id 4242 not in the entity map"}
    with pytest.raises(Stopped) as e:
        b._read("mem self", lambda s: "error" not in s)
    assert e.value.final is True and "4242" in str(e.value)
    assert 1060.0 < clock.now < 1062.0


def test_read_gives_up_soon_when_nobody_is_logged_in(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem self"] = {"error": "noplayer: id 0 not in the entity map"}
    with pytest.raises(Stopped):
        b._read("mem self", lambda s: "error" not in s)
    assert clock.now < 1006.0


def test_read_gives_up_at_once_on_a_real_error(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = {"error": "nonsense"}
    with pytest.raises(Stopped) as e:
        b._read("mem", lambda s: s.get("ok"))
    assert b.sleeps == [] and e.value.final is True
    assert "nonsense" in str(e.value)


def test_read_gives_up_on_a_non_dict_answer(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = ["not", "a", "dict"]
    with pytest.raises(Stopped) as e:
        b._read("mem", lambda s: jsonio.is_object(s) and s.get("ok"))
    assert "?" in str(e.value)


def test_read_crashes_on_a_non_dict_answer_when_the_reader_expects_a_dict(clock: Clock) -> None:
    """Observed behaviour: good(r) runs before the non-dict guard, and every real reader
    (snap/me/map_id) calls .get() on it, so a misbehaving agent answering with a list raises
    AttributeError here instead of the game's own Stopped. Documented, not fixed."""
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = ["not", "a", "dict"]
    with pytest.raises(AttributeError):
        b._read("mem", lambda s: s.get("ok"))


def test_read_gives_up_on_a_value_the_reader_rejects(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem"] = {"ok": False}
    with pytest.raises(Stopped):
        b._read("mem", lambda s: s.get("ok"))
    assert b.sleeps == []


# ==================================================================== snap / me / map_id
def test_snap_returns_the_reading(clock: Clock) -> None:
    b = Read()
    b.link.answers["mem"] = {"ok": True, "self": {"id": 1}}
    assert b.snap()["self"]["id"] == 1


def test_snap_asks_for_the_lean_state_when_the_agent_has_it(clock: Clock) -> None:
    b = Read()
    b.agent_state = True
    b.link.answers["mem state"] = {"ok": True, "self": {"id": 2}}
    assert b.snap()["self"]["id"] == 2


def test_snap_gives_up_when_the_game_is_not_ok(clock: Clock) -> None:
    b = Read()
    b.link.answers["mem"] = {"ok": False}
    with pytest.raises(Stopped):
        b.snap()


def test_me_remembers_the_reading(clock: Clock) -> None:
    b = Read()
    s = {"id": 1, "x": 2.0, "y": 3.0}
    b.link.answers["mem self"] = s
    assert b.me() == s
    assert b.last_me is s


def test_me_retries_an_error_then_succeeds(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem self"] = Seq({"error": "busy: x"}, {"id": 9})
    assert b.me() == {"id": 9}
    assert b.last_me == {"id": 9}
    assert b.sleeps == [0.25]


def test_map_id_returns_the_id(clock: Clock) -> None:
    b = Read()
    b.link.answers["mem map"] = {"id": 5121}
    assert b.map_id() == 5121


def test_map_id_without_an_id_raises_a_key_error(clock: Clock) -> None:
    """Observed behaviour: the reader accepts a map answer with no "error" key, then indexes
    "id"; an agent reporting a map without an id makes map_id() raise KeyError rather than stop
    the run. Left as-is and documented."""
    b = Read()
    b.link.answers["mem map"] = {}
    with pytest.raises(KeyError):
        b.map_id()


# ==================================================================== settled_map
def test_settled_map_returns_the_first_settled_map(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem map"] = {"id": 5121}
    b.link.answers["mem self"] = {"id": 1}
    assert b.settled_map() == 5121
    assert b.last_me == {"id": 1}
    assert b.sleeps == []


def test_settled_map_skips_a_map_still_at_zero(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem map"] = Seq({"id": 0}, {"id": 5121})
    b.link.answers["mem self"] = {"id": 1}
    assert b.settled_map() == 5121
    assert b.sleeps == [0.3]


def test_settled_map_waits_for_the_player_to_come_back(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem map"] = {"id": 5121}
    b.link.answers["mem self"] = Seq({"error": "noplayer: id 1 not in the entity map"}, {"id": 1})
    assert b.settled_map() == 5121
    assert b.last_me == {"id": 1}
    assert b.sleeps == [0.3]


def test_settled_map_waits_when_the_map_changes_during_the_reads(clock: Clock) -> None:
    # map-id-first: the new id is read, then the player, then the map changed under us: read again
    b = Read()
    ticking(b, clock)
    b.link.answers["mem map"] = Seq({"id": 1001}, {"id": 5121}, {"id": 5121})
    b.link.answers["mem self"] = {"id": 1, "x": 3, "y": 4}
    assert b.settled_map() == 5121
    assert b.sleeps == [0.3]


def test_settled_map_times_out_and_reads_the_plain_way(clock: Clock) -> None:
    b = Read()
    ticking(b, clock)
    b.link.answers["mem map"] = {"id": 0}
    b.link.answers["mem self"] = {"id": 1}
    assert b.settled_map(seconds=1.0) == 0
    assert b.last_me == {"id": 1}
    assert b.sleeps == [0.3, 0.3, 0.3, 0.3]


# ==================================================================== minutes
def test_minutes_counts_from_the_start(clock: Clock) -> None:
    b = Read()
    b.started = clock.now - 180
    assert b.minutes() == 3.0


# ==================================================================== run_requests
def test_run_requests_does_nothing_while_busy() -> None:
    b = Read()
    b.doing_request = True
    ran: list[int] = []
    b.requests.put(lambda bot: ran.append(1))
    b.run_requests()
    assert ran == [] and b.requests.qsize() == 1


def test_run_requests_waits_for_held_keys() -> None:
    b = Read()
    b.held = {"w"}
    ran: list[int] = []
    b.requests.put(lambda bot: ran.append(1))
    b.run_requests()
    assert ran == [] and b.requests.qsize() == 1


def test_run_requests_with_nothing_queued() -> None:
    b = Read()
    assert b.run_requests() is None


def test_run_requests_runs_the_queue_in_order() -> None:
    b = Read()
    seen: list[tuple[str, bool]] = []

    def job(name: str) -> Callable[[Bot], None]:
        def run(bot: Bot) -> None:
            assert bot is b
            seen.append((name, b.doing_request))
        return run

    b.requests.put(job("a"))
    b.requests.put(job("b"))
    b.run_requests()
    assert seen == [("a", True), ("b", True)]
    assert b.doing_request is False
    assert b.requests.empty()


def test_run_requests_clears_its_flag_after_a_job_raises() -> None:
    b = Read()

    def boom(bot: Bot) -> NoReturn:
        raise RuntimeError("nope")

    b.requests.put(boom)
    with pytest.raises(RuntimeError):
        b.run_requests()
    assert b.doing_request is False


# ==================================================================== timed
def test_timed_counts_a_phase(clock: Clock) -> None:
    b = Read()
    b.timing = {}
    with b.timed("fight"):
        clock.advance(10)
    assert b.timing == {"fight": 10.0}
    assert b._phases == []


def test_timed_adds_to_what_a_phase_already_has(clock: Clock) -> None:
    b = Read()
    b.timing = {"fight": 5.0}
    with b.timed("fight"):
        clock.advance(2)
    assert b.timing == {"fight": 7.0}


def test_timed_without_timing_does_nothing(clock: Clock) -> None:
    b = Read()
    b.timing = None
    with b.timed("fight"):
        clock.advance(10)
    assert b.timing is None
    assert b._phases == []


def test_timed_nests_without_double_counting(clock: Clock) -> None:
    b = Read()
    b.timing = {}
    with b.timed("fight"):
        clock.advance(2)
        with b.timed("rest"):
            clock.advance(3)
        clock.advance(1)
    assert b.timing["fight"] == 3.0             # the 3 s of rest are not counted again
    assert b.timing["rest"] == 3.0
    assert b._phases == []


# ==================================================================== timing_report
def test_timing_report_without_timing_does_nothing(clock: Clock) -> None:
    b = Read()
    b.timing = None
    b.timing_report()
    assert b.lines == []


def test_timing_report_waits_for_the_hour(clock: Clock) -> None:
    b = Read()
    b.timing = {"_since": clock.now - 3599, "_kills": 0}
    t = b.timing
    b.timing_report()
    assert b.lines == []
    assert b.timing is t


def test_timing_report_final_waits_only_a_minute(clock: Clock) -> None:
    b = Read()
    b.timing = {"_since": clock.now - 100, "_kills": 0}
    b.timing_report(final=True)
    assert b.lines and b.lines[0].startswith("last ")


def test_timing_report_lays_out_the_hour(clock: Clock) -> None:
    b = Read()
    b.timing = {"_since": clock.now - 3600, "_kills": 10, "search": 900.0, "fight": 1800.0}
    b.kills = 40
    b.timing_report()
    assert b.lines == ["last 60 min: 30 kills (30 an hour), 120.0 s a kill - "
                       "searching 25%, fighting 50%, other 25%"]
    assert b.timing == {"_since": clock.now, "_kills": 40, "_contribution": -1}


def test_timing_report_adds_the_contribution_earned(clock: Clock) -> None:
    import types
    b = Read()
    b.tally = as_kind(Tally, types.SimpleNamespace(sheet={"contribution": 25600}))
    b.timing = {"_since": clock.now - 3600, "_kills": 0, "_contribution": 21000}
    b.timing_report()
    assert b.lines[1] == "last 60 min: contribution +4600 (4600 an hour)"
    assert b.timing["_contribution"] == 25600


def test_timing_report_with_no_kills(clock: Clock) -> None:
    b = Read()
    b.timing = {"_since": clock.now - 3600, "_kills": 5}
    b.kills = 5
    b.timing_report()
    assert "0 kills" in b.lines[0]
    assert "3600.0 s a kill" in b.lines[0]


# ==================================================================== ui
def test_ui_returns_the_reading() -> None:
    b = Read()
    b.link.answers["mem ui"] = {"panel": 2, "cursor": 5}
    assert b.ui() == {"panel": 2, "cursor": 5}


def test_ui_returns_empty_on_error() -> None:
    b = Read()
    b.link.answers["mem ui"] = {"error": "no ui"}
    assert b.ui() == {}
