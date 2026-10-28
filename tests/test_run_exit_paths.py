"""Every way a run can end must leave the game safe, whatever the reason.

test_runner.py checks the individual endings. This file checks what they have in common, as
invariants that must hold on EVERY exit path, so a new `except` branch or a refactor of Run.run()
can't quietly skip a cleanup step:

  * the connection to the game is closed;
  * the keys this run held are let go (its own, never another controller's);
  * speech the bot muted is switched back on, unless the player chose otherwise;
  * the after-stop action (close the game) happens only when a farming run ended by itself;
  * the player who asked for the run is told why it stopped (unless they pressed Stop);
  * a failing cleanup step is logged and never takes the run thread down.
"""

from __future__ import annotations

from typing import Any, NoReturn

import pytest
from test_runner import FakeBot, FakeLink, FakeRunner, FakeTally, FarmingRunner, bot_of, make
from conftest import as_kind

from unclefarmer.engine import script as script_mod
from unclefarmer.engine.bot import Stopped
from unclefarmer.link import AgentError
from pathlib import Path
from unclefarmer.engine.botparts.parts import Parts


class Raises(FakeRunner):
    """A script runner that ends the run by raising `exc`."""

    exc: BaseException = RuntimeError("never raised")

    def run(self) -> None:
        super().run()
        raise self.exc


def raising(exc: BaseException) -> type[Raises]:
    class Raising(Raises):
        pass
    Raising.exc = exc
    return Raising


class LetGoBot(FakeBot):
    """A bot that has let_go(): the run must prefer it to the blunt `keysup`."""

    def __init__(self, *a: Any, **k: Any) -> None:
        super().__init__(*a, **k)
        self.let_go_calls = 0

    def let_go(self) -> None:
        self.let_go_calls += 1


STAY = Stopped("called away")
STAY.stay = True
ENDINGS: dict[str, type[FakeRunner]] = {
    "finished": FarmingRunner,
    "stopped": raising(Stopped("a stop rule")),
    "stopped-and-stay": raising(STAY),
    "script-error": raising(script_mod.ScriptError(2, "bad")),
    "agent-lost": raising(AgentError("closed")),
    "agent-missing": raising(AgentError("no_game")),
    "os-error": raising(OSError("pipe broke")),
    "bug": raising(ValueError("a bug in the bot")),
}


@pytest.mark.parametrize("ending", ENDINGS)
def test_every_ending_closes_the_link_and_lets_go_of_the_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ending: str, allow_ignored_failures: None):
    run, links, _, _done = make(monkeypatch, tmp_path, runner=ENDINGS[ending])
    run.run()
    assert links[0].closed is True
    assert "keysup" in links[0].sent       # FakeBot has no let_go: the blunt release is the fallback


@pytest.mark.parametrize("ending", ENDINGS)
def test_every_ending_prefers_letting_go_of_only_its_own_keys(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ending: str,
                                                              allow_ignored_failures: None):
    from unclefarmer.engine import runner as R
    run, links, _, _ = make(monkeypatch, tmp_path, runner=ENDINGS[ending])
    monkeypatch.setattr(R, "Bot", LetGoBot)
    run.run()
    assert as_kind(LetGoBot, run.bot).let_go_calls == 1
    assert "keysup" not in links[0].sent   # a `keysup` could drop another controller's keys


@pytest.mark.parametrize("ending", ENDINGS)
def test_every_ending_turns_the_game_speech_back_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ending: str, allow_ignored_failures: None):
    run, links, _, _ = make(monkeypatch, tmp_path, runner=ENDINGS[ending], app={"game_speech": "running"})
    run.run()
    sent = links[0].sent
    assert "speech off" in sent
    assert sent.index("speech on") > sent.index("speech off")


def test_speech_the_player_switched_off_by_hand_stays_off(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, links, _, _ = make(monkeypatch, tmp_path, app={"game_speech": "running"})
    run.speech_choice = "off"
    run.run()
    assert "speech on" not in links[0].sent


# ---------------------------------------------------------------- the after-stop action
@pytest.mark.parametrize("ending,closes", [
    ("finished", True),             # farming ran out its plan: close
    ("stopped", True),              # a stop rule fired by itself: still the end of farming
    ("stopped-and-stay", False),    # called away by a player: not the end of farming
    ("agent-lost", False),          # the game is gone or broken: nothing to close
    ("os-error", False),
])
def test_after_stop_only_closes_the_game_when_farming_ended_by_itself(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, ending: str, closes: bool):
    class Farm(ENDINGS[ending]):
        def run(self) -> None:
            self.bot.farmed = True
            super().run()

    run, links, _, _ = make(monkeypatch, tmp_path, runner=Farm, app={"after_stop": "close_game"})
    run.run()
    assert ("closegame" in links[0].sent) is closes


def test_pressing_stop_never_closes_the_game(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class StopPressed(FarmingRunner):
        def run(self) -> None:
            super().run()
            self.bot.stop_event.set()
            raise Stopped("stopped by the player")

    run, links, _, _ = make(monkeypatch, tmp_path, runner=StopPressed, app={"after_stop": "close_game"})
    run.run()
    assert run.ended_by_itself is False
    assert "closegame" not in links[0].sent


@pytest.mark.parametrize("setting", ["nothing", "", "garbage"])
def test_after_stop_nothing_means_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, setting: str):
    run, links, _, _ = make(monkeypatch, tmp_path, runner=FarmingRunner, app={"after_stop": setting})
    run.run()
    assert "closegame" not in links[0].sent


def test_a_shutdown_closes_the_game_too_and_arms_the_agent_first(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, links, _, _ = make(monkeypatch, tmp_path, runner=FarmingRunner, app={"after_stop": "shutdown"})
    run.run()
    sent = links[0].sent
    assert sent[-2:] == ["arm", "closegame"]       # the agent is armed right before the game is closed


# ---------------------------------------------------------------- guarding after an early stop
def test_an_early_stop_leaves_the_character_somewhere_safe(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, _, _, _ = make(monkeypatch, tmp_path, runner=raising(Stopped("stuck")))
    run.run()
    assert run.bot is not None
    assert bot_of(run).defend_calls == 1 and bot_of(run).finish_calls == 1


def test_being_called_away_guards_but_stays_where_it_is(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, _, _, _ = make(monkeypatch, tmp_path, runner=raising(STAY))
    run.run()
    assert run.bot is not None
    assert bot_of(run).defend_calls == 1 and bot_of(run).finish_calls == 0


def test_a_bug_still_guards_the_character(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allow_ignored_failures: None):
    # "a bug is no reason to stand still while monsters hit us"
    run, _, logs, _done = make(monkeypatch, tmp_path, runner=raising(ValueError("boom")))
    run.run()
    assert run.bot is not None
    assert bot_of(run).defend_calls == 1
    assert any("unexpected error" in line for line, _ in logs)


def test_the_walking_assistant_is_never_guarded(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, _, _, _ = make(monkeypatch, tmp_path, walk=True, runner=raising(Stopped("stuck")))
    run.run()
    assert run.bot is not None
    assert bot_of(run).defend_calls == 0 and bot_of(run).finish_calls == 0


# ---------------------------------------------------------------- telling the player
def test_the_player_who_asked_is_told_why_the_run_stopped(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, _, _, _ = make(monkeypatch, tmp_path, runner=raising(Stopped("lost sight of them")))
    told: list[str] = []
    run.tell = told.append
    run.run()
    assert told == ["Stopped: lost sight of them"]


def test_nobody_is_told_after_pressing_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    class StopPressed(FakeRunner):
        def run(self) -> None:
            self.bot.stop_event.set()
            raise Stopped("stopped by the player")

    run, _, _, _ = make(monkeypatch, tmp_path, runner=StopPressed)
    told: list[str] = []
    run.tell = told.append
    run.run()
    assert told == []


def test_nobody_is_told_about_a_normal_arrival(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    run, _, _, _ = make(monkeypatch, tmp_path, walk=True)
    told: list[str] = []
    run.tell = told.append
    run.run()
    assert told == []


def test_a_failing_tell_does_not_kill_the_run_thread(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allow_ignored_failures: None):
    run, links, _, _ = make(monkeypatch, tmp_path, runner=raising(Stopped("x")))

    def broken(_line: str) -> NoReturn:
        raise RuntimeError("the private message failed")

    run.tell = broken
    run.run()                                      # must not raise
    assert links[0].closed is True


# ---------------------------------------------------------------- cleanup that itself fails
def test_a_dead_link_during_cleanup_is_logged_and_the_link_still_closed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    from unclefarmer.engine import runner as R
    run, links, logs, _ = make(monkeypatch, tmp_path, runner=FarmingRunner, app={"after_stop": "close_game"})
    make_link = R.AgentLink

    def dying_link(pid: int | None = None, **kw: object) -> FakeLink:
        link = as_kind(FakeLink, make_link(pid))
        real_send = link.send

        def send(line: str) -> str:
            if line == "keysup":
                link.sent.append(line)
                raise OSError("the game exited")
            return real_send(line)

        link.send = send
        return link

    monkeypatch.setattr(R, "AgentLink", dying_link)
    run.run()                                      # must not raise
    assert any("couldn't finish at the game's end" in line for line, _ in logs)
    assert links[0].closed is True


def test_results_are_saved_even_when_finishing_them_fails_twice(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, allow_ignored_failures: None):
    from unclefarmer import stats
    run, _, logs, _ = make(monkeypatch, tmp_path, runner=FarmingRunner)

    class BrokenTally(FakeTally):
        saved = False

        def finish(self, bot: Parts | None=None) -> None:
            raise RuntimeError("bad sheet")

        def save(self) -> None:
            type(self).saved = True

    monkeypatch.setattr(stats, "Tally", BrokenTally)
    run.run()
    assert any("couldn't finish the results" in line for line, _ in logs)
    assert BrokenTally.saved is True               # the player still gets what the run earned
