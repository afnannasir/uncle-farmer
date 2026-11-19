"""Run, Autopilot: a character's whole day in one window (master plan, Phase 4).

The day is what a farming run already does around its plan: the Before farming list (the day's
chores, a cave, restocking...), the plan itself up to the 00:00 GMT daily stop, and the After
farming list (selling and storing, the Gem Maker...). This window shows the three together, with
the same lists the Triggers tab edits (one place they are kept: the profile's trigger_sequences),
and starts the day when the player presses Start. Nothing starts by itself (the player's choice,
2026-11-19); a schedule can still start a plan as before."""

from __future__ import annotations

from typing import Any

import wx

from .. import settings
from ..engine import triggers
from ..i18n import _
from .common import Dialog, Form, error, page, save_file, tabs, with_code
from .mainparts.base import Item, saved_item
from .triggers import SequenceEditor

STAGE_TITLES = {"before": "Before farming", "after": "After farming"}


def plan_choices(items: list[Item]) -> list[Item]:
    """The plans (not scripts) the day can farm with, in the order the Plans tab shows them."""
    return [it for it in items if it[0] == "plan"]


def default_plan(p: dict[str, Any], plans: list[Item], last: str | None) -> int:
    """Which plan the window offers first: the one the autopilot used before, else the one the
    character ran last, else the first."""
    for saved in (str(p.get("autopilot_plan") or ""), last or ""):
        if saved and saved_item(saved) in plans:
            return plans.index(saved_item(saved))
    return 0


class AutopilotDialog(Dialog):
    """The day for one character: Before farming, the plan, After farming. Start saves and runs."""

    def __init__(self, parent: wx.Window, character: str, items: list[Item], last: str | None) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Autopilot"), name=settings.profile_display(character)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.p = settings.load_profile(character)
        self.plans = plan_choices(items)
        self.item: Item | None = None
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.plan = f.choice(_("&Farm with the plan:"), [name for _kind, name in self.plans] or [_("(no plan yet)")],
                             default_plan(self.p, self.plans, last))
        f.readonly(_("The day:"), _(
            "1. the Before farming list, top to bottom; 2. the plan, until it ends or the daily stop before the "
            "game's day reset; 3. the After farming list, when the plan ended by itself (not after Stop). "
            "These are the same lists as Character settings, Triggers."), lines=4)
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
        for label, code in ((_("Save"), wx.ID_OK), (_("Sta&rt the day"), wx.ID_YES)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, with_code(self.done, code))
            row.Add(btn, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0)
        root.Add(row, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(620, 520))

    def chosen(self) -> Item | None:
        i = self.plan.GetSelection()
        return self.plans[i] if self.plans and 0 <= i < len(self.plans) else None

    def done(self, code: int) -> None:
        item = self.chosen()
        if code == wx.ID_YES and item is None:
            error(self, _("Make a plan first (the Plans tab): the day farms with one."))
            return
        self.p["trigger_sequences"] = {stage: editor.value() for stage, editor in self.editors.items()}
        self.p["autopilot_plan"] = "%s:%s" % item if item else ""
        if save_file(self, settings.save_profile, self.character, self.p):
            self.item = item
            self.EndModal(code)
