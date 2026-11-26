"""Run, Character chain: the characters that farm one after the other in one game, in order, each with
its plan or its Autopilot day, and what happens when every one has farmed (engine/chain.py)."""

from __future__ import annotations

from collections.abc import Sequence

import wx

from .. import settings
from ..engine import chain
from ..i18n import N_, _
from .common import Dialog, Form, buttons, error, info, labeled, ok_cancel

END_LABELS = (("stay", N_("Stay logged in on the last character")), ("close", N_("Close the game")))
AUTOPILOT = "autopilot"


def row_label(row: chain.Row, done: bool) -> str:
    what = _("its Autopilot day") if row.do == AUTOPILOT else _("plan {plan}", plan=row.plan)
    text = _("{name}: {what}", name=settings.profile_display(row.profile), what=what)
    return _("{row} (done today)", row=text) if done else text


class AddRowDialog(Dialog):
    """One more character for the chain, and what it does."""

    def __init__(self, parent: wx.Window, characters: Sequence[str], plans: Sequence[str],
                 taken: Sequence[str] = ()) -> None:
        super().__init__(parent, title=_("Add a character to the chain"))
        # one row per character: its done mark is by character, so a second row would never run
        self.characters = [c for c in characters if not any(settings.same_character(c, t) for t in taken)]
        self.plans = list(plans)
        self.row: chain.Row | None = None
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.who = f.choice(_("C&haracter:"), [settings.profile_display(c) for c in self.characters])
        self.what = f.choice(_("&What it does:"), [_("Its Autopilot day")] + [_("Plan {plan}", plan=p) for p in self.plans])
        root.Add(f.grid, 0, wx.EXPAND | wx.ALL, 12)
        ok_cancel(self, root, _("&Add"))
        self.SetSizerAndFit(root)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)

    def on_ok(self, _evt: wx.CommandEvent) -> None:
        i, w = self.who.GetSelection(), self.what.GetSelection()
        if not 0 <= i < len(self.characters):
            error(self, _("Choose a character. Only characters whose account the bot knows can be in the chain: "
                          "log in to one once by hand."))
            return
        self.row = (chain.Row(self.characters[i], AUTOPILOT) if w <= 0
                    else chain.Row(self.characters[i], "plan", self.plans[w - 1]))
        self.EndModal(wx.ID_OK)


class ChainDialog(Dialog):
    """The chain's rows and its end. Save keeps them; Start the chain saves and starts it (ID_YES)."""

    def __init__(self, parent: wx.Window, characters: Sequence[str], plans: Sequence[str]) -> None:
        super().__init__(parent, title=_("Character chain"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.characters, self.plans = list(characters), list(plans)
        self.rows, end = chain.load()
        root = wx.BoxSizer(wx.VERTICAL)
        panel = wx.Panel(self)
        box = wx.BoxSizer(wx.VERTICAL)
        note = wx.StaticText(panel, label=_(
            "The characters farm one after the other in the game selected when the chain starts. Each one is "
            "logged in by itself (its saved account), does its plan or its Autopilot day, and is done for the "
            "game's day when its run ends by itself; then the next one comes. Stop ends the whole chain. A "
            "character done with more than 10 minutes of drop time left is done again once the others are."))
        note.Wrap(560)
        box.Add(note, 0, wx.BOTTOM, 8)
        self.list = labeled(panel, box, _("Characters, in &order:"), lambda w: wx.ListBox(w, size=wx.Size(-1, 160)), 1)
        buttons(panel, box, (_("A&dd..."), self.on_add), (_("&Remove"), self.on_remove),
                (_("Move &up"), lambda e: self.move(-1)), (_("Move do&wn"), lambda e: self.move(1)))
        f = Form(panel)
        self.end = f.choice(_("At the &end of the chain:"), [_(label) for _key, label in END_LABELS],
                            [k for k, _label in END_LABELS].index(end))
        box.Add(f.grid, 0, wx.TOP, 8)
        buttons(panel, box, (_("&Let every character farm again today"), self.on_again))
        panel.SetSizer(box)
        root.Add(panel, 1, wx.EXPAND | wx.ALL, 12)
        ok_cancel(self, root, _("&Save"), extra=[(_("Start the c&hain"), self.on_start)])
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(560, 420))
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.show()

    def show(self, select: int = 0) -> None:
        done = chain.done_today()
        self.list.Set([row_label(r, r.profile in done) for r in self.rows])
        if self.rows:
            self.list.SetSelection(max(0, min(select, len(self.rows) - 1)))

    def on_add(self, _evt: object = None) -> None:
        if not self.characters:
            error(self, _("No character can be logged in by the bot yet: log in to each one once by hand, so its "
                          "account is known."))
            return
        if all(any(settings.same_character(c, r.profile) for r in self.rows) for c in self.characters):
            error(self, _("Every character the bot can log in is in the chain already."))
            return
        with AddRowDialog(self, self.characters, self.plans, [r.profile for r in self.rows]) as d:
            if d.ShowModal() == wx.ID_OK and d.row is not None:
                self.rows.append(d.row)
                self.show(len(self.rows) - 1)
                self.list.SetFocus()

    def on_remove(self, _evt: object = None) -> None:
        i = self.list.GetSelection()
        if 0 <= i < len(self.rows):
            del self.rows[i]
            self.show(i)
            self.list.SetFocus()

    def move(self, step: int) -> None:
        i = self.list.GetSelection()
        j = i + step
        if i < 0 or not 0 <= j < len(self.rows):
            return
        self.rows[i], self.rows[j] = self.rows[j], self.rows[i]
        self.show(j)
        self.list.SetFocus()

    def on_again(self, _evt: object = None) -> None:
        chain.clear()
        self.show(max(0, self.list.GetSelection()))
        info(self, _("Every character of the chain can farm again today."))

    def save(self) -> bool:
        if not self.rows:
            error(self, _("Add at least one character to the chain."))
            return False
        try:
            chain.save(self.rows, END_LABELS[max(0, self.end.GetSelection())][0])
        except OSError as e:
            error(self, _("The chain couldn't be saved: {err}", err=e))
            return False
        return True

    def on_ok(self, _evt: wx.CommandEvent) -> None:
        if self.save():
            self.EndModal(wx.ID_OK)

    def on_start(self, _evt: object = None) -> None:
        if self.save():
            self.EndModal(wx.ID_YES)
