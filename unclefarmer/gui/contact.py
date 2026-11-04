"""Help, Contact the developer: the address to write to, with buttons to write an email or copy it."""

from __future__ import annotations

import webbrowser

import wx

from .. import __version__, contact
from ..i18n import _
from .common import Dialog, copy_text, error, labeled


def message() -> str:
    return _("Questions, ideas, or want to donate? Write to the developer at {email}. Bugs are best reported "
             "on GitHub, so everyone can see the fix: {issues}", email=contact.EMAIL, issues=contact.ISSUES)


def write_email() -> bool:
    """Open the player's email program on a new message to the developer."""
    return webbrowser.open(contact.mailto("Uncle Farmer %s" % __version__))


class ContactDialog(Dialog):
    def __init__(self, parent: wx.Window) -> None:
        super().__init__(parent, title=_("Contact the developer"))
        root = wx.BoxSizer(wx.VERTICAL)
        box = wx.BoxSizer(wx.VERTICAL)
        about = wx.StaticText(self, label=message())
        about.Wrap(460)
        box.Add(about, 0, wx.TOP, 4)
        self.address = labeled(self, box, _("&Email address:"),
                               lambda p: wx.TextCtrl(p, value=contact.EMAIL, style=wx.TE_READONLY))
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, handler in ((_("&Write an email"), self.on_write), (_("&Copy the address"), self.on_copy)):
            b = wx.Button(self, label=label)
            b.Bind(wx.EVT_BUTTON, handler)
            row.Add(b, 0, wx.RIGHT, 8)
        row.AddStretchSpacer()
        row.Add(wx.Button(self, wx.ID_CANCEL, _("&Close")))
        box.Add(row, 0, wx.EXPAND | wx.TOP, 12)
        root.Add(box, 1, wx.EXPAND | wx.ALL, 12)
        self.SetSizerAndFit(root)
        self.address.SetFocus()

    def on_write(self, evt: wx.CommandEvent) -> None:
        if not write_email():
            error(self, _("No email program opened. Write to {email} from your email.", email=contact.EMAIL))

    def on_copy(self, evt: wx.CommandEvent) -> None:
        if copy_text(contact.EMAIL):
            wx.MessageBox(_("Copied {email}.", email=contact.EMAIL), _("Contact the developer"), wx.OK, self)
        else:
            error(self, _("Couldn't copy it. The address is {email}", email=contact.EMAIL))
