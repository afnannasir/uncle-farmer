"""Concurrency stress for the shared state around a run: the GUI pause/stop path, the run's
fight loop, and the messenger's private-message commands.

The point is not a particular outcome (pause and stop may win in any order) but that none of the
threads deadlock or leave an error behind. A faulthandler watchdog dumps every thread's stack and
exits if the test hangs, instead of leaving CI to time out with no clue where.
"""

from __future__ import annotations

import faulthandler
import threading
import time
from typing import Any

import pytest

from unclefarmer import messages, settings
from unclefarmer.engine import runner
from unclefarmer.engine.bot import Bot
from unclefarmer.engine.botparts.base import Stopped
from conftest import as_kind, stand_in
from unclefarmer.engine import combat
from collections.abc import Callable, Iterator
from unclefarmer.engine.runner import Run
from unclefarmer.messages import T, Ts


class Link:
    """The agent, shared by the fight loop and the messenger. Every method is locked: the test is
    checking the app's threads, not the fake's."""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.sent: list[str] = []
        self.speech = "on"

    def send(self, line: str) -> str:
        with self.lock:
            self.sent.append(line)
            if line == "speech":
                return self.speech
            if line in ("speech on", "speech off"):
                self.speech = line.split()[1]
                return self.speech
            if line.startswith("keydown "):
                return "down " + line.split()[1]
            if line.startswith("keyup "):
                return "up"
            if line.startswith("guardmods "):
                return line.split()[1]
            if line.startswith("key "):
                return "tapped"
            if line.startswith("pm "):
                return "sent"
            return "ok"

    def json(self, line: str) -> Any:
        return {"ok": True, "open": 0} if line == "input" else {}

    def close(self) -> None:
        pass


class SkillBook:
    """Just enough of the combat book for burst(): a key press that the game confirms."""

    ok = True
    cls = "Warrior"
    press_confirmed = True
    casts_done = 0

    def read_casts(self, me_id: int | None) -> None:
        pass


class Host:
    """The window's side of the messenger: run_for/call/log/stop_bot."""

    def __init__(self, run: Run) -> None:
        self.run = run
        self.stop_calls: list[int] = []
        self.logs: list[tuple[str, bool, str | None, bool]] = []

    def run_for(self, pid: int) -> Run | None:
        return self.run

    def call(self, fn: Callable[[*Ts], T], *args: *Ts) -> T | str | None:
        return fn(*args)

    def start_bot(self, pid: int, plan: str | None) -> str | None:
        return None

    def errand_for(self, pid: int, text: str, title: str, tell: Callable[[str], None] | None = None,
                   follows: str | None = None) -> str | None:
        return None

    def follow_for(self, pid: int, leader: str, tell: Callable[[str], None] | None = None) -> str | None:
        return None

    def unfollow(self, pid: int) -> str | None:
        return None

    def stop_bot(self, pid: int) -> None:
        self.stop_calls.append(pid)
        self.run.stop()

    def log(self, line: str, speak: bool=False, who: str | None=None, private: bool=False) -> None:
        self.logs.append((line, speak, who, private))

    def character(self, pid: int) -> str | None:
        return "Hero"


class Frame:
    """Just the MainFrame attributes toggle_pause()/stop() use."""

    def __init__(self, run: Run) -> None:
        self.runs = {1: run}
        self.said: list[str] = []
        self.syncs = 0
        self.labels = 0

    def say(self, text: str, run: Run | None = None) -> None:
        self.said.append(text)

    def sync_pause(self):
        self.syncs += 1

    def label_games(self):
        self.labels += 1


def make_run(monkeypatch: pytest.MonkeyPatch) -> tuple[Run, Bot, Link, list[str], threading.Event]:
    link = Link()
    lines: list[str] = []
    paused = threading.Event()

    def log(line: str, speak: bool=False) -> None:
        lines.append(line)
        if "paused" in line:
            paused.set()

    run = runner.Run("", "Stress", 1, lambda line, speak, run: None, lambda reason, run: None)
    bot = Bot(link, settings.load_profile("_test_"), log, run.stop_event)
    bot.held = {"d"}                 # a walk in progress, so pause has keys to let go
    bot.guarding = True
    bot.skillbook = as_kind(combat.SkillBook, SkillBook())
    run.bot = bot
    return run, bot, link, lines, paused


def start_fight(bot: Bot, errors: list[BaseException]) -> threading.Thread:
    started = threading.Event()

    def fight() -> None:
        started.set()
        try:
            while True:
                bot.burst("j", 7, longest=0.05)      # the real fight's key-burst path
        except Stopped:
            return
        except BaseException as e:      # noqa: BLE001 - reported by the test
            errors.append(e)

    thread = threading.Thread(target=fight, name="unclefarmer-stress-fight", daemon=True)
    thread.start()
    assert started.wait(2.0), "the fake fight didn't start"
    return thread


def finish(threads: list[threading.Thread], run: Run, bot: Bot, errors: list[BaseException] | None = None) -> None:
    for thread in threads:
        thread.join(5.0)
    if run.stop_event.is_set():
        for thread in threads:
            thread.join(2.0)
    bot.let_go()
    assert not any(thread.is_alive() for thread in threads), "a stress thread deadlocked (stop=%s paused=%s alive=%s errors=%r)" % (run.stop_event.is_set(), bot.paused.is_set(), [t.name for t in threads if t.is_alive()], errors)


@pytest.fixture(autouse=True)
def _deadlock_watchdog() -> Iterator[None]:
    """A deadlock dumps all thread stacks and fails the test instead of hanging CI."""
    faulthandler.dump_traceback_later(20, exit=True)
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()


def test_gui_pause_and_stop_race_a_fake_fight(monkeypatch: pytest.MonkeyPatch):
    pytest.importorskip("wx")
    from unclefarmer.gui.main import MainFrame

    run, bot, link, _lines, paused = make_run(monkeypatch)
    frame = Frame(run)
    errors: list[BaseException] = []
    fight = start_fight(bot, errors)
    toggles = 0

    def gui():
        nonlocal toggles
        try:
            # Pause once and wait until the fight has actually entered the pause branch.
            while not run.stop_event.is_set():
                MainFrame.toggle_pause(as_kind(MainFrame, frame), 1)
                toggles += 1
                if paused.wait(0.5):
                    break
            # While paused, keep the pause key busy, then press Stop from the same GUI path.
            for _ in range(20):
                if run.stop_event.is_set():
                    break
                MainFrame.toggle_pause(as_kind(MainFrame, frame), 1)
                toggles += 1
                time.sleep(0.001)
            MainFrame.stop(as_kind(MainFrame, frame), 1)
        except BaseException as e:      # noqa: BLE001
            errors.append(e)

    gui_thread = threading.Thread(target=gui, name="unclefarmer-stress-gui", daemon=True)
    gui_thread.start()
    finish([fight, gui_thread], run, bot, errors)

    assert not errors
    assert run.stop_event.is_set()
    assert toggles > 10
    assert frame.syncs > 0
    assert any(line.startswith("keyup ") for line in link.sent)
    assert any(line == "guardmods off" for line in link.sent)


def test_private_message_pause_resume_stop_race_a_fake_fight(monkeypatch: pytest.MonkeyPatch):
    run, bot, link, _lines, paused = make_run(monkeypatch)
    host = Host(run)
    cfg: dict[str, Any] = dict(messages.DEFAULTS)
    cfg.update(pm_trusted=["Boss"], pm_commands=True, pm_replies="never", pm_speak="never")
    monkeypatch.setattr(messages, "config", stand_in(messages.config, lambda: cfg))
    messenger = messages.Messenger(1, host)
    messenger.link = link
    errors: list[BaseException] = []
    fight = start_fight(bot, errors)

    def private_messages():
        try:
            # Enter the pause branch, let the fight notice it, then hammer pause/resume.
            messenger.received({"name": "Boss", "text": "pause", "id": 7})
            assert paused.wait(2.0), "the private pause command didn't reach the fight"
            for i in range(80):
                if run.stop_event.is_set():
                    break
                messenger.received({"name": "Boss", "text": "pause" if i % 2 else "resume", "id": 7})
                messenger.flush()
                time.sleep(0.001)
            messenger.received({"name": "Boss", "text": "stop", "id": 7})
        except BaseException as e:      # noqa: BLE001
            errors.append(e)

    msg_thread = threading.Thread(target=private_messages, name="unclefarmer-stress-pm", daemon=True)
    msg_thread.start()
    finish([fight, msg_thread], run, bot, errors)

    assert not errors
    assert run.stop_event.is_set()
    assert host.stop_calls == [1]
    assert any(line == "guardmods off" for line in link.sent)
    assert any(line == "guardmods on" for line in link.sent)
