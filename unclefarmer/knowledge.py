"""What the bot knows about the game besides the maps: cities and their service NPCs, every NPC we
have seen, items and what they are for. Built from the game's own files into
data/knowledge.json (read-only here)."""

from typing import Any
import os
import re
import threading

from . import jsonio as json, paths

_k = None
_lock = threading.Lock()

# Which shop sells what (the potion sellers are called Apothecary / Pharmacist / Potion Merchant,
# role "potions"; everything else everyday comes from the Grocer).
SHOP_FOR_CATEGORY = {
    "hp_potion": "potions", "mp_potion": "potions", "pet_hp_potion": "potions", "pet_mp_potion": "potions",
    "potion": "potions", "food": "grocer", "drink": "grocer", "pet_food": "grocer", "pet_drink": "grocer",
    "repair_one": "grocer", "repair_equipped": "grocer", "repair_bag": "grocer", "hearthstone": "grocer",
    "memory_teleport": "grocer", "teleport_stone": "grocer",
}
# Who buys what you sell.
BUYER_FOR_CATEGORY = {"fish": "fish_buyer", "shard": "shard_market", "gem": "shard_market"}


def data() -> dict[str, Any]:
    global _k
    with _lock:
        if _k is None:
            path = os.path.join(paths.DATA, "knowledge.json")
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    _k = json.load(f)
            else:
                _k = {"npcs": [], "cities": [], "items": [], "maps": [], "squares": {}}
            _k["_items"] = {i["name"].lower(): i for i in _k.get("items", [])}
    return _k


def item(name):
    return data()["_items"].get((name or "").strip().lower())


def category(name):
    i = item(name)
    if i and i.get("category") not in (None, "", "other"):
        return i["category"]
    n = (name or "").lower()   # the table says "other" for ores and herbs too: go by the name
    if n.endswith("shard"):
        return "shard"
    if re.match(r"^level \d+ ", n):
        return "gem"
    if re.search(r"(fish|crab|shrimp|lobster|turtle|alligator|barracuda|bullfrog|jellyfish)$", n):
        return "fish"
    if re.search(r"herb|grass|flower|leaf|root", n):
        return "herb"
    if re.search(r"ore$|crystal ore", n):
        return "ore"
    return "other"


def shop_role(name):
    """Which kind of NPC sells this item (None if no shop is known to)."""
    return SHOP_FOR_CATEGORY.get(category(name))


def buyer_role(name):
    c = category(name)
    if c in BUYER_FOR_CATEGORY:
        return BUYER_FOR_CATEGORY[c]
    return {"herb": "herb_buyer", "ore": "ore_buyer"}.get(c)


def cities() -> list[dict[str, Any]]:
    return data().get("cities", [])


def service_npcs(role):
    """Every NPC of that role we know, as (map, name, x, y), city ones first."""
    city_maps = {c["map"] for c in cities()}
    out = [(n["map"], n["name"], n["x"], n["y"]) for n in data().get("npcs", []) if n["role"] == role]
    out.sort(key=lambda t: (t[0] not in city_maps, t[0]))
    return out


def traders(item, they):
    """NPCs the game has shown trading this item: they="sell" (you can buy it there) or "buy".
    [(map, name, x, y, price, currency)], cheapest first."""
    want = (item or "").strip().lower()
    out = []
    for n in data().get("npcs", []):
        for g in n.get("goods", []):
            if g["item"].strip().lower() == want and g["they"] == they:
                out.append((n["map"], n["name"], n["x"], n["y"], g["price"], g["currency"]))
    out.sort(key=lambda t: (t[5] != "arnebia", t[4]))
    return out


def describe_npc(name):
    for n in data().get("npcs", []):
        if n["name"].lower() == (name or "").lower():
            return n
    return None
