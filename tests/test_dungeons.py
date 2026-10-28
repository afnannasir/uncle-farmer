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


def test_learn_remembers_where_a_circle_was(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    learned = tmp_path / "_learned_entrances.json"
    monkeypatch.setattr(dungeons, "LEARNED", str(learned))
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    e = found("Baru Grottoe")
    assert e["at"] is None
    dungeons.learn({"name": e["name"], "map": e["map"]}, "Baru Grottoe Teleportation circle", 180.4, 80.6)
    assert learned.exists()
    got = found("Baru Grottoe")
    assert got["at"] == [180, 81]
    assert got["npc"] == "Baru Grottoe Teleportation circle"


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
    assert found("Baru Grottoe")["at"] is None


def test_data_missing_file_gives_the_empty_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    monkeypatch.setattr(dungeons, "LEARNED", str(tmp_path / "none.json"))
    dungeons._d = None
    assert dungeons.entrances() == []
    assert dungeons.not_found() == []
