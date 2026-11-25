"""The bag catches up a moment after a menu closes: a withdrawal, a purchase or a sale counted at
once read 0, and the farm loop's restock bought again what the storehouse had just handed over, or
a sale's money went to the next job and what was sold was taken to the storehouse."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import Clock, as_bot, returning, stand_in

from unclefarmer import knowledge
from unclefarmer.engine import town

LAG = 0.3        # seconds the game takes to show the change in the bag


class LaggyBot:
    def __init__(self, bag: dict[str, int]) -> None:
        self.clock = Clock()
        self.bag = dict(bag)
        self.coming: list[tuple[float, str, int]] = []      # (when, item, change)
        self.lines: list[str] = []
        self.p: dict[str, Any] = {}
        self.stop_event = None

    def later(self, item: str, change: int) -> None:
        self.coming.append((self.clock.time() + LAG, item, change))

    def bag_rows(self) -> list[dict[str, Any]]:
        now = self.clock.time()
        for when, item, change in [c for c in self.coming if c[0] <= now]:
            self.bag[item] = self.bag.get(item, 0) + change
            self.coming.remove((when, item, change))
        return [{"name": k, "count": v} for k, v in self.bag.items() if v]

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def dialog(self) -> dict[str, Any]:
        return {"open": 1, "top": {"rows": [[{"options": ["Medium Red Potion"]}]]}}

    def find_entry(self, top: Any, pattern: str) -> tuple[int, int] | None:
        return (0, 0)

    def close_dialogs(self) -> None: ...

    def note_dialog(self, *_args: Any) -> None: ...

    def log(self, text: str, speak: bool = False) -> None:
        self.lines.append(text)


@pytest.fixture
def at_the_npc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Npc")))
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(True)))
    monkeypatch.setattr(town, "in_town", stand_in(town.in_town, lambda b, npcs: list(npcs)))
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs,
                                                            lambda role: [(1081, "Storehouse Keeper", 1, 1)]))


def test_a_withdrawal_while_farming_counts_what_the_storehouse_handed_over(
        monkeypatch: pytest.MonkeyPatch, at_the_npc: None) -> None:
    b = LaggyBot({"Medium Red Potion": 10})
    def took(_bot: Any, _pattern: str, n: int | None) -> bool:
        b.later("Medium Red Potion", n or 0)
        return True
    monkeypatch.setattr(town, "pick", took)
    assert town.withdraw(as_bot(b), "Medium Red Potion", 40) == 40          # no stock: the farm loop's way


def test_a_sale_counts_what_left_the_bag(monkeypatch: pytest.MonkeyPatch, at_the_npc: None) -> None:
    b = LaggyBot({"Wolf Fur": 30, "arnebia": 1000})
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders,
                                                      lambda item, they: [(1081, "Grocer", 1, 1, 10, "arnebia")]))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: "grocer"))

    def sold(_bot: Any, _pattern: str, n: int | None) -> bool:
        b.later("Wolf Fur", -(n or 0))
        b.later("arnebia", 10 * (n or 0))
        return True
    monkeypatch.setattr(town, "pick", sold)
    assert town.sell(as_bot(b), "Wolf Fur") == 30
    assert b.bag_rows() == [{"name": "arnebia", "count": 1300}]
