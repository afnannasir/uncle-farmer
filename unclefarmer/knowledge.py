"""What the bot knows about the game besides the maps: cities and their service NPCs, every NPC we
have seen, items and what they are for. Built from the game's own files into
data/knowledge.json (read-only here)."""

from __future__ import annotations

from typing import Any
import os
import re
import threading

from . import jsonio as json, paths

_k: dict[str, Any] | None = None
_lock = threading.Lock()

# Which shop sells what (the potion sellers are called Apothecary / Pharmacist / Potion Merchant,
# role "potions"; everything else everyday comes from the Grocer).
SHOP_FOR_CATEGORY: dict[str, str] = {
    "hp_potion": "potions", "mp_potion": "potions", "pet_hp_potion": "potions", "pet_mp_potion": "potions",
    "potion": "potions", "food": "grocer", "drink": "grocer", "pet_food": "grocer", "pet_drink": "grocer",
    "repair_one": "grocer", "repair_equipped": "grocer", "repair_bag": "grocer", "hearthstone": "grocer",
    "memory_teleport": "grocer", "teleport_stone": "grocer",
}
# Who buys what you sell.
BUYER_FOR_CATEGORY: dict[str, str] = {"fish": "fish_buyer", "shard": "shard_market", "gem": "shard_market"}


def data() -> dict[str, Any]:
    global _k
    with _lock:
        if _k is None:
            path = os.path.join(paths.DATA, "knowledge.json")
            k: dict[str, Any]
            if os.path.exists(path):
                with open(path, encoding="utf-8") as f:
                    k = json.load(f)
            else:
                k = {"npcs": [], "cities": [], "items": [], "maps": [], "squares": {}}
            k["_items"] = {i["name"].lower(): i for i in k.get("items", [])}
            _k = k
        return _k


def item(name: str | None) -> dict[str, Any] | None:
    return data()["_items"].get((name or "").strip().lower())


def category(name: str | None) -> str:
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


_lists: dict[str, dict[str, int]] | None = None


def shop_lists() -> dict[str, dict[str, int]]:
    """{role: {lower-cased item: unit price}}: what every town shop of that kind sells, read from
    their menus in the game (data/shop_lists.json). A role missing here has no list."""
    global _lists
    with _lock:
        if _lists is None:
            raw: object = json.read(os.path.join(paths.DATA, "shop_lists.json"), {}, encoding="utf-8")
            lists: object = raw.get("lists") if json.is_object(raw) else None
            out: dict[str, dict[str, int]] = {}
            if json.is_object(lists):
                for role, goods in lists.items():
                    if json.is_object(goods):
                        out[role] = {str(k).strip().lower(): v for k, v in goods.items()
                                     if isinstance(v, int) and not isinstance(v, bool)}
            _lists = out
        return _lists


def listed_role(name: str | None) -> str | None:
    """The kind of town shop whose list has this item, or None."""
    want = (name or "").strip().lower()
    return next((role for role, goods in shop_lists().items() if want in goods), None)


def shop_role(name: str | None) -> str | None:
    """Which kind of NPC sells this item (None if no shop is known to). A shop list decides for
    its kind: an item it doesn't have isn't sold there, whatever its category (the grocers sell no
    food or teleport stones, and no shop sells a Super Red Potion - live 2026-11-19)."""
    listed = listed_role(name)
    if listed:
        return listed
    role = SHOP_FOR_CATEGORY.get(category(name))
    return None if role in shop_lists() else role


def buyer_role(name: str | None) -> str | None:
    c = category(name)
    if c in BUYER_FOR_CATEGORY:
        return BUYER_FOR_CATEGORY[c]
    return {"herb": "herb_buyer", "ore": "ore_buyer"}.get(c)


def cities() -> list[dict[str, Any]]:
    return data().get("cities", [])


def service_npcs(role: str) -> list[tuple[int, str, float, float]]:
    """Every NPC of that role we know, as (map, name, x, y), city ones first."""
    city_maps = {c["map"] for c in cities()}
    out = [(n["map"], n["name"], n["x"], n["y"]) for n in data().get("npcs", []) if n["role"] == role]
    out.sort(key=lambda t: (t[0] not in city_maps, t[0]))
    return out


def traders(item: str | None, they: str) -> list[tuple[int, str, float, float, int, str]]:
    """NPCs the game has shown trading this item: they="sell" (you can buy it there) or "buy".
    [(map, name, x, y, price, currency)], cheapest first."""
    want = (item or "").strip().lower()
    out: list[tuple[int, str, float, float, int, str]] = []
    for n in data().get("npcs", []):
        for g in n.get("goods", []):
            if g["item"].strip().lower() == want and g["they"] == they:
                out.append((n["map"], n["name"], n["x"], n["y"], g["price"], g["currency"]))
    out.sort(key=lambda t: (t[5] != "arnebia", t[4]))
    return out


def unit_price(name: str | None) -> int | None:
    """What one of the item costs at a shop, in arnebia: the cheapest NPC menu the game has shown
    selling it, else the price its item record holds (the shops' list price, e.g. a grocer's
    toolboxes and hearthstones, whose menus the game data never recorded). None when unknown."""
    sold = [t[4] for t in traders(name, "sell") if t[5] == "arnebia" and t[4] > 0]
    if sold:
        return min(sold)
    role = listed_role(name)
    if role:
        return shop_lists()[role][(name or "").strip().lower()]
    price = (item(name) or {}).get("price")
    return price if isinstance(price, int) and not isinstance(price, bool) and price > 0 else None


def describe_npc(name: str | None) -> dict[str, Any] | None:
    for n in data().get("npcs", []):
        if n["name"].lower() == (name or "").lower():
            return n
    return None
