# pyright: reportPrivateUsage=false
"""Odd input that used to break things: scripts, settings, results files, translations, weights.

  - script: `monsters` takes every operator SCRIPTING.md promises (* / // % - abs round); bytes are
    held to the text limits; nan, inf and 1e999 are script errors, not crashes; a byte order mark
    at the start is not part of the first command.
  - settings and results files saved with a byte order mark read as they are (they went to
    .corrupt with defaults, or lost their first record), and a second broken file never
    overwrites the first one's .corrupt copy.
  - a .po round trip keeps U+2028 and the other Unicode line separators inside a text.
  - one bad results line (no object, a bad start, a null level) costs only itself.
  - a load that didn't move keeps the item's learned weight (it became 0)."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import catalog, settings, stats
from unclefarmer.engine import bagrules, script


def value(expr: str, monsters: int = 4) -> Any:
    node = script.compile_expr(expr, 1)
    return script.evaluate(node, {"monsters": script._Lazy(lambda: monsters)},
                           {"abs": abs, "round": round, "min": min, "max": max, "int": int})


@pytest.mark.parametrize("expr,want", [("monsters * 2", 8), ("2 * monsters", 8), ("monsters / 2", 2.0),
                                       ("8 / monsters", 2.0), ("monsters // 3", 1), ("monsters % 3", 1),
                                       ("-monsters", -4), ("abs(monsters - 10)", 6), ("round(monsters)", 4),
                                       ("max(monsters, 2) * 3 - 1", 11), ("monsters * 2 > 7", True)])
def test_monsters_takes_the_documented_operators(expr: str, want: Any) -> None:
    assert value(expr) == want


def test_bytes_are_held_to_the_text_limits() -> None:
    with pytest.raises(script.ScriptError):
        script.compile_expr("b'" + "a" * (script.MAX_TEXT + 1) + "'", 1)
    with pytest.raises(ValueError):
        value("b'ab' * 1000000")
    big = script.compile_expr("x + x", 1)                    # a concatenation that grows too far
    with pytest.raises(ValueError):
        script.evaluate(big, {"x": b"a" * 6000}, {})
    with pytest.raises(ValueError):
        script.evaluate(big, {"x": "a" * 6000}, {})


@pytest.mark.parametrize("text", ["repeat 1e999\nsay hi\nend\n", "wait nan\n", "wait inf\n", "set n = 1e999\n",
                                  "walk 1e999,5\n", "key t 1e999\n"])
def test_numbers_that_arent_finite_are_script_errors(text: str) -> None:
    err = script.check(text)
    assert err is not None and err.startswith("line 1:")


def test_a_byte_order_mark_is_not_part_of_the_first_command() -> None:
    assert script.check("﻿say hi\n") is None
    assert script.check("﻿bogus\n") == script.check("bogus\n")


def test_settings_saved_with_a_byte_order_mark_are_kept(tmp_path: Path) -> None:
    p = tmp_path / "settings.json"
    p.write_text('{"language": "ur"}', encoding="utf-8-sig")
    data = settings._read(str(p), settings.APP_DEFAULTS, settings.AppSettings)
    assert data["language"] == "ur"
    assert not os.path.exists(str(p) + ".corrupt")


def test_a_second_broken_file_keeps_the_first_ones_copy(tmp_path: Path) -> None:
    p = tmp_path / "x.json"
    for i in range(3):
        p.write_text("{broken %d" % i, encoding="utf-8")
        settings._read(str(p), settings.APP_DEFAULTS, settings.AppSettings)
    kept = sorted(f.name for f in tmp_path.iterdir() if ".corrupt" in f.name)
    assert kept == ["x.json.corrupt", "x.json.corrupt.2", "x.json.corrupt.3"]
    assert (tmp_path / "x.json.corrupt").read_text() == "{broken 0"


def results(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, lines: list[str], bom: bool = False) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    (tmp_path / "Hero.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8-sig" if bom else "utf-8")


def rec(**kw: Any) -> str:
    return json.dumps(dict({"kind": "farming", "start": "2026-11-01 10:00:00", "kills": 1}, **kw))


def test_a_results_file_with_a_byte_order_mark_keeps_its_first_record(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results(tmp_path, monkeypatch, [rec(kills=5), rec(kills=7)], bom=True)
    assert [r["kills"] for r in stats.runs("Hero")] == [5, 7]


def test_bad_lines_cost_only_themselves(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    results(tmp_path, monkeypatch, ["[1, 2]", "5", "not json", rec(start="garbage", kills=3), rec(start=None, kills=4),
                                    rec(shards=[1, 2], kills=6), rec(kills=7)])
    assert [r["kills"] for r in stats.runs("Hero", since=0)] == [7]
    assert [r["kills"] for r in stats.runs("Hero")] == [3, 4, 7]       # (no period asked: the start doesn't matter)


def test_adding_up_takes_null_and_odd_numbers_in_its_stride() -> None:
    total = stats.add_up([{"kind": "farming", "level": None, "kills": 1, "minutes": None},
                          {"kind": "farming", "level": "12", "kills": "3"},
                          {"kind": "farming", "level": 40, "kills": 2, "xp_needed": 99}])
    assert total["kills"] == 3 and total["level"] == 40 and total["xp_needed"] == 99


def test_a_translation_with_unicode_line_separators_survives_the_round_trip() -> None:
    entries = [("a b", "c d"), ("x\x85y", "z w"), ("plain", "sada")]
    text = catalog.render_po({"Language": "ur"}, entries)
    assert sorted(catalog.parse_po(text)[1]) == sorted(entries)
    again = catalog.render_po({"Language": "ur"}, catalog.parse_po(text)[1])
    assert sorted(catalog.parse_po(again)[1]) == sorted(entries)


def test_an_unchanged_load_keeps_the_weight(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(bagrules, "WEIGHTS", str(tmp_path / "w.json"))
    assert bagrules.learn_weight("Wolf Fur", 100, 98, 2) == 1.0
    assert bagrules.learn_weight("Wolf Fur", 100, 100, 2) is None
    assert bagrules.weights() == {"wolf fur": 1.0}
