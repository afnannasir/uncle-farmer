# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Where each dungeon is entered: the built table against real data, name matching, and the learned
entrances file (written into a temp profiles folder, never the repo)."""

from __future__ import annotations

import pytest

from unclefarmer import dungeons, paths
from pathlib import Path
from conftest import stand_in
from typing import Any
from collections.abc import Iterator


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    dungeons._d = None
    yield
    dungeons._d = None


def test_norm_and_key() -> None:
    assert dungeons._norm("  The  Big   Cave ") == "big cave"
    assert dungeons._norm("The") == "the"
    assert dungeons._norm(None) == ""
    assert dungeons._key({"name": "The Big Cave", "map": 5}) == "big cave|5"


def test_entrances_are_sorted_and_shaped() -> None:
    es = dungeons.entrances()
    assert len(es) == 43
    assert es == sorted(es, key=lambda e: (e["name"].lower(), e["map"]))
    assert all({"name", "map", "npc", "at", "spots"} <= set(e) for e in es)


def test_not_found() -> None:
    nf = dungeons.not_found()
    assert {"name": "Ice and Snow Cave", "maps": [3802]} in nf
    assert all("name" in e and "maps" in e for e in nf)


def found(name: str) -> dict[str, Any]:
    """The dungeon entry, which every caller here knows exists."""
    entry = dungeons.find(name)
    assert entry is not None
    return entry


def test_find_by_name_and_by_npc() -> None:
    assert found("active volcano")["map"] == 1731
    assert found("  Active   Volcano  ")["name"] == "active volcano"
    assert found("Baru Grottoe Teleportation circle")["name"] == "Baru Grottoe"


def test_find_unknown_is_none() -> None:
    assert dungeons.find("no such dungeon") is None
    assert dungeons.find("") is None
    assert dungeons.find(None) is None


def test_find_is_none_when_ambiguous(monkeypatch: pytest.MonkeyPatch) -> None:
    fake: dict[str, Any] = {"entrances": [{"name": "Twin", "npc": "Twin circle", "map": 1, "spots": []},
                          {"name": "Twin", "npc": "Other circle", "map": 2, "spots": []}],
            "not_found": []}
    monkeypatch.setattr(dungeons, "data", stand_in(dungeons.data, lambda: fake))
    monkeypatch.setattr(dungeons, "_learned", stand_in(dungeons._learned, lambda: {}))
    assert dungeons.find("Twin") is None                  # two hits: refuse to guess


def test_is_entrance() -> None:
    e = dungeons.find("active volcano")
    assert dungeons.is_entrance(e, "active volcano Teleportation circle")
    assert dungeons.is_entrance(e, "active volcano Teleportation circle extra")   # a prefixed name
    assert not dungeons.is_entrance(e, "")
    assert not dungeons.is_entrance(e, None)
    assert not dungeons.is_entrance(e, "something else entirely")


def test_death_wormhole_circle_has_its_own_name() -> None:
    # The game names it "Wormhole teleportation Circle": it doesn't start with the dungeon's name.
    e = dungeons.find("death wormhole")
    assert e is not None
    assert dungeons.is_entrance(e, "Wormhole teleportation Circle")
    assert not dungeons.is_entrance(e, "Bubble Cube Teleportation Circle")


def test_hidden_water_cave_circles_are_placed() -> None:
    # Measured in the game: the five circles of hidden water cave (1602), one per stone room.
    want = {"death wormhole": [26, 49], "wolf head mountain": [61, 49], "bubble cube": [96, 49],
            "ice maze": [28, 107], "Herbal Mountain": [93, 107]}
    for name, at in want.items():
        e = dungeons.find(name)
        assert e is not None and e["map"] == 1602 and e["at"] == at, name
n
# a dungeon the game's files place nowhere: every real one has its entrance now
UNPLACED: dict[str, Any] = {"entrances": [{"name": "Hidden Cave", "map": 7, "npc": None, "at": None,
                                          "spots": [[180, 80, "dungeon entrance"]]}],
                            "not_found": []}


def test_learn_remembers_where_a_circle_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    learned = tmp_path / "_learned_entrances.json"
    monkeypatch.setattr(dungeons, "LEARNED", str(learned))
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(dungeons, "data", stand_in(dungeons.data, lambda: UNPLACED))
    e = found("Hidden Cave")
    assert e["at"] is None
    dungeons.learn({"name": e["name"], "map": e["map"]}, "Hidden Cave Teleportation circle", 180.4, 80.6)
    assert learned.exists()
    got = found("Hidden Cave")
    assert got["at"] == [180, 81]
    assert got["npc"] == "Hidden Cave Teleportation circle"


def test_learn_does_not_override_a_known_entrance(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    learned = tmp_path / "_learned_entrances.json"
    monkeypatch.setattr(dungeons, "LEARNED", str(learned))
    e = found("active volcano")
    assert e["at"] == [216, 25]
    dungeons.learn({"name": e["name"], "map": e["map"]}, "wrong circle", 1, 1)
    assert found("active volcano")["at"] == [216, 25]     # the listed cell wins


def test_a_broken_learned_file_is_ignored(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    learned = tmp_path / "_learned_entrances.json"
    learned.write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(dungeons, "LEARNED", str(learned))
    monkeypatch.setattr(dungeons, "data", stand_in(dungeons.data, lambda: UNPLACED))
    assert found("Hidden Cave")["at"] is None


def test_data_missing_file_gives_the_empty_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    monkeypatch.setattr(dungeons, "LEARNED", str(tmp_path / "none.json"))
    dungeons._d = None
    assert dungeons.entrances() == []
    assert dungeons.not_found() == []


def test_entrances_measured_in_the_game_are_placed() -> None:
    # Read from the game's entities on each map (2026-11-15). Lisa's Ukulele is entered through
    # an NPC, not a circle; open sea, space battleship and Treasure pavilion stand far from the
    # two entrances the game's files mark on far off regions, so a search there never finds them.
    want = {"Lisa's Ukulele": ("Villager Lisa", [179, 128]),
            "open sea": ("open sea Teleportation circle", [245, 180]),
            "space battleship": ("space battleship Teleportation circle", [277, 180]),
            "Treasure pavilion": ("Treasure pavilion Teleportation circle", [277, 220]),
            "bottomless pit": ("bottomless pit", [150, 19])}
    for name, (npc, at) in want.items():
        e = dungeons.find(name)
        assert e is not None and e["npc"] == npc and e["at"] == at, name
        assert dungeons.is_entrance(e, npc)
