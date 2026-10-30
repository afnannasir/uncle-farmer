"""Town errands: the storehouse, shops, buyers - and keeping the bag supplied (restock).

Everything goes through the game's own menus: talk to the NPC (bot.talk), pick an entry with the
walking keys and Enter (bot.choose), and when the game asks "how many?" type the number into its
input box (agent `type` / `typeenter`, the window's own key handlers). Menu texts as the game
shows them:
  storehouse   "item storage" -> "1; 20 Memory Butterfly" ... (pick, then type how many to take out)
               "deposit item" -> your bag rows (pick, then type how many to put in)
  buyers       "Hello <name>, what would you like to sell?" -> "101 Milkfish; Unit price 670 arnebia"
  shops        not logged yet: the bot looks for an entry naming the item, or a "buy" entry first
Every menu met is written to logs/dialogs.log so the unknown ones can be learned from a live run.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Callable, Iterable, Sequence
from typing import Any

from .. import atlas, jsonio as json, knowledge, paths, stats, travel
from ..clock import clock_for
from ..i18n import _
from . import bagrules
from .bot import CONFIRM, Bot, Stopped


def counts(b: Bot) -> dict[str, int]:
    """{item name (lower): count} from the bag, summed over its rows."""
    out: dict[str, int] = {}
    for r in b.bag_rows():
        n = (r.get("name") or "").strip().lower()
        out[n] = out.get(n, 0) + int(r.get("count") or 0)
    return out


def item_pattern(item: str) -> str:
    """Matches the item in a menu entry ("187 Large Red Potion", "Large Red Potion; Unit price ...")
    but not a longer name containing it ("Pet Large Red Potion")."""
    return r"(^|[;|]\s*|\d\s+)" + re.escape(item.strip()) + r"\s*($|[|;,])"


# ---------------------------------------------------------------------------------------- getting there
Npc = tuple[int, str, float, float]      # (map, name, x, y)


def nearest(b: Bot, role: str | None, candidates: Sequence[Npc] | None = None) -> Npc | None:
    """The NPC (of that role, or from candidates) cheapest to reach from here: (map, name, x, y)."""
    here = b.map_id()
    me = b.me()
    best: tuple[float, Npc] | None = None
    for mid, name, x, y in (candidates if candidates is not None else knowledge.service_npcs(role or "")):
        legs, cost = travel.plan(here, (me["x"], me["y"]), mid, (x, y + 1), with_cost=True,
                                 transport=b.p.get("use_transport", True), vip=b.p.get("vip", False),
                                 bag=b.bag_rows() if b.p.get("use_travel_items", False) else [],
                                 hearth_home=b.p.get("hearth_home") or None)
        if legs is None or cost is None:
            continue
        if best is None or cost < best[0]:
            best = (cost, (mid, name, x, y))
    return best[1] if best else None


def visit(b: Bot, role: str | None, name: str | None = None, candidates: Sequence[Npc] | None = None) -> str:
    """Travel to the nearest NPC of a role (or the named one, or the nearest of candidates) and open
    its menu. Returns its name."""
    if candidates:
        target = nearest(b, role, candidates)
    elif name:
        known = knowledge.describe_npc(name)
        target = (known["map"], known["name"], known["x"], known["y"]) if known else None
    else:
        target = nearest(b, role)
    if target is None:
        raise Stopped(_("I don't know where to find a {role} - visit one with the agent running first",
                        role=(role or name or "").replace("_", " ")))     # a named NPC has no role
    mid, npc, x, y = target
    b.log(_("going to {npc} on {map}", npc=npc, map=atlas.label(mid)))
    b.travel(mid)
    if not b.talk(npc, (x, y)):
        raise Stopped(_("couldn't talk to {npc}", npc=npc))
    return npc


# ---------------------------------------------------------------------------------------- menus + typing
def wait_input(b: Bot, want_open: bool = True, seconds: float = 3.0) -> bool:
    end = clock_for(b).time() + seconds
    while clock_for(b).time() < end:
        s = b.a.json("input")
        if s.get("ok") and bool(s.get("open")) == want_open:
            return True
        b.sleep(0.15)
    return False


def type_text(b: Bot, text: str) -> bool:
    """Answer a box that asks for a name. True once the game has taken it."""
    if not wait_input(b, True):
        return False
    b.a.send("typeclear")
    r = b.a.send("type %s" % text)
    if not r.startswith("typed"):
        b.log(_("couldn't type into the game: {why}", why=r), speak=True)
        return False
    b.sleep(0.2)
    b.a.send("typeenter")
    return wait_input(b, False)


def type_number(b: Bot, n: int) -> bool:
    """Answer the game's "how many?" box. True once it has taken the number."""
    if not wait_input(b, True):
        return False
    b.a.send("typeclear")
    r = b.a.send("type %d" % int(n))
    if not r.startswith("typed"):
        b.log(_("couldn't type into the game: {why}", why=r), speak=True)
        return False
    b.sleep(0.2)
    b.a.send("typeenter")
    if not wait_input(b, False):
        b.log(_("the game didn't take the number"), speak=True)
        return False
    b.sleep(0.4)
    # some menus ask "are you sure?" after the number
    d = b.dialog()
    if d["open"] and d.get("top") and b.find_entry(d["top"], CONFIRM) is not None:
        b.choose(CONFIRM)
    return True


def pick(b: Bot, pattern: str, quantity: int | None = None) -> bool:
    """Choose an entry; if the game then asks how many, type quantity."""
    if b.choose(pattern) is None:
        return False
    if quantity is None:
        return True
    return type_number(b, quantity)


def open_section(b: Bot, pattern: str) -> bool:
    """Go one level into a menu (e.g. "item storage"), if the open menu has that entry."""
    d = b.dialog()
    if d["open"] and d.get("top") and b.find_entry(d["top"], pattern) is not None:
        b.choose(pattern)
        return bool(b.wait_dialog(3.0))
    return False


# ---------------------------------------------------------------------------------------- errands
def withdraw(b: Bot, item: str, n: int):
    """Take up to n of item out of the storehouse. Returns how many arrived in the bag."""
    before = counts(b).get(item.lower(), 0)
    visit(b, "storehouse")
    try:
        if not open_section(b, r"^\s*item storage"):
            b.log(_("the storehouse menu has no item storage"), speak=True)
            return 0
        d = b.dialog()
        if b.find_entry(d["top"], item_pattern(item)) is None:
            b.log(_("no {item} in the storehouse", item=item))
            return 0
        pick(b, item_pattern(item), n)
    finally:
        b.close_dialogs()
    got = counts(b).get(item.lower(), 0) - before
    b.log(_("took {n} {item} out of the storehouse", n=got, item=item))
    stats.moved(b, "withdrawn", item, got)
    return got


def deposit(b: Bot, item: str, n: int | None=None):
    """Put n (default: all) of item into the storehouse."""
    have = counts(b).get(item.lower(), 0)
    if have <= 0:
        return 0
    n = have if n is None else min(n, have)
    visit(b, "storehouse")
    try:
        if not open_section(b, r"^\s*deposit item"):
            b.log(_("the storehouse menu has no deposit"), speak=True)
            return 0
        if not pick(b, item_pattern(item), n):
            return 0
    finally:
        b.close_dialogs()
    put = have - counts(b).get(item.lower(), 0)
    b.log(_("put {n} {item} into the storehouse", n=put, item=item))
    stats.moved(b, "stored", item, put)
    return put


# Which shops turned out to stock what, learned on the way: {"<map>|<npc>": {"sells": [...],
# "lacks": [...]}} (lower-cased items). The game data records one potion seller's menu (Apothecary
# Phillips, Giant Rock City); every town has one, and the game describes nearly all of them alike.
SHOPS_SEEN = "_learned_shops.json"   # in the profiles folder (the player's own, never in a release)
BUY_TRIES = 3           # shops tried, nearest first, before an item is given up as not for sale
_shops_lock = threading.Lock()


def _shop_key(npc: Npc) -> str:
    return "%d|%s" % (npc[0], npc[1].strip().lower())


def _shops_seen() -> dict[str, dict[str, list[str]]]:
    try:
        with open(os.path.join(paths.PROFILES, SHOPS_SEEN), encoding="utf-8") as f:
            got = json.load(f)
        return got if json.is_object(got) else {}
    except (OSError, ValueError):
        return {}


def note_shop(npc: Npc, item: str, sells: bool) -> None:
    """Remember that this shop's menu listed the item (or didn't)."""
    with _shops_lock:
        seen = _shops_seen()
        row = seen.setdefault(_shop_key(npc), {"sells": [], "lacks": []})
        want, other = ("sells", "lacks") if sells else ("lacks", "sells")
        name = item.strip().lower()
        row[other] = [x for x in row.get(other, []) if x != name]
        if name not in row.setdefault(want, []):
            row[want].append(name)
        try:
            with open(os.path.join(paths.PROFILES, SHOPS_SEEN), "w", encoding="utf-8") as f:
                json.dump(seen, f, ensure_ascii=False, indent=1)
        except OSError:
            pass


def shops_for(item: str) -> list[Npc]:
    """Where the item may be bought for arnebia: the shops the game data has seen selling it, and
    every NPC of the kind that sells it (every town's potion seller or grocer) that hasn't turned
    out not to stock it. The nearest one is visited first (buy)."""
    name = item.strip().lower()
    seen = _shops_seen()
    out: list[Npc] = [t[:4] for t in knowledge.traders(item, "sell") if t[5] == "arnebia"]
    role = knowledge.shop_role(item)
    for npc in knowledge.service_npcs(role) if role else []:
        if npc not in out and name not in seen.get(_shop_key(npc), {}).get("lacks", []):
            out.append(npc)
    return out


def _buy_menu(b: Bot, item: str) -> bool:
    """With a shop's menu open: get to the list that names the item. True when it is there."""
    d = b.dialog()
    if not d.get("open") or not d.get("top") or b.find_entry(d["top"], item_pattern(item)) is None:
        # a top menu first ("buy goods", "buy", "shop")
        for section in (r"^\s*buy\b", r"\bbuy goods", r"\bshop\b", r"\bpurchase"):
            if open_section(b, section):
                break
        d = b.dialog()
    if not d.get("open") or not d.get("top") or b.find_entry(d["top"], item_pattern(item)) is None:
        b.note_dialog(d, "looking for %s to buy" % item)
        return False
    return True


def buy(b: Bot, item: str, n: int) -> int:
    """Buy n of item at the nearest shop that sells it: the shops seen selling it and every one of
    the kind that should (a potion seller, a grocer), nearest by travel first. Before, only the one
    shop whose menu the game data held was ever used - a Priest farming Flaming Mountain went to
    Giant Rock City for potions every time. A shop whose menu turns out not to list it is
    remembered and the next nearest tried (BUY_TRIES). Returns how many arrived."""
    shops = shops_for(item)
    if not shops:
        raise Stopped(_("I don't know which shop sells {item}", item=item))
    before = counts(b).get(item.lower(), 0)
    tried: list[Npc] = []
    for _try in range(BUY_TRIES):
        target = nearest(b, None, [s for s in shops if s not in tried])
        if target is None:
            break
        tried.append(target)
        visit(b, None, candidates=[target])
        try:
            if not _buy_menu(b, item):
                note_shop(target, item, False)
                b.log(_("{npc} doesn't sell {item} - trying the next nearest shop", npc=target[1], item=item))
                continue
            note_shop(target, item, True)
            pick(b, item_pattern(item), n)
        finally:
            b.close_dialogs()
        got = counts(b).get(item.lower(), 0) - before
        b.log(_("bought {n} {item}", n=got, item=item))
        stats.moved(b, "bought", item, got)
        return got
    b.log(_("no shop near enough lists {item} - see logs\\dialogs.log", item=item), speak=True)
    return 0


def sell(b: Bot, item: str, n: int | None=None):
    """Sell n (default: all) of item to an NPC the game has shown buying it (best price), or else
    the kind of NPC that should."""
    buyers = [t[:4] for t in sorted(knowledge.traders(item, "buy"),
                                    key=lambda t: (t[5] != "arnebia", -t[4]))]
    role = knowledge.buyer_role(item)
    if not buyers and role is None:
        raise Stopped(_("I don't know who buys {item}", item=item))
    have = counts(b).get(item.lower(), 0)
    if have <= 0:
        return 0
    n = have if n is None else min(n, have)
    visit(b, role, candidates=buyers or None)
    try:
        d = b.dialog()
        if not d.get("open") or not d.get("top") or b.find_entry(d["top"], item_pattern(item)) is None:
            open_section(b, r"\bsell\b")
            d = b.dialog()
        if not d.get("open") or not d.get("top") or b.find_entry(d["top"], item_pattern(item)) is None:
            b.note_dialog(d, "looking for %s to sell" % item)
            b.log(_("{item} isn't on the buyer's list", item=item), speak=True)
            return 0
        pick(b, item_pattern(item), n)
    finally:
        b.close_dialogs()
    sold = have - counts(b).get(item.lower(), 0)
    b.log(_("sold {n} {item}", n=sold, item=item))
    stats.moved(b, "sold", item, sold)
    return sold


# ---------------------------------------------------------------------------------------- the collection
# What gathering brings in, and who buys it. The buyers stand side by side in the capitals.
COLLECTION_BUYERS = {"fish": "fish_buyer", "ore": "ore_buyer", "herb": "herb_buyer"}
# A buyer's row for one kind in the bag: "101 Milkfish; Unit price 670 arnebia"
BUYER_ROW = re.compile(r"^\s*(\d+)\s+(.+?)\s*;\s*Unit price\s+(\d+)\s+arnebia", re.I)


def buyer_rows(b: Bot) -> list[tuple[int, str, int]]:
    """[(count, item, price)] of the open buyer's menu."""
    d = b.dialog()
    out: list[tuple[int, str, int]] = []
    top: dict[str, Any] = d.get("top") or {}
    rows: list[list[Any]] = top.get("rows", []) if d.get("open") else []
    for row in rows:
        for e in row:
            for t in (b.entry_texts(e) if e else []):
                m = BUYER_ROW.match(t)
                if m:
                    out.append((int(m.group(1)), m.group(2).strip(), int(m.group(3))))
    return out


def collection_left(p: dict[str, Any], rows: list[tuple[int, str, int]]) -> list[tuple[str, int]]:
    """[(item, how many to sell)] of a buyer's rows: everything it lists, but never what the player
    told the bag manager to keep, and never below the number they keep of an item."""
    out: list[tuple[str, int]] = []
    for count, item, _price in rows:
        r = bagrules.rule(p, item)
        if r["chosen"] and r["action"] == "keep":
            continue
        n = count - (r["keep"] if r["chosen"] else 0)
        if n > 0:
            out.append((item, n))
    return out


def sell_collection(b: Bot, kinds: tuple[str, ...] = ("fish", "ore", "herb")) -> int:
    """Sell what gathering brought in to the nearest Aquaculture, Ore or Herb Buyer: each lists
    what of its kind the bag holds ("101 Milkfish; Unit price 670 arnebia"), and every row is sold
    (the bag manager's keep rules apart) until it says there is nothing left of its kind. A kind no
    buyer is known for is said and passed over. Returns the arnebia it brought in."""
    earned = 0
    for kind in kinds:
        role = COLLECTION_BUYERS[kind]
        if not knowledge.service_npcs(role):
            b.log(_("I don't know where a {role} is", role=role.replace("_", " ")))
            continue
        before = counts(b).get("arnebia", 0)
        npc = None
        sold_any = 0
        for _round in range(60):
            # a row at a time, the menu opened afresh each time: after a sale its rows are old
            b.close_dialogs()
            if npc is None:
                npc = visit(b, role)
            elif not b.talk(npc):
                break
            b.wait_dialog(3.0)
            rows = buyer_rows(b)
            if not rows:
                break                         # "You have no fish and shrimp to sell"
            todo = collection_left(b.p, rows)
            if not todo:
                break
            item, n = todo[0]
            have = counts(b).get(item.lower(), 0)
            if not pick(b, r"^\s*\d+\s+" + re.escape(item) + r"\s*;\s*Unit price", n):
                b.log(_("the {role} didn't take {item}", role=role.replace("_", " "), item=item))
                break
            gone = have - counts(b).get(item.lower(), 0)
            if gone <= 0:
                break
            sold_any += 1
            b.log(_("sold {n} {item}", n=gone, item=item))
            stats.moved(b, "sold", item, gone)
        b.close_dialogs()
        got = counts(b).get("arnebia", 0) - before
        if sold_any:
            earned += max(0, got)
            b.log(_("sold the {kind} for {n} arnebia", kind=_collection_word(kind), n=max(0, got)), speak=True)
    return earned


def _collection_word(kind: str):
    return {"fish": _("fish"), "ore": _("ore"), "herb": _("herbs")}[kind]


# ---------------------------------------------------------------------------------------- restock
Need = tuple[dict[str, Any], int, int]     # (restock rule, in the bag, wanted)


def needs(b: Bot, rules: Sequence[dict[str, Any]] | None, start: bool = False) -> list[Need]:
    """[(rule, have, want)] for every rule that should be topped up now. While farming a rule
    triggers below its min; at the start of a run (or once a trip is happening anyway) anything
    below its stock is topped up too."""
    c = counts(b)
    out: list[Need] = []
    for r in rules or []:
        item = (r.get("item") or "").strip()
        if not item or not r.get("on", True):
            continue
        have = c.get(item.lower(), 0)
        stock = int(r.get("stock") or 0)
        low = int(r.get("min") or 0)
        if have < low or (start and have < stock):
            out.append((r, have, stock))
    return out


def restock(b: Bot, rules: list[dict[str, Any]], start: bool = False) -> bool:
    """Top up every rule that needs it: storehouse first (free), then the shop. True if a trip was made."""
    todo = needs(b, rules, start)
    told: set[str] = getattr(b, "supply_told", set())
    b.supply_told = told
    # "just tell me" rules never make a trip: say it once, then leave them
    for r, have, _w in todo:
        if bagrules.supply_rule(r) == "tell" and r["item"] not in told:
            told.add(r["item"])
            b.log(_("low on {item}: {have} left", item=r["item"], have=have), speak=True)
    todo = [t for t in todo if bagrules.supply_rule(t[0]) != "tell"]
    if not todo:
        return False
    # once we're going anyway, top up everything that's short
    todo = [t for t in needs(b, rules, True) if bagrules.supply_rule(t[0]) != "tell"]
    b.log(_("restocking: {what}", what=", ".join("%s %d/%d" % (r["item"], h, w) for r, h, w in todo)), speak=True)
    for r, have, want in todo:
        missing = want - have
        if missing <= 0:
            continue
        source = bagrules.supply_rule(r)
        if source == "tell":
            b.log(_("low on {item}: {have} left (the bag manager says only to tell you)", item=r["item"], have=have),
                  speak=True)
            continue
        if source in ("both", "storage"):
            missing -= withdraw(b, r["item"], missing)
        if missing > 0 and source in ("both", "buy"):
            buy(b, r["item"], missing)
    still = [(r["item"], h) for r, h, _w in needs(b, rules, False)]
    if still:
        b.log(_("still short after restocking: {what}", what=", ".join("%s %d" % s for s in still)), speak=True)
    return True


# ---------------------------------------------------------------------------------------- shards
def shard_catalog() -> tuple[dict[str, dict[str, Any]], dict[str, list[float]]]:
    """{shard name: {"price", "makes"}} and {map: [x, y]} of the Shard Markets (data/shards.json,
    built from the market's own menus)."""
    try:
        with open(os.path.join(paths.DATA, "shards.json"), encoding="utf-8") as f:
            d: dict[str, Any] = json.load(f)
        return d.get("shards", {}), d.get("markets", {})
    except (OSError, ValueError):
        return {}, {}


def market_candidates(markets: dict[str, list[float]]) -> list[Npc]:
    """(map, name, x, y) for each Shard Market, under the name the game uses there now: the survey
    found the "Shard Marketplace" renamed "Shard Broker" in every city (2026-10-11), and talk()
    finds an NPC by its exact name."""
    out: list[Npc] = []
    for m, xy in markets.items():
        npcs: list[dict[str, Any]] = knowledge.data().get("npcs", [])
        here = [n for n in npcs
                if n.get("map") == int(m) and n.get("role") == "shard_market"
                and any(src.startswith("survey") for src in n.get("sources", []))]
        best = min(here, key=lambda n: abs(n["x"] - xy[0]) + abs(n["y"] - xy[1]), default=None)
        out.append((int(m), best["name"] if best else "Shard Broker", xy[0], xy[1]))
    return out


def sell_shards(b: Bot, names: Iterable[str], at_most: dict[str, int] | None = None) -> int:
    """Sell shards of the given types at the nearest Shard Market (live menu, seen in our logs:
    talk -> a row per shard "<name> | Unit price N arnebia" -> "buy" / "sell" -> "Please enter the
    quantity to sell, up to 2000"). at_most: {lower-cased name: how many} - the bag rule's "sell N,
    keep the rest". Returns how many kinds were sold."""
    _s, markets = shard_catalog()
    have = counts(b)
    todo: list[tuple[str, int]] = []
    for n in names:
        in_bag = have.get(n.lower(), 0)
        if in_bag > 0:
            todo.append((n, min(in_bag, (at_most or {}).get(n.lower(), in_bag))))
    if not todo:
        return 0
    if not markets:
        b.log(_("I don't know where a Shard Market is"), speak=True)
        return 0
    candidates = market_candidates(markets)
    sold = 0
    for name, n in todo:
        for _try in range(2):
            d = b.dialog()
            if not d["open"] or not d.get("top") or b.find_entry(d["top"], item_pattern(name)) is None:
                b.close_dialogs()
                visit(b, None, candidates=candidates)
                d = b.dialog()
            if d.get("top") and b.find_entry(d["top"], item_pattern(name)) is not None:
                break
        else:
            b.log(_("the Shard Market didn't list {item}", item=name))
            continue
        before = counts(b).get(name.lower(), 0)
        if b.choose(item_pattern(name)) is None or not b.wait_dialog(3.0):
            continue
        left = n
        while left > 0:                      # the box takes at most 2000 at a time
            step = min(left, 2000)
            if not pick(b, r"\bsell\b", step):
                break
            left -= step
            if left > 0 and (b.choose(item_pattern(name)) is None or not b.wait_dialog(3.0)):
                break
        gone = before - counts(b).get(name.lower(), 0)
        if gone > 0:
            sold += 1
            b.log(_("sold {n} {item} at the Shard Market", n=gone, item=name))
            stats.moved(b, "sold", name, gone)
    b.close_dialogs()
    return sold


# ---------------------------------------------------------------------------------------- gems
GEM_SHARD = re.compile(r"^\s*Level (\d+) (.+?) Shard\s*$", re.I)
SHARDS_PER_GEM = 4      # the game's own instructions: four shards of a colour and level make a gem


def gem_of(shard: str | None) -> str | None:
    """("Level 1 Obsidian Shard") -> "Level 1 Obsidian"; None for anything else (Enhance Crystal
    Shards make crystals, not gems)."""
    m = GEM_SHARD.match(shard or "")
    return "Level %s %s" % (m.group(1), m.group(2)) if m else None


def menu_text(b: Bot) -> str:
    d = b.dialog()
    top: dict[str, Any] = d.get("top") or {}
    return " | ".join(t for row in top.get("rows", []) for e in row if e for t in b.entry_texts(e))


def _open_shard_synthesis(b: Bot, opener: Callable[[], object] | None = None) -> bool:
    """The Gem Craftsman's shard list, open. Reuses an open one, else opens one: opener(), or the
    nearest Gem Craftsman."""
    for _try in range(2):
        d = b.dialog()
        top = d.get("top") if d["open"] else None
        if top and b.find_entry(top, r"^\s*shard synthesis") is not None:
            b.choose(r"^\s*shard synthesis")
            return bool(b.wait_dialog(3.0))
        if top and b.find_entry(top, r"Shard\s*\d+\s*$") is not None:
            return True                       # already in the shard list
        b.close_dialogs()
        if opener:
            opener()
        else:
            visit(b, "gem_craftsman")
    return False


def make_gems(b: Bot, wanted: dict[str, int], opener: Callable[[], object] | None = None) -> int:
    """Turn shards into gems at the Gem Craftsman ("shard synthesis"), for the Gem Maker
    (engine/crafting.py). wanted: {shard name: how many of it may be used}. Four shards make one gem
    of their level and this never fails; leftovers (fewer than four) stay in the bag. The menu is read live: the
    shard list ("Level 1 Obsidian Shard42"), then the craftsman's own line "synthesize 1 Level 1
    Obsidian needs 4 Level 1 Obsidian Shard; fee N arnebia" and the "please enter" box, so the
    number of shards and the fee come from the game, not from here. Returns the gems made."""
    have = counts(b)
    spelled = {(r.get("name") or "").strip().lower(): (r.get("name") or "").strip() for r in b.bag_rows()}
    todo: list[tuple[str, str, int]] = []
    for name, n in wanted.items():
        name = spelled.get(name.strip().lower(), name.strip())      # the bag's own spelling
        gem = gem_of(name)
        n = min(n, have.get(name.lower(), 0))
        if gem and n >= SHARDS_PER_GEM:
            todo.append((name, gem, n))
    if not todo:
        return 0
    b.log(_("making gems: {list}", list=", ".join("%d %s" % (n, s) for s, _g, n in todo)), speak=True)
    made = 0
    for shard, gem, n in todo:
        if not _open_shard_synthesis(b, opener):
            b.log(_("couldn't open the Gem Craftsman's shard synthesis"), speak=True)
            break
        row = r"^\s*" + re.escape(shard) + r"\s*\d+\s*$"
        if b.choose(row) is None or not b.wait_dialog(3.0):
            b.log(_("the Gem Craftsman didn't list {item}", item=shard))
            continue
        # some shards first ask which level to make ("synthesize Level 3 ..."): take this shard's own
        top = b.dialog().get("top")
        if top and b.find_entry(top, r"^\s*synthesize Level \d") is not None:
            if b.choose(r"^\s*synthesize\s+" + re.escape(gem) + r"\s*$") is None or not b.wait_dialog(3.0):
                b.log(_("the Gem Craftsman offered no way to make {gem}", gem=gem))
                continue
        text = menu_text(b)
        need = re.search(r"needs\s+(\d+)", text, re.I)
        need = int(need.group(1)) if need else SHARDS_PER_GEM
        fee = re.search(r"fee\s+(\d+)\s+arnebia", text, re.I)
        fee = int(fee.group(1)) if fee else 0
        b.p.setdefault("gem_fees", {})[gem.lower()] = fee      # so shards are only bought when the fee is affordable too
        k = n // max(1, need)
        if fee:
            spare = counts(b).get("arnebia", 0) - int(b.p.get("gem_keep_arnebia", 20000))
            k = min(k, max(0, spare) // fee)
        if k <= 0:
            b.log(_("not enough spare arnebia to make {gem} (fee {fee} each)", gem=gem, fee=fee), speak=True)
            b.close_dialogs()
            continue
        before = counts(b).get(gem.lower(), 0)
        if not pick(b, r"please enter|enter quantity|enter the number", k):
            b.log(_("the Gem Craftsman didn't ask how many {gem} to make", gem=gem))
            b.close_dialogs()
            continue
        b.sleep(0.6)
        got = counts(b).get(gem.lower(), 0) - before
        made += max(0, got)
        b.log(_("made {n} {gem} from {used} {shard}", n=got, gem=gem, used=got * need, shard=shard))
    b.close_dialogs()
    return made



def load_now(b: Bot) -> int | None:
    """The bag's load right now (opens and closes the character sheet), or None."""
    sheet: dict[str, Any] = b.refresh_sheet(force=True) or {}
    return sheet.get("load") if sheet.get("maxload", 0) > 0 else None


def unload(b: Bot) -> int:
    """A bag trip by the bag manager's rules (engine/bagrules.py): for each item, keep / store /
    sell / throw away, keeping the number the player set. Shards are sold at the Shard Market,
    other things to an NPC the game has shown buying them; what can't be sold is stored instead.
    Every action is weighed (sheet load before and after) so the bag manager learns item weights.
    Returns how many kinds of item left the bag."""
    scan: dict[str, Any] = b.p.get("scan") or {}
    hotkeys: list[dict[str, Any]] = scan.get("hotkeys", [])
    hot: list[str] = [h["item"] for h in hotkeys if h.get("item")]
    bag: dict[str, int] = {}
    for r in b.bag_rows():
        if r.get("name") and r.get("count"):
            bag[r["name"]] = bag.get(r["name"], 0) + int(r["count"])
    start: dict[str, Any] = getattr(b, "bag_start", None) or {}
    gained = {k.lower(): v - start.get(k.lower(), 0) for k, v in bag.items()}
    todo = bagrules.plan(b.p, bag, hot, gained)
    if not todo:
        b.log(_("the bag is heavy, but no rule says to store, sell or throw anything in it - "
                "open the bag manager to choose"), speak=True)
        return 0
    b.log(_("bag trip: {items}", items=", ".join("%s %d %s" % (a, n, k) for k, a, n in todo)), speak=True)
    done = 0
    _shards, _markets = shard_catalog()
    shard_names = {n.lower() for n in _shards}
    # 1. shards in one visit to the market
    to_market = [(k, n) for k, a, n in todo if a == "sell" and k.lower() in shard_names]
    for name, n in to_market:
        if _weighed(b, name, lambda name=name: _sell_one_shard(b, name, n)):
            done += 1
    # 2. other sales to NPC buyers; what nobody buys falls back to the storehouse
    store = [(k, n) for k, a, n in todo if a == "store"]
    done += _sell_to_buyers(b, [(k, n) for k, a, n in todo if a == "sell" and k.lower() not in shard_names], store)
    # 3. the storehouse
    for name, n in store:
        if _try_trip(b, _("couldn't store {item}: {why}"), name,
                     lambda name=name, n=n: _weighed(b, name, lambda: deposit(b, name, n))):
            done += 1
    # 4. throwing away, last, only what the player said
    for name, a, n in todo:
        if a == "throw":
            if _weighed(b, name, lambda name=name, n=n: b.throw_item(name, n)):
                done += 1
    return done


def _weighed(b: Bot, name: str, action: Callable[[], int]) -> int:
    """Do it, and learn the item's weight from the load before and after."""
    before = load_now(b)
    moved = action()
    if moved:
        w = bagrules.learn_weight(name, before, load_now(b), moved)
        if w is not None:
            b.log(_("{item} weighs {w} each", item=name, w=w))
    return moved


def _try_trip(b: Bot, failed: str, name: str, action: Callable[[], int]) -> int | None:
    """action(); a Stopped from it is said (failed: "couldn't ... {item}: {why}") and passed over,
    unless the player pressed Stop."""
    try:
        return action()
    except Stopped as e:
        if b.stop_event.is_set():
            raise
        b.log(failed.format(item=name, why=e))
        return None


def _sell_to_buyers(b: Bot, sales: Iterable[tuple[str, int]], store: list[tuple[str, int]]) -> int:
    """Sell to NPC buyers; what nobody buys (or that didn't sell) goes onto store instead."""
    done = 0
    for name, n in sales:
        if bagrules.buyer(name) or knowledge.buyer_role(name):
            if _try_trip(b, _("couldn't sell {item}: {why}"), name,
                         lambda name=name, n=n: _weighed(b, name, lambda: sell(b, name, n))):
                done += 1
                continue
        b.log(_("nobody I know buys {item} - storing it instead", item=name))
        store.append((name, n))
    return done


def _sell_one_shard(b: Bot, name: str, n: int) -> int:
    before = counts(b).get(name.lower(), 0)
    sell_shards(b, [name], at_most={name.lower(): n})
    return before - counts(b).get(name.lower(), 0)
