"""The script editor."""

from __future__ import annotations

import os

import wx

from .. import filelock, jsonio
from ..engine import plan as plans
from ..engine import script as lang
from ..i18n import _
from .common import Dialog, Form, Handler, ask, error, guide_text, info, save_file, show_text, modal

SNIPPETS = [
    ("travel", 'travel "Wolf Prairie"'),
    ("walk", "walk 120 53"),
    ("farm", "farm map=5121 minutes=30 patrol=random"),
    ("farm (kills, only some monsters)", 'farm kills=100 only="wolf|fox" elites=avoid'),
    ("key", "key 1"),
    ("hold", "hold w 2"),
    ("wait", "wait 5"),
    ("say", "say HP is {hp_pct}%, {kills} kills"),
    ("set", "set laps = 0"),
    ("heal", "heal"),
    ("hearthstone", "hearthstone"),
    ("stop_when (potions)", 'stop_when bag("Large Red Potion") < 10 : out of potions'),
    ("stop_when (time)", 'stop_when clock == "23:00" : bed time'),
    ("stop", "stop done for today"),
    ("repeat ... end", "repeat 3\n  \nend"),
    ("repeat forever", "repeat\n  \nend"),
    ("while ... end", "while kills < 500\n  \nend"),
    ("if ... else ... end", "if hp_pct < 50\n  heal\nelse\n  \nend"),
    ("sub ... end", "sub go_home\n  hearthstone\nend"),
    ("call", "call go_home"),
    ("on_stop ... end", "on_stop\n  hearthstone\nend"),
]

EXAMPLE = """# A new script. Lines starting with # are notes for you; the bot skips them.
# Press F1 for every command.

stop_when bag("Large Red Potion") < 10 : out of red potions

on_stop
  hearthstone
end

repeat
  farm map=5121 minutes=30
  say finished a round, {kills} kills so far
end
"""


class ScriptDialog(Dialog):
    def __init__(self, parent: wx.Window, name: str = "") -> None:
        super().__init__(parent, title=_("Script: {name}", name=name or _("new")), size=wx.Size(820, 640),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.original: str = name
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.name = f.text(_("Script &name:"), name)
        root.Add(f.grid, 0, wx.EXPAND)
        root.Add(wx.StaticText(self, label=_("Script &text:")), 0, wx.TOP, 8)
        self.text = wx.TextCtrl(self, style=wx.TE_MULTILINE | wx.TE_DONTWRAP | wx.TE_PROCESS_TAB | wx.TE_RICH2)
        self.text.SetName(_("Script text"))
        self.text.SetFont(wx.Font(wx.FontInfo(11).Family(wx.FONTFAMILY_TELETYPE)))
        root.Add(self.text, 1, wx.EXPAND | wx.TOP, 2)
        if name and os.path.exists(plans.script_path(name)):
            with open(plans.script_path(name), encoding="utf-8") as fh:
                self.text.SetValue(fh.read())
        else:
            self.text.SetValue(EXAMPLE)

        row = wx.BoxSizer(wx.HORIZONTAL)
        specs: list[tuple[str, Handler]] = [
            (_("&Check (F5)"), self.on_check), (_("&Insert command..."), self.on_insert),
            (_("Command &list (F1)"), self.on_help),
            (_("Scripting &guide"), lambda e: show_text(self, _("Script guide"), guide_text("SCRIPTING.md"))),
            (_("&Save"), self.on_save),
            (_("Close"), self.on_close)]
        for label, handler in specs:
            b = wx.Button(self, label=label)
            b.Bind(wx.EVT_BUTTON, handler)
            row.Add(b, 0, wx.RIGHT, 6)
        root.Add(row, 0, wx.TOP, 8)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(root, 1, wx.EXPAND | wx.ALL, 10)
        self.SetSizer(outer)
        self.SetEscapeId(wx.ID_NONE)
        self.Bind(wx.EVT_CLOSE, self.on_close)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.saved_text = self.text.GetValue()
        self.text.SetFocus()

    def on_key(self, evt: wx.KeyEvent) -> None:
        k = evt.GetKeyCode()
        if k == wx.WXK_F1:
            self.on_help(None)
        elif k == wx.WXK_F5:
            self.on_check(None)
        elif k == wx.WXK_ESCAPE:
            self.on_close(None)
        elif k == ord("S") and evt.ControlDown():
            self.on_save(None)
        else:
            evt.Skip()

    def problem(self) -> str | None:
        return lang.check(self.text.GetValue())

    def on_check(self, _evt: wx.Event | None = None) -> None:
        p = self.problem()
        if p is None:
            info(self, _("The script reads correctly."))
            return
        error(self, p)
        self.go_to_error(p)

    def go_to_error(self, message: str) -> None:
        try:
            n = int(message.split(":")[0].split()[-1])
        except (ValueError, IndexError):
            return
        pos = self.text.XYToPosition(0, n - 1)
        if pos >= 0:
            self.text.SetInsertionPoint(pos)
        self.text.SetFocus()

    def on_insert(self, _evt: wx.Event | None = None) -> None:
        labels = [s[0] for s in SNIPPETS]
        with modal(wx.SingleChoiceDialog(self, _("Choose a command to insert at the cursor:"), _("Insert command"),
                                   labels)) as d:
            if d.ShowModal() == wx.ID_OK:
                self.text.WriteText(SNIPPETS[d.GetSelection()][1] + "\n")
        self.text.SetFocus()

    def on_help(self, _evt: wx.Event | None = None) -> None:
        show_text(self, _("Script commands"), lang.__doc__ or "")

    def on_save(self, _evt: wx.Event | None = None) -> bool:
        name = self.name.GetValue().strip()
        if not name:
            error(self, _("Give the script a name."))
            return False
        p = self.problem()
        if p and not ask(self, _("The script has a mistake:\n{p}\n\nSave it anyway?", p=p)):
            return False
        if name != self.original and \
                any(n.lower() == name.lower() for n in plans.script_names()) and \
                not plans.same_file(plans.script_path(self.original or ""), plans.script_path(name)) and \
                not ask(self, _("A script called {name} exists. Replace it?", name=name)):
            return False
        if not save_file(self, jsonio.save_text, plans.script_path(name), self.text.GetValue()):
            return False
        if self.original and self.original != name and \
                not plans.same_file(plans.script_path(self.original), plans.script_path(name)) and \
                os.path.exists(plans.script_path(self.original)):
            try:
                filelock.delete(plans.script_path(self.original))
            except OSError as e:
                error(self, _("The script was saved, but the old file couldn't be removed: {err}", err=e))
        self.original = name
        self.saved_text = self.text.GetValue()
        self.SetTitle(_("Script: {name}", name=name))
        return True

    def on_close(self, _evt: wx.Event | None = None) -> None:
        if self.text.GetValue() != self.saved_text:
            answer = wx.MessageBox(_("Save changes to the script?"), _("Uncle Farmer"),
                                   wx.YES_NO | wx.CANCEL | wx.ICON_QUESTION, self)
            if answer == wx.CANCEL:
                return
            if answer == wx.YES and not self.on_save(None):
                return
        self.EndModal(wx.ID_OK)
