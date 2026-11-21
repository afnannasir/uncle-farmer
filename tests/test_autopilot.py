"""Run, Autopilot (master plan, Phase 4): the day for one character - the Before farming list, the
farming, the After farming list. Every way of running it stays a choice: the plan or the best map
for the level, the Triggers lists or one script of the day, Start only or every day at a time."""
from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import schedule, settings
from unclefarmer.engine import autopilot, script, triggers
from unclefarmer.engine.plan import script_path
from unclefarmer.gui.autopilot import default_plan, plan_choices, sync_schedule
from unclefarmer.gui.main import action_menu_states


def profile(**kw: Any) -> dict[str, Any]:
    p = dict(settings.PROFILE_DEFAULTS)
    p.update(kw)
    return p


def lists(before: list[dict[str, Any]], after: list[dict[str, Any]]) -> dict[str, Any]:
    return {"before": {"steps": before}, "after": {"steps": after}}


def test_only_plans_are_offered_and_the_last_choice_comes_first() -> None:
    items = [("script", "chores only"), ("plan", "turkey cave"), ("plan", "margit")]
    plans = plan_choices(items)
    assert plans == [("plan", "turkey cave"), ("plan", "margit")]
    assert default_plan({"autopilot_plan": "plan:margit"}, plans, "plan:turkey cave") == 1
    assert default_plan({"autopilot_plan": ""}, plans, "plan:margit") == 1       # the last run
    assert default_plan({"autopilot_plan": "plan:gone"}, plans, None) == 0


def test_the_menu_entry_needs_a_game_a_plan_and_no_run() -> None:
    def state(available: bool = True, running: bool = False, has_items: bool = True) -> bool:
        return action_menu_states(available=available, running=running, any_running=running, has_items=has_items,
                                  has_messengers=False, selected=None)["run_autopilot"]
    assert state()
    assert not state(available=False) and not state(running=True) and not state(has_items=False)


def test_the_choices_default_and_an_unknown_one_falls_back() -> None:
    c = autopilot.choices(profile())
    assert (c["farm"], c["how"], c["start"], c["time"]) == ("plan", "lists", "manual", "06:00")
    assert autopilot.choices(profile(autopilot_farm="moon", autopilot_how="script"))["farm"] == "plan"
    assert settings.Profile.model_validate(profile(autopilot_how="script")).autopilot_how == "script"


def test_the_day_as_a_script_has_its_steps_in_order_and_parses() -> None:
    p = profile(trigger_sequences=lists(
        [triggers.step("chores"), triggers.step("restock", 30)],
        [triggers.step("sell_shards"), triggers.step("dungeon"), triggers.step("gem_maker", enabled=False)]))

    def read(action: str) -> str:
        return "dungeon Scorpion Cave\n" if action == "dungeon" else action + "\n"
    text = autopilot.day_script(p, "login Hero\nfarm map=5281 minutes=60\n", read, "the day of Hero")
    lines = [line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("#")]
    assert lines[:4] == ["login Hero", "triggers off", "chores", "restock"]
    assert lines[4:] == ["wait 30", "farm map=5281 minutes=60", "on_stop", "sell_shards", "end"]
    assert "left out:" in text                       # the cave farms: no After step
    main, _subs, on_stop = script.parse(text)
    assert [n.kind for n in on_stop] == ["sell_shards"] and main[1].kind == "triggers"


def test_triggers_off_and_farm_map_best_parse() -> None:
    r = script.Runner(object(), "triggers off\nfarm map=best\n")  # type: ignore[arg-type]
    assert r.own_lists and r.main[1].opts["map"] == script.BEST_MAP
    assert not script.Runner(object(), "farm\n").own_lists  # type: ignore[arg-type]
    main, subs, _on_stop = script.parse("farm map=best\n")
    assert triggers.first_farm_map(main, subs) is None   # chosen when it runs
    with pytest.raises(script.ScriptError):
        script.parse("triggers on\n")


class SheetBot:
    def __init__(self, level: int | None) -> None:
        self.level = level
        self.lines: list[str] = []

    def refresh_sheet(self, force: bool = False) -> dict[str, Any]:
        return {"level": self.level} if self.level is not None else {}

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)


def test_the_best_map_is_the_first_that_fits_the_level_now() -> None:
    from unclefarmer import atlas
    b = SheetBot(40)
    mid = script.best_map(b)  # type: ignore[arg-type]
    assert mid == atlas.maps_for_level(40)[0][0] and "best map for level 40" in b.lines[0]
    with pytest.raises(script.Stopped):
        script.best_map(SheetBot(None))  # type: ignore[arg-type]


def test_what_start_runs_for_each_choice() -> None:
    p = profile(autopilot_plan="plan:margit")
    assert autopilot.item_for(p, "Hero", "farm\n") == ("plan", "margit")
    assert autopilot.item_for(profile(), "Hero", None) is None               # no plan chosen
    best = autopilot.item_for(profile(autopilot_farm="best"), "Hero", None)
    assert best is not None and best == ("script", autopilot.FARM_SCRIPT % "Hero")
    with open(script_path(best[1]), encoding="utf-8") as f:
        assert "farm map=best" in f.read()
    day = autopilot.item_for(profile(autopilot_how="script", autopilot_plan="plan:margit"), "Hero", "farm\n")
    assert day is not None and day == ("script", autopilot.DAY_SCRIPT % "Hero")
    path = script_path(day[1])
    with open(path, "w", encoding="utf-8") as f:
        f.write("# mine\nfarm\n")                                             # the player changed it
    autopilot.item_for(profile(autopilot_how="script"), "Hero", "farm\n")
    with open(path, encoding="utf-8") as f:
        assert f.read() == "# mine\nfarm\n"                                   # kept
    autopilot.write_day(profile(autopilot_how="script"), "Hero", "farm\n")    # "write it again"
    with open(path, encoding="utf-8") as f:
        assert "triggers off" in f.read()


def test_every_day_at_keeps_one_schedule_in_step(tmp_path: Path) -> None:
    sched = schedule.Scheduler(str(tmp_path / "schedules.json"))
    now = dt.datetime(2026, 11, 19, 10, 0)
    p = profile(autopilot_start="daily", autopilot_time="06:30")
    sid = sync_schedule(sched, p, "Hero", ("plan", "margit"), now)
    s = sched.get(sid)
    assert s is not None and (s["item"], s["repeat"], s["time"], s["character"]) == ("plan:margit", "daily", "06:30", "Hero")
    p["autopilot_schedule"], p["autopilot_time"] = sid, "07:00"
    assert sync_schedule(sched, p, "Hero", ("plan", "margit"), now) == sid      # changed, not a second one
    assert len(sched.items) == 1 and sched.items[0]["time"] == "07:00"
    p["autopilot_start"] = "manual"
    assert sync_schedule(sched, p, "Hero", ("plan", "margit"), now) == "" and not sched.items


def test_the_window_shows_the_lists_and_saves_every_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    import wx

    from unclefarmer.gui.autopilot import AutopilotDialog
    _app = wx.GetApp() or wx.App(False)
    settings.save_profile("Hero", profile(trigger_sequences=lists([triggers.step("chores")], [])))
    frame = wx.Frame(None)
    d = AutopilotDialog(frame, "Hero", [("plan", "turkey cave"), ("plan", "margit")], "plan:margit")
    try:
        assert d.plan.GetSelection() == 1
        assert d.editors["before"].list.GetCount() == 1
        assert not d.time.IsEnabled()                         # "only when I press Start"
        d.plan.SetSelection(0)
        d.start.SetSelection(1)
        d.time.SetValue("6:5")

        def ended(code: int) -> None:
            pass                              # not shown modally here
        monkeypatch.setattr(d, "EndModal", ended)
        d.done(wx.ID_YES)
        assert d.item == ("plan", "turkey cave")
        saved = settings.load_profile("Hero")
        assert (saved["autopilot_plan"], saved["autopilot_start"], saved["autopilot_time"]) == ("plan:turkey cave", "daily", "06:05")
        assert saved["trigger_sequences"]["before"]["steps"][0]["action"] == "chores"
    finally:
        d.Destroy()
        frame.Destroy()
