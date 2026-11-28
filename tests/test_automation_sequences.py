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
    # the stand-in plans farm where they stand: the (ticked by default) skip would leave the list out
    return {"trigger_sequences": {stage: {"steps": list(rows)}}, "skip_before_on_farm": False}


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


class DayBot(FakeBot):
    """A FakeBot with the daily stop fields of the real Bot."""

    def __init__(self) -> None:
        super().__init__()
        self.daily_stop_at = 0.0
        self.daily_done = False
        self.daily_reset = ""


def test_only_the_wait_runs_past_the_daily_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    # the steps keep farming's deadline; only the wait between them may pass it
    bot = DayBot()
    bot.daily_stop_at = 1234.0
    bot.p = profile(triggers.step("clean_bag", 600), triggers.step("restock"), stage="after")
    seen: list[tuple[str, float]] = []
    def action(_b: Any, name: str) -> None:
        seen.append((name, bot.daily_stop_at))
    def sleep(_seconds: float) -> None:
        seen.append(("wait", bot.daily_stop_at))
    monkeypatch.setattr(triggers, "run_action", action)
    monkeypatch.setattr(bot, "sleep", sleep, raising=False)
    triggers.run_stage(as_bot(bot), "after", bot.log, bot.stop_event.is_set)
    assert seen == [("clean_bag", 1234.0), ("wait", 0.0), ("restock", 1234.0)]
    assert bot.daily_stop_at == 1234.0


def test_the_after_list_keeps_the_daily_stop(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = profile(triggers.step("clean_bag", 600), triggers.step("restock"), stage="after")
    run, _logs, jobs = triggered(monkeypatch, tmp_path, cfg)
    observed: list[object] = []
    def stage(bot: Any, name: str, log: Any, stopped: Any, window: Any = None) -> list[tuple[str, bool]]:
        if name == "after":
            observed.append(getattr(bot, "daily_stop_at", "untouched"))
        return []
    monkeypatch.setattr(triggers, "run_stage", stage)
    run.run()
    assert jobs.ran == ["FARMING"] and observed == ["untouched"]     # not lifted for the whole list


@pytest.mark.parametrize("deterministic", [False, True])
def test_a_step_that_farms_waits_for_the_days_window(deterministic: bool, monkeypatch: pytest.MonkeyPatch) -> None:
    """A step with a farm line asks for the day's window; a dungeon doesn't (it spends no drop time)."""
    bot = FakeBot()
    (Path(paths.SCRIPTS) / "farms.mwf").write_text("farm minutes=30\n", encoding="utf-8")
    bot.p = profile(triggers.step("script:farms", deterministic=deterministic), triggers.step("dungeon"),
                    triggers.step("restock"), stage="after")
    ran: list[str] = []
    def action(_b: Any, name: str) -> None:
        ran.append(name)
    monkeypatch.setattr(triggers, "run_action", action)
    asked: list[str] = []
    def window() -> None:
        asked.append("window")
        raise runner.Stopped("the day's farming is over")
    if deterministic:
        with pytest.raises(triggers.TriggerFailed):
            triggers.run_stage(as_bot(bot), "after", bot.log, bot.stop_event.is_set, window)
        assert ran == []
    else:
        done = triggers.run_stage(as_bot(bot), "after", bot.log, bot.stop_event.is_set, window)
        assert done == [("script:farms", False), ("dungeon", True), ("restock", True)]
        assert ran == ["dungeon", "restock"]
    assert asked == ["window"]                 # asked for the farming step only, not for the dungeon or restock


def test_which_steps_farm(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    bot = FakeBot()
    no_settings: dict[str, Any] = {}
    monkeypatch.setattr(dungeon, "config", returning(no_settings))
    (Path(paths.SCRIPTS) / "farms.mwf").write_text("restock\nif kills < 5\n  farm minutes=30\nend\n", encoding="utf-8")
    (Path(paths.SCRIPTS) / "town.mwf").write_text("restock\nsell_shards\n", encoding="utf-8")
    b = as_bot(bot)
    # a dungeon spends no drop time: it isn't held to the day's farming window
    assert not triggers.step_farms(b, "dungeon") and triggers.step_farms(b, "script:farms")
    assert not triggers.step_farms(b, "script:town") and not triggers.step_farms(b, "restock")
    assert not triggers.step_farms(b, "chores") and not triggers.step_farms(b, "script:gone")


def test_the_farming_window_stops_near_the_reset_and_arms_the_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    import datetime as dt
    run = runner.Run("before", "before", 77, returning(None), returning(None))
    bot = DayBot()
    bot.daily_stop_at, bot.daily_done = 5.0, True
    run.bot = as_bot(bot)
    monkeypatch.setattr(runner.daily, "plan", returning((None, "05:00")))
    with pytest.raises(runner.Stopped):
        run.farming_window()
    end = dt.datetime(2026, 11, 16, 4, 50)
    monkeypatch.setattr(runner.daily, "plan", returning((end, None)))
    run.farming_window()
    assert bot.daily_stop_at == end.timestamp() and bot.daily_done is False


def test_a_failed_after_step_still_lets_the_after_stop_run(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    cfg = profile(triggers.step("restock", deterministic=True), triggers.step("clean_bag"), stage="after")
    run, logs, jobs = triggered(monkeypatch, tmp_path, cfg)
    jobs.raises["restock"] = runner.Stopped("supplies unavailable")
    run.run()
    assert jobs.ran == ["FARMING", "restock"] and run.failed
    assert run.ended_by_itself                 # farming ended by itself: close the game / shut down still apply
    assert any("restock" in line or "restocking" in line for line, _speak in logs)


@pytest.mark.parametrize("text, kind, checked", [("dungeon\n", "tool", False), ("restock\n", "tool", False),
                                                   ("farm map=5121\n", "script", True)])
def test_only_a_run_that_farms_public_maps_keeps_the_daily_stop(text: str, kind: str, checked: bool,
                                                                monkeypatch: pytest.MonkeyPatch) -> None:
    """Only killing monsters on public maps spends the drop time: a scheduled dungeon may run right
    before the reset (its entry is the day's), a farm line may not."""
    run = runner.Run(text, text.strip(), 77, returning(None), returning(None))
    run.kind = kind
    monkeypatch.setattr(runner.daily, "plan", returning((None, "05:00")))
    class Reached(Exception):
        pass
    def link(**_kw: Any) -> None:
        raise Reached()
    monkeypatch.setattr(runner, "AgentLink", link)
    with pytest.raises(runner.Stopped if checked else Reached):
        run._start({})  # pyright: ignore[reportPrivateUsage]


def test_delays_stop_at_an_hour_and_the_dungeon_step_keeps_its_end_place(monkeypatch: pytest.MonkeyPatch) -> None:
    assert triggers.step("restock", triggers.MAX_DELAY)["delay"] == 3600.0
    with pytest.raises(ValueError):
        triggers.step("restock", triggers.MAX_DELAY + 1)
    monkeypatch.setattr(dungeon, "config", returning({"end": {"how": "here"}}))
    assert triggers.action_text(as_bot(FakeBot()), "dungeon") == dungeon.script({"end": {"how": "here"}})


def test_a_broken_saved_list_says_where_to_fix_it() -> None:
    bot = FakeBot()
    bot.p = {"trigger_sequences": {"before": "nonsense"}}
    with pytest.raises(triggers.TriggerFailed, match="Character settings"):
        triggers.run_stage(as_bot(bot), "before", bot.log, bot.stop_event.is_set)
    with pytest.raises(triggers.TriggerFailed):
        triggers.ticked(bot.p, "before")


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


@pytest.mark.parametrize("mouse", [False, True])
def test_trigger_context_actions_edit_toggle_and_remove_selected_row(monkeypatch: pytest.MonkeyPatch, mouse: bool) -> None:
    app = wx.GetApp() or wx.App(False)
    parent = wx.Frame(None)
    editor = gui.SequenceEditor(parent, profile(triggers.step("restock"), triggers.step("clean_bag", 7, deterministic=True))["trigger_sequences"]["before"])
    entries: list[Any] = []
    def capture_actions(owner: wx.Window, actions: list[gui.ContextAction]) -> None:
        entries.extend(actions)
    monkeypatch.setattr(gui, "popup_actions", capture_actions)
    editor.list.SetSelection(0 if mouse else 1)
    event = wx.ContextMenuEvent(wx.EVT_CONTEXT_MENU.typeId)
    if mouse:
        monkeypatch.setattr(editor.list, "HitTest", returning(1))
        event.SetPosition(editor.list.ClientToScreen(wx.Point(5, 5)))
    else:
        event.SetPosition(wx.DefaultPosition)
    editor.context_menu(event)
    assert editor.list.GetSelection() == 1
    assert len(entries) == 3 and all(action[2] for action in entries)
    rows = editor.value()["steps"]
    class EditDialog:
        def __init__(self, parent: Any, row: dict[str, Any], *, keyboard_names: tuple[str, ...] = ()) -> None:
            assert row == rows[1]
            self.fields = dict(row, delay=12)
        def __enter__(self) -> Any:
            return self
        def __exit__(self, *args: Any) -> None:
            pass
        def ShowModal(self) -> int:
            return wx.ID_OK
    monkeypatch.setattr(gui, "StepEditor", EditDialog)
    entries[0][1](wx.CommandEvent())
    assert editor.rows[1]["delay"] == 12 and editor.rows[1]["deterministic"]
    entries[1][1](wx.CommandEvent())
    assert not editor.rows[1]["enabled"] and editor.rows[0] == rows[0]
    entries[2][1](wx.CommandEvent())
    assert editor.value()["steps"] == [rows[0]]
    buttons = [child.GetLabel() for child in editor.GetChildren() if isinstance(child, wx.Button)]
    assert len(buttons) == 3  # Add, Move up, Move down
    parent.Destroy()
    app.ProcessPendingEvents()


def test_empty_trigger_context_actions_are_disabled(monkeypatch: pytest.MonkeyPatch) -> None:
    app = wx.GetApp() or wx.App(False)
    parent = wx.Frame(None)
    editor = gui.SequenceEditor(parent, {"steps": []})
    entries: list[Any] = []
    def capture_actions(owner: wx.Window, actions: list[gui.ContextAction]) -> None:
        entries.extend(actions)
    monkeypatch.setattr(gui, "popup_actions", capture_actions)
    event = wx.ContextMenuEvent(wx.EVT_CONTEXT_MENU.typeId)
    event.SetPosition(wx.DefaultPosition)
    editor.context_menu(event)
    assert len(entries) == 3 and not any(action[2] for action in entries)
    parent.Destroy()
    app.ProcessPendingEvents()


def test_main_menu_has_no_plans_menu_and_retains_plan_shortcuts(monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui.main import MainFrame
    app = wx.GetApp() or wx.App(False)
    frame = MainFrame.__new__(MainFrame)
    wx.Frame.__init__(frame, None)
    monkeypatch.setattr(frame, "sync_action_menus", returning(None))
    captured: list[tuple[int, int]] = []
    real_table = wx.AcceleratorTable
    def capture(entries: Any) -> Any:
        captured.extend((e.GetFlags(), e.GetKeyCode()) for e in entries)
        return real_table(entries)
    monkeypatch.setattr(wx, "AcceleratorTable", capture)
    frame.build_menu()
    assert frame.GetMenuBar().FindMenu("Plans") == wx.NOT_FOUND
    assert "plans" not in frame.action_menus
    assert (wx.ACCEL_CTRL, ord("N")) in captured
    assert (wx.ACCEL_CTRL | wx.ACCEL_SHIFT, ord("N")) in captured
    frame.Destroy()
    app.ProcessPendingEvents()
