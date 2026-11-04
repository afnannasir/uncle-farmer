"""Tools, Update shard prices: the nearest Shard Market's list read into data/shards.json.

The market's list is the one the game showed (logs/dialogs.log): a header row, then one row per
shard with options [name, "Unit price N arnebia; ..."] and cmd [1, item id, N]. A price is kept only
when the text and the command agree and it is a sane whole number; everything else - and every shard
the list didn't show - keeps its old price. The file keeps its own layout: only numbers change.
The tests run on a copy of the real data/shards.json.
"""

from __future__ import annotations

import json as stdjson
import shutil
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import paths, stats
from unclefarmer.engine import script as S, town
from conftest import as_bot, returning

REAL = Path(paths.DATA) / "shards.json"


def market(rows: list[tuple[str, int, int | None]], head: bool = True) -> dict[str, Any]:
    """The Shard Market's list: (name, price shown, price the command sends) per row."""
    out: list[list[dict[str, Any]]] = []
    if head:
        out.append([{"flags": 0, "options": ["Welcome to Shard  Market"]},
                    {"flags": 8, "options": ["View description"], "cmd": [4]}])
    for i, (name, shown, sent) in enumerate(rows):
        e: dict[str, Any] = {"flags": 8, "options": [name, "Unit price %d arnebia; can make a gem" % shown]}
        if sent is not None:
            e["cmd"] = [1, 20385 + 20 * i, sent]
        out.append([e])
    return {"row": 0, "col": 0, "rows": out}


class MarketBot:
    """At the Shard Market with its list open."""

    def __init__(self, top: dict[str, Any] | None) -> None:
        self.top = top
        self.lines: list[str] = []
        self.spoken: list[str] = []
        self.notes: list[str] = []
        self.closed = 0

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def dialog(self) -> dict[str, Any]:
        return {"ok": True, "open": 1 if self.top else 0, "top": self.top}

    def note_dialog(self, d: dict[str, Any], why: str) -> None:
        self.notes.append(why)

    def close_dialogs(self) -> None:
        self.closed += 1


@pytest.fixture
def data(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[Path]:
    shutil.copy(REAL, tmp_path / "shards.json")
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    monkeypatch.setattr(town, "visit", returning(""))
    stats.forget_prices()
    yield tmp_path / "shards.json"
    stats.forget_prices()


def prices(path: Path) -> dict[str, int]:
    return {k: v["price"] for k, v in stdjson.loads(path.read_text(encoding="utf-8"))["shards"].items()}


def update(top: dict[str, Any] | None) -> tuple[MarketBot, int]:
    b = MarketBot(top)
    n = town.update_shard_prices(as_bot(b))
    return b, n


# ================================================================ reading and saving
def test_the_market_s_prices_replace_the_old_ones_for_every_shard_it_shows(data: Path) -> None:
    before = prices(data)
    b, n = update(market([("Level 1 Ruby Shard", 2550, 2550), ("Level 1 Sapphire Shard", 21, 21),
                          ("Weapon Enhance Crystal Shard", before["Weapon Enhance Crystal Shard"],
                           before["Weapon Enhance Crystal Shard"])]))
    after = prices(data)
    assert after["Level 1 Ruby Shard"] == 2550 and after["Level 1 Sapphire Shard"] == 21
    assert {k: v for k, v in after.items() if k not in ("Level 1 Ruby Shard", "Level 1 Sapphire Shard")} == \
        {k: v for k, v in before.items() if k not in ("Level 1 Ruby Shard", "Level 1 Sapphire Shard")}
    assert n == 2
    assert "Level 1 Ruby Shard: %d -> 2550 arnebia" % before["Level 1 Ruby Shard"] in b.lines
    assert b.spoken[-1].startswith("shard prices: 2 changed, 1 unchanged, ")
    assert b.closed >= 1 and b.notes == ["reading the shard prices"]


def test_the_file_keeps_its_layout_and_every_other_field(data: Path) -> None:
    text = data.read_text(encoding="utf-8")
    update(market([("Level 1 Ruby Shard", 2550, 2550)]))
    new = data.read_text(encoding="utf-8")
    old_lines, new_lines = text.splitlines(), new.splitlines()
    assert len(old_lines) == len(new_lines)
    changed = [(a, b) for a, b in zip(old_lines, new_lines) if a != b]
    assert len(changed) == 1 and changed[0][1].strip() == '"price": 2550,'
    old, got = stdjson.loads(text), stdjson.loads(new)
    assert got["markets"] == old["markets"]
    assert {k: v["makes"] for k, v in got["shards"].items()} == {k: v["makes"] for k, v in old["shards"].items()}


def test_the_new_prices_are_the_ones_the_results_use(data: Path) -> None:
    update(market([("Level 1 Ruby Shard", 3333, 3333)]))
    assert stats.unit_prices()["level 1 ruby shard"] == (3333, stats.SHARD)
    assert town.shard_catalog()[0]["Level 1 Ruby Shard"]["price"] == 3333


# ================================================================ what is never written
@pytest.mark.parametrize("row, why", [
    (("Level 1 Ruby Shard", 2550, 2551), "isn't the one the menu sends"),
    (("Level 1 Ruby Shard", 0, 0), "isn't a price"),
    (("Level 1 Ruby Shard", 999999999, 999999999), "isn't a price"),
    (("Level 1 Ruby Shard", 2550, None), "not a shard row"),
])
def test_a_price_that_doesn_t_check_out_is_never_written(data: Path, row: tuple[str, int, int | None], why: str) -> None:
    before = prices(data)
    b, n = update(market([row, ("Level 1 Sapphire Shard", 30, 30)]))
    after = prices(data)
    assert after["Level 1 Ruby Shard"] == before["Level 1 Ruby Shard"]
    assert after["Level 1 Sapphire Shard"] == 30 and n == 1
    assert any(why in line for line in b.lines)


def test_a_row_without_a_price_shown_is_left_out(data: Path) -> None:
    before = prices(data)
    top = market([("Level 1 Ruby Shard", 2550, 2550)])
    top["rows"][1][0]["options"] = ["Level 1 Ruby Shard"]
    b, n = update(top)
    assert n == 0 and prices(data) == before
    assert any("no price shown" in line for line in b.lines)
    assert b.spoken == ["no shard prices could be read - nothing changed"]


def test_a_shard_listed_twice_at_two_prices_keeps_its_old_one(data: Path) -> None:
    before = prices(data)
    b, _n = update(market([("Level 1 Ruby Shard", 2550, 2550), ("Level 1  Ruby Shard", 2600, 2600)]))
    assert prices(data)["Level 1 Ruby Shard"] == before["Level 1 Ruby Shard"]
    assert any("listed twice" in line for line in b.lines)


def test_shards_the_list_didn_t_show_keep_their_old_price_and_are_named(data: Path) -> None:
    before = prices(data)
    b, _n = update(market([("Level 1 Ruby Shard", 2550, 2550)]))
    after = prices(data)
    missing = sorted(set(before) - {"Level 1 Ruby Shard"})
    assert all(after[k] == before[k] for k in missing)
    assert "not read, old price kept: " + ", ".join(missing) in b.lines


def test_a_shard_the_price_list_doesn_t_have_is_left_out(data: Path) -> None:
    before = prices(data)
    b, _n = update(market([("Level 9 Dragon Shard", 50000, 50000), ("Level 1 Ruby Shard", 2550, 2550)]))
    assert "Level 9 Dragon Shard" not in prices(data) and set(prices(data)) == set(before)
    assert "not in the price list, left out: Level 9 Dragon Shard" in b.lines


@pytest.mark.parametrize("top", [None, market([("Level 1 Ruby Shard", 2550, 2550)], head=False),
                                 {"rows": [[{"options": ["item storage"]}]]}])
def test_a_menu_that_isn_t_the_market_s_list_changes_nothing(data: Path, top: dict[str, Any] | None) -> None:
    text = data.read_text(encoding="utf-8")
    b, n = update(top)
    assert n == 0 and data.read_text(encoding="utf-8") == text
    assert b.spoken == ["no shard prices could be read - nothing changed"]


def test_a_price_list_that_can_t_be_read_is_said_and_left_alone(data: Path) -> None:
    data.write_text("{broken", encoding="utf-8")
    b, n = update(market([("Level 1 Ruby Shard", 2550, 2550)]))
    assert n == 0 and data.read_text(encoding="utf-8") == "{broken"
    assert b.spoken == ["the shard price list (data\\shards.json) can't be read"]


def test_a_price_not_where_the_file_keeps_it_saves_nothing(data: Path) -> None:
    d = stdjson.loads(data.read_text(encoding="utf-8"))
    d["shards"]["Level 1 Ruby Shard"] = {"makes": "x", "price": 1}          # price not first: not found
    data.write_text(stdjson.dumps(d, indent=1), encoding="utf-8")
    text = data.read_text(encoding="utf-8")
    b, n = update(market([("Level 1 Sapphire Shard", 30, 30), ("Level 1 Ruby Shard", 2550, 2550)]))
    assert n == 0 and data.read_text(encoding="utf-8") == text
    assert any(line.startswith("couldn't save the shard prices") for line in b.spoken)


def test_save_shard_prices_skips_what_isn_t_a_price(data: Path) -> None:
    before = prices(data)
    bad: dict[str, Any] = {"Level 1 Ruby Shard": True, "Level 1 Sapphire Shard": -5, "Not a shard": 10}
    assert town.save_shard_prices(bad) == []
    assert prices(data) == before


# ================================================================ where it is reached from
def test_update_shard_prices_is_a_script_command_and_a_tool() -> None:
    assert "update_shard_prices" in S.COMMANDS
    assert S.check("update_shard_prices\n") is None
    assert stats.tool_name("update_shard_prices") == "Updating shard prices"


def test_tools_update_shard_prices_runs_the_job(monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer.gui import main
    errands: list[str] = []

    class Win:
        def __getattr__(self, name: str) -> Any:
            attr = getattr(main.MainFrame, name)
            return attr.__get__(self) if callable(attr) else attr

        def run_errand(self, text: str, title: str, walk: bool = False) -> None:
            errands.append(text)
    win: Any = Win()
    win.update_shard_prices_now()
    assert errands == ["update_shard_prices\n"]
