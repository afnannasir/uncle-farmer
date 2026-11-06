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
from collections.abc import Callable, Iterable, Mapping, Sequence
from typing import Any

from .. import atlas, jsonio as json, knowledge, paths, stats, travel
from ..clock import clock_for
from ..i18n import _
from . import bagrules, menus
from .bot import CONFIRM, Bot, Stopped
from .botparts.travel import ways_out
from .transactions import InventoryTransaction


NUMBER_AGENT = 56
INPUT_POLL = 0.025


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
    """The NPC (of that role, or from candidates) cheapest to reach from here: (map, name, x, y).

    From inside a map we can't plan our way out of (a gang base, a dungeon) every other map costs
    the same unknown "leave", so the first NPC in the data's order used to win: a Gem Maker in the
    gang base entered from central plains went on to Mario Basin's craftsman. There, an NPC on
    another map is judged from where the way out puts us, when that is known (ways_out)."""
    here = b.map_id()
    me = b.me()
    out = ways_out(b).get(here) if travel.is_instance(here) else None
    best: tuple[float, Npc] | None = None
    rows = b.bag_rows()
    purse = travel.arnebia_in(rows)
    for mid, name, x, y in (candidates if candidates is not None else knowledge.service_npcs(role or "")):
        start_map, start_cell, extra = here, (me["x"], me["y"]), 0.0
        if out is not None and mid != here:
            start_map, start_cell, extra = out[0], out[1], travel.LEAVE_COST
        legs, cost = travel.plan(start_map, start_cell, mid, (x, y + 1), with_cost=True,
                                 transport=b.p.get("use_transport", True), vip=b.p.get("vip", False),
                                 avoid=travel.kept_off(b.p),
                                 bag=rows if b.p.get("use_travel_items", False) else [],
                                 hearth_home=b.p.get("hearth_home") or None, arnebia=purse)
        if legs is None or cost is None:
            continue
        cost += extra
        if best is None or cost < best[0]:
            best = (cost, (mid, name, x, y))
    return best[1] if best else None


def _step_out_first(b: Bot, target: Npc | None) -> bool:
    """In a gang base whose way out we haven't seen yet (the run started inside it), with the chosen
    NPC on another map: walk out first, so the nearest one is chosen from where we come out rather
    than by the data's order. (Dungeons are left as travel plans it: a travel item may beat the
    Action menu there.) True when we left (choose again)."""
    here = b.map_id()
    if target is None or target[0] == here or travel.gang_exit(here) is None or here in ways_out(b):
        return False
    leave = getattr(b, "leave_here", None)
    if leave is None:
        return False
    b.log(_("leaving {map} first, to find the nearest from outside", map=atlas.label(here)))
    return bool(leave()) and b.map_id() != here


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
    if not name and _step_out_first(b, target):
        target = nearest(b, role, candidates or None)
    if target is None and candidates:
        raise Stopped(_("none of the {n} places that could do it can be reached from {map}",
                        n=len(candidates), map=atlas.label(b.map_id())))
    if target is None:
        raise Stopped(_("I don't know where to find a {role} - visit one with the agent running first",
                        role=(role or name or "").replace("_", " ")))     # a named NPC has no role
    mid, npc, x, y = target
    b.log(_("going to {npc} on {map}", npc=npc, map=atlas.label(mid)))
    stats.reading(b)            # the results: loot before the trip, fares on it, the trade after it
    b.travel(mid)
    stats.reading(b)
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
        b.sleep(INPUT_POLL)
    return False


def wait_menu_change(b: Bot, before: dict[str, Any] | None) -> None:
    """Use the state-aware wait when the bot has it, preserving the older fallback otherwise."""
    waiter = getattr(b, "wait_menu_change", None)
    if callable(waiter):
        waiter(before)
    else:
        b.sleep(0.4)


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
    before_menu = b.dialog().get("top")
    if int(getattr(b, "agent_version", 0) or 0) >= NUMBER_AGENT:
        answer = b.a.send("number %d" % int(n))
        if not answer.startswith("entered"):
            b.log(_("couldn't type into the game: {why}", why=answer), speak=True)
            return False
        if not wait_input(b, False):
            b.log(_("the game didn't take the number"), speak=True)
            return False
        wait_menu_change(b, before_menu)
        return True
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
    wait_menu_change(b, before_menu)
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
def in_town(b: Bot, npcs: Sequence[Npc]) -> list[Npc]:
    """The NPCs of a restock trip's town (b.restock_town, a map id) - all of them outside a trip:
    a trip never goes on from one town to another for its errands."""
    town_map: int | None = getattr(b, "restock_town", None)
    return list(npcs) if town_map is None else [n for n in npcs if n[0] == town_map]


def withdraw(b: Bot, item: str, n: int):
    """Take up to n of item out of the storehouse (the trip's town's, during a restock). Returns
    how many arrived in the bag, counted at the storehouse: a potion drunk on the way there is not a
    potion the storehouse failed to hand over (that used to send the trip on to a shop)."""
    houses = in_town(b, knowledge.service_npcs("storehouse"))
    if not houses:
        b.log(_("no storehouse in {map} - not taking {item} out", map=atlas.label(getattr(b, "restock_town", 0) or 0),
                item=item))
        return 0
    visit(b, "storehouse", candidates=houses)
    before = counts(b).get(item.lower(), 0)
    try:
        if not open_section(b, r"^\s*item storage"):
            b.log(_("the storehouse menu has no item storage"), speak=True)
            return 0
        d = b.dialog()
        if b.find_entry(d["top"], item_pattern(item)) is None:
            b.log(_("no {item} in the storehouse", item=item))
            b.__dict__.setdefault("storehouse_lacks", set()).add(item.strip().lower())
            return 0
        pick(b, item_pattern(item), n)
    finally:
        b.close_dialogs()
    got = max(0, counts(b).get(item.lower(), 0) - before)
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
    if put > 0:
        b.__dict__.setdefault("storehouse_lacks", set()).discard(item.strip().lower())   # it has some now
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
    shops = in_town(b, shops)
    if not shops:
        b.log(_("no shop in {map} sells {item} - not going to another town for it",
                map=atlas.label(getattr(b, "restock_town", 0) or 0), item=item), speak=True)
        return 0
    before = counts(b).get(item.lower(), 0)
    tried: list[Npc] = []
    for _try in range(BUY_TRIES):
        target = nearest(b, None, [s for s in shops if s not in tried])
        if target is None:
            break
        tried.append(target)
        visit(b, None, candidates=[target])
        try:
            # a potion seller's verified list: the item by its index, no walk down the list
            if menus.pick(b, menus.POTION_SHOP, name=item) is not None:
                note_shop(target, item, True)
                type_number(b, n)
            elif not _buy_menu(b, item):
                note_shop(target, item, False)
                b.log(_("{npc} doesn't sell {item} - trying the next nearest shop", npc=target[1], item=item))
                continue
            else:
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
        transaction = InventoryTransaction.begin(b)
        before = transaction.before.get("arnebia", 0)
        attempted: set[str] = set()
        for _round in range(60):
            if _round == 0:
                visit(b, role)
                if not b.wait_dialog(3.0):
                    break
            rows = buyer_rows(b)
            todo = [(item, n) for item, n in collection_left(b.p, rows) if item.lower() not in attempted]
            if not todo:
                break
            item, n = todo[0]
            if not pick(b, r"^\s*\d+\s+" + re.escape(item) + r"\s*;\s*Unit price", n):
                b.log(_("the {role} didn't take {item}", role=role.replace("_", " "), item=item))
                break
            attempted.add(item.lower())
            transaction.expect(item, -n)
        b.close_dialogs()
        after = transaction.settle(b) if transaction.expected else transaction.before
        moved = transaction.removed(after)
        got = after.get("arnebia", 0) - before
        for item, n in moved:
            b.log(_("sold {n} {item}", n=n, item=item))
            stats.moved(b, "sold", item, n)
        if moved:
            earned += max(0, got)
            b.log(_("sold the {kind} for {n} arnebia", kind=_collection_word(kind), n=max(0, got)), speak=True)
    return earned


def _collection_word(kind: str):
    return {"fish": _("fish"), "ore": _("ore"), "herb": _("herbs")}[kind]


# ---------------------------------------------------------------------------------------- restock
Need = tuple[dict[str, Any], int, int]     # (restock rule, in the bag, wanted)


RESTOCK_RETRY = 1800.0   # seconds before a restock that got nothing is tried again (or sooner: more arnebia)


def needs(b: Bot, rules: Sequence[dict[str, Any]] | None, start: bool = False) -> list[Need]:
    """[(rule, have, want)] for every rule that should be topped up now. A rule triggers once the
    item is down to its min ("refill when fewer than": 30 means 30 or fewer); its stock ("fill up
    to") is only how far a trip fills it. With start (an explicit restock, or a trip happening
    anyway) anything below its stock is topped up too - never a reason to leave on its own: 199 of
    200 above a min of 30 is no trip."""
    c = counts(b)
    out: list[Need] = []
    for r in rules or []:
        item = (r.get("item") or "").strip()
        if not item or not r.get("on", True):
            continue
        have = c.get(item.lower(), 0)
        stock = int(r.get("stock") or 0)
        low = int(r.get("min") or 0)
        if (low > 0 and have <= low or start) and have < stock:
            out.append((r, have, stock))
    return out


def _sources(r: dict[str, Any]) -> tuple[bool, bool]:
    """(from the storehouse?, from a shop?) for a rule."""
    source = bagrules.supply_rule(r)
    return source in ("both", "storage"), source in ("both", "buy")


def possible(b: Bot, r: dict[str, Any], why: list[str]) -> bool:
    """Whether a trip could get any of this rule's item: the storehouse isn't known to be out of it,
    or there is arnebia to buy it; and a trip that got none of it lately (RESTOCK_RETRY) isn't
    made again before more arnebia has come in. Why not goes into why."""
    item = r["item"].strip().lower()
    store, shop = _sources(r)
    bag = counts(b)
    money = bag.get(stats.ARNEBIA)          # None: the bag shows no arnebia row - not known to be broke
    failed: dict[str, tuple[float, int]] = getattr(b, "restock_failed", {})
    last = failed.get(item)
    now = clock_for(b).time()
    if last is not None and now - last[0] < RESTOCK_RETRY and (money or 0) <= last[1]:
        why.append(_("the last trip for {item} got none, and nothing has changed since", item=r["item"]))
        return False
    lacks: set[str] = getattr(b, "storehouse_lacks", set())
    from_store = store and item not in lacks
    from_shop = shop and (money is None or money > 0)
    if from_store or from_shop:
        return True
    if store and not shop:
        why.append(_("the storehouse has no {item}", item=r["item"]))
    elif shop and money is not None and money <= 0:
        why.append(_("no arnebia to buy {item}", item=r["item"]))
    else:
        why.append(_("the storehouse has no {item} and there is no arnebia to buy it", item=r["item"]))
    return False


def restock_town(b: Bot, todo: Sequence[Need]) -> int | None:
    """The one town (a map) a restock trip goes to: the nearest that has what the trip needs - a
    storehouse for the rules that take from it, a shop of the right kind for those that buy -
    preferring one with all of it. The town we are in first, whatever else is near. None when no
    town has any of it."""
    want_store = any(_sources(r)[0] for r, _h, _w in todo)
    shop_items = [r["item"] for r, _h, _w in todo if _sources(r)[1]]
    towns: dict[int, list[Npc]] = {}
    for npc in (knowledge.service_npcs("storehouse") if want_store else []):
        towns.setdefault(npc[0], []).append(npc)
    for item in shop_items:
        for npc in shops_for(item):
            towns.setdefault(npc[0], []).append(npc)
    if not towns:
        return None

    def covers(mid: int) -> int:
        here = towns[mid]
        store = want_store and any(n in knowledge.service_npcs("storehouse") for n in here)
        shops = sum(1 for item in shop_items if any(n in here for n in shops_for(item)))
        return int(store) + shops

    best = max(covers(m) for m in towns)
    full = [m for m in towns if covers(m) == best]
    here = b.map_id()
    if here in full:
        return here
    near = nearest(b, None, [npc for m in full for npc in towns[m]])
    return near[0] if near else None


def restock(b: Bot, rules: list[dict[str, Any]], start: bool = False) -> bool:
    """Top up every rule that needs it: storehouse first (free), then the shop, all in one town
    (restock_town) - never on to another town. A rule that can't be met (no arnebia, the
    storehouse out of it, a trip that got none) makes no trip, and says why once. True if a trip
    was made."""
    todo = needs(b, rules, start)
    told: set[str] = getattr(b, "supply_told", set())
    b.supply_told = told
    # "just tell me" rules never make a trip: say it once, then leave them
    for r, have, _w in todo:
        if bagrules.supply_rule(r) == "tell" and r["item"] not in told:
            told.add(r["item"])
            b.log(_("low on {item}: {have} left", item=r["item"], have=have), speak=True)
    todo = [t for t in todo if bagrules.supply_rule(t[0]) != "tell"]
    skipped: set[str] = getattr(b, "restock_skipped", set())
    b.restock_skipped = skipped
    able: list[Need] = []
    for t in todo:
        why: list[str] = []
        if possible(b, t[0], why):
            able.append(t)
            skipped.discard(t[0]["item"])
        elif t[0]["item"] not in skipped:
            skipped.add(t[0]["item"])
            b.log(_("not restocking {item}: {why}", item=t[0]["item"], why=why[0] if why else "?"), speak=True)
    if not able:
        return False
    # once we're going anyway, top up everything that's short (and can be had)
    todo = [t for t in needs(b, rules, True) if bagrules.supply_rule(t[0]) != "tell" and possible(b, t[0], [])]
    where = restock_town(b, todo)
    what = ", ".join("%s %d/%d" % (r["item"], h, w) for r, h, w in todo)
    if where is None:
        # no town found from here by the data: the errands look for their NPCs themselves
        b.log(_("restocking: {what}", what=what), speak=True)
    else:
        b.log(_("restocking in {map}: {what}", map=atlas.label(where), what=what), speak=True)
    b.restock_town = where
    try:
        for r, have, want in todo:
            got = _fill(b, r, want - have)
            item = r["item"].strip().lower()
            if got <= 0 and counts(b).get(item, 0) <= int(r.get("min") or 0):
                b.__dict__.setdefault("restock_failed", {})[item] = (clock_for(b).time(),
                                                                     counts(b).get(stats.ARNEBIA) or 0)
            elif got > 0:
                getattr(b, "restock_failed", {}).pop(item, None)
    finally:
        b.restock_town = None
    still = [(r["item"], h) for r, h, _w in needs(b, rules, False)]
    if still:
        b.log(_("still short after restocking: {what}", what=", ".join("%s %d" % s for s in still)), speak=True)
    return True


def _fill(b: Bot, r: dict[str, Any], missing: int) -> int:
    """Withdraw, then buy what is still missing, as the rule's source allows. How many came in."""
    if missing <= 0:
        return 0
    store, shop = _sources(r)
    got = 0
    if store:
        got += withdraw(b, r["item"], missing)
    if missing - got > 0 and shop:
        got += buy(b, r["item"], missing - got) or 0
    return got


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
    finds an NPC by its exact name.

    Every city's market, not only those in shards.json: that file holds the cities whose market
    menu was read (its prices), and leaving the others out sent a character in Mario Basin or
    Holy Blood City to sell shards in another city past their own market."""
    out: list[Npc] = []
    npcs: list[dict[str, Any]] = knowledge.data().get("npcs", [])

    def surveyed(n: dict[str, Any]) -> bool:
        return any(str(src).startswith("survey") for src in n.get("sources", []))

    for m, xy in markets.items():
        here = [n for n in npcs if n.get("map") == int(m) and n.get("role") == "shard_market" and surveyed(n)]
        best = min(here, key=lambda n: abs(n["x"] - xy[0]) + abs(n["y"] - xy[1]), default=None)
        out.append((int(m), best["name"] if best else "Shard Broker", xy[0], xy[1]))
    seen = {npc[0] for npc in out}
    for mid, name, x, y in knowledge.service_npcs("shard_market"):
        if mid in seen or atlas.kind(mid) == "gang":
            continue
        seen.add(mid)
        known = next((n for n in npcs if n.get("map") == mid and n.get("name") == name), None)
        out.append((mid, name if known is not None and surveyed(known) else "Shard Broker", x, y))
    return out


TRADE_MOST = 2000      # the Shard Market's "how many" box takes at most this many at a time
MARKET_OPEN = 0.3      # a broker's fixed market list follows their conversation promptly


def market_lists(b: Bot, name: str, wait: float = 0.0) -> bool:
    """The Shard Market's list is open and has the shard: by the verified layout when the agent
    reads it (agent 53), else by the entries' text. `wait` gives the completed previous trade a
    moment to show the market after opening a conversation."""
    if menus.find(b, menus.SHARD_MARKET, name=name, wait=wait) is not None:
        return True
    d = b.dialog()
    return bool(d["open"] and d.get("top") and b.find_entry(d["top"], item_pattern(name)) is not None)


def shard_trade(b: Bot, name: str, action: str, n: int,
                reopen: Callable[[], bool] | None = None) -> int:
    """With the Shard Market's list open: buy or sell n of a shard, in steps the "how many" box
    takes (TRADE_MOST); the list opens again after each. Each step goes by the verified index when
    the agent can (the shard's row, then the buy or sell row of that same shard's id), else by the
    entries' text as before. A step that began by index never goes on by text: a buy/sell menu
    that isn't the one for the shard just chosen is left unanswered, so the wrong shard is never
    bought or sold. Returns how many were asked for in the steps taken."""
    done = 0
    while done < n:
        # A completed sale closes the broker in the real client. Reopen the fixed market before
        # another 2,000-item batch instead of waiting for a list that cannot return by itself.
        if done and reopen is not None and not reopen():
            break
        step = min(n - done, TRADE_MOST)
        item_id = menus.item_id(b, menus.SHARD_MARKET, name)
        if item_id is not None and menus.shard_sell(b, item_id, step):
            done += step
            continue
        # Each choice is direct (the agent verifies the fixed market's item id and row), so do
        # not pause after it.  The next direct choice waits for the game's real next menu instead.
        listed = menus.pick(b, menus.SHARD_MARKET, name=name, settle=False)
        if listed is not None:
            if menus.pick(b, menus.SHARD_TRADE, item_id=int(listed["id"]), action=action, wait=3.0,
                          then_text=False, settle=False) is None:
                b.log(_("the Shard Market's buy and sell menu for {item} didn't come up as expected - "
                        "nothing chosen there, see logs\\dialogs.log", item=name), speak=True)
                b.close_dialogs()
                break
            if not type_number(b, step):
                break
        else:
            if b.choose(item_pattern(name)) is None or not b.wait_dialog(3.0):
                break
            if not pick(b, r"\b%s\b" % action, step):
                break
        done += step
    return done


def _sell_shard_batch(b: Bot, todo: list[tuple[str, int]], transaction: InventoryTransaction,
                      open_market: Callable[[str], bool]) -> list[tuple[str, int]] | None:
    """Sell one verified plan in the agent. None leaves the ordinary sequential path in charge."""
    if not open_market(todo[0][0]):
        return None
    ids = menus.market_item_ids(b, [name for name, _n in todo])
    plan = list(zip(ids or [], [n for _name, n in todo]))
    if ids is None or not menus.shard_sell_all(b, plan):
        return None
    for name, n in todo:
        transaction.expect(name, -n)
    b.close_dialogs()
    return transaction.removed(transaction.settle(b))


def _sell_shard_steps(b: Bot, todo: list[tuple[str, int]], transaction: InventoryTransaction,
                      open_market: Callable[[str], bool]) -> list[tuple[str, int]]:
    """The compatible per-shard path, used when an agent cannot take the batch transaction."""
    for name, n in todo:
        if not open_market(name):
            b.log(_("the Shard Market didn't list {item}", item=name))
            continue
        transaction.expect(name, -shard_trade(b, name, "sell", n, reopen=lambda name=name: open_market(name)))
    b.close_dialogs()
    return transaction.removed(transaction.settle(b)) if transaction.expected else []


def sell_shards(b: Bot, names: Iterable[str], at_most: dict[str, int] | None = None) -> int:
    """Sell shards of the given types at the nearest Shard Market (live menu, seen in our logs:
    talk -> a row per shard "<name> | Unit price N arnebia" -> "buy" / "sell" -> "Please enter the
    quantity to sell, up to 2000"). at_most: {lower-cased name: how many} - the bag rule's "sell N,
    keep the rest". Returns how many kinds were sold."""
    _s, markets = shard_catalog()
    transaction = InventoryTransaction.begin(b)
    have = transaction.before
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
    broker: str | None = None

    def open_market(name: str) -> bool:
        """Open the broker's fixed market only when it is not already on screen."""
        nonlocal broker
        # An indexed pick validates the entire fixed layout atomically. Avoid an extra `menu`
        # request when a current agent already reports that some dialog is open; it will refuse
        # safely if it is not the market. Older agents still identify the item by menu text here.
        if menus.can_pick(b):
            if b.dialog().get("open"):
                return True
        elif market_lists(b, name):
            return True
        b.close_dialogs()
        if broker is None:
            broker = visit(b, None, candidates=candidates)
        elif not b.talk(broker):
            return False
        return market_lists(b, name, MARKET_OPEN)

    # A whole market plan is faster than one controller command per shard. The agent validates
    # every row and sell menu itself; statistics still use the final bag reconciliation below.
    moved = _sell_shard_batch(b, todo, transaction, open_market)
    if moved is not None:
        for name, gone in moved:
            b.log(_("sold {n} {item} at the Shard Market", n=gone, item=name))
            stats.moved(b, "sold", name, gone)
        return len(moved)

    moved = _sell_shard_steps(b, todo, transaction, open_market)
    for name, gone in moved:
        b.log(_("sold {n} {item} at the Shard Market", n=gone, item=name))
        stats.moved(b, "sold", name, gone)
    return len(moved)


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


# ---------------------------------------------------------------------------------------- Tools: by hand
def _bag_and_hotkeys(b: Bot) -> tuple[dict[str, int], list[str]]:
    """{item as the bag spells it: count} and the items on hotkeys, as a bag trip reads them."""
    scan: dict[str, Any] = b.p.get("scan") or {}
    hotkeys: list[dict[str, Any]] = scan.get("hotkeys", [])
    bag: dict[str, int] = {}
    for r in b.bag_rows():
        if r.get("name") and r.get("count"):
            bag[r["name"]] = bag.get(r["name"], 0) + int(r["count"])
    return bag, [h["item"] for h in hotkeys if h.get("item")]


def shards_to_sell(b: Bot) -> tuple[list[tuple[str, int]], list[str]]:
    """What Tools, Sell shards now would sell: [(shard, how many)] that the bag manager says to sell
    (keeping the number its rule keeps) and the Shard Market buys; and the shards set to sell that
    the market doesn't list (not sold, said)."""
    bag, hot = _bag_and_hotkeys(b)
    market, _m = shard_catalog()
    buys = {n.lower() for n in market}
    sell: list[tuple[str, int]] = []
    other: list[str] = []
    for name, action, n in bagrules.plan(b.p, bag, hot, None):
        if action != "sell" or "shard" not in name.lower():
            continue
        if name.lower() in buys:
            sell.append((name, n))
        else:
            other.append(name)
    return sell, other


def sell_shards_now(b: Bot) -> int:
    """Tools, Sell shards now: sell the shards the bag manager says to sell at the nearest Shard
    Market - the one in this city when it has one - and nothing else (the bag trip's shard step on
    its own). Says what was sold and what wasn't. Returns how many kinds were sold."""
    todo, other = shards_to_sell(b)
    for name in other:
        b.log(_("not selling {item}: the Shard Market doesn't buy it", item=name))
    if not todo:
        b.log(_("no shards to sell: nothing in the bag is on the bag manager's sell list"), speak=True)
        return 0
    before = counts(b)
    b.log(_("selling shards: {list}", list=", ".join("%d %s" % (n, s) for s, n in todo)))
    sell_shards(b, [s for s, _n in todo], at_most={s.lower(): n for s, n in todo})
    after = counts(b)
    sold = [(s, before.get(s.lower(), 0) - after.get(s.lower(), 0)) for s, _n in todo]
    done = [(s, k) for s, k in sold if k > 0]
    left = [s for s, k in sold if k <= 0]
    if left:
        b.log(_("not sold: {list}", list=", ".join(left)), speak=True)
    if done:
        b.log(_("sold {list}; {n} arnebia came in", list=", ".join("%d %s" % (k, s) for s, k in done),
                n=max(0, after.get(stats.ARNEBIA, 0) - before.get(stats.ARNEBIA, 0))), speak=True)
    return len(done)


def throw_list(p: dict[str, Any]) -> list[tuple[str, int]]:
    """The bag manager's throw-away list: [(item, how many it keeps)], only what the player put
    there themselves."""
    rules: dict[str, object] = p.get("bag_rules") or {}
    return sorted((name, int(r.get("keep") or 0)) for name, r in rules.items()
                  if json.is_object(r) and r.get("action") == "throw")


def clean_bag(b: Bot) -> int:
    """Tools, Clean the bag now: throw away, right where we stand, what the bag manager's
    throw-away list says (keeping the number each rule keeps) - only what is on that list, never
    anything else. Says what went, what couldn't be thrown and what wasn't there. Returns how many
    kinds of item left the bag."""
    listed = {name for name, _keep in throw_list(b.p)}
    if not listed:
        b.log(_("the throw-away list is empty - choose items in Character, Bag and supplies"), speak=True)
        return 0
    bag, hot = _bag_and_hotkeys(b)
    if not bag:
        b.log(_("the bag is empty - nothing to throw away"), speak=True)
        return 0
    todo = [(name, n) for name, action, n in bagrules.plan(b.p, bag, hot, None)
            if action == "throw" and name.lower() in listed and bagrules.rule(b.p, name)["chosen"]]
    if not todo:
        b.log(_("nothing in the bag is on the throw-away list (or only what its rules keep)"), speak=True)
        return 0
    thrown: list[tuple[str, int]] = []
    failed: list[str] = []
    for name, n in todo:
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        gone = b.throw_item(name, n)
        if gone > 0:
            thrown.append((name, gone))
        else:
            failed.append(name)
    if failed:
        b.log(_("couldn't throw away: {list}", list=", ".join(failed)), speak=True)
    if thrown:
        b.log(_("threw away {list}", list=", ".join("%d %s" % (k, s) for s, k in thrown)), speak=True)
    return len(thrown)


# ---------------------------------------------------------------------------------------- shard prices
# The Shard Market's prices change; data/shards.json keeps the last ones read, which the bag manager
# and the results use. Tools, Update shard prices reads the market's list again.
SHARDS_FILE = "shards.json"
PRICE_MOST = 100_000_000          # no shard has cost anywhere near this: a reading above it is wrong
SHOWN_PRICE = re.compile(r"\bunit price\s+(\d{1,9})\s+arnebia", re.I)


class PriceReading:
    """What the market's list gave: valid prices by the name shards.json uses, and what was left out."""

    def __init__(self) -> None:
        self.prices: dict[str, int] = {}
        self.bad: list[str] = []          # rows that were read but not used, with why
        self.unknown: list[str] = []      # shards the list shows that shards.json doesn't have


def read_market_prices(top: dict[str, Any] | None, known: Iterable[str]) -> PriceReading:
    """The prices on the Shard Market's list as the game shows it (logs/dialogs.log): row 0 a header
    with no command, then one row per shard, options [name, "Unit price N arnebia; ..."] and cmd
    [1, item id, N]. A price is used only when the text and the command agree, it is a whole number
    from 1 to PRICE_MOST, the shard is one shards.json has, and no other row gives it another
    price. Anything else is left out with the reason; a menu that isn't the market's list gives
    nothing at all."""
    out = PriceReading()
    rows: list[list[dict[str, Any] | None]] = (top or {}).get("rows") or []
    names = {" ".join(k.split()).casefold(): k for k in known}
    head = rows[0][0] if rows and rows[0] else None
    if head is None or head.get("cmd") is not None or len(rows) < 2:
        out.bad.append(_("the open menu isn't the Shard Market's list"))
        return out
    seen: dict[str, int] = {}
    clash: set[str] = set()
    for r, row in enumerate(rows[1:], start=1):
        e = row[0] if len(row) == 1 else None
        options: list[Any] = (e or {}).get("options") or []
        name = str(options[0]).strip() if options and options[0] else ""
        cmd: list[Any] = (e or {}).get("cmd") or []
        label = name or _("row {n}", n=r)
        if e is None or not name or len(cmd) != 3 or cmd[0] != 1:
            out.bad.append(_("{row}: not a shard row", row=label))
            continue
        shown = [int(m.group(1)) for t in options[1:] if isinstance(t, str) for m in [SHOWN_PRICE.search(t)] if m]
        sent = cmd[2]
        if not shown:
            out.bad.append(_("{row}: no price shown", row=label))
            continue
        if not isinstance(sent, int) or isinstance(sent, bool) or sent != shown[0]:
            out.bad.append(_("{row}: the price shown ({shown}) isn't the one the menu sends ({sent})",
                             row=label, shown=shown[0], sent=sent))
            continue
        if not 1 <= sent <= PRICE_MOST:
            out.bad.append(_("{row}: {price} isn't a price", row=label, price=sent))
            continue
        key = names.get(" ".join(name.split()).casefold())
        if key is None:
            out.unknown.append(name)
            continue
        if key in seen and seen[key] != sent:
            clash.add(key)
        seen[key] = sent
    for key in sorted(clash):
        out.bad.append(_("{row}: listed twice with different prices", row=key))
        seen.pop(key, None)
    out.prices = seen
    return out


def save_shard_prices(prices: Mapping[str, int]) -> list[tuple[str, int, int]]:
    """Write the new prices into data/shards.json, every other field and shard as it was, in the
    file's own layout. Only valid prices of shards it has are written (read_market_prices); one that
    isn't a positive whole number is never written over a good one. Returns [(shard, old, new)] of
    the prices that changed."""
    path = os.path.join(paths.DATA, SHARDS_FILE)
    with open(path, encoding="utf-8") as f:
        text = f.read()
    data: object = json.loads(text)
    shards: object = data.get("shards") if json.is_object(data) else None
    if not json.is_object(data) or not json.is_object(shards):
        raise ValueError(_("{file} isn't a shard price list", file=SHARDS_FILE))
    changed: list[tuple[str, int, int]] = []
    for name, price in prices.items():
        row: object = shards.get(name)
        if not json.is_object(row) or isinstance(price, bool) or not 1 <= price <= PRICE_MOST:
            continue
        old = row.get("price")
        if old == price:
            continue
        # only the number changes: the file keeps its own layout, byte for byte
        at = re.compile(r"(%s\s*:\s*\{\s*\"price\"\s*:\s*)-?\d+(?=\s*[,}])" % re.escape(json.dumps(name)))
        text, n = at.subn(lambda m: m.group(1) + str(price), text, count=1)
        if n != 1:
            raise ValueError(_("{file}: the price of {shard} isn't where it should be", file=SHARDS_FILE, shard=name))
        row["price"] = price
        changed.append((name, old if isinstance(old, int) else 0, price))
    if changed:
        if json.loads(text) != data:       # the edit must say exactly what was meant, or nothing is saved
            raise ValueError(_("{file}: the new prices didn't come out right - nothing saved", file=SHARDS_FILE))
        json.save_text(path, text)
    return changed


def update_shard_prices(b: Bot) -> int:
    """Tools, Update shard prices: read the nearest Shard Market's list and keep its prices in
    data/shards.json. Says what changed, what stayed, and every row or shard that couldn't be read
    (those keep their old price). Returns how many prices changed."""
    shards, markets = shard_catalog()
    if not shards:
        b.log(_("the shard price list (data\\shards.json) can't be read"), speak=True)
        return 0
    if not markets:
        b.log(_("I don't know where a Shard Market is"), speak=True)
        return 0
    visit(b, None, candidates=market_candidates(markets))
    try:
        d = b.dialog()
        b.note_dialog(d, "reading the shard prices")
        got = read_market_prices(d.get("top"), shards)
    finally:
        b.close_dialogs()
    for why in got.bad:
        b.log(_("shard price not used: {why}", why=why))
    if got.unknown:
        b.log(_("not in the price list, left out: {list}", list=", ".join(got.unknown)))
    missing = sorted(set(shards) - set(got.prices))
    if missing:
        b.log(_("not read, old price kept: {list}", list=", ".join(missing)))
    if not got.prices:
        b.log(_("no shard prices could be read - nothing changed"), speak=True)
        return 0
    try:
        changed = save_shard_prices(got.prices)
    except (OSError, ValueError) as e:
        b.log(_("couldn't save the shard prices: {err}", err=e), speak=True)
        return 0
    if changed:
        stats.forget_prices()           # the results cost old records at the new prices from now on
    for name, old, new in changed:
        b.log(_("{shard}: {old} -> {new} arnebia", shard=name, old=old, new=new))
    b.log(_("shard prices: {changed} changed, {same} unchanged, {missing} not read",
            changed=len(changed), same=len(got.prices) - len(changed), missing=len(missing)), speak=True)
    return len(changed)


def _sell_one_shard(b: Bot, name: str, n: int) -> int:
    before = counts(b).get(name.lower(), 0)
    sell_shards(b, [name], at_most={name.lower(): n})
    return before - counts(b).get(name.lower(), 0)
