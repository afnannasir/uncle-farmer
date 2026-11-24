"""The `wait` command: log out by the setting menu, wait, log back in with the saved account and
go on; and one action at a time ("Busy")."""
from __future__ import annotations

import collections
import queue
import threading
from types import SimpleNamespace
from typing import Any

import pytest

from unclefarmer import messages
from unclefarmer.engine import logout
from unclefarmer.engine.botparts.base import Stopped
from unclefarmer.engine.runner import Run as RealRun
from conftest import Clock, HostStub, as_bot, as_kind, returning

HERO = 4242


class Link:
    """The game: in the world until Switch account, at the login screen until autologin."""

    def __init__(self, login_fails: bool = False) -> None:
        self.inside = True
        self.login_fails = login_fails
        self.sent: list[str] = []

    def send(self, line: str) -> str:
        self.sent.append(line)
        return "armed" if line == "arm" else "ok"

    def json(self, line: str) -> dict[str, Any]:
        self.sent.append(line)
        if line == "mem state":
            return {"ok": True, "map": {"id": 5281}} if self.inside else {"ok": False, "error": "noworld: closed"}
        if line.startswith("autologin "):
            return {"ok": True}
        if line == "autologinstatus":
            if self.login_fails:
                return {"ok": True, "running": False, "status": "the character never loaded"}
            self.inside = True
            return {"ok": True, "running": False, "status": "ready"}
        if line == "mem self":
            return {"id": HERO}
        return {"ok": True}


class Bot:
    def __init__(self, link: Link, menu: tuple[str, ...] = ("setting menu", "switch account", "quit")) -> None:
        self.a = link
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.profile_key = "Hero--S1--4242"
        self.menu = menu
        self.opened = False
        self.keys: list[str] = []
        self.lines: list[str] = []
        self.counted: list[bool] = []
        self.logins_after = 0
        self.resumed_at = 0.0

    def close_dialogs(self) -> None:
        self.opened = False

    def tap_key(self, k: str, ms: int = 60) -> None:
        self.keys.append(k)
        self.opened = True

    def dialog(self) -> dict[str, Any]:
        return {"ok": True, "open": 1 if self.opened else 0}

    def choose(self, pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        import re
        if any(re.search(pattern, m, re.I) for m in self.menu):
            self.a.inside = False
            return {"text": "switch account"}
        return None

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def let_go(self, k: str | None = None) -> None:
        pass

    def log(self, text: str, speak: bool = False) -> None:
        self.lines.append(text)

    def _count_pause(self, paused: bool) -> None:
        self.counted.append(paused)

    def after_login(self) -> None:
        self.logins_after += 1


@pytest.fixture(autouse=True)
def saved_account(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout.settings, "load_profile", returning({"login_email": "hero@example.com"}))
    monkeypatch.setattr(logout.settings, "profile_id", returning(HERO))
    monkeypatch.setattr(logout.settings, "profile_server", returning("S1"))
    monkeypatch.setattr(logout.gamekeys, "option_key", returning("f1"))


def test_a_wait_logs_out_waits_and_comes_back_in() -> None:
    link = Link()
    bot = Bot(link)
    told: list[str] = []
    start = bot.clock.time()
    logout.wait_out(as_bot(bot), 45, "Bob", told.append)
    assert bot.keys == ["f1"]
    assert bot.clock.time() - start >= 45 * 60
    assert "autologin hero@example.com 4242 S1" in link.sent
    assert link.inside and bot.logins_after == 1
    assert bot.counted == [True, False]              # the wait is a pause: nothing in it is the run's
    assert bot.resumed_at >= start + 45 * 60
    assert told == ["Back in after 45 minutes."]
    assert "logged out for 45 minutes, as Bob asked" in bot.lines
    assert [x for x in bot.lines if x.startswith("10 of 45")]       # a line every 10 minutes


def test_no_saved_account_never_logs_out(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(logout.settings, "load_profile", returning(dict[str, str]()))
    bot = Bot(Link())
    told: list[str] = []
    logout.wait_out(as_bot(bot), 5, "Bob", told.append)
    assert bot.keys == [] and bot.a.inside
    assert told and told[0].startswith("I don't know this character's saved account")


def test_a_menu_without_switch_account_leaves_the_character_in() -> None:
    bot = Bot(Link(), menu=("setting menu", "quit"))
    told: list[str] = []
    logout.wait_out(as_bot(bot), 5, "Bob", told.append)
    assert bot.a.inside and bot.counted == [True, False]
    assert told == ["Couldn't log out: the setting menu has no Switch account"]


def test_a_login_that_keeps_failing_ends_the_run() -> None:
    link = Link(login_fails=True)
    bot = Bot(link)
    with pytest.raises(Stopped, match="Could not log back in"):
        logout.wait_out(as_bot(bot), 1, "Bob", lambda text: None)
    assert sum(1 for x in link.sent if x.startswith("autologin ")) == logout.TRIES


def test_stop_during_the_wait_leaves_the_character_out() -> None:
    link = Link()
    bot = Bot(link)
    bot.stop_event.set()
    with pytest.raises(Stopped, match="stays logged out"):
        logout.wait_out(as_bot(bot), 30, "Bob", lambda text: None)
    assert not link.inside and not any(x.startswith("autologin ") for x in link.sent)


@pytest.mark.parametrize("rest, want", [("", 30), ("45", 45), ("45 min", 45), ("10minutes", 10), ("0", None),
                                        ("1000", None), ("soon", None)])
def test_the_minutes_asked_for(rest: str, want: int | None) -> None:
    assert logout.minutes_in(rest, 30) == want


def test_wait_is_parsed_with_or_without_minutes() -> None:
    assert messages.parse_command("wait") == ("wait", "")
    assert messages.parse_command("wait 45") == ("wait", "45")


# ---------------------------------------------------------------- the command, and Busy
class FakeRun:
    def __init__(self) -> None:
        self.bot = SimpleNamespace(requests=queue.Queue(), stop_event=threading.Event())
        self.title = "farming"


class Host(HostStub):
    def __init__(self, run: FakeRun | None) -> None:
        self.run = run

    def run_for(self, pid: int) -> RealRun | None:
        return None if self.run is None else as_kind(RealRun, self.run)


CLOCK = Clock()


def messenger(run: FakeRun | None, monkeypatch: pytest.MonkeyPatch, allowed: bool = True) -> messages.Messenger:
    m = messages.Messenger.__new__(messages.Messenger)
    m.host, m.pid, m.outbox = Host(run), 1, collections.deque()
    m.clock = CLOCK
    cfg = dict(messages.DEFAULTS, pm_wait=allowed, pm_wait_minutes=30)
    monkeypatch.setattr(messages, "config", lambda: cfg)
    return m


def answers(m: messages.Messenger) -> list[str]:
    return [x[2] for x in m.outbox]


def test_wait_is_off_unless_ticked(monkeypatch: pytest.MonkeyPatch) -> None:
    m = messenger(FakeRun(), monkeypatch, allowed=False)
    m.command(5, "Bob", "wait", "")
    assert answers(m) == ["That command is turned off on this character."]


def test_a_number_out_of_range_is_explained(monkeypatch: pytest.MonkeyPatch) -> None:
    m = messenger(FakeRun(), monkeypatch)
    m.command(5, "Bob", "wait", "5000")
    assert answers(m) == ["Send wait and 1 to 999 minutes, for example: wait 45"]


def test_a_running_bot_waits_between_steps_and_other_actions_are_busy(monkeypatch: pytest.MonkeyPatch) -> None:
    run = FakeRun()
    m = messenger(run, monkeypatch)
    waited: list[int] = []
    def wait_out(bot: object, minutes: int, who: str, tell: object) -> None:
        waited.append(minutes)
    monkeypatch.setattr(logout, "wait_out", wait_out)
    m.command(5, "Bob", "wait", "20")
    assert answers(m) == ["Got it: logging out for 20 minutes, then back to farming."]
    m.outbox.clear()
    CLOCK.advance(5 * 60)
    m.command(5, "Bob", "home", "")
    assert answers(m) == ["Busy, one command at a time. Still on: wait (15 min left)."]
    m.outbox.clear()
    monkeypatch.setattr(m, "snap", lambda: None)
    m.command(5, "Bob", "status", "")                # a question is always answered
    assert answers(m) == ["Not logged in."]
    job = run.bot.requests.get_nowait()
    job(run.bot)                                     # the bot does it between two steps
    assert waited == [20] and m.busy is None


def test_an_action_the_run_never_did_is_not_busy_once_the_run_is_over(monkeypatch: pytest.MonkeyPatch) -> None:
    run = FakeRun()
    m = messenger(run, monkeypatch)
    m.command(5, "Bob", "wait", "20")
    assert m.still_on() is not None
    m.host = Host(None)                              # the run ended before its turn came
    assert m.still_on() is None
