"""What the items a run used up cost in arnebia: unit price x how many were used, per item and in all.

The prices are the project's own stored ones (real data here, not stand-ins): a shop's fixed price
from data/knowledge.json, a shard's Shard Market price from data/shards.json - which changes, so
those are shown as estimates. Bought items are not "used"; an item without a price is named, never
counted as free; a run saved twice counts once; a saved run keeps the prices it was costed at.
"""

from __future__ import annotations

import json as stdjson
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import paths, stats

POTION = "Large Red Potion"            # Apothecary Phillips sells it for 450 arnebia (knowledge.json)
PET_POTION = "Pet Small Blue Potion"   # 120 arnebia there
SHARD = "Level 1 Ruby Shard"
NO_PRICE = "Rice Roll"                 # nobody in the game data sells it for arnebia


def shard_price(name: str) -> int:
    with open(Path(paths.DATA) / "shards.json", encoding="utf-8") as f:
        price: int = stdjson.load(f)["shards"][name]["price"]
    return price


def tally(changes: dict[str, int], bought: dict[str, int] | None = None, tool: str | None = None) -> stats.Tally:
    t = stats.Tally("Hero", "Wolves", tool=tool)
    part = t.parts[t.main]
    part.changes = dict(changes)
    part.moved["bought"] = dict(bought or {})
    return t


def spent(r: dict[str, Any]) -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = r["spent"]
    return out


# ================================================================ the prices
def test_shop_prices_are_the_fixed_ones_and_shard_prices_the_market_s() -> None:
    prices = stats.unit_prices()
    assert prices[POTION.lower()] == (450, stats.SHOP)
    assert prices[PET_POTION.lower()] == (120, stats.SHOP)
    assert prices[SHARD.lower()] == (shard_price(SHARD), stats.SHARD)
    assert NO_PRICE.lower() not in prices


def test_the_stored_shop_prices_are_read_never_written() -> None:
    before = (Path(paths.DATA) / "knowledge.json").read_bytes()
    stats.unit_prices()
    stats.report(tally({POTION: -3}).result(), "x")
    assert (Path(paths.DATA) / "knowledge.json").read_bytes() == before


# ================================================================ one run
def test_each_item_used_costs_its_count_times_its_unit_price() -> None:
    r = tally({POTION: -12, PET_POTION: -5, SHARD: -4}).result()
    s = spent(r)
    assert s[POTION] == {"n": 12, "price": 450, "source": stats.SHOP, "cost": 5400}
    assert s[PET_POTION] == {"n": 5, "price": 120, "source": stats.SHOP, "cost": 600}
    assert s[SHARD] == {"n": 4, "price": shard_price(SHARD), "source": stats.SHARD, "cost": 4 * shard_price(SHARD)}
    text = stats.report(r, "Results")
    assert "    Large Red Potion: 12 used x 450 = 5,400 arnebia" in text
    assert "    Pet Small Blue Potion: 5 used x 120 = 600 arnebia" in text
    total = 5400 + 600 + 4 * shard_price(SHARD)
    assert "Total arnebia spent on items used: {:,} (estimated: it includes shards)".format(total) in text


def test_shard_prices_come_after_a_note_that_they_are_estimated() -> None:
    text = stats.report(tally({POTION: -2, SHARD: -3}).result(), "Results").splitlines()
    fixed = text.index("At the shops' fixed prices:")
    note = text.index("Shard prices change at the Shard Market, so these prices are estimated:")
    shard_line = next(i for i, line in enumerate(text) if line.strip().startswith(SHARD + ":"))
    potion_line = next(i for i, line in enumerate(text) if line.strip().startswith(POTION + ":"))
    assert fixed < potion_line < note < shard_line


def test_with_shop_items_only_the_total_is_not_called_an_estimate() -> None:
    text = stats.report(tally({POTION: -2}).result(), "Results")
    assert "Total arnebia spent on items used: 900\n" in text + "\n"
    assert "estimated" not in text


def test_bought_items_are_not_used_items() -> None:
    """10 bought and 3 drunk: the bag shows +7. Three were used - the ten bought are a purchase."""
    r = tally({POTION: 7}, bought={POTION: 10}).result()
    assert r["used"] == {POTION: 3}
    assert spent(r)[POTION]["n"] == 3 and spent(r)[POTION]["cost"] == 1350
    text = stats.report(r, "Results")
    assert "Bought:" in text and "    10 Large Red Potion" in text
    assert "bought items are not counted here" in text


def test_an_item_without_a_stored_price_is_named_and_never_counted_as_free() -> None:
    r = tally({POTION: -1, NO_PRICE: -6}).result()
    assert spent(r)[NO_PRICE] == {"n": 6, "price": None, "source": None, "cost": None}
    text = stats.report(r, "Results")
    assert "No price stored, not counted: 6 Rice Roll" in text
    assert "Total arnebia spent on items used: 450" in text


def test_a_job_s_report_shows_its_spending_too() -> None:
    r = tally({SHARD: -8, "Level 2 Ruby": 2}, tool="gem_maker").result()
    text = stats.report(r, "Gem Maker")
    assert "Shard prices change at the Shard Market, so these prices are estimated:" in text
    assert "Level 1 Ruby Shard: 8 used x" in text


def test_nothing_used_means_no_spending_section() -> None:
    text = stats.report(tally({"Wolf Fur": 9}).result(), "Results")
    assert "Arnebia spent" not in text


# ================================================================ many runs
def test_runs_add_up_per_item_and_in_all_even_when_a_price_changed() -> None:
    a = tally({POTION: -2, SHARD: -1}).result()
    b = tally({POTION: -3}).result()
    c = tally({SHARD: -2}).result()
    spent(c)[SHARD].update(price=100, cost=200)            # costed when the market paid less
    total = stats.add_up([a, b, c])
    s = spent(total)
    assert s[POTION] == {"n": 5, "price": 450, "source": stats.SHOP, "cost": 2250}
    assert s[SHARD]["n"] == 3 and s[SHARD]["cost"] == shard_price(SHARD) + 200 and s[SHARD]["varied"] is True
    text = stats.report(total, "This week")
    assert "Level 1 Ruby Shard: 3 used x about" in text
    assert "Total arnebia spent on items used: {:,}".format(2250 + shard_price(SHARD) + 200) in text


def test_a_saved_run_keeps_the_prices_it_was_costed_at(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    t = tally({SHARD: -2})
    t.parts["farming"].kills = 1
    t.save()
    monkeypatch.setattr(stats, "_prices", lambda: {SHARD.lower(): 99999})    # the market's price moved since
    stats.forget_prices()
    got = stats.runs("Hero")
    assert spent(got[0])[SHARD]["price"] == shard_price(SHARD)
    stats.forget_prices()


def test_old_records_are_costed_at_the_stored_prices(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    stats.forget_prices()
    old = {"character": "Hero", "run": "Wolves", "kind": "farming", "start": "2026-10-01 10:00:00", "minutes": 60,
           "kills": 5, "deaths": 0, "arnebia": 0, "picked": {}, "used": {POTION: 4}, "moved": {}}
    (tmp_path / "Hero.jsonl").write_text(stdjson.dumps(old) + "\n", encoding="utf-8")
    got = stats.runs("Hero")
    assert spent(got[0])[POTION] == {"n": 4, "price": 450, "source": stats.SHOP, "cost": 1800}
    assert "used" in got[0] and "spent" not in old       # the file itself is not rewritten
    assert stdjson.loads((tmp_path / "Hero.jsonl").read_text(encoding="utf-8").strip()) == old


def test_a_run_written_twice_counts_its_usage_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    t = tally({POTION: -3})
    t.parts["farming"].kills = 2
    t.save()
    t.save()
    line = (tmp_path / "Hero.jsonl").read_text(encoding="utf-8")
    (tmp_path / "Hero.jsonl").write_text(line + line, encoding="utf-8")   # the same line twice, as a copy would
    got = stats.runs("Hero")
    assert len(got) == 1
    assert spent(stats.add_up(got))[POTION]["n"] == 3
