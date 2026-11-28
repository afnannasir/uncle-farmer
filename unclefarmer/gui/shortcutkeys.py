"""File, Keyboard shortcuts: every action of the bot with its keys and where they work, the same
table as shortcuts.txt (unclefarmer/shortcuts.py). The keys are typed as text ("Ctrl+Shift+R"), which
a screen reader reads back as it is: no key is caught, so NVDA's own keys never get in the way.
Nothing is kept until Save, which writes the file and puts the keys to work at once."""

from __future__ import annotations

import os
from typing import Any

import wx

from .. import shortcuts
from ..i18n import _
from .common import Dialog, Form, buttons, error, first_selected, labeled, ok_cancel, select_row

WHERE = shortcuts.WHERE


class ShortcutsDialog(Dialog):
    def __init__(self, parent: wx.Window, table: shortcuts.Table) -> None:
        super().__init__(parent, title=_("Keyboard shortcuts"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.table: shortcuts.Table = {k: shortcuts.Shortcut(v.keys, v.where) for k, v in table.items()}
        self.typed: dict[str, str] = {k: v.keys for k, v in self.table.items()}    # as typed, checked on Save
        root = wx.BoxSizer(wx.VERTICAL)
        box = wx.BoxSizer(wx.VERTICAL)
        self.rows = labeled(self, box, _("&Shortcuts:"), lambda w: wx.ListCtrl(
            w, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=wx.Size(640, 300)), 1)
        for i, (title, width) in enumerate(((_("Action"), 330), (_("Keys"), 130), (_("Works"), 180))):
            self.rows.InsertColumn(i, title, width=width)
        f = Form(self)
        self.keys = f.text(_("&Keys (for example Ctrl+Shift+R or F7; empty: none):"))
        self.where = f.choice(_("&Works:"), [shortcuts.where_label(w) for w in WHERE])
        self.note = f.readonly(_("Note:"), lines=3)
        box.Add(f.grid, 0, wx.EXPAND | wx.TOP, 10)
        buttons(self, box, (_("Default for &this one"), lambda e: self.default_one()),
                (_("&All the defaults"), lambda e: self.all_defaults()),
                (_("&Open the file"), lambda e: self.open_file()),
                (_("&Read the file again"), lambda e: self.read_file()))
        root.Add(box, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        ok_cancel(self, root, _("Save"))
        self.SetSizerAndFit(root)
        self.fill()
        self.rows.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self.show())
        self.keys.Bind(wx.EVT_TEXT, lambda e: self.typed_keys())
        self.where.Bind(wx.EVT_CHOICE, lambda e: self.chose_where())
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        if self.rows.GetItemCount():
            select_row(self.rows, 0)
        self.show()

    # ---------------------------------------------------------------- the list
    def fill(self) -> None:
        self.rows.DeleteAllItems()
        for i, a in enumerate(shortcuts.ACTIONS):
            self.rows.InsertItem(i, shortcuts.label(a))
            self.label_row(i)

    def label_row(self, i: int) -> None:
        a = shortcuts.ACTIONS[i]
        typed = self.typed[a.id].strip()
        self.rows.SetItem(i, 1, typed or _("none"))
        self.rows.SetItem(i, 2, shortcuts.where_label(self.table[a.id].where) if typed else "")

    def current(self) -> int | None:
        i = first_selected(self.rows)
        return i if 0 <= i < len(shortcuts.ACTIONS) else None

    def show(self) -> None:
        """The selected action's keys and where they work, in the boxes below the list."""
        i = self.current()
        on = i is not None
        for ctrl in (self.keys, self.where):
            ctrl.Enable(on)
        if i is None:
            return
        a = shortcuts.ACTIONS[i]
        self.keys.ChangeValue(self.typed[a.id])         # (no text event: nothing changed)
        self.where.SetSelection(WHERE.index(self.table[a.id].where))
        self.check(i)

    def typed_keys(self) -> None:
        i = self.current()
        if i is not None:
            self.typed[shortcuts.ACTIONS[i].id] = self.keys.GetValue()
            self.label_row(i)
            self.check(i)

    def chose_where(self) -> None:
        i = self.current()
        if i is not None:
            a = shortcuts.ACTIONS[i]
            self.table[a.id] = shortcuts.Shortcut(self.table[a.id].keys, WHERE[max(0, self.where.GetSelection())])
            self.label_row(i)
            self.check(i)

    def check(self, i: int) -> None:
        """What to know about the selected action's keys: why they can't be used, another action that
        has them, or what working from everywhere takes away."""
        a = shortcuts.ACTIONS[i]
        self.note.SetValue(self.why_not(a.id) or self.warning(a.id))

    def why_not(self, action_id: str) -> str:
        try:
            keys = shortcuts.canonical(self.typed[action_id])
        except shortcuts.BadKeys as e:
            return str(e)
        if not keys:
            return ""
        other = next((b for b in shortcuts.ACTIONS if b.id != action_id and self.keys_of(b.id) == keys), None)
        if other is not None:
            return _("{keys} is also {action}: one of them has to change.", keys=keys, action=shortcuts.label(other))
        return ""

    def warning(self, action_id: str) -> str:
        keys = self.keys_of(action_id)
        if not keys:
            return _("No key: use the menu.")
        if self.table[action_id].where == shortcuts.EVERYWHERE:
            return _("Works everywhere: while Uncle Farmer runs, {keys} no longer reaches the game, NVDA or any "
                     "other program. A key no program uses is best, such as Pause or a key from F7 to F12 with "
                     "Ctrl or Shift.", keys=keys)
        if self.table[action_id].where == shortcuts.IN_GAMES:
            return _("Works while an Uncle Farmer window or one of the games is in front: in a game, {keys} no "
                     "longer reaches the game. In any other program, {keys} does what it always did.", keys=keys)
        return _("Works while an Uncle Farmer window is in front.")

    def keys_of(self, action_id: str) -> str:
        """The keys as typed, written the bot's way; "" when they can't be read."""
        try:
            return shortcuts.canonical(self.typed[action_id])
        except shortcuts.BadKeys:
            return ""

    # ---------------------------------------------------------------- the buttons
    def default_one(self) -> None:
        i = self.current()
        if i is None:
            return
        a = shortcuts.ACTIONS[i]
        self.table[a.id] = shortcuts.Shortcut(a.keys, a.where)
        self.typed[a.id] = a.keys
        self.label_row(i)
        self.show()
        self.keys.SetFocus()

    def all_defaults(self) -> None:
        self.set_table(shortcuts.defaults())

    def set_table(self, table: shortcuts.Table) -> None:
        self.table = table
        self.typed = {k: v.keys for k, v in table.items()}
        for i in range(len(shortcuts.ACTIONS)):
            self.label_row(i)
        self.show()

    def open_file(self) -> None:
        """The file in the player's editor, written first when there is none yet. Changes made there
        come into this window with Read the file again."""
        path = shortcuts.file_path()
        try:
            if not os.path.exists(path):
                shortcuts.save(self.table)
            os.startfile(path)
        except OSError as e:
            error(self, _("Couldn't open {path}: {err}", path=path, err=e))

    def read_file(self) -> None:
        table, problems = shortcuts.load()
        self.set_table(table)
        if problems:
            error(self, "\n".join(problems), _("Keyboard shortcuts"))

    # ---------------------------------------------------------------- Save
    def checked(self) -> shortcuts.Table | None:
        """The table as typed, or None after saying what has to change first (and selecting it)."""
        out: shortcuts.Table = {}
        for i, a in enumerate(shortcuts.ACTIONS):
            why = self.why_not(a.id)
            if why:
                select_row(self.rows, i)
                self.show()
                error(self, _("{action}: {why}", action=shortcuts.label(a), why=why), _("Keyboard shortcuts"))
                self.keys.SetFocus()
                return None
            out[a.id] = shortcuts.Shortcut(self.keys_of(a.id), self.table[a.id].where)
        return out

    def on_ok(self, _evt: Any) -> None:
        table = self.checked()
        if table is None:
            return
        try:
            shortcuts.save(table)
        except OSError as e:
            error(self, _("Couldn't save {path}: {err}", path=shortcuts.file_path(), err=e))
            return
        self.table = table
        self.EndModal(wx.ID_OK)
