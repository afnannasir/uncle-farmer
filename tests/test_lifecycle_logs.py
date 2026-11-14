# pyright: reportPrivateUsage=false
# White-box tests: they call __main__'s stage helpers (leading-underscore names) directly.
"""bootup.log and teardown.log: every stage written as it starts and ends, a failure with its
traceback, an END that never says ready or closed after a failure, and a close that goes on past a
failed step. Every file lands in the test's own logs folder (conftest's user_dirs)."""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import applog, paths

wx = pytest.importorskip("wx")

from unclefarmer import __main__ as start, filelock, instance, settings  # noqa: E402
from unclefarmer.gui import main  # noqa: E402
from unclefarmer.gui.mainparts import editing, updates  # noqa: E402


@pytest.fixture(autouse=True)
def fresh_logs(monkeypatch: pytest.MonkeyPatch) -> None:
    """New Lifecycle objects for each test: the module's own carry state from one start to the next."""
    monkeypatch.setattr(applog, "bootup", applog.Lifecycle("bootup.log", "startup"))
    monkeypatch.setattr(applog, "teardown", applog.Lifecycle("teardown.log", "shutdown"))
    monkeypatch.setattr(applog, "_warned", False)


def lines(name: str) -> list[str]:
    with open(os.path.join(paths.LOGS, name), encoding="utf-8") as f:
        return f.read().splitlines()


def text(name: str) -> str:
    return "\n".join(lines(name))


# ------------------------------------------------------------ Lifecycle itself
def test_a_clean_start_has_begin_each_stage_and_a_ready_end() -> None:
    boot = applog.bootup
    boot.begin("Uncle Farmer 1.0")
    with boot.step("settings"):
        pass
    assert boot.end("ready") is True
    rows = lines("bootup.log")
    assert "INFO" in rows[0] and "[%d] BEGIN startup: Uncle Farmer 1.0" % os.getpid() in rows[0]
    assert rows[1].endswith("settings: started")
    assert "settings: done (" in rows[2]
    assert "END startup: ready in " in rows[3] and "INFO" in rows[3]
    assert rows[0][:4].isdigit() and rows[0][19] == "."        # 2026-11-02 12:00:00.123


def test_a_failed_stage_has_its_traceback_and_the_end_says_failed_not_ready() -> None:
    boot = applog.bootup
    boot.begin("x")
    with pytest.raises(ValueError) as caught:
        with boot.step("settings"):
            raise ValueError("bad json")
    boot.crashed(caught.value)
    log = text("bootup.log")
    assert "ERROR" in log and "settings: FAILED after" in log and "ValueError: bad json" in log
    assert log.count("Traceback (most recent call last)") == 1      # once: the END doesn't repeat it
    assert "END startup: FAILED at 'settings'" in log
    assert "END startup: ready" not in log


def test_an_error_outside_any_stage_is_written_with_its_traceback() -> None:
    boot = applog.bootup
    boot.begin("x")
    try:
        raise RuntimeError("between stages")
    except RuntimeError as e:
        boot.crashed(e)
    log = text("bootup.log")
    assert "unexpected error during 'outside any stage': RuntimeError: between stages" in log
    assert "Traceback" in log and "END startup: FAILED at 'outside any stage'" in log


def test_a_step_that_needn_t_work_is_written_down_and_the_next_goes_ahead() -> None:
    down = applog.teardown
    down.begin("close")
    with down.step("stop the timer", must=False):
        raise OSError("gone")
    with down.step("destroy the window", must=False):
        pass
    assert down.end("closed") is False
    log = text("teardown.log")
    assert "stop the timer: FAILED" in log and "OSError: gone" in log
    assert "destroy the window: done" in log
    assert "END shutdown: closed in" in log and "with 1 problem: stop the timer" in log
    assert "WARNING" in lines("teardown.log")[-1]


def test_ctrl_c_is_never_held_back_even_by_a_step_that_needn_t_work() -> None:
    down = applog.teardown
    down.begin("close")
    with pytest.raises(KeyboardInterrupt):
        with down.step("wait", must=False):
            raise KeyboardInterrupt
    assert down.failed == "wait"


def test_a_problem_inside_a_stage_shows_on_its_closing_line() -> None:
    down = applog.teardown
    down.begin("close")
    with down.step("wait for the bots to end", must=False):
        down.problem("the bot of game 7 still runs after 5 s")
    log = text("teardown.log")
    assert "wait for the bots to end: finished with problems" in log
    assert "wait for the bots to end: done" not in log


def test_the_last_block_without_an_end_is_reported_by_the_next_begin() -> None:
    with open(os.path.join(paths.LOGS, "bootup.log"), "w", encoding="utf-8") as f:
        f.write("2026-11-01 10:00:00.000  INFO    [4242] BEGIN startup: old\n"
                "2026-11-01 10:00:00.010  INFO    [4242] settings: started\n"
                "2026-11-01 10:00:00.020  INFO    [4242] settings: done (10 ms)\n"
                "2026-11-01 10:00:00.030  INFO    [4242] main window: started\n"
                "2026-11-01 11:00:00.000  INFO    [5151] BEGIN startup: finished one\n"
                "2026-11-01 11:00:01.000  INFO    [5151] END startup: ready in 1.0 s\n")
    applog.bootup.begin("new")
    log = text("bootup.log")
    assert "the startup of pid 4242 before this one has no END" in log and "during 'main window'" in log
    assert "pid 5151" not in log


def test_a_log_that_can_t_be_written_never_stops_the_app(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    blocker = tmp_path / "not a folder"
    blocker.write_text("")
    monkeypatch.setattr(paths, "LOGS", str(blocker))
    boot = applog.bootup
    boot.begin("x")
    with boot.step("settings"):
        pass
    assert boot.end("ready") is True          # nothing written, nothing raised, and the start went on


# ------------------------------------------------------------ the app's start (__main__)
@pytest.fixture
def quiet_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """The start without touching the real data files or the one-copy lock of this PC."""
    monkeypatch.setattr(sys, "argv", ["unclefarmer"])
    monkeypatch.setattr(instance, "acquire", lambda: True)
    monkeypatch.setattr(instance, "release", lambda: None)
    monkeypatch.setattr(filelock, "acquire_startup", lambda: None)
    monkeypatch.setattr(filelock, "release_all", lambda: None)


@pytest.mark.usefixtures("quiet_start")
def test_a_start_that_fails_names_the_stage_and_still_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken() -> dict[str, Any]:
        raise ValueError("settings.json is not JSON")
    monkeypatch.setattr(settings, "load_app", broken)
    with pytest.raises(ValueError):
        start.main()
    log = text("bootup.log")
    assert "BEGIN startup: Uncle Farmer " in log and "one copy only: done" in log
    assert "hold the data files: done" in log
    assert "settings: FAILED" in log and "settings.json is not JSON" in log
    assert "END startup: FAILED at 'settings' after " in lines("bootup.log")[-1]
    assert log.count("Traceback") == 1                  # once, not again at the END


@pytest.mark.usefixtures("quiet_start")
def test_a_second_copy_says_so_and_ends(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(instance, "acquire", lambda: False)
    monkeypatch.setattr(instance, "focus_existing", lambda title="": True)
    assert start._start(applog.bootup) is None
    log = text("bootup.log")
    assert "another Uncle Farmer is open" in log and "END startup: not started (another copy is open)" in log


@pytest.mark.usefixtures("quiet_start")
def test_the_end_of_a_close_goes_on_past_a_failed_release(monkeypatch: pytest.MonkeyPatch) -> None:
    released: list[str] = []

    def broken() -> None:
        raise OSError("locked")
    monkeypatch.setattr(filelock, "release_all", broken)
    monkeypatch.setattr(instance, "release", lambda: released.append("lock"))
    down = applog.teardown
    down.begin("close requested (the window)")
    start._finish(down)
    log = text("teardown.log")
    assert "main loop ended" in log and "release the data files: FAILED" in log and "OSError: locked" in log
    assert released == ["lock"] and "release the one-copy lock: done" in log
    assert "END shutdown: closed in" in log and "with 1 problem" in log


@pytest.mark.usefixtures("quiet_start")
def test_a_main_loop_that_ends_without_a_close_still_gets_a_block() -> None:
    start._finish(applog.teardown)
    log = text("teardown.log")
    assert "BEGIN shutdown: the main loop ended without a close request" in log
    assert lines("teardown.log")[-1].split("] ", 1)[1].startswith("END shutdown: closed in")


# ------------------------------------------------------------ the window's part
class Closing:
    """A stand-in main window for on_close and first_run; the rest is MainFrame's own."""

    def __init__(self, runs: dict[int, Any] | None = None, answer: bool = True) -> None:
        self.runs = dict(runs or {})
        self.messengers: dict[int, Any] = {1: Stoppable()}
        self.chats: dict[int, Any] = {1: Stoppable()}
        self.close_reason = ""
        self.hotkeys = [main.HOTKEY_ID, main.RESULTS_HOTKEY_ID]
        self.destroyed = False
        self.answer = answer
        self.timer = Stoppable()
        self.released: list[int] = []
        self.gone = False
        self.s: dict[str, Any] = {}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(main.MainFrame, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr

    def UnregisterHotKey(self, hotkey: int) -> bool:
        self.released.append(hotkey)
        return True

    def Destroy(self) -> bool:
        self.gone = True
        return True


class Stoppable:
    def __init__(self, fails: bool = False) -> None:
        self.stopped, self.fails = False, fails

    def stop(self) -> None:
        if self.fails:
            raise RuntimeError("stuck")
        self.stopped = True

    Stop = stop


class Bot:
    def __init__(self, pid: int, hangs: bool = False) -> None:
        self.pid, self.hangs, self.stopped, self.waited = pid, hangs, False, 0.0

    def stop(self) -> None:
        self.stopped = True

    def join(self, timeout: float) -> None:
        self.waited = timeout

    def is_alive(self) -> bool:
        return self.hangs


class Event:
    def __init__(self, can_veto: bool = True) -> None:
        self.can_veto, self.vetoed = can_veto, False

    def CanVeto(self) -> bool:
        return self.can_veto

    def Veto(self) -> None:
        self.vetoed = True


@pytest.fixture
def answers(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    asked: list[str] = []

    def ask(win: Closing, question: str, *a: Any) -> bool:
        asked.append(question)
        return win.answer
    monkeypatch.setattr(main, "ask", ask)
    return asked


@pytest.mark.usefixtures("answers")
def test_a_close_stops_everything_and_writes_each_step() -> None:
    bots = {7: Bot(7), 8: Bot(8)}
    w = Closing(bots)
    w.on_close(Event())
    assert all(b.stopped and b.waited == main.BOT_STOP_WAIT for b in bots.values())
    assert w.messengers[1].stopped and w.chats[1].stopped and w.timer.stopped
    assert w.released == [main.HOTKEY_ID, main.RESULTS_HOTKEY_ID] and w.gone and w.destroyed
    log = text("teardown.log")
    assert "BEGIN shutdown: close requested (the window)" in log
    for step in ("ask 2 bots to stop", "wait for the bots to end", "ask the private messages and chat readers to stop",
                 "release the hotkeys", "stop the game list timer", "destroy the window"):
        assert step + ": done" in log
    assert "END" not in log            # __main__ writes it once the main loop is over


@pytest.mark.usefixtures("answers")
def test_a_bot_that_doesn_t_end_in_time_is_written_down() -> None:
    w = Closing({7: Bot(7, hangs=True)})
    w.on_close(Event())
    log = text("teardown.log")
    assert "the bot of game 7 still runs after 5 s" in log and "WARNING" in log
    assert "wait for the bots to end: finished with problems" in log
    assert w.gone
    applog.teardown.end("closed")
    assert "END shutdown: closed in" in lines("teardown.log")[-1] and "1 problem" in lines("teardown.log")[-1]


@pytest.mark.usefixtures("answers")
def test_a_failed_close_step_leaves_the_others_to_run() -> None:
    w = Closing()
    w.messengers = {1: Stoppable(fails=True)}
    w.timer = Stoppable(fails=True)
    w.on_close(Event())
    log = text("teardown.log")
    assert "ask the private messages and chat readers to stop: FAILED" in log and "stop the game list timer: FAILED" in log
    assert log.count("Traceback (most recent call last)") == 2 and "RuntimeError: stuck" in log
    assert w.released and w.gone and "destroy the window: done" in log


def test_keeping_the_bots_running_cancels_the_close(answers: list[str]) -> None:
    bot = Bot(7)
    w = Closing({7: bot}, answer=False)
    evt = Event()
    w.on_close(evt)
    assert answers and evt.vetoed and not bot.stopped and not w.gone
    assert "END shutdown: cancelled: the player kept the bots running" in lines("teardown.log")[-1]


@pytest.mark.usefixtures("answers")
def test_pending_update_cannot_close_while_a_bot_still_runs(monkeypatch: pytest.MonkeyPatch) -> None:
    w = Closing({7: Bot(7, hangs=True)})
    monkeypatch.setattr(w, "pending_update", object(), raising=False)
    event = Event()
    w.on_close(event)
    assert event.vetoed and not w.gone and not w.destroyed
    assert "update cancelled: shutdown is not safe" in text("teardown.log")


@pytest.mark.usefixtures("answers")
def test_failed_window_destruction_does_not_authorize_an_update(monkeypatch: pytest.MonkeyPatch) -> None:
    w = Closing()
    monkeypatch.setattr(w, "Destroy", lambda: False)
    w.on_close(Event())
    assert not w.destroyed


@pytest.mark.usefixtures("answers")
def test_dead_paused_copier_vetoes_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    from types import SimpleNamespace
    w = Closing()
    monkeypatch.setattr(w, "pending_update", SimpleNamespace(process=SimpleNamespace(poll=lambda: 1)), raising=False)
    event = Event()
    w.on_close(event)
    assert event.vetoed and not w.gone and not w.destroyed


@pytest.mark.usefixtures("answers")
def test_the_close_says_why_when_it_isn_t_the_player() -> None:
    w = Closing()
    w.close_reason = "the update to 9.9 is ready: the updater takes over"
    w.on_close(Event())
    assert "close requested (the update to 9.9 is ready" in text("teardown.log")
    Closing().on_close(Event(can_veto=False))
    assert "close requested (Windows is ending the session)" in text("teardown.log")


def nothing(self: object) -> None:
    pass


def test_first_run_ends_the_start_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(updates.UpdatesMixin, "after_update", nothing)
    monkeypatch.setattr(editing.EditingMixin, "check_game", nothing)
    applog.bootup.begin("x")
    Closing().first_run()
    log = text("bootup.log")
    assert "update result and check: done" in log and "game folder and agent: done" in log
    assert lines("bootup.log")[-1].split("] ", 1)[1].startswith("END startup: ready in")


def test_a_failed_first_check_is_written_and_the_start_isn_t_called_clean(monkeypatch: pytest.MonkeyPatch) -> None:
    def broken(self: object) -> None:
        raise OSError("no network")
    checked: list[bool] = []

    def check(self: object) -> None:
        checked.append(True)
    monkeypatch.setattr(updates.UpdatesMixin, "after_update", broken)
    monkeypatch.setattr(editing.EditingMixin, "check_game", check)
    applog.bootup.begin("x")
    Closing().first_run()
    log = text("bootup.log")
    assert "update result and check: FAILED" in log and "OSError: no network" in log
    assert checked == [True]                                   # the agent check still ran
    assert "END startup: ready in" in log and "with 1 problem: update result and check" in log
    assert "WARNING" in lines("bootup.log")[-1]
