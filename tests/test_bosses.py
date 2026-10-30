# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Field bosses are never targets, nor is anything near one, and the bot
walks off when one comes within boss_distance steps."""

from __future__ import annotations

import contextlib

from conftest import as_bot, ME
from test_wrong_target import Fighter

from unclefarmer.engine import bosses
from collections.abc import Callable, Sequence
from typing import Any
from pathlib import Path

import pytest


def mob(i: int, name: str, x: float | int, y: float=100.0, hp: int=1000) -> dict[str, Any]:
    return {"id": i, "kind": "monster", "side": -2, "name": name, "x": x, "y": y, "hp": hp, "maxhp": hp}


def test_the_boss_list_is_the_named_field_bosses_only() -> None:
    n = bosses.names()
    assert n["pig-face bat king"] == 20
    for boss in ("man-wolf caesar", "monkey king wukong", "demonized barbarian tyrant", "lich king",
                 "vampire lord dracula", "dark priest", "meteor queen elsa", "fire lord", "pharaoh tar",
                 "ice queen los", "elven king erth", "whale monster bigger"):
        assert boss in n, boss
    for other in ("year beast", "golden pig", "super pharaoh", "captain", "hell wraith"):
        assert other not in n, other      # event and dungeon super monsters
    assert bosses.is_boss(mob(-1, "Pig-Face  Bat King", 0))
    assert not bosses.is_boss(mob(-1, "Crimson Scorpion", 0))
    assert not bosses.is_boss({"kind": "player", "name": "Death Worm"})


def test_no_boss_and_nothing_near_one_is_a_target() -> None:
    near_boss = mob(-2, "Wolf", 112.0)
    far = mob(-3, "Wolf", 60.0)
    boss = mob(-1, "Death Worm", 120.0, hp=900000)
    b = Fighter([boss, near_boss, far], {"id": ME, "x": 100.0, "y": 100.0})
    b.p["boss_distance"] = 40
    b.walk_dist = lambda snap, e, limit=None: abs(e["x"] - 100.0)
    assert [e["id"] for e in b.targets(b.snap())] == [-3]
    b.p["boss_distance"] = 0                              # keeping away switched off
    assert {e["id"] for e in b.targets(b.snap())} >= {-2, -3}


class Walker:
    def __init__(self, snap: dict[str, Any], cells: list[tuple[int, int]]) -> None:
        self.p = {"boss_distance": 40}
        self.s, self.cells = snap, list(cells)
        self.lines: list[str] = []
        self.walked: list[tuple[float, float]] = []

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def snap(self) -> dict[str, Any]:
        return self.s

    def random_cell(self, map_id: int, area: Sequence[float] | None, me: dict[str, Any], radius: int=40) -> tuple[int, int] | None:
        return self.cells.pop(0) if self.cells else None

    def walk_to(self, cell: tuple[float, float], near: Callable[[dict[str, Any]], bool] | None=None) -> str:
        self.walked.append(cell)
        return "arrived"

    def timed(self, phase: str) -> contextlib.nullcontext[None]:
        return contextlib.nullcontext()


def test_a_boss_close_by_sends_us_to_the_spot_furthest_from_it() -> None:
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 110.0, hp=900000)]}
    w = Walker(snap, [(105, 100), (40, 100), (90, 100)])
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.walked == [(40, 100)]
    assert w.lines == ["Death Worm (a boss) is 10 steps away - keeping 40 steps from it"]


def test_a_boss_far_off_or_the_option_off_changes_nothing() -> None:
    snap: dict[str, Any] = {"self": {"id": ME, "x": 100.0, "y": 100.0},
                            "entities": [mob(-1, "Death Worm", 150.0, hp=900000)]}
    w = Walker(snap, [(40, 100)])
    assert not bosses.keep_away(as_bot(w), snap, 5121, None)
    snap["entities"][0]["x"] = 105.0
    w.p["boss_distance"] = 0
    assert not bosses.keep_away(as_bot(w), snap, 5121, None) and w.walked == []


def test_from_texts_reads_nested_descriptions() -> None:
    data = {"texts": [{"a": "Pig-Face Bat King; level 20 super monster; a boss"},
                      {"b": ["Lich King; level 90 super monster; cold"]},
                      "Crimson Scorpion; level 10 elite monster; not a boss"]}
    import json
    import os
    import tempfile
    fd, path = tempfile.mkstemp(suffix=".json")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f)
        got = bosses._from_texts(path)
        assert got == {"pig-face bat king": 20, "lich king": 90}
    finally:
        os.remove(path)


def test_from_texts_is_empty_when_the_file_cannot_be_read(tmp_path: Path) -> None:
    assert bosses._from_texts(str(tmp_path / "missing.json")) == {}
    broken = tmp_path / "broken.json"
    broken.write_text("{not json", encoding="utf-8")
    assert bosses._from_texts(str(broken)) == {}


def test_names_falls_back_to_an_empty_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bosses, "FILE", "no-such-bosses.json")
    monkeypatch.setattr(bosses, "_names", None)
    assert bosses.names() == {}


def test_in_sight_keeps_only_living_bosses() -> None:
    snap = {"entities": [mob(-1, "Death Worm", 10, hp=0), mob(-2, "Death Worm", 20, hp=10),
                         mob(-3, "Wolf", 30, hp=100)]}
    assert [e["id"] for e in bosses.in_sight(snap)] == [-2]


def test_too_close_compares_the_straight_line() -> None:
    b = mob(-2, "Wolf", 110.0)
    assert bosses.too_close(b, [mob(-1, "Death Worm", 100.0)], 20)
    assert not bosses.too_close(b, [mob(-1, "Death Worm", 100.0)], 5)


def test_keep_away_does_nothing_without_a_reachable_cell() -> None:
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 105.0, hp=900000)]}
    w = Walker(snap, [])
    assert not bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.walked == []


def test_keep_away_tries_the_whole_map_when_a_farm_area_is_set() -> None:
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 105.0, hp=900000)]}
    w = Walker(snap, [(90, 100), (40, 100), (30, 100)])
    assert bosses.keep_away(as_bot(w), snap, 5121, [80, 80, 120, 120])
    assert w.walked == [(30, 100)]


def test_keep_away_farms_on_when_nowhere_is_further_off() -> None:
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 105.0, hp=900000)]}
    w = Walker(snap, [(101, 100), (102, 100)])
    assert not bosses.keep_away(as_bot(w), snap, 5121, None)
    assert w.walked == []


def test_keep_away_says_the_warning_once_per_boss() -> None:
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 105.0, hp=900000)]}
    w = Walker(snap, [])
    w.random_cell = lambda *a, **k: (40, 100)   # type: ignore[method-assign]
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert len(w.lines) == 1
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert len(w.lines) == 1                     # already told for this boss
    snap["entities"] = [mob(-2, "Lich King", 105.0, hp=900000)]
    assert bosses.keep_away(as_bot(w), snap, 5121, None)
    assert len(w.lines) == 2                     # a different boss is told


def test_main_reports_bosses_missing_from_the_game_text(monkeypatch: pytest.MonkeyPatch,
                                                       capsys: pytest.CaptureFixture[str]) -> None:
    def found(path: str) -> dict[str, int]:
        return {"lich king": 90}

    monkeypatch.setattr(bosses, "names", lambda: {"lich king": 90, "new boss": 5})
    monkeypatch.setattr(bosses, "_from_texts", found)
    bosses.main()
    out = capsys.readouterr().out.strip()
    assert "2 bosses, 1 not found" in out and "new boss" in out
