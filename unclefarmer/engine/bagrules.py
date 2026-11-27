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

from .. import applog, jsonio as json, knowledge, paths

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
    """(npc, price, currency) of the cheapest NPC shop the game has shown selling it, else the
    kind of shop that sells it at the item's list price ("a grocer", 20000, "arnebia"), or None."""
    t = knowledge.traders(name, "sell")
    if t:
        return t[0][1], t[0][4], t[0][5]
    price = knowledge.unit_price(name)
    role = knowledge.shop_role(name)
    if price and role:
        return "a " + role.replace("_", " "), price, "arnebia"
    return None


def can_sell(name: str) -> bool:
    return shard_price(name) is not None or buyer(name) is not None or knowledge.buyer_role(name) is not None


_last_weights: dict[str, float] = {}     # the last good reading, for a moment the file can't be read


def _read_weights() -> dict[str, float]:
    """The weights file as it is ({} when there is none yet); OSError when it can't be read and
    ValueError when it is broken, never {} for those (learn_weight saves what it gets back)."""
    raw: object = json.read(WEIGHTS, {}, encoding="utf-8")
    if not json.is_object(raw):
        raise ValueError("%s isn't a JSON object" % WEIGHTS)
    return raw


def weights() -> dict[str, float]:
    """{item (lower case): its weight}, as learned from the load (learn_weight). Read under the
    file's lock; a moment the file can't be read gives the last good reading."""
    global _last_weights
    with json.lock_for(WEIGHTS):
        try:
            got = _read_weights()
        except ValueError:
            return {}
        except OSError as e:
            applog.write("couldn't read %s, using the last reading: %s" % (WEIGHTS, e), "WARNING")
            return dict(_last_weights)
        _last_weights = dict(got)
        return got


def learn_weight(name: str, load_before: float | None, load_after: float | None, n: int) -> float | None:
    """The load fell by (before - after) when n of the item left the bag: remember its weight. A load
    that didn't move says nothing (the reading may be old, or rounded): an earlier weight is kept,
    where it used to be overwritten with 0.

    The whole read-change-save holds the file's lock: two items learned at once each read the same
    file and the second save dropped the first weight, though both said they had learned it. The
    weight is returned only once it is saved; None when it couldn't be (said in the log)."""
    global _last_weights
    if n <= 0 or load_before is None or load_after is None or load_before <= load_after:
        return None
    w = round((load_before - load_after) / float(n), 3)
    with json.lock_for(WEIGHTS):
        try:
            data = _read_weights()
        except ValueError:
            json.copy_aside(WEIGHTS)       # broken: kept as .corrupt, then started again
            data = {}
        except OSError as e:
            applog.write("couldn't read %s, so the weight of %s wasn't kept: %s" % (WEIGHTS, name, e), "WARNING")
            return None
        data[name.lower()] = w
        try:
            json.save(WEIGHTS, data, indent=1, sort_keys=True)
        except OSError as e:
            applog.write("couldn't save %s: %s" % (WEIGHTS, e), "WARNING")
            return None
        _last_weights = dict(data)
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


# What a supply is refilled from and up to, by the item's kind (knowledge.category): (min, stock).
# Sized from the players' own runs (2026-11-27: 25-46 blue potions an hour, up to 20 of the pet's,
# 4-5 Pet Rice Rolls, a toolbox every 3 hours or more) - a row ticked by mistake with a shop as its
# source buys a night's worth, never 200 Alloy Toolboxes at 80 000 arnebia each.
SUPPLY_SIZES: dict[str, tuple[int, int]] = {
    "hp_potion": (30, 200), "mp_potion": (30, 200),
    "pet_hp_potion": (20, 100), "pet_mp_potion": (20, 100),
    "pet_food": (3, 30),
    "repair_equipped": (1, 5),     # a toolbox mends everything worn
    "repair_one": (10, 50),        # a plier mends one piece
}
OTHER_SIZE = (5, 20)               # anything else: a potion of another kind, a dish
# The supplies a character is offered, in this order: the kind, and when it is offered.
USES = ("hp_potion", "mp_potion", "pet_hp_potion", "pet_mp_potion", "pet_food", "repair_equipped")
_RANK = ("small", "medium", "large", "super", "potent", "alloy")
_OFFERED = (*SUPPLY_SIZES, "potion", "food")    # kinds a hotkey item is offered for


def sold_in_shops(name: str) -> bool:
    """Whether a shop sells it for arnebia (a town shop's list, or a menu the game has shown)."""
    return bool(knowledge.shop_role(name)) or any(t[5] == "arnebia" for t in knowledge.traders(name, "sell"))


def new_supply(name: str) -> dict[str, Any]:
    """A supply row for the item, sized by its kind: off ("tell") while a shop sells it, so nothing
    is bought until the player picks a source; from the storehouse only when no shop does (the
    Super potions are crafted or come in gifts). A name the game data doesn't know (mistyped, say)
    stays "tell" too: no trip to the storehouse for an item that may not exist."""
    low, fill = SUPPLY_SIZES.get(knowledge.category(name), OTHER_SIZE)
    known = knowledge.item(name) is not None
    return {"item": name, "min": low, "stock": fill,
            "source": "storage" if known and not sold_in_shops(name) else "tell", "on": True}


def _strongest(names: Sequence[str]) -> str | None:
    return max(names, key=lambda n: max([i for i, w in enumerate(_RANK) if w in n.lower()] or [-1]), default=None)


def _wanted(kind: str, p: dict[str, Any]) -> bool:
    """Whether the character needs this kind of supply: the blues only for one that uses MP, the
    pet's only with a pet ticked."""
    if kind == "mp_potion":
        return bool(p.get("mp_use", True))
    if kind.startswith("pet_"):
        pets: object = p.get("pets")
        return json.is_array(pets) and any(json.is_object(x) and x.get("use") for x in pets)
    return True


def _named(kind: str, p: dict[str, Any]) -> str | None:
    """The item the profile names for this kind (a potion other than "auto", the pet's food)."""
    key = {"hp_potion": "hp_potion", "mp_potion": "mp_potion", "pet_hp_potion": "pet_hp_potion",
           "pet_mp_potion": "pet_mp_potion", "pet_food": "pet_food"}.get(kind)
    got = str(p.get(key) or "").strip() if key else ""
    return got if got and got.lower() != "auto" else None


def suggest_supplies(scan: dict[str, Any] | None, p: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """Supplies worth watching: [{"item", "min", "stock", "source", "on"}], one for each thing the
    character uses (USES: its red and blue potions, the pet's, the pet's food, a toolbox), as a
    dungeon run picks them: the item the profile names, else the one on a hotkey, else the
    strongest in the bag; then any other supply on a hotkey (a plier, a Mist Potion). Sized by kind
    (SUPPLY_SIZES); off until the player picks a source (new_supply)."""
    p = p or {}
    hotkeys = [str(h.get("item") or "").strip() for h in (scan or {}).get("hotkeys", []) if json.is_object(h)]
    hotkeys = [h for h in hotkeys if h]
    bag = [str(b.get("name") or "").strip() for b in (scan or {}).get("bag", [])
           if json.is_object(b) and int(b.get("count") or 0) > 0]
    out: list[dict[str, Any]] = []
    seen: set[str] = set()

    def offer(name: str) -> None:
        if name.lower() not in seen:
            seen.add(name.lower())
            out.append(new_supply(name))

    for kind in USES:
        if not _wanted(kind, p):
            continue
        item = (_named(kind, p)
                or _strongest([h for h in hotkeys if knowledge.category(h) == kind])
                or _strongest([b for b in bag if knowledge.category(b) == kind]))
        if item:
            offer(item)
    for h in hotkeys:
        if knowledge.category(h) in _OFFERED:
            offer(h)
    return out
