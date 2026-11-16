# pyright: reportPrivateUsage=false
"""Restocking must use every available storage stack before spending money."""
from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot, returning
from test_town import TownBot
from unclefarmer.engine import town


ITEM = "Large Red Potion"


class StorageBot(TownBot):
    """Separate normal/restricted UUIDs, with the menu rebuilt after each visit."""

    def __init__(self, amounts: tuple[int, int], restricted_first: bool = False, delay: float = 0) -> None:
        super().__init__()
        self.storage = list(amounts)
        self.restricted_first = restricted_first
        self.selected: list[int] = []
        self.purchases: list[int] = []
        self.visits = 0
        self.delay = delay
        self.delivery: tuple[float, int] | None = None

    def open_storage(self, *args: Any, **kwargs: Any) -> bool:
        self.visits += 1
        rows: list[list[dict[str, Any]]] = []
        for uid, amount in enumerate(self.storage):
            if amount <= 0:
                continue
            label = f"{len(rows) + 1}; {amount} {ITEM}"
            if uid == (0 if self.restricted_first else 1):
                label += "|||; restricted from trading"
            rows.append([{"options": [label], "cmd": [1, uid, 0]}])
        self._dialog = {"open": 1, "top": {"rows": rows}}
        return True

    def choose(self, pattern: str) -> dict[str, Any] | None:
        entry = super().choose(pattern)
        if entry is not None:
            self.selected.append(entry["cmd"][1])
        return entry

    def number(self, b: Any, n: int) -> bool:
        uid = self.selected[-1]
        amount = min(n, self.storage[uid])
        self.storage[uid] -= amount
        if self.delay:
            self.delivery = (self.clock.time() + self.delay, amount)
        else:
            self.add(ITEM, amount)
        return True

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        if self.delivery is not None and self.clock.time() >= self.delivery[0]:
            self.add(ITEM, self.delivery[1])
            self.delivery = None

    def buy(self, b: Any, item: str, n: int, *, stock: int | None = None) -> int:
        self.purchases.append(n)
        self.add(item, n)
        return n


def prepare(monkeypatch: pytest.MonkeyPatch, b: StorageBot) -> None:
    monkeypatch.setattr(town, "visit", returning("Storehouse"))
    monkeypatch.setattr(town, "open_section", b.open_storage)
    monkeypatch.setattr(town, "type_number", b.number)
    monkeypatch.setattr(town, "buy", b.buy)


@pytest.mark.parametrize("restricted_first", [False, True])
@pytest.mark.parametrize("stock", [None, 200])
@pytest.mark.parametrize("source", ["both", "storage"])
def test_split_storage_fills_from_both_variants_without_buying(
    monkeypatch: pytest.MonkeyPatch, restricted_first: bool, stock: int | None, source: str,
) -> None:
    b = StorageBot((10, 200), restricted_first)
    prepare(monkeypatch, b)
    assert town._fill(as_bot(b), {"item": ITEM, "source": source}, 200, stock=stock) == 200
    assert b.selected == [0, 1]
    assert b.storage == [0, 10]
    assert b.purchases == []
    assert town.counts(as_bot(b))[ITEM.lower()] == 200


@pytest.mark.parametrize("source,received,purchases", [("both", 200, [170]), ("storage", 30, [])])
@pytest.mark.parametrize("stock", [None, 200])
def test_buys_only_shortfall_after_all_storage_stacks_are_empty(
    monkeypatch: pytest.MonkeyPatch, source: str, received: int, purchases: list[int], stock: int | None,
) -> None:
    b = StorageBot((10, 20))
    prepare(monkeypatch, b)
    assert town._fill(as_bot(b), {"item": ITEM, "source": source}, 200, stock=stock) == received
    assert b.selected == [0, 1]
    assert b.storage == [0, 0]
    assert b.purchases == purchases


def test_full_first_stack_does_not_open_storage_again(monkeypatch: pytest.MonkeyPatch) -> None:
    b = StorageBot((200, 200))
    prepare(monkeypatch, b)
    assert town._fill(as_bot(b), {"item": ITEM, "source": "both"}, 200, stock=200) == 200
    assert b.visits == 1 and b.selected == [0]
    assert b.storage == [0, 200] and b.purchases == []


def test_empty_storage_falls_back_to_buying_once(monkeypatch: pytest.MonkeyPatch) -> None:
    b = StorageBot((0, 0))
    prepare(monkeypatch, b)
    assert town._fill(as_bot(b), {"item": ITEM, "source": "both"}, 200, stock=200) == 200
    assert b.visits == 1 and b.selected == [] and b.purchases == [200]


def test_delayed_inventory_update_still_uses_second_stack(monkeypatch: pytest.MonkeyPatch) -> None:
    b = StorageBot((10, 200), delay=0.8)
    prepare(monkeypatch, b)
    assert town._fill(as_bot(b), {"item": ITEM, "source": "both"}, 200, stock=200) == 200
    assert b.selected == [0, 1] and b.purchases == []


def test_no_inventory_progress_does_not_retry_storage_forever(monkeypatch: pytest.MonkeyPatch) -> None:
    b = StorageBot((10, 200))
    prepare(monkeypatch, b)
    calls: list[int] = []

    def consumed(b: Any, item: str, n: int, *, stock: int | None = None) -> int:
        calls.append(n)
        assert len(calls) == 1, "storage retried despite no progress toward the target"
        return 10  # arrived but consumed: bag count is still zero

    monkeypatch.setattr(town, "withdraw", consumed)
    town._fill(as_bot(b), {"item": ITEM, "source": "both"}, 200, stock=200)
    assert calls == [200] and b.purchases == [200]
