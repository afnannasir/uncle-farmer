"""A game lost in the middle of a run is opened again and the run carried on (gui/mainparts/carryon.py
revive), at most resume.RESTARTS_MOST times a game day; and a game opened for logging in is waited
for while it starts, instead of a second one beside it (runs.starting_games)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any, cast

import pytest

pytest.importorskip("wx")

from conftest import Clock, as_bot, returning  # noqa: E402
from test_script import FakeBot  # noqa: E402
from unclefarmer import gameinstall, settings  # noqa: E402
from unclefarmer.engine import resume, script as S  # noqa: E402
from unclefarmer.engine.runner import Run  # noqa: E402
from unclefarmer.gui import agentfix  # noqa: E402
from unclefarmer.gui.mainparts import carryon as carry_part, runs as runs_part  # noqa: E402

HERO = settings.character_profile("Hero", 1, "S1")


def quiet(*_args: object) -> None:
    pass


def lost_run(**flags: Any) -> Run:
    run = Run("farm map=5121\n", "Night", 7, quiet, quiet)
    run.kind = "plan"
    run.profile = HERO
    run.script = S.Runner(as_bot(FakeBot()), run.text)
    run.game_lost = True
    for name, value in flags.items():
        setattr(run, name, value)
    return run


class Window(SimpleNamespace):
    """The part of the main window revive() and starting_games() use."""

    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.said: list[str] = []
        self.reopened: list[tuple[str, dict[str, Any], tuple[str, str]]] = []

    def say(self, text: str, run: object = None) -> None:
        self.said.append(text)

    def _reopen(self, run: Run, key: str, row: dict[str, Any], item: tuple[str, str]) -> None:
        self.reopened.append((key, row, item))

    def back_on_line(self, run: Run, key: str) -> bool:
        return False


def window(restart: bool = True) -> Window:
    return Window(destroyed=False, killing_games=set(), s={"restart_lost_games": restart}, items=[("plan", "Night")])


@pytest.fixture
def can_log_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "automatic_login_profiles", returning([(HERO, "hero@example.com")]))


def revive(frame: Window, run: Run) -> None:
    carry_part.CarryOnMixin.revive(cast(Any, frame), run)


def test_a_lost_game_is_opened_again_with_the_runs_place(can_log_in: None) -> None:
    resume.save(HERO, {"item": "plan:Night", "text": "x", "path": [{"i": 4}]})
    frame = window()
    revive(frame, lost_run())
    assert len(frame.reopened) == 1
    key, row, item = frame.reopened[0]
    assert key == HERO and item == ("plan", "Night") and row["path"] == [{"i": 4}]
    assert "1 of 3" in frame.said[0]
    assert resume.restarts_today(HERO) == 1


def test_a_run_lost_before_its_first_save_starts_its_plan_again(can_log_in: None) -> None:
    frame = window()
    run = lost_run()
    revive(frame, run)
    _key, row, _item = frame.reopened[0]
    assert row["item"] == "plan:Night" and row["text"] == resume.fingerprint(run.text) and "path" not in row


def test_after_three_restarts_in_a_day_the_run_ends(can_log_in: None) -> None:
    for _n in range(resume.RESTARTS_MOST):
        resume.note_restart(HERO)
    resume.save(HERO, {"item": "plan:Night", "text": "x"})
    frame = window()
    revive(frame, lost_run())
    assert frame.reopened == [] and "3 times today" in frame.said[0]
    assert resume.get(HERO) is None


def test_restarts_count_again_on_a_new_game_day() -> None:
    resume.note_restart(HERO, "2026-11-21")
    assert resume.restarts_today(HERO, "2026-11-21") == 1
    assert resume.restarts_today(HERO, "2026-11-22") == 0
    assert resume.note_restart(HERO, "2026-11-22") == 1


@pytest.mark.parametrize("flags", [
    {"game_lost": False},            # it ended some other way
    {"app_closing": True},           # Uncle Farmer is closing
    {"kind": "tool"},                # a tool is started again by hand
    {"script": None},                # it never got going
])
def test_nothing_is_opened_again_when_the_run_was_not_lost_going(can_log_in: None, flags: dict[str, Any]) -> None:
    frame = window()
    revive(frame, lost_run(**flags))
    assert frame.reopened == [] and frame.said == []


def test_nothing_is_opened_again_after_stop_or_with_the_setting_off(can_log_in: None) -> None:
    run = lost_run()
    run.stop()
    frame = window()
    revive(frame, run)
    off = window(restart=False)
    revive(off, lost_run())
    assert frame.reopened == [] and off.reopened == []


@pytest.mark.parametrize("worker_finished", [False, True])
def test_an_intentional_kill_is_not_reopened(can_log_in: None, worker_finished: bool) -> None:
    resume.save(HERO, {"item": "plan:Night", "text": "x"})
    frame = window()
    run = lost_run()
    if worker_finished:
        run.stop()             # the kill completion reached the window first
    else:
        frame.killing_games = {run.pid}  # the loss notification reached it first
    revive(frame, run)
    assert frame.reopened == [] and resume.restarts_today(HERO) == 0
    assert resume.get(HERO) is None


@pytest.mark.parametrize(("updating", "enabled"), [(True, True), (False, False)])
def test_carry_on_waits_until_the_window_can_open_a_dialog(updating: bool, enabled: bool,
                                                        monkeypatch: pytest.MonkeyPatch) -> None:
    waiting: list[object] = []

    def later(delay: int, callback: object) -> None:
        waiting.append(callback)

    def rows_before_ready() -> None:
        pytest.fail("the carry-on offer should wait while another dialog or update is active")

    frame = Window(destroyed=False, updating=updating, IsEnabled=returning(enabled),
                   carry_on_rows=rows_before_ready)
    frame.offer_carry_on = carry_part.CarryOnMixin.offer_carry_on.__get__(cast(Any, frame))
    monkeypatch.setattr(carry_part.wx, "CallLater", later)
    frame.offer_carry_on()
    assert waiting == [frame.offer_carry_on]


def test_a_character_without_a_saved_account_is_only_told(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings, "automatic_login_profiles", returning([]))
    frame = window()
    revive(frame, lost_run())
    assert frame.reopened == [] and "log in to it once by hand" in frame.said[0]


# ---------------------------------------------------------------- a game being opened
def opening(opened: dict[int, float], games: list[Any], now: float) -> Any:
    return Window(opened_games=opened, games=games, clock=Clock(now))


def test_a_game_still_starting_is_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gameinstall, "running_games", returning([(51, "D:/Game")]))
    frame = opening({51: 100.0}, [], 200.0)
    assert runs_part.starting_games(frame) == [51]


def test_a_game_that_showed_its_agent_or_closed_is_no_longer_waited_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gameinstall, "running_games", returning([(51, "D:/Game")]))
    frame = opening({51: 100.0, 52: 100.0}, [(51, None, None)], 200.0)
    assert runs_part.starting_games(frame) == [] and frame.opened_games == {}


def test_a_game_that_never_starts_is_closed(monkeypatch: pytest.MonkeyPatch) -> None:
    closed: list[list[int]] = []
    monkeypatch.setattr(gameinstall, "running_games", returning([(51, "D:/Game")]))
    def close_then(pids: list[int], then: Any, *, force: bool = False) -> None:
        closed.append(pids)
    monkeypatch.setattr(agentfix, "close_then", close_then)
    frame = opening({51: 100.0}, [], 100.0 + runs_part.LOGIN_START_MOST + 1)
    assert runs_part.starting_games(frame) == []
    assert closed == [[51]] and frame.said


def test_automatic_login_waits_for_a_game_it_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gameinstall, "running_games", returning([(51, "D:/Game")]))
    frame = opening({51: 100.0}, [], 150.0)
    frame.runs, frame.pending_logins = {}, {}
    frame.refresh_games = lambda: None
    frame.open_client_for_login = lambda: pytest.fail("a second game was opened")
    pid, why = runs_part.RunsMixin.automatic_login_window(frame, HERO)
    assert pid is None and "waiting for the game opened" in why


# ---------------------------------------------------------------- a game back on line by itself
def reopening(monkeypatch: pytest.MonkeyPatch, names: dict[int, str | None]) -> tuple[Window, list[list[int]]]:
    """A window whose games list shows names (pid -> character, None: not logged in), and the
    closes asked for."""
    closed: list[list[int]] = []

    def close_then(pids: list[int], then: Any, force: bool = False) -> None:
        closed.append(list(pids))
    monkeypatch.setattr(gameinstall, "running_games", returning([(pid, "C:/Game") for pid in names]))
    monkeypatch.setattr(agentfix, "close_then", close_then)
    frame = window()
    frame.games = [(pid, name, "Margit Rainforest") for pid, name in names.items()]
    frame.refresh_games = quiet
    carried: list[Any] = []
    frame.carried = carried

    def back_on_line(run: Run, key: str) -> bool:
        return carry_part.CarryOnMixin.back_on_line(cast(Any, frame), run, key)
    frame.carry_on = carried.extend
    frame.back_on_line = back_on_line
    return frame, closed


def reopen(frame: Window, run: Run) -> None:
    carry_part.CarryOnMixin._reopen(cast(Any, frame), run, HERO, {"item": "plan:Night"}, ("plan", "Night"))  # pyright: ignore[reportPrivateUsage]


def test_a_game_that_logged_back_in_by_itself_is_carried_on_in(monkeypatch: pytest.MonkeyPatch,
                                                              can_log_in: None) -> None:
    # the connection came back as the run was ending: closing the game would throw that away
    frame, closed = reopening(monkeypatch, {7: HERO})
    revive(frame, lost_run(lost_connection=True))
    assert closed == [] and len(frame.carried) == 1 and frame.reopened == []
    assert "back on line" in frame.said[0]
    assert resume.restarts_today(HERO) == 0          # no restart: used up the day's three before


def test_a_game_still_off_line_is_closed_and_opened_again(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, closed = reopening(monkeypatch, {7: None})
    reopen(frame, lost_run(lost_connection=True))
    assert closed == [[7]] and frame.carried == []


def test_a_frozen_game_is_opened_again_whatever_it_shows(monkeypatch: pytest.MonkeyPatch) -> None:
    frame, closed = reopening(monkeypatch, {7: HERO})
    reopen(frame, lost_run(lost_connection=False))
    assert closed == [[7]] and frame.carried == []


# ---------------------------------------------------------------- the player's own games
def test_a_lost_run_carries_on_only_in_a_game_the_bot_opened(monkeypatch: pytest.MonkeyPatch) -> None:
    # the player's game at its login screen was taken, the login failed there (another account
    # being logged in by hand), and that game was closed by force to open it again
    monkeypatch.setattr(gameinstall, "running_games", returning([(51, "D:/Game")]))
    frame = opening({}, [(51, None, None)], 200.0)
    frame.runs, frame.pending_logins, frame.our_games = {}, {}, set()
    frame.refresh_games = quiet
    opened: list[str] = []
    frame.open_client_for_login = lambda: opened.append("game") or None
    pid, _why = runs_part.RunsMixin.automatic_login_window(frame, HERO, ours_only=True)
    assert pid is None and opened == ["game"]                  # a game of its own, not the player's
    frame.our_games = {51}
    assert runs_part.RunsMixin.automatic_login_window(frame, HERO, ours_only=True)[0] == 51
    frame.our_games = set()
    assert runs_part.RunsMixin.automatic_login_window(frame, HERO)[0] == 51   # a schedule: as before


def test_stop_every_bot_cancels_the_runs_waiting_to_carry_on() -> None:
    # a game the player closed by hand was opened again every minute and a half for five minutes
    resume.save(HERO, {"item": "plan:Night", "text": "x"})
    frame = window()
    frame.runs = {}
    frame.carry_queue = [(HERO, {"item": "plan:Night"}, ("plan", "Night"), 0.0)]
    def item_label(item: tuple[str, str]) -> str:
        return item[1]

    def cancel_carry_on(key: str | None = None) -> bool:
        return carry_part.CarryOnMixin.cancel_carry_on(cast(Any, frame), key)
    frame.item_label, frame.cancel_carry_on = item_label, cancel_carry_on
    runs_part.RunsMixin.stop_all(cast(Any, frame))
    assert frame.carry_queue == [] and resume.get(HERO) is None
    assert frame.said == ["Night for Hero - S1 won't carry on."]
