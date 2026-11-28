"""File, Keyboard shortcuts: every action of the bot with its keys and where they work, the same
table as shortcuts.txt (unclefarmer/shortcuts.py). The keys are typed as text ("Ctrl+Shift+R"), which
a screen reader reads back as it is. Press the keys catches them instead, for players who would rather
press them (a player asked for it): a small window takes the next keys pressed, Escape leaves it, and
they go into the Keys box, checked as typed ones are. NVDA's own keys (with Insert or Caps Lock) never
reach it. Nothing is kept until Save, which writes the file and puts the keys to work at once."""

from __future__ import annotations

import os
from typing import Any

import wx

from .. import shortcuts
from ..i18n import _
from .common import Dialog, Form, buttons, error, first_selected, labeled, ok_cancel, select_row

WHERE = shortcuts.WHERE
_MODIFIER_KEYS = {wx.WXK_CONTROL, wx.WXK_SHIFT, wx.WXK_ALT, wx.WXK_WINDOWS_LEFT, wx.WXK_WINDOWS_RIGHT,
                  wx.WXK_WINDOWS_MENU, wx.WXK_CAPITAL, wx.WXK_NUMLOCK, wx.WXK_SCROLL, wx.WXK_RAW_CONTROL}
_KEY_NAMES: dict[int, str] = {wx.WXK_PAUSE: "Pause", wx.WXK_SPACE: "Space", wx.WXK_INSERT: "Insert", wx.WXK_DELETE: "Delete",
              wx.WXK_HOME: "Home", wx.WXK_END: "End", wx.WXK_PAGEUP: "PageUp", wx.WXK_PAGEDOWN: "PageDown",
              wx.WXK_UP: "Up", wx.WXK_DOWN: "Down", wx.WXK_LEFT: "Left", wx.WXK_RIGHT: "Right",
              wx.WXK_TAB: "Tab", wx.WXK_RETURN: "Enter", wx.WXK_BACK: "Backspace",
              wx.WXK_NUMPAD_ENTER: "Enter"}


# keys a screen reader user moves with to read the window's text: never caught alone (with Ctrl they are)
_READING_KEYS = {wx.WXK_UP, wx.WXK_DOWN, wx.WXK_LEFT, wx.WXK_RIGHT, wx.WXK_HOME, wx.WXK_END, wx.WXK_PAGEUP,
                 wx.WXK_PAGEDOWN, wx.WXK_TAB}


def pressed(code: int, ctrl: bool, alt: bool, shift: bool) -> str | None:
    """The keys pressed as the Keys box takes them ("Ctrl+Shift+R"), or None while only Ctrl, Alt or
    Shift is down (the key to come with them is waited for) and for the keys that read the window
    (an arrow, Home, End, PageUp, PageDown or Tab without Ctrl: no shortcut can be one of those). A key
    the bot can't use is named all the same, so the box says why (shortcuts.canonical)."""
    if code in _MODIFIER_KEYS or (code in _READING_KEYS and not ctrl):
        return None
    if wx.WXK_F1 <= code <= wx.WXK_F24:
        key = "F%d" % (code - wx.WXK_F1 + 1)
    elif code in _KEY_NAMES:
        key = _KEY_NAMES[code]
    elif wx.WXK_NUMPAD0 <= code <= wx.WXK_NUMPAD9:
        key = "Numpad%d" % (code - wx.WXK_NUMPAD0)
    elif 32 < code < 127:
        key = chr(code).upper()
    else:
        key = "key %d" % code
    return "+".join([m for m, down in (("Ctrl", ctrl), ("Alt", alt), ("Shift", shift)) if down] + [key])


class KeyCatcher(Dialog):
    """Takes the next keys pressed (with Ctrl, Alt or Shift); Escape leaves without any."""

    def __init__(self, parent: wx.Window, action: str) -> None:
        super().__init__(parent, title=_("Press the keys"))
        self.keys = ""
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.say = f.readonly(_("Press the keys:"))        # one line: NVDA reads all of it on focus
        self.say.SetValue(_("Press the keys for {action} now, with Ctrl, Alt or Shift if you want them. "
                            "Escape: leave without changing them.", action=action))
        root.Add(f.grid, 0, wx.EXPAND | wx.ALL, 12)
        self.SetSizerAndFit(root)
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.say.SetFocus()

    def on_key(self, evt: wx.KeyEvent) -> None:
        code = evt.GetKeyCode()
        if code == wx.WXK_ESCAPE and not (evt.ControlDown() or evt.AltDown() or evt.ShiftDown()):
            self.EndModal(wx.ID_CANCEL)
            return
        got = pressed(code, evt.ControlDown(), evt.AltDown(), evt.ShiftDown())
        if got is None:
            return                    # Ctrl, Alt or Shift alone (the key comes next), or a key that reads
        self.keys = got
        self.EndModal(wx.ID_OK)


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
        buttons(self, box, (_("&Press the keys..."), lambda e: self.catch_keys()),
                (_("Default for &this one"), lambda e: self.default_one()),
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
    def catch_keys(self) -> None:
        """The keys pressed in a small window, into the Keys box (checked as typed ones are)."""
        i = self.current()
        if i is None:
            return
        with KeyCatcher(self, shortcuts.label(shortcuts.ACTIONS[i])) as d:
            got = d.keys if d.ShowModal() == wx.ID_OK else ""
        if got:
            self.keys.SetValue(got)       # (its text event checks it and fills the row)
        self.keys.SetFocus()

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
