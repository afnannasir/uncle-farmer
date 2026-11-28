"""The keyboard shortcuts in the main window and in File, Keyboard shortcuts: the menus show the keys
the player gave, a key from anywhere acts on the game in front (the Games list follows it), waits
while a window of ours is open, and is refused while its menu entry is greyed out; the next game, its
status and its window; the editing window checks the keys and writes shortcuts.txt."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any, cast

import pytest

from conftest import returning

from unclefarmer import shortcuts
from unclefarmer.shortcuts import EVERYWHERE, IN_APP, Shortcut

wx = pytest.importorskip("wx")

from unclefarmer.gui.mainparts import keys  # noqa: E402


@pytest.fixture
def app() -> Iterator[Any]:
    a = wx.GetApp() or wx.App(False)
    yield a
    a.ProcessPendingEvents()


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []

    def speak(text: str, interrupt: bool = False) -> bool:
        lines.append(text)
        return True
    monkeypatch.setattr(keys.speech, "speak", speak)
    return lines


class Item:
    def __init__(self, on: bool = True) -> None:
        self.on = on

    def IsEnabled(self) -> bool:  # noqa: N802 (wx's name)
        return self.on


class Frame:
    """What the key part of the main window uses, and nothing else."""

    def __init__(self, games: list[tuple[int, str, str]], selected: int | None = 0) -> None:
        self.games: list[tuple[int, str | None, str | None]] = list(games)
        self.selected = selected
        self.destroyed = False
        self.enabled = True
        self.raised = 0
        self.done: list[str] = []
        self.runs: dict[int, Any] = {}
        self.last_line: dict[int, str] = {}
        self.snap: Any = None
        self.action_items: dict[str, Item] = {}
        self.action_handlers: dict[str, Any] = {a.id: self.handler(a.id) for a in shortcuts.ACTIONS}
        self.shortcut_table = shortcuts.defaults()
        self.hotkeys: list[int] = []
        self.hotkeys_refused: set[str] = set()
        self.registered: list[tuple[int, int, int]] = []
        self.refuse: set[int] = set()
        self.logged: list[str] = []

    def handler(self, name: str) -> Any:
        def run(_e: object) -> None:
            self.done.append(name)
        return run

    # games
    def selected_index(self) -> int | None:
        return self.selected

    def selected_pid(self) -> int | None:
        return self.games[self.selected][0] if self.selected is not None else None

    def select_game(self, pid: int) -> None:
        self.selected = next(i for i, g in enumerate(self.games) if g[0] == pid)

    def game_row(self, game: tuple[int, str | None, str | None]) -> tuple[str, str, str]:
        return (game[1] or "", game[2] or "", "Running" if game[0] in self.runs else "Stopped")

    def game_said(self, index: int) -> str:
        return keys.KeysMixin.game_said(cast(Any, self), index)

    # the window
    def IsEnabled(self) -> bool:  # noqa: N802
        return self.enabled

    def IsIconized(self) -> bool:  # noqa: N802
        return False

    def Raise(self) -> None:  # noqa: N802
        self.raised += 1

    def SetFocus(self) -> None:  # noqa: N802
        pass

    def sync_action_menus(self) -> None:
        pass

    def do_action(self, action_id: str) -> None:
        keys.KeysMixin.do_action(cast(Any, self), action_id)

    # hotkeys
    def RegisterHotKey(self, hid: int, mods: int, vk: int) -> bool:  # noqa: N802
        if vk in self.refuse:
            return False
        self.registered.append((hid, mods, vk))
        return True

    def UnregisterHotKey(self, hid: int) -> bool:  # noqa: N802
        self.registered = [r for r in self.registered if r[0] != hid]
        return True

    def Bind(self, *_a: Any, **_k: Any) -> None:  # noqa: N802
        pass

    def Unbind(self, *_a: Any, **_k: Any) -> None:  # noqa: N802
        pass

    def _append(self, line: str, _speak: bool) -> None:
        self.logged.append(line)


GAMES = [(11, "Hero", "Tulip City"), (22, "Bob", "Wolf Prairie"), (33, "Ann", "Margit Rainforest")]


def press(frame: Frame, action: str, front: int | None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keys, "foreground_pid", lambda: front)
    keys.KeysMixin.on_hotkey(cast(Any, frame), action)


def test_a_key_from_the_game_in_front_acts_on_that_game(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = Frame(GAMES, selected=0)
    press(f, "run_pause", 33, monkeypatch)
    assert f.selected == 2 and f.done == ["run_pause"]       # the list followed the game in front
    press(f, "run_pause", 4444, monkeypatch)                  # another program in front: the selected one
    assert f.selected == 2 and f.done == ["run_pause", "run_pause"]
    assert f.raised == 0                                      # a quiet command leaves the windows as they are


def test_a_key_that_opens_a_window_brings_the_bot_forward_and_waits_for_an_open_one(
        monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = Frame(GAMES)
    press(f, "character_bag", None, monkeypatch)
    assert f.done == ["character_bag"] and f.raised == 1
    f.enabled = False                                         # a modal window of ours is open
    press(f, "character_bag", None, monkeypatch)
    assert f.done == ["character_bag"] and said == ["Close the open Uncle Farmer window first."]
    press(f, "run_stop", None, monkeypatch)                   # a quiet one still works
    assert f.done == ["character_bag", "run_stop"]


def test_a_quiet_key_while_a_window_of_ours_is_open_leaves_the_selection(
        monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = Frame(GAMES, selected=0)
    f.enabled = False                       # the Keyboard Manager is open for the selected game
    press(f, "run_pause", 22, monkeypatch)
    assert f.selected == 0 and f.done == ["run_pause"]


def test_a_key_is_refused_while_its_menu_entry_is_greyed_out(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = Frame(GAMES)
    f.action_items["run_stop"] = Item(False)
    press(f, "run_stop", None, monkeypatch)
    assert f.done == [] and said == ["Run, Stop can't be used now."]


def test_the_next_and_previous_game_go_round_and_are_said(said: list[str]) -> None:
    f = Frame(GAMES, selected=0)
    moved: list[int] = []
    keys.select_row = lambda lc, row: moved.append(row)  # type: ignore[assignment]
    try:
        f.game_list = None  # type: ignore[attr-defined]
        keys.KeysMixin.next_game(cast(Any, f))
        f.selected = 2
        keys.KeysMixin.next_game(cast(Any, f))
        keys.KeysMixin.next_game(cast(Any, f), -1)
    finally:
        from unclefarmer.gui import common
        keys.select_row = common.select_row
    assert moved == [1, 0, 1]
    assert said == ["Bob, Wolf Prairie, Stopped", "Hero, Tulip City, Stopped", "Bob, Wolf Prairie, Stopped"]
    empty = Frame([], selected=None)
    keys.KeysMixin.next_game(cast(Any, empty))
    assert said[-1] == "No game with the agent is running."


def test_the_status_says_the_row_what_the_bot_does_and_the_hp(said: list[str]) -> None:
    f = Frame(GAMES, selected=1)
    f.runs[22] = object()
    f.last_line[22] = "fighting a Wolf"

    class Field:
        def GetValue(self) -> str:  # noqa: N802
            return "900 of 1000 (90%)"
    f.fields = {"hp": Field()}  # type: ignore[attr-defined]
    f.snap = (22, {}, 0.0)
    keys.KeysMixin.speak_status(cast(Any, f))
    assert said == ["Bob, Wolf Prairie, Running. fighting a Wolf. HP 900 of 1000 (90%)"]
    f.selected = None
    keys.KeysMixin.speak_status(cast(Any, f))
    assert said[-1] == "Select a game first."


def test_a_game_window_that_can_t_be_found_is_said(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = Frame(GAMES, selected=0)
    monkeypatch.setattr(keys, "window_of", returning(None))
    keys.KeysMixin.show_game(cast(Any, f))
    assert said == ["Couldn't bring Hero, Tulip City, Stopped to the front."]


def test_hotkeys_are_held_for_the_keys_that_work_everywhere_and_a_taken_one_is_said(
        monkeypatch: pytest.MonkeyPatch) -> None:
    installed: list[set[str]] = []
    def install(frame: Frame) -> None:
        installed.append(set(frame.hotkeys_refused))
    monkeypatch.setattr(keys, "install_accelerators", install)
    f = Frame(GAMES)
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    assert [(m & ~keys.MOD_NOREPEAT, v) for _h, m, v in f.registered] == [(0, 0x13), (shortcuts.MOD_SHIFT, 0x13)]   # Pause, Shift+Pause
    assert f.hotkeys == [keys.HOTKEY_BASE, keys.HOTKEY_BASE + 1]
    f.shortcut_table["run_toggle"] = Shortcut("", EVERYWHERE)
    f.shortcut_table["game_next"] = Shortcut("Ctrl+F2", EVERYWHERE)
    f.refuse = {0x71}                                     # another program holds F2's key
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    assert [(m & ~keys.MOD_NOREPEAT, v) for _h, m, v in f.registered] == [(shortcuts.MOD_SHIFT, 0x13)]
    assert f.logged == ["Another program is using Ctrl+F2, so Game, Select the next game works only in Uncle Farmer."]
    assert installed == [{"game_next"}]                 # so it works in the bot at least
    f.refuse = set()
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    assert installed == [{"game_next"}, set()]


@pytest.fixture
def menus(app: Any) -> Iterator[Any]:
    from test_menu_layout import MenuFrame
    window = MenuFrame()
    yield window
    window.Destroy()


def test_the_menus_show_the_player_s_keys(menus: Any) -> None:
    assert menus.action_items["game_mute"].GetItemLabel().endswith("\tCtrl+M")
    assert "\t" not in menus.action_items["game_next"].GetItemLabel()           # no key yet
    table = shortcuts.defaults()
    table["game_mute"] = Shortcut("", IN_APP)
    table["game_next"] = Shortcut("Ctrl+F2", EVERYWHERE)
    table["run_start"] = Shortcut("F7", IN_APP)
    menus.shortcut_table = table
    for action_id, (item, base) in menus.menu_bases.items():
        item.SetItemLabel(keys.menu_label(menus, action_id, base))
    keys.install_accelerators(menus)
    assert menus.action_items["game_mute"].GetItemLabel() == "&Mute this game"
    assert menus.action_items["game_next"].GetItemLabel().endswith("\tCtrl+F2")
    assert menus.action_items["run_start"].GetItemLabel().endswith("\tF7")
    assert menus.GetAcceleratorTable().IsOk()


def test_every_action_has_its_command(menus: Any) -> None:
    assert set(menus.action_handlers) == set(shortcuts.BY_ID)


def test_the_help_lists_the_keys_as_they_are() -> None:
    from unclefarmer.gui.main import shortcuts_text
    table = shortcuts.defaults()
    table["game_next"] = Shortcut("Ctrl+F2", EVERYWHERE)
    text = shortcuts_text(table)
    anywhere, _sep, rest = text.partition("In Uncle Farmer's main window")
    assert "Ctrl+F2" in anywhere and "Pause" in anywhere and "F5" not in anywhere
    assert "F5" in rest and "Shift+Delete" in rest


# ---------------------------------------------------------------- File, Keyboard shortcuts
@pytest.fixture
def dialog(app: Any, monkeypatch: pytest.MonkeyPatch) -> Iterator[Any]:
    from unclefarmer.gui import shortcutkeys
    errors: list[str] = []

    def error(_w: object, message: str, title: str | None = None) -> None:
        errors.append(message)
    monkeypatch.setattr(shortcutkeys, "error", error)
    frame = wx.Frame(None)
    d: Any = shortcutkeys.ShortcutsDialog(frame, shortcuts.defaults())
    d.EndModal = returning(None)
    d.errors = errors
    yield d
    d.Destroy()
    frame.Destroy()


def row_of(action_id: str) -> int:
    return next(i for i, a in enumerate(shortcuts.ACTIONS) if a.id == action_id)


def choose(d: Any, action_id: str, typed: str, where: str | None = None) -> None:
    from unclefarmer.gui.common import select_row
    select_row(d.rows, row_of(action_id))
    d.show()
    d.keys.SetValue(typed)                    # its text event fills the row
    if where is not None:
        d.where.SetSelection(shortcuts.WHERE.index(where))
        d.chose_where()


def test_the_window_saves_the_keys_typed(dialog: Any) -> None:
    choose(dialog, "game_next", "ctrl+f2")
    assert dialog.rows.GetItemText(row_of("game_next"), 1) == "ctrl+f2"
    assert "no longer reaches the game, NVDA" in dialog.note.GetValue()
    choose(dialog, "game_mute", "", IN_APP)
    assert dialog.rows.GetItemText(row_of("game_mute"), 1) == "none"
    dialog.on_ok(None)
    assert dialog.errors == []
    table, problems = shortcuts.load()
    assert problems == [] and table["game_next"] == Shortcut("Ctrl+F2", EVERYWHERE)
    assert table["game_mute"].keys == "" and table["run_start"].keys == "F5"
    assert dialog.table == table


def test_the_window_says_what_must_change_and_saves_nothing(dialog: Any) -> None:
    choose(dialog, "game_next", "m")
    assert "needs Ctrl" in dialog.note.GetValue()
    dialog.on_ok(None)
    assert len(dialog.errors) == 1 and dialog.errors[0].startswith("Game, Select the next game: M: ")
    assert shortcuts.load() == (shortcuts.defaults(), [])     # nothing written
    choose(dialog, "game_next", "F5")                          # Run, Start's
    assert "F5 is also Run, Start" in dialog.note.GetValue()
    dialog.on_ok(None)
    assert len(dialog.errors) == 2 and dialog.errors[1] == ("Run, Start: F5 is also Game, Select the next game: "
                                                            "one of them has to change.")


def test_the_defaults_come_back_one_or_all(dialog: Any) -> None:
    choose(dialog, "run_start", "F7")
    choose(dialog, "run_stop", "F8")
    dialog.default_one()                                       # Run, Stop
    assert dialog.typed["run_stop"] == "Shift+F5" and dialog.typed["run_start"] == "F7"
    dialog.all_defaults()
    assert dialog.typed["run_start"] == "F5"
    assert dialog.rows.GetItemText(row_of("run_start"), 1) == "F5"


def test_read_the_file_again_takes_what_was_written_in_notepad(dialog: Any) -> None:
    with open(shortcuts.file_path(), "w", encoding="utf-8") as f:
        f.write("run_start = F9 = global\nnonsense\n")
    dialog.read_file()
    assert dialog.typed["run_start"] == "F9" and dialog.table["run_start"].where == EVERYWHERE
    assert len(dialog.errors) == 1 and "line 2" in dialog.errors[0]
