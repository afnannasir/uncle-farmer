# pyright: reportPrivateUsage=false
"""Collecting the game's numbers (learnids), asking them (gameids), folding players' files in
(idsmerge). The answers are shaped as the agent gives them (seen in game, 2026-11-27)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import pytest
from conftest import stand_in

from unclefarmer import gameids, gamelang, idsmerge, jsonio, learnids, link, paths

SHOP = {"open": 1, "top": {"rows": [
    [{"flags": 9, "options": ["Small Red Potion", "unit price 60 arnebia",
                              "Recover 5% Max HP and 500 points of HP, 5 seconds CD."], "cmd": [1, 23385, 0]}],
    [{"flags": 8, "options": ["Level 1 Ruby Shard", "Unit price 2725 arnebia"], "cmd": [1, 20385, 2725]}],
    [None],
]}}
TAKE_OUT = {"open": 1, "top": {"rows": [[{"flags": 13, "options": ["249 Large Red Potion"], "cmd": [1, 60495343, 0]}]]}}
STATE: dict[str, Any] = {"ok": True, "map": {"id": 5361}, "self": {"id": 42, "name": "Hero"}, "entities": [
    {"id": -5, "kind": "monster", "side": -2, "name": "beak shark", "facing": 610, "hp": 900, "maxhp": 1000},
    {"id": -6, "kind": "monster", "name": "Loot", "facing": 3},                       # on the ground: no HP
    {"id": 7, "kind": "player", "side": 0, "name": "Bob", "facing": 1, "hp": 5, "maxhp": 5},
    {"id": 8, "kind": "player", "side": 0, "name": "Frog belongs to Bob", "facing": 2, "hp": 5, "maxhp": 5},
    {"id": -9, "kind": "npc", "side": -1, "name": "Grocer Lana", "facing": 400, "hp": 20000000, "maxhp": 20000000},
]}
SKILLS: dict[str, Any] = {"combat": [{"id": 731, "name": "Rapid Arrows"}, {"id": 761, "name": "Bull's Eye"}],
          "talents": [{"id": 12, "b": 1, "name": "Keen Eye"}],
          "profession": [{"id": 11, "name": "Mining"}], "attack_keys": {}, "profession_keys": {}}


@pytest.fixture
def english(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    lang = ["english"]
    monkeypatch.setattr(learnids, "_language", stand_in(learnids._language, lambda: lang[0]))
    return lang


def saved() -> dict[str, Any]:
    learnids.save()
    return learnids.read()


def test_bag_rows_are_noted_once_a_run_under_the_game_s_language(english: list[str]) -> None:
    rows = [{"uid": 1, "type": 23387, "name": "Large Red Potion", "count": 3}, {"uid": 2, "type": 0, "name": "x"}]
    learnids.note("mem bagfull", rows)
    learnids.note("mem bagfull", rows)                       # the same run: counted once
    english[0] = "french"
    learnids.note("mem equipment", [{"uid": 3, "type": 30001, "name": "Epee"}])
    got = saved()
    assert got["items"]["23387"]["names"] == {"english": {"Large Red Potion": 1}}
    assert got["items"]["30001"]["names"] == {"french": {"Epee": 1}}
    assert "0" not in got["items"]
    assert "Hero" not in jsonio.dumps(got)


def test_a_shop_row_gives_name_price_and_description_but_a_take_out_row_doesn_t(english: list[str]) -> None:
    learnids.note("dialog", SHOP)
    learnids.note("dialog", TAKE_OUT)                        # [1, bag uid, 0]: no price, not an item type
    items = saved()["items"]
    assert items["23385"]["about"].startswith("Recover 5% Max HP") and items["23385"]["price"] == 60
    assert "price" not in items["20385"] and "about" not in items["20385"]    # the Shard Market's: the day's price
    assert set(items) == {"23385", "20385"}
    english[0] = "spanish"
    learnids.note("dialog", {"open": 1, "top": {"rows": [[{"options": ["Pocion", "unit price 61", "Cura algo"],
                                                            "cmd": [1, 23386, 0]}]]}})
    assert "about" not in saved()["items"]["23386"]            # a description from another language: no


def test_only_living_monsters_are_noted_never_players_pets_or_npcs(english: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [100.0]
    monkeypatch.setattr(learnids.time, "monotonic", stand_in(learnids.time.monotonic, lambda: clock[0]))
    learnids.note("mem state", STATE, 1)
    shark: dict[str, Any] = STATE["entities"][0]
    stronger: dict[str, Any] = {**STATE, "map": {"id": 5264}, "entities": [{**shark, "maxhp": 1500}]}
    learnids.note("mem state", stronger, 1)                  # within 5 s on that link: not looked at
    clock[0] += learnids.STATE_EVERY
    learnids.note("mem state", stronger, 1)
    got = saved()
    assert set(got["monsters"]) == {"610"}
    assert got["monsters"]["610"]["maps"] == [5361, 5264] and got["monsters"]["610"]["maxhp"] == 1500
    text = jsonio.dumps(got)
    assert "Bob" not in text and "Grocer" not in text and "Hero" not in text


def test_skills_get_their_class_and_talents_stay_by_class(english: list[str]) -> None:
    learnids.note("mem skills", SKILLS)
    got = saved()
    assert got["skills"]["731"] == {"names": {"english": {"Rapid Arrows": 1}}, "classes": ["Hunter"]}
    assert got["professions"]["11"]["names"]["english"] == {"Mining": 1}
    assert got["talents"]["Hunter"]["12"]["names"]["english"] == {"Keen Eye": 1}


def test_odd_answers_note_nothing_and_never_raise(english: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    for line, data in (("mem bagfull", {"error": "nobag"}), ("mem bagfull", None), ("mem state", [1, 2]),
                       ("dialog", {"open": 0}), ("mem skills", "x"), ("said 0", [{"text": "hi"}]), ("", None),
                       ("mem bagfull", [{"type": "23387", "name": "x"}, {"type": True, "name": "y"}])):
        learnids.note(line, data)
    assert not os.path.exists(learnids.path())               # nothing new: no file at all
    learnids.save()
    assert not os.path.exists(learnids.path())

    def broken(*_a: object) -> None:
        raise KeyError("a shape nobody saw")
    monkeypatch.setattr(learnids, "_items", broken)
    learnids.note("mem bagfull", [])                         # swallowed: the run goes on
    learnids.note("mem skills", SKILLS)                       # and collecting stops for this run
    assert not learnids._notes.dirty


def test_a_save_adds_to_the_file_and_a_broken_one_is_put_aside(english: list[str]) -> None:
    learnids.note("mem bagfull", [{"type": 23387, "name": "Large Red Potion"}])
    learnids.save()
    table = learnids.read()
    table["items"]["99"] = {"names": {"english": {"Written meanwhile": 1}}}     # another copy, the player
    jsonio.save(learnids.path(), table)
    learnids._notes.seen.clear()                              # as another run
    learnids.note("mem bagfull", [{"type": 23387, "name": "Large Red Potion"}])
    got = saved()
    assert got["items"]["99"] and got["items"]["23387"]["names"]["english"]["Large Red Potion"] == 2
    Path(learnids.path()).write_text("{broken", encoding="utf-8")
    learnids._notes.seen.clear()
    learnids.note("mem bagfull", [{"type": 23397, "name": "Large Blue Potion"}])
    got = saved()
    assert set(got["items"]) == {"23397"}
    aside = [f for f in Path(learnids.path()).parent.iterdir() if f.name.startswith(learnids.FILE + ".corrupt")]
    assert [f.read_text(encoding="utf-8") for f in aside] == ["{broken"]


def test_saves_wait_five_minutes_while_notes_come(english: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    clock = [learnids._notes.saved_at]
    monkeypatch.setattr(learnids.time, "monotonic", stand_in(learnids.time.monotonic, lambda: clock[0]))
    learnids.note("mem bagfull", [{"type": 1001, "name": "A"}])
    assert not os.path.exists(learnids.path())
    clock[0] += learnids.SAVE_EVERY
    learnids.note("mem bagfull", [{"type": 1002, "name": "B"}])
    assert set(learnids.read()["items"]) == {"1001", "1002"}


def test_every_link_answer_is_looked_at(english: list[str], monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from test_link import Sock, patch, port_file
    sock = Sock([b"ok\n", b'[{"uid":1,"type":23387,"name":"Large Red Potion","count":3}]\n'])
    patch(monkeypatch, tmp_path, sock)
    port_file(tmp_path, 1, 17311, "sec", 1000)
    a = link.AgentLink()
    assert a.json("mem bagfull")[0]["type"] == 23387
    assert saved()["items"]["23387"]["names"]["english"] == {"Large Red Potion": 1}


# ---------------------------------------------------------------- the table
@pytest.fixture
def table(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    data = tmp_path / "data"
    data.mkdir()
    jsonio.save(str(data / gameids.FILE), {"format": 1, "items": {"23387": {"name": "Large Red Potion"}},
                                           "skills": {"1101": {"name": "Fireball", "class": "Elf"}},
                                           "monsters": {}, "professions": {}})
    monkeypatch.setattr(paths, "DATA", str(data))
    gameids.forget()
    return data


def test_the_shipped_table_first_then_this_pc_s_english_names(table: Path, english: list[str]) -> None:
    learnids.note("mem bagfull", [{"type": 23387, "name": "Renamed"}, {"type": 16398, "name": "2x experience acceleration card"}])
    english[0] = "french"
    learnids.note("mem bagfull", [{"type": 16397, "name": "carte 1.75x"}])
    learnids.save()
    gameids.forget()
    assert gameids.item_name(23387) == "Large Red Potion"
    assert gameids.item_name(16398) == "2x experience acceleration card"
    assert gameids.item_name(16397) is None                  # only seen in French: not trusted
    assert gameids.item_name(1) is None and gameids.item_name("23387") is None
    assert gameids.skill_name(1101) == "Fireball" and gameids.skill_class(1101) == "Elf"
    assert gameids.numbers_named("items", "large red potion") == [23387]


def test_a_broken_table_answers_nothing(table: Path) -> None:
    (table / gameids.FILE).write_text("{", encoding="utf-8")
    gameids.forget()
    assert gameids.item_name(23387) is None


def test_the_shipped_table_holds_what_the_game_showed() -> None:
    gameids.forget()
    assert gameids.item_name(23387) == "Large Red Potion" and gameids.item_name(23885) == "Hearthstone"
    assert gameids.skill_name(1101) == "Fireball" and gameids.skill_class(1101) == "Elf"
    assert gameids.monster_name(7203) == "Chest"


# ---------------------------------------------------------------- folding players' files in
def learned_file(tmp_path: Path, name: str, **sections: Any) -> str:
    path = tmp_path / name
    jsonio.save(str(path), {"format": learnids.FORMAT, **sections})
    return str(path)


def game_texts(tmp_path: Path, *pairs: tuple[str, str]) -> str:
    folder = tmp_path / "game" / "language" / "english" / "more"
    folder.mkdir(parents=True)
    with open(folder / "item.txt", "w", encoding="utf-8", newline="") as f:
        f.write("".join(k + "\r\n" + v + "\r\n\r\n" for k, v in pairs))
    return str(tmp_path / "game")


def test_merging_takes_english_names_the_game_knows_and_lists_the_rest(table: Path, tmp_path: Path) -> None:
    one = learned_file(tmp_path, "one.json", items={
        "16398": {"names": {"english": {"2x experience acceleration card": 2}}, "about": "Doubles experience"},
        "16397": {"names": {"french": {"carte": 1}}},
        "20387": {"names": {"english": {"Level 2 Ruby Shard": 1}}},
        "23387": {"names": {"english": {"Large Red Potion (new)": 1}}},
        "777": {"names": {"english": {"Not a game item": 1}}},
    }, skills={"1111": {"names": {"english": {"Fire Impact": 1}}, "classes": ["Elf"]}},
       talents={"Elf": {"12": {"names": {"english": {"Keen Eye": 1}}}, "13": {"names": {"english": {"Keen Eye": 1}}}}})
    broken = tmp_path / "broken.json"
    broken.write_text("{", encoding="utf-8")
    # as the game's own files have them: templates for numbered names
    game = game_texts(tmp_path, ("{d}x experience acceleration card", "$1x experience acceleration card"),
                      ("Level {d} Ruby Shard", "Level $1 Ruby Shard"),
                      ("Large Red Potion (new)", "Large Red Potion (new)"), ("Fire Impact", "Fire Impact"))
    report = idsmerge.merge([one, str(broken)], game)
    got = jsonio.read(str(table / gameids.FILE))
    assert got["items"]["16398"] == {"name": "2x experience acceleration card", "about": "Doubles experience"}
    assert "16397" not in got["items"] and "777" not in got["items"]
    assert got["items"]["23387"]["name"] == "Large Red Potion"          # kept, the other listed
    assert got["skills"]["1111"] == {"name": "Fire Impact", "class": "Elf"}
    assert got["items"]["20387"] == {"name": "Level 2 Ruby Shard"}
    assert report[0] == "added: 2 items, 0 monsters, 1 skills, 0 professions"
    text = "\n".join(report)
    assert "broken.json" in text and "Not a game item" in text and "kept 'Large Red Potion'" in text
    assert "'Keen Eye': numbers 12, 13" in text
    assert list(got["items"]) == sorted(got["items"], key=int)


def test_merging_without_the_game_folder_takes_english_names_unchecked(table: Path, tmp_path: Path) -> None:
    one = learned_file(tmp_path, "one.json", monsters={"610": {"names": {"english": {"beak shark": 3}}, "maxhp": 9}})
    assert idsmerge.merge([one])[0] == "added: 0 items, 1 monsters, 0 skills, 0 professions"
    assert gameids.monster_name(610) == "beak shark"


def test_the_game_language_is_read_from_lang_txt(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "language" / "french").mkdir(parents=True)
    (tmp_path / "language" / "lang.txt").write_text("French\n", encoding="utf-8")
    monkeypatch.setattr(gamelang, "_game_folder", stand_in(gamelang._game_folder, lambda: str(tmp_path)))
    monkeypatch.setattr(gamelang._state, "key", None)
    monkeypatch.setattr(gamelang._state, "checked", 0.0)
    assert gamelang.language() == "french"
    (tmp_path / "language" / "lang.txt").write_text("", encoding="utf-8")
    gamelang.forget()
    assert gamelang.language() == "english"
    monkeypatch.setattr(gamelang, "_game_folder", stand_in(gamelang._game_folder, lambda: ""))
    gamelang.forget()
    assert gamelang.language() == "unknown"           # no game folder: nothing says it is English


def test_a_file_broken_inside_is_skipped_and_the_others_go_in(table: Path, tmp_path: Path) -> None:
    bad = learned_file(tmp_path, "bad.json", items={"1": {"names": {"english": {"Odd": "many"}}}},
                       talents={"Elf": ["not", "a", "table"]})
    good = learned_file(tmp_path, "good.json", items={"16398": {"names": {"english": {"Card": 1}}}})
    report = idsmerge.merge([bad, good])
    assert report[0] == "added: 1 items, 0 monsters, 0 skills, 0 professions"
    assert any("skipped" in line and "bad.json" in line for line in report)
    assert "1" not in jsonio.read(str(table / gameids.FILE))["items"]


def test_a_learned_file_broken_inside_is_put_aside_at_the_next_save(english: list[str]) -> None:
    jsonio.save(learnids.path(), {"format": learnids.FORMAT, "items": {"5": {"names": {"english": {"x": "many"}}}}})
    learnids.note("mem bagfull", [{"type": 5, "name": "x"}])
    got = saved()
    assert got["items"]["5"]["names"]["english"] == {"x": 1}
    assert any(f.name.startswith(learnids.FILE + ".corrupt") for f in Path(learnids.path()).parent.iterdir())


def test_a_file_that_can_t_be_read_now_is_never_written_over(english: list[str], monkeypatch: pytest.MonkeyPatch) -> None:
    jsonio.save(learnids.path(), {"format": learnids.FORMAT, "items": {"7": {"names": {"english": {"Old": 4}}}}})
    real = jsonio.read

    def held(path: str, default: Any = None, encoding: str = "utf-8-sig") -> Any:
        raise PermissionError("held by another program")
    monkeypatch.setattr(learnids.jsonio, "read", held)
    learnids.note("mem bagfull", [{"type": 8, "name": "New"}])
    learnids.save()
    monkeypatch.setattr(learnids.jsonio, "read", real)
    assert set(learnids.read()["items"]) == {"7"}            # untouched
    assert learnids._notes.dirty                              # kept for the next save
    learnids.save()
    assert set(learnids.read()["items"]) == {"7", "8"}


def test_a_newer_bot_s_file_is_left_alone(english: list[str]) -> None:
    jsonio.save(learnids.path(), {"format": learnids.FORMAT + 1, "items": {}})
    learnids.note("mem bagfull", [{"type": 8, "name": "New"}])
    learnids.save()
    assert jsonio.read(learnids.path())["format"] == learnids.FORMAT + 1
