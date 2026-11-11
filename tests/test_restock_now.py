# pyright: reportPrivateUsage=false
"""Explicit supply top-ups through the same script job used by Tools and triggers."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from conftest import as_bot, returning, stand_in
from test_town import TownBot

from unclefarmer import knowledge
from unclefarmer.engine import bagrules, script, town, triggers
from unclefarmer.engine.bot import Bot, Stopped


ITEM = "Large Red Potion"
RULE = {"item": ITEM, "min": 20, "stock": 100, "source": "buy", "on": True}


class SupplyBot(TownBot):
    restocking = False

    def restock_now(self, start: bool = False) -> bool:
        return Bot.restock_now(as_bot(self), start)

    def check(self) -> None:
        if self.stop_event.is_set():
            raise Stopped("stopped", final=True)


@pytest.fixture
def supplies(monkeypatch: pytest.MonkeyPatch) -> tuple[SupplyBot, list[int]]:
    tb = SupplyBot(bag=[{"name": ITEM, "count": 60}, {"name": "Arnebia", "count": 10000}],
                  profile={"restock": [dict(RULE)]})
    requested: list[int] = []
    shop = (1, "Apothecary", 0.0, 0.0)
    monkeypatch.setattr(town, "shops_for", stand_in(town.shops_for, lambda item: [shop]))
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, lambda b, role, candidates=None:
                                                candidates[0] if candidates else None))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: "Apothecary"))
    monkeypatch.setattr(town.menus, "pick", stand_in(town.menus.pick, returning(None)))
    monkeypatch.setattr(town, "_buy_menu", stand_in(town._buy_menu, lambda b, item: True))
    monkeypatch.setattr(knowledge, "unit_price", stand_in(knowledge.unit_price, lambda name: 10))

    def purchase(b: Bot, pattern: str, quantity: int | None = None) -> bool:
        assert quantity is not None
        requested.append(quantity)
        tb.add(ITEM, quantity)
        tb.remove("Arnebia", quantity * 10)
        return True

    monkeypatch.setattr(town, "pick", stand_in(town.pick, purchase))
    return tb, requested


@pytest.mark.parametrize("have,missing", [(0, 100), (25, 75), (60, 40), (95, 5), (100, 0), (120, 0)])
def test_manual_job_buys_only_the_missing_quantity(
        supplies: tuple[SupplyBot, list[int]], have: int, missing: int) -> None:
    b, requested = supplies
    b.bag[0]["count"] = have
    script.Runner(as_bot(b), "restock\n").run()
    assert requested == ([missing] if missing else [])
    assert town.counts(as_bot(b))[ITEM.lower()] == max(have, 100)
    assert not b.restocking
    assert any("nothing to restock" in line if missing == 0 else "configured targets" in line for line in b.spoken)


@pytest.mark.parametrize("stage", ["before", "after"])
def test_trigger_bypasses_threshold_and_uses_current_inventory(
        supplies: tuple[SupplyBot, list[int]], stage: str) -> None:
    b, requested = supplies
    b.p[triggers.setting("restock", stage)] = True
    assert town.needs(as_bot(b), [RULE]) == []
    assert triggers.run_stage(as_bot(b), stage, b.log, b.stop_event.is_set) == [("restock", True)]
    assert requested == [40]
    assert town.counts(as_bot(b))[ITEM.lower()] == 100


def test_automatic_restock_keeps_its_threshold(supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    assert b.restock_now() is False
    assert requested == []
    b.bag[0]["count"] = 20
    assert b.restock_now() is True
    assert requested == [80]


def test_recounts_multiple_stacks_and_inventory_received_on_the_way(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.bag = [{"name": ITEM, "count": 25}, {"name": ITEM.upper(), "count": 35},
             {"name": "Arnebia", "count": 10000}]
    tb = b

    def arrive(b: Bot, role: str | None, name: str | None = None,
               candidates: Sequence[town.Npc] | None = None) -> str:
        tb.add(ITEM, 15)
        return "Apothecary"

    monkeypatch.setattr(town, "visit", stand_in(town.visit, arrive))
    town.restock_now(as_bot(b))
    assert requested == [25]
    assert town.counts(as_bot(b))[ITEM.lower()] == 100


def test_partial_storehouse_withdrawal_buys_only_the_remaining_shortfall(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.p["restock"] = [dict(RULE, source="both")]
    withdrawals: list[int] = []
    tb = b

    def withdraw(b: Bot, item: str, n: int, *, stock: int | None = None) -> int:
        withdrawals.append(n)
        tb.add(item, 10)
        return 10

    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, withdraw))
    town.restock_now(as_bot(b))
    assert withdrawals == [40] and requested == [30]
    assert town.counts(as_bot(b))[ITEM.lower()] == 100


def test_storehouse_recounts_on_arrival_and_retries_stale_missing_stock(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    tb = b
    b.p["restock"] = [dict(RULE, source="storage")]
    b.__dict__["storehouse_lacks"] = {ITEM.lower()}
    b._dialog = {"ok": True, "open": 1,
                 "top": {"rows": [[{"options": ["1; 500 Large Red Potion"]}]], "row": 0, "col": 0}}
    monkeypatch.setattr(knowledge, "service_npcs",
                        stand_in(knowledge.service_npcs, lambda role: [(1, "Storehouse", 0.0, 0.0)]))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))

    def arrive(b: Bot, role: str | None, name: str | None = None,
               candidates: Sequence[town.Npc] | None = None) -> str:
        tb.add(ITEM, 15)
        return "Storehouse"

    def collect(b: Bot, pattern: str, quantity: int | None = None) -> bool:
        assert quantity is not None
        requested.append(quantity)
        tb.add(ITEM, quantity)
        return True

    monkeypatch.setattr(town, "visit", stand_in(town.visit, arrive))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, collect))
    town.restock_now(as_bot(b))
    assert requested == [25] and town.counts(as_bot(b))[ITEM.lower()] == 100
    assert b.__dict__["storehouse_lacks"] == set()


@pytest.mark.parametrize("delay", [1.2, 4.0])
@pytest.mark.parametrize("item,have,target", [
    ("Pet Large Red Potion", 117, 150),
    ("Pet Large Blue Potion", 124, 150),
    ("Pet Rice Roll", 20, 30),
])
def test_delayed_storehouse_list_is_loaded_before_checking_stock(
        monkeypatch: pytest.MonkeyPatch, item: str, have: int, target: int, delay: float) -> None:
    b = SupplyBot(bag=[{"name": item, "count": have}],
                  profile={"restock": [dict(RULE, item=item, stock=target, source="storage")]})
    root: dict[str, Any] = {"sig": 1, "rows": [[{"options": ["item storage"]}]], "row": 0, "col": 0}
    items: dict[str, Any] = {"sig": 2, "rows": [[{"options": ["1; 1500 " + item]}]], "row": 0, "col": 0}
    ready_at = b.clock.time() + delay
    requested: list[int] = []

    def dialog() -> dict[str, Any]:
        top = items if b.clock.time() >= ready_at else root
        return {"ok": True, "open": 1, "top": top}

    def wait(before: dict[str, Any] | None, seconds: float = 0.4, depth: int = 1) -> dict[str, Any]:
        return Bot.wait_menu_change(as_bot(b), before, seconds, depth)

    def collect(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        assert quantity is not None
        requested.append(quantity)
        b.add(item, quantity)
        return True

    monkeypatch.setattr(knowledge, "service_npcs",
                        stand_in(knowledge.service_npcs, lambda role: [(1, "Storehouse", 0.0, 0.0)]))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: "Storehouse"))
    monkeypatch.setattr(b, "dialog", dialog)
    monkeypatch.setattr(b, "wait_menu_change", wait)
    monkeypatch.setattr(town, "pick", collect)
    town.restock_now(as_bot(b))
    assert requested == ([target - have] if delay < 3.0 else [])
    assert town.counts(as_bot(b))[item.lower()] == (target if delay < 3.0 else have)
    assert item.lower() not in b.__dict__.get("storehouse_lacks", set[str]())


def test_insufficient_currency_stops_with_failure_feedback(supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.bag[1]["count"] = 0
    assert town.restock_now(as_bot(b)) is False
    assert requested == []
    assert any("no arnebia" in line for line in b.spoken)


def test_partial_currency_purchase_reports_remaining_shortfall(supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.bag[1]["count"] = 70
    assert town.restock_now(as_bot(b)) is True
    assert requested == [7]
    assert town.counts(as_bot(b))[ITEM.lower()] == 67
    assert any("still short after restocking" in line for line in b.spoken)


def test_unavailable_shop_item_makes_no_purchase(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    monkeypatch.setattr(town, "_buy_menu", stand_in(town._buy_menu, lambda b, item: False))
    town.restock_now(as_bot(b))
    assert requested == []
    assert any("still short after restocking" in line for line in b.spoken)


def test_shop_partial_stock_is_reconciled_without_duplicate_purchase(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    tb = b

    def partial(b: Bot, pattern: str, quantity: int | None = None) -> bool:
        assert quantity is not None
        requested.append(quantity)
        tb.add(ITEM, 12)
        return True

    monkeypatch.setattr(town, "pick", stand_in(town.pick, partial))
    town.restock_now(as_bot(b))
    assert requested == [40]
    assert town.counts(as_bot(b))[ITEM.lower()] == 72
    assert any("still short after restocking" in line for line in b.spoken)


@pytest.mark.parametrize("load,maximum,weight,expected", [(90, 100, 2, 5), (100, 100, None, 0)])
def test_capacity_caps_explicit_purchases(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]],
        load: int, maximum: int, weight: int | None, expected: int) -> None:
    b, requested = supplies
    monkeypatch.setattr(b, "me", lambda: {"sheet": {"load": load, "maxload": maximum}})
    monkeypatch.setattr(bagrules, "weights", lambda: {} if weight is None else {ITEM.lower(): weight})
    town.restock_now(as_bot(b))
    assert requested == ([expected] if expected else [])
    assert town.counts(as_bot(b))[ITEM.lower()] == 60 + expected


def test_reentrant_and_repeated_requests_do_not_double_buy(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    purchase = town.pick

    def nested(b: Bot, pattern: str, quantity: int | None = None) -> bool:
        assert town.restock_now(b) is False
        return purchase(b, pattern, quantity)

    monkeypatch.setattr(town, "pick", stand_in(town.pick, nested))
    town.restock_now(as_bot(b))
    town.restock_now(as_bot(b))
    assert requested == [40] and not b.restocking


def test_manual_retry_bypasses_previous_failed_trip_backoff(supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.__dict__["restock_failed"] = {ITEM.lower(): (b.clock.time(), 10000)}
    town.restock_now(as_bot(b))
    assert requested == [40]


def test_duplicate_rules_recount_before_each_purchase(supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.p["restock"] = [dict(RULE), dict(RULE)]
    town.restock_now(as_bot(b))
    assert requested == [40] and town.counts(as_bot(b))[ITEM.lower()] == 100


def test_disabled_and_notify_only_rules_do_not_purchase(supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    b.p["restock"] = [dict(RULE, on=False), dict(RULE, source="tell")]
    assert town.restock_now(as_bot(b)) is False
    assert requested == []


def test_missing_shop_reports_failure_and_releases_guard(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    monkeypatch.setattr(town, "shops_for", stand_in(town.shops_for, lambda item: []))
    town.restock_now(as_bot(b))
    assert requested == [] and not b.restocking
    assert any("couldn't restock" in line for line in b.lines)


def test_explicit_purchase_waits_for_the_bag_to_confirm(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]]) -> None:
    b, requested = supplies
    tb = b
    available_at: list[float] = []
    original_rows = tb.bag_rows

    def delayed(b: Bot, pattern: str, quantity: int | None = None) -> bool:
        assert quantity is not None
        requested.append(quantity)
        available_at.append(tb.clock.time() + 0.25)
        return True

    def rows() -> list[dict[str, Any]]:
        if available_at and tb.clock.time() >= available_at[0]:
            tb.add(ITEM, requested[0])
            available_at.clear()
        return original_rows()

    monkeypatch.setattr(town, "pick", stand_in(town.pick, delayed))
    monkeypatch.setattr(b, "bag_rows", rows)
    town.restock_now(as_bot(b))
    town.restock_now(as_bot(b))
    assert requested == [40] and town.counts(as_bot(b))[ITEM.lower()] == 100


@pytest.mark.parametrize("final", [False, True])
def test_failed_visit_clears_guard_and_preserves_final_stops(
        monkeypatch: pytest.MonkeyPatch, supplies: tuple[SupplyBot, list[int]], final: bool) -> None:
    b, requested = supplies

    def unavailable(*args: Any, **kwargs: Any) -> str:
        raise Stopped("shop unavailable", final=final)

    monkeypatch.setattr(town, "visit", stand_in(town.visit, unavailable))
    if final:
        with pytest.raises(Stopped):
            town.restock_now(as_bot(b))
    else:
        town.restock_now(as_bot(b))
        assert any("couldn't restock" in line for line in b.lines)
    assert requested == [] and not b.restocking
    assert b.__dict__.get("restock_town") is None
