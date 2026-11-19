"""Run, Autopilot: a character's whole day in one window (master plan, Phase 4).

The day is what a farming run already does around its farming: the Before farming list (the day's
chores, a cave, restocking...), the farming up to the 00:00 GMT daily stop, and the After farming
list (selling and storing, the Gem Maker...). The lists are the ones the Triggers tab edits (kept
once: the profile's trigger_sequences). Every way of running it the player was asked about stays
a choice (2026-11-19, engine/autopilot.py):

  - farm with the character's plan, or with the best map for its level when the day starts;
  - run the lists around the farming, or as one script of the day that the player can read, change
    and schedule like any other script;
  - start only when Start is pressed, or every day at a time (a daily schedule the window keeps in
    Run, Schedules)."""

from __future__ import annotations

import datetime as dt
from typing import Any

import wx

from .. import schedule, settings
from ..engine import autopilot, plan as plans, triggers
from ..i18n import N_, _
from .common import Dialog, Form, error, page, save_file, tabs, with_code
from .mainparts.base import Item, saved_item
from .triggers import SequenceEditor

STAGE_TITLES = {"before": N_("Before farming"), "after": N_("After farming")}
FARM_NAMES = (N_("The character's plan"), N_("The best map for the character's level"))
HOW_NAMES = (N_("The Triggers lists around the farming"), N_("One script of the day (to read, change or schedule)"))
START_NAMES = (N_("Only when I press Start"), N_("Every day at the time below"))


def plan_choices(items: list[Item]) -> list[Item]:
    """The plans (not scripts) the day can farm with, in the order the Plans tab shows them."""
    return [it for it in items if it[0] == "plan"]


def default_plan(p: dict[str, Any], plans_: list[Item], last: str | None) -> int:
    """Which plan the window offers first: the one the autopilot used before, else the one the
    character ran last, else the first."""
    for saved in (str(p.get("autopilot_plan") or ""), last or ""):
        if saved and saved_item(saved) in plans_:
            return plans_.index(saved_item(saved))
    return 0


def plan_text(item: Item | None) -> str | None:
    """A plan's script, or None (none chosen, or it can't be read)."""
    if item is None or item[0] != "plan":
        return None
    try:
        return plans.to_script(plans.load(item[1]))
    except (OSError, ValueError):
        return None


def sync_schedule(scheduler: schedule.Scheduler, p: dict[str, Any], character: str, item: Item | None,
                  now: dt.datetime) -> str:
    """Keep the window's daily schedule in step with its choices: made or changed for "every day",
    taken away for "only when I press Start" (or nothing to run). The schedule's id ("" for none)."""
    c = autopilot.choices(p)
    sid = str(p.get("autopilot_schedule") or "")
    have = scheduler.get(sid) if sid else None
    if c["start"] == "daily" and item is not None:
        fields: dict[str, Any] = {"item": "%s:%s" % item, "character": character, "repeat": schedule.DAILY,
                                  "time": c["time"], "date": "", "days": [], "enabled": True}
        if have is not None:
            return str(scheduler.update(sid, fields, now)["id"])
        return str(scheduler.add(fields, now)["id"])
    if have is not None:
        scheduler.remove(sid)
    return ""


class AutopilotDialog(Dialog):
    """The day for one character. Save keeps the choices; Start the day saves and runs it."""

    def __init__(self, parent: wx.Window, character: str, items: list[Item], last: str | None) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Autopilot"), name=settings.profile_display(character)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.display = settings.profile_display(character)
        self.p = settings.load_profile(character)
        c = autopilot.choices(self.p)
        self.plans = plan_choices(items)
        self.item: Item | None = None
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.farm = f.choice(_("&Farm with:"), [_(n) for n in FARM_NAMES], autopilot.FARMS.index(c["farm"]))
        self.plan = f.choice(_("&Plan:"), [name for _kind, name in self.plans] or [_("(no plan yet)")],
                             default_plan(self.p, self.plans, last))
        self.how = f.choice(_("&Run the day as:"), [_(n) for n in HOW_NAMES], autopilot.HOWS.index(c["how"]))
        self.start = f.choice(_("&Start:"), [_(n) for n in START_NAMES], autopilot.STARTS.index(c["start"]))
        self.time = f.text(_("&Time (HH:MM, this computer's clock):"), c["time"])
        f.readonly(_("The day:"), _(
            "1. the Before farming list, top to bottom; 2. the farming, until the plan ends or the daily stop before "
            "the game's day reset; 3. the After farming list, when the farming ended by itself (not after Stop). "
            "These are the same lists as Character settings, Triggers. The script of the day is written from them "
            "once; after that it is yours to change, until you write it again."), lines=4)
        root.Add(f.grid, 0, wx.EXPAND | wx.ALL, 12)
        nb = tabs(self)
        self.editors: dict[str, SequenceEditor] = {}
        for stage in triggers.STAGES:
            panel, box = page(nb, _(STAGE_TITLES[stage]))
            try:
                cfg = triggers.sequence(self.p, stage)
            except ValueError:
                legacy = {k: v for k, v in self.p.items() if k != "trigger_sequences"}
                cfg = triggers.sequence(legacy, stage)
            editor = SequenceEditor(panel, cfg)
            box.Add(editor, 1, wx.EXPAND)
            self.editors[stage] = editor
        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        write = wx.Button(self, label=_("&Write the script of the day now"))
        write.Bind(wx.EVT_BUTTON, lambda e: self.write_script())
        row.Add(write, 0, wx.RIGHT, 24)
        for label, code in ((_("Save"), wx.ID_OK), (_("Sta&rt the day"), wx.ID_YES)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, with_code(self.done, code))
            row.Add(btn, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0)
        root.Add(row, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        for ctrl in (self.farm, self.start):
            ctrl.Bind(wx.EVT_CHOICE, lambda e: self.sync())
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(660, 600))
        self.sync()

    def sync(self) -> None:
        self.plan.Enable(autopilot.FARMS[self.farm.GetSelection()] == "plan")
        self.time.Enable(autopilot.STARTS[self.start.GetSelection()] == "daily")

    def chosen(self) -> Item | None:
        i = self.plan.GetSelection()
        return self.plans[i] if self.plans and 0 <= i < len(self.plans) else None

    def gather(self) -> dict[str, Any] | None:
        """The profile with the window's choices in it, or None (said) when one can't be used."""
        farm = autopilot.FARMS[self.farm.GetSelection()]
        start = autopilot.STARTS[self.start.GetSelection()]
        item = self.chosen()
        if farm == "plan" and item is None:
            error(self, _("Make a plan first (the Plans tab), or farm on the best map for the level."))
            return None
        time = self.time.GetValue().strip()
        if start == "daily":
            try:
                h, m = schedule.parse_time(time)
            except ValueError:
                error(self, _("Write the time as HH:MM, for example 06:30."))
                self.time.SetFocus()
                return None
            time = "%02d:%02d" % (h, m)
        p = dict(self.p)
        p["trigger_sequences"] = {stage: editor.value() for stage, editor in self.editors.items()}
        p.update(autopilot_farm=farm, autopilot_how=autopilot.HOWS[self.how.GetSelection()], autopilot_start=start,
                 autopilot_time=time or "06:00", autopilot_plan="%s:%s" % item if item else "")
        return p

    def write_script(self) -> None:
        p = self.gather()
        if p is None:
            return
        try:
            name = autopilot.write_day(p, self.display, plan_text(self.chosen()))
        except OSError as e:
            error(self, _("Couldn't write it: {err}", err=e))
            return
        wx.MessageBox(_("The day is written as the script \"{name}\". Open it from the Plans tab to read or change "
                        "it; \"Write the script of the day now\" writes it again from this window.", name=name),
                      _("Autopilot"), wx.OK | wx.ICON_INFORMATION, self)

    def done(self, code: int) -> None:
        p = self.gather()
        if p is None:
            return
        try:
            item = autopilot.item_for(p, self.display, plan_text(self.chosen()))
        except OSError as e:
            error(self, _("Couldn't write it: {err}", err=e))
            return
        if code == wx.ID_YES and item is None:
            error(self, _("Make a plan first (the Plans tab), or farm on the best map for the level."))
            return
        if save_file(self, settings.save_profile, self.character, p):
            self.p = p
            self.item = item
            self.EndModal(code)
