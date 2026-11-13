# pyright: reportPrivateUsage=false
# White-box tests: they call the run's before/after stages and use test_runner's stand-ins.
"""Character settings, Triggers: six Tools jobs (Sell shards now, Clean the bag now, Update shard
prices, Gathering, Store items now, Restock now), each with its own tick before farming and after farming. The
two ticks are independent: a job ticked at both runs once before and once after. The jobs run one
after the other in a fixed order, the same code the Tools menu runs, and never for a run that isn't
farming."""

from __future__ import annotations

import contextlib
from pathlib import Path
from typing import Any

import pytest

from conftest import as_bot, stand_in
from test_runner import FakeRunner, FarmingRunner, bot_of, make
from unclefarmer import settings, stats
from unclefarmer.engine import gathering, runner as R, script as script_mod, town, triggers
from unclefarmer.engine.bot import Stopped
from unclefarmer.link import AgentError

ORDER = ["sell_shards", "clean_bag", "update_shard_prices", "gather", "store_items", "restock"]


class Jobs:
    """The jobs as stand-ins: what ran (with the stage it ran in), what each raises."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.ran: list[str] = []
        self.raises: dict[str, BaseException] = {}
        self.doing: list[str] = []
        self.stage = ""
        for job, fn in triggers.TOWN_JOBS.items():
            monkeypatch.setattr(town, fn, self.fake(job))
        monkeypatch.setattr(gathering, "run", stand_in(gathering.run, lambda b, cfg, check_only=False: self.job("gather")))

        def doing(bot: Any, name: str) -> Any:
            self.doing.append(name)
            return contextlib.nullcontext()
        monkeypatch.setattr(stats, "doing", doing)

    def fake(self, job: str) -> Any:
        """A stand-in for the town function of that job."""
        def run(b: Any) -> Any:
            return self.job(job)
        return run

    def job(self, name: str) -> Any:
        self.ran.append("%s:%s" % (self.stage, name) if self.stage else name)
        if name in self.raises:
            raise self.raises[name]
        return 1


class MarkingRunner(FarmingRunner):
    """Farming that notes in the jobs' list where the plan itself ran."""
    jobs: Jobs | None = None

    def run(self) -> None:
        assert MarkingRunner.jobs is not None
        MarkingRunner.jobs.ran.append("FARMING")
        super().run()


def triggered(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, profile: dict[str, bool],
              runner: type[FakeRunner] = MarkingRunner, **kw: Any) -> tuple[R.Run, list[tuple[str, bool]], Jobs]:
    run, _links, logs, _done = make(monkeypatch, tmp_path, runner=runner, **kw)
    from unclefarmer import scan as scan_mod
    monkeypatch.setattr(scan_mod, "scan_and_save",
                        stand_in(scan_mod.scan_and_save, lambda link, character, game_folder="": (dict(profile), {})))
    jobs = Jobs(monkeypatch)
    MarkingRunner.jobs = jobs
    return run, logs, jobs


def ticks(before: list[str] = [], after: list[str] = []) -> dict[str, bool]:     # noqa: B006 (read only)
    p = {triggers.setting(j, "before"): j in before for j in ORDER}
    p.update({triggers.setting(j, "after"): j in after for j in ORDER})
    return p


def said(logs: list[tuple[str, bool]], text: str) -> int:
    return sum(text in line for line, _speak in logs)


# ---------------------------------------------------------------- the settings
def test_every_job_has_two_independent_settings_off_by_default() -> None:
    p = settings.load_profile("Nobody Yet")
    for job in ORDER:
        for stage in triggers.STAGES:
            assert p[triggers.setting(job, stage)] is False
    assert len(triggers.SETTINGS) == 2 * len(triggers.JOBS) == len(set(triggers.SETTINGS))
    assert "sell_shards_after_farming" in triggers.SETTINGS        # the old names kept: nothing to move


@pytest.mark.parametrize("job", ORDER)
def test_both_ticks_of_a_job_stay_on_together_after_a_restart(job: str) -> None:
    p = settings.load_profile("Hero")
    p[triggers.setting(job, "before")] = True
    settings.save_profile("Hero", p)
    again = settings.load_profile("Hero")
    assert again[triggers.setting(job, "before")] is True and again[triggers.setting(job, "after")] is False
    again[triggers.setting(job, "after")] = True                    # ticking after leaves before on
    settings.save_profile("Hero", again)
    last = settings.load_profile("Hero")
    assert last[triggers.setting(job, "before")] is True and last[triggers.setting(job, "after")] is True
    last[triggers.setting(job, "before")] = False                   # and unticking one leaves the other
    settings.save_profile("Hero", last)
    final = settings.load_profile("Hero")
    assert final[triggers.setting(job, "before")] is False and final[triggers.setting(job, "after")] is True
    others = [k for k in triggers.SETTINGS if not k.startswith(job + "_")]
    assert not any(final[k] for k in others)                        # nothing else changed


def test_an_old_profile_keeps_its_after_farming_ticks() -> None:
    from unclefarmer import jsonio, paths
    import os
    jsonio.save(os.path.join(paths.PROFILES, "Old.json"), {"sell_shards_after_farming": True,
                                                            "clean_bag_after_farming": True})
    p = settings.load_profile("Old")
    assert p["sell_shards_after_farming"] is True and p["clean_bag_after_farming"] is True
    assert triggers.ticked(p, "after") == ["sell_shards", "clean_bag"] and triggers.ticked(p, "before") == []


def test_a_setting_of_the_wrong_kind_is_off() -> None:
    from unclefarmer import jsonio, paths
    import os
    jsonio.save(os.path.join(paths.PROFILES, "Odd.json"), {"gather_before_farming": "yes please",
                                                            "store_items_after_farming": [1]})
    p = settings.load_profile("Odd")
    assert triggers.ticked(p, "before") == [] and triggers.ticked(p, "after") == []


# ---------------------------------------------------------------- one stage
@pytest.mark.parametrize("job", ORDER)
def test_each_job_runs_before_farming_when_ticked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, job: str) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=[job]))
    run.run()
    assert jobs.ran == [job, "FARMING"]
    assert jobs.doing == [job]                                      # its own results part
    assert said(logs, "before farming, automatically: " + triggers.what(job)) == 1


@pytest.mark.parametrize("job", ORDER)
def test_each_job_runs_after_farming_when_ticked(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, job: str) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks(after=[job]))
    run.run()
    assert jobs.ran == ["FARMING", job]
    assert said(logs, "after farming, automatically: " + triggers.what(job)) == 1


def test_all_ticked_run_in_order_at_both_stages(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=ORDER, after=ORDER))
    run.run()
    assert jobs.ran == ORDER + ["FARMING"] + ORDER                 # once before, once after, each
    assert said(logs, "before farming: done selling shards, cleaning the bag, updating shard prices, "
                      "gathering, storing items") == 1
    assert said(logs, "after farming: done selling shards") == 1
    run._before_farming()                                           # asked again: nothing twice
    run._after_farming()
    assert jobs.ran == ORDER + ["FARMING"] + ORDER


def test_the_same_job_at_both_stages_and_another_only_after(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["store_items"], after=["store_items", "sell_shards"]))
    run.run()
    assert jobs.ran == ["store_items", "FARMING", "sell_shards", "store_items"]


def test_nothing_ticked_runs_nothing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks())
    run.run()
    assert jobs.ran == ["FARMING"] and said(logs, "automatically") == 0


# ---------------------------------------------------------------- which runs
def test_no_before_farming_for_a_tool_a_walk_or_a_script_that_doesnt_farm(monkeypatch: pytest.MonkeyPatch,
                                                                          tmp_path: Path) -> None:
    tool, _l, jobs = triggered(monkeypatch, tmp_path, ticks(before=ORDER, after=ORDER))
    tool.kind = "errand"                                            # Tools, Sell shards now itself
    tool.run()
    assert jobs.ran == ["FARMING"]
    walk, _l2, jobs2 = triggered(monkeypatch, tmp_path, ticks(before=ORDER, after=ORDER), walk=True)
    walk.run()
    assert jobs2.ran == ["FARMING"]
    idle, _l3, jobs3 = triggered(monkeypatch, tmp_path, ticks(before=ORDER, after=ORDER), runner=FakeRunner)
    idle.run()                                                      # a script with no farm command
    assert jobs3.ran == []


def test_no_after_farming_after_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    class StoppedRunner(MarkingRunner):
        def run(self) -> None:
            super().run()
            self.bot.stop_event.set()
            raise Stopped("stopped")
    run, _logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["sell_shards"], after=["sell_shards"]),
                                 runner=StoppedRunner)
    run.run()
    assert jobs.ran == ["sell_shards", "FARMING"]                   # before ran, after never did


def test_a_script_farms_when_a_farm_or_dungeon_line_is_anywhere_in_it() -> None:
    def farms(text: str) -> bool:
        main, subs, _stop = script_mod.parse(text)
        return triggers.farms(main, subs)
    assert farms("farm map=\"Wolf Prairie\" minutes=30\n")
    assert farms("repeat 3\n  if 1 > 0\n    wait 5\n  else\n    farm minutes=10\n  end\nend\n")
    assert farms("sub hunt\n  farm minutes=5\nend\ncall hunt\n")
    assert farms("dungeon\n")
    assert not farms("sell_shards\nclean_bag\nwait 2\n")
    assert not farms("travel 1041\nsay done\n")


# ---------------------------------------------------------------- failures
@pytest.mark.usefixtures("allow_ignored_failures")
def test_a_failing_preparation_is_said_and_farming_still_starts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["sell_shards", "clean_bag", "store_items"]))
    jobs.raises["sell_shards"] = Stopped("no Shard Market can be reached")
    jobs.raises["clean_bag"] = RuntimeError("menu changed")
    run.run()
    assert jobs.ran == ["sell_shards", "clean_bag", "store_items", "FARMING"]
    assert any("selling shards didn't finish: no Shard Market can be reached" in line and speak for line, speak in logs)
    assert said(logs, "cleaning the bag didn't finish") == 1
    assert said(logs, "before farming: done storing items; didn't finish selling shards, cleaning the bag") == 1


def test_stop_during_preparation_ends_the_run_before_farming(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["sell_shards", "clean_bag"], after=ORDER))

    def stopping(name: str) -> int:
        jobs.ran.append(name)
        run.stop_event.set()
        raise Stopped("stopped")
    jobs.job = stopping                                             # type: ignore[method-assign]
    run.run()
    assert jobs.ran == ["sell_shards"]                              # no next job, no farming, no after
    assert not getattr(bot_of(run), "ran", False)


def test_a_lost_game_during_preparation_ends_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["update_shard_prices", "gather"]))
    jobs.raises["update_shard_prices"] = AgentError("the game closed")
    run.run()
    assert jobs.ran == ["update_shard_prices"]
    assert said(logs, "lost the connection to the game") >= 1


def test_a_final_stop_from_a_preparation_ends_the_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["gather", "store_items"]))
    e = Stopped("keys refused")
    e.final = True                                                  # type: ignore[attr-defined]
    jobs.raises["gather"] = e
    run.run()
    assert jobs.ran == ["gather"]


# ---------------------------------------------------------------- one implementation
@pytest.mark.parametrize("job", ORDER)
def test_the_script_command_runs_the_same_job(monkeypatch: pytest.MonkeyPatch, job: str) -> None:
    jobs = Jobs(monkeypatch)
    from test_script import FakeBot as ScriptBot
    b = ScriptBot()
    script_mod.Runner(as_bot(b), job + "\n").run()                          # Tools menu: run_errand("<job>\n")
    assert jobs.ran == [job] and jobs.doing == [job]
