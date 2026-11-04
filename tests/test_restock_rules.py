# pyright: reportPrivateUsage=false
"""Restocking: when a trip is due, where it goes, what it counts, and what it gives up on.

  - "Refill when fewer than" (min) triggers, "fill up to" (stock) is only how far a trip fills:
    with 30 / 200, 199 and 100 make no trip, 30 and 29 do; any one rule down to its min is enough.
  - A trip does its errands in one town - the one we stand in if it has them - and never goes on to
    another town for a shop (the nearest shop of any town used to be tried, three in turn).
  - A trip that can't get anything (no arnebia, the storehouse out of it) isn't made again every
    30 s; it says why once, and tries again after RESTOCK_RETRY or once there is more arnebia.
  - The storehouse's count is taken at the storehouse: a potion drunk on the way there is not
    stock it failed to hand over (that sent the trip on to a shop).
  - After the bag trip, the way back says so (it said "died or moved?"), and the restock rules are
    looked at before the town is left."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from conftest import as_bot, stand_in

from test_town import TownBot
from unclefarmer import knowledge
from unclefarmer.engine import town
from unclefarmer.engine.bot import Bot

RULE = {"item": "Large Red Potion", "min": 30, "stock": 200, "source": "both", "on": True}


def bot(potions: int, arnebia: int | None = 50000, **kw: Any) -> TownBot:
    bag = [{"name": "Large Red Potion", "count": potions}]
    if arnebia is not None:
        bag.append({"name": "Arnebia", "count": arnebia})
    return TownBot(bag=bag, **kw)


@pytest.mark.parametrize("have,trip", [(199, False), (100, False), (31, False), (30, True), (29, True), (0, True)])
def test_the_min_triggers_and_the_stock_does_not(have: int, trip: bool) -> None:
    assert bool(town.needs(as_bot(bot(have)), [RULE])) is trip


def test_one_rule_at_its_min_is_enough_and_the_trip_fills_the_others_too() -> None:
    b = TownBot(bag=[{"name": "Large Red Potion", "count": 150}, {"name": "Large Blue Potion", "count": 12}])
    blue = dict(RULE, item="Large Blue Potion", min=20, stock=100)
    assert [r["item"] for r, _h, _w in town.needs(as_bot(b), [RULE, blue])] == ["Large Blue Potion"]
    # once going, everything under its stock is topped up (start=True is "a trip is happening")
    assert {r["item"] for r, _h, _w in town.needs(as_bot(b), [RULE, blue], start=True)} == {"Large Red Potion",
                                                                                         "Large Blue Potion"}


def test_a_rule_whose_min_is_its_stock_makes_no_endless_trips() -> None:
    assert town.needs(as_bot(bot(30)), [dict(RULE, stock=30)]) == []


def shops_everywhere(monkeypatch: pytest.MonkeyPatch, shops: list[town.Npc], houses: list[town.Npc]) -> None:
    monkeypatch.setattr(town, "shops_for", stand_in(town.shops_for, lambda item: list(shops)))
    real = knowledge.service_npcs
    monkeypatch.setattr(knowledge, "service_npcs",
                        stand_in(knowledge.service_npcs, lambda role: list(houses) if role == "storehouse" else real(role)))


def test_the_town_we_stand_in_is_used_when_it_has_what_is_needed(monkeypatch: pytest.MonkeyPatch) -> None:
    shops_everywhere(monkeypatch, [(1041, "Apothecary", 180, 220), (1121, "Magnus", 45, 141)],
                     [(1041, "Storehouse", 204, 196), (1121, "Storehouse", 68, 168)])
    b = bot(10)
    b.map = 1121
    assert town.restock_town(as_bot(b), town.needs(as_bot(b), [RULE])) == 1121


def test_buy_stays_in_the_trips_town(monkeypatch: pytest.MonkeyPatch) -> None:
    shops_everywhere(monkeypatch, [(1041, "Apothecary", 180, 220), (1121, "Magnus", 45, 141)], [])
    b = bot(10)
    b.__dict__["restock_town"] = 1121
    visited: list[list[town.Npc]] = []
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None:
                                                visited.append(list(candidates or [])) or "x"))
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, lambda b, role, candidates=None:
                                                  (candidates or [None])[0]))
    monkeypatch.setattr(town, "_buy_menu", stand_in(town._buy_menu, lambda b, item: False))
    assert town.buy(as_bot(b), "Large Red Potion", 50) == 0
    assert visited and all(npc[0] == 1121 for c in visited for npc in c)      # never Giant Rock


def test_a_town_with_no_shop_for_it_buys_nothing_rather_than_travel_on(monkeypatch: pytest.MonkeyPatch) -> None:
    shops_everywhere(monkeypatch, [(1041, "Apothecary", 180, 220)], [])
    b = bot(10)
    b.__dict__["restock_town"] = 1121
    assert town.buy(as_bot(b), "Large Red Potion", 50) == 0
    assert any("not going to another town" in line for line in b.lines)
    assert b.travels == []


def test_withdraw_counts_at_the_storehouse_not_before_the_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    shops_everywhere(monkeypatch, [], [(1041, "Storehouse", 204, 196)])
    tb = bot(10)

    def visit(b: Bot, role: str | None, name: str | None = None, candidates: Sequence[town.Npc] | None = None) -> str:
        tb.remove("Large Red Potion", 3)           # drunk on the way there
        return "Storehouse"
    monkeypatch.setattr(town, "visit", stand_in(town.visit, visit))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))
    tb.__dict__["_dialog"] = {"ok": True, "open": 1,
                              "top": {"rows": [[{"options": ["1; 500 Large Red Potion"]}]], "row": 0, "col": 0}}

    def pick(b: Bot, pattern: str, quantity: int | None = None) -> bool:
        tb.add("Large Red Potion", quantity or 0)
        return True
    monkeypatch.setattr(town, "pick", stand_in(town.pick, pick))
    assert town.withdraw(as_bot(tb), "Large Red Potion", 190) == 190     # not 187


def test_no_arnebia_and_no_storage_makes_no_trip_and_says_why_once(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bot(10, arnebia=0)
    rule = dict(RULE, source="buy")
    calls: list[str] = []
    monkeypatch.setattr(town, "buy", stand_in(town.buy, lambda b, item, n: calls.append("buy") or 0))
    assert town.restock(as_bot(b), [rule]) is False
    assert town.restock(as_bot(b), [rule]) is False
    assert calls == []
    said = [line for line in b.spoken if "not restocking Large Red Potion" in line]
    assert len(said) == 1 and "no arnebia" in said[0]


def test_a_trip_that_got_nothing_is_not_repeated_until_something_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bot(10, arnebia=100)
    calls: list[str] = []
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, lambda b, item, n: calls.append("withdraw") or 0))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, lambda b, item, n: calls.append("buy") or 0))
    monkeypatch.setattr(town, "restock_town", stand_in(town.restock_town, lambda b, todo: 1041))
    assert town.restock(as_bot(b), [RULE]) is True                 # the trip: nothing to be had
    assert calls == ["withdraw", "buy"]
    for _second in range(5):
        b.clock.advance(30)
        assert town.restock(as_bot(b), [RULE]) is False             # not every 30 s again
    assert calls == ["withdraw", "buy"]
    b.add("Arnebia", 5000)                                          # money came in: worth a try
    assert town.restock(as_bot(b), [RULE]) is True
    b.clock.advance(town.RESTOCK_RETRY + 1)
    assert town.restock(as_bot(b), [RULE]) is True                  # and in any case after a while


def test_a_storehouse_out_of_it_is_remembered_until_we_store_some(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bot(10, arnebia=None)
    b.__dict__["storehouse_lacks"] = {"large red potion"}
    assert town.possible(as_bot(b), dict(RULE, source="storage"), []) is False
    assert town.possible(as_bot(b), dict(RULE, source="both"), []) is True     # (no arnebia row: may buy)


def test_the_farm_loop_s_way_back_after_the_bag_trip_says_so_and_restocks_first() -> None:
    from test_farming import Farm        # the farming stand-in
    b = Farm()
    b.away_reason = "the bag trip"
    order: list[str] = []
    b.restock_now = lambda start=False: order.append("restock") or False
    b.travel = lambda dst, cell=None: order.append("travel")
    assert b._off_map(5121) is False
    assert order == ["restock", "travel"]
    assert any("back to" in line and "after the bag trip" in line for line in b.lines)
    assert not any("died or moved" in line for line in b.lines)
    b.away_reason = None
    order.clear()
    b._off_map(5121)
    assert order == ["travel"] and any("died or moved" in line for line in b.lines)
