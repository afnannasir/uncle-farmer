"""Run, Autopilot (master plan, Phase 4): the day for one character - the Before farming list, a plan,
the After farming list - in one window, started only when the player presses Start."""
from __future__ import annotations

import pytest

from unclefarmer import settings
from unclefarmer.gui.autopilot import default_plan, plan_choices
from unclefarmer.gui.main import action_menu_states


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


def test_the_profile_keeps_the_autopilot_plan() -> None:
    assert settings.PROFILE_DEFAULTS["autopilot_plan"] == ""
    assert settings.Profile.model_validate(dict(settings.PROFILE_DEFAULTS, autopilot_plan="plan:x")).autopilot_plan == "plan:x"


def test_the_window_shows_the_lists_and_saves_the_plan_and_the_lists(monkeypatch: pytest.MonkeyPatch) -> None:
    import wx

    from unclefarmer.gui.autopilot import AutopilotDialog
    _app = wx.GetApp() or wx.App(False)
    p = dict(settings.PROFILE_DEFAULTS)
    from unclefarmer.engine import triggers
    p["trigger_sequences"] = {"before": {"steps": [triggers.step("chores")]}}
    settings.save_profile("Hero", p)
    frame = wx.Frame(None)
    d = AutopilotDialog(frame, "Hero", [("plan", "turkey cave"), ("plan", "margit")], "plan:margit")
    try:
        assert d.plan.GetSelection() == 1
        assert d.editors["before"].list.GetCount() == 1
        d.plan.SetSelection(0)
        def ended(code: int) -> None:
            pass                              # not shown modally here
        monkeypatch.setattr(d, "EndModal", ended)
        d.done(wx.ID_YES)
        assert d.item == ("plan", "turkey cave")
        saved = settings.load_profile("Hero")
        assert saved["autopilot_plan"] == "plan:turkey cave"
        assert saved["trigger_sequences"]["before"]["steps"][0]["action"] == "chores"
    finally:
        d.Destroy()
        frame.Destroy()
