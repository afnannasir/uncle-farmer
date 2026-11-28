"""Two things players asked for about the keys: the next game, pressed from inside a game, goes round the
list (it used to start again from the game in front, so it always gave the same other game), and keys
that work in Uncle Farmer and in the games only - held as hotkeys while one of our games is in front,
let go for any other program."""

from __future__ import annotations

import os
from typing import Any, cast

import pytest

from unclefarmer import shortcuts
from unclefarmer.shortcuts import IN_APP, IN_GAMES, Shortcut

wx = pytest.importorskip("wx")

from test_shortcuts_window import Frame, GAMES, press  # noqa: E402

from unclefarmer.gui.mainparts import keys  # noqa: E402

F11 = 0x7A


@pytest.fixture
def said(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lines: list[str] = []

    def speak(text: str, interrupt: bool = False) -> bool:
        lines.append(text)
        return True
    monkeypatch.setattr(keys.speech, "speak", speak)
    return lines


def cycling(monkeypatch: pytest.MonkeyPatch, selected: int = 0) -> Frame:
    """A frame whose next and previous game keys really move the selection."""
    f = Frame(GAMES, selected=selected)

    def select_row(_lc: object, row: int) -> None:
        f.selected = row
    monkeypatch.setattr(keys, "select_row", select_row)
    f.game_list = None  # type: ignore[attr-defined]

    def next_one(_e: object) -> None:
        keys.KeysMixin.next_game(cast(Any, f))

    def previous_one(_e: object) -> None:
        keys.KeysMixin.next_game(cast(Any, f), -1)
    f.action_handlers["game_next"] = next_one
    f.action_handlers["game_previous"] = previous_one
    return f


# ------------------------------------------------------------------ going round from inside a game
def test_the_next_game_from_inside_a_game_goes_round_the_list(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = cycling(monkeypatch)
    seen: list[int | None] = []
    for _i in range(4):
        press(f, "game_next", 11, monkeypatch)       # the player stays in Hero's game all along
        seen.append(f.selected)
    assert seen == [1, 2, 0, 1]
    assert said[:3] == ["Bob, Wolf Prairie, Stopped", "Ann, Margit Rainforest, Stopped", "Hero, Tulip City, Stopped"]


def test_the_previous_game_from_inside_a_game_goes_round_too(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = cycling(monkeypatch)
    seen: list[int | None] = []
    for _i in range(3):
        press(f, "game_previous", 11, monkeypatch)
        seen.append(f.selected)
    assert seen == [2, 1, 0]


def test_status_and_show_keep_the_game_just_selected(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = cycling(monkeypatch)
    press(f, "game_next", 11, monkeypatch)
    press(f, "game_status", 11, monkeypatch)
    press(f, "game_show", 11, monkeypatch)
    assert f.selected == 1 and f.done == ["game_status", "game_show"]


def test_start_or_stop_still_acts_on_the_game_in_front(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    """Its label says so: in a game window, that game's bot - even right after picking another."""
    f = cycling(monkeypatch)
    press(f, "game_next", 11, monkeypatch)
    assert f.selected == 1
    press(f, "run_toggle", 11, monkeypatch)
    assert f.selected == 0 and f.done == ["run_toggle"]
    press(f, "game_next", 11, monkeypatch)             # and the next game goes on from there
    assert f.selected == 1


def test_from_another_program_the_next_game_goes_round_as_before(monkeypatch: pytest.MonkeyPatch, said: list[str]) -> None:
    f = cycling(monkeypatch)
    for _i in range(3):
        press(f, "game_next", 4444, monkeypatch)
    assert f.selected == 0


# ------------------------------------------------------------------ keys for the games only
class GamesFrame(Frame):
    """The key part's stand-in, with the games-only keys' own steps."""

    def hold_game_keys(self) -> None:
        keys.KeysMixin.hold_game_keys(cast(Any, self))

    def drop_game_keys(self) -> None:
        keys.KeysMixin.drop_game_keys(cast(Any, self))


def games_frame(**keys_of: str) -> GamesFrame:
    f = GamesFrame(GAMES)
    for action, k in keys_of.items():
        f.shortcut_table[action] = Shortcut(k, IN_GAMES)
    return f


def held_vks(f: Frame) -> list[int]:
    return [vk for _hid, _mods, vk in f.registered]


def test_a_games_key_is_held_only_while_one_of_our_games_is_in_front() -> None:
    f = games_frame(game_next="F11")
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    assert F11 not in held_vks(f)                    # not at start: nothing in front yet
    keys.KeysMixin.follow_front_keys(cast(Any, f), 22)
    assert held_vks(f).count(F11) == 1 and len(f.hotkeys) == 3     # with Pause and Shift+Pause
    keys.KeysMixin.follow_front_keys(cast(Any, f), 33)              # another of ours: still held, once
    assert held_vks(f).count(F11) == 1 and len(f.hotkeys) == 3
    keys.KeysMixin.follow_front_keys(cast(Any, f), 4444)            # a browser: let go
    assert F11 not in held_vks(f) and len(f.hotkeys) == 2
    keys.KeysMixin.follow_front_keys(cast(Any, f), os.getpid())     # Uncle Farmer itself: its accelerator does
    assert F11 not in held_vks(f)
    keys.KeysMixin.follow_front_keys(cast(Any, f), None)
    assert F11 not in held_vks(f)


def test_a_game_that_left_the_list_lets_the_keys_go() -> None:
    f = games_frame(game_next="F11")
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    keys.KeysMixin.follow_front_keys(cast(Any, f), 22)
    f.games = [g for g in f.games if g[0] != 22]     # its game closed, another window came in front with its pid
    keys.KeysMixin.follow_front_keys(cast(Any, f), 22)
    assert F11 not in held_vks(f)


def test_a_change_of_keys_lets_the_old_ones_go_and_holds_the_new_at_the_next_look() -> None:
    f = games_frame(game_next="F11")
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    keys.KeysMixin.follow_front_keys(cast(Any, f), 22)
    f.shortcut_table["game_next"] = Shortcut("F9", IN_GAMES)
    keys.KeysMixin.register_hotkeys(cast(Any, f))   # what apply_shortcuts does after Save
    assert F11 not in held_vks(f) and 0x78 not in held_vks(f)
    keys.KeysMixin.follow_front_keys(cast(Any, f), 22)
    assert 0x78 in held_vks(f) and F11 not in held_vks(f)
    f.shortcut_table["game_next"] = Shortcut("F9", IN_APP)          # back to the bot only
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    keys.KeysMixin.follow_front_keys(cast(Any, f), 22)
    assert 0x78 not in held_vks(f)


def test_a_games_key_another_program_holds_is_said_once() -> None:
    f = games_frame(game_next="F11")
    f.refuse = {F11}
    keys.KeysMixin.register_hotkeys(cast(Any, f))
    for front in (22, 4444, 33, 4444, 11):           # in and out of the games many times
        keys.KeysMixin.follow_front_keys(cast(Any, f), front)
    assert f.logged == ["Another program is using F11, so Game, Select the next game works only in Uncle Farmer."]
    assert len(f.hotkeys) == 2


class Window:
    """What install_accelerators uses of the window."""

    def __init__(self, table: shortcuts.Table) -> None:
        self.shortcut_table = table
        self.action_handlers: dict[str, Any] = {a.id: print for a in shortcuts.ACTIONS}
        self.action_ids = {a.id: 9000 + i for i, a in enumerate(shortcuts.ACTIONS)}
        self.entries: list[Any] = []

    def SetAcceleratorTable(self, table: Any) -> None:  # noqa: N802 (wx's name)
        pass


def test_a_games_key_is_also_an_accelerator_of_the_bot_s_window(app: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    table = shortcuts.defaults()
    table["game_next"] = Shortcut("F11", IN_GAMES)
    w = Window(table)
    got: list[list[Any]] = []

    def table_of(entries: list[Any]) -> None:
        got.append(entries)
    monkeypatch.setattr(keys.wx, "AcceleratorTable", table_of)
    keys.install_accelerators(w)
    codes = [e.GetKeyCode() for e in got[0]]
    assert wx.WXK_F11 in codes and wx.WXK_PAUSE not in codes     # Pause works everywhere: a hotkey


def test_the_file_takes_games_and_says_the_three_words(app: Any) -> None:
    table, problems = shortcuts.parse("game_next = f11 = games\nrun_start = F7 = sideways\n")
    assert table["game_next"] == Shortcut("F11", IN_GAMES)
    assert problems == ["shortcuts.txt line 2: sideways isn't global, games or inapp."]
    text = shortcuts.render(table)
    assert "game_next = F11 = games" in text and "# games works in Uncle Farmer's windows" in text
    assert shortcuts.where_label(IN_GAMES) == "in Uncle Farmer and in the games only"


@pytest.fixture
def app() -> Any:
    a = wx.GetApp() or wx.App(False)
    yield a
    a.ProcessPendingEvents()
