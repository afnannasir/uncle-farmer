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


def test_english_changes_nothing(game: Path) -> None:
    (game / "language" / "lang.txt").write_text("english", encoding="utf-8")
    gamelang.forget()
    assert gamelang.table() is None
    data = {"name": "Evcil Büyük Kırmızı İksir"}
    assert gamelang.english(data) is data
