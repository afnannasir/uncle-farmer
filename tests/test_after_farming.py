# pyright: reportPrivateUsage=false
# White-box tests: they read the run's after_farming_done flag and use test_runner's stand-ins.
"""Character settings' after-farming jobs: Sell shards now and Clean the bag now, each run by
itself after a farming run that finished (when ticked), one after the other, once, and never
after Stop, a tool or anything that wasn't the end of farming. The Tools menu still runs both by
hand, whatever the settings say."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from conftest import stand_in
from test_runner import FakeRunner, FarmingRunner, bot_of, make
from unclefarmer import settings, stats
from unclefarmer.engine import runner as R, town
from unclefarmer.engine.bot import Stopped


class Jobs:
    """town's two jobs, as stand-ins: the order they ran in, what they raise, what each saw."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ran: list[str] = []
        self.raises: dict[str, BaseException] = {}
        self.doing: list[str] = []
        monkeypatch.setattr(town, "sell_shards_now", stand_in(town.sell_shards_now, lambda b: self.job("sell", b)))
        monkeypatch.setattr(town, "clean_bag", stand_in(town.clean_bag, lambda b: self.job("clean", b)))
        def doing(bot: Any, name: str) -> Any:
            self.doing.append(name)
            return contextlib.nullcontext()
        monkeypatch.setattr(stats, "doing", doing)

    def job(self, name: str, b: Any) -> int:
        self.ran.append(name)
        if name in self.raises:
            raise self.raises[name]
        return 1


def farming_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, sell: bool, clean: bool,
                runner: type[FakeRunner] = FarmingRunner, **kw: Any) -> tuple[R.Run, list[tuple[str, bool]], Jobs]:
    run, _links, logs, _done = make(monkeypatch, tmp_path, runner=runner, **kw)
    from unclefarmer import scan as scan_mod
    profile = {"sell_shards_after_farming": sell, "clean_bag_after_farming": clean}
    monkeypatch.setattr(scan_mod, "scan_and_save",
                        stand_in(scan_mod.scan_and_save, lambda link, character, game_folder="": (dict(profile), {})))
    return run, logs, Jobs(monkeypatch)


def said(logs: list[tuple[str, bool]], text: str) -> int:
    return sum(text in line for line, _speak in logs)


@pytest.mark.parametrize("sell, clean, want", [
    (False, False, []), (True, False, ["sell"]), (False, True, ["clean"]), (True, True, ["sell", "clean"])])
def test_each_ticked_job_runs_once_after_farming_in_order(monkeypatch: pytest.MonkeyPatch, tmp_path: Path,
                                                          sell: bool, clean: bool, want: list[str]) -> None:
    run, logs, jobs = farming_run(monkeypatch, tmp_path, sell, clean)
    run.run()
    assert jobs.ran == want
    assert jobs.doing == [{"sell": "sell_shards", "clean": "clean_bag"}[j] for j in want]   # results apart
    assert said(logs, "after farming, automatically: selling shards") == (1 if sell else 0)
    assert said(logs, "after farming, automatically: cleaning the bag") == (1 if clean else 0)
    run._after_farming()                                   # asked again: nothing runs twice
    assert jobs.ran == want


def test_the_jobs_come_before_the_results_close_and_the_game_is_closed(monkeypatch: pytest.MonkeyPatch,
                                                                       tmp_path: Path) -> None:
    run, logs, jobs = farming_run(monkeypatch, tmp_path, True, True, app={"after_stop": "close_game"})
    order: list[str] = []
    jobs_job = jobs.job

    def noting(name: str, b: Any) -> int:
        order.append(name)
        return jobs_job(name, b)
    jobs.job = noting                                      # type: ignore[method-assign]
    real_end = run._end

    def end(app: dict[str, Any], reason: str) -> None:
        order.append("end")
        real_end(app, reason)
    run._end = end                                         # type: ignore[method-assign]
    run.run()
    assert order == ["sell", "clean", "end"]
    assert said(logs, "farming is over - closing the game") == 1


@pytest.mark.usefixtures("allow_ignored_failures")      # a job's bug is logged and the next job runs
def test_a_failing_job_still_lets_the_other_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, logs, jobs = farming_run(monkeypatch, tmp_path, True, True)
    jobs.raises["sell"] = RuntimeError("market menu changed")
    run.run()
    assert jobs.ran == ["sell", "clean"]
    assert said(logs, "selling shards didn't finish") == 1
    jobs2_run, logs2, jobs2 = farming_run(monkeypatch, tmp_path, True, True)
    jobs2.raises["clean"] = Stopped("stuck")
    jobs2_run.run()
    assert jobs2.ran == ["sell", "clean"] and said(logs2, "cleaning the bag didn't finish: stuck") == 1


def test_a_lost_game_ends_the_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from unclefarmer.link import AgentError
    run, _logs, jobs = farming_run(monkeypatch, tmp_path, True, True)
    jobs.raises["sell"] = AgentError("the game closed")
    run.run()
    assert jobs.ran == ["sell"]                            # nothing more can be done in a game that is gone


def test_stop_during_the_jobs_ends_them(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _logs, jobs = farming_run(monkeypatch, tmp_path, True, True)

    def stopping(name: str, b: Any) -> int:
        jobs.ran.append(name)
        run.stop_event.set()                               # the player pressed Stop meanwhile
        raise Stopped("stopped")
    jobs.job = stopping                                    # type: ignore[method-assign]
    run.run()
    assert jobs.ran == ["sell"]


def test_nothing_runs_after_a_tool_or_a_run_that_never_farmed(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _logs, jobs = farming_run(monkeypatch, tmp_path, True, True, runner=FakeRunner)
    run.run()                                              # it finished, but nothing was farmed
    assert jobs.ran == [] and run.ended_by_itself is True
    tool, _logs2, jobs2 = farming_run(monkeypatch, tmp_path, True, True)
    tool.kind = "errand"                                   # a Tools menu job (Sell shards now itself)
    tool.run()
    assert jobs2.ran == []


def test_nothing_runs_after_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class StoppedRunner(FarmingRunner):
        def run(self) -> None:
            super().run()
            self.bot.stop_event.set()                      # the player's Stop
            raise Stopped("stopped")
    run, _logs, jobs = farming_run(monkeypatch, tmp_path, True, True, runner=StoppedRunner)
    run.run()
    assert run.ended_by_itself is False and jobs.ran == []


def test_nothing_runs_when_called_away_or_the_walk_ends(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class CalledAway(FarmingRunner):
        def run(self) -> None:
            super().run()
            e = Stopped("Ali asked us to come")
            e.asked = True                                 # type: ignore[attr-defined]
            raise e
    run, _logs, jobs = farming_run(monkeypatch, tmp_path, True, True, runner=CalledAway)
    run.run()
    assert jobs.ran == []
    walk, _logs2, jobs2 = farming_run(monkeypatch, tmp_path, True, True, walk=True)
    walk.run()
    assert jobs2.ran == []


def test_a_stop_rule_ending_farming_counts_as_its_end(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class DropTimeUsed(FarmingRunner):
        def run(self) -> None:
            super().run()
            raise Stopped("the day's drop time is used up")
    run, _logs, jobs = farming_run(monkeypatch, tmp_path, True, False, runner=DropTimeUsed)
    run.run()
    assert run.ended_by_itself is True and bot_of(run).defend_calls >= 1
    assert jobs.ran == ["sell"]


# ---------------------------------------------------------------- the settings themselves
def test_both_settings_are_off_for_every_existing_character() -> None:
    p = settings.load_profile("Nobody Yet")
    assert p["sell_shards_after_farming"] is False and p["clean_bag_after_farming"] is False


def test_each_setting_is_kept_on_its_own_after_a_restart() -> None:
    p = settings.load_profile("Hero")
    p["sell_shards_after_farming"] = True
    settings.save_profile("Hero", p)
    again = settings.load_profile("Hero")                 # read from the file, as the next start does
    assert again["sell_shards_after_farming"] is True and again["clean_bag_after_farming"] is False
    again["clean_bag_after_farming"] = True
    again["sell_shards_after_farming"] = False
    settings.save_profile("Hero", again)
    last = settings.load_profile("Hero")
    assert last["sell_shards_after_farming"] is False and last["clean_bag_after_farming"] is True


def test_a_setting_of_the_wrong_kind_falls_back_to_off(tmp_path: Path) -> None:
    from unclefarmer import jsonio, paths
    import os
    jsonio.save(os.path.join(paths.PROFILES, "Odd.json"), {"sell_shards_after_farming": "yes please"})
    p = settings.load_profile("Odd")
    assert p["sell_shards_after_farming"] is False and p["clean_bag_after_farming"] is False


# ---------------------------------------------------------------- the Tools menu, by hand
@pytest.mark.parametrize("ticked", [False, True])
def test_the_tools_menu_runs_both_by_hand_whatever_the_settings_say(monkeypatch: pytest.MonkeyPatch, ticked: bool) -> None:
    pytest.importorskip("wx")
    from unclefarmer.gui.mainparts import editing
    p = settings.load_profile("Hero")
    p.update(sell_shards_after_farming=ticked, clean_bag_after_farming=ticked,
             bag_rules={"old boots": {"action": "throw", "keep": 0}})
    settings.save_profile("Hero", p)
    errands: list[tuple[str, str]] = []
    lines: list[str] = []

    class Window:
        def run_errand(self, text: str, title: str, walk: bool = False) -> None:
            errands.append((text, title))

        def log(self, line: str, speak: bool = False, who: str | None = None, private: bool = False) -> None:
            lines.append(line)

        def snapshot(self) -> dict[str, Any]:
            return {"self": {"name": "Hero"}}

        def selected_pid(self) -> int:
            return 1

        runs: dict[int, Any] = {}

        def say(self, text: str) -> None:
            lines.append(text)

    def yes(parent: object, text: str, title: str | None = None) -> bool:
        return True
    monkeypatch.setattr(editing, "ask", yes)
    w: Any = Window()
    editing.EditingMixin.sell_shards_now(w)
    editing.EditingMixin.clean_bag_now(w)
    assert errands == [("sell_shards\n", "Selling shards"), ("clean_bag\n", "Cleaning the bag")]
    assert "Sell shards now: started by hand" in lines and "Clean the bag now: started by hand" in lines
    after = settings.load_profile("Hero")                 # running by hand changes no setting
    assert after["sell_shards_after_farming"] is ticked and after["clean_bag_after_farming"] is ticked
