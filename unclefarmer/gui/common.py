"""Building blocks that keep every window consistent and screen-reader friendly.

Every input gets a visible label placed right before it (NVDA reads that label when the input gets
focus) and the same text as its accessible name. Only native controls are used. Related fields sit
in a labelled group (wx.StaticBox), which NVDA announces when focus enters it.

Wording rules for every window: labels are short and say what the field is, not how it works; a
unit goes in brackets at the end ("Time limit (minutes):"); buttons are a verb, with "..." when
they open another window; no "0 = never" codes - an optional value is a check box that enables it.
"""

import ctypes
import os
import re
from typing import Any

import wx

from .. import paths, speech
from ..i18n import _


def _name(label):
    return label.replace("&", "").rstrip(":").strip()


def field_label(ctrl):
    """The label a Form field was built with (set by Form.add), or None for a plain control."""
    return getattr(ctrl, "label_ctrl", None)


def enable_label(ctrl, on: bool) -> None:
    """Enable or disable the label beside a Form field along with the field itself."""
    label = field_label(ctrl)
    if label is not None:
        label.Enable(bool(on))


def bind(ctrl, event, handler):
    """ctrl.Bind for the loops that mix event kinds (a choice and a spin control): Pyright cannot
    pick one PyEventBinder for the union, the control can."""
    ctrl.Bind(event, handler)


class Form:
    box: Any                    # the group's StaticBoxSizer, set by group()
    """A two-column label/control grid inside a parent, in tab order.

    Each label is created before its control: Windows screen readers name a field after the static
    text that precedes it, so the order matters, not just the layout.
    """

    def __init__(self, parent):
        self.parent = parent
        self.grid = wx.GridBagSizer(8, 12)
        self.row = 0

    def add(self, label, make):
        """make(parent) builds the control; it is called after the label exists."""
        text = wx.StaticText(self.parent, label=label)
        ctrl = make(self.parent)
        ctrl.SetName(_name(label))
        self.grid.Add(text, (self.row, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.grid.Add(ctrl, (self.row, 1), flag=wx.EXPAND)
        self.next_row()
        field: Any = ctrl
        field.label_ctrl = text       # field_label() reads it back
        return ctrl


    def next_row(self):
        self.row += 1
        if not self.grid.IsColGrowable(1):
            self.grid.AddGrowableCol(1, 1)

    def text(self, label, value="", **kw):
        return self.add(label, lambda p: wx.TextCtrl(p, value=str(value), **kw))

    def readonly(self, label, value="", lines=1):
        style = wx.TE_READONLY | (wx.TE_MULTILINE if lines > 1 else 0)
        size = wx.Size(-1, 20 * lines + 8) if lines > 1 else wx.DefaultSize
        return self.add(label, lambda p: wx.TextCtrl(p, value=str(value), style=style, size=size))

    def number(self, label, value, lo, hi):
        return self.add(label, lambda p: wx.SpinCtrl(p, min=lo, max=hi, initial=int(value)))

    def decimal(self, label, value, lo, hi, step=None):
        """A plain text box for a number with a fraction. (wx's decimal spin box is a composite
        control whose inner edit box has no label for screen readers, so it isn't used.)"""
        c = self.add(label, lambda p: wx.TextCtrl(p, value=("%g" % float(value))))
        field: Any = c            # the number and name ride along for read_decimal
        field.range = (lo, hi)
        field.label = _name(label)
        return c

    def choice(self, label, items, selected=0):
        def make(p):
            c = wx.Choice(p, choices=items)
            c.SetSelection(selected if 0 <= selected < len(items) else 0)
            return c
        return self.add(label, make)

    def check(self, label, value):
        c = wx.CheckBox(self.parent, label=label)
        c.SetValue(bool(value))
        self.grid.Add(c, (self.row, 0), span=(1, 2))
        self.next_row()
        return c

    def optional(self, check_label, on, value_label, value, lo, hi, decimal=False):
        """A check box that switches a value on, then the value (disabled while unticked).
        Returns (check, value control)."""
        box = self.check(check_label, on)
        ctrl = (self.decimal if decimal else self.number)(value_label, value, lo, hi)

        def sync(_evt=None):
            ctrl.Enable(box.GetValue())
            label = field_label(ctrl)
            if label is not None:
                label.Enable(box.GetValue())
        box.Bind(wx.EVT_CHECKBOX, sync)
        sync()
        return box, ctrl


def group(parent, sizer, title, proportion=0):
    """A labelled group of fields: returns a Form whose controls live in the group's box."""
    box = wx.StaticBoxSizer(wx.VERTICAL, parent, title)
    form = Form(box.GetStaticBox())
    box.Add(form.grid, 0, wx.EXPAND | wx.ALL, 8)
    sizer.Add(box, proportion, wx.EXPAND | wx.TOP, 10)
    form.box = box
    return form


class _TabsOnly(wx.Accessible):
    """A Windows tab control holds its pages as child windows (and a hidden scroll arrow), and
    tells a screen reader it has that many children: NVDA read the third of three tabs as "3 of 7".
    This one answers with the number of tabs; everything else stays the control's own."""

    def __init__(self, notebook):
        super().__init__(notebook)
        self.notebook = notebook

    def GetChildCount(self):
        return wx.ACC_OK, self.notebook.GetPageCount()


def tabs(parent):
    """A wx.Notebook whose tabs a screen reader counts right ("1 of 3", not "1 of 7")."""
    nb = wx.Notebook(parent)
    notebook: Any = nb
    notebook._tabs_only = _TabsOnly(nb)     # keep it alive alongside the control
    nb.SetAccessible(notebook._tabs_only)
    return nb


def page(notebook, title):
    """A notebook page with a padded vertical sizer: returns (panel, sizer)."""
    panel = wx.Panel(notebook)
    outer = wx.BoxSizer(wx.VERTICAL)
    inner = wx.BoxSizer(wx.VERTICAL)
    outer.Add(inner, 1, wx.EXPAND | wx.ALL, 12)
    panel.SetSizer(outer)
    notebook.AddPage(panel, title)
    return panel, inner


def read_decimal(ctrl):
    """The number in a Form.decimal box, or ValueError with a message for the user."""
    field: Any = ctrl
    lo, hi = field.range
    try:
        v = float(ctrl.GetValue().strip().replace(",", "."))
    except ValueError:
        raise ValueError(_("{field}: enter a number, for example 1.5", field=field.label))
    if not lo <= v <= hi:
        raise ValueError(_("{field}: enter a number from {lo} to {hi}", field=field.label, lo=lo, hi=hi))
    return v


def pick(items, value, default=0):
    """The index of value in items, or default for a value this version doesn't know.

    Profiles and plans are plain files a player can edit (or keep from an older version), so a
    choice read from one may no longer be in the list: a window must open with the closest choice
    instead of failing."""
    try:
        return items.index(value)
    except ValueError:
        return default


def save_file(parent, writer, *args, **kwargs):
    """Write something for an editor and tell the player if it couldn't be written.

    Saves are atomic (a failed one keeps the old file), but a full disk or a read-only folder used
    to leave a dialog that simply did nothing. Returns True when the writer succeeded."""
    try:
        writer(*args, **kwargs)
        return True
    except (OSError, ValueError) as e:
        error(parent, _("Couldn't save: {err}", err=e))
        return False


def labeled(parent, sizer, label, make, proportion=0):
    """A label above a control (for lists and big text boxes); make(parent) builds the control."""
    sizer.Add(wx.StaticText(parent, label=label), 0, wx.TOP, 8)
    ctrl = make(parent)
    ctrl.SetName(_name(label))
    sizer.Add(ctrl, proportion, wx.EXPAND | wx.TOP, 4)
    return ctrl


def buttons(parent, sizer, *specs):
    """A row of buttons: specs are (label, handler) pairs. Returns the buttons."""
    row = wx.BoxSizer(wx.HORIZONTAL)
    out = []
    for label, handler in specs:
        b = wx.Button(parent, label=label)
        b.Bind(wx.EVT_BUTTON, handler)
        row.Add(b, 0, wx.RIGHT, 8)
        out.append(b)
    sizer.Add(row, 0, wx.TOP, 10)
    return out


def ok_cancel(dialog, sizer, ok_label=None, extra=None):
    """The dialog's closing row. extra: (label, handler) for one button on the left (Advanced...)."""
    row = wx.BoxSizer(wx.HORIZONTAL)
    if extra and not isinstance(extra[0], tuple):
        extra = [extra]
    for label, handler in extra or ():
        b = wx.Button(dialog, label=label)
        b.Bind(wx.EVT_BUTTON, handler)
        row.Add(b, 0, wx.RIGHT, 8)
    row.AddStretchSpacer()
    std = dialog.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
    if ok_label:
        std.GetAffirmativeButton().SetLabel(ok_label)
    row.Add(std, 0)
    sizer.Add(row, 0, wx.EXPAND | wx.ALL, 12)


def error(parent, message, title=None):
    wx.MessageBox(message, title or _("Uncle Farmer"), wx.OK | wx.ICON_ERROR, parent)


def info(parent, message, title=None):
    wx.MessageBox(message, title or _("Uncle Farmer"), wx.OK | wx.ICON_INFORMATION, parent)


def ask(parent, message, title=None):
    return wx.MessageBox(message, title or _("Uncle Farmer"), wx.YES_NO | wx.ICON_QUESTION, parent) == wx.YES


def guide_text(name):
    """A guide from the docs folder as plain text a screen reader reads well: no markdown marks,
    table rows as "name: meaning"."""
    try:
        with open(os.path.join(paths.DOCS, name), encoding="utf-8") as f:
            md = f.read()
    except OSError:
        return _("The guide {name} is missing from the docs folder.", name=name)
    out = []
    for line in md.splitlines():
        if re.match(r"^\|[-| ]+\|$", line.strip()):
            continue                                     # a table's |---|---| rule
        if line.strip().startswith("|"):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            line = ": ".join(c for c in cells if c)
        line = re.sub(r"^#+\s*", "", line)
        line = line.replace("`", "").replace("**", "")
        out.append(line)
    return "\n".join(out)


def show_text(parent, title, text):
    """A read-only text window: help, script previews. Escape closes it."""
    dlg = wx.Dialog(parent, title=title, size=wx.Size(720, 560), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
    s = wx.BoxSizer(wx.VERTICAL)
    box = wx.TextCtrl(dlg, value=text, style=wx.TE_MULTILINE | wx.TE_READONLY | wx.TE_DONTWRAP)
    box.SetName(title)
    s.Add(box, 1, wx.EXPAND | wx.ALL, 8)
    row = dlg.CreateStdDialogButtonSizer(wx.CLOSE)
    dlg.FindWindow(wx.ID_CLOSE).Bind(wx.EVT_BUTTON, lambda e: dlg.EndModal(wx.ID_CLOSE))
    dlg.SetEscapeId(wx.ID_CLOSE)
    s.Add(row, 0, wx.EXPAND | wx.ALL, 8)
    dlg.SetSizer(s)
    box.SetFocus()
    dlg.ShowModal()
    dlg.Destroy()


# ---------------------------------------------------------------- keys
KNOWN_KEYS = set("abcdefghijklmnopqrstuvwxyz0123456789") | {
    ".", ",", ";", "/", "'", "-", "=", "[", "]", "\\", "space", "enter", "esc", "tab", "backspace",
    "delete", "up", "down", "left", "right", "lctrl", "rctrl", "alt", "shift", "`", "capslock"} | {
    "f%d" % i for i in range(1, 13)} | {"num%d" % i for i in range(10)}


def key_ok(text):
    """One key, or a combination like lctrl+alt+up, in the names the agent understands."""
    parts = text.strip().lower().split("+")
    return bool(text.strip()) and " " not in text.strip() and all(p in KNOWN_KEYS for p in parts)


_WX_KEYS = {
    wx.WXK_SPACE: "space", wx.WXK_RETURN: "enter", wx.WXK_NUMPAD_ENTER: "enter", wx.WXK_BACK: "backspace",
    wx.WXK_DELETE: "delete", wx.WXK_UP: "up", wx.WXK_DOWN: "down", wx.WXK_LEFT: "left", wx.WXK_RIGHT: "right",
    wx.WXK_CAPITAL: "capslock",
}
_VK_LCTRL, _VK_RCTRL = 0xA2, 0xA3


def _held(vk):
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def key_from_event(evt):
    """The game's name for the key in a key-down event (with held modifiers), or None."""
    code = evt.GetKeyCode()
    if code == wx.WXK_CONTROL:
        return "rctrl" if _held(_VK_RCTRL) else "lctrl"
    if code == wx.WXK_ALT:
        return "alt"
    if code == wx.WXK_SHIFT:
        return "shift"
    if code in _WX_KEYS:
        key = _WX_KEYS[code]
    elif wx.WXK_F1 <= code <= wx.WXK_F12:
        key = "f%d" % (code - wx.WXK_F1 + 1)
    elif wx.WXK_NUMPAD0 <= code <= wx.WXK_NUMPAD9:
        key = "num%d" % (code - wx.WXK_NUMPAD0)
    else:
        ch = chr(evt.GetUnicodeKey()).lower() if evt.GetUnicodeKey() not in (0, wx.WXK_NONE) else ""
        if ch not in KNOWN_KEYS and (48 <= code <= 57 or 65 <= code <= 90):
            ch = chr(code).lower()       # Ctrl+letter arrives as a control character, not a letter
        if ch in KNOWN_KEYS:
            key = ch
        else:
            return None
    mods = []
    if evt.ControlDown():
        mods.append("rctrl" if _held(_VK_RCTRL) and not _held(_VK_LCTRL) else "lctrl")
    if evt.AltDown():
        mods.append("alt")
    if evt.ShiftDown():
        mods.append("shift")
    return "+".join(mods + [key])


class KeyDialog(wx.Dialog):
    """Change one key: press it in the Key field (Tab leaves the field), or type its name in the
    Name field. Both show the same value."""

    def __init__(self, parent, action, current):
        super().__init__(parent, title=_("Change key"))
        self.value = current
        root = wx.BoxSizer(wx.VERTICAL)
        f = Form(self)
        self.capture = f.add(_("Press the new key for {action}:", action=action),
                             lambda p: wx.TextCtrl(p, value=current, style=wx.TE_READONLY, size=wx.Size(220, -1)))
        self.typed = f.text(_("Or type its &name:"), current)
        root.Add(f.grid, 0, wx.EXPAND | wx.ALL, 12)
        ok_cancel(self, root)
        self.SetSizerAndFit(root)
        self.capture.Bind(wx.EVT_KEY_DOWN, self.on_key)
        self.capture.Bind(wx.EVT_CHAR, lambda e: None)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.capture.SetFocus()

    def on_key(self, evt):
        code = evt.GetKeyCode()
        if code == wx.WXK_TAB or (code == wx.WXK_ESCAPE and not evt.HasAnyModifiers()):
            evt.Skip()
            return
        key = key_from_event(evt)
        if key:
            self.capture.ChangeValue(key)
            self.typed.ChangeValue(key)
            speech.speak(key, interrupt=True)

    def on_ok(self, _evt):
        key = self.typed.GetValue().strip().lower()
        if not key_ok(key):
            error(self, _("'{value}' isn't a key the bot can press.", value=key))
            return
        self.value = key
        self.EndModal(wx.ID_OK)
