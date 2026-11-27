"""The main window's keyboard shortcuts (unclefarmer/shortcuts.py): its menus' accelerators, the
Windows hotkeys that work from anywhere, and the commands made for keys alone - the next or the
previous game, its status, its window.

A key that works from anywhere acts on the game in front when that is one of ours: the Games list
selects it first, so the next key (and the bot's own window) go on with the same game. From any other
program it acts on the game selected in the list. A command that opens a window brings Uncle Farmer
to the front first, and waits while one of its windows is open (a hotkey still reaches the main
window behind a modal one).
"""

from __future__ import annotations

import ctypes
import os
import sys
from collections.abc import Callable
from typing import Any

import wx

from ... import shortcuts, speech
from ...i18n import _
from ..common import select_row, shortcut_label
from .base import FrameParts, foreground_pid

Handler = Callable[[Any], object]
HOTKEY_BASE = 0xBF01            # Pause and Shift+Pause kept their old ids (the first two actions)
MOD_NOREPEAT = 0x4000
SW_RESTORE = 9


def install_accelerators(frame: Any) -> None:
    """The window's accelerators for the actions whose keys work in Uncle Farmer only. A key that
    works from anywhere is a hotkey instead, which reaches this window too - unless another program
    holds it (hotkeys_refused): then it works at least here."""
    table: shortcuts.Table = frame.shortcut_table
    refused: set[str] = getattr(frame, "hotkeys_refused", set())
    entries: list[wx.AcceleratorEntry] = []
    for a in shortcuts.ACTIONS:
        s = table[a.id]
        here = s.where == shortcuts.IN_APP or a.id in refused
        if not s.keys or not here or a.id not in frame.action_handlers:
            continue
        entry = wx.AcceleratorEntry()
        if entry.FromString(s.keys):
            entry.Set(entry.GetFlags(), entry.GetKeyCode(), frame.action_ids[a.id])
            entries.append(entry)
    frame.SetAcceleratorTable(wx.AcceleratorTable(entries))


def menu_label(frame: Any, action_id: str, base: str) -> str:
    """A menu entry's label with its keys after a tab, as Windows shows them."""
    table: shortcuts.Table = frame.shortcut_table
    return shortcut_label(base, table[action_id].keys)


def window_of(pid: int) -> int | None:
    """The game's main window: its first visible top-level window with a title."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32
    found: list[int] = []
    proc = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_void_p, ctypes.c_void_p)

    def each(hwnd: int | None, _param: int | None) -> bool:
        if not hwnd or not user32.IsWindowVisible(hwnd) or not user32.GetWindowTextLengthW(hwnd):
            return True
        owner = ctypes.c_ulong()
        user32.GetWindowThreadProcessId(hwnd, ctypes.byref(owner))
        if owner.value == pid:
            found.append(hwnd)
            return False
        return True
    user32.EnumWindows.argtypes = (proc, ctypes.c_void_p)
    user32.EnumWindows(proc(each), None)
    return found[0] if found else None


def bring_forward(hwnd: int) -> bool:
    user32 = ctypes.windll.user32
    user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    user32.IsIconic.argtypes = (ctypes.c_void_p,)
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    return bool(user32.SetForegroundWindow(hwnd))


class KeysMixin(FrameParts):
    shortcut_table: shortcuts.Table
    action_handlers: dict[str, Handler]      # action id -> what it does (the menus' own handlers)
    action_ids: dict[str, int]               # action id -> its command id (a menu item's, or its own)
    menu_bases: dict[str, tuple[wx.MenuItem, str]]   # action id -> its menu item and label without keys
    hotkeys_refused: set[str]                # actions whose everywhere keys another program holds

    def load_shortcuts(self) -> None:
        """The player's shortcuts. What their file holds that can't be used goes into the log, said
        once; without a file, one is written with every default, for the player to find and edit."""
        table, problems = shortcuts.load()
        self.shortcut_table = table
        for line in problems:
            self._append(line, False)
        if problems:
            self.say(_("Some keyboard shortcuts couldn't be used: see the Log tab, or File, Keyboard shortcuts."))
        if not os.path.exists(shortcuts.file_path()):
            try:
                shortcuts.save(table)
            except OSError:
                pass                      # the defaults work all the same

    def register_hotkeys(self) -> None:
        """Hold a Windows hotkey for each action whose keys work from anywhere (again after a change:
        the old ones are let go first). A key another program holds is said in the log."""
        from ... import applog
        for hotkey in self.hotkeys:
            self.UnregisterHotKey(hotkey)
            self.Unbind(wx.EVT_HOTKEY, id=hotkey)
        self.hotkeys = []
        before: set[str] = getattr(self, "hotkeys_refused", set())
        self.hotkeys_refused = set()
        wanted = 0
        for i, a in enumerate(shortcuts.ACTIONS):
            s = self.shortcut_table[a.id]
            if not s.keys or s.where != shortcuts.EVERYWHERE:
                continue
            wanted += 1
            hid = HOTKEY_BASE + i
            mods, vk = shortcuts.hotkey(s.keys)
            if self.RegisterHotKey(hid, mods | MOD_NOREPEAT, vk):
                self.hotkeys.append(hid)
                self.Bind(wx.EVT_HOTKEY, lambda e, act=a.id: self.on_hotkey(act), id=hid)
            else:
                applog.bootup.problem("%s is taken by another program: %s works only in the bot" % (s.keys, a.id))
                self.hotkeys_refused.add(a.id)
                self._append(_("Another program is using {keys}, so {action} works only in Uncle Farmer.",
                               keys=s.keys, action=shortcuts.label(a)), False)
        applog.bootup.note("hotkeys held: %d of %d" % (len(self.hotkeys), wanted))
        if self.hotkeys_refused != before:
            install_accelerators(self)          # a refused key works in the bot at least

    def apply_shortcuts(self, table: shortcuts.Table) -> None:
        """New shortcuts, at once: the menus' labels, the accelerators and the hotkeys."""
        self.shortcut_table = table
        for action_id, (item, base) in self.menu_bases.items():
            item.SetItemLabel(menu_label(self, action_id, base))
        install_accelerators(self)
        self.register_hotkeys()

    def open_shortcuts(self) -> None:
        from ..shortcutkeys import ShortcutsDialog
        with ShortcutsDialog(self, self.shortcut_table) as d:
            if d.ShowModal() == wx.ID_OK:
                self.apply_shortcuts(d.table)

    # ---------------------------------------------------------------- a key pressed
    def on_hotkey(self, action_id: str) -> None:
        """A key from anywhere: the game in front, if it is ours, becomes the selected one; a command
        that opens a window brings Uncle Farmer forward, and waits while one of its windows is open."""
        if self.destroyed:
            return
        action = shortcuts.BY_ID[action_id]
        modal = not self.IsEnabled()            # a window of ours is open over this one
        if modal and not action.quiet:
            speech.speak(_("Close the open Uncle Farmer window first."))
            return
        front = foreground_pid()
        # not while a window of ours is open: it may be about the selected game (Keyboard Manager)
        if not modal and front is not None and front != self.selected_pid() and any(g[0] == front for g in self.games):
            self.select_game(front)
        if not action.quiet:
            if self.IsIconized():
                self.Iconize(False)
            self.Raise()
            self.SetFocus()
        self.do_action(action_id)

    def do_action(self, action_id: str) -> None:
        """Run an action as its menu entry does: not while its entry is greyed out."""
        handler = self.action_handlers.get(action_id)
        if handler is None:
            return
        self.sync_action_menus()
        item = self.action_items.get(action_id)
        if item is not None and not item.IsEnabled():
            speech.speak(_("{action} can't be used now.", action=shortcuts.label(shortcuts.BY_ID[action_id])))
            return
        handler(None)

    # ---------------------------------------------------------------- the commands made for keys
    def select_game(self, pid: int) -> None:
        index = next((i for i, g in enumerate(self.games) if g[0] == pid), None)
        if index is not None:
            select_row(self.game_list, index)

    def next_game(self, step: int = 1) -> None:
        """Select the next (or, step -1, the previous) game in the list, round the end, and say it."""
        if not self.games:
            speech.speak(_("No game with the agent is running."))
            return
        i = self.selected_index()
        index = 0 if i is None else (i + step) % len(self.games)
        if index != i:
            select_row(self.game_list, index)
        speech.speak(self.game_said(index), interrupt=True)

    def game_said(self, index: int) -> str:
        """The game as the list shows it: character, map, what its bot does."""
        return ", ".join(t for t in self.game_row(self.games[index]) if t)

    def speak_status(self) -> None:
        """The selected game: the list's row, then what its bot is doing and the HP, when read."""
        i = self.selected_index()
        if i is None:
            speech.speak(_("No game with the agent is running.") if not self.games else _("Select a game first."))
            return
        pid = self.games[i][0]
        parts = [self.game_said(i)]
        if pid in self.runs and self.last_line.get(pid):
            parts.append(self.last_line[pid])
        if self.snap and self.snap[0] == pid:
            hp = self.fields["hp"].GetValue()
            if hp:
                parts.append(_("HP {hp}", hp=hp))
        speech.speak(". ".join(parts), interrupt=True)

    def show_game(self) -> None:
        """Bring the selected game's window to the front (and out of the taskbar)."""
        i = self.selected_index()
        if i is None:
            speech.speak(_("No game with the agent is running.") if not self.games else _("Select a game first."))
            return
        pid = self.games[i][0]
        hwnd = window_of(pid)
        if hwnd is None or not bring_forward(hwnd):
            speech.speak(_("Couldn't bring {name} to the front.", name=self.game_said(i)))
