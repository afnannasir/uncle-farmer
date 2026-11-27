"""The keyboard shortcuts the player can change (unclefarmer/shortcuts.py): every action's keys and
where they work, read from and written to shortcuts.txt, with what can't be used said and left out.
A player asked for it: "Pause_bot = shift+pause = global, Mute_bot = ctrl+m = inapp"."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from unclefarmer import paths, shortcuts
from unclefarmer.shortcuts import EVERYWHERE, IN_APP, Shortcut

# what the bot had before the keys could be changed (gui/main.py, 2026-11-27)
OLD = {"run_toggle": "Pause", "run_speak_front": "Shift+Pause", "run_start": "F5", "run_stop": "Shift+F5",
       "run_pause": "F6", "run_stop_all": "Ctrl+Shift+S", "run_walk": "Ctrl+W", "run_schedules": "Ctrl+Shift+H",
       "run_autopilot": "Ctrl+Shift+A", "run_chain": "Ctrl+Shift+C", "run_results": "Ctrl+R",
       "run_speak_results": "Ctrl+Shift+R", "game_mute": "Ctrl+M", "game_unmute": "Ctrl+U", "game_open": "Ctrl+O",
       "tools_gem": "Ctrl+G", "tools_breaker": "Ctrl+Shift+B", "tools_potions": "Ctrl+Shift+O",
       "tools_enhance": "Ctrl+Shift+E", "tools_chores": "Ctrl+Shift+D", "tools_gather": "Ctrl+Shift+F",
       "tools_dungeon": "Ctrl+Shift+G", "tools_xploss": "Ctrl+Shift+X", "tools_market": "Ctrl+Shift+K",
       "tools_buycoll": "Ctrl+Shift+L", "character_settings": "Ctrl+P", "character_bag": "Ctrl+B",
       "messages_private": "Ctrl+Shift+M", "messages_chat": "Ctrl+T", "plans_new": "Ctrl+N",
       "plans_new_script": "Ctrl+Shift+N", "plans_edit": "Ctrl+E", "plans_duplicate": "Ctrl+D",
       "view_bot": "Ctrl+1", "view_plans": "Ctrl+2", "view_log": "Ctrl+3", "help_quick": "F1"}


def test_the_defaults_are_the_keys_the_bot_always_had() -> None:
    table = shortcuts.defaults()
    assert {k: s.keys for k, s in table.items() if s.keys} == OLD
    assert {k for k, s in table.items() if s.keys and s.where == EVERYWHERE} == {"run_toggle", "run_speak_front"}
    assert shortcuts.clashes(table) == []
    for a in shortcuts.ACTIONS:
        assert shortcuts.canonical(a.keys) == a.keys           # written the bot's own way, and usable
    assert len(shortcuts.BY_ID) == len(shortcuts.ACTIONS)


def test_the_new_actions_have_no_key_until_the_player_gives_one() -> None:
    for action in ("game_next", "game_previous", "game_status", "game_show"):
        assert shortcuts.defaults()[action] == Shortcut("", EVERYWHERE)


@pytest.mark.parametrize("typed,keys", [
    ("ctrl+shift+r", "Ctrl+Shift+R"), ("Shift + Ctrl + r", "Ctrl+Shift+R"), ("shift+pause", "Shift+Pause"),
    ("PAUSE", "Pause"), ("break", "Pause"), ("f12", "F12"), ("alt+shift+f7", "Alt+Shift+F7"),
    ("control+shift+pgdn", "Ctrl+Shift+PageDown"), ("ctrl+del", "Ctrl+Delete"), ("ctrl+1", "Ctrl+1"), ("none", ""), ("  ", ""),
])
def test_keys_are_read_in_any_spelling(typed: str, keys: str) -> None:
    assert shortcuts.canonical(typed) == keys


@pytest.mark.parametrize("typed,why", [
    ("m", "needs Ctrl"), ("shift+m", "needs Ctrl"), ("alt+m", "needs Ctrl"), ("ctrl+alt+m", "AltGr"),
    ("ctrl+c", "kept for every window"), ("shift+f10", "kept for every window"), ("alt+f4", "kept for every window"),
    ("ctrl+", "joined by +"), ("win+m", "isn't Ctrl, Alt or Shift"), ("ctrl+enter", "doesn't know the key"),
    ("ctrl+shift", "a key besides"), ("f25", "doesn't know the key"),
])
def test_keys_that_would_get_in_the_way_are_refused_with_why(typed: str, why: str) -> None:
    with pytest.raises(shortcuts.BadKeys, match=why):
        shortcuts.canonical(typed)


def test_windows_hotkey_codes() -> None:
    assert shortcuts.hotkey("Pause") == (0, 0x13)
    assert shortcuts.hotkey("Shift+Pause") == (shortcuts.MOD_SHIFT, 0x13)
    assert shortcuts.hotkey("Ctrl+Shift+F12") == (shortcuts.MOD_CONTROL | shortcuts.MOD_SHIFT, 0x7B)
    assert shortcuts.hotkey("Ctrl+Alt+F1") == (shortcuts.MOD_CONTROL | shortcuts.MOD_ALT, 0x70)
    assert shortcuts.hotkey("Ctrl+M") == (shortcuts.MOD_CONTROL, ord("M"))
    assert shortcuts.hotkey("Ctrl+PageUp") == (shortcuts.MOD_CONTROL, 0x21)


def test_the_player_s_own_example_reads() -> None:
    table, problems = shortcuts.parse("run_toggle =shift+pause= global\nGAME_MUTE=ctrl+m=InApp\n")
    assert table["run_toggle"] == Shortcut("Shift+Pause", EVERYWHERE)
    assert table["game_mute"] == Shortcut("Ctrl+M", IN_APP)
    # Shift+Pause was the results' key: the file doesn't name that action, so it lets the key go
    assert table["run_speak_front"].keys == ""
    assert problems == ["Run, Speak the results of the game in front has no key now: Shift+Pause is "
                        "Run, Start or stop the bot (in a game window: that game's bot)'s."]


def test_a_key_moved_to_another_action_takes_it_from_its_default_owner() -> None:
    table, problems = shortcuts.parse("run_start = ctrl+m = inapp\n")
    assert table["run_start"] == Shortcut("Ctrl+M", IN_APP)
    assert table["game_mute"].keys == ""
    assert problems == ["Game, Mute this game has no key now: Ctrl+M is Run, Start's."]


def test_one_key_given_twice_in_the_file_keeps_the_first_and_gives_the_other_its_own() -> None:
    table, problems = shortcuts.parse("run_start = F7 = inapp\nrun_stop = f7 = global\n")
    assert table["run_start"].keys == "F7" and table["run_stop"] == Shortcut("Shift+F5", EVERYWHERE)
    assert problems == ["F7 is given to both Run, Start and Run, Stop: Run, Stop keeps its own key."]


def test_lines_that_can_t_be_used_are_said_and_the_rest_still_counts() -> None:
    text = ("﻿# my keys\n\n"
            "bogus = F2 = global\n"
            "run_stop = F8 = sideways\n"
            "run_pause = zz = inapp\n"
            "run_walk = F9\n"
            "game_next = Ctrl+F2 = global\n")
    table, problems = shortcuts.parse(text)
    assert table["game_next"] == Shortcut("Ctrl+F2", EVERYWHERE)
    assert table["run_stop"].keys == "Shift+F5" and table["run_pause"].keys == "F6" and table["run_walk"].keys == "Ctrl+W"
    assert [p.split(":")[0] for p in problems] == ["shortcuts.txt line 3", "shortcuts.txt line 4",
                                                   "shortcuts.txt line 5", "shortcuts.txt line 6"]
    assert "no action called bogus" in problems[0] and "sideways isn't global or inapp" in problems[1]


def test_none_takes_a_key_away() -> None:
    table, problems = shortcuts.parse("run_toggle = none = global\n")
    assert problems == [] and table["run_toggle"] == Shortcut("", EVERYWHERE)


def test_the_file_written_reads_back_the_same(tmp_path: Path) -> None:
    table = shortcuts.defaults()
    table["run_pause"] = Shortcut("Ctrl+F6", EVERYWHERE)
    table["game_mute"] = Shortcut("", IN_APP)
    path = str(tmp_path / "shortcuts.txt")
    shortcuts.save(table, path)
    raw = open(path, "rb").read()
    assert b"\r\nrun_pause = Ctrl+F6 = global\r\n" in raw and b"game_mute = none = inapp" in raw
    assert b"# Pause or go on" in raw                     # what each line is for
    again, problems = shortcuts.load(path)
    assert problems == [] and again == table


def test_no_file_is_every_default_and_an_unreadable_one_is_said(tmp_path: Path) -> None:
    assert shortcuts.load(str(tmp_path / "none.txt")) == (shortcuts.defaults(), [])
    table, problems = shortcuts.load(str(tmp_path))           # a folder: can't be read as a file
    assert table == shortcuts.defaults() and len(problems) == 1 and "couldn't be read" in problems[0]


def test_the_file_is_beside_settings_json() -> None:
    assert shortcuts.file_path() == os.path.join(os.path.dirname(paths.SETTINGS), "shortcuts.txt")


def test_three_actions_on_one_key_end_with_one_on_it() -> None:
    # Ctrl+R is Run, Results' default: the file gives it to two more actions
    table, problems = shortcuts.parse("game_mute = Ctrl+R = inapp\ntools_gem = ctrl+r = inapp\n")
    assert table["run_results"].keys == "" and table["game_mute"].keys == "Ctrl+R"
    assert table["tools_gem"].keys == "Ctrl+G"                     # back to its own
    assert shortcuts.clashes(table) == []
    assert problems == ["Run, Results has no key now: Ctrl+R is Game, Mute this game's.",
                        "Ctrl+R is given to both Game, Mute this game and Tools, Gem Maker: "
                        "Tools, Gem Maker keeps its own key."]


def test_no_file_leaves_two_actions_on_one_key() -> None:
    import random
    ids = [a.id for a in shortcuts.ACTIONS]
    keys = ["Ctrl+M", "Ctrl+R", "F5", "Ctrl+G", "Pause", "Ctrl+Shift+R", "F9"]
    rng = random.Random(7)
    for _ in range(3000):
        text = "".join("%s = %s = %s\n" % (rng.choice(ids), rng.choice(keys), rng.choice(["inapp", "global"]))
                       for _line in range(rng.randint(1, 6)))
        table, _problems = shortcuts.parse(text)
        assert shortcuts.clashes(table) == [], text


def test_an_action_named_twice_takes_its_last_line_and_says_so() -> None:
    table, problems = shortcuts.parse("run_start = F7 = inapp\nrun_start = F8 = inapp\n")
    assert table["run_start"].keys == "F8"
    assert problems == ["shortcuts.txt line 2: run_start is on line 1 too; this line counts."]


@pytest.mark.parametrize("typed", ["ctrl+pageup", "ctrl+pgdn", "ctrl+insert"])
def test_the_tab_and_copy_keys_of_boxes_are_kept(typed: str) -> None:
    with pytest.raises(shortcuts.BadKeys, match="kept for every window"):
        shortcuts.canonical(typed)
