"""The bag manager: what to keep, store, sell or throw away, and how to keep supplies up.

The player decides (2026-10-04: "our bot must be smart, and for users, there should be control"):
every item in the bag can have a rule - keep / store / sell / throw away, and how many to keep in
the bag - and every supply (potions, pet potions, food...) a refill rule - take from the storehouse,
buy at the shop, both, or just tell me. The bot only suggests; the player's choice wins.

What makes the suggestions smart, all from the game itself:
  - shard prices from the Shard Market's own menu (data/shards.json),
  - which NPC buys or sells an item, and for how much (data/knowledge.json),
  - item weights the bot learns on its own: the character sheet shows the bag's load, so the
    load before and after storing or selling N of an item gives that item's weight.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, TypedDict
import os
import re

from .. import jsonio as json, knowledge, paths

# Never suggested for anything but "keep": supplies, travel items, money, anything on a hotkey.
SUPPLY = re.compile(r"potion|teleportation stone|hearthstone|memory butterfly|resurrection crystal|"
                    r"rice roll|toolbox|pill|card|arnebia|gold coin|candy|food|jerky|meat", re.I)
WEIGHTS = os.path.join(paths.PROFILES, "_learned_weights.json")


class BagRule(TypedDict):
    """What to do with an item: keep, sell, store or throw it (action), how many to keep, and
    whether the player chose that (else it is the bot's suggestion)."""
    action: str
    keep: int
    chosen: bool


# ---------------------------------------------------------------- facts about an item
def shard_price(name: str) -> int | None:
    from .town import shard_catalog
    shards, _m = shard_catalog()
    for n, v in shards.items():
        if n.lower() == name.lower():
            return v["price"]
    return None


def buyer(name: str) -> tuple[str, int, str] | None:
    """(npc, price, currency) of the best NPC buyer the game has shown for it, or None."""
    t = knowledge.traders(name, "buy")
    if t:
        best = max(t, key=lambda r: r[4])
        return best[1], best[4], best[5]
    return None


def seller(name: str) -> tuple[str, int, str] | None:
    """(npc, price, currency) of the cheapest NPC shop the game has shown selling it, or None."""
    t = knowledge.traders(name, "sell")
    return (t[0][1], t[0][4], t[0][5]) if t else None


def can_sell(name: str) -> bool:
    return shard_price(name) is not None or buyer(name) is not None or knowledge.buyer_role(name) is not None


def weights() -> dict[str, float]:
    """{item (lower case): its weight}, as learned from the load (learn_weight)."""
    try:
        with open(WEIGHTS, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def learn_weight(name: str, load_before: float | None, load_after: float | None, n: int) -> float | None:
    """The load fell by (before - after) when n of the item left the bag: remember its weight."""
    if n <= 0 or load_before is None or load_after is None or load_before < load_after:
        return None
    w = round((load_before - load_after) / float(n), 3)
    data = weights()
    data[name.lower()] = w
    try:
        with open(WEIGHTS, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=1, sort_keys=True)
    except OSError:
        pass
    return w


def about(name: str, count: int | None = None, hotkey: bool = False) -> str:
    """One line of facts for the bag manager."""
    bits: list[str] = []
    sp = shard_price(name)
    if sp is not None:
        bits.append("Shard Market pays about %d arnebia each" % sp)
    by = buyer(name)
    if by:
        bits.append("%s buys it for %s %s" % by)
    else:
        role = knowledge.buyer_role(name)
        if role:
            bits.append("a %s buys it" % role.replace("_", " "))
    sl = seller(name)
    if sl:
        bits.append("%s sells it for %s %s" % sl)
    w = weights().get(name.lower())
    if w is not None:
        bits.append("weighs %g each" % w + ("" if count is None else ", %g for all %d" % (w * count, count)))
    if hotkey:
        bits.append("on a hotkey")
    return "; ".join(bits) or "nothing known yet"


# ---------------------------------------------------------------- suggestions
def suggest(name: str, hotkey: bool = False) -> tuple[str, int]:
    """The bot's advice for an item with no rule yet: (action, keep)."""
    if hotkey or SUPPLY.search(name):
        return "keep", 0
    # Shards included: prices change, so the player sorts every shard into throw away / keep /
    # sell; one never sorted stays in the bag. Profiles from before keep the old price rule as
    # their lists (settings._upgrade).
    return "keep", 0         # unknown things stay where they are until the player decides


def rule(p: dict[str, Any], name: str, hotkey: bool = False) -> BagRule:
    """The rule the player set for the item, else the suggestion."""
    rules: dict[str, dict[str, Any]] = p.get("bag_rules") or {}
    r = rules.get(name.lower())
    if r:
        return {"action": r.get("action", "keep"), "keep": int(r.get("keep", 0)), "chosen": True}
    a, k = suggest(name, hotkey)
    return {"action": a, "keep": k, "chosen": False}


def plan(p: dict[str, Any], bag: dict[str, int], hotkeys: Sequence[str] = (), gained: dict[str, int] | None = None) -> list[tuple[str, str, int]]:
    """What a bag trip would do: [(name, action, how_many)]. bag: {name: count}. With
    only_this_run (the default) nothing beyond what this run picked up (gained) is touched,
    unless the player chose a rule for that item themselves."""
    hot = {h.lower() for h in hotkeys}
    out: list[tuple[str, str, int]] = []
    for name, count in sorted(bag.items()):
        r = rule(p, name, name.lower() in hot)
        if r["action"] == "keep":
            continue
        n = count - r["keep"]
        if p.get("only_this_run", True) and not r["chosen"] and gained is not None:
            n = min(n, gained.get(name.lower(), 0))
        if n > 0:
            out.append((name, r["action"], n))
    return out


# ---------------------------------------------------------------- supplies
def supply_rule(r: dict[str, Any]) -> str:
    """Old restock rules (storage_first/buy) read as one source."""
    if "source" in r:
        return r["source"]
    if r.get("storage_first", True) and r.get("buy", True):
        return "both"
    if r.get("storage_first", True):
        return "storage"
    if r.get("buy", True):
        return "buy"
    return "tell"


def suggest_supplies(scan: dict[str, Any] | None) -> list[dict[str, Any]]:
    """Supplies worth watching, from what sits on the hotkeys: [{"item", "min", "stock", "source"}].
    Off ("tell") until the player picks a source, so nothing is bought or taken unasked."""
    out: list[dict[str, Any]] = []
    hotkeys: list[dict[str, Any]] = (scan or {}).get("hotkeys", [])
    for h in hotkeys:
        item = h.get("item")
        if not item or not re.search(r"potion|rice roll|food|toolbox", item, re.I):
            continue
        out.append({"item": item, "min": 30, "stock": 200, "source": "tell", "on": True})
    return out
