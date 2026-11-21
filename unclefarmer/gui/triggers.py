"""Ordered per-character Before/After actions, edited as part of Character settings."""
from __future__ import annotations

from typing import Any

import wx

from ..engine import keyboard, plan, triggers
from ..i18n import _
from .common import ContextAction, Dialog, Form, buttons, error, labeled, ok_cancel, opens, popup_actions, read_decimal


class StepEditor(Dialog):
    def __init__(self, parent: wx.Window | None, row: dict[str, Any] | None = None, *,
                 keyboard_names: tuple[str, ...] = ()) -> None:
        super().__init__(parent, title=_("Trigger action"))
        row = row or triggers.step("restock")
        self.keyboard_actions = [keyboard.ACTION_PREFIX + name for name in keyboard_names]
        self.actions = ([job for job, _label in triggers.TOOLS] + self.keyboard_actions
                        + ["script:" + name for name in plan.script_names()])
        if row["action"] not in self.actions:
            self.actions.append(row["action"])  # show a removed script so it can be repaired
        self.fields: dict[str, Any] = {}
        box = wx.BoxSizer(wx.VERTICAL)
        form = Form(self)
        self.action = form.choice(_("&Action:"), [triggers.what(a) for a in self.actions], self.actions.index(row["action"]))
        self.enabled = form.check(_("&Enabled"), row["enabled"])
        self.strict = form.check(_("&DETERMINISTIC: stop the sequence if this action fails"), row["deterministic"])
        self.delay = form.decimal(_("Delay after success (&seconds):"), row["delay"], 0, triggers.MAX_DELAY)
        box.Add(form.grid, 0, wx.EXPAND | wx.ALL, 12)
        note = wx.StaticText(self, label=_("The delay runs before the next enabled action. Zero starts it immediately; the last action has no delay."))
        note.Wrap(480)
        box.Add(note, 0, wx.ALL, 12)
        ok_cancel(self, box)
        self.SetSizerAndFit(box)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)

    def read(self) -> dict[str, Any]:
        delay = read_decimal(self.delay)
        row = triggers.step(self.actions[self.action.GetSelection()], delay, self.enabled.GetValue(), self.strict.GetValue())
        if row["action"].startswith("script:") and row["action"][7:] not in plan.script_names():
            raise ValueError(_("That script no longer exists. Choose another action."))
        if row["action"].startswith(keyboard.ACTION_PREFIX) and row["action"] not in self.keyboard_actions:
            raise ValueError(_("That keyboard profile no longer exists. Choose another action."))
        return row

    def on_ok(self, _evt: wx.Event) -> None:
        try:
            self.fields = self.read()
        except ValueError as e:
            error(self, str(e))
            return
        self.EndModal(wx.ID_OK)


class SequenceEditor(wx.Panel):
    def __init__(self, parent: wx.Window, cfg: dict[str, Any], *, keyboard_names: tuple[str, ...] = ()) -> None:
        super().__init__(parent)
        self.rows: list[dict[str, Any]] = [dict(row) for row in cfg["steps"]]
        self.keyboard_names = keyboard_names
        box = wx.BoxSizer(wx.VERTICAL)
        self.list = labeled(self, box, _("Actions in execution &order:"), lambda w: wx.ListBox(w, size=wx.Size(-1, 150)), 1)
        buttons(self, box, (opens(_("&Add")), lambda e: self.edit()))
        buttons(self, box, (_("Move &up"), lambda e: self.move(-1)), (_("Move &down"), lambda e: self.move(1)))
        self.list.Bind(wx.EVT_LISTBOX_DCLICK, lambda e: self.edit(self.list.GetSelection()))
        self.list.Bind(wx.EVT_CONTEXT_MENU, self.context_menu)
        self.SetSizer(box)
        self.fill()

    def context_menu(self, event: wx.ContextMenuEvent) -> None:
        pos = event.GetPosition()
        if pos != wx.DefaultPosition:
            index = self.list.HitTest(self.list.ScreenToClient(pos))
            self.list.SetSelection(index)
        selected = 0 <= self.list.GetSelection() < len(self.rows)
        entries: list[ContextAction] = [
            (opens(_("&Edit")), lambda e: self.edit(self.list.GetSelection()), selected),
            (_("Enable/&disable"), lambda e: self.toggle(), selected),
            (_("&Remove"), lambda e: self.remove(), selected),
        ]
        popup_actions(self.list, entries)

    def fill(self, select: int = 0) -> None:
        self.list.Set([_("{n}. {action} — {state}; delay {seconds} s; deterministic: {strict}", n=i + 1, action=triggers.what(row["action"]),
                         state=_("Enabled") if row["enabled"] else _("Disabled"), seconds=row["delay"],
                         strict=_("Yes") if row["deterministic"] else _("No"))
                       for i, row in enumerate(self.rows)])
        if self.rows:
            self.list.SetSelection(max(0, min(select, len(self.rows) - 1)))

    def value(self) -> dict[str, Any]:
        return {"steps": [dict(row) for row in self.rows]}

    def edit(self, index: int | None = None) -> None:
        if index is not None and not 0 <= index < len(self.rows):
            return
        with StepEditor(self, self.rows[index] if index is not None else None, keyboard_names=self.keyboard_names) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            if index is None:
                self.rows.append(d.fields)
                index = len(self.rows) - 1
            else:
                self.rows[index] = d.fields
            self.fill(index)

    def move(self, direction: int) -> None:
        i = self.list.GetSelection()
        target = i + direction
        if 0 <= i < len(self.rows) and 0 <= target < len(self.rows):
            self.rows[i], self.rows[target] = self.rows[target], self.rows[i]
            self.fill(target)

    def remove(self) -> None:
        i = self.list.GetSelection()
        if 0 <= i < len(self.rows):
            self.rows.pop(i)
            self.fill(i)

    def toggle(self) -> None:
        i = self.list.GetSelection()
        if 0 <= i < len(self.rows):
            self.rows[i]["enabled"] = not self.rows[i]["enabled"]
            self.fill(i)
