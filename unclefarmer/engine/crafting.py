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

import re
from types import SimpleNamespace
from typing import Any, Callable

from .. import atlas, knowledge
from ..i18n import _
from . import bagrules, town
from .bot import Stopped
from .botparts.base import STOPPING, note_failure

# The craftsmen in a gang's base: the same cells in every gang (seen in two gangs' bases).
GANG_SPOTS = {"forger": ("Forger", (28, 94)), "gem_craftsman": ("Gem Craftsman", (59, 93)),
              "artificer": ("Artificer", (53, 94))}
GEM = re.compile(r"^\s*Level (\d+) ([A-Za-z' ]+?)\s*$", re.I)


# ---------------------------------------------------------------------------------------- getting there
def in_gang(b):
    mid = b.map_id()
    return atlas.kind(mid) == "gang" or "gang" in atlas.label(mid).lower()


def place(cfg):
    """Where a tool's craftsman is: "gang" (your gang's), "gang:<name>" (that gang's) or "city"."""
    where = cfg.get("where", "gang")
    if where == "other_gang":
        name = (cfg.get("gang_name") or "").strip()
        return "gang:" + name if name else "gang"
    return where


def _reachable(b, role):
    """We stand in the half of the gang base where that craftsman is (see the module notes)."""
    return reachable_cell(b, GANG_SPOTS[role][1])


def reachable_cell(b, cell):
    """We stand in the part of this map that cell is in: a gang base's two halves are walled apart."""
    me = b.me()
    try:
        m = atlas.model(b.map_id())
        return m.comp_of(int(me["x"]), int(me["y"])) == m.comp_of(*cell)
    except STOPPING:
        raise
    except Exception as e:
        note_failure(b, "reading the map", e)
        return True                       # no map data: let the walk find out


def _pick_gang(b, gang):
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


def pick_at_entrance(b, gang):
    """At an open Gang Entrance menu: the gang named `gang`, through "all gangs" (or "Allied
    gangs"), chosen. Its own menu (which hall to enter) is what opens next. False, said, when no
    list has it."""
    b.log(_("looking for the gang {gang} at the Gang Entrance", gang=gang))
    top = b.dialog().get("top") or {}
    for listing in (r"(?i)all\s+gangs", r"(?i)allied\s+gangs"):
        if b.find_entry(top, listing) is not None and b.choose(listing) is not None and _pick_gang(b, gang):
            return True
        b.close_dialogs()
        town.visit(b, "gang_entrance")
        top = b.dialog().get("top") or {}
    b.log(_("the Gang Entrance lists no gang called {gang}", gang=gang), speak=True)
    return False


def enter_gang(b, gang=None, role="gem_craftsman"):
    """Into a gang base's OUTER hall, where the craftsmen are: your own gang's through the nearest
    Gang Entrance, or the gang named `gang` through its "all gangs" list. Already in a gang base
    but in the walled-off inner hall (or the wrong gang's): out and in again."""
    if in_gang(b) and _reachable(b, role) and not gang:
        return True
    b.close_dialogs()
    town.visit(b, "gang_entrance")          # (leaves a gang base first when we are in one)
    mid = b.map_id()
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
    if not _reachable(b, role):
        b.log(_("landed in the gang's inner hall, which is walled off from the craftsmen"), speak=True)
        return False
    b.log(_("in {where}", where=b.snap().get("map", {}).get("name") or atlas.label(b.map_id())))
    return True


def open_craftsman(b, role, where):
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
def menu_text(b):
    return town._menu_text(b)


def entries(b):
    """[(row, col, entry)] of the open menu."""
    top = b.dialog().get("top") or {}
    return [(r, c, e) for r, row in enumerate(top.get("rows", [])) for c, e in enumerate(row) if e]


def entry_like(b, pattern):
    rx = re.compile(pattern, re.I)
    for _r, _c, e in entries(b):
        if any(rx.search(t) for t in b.entry_texts(e)):
            return e
    return None


def is_ticked(e):
    """A checkbox entry's state: the Forger writes "selected" / "not selected"; others carry it in
    the last number of the entry's command."""
    text = " ".join(t for t in (e.get("text"), e.get("raw"), e.get("f"), e.get("g")) if t).lower()
    if "not selected" in text:
        return False
    if "selected" in text:
        return True
    cmd = e.get("cmd") or []
    return bool(cmd and cmd[-1])


def tick(b, pattern, want=True):
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


def spare_arnebia(b):
    return town.counts(b).get("arnebia", 0) - int(b.p.get("gem_keep_arnebia", 20000))


def _num(rx, text, default=None, cast: Callable[[str], Any] = int):
    m = re.search(rx, text, re.I)
    return cast(m.group(1)) if m else default


# ---------------------------------------------------------------------------------------- gems
def gem_levels(b, colour):
    """{level: count} of a gem colour in the bag."""
    out = {}
    for name, n in town.counts(b).items():
        m = GEM.match(name)
        if m and m.group(2).strip().lower() == colour.lower() and "shard" not in name:
            out[int(m.group(1))] = out.get(int(m.group(1)), 0) + n
    return out


def raise_gems(b, colour, level, k, boost, protect, where, check_only=False):
    """Combine 3 Level `level` gems into 1 a level higher, k times. With protect, one at a time
    (only single synthesis takes a protection rune); otherwise in one batch. Returns (made, lost)."""
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
            b.log(_("not enough spare arnebia to raise {gem} (fee {fee})", gem=gem, fee=fee), speak=True)
            break
        want_boost = boost and level >= 2 and town.counts(b).get(shard.lower(), 0) > 0
        if protect:
            if not _synthesize_one(b, want_boost, shard, section, where):
                break
            done += 1
        else:
            if not _synthesize_batch(b, want_boost, each):
                break
            done = each
    b.close_dialogs()
    after = town.counts(b)
    made = after.get(nxt.lower(), 0) - before.get(nxt.lower(), 0)
    lost = max(0, done - made)
    if done:
        b.log(_("{gem}: {tries} tries, {made} {nxt} made, {lost} failed", gem=gem, tries=done, made=made,
                nxt=nxt, lost=lost))
    return made, lost


def _synthesize_one(b, want_boost, shard, section, where):
    """One protected synthesis (the boost shard added when wanted). False: stop."""
    if not tick(b, r"add 1 .*Shard", want_boost) and want_boost:
        b.log(_("couldn't add the {shard}", shard=shard))
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


def _synthesize_batch(b, want_boost, each):
    """A batch synthesis of `each` (the boost shards used automatically when wanted). False: stop."""
    if want_boost:
        tick(b, r"Automatically use .*Shard", True)
    if not town.pick(b, r"Please enter quantity|please enter", each):
        b.log(_("the Gem Craftsman didn't ask how many"))
        return False
    b.sleep(0.6)
    return True


# ---------------------------------------------------------------------------------------- getting shards
def storage_count(b, item):
    """How many of item the storehouse holds ("1; 20 Memory Butterfly"), with its list open."""
    for _r, _c, e in entries(b):
        for t in b.entry_texts(e):
            m = re.search(r"(\d+)\s+" + re.escape(item) + r"\s*($|[;|,])", t, re.I)
            if m:
                return int(m.group(1))
    return 0


def take_from_storage(b, item, n, check_only=False):
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


def fee_per_shard(b, shard):
    """The Gem Craftsman's fee for making this shard's gem, per shard (a quarter of the gem's fee),
    as last seen; None if the bot hasn't seen it yet."""
    gem = town.gem_of(shard)
    fee = (b.p.get("gem_fees") or {}).get(gem.lower()) if gem else None
    return None if fee is None else fee / float(town.SHARDS_PER_GEM)


def fees_owed(b, shards):
    """The gem fees still to pay for the chosen shards already in the bag."""
    have = town.counts(b)
    return sum(have.get(s.lower(), 0) * (fee_per_shard(b, s) or 0) for s in shards)


def buy_shards(b, shard, n, max_price=0, check_only=False, owed=0):
    """Buy up to n of a shard at the nearest Shard Market, at no more than max_price each (0: any
    price), and never below the arnebia reserve. The market's own row gives the price. Shards are
    bought only as far as the gem fee for them can be paid too: a shard bought but never made into a
    gem is money lost."""
    if n <= 0:
        return 0
    _s, markets = town.shard_catalog()
    if not markets:
        b.log(_("I don't know where a Shard Market is"), speak=True)
        return 0
    town.visit(b, None, candidates=town.market_candidates(markets))
    before = town.counts(b).get(shard.lower(), 0)
    try:
        pattern = town.item_pattern(shard)
        e = entry_like(b, pattern)
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
        if price:
            fee = fee_per_shard(b, shard)
            budget = max(0, spare_arnebia(b) - int(owed))
            if fee is None:
                budget = int(budget * 0.8)        # fee not seen yet: leave a fifth for it
                fee = 0
            n = min(n, int(budget // (price + fee)))
        if n <= 0:
            b.log(_("not enough spare arnebia to buy {shard}", shard=shard), speak=True)
            return 0
        left = n
        while left > 0:                       # the box takes at most 2000 at a time
            if b.choose(pattern) is None or not b.wait_dialog(3.0):
                break
            step = min(left, 2000)
            if not town.pick(b, r"\bbuy\b", step):
                break
            left -= step
    finally:
        b.close_dialogs()
    got = town.counts(b).get(shard.lower(), 0) - before
    if got:
        b.log(_("bought {n} {shard}", n=got, shard=shard))
    return got


def fetch_shards(b, shard, n, supply, max_price=0, check_only=False, chosen=()):
    """Bring up to n of a shard into the bag: supply "both" (storehouse, then the market),
    "storage" or "buy". chosen: every shard the Gem Maker is working on, whose fees are kept back
    when buying. Returns how many arrived."""
    got = 0
    if supply in ("both", "storage"):
        got += take_from_storage(b, shard, n, check_only)
    if supply in ("both", "buy") and got < n:
        got += buy_shards(b, shard, n - got, max_price, check_only, owed=fees_owed(b, chosen or (shard,)))
    return got


def room_for(b, shard, share):
    """How many of a shard fit into `share` of the bag's free room (up to the profile's bag_full_at
    percent), from the character sheet's load and the shard's learned weight; None if its weight
    isn't known yet."""
    sheet = b.refresh_sheet(force=True) or {}
    if not sheet.get("maxload"):
        return None
    free = sheet["maxload"] * float(b.p.get("bag_full_at", 100)) / 100.0 - sheet.get("load", 0)
    w = bagrules.weights().get(shard.lower())
    if not w:
        return None
    return max(0, min(2000, int(free * share // w)))


def fill_with(b, shard, share, supply, max_price, chosen=()):
    """Fill `share` of the bag's free room with a shard. Learns the shard's weight first if needed
    (the load before and after a small first batch)."""
    k = room_for(b, shard, share)
    got = 0
    if k is None:
        before = town._load(b)
        got = fetch_shards(b, shard, 20, supply, max_price, chosen=chosen)
        if got:
            bagrules.learn_weight(shard, town._load(b), before, got)      # the load went up by got shards
        k = room_for(b, shard, share)
        if k is None:
            return got
    return got + fetch_shards(b, shard, k, supply, max_price, chosen=chosen)


def colour_of(name):
    """The gem colour of a gem or shard name ("Level 2 Topaz Shard" -> "Topaz"), or None."""
    m = re.match(r"^\s*Level \d+ ([A-Za-z' ]+?)(?: Shard)?\s*$", name or "", re.I)
    if not m or "crystal" in m.group(1).lower():
        return None
    return m.group(1).strip()


def shard_levels(b, colour):
    """{level: count} of a colour's shards in the bag."""
    out = {}
    for name, n in town.counts(b).items():
        m = town.GEM_SHARD.match(name)
        if m and m.group(2).strip().lower() == colour.lower():
            out[int(m.group(1))] = out.get(int(m.group(1)), 0) + n
    return out


def shortfall(gems, shards, level, missing, protect):
    """What making `missing` more Level `level` gems still needs, from the gems and shards of that
    colour ({level: count}): {lv: gems still to come at lv} for lv 1..level, and the Level 1 shards
    to get. 4 shards make a gem; a combine works half the time, so a gem a level up takes about 4
    gems below it (3 with protection runes: a failure then keeps all three). Boost shards are left
    out, so the estimate errs high; each round of the Gem Maker plans again from the real bag."""
    per = 3 if protect else 4
    short, out = max(0, missing), {}
    for lv in range(level, 0, -1):
        short = max(0, short - (gems.get(lv, 0) if lv < level else 0) - shards.get(lv, 0) // 4)
        out[lv] = short
        if lv > 1:
            short *= per
    return out, max(0, out.get(1, 0) * 4 - shards.get(1, 0) % 4)


def make_most(b, cfg, check_only=False):
    """Make the most of the gems and shards in the bag, colour by colour: shards into gems (4 make
    1), then gems up a level (3 make 1) as far as they go, again and again, because a failed combine
    leaves a shard that the next round turns back into a gem. Shards left over (fewer than four) are
    the +20% boost. It cleans the bag: many shards and small gems become a few big ones."""
    where = place(cfg)
    up_to, keep = int(cfg.get("up_to", 20)), int(cfg.get("keep", 0))
    boost, protect = cfg.get("boost", True), cfg.get("protect", False)
    opener = lambda: open_craftsman(b, "gem_craftsman", where)       # noqa: E731
    wanted_colours = {c.lower() for c in cfg.get("colours", [])}     # empty: every colour

    def colours_now():
        out = {}
        for name, n in town.counts(b).items():
            col = colour_of(name)
            if col and n > 0 and (not wanted_colours or col.lower() in wanted_colours):
                out[col.lower()] = col.title() if col.islower() else col
        return sorted(out.values())

    if check_only:
        for col in colours_now():
            have = town.counts(b)
            shards = {lv: have.get(("Level %d %s Shard" % (lv, col)).lower(), 0) for lv in range(1, up_to + 1)}
            gems = gem_levels(b, col)
            b.log(_("{col}: shards {shards}; gems {gems}", col=col,
                    shards=", ".join("%d at level %d" % (n, lv) for lv, n in sorted(shards.items()) if n) or _("none"),
                    gems=", ".join("%d at level %d" % (n, lv) for lv, n in sorted(gems.items()) if n) or _("none")))
        open_craftsman(b, "gem_craftsman", where)
        b.log(_("the Gem Craftsman is open; nothing was made (check only)"))
        b.close_dialogs()
        return
    for _round in range(12):
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        did = 0
        # 1. every shard of the chosen colours into gems of its level
        shards = {name: 10 ** 9 for name in town.counts(b) if town.gem_of(name) and colour_of(name)
                  and (not wanted_colours or (colour_of(name) or "").lower() in wanted_colours)}
        if shards:
            did += town.make_gems(b, shards, opener=opener)
        # 2. each colour up, level by level
        for col in colours_now():
            for lv in range(1, up_to):
                k = max(0, gem_levels(b, col).get(lv, 0) - keep) // 3
                if k > 0:
                    made, lost = raise_gems(b, col, lv, k, boost, protect, where)
                    did += made + lost
                if b.stop_event.is_set():
                    raise Stopped(_("stopped"))
        if not did:
            break
    b.log(_("the bag's gems are as high as they go"), speak=True)
    b.close_dialogs()


def gem_maker(b, cfg, check_only=False):
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
    for _round in range(40):
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        if not job.most and gem_levels(b, colour).get(job.level, 0) >= job.target:
            break
        if _gem_round(b, job):
            continue
        # 3. nothing left to make from: more Level 1 shards, if asked and if that is what's short
        if job.supply == "none" or not _gem_refill(b, job):
            break
    _gem_report(b, job)
    b.close_dialogs()


class _GemJob:
    """What the Gem Maker was asked for, read once from its settings."""

    def __init__(self, b, cfg, colour):
        self.colour, self.level = colour, int(cfg.get("level", 1))
        self.most = cfg.get("amount") == "most"
        self.where = place(cfg)
        self.supply = cfg.get("supply", "none")
        self.max_price = int(cfg.get("max_price") or 0)
        self.boost, self.protect = cfg.get("boost", True), cfg.get("protect", False)
        self.opener = lambda: open_craftsman(b, "gem_craftsman", self.where)
        self.gem, self.shard1 = "Level %d %s" % (self.level, colour), "Level 1 %s Shard" % colour
        self.start = gem_levels(b, colour).get(self.level, 0)
        self.target = self.start + max(1, int(cfg.get("count", 1)))


def _gem_check(b, job):
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


def _gem_round(b, job):
    """One round of making: the colour's shards up to the target level into gems, then gems up a
    level at a time, only as many tries as the target still needs (all of them for "most").
    Returns how much was made or tried."""
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
            made, lost = raise_gems(b, colour, lv, k, job.boost, job.protect, job.where)
            did += made + lost
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
    return did


def _gem_refill(b, job):
    """More Level 1 shards for what the target still needs (a bagful at most). How many came;
    0 or None when it's no use going on."""
    colour, level = job.colour, job.level
    gems = gem_levels(b, colour)
    if any(gems.get(lv, 0) >= 3 for lv in range(1, level)):
        return 0                              # gems left that wouldn't combine: runes or arnebia ran out
    if job.most:
        return fill_with(b, job.shard1, 1.0, job.supply, job.max_price, chosen=[job.shard1])
    _short, need = shortfall(gems, shard_levels(b, colour), level, job.target - gems.get(level, 0), job.protect)
    room = room_for(b, job.shard1, 1.0)       # a high level can take thousands: a bagful at a time
    if room is not None and need > room:
        need = room
        if not need:
            b.log(_("the bag is too full for more {shard}", shard=job.shard1), speak=True)
    return fetch_shards(b, job.shard1, need, job.supply, job.max_price, chosen=[job.shard1]) if need else 0


def _gem_report(b, job):
    have = gem_levels(b, job.colour).get(job.level, 0)
    if job.most:
        b.log(_("made {n} {gem}; {have} in the bag", n=have - job.start, gem=job.gem, have=have), speak=True)
    elif have >= job.target:
        b.log(_("done: made {n} {gem}; {have} in the bag", n=have - job.start, gem=job.gem, have=have), speak=True)
    else:
        b.log(_("made {n} of the {want} {gem} asked for; {have} in the bag", n=have - job.start,
                want=job.target - job.start, gem=job.gem, have=have), speak=True)


# ---------------------------------------------------------------------------------------- enhancing
def _item_row(b, name):
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


def _crystals(b, kind, level):
    return town.counts(b).get(("Level %d %s Enhance Crystal" % (level, kind)).lower(), 0)


def _recipe(b, kind, level):
    """{"shards", "fee"} for one crystal of that kind and level, as last read at the Gem Craftsman."""
    return (b.p.get("crystal_recipes") or {}).get("%s %d" % (kind.lower(), level))


def make_crystals(b, kind, level, n, where):
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


def stock_crystals(b, cfg, kind, level, want, where):
    """Bring the bag up to `want` Level `level` <kind> Enhance Crystals: crystal shards from the
    storehouse and/or the Shard Broker first when cfg allows (cfg["crystal_supply"], at no more than
    cfg["crystal_max_price"] each), then made into crystals at the Gem Craftsman. Returns the
    crystals made."""
    short = want - _crystals(b, kind, level)
    if short <= 0:
        return 0
    shard = "%s Enhance Crystal Shard" % kind
    supply = cfg.get("crystal_supply", "both")
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


def enhance(b, cfg, check_only=False):
    """The Equipment Enhancer: enhance each chosen item up to cfg["up_to"] (+12 at most). Before
    the first try at a crystal level the bag is stocked to cfg["crystal_stock"] crystals; when they
    run out, cfg["crystal_refill"] more are made. cfg is the profile's "enhancer" (see
    gui/crafting.py)."""
    job = SimpleNamespace(where=place(cfg), target=min(MAX_ENHANCE, int(cfg.get("up_to", 10))),
                          protect_mode=cfg.get("protect", "always"),          # always | from | never
                          protect_from=int(cfg.get("protect_from", 8)), max_fails=int(cfg.get("max_fails", 5)),
                          making=cfg.get("make_crystals", True), stock=int(cfg.get("crystal_stock", 0)),
                          refill=int(cfg.get("crystal_refill", 10)),
                          stocked=set())                                     # (kind, level) already stocked this run
    for item in cfg.get("items", []):
        fails = 0
        while not b.stop_event.is_set():
            # each try ends in: "stop" (the whole job), "next" (the next item), "again" (this one)
            outcome, offer = _forger_offer(b, job, item)
            if outcome is None:
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


def _forger_offer(b, job, item):
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
    text = menu_text(b)
    o = SimpleNamespace(frm=_num(r"from\s+(\d+)\s+to", text, level or 0),
                        need_n=_num(r"requires\s+(\d+)\s+Level", text, 1),
                        need_lv=_num(r"requires\s+\d+\s+Level\s+(\d+)", text, 1),
                        kind="Weapon" if re.search(r"Weapon Enhance Crystal", text, re.I) else "Equipment",
                        cost=_num(r"costs\s+(\d+)\s+arnebia", text, 0),
                        chance=_num(r"([\d.]+)%\s*success rate", text, None, float),
                        runes=_num(r"Use\s+(\d+)\s+\w+ Enhance Protection Rune", text, 0))
    o.protect = job.protect_mode == "always" or (job.protect_mode == "from" and o.frm >= job.protect_from)
    o.have_runes = town.counts(b).get(("%s Enhance Protection Rune" % o.kind).lower(), 0)
    return None, o


def _enhance_check(b, cfg, job, item, o):
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


def _enhance_try(b, cfg, job, item, o, fails):
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


def _crystals_for(b, cfg, job, o):
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


def _runes_ready(b, cfg, item, o):
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
