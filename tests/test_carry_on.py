"""Carrying on an interrupted run (engine/resume.py): the saved rows, the script's place (the line,
a loop's turn, an if's branch, a farm's progress) and the run's end deciding whether it is kept."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_script import FakeBot
from unclefarmer.engine import resume, script as S
from unclefarmer.engine.botparts.base import GameLost
from unclefarmer.engine.runner import Run

LOOP = """stop_when minutes >= 300 : time limit reached
set n = 1
repeat
  set n = n + 1
  farm map=5121 minutes=30
  farm map=5361 minutes=30 kills=100
end
"""


class Halt(Exception):
    pass


class PlaceBot(FakeBot):
    """Farms record the runner's place, and the run is cut off at the chosen farm."""

    def __init__(self, halt_at: int, **kw: Any) -> None:
        super().__init__(**kw)
        self.halt_at = halt_at
        self.runner: S.Runner | None = None
        self.place: dict[str, Any] | None = None

    def farm(self, mid: int, **kw: Any) -> None:
        super().farm(mid, **kw)
        if len([c for c in self.calls if c[0] == "farm"]) == self.halt_at:
            assert self.runner is not None
            self.kills += 7              # this farm's kills so far
            self.place = self.runner.position()
            raise Halt()


def cut_off(text: str, halt_at: int, **kw: Any) -> tuple[PlaceBot, dict[str, Any]]:
    bot = PlaceBot(halt_at, **kw)
    runner = S.Runner(as_bot(bot), text)
    bot.runner = runner
    with pytest.raises(Halt):
        runner.run()
    assert bot.place is not None
    return bot, bot.place


def farms(bot: FakeBot) -> list[tuple[int, dict[str, Any]]]:
    return [(c[1], c[2]) for c in bot.calls if c[0] == "farm"]


# ---------------------------------------------------------------- the saved rows
def test_a_row_is_saved_read_and_cleared() -> None:
    resume.save("Hero--S1--1", {"item": "plan:Night", "text": "abc", "path": [{"i": 2}]})
    row = resume.get("Hero--S1--1")
    assert row is not None and row["path"] == [{"i": 2}] and row["day"]
    resume.clear("Hero--S1--1")
    assert resume.get("Hero--S1--1") is None


def test_only_todays_rows_are_pending_and_older_ones_are_forgotten() -> None:
    resume.save("Hero--S1--1", {"item": "plan:Night", "text": "abc", "day": "2026-11-21"})
    resume.save("Bob--S1--2", {"item": "script:Loop", "text": "def", "day": "2026-11-22"})
    assert [k for k, _row in resume.pending("2026-11-22")] == ["Bob--S1--2"]
    assert resume.get("Hero--S1--1") is None


def test_item_of_reads_the_plan_or_script() -> None:
    assert resume.item_of({"item": "plan:Night farm"}) == ("plan", "Night farm")
    assert resume.item_of({"item": "tool:dungeon"}) is None
    assert resume.item_of({}) is None


# ---------------------------------------------------------------- the script's place
def test_the_place_inside_a_loop_says_the_line_the_turn_and_the_farms_progress() -> None:
    _bot, place = cut_off(LOOP, halt_at=4, kills=0)       # turn 2, its second farm
    path = place["path"]
    assert path[0]["i"] == 2 and path[0]["turn"] == 2      # the repeat, on its second turn
    assert path[1]["i"] == 2                               # the second farm of the turn
    assert path[1]["farm_kills"] == 7 and path[1]["farm_minutes"] >= 0
    assert place["vars"]["n"] == 3 and place["kills"] == 7


def test_carrying_on_goes_straight_to_the_saved_farm_with_what_is_left() -> None:
    _bot, place = cut_off(LOOP, halt_at=4, kills=0)
    place["path"][1]["farm_minutes"] = 12.0
    place["minutes"] = 95.0
    bot = FakeBot(kills=0)
    runner = S.Runner(as_bot(bot), LOOP.replace("repeat", "repeat 2"))
    runner.resume_from(place)
    runner.run()
    done = farms(bot)
    assert done[0][0] == 5361                               # no Wolf Prairie first
    assert done[0][1]["minutes"] == pytest.approx(18.0)     # 30 less the 12 already farmed
    assert done[0][1]["kills"] == 93                        # 100 less the 7 already made
    assert len(done) == 1                                   # it was turn 2 of 2
    assert runner.vars["n"] == 3                            # the set lines on the way were not run again
    assert bot.watches                                      # ... but the time limit was
    assert runner.names()["minutes"] == pytest.approx(95.0)


def test_a_farm_already_done_when_carried_on_is_left_out() -> None:
    _bot, place = cut_off(LOOP, halt_at=1, kills=0)
    place["path"][-1]["farm_minutes"] = 31.0
    bot = FakeBot()
    runner = S.Runner(as_bot(bot), LOOP.replace("repeat", "repeat 1"))
    runner.resume_from(place)
    runner.run()
    assert [mid for mid, _kw in farms(bot)] == [5361]


def test_carrying_on_inside_an_if_keeps_the_branch_it_had_taken() -> None:
    text = "if kills >= 1000\n  farm map=5121\nelse\n  farm map=5361 minutes=20\n  farm map=5281\nend\n"
    _bot, place = cut_off(text, halt_at=1)
    assert place["path"][0]["branch"] == 1
    bot = FakeBot(kills=5000)          # the condition says otherwise now: the branch stays
    runner = S.Runner(as_bot(bot), text)
    runner.resume_from(place)
    runner.run()
    assert [mid for mid, _kw in farms(bot)] == [5361, 5281]


def test_no_place_while_nothing_runs() -> None:
    assert S.Runner(as_bot(FakeBot()), LOOP).position() is None


# ---------------------------------------------------------------- the run's end
def quiet(*_args: object) -> None:
    pass


def make_run(kind: str = "plan") -> Run:
    run = Run("farm map=5121\n", "Night", 1, quiet, quiet)
    run.kind = kind
    run.profile = "Hero--S1--1"
    run.script = S.Runner(as_bot(FakeBot()), run.text)
    resume.save("Hero--S1--1", {"item": "plan:Night", "text": resume.fingerprint(run.text)})
    return run


@pytest.mark.parametrize(("lost", "closing", "by_itself", "kept"), [
    (True, False, False, True),       # the game was lost
    (False, True, False, True),       # Uncle Farmer closed under it
    (False, False, False, False),     # Stop, or a failure that carrying on would repeat
    (False, False, True, False),      # it finished
    (True, False, True, False),
])
def test_the_place_is_kept_only_when_the_run_did_not_end(lost: bool, closing: bool, by_itself: bool,
                                                          kept: bool) -> None:
    run = make_run()
    run.game_lost, run.app_closing, run.ended_by_itself = lost, closing, by_itself
    run._settle_place()          # pyright: ignore[reportPrivateUsage]
    assert (resume.get("Hero--S1--1") is not None) is kept
    run.save_place()             # nothing is saved once the end has decided
    assert (resume.get("Hero--S1--1") is not None) is kept


def test_tools_and_errands_keep_no_place() -> None:
    assert not make_run("tool").keeps_place()
    assert make_run("script").keeps_place()


def test_an_edited_plan_starts_from_its_beginning() -> None:
    run = make_run()
    run.resume_row = {"text": "another version", "path": [{"i": 3}]}
    script = S.Runner(as_bot(FakeBot()), run.text)
    run._take_place(script)      # pyright: ignore[reportPrivateUsage]
    assert run.resume_row is None and script.ahead == []


def test_a_lost_game_is_not_guarded_and_keeps_its_place() -> None:
    run = make_run()
    reason = run._stopped(GameLost("the game can't be read: noworld: gone"))   # pyright: ignore[reportPrivateUsage]
    assert run.game_lost and run.failed and not run.ended_by_itself
    assert "noworld" in reason
