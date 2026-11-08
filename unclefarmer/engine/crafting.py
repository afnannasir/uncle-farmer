"""The crafting tools: the Gem Maker and the Equipment Enhancer.

Both use the game's own craftsmen and read every number from their live menus. The rules come
from the craftsmen's own instruction pages (data/game_texts.json, gametexts.py):

  Gem Craftsman ("shard synthesis", "gem synthesis", "Batch gem synthesis")
    - 4 shards of a colour and level make 1 gem of that level; this never fails.
    - 3 gems make 1 gem a level higher with a 50% chance; a failure shatters one gem into a shard.
    - From level 2, a shard of the same colour and level adds 20%; a Gem Protection Rune keeps the
      gem whole on a failure.
  Forger ("enhance equipment")
    - each try takes one Enhance Crystal (its level set by the item's level) and arnebia; the
      chance falls as the level rises; a failure drops the level by one unless Enhance Protection
      Runes are used; Boost Runes add 5%.

Where: a gang's own craftsmen are cheaper (its Lake lowers gem costs, its Mining Area forging
costs: the game's gang production rules), so the bot goes to a gang's base when you choose that -
your own gang's, or another gang's by its name. Every gang base has the same layout (maps 4001-4006):
two halves with a solid wall between them at y=60 and no way through. The Gang Entrance's "Enter the
inner hall" lands in the north half (Head of inner hall, Guru Elder, storage); the craftsmen (Gem
Craftsman 59,93, Artificer 53,94, Forger 28,94, Equipment Maker 22,93) are in the south half, the
OUTER hall ("Enter the Outer hall of the gang"). The Gang Entrance menu: "Enter the Outer hall of the
gang", "Enter the inner hall of the gang", "Allied gangs", "all gangs".

Nothing here spends below the arnebia reserve (profile gem_keep_arnebia), and "check only" walks
there, reads the menus and reports what it would do without pressing anything that costs.

A combine that works gives NO shard back; only a broken gem leaves one (the next round's boost
shard, or 4 of them a new gem). So nothing here may count on a shard coming back: a lucky streak
leaves none, and the boost box ("add 1 ... Shard") must not stay ticked once the last boost shard
is gone - the game then makes nothing. Every try and every round is judged by what the bag shows
afterwards (_colour_state): a try the game didn't carry out is no "failed" try, and a round that
changed nothing ends the Gem Maker instead of being made again and again (up to 40 rounds, each
with a walk back to the craftsman).

Menus as the game shows them (from logged sessions):
  shard list        "Level 1 Obsidian Shard42"
  crystal levels    "synthesize Level 5 Equipment Enhance Crystal"
  shard amount      "synthesize 1 Level 6 Equipment Enhance Crystal needs 70 Equipment Enhance Crystal
                    Shard; fee 2051 arnebia" + "please enter"
  batch synthesis   "Level 2 Topaz 15" -> "It takes 3 Level 2 Topaz and cost 527 arnebia to synthesize
                    each Level 3 Topaz" + [ ] "Automatically use Level 2 Topaz Shard in the backpack to
                    increase the success rate" + "Please enter quantity"
  gem synthesis     "Level 2 Topaz 4" -> "use 3 Level 2 Topaz to Synthesize 1 Level 3 Topaz; fee 527
                    arnebia" + [ ] "add 1 Level 2 Topaz Shard" + [ ] "use 1 Gem Protection Rune" +
                    "OK to Synthesize"
  enhance           "Epic  Sniper Belt;rare | enhancement level: 10" -> "forging Epic Sniper Belt;rare ,
                    Enhancement level increased from 10 to 11, requires 1 Level 5 Equipment Enhance
                    Crystal, costs 9523 arnebia" + "5.0% success rate" + [ ] "Use 11 Equipment Enhance
                    Boost Rune; ...; not selected" + [ ] "Use 11 Equipment Enhance Protection Rune ...;
                    not selected" + "start forging"
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any, TypeVar

from .. import atlas, knowledge
from ..clock import clock_for
from ..i18n import _
from . import bagrules, menus, town
from .bot import Bot, Stopped
from .botparts.base import STOPPING, note_failure
from .botparts.travel import note_way_out

# The craftsmen in a gang's base: the same cells in every gang (seen in two gangs' bases).
GANG_SPOTS = {"forger": ("Forger", (28, 94)), "gem_craftsman": ("Gem Craftsman", (59, 93)),
              "artificer": ("Artificer", (53, 94))}
Entry = dict[str, Any]           # one entry of a game menu (link `dialog`)
N = TypeVar("N")
D = TypeVar("D")
S = TypeVar("S")
GEM = re.compile(r"^\s*Level (\d+) ([A-Za-z' ]+?)\s*$", re.I)
RUNE = "Gem Protection Rune"
SETTLE = 2.0             # seconds the bag is given to show a combine's result before it counts as none
IDLE_ROUNDS = 2          # rounds in a row that change nothing, with gems still to combine, tried again
MAX_ROUNDS = 1000        # a safety net only: every round that counts changed the bag, and buying is bounded
OFFER_WAIT = 5.0         # seconds the Forger's page for an item is given to show what its next level takes
MENU_POLL = 0.15         # seconds between reads of a menu that is still changing


# ---------------------------------------------------------------------------------------- getting there
def in_gang(b: Bot) -> bool:
    mid = b.map_id()
    return atlas.kind(mid) == "gang" or "gang" in atlas.label(mid).lower()


def place(cfg: dict[str, Any]) -> str:
    """Where a tool's craftsman is: "gang" (your gang's), "gang:<name>" (that gang's) or "city"."""
    where: str = cfg.get("where", "gang")
    if where == "other_gang":
        name = (cfg.get("gang_name") or "").strip()
        return "gang:" + name if name else "gang"
    return where


def _reachable(b: Bot, role: str) -> bool:
    """We stand in the half of the gang base where that craftsman is (see the module notes)."""
    return reachable_cell(b, GANG_SPOTS[role][1])


def reachable_cell(b: Bot, cell: tuple[int, int]) -> bool:
    """We can walk from here to cell. A gang base's inner hall walks out to the outer hall through
    its inner door, but nothing walks back in (a one-way door strip; the way in is the Gang
    Entrance), so this follows the one-way gates, not just which part of the map cell is in."""
    me = b.me()
    try:
        m = atlas.model(b.map_id())
        here = (int(me["x"]), int(me["y"]))
        if m.comp_of(*here) != m.comp_of(*cell):
            return False
        if not hasattr(m, "gated_route"):
            return True                   # a map with no gates to follow (no data for them)
        start = here if m.ok(*here) else m.nearest_walkable(here[0], here[1], 4)
        goal = (int(cell[0]), int(cell[1]))
        goal = goal if m.ok(*goal) else m.nearest_walkable(goal[0], goal[1], 4)
        if start is None or goal is None:
            return True                   # no walkable cell to judge by: let the walk find out
        return m.gated_route((start[0], start[1]), (goal[0], goal[1])) is not None
    except STOPPING:
        raise
    except Exception as e:
        note_failure(b, "reading the map", e)
        return True                       # no map data: let the walk find out


def _pick_gang(b: Bot, gang: str) -> bool:
    """After "all gangs" (or "Allied gangs"): find that gang by name - typing it when the game asks
    for a name, turning pages when there are pages - and choose it."""
    want = r"(?i)(^|[^a-z0-9])%s([^a-z0-9]|$)" % re.escape(gang)
    for _page in range(40):
        if town.wait_input(b, True, 0.6):     # a box asking for the gang's name
            if not town.type_text(b, gang):
                return False
            continue
        d = b.wait_dialog(3.0)
        if d is None or not d.get("top"):
            return False
        top = d["top"]
        if b.find_entry(top, want) is not None:
            return b.choose(want) is not None
        if b.find_entry(top, r"(?i)please enter|enter .*name|search|find") is not None:
            b.choose(r"(?i)please enter|enter .*name|search|find")
            continue
        if b.find_entry(top, r"(?i)next\s*page|^\s*next\b|>>") is None:
            return False
        b.choose(r"(?i)next\s*page|^\s*next\b|>>")
        b.sleep(0.3)
    return False


def pick_at_entrance(b: Bot, gang: str) -> bool:
    """At an open Gang Entrance menu: the gang named `gang`, through "all gangs" (or "Allied
    gangs"), chosen. Its own menu (which hall to enter) is what opens next. False, said, when no
    list has it."""
    b.log(_("looking for the gang {gang} at the Gang Entrance", gang=gang))
    top: dict[str, Any] = b.dialog().get("top") or {}
    for listing in (r"(?i)all\s+gangs", r"(?i)allied\s+gangs"):
        if b.find_entry(top, listing) is not None and b.choose(listing) is not None and _pick_gang(b, gang):
            return True
        b.close_dialogs()
        town.visit(b, "gang_entrance")
        top = b.dialog().get("top") or {}
    b.log(_("the Gang Entrance lists no gang called {gang}", gang=gang), speak=True)
    return False


def enter_gang(b: Bot, gang: str | None=None, role: str="gem_craftsman") -> bool:
    """Into a gang base's OUTER hall, where the craftsmen are: your own gang's through the nearest
    Gang Entrance, or the gang named `gang` through its "all gangs" list. Already in a gang base
    but in the walled-off inner hall (or the wrong gang's): out and in again."""
    if in_gang(b) and _reachable(b, role) and not gang:
        return True
    b.close_dialogs()
    town.visit(b, "gang_entrance")          # (leaves a gang base first when we are in one)
    mid = b.map_id()
    me = b.me()
    if gang:
        if not pick_at_entrance(b, gang):
            return False
        d = b.wait_dialog(2.0)
        if d and d.get("top") and b.find_entry(d["top"], r"(?i)outer\s+hall") is not None:
            b.choose(r"(?i)outer\s+hall")
    elif b.choose(r"(?i)outer\s+hall") is None:
        return False
    for _i in range(2):                      # "are you sure?" / an entry fee
        d = b.dialog()
        if d["open"] == 0 or b.map_id() != mid or b.find_entry(d["top"], town.CONFIRM) is None:
            break
        b.choose(town.CONFIRM)
    if not b.wait_map_change(mid, 15):
        b.close_dialogs()
        return False
    if not in_gang(b):
        return False
    # the base's exit leads back here: judge the nearest storehouse or market from this city
    note_way_out(b, b.map_id(), mid, (me.get("x", 0), me.get("y", 0)))
    if not _reachable(b, role):
        b.log(_("landed in the gang's inner hall, which is walled off from the craftsmen"), speak=True)
        return False
    b.log(_("in {where}", where=b.snap().get("map", {}).get("name") or atlas.label(b.map_id())))
    return True


def open_craftsman(b: Bot, role: str, where: str) -> str:
    """Open a craftsman's menu. where: "gang" (your gang's, cheaper), "gang:<name>" (that gang's) or
    "city" (the nearest city's). Falls back to a city when the gang's can't be reached. Returns
    where it went."""
    if where == "gang" or where.startswith("gang:"):
        gang = where[5:] or None
        try:
            if enter_gang(b, gang, role):
                name, cell = GANG_SPOTS[role]
                if b.talk(name, cell):
                    return "gang"
        except Stopped as e:
            if b.stop_event.is_set() or getattr(e, "final", False):
                raise           # a stop rule or an unreadable game: not "try the city instead"
        if gang:
            b.log(_("couldn't reach the {who} of the gang {gang}; going to the nearest city's",
                    who=GANG_SPOTS[role][0], gang=gang), speak=True)
        else:
            b.log(_("couldn't reach your gang's {who}; going to the nearest city's", who=GANG_SPOTS[role][0]),
                  speak=True)
    city = [n for n in knowledge.service_npcs(role) if atlas.kind(n[0]) != "gang"]
    town.visit(b, role, candidates=city or None)
    return "city"


# ---------------------------------------------------------------------------------------- menu helpers
def menu_text(b: Bot) -> str:
    return town.menu_text(b)


def entries(b: Bot) -> list[tuple[int, int, Entry]]:
    """[(row, col, entry)] of the open menu."""
    top: dict[str, Any] = b.dialog().get("top") or {}
    rows: list[list[Entry | None]] = top.get("rows", [])
    return [(r, c, e) for r, row in enumerate(rows) for c, e in enumerate(row) if e]


def entry_like(b: Bot, pattern: str) -> Entry | None:
    rx = re.compile(pattern, re.I)
    for _r, _c, e in entries(b):
        if any(rx.search(t) for t in b.entry_texts(e)):
            return e
    return None


def is_ticked(e: Entry) -> bool:
    """A checkbox entry's state: the Forger writes "selected" / "not selected"; others carry it in
    the last number of the entry's command."""
    text = " ".join(t for t in (e.get("text"), e.get("raw"), e.get("f"), e.get("g")) if t).lower()
    if "not selected" in text:
        return False
    if "selected" in text:
        return True
    cmd: list[Any] = e.get("cmd") or []
    return bool(cmd and cmd[-1])


def tick(b: Bot, pattern: str, want: bool = True) -> bool:
    """Set a checkbox entry. True when the game shows it set as wanted."""
    e = entry_like(b, pattern)
    if e is None:
        return False
    if is_ticked(e) == want:
        return True
    before = dict(e)
    b.choose(pattern)
    b.sleep(0.4)
    e = entry_like(b, pattern)
    if e is not None and e != before and is_ticked(e) == want:
        return True
    if e is not None and is_ticked(e) != want:
        b.key("space")                       # the game's own checkbox key
        b.sleep(0.4)
        e = entry_like(b, pattern)
    return e is not None and is_ticked(e) == want


def spare_arnebia(b: Bot) -> int:
    return town.counts(b).get("arnebia", 0) - int(b.p.get("gem_keep_arnebia", 20000))


def wait_text(b: Bot, pattern: str, seconds: float) -> tuple[re.Match[str] | None, str]:
    """Read the open menu until its text matches pattern (the server sends the next page a moment
    after a choice, and the page before stays up until then). (the match or None, the last text read)."""
    rx = re.compile(pattern, re.I)
    end = clock_for(b).time() + seconds
    while True:
        text = menu_text(b)
        m = rx.search(text)
        if m is not None or clock_for(b).time() >= end:
            return m, text
        b.sleep(MENU_POLL)


def _num(rx: str, text: str, default: D, cast: Callable[[str], N] = int) -> N | D:
    """The first group of rx in text, through cast; default when rx isn't there."""
    m = re.search(rx, text, re.I)
    return cast(m.group(1)) if m else default


# ---------------------------------------------------------------------------------------- gems
def gem_levels(b: Bot, colour: str) -> dict[int, int]:
    """{level: count} of a gem colour in the bag."""
    out: dict[int, int] = {}
    for name, n in town.counts(b).items():
        m = GEM.match(name)
        if m and m.group(2).strip().lower() == colour.lower() and "shard" not in name:
            out[int(m.group(1))] = out.get(int(m.group(1)), 0) + n
    return out


def raise_gems(b: Bot, colour: str, level: int, k: int, boost: bool, protect: bool, where: str,
               check_only: bool=False, blocked: list[str] | None = None) -> tuple[int, int]:
    """Combine 3 Level `level` gems into 1 a level higher, k times. With protect, one at a time
    (only single synthesis takes a protection rune); otherwise in one batch. Returns (made, lost).
    blocked: gets why it couldn't combine when that is something that won't pass by itself (the
    arnebia at the reserve, no protection rune left), so the Gem Maker stops instead of trying again."""
    gem, nxt = "Level %d %s" % (level, colour), "Level %d %s" % (level + 1, colour)
    before = town.counts(b)
    open_craftsman(b, "gem_craftsman", where)
    section = r"^\s*gem synthesis" if protect else r"^\s*Batch gem synthesis"
    if b.choose(section) is None or not b.wait_dialog(3.0):
        b.log(_("the Gem Craftsman's menu has no gem synthesis"))
        return 0, 0
    row = r"^\s*" + re.escape(gem) + r"\s+\d+\s*$"
    shard = gem + " Shard"
    done = 0

    def state() -> tuple[int, ...]:
        have = town.counts(b)
        return tuple(have.get(n.lower(), 0) for n in (gem, nxt, shard, RUNE))
    for _i in range(k if protect else 1):
        if b.choose(row) is None or not b.wait_dialog(3.0):
            b.log(_("the Gem Craftsman didn't list {gem}", gem=gem))
            break
        text = menu_text(b)
        fee = _num(r"(?:cost|fee)\s+(\d+)\s+arnebia", text, 0)
        each = k if not protect else 1
        if fee:
            each = min(each, max(0, spare_arnebia(b)) // fee)
        if check_only:
            b.log(_("would combine {n} x 3 {gem} into {nxt}: fee {fee} each, 50% chance{boost}{protect}",
                    n=k, gem=gem, nxt=nxt, fee=fee,
                    boost=_(", +20% with {shard}", shard=shard) if boost and level >= 2 else "",
                    protect=_(", protected by runes") if protect else ""))
            b.close_dialogs()
            return 0, 0
        if each <= 0:
            why = _("not enough spare arnebia to raise {gem} (fee {fee})", gem=gem, fee=fee)
            b.log(why, speak=True)
            if blocked is not None:
                blocked.append(why)
            break
        # a boost shard only exists after a gem broke: never ask for one the bag doesn't hold
        want_boost = boost and level >= 2 and town.counts(b).get(shard.lower(), 0) > 0
        was = state()
        if protect:
            if not _synthesize_one(b, want_boost, shard, section, where, blocked):
                if blocked is not None and town.counts(b).get(RUNE.lower(), 0) <= 0:
                    blocked.append(_("no Gem Protection Rune left, and combining is set to use one"))
                break
        elif not _synthesize_batch(b, want_boost, each):
            break
        if not _settled(b, was, state):
            # The bag showed no change: the game carried nothing out (a stale boost box, a
            # refusal), or its answer is late. No try, not a failure; the Gem Maker looks at the
            # bag again after the round and only then decides.
            b.log(_("the bag showed no change after combining {gem} - leaving it for this round", gem=gem))
            break
        done += 1 if protect else each
    b.close_dialogs()
    after = town.counts(b)
    made = after.get(nxt.lower(), 0) - before.get(nxt.lower(), 0)
    lost = max(0, done - made)
    if done:
        b.log(_("{gem}: {tries} tries, {made} {nxt} made, {lost} failed", gem=gem, tries=done, made=made,
                nxt=nxt, lost=lost))
    return made, lost


def _settled(b: Bot, before: S, read: Callable[[], S]) -> bool:
    """The bag shows a combine's result: some count of the gem, the next gem, its shard or the
    runes moved (or, for a whole round, the colour's gems and shards). Waits up to SETTLE for the
    game's bag update. False: nothing happened."""
    waited = 0.0
    while read() == before:
        if waited >= SETTLE:
            return False
        b.sleep(0.25)
        waited += 0.25
    return True


def _synthesize_one(b: Bot, want_boost: bool, shard: str, section: str, where: str,
                    blocked: list[str] | None = None) -> bool:
    """One protected synthesis (the boost shard added when wanted, and the box cleared when the bag
    has none - it may still be ticked from the last try, which used the last one up). False: stop.
    A box that stays ticked with no such shard makes the game refuse the try: blocked gets that."""
    if not tick(b, r"add 1 .*Shard", want_boost):
        if want_boost:
            b.log(_("couldn't add the {shard}", shard=shard))
        elif (e := entry_like(b, r"add 1 .*Shard")) is not None and is_ticked(e):
            why = _("the {shard} box stays ticked with none in the bag", shard=shard)
            b.log(why)
            if blocked is not None:
                blocked.append(why)
    if not tick(b, r"Gem Protection Rune", True):
        b.log(_("no Gem Protection Rune to use - stopping, as asked"), speak=True)
        return False
    if b.choose(r"OK to Synthesize") is None:
        return False
    b.sleep(0.6)
    if b.dialog()["open"] == 0:
        open_craftsman(b, "gem_craftsman", where)
        if b.choose(section):
            b.wait_dialog(3.0)
    return True


def _synthesize_batch(b: Bot, want_boost: bool, each: int) -> bool:
    """A batch synthesis of `each` (the boost shards used automatically when wanted). False: stop."""
    if want_boost:
        tick(b, r"Automatically use .*Shard", True)
    elif (e := entry_like(b, r"Automatically use .*Shard")) is not None and is_ticked(e):
        tick(b, r"Automatically use .*Shard", False)      # ticked from before, and none to use now
    if not town.pick(b, r"Please enter quantity|please enter", each):
        b.log(_("the Gem Craftsman didn't ask how many"))
        return False
    b.sleep(0.6)
    return True


# ---------------------------------------------------------------------------------------- getting shards
def storage_count(b: Bot, item: str) -> int:
    """How many of item the storehouse holds ("1; 20 Memory Butterfly"), with its list open."""
    for _r, _c, e in entries(b):
        for t in b.entry_texts(e):
            m = re.search(r"(\d+)\s+" + re.escape(item) + r"\s*($|[;|,])", t, re.I)
            if m:
                return int(m.group(1))
    return 0


def take_from_storage(b: Bot, item: str, n: int, check_only: bool=False) -> int:
    """Take up to n of item out of the storehouse, never asking for more than it holds."""
    if n <= 0:
        return 0
    before = town.counts(b).get(item.lower(), 0)
    town.visit(b, "storehouse")
    try:
        if not town.open_section(b, r"^\s*item storage"):
            return 0
        k = min(n, storage_count(b, item))
        if check_only:
            b.log(_("the storehouse has {n} {item}", n=storage_count(b, item), item=item))
            return 0
        if k <= 0:
            return 0
        town.pick(b, town.item_pattern(item), k)
    finally:
        b.close_dialogs()
    got = town.counts(b).get(item.lower(), 0) - before
    if got:
        b.log(_("took {n} {item} out of the storehouse", n=got, item=item))
    return got


def fee_per_shard(b: Bot, shard: str) -> float | None:
    """The Gem Craftsman's fee for making this shard's gem, per shard (a quarter of the gem's fee),
    as last seen; None if the bot hasn't seen it yet."""
    gem = town.gem_of(shard)
    fees: dict[str, int] = b.p.get("gem_fees") or {}
    fee = fees.get(gem.lower()) if gem else None
    return None if fee is None else fee / float(town.SHARDS_PER_GEM)


def fees_owed(b: Bot, shards: Iterable[str]) -> float:
    """The gem fees still to pay for the chosen shards already in the bag."""
    have = town.counts(b)
    return sum(have.get(s.lower(), 0) * (fee_per_shard(b, s) or 0) for s in shards)


def buy_shards(b: Bot, shard: str, n: int, max_price: int = 0, check_only: bool=False, owed: float = 0) -> int:
    """Buy up to n of a shard at the nearest Shard Market, at no more than max_price each (0: any
    price), and never below the arnebia reserve. The market's own row gives the price. Shards are
    bought only as far as the gem fee for them can be paid too: a shard bought but never made into a
    gem is money lost.

    The market's "how many" box takes at most town.TRADE_MOST at a time, so a bigger buy goes in
    batches. Each batch is worked out afresh: what is still wanted, what the box takes, what still
    fits in the bag (up to the profile's bag_full_at load) and what the spare arnebia still pays
    for, the smallest of them. A batch the market answered with fewer shards than asked for (or
    none) ends the buying: the bag, the arnebia or the market said no, and asking again would only
    repeat it."""
    if n <= 0:
        return 0
    _s, markets = town.shard_catalog()
    if not markets:
        b.log(_("I don't know where a Shard Market is"), speak=True)
        return 0
    room = None if check_only else room_for(b, shard, 1.0)      # read before the market's menu is open
    candidates = town.market_candidates(markets)
    town.visit(b, None, candidates=candidates)
    before = town.counts(b).get(shard.lower(), 0)
    try:
        listed = menus.find(b, menus.SHARD_MARKET, name=shard)     # the verified list's own row
        if listed is not None:
            price = int(listed.get("price") or 0)
        else:
            e = entry_like(b, town.item_pattern(shard))
            if e is None:
                b.log(_("the Shard Market doesn't sell {shard}", shard=shard))
                return 0
            price = _num(r"Unit price\s+(\d+)", " ".join(b.entry_texts(e)), 0)
        if check_only:
            b.log(_("the Shard Market sells {shard} at {price} arnebia each", shard=shard, price=price))
            return 0
        if max_price and price > max_price:
            b.log(_("{shard} costs {price} at the market, more than your limit of {limit}",
                    shard=shard, price=price, limit=max_price), speak=True)
            return 0
        fee = fee_per_shard(b, shard)

        def affordable(bought: int) -> int:
            """How many more the spare arnebia pays for, with the gem fees of those bought so far kept back."""
            if not price:
                return n
            budget = max(0.0, spare_arnebia(b) - owed - bought * (fee or 0))
            if fee is None:
                budget *= 0.8                     # fee not seen yet: leave a fifth for it
            return int(budget // (price + (fee or 0)))

        n = min(n, affordable(0))
        if n <= 0:
            b.log(_("not enough spare arnebia to buy {shard}", shard=shard), speak=True)
            return 0
        if room is not None and room <= 0:
            b.log(_("the bag is too full for more {shard}", shard=shard), speak=True)
            return 0
        _buy_batches(b, shard, n, room, affordable, lambda: town.visit(b, None, candidates=candidates))
    finally:
        b.close_dialogs()
    got = town.counts(b).get(shard.lower(), 0) - before
    if got:
        b.log(_("bought {n} {shard}", n=got, shard=shard))
    return got


def _buy_batches(b: Bot, shard: str, n: int, room: int | None, affordable: Callable[[int], int],
                 revisit: Callable[[], object]) -> int:
    """With the Shard Market's list open: buy n of a shard, one batch at a time, each the smallest
    of what is still wanted, town.TRADE_MOST, the bag's room left (room: what fitted before the
    first; None: not known) and affordable(bought so far). Returns how many came."""
    got = 0
    while got < n:
        fits = room_left(b, shard, room, got)
        step = min(n - got, town.TRADE_MOST, affordable(got), fits if fits is not None else n)
        if step <= 0:
            b.log(_("bought {n} {shard}: the bag has no room for more", n=got, shard=shard) if fits == 0 else
                  _("bought {n} {shard}: the spare arnebia pays for no more", n=got, shard=shard))
            break
        if not town.market_lists(b, shard):
            b.close_dialogs()                 # the list didn't open again after the last batch
            revisit()
            if not town.market_lists(b, shard):
                b.log(_("the Shard Market's list didn't open again - bought {n} {shard}", n=got, shard=shard),
                      speak=True)
                break
        had = town.counts(b).get(shard.lower(), 0)
        town.shard_trade(b, shard, "buy", step)      # one batch: by index when it can
        came = _arrived(b, shard, had, step)
        got += came
        if came < step:
            b.log(_("the Shard Market sold {came} of the {step} {shard} asked for - buying no more now",
                    came=came, step=step, shard=shard), speak=True)
            break
    return got


def room_left(b: Bot, shard: str, room: int | None, got: int) -> int | None:
    """How many more of a shard fit, `got` after a reading said `room`: that reading less what came
    since, and lower still if a fresh reading says so. A fresh reading is taken only when the agent
    reads the character sheet without opening it (a sheet window would close the market's menu).
    None: the shard's weight isn't known, so the bag can't be judged."""
    if room is None:
        return None
    left = max(0, room - got)
    if getattr(b, "agent_sheet", False):
        now = room_for(b, shard, 1.0)
        if now is not None:
            left = min(left, now)
    return left


def _arrived(b: Bot, item: str, had: int, want: int) -> int:
    """How many of item came into the bag since it held `had`, giving the game's bag update up to
    SETTLE to show all `want` of them."""
    waited = 0.0
    while True:
        came = town.counts(b).get(item.lower(), 0) - had
        if came >= want or waited >= SETTLE:
            return max(0, came)
        b.sleep(0.25)
        waited += 0.25


def fetch_shards(b: Bot, shard: str, n: int, supply: str, max_price: int = 0, check_only: bool=False,
                 chosen: Sequence[str] = ()) -> int:
    """Bring up to n of a shard into the bag: supply "both" (storehouse, then the market),
    "storage" or "buy". chosen: every shard the Gem Maker is working on, whose fees are kept back
    when buying. Returns how many arrived."""
    got = 0
    if supply in ("both", "storage"):
        got += take_from_storage(b, shard, n, check_only)
    if supply in ("both", "buy") and got < n:
        got += buy_shards(b, shard, n - got, max_price, check_only, owed=fees_owed(b, chosen or (shard,)))
    return got


def room_for(b: Bot, shard: str, share: float) -> int | None:
    """How many of a shard fit into `share` of the bag's free room (up to the profile's bag_full_at
    percent), from the character sheet's load and the shard's learned weight; None if its weight
    isn't known yet. The bag alone limits it: the Shard Market's 2000 a time is buy_shards' to keep,
    and the storehouse has no such limit."""
    sheet: dict[str, Any] = b.refresh_sheet(force=True) or {}
    if not sheet.get("maxload"):
        return None
    free = sheet["maxload"] * float(b.p.get("bag_full_at", 100)) / 100.0 - sheet.get("load", 0)
    w = bagrules.weights().get(shard.lower())
    if not w:
        return None
    return max(0, int(free * share // w))


def fill_with(b: Bot, shard: str, share: float, supply: str, max_price: int, chosen: Sequence[str] = (),
              want: int | None = None) -> int:
    """Fill `share` of the bag's free room with a shard, or bring `want` of it when fewer fit.
    Learns the shard's weight first if needed (the load before and after a small first batch)."""
    k = room_for(b, shard, share)
    got = 0
    if k is None:
        before = town.load_now(b)
        got = fetch_shards(b, shard, 20 if want is None else min(20, want), supply, max_price, chosen=chosen)
        if got:
            bagrules.learn_weight(shard, town.load_now(b), before, got)      # the load went up by got shards
        k = room_for(b, shard, share)
        if k is None:
            return got
    if want is not None:
        k = min(k, want - got)
    return got + fetch_shards(b, shard, k, supply, max_price, chosen=chosen) if k > 0 else got


def colour_of(name: str) -> str | None:
    """The gem colour of a gem or shard name ("Level 2 Topaz Shard" -> "Topaz"), or None."""
    m = re.match(r"^\s*Level \d+ ([A-Za-z' ]+?)(?: Shard)?\s*$", name or "", re.I)
    if not m or "crystal" in m.group(1).lower():
        return None
    return m.group(1).strip()


def shard_levels(b: Bot, colour: str) -> dict[int, int]:
    """{level: count} of a colour's shards in the bag."""
    out: dict[int, int] = {}
    for name, n in town.counts(b).items():
        m = town.GEM_SHARD.match(name)
        if m and m.group(2).strip().lower() == colour.lower():
            out[int(m.group(1))] = out.get(int(m.group(1)), 0) + n
    return out


def shortfall(gems: Mapping[int, int], shards: Mapping[int, int], level: int, missing: int,
              protect: bool) -> tuple[dict[int, int], int]:
    """What making `missing` more Level `level` gems still needs, from the gems and shards of that
    colour ({level: count}): {lv: gems still to come at lv} for lv 1..level, and the Level 1 shards
    to get. 4 shards make a gem; a combine works half the time, so a gem a level up takes about 4
    gems below it (3 with protection runes: a failure then keeps all three). Boost shards are left
    out, so the estimate errs high; each round of the Gem Maker plans again from the real bag."""
    per = 3 if protect else 4
    short = max(0, missing)
    out: dict[int, int] = {}
    for lv in range(level, 0, -1):
        short = max(0, short - (gems.get(lv, 0) if lv < level else 0) - shards.get(lv, 0) // 4)
        out[lv] = short
        if lv > 1:
            short *= per
    return out, max(0, out.get(1, 0) * 4 - shards.get(1, 0) % 4)


def make_most(b: Bot, cfg: dict[str, Any], check_only: bool=False) -> None:
    """Make the most of the gems and shards in the bag, colour by colour: shards into gems (4 make
    1), then gems up a level (3 make 1) as far as they go, again and again, because a failed combine
    leaves a shard that the next round turns back into a gem. Shards left over (fewer than four) are
    the +20% boost. It cleans the bag: many shards and small gems become a few big ones."""
    job = _BagJob(cfg)
    if check_only:
        for col in job.colours_now(b):
            have = town.counts(b)
            shards = {lv: have.get(("Level %d %s Shard" % (lv, col)).lower(), 0) for lv in range(1, job.up_to + 1)}
            gems = gem_levels(b, col)
            b.log(_("{col}: shards {shards}; gems {gems}", col=col,
                    shards=", ".join("%d at level %d" % (n, lv) for lv, n in sorted(shards.items()) if n) or _("none"),
                    gems=", ".join("%d at level %d" % (n, lv) for lv, n in sorted(gems.items()) if n) or _("none")))
        open_craftsman(b, "gem_craftsman", job.where)
        b.log(_("the Gem Craftsman is open; nothing was made (check only)"))
        b.close_dialogs()
        return
    idle = 0
    for _round in range(MAX_ROUNDS):
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        before = _bag_gems(b)
        blocked: list[str] = []
        _bag_round(b, job, blocked)
        if _settled(b, before, lambda: _bag_gems(b)):
            idle = 0
            continue                  # something changed: another round from the bag as it is now
        left = job.combinable(b)
        if not left:
            b.log(_("the bag's gems are as high as they go"), speak=True)
        elif blocked:
            b.log(_("the Gem Maker can't go on: {why}", why=blocked[0]), speak=True)
        else:
            idle += 1
            if _idle_round(b, left, idle):
                continue
        break
    else:
        b.log(_("the Gem Maker stopped after {n} rounds", n=MAX_ROUNDS), speak=True)
    b.close_dialogs()


class _BagJob:
    """What "make the most of my bag" was asked for, read once from its settings."""

    def __init__(self, cfg: dict[str, Any]) -> None:
        self.where = place(cfg)
        self.up_to, self.keep = int(cfg.get("up_to", 20)), int(cfg.get("keep", 0))
        self.boost: bool = cfg.get("boost", True)
        self.protect: bool = cfg.get("protect", False)
        colours: list[str] = cfg.get("colours", [])
        self.wanted = {c.lower() for c in colours}     # empty: every colour

    def wants(self, colour: str | None) -> bool:
        return bool(colour) and (not self.wanted or (colour or "").lower() in self.wanted)

    def colours_now(self, b: Bot) -> list[str]:
        out: dict[str, str] = {}
        for name, n in town.counts(b).items():
            col = colour_of(name)
            if col and n > 0 and self.wants(col):
                out[col.lower()] = col.title() if col.islower() else col
        return sorted(out.values())

    def combinable(self, b: Bot) -> str:
        """What could still be made: 4 shards of a level, or 3 gems (above those kept) below up_to."""
        have = town.counts(b)
        for col in self.colours_now(b):
            gems = gem_levels(b, col)
            for lv in range(1, self.up_to + 1):
                n = have.get(("Level %d %s Shard" % (lv, col)).lower(), 0)
                if n >= town.SHARDS_PER_GEM:
                    return "%d Level %d %s Shard" % (n, lv, col)
                if lv < self.up_to and gems.get(lv, 0) - self.keep >= 3:
                    return "%d Level %d %s" % (gems[lv], lv, col)
        return ""


def _bag_round(b: Bot, job: _BagJob, blocked: list[str]) -> None:
    """One round of make_most: every shard of the chosen colours into gems of its level, then each
    colour up, level by level."""
    shards = {name: 10 ** 9 for name in town.counts(b) if town.gem_of(name) and job.wants(colour_of(name))}
    if shards:
        town.make_gems(b, shards, opener=lambda: open_craftsman(b, "gem_craftsman", job.where))
    for col in job.colours_now(b):
        for lv in range(1, job.up_to):
            k = max(0, gem_levels(b, col).get(lv, 0) - job.keep) // 3
            if k > 0:
                raise_gems(b, col, lv, k, job.boost, job.protect, job.where, blocked=blocked)
            if b.stop_event.is_set():
                raise Stopped(_("stopped"))


def _idle_round(b: Bot, left: str, idle: int) -> bool:
    """The idle-th round in a row that changed nothing although the bag still has `left` to make
    something of, with nothing known in the way: a menu that didn't open, a walk cut short, a late
    or lost answer. True: try another round (IDLE_ROUNDS times at most); False: stop, said why."""
    if idle > IDLE_ROUNDS:
        b.log(_("the Gem Craftsman made nothing in {n} rounds in a row although the bag still has {what} - "
                "stopping; see logs\\dialogs.log", n=idle, what=left), speak=True)
        return False
    b.log(_("nothing came of this round although the bag still has {what} - trying again", what=left))
    b.close_dialogs()
    return True


def gem_maker(b: Bot, cfg: dict[str, Any], check_only: bool=False) -> None:
    """The Gem Maker. cfg["mode"] "bag" makes the most of the bag (make_most); otherwise cfg["count"] more Level cfg["level"] gems of cfg["colour"] ("most": as many as
    can be made). Each round: that colour's shards into gems (4 make 1), then gems a level up (3
    make 1, half the time) as far as the target needs, and when the bag runs short, Level 1 shards
    from the storehouse or the Shard Market (the only level it sells). A failed combine leaves a
    shard that the next round makes back into a gem; shards left over (fewer than four) are the +20%
    boost. cfg is the profile's "gem_maker" (see gui/crafting.py)."""
    if cfg.get("mode") == "bag":
        return make_most(b, cfg, check_only)
    colour = (cfg.get("colour") or "").strip()
    if not colour:
        raise Stopped(_("set it up first: Tools, {tool}", tool=_("Gem Maker")))
    job = _GemJob(b, cfg, colour)
    if check_only:
        _gem_check(b, job)
        return
    idle = 0
    for _round in range(MAX_ROUNDS):
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        if not job.most and gem_levels(b, colour).get(job.level, 0) >= job.target:
            break
        before = _colour_state(b, colour)
        blocked: list[str] = []
        _gem_round(b, job, blocked)
        # Progress is what the bag shows, given its update a moment to come: a combine whose
        # answer is late is still progress, and the next round plans from the bag as it is.
        if _settled(b, before, lambda: _colour_state(b, colour)):
            idle = 0
            continue
        if blocked or (blocked := _fees_unpaid(b, job)):
            b.log(_("the Gem Maker can't go on: {why}", why=blocked[0]), speak=True)
            break
        left = _combinable(b, job)
        if left:
            idle += 1                 # enough to combine, nothing in the way, and still nothing happened
            if _idle_round(b, left, idle):
                continue
            break
        # 3. nothing left to make from: more Level 1 shards, if asked
        if job.supply == "none":
            b.log(_("nothing left to make {gem} from, and getting more {shard} is off", gem=job.gem, shard=job.shard1))
            break
        if not _gem_refill(b, job):
            break
        idle = 0
    else:
        b.log(_("the Gem Maker stopped after {n} rounds", n=MAX_ROUNDS), speak=True)
    _gem_report(b, job)
    b.close_dialogs()


def _combinable(b: Bot, job: _GemJob) -> str:
    """What the bag still holds that the Gem Maker could make something of: 4 or more shards of a
    level up to the target's, or 3 or more gems below it ("9 Level 1 Topaz"); "" for nothing."""
    gems, shards = gem_levels(b, job.colour), shard_levels(b, job.colour)
    for lv in range(1, job.level + 1):
        if lv < job.level and gems.get(lv, 0) >= 3:
            return "%d Level %d %s" % (gems[lv], lv, job.colour)
        if shards.get(lv, 0) >= town.SHARDS_PER_GEM:
            return "%d Level %d %s Shard" % (shards[lv], lv, job.colour)
    return ""


def _fees_unpaid(b: Bot, job: _GemJob) -> list[str]:
    """Shards to make into gems whose fee (as last seen at the craftsman) the spare arnebia can't
    pay: [why], or [] when that isn't what stands in the way."""
    fees: dict[str, int] = b.p.get("gem_fees") or {}
    spare = spare_arnebia(b)
    shards = shard_levels(b, job.colour)
    for lv in range(1, job.level + 1):
        gem = "Level %d %s" % (lv, job.colour)
        fee = fees.get(gem.lower())
        if shards.get(lv, 0) >= town.SHARDS_PER_GEM and fee and spare < fee:
            return [_("not enough spare arnebia to make {gem} (fee {fee} each)", gem=gem, fee=fee)]
    return []


def _colour_state(b: Bot, colour: str) -> tuple[tuple[tuple[int, int], ...], tuple[tuple[int, int], ...]]:
    """The colour's gems and shards in the bag, level by level: a round made progress only if this
    changed."""
    return tuple(sorted(gem_levels(b, colour).items())), tuple(sorted(shard_levels(b, colour).items()))


def _bag_gems(b: Bot) -> tuple[tuple[str, int], ...]:
    """Every gem and gem shard in the bag, for make_most's "did the round change anything"."""
    return tuple(sorted((n, k) for n, k in town.counts(b).items() if colour_of(n) and k))


class _GemJob:
    """What the Gem Maker was asked for, read once from its settings."""

    def __init__(self, b: Bot, cfg: dict[str, Any], colour: str) -> None:
        self.colour, self.level = colour, int(cfg.get("level", 1))
        self.most = cfg.get("amount") == "most"
        self.where = place(cfg)
        self.supply: str = cfg.get("supply", "none")
        self.max_price = int(cfg.get("max_price") or 0)
        self.boost: bool = cfg.get("boost", True)
        self.protect: bool = cfg.get("protect", False)
        self.opener: Callable[[], str] = lambda: open_craftsman(b, "gem_craftsman", self.where)
        self.gem, self.shard1 = "Level %d %s" % (self.level, colour), "Level 1 %s Shard" % colour
        self.start = gem_levels(b, colour).get(self.level, 0)
        self.target = self.start + max(1, int(cfg.get("count", 1)))


def _gem_check(b: Bot, job: _GemJob) -> None:
    """Check only: what there is, what it would take, the prices, and the craftsman open."""
    colour = job.colour
    gems, shards = gem_levels(b, colour), shard_levels(b, colour)
    b.log(_("{col}: shards {shards}; gems {gems}", col=colour,
            shards=", ".join("%d at level %d" % (n, lv) for lv, n in sorted(shards.items()) if n) or _("none"),
            gems=", ".join("%d at level %d" % (n, lv) for lv, n in sorted(gems.items()) if n) or _("none")))
    if not job.most:
        _short, need = shortfall(gems, shards, job.level, job.target - job.start, job.protect)
        b.log(_("{n} more {gem} would take about {k} more {shard}", n=job.target - job.start, gem=job.gem, k=need,
                shard=job.shard1), speak=True)
    if job.supply != "none":
        fetch_shards(b, job.shard1, 1, job.supply, job.max_price, check_only=True)
    open_craftsman(b, "gem_craftsman", job.where)
    b.log(_("the Gem Craftsman is open; nothing was made (check only)"))
    b.close_dialogs()


def _gem_round(b: Bot, job: _GemJob, blocked: list[str] | None = None) -> int:
    """One round of making: the colour's shards up to the target level into gems, then gems up a
    level at a time, only as many tries as the target still needs (all of them for "most").
    Returns how much was made or tried; blocked gets what stood in the way for good (raise_gems)."""
    colour, level = job.colour, job.level
    did = 0
    shards = shard_levels(b, colour)
    wanted = {"Level %d %s Shard" % (lv, colour): 10 ** 9 for lv in range(1, level + 1) if shards.get(lv, 0) >= 4}
    if wanted:
        did += town.make_gems(b, wanted, opener=job.opener)
    for lv in range(1, level):
        gems = gem_levels(b, colour)
        k = gems.get(lv, 0) // 3
        if not job.most:
            short, _need = shortfall(gems, shard_levels(b, colour), level,
                                     job.target - gems.get(level, 0), job.protect)
            k = min(k, 2 * short.get(lv + 1, 0))      # half the tries work
        if k > 0:
            made, lost = raise_gems(b, colour, lv, k, job.boost, job.protect, job.where, blocked=blocked)
            did += made + lost
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
    return did


def _gem_refill(b: Bot, job: _GemJob) -> int:
    """More Level 1 shards for what the target still needs, as many as the bag takes (from the
    storehouse in one go, from the market in its batches). How many came; 0, with the reason
    logged, when it's no use going on. Called only when nothing in the bag can be combined."""
    colour, level = job.colour, job.level
    want: int | None = None
    if not job.most:
        gems = gem_levels(b, colour)
        _short, want = shortfall(gems, shard_levels(b, colour), level, job.target - gems.get(level, 0), job.protect)
        if want <= 0:
            b.log(_("nothing left to make {gem} from", gem=job.gem), speak=True)
            return 0
    if room_for(b, job.shard1, 1.0) == 0:
        b.log(_("the bag is too full for more {shard}", shard=job.shard1), speak=True)
        return 0
    got = fill_with(b, job.shard1, 1.0, job.supply, job.max_price, chosen=[job.shard1], want=want)
    if not got:
        b.log(_("no more {shard} came into the bag - the Gem Maker stops here", shard=job.shard1), speak=True)
    return got


def _gem_report(b: Bot, job: _GemJob) -> None:
    have = gem_levels(b, job.colour).get(job.level, 0)
    if job.most:
        b.log(_("made {n} {gem}; {have} in the bag", n=have - job.start, gem=job.gem, have=have), speak=True)
    elif have >= job.target:
        b.log(_("done: made {n} {gem}; {have} in the bag", n=have - job.start, gem=job.gem, have=have), speak=True)
    else:
        b.log(_("made {n} of the {want} {gem} asked for; {have} in the bag", n=have - job.start,
                want=job.target - job.start, gem=job.gem, have=have), speak=True)


# ---------------------------------------------------------------------------------------- enhancing
def _item_row(b: Bot, name: str) -> tuple[Entry | None, int | None]:
    """The Forger's list entry for an item whose name contains `name`, and its level."""
    rx = re.compile(re.escape(name.strip()), re.I)
    for _r, _c, e in entries(b):
        texts = b.entry_texts(e)
        if any(rx.search(t) for t in texts):
            level = _num(r"enhancement level:\s*(\d+)", " ".join(texts), None)
            return e, level
    return None, None


# The Forger's crystal for an item, by the item's own level (the Forger's instructions, and the
# game's players): gear of levels 15-29 takes Level 1 crystals, 30-44 Level 2, 45-59 Level 3, then
# one crystal level more for every 10 gear levels. The bot reads the real requirement from the
# Forger's menu each try; this is for the window's notes.
CRYSTAL_LEVELS = ((100, 8), (90, 7), (80, 6), (70, 5), (60, 4), (45, 3), (30, 2), (15, 1))
MAX_ENHANCE = 12          # +12 (Godbless) is the highest enhancement level
# Equipment the Forger never enhances (players' word for it), by the game's own names (its english
# language pack, more\equipment.txt; the diamond ring's name carries the "lzs" mark-up): the three
# wedding rings, the Mist god Emblem, the four bags (soft pouch, hot bag, weightless sack, Realm
# Pack: levels 40, 60, 80, 100), the two food-shaped commemorative badges (Five-Nuts Osmanthus Cake,
# Crispy potato and vegetable pie) - and every medal and badge (NOT_ENHANCEABLE_WORD): the pack has
# 30 medals (attack medals one to five star, contribution, demon chasing, hunting, arena, tactical,
# tutor ...) and the War badges, and no other equipment with either word in its name.
NOT_ENHANCEABLE = frozenset({"gold wedding ring", "platinum wedding ring", "diamond wedding ring",
                             "mist god emblem",
                             "soft pouch", "hot bag", "weightless sack", "realm pack",
                             "five-nuts osmanthus cake", "crispy potato and vegetable pie"})
NOT_ENHANCEABLE_WORD = re.compile(r"\b(medal|badge)\b", re.I)


def gear_name(name: str) -> str:
    """An equipment name as the player reads it: the game's "lzs" mark-up taken out
    ("lzs0lzs2243lzs1lzsdiamond wedding ring" -> "diamond wedding ring")."""
    return re.sub(r"(lzs\d*)+", "", name).strip()


def enhanceable(name: str) -> bool:
    """False for equipment the Forger never enhances (wedding rings, medals and badges, the Mist god
    Emblem, the bags), so it isn't offered."""
    shown = gear_name(name)
    return shown.lower() not in NOT_ENHANCEABLE and not NOT_ENHANCEABLE_WORD.search(shown)


def _crystals(b: Bot, kind: str, level: int) -> int:
    return town.counts(b).get(("Level %d %s Enhance Crystal" % (level, kind)).lower(), 0)


def _recipe(b: Bot, kind: str, level: int) -> dict[str, int] | None:
    """{"shards", "fee"} for one crystal of that kind and level, as last read at the Gem Craftsman."""
    recipes: dict[str, dict[str, int]] = b.p.get("crystal_recipes") or {}
    return recipes.get("%s %d" % (kind.lower(), level))


def make_crystals(b: Bot, kind: str, level: int, n: int, where: str) -> int:
    """Make n Level `level` <kind> Enhance Crystals from shards at the Gem Craftsman. Remembers how
    many shards and how much arnebia one takes (the craftsman's own line), even when it makes none."""
    open_craftsman(b, "gem_craftsman", where)
    if b.choose(r"^\s*shard synthesis") is None or not b.wait_dialog(3.0):
        return 0
    if b.choose(r"^\s*%s Enhance Crystal Shard\s*\d+\s*$" % kind) is None or not b.wait_dialog(3.0):
        b.log(_("no {kind} Enhance Crystal Shards to make crystals from", kind=kind))
        b.close_dialogs()
        return 0
    if b.choose(r"^\s*synthesize Level %d %s Enhance Crystal\s*$" % (level, kind)) is None or not b.wait_dialog(3.0):
        b.close_dialogs()
        return 0
    text = menu_text(b)
    need = _num(r"needs\s+(\d+)", text, 0)
    fee = _num(r"fee\s+(\d+)\s+arnebia", text, 0)
    if need:
        b.p.setdefault("crystal_recipes", {})["%s %d" % (kind.lower(), level)] = {"shards": need, "fee": fee}
    shards = town.counts(b).get(("%s Enhance Crystal Shard" % kind).lower(), 0)
    k = min(n, shards // need if need else 0)
    if fee:
        k = min(k, max(0, spare_arnebia(b)) // fee)
    if k <= 0:
        if n > 0:
            b.log(_("not enough shards or spare arnebia for a Level {lv} {kind} Enhance Crystal ({need} shards, "
                    "fee {fee})", lv=level, kind=kind, need=need, fee=fee))
        b.close_dialogs()
        return 0
    before = _crystals(b, kind, level)
    town.pick(b, r"please enter", k)
    b.sleep(0.6)
    b.close_dialogs()
    got = _crystals(b, kind, level) - before
    b.log(_("made {n} Level {lv} {kind} Enhance Crystals", n=got, lv=level, kind=kind))
    return got


def stock_crystals(b: Bot, cfg: dict[str, Any], kind: str, level: int, want: int, where: str) -> int:
    """Bring the bag up to `want` Level `level` <kind> Enhance Crystals: crystal shards from the
    storehouse and/or the Shard Broker first when cfg allows (cfg["crystal_supply"], at no more than
    cfg["crystal_max_price"] each), then made into crystals at the Gem Craftsman. Returns the
    crystals made."""
    short = want - _crystals(b, kind, level)
    if short <= 0:
        return 0
    shard = "%s Enhance Crystal Shard" % kind
    supply: str = cfg.get("crystal_supply", "both")
    max_price = int(cfg.get("crystal_max_price") or 0)
    if supply != "none":
        recipe = _recipe(b, kind, level)
        if recipe is None:
            # how many shards a crystal takes is on the craftsman's menu, which lists only shards
            # we carry: bring a few, read it, then get the rest
            if town.counts(b).get(shard.lower(), 0) == 0:
                fetch_shards(b, shard, 5, supply, max_price, chosen=[shard])
            make_crystals(b, kind, level, 0, where)
            recipe = _recipe(b, kind, level)
        if recipe:
            need = short * recipe["shards"] - town.counts(b).get(shard.lower(), 0)
            room = room_for(b, shard, 1.0)
            if room is not None:
                need = min(need, room)
            if need > 0:
                fetch_shards(b, shard, need, supply, max_price, chosen=[shard])
    return make_crystals(b, kind, level, short, where)


@dataclass
class _EnhanceJob:
    """What the Equipment Enhancer was asked for, read once from its settings."""
    where: str
    target: int
    protect_mode: str            # always | from | never
    protect_from: int
    max_fails: int
    making: bool
    stock: int
    refill: int
    stocked: set[tuple[str, int]] = field(default_factory=set[tuple[str, int]])   # (kind, level) stocked this run


@dataclass
class _Offer:
    """The Forger's offer for an item's next level, as its menu words it."""
    frm: int
    need_n: int
    need_lv: int
    kind: str                    # Weapon | Equipment
    cost: int
    chance: float | None
    runes: int
    protect: bool = False
    have_runes: int = 0


def enhance(b: Bot, cfg: dict[str, Any], check_only: bool=False) -> None:
    """The Equipment Enhancer: enhance each chosen item up to cfg["up_to"] (+12 at most). Before
    the first try at a crystal level the bag is stocked to cfg["crystal_stock"] crystals; when they
    run out, cfg["crystal_refill"] more are made. cfg is the profile's "enhancer" (see
    gui/crafting.py)."""
    job = _EnhanceJob(where=place(cfg), target=min(MAX_ENHANCE, int(cfg.get("up_to", 10))),
                      protect_mode=cfg.get("protect", "always"),
                      protect_from=int(cfg.get("protect_from", 8)), max_fails=int(cfg.get("max_fails", 5)),
                      making=cfg.get("make_crystals", True), stock=int(cfg.get("crystal_stock", 0)),
                      refill=int(cfg.get("crystal_refill", 10)))
    # a wedding ring ticked before the list stopped offering one is left out, not walked to the Forger for
    items: list[str] = [i for i in cfg.get("items", []) if enhanceable(i)]
    for item in items:
        fails = 0
        while not b.stop_event.is_set():
            # each try ends in: "stop" (the whole job), "next" (the next item), "again" (this one)
            outcome, offer = _forger_offer(b, job, item)
            if offer is not None:          # no outcome yet: there is a level to try
                if check_only:
                    _enhance_check(b, cfg, job, item, offer)
                    outcome = "next"
                else:
                    outcome, fails = _enhance_try(b, cfg, job, item, offer, fails)
            if outcome == "stop":
                return
            if outcome == "next":
                break
    b.close_dialogs()


def _forger_offer(b: Bot, job: _EnhanceJob, item: str) -> tuple[str, None] | tuple[None, _Offer]:
    """The Forger's offer for the item's next level: (None, what it takes), or ("stop" | "next",
    None) when there is nothing to try."""
    open_craftsman(b, "forger", job.where)
    if b.choose(r"^\s*enhance equipment") is None or not b.wait_dialog(3.0):
        b.log(_("the Forger has no enhance equipment"), speak=True)
        return "stop", None
    e, level = _item_row(b, item)
    if e is None:
        b.log(_("the Forger didn't list {item} (is it equipped or in the bag?)", item=item), speak=True)
        return "next", None
    if level is not None and level >= job.target:
        b.log(_("{item} is +{lv}: done", item=item, lv=level), speak=True)
        return "next", None
    b.choose(re.escape(item.strip()))
    if not b.wait_dialog(3.0):
        return "next", None
    # the item list stays up until the Forger's page for the item comes: wait for what it takes,
    # never guess it (a guessed Level 1 would have the bot make the wrong crystals)
    need, text = wait_text(b, r"requires\s+(\d+)\s+Level\s+(\d+)", OFFER_WAIT)
    if need is None:
        b.log(_("the Forger didn't say which crystal {item} needs - skipping it", item=item), speak=True)
        b.log(_("on the Forger's screen: {screen}", screen=text or _("nothing")))
        b.close_dialogs()
        return "next", None
    o = _Offer(frm=_num(r"from\s+(\d+)\s+to", text, level or 0),
               need_n=int(need.group(1)),
               need_lv=int(need.group(2)),
               kind="Weapon" if re.search(r"Weapon Enhance Crystal", text, re.I) else "Equipment",
               cost=_num(r"costs\s+(\d+)\s+arnebia", text, 0),
               chance=_num(r"([\d.]+)%\s*success rate", text, None, float),
               runes=_num(r"Use\s+(\d+)\s+\w+ Enhance Protection Rune", text, 0))
    o.protect = job.protect_mode == "always" or (job.protect_mode == "from" and o.frm >= job.protect_from)
    o.have_runes = town.counts(b).get(("%s Enhance Protection Rune" % o.kind).lower(), 0)
    return None, o


def _enhance_check(b: Bot, cfg: dict[str, Any], job: _EnhanceJob, item: str, o: _Offer) -> None:
    """Check only: what the next level takes, what there is, and where crystal shards would come from."""
    b.log(_("{item}: +{a} to +{b} needs {n} Level {lv} {kind} Enhance Crystal (you have {have}), "
            "{cost} arnebia, {chance}% chance; protection takes {runes} runes (you have {hr})",
            item=item, a=o.frm, b=o.frm + 1, n=o.need_n, lv=o.need_lv, kind=o.kind,
            have=_crystals(b, o.kind, o.need_lv), cost=o.cost, chance=o.chance if o.chance is not None else "?",
            runes=o.runes, hr=o.have_runes), speak=True)
    b.close_dialogs()
    shard = "%s Enhance Crystal Shard" % o.kind
    recipe = _recipe(b, o.kind, o.need_lv)
    b.log(_("{n} {shard} in the bag; a Level {lv} crystal takes {per}", n=town.counts(b).get(shard.lower(), 0),
            shard=shard, lv=o.need_lv, per=_("{k} shards and {fee} arnebia", k=recipe["shards"], fee=recipe["fee"])
            if recipe else _("a number the Gem Craftsman will tell")))
    if job.making and cfg.get("crystal_supply", "both") != "none":
        fetch_shards(b, shard, 1, cfg.get("crystal_supply", "both"),
                     int(cfg.get("crystal_max_price") or 0), check_only=True)


def _enhance_try(b: Bot, cfg: dict[str, Any], job: _EnhanceJob, item: str, o: _Offer, fails: int) -> tuple[str, int]:
    """One try at the next level: arnebia, crystals (stocked or refilled), runes, then forge.
    (outcome, failures in a row)."""
    if o.cost and spare_arnebia(b) < o.cost:
        b.log(_("not enough spare arnebia to enhance {item} ({cost})", item=item, cost=o.cost), speak=True)
        b.close_dialogs()
        return "stop", fails
    crystals = _crystals_for(b, cfg, job, o)
    if crystals:
        return crystals, fails
    if not _runes_ready(b, cfg, item, o):
        return "stop", fails
    if b.choose(r"start forging") is None:
        return "stop", fails
    b.sleep(1.0)
    # read the item's level again from the Forger's list
    b.close_dialogs()
    open_craftsman(b, "forger", job.where)
    if b.choose(r"^\s*enhance equipment"):
        b.wait_dialog(3.0)
    _e, now = _item_row(b, item)
    b.close_dialogs()
    if now is not None and now > o.frm:
        b.log(_("{item}: +{lv}, success", item=item, lv=now))
        return "again", 0
    fails += 1
    b.log(_("{item}: failed, now +{lv} ({n} in a row)", item=item,
            lv=now if now is not None else "?", n=fails))
    if fails >= job.max_fails:
        b.log(_("{n} failures in a row - stopping {item}", n=fails, item=item), speak=True)
        return "next", fails
    return "again", fails


def _crystals_for(b: Bot, cfg: dict[str, Any], job: _EnhanceJob, o: _Offer) -> str | None:
    """The crystals this level takes: stocked up the first time at a crystal level, refilled when
    they run out. None when there are enough; "again" (back to the Forger with them) or "stop"."""
    have = _crystals(b, o.kind, o.need_lv)
    want = 0
    if job.making and (o.kind, o.need_lv) not in job.stocked and have < job.stock:
        want = max(job.stock, o.need_n)                     # first time at this crystal level: stock up
    elif have < o.need_n:
        want = have + max(job.refill, o.need_n)             # ran out: make the refill
    job.stocked.add((o.kind, o.need_lv))
    if not want:
        return None
    b.close_dialogs()
    if job.making:
        b.log(_("getting Level {lv} {kind} Enhance Crystals up to {n}", lv=o.need_lv, kind=o.kind, n=want))
        stock_crystals(b, cfg, o.kind, o.need_lv, want, job.where)
    if _crystals(b, o.kind, o.need_lv) < o.need_n:
        b.log(_("out of Level {lv} {kind} Enhance Crystals - stopping", lv=o.need_lv, kind=o.kind), speak=True)
        return "stop"
    return "again"


def _runes_ready(b: Bot, cfg: dict[str, Any], item: str, o: _Offer) -> bool:
    """The protection rune ticked when asked for (never forging without it), and the boost rune
    when wanted and there is one. False: stop."""
    if o.protect:
        if entry_like(b, r"don't have enough \w+ Enhance Protection Rune") or o.have_runes < o.runes:
            b.log(_("not enough protection runes for {item} - stopping, as asked", item=item), speak=True)
            b.close_dialogs()
            return False
        if not tick(b, r"Enhance Protection Rune", True):
            b.log(_("couldn't tick the protection rune - not forging without it"), speak=True)
            b.close_dialogs()
            return False
    if cfg.get("boost", False) and not entry_like(b, r"don't have enough \w+ Enhance Boost Rune"):
        tick(b, r"Enhance Boost Rune", True)
    return True
