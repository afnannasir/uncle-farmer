"""Shared reconciliation for a sequence of menu actions that changes the bag.

The game can accept an action before its bag snapshot catches up.  A transaction records every
requested change and waits once for the final snapshot, avoiding a bag request after each action
without treating a local menu transition as proof that a sale succeeded.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from ..clock import clock_for

if TYPE_CHECKING:
    from .bot import Bot


SETTLE_SECONDS = 3.0
SETTLE_POLL = 0.05


def _int_dict() -> dict[str, int]:
    return {}


def _str_dict() -> dict[str, str]:
    return {}


def counts(b: Bot) -> dict[str, int]:
    """The bag's item counts, combined by case-insensitive name."""
    out: dict[str, int] = {}
    for row in b.bag_rows():
        name = (row.get("name") or "").strip().lower()
        out[name] = out.get(name, 0) + int(row.get("count") or 0)
    return out


@dataclass
class InventoryTransaction:
    """The bag before a sequential operation and its expected signed deltas."""

    before: dict[str, int]
    expected: dict[str, int] = field(default_factory=_int_dict)
    labels: dict[str, str] = field(default_factory=_str_dict)

    @classmethod
    def begin(cls, b: Bot) -> InventoryTransaction:
        return cls(counts(b))

    def expect(self, item: str, delta: int) -> None:
        if not delta:
            return
        key = item.strip().lower()
        self.expected[key] = self.expected.get(key, 0) + delta
        self.labels.setdefault(key, item)

    def settle(self, b: Bot, seconds: float = SETTLE_SECONDS) -> dict[str, int]:
        """Return the first bag snapshot that contains every expected change, or the latest one."""
        end = clock_for(b).time() + seconds
        while True:
            after = counts(b)
            if all(after.get(item, 0) <= self.before.get(item, 0) + delta
                   if delta < 0 else after.get(item, 0) >= self.before.get(item, 0) + delta
                   for item, delta in self.expected.items()):
                return after
            if clock_for(b).time() >= end:
                return after
            b.sleep(SETTLE_POLL)

    def removed(self, after: Mapping[str, int]) -> list[tuple[str, int]]:
        """The actual removals among the items this transaction requested."""
        return [(self.labels[item], amount) for item in self.expected
                if (amount := max(0, self.before.get(item, 0) - after.get(item, 0)))]
