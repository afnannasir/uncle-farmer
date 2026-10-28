"""Quick phrases: the codes, reading a character's phrase file, and filling empty slots with the
codes the bot wants - none of which needs a game, only a tiny fake link."""

from __future__ import annotations

import os
from typing import Any

import pytest

from unclefarmer import gamekeys, phrases
from pathlib import Path
from conftest import as_bot, stand_in

KEYS = ["shift+%s" % k for k in "1234567890-="]


class Agent:
    def __init__(self, reply: str | Exception = "set") -> None:
        self.reply = reply
        self.sent: list[str] = []

    def send(self, line: str) -> str:
        self.sent.append(line)
        if isinstance(self.reply, Exception):
            raise self.reply
        return self.reply


class Bot:
    def __init__(self, reply: str | Exception = "set", **p: Any) -> None:
        self.a = Agent(reply)
        self.p = dict(p)
        self.lines: list[str] = []
        self.phrases: dict[int, str] = {}

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)


@pytest.fixture(autouse=True)
def fixed_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(gamekeys, "phrase_key_list", stand_in(gamekeys.phrase_key_list, lambda: list(KEYS)))
    monkeypatch.setattr(phrases, "_told", set[str]())


def game(tmp_path: Path, char_id: int=5) -> str:
    """A game folder (mw.exe) where this character has played (users/<id>), with no phrases yet."""
    (tmp_path / "mw.exe").write_bytes(b"MZ")
    (tmp_path / "users" / str(char_id)).mkdir(parents=True, exist_ok=True)
    return str(tmp_path)


def write_phrase(tmp_path: Path, char_id: int, lines: list[str]) -> str:
    folder = game(tmp_path, char_id)
    (tmp_path / "users" / str(char_id) / "phrase").write_text("\n".join(lines), encoding="utf-8")
    return folder


# ---------------------------------------------------------------- what
@pytest.mark.parametrize("code", ["/0", "/1", "/2", "/3", "/4", "/5", "/6", "/7", "/8", "/9"])
def test_what_is_known_for_every_code(code: str) -> None:
    assert phrases.what(code)


def test_what_is_empty_for_unknown_codes() -> None:
    assert phrases.what("/x") == ""
    assert phrases.what("") == ""
    assert phrases.what(None) == ""


# ---------------------------------------------------------------- code_of
@pytest.mark.parametrize("text,code", [("/0", "/0"), ("/6 target", "/6"), ("  /5  ", "/5"),
                                       ("hello", None), ("/a", None), ("/", None), ("", None), (None, None)])
def test_code_of(text: str, code: str | None) -> None:
    assert phrases.code_of(text) == code


# ---------------------------------------------------------------- path / read
def test_path_uses_users_folder() -> None:
    assert phrases.path("C:/game", 42) == os.path.join("C:/game", "users", "42", "phrase")


def test_read_missing_file_is_empty(tmp_path: Path) -> None:
    assert phrases.read(str(tmp_path), 7) == {}


def test_read_keeps_only_valid_slots(tmp_path: Path) -> None:
    folder = write_phrase(tmp_path, 5, ["1,/6", "2, hi there ", "13,bad", "0,bad", "abc,bad", "3,", " 4 , /5 "])
    assert phrases.read(folder, 5) == {1: "/6", 2: "hi there", 4: "/5"}


def test_read_later_line_wins(tmp_path: Path) -> None:
    folder = write_phrase(tmp_path, 5, ["1,first", "1,second"])
    assert phrases.read(folder, 5) == {1: "second"}


# ---------------------------------------------------------------- slot_of
def test_slot_of_a_plain_code() -> None:
    assert phrases.slot_of({1: "x", 2: "/0"}, "/0") == 2


def test_slot_of_0_to_2_needs_no_extra_text() -> None:
    assert phrases.slot_of({1: "/0 chat", 2: "/0"}, "/0") == 2
    assert phrases.slot_of({1: "/0 chat"}, "/0") is None


def test_slot_of_3_up_ignores_what_follows() -> None:
    assert phrases.slot_of({4: "/3 anything at all"}, "/3") == 4


def test_slot_of_missing_is_none() -> None:
    assert phrases.slot_of({1: "/6"}, "/5") is None


# ---------------------------------------------------------------- describe
def test_describe_a_code_and_plain_text() -> None:
    lines = phrases.describe({1: "/6", 2: "hello there"})
    assert lines[0] == "shift+1: /6 - %s" % phrases.what("/6")
    assert lines[1] == "shift+2: text - a chat line"


@pytest.mark.parametrize("code", ["/0", "/1", "/2"])
def test_describe_0_to_2_with_text_says_it_to_the_team(code: str) -> None:
    (line,) = phrases.describe({1: code + " the raid is here"})
    assert "and says it to the team" in line


@pytest.mark.parametrize("code", ["/3", "/6", "/9"])
def test_describe_3_up_never_says_it_to_the_team(code: str) -> None:
    (line,) = phrases.describe({1: code + " trailing"})
    assert "and says it to the team" not in line


# ---------------------------------------------------------------- wanted
def test_wanted_for_a_non_healer_drops_7() -> None:
    assert phrases.wanted("warrior") == ["/6", "/5", "/4"]


@pytest.mark.parametrize("cls", ["priest", "sorceress", "witcher", " Priest "])
def test_wanted_for_a_healer_keeps_7(cls: str) -> None:
    assert "/7" in phrases.wanted(cls)


def test_wanted_without_a_class_drops_7() -> None:
    assert "/7" not in phrases.wanted(None)
    assert "/7" not in phrases.wanted("")


# ---------------------------------------------------------------- arrange
def test_arrange_fills_empty_slots_with_wanted_codes(tmp_path: Path) -> None:
    b = Bot()
    out = phrases.arrange(as_bot(b), game(tmp_path), 5, "warrior", True)
    assert out == {1: "/6", 2: "/5", 3: "/4"}
    assert b.a.sent == ["phrase 1 /6", "phrase 2 /5", "phrase 3 /4"]
    assert b.phrases == out
    assert sum("put " in line for line in b.lines) == 3


def test_arrange_a_healer_also_wants_7(tmp_path: Path) -> None:
    b = Bot()
    out = phrases.arrange(as_bot(b), game(tmp_path), 5, "priest", True)
    assert out == {1: "/6", 2: "/5", 3: "/4", 4: "/7"}


def test_arrange_can_set_false_only_reports(tmp_path: Path) -> None:
    b = Bot()
    out = phrases.arrange(as_bot(b), game(tmp_path), 5, "warrior", False)
    assert out == {}
    assert b.a.sent == []
    assert any("restart the game" in line for line in b.lines)
    assert any("would go on the empty" in line for line in b.lines)


def test_arrange_stops_when_the_agent_refuses(tmp_path: Path) -> None:
    b = Bot(reply="nope")
    out = phrases.arrange(as_bot(b), game(tmp_path), 5, "warrior", True)
    assert out == {}
    assert b.a.sent == ["phrase 1 /6"]
    assert any("couldn't set a phrase" in line for line in b.lines)


def test_arrange_survives_a_send_that_raises(tmp_path: Path, allow_ignored_failures: None) -> None:
    b = Bot(reply=OSError("link gone"))
    out = phrases.arrange(as_bot(b), game(tmp_path), 5, "warrior", True)
    assert out == {}
    assert any("couldn't set a phrase" in line for line in b.lines)


def test_arrange_respects_fill_empty_phrases_off(tmp_path: Path) -> None:
    b = Bot(fill_empty_phrases=False)
    out = phrases.arrange(as_bot(b), game(tmp_path), 5, "warrior", True)
    assert out == {}
    assert b.a.sent == []


def test_arrange_stops_when_no_slot_is_empty(tmp_path: Path) -> None:
    folder = write_phrase(tmp_path, 5, ["%d,chat %d" % (s, s) for s in range(1, 13)])
    b = Bot()
    out = phrases.arrange(as_bot(b), folder, 5, "warrior", True)
    assert set(out) == set(range(1, 13))
    assert b.a.sent == []
    assert any("no empty phrase slot" in line for line in b.lines)


def test_arrange_reuses_an_existing_slot(tmp_path: Path) -> None:
    folder = write_phrase(tmp_path, 5, ["1,/6 already here"])
    b = Bot()
    out = phrases.arrange(as_bot(b), folder, 5, "warrior", True)
    assert out[1] == "/6 already here"
    assert "phrase 1 /6" not in b.a.sent


def test_arrange_lists_a_character_only_once(tmp_path: Path) -> None:
    folder = write_phrase(tmp_path, 5, ["1,/6 already here"])
    b = Bot()
    phrases.arrange(as_bot(b), folder, 5, "warrior", True)
    first = sum(line.startswith("phrase ") for line in b.lines)

    b2 = Bot()
    phrases.arrange(as_bot(b2), folder, 5, "warrior", True)
    assert first == 1
    assert not any(line.startswith("phrase ") for line in b2.lines)


def test_arrange_never_fills_phrases_it_cant_see(tmp_path: Path) -> None:
    # no game folder set, or a copy of the game this character never played in: the phrase file
    # isn't there to read, which says nothing about the player's phrases - they stay as they are
    for folder in ("", str(tmp_path)):
        b = Bot()
        assert phrases.arrange(as_bot(b), folder, 5, "warrior", True) == {}
        assert b.a.sent == []
        assert any("leaving them all alone" in line for line in b.lines)
    (tmp_path / "mw.exe").write_bytes(b"MZ")                   # a game, but not this character's copy
    b = Bot()
    phrases.arrange(as_bot(b), str(tmp_path), 5, "warrior", True)
    assert b.a.sent == []


def test_known_needs_the_game_and_the_characters_own_folder(tmp_path: Path) -> None:
    assert phrases.known("", 5) is False
    assert phrases.known(str(tmp_path), 5) is False
    game(tmp_path, 5)
    assert phrases.known(str(tmp_path), 5) is True
    assert phrases.known(str(tmp_path), 6) is False
