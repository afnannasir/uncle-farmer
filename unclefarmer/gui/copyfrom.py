"""Copy from another character: which character, which parts of its settings, and whether they add
to this one's or replace them. Bag and supplies and the character window each give their parts."""

from __future__ import annotations

from collections.abc import Sequence

import wx

from .. import settings
from ..i18n import _
from .common import Dialog, Form, error, labeled, ok_cancel


def other_characters(character: str) -> list[str]:
    """Every character with settings but this one - nor its own old name-only file."""
    return [k for k in settings.profiles() if k != character and not settings.same_character(k, character)
            and not settings.is_profile_of(character, k)]


class CopyFromDialog(Dialog):
    def __init__(self, parent: wx.Window, others: Sequence[str], parts: Sequence[tuple[str, str]]) -> None:
        """parts: (key, label to translate) of what can be copied; the first one is ticked."""
        super().__init__(parent, title=_("Copy from another character"), style=wx.DEFAULT_DIALOG_STYLE)
        self.others = list(others)
        self.keys = [k for k, _l in parts]
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.who = f.choice(_("Copy &from:"), [settings.profile_display(k) for k in self.others], 0)
        self.how = f.choice(_("&How:"), [_("Add to this character's lists (what is only here stays)"),
                                          _("Replace this character's lists with theirs")], 0)
        root.Add(f.grid, 0, wx.EXPAND | wx.ALL, 12)
        self.parts = labeled(self, root, _("&What to copy:"),
                             lambda w: wx.CheckListBox(w, choices=[_(label) for _k, label in parts],
                                                       size=wx.Size(-1, 140)), 1)
        self.parts.Check(0, True)
        ok_cancel(self, root, _("Copy"))
        self.SetSizerAndFit(root)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)

    def picked(self) -> tuple[str, list[str], bool]:
        """(the character, the parts' keys, replace)."""
        parts = [k for i, k in enumerate(self.keys) if self.parts.IsChecked(i)]
        return self.others[max(0, self.who.GetSelection())], parts, self.how.GetSelection() == 1

    def on_ok(self, _evt: wx.Event) -> None:
        if not self.picked()[1]:
            error(self, _("Tick at least one thing to copy."))
            return
        self.EndModal(wx.ID_OK)
