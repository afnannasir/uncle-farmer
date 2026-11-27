"""The keyboard shortcuts, which the player can change: every command of the main window's menus, the
two keys that work from anywhere (Pause, Shift+Pause) and a few commands made for keys alone (the
next game, its status, its window).

Each action has its keys (or none) and where they work: EVERYWHERE, even inside the game or another
program (a Windows hotkey, which takes the key away from every other program while Uncle Farmer
runs), or IN_APP, only while an Uncle Farmer window is in front (the menus' accelerators).

They live in shortcuts.txt beside settings.json, one line an action, as a player asked for it:

    run_start = F5 = inapp
    run_toggle = Pause = global
    game_next = none = global

File, Keyboard shortcuts edits the same file. A line that can't be used (an unknown action, a key the
bot doesn't take, two actions on one key) is reported and left out: that action keeps its default,
and the rest of the file still counts. No file: every default, as the bot always had them. The file
is the player's own, never shipped, so an update leaves it alone.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass

from . import jsonio, paths
from .i18n import N_, _

EVERYWHERE, IN_APP = "global", "inapp"
WHERE = (EVERYWHERE, IN_APP)
NONE = "none"                   # the file's word for no key
NAME = "shortcuts.txt"


def file_path() -> str:
    """The shortcuts file, beside settings.json."""
    return os.path.join(os.path.dirname(paths.SETTINGS), NAME)


@dataclass(frozen=True)
class Action:
    id: str            # the file's name for it, and the menu item's key (gui/main.py action_items)
    group: str         # the menu it is in (a msgid, with its & letter)
    label: str         # its menu label (a msgid, with its & letter)
    keys: str = ""     # the default keys ("" none), as canonical() writes them
    where: str = IN_APP
    quiet: bool = False  # does its work without opening a window: may run with a window open


T, C, F, V, R, G, M, H, P = (N_("&Tools"), N_("&Character"), N_("&File"), N_("&View"), N_("&Run"), N_("&Game"),
                             N_("&Messages"), N_("&Help"), N_("Plans"))
ACTIONS: tuple[Action, ...] = (
    # from anywhere, as they always were
    Action("run_toggle", R, N_("Start or stop the bot (in a game window: that game's bot)"), "Pause", EVERYWHERE, True),
    Action("run_speak_front", R, N_("Speak the results of the game in front"), "Shift+Pause", EVERYWHERE, True),
    # the Run menu
    Action("run_start", R, N_("&Start"), "F5", quiet=True),
    Action("run_stop", R, N_("S&top"), "Shift+F5", quiet=True),
    Action("run_pause", R, N_("P&ause or go on"), "F6", quiet=True),
    Action("run_stop_all", R, N_("Stop &every bot"), "Ctrl+Shift+S", quiet=True),
    Action("run_walk", R, N_("Wa&lk to a dungeon or map"), "Ctrl+W"),
    Action("run_schedules", R, N_("Sche&dules"), "Ctrl+Shift+H"),
    Action("run_autopilot", R, N_("&Autopilot: the whole day"), "Ctrl+Shift+A"),
    Action("run_chain", R, N_("&Character chain"), "Ctrl+Shift+C"),
    Action("run_results", R, N_("&Results"), "Ctrl+R"),
    Action("run_speak_results", R, N_("Spea&k the results"), "Ctrl+Shift+R", quiet=True),
    # the Game menu
    Action("game_mute", G, N_("&Mute this game"), "Ctrl+M", quiet=True),
    Action("game_unmute", G, N_("&Unmute this game"), "Ctrl+U", quiet=True),
    Action("game_mute_all", G, N_("Mute e&very game"), quiet=True),
    Action("game_unmute_all", G, N_("Unmute every &game"), quiet=True),
    Action("game_next", G, N_("Select the &next game"), "", EVERYWHERE, True),
    Action("game_previous", G, N_("Select the &previous game"), "", EVERYWHERE, True),
    Action("game_status", G, N_("Speak the selected game's st&atus"), "", EVERYWHERE, True),
    Action("game_show", G, N_("Bring the selected game to the &front"), "", EVERYWHERE, True),
    Action("game_open", G, N_("Open another game &window"), "Ctrl+O", quiet=True),
    # the Tools menu
    Action("tools_gem", T, N_("&Gem Maker"), "Ctrl+G"),
    Action("tools_breaker", T, N_("Gem Brea&ker"), "Ctrl+Shift+B"),
    Action("tools_potions", T, N_("P&otion Maker"), "Ctrl+Shift+O"),
    Action("tools_enhance", T, N_("&Equipment Enhancer"), "Ctrl+Shift+E"),
    Action("tools_chores", T, N_("&Daily chores"), "Ctrl+Shift+D"),
    Action("tools_gather", T, N_("Gathe&ring"), "Ctrl+Shift+F"),
    Action("tools_dungeon", T, N_("D&ungeons"), "Ctrl+Shift+G"),
    Action("tools_xploss", T, N_("&XP loser"), "Ctrl+Shift+X"),
    Action("tools_market", T, N_("&Market searcher and buyer"), "Ctrl+Shift+K"),
    Action("tools_buycoll", T, N_("Buy co&llection items"), "Ctrl+Shift+L"),
    Action("tools_bounties", T, N_("Hunting bount&ies")),
    Action("tools_construction", T, N_("Construction &scanner")),
    Action("tools_collection", T, N_("Sell the &collection now")),
    Action("tools_shards", T, N_("Sell s&hards now")),
    Action("tools_clean", T, N_("Clean the &bag now")),
    Action("tools_store", T, N_("S&tore items now")),
    Action("tools_restock", T, N_("Restock &now")),
    Action("tools_prices", T, N_("Update shard &prices")),
    Action("tools_silencer", T, N_("Sound Silencer")),
    Action("tools_keyboard", T, N_("&Keyboard Manager")),
    # the Character menu
    Action("character_settings", C, N_("&Settings"), "Ctrl+P"),
    Action("character_bag", C, N_("&Bag and supplies"), "Ctrl+B"),
    Action("character_scan", C, N_("S&can now")),
    Action("character_other", C, N_("&Another character")),
    Action("character_export", C, N_("E&xport character settings")),
    Action("character_import", C, N_("&Import character settings")),
    # the Messages menu
    Action("messages_private", M, N_("&Private messages"), "Ctrl+Shift+M"),
    Action("messages_chat", M, N_("C&hat"), "Ctrl+T"),
    Action("messages_reset", M, N_("Let everyone get replies &again"), quiet=True),
    # the Plans tab
    Action("plans_new", P, N_("&New plan"), "Ctrl+N"),
    Action("plans_new_script", P, N_("New &script"), "Ctrl+Shift+N"),
    Action("plans_edit", P, N_("&Edit"), "Ctrl+E"),
    Action("plans_duplicate", P, N_("D&uplicate"), "Ctrl+D"),
    # the File, View and Help menus
    Action("file_settings", F, N_("&Settings")),
    Action("file_shortcuts", F, N_("Keyboard s&hortcuts")),
    Action("file_logs", F, N_("Open the &log folder"), quiet=True),
    Action("file_results", F, N_("Open the &results folder"), quiet=True),
    Action("file_exit", F, N_("E&xit")),
    Action("view_bot", V, N_("&Bot"), "Ctrl+1"),
    Action("view_plans", V, N_("&Plans"), "Ctrl+2"),
    Action("view_log", V, N_("&Log"), "Ctrl+3"),
    Action("help_quick", H, N_("&Quick guide"), "F1"),
    Action("help_user_guide", H, N_("&User guide (opens in your browser)"), quiet=True),
    Action("help_shortcuts", H, N_("&Keyboard shortcuts")),
    Action("help_game", H, N_("Ho&w the game works")),
    Action("help_scripting", H, N_("Script &guide")),
    Action("help_commands", H, N_("&Script commands")),
    Action("help_trusted", H, N_("&Commands for trusted players")),
    Action("help_changelog", H, N_("Change&log")),
    Action("help_updates", H, N_("Check for &updates")),
    Action("help_contact", H, N_("Contact the &developer")),
    Action("help_telegram", H, N_("Join the &Telegram group"), quiet=True),
    Action("help_about", H, N_("&About Uncle Farmer")),
)
BY_ID = {a.id: a for a in ACTIONS}

# Keys a shortcut can't take: the ones every window needs (copy, paste, the menu bar, the context
# menu, moving between tabs, closing a window).
RESERVED = {"Ctrl+C", "Ctrl+V", "Ctrl+X", "Ctrl+A", "Ctrl+Z", "Ctrl+Y", "Ctrl+Tab", "Ctrl+Shift+Tab", "F10",
            "Shift+F10", "Alt+F4", "Ctrl+F4"}
_NAMED = {"pause": "Pause", "break": "Pause", "space": "Space", "insert": "Insert", "ins": "Insert",
          "delete": "Delete", "del": "Delete", "home": "Home", "end": "End", "pageup": "PageUp", "pgup": "PageUp",
          "pagedown": "PageDown", "pgdn": "PageDown", "up": "Up", "down": "Down", "left": "Left", "right": "Right"}
_MODIFIERS = {"ctrl": "Ctrl", "control": "Ctrl", "alt": "Alt", "shift": "Shift"}
_ORDER = ("Ctrl", "Alt", "Shift")
# Windows' virtual-key codes, for RegisterHotKey
_VK = {"Pause": 0x13, "Space": 0x20, "PageUp": 0x21, "PageDown": 0x22, "End": 0x23, "Home": 0x24, "Left": 0x25,
       "Up": 0x26, "Right": 0x27, "Down": 0x28, "Insert": 0x2D, "Delete": 0x2E}
MOD_ALT, MOD_CONTROL, MOD_SHIFT = 0x0001, 0x0002, 0x0004
_MOD_BITS = {"Ctrl": MOD_CONTROL, "Alt": MOD_ALT, "Shift": MOD_SHIFT}


class BadKeys(ValueError):
    """Keys the bot can't use, with why, for the player."""


def canonical(text: str) -> str:
    """The keys as the bot writes them ("Ctrl+Shift+R"), from any spelling ("shift + ctrl + r");
    "" for no key ("", "none"). BadKeys says why keys can't be a shortcut."""
    raw = text.strip()
    if not raw or raw.lower() == NONE:
        return ""
    parts = [p.strip() for p in raw.split("+")]
    if any(not p for p in parts):
        raise BadKeys(_("\"{keys}\": write the keys joined by +, for example Ctrl+Shift+R.", keys=raw))
    mods: set[str] = set()
    for p in parts[:-1]:
        m = _MODIFIERS.get(p.lower())
        if m is None:
            raise BadKeys(_("\"{keys}\": {part} isn't Ctrl, Alt or Shift.", keys=raw, part=p))
        mods.add(m)
    key = _key_name(parts[-1], raw)
    out = "+".join([m for m in _ORDER if m in mods] + [key])
    _check(out, mods, key)
    return out


def _key_name(part: str, raw: str) -> str:
    low = part.lower()
    if low in _MODIFIERS:
        raise BadKeys(_("\"{keys}\": a shortcut needs a key besides Ctrl, Alt and Shift.", keys=raw))
    if len(part) == 1 and (part.isascii() and part.isalnum()):
        return part.upper()
    if re.fullmatch(r"f([1-9]|1[0-9]|2[0-4])", low):
        return low.upper()
    if low in _NAMED:
        return _NAMED[low]
    raise BadKeys(_("\"{keys}\": the bot doesn't know the key {part}. Use a letter, a digit, F1 to F24, Pause, "
                    "Space, Insert, Delete, Home, End, PageUp, PageDown or an arrow.", keys=raw, part=part))


def _check(keys: str, mods: set[str], key: str) -> None:
    if keys in RESERVED:
        raise BadKeys(_("{keys} is kept for every window (copy, paste, menus, tabs): choose another.", keys=keys))
    plain = len(key) == 1 or key in ("Space", "Insert", "Delete", "Home", "End", "PageUp", "PageDown",
                                     "Up", "Down", "Left", "Right")
    if plain and "Ctrl" not in mods:
        raise BadKeys(_("{keys}: a letter, a digit, Space, an arrow or Insert, Delete, Home, End, PageUp or "
                        "PageDown needs Ctrl, or it would be typed into the bot's own boxes.", keys=keys))
    if plain and "Alt" in mods:
        raise BadKeys(_("{keys}: Ctrl+Alt with a letter is AltGr on many keyboards, which types characters. "
                        "Use Ctrl or Ctrl+Shift.", keys=keys))


def hotkey(keys: str) -> tuple[int, int]:
    """(modifier bits, virtual-key code) of canonical keys, for Windows' RegisterHotKey."""
    *mods, key = keys.split("+")
    bits = 0
    for m in mods:
        bits |= _MOD_BITS[m]
    if len(key) == 1:
        return bits, ord(key)
    if key[0] == "F" and key[1:].isdigit():
        return bits, 0x6F + int(key[1:])          # F1 = 0x70
    return bits, _VK[key]


@dataclass
class Shortcut:
    keys: str          # canonical, "" for none
    where: str


Table = dict[str, Shortcut]


def defaults() -> Table:
    return {a.id: Shortcut(a.keys, a.where) for a in ACTIONS}


def label(action: Action) -> str:
    """The action as the player reads it, without its & letter: "Run, Start"."""
    return "%s, %s" % (_(action.group).replace("&", ""), _(action.label).replace("&", ""))


def where_label(where: str) -> str:
    return _("everywhere, even in the game") if where == EVERYWHERE else _("only in Uncle Farmer")


def clashes(table: Table) -> list[tuple[str, str]]:
    """Pairs of actions (in ACTIONS' order) given the same keys. A key from everywhere also works in
    the bot's windows, so any two on one key clash, wherever each works."""
    seen: dict[str, str] = {}
    out: list[tuple[str, str]] = []
    for a in ACTIONS:
        keys = table[a.id].keys
        if not keys:
            continue
        if keys in seen:
            out.append((seen[keys], a.id))
        else:
            seen[keys] = a.id
    return out


def parse(text: str) -> tuple[Table, list[str]]:
    """The table a shortcuts file gives, and what in it couldn't be used (one line each, for the
    player). Every action the file doesn't name keeps its default; a line that can't be used is left
    out; two actions on one key: the second keeps its default, or no key when that clashes too."""
    table = defaults()
    problems: list[str] = []
    named: set[str] = set()
    for n, line in enumerate(text.splitlines(), 1):
        line = line.strip().lstrip("﻿")
        if not line or line.startswith("#"):
            continue
        parts = [p.strip() for p in line.split("=")]
        if len(parts) != 3:
            problems.append(_("shortcuts.txt line {n}: write action = keys = where, for example run_start = F5 = inapp.",
                              n=n))
            continue
        name, keys, where = parts[0].lower(), parts[1], parts[2].lower()
        if name not in BY_ID:
            problems.append(_("shortcuts.txt line {n}: there is no action called {name}.", n=n, name=parts[0]))
            continue
        if where not in WHERE:
            problems.append(_("shortcuts.txt line {n}: {where} isn't global or inapp.", n=n, where=parts[2]))
            continue
        try:
            table[name] = Shortcut(canonical(keys), where)
        except BadKeys as e:
            problems.append(_("shortcuts.txt line {n}: {why}", n=n, why=e))
            continue
        named.add(name)
    restored: set[str] = set()
    for first, second in clashes(table):
        if first in named and second in named:      # the file gives one key twice: the first line in
            keys = table[second].keys              # the menus' order keeps it, the other its default
            table[second] = Shortcut(BY_ID[second].keys, table[second].where)
            restored.add(second)
            problems.append(_("{keys} is given to both {one} and {other}: {other} keeps its own key.",
                              keys=keys, one=label(BY_ID[first]), other=label(BY_ID[second])))
        else:                                       # the player gave an action another one's default
            keep, drop = (first, second) if first in named else (second, first)
            problems.append(_("{action} has no key now: {keys} is {other}'s.", action=label(BY_ID[drop]),
                              keys=table[drop].keys, other=label(BY_ID[keep])))
            table[drop] = Shortcut("", table[drop].where)
    for first, second in clashes(table):            # a default given back that another action has
        drop = second if second in restored else first
        problems.append(_("{action} has no key now: {keys} is {other}'s.", action=label(BY_ID[drop]),
                          keys=table[drop].keys, other=label(BY_ID[first if drop == second else second])))
        table[drop] = Shortcut("", table[drop].where)
    return table, problems


def render(table: Table) -> str:
    """The shortcuts file for this table: every action, under its menu's name, with what it does."""
    lines = ["# " + _("Uncle Farmer's keyboard shortcuts. One line an action: action = keys = where."),
             "# " + _("keys: for example F5, Ctrl+Shift+R or Shift+Pause; none for no key."),
             "# " + _("where: global works everywhere, even inside the game (the key no longer reaches the game or "
                      "any other program while Uncle Farmer runs); inapp only in Uncle Farmer's windows."),
             "# " + _("A line that can't be used is said when Uncle Farmer starts, and that action keeps its default."),
             ""]
    group = ""
    for a in ACTIONS:
        if a.group != group:
            group = a.group
            lines.append("# --- " + _(group).replace("&", ""))
        s = table[a.id]
        lines.append("# " + _(a.label).replace("&", ""))
        lines.append("%s = %s = %s" % (a.id, s.keys or NONE, s.where))
    return "\n".join(lines) + "\n"


def load(path: str | None = None) -> tuple[Table, list[str]]:
    """The player's shortcuts and what in their file couldn't be used; every default without a file.
    A file that can't be read is said, and the defaults are used."""
    path = path or file_path()
    try:
        with open(path, encoding="utf-8-sig") as f:
            text = f.read()
    except FileNotFoundError:
        return defaults(), []
    except (OSError, UnicodeDecodeError) as e:
        return defaults(), [_("shortcuts.txt couldn't be read ({err}): the default shortcuts are used.", err=e)]
    return parse(text)


def save(table: Table, path: str | None = None) -> None:
    """Write the table (atomically). OSError when it can't be written."""
    jsonio.save_text(path or file_path(), render(table), newline="\r\n")
