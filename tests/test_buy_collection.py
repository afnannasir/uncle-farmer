# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Tools, Buy collection items (engine/buycollection.py): the market's collection lots on the list,
cheapest first, never above the most a unit, up to the day's count kept per character for the game's
day; the game's refusals (the daily cap, gold, overweight, a lot that can't be had); Check only; selling
afterwards and to make room."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import Clock, as_bot, stand_in

from unclefarmer import paths, settings
from unclefarmer.engine import activities, buycollection as bc, market, script, town
from unclefarmer.engine.botparts.base import Stopped


def lot(i: int, name: str, qty: int, price: int, opens_in: int = 0) -> market.Lot:
    return market.Lot(id=i, cat=bc.COLLECTIONS, name=name, qty=qty, price=price, opens_in=opens_in)


class Market:
    """The market's collection pages, cheapest first, and what the game answers each purchase."""

    def __init__(self, pages: list[list[market.Lot]], answers: dict[int, list[tuple[str, str]]] | None = None) -> None:
        self.pages = pages
        self.answers = answers or {}
        self.bought: list[tuple[int, int]] = []
        self.sold_kinds: list[Any] = []

    def goto(self, b: Any, cat: int, order: int, page: int) -> market.Page | None:
        assert cat == bc.COLLECTIONS and order == market.PRICE
        if not 1 <= page <= len(self.pages):
            return None
        lots = [x for x in self.pages[page - 1] if x.qty > 0]
        return market.Page(cat, order, page, len(self.pages), lots, 0, {}, lot_at={x.id: (0, 0) for x in lots})

    def buy(self, b: Any, pg: market.Page, x: market.Lot, want: int) -> tuple[str, str]:
        script_ = self.answers.get(x.id) or []
        outcome, heard = script_.pop(0) if script_ else (bc.BOUGHT, "successfully purchased")
        if outcome == bc.BOUGHT:
            self.bought.append((x.id, want))
            for page in self.pages:
                for y in page:
                    if y.id == x.id:
                        y.qty -= want
            b.bag[x.name.lower()] = b.bag.get(x.name.lower(), 0) + want
        return outcome, heard


class Bot:
    def __init__(self) -> None:
        self.clock = Clock()
        self.p: dict[str, Any] = {}
        self.profile_key = "Hero"
        self.lines: list[str] = []
        self.bag: dict[str, int] = {}
        self.closed = 0
        self.tally = None

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def check(self) -> None:
        pass

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def close_dialogs(self) -> None:
        self.closed += 1

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": n, "count": c} for n, c in self.bag.items()]


@pytest.fixture
def world(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(bc.daily, "game_day", stand_in(bc.daily.game_day, lambda now=None: "2026-11-26"))

    def make(pages: list[list[market.Lot]], answers: dict[int, list[tuple[str, str]]] | None = None,
             sold: int = 1) -> tuple[Bot, Market, list[str]]:
        m = Market(pages, answers)
        monkeypatch.setattr(bc.market, "goto", stand_in(market.goto, m.goto))
        monkeypatch.setattr(bc, "_buy_lot", m.buy)
        sales: list[str] = []

        def sell(b: Any, kinds: Any = None, *, require_empty: bool = False) -> int:
            sales.append("sold")
            m.sold_kinds.append(kinds)
            b.bag.clear()
            return sold
        monkeypatch.setattr(bc.town, "sell_collection", sell)
        return Bot(), m, sales
    return make


def cfg(**kw: Any) -> dict[str, Any]:
    return bc.config({"buy_collection": dict({"items": ["Milkfish", "Red Shrimp"], "max_price": 200}, **kw)})


# ------------------------------------------------------------------ the settings
def test_the_settings_are_checked() -> None:
    c = bc.config({"buy_collection": {"items": [" Milkfish ", "", 7], "daily": 9999, "max_price": 0,
                                      "when_heavy": "dance"}})
    assert c["items"] == ["Milkfish", "7"] and c["daily"] == bc.DAILY_MOST
    assert c["max_price"] == bc.DEFAULTS["max_price"] and c["when_heavy"] == "stop"
    assert bc.config({})["items"] == [] and bc.config({"buy_collection": "junk"})["daily"] == bc.DAILY_MOST


def test_a_name_is_a_whole_name() -> None:
    assert bc.wanted(lot(1, "Milkfish", 1, 1), ["milkfish"])
    assert bc.wanted(lot(1, "Flying  Fish", 1, 1), ["flying fish"])
    assert not bc.wanted(lot(1, "Flying Fish", 1, 1), ["Fish"])          # the market's own search would
    assert not bc.wanted(market.Lot(id=1, cat=0, name="Milkfish", qty=1, price=1, equip=True), ["Milkfish"])


def test_the_known_items_are_the_buyers_goods() -> None:
    known = bc.known_items()
    assert "Milkfish" in known and "Red Shrimp" in known
    assert known == sorted(known, key=str.lower)


# ------------------------------------------------------------------ buying
def test_it_buys_the_cheapest_lots_on_the_list_and_stops_at_the_most_a_unit(world: Any) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50), lot(2, "Bigmouth Fish", 99, 60),
                           lot(3, "Red Shrimp", 5, 80), lot(4, "Milkfish", 30, 250)]])
    assert bc.run(as_bot(b), cfg()) == 15
    assert m.bought == [(1, 10), (3, 5)]                     # never the fish off the list, nor the dear one
    assert bc.bought_today("Hero") == 15
    assert any("more than the most you set (200)" in line for line in b.lines)
    assert any("bought 15 items for 900 gold coins; 485 left to buy today" in line for line in b.lines)


def test_the_day_count_is_kept_and_starts_again_with_the_games_day(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 400, 10), lot(2, "Milkfish", 400, 11)]])
    assert bc.run(as_bot(b), cfg(daily=300)) == 300
    assert m.bought == [(1, 300)]
    assert bc.run(as_bot(b), cfg(daily=300)) == 0
    assert any("bought already" in line for line in b.lines)
    monkeypatch.setattr(bc.daily, "game_day", stand_in(bc.daily.game_day, lambda now=None: "2026-11-27"))
    assert bc.bought_today("Hero") == 0
    assert bc.run(as_bot(b), cfg(daily=300)) == 300


def test_lots_in_their_first_hour_are_passed_over(world: Any) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50, opens_in=1800), lot(2, "Milkfish", 3, 60)]])
    bc.run(as_bot(b), cfg())
    assert m.bought == [(2, 3)]


def test_it_reads_every_page(world: Any) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 2, 10)], [lot(2, "Bigmouth Fish", 5, 20)], [lot(3, "Red Shrimp", 4, 30)]])
    assert bc.run(as_bot(b), cfg()) == 6
    assert m.bought == [(1, 2), (3, 4)]
    assert any("no more lots on the list" in line for line in b.lines)


def test_what_arrives_is_counted_from_the_bag(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50)]])
    real = m.buy

    def short(bot: Any, pg: market.Page, x: market.Lot, want: int) -> tuple[str, str]:
        got = real(bot, pg, x, want)
        bot.bag["milkfish"] -= 4                              # only 6 came into the bag
        return got
    monkeypatch.setattr(bc, "_buy_lot", short)
    assert bc.run(as_bot(b), cfg()) == 6 and bc.bought_today("Hero") == 6


# ------------------------------------------------------------------ the game's refusals
def test_the_daily_cap_ends_the_day(world: Any) -> None:
    b, _m, _sales = world([[lot(1, "Milkfish", 10, 50)]],
                          {1: [(bc.CAP, "Exceeded the maximum number of collected items")]})
    assert bc.run(as_bot(b), cfg()) == 0
    assert bc.bought_today("Hero") == bc.DAILY_MOST
    assert any("all bought" in line for line in b.lines)


def test_no_gold_ends_the_run(world: Any) -> None:
    b, _m, _sales = world([[lot(1, "Milkfish", 10, 50), lot(2, "Milkfish", 1, 51)]],
                          {1: [(bc.GOLD, "Not enough gold coins")]})
    assert bc.run(as_bot(b), cfg()) == 0
    assert any("not enough gold coins for 500" in line for line in b.lines)


def test_a_lot_that_cant_be_had_is_passed_over(world: Any) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50), lot(2, "Milkfish", 2, 51)]],
                         {1: [(bc.SKIP, "Item does not exist")]})
    assert bc.run(as_bot(b), cfg()) == 2
    assert m.bought == [(2, 2)]
    assert any("not bought: Milkfish (Item does not exist)" in line for line in b.lines)


def test_too_many_refusals_in_a_row_stop_it(world: Any) -> None:
    lots = [lot(i, "Milkfish", 1, 10 + i) for i in range(1, 15)]
    b, m, _sales = world([lots], {i: [(bc.SKIP, "Item does not exist")] for i in range(1, 15)})
    assert bc.run(as_bot(b), cfg()) == 0
    assert m.bought == [] and any("10 lots in a row" in line for line in b.lines)


def test_silence_with_the_lot_in_the_bag_counts_as_bought(world: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50)]])
    real = m.buy

    def quiet(bot: Any, pg: market.Page, x: market.Lot, want: int) -> tuple[str, str]:
        real(bot, pg, x, want)                                # bought: the bag has them...
        return bc.LOST, "the game said nothing"               # ...but no word came
    monkeypatch.setattr(bc, "_buy_lot", quiet)
    assert bc.run(as_bot(b), cfg()) == 10 and bc.bought_today("Hero") == 10


def test_the_sale_goes_to_the_buyers_of_the_listed_items(world: Any) -> None:
    assert bc.sale_kinds(["Milkfish"]) == ("fish",)
    assert bc.sale_kinds(["Milkfish", "Iron Ore", "Odd Pearl"]) == ("fish", "ore")   # a fisher buying ore sells it
    assert bc.sale_kinds(["Odd Pearl"]) == tuple(town.COLLECTION_BUYERS)     # none known: every buyer
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50)]])
    bc.run(as_bot(b), cfg(items=["Milkfish"], sell_after=True))
    assert m.sold_kinds == [("fish",)]                        # not the character's own gathering kind only


def test_silence_from_the_game_ends_the_run(world: Any) -> None:
    b, _m, _sales = world([[lot(1, "Milkfish", 10, 50)]], {1: [(bc.LOST, "the game said nothing")]})
    assert bc.run(as_bot(b), cfg()) == 0
    assert any("look in the bag" in line for line in b.lines)


def test_too_heavy_stops_unless_told_to_sell(world: Any) -> None:
    b, _m, sales = world([[lot(1, "Milkfish", 10, 50)]], {1: [(bc.TOO_HEAVY, "overweight")]})
    assert bc.run(as_bot(b), cfg()) == 0 and sales == []
    assert any("too heavy for more" in line for line in b.lines)
    b, m, sales = world([[lot(1, "Milkfish", 10, 50)]], {1: [(bc.TOO_HEAVY, "overweight")]})
    assert bc.run(as_bot(b), cfg(when_heavy="sell")) == 10
    assert sales == ["sold"] and m.bought == [(1, 10)]


def test_too_heavy_with_buyers_that_take_nothing_stops(world: Any) -> None:
    b, _m, sales = world([[lot(1, "Milkfish", 10, 50)]], {1: [(bc.TOO_HEAVY, "overweight")]}, sold=0)
    assert bc.run(as_bot(b), cfg(when_heavy="sell")) == 0
    assert sales == ["sold"] and any("took nothing" in line for line in b.lines)


# ------------------------------------------------------------------ check, selling, setting up
def test_check_only_buys_nothing_and_says_what_it_would(world: Any) -> None:
    b, m, _sales = world([[lot(1, "Milkfish", 10, 50), lot(2, "Red Shrimp", 4, 60)]])
    assert bc.run(as_bot(b), cfg(), check=True) == 14
    assert m.bought == [] and bc.bought_today("Hero") == 0
    assert "would buy 10 Milkfish for 500 gold coins" in b.lines
    assert any("would buy 14 items for 740 gold coins" in line for line in b.lines)


def test_it_sells_afterwards_when_asked_and_bought_something(world: Any) -> None:
    b, _m, sales = world([[lot(1, "Milkfish", 10, 50)]])
    bc.run(as_bot(b), cfg(sell_after=True))
    assert sales == ["sold"]
    b, _m, sales = world([[lot(1, "Milkfish", 10, 500)]])
    bc.run(as_bot(b), cfg(sell_after=True))                  # nothing bought: nothing to sell
    assert sales == []


def test_nothing_ticked_says_to_set_it_up(world: Any) -> None:
    b, _m, _sales = world([[]])
    with pytest.raises(Stopped, match="set it up first"):
        bc.run(as_bot(b), bc.config({}))


def test_the_day_file_survives_a_broken_or_old_one(world: Any, tmp_path: Path) -> None:
    (tmp_path / bc.BOUGHT_FILE).write_text("not json", encoding="utf-8")
    assert bc.bought_today("Hero") == 0
    (tmp_path / bc.BOUGHT_FILE).write_text(json.dumps({"day": "2026-11-25", "bought": {"Hero": 400}}),
                                           encoding="utf-8")
    assert bc.bought_today("Hero") == 0
    bc.set_bought_today("Hero", 999)
    assert bc.bought_today("Hero") == bc.DAILY_MOST


def test_it_is_a_tool_a_trigger_step_and_a_script_command() -> None:
    a = activities.get("buy_collection")
    assert a.checks and not a.direct and not a.farms and a.listed
    assert script.parse("buy_collection check")[0][0].opts.get("check") is True


def test_the_bots_own_files_are_no_characters(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    for name in ("_game_restarts", "_interrupted_runs", bc.BOUGHT_FILE[:-5]):
        (tmp_path / (name + ".json")).write_text("{}", encoding="utf-8")
    (tmp_path / "_Ace.json").write_text(json.dumps({"attack_keys": ["1"]}), encoding="utf-8")   # a name with a sign first
    (tmp_path / "Hero.json").write_text("{}", encoding="utf-8")
    assert settings.profiles() == ["_Ace", "Hero"]


def test_town_counts_reads_the_bag_this_way() -> None:
    b = Bot()
    b.bag = {"milkfish": 3}
    assert town.counts(as_bot(b)) == {"milkfish": 3}
