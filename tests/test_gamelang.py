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


def test_a_translation_comes_back_in_the_english_packs_wording(tmp_path: Path) -> None:
    """The bot's names are the english pack's, and that pack rewords some of the server's. Live
    with the Spanish pack (2026-11-11): "Mercado de fragmentos" came back as the server's "Shard
    Marketplace", and a run stopped with "couldn't talk to Shard Broker" beside that NPC."""
    lang = tmp_path / "language"
    write(str(lang / "english" / "more" / "NPC.txt"), [
        ("Shard Marketplace", "Shard Broker"),
        ("Bone Cookie", "Pet Bone Cookie"),
        ("Lesser Cattle Soul Stone", "Lesser Cow Soul Stone"),
        ("{d} Cattle", "$1 Cows"),                    # a template: not an exact name's business
    ])
    write(str(lang / "xx" / "more" / "NPC.txt"), [
        ("Shard Marketplace", "Mercado de fragmentos"),
        ("Bone Cookie", "Galleta de Hueso"),
        ("Lesser Cattle Soul Stone", "Piedra menor de alma de vaca"),
        ("Frog", "Rana"),                             # the english pack says nothing: the key stands
    ])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("Mercado de fragmentos") == "Shard Broker"
    assert t.word("Galleta de Hueso") == "Pet Bone Cookie"
    assert t.word("Piedra menor de alma de vaca") == "Lesser Cow Soul Stone"
    assert t.word("Rana") == "Frog"


def test_a_reworded_name_is_found_inside_a_joined_name(tmp_path: Path) -> None:
    """A pet's name is its kind and its own name, turned back part by part: the kind too is the
    english pack's wording."""
    lang = tmp_path / "language"
    write(str(lang / "english" / "more" / "pet.txt"), [("Cattle", "Cow")])
    write(str(lang / "xx" / "more" / "pet.txt"), [("Cattle", "Vaca"), (" belongs to {s}", " pertenece a $1")])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("Vaca Lola pertenece a Hero") == "Cow Lola belongs to Hero"


def test_shown_keeps_the_key_when_the_english_pack_has_holes_or_nothing(tmp_path: Path) -> None:
    eng = {"Bone Cookie": "Pet Bone Cookie", "Arnebia piece": "$1 Arnebia", "Empty": ""}
    assert gamelang._shown("Bone Cookie", eng) == "Pet Bone Cookie"
    assert gamelang._shown("Arnebia piece", eng) == "Arnebia piece"
    assert gamelang._shown("Empty", eng) == "Empty"
    assert gamelang._shown("Frog", eng) == "Frog"


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


def test_english_changes_nothing(game: Path) -> None:
    (game / "language" / "lang.txt").write_text("english", encoding="utf-8")
    gamelang.forget()
    assert gamelang.table() is None
    data = {"name": "Evcil Büyük Kırmızı İksir"}
    assert gamelang.english(data) is data


def test_the_same_original_with_a_space_at_an_edge_does_not_win(tmp_path: Path) -> None:
    """Live with the Spanish pack (2026-11-11): Item2.txt has " Platinum wedding ring" and
    equipment.txt "Platinum wedding ring", both "anillo de boda de platino". Item2.txt is read
    first, and 17 of a character's 19 pieces of equipment came back with a space in front."""
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("确定", "Yes")])
    write(str(lang / "xx" / "more" / "Item2.txt"), [
        (" Platinum wedding ring", "anillo de boda de platino"),
        ("Total Monster Kills: ", "Total de monstruos asesinados: "),      # spaced like its translation: kept
    ])
    write(str(lang / "xx" / "more" / "equipment.txt"), [("Platinum wedding ring", "anillo de boda de platino")])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("anillo de boda de platino") == "Platinum wedding ring"
    assert t.word("Total de monstruos asesinados: ") == "Total Monster Kills: "


def test_an_original_spaced_like_its_translation_wins_whatever_the_order(tmp_path: Path) -> None:
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("确定", "Yes")])
    write(str(lang / "xx" / "more" / "a.txt"), [("Celestial robe", "Tunica"), (" unread messages", " sin leer")])
    write(str(lang / "xx" / "more" / "b.txt"), [(" Celestial robe", "Tunica"), ("unread messages", " sin leer")])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("Tunica") == "Celestial robe"                # the first one was right: it stays
    assert t.word(" sin leer") == " unread messages"           # spaced like " sin leer"


def test_two_different_originals_are_not_touched_by_the_spacing_rule(tmp_path: Path) -> None:
    """"Salir" is Leave, exit and quit: that is a real ambiguity, and the first still wins."""
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("确定", "Yes")])
    write(str(lang / "xx" / "more" / "a.txt"), [("Leave", "Salir")])
    write(str(lang / "xx" / "more" / "b.txt"), [("exit", "Salir"), (" quit", "Salir")])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("Salir") == "Leave"


def marked_game(tmp_path: Path) -> gamelang.Table:
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("(限制)", " (restricted) "), ("(锁定)", " (locked) ")])
    write(str(lang / "xx" / "basic.txt"), [("(限制)", " (restringido) "), ("(锁定)", " (bloqueado) ")])
    write(str(lang / "xx" / "more" / "item.txt"), [
        ("Memory Butterfly", "Mariposa de memoria"),
        ("{d} Memory Butterfly", "$1 Mariposa de memoria"),
        ("Level {d} Turquoise Shard", "Fragmento de turquesa de nivel $1"),
        ("{d} Level {d} Turquoise Shard", "$1 Fragmento de turquesa de nivel $2"),
    ])
    return gamelang.Table(str(lang / "xx"), str(lang / "english"))


def test_a_name_with_the_client_s_mark_after_it_comes_back_name_and_mark(tmp_path: Path) -> None:
    # The storehouse's "deposit item" list in Spanish, live 2026-11-11: the marked entries stayed
    # Spanish, and the shard's came back "1 Level 2 (restringido)  Turquoise Shard" - the hole of
    # its name had taken the mark. Depositing it said 'the menu has no entry like "...Level\ 2    # Turquoise\ Shard..."'.
    t = marked_game(tmp_path)
    assert t.word("78 Mariposa de memoria (restringido) ") == "78 Memory Butterfly (restricted)"
    assert t.word("1 Fragmento de turquesa de nivel 2 (restringido) ") == "1 Level 2 Turquoise Shard (restricted)"
    assert t.word("1 Fragmento de turquesa de nivel 2 (restringido)  (bloqueado) ") ==         "1 Level 2 Turquoise Shard (restricted) (locked)"
    assert t.word("78 Mariposa de memoria") == "78 Memory Butterfly"           # unmarked: as before
    assert t.word(" (restringido) ") == " (restricted) "                       # the mark alone: as before
    assert t.word("Algo nuevo (restringido) ") == "Algo nuevo (restricted)"    # a name it doesn't know


def test_a_pack_that_leaves_the_marks_as_they_are_still_has_them_split_off(tmp_path: Path) -> None:
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("(限制)", " (restricted) ")])
    write(str(lang / "xx" / "more" / "item.txt"), [("{d} Memory Butterfly", "$1 Mariposa de memoria")])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("78 Mariposa de memoria(限制)") == "78 Memory Butterfly (restricted)"


def test_the_template_with_the_most_words_of_its_own_wins(tmp_path: Path) -> None:
    # Turkish, 2026-11-12: basic.txt's "Level {d}" is read before the shards' own lines, and its
    # "Seviye $1" took "Seviye 1 Akuamarin Parçası" whole - the bag's shard came back "Level 1
    # Akuamarin Parçası", so no bag rule knew it and the Shard Market "didn't list" it. A player
    # could sell only the Equipment Enhance Crystal Shard, the one shard with no level in its name.
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("确定", "Yes")])
    write(str(lang / "xx" / "basic.txt"), [("Level {d}", "Seviye $1")])
    write(str(lang / "xx" / "more" / "item.txt"), [
        ("Level {d} Aquamarine Shard", "Seviye $1 Akuamarin Parçası"),
        ("Level {d} Ruby Shard", "Seviye $1 Yakut Parçası"),
        ("Equipment Enhance Crystal Shard", "Ekipman Geliştirme Kristal Parçası"),
    ])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("Seviye 1 Akuamarin Parçası") == "Level 1 Aquamarine Shard"
    assert t.word("Seviye 3 Yakut Parçası") == "Level 3 Ruby Shard"
    assert t.word("Ekipman Geliştirme Kristal Parçası") == "Equipment Enhance Crystal Shard"
    assert t.word("Seviye 12") == "Level 12"                  # the short one still does its own job


def test_templates_with_as_many_words_keep_the_order_they_were_read_in(tmp_path: Path) -> None:
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("确定", "Yes")])
    write(str(lang / "xx" / "more" / "a.txt"), [("First {d} one", "Abc $1 xyz"), ("Other {d} one", "Abc $1 xyz")])
    t = gamelang.Table(str(lang / "xx"), str(lang / "english"))
    assert t.word("Abc 4 xyz") == "First 4 one"


def counted_game(tmp_path: Path) -> gamelang.Table:
    lang = tmp_path / "language"
    write(str(lang / "english" / "basic.txt"), [("(限制)", " (restricted) ")])
    write(str(lang / "xx" / "basic.txt"), [("(限制)", " (sınırlı) "), ("Level {d}", "Seviye $1"),
                                           ("{d} hours {d} minutes", "$1 saat $2 dakika"), ("hours", "saat")])
    write(str(lang / "xx" / "more" / "item.txt"), [
        ("Level {d} Grape Stone Shard", "Seviye $1 Üzüm Taşı Parçası"),
        ("Equipment Enhance Crystal Shard", "Ekipman Geliştirme Kristal Parçası"),
    ])
    return gamelang.Table(str(lang / "xx"), str(lang / "english"))


def test_a_count_before_a_name_comes_back_count_and_name(tmp_path: Path) -> None:
    # The storehouse's "deposit item" list in Turkish, live 2026-11-13. The pack has no line for a
    # counted shard, so "16 Seviye 1 Üzüm Taşı Parçası (sınırlı) " came back with its name still
    # Turkish and depositing it said 'the menu has no entry like "...Level\ 1\ Grape\ Stone\ Shard..."'.
    t = counted_game(tmp_path)
    assert t.word("16 Seviye 1 Üzüm Taşı Parçası (sınırlı) ") == "16 Level 1 Grape Stone Shard (restricted)"
    assert t.word("16 Seviye 1 Üzüm Taşı Parçası") == "16 Level 1 Grape Stone Shard"
    assert t.word("1445 Ekipman Geliştirme Kristal Parçası") == "1445 Equipment Enhance Crystal Shard"
    assert t.word("16 Bilinmeyen şey") == "16 Bilinmeyen şey"            # a name it doesn't know: as it was


def test_a_line_of_the_pack_for_the_whole_text_wins_over_the_count(tmp_path: Path) -> None:
    # "3 saat 7 dakika" is the pack's own "{d} hours {d} minutes", not 3 of something called
    # "saat 7 dakika": with the count tried first, 239 phrases of the real packs came back broken.
    t = counted_game(tmp_path)
    assert t.word("3 saat 7 dakika") == "3 hours 7 minutes"

