"""Ordered automation shares the farming/tool runner, clock, cancellation and scheduler."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
import wx

from conftest import as_bot, returning, Clock
from test_script import FakeBot
from test_triggers import triggered
from test_schedule import Window, daily, fresh, T0, outcomes
from unclefarmer import paths, settings, schedule
from unclefarmer.engine import triggers, runner, dungeon
from unclefarmer.gui import triggers as gui, schedules
from unclefarmer.gui.mainparts.runs import RunsMixin
from unclefarmer.gui.mainparts import runs as runs_gui


def profile(*rows: dict[str, Any], stage: str = "before") -> dict[str, Any]:
    return {"trigger_sequences": {stage: {"steps": list(rows)}}}


@pytest.mark.parametrize("stage", triggers.STAGES)
def test_order_delays_disabled_steps_and_independent_stages(stage: str, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = FakeBot()
    bot.p = profile(triggers.step("restock", 600), triggers.step("gather", 99, enabled=False),
                    triggers.step("store_items", 0), triggers.step("clean_bag", 700), stage=stage)
    events: list[object] = []
    def action(_b: Any, name: str) -> None:
        events.append(name)
    def sleep(seconds: float) -> None:
        events.append(("delay", seconds))
    monkeypatch.setattr(triggers, "run_action", action)
    monkeypatch.setattr(bot, "sleep", sleep)
    done = triggers.run_stage(as_bot(bot), stage, bot.log, bot.stop_event.is_set)
    assert done == [("restock", True), ("store_items", True), ("clean_bag", True)]
    assert events == ["restock", ("delay", 600), "store_items", "clean_bag"]
    assert triggers.ticked(bot.p, "after" if stage == "before" else "before") == []


@pytest.mark.parametrize("strict", [False, True])
def test_only_a_failing_checked_trigger_stops_the_sequence(strict: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    bot = FakeBot()
    bot.p = profile(triggers.step("restock", 40, deterministic=strict), triggers.step("clean_bag", deterministic=True))
    events: list[str] = []
    def action(_b: Any, name: str) -> None:
        events.append(name)
        if name == "restock":
            raise runner.Stopped("could not finish")
    monkeypatch.setattr(triggers, "run_action", action)
    if strict:
        with pytest.raises(triggers.TriggerFailed):
            triggers.run_stage(as_bot(bot), "before", bot.log, bot.stop_event.is_set)
        assert events == ["restock"]
    else:
        assert triggers.run_stage(as_bot(bot), "before", bot.log, bot.stop_event.is_set) == [("restock", False), ("clean_bag", True)]
        assert events == ["restock", "clean_bag"]
    assert not bot.calls  # failed actions never apply a success delay


def test_stop_during_delay_never_runs_the_next_action(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = FakeBot()
    bot.p = profile(triggers.step("restock", 600), triggers.step("clean_bag"))
    seen: list[str] = []
    def action(_b: Any, name: str) -> None:
        seen.append(name)
    def sleep(_seconds: float) -> None:
        bot.stop_event.set()
        raise runner.Stopped("stopped by you")
    monkeypatch.setattr(triggers, "run_action", action)
    monkeypatch.setattr(bot, "sleep", sleep)
    with pytest.raises(runner.Stopped):
        triggers.run_stage(as_bot(bot), "before", bot.log, bot.stop_event.is_set)
    assert seen == ["restock"]


def test_script_runs_in_order_and_its_watchers_do_not_leak(monkeypatch: pytest.MonkeyPatch) -> None:
    Path(paths.SCRIPTS, "prepare.mwf").write_text('wait 2\nstop_when kills > 99\nsay ready\n', encoding="utf-8")
    bot = FakeBot()
    original = bot.watches
    bot.p = profile(triggers.step("script:prepare", 5), triggers.step("restock"))
    seen: list[str] = []
    def job(_b: Any, name: str, check_only: bool = False) -> None:
        seen.append(name)
    monkeypatch.setattr(triggers, "run_job", job)
    assert all(ok for _name, ok in triggers.run_stage(as_bot(bot), "before", bot.log, bot.stop_event.is_set))
    assert bot.calls == [("sleep", 2), ("sleep", 5)] and seen == ["restock"]
    assert bot.watches is original and not bot.watches


@pytest.mark.parametrize("text", [None, "not_a_command\n", "say {missing_name}\n"])
def test_failed_scripts_obey_deterministic_setting(text: str | None) -> None:
    if text is not None:
        Path(paths.SCRIPTS, "broken.mwf").write_text(text, encoding="utf-8")
    bot = FakeBot()
    bot.p = profile(triggers.step("script:broken", deterministic=True), triggers.step("clean_bag"))
    with pytest.raises(triggers.TriggerFailed):
        triggers.run_stage(as_bot(bot), "before", bot.log, bot.stop_event.is_set)


@pytest.mark.parametrize("delay", [-1, float("nan"), float("inf"), True, "10"])
def test_invalid_delays_are_rejected(delay: Any) -> None:
    with pytest.raises(ValueError):
        triggers.step("restock", delay)


def test_legacy_migration_and_new_profile_persistence() -> None:
    old = {"gather_before_farming": True, "restock_after_farming": True}
    assert triggers.ticked(old, "before") == ["gather"]
    assert triggers.ticked(old, "after") == ["restock"]
    new = profile(triggers.step("restock", 600, deterministic=True), triggers.step("script:chores"))
    settings.save_profile("Hero", new)
    assert triggers.sequence(settings.load_profile("Hero"), "before") == new["trigger_sequences"]["before"]
    assert "trigger_sequences" not in old


def test_strict_preparation_failure_prevents_farming(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = profile(triggers.step("restock", deterministic=True), triggers.step("clean_bag"))
    run, _logs, jobs = triggered(monkeypatch, tmp_path, cfg)
    jobs.raises["restock"] = runner.Stopped("supplies unavailable")
    run.run()
    assert jobs.ran == ["restock"] and run.failed
    assert not run.ended_by_itself


def test_after_sequence_wait_survives_farming_deadline(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = profile(triggers.step("clean_bag", 600), triggers.step("restock"), stage="after")
    run, _logs, jobs = triggered(monkeypatch, tmp_path, cfg)
    observed: list[float] = []
    def stage(bot: Any, name: str, log: Any, stopped: Any) -> list[tuple[str, bool]]:
        if name == "after":
            observed.append(bot.daily_stop_at)
        return []
    monkeypatch.setattr(triggers, "run_stage", stage)
    run.run()
    assert observed == [0] and jobs.ran == ["FARMING"]


def test_standalone_sequence_does_not_start_farming_or_recurse(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, _logs, jobs = triggered(monkeypatch, tmp_path, profile(triggers.step("restock")))
    run.kind, run.text = "trigger", "before"
    run.run()
    assert jobs.ran == ["restock"] and run.ended_by_itself and not run.failed
    assert not run.farmed()


def test_scheduled_dungeon_uses_the_tools_saved_ending_place(monkeypatch: pytest.MonkeyPatch) -> None:
    bot = FakeBot()
    monkeypatch.setattr(dungeon, "config", returning({"end": {"how": "here"}}))
    monkeypatch.setattr(dungeon, "run", returning(None))
    monkeypatch.setattr(bot, "finish", returning(None), raising=False)
    run = runner.Run("dungeon\n", "dungeon", 77, returning(None), returning(None))
    run.kind = "tool"
    monkeypatch.setattr(run, "_start", returning(as_bot(bot)))
    run.run()
    assert bot.end_place == ("here",)
    assert run.ended_by_itself


def test_scheduled_sequence_failure_is_recorded_as_an_error(tmp_path: Path) -> None:
    sch = fresh(tmp_path)
    entry = sch.add(daily(item="trigger:after"), T0)
    window = Window(sch, [(22, "Hero", None)])
    window.scheduled_items = returning([("trigger", "after")])
    window.schedule_tick()
    run = window.runs[22]
    run.ended_by_itself, run.failed = True, True
    window.schedule_ended(run, "one action failed")
    assert outcomes(entry) == [schedule.STARTED, schedule.FAILED]


@pytest.mark.parametrize("item", ["tool:restock", "trigger:before", "trigger:after"])
def test_scheduler_reuses_occurrence_and_busy_handling_for_automation(item: str, tmp_path: Path) -> None:
    sch = fresh(tmp_path)
    entry = sch.add(daily(item=item), T0)
    window = Window(sch, [(22, "Hero", None)])
    kind, name = item.split(":")
    window.scheduled_items = returning([(kind, name)])
    window.schedule_tick()
    assert window.started == [(22, (kind, name), True)]
    assert window.runs[22].schedule == (entry["id"], "2026-11-02 06:00")
    window.schedule_tick()
    assert len(window.started) == 1 and outcomes(entry) == [schedule.STARTED]


@pytest.mark.parametrize(("item", "text"), [(("tool", "restock"), "restock\n"),
                                           (("trigger", "before"), "before"), (("trigger", "after"), "after")])
def test_automation_start_preserves_saved_login_and_marks_before_start(
    item: tuple[str, str], text: str, monkeypatch: pytest.MonkeyPatch,
) -> None:
    key = settings.character_profile("Hero", 2001, "S1")
    mark = ("automation", "2026-11-15 05:00")
    started: list[tuple[str | None, tuple[str, str] | None, str]] = []
    class StubRun:
        def __init__(self, source: str, title: str, *_args: object, **_kwargs: object) -> None:
            self.text, self.title = source, title
            self.profile: str | None = None
            self.schedule: tuple[str, str] | None = None

        def start(self) -> None:
            started.append((self.profile, self.schedule, self.text))
    monkeypatch.setattr(runs_gui, "Run", StubRun)
    frame: Any = SimpleNamespace(
        s={"last_run": "plan:Keep"}, games=[(1, key, "Town")], runs={}, last_line={}, clock=Clock(),
        selected_item=returning(None), selected_pid=returning(1), label_games=returning(None),
        update_buttons=returning(None), poll=returning(None), on_log=returning(None), on_done=returning(None),
        locate=returning(None), last_seen=returning(None))
    assert RunsMixin.start(frame, 1, item, remote=True, profile=key, schedule_mark=mark) == "Starting %s." % item[1]
    assert started == [(key, mark, text)]
    assert frame.s == {"last_run": "plan:Keep"}


def test_gui_reordering_and_scheduler_tab_selection(tmp_path: Path) -> None:
    app = wx.GetApp() or wx.App(False)
    parent = wx.Frame(None)
    editor = gui.SequenceEditor(parent, triggers.sequence(profile(triggers.step("restock", deterministic=True),
                                                                 triggers.step("clean_bag")), "before"))
    editor.list.SetSelection(1)
    editor.move(-1)
    assert [row["action"] for row in editor.value()["steps"]] == ["clean_bag", "restock"]
    assert editor.rows[1]["deterministic"]
    editor.toggle()
    assert not editor.rows[0]["enabled"]
    sch = fresh(tmp_path)
    entries = [sch.add(daily(item=item), T0) for item in ("tool:restock", "plan:Wolves", "trigger:after")]
    with schedules.SchedulesDialog(parent, sch, [("plan", "Wolves"), ("tool", "restock"), ("trigger", "after")],
                                   lambda item: item[1], ["Hero"], lambda: T0) as dialog:
        assert dialog.nb.GetPageCount() == 3
        for index, entry in ((0, entries[1]), (1, entries[0]), (2, entries[2])):
            dialog.nb.SetSelection(index)
            assert dialog.selected() is entry
            assert dialog.available() == [schedule.item_of(entry)]
    parent.Destroy()
    app.ProcessPendingEvents()


def test_step_editor_exposes_per_action_strictness_and_validates_delay() -> None:
    app = wx.GetApp() or wx.App(False)
    with gui.StepEditor(None, triggers.step("restock")) as editor:
        editor.strict.SetValue(True)
        editor.delay.SetValue("600")
        assert editor.read()["deterministic"] and editor.read()["delay"] == 600
        editor.delay.SetValue("nan")
        with pytest.raises(ValueError):
            editor.read()
    app.ProcessPendingEvents()
