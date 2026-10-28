"""Building blocks that keep every window consistent and screen-reader friendly.

Every input gets a visible label placed right before it (NVDA reads that label when the input gets
focus) and the same text as its accessible name. Only native controls are used. Related fields sit
in a labelled group (wx.StaticBox), which NVDA announces when focus enters it.

Wording rules for every window: labels are short and say what the field is, not how it works; a
unit goes in brackets at the end ("Time limit (minutes):"); buttons are a verb, with "..." when
they open another window; no "0 = never" codes - an optional value is a check box that enables it.
"""

from __future__ import annotations

import contextlib
import ctypes
import os
import re
import weakref
from collections.abc import Callable, Generator, Sequence
from types import TracebackType
from typing import Any, Literal, Self, TypeVar, overload

import wx

from .. import paths, speech
from ..i18n import _

W = TypeVar("W", bound=wx.Window)
D = TypeVar("D", bound=wx.Dialog)
Handler = Callable[[wx.CommandEvent], object]      # a button's, check box's or choice's handler
Reader = Callable[[], "dict[str, Any] | None"]     # reads the game now (the main window's snapshot)


class Dialog(wx.Dialog):
    """Every dialog of ours: `with SomeDialog(...) as d` keeps the dialog's own type (wxPython's stub
    leaves wx.Dialog.__enter__ unannotated). Leaving the with destroys it, as wx's own __exit__ does."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, exc_type: type[BaseException] | None, exc_val: BaseException | None,
                 exc_tb: TracebackType | None) -> None:
        self.Destroy()


@contextlib.contextmanager
def modal(dlg: D) -> Generator[D, None, None]:
    """`with modal(wx.TextEntryDialog(...)) as d`: wx's own `with dialog as d` for its built-in dialogs
    (destroyed at the end), keeping their type."""
    try:
        yield dlg
    finally:
        dlg.Destroy()


# What the forms keep about their controls, without setting attributes on wx's own objects; an
# entry goes with its control.
_LABELS: "weakref.WeakKeyDictionary[wx.Window, wx.StaticText]" = weakref.WeakKeyDictionary()   # Form.add
_DECIMALS: "weakref.WeakKeyDictionary[wx.Window, tuple[float, float, str]]" = weakref.WeakKeyDictionary()  # range, name
_ACCESSIBLE: "weakref.WeakKeyDictionary[wx.Window, wx.Accessible]" = weakref.WeakKeyDictionary()  # kept alive


def _name(label: str) -> str:
    return label.replace("&", "").rstrip(":").strip()


def field_label(ctrl: wx.Window) -> wx.StaticText | None:
    """The label a Form field was built with (set by Form.add), or None for a plain control."""
    return _LABELS.get(ctrl)


def enable_label(ctrl: wx.Window, on: bool) -> None:
    """Enable or disable the label beside a Form field along with the field itself."""
    label = field_label(ctrl)
    if label is not None:
        label.Enable(bool(on))



class Form:
    box: wx.StaticBoxSizer      # the group's box, set by group()
    """A two-column label/control grid inside a parent, in tab order.

    Each label is created before its control: Windows screen readers name a field after the static
    text that precedes it, so the order matters, not just the layout.
    """

    def __init__(self, parent: wx.Window) -> None:
        self.parent = parent
        self.grid = wx.GridBagSizer(8, 12)
        self.row = 0

    def add(self, label: str, make: Callable[[wx.Window], W]) -> W:
        """make(parent) builds the control; it is called after the label exists."""
        text = wx.StaticText(self.parent, label=label)
        ctrl = make(self.parent)
        ctrl.SetName(_name(label))
        self.grid.Add(text, (self.row, 0), flag=wx.ALIGN_CENTER_VERTICAL)
        self.grid.Add(ctrl, (self.row, 1), flag=wx.EXPAND)
        self.next_row()
        _LABELS[ctrl] = text        # field_label() reads it back
        return ctrl


    def next_row(self) -> None:
        self.row += 1
        if not self.grid.IsColGrowable(1):
            self.grid.AddGrowableCol(1, 1)

    def text(self, label: str, value: object = "", style: int = 0, size: wx.Size = wx.DefaultSize) -> wx.TextCtrl:
        return self.add(label, lambda p: wx.TextCtrl(p, value=str(value), style=style, size=size))

    def readonly(self, label: str, value: object = "", lines: int = 1) -> wx.TextCtrl:
        style = wx.TE_READONLY | (wx.TE_MULTILINE if lines > 1 else 0)
        size = wx.Size(-1, 20 * lines + 8) if lines > 1 else wx.DefaultSize
        return self.add(label, lambda p: wx.TextCtrl(p, value=str(value), style=style, size=size))

    def number(self, label: str, value: float, lo: int, hi: int) -> wx.SpinCtrl:
        return self.add(label, lambda p: wx.SpinCtrl(p, min=lo, max=hi, initial=int(value)))

    def decimal(self, label: str, value: float, lo: float, hi: float, step: float | None = None) -> wx.TextCtrl:
        """A plain text box for a number with a fraction. (wx's decimal spin box is a composite
        control whose inner edit box has no label for screen readers, so it isn't used.)"""
        c = self.add(label, lambda p: wx.TextCtrl(p, value=("%g" % float(value))))
        _DECIMALS[c] = (lo, hi, _name(label))       # for read_decimal
        return c

    def choice(self, label: str, items: Sequence[str], selected: int = 0) -> wx.Choice:
        def make(p: wx.Window) -> wx.Choice:
            c = wx.Choice(p, choices=list(items))
            c.SetSelection(selected if 0 <= selected < len(items) else 0)
            return c
        return self.add(label, make)

    def check(self, label: str, value: object) -> wx.CheckBox:
        c = wx.CheckBox(self.parent, label=label)
        c.SetValue(bool(value))
        self.grid.Add(c, (self.row, 0), span=(1, 2))
        self.next_row()
        return c

    @overload
    def optional(self, check_label: str, on: bool, value_label: str, value: float, lo: float, hi: float,
                 decimal: Literal[False] = False) -> tuple[wx.CheckBox, wx.SpinCtrl]: ...
    @overload
    def optional(self, check_label: str, on: bool, value_label: str, value: float, lo: float, hi: float, *,
                 decimal: Literal[True]) -> tuple[wx.CheckBox, wx.TextCtrl]: ...
    def optional(self, check_label: str, on: bool, value_label: str, value: float, lo: float, hi: float,
                 decimal: bool = False) -> tuple[wx.CheckBox, wx.SpinCtrl | wx.TextCtrl]:
        """A check box that switches a value on, then the value (disabled while unticked).
        Returns (check, value control)."""
        box = self.check(check_label, on)
        ctrl: wx.SpinCtrl | wx.TextCtrl = (self.decimal(value_label, value, lo, hi) if decimal
                                          else self.number(value_label, value, int(lo), int(hi)))

        def sync(_evt: wx.Event | None = None) -> None:
            ctrl.Enable(box.GetValue())
            label = field_label(ctrl)
            if label is not None:
                label.Enable(box.GetValue())
        box.Bind(wx.EVT_CHECKBOX, sync)
        sync()
        return box, ctrl


# wxPython's ListCtrl.Select, Focus and GetFirstSelected are Python helpers that its type stub leaves
# unannotated; these make exactly the calls those helpers make, through methods the stub types.
def with_code(handler: Callable[[int], object], code: int) -> Handler:
    """A button's handler that calls handler(code): the closing buttons (Save, Check only, Start)."""
    return lambda _evt: handler(code)


def standing(snap: dict[str, Any] | None) -> tuple[int, float, float] | None:
    """(map id, x, y) of the logged-in character in a game reading, or None."""
    me: dict[str, Any] = (snap or {}).get("self") or {}
    where: dict[str, Any] = (snap or {}).get("map") or {}
    mid: int | None = where.get("id")
    if not me or mid is None:
        return None
    return mid, me["x"], me["y"]


def first_selected(lc: wx.ListCtrl) -> int:
    """The first selected row, or -1 when none is (ListCtrl.GetFirstSelected)."""
    return lc.GetNextItem(-1, wx.LIST_NEXT_ALL, wx.LIST_STATE_SELECTED)


def select_row(lc: wx.ListCtrl, row: int) -> None:
    """Select a row and give it the focus, scrolled into view (ListCtrl.Select, then Focus)."""
    lc.SetItemState(row, wx.LIST_STATE_SELECTED, wx.LIST_STATE_SELECTED)
    lc.SetItemState(row, wx.LIST_STATE_FOCUSED, wx.LIST_STATE_FOCUSED)
    lc.EnsureVisible(row)


def group(parent: wx.Window, sizer: wx.Sizer, title: str, proportion: int = 0) -> Form:
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

    def __init__(self, notebook: wx.Notebook) -> None:
        super().__init__(notebook)
        self.notebook = notebook

    def GetChildCount(self) -> tuple[wx.AccStatus, int]:
        return wx.ACC_OK, self.notebook.GetPageCount()


def tabs(parent: wx.Window) -> wx.Notebook:
    """A wx.Notebook whose tabs a screen reader counts right ("1 of 3", not "1 of 7")."""
    nb = wx.Notebook(parent)
    acc = _TabsOnly(nb)
    _ACCESSIBLE[nb] = acc          # keep it alive alongside the control
    nb.SetAccessible(acc)
    return nb


def page(notebook: wx.Notebook, title: str) -> tuple[wx.Panel, wx.BoxSizer]:
    """A notebook page with a padded vertical sizer: returns (panel, sizer)."""
    panel = wx.Panel(notebook)
    outer = wx.BoxSizer(wx.VERTICAL)
    inner = wx.BoxSizer(wx.VERTICAL)
    outer.Add(inner, 1, wx.EXPAND | wx.ALL, 12)
    panel.SetSizer(outer)
    notebook.AddPage(panel, title)
    return panel, inner


def read_decimal(ctrl: wx.TextCtrl) -> float:
    """The number in a Form.decimal box, or ValueError with a message for the user."""
    lo, hi, label = _DECIMALS[ctrl]
    try:
        v = float(ctrl.GetValue().strip().replace(",", "."))
    except ValueError:
        raise ValueError(_("{field}: enter a number, for example 1.5", field=label))
    if not lo <= v <= hi:
        raise ValueError(_("{field}: enter a number from {lo} to {hi}", field=label, lo=lo, hi=hi))
    return v


def pick(items: Sequence[Any], value: object, default: int = 0) -> int:
    """The index of value in items, or default for a value this version doesn't know.

    Profiles and plans are plain files a player can edit (or keep from an older version), so a
    choice read from one may no longer be in the list: a window must open with the closest choice
    instead of failing."""
    try:
        return items.index(value)
    except ValueError:
        return default


def save_file(parent: wx.Window | None, writer: Callable[..., object], *args: Any, **kwargs: Any) -> bool:
    """Write something for an editor and tell the player if it couldn't be written.

    Saves are atomic (a failed one keeps the old file), but a full disk or a read-only folder used
    to leave a dialog that simply did nothing. Returns True when the writer succeeded."""
    try:
        writer(*args, **kwargs)
        return True
    except (OSError, ValueError) as e:
        error(parent, _("Couldn't save: {err}", err=e))
        return False


def labeled(parent: wx.Window, sizer: wx.Sizer, label: str, make: Callable[[wx.Window], W], proportion: int = 0) -> W:
    """A label above a control (for lists and big text boxes); make(parent) builds the control."""
    sizer.Add(wx.StaticText(parent, label=label), 0, wx.TOP, 8)
    ctrl = make(parent)
    ctrl.SetName(_name(label))
    sizer.Add(ctrl, proportion, wx.EXPAND | wx.TOP, 4)
    return ctrl


def buttons(parent: wx.Window, sizer: wx.Sizer, *specs: tuple[str, Handler]) -> list[wx.Button]:
    """A row of buttons: specs are (label, handler) pairs. Returns the buttons."""
    row = wx.BoxSizer(wx.HORIZONTAL)
    out: list[wx.Button] = []
    for label, handler in specs:
        b = wx.Button(parent, label=label)
        b.Bind(wx.EVT_BUTTON, handler)
        row.Add(b, 0, wx.RIGHT, 8)
        out.append(b)
    sizer.Add(row, 0, wx.TOP, 10)
    return out


def ok_cancel(dialog: wx.Dialog, sizer: wx.Sizer, ok_label: str | None = None,
              extra: Sequence[tuple[str, Handler]] = ()) -> None:
    """The dialog's closing row. extra: (label, handler) of the buttons on the left (Advanced...)."""
    row = wx.BoxSizer(wx.HORIZONTAL)
    for label, handler in extra:
        b = wx.Button(dialog, label=label)
        b.Bind(wx.EVT_BUTTON, handler)
        row.Add(b, 0, wx.RIGHT, 8)
    row.AddStretchSpacer()
    std = dialog.CreateStdDialogButtonSizer(wx.OK | wx.CANCEL)
    if ok_label:
        std.GetAffirmativeButton().SetLabel(ok_label)
    row.Add(std, 0)
    sizer.Add(row, 0, wx.EXPAND | wx.ALL, 12)


def copy_text(text: str) -> bool:
    """Put text on the clipboard; False when the clipboard couldn't be opened. wx.Clipboard.Get() is the
    object wx.TheClipboard stands for (a lazy wrapper the type stub can't see through)."""
    clip = wx.Clipboard.Get()
    if not clip.Open():
        return False
    try:
        clip.SetData(wx.TextDataObject(text))
    finally:
        clip.Close()          # never leave the clipboard open on a failure
    return True


def error(parent: wx.Window | None, message: str, title: str | None = None) -> None:
    wx.MessageBox(message, title or _("Uncle Farmer"), wx.OK | wx.ICON_ERROR, parent)


def info(parent: wx.Window | None, message: str, title: str | None = None) -> None:
    wx.MessageBox(message, title or _("Uncle Farmer"), wx.OK | wx.ICON_INFORMATION, parent)


def ask(parent: wx.Window | None, message: str, title: str | None = None) -> bool:
    return wx.MessageBox(message, title or _("Uncle Farmer"), wx.YES_NO | wx.ICON_QUESTION, parent) == wx.YES


def guide_text(name: str) -> str:
    """A guide from the docs folder as plain text a screen reader reads well: no markdown marks,
    table rows as "name: meaning"."""
    try:
        with open(os.path.join(paths.DOCS, name), encoding="utf-8") as f:
            md = f.read()
    except OSError:
        return _("The guide {name} is missing from the docs folder.", name=name)
    out: list[str] = []
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


def show_text(parent: wx.Window, title: str, text: str) -> None:
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


def key_ok(text: str) -> bool:
    """One key, or a combination like lctrl+alt+up, in the names the agent understands."""
    parts = text.strip().lower().split("+")
    return bool(text.strip()) and " " not in text.strip() and all(p in KNOWN_KEYS for p in parts)


_WX_KEYS: dict[int, str] = {
    wx.WXK_SPACE: "space", wx.WXK_RETURN: "enter", wx.WXK_NUMPAD_ENTER: "enter", wx.WXK_BACK: "backspace",
    wx.WXK_DELETE: "delete", wx.WXK_UP: "up", wx.WXK_DOWN: "down", wx.WXK_LEFT: "left", wx.WXK_RIGHT: "right",
    wx.WXK_CAPITAL: "capslock",
}
_VK_LCTRL, _VK_RCTRL = 0xA2, 0xA3


def _held(vk: int) -> bool:
    return bool(ctypes.windll.user32.GetAsyncKeyState(vk) & 0x8000)


def key_from_event(evt: wx.KeyEvent) -> str | None:
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
    mods: list[str] = []
    if evt.ControlDown():
        mods.append("rctrl" if _held(_VK_RCTRL) and not _held(_VK_LCTRL) else "lctrl")
    if evt.AltDown():
        mods.append("alt")
    if evt.ShiftDown():
        mods.append("shift")
    return "+".join(mods + [key])


class KeyDialog(Dialog):
    """Change one key: press it in the Key field (Tab leaves the field), or type its name in the
    Name field. Both show the same value."""

    def __init__(self, parent: wx.Window, action: str, current: str) -> None:
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

    def on_key(self, evt: wx.KeyEvent) -> None:
        code = evt.GetKeyCode()
        if code == wx.WXK_TAB or (code == wx.WXK_ESCAPE and not evt.HasAnyModifiers()):
            evt.Skip()
            return
        key = key_from_event(evt)
        if key:
            self.capture.ChangeValue(key)
            self.typed.ChangeValue(key)
            speech.speak(key, interrupt=True)

    def on_ok(self, _evt: wx.CommandEvent) -> None:
        key = self.typed.GetValue().strip().lower()
        if not key_ok(key):
            error(self, _("'{value}' isn't a key the bot can press.", value=key))
            return
        self.value = key
        self.EndModal(wx.ID_OK)
