"""A sale's arnebia comes into the bag a moment after its items leave it (seen live: the money of
a shard sale arrived once the sale was over, and the results counted it in the next job). The
sales wait for it, so it is counted where it was earned."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from conftest import as_bot, returning, stand_in, Clock

from unclefarmer import knowledge, stats
from unclefarmer.engine import town, transactions
from unclefarmer.engine.transactions import InventoryTransaction


class Bag:
    """A bag whose arnebia comes in late: pay() queues money that lands `delay` seconds on."""

    def __init__(self, clock: Clock, rows: dict[str, int], delay: float = 0.4) -> None:
        self.clock, self.rows, self.delay = clock, dict(rows), delay
        self.owed: list[tuple[float, int]] = []
        self.reads = 0
        self.lines: list[str] = []
        self.spoken: list[str] = []

    def pay(self, n: int) -> None:
        self.owed.append((self.clock.time() + self.delay, n))

    def bag_rows(self) -> list[dict[str, Any]]:
        self.reads += 1
        now = self.clock.time()
        for due, n in [o for o in self.owed if o[0] <= now]:
            self.rows["Arnebia"] = self.rows.get("Arnebia", 0) + n
            self.owed.remove((due, n))
        return [{"name": k, "count": v} for k, v in self.rows.items()]

    def remove(self, name: str, n: int) -> None:
        self.rows[name] = self.rows.get(name, 0) - n

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def close_dialogs(self) -> None:
        pass


def test_paid_waits_for_late_money_and_for_it_to_stop_coming() -> None:
    clock = Clock()
    b = Bag(clock, {"Shard": 5, "Arnebia": 100})
    t = InventoryTransaction(transactions.counts(as_bot(b)))
    b.remove("Shard", 5)
    b.pay(3000)
    clock.advance(0.0)
    b.owed.append((clock.time() + 0.7, 40))        # a second part of the money, a little later
    after = t.paid(as_bot(b), transactions.counts(as_bot(b)))
    assert after["arnebia"] == 3140


def test_paid_gives_up_after_the_settle_time_when_no_money_comes() -> None:
    clock = Clock()
    b = Bag(clock, {"Shard": 5, "Arnebia": 100})
    t = InventoryTransaction(transactions.counts(as_bot(b)))
    start = clock.time()
    after = t.paid(as_bot(b), transactions.counts(as_bot(b)))
    assert after["arnebia"] == 100
    assert clock.time() - start == pytest.approx(transactions.SETTLE_SECONDS, abs=0.1)


def test_paid_returns_at_once_without_an_arnebia_row() -> None:
    clock = Clock()
    b = Bag(clock, {"Shard": 5})
    t = InventoryTransaction(transactions.counts(as_bot(b)))
    reads = b.reads
    assert t.paid(as_bot(b), {"shard": 0}) == {"shard": 0}
    assert b.reads == reads


def test_shard_sale_counts_money_that_comes_after_the_shards(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    b = Bag(clock, {"Sapphire Shard": 3, "Arnebia": 1000})
    seen: list[int] = []
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({"Sapphire Shard": {}}, {5: [1, 1]})))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Shard Broker")))
    monkeypatch.setattr(town, "market_lists", stand_in(town.market_lists, returning(True)))
    monkeypatch.setattr(town.menus, "can_pick", stand_in(town.menus.can_pick, returning(False)))

    def trade(b: town.Bot, name: str, action: str, n: int, reopen: Callable[[], bool] | None = None) -> int:
        bag.remove(name, n)
        bag.pay(60)
        return n

    def moved(bot: stats.Parts, how: str, item: str, n: int) -> None:
        seen.append(bag.rows["Arnebia"])      # what the results read when the sale is noted

    bag = b
    monkeypatch.setattr(town, "shard_trade", stand_in(town.shard_trade, trade))
    monkeypatch.setattr(stats, "moved", stand_in(stats.moved, moved))
    assert town.sell_shards(as_bot(b), ["Sapphire Shard"]) == 1
    assert seen == [1060]


def test_collection_sale_counts_money_that_comes_after_the_items(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    b = Bag(clock, {"Milkfish": 4, "Arnebia": 0})
    rows: list[tuple[int, str, int]] = [(4, "Milkfish", 100)]
    b.dialog = lambda: {"ok": True, "open": 1}  # type: ignore[attr-defined]
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}  # type: ignore[attr-defined]
    b.p = {}  # type: ignore[attr-defined]
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: [(1, "Buyer", 0, 0)]))

    def pick(b: town.Bot, pattern: str, quantity: int | None = None) -> bool:
        bag.remove("Milkfish", 4)
        bag.pay(400)
        rows.clear()
        return True

    def left(p: dict[str, Any], rows: list[tuple[int, str, int]]) -> list[tuple[str, int]]:
        return [("Milkfish", 4)] if rows else []

    bag = b
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Buyer")))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, pick))
    monkeypatch.setattr(town, "buyer_rows", stand_in(town.buyer_rows, lambda b: list(rows)))
    monkeypatch.setattr(town, "collection_left", stand_in(town.collection_left, left))
    monkeypatch.setattr(stats, "moved", stand_in(stats.moved, returning(None)))
    assert town.sell_collection(as_bot(b), ("fish",)) == 400
