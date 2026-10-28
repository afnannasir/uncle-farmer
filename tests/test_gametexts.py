# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""gametexts.py: the game's own texts, read from a (fake) game folder into a JSON file.

Covers the English/foreign line test, the game-folder timestamp, the build from the english
folder + version/language.gz + the paired language folders, the cached texts() reader (build,
rebuild after an update, broken/missing output, a game that is not a folder), find/has, and main().

Nothing touches a real game install: gametexts.OUT and paths.DATA are pointed at tmp_path, the
global cache is reset per test, and settings.load_app is stubbed so the repo is never read or
written.
"""

from __future__ import annotations

import gzip
import json
import os

import pytest

from unclefarmer import gametexts, paths, settings
from pathlib import Path
from conftest import stand_in


@pytest.fixture(autouse=True)
def sandbox(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """Keep game_texts.json out of the repo and the module cache fresh."""
    data = tmp_path / "data"
    monkeypatch.setattr(paths, "DATA", str(data))
    monkeypatch.setattr(gametexts, "OUT", str(data / "game_texts.json"))
    monkeypatch.setattr(gametexts, "_cache", None)
    monkeypatch.setattr(settings, "load_app", stand_in(settings.load_app, lambda: {"game_folder": ""}))
    return tmp_path


def make_game(root: Path, english: str | None=None, gz: str | None=None, paired: dict[str, str] | None=None, stamp: int | None=None) -> Path:
    """A pretend game folder: language/english, version/language.gz and paired folders."""
    game = root / "game"
    if english is not None:
        d = game / "language" / "english"
        d.mkdir(parents=True, exist_ok=True)
        (d / "basic.txt").write_text(english, encoding="utf-8")
    if gz is not None:
        d = game / "version"
        d.mkdir(parents=True, exist_ok=True)
        p = d / "language.gz"
        with gzip.open(p, "wb") as f:
            f.write(gz.encode("utf-8"))
        if stamp is not None:
            os.utime(p, (stamp, stamp))
    for lang, text in (paired or {}).items():
        d = game / "language" / lang
        d.mkdir(parents=True, exist_ok=True)
        (d / "basic.txt").write_text(text, encoding="utf-8")
    return game


def write_out(out: str, stamp: int, texts: list[str]) -> None:
    os.makedirs(os.path.dirname(str(out)), exist_ok=True)
    with open(str(out), "w", encoding="utf-8") as f:
        json.dump({"stamp": stamp, "texts": texts}, f)


# ------------------------------------------------------------------------------- _english
def test_english_rejects_blank_and_foreign() -> None:
    assert gametexts._english("") is False
    assert gametexts._english("   \n") is False
    assert gametexts._english("Привет мир") is False          # Cyrillic
    assert gametexts._english("你好世界") is False             # CJK
    assert gametexts._english("こんにちは") is False           # kana
    assert gametexts._english("안녕하세요") is False           # Hangul


def test_english_short_labels_must_be_plain_ascii() -> None:
    assert gametexts._english("Hello") is True
    assert gametexts._english("café") is False                # latin-1 but not ascii
    assert gametexts._english("naïve") is False


def test_english_long_lines_need_two_english_words() -> None:
    assert gametexts._english("Please enter your name so we can help you") is True
    # 25+ chars but only one word from the English list: rejected
    assert gametexts._english("aaaaaaaaaaaaaaaaaaaaaaaa enter") is False
    # 25+ chars, two English words, but a word from another language: rejected
    assert gametexts._english("Please enter the room de now with your key") is False


def test_english_long_foreign_line_is_rejected_first() -> None:
    assert gametexts._english("Please enter your сила name so we can help you") is False


# ------------------------------------------------------------------------------- _stamp
def test_stamp_is_zero_without_language_gz(tmp_path: Path) -> None:
    game = make_game(tmp_path, english="Hello there friend\n")
    assert gametexts._stamp(str(game)) == 0


def test_stamp_is_the_language_gz_mtime(tmp_path: Path) -> None:
    game = make_game(tmp_path, gz="anything\n", stamp=1700000000)
    assert gametexts._stamp(str(game)) == 1700000000


# ------------------------------------------------------------------------------- build
def test_build_reads_english_gz_and_paired_folders(tmp_path: Path) -> None:
    game = make_game(
        tmp_path,
        english="Hello there friend\nПривет\n\nHello there friend\r\n",
        gz="Menu/options.txt\nPlease enter your name now\nBonjour le monde\n",
        paired={"russian": "Please enter your name now\nWorld of Warcraft gold\n"},
        stamp=1700000000,
    )
    n = gametexts.build(str(game))
    assert n == 4
    with open(gametexts.OUT, encoding="utf-8") as f:
        data = json.load(f)
    assert data["stamp"] == 1700000000
    assert data["texts"] == [
        "Hello there friend",
        "Please enter your name now",
        "Bonjour le monde",          # a short ASCII line is "english" whatever the language
        "World of Warcraft gold",
    ]


def test_build_of_an_empty_folder_writes_no_texts(tmp_path: Path) -> None:
    game = make_game(tmp_path)                       # nothing at all
    assert gametexts.build(str(game)) == 0
    with open(gametexts.OUT, encoding="utf-8") as f:
        data = json.load(f)
    assert data == {"stamp": 0, "texts": []}


def test_build_skips_the_language_gz_file_listing(tmp_path: Path) -> None:
    game = make_game(tmp_path, gz="russian/basic.txt\nA real english line you can read here\n")
    assert gametexts.build(str(game)) == 1
    assert gametexts.texts(str(game)) == ["A real english line you can read here"]


# ------------------------------------------------------------------------------- texts
def test_texts_builds_when_no_output_exists(tmp_path: Path) -> None:
    game = make_game(tmp_path, english="Hello there friend\n", stamp=1700000000)
    assert gametexts.texts(str(game)) == ["Hello there friend"]
    assert os.path.exists(gametexts.OUT)


def test_texts_reads_existing_output_without_a_game(tmp_path: Path) -> None:
    write_out(gametexts.OUT, 0, ["From the file", "Another line"])
    assert gametexts.texts(str(tmp_path / "nope")) == ["From the file", "Another line"]


def test_texts_of_an_empty_game_is_empty(tmp_path: Path) -> None:
    assert gametexts.texts("") == []


def test_texts_is_cached_and_returns_the_same_list(tmp_path: Path) -> None:
    game = make_game(tmp_path, english="Hello there friend\n", stamp=1700000000)
    first = gametexts.texts(str(game))
    second = gametexts.texts(str(game))
    assert first is second


def test_texts_uses_settings_when_no_game_given(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    game = make_game(tmp_path, english="Hello there friend\n", stamp=1700000000)
    monkeypatch.setattr(settings, "load_app", stand_in(settings.load_app, lambda: {"game_folder": str(game)}))
    assert gametexts.texts() == ["Hello there friend"]


def test_texts_rebuilds_after_the_game_updates(tmp_path: Path) -> None:
    game = make_game(tmp_path, english="First line\n", gz="", stamp=1700000000)
    assert gametexts.texts(str(game)) == ["First line"]
    (game / "language" / "english" / "basic.txt").write_text("Second line\n", encoding="utf-8")
    os.utime(game / "version" / "language.gz", (1700000100, 1700000100))
    assert gametexts.texts(str(game)) == ["Second line"]


def test_texts_rebuilds_over_a_broken_output_file(tmp_path: Path) -> None:
    game = make_game(tmp_path, english="Hello there friend\n", stamp=1700000000)
    os.makedirs(os.path.dirname(gametexts.OUT), exist_ok=True)
    with open(gametexts.OUT, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert gametexts.texts(str(game)) == ["Hello there friend"]


def test_texts_falls_back_to_empty_when_output_is_broken_and_game_is_not_a_folder(tmp_path: Path) -> None:
    os.makedirs(os.path.dirname(gametexts.OUT), exist_ok=True)
    with open(gametexts.OUT, "w", encoding="utf-8") as f:
        f.write("{not json")
    assert gametexts.texts(str(tmp_path / "nope")) == []


def test_texts_does_not_rebuild_without_a_language_gz(tmp_path: Path) -> None:
    # A game folder with english files but no version/language.gz has stamp 0, so the condition
    # that triggers a rebuild is false and the existing output is served unchanged.
    game = make_game(tmp_path, english="A brand new line\n")
    write_out(gametexts.OUT, 0, ["Stale"])
    assert gametexts.texts(str(game)) == ["Stale"]


# ------------------------------------------------------------------------------- find / has
def test_find_is_a_case_insensitive_regex(tmp_path: Path) -> None:
    write_out(gametexts.OUT, 0, ["Hello there friend", "Goodbye my friend"])
    assert gametexts.find("HELLO") == ["Hello there friend"]
    assert gametexts.find("friend") == ["Hello there friend", "Goodbye my friend"]
    assert gametexts.find(r"^Good") == ["Goodbye my friend"]


def test_has_is_an_exact_stripped_match(tmp_path: Path) -> None:
    write_out(gametexts.OUT, 0, ["Hello there friend"])
    assert gametexts.has("Hello there friend") is True
    assert gametexts.has("  Hello there friend  ") is True
    assert gametexts.has("Hello") is False


# ------------------------------------------------------------------------------- main
def test_main_builds_a_given_folder(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    game = make_game(tmp_path, english="Hello there friend\n", stamp=1700000000)
    gametexts.main([str(game)])
    out = capsys.readouterr().out
    assert "1 texts written to %s" % gametexts.OUT == out.strip()


def test_main_find_prints_matches(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> None:
    game = make_game(tmp_path, english="Hello there friend\n", stamp=1700000000)
    monkeypatch.setattr(settings, "load_app", stand_in(settings.load_app, lambda: {"game_folder": str(game)}))
    gametexts.main(["--find", "hello"])
    assert capsys.readouterr().out.strip() == "Hello there friend"


def test_main_with_a_bad_folder_exits(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        gametexts.main([str(tmp_path / "missing")])


def test_main_without_arguments_uses_settings_and_exits_when_unset() -> None:
    with pytest.raises(SystemExit):
        gametexts.main([])


def test_main_find_without_a_pattern_is_treated_as_a_folder(tmp_path: Path) -> None:
    # argv[:1] == ["--find"] but len == 1, so it falls through and tries to open "--find" as a
    # folder; documents the current argument handling.
    with pytest.raises(SystemExit):
        gametexts.main(["--find"])
