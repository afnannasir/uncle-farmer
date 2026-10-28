"""Private messages: who is trusted, how strangers are answered, and the reply lines themselves."""

from __future__ import annotations

from typing import Any

import wx

from .. import messages
from ..i18n import _
from .common import Dialog, group, labeled, ok_cancel, page, save_file, tabs

MODES = ["running", "always", "never"]
SPEAK = ["trusted", "everyone", "never"]


class MessagesDialog(Dialog):
    def __init__(self, parent: wx.Window, app_settings: dict[str, Any]) -> None:
        super().__init__(parent, title=_("Private messages"), size=wx.Size(720, 640),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.s = app_settings
        c = messages.config()
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        panel, box = page(nb, _("Answering"))
        f = group(panel, box, _("Strangers"))
        self.mode = f.choice(_("&Answer strangers:"), [_("While a bot runs"), _("Always"), _("Never")],
                             MODES.index(c["pm_replies"]) if c["pm_replies"] in MODES else 0)
        self.max = f.number(_("&Replies before the goodbye:"), c["pm_max_replies"], 1, 20)
        self.quiet = f.number(_("Then stay &quiet for (minutes):"), c["pm_quiet_minutes"], 1, 1440)
        delay = (list(c["pm_delay"]) + [0, 0])[:2]
        self.lo = f.number(_("&Delay before replying, from (seconds):"), delay[0], 0, 120)
        self.hi = f.number(_("To (seconds):"), delay[1], 0, 300)
        f = group(panel, box, _("Trusted players"))
        self.trusted = f.text(_("&Names or ids (separated by commas):"), ", ".join(str(n) for n in c["pm_trusted"]))
        self.commands = f.check(_("Accept &commands from them"), c["pm_commands"])
        self.commands_in_front = f.check(_("Take their commands while the game &window is in front"),
                                         c["pm_commands_in_front"])
        self.invites = f.check(_("Accept their team &invites"), c["pm_accept_invites"])
        self.away = f.check(_("Tell them now and then that I'm a&way"), c["pm_away_note"])
        f = group(panel, box, _("Speech"))
        self.speak = f.choice(_("S&peak incoming messages:"), [_("From trusted players"), _("From everyone"),
                                                               _("Never")],
                              SPEAK.index(c["pm_speak"]) if c["pm_speak"] in SPEAK else 0)

        panel, box = page(nb, _("Reply lines"))
        self.replies = labeled(panel, box, _("&Replies, one per line:"),
                               lambda w: wx.TextCtrl(w, value="\n".join(messages.replies()),
                                                     style=wx.TE_MULTILINE | wx.TE_DONTWRAP), 2)
        self.goodbyes = labeled(panel, box, _("&Goodbyes, one per line:"),
                                lambda w: wx.TextCtrl(w, value="\n".join(messages.goodbyes()),
                                                      style=wx.TE_MULTILINE | wx.TE_DONTWRAP), 1)
        self.count = wx.StaticText(panel, label="")
        box.Add(self.count, 0, wx.TOP, 8)
        self.call_by_name = wx.CheckBox(panel, label=_("&Call them by their name"))
        self.call_by_name.SetValue(c.get("pm_call_by_name", True))
        box.Add(self.call_by_name, 0, wx.TOP, 8)
        box.Add(wx.StaticText(panel, label=_("{name} in a line becomes the player's name; a line without it starts "
                                             "with their name while this is ticked.")), 0, wx.TOP, 4)
        for t in (self.replies, self.goodbyes):
            t.Bind(wx.EVT_TEXT, lambda e: self.recount())
        self.recount()

        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        ok_cancel(self, root, _("Save"))
        self.SetSizer(root)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)

    @staticmethod
    def lines(ctrl: wx.TextCtrl) -> list[str]:
        return [l.strip() for l in ctrl.GetValue().splitlines() if l.strip()]

    def recount(self) -> None:
        self.count.SetLabel(_("{r} replies, {g} goodbyes", r=len(self.lines(self.replies)),
                              g=len(self.lines(self.goodbyes))))

    def on_ok(self, evt: wx.CommandEvent) -> None:
        s = self.s
        s["pm_replies"] = MODES[max(0, self.mode.GetSelection())]
        s["pm_trusted"] = [n.strip() for n in self.trusted.GetValue().replace(";", ",").split(",") if n.strip()]
        s["pm_commands"] = self.commands.GetValue()
        s["pm_commands_in_front"] = self.commands_in_front.GetValue()
        s["pm_accept_invites"] = self.invites.GetValue()
        s["pm_away_note"] = self.away.GetValue()
        s["pm_call_by_name"] = self.call_by_name.GetValue()
        s["pm_max_replies"] = self.max.GetValue()
        s["pm_quiet_minutes"] = self.quiet.GetValue()
        lo, hi = self.lo.GetValue(), self.hi.GetValue()
        s["pm_delay"] = [min(lo, hi), max(lo, hi)]
        s["pm_speak"] = SPEAK[max(0, self.speak.GetSelection())]
        if not save_file(self, messages.save_lines, messages.REPLIES_FILE, self.lines(self.replies)):
            return
        if not save_file(self, messages.save_lines, messages.GOODBYES_FILE, self.lines(self.goodbyes)):
            return
        self.EndModal(wx.ID_OK)
