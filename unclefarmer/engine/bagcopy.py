"""Copying a part of one character's bag settings to another: its throw-away, sell, storehouse or keep
list, its supplies, its full bag and equipment choices. Sorting a list item by item for each
character was a lot of work; the bag manager's "Copy from another character" uses these.

Each copy either adds to what the character has (an item in the other's list goes into the same
list here, with what it leaves in the bag; what is only here stays), or replaces it (the list here
becomes the other's: an item only here goes back to the bot's own default for it). Plain data in,
plain data out: nothing is saved here.
"""

from __future__ import annotations

import copy
from collections.abc import Iterable
from typing import Any

LISTS = ("throw", "sell", "store", "keep")
# The Full bag page: when the bag is full, and the equipment thrown away by rarity.
FULL_KEYS = ("bag_full_at", "when_bag_full", "gear_throw", "gear_throw_minutes", "gear_keep_bonus")

Rules = dict[str, dict[str, Any]]


def copy_rules(mine: Rules, theirs: Rules, lists: Iterable[str], replace: bool) -> tuple[Rules, dict[str, int], dict[str, int]]:
    """(the rules after the copy, items put into each list, items already there as they are)."""
    wanted = [a for a in LISTS if a in set(lists)]
    out: Rules = {k: dict(v) for k, v in mine.items()}
    added = {a: 0 for a in wanted}
    same = {a: 0 for a in wanted}
    if replace:
        for name, rule in list(out.items()):
            action = rule.get("action", "keep")
            if action in wanted and (theirs.get(name) or {}).get("action", "keep") != action:
                del out[name]        # only in this character's list: back to the bot's own default
    for name, rule in theirs.items():
        action = rule.get("action", "keep")
        if action not in wanted:
            continue
        keep = int(rule.get("keep", 0) or 0)
        now = out.get(name)
        if now and now.get("action", "keep") == action and int(now.get("keep", 0) or 0) == keep:
            same[action] += 1
            continue
        out[name] = {"action": action, "keep": keep}
        added[action] += 1
    return out, added, same


def copy_supplies(mine: list[dict[str, Any]], theirs: list[dict[str, Any]],
                  replace: bool) -> tuple[list[dict[str, Any]], int, int]:
    """(the supplies after the copy, supplies added, ones already here). Adding keeps this
    character's own refill numbers for an item both have."""
    if replace:
        return copy.deepcopy(theirs), len(theirs), 0
    out = copy.deepcopy(mine)
    have = {str(r.get("item", "")).strip().lower() for r in out}
    added = same = 0
    for r in theirs:
        if str(r.get("item", "")).strip().lower() in have:
            same += 1
            continue
        out.append(copy.deepcopy(r))
        added += 1
    return out, added, same


def full_settings(theirs: dict[str, Any]) -> dict[str, Any]:
    """The other character's Full bag page, to set here (a page of choices: always replaced)."""
    return {k: copy.deepcopy(theirs[k]) for k in FULL_KEYS if k in theirs}
