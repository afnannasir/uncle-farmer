# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The game in another language: what the agent reads (translated by the game) comes back in
English, so potions, skills, menus and pets are found as with the English game."""

from __future__ import annotations

import os

import pytest

from unclefarmer import gamelang
from pathlib import Path
from conftest import stand_in


def write(path: str, pairs: list[tuple[str, str]], crlf: bool=True) -> None:
    os.makedirs(os.path.dirname(path), exist_ok=True)
    nl = "\r\n" if crlf else "\n"
    with open(path, "w", encoding="utf-8", newline="") as f:
        f.write((nl + nl).join(k + nl + v for k, v in pairs) + nl)


@pytest.fixture
def game(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    lang = tmp_path / "language"
    # the client's own words are keyed in Chinese; their English is in the english folder
    write(str(lang / "english" / "basic.txt"), [("确定", "Yes"), ("地图复活", "Map Respawn")])
    write(str(lang / "english" / "more" / "item.txt"), [("{d} Pet Rice Roll", "$1 Pet Rice Roll")])
    write(str(lang / "xx" / "basic.txt"), [("确定", "Evet"), ("地图复活", "Haritada doğ")])
    write(str(lang / "xx" / "more" / "item.txt"), [
        ("Pet Large Red Potion", "Evcil Büyük Kırmızı İksir"),
        ("Frog", "Kurbağa"),
        ("Crimson Scorpion", "kızıl akrep"),
        ("{d} Pet Rice Roll", "$1 Evcil Pirinç Rulosu"),
        ("Map Respawn with Resurrection Crystal; Costs {d} Resurrection Crystals",
         "Haritada kristal ile doğ; $1 kristal harcanacak"),
        (" belongs to {s}", " ait olduğu kişi $1"),
        ("Hero", "Kahraman"),                      # also a word: a player called that keeps the name
    ])
    write(str(lang / "xx" / "more" / "broken.txt"), [("Bash", "Darbe")], crlf=False)   # the game ignores it too
    (lang / "lang.txt").write_text("xx", encoding="utf-8")
    monkeypatch.setattr(gamelang, "_game_folder", stand_in(gamelang._game_folder, lambda: str(tmp_path)))
    monkeypatch.setattr(gamelang._state, "key", None)
    monkeypatch.setattr(gamelang._state, "checked", 0.0)
    return tmp_path


def test_names_and_menu_lines_come_back_in_english(game: Path) -> None:
    t = gamelang.table()
    assert t is not None
    assert t.word("Evcil Büyük Kırmızı İksir") == "Pet Large Red Potion"
    assert t.word("Evet") == "Yes" and t.word("Haritada doğ") == "Map Respawn"   # Chinese keys
    assert t.word("12 Evcil Pirinç Rulosu") == "12 Pet Rice Roll"                  # a template
    assert t.word("Haritada kristal ile doğ; 3 kristal harcanacak") == \
        "Map Respawn with Resurrection Crystal; Costs 3 Resurrection Crystals"
    assert t.word("Darbe") == "Darbe"             # a file with bare line feeds: ignored, as by the game
    assert t.word("Something new") == "Something new"


def test_a_pet_s_name_is_turned_back_part_by_part(game: Path) -> None:
    t = gamelang.table()
    assert t is not None
    assert t.word("Kurbağa Hopper ait olduğu kişi Kahraman") == "Frog Hopper belongs to Kahraman"


def test_an_agent_answer_is_english_but_players_keep_their_names(game: Path) -> None:
    snap = {"self": {"id": 7, "kind": "player", "name": "Kahraman", "hp": 5},
            "entities": [{"id": -1, "kind": "monster", "name": "kızıl akrep"},
                         {"id": 9, "kind": "player", "name": "Kahraman"},
                         {"id": 10000001, "kind": "player", "name": "Kurbağa x ait olduğu kişi Kahraman"}],
            "map": {"id": 5121, "name": "Haritada doğ"}}
    out = gamelang.english(snap)
    assert out["self"]["name"] == "Kahraman"
    assert [e["name"] for e in out["entities"]] == ["Crimson Scorpion", "Kahraman",
                                                    "Frog x belongs to Kahraman"]
    assert out["self"]["hp"] == 5


def test_the_server_s_own_text_is_left_as_it_came(game: Path) -> None:
    assert "said" not in gamelang.COMMANDS          # IN op=60 lines: English from the server
    entry = {"raw": "Pet Large Red Potion", "text": "Evcil Büyük Kırmızı İksir"}
    sheet = {"counter_name": "Evet", "level": 50}
    out = gamelang.english({"top": {"entries": [[entry]]}, "self": {"sheet": sheet}})
    assert out["top"]["entries"][0][0] == {"raw": "Pet Large Red Potion", "text": "Pet Large Red Potion"}
    assert out["self"]["sheet"] == sheet


def test_english_changes_nothing(game: Path) -> None:
    (game / "language" / "lang.txt").write_text("english", encoding="utf-8")
    gamelang.forget()
    assert gamelang.table() is None
    data = {"name": "Evcil Büyük Kırmızı İksir"}
    assert gamelang.english(data) is data


# ---------------------------------------------------------------- the english folder's wording
@pytest.fixture
def packs(tmp_path: Path) -> Path:
    """An english folder that rewords server text (as the game's own one does), an English variant
    that leaves it as the server sends it, and a translation."""
    lang = tmp_path / "language"
    write(str(lang / "english" / "more" / "item.txt"), [
        ("Bone Cookie", "Pet Bone Cookie"),
        ("Lesser Cattle Soul Stone", "Lesser Cow Soul Stone"),
        ("{d} Arnebia piece", "$1 Arnebia"),
        ("Level {d} Ruby Shard", "Level $1 Ruby Shard"),
        ("Cow", "Cow"),
    ])
    write(str(lang / "en_uk" / "more" / "item.txt"), [("Bone Cookie", "Bone Cookie")])   # kept as sent
    write(str(lang / "xx" / "more" / "item.txt"), [
        ("Bone Cookie", "Kemik Kurabiye"),
        ("Pharmacist Mia", "Eczacı Mia"),
        ("Apothecary Mia", "Eczacı Mia"),           # one translation for two originals
        ("Armory", "cephanelik"),
        ("armory", "cephanelik"),
        ("Level {d} {s}", "Seviye $1 $2"),
        ("Level {d} Ruby Shard", "Seviye $1 Yakut Parçası"),
    ])
    return lang


KNOWN = {"pharmacist mia": "Pharmacist Mia", "armory": "Armory", "pet bone cookie": "Pet Bone Cookie"}


def test_an_english_variant_comes_back_in_the_english_folder_s_wording(packs: Path) -> None:
    t = gamelang.Table(str(packs / "en_uk"), str(packs / "english"), KNOWN)
    assert t.word("Bone Cookie") == "Pet Bone Cookie"                 # its entry keeps the server's text
    assert t.word("Lesser Cattle Soul Stone") == "Lesser Cow Soul Stone"   # no entry: as the server sent it
    assert t.word("12 Arnebia piece") == "12 Arnebia"                  # a template it doesn't have
    assert t.word("Cow") == "Cow"


def test_a_translation_comes_back_in_the_english_folder_s_wording(packs: Path) -> None:
    t = gamelang.Table(str(packs / "xx"), str(packs / "english"), KNOWN)
    assert t.word("Kemik Kurabiye") == "Pet Bone Cookie"               # not the server's "Bone Cookie"


def test_a_shared_translation_is_the_name_the_bot_knows_written_its_way(packs: Path) -> None:
    t = gamelang.Table(str(packs / "xx"), str(packs / "english"), KNOWN)
    assert t.word("Eczacı Mia") == "Pharmacist Mia"
    assert t.word("cephanelik") == "Armory"


def test_the_most_particular_template_comes_first(packs: Path) -> None:
    t = gamelang.Table(str(packs / "xx"), str(packs / "english"), KNOWN)
    assert t.word("Seviye 3 Yakut Parçası") == "Level 3 Ruby Shard"


def test_the_bot_s_known_names_come_from_its_data() -> None:
    names = gamelang.known_names()
    assert len(names) > 1000
    assert all(k == " ".join(k.split()).lower() for k in names)


# ---------------------------------------------------------------- the game's ids before any words
@pytest.fixture
def id_tables(monkeypatch: pytest.MonkeyPatch) -> None:
    t = gamelang._Ids.__new__(gamelang._Ids)
    t.items = {23386: "Medium Red Potion"}
    t.skills = {1611: "Soul Purify"}
    t.npcs = {(1081, -19): ("Pharmacist Mia", 67.0, 64.0)}
    t.by_name = None
    monkeypatch.setattr(gamelang, "_ids", t)


@pytest.mark.usefixtures("id_tables")
def test_ids_settle_npcs_items_and_skills_whatever_the_words(packs: Path) -> None:
    t = gamelang.Table(str(packs / "xx"), str(packs / "english"), KNOWN)
    snap = {"map": {"id": 1081, "name": "x"},
            "entities": [{"id": -19, "kind": "npc", "name": "Eczacı Mia", "x": 67.0, "y": 65.0},  # a shared word
                         {"id": -19, "kind": "monster", "name": "Kemik Kurabiye", "x": 67, "y": 64},  # words only
                         {"id": -5, "kind": "npc", "name": "cephanelik", "x": 1, "y": 1}]}      # no id known
    out = gamelang.english(snap, t)
    assert [e["name"] for e in out["entities"]] == ["Pharmacist Mia", "Pet Bone Cookie", "Armory"]
    # after a game update shifted the ids, that id belongs to an NPC standing elsewhere: words decide
    moved = {"map": {"id": 1081}, "entities": [{"id": -19, "kind": "npc", "name": "cephanelik", "x": 20, "y": 20}]}
    assert gamelang.english(moved, t)["entities"][0]["name"] == "Armory"
    skills = gamelang.english({"combat": [{"id": 1611, "kind": 1, "name": "Ruh Arındırma"}],
                               "talents": [], "profession": [{"id": 9, "name": "cephanelik"}]}, t)
    assert skills["combat"][0]["name"] == "Soul Purify" and skills["profession"][0]["name"] == "Armory"
    bag = gamelang.english([{"uid": 5, "type": 23386, "name": "Orta Kırmızı İksir", "count": 3},
                            {"uid": 6, "type": 1, "name": "Kemik Kurabiye", "count": 1}], t)
    assert [r["name"] for r in bag] == ["Medium Red Potion", "Pet Bone Cookie"]


@pytest.mark.usefixtures("id_tables")
def test_menu_rows_are_settled_by_item_id_and_names_lead_to_ids(packs: Path) -> None:
    t = gamelang.Table(str(packs / "xx"), str(packs / "english"), KNOWN)
    menu = gamelang.english({"ok": True, "layout": "potion_shop", "verified": True,
                             "items": [{"row": 0, "col": 0, "id": 23386, "name": "Orta Kırmızı İksir", "price": 150}]}, t)
    assert menu["items"][0]["name"] == "Medium Red Potion"
    assert "menu" in gamelang.COMMANDS
    assert gamelang.item_type("medium  red potion") == 23386 and gamelang.item_type("Nothing") is None


def test_exact_matches_ignore_case_where_one_entry_fits(packs: Path) -> None:
    t = gamelang.Table(str(packs / "xx"), str(packs / "english"), KNOWN)
    assert t.word("KEMIK kurabiye") == "Pet Bone Cookie"
    assert t.word("Eczacı mia") == "Pharmacist Mia"


def test_each_game_folder_has_its_own_language(packs: Path, tmp_path: Path) -> None:
    (packs / "lang.txt").write_text("xx", encoding="utf-8")
    other = tmp_path / "second"
    (other / "language" / "english").mkdir(parents=True)
    (other / "language" / "lang.txt").write_text("english", encoding="utf-8")
    gamelang.forget()
    assert gamelang.table(str(tmp_path)) is not None            # the first copy plays in xx
    assert gamelang.table(str(other)) is None                   # the second in English


def test_the_player_is_told_about_another_pack_or_an_old_english_one(packs: Path, tmp_path: Path) -> None:
    (packs / "lang.txt").write_text("xx", encoding="utf-8")
    notes = gamelang.notes(str(tmp_path))
    assert any("language is xx" in n for n in notes)
    assert any("older than the one" in n and "shops.txt" in n for n in notes)     # its english folder lacks them
    for name in gamelang.ENGLISH_PACK_FILES:
        write(str(packs / "english" / "more" / name), [("a", "b")])
    (packs / "lang.txt").write_text("english", encoding="utf-8")
    assert gamelang.notes(str(tmp_path)) == []


def test_server_text_is_put_in_the_english_pack_s_wording(packs: Path) -> None:
    w = gamelang.EnglishWording(str(packs / "english"))
    assert w.shown("Bone Cookie") == "Pet Bone Cookie"
    assert w.shown("12 Arnebia piece") == "12 Arnebia"
    assert w.shown("Something new") == "Something new"


def test_the_id_tables_ship_with_the_bot() -> None:
    table = gamelang.ids()
    assert len(table.items) > 100 and len(table.skills) > 10 and len(table.npcs) > 50
    assert all(mid > 0 and nid < 0 for mid, nid in table.npcs)       # NPCs only, never a player
