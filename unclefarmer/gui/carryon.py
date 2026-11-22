"""The offer to carry on the runs that didn't finish (engine/resume.py), when Uncle Farmer starts.

It carries on by itself once its countdown is over, so a computer that restarted in the night
farms on with nobody there; Escape or "Don't carry on" says no.
"""

from __future__ import annotations

import wx

from ..i18n import _
from .common import buttons


class CarryOnDialog(wx.Dialog):
    def __init__(self, parent: wx.Window | None, lines: list[str], seconds: int) -> None:
        super().__init__(parent, title=_("Carry on the interrupted runs"))
        box = wx.BoxSizer(wx.VERTICAL)
        text = "\n".join([_("These runs didn't finish:"), *lines, "",
                          _("They carry on where they were in {n} seconds.", n=seconds)])
        box.Add(wx.StaticText(self, label=text), 0, wx.ALL, 10)
        go, no = buttons(self, box, (_("&Carry on now"), lambda e: self.EndModal(wx.ID_OK)),
                         (_("&Don't carry on"), lambda e: self.EndModal(wx.ID_CANCEL)))
        no.SetId(wx.ID_CANCEL)          # Escape says no
        go.SetDefault()
        self.SetEscapeId(wx.ID_CANCEL)
        self.SetSizerAndFit(box)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self._time_up(), self.timer)
        self.timer.StartOnce(seconds * 1000)
        go.SetFocus()

    def _time_up(self) -> None:
        if self.IsModal():
            self.EndModal(wx.ID_OK)

    @classmethod
    def carry_on(cls, parent: wx.Window | None, lines: list[str], seconds: int) -> bool:
        """True to carry on: the button, Enter, or the countdown running out."""
        d = cls(parent, lines, seconds)
        try:
            return d.ShowModal() == wx.ID_OK
        finally:
            d.timer.Stop()
            d.Destroy()
