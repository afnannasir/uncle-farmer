"""Tools, XP loser: die to guards on purpose until the next level needs as much experience as the
player sets (engine/xploss.py). Saved in the character's profile; the Triggers tab can run it before
or after farming. Check only reads the numbers and looks for guards, changing nothing."""

from __future__ import annotations

from typing import Any

import wx

from .. import settings
from ..engine import xploss
from ..i18n import _
from .common import Dialog, Form, Reader, save_file, with_code
from .crafting import CHECK, START


def needed_now(game: Reader) -> int | None:
    """The experience the next level needs, from the game's last character sheet reading."""
    try:
        s = game()
    except Exception:          # no game, or the agent busy: the window works without the number
        return None
    me: dict[str, Any] = (s or {}).get("self") or {}
    sheet: dict[str, Any] = me.get("sheet") or {}
    v = sheet.get("xp_needed")
    return int(v) if isinstance(v, (int, float)) else None


class XpLoserDialog(Dialog):
    def __init__(self, parent: wx.Window, character: str, game: Reader) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("XP loser"),
                                         name=settings.profile_display(character)),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.p = settings.load_profile(character)
        c = self.cfg = xploss.config(self.p)
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        now = needed_now(game)
        f.readonly(_("Now:"), _("the next level needs {n} experience", n=now) if now is not None and now >= 0
                   else _("the top level - nothing to lose") if now is not None
                   else _("not known yet (the bot reads it from the character sheet when it starts)"))
        self.mode = f.choice(_("&Lose experience:"), [_(m) for m in xploss.MODE_NAMES],
                             xploss.MODES.index(c["mode"]) if c["mode"] in xploss.MODES else 0)
        start = int(c["until_needed"] if c["mode"] == "until" else c["lose"]) or (now or 0)
        self.amount = f.number(_("&Experience:"), start, 0, 2_000_000_000)
        self.max_deaths = f.number(_("Stop after at most this many &deaths:"), int(c["max_deaths"]), 1, 999)
        f.readonly(_("How it works:"), _(
            "The bot finds a guard (in sight, where one was seen before, or in the nearest town), switches "
            "the PK mode to all mode, hits the guard once and lets it kill the character: no potions, no "
            "heals, no fighting back. It respawns in the city and goes again until the next level needs at "
            "least that much experience. The pet is put away first, and the PK mode is put back at the end. "
            "Deaths wear your gear like any death; a black-name character (Morality -1000 or lower) can "
            "lose worn gear when it dies, so the XP loser won't run for one."), lines=7)
        root.Add(f.grid, 1, wx.EXPAND | wx.ALL, 12)
        buttons = wx.BoxSizer(wx.HORIZONTAL)
        for label, code in ((_("Save"), wx.ID_OK), (_("&Check only"), CHECK), (_("Sta&rt"), START)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, with_code(self.done, code))
            buttons.Add(btn, 0, wx.RIGHT, 8)
        buttons.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0)
        root.Add(buttons, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(560, 420))

    def done(self, code: int) -> None:
        mode = xploss.MODES[max(0, self.mode.GetSelection())]
        amount = self.amount.GetValue()
        if code == START and amount <= 0:
            wx.MessageBox(_("Type how much experience: the most the next level should need, or how much to lose."),
                          _("XP loser"), wx.OK | wx.ICON_INFORMATION, self)
            self.amount.SetFocus()
            return
        self.cfg.update(mode=mode, max_deaths=self.max_deaths.GetValue())
        self.cfg["until_needed" if mode == "until" else "lose"] = amount
        self.p["xp_loser"] = self.cfg
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)
