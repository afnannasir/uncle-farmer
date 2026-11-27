"""Tools, Potion Maker: the gang Pharmacist's "Make Medicine" and "Fuse Medicine".

What the game does (one recorded hand run, 2026-11-21, in two gangs' bases):
  - the Pharmacist stands at 52,71 in the OUTER hall of every gang base; its menu is "Make Medicine"
    and "Fuse Medicine";
  - "Make Medicine" lists 28 recipes, each "synthesize 50 Small Red Potion" + "needs 2 Lesser Blood"
    (the needs run together: "needs 2 Lesser Blood1 medium Blood1 Higher blood"). A recipe opens a
    page "synthesize 50 Small Red Potion; cost 608 arnebia" + "start synthesis": the fee is read there
    before anything is spent. It was 608 in the player's own gang and 765 in another gang. Each
    "start synthesis" makes one batch of 50 (or 45). Without the materials the game says "not enough
    materials" and makes nothing;
  - "Fuse Medicine" lists 8 fusions, "merge of 1 Medium Red Potion requires 5 Small Red Potion": Small
    to Medium to Large, red and blue, for players and for pets. It is free, a number box takes up to
    100 at a time, and there is no page to confirm;
  - the materials (Lesser, medium, Higher, Excellent and Premium Blood and Pelt) drop from monsters
    and are sold on the player market (Ansible Space-time Marketplace, category "materials").

The plan is worked out from the bag (plan(), pure): what the player wants to have of each potion,
made the cheaper way for the sizes that can be fused (Smalls, fused up) or each from its own recipe.
The materials come from the bag, then the town storehouse, then the market: cheapest lots first,
never above the most a piece the player set for that material, and never above the run's gold limit.
Nothing is spent below the arnebia reserve, and "check only" reads the real fees and says what it
would do without making anything.
"""

from __future__ import annotations

import copy
import math
import re
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import Any

from ..clock import clock_for
from ..i18n import N_, _
from . import bagrules, chores, crafting, market, town
from .bot import Bot, Stopped

ROLE = "pharmacist"
PHARMACIST = ("Pharmacist", (52, 71))       # the same cell in every gang base (seen in two)
crafting.GANG_SPOTS.setdefault(ROLE, PHARMACIST)
MAKE = r"^\s*make medicine\s*$"
FUSE = r"^\s*fuse medicine\s*$"
START = r"^\s*start synthesis\s*$"
FUSE_MOST = 100                  # the fuse box takes at most this many at a time
FUSE_TAKES = 5                   # what one fused potion takes of the size below
PAGE_WAIT = 3.0                  # seconds a recipe's page is given to come up
SETTLE = 3.0                     # seconds the bag is given to show what was made
NO_MATERIALS = re.compile(r"not enough materials", re.I)
OVERWEIGHT = re.compile(r"\boverweight\b|bag is full|not enough space", re.I)
MOST_BATCHES = 400               # a safety net: 20,000 potions in one run
MOST_ROUNDS = 50                 # bagfuls in one run, at most (each round that counts changes the bag)
MARKET_PAGES = 60               # pages of materials read at most for one material
MATERIALS_CATEGORY = 100         # the market's "materials" (market.CATEGORIES)


@dataclass(frozen=True)
class Recipe:
    name: str                    # what it makes
    id: int                      # the game's item id (the menu's command carries it)
    makes: int                   # potions a batch
    needs: Mapping[str, int]     # material -> how many a batch


# From the recorded menu (the game's own words and ids); the live menu is read again at the
# Pharmacist and wins where it differs.
RECIPES: tuple[Recipe, ...] = (
    Recipe("Small Red Potion", 23385, 50, {"Lesser Blood": 2}),
    Recipe("Small Blue Potion", 23395, 50, {"Lesser Pelt": 2}),
    Recipe("Medium Red Potion", 23386, 50, {"Lesser Blood": 2, "medium Blood": 1}),
    Recipe("Medium Blue Potion", 23396, 50, {"Lesser Pelt": 2, "Medium Pelt": 1}),
    Recipe("Large Red Potion", 23387, 50, {"Lesser Blood": 2, "medium Blood": 1, "Higher blood": 1}),
    Recipe("Large Blue Potion", 23397, 50, {"Lesser Pelt": 2, "Medium Pelt": 1, "Higher Pelt": 1}),
    Recipe("Super Red Potion", 23388, 50, {"Lesser Blood": 4, "medium Blood": 2, "Excellent Blood": 1}),
    Recipe("Super Blue Potion", 23398, 50, {"Lesser Pelt": 4, "Medium Pelt": 2, "Excellent Pelt": 1}),
    Recipe("Plasma", 23390, 45, {"Lesser Blood": 1, "medium Blood": 1, "Higher blood": 2}),
    Recipe("Magic Potion", 23400, 45, {"Lesser Pelt": 1, "Medium Pelt": 1, "Higher Pelt": 2}),
    Recipe("Potent Plasma", 23391, 45, {"Lesser Blood": 2, "Higher blood": 1, "Excellent Blood": 2}),
    Recipe("Potent Magic Potion", 23401, 45, {"Lesser Pelt": 2, "Higher Pelt": 1, "Excellent Pelt": 2}),
    Recipe("Serum Protein", 23392, 45, {"Higher blood": 2, "Excellent Blood": 2, "Premium Blood": 1}),
    Recipe("Mist Potion", 23402, 45, {"Higher Pelt": 2, "Excellent Pelt": 2, "Premium Pelt": 1}),
    Recipe("Hemoglobin", 23393, 45, {"Excellent Blood": 2, "Premium Blood": 3}),
    Recipe("Mist Essence", 23403, 45, {"Excellent Pelt": 2, "Premium Pelt": 3}),
    Recipe("Pet Small Red Potion", 23435, 50, {"Lesser Blood": 4}),
    Recipe("Pet Small Blue Potion", 23445, 50, {"Lesser Pelt": 4}),
    Recipe("Pet Medium Red Potion", 23436, 50, {"Lesser Blood": 4, "medium Blood": 2}),
    Recipe("Pet Medium Blue Potion", 23446, 50, {"Lesser Pelt": 4, "Medium Pelt": 2}),
    Recipe("Pet Large Red Potion", 23437, 50, {"Lesser Blood": 1, "medium Blood": 1, "Higher blood": 3}),
    Recipe("Pet Large Blue Potion", 23447, 50, {"Lesser Pelt": 1, "Medium Pelt": 1, "Higher Pelt": 3}),
    Recipe("Pet Super Red Potion", 23438, 50,
           {"Lesser Blood": 1, "medium Blood": 1, "Higher blood": 2, "Excellent Blood": 2}),
    Recipe("Pet Super Blue Potion", 23448, 50,
           {"Lesser Pelt": 1, "Medium Pelt": 1, "Higher Pelt": 2, "Excellent Pelt": 2}),
    Recipe("Pet Plasma", 23440, 45, {"Lesser Blood": 2, "medium Blood": 1, "Higher blood": 4}),
    Recipe("Pet Potent Plasma", 23441, 45,
           {"Lesser Blood": 2, "medium Blood": 1, "Higher blood": 1, "Excellent Blood": 4}),
    Recipe("Pet Albumin", 23442, 45,
           {"medium Blood": 2, "Higher blood": 1, "Excellent Blood": 4, "Premium Blood": 2}),
    Recipe("Pet globulin", 23443, 45,
           {"Lesser Blood": 2, "medium Blood": 1, "Higher blood": 1, "Excellent Blood": 2, "Premium Blood": 6}),
)
BY_NAME: dict[str, Recipe] = {r.name.lower(): r for r in RECIPES}
# The fusion chains, smallest first: each size is FUSE_TAKES of the one before
CHAINS: tuple[tuple[str, str, str], ...] = (
    ("Small Red Potion", "Medium Red Potion", "Large Red Potion"),
    ("Small Blue Potion", "Medium Blue Potion", "Large Blue Potion"),
    ("Pet Small Red Potion", "Pet Medium Red Potion", "Pet Large Red Potion"),
    ("Pet Small Blue Potion", "Pet Medium Blue Potion", "Pet Large Blue Potion"),
)
FUSED_FROM: dict[str, str] = {c[i + 1].lower(): c[i] for c in CHAINS for i in range(len(c) - 1)}
MATERIALS: tuple[str, ...] = ("Lesser Blood", "medium Blood", "Higher blood", "Excellent Blood", "Premium Blood",
                              "Lesser Pelt", "Medium Pelt", "Higher Pelt", "Excellent Pelt", "Premium Pelt")
WAYS = ("fuse", "make")                       # a fusible size: Smalls fused up, or its own recipe
FUSE_UP = ("no", "medium", "large")           # afterwards: spare potions fused up to this size
SUPPLIES = ("bag", "storage", "buy", "both")  # where missing materials come from
SUPPLY_LABELS = (N_("Only what is in the bag"), N_("The town storehouse"), N_("The market"),
                 N_("The town storehouse, then the market"))
DEFAULTS: dict[str, Any] = {
    "where": "gang",             # "gang" (your own gang's Pharmacist, cheaper) or "other_gang"
    "gang_name": "",
    "targets": {},               # potion name -> how many to have in the bag
    "way": "fuse",
    "fuse_up": "no",
    "keep_small": 0,             # with fuse_up: of each Small potion this many stay unfused
    "keep_medium": 0,            # and of each Medium one
    "supply": "both",
    "prices": {},                # material -> the most gold coins a piece (0 or none: cheapest first, any price)
    "gold_cap": 0,               # the most gold coins spent on materials in one run (0: no limit)
    "store": False,              # put the potions made into the town storehouse, and go on a bagful at a time
}
_NEEDS = re.compile(r"(\d+)\s*(" + "|".join(re.escape(m) for m in MATERIALS) + r")", re.I)
_ROW = re.compile(r"^\s*synthesize\s+(\d+)\s+(.+?)\s*$", re.I)
_PAGE = re.compile(r"synthesize\s+(\d+)\s+(.+?)\s*;\s*cost\s+(\d+)\s+arnebia", re.I)


def config(p: Mapping[str, Any]) -> dict[str, Any]:
    return {**copy.deepcopy(DEFAULTS), **(p.get("potion_maker") or {})}


def material_name(name: str) -> str:
    """A material as the game writes it ("medium blood" -> "medium Blood")."""
    low = name.strip().lower()
    return next((m for m in MATERIALS if m.lower() == low), name.strip())


def parse_needs(text: str) -> dict[str, int]:
    """'needs 2 Lesser Blood1 medium Blood' -> {'Lesser Blood': 2, 'medium Blood': 1}."""
    out: dict[str, int] = {}
    for n, mat in _NEEDS.findall(text or ""):
        key = material_name(mat)
        out[key] = out.get(key, 0) + int(n)
    return out


def size_of(name: str) -> int:
    """0 Small, 1 Medium, 2 Large in its chain; -1 for a potion that is not fused."""
    low = name.lower()
    for chain in CHAINS:
        for i, n in enumerate(chain):
            if n.lower() == low:
                return i
    return -1


# ---------------------------------------------------------------------------------------- the plan (pure)
@dataclass
class Plan:
    batches: dict[str, int] = field(default_factory=lambda: {})     # recipe -> batches to make
    fuses: list[tuple[str, int]] = field(default_factory=lambda: [])  # (what is made, how many), in order
    materials: dict[str, int] = field(default_factory=lambda: {})   # material -> how many all batches need
    missing: dict[str, int] = field(default_factory=lambda: {})     # of those, not in the bag
    after: dict[str, int] = field(default_factory=lambda: {})       # the potions in the bag afterwards

    def empty(self) -> bool:
        return not self.batches and not any(n for _p, n in self.fuses)


def _wants(cfg: Mapping[str, Any]) -> dict[str, int]:
    raw: Mapping[str, Any] = cfg.get("targets") or {}
    out: dict[str, int] = {}
    for name, n in raw.items():
        r = BY_NAME.get(str(name).lower())
        try:
            k = int(n or 0)
        except (TypeError, ValueError):
            k = 0
        if r is not None and k > 0:
            out[r.name] = k
    return out


def _demand(bag: Mapping[str, int], wants: Mapping[str, int], fuse: bool) -> tuple[dict[str, int], dict[str, int]]:
    """({potion (lower): how many to have once the batches are made}, {potion (lower): how many to
    fuse}). Biggest size first: a Large that is fused asks the Medium below for five each, and what
    is in the bag now is kept."""
    demand: dict[str, int] = {n.lower(): k for n, k in wants.items()}
    fuses: dict[str, int] = {}
    if not fuse:
        return demand, fuses
    for chain in CHAINS:
        for i in range(len(chain) - 1, 0, -1):
            name = chain[i].lower()
            short = max(0, demand.get(name, 0) - bag.get(name, 0))
            if short:
                fuses[name] = short
                src = chain[i - 1].lower()
                demand[src] = demand.get(src, 0) + short * FUSE_TAKES
                demand[name] = bag.get(name, 0)
    return demand, fuses


def plan(have: Mapping[str, int], cfg: Mapping[str, Any], recipes: Mapping[str, Recipe] | None = None,
         most_batches: Mapping[str, int] | None = None, make: bool = True) -> Plan:
    """What to make and fuse so the bag holds what cfg's targets ask for. have: {name (lower): count}.
    recipes: the live menu's (by lower name), else RECIPES. most_batches: a cap per recipe (the
    arnebia it pays for). make False: only the fusions what is in the bag allows."""
    book = recipes or BY_NAME
    bag = {k.lower(): int(v) for k, v in have.items()}
    demand, fuses = _demand(bag, _wants(cfg), cfg.get("way", "fuse") == "fuse")
    p = Plan()
    for name, want in demand.items():
        r = book.get(name)
        short = max(0, want - bag.get(name, 0))
        if r is None or not short or not make:
            continue
        n = math.ceil(short / r.makes)
        if most_batches is not None:
            n = min(n, max(0, int(most_batches.get(r.name, n))))
        if n:
            p.batches[r.name] = n
    for rname, n in p.batches.items():
        r = book[rname.lower()]
        for mat, k in r.needs.items():
            p.materials[mat] = p.materials.get(mat, 0) + k * n
    p.missing = {m: k - bag.get(m.lower(), 0) for m, k in p.materials.items() if k > bag.get(m.lower(), 0)}
    after = dict(bag)
    for rname, n in p.batches.items():
        key = rname.lower()
        after[key] = after.get(key, 0) + n * book[key].makes
    # fuses in chain order, smallest first, each only as far as the size below now allows
    for chain in CHAINS:
        for i in range(1, len(chain)):
            name, src = chain[i].lower(), chain[i - 1].lower()
            want = fuses.get(name, 0)
            n = min(want, after.get(src, 0) // FUSE_TAKES)
            if n:
                after[src] -= n * FUSE_TAKES
                after[name] = after.get(name, 0) + n
                p.fuses.append((chain[i], n))
    p.fuses += fuse_up_steps(after, cfg)
    names = {r.name.lower(): r.name for r in RECIPES}
    p.after = {names[k]: v for k, v in after.items() if k in names and v}
    return p


def fuse_up_steps(after: dict[str, int], cfg: Mapping[str, Any]) -> list[tuple[str, int]]:
    """The spare potions fused up to cfg's fuse_up size, smallest first; after is changed to match.
    A size's target and its keep stay unfused."""
    top = FUSE_UP.index(cfg.get("fuse_up", "no")) if cfg.get("fuse_up", "no") in FUSE_UP else 0
    if not top:
        return []
    wants = {n.lower(): k for n, k in _wants(cfg).items()}
    keep = (int(cfg.get("keep_small") or 0), int(cfg.get("keep_medium") or 0))
    steps: list[tuple[str, int]] = []
    for chain in CHAINS:
        for i in range(1, top + 1):
            name, src = chain[i].lower(), chain[i - 1].lower()
            stay = max(wants.get(src, 0), keep[i - 1])
            n = max(0, after.get(src, 0) - stay) // FUSE_TAKES
            if n:
                after[src] -= n * FUSE_TAKES
                after[name] = after.get(name, 0) + n
                steps.append((chain[i], n))
    return steps


def affordable(batches: Mapping[str, int], fees: Mapping[str, int], spare: int) -> dict[str, int]:
    """The most batches of each recipe the spare arnebia pays for, Smalls first (fused sizes need
    them), then in the plan's order. A recipe whose fee isn't known is left as it is."""
    order = sorted(batches, key=lambda n: (size_of(n) if size_of(n) >= 0 else 9, n))
    out: dict[str, int] = {}
    left = spare
    for name in order:
        fee = fees.get(name)
        n = batches[name]
        if fee:
            n = min(n, max(0, left) // fee)
            left -= n * fee
        out[name] = n
    return out


def lots_to_buy(lots: Iterable[market.Lot], name: str, n: int, most: int) -> list[market.Order]:
    """Orders for n of a material from the cheapest lots of exactly that name, none above most a
    piece (0: any price)."""
    exact = [x for x in lots if not x.equip and " ".join(x.name.split()).lower() == name.lower()]
    return market.cheapest_orders(exact, n, most)


# ---------------------------------------------------------------------------------------- at the Pharmacist
def _where(cfg: Mapping[str, Any]) -> str:
    where = crafting.place(dict(cfg))
    return where if where.startswith("gang") else "gang"     # only gang bases have a Pharmacist


def open_pharmacist(b: Bot, where: str) -> None:
    """Talk to the Pharmacist of the gang base `where` names (crafting.place's "gang" or
    "gang:<name>"). Stopped, said, when it can't be reached: no city has one."""
    gang = where[5:] or None
    if not crafting.enter_gang(b, gang, ROLE):
        raise Stopped(_("couldn't get into the gang's outer hall to reach the Pharmacist"))
    name, cell = PHARMACIST
    if not b.talk(name, cell):
        raise Stopped(_("couldn't talk to the Pharmacist"))


def _open(b: Bot, where: str, section: str) -> bool:
    open_pharmacist(b, where)
    if town.open_section(b, section):
        return True
    b.log(_("the Pharmacist's menu has no \"{entry}\"", entry="Make Medicine" if section == MAKE else "Fuse Medicine"),
          speak=True)
    b.close_dialogs()
    return False


def live_recipes(b: Bot) -> dict[str, Recipe]:
    """The open "Make Medicine" list as recipes (by lower name); the recorded ones fill any gap."""
    out = dict(BY_NAME)
    for _r, _c, e in crafting.entries(b):
        texts = b.entry_texts(e)
        m = next((m for t in texts if (m := _ROW.match(t))), None)
        if m is None:
            continue
        none: dict[str, int] = {}
        needs = next((parse_needs(t) for t in texts if t.lower().lstrip().startswith("needs")), none)
        known = BY_NAME.get(m.group(2).lower())
        cmd: list[Any] = e.get("cmd") or []
        rid = int(cmd[-1]) if cmd and isinstance(cmd[-1], int) else (known.id if known else 0)
        name = known.name if known else m.group(2).strip()
        out[name.lower()] = Recipe(name, rid, int(m.group(1)), needs or (dict(known.needs) if known else {}))
    return out


def _recipe_row(name: str) -> str:
    return r"^\s*synthesize\s+\d+\s+" + re.escape(name) + r"\s*$"


def read_page(b: Bot, where: str, name: str) -> tuple[int, int] | None:
    """Open a recipe's page: (how many a batch makes, its fee). None, said, when it doesn't come up.
    The page stays open (start synthesis is on it)."""
    if not _open(b, where, MAKE):
        return None
    if b.choose(_recipe_row(name)) is None:
        b.log(_("the Pharmacist doesn't list {potion}", potion=name), speak=True)
        b.close_dialogs()
        return None
    m, _text = crafting.wait_text(b, r"synthesize\s+(\d+)\s+" + re.escape(name) + r"\s*;\s*cost\s+(\d+)\s+arnebia",
                                  PAGE_WAIT)
    if m is None:
        b.log(_("the Pharmacist's page for {potion} didn't come up - nothing was spent", potion=name), speak=True)
        b.close_dialogs()
        return None
    return int(m.group(1)), int(m.group(2))


def note_fee(b: Bot, where: str, name: str, fee: int) -> None:
    fees: dict[str, dict[str, int]] = b.p.setdefault("potion_fees", {})
    fees.setdefault(where, {})[name] = fee


def known_fees(b: Bot, where: str) -> dict[str, int]:
    fees: Mapping[str, Mapping[str, int]] = b.p.get("potion_fees") or {}
    return dict(fees.get(where) or {})


def read_fees(b: Bot, where: str, names: Iterable[str]) -> dict[str, int]:
    """Each recipe's fee, read off its page (nothing is spent)."""
    out: dict[str, int] = {}
    for name in names:
        page = read_page(b, where, name)
        b.close_dialogs()
        if page is not None:
            out[name] = page[1]
            note_fee(b, where, name, page[1])
    return out


def _came(b: Bot, name: str, had: int, want: int) -> int:
    """How many of name came into the bag since it held `had`, given SETTLE to show all `want`."""
    clock = clock_for(b)
    end = clock.time() + SETTLE
    while True:
        came = town.counts(b).get(name.lower(), 0) - had
        if came >= want or clock.time() >= end:
            return max(0, came)
        b.sleep(0.25)


def make_batch(b: Bot, where: str, r: Recipe) -> str:
    """One batch of a recipe: "made", "materials" (the game said not enough), "arnebia", "heavy" or
    "failed" (said)."""
    page = read_page(b, where, r.name)
    if page is None:
        return "failed"
    makes, fee = page
    note_fee(b, where, r.name, fee)
    if crafting.spare_arnebia(b) < fee:
        b.close_dialogs()
        b.log(_("not enough spare arnebia to make {potion} (fee {fee})", potion=r.name, fee=fee), speak=True)
        return "arnebia"
    had = town.counts(b).get(r.name.lower(), 0)
    since = chores.said_mark(b)
    if b.choose(START) is None:
        b.close_dialogs()
        b.log(_("the Pharmacist's page for {potion} has no \"start synthesis\"", potion=r.name), speak=True)
        return "failed"
    came = _came(b, r.name, had, makes)
    b.close_dialogs()
    if came:
        b.log(_("made {n} {potion} for {fee} arnebia", n=came, potion=r.name, fee=fee))
        return "made"
    said = chores.said_since(b, since)
    if any(NO_MATERIALS.search(t) for t in said):
        b.log(_("the Pharmacist says there aren't enough materials for {potion}", potion=r.name), speak=True)
        return "materials"
    if any(OVERWEIGHT.search(t) for t in said):
        b.log(_("the bag is too full for more {potion}", potion=r.name), speak=True)
        return "heavy"
    b.log(_("the bag showed no new {potion} after making it", potion=r.name), speak=True)
    return "failed"


def _fuse_row(name: str) -> str:
    return r"^\s*merge of 1\s+" + re.escape(name) + r"\s+requires\b"


def fuse(b: Bot, where: str, name: str, n: int) -> int:
    """Fuse up to n of a potion from the size below, FUSE_MOST at a time. How many were made."""
    src = FUSED_FROM.get(name.lower())
    if src is None:
        return 0
    made = 0
    while made < n:
        have = town.counts(b).get(src.lower(), 0) // FUSE_TAKES
        step = min(n - made, FUSE_MOST, have)
        if step <= 0:
            break
        if not _open(b, where, FUSE):
            break
        had = town.counts(b).get(name.lower(), 0)
        if not town.pick(b, _fuse_row(name), step):
            b.close_dialogs()
            b.log(_("the Pharmacist didn't ask how many {potion} to fuse", potion=name), speak=True)
            break
        came = _came(b, name, had, step)
        b.close_dialogs()
        if not came:
            b.log(_("the bag showed no new {potion} after fusing", potion=name), speak=True)
            break
        made += came
        b.log(_("fused {k} {src} into {n} {potion}", k=came * FUSE_TAKES, src=src, n=came, potion=name))
    return made


# ---------------------------------------------------------------------------------------- materials
def buy_material(b: Bot, name: str, n: int, most: int, cap: int) -> tuple[int, int, bool]:
    """Buy n of a material at the player market, the cheapest lots first, none above most gold coins a
    piece (0: any price), spending at most cap (0: no limit). (how many came, gold coins spent, and
    whether a purchase's outcome is unknown: then nothing more may be bought blind)."""
    if n <= 0:
        return 0, 0, False
    found: list[market.Lot] = []

    def on_page(pg: market.Page) -> bool:
        found.extend(x for x in pg.lots if not x.opens_in)
        mine = [x for x in found if " ".join(x.name.split()).lower() == name.lower()]
        if sum(x.qty for x in mine) >= n:
            return False                    # cheapest first: what is found is the cheapest there is
        return not (most and pg.lots and min(x.price for x in pg.lots) > most)

    market.read_pages(b, MATERIALS_CATEGORY, market.PRICE, on_page, most=MARKET_PAGES)
    orders = lots_to_buy(found, name, n, most)
    if not orders:
        b.log(_("the market has no {item} at {most} gold coins or less", item=name, most=most) if most else
              _("the market has no {item} for sale", item=name), speak=True)
        return 0, 0, False
    had = town.counts(b).get(name.lower(), 0)
    done = market.buy_batch(b, orders, cap)
    b.close_dialogs()
    return max(0, town.counts(b).get(name.lower(), 0) - had), done.spent, done.unsure


def _bring(b: Bot, mat: str, fetch: Callable[[], int]) -> int:
    """fetch() brings some of a material; the first time, the load before and after teaches the bag
    manager its weight (room_batches needs it)."""
    if bagrules.weights().get(mat.lower()):
        return fetch()
    before = town.load_now(b)
    got = fetch()
    if got:
        bagrules.learn_weight(mat, town.load_now(b), before, got)    # the load went up by got of it
    return got


def fetch_materials(b: Bot, missing: Mapping[str, int], cfg: Mapping[str, Any], spent: int = 0,
                    st: _Run | None = None) -> tuple[dict[str, int], int]:
    """Bring the missing materials into the bag, as cfg's supply says. (what is still missing, the
    gold coins spent on materials this run: `spent` before this call, and this call's). st: the
    run, whose market_unsure stops any more buying once a purchase's outcome was unknown."""
    supply = cfg.get("supply", "both")
    if st is not None and st.market_unsure:
        supply = "storage" if supply == "both" else ("none" if supply == "buy" else supply)
    left = {m: k for m, k in missing.items() if k > 0}
    if supply in ("storage", "both"):
        for mat in list(left):
            want = left[mat]
            left[mat] -= _bring(b, mat, lambda m=mat, n=want: crafting.take_from_storage(b, m, n))
        left = {m: k for m, k in left.items() if k > 0}
    if supply in ("buy", "both") and left:
        prices: Mapping[str, Any] = cfg.get("prices") or {}
        cap = int(cfg.get("gold_cap") or 0)
        for mat in list(left):
            if cap and spent >= cap:
                b.log(_("the gold limit for materials ({cap}) is spent", cap=cap), speak=True)
                break
            most = int(prices.get(mat) or prices.get(mat.lower()) or 0)
            costs: list[int] = []

            unsure: list[bool] = []

            def buy(m: str = mat, n: int = left[mat], top: int = most, budget: int = cap - spent if cap else 0) -> int:
                got, cost, lost = buy_material(b, m, n, top, budget)
                costs.append(cost)
                unsure.append(lost)
                return got
            left[mat] -= _bring(b, mat, buy)
            spent += sum(costs)
            if any(unsure):
                if st is not None:
                    st.market_unsure = True
                b.log(_("the market didn't say whether {item} was bought: nothing more is bought this run - "
                        "look in the bag", item=mat), speak=True)
                break
        left = {m: k for m, k in left.items() if k > 0}
    return left, spent


def room_batches(b: Bot, batches: Mapping[str, int], book: Mapping[str, Recipe]) -> dict[str, int]:
    """The batches whose missing materials fit into the bag up to its full mark (the profile's
    bag_full_at), Smalls first. Only the materials brought in are held to it: making and fusing
    still work in a bag over its full mark, and the potions may take the load past it. A material
    whose weight isn't known yet counts as nothing (it is weighed when it comes: _bring); with no
    load on the sheet every batch is kept."""
    sheet: dict[str, Any] = b.refresh_sheet(force=True) or {}
    if not sheet.get("maxload"):
        return dict(batches)
    free = sheet["maxload"] * float(b.p.get("bag_full_at", 100)) / 100.0 - sheet.get("load", 0)
    weight = bagrules.weights()
    bag = {k.lower(): v for k, v in town.counts(b).items()}
    out: dict[str, int] = {}
    for name in sorted(batches, key=lambda n: (size_of(n) if size_of(n) >= 0 else 9, n)):
        r = book[name.lower()]
        n = 0
        while n < batches[name]:
            need = sum(max(0, k - max(0, bag.get(m.lower(), 0))) * weight.get(m.lower(), 0.0)
                       for m, k in r.needs.items())
            if need and need > free:
                break                       # (materials already in the bag need no room)
            free -= need
            for m, k in r.needs.items():
                bag[m.lower()] = bag.get(m.lower(), 0) - k
            n += 1
        if n:
            out[name] = n
    return out


def stored_counts(b: Bot, names: Iterable[str]) -> dict[str, int]:
    """How many of each potion the town storehouse holds (its item storage list)."""
    names = list(names)
    if not names:
        return {}
    town.visit(b, "storehouse")
    try:
        if not town.open_section(b, r"^\s*item storage"):
            b.log(_("couldn't read the storehouse's list: only the bag counts"), speak=True)
            return {}
        return {n: crafting.storage_count(b, n) for n in names}
    finally:
        b.close_dialogs()


def round_cfg(cfg: Mapping[str, Any], stored: Mapping[str, int]) -> dict[str, Any]:
    """cfg with each target less what the storehouse holds of it: the plan works on the bag alone,
    so a potion in the storehouse is never counted on for fusing."""
    out = dict(cfg)
    out["targets"] = {n: max(0, k - stored.get(n, 0)) for n, k in _wants(cfg).items()}
    return out


def to_store(cfg: Mapping[str, Any]) -> list[str]:
    """The potions put into the storehouse after a round: the targets, and the size spare potions
    are fused up to. The sizes fused from stay in the bag for the next round's fusing."""
    out = list(_wants(cfg))
    top = FUSE_UP.index(cfg.get("fuse_up", "no")) if cfg.get("fuse_up", "no") in FUSE_UP else 0
    if top:
        out += [c[top] for c in CHAINS if c[top] not in out]
    return out


def store_made(b: Bot, cfg: Mapping[str, Any], kept: Mapping[str, int], stored: dict[str, int]) -> int:
    """Put the potions this run made (what the bag has over `kept`, its count at the start) into the
    town storehouse; stored is counted up. How many went in."""
    put = 0
    have = town.counts(b)
    for name in to_store(cfg):
        n = have.get(name.lower(), 0) - kept.get(name.lower(), 0)
        if n > 0:
            k = int(town.deposit(b, name, n) or 0)
            stored[name] = stored.get(name, 0) + k
            put += k
    return put


def _shorten(batches: Mapping[str, int], book: Mapping[str, Recipe], have: Mapping[str, int]) -> dict[str, int]:
    """The batches the materials in the bag are enough for, Smalls first."""
    bag = {k.lower(): v for k, v in have.items()}
    out: dict[str, int] = {}
    for name in sorted(batches, key=lambda n: (size_of(n) if size_of(n) >= 0 else 9, n)):
        r = book[name.lower()]
        n = batches[name]
        for mat, k in r.needs.items():
            n = min(n, bag.get(mat.lower(), 0) // k) if k else n
        for mat, k in r.needs.items():
            bag[mat.lower()] = bag.get(mat.lower(), 0) - n * k
        if n:
            out[name] = n
    return out


# ---------------------------------------------------------------------------------------- the run
def describe(p: Plan, fees: Mapping[str, int]) -> list[str]:
    lines: list[str] = []
    for name, n in p.batches.items():
        fee = fees.get(name)
        lines.append(_("make {n} batches of {potion} ({fee} arnebia each)", n=n, potion=name, fee=fee) if fee else
                     _("make {n} batches of {potion}", n=n, potion=name))
    for name, n in p.fuses:
        lines.append(_("fuse {n} {potion}", n=n, potion=name))
    for mat, k in p.materials.items():
        lines.append(_("needs {n} {item} ({k} missing)", n=k, item=mat, k=p.missing.get(mat, 0)))
    return lines


def _check(b: Bot, cfg: Mapping[str, Any], where: str) -> None:
    """Check only: the plan, with each recipe's fee read off its page; nothing is made or bought."""
    stored = stored_counts(b, _wants(cfg)) if cfg.get("store") else {}
    for name, n in stored.items():
        if n:
            b.log(_("the storehouse has {n} {item}", n=n, item=name))
    p = plan(town.counts(b), round_cfg(cfg, stored))
    fees = read_fees(b, where, p.batches) if p.batches else {}
    if not p.batches:
        open_pharmacist(b, where)
    for line in describe(p, fees):
        b.log(line)
    total = sum(n * fees.get(name, 0) for name, n in p.batches.items())
    if total:
        b.log(_("the fees come to {total} arnebia; {spare} is spare", total=total, spare=crafting.spare_arnebia(b)))
    b.log(_("the Pharmacist is open; nothing was made (check only)"), speak=True)
    b.close_dialogs()


def _make_all(b: Bot, where: str, batches: Mapping[str, int], book: Mapping[str, Recipe]) -> int:
    """Make the batches, Smalls first. How many batches were made."""
    made = 0
    for name in sorted(batches, key=lambda n: (size_of(n) if size_of(n) >= 0 else 9, n)):
        r = book[name.lower()]
        for _i in range(batches[name]):
            if b.stop_event.is_set():
                raise Stopped(_("stopped"))
            if made >= MOST_BATCHES:
                return made
            outcome = make_batch(b, where, r)
            if outcome == "made":
                made += 1
                continue
            if outcome in ("arnebia", "heavy"):
                return made
            break                           # materials or a page that didn't come: the next recipe
    return made


@dataclass
class _Run:
    where: str
    kept: dict[str, int]                  # the bag's potions at the start: they stay in the bag
    stored: dict[str, int]                # the storehouse's, counted up as potions go in
    book: dict[str, Recipe]
    fees: dict[str, int]
    spent: int = 0                        # gold coins on materials, all rounds
    made: int = 0
    fused: int = 0
    put: int = 0
    told_cap: bool = False
    market_unsure: bool = False           # a purchase's outcome was unknown: no more buying this run


def _make_round(b: Bot, cfg: Mapping[str, Any], st: _Run, p: Plan) -> int:
    """Bring what a bagful of batches needs and make them. How many batches were made."""
    unknown = [n for n in p.batches if n not in st.fees]
    if unknown:
        st.fees.update(read_fees(b, st.where, unknown))
        if _open(b, st.where, MAKE):
            st.book = live_recipes(b)
        b.close_dialogs()
    cap = affordable(p.batches, st.fees, crafting.spare_arnebia(b))
    if sum(cap.values()) < sum(p.batches.values()) and not st.told_cap:
        st.told_cap = True
        b.log(_("the spare arnebia pays for {n} of the {all} batches", n=sum(cap.values()),
                all=sum(p.batches.values())), speak=True)
    fit = room_batches(b, cap, st.book)
    if sum(cap.values()) and not fit:
        b.log(_("the bag has no room for more Bloods and Pelts"), speak=True)
        return 0
    rp = plan(town.counts(b), round_cfg(cfg, st.stored), st.book, fit)
    if rp.missing:
        still, st.spent = fetch_materials(b, rp.missing, cfg, st.spent, st)
        for mat, k in still.items():
            b.log(_("{n} {item} still missing", n=k, item=mat), speak=True)
    return _make_all(b, st.where, _shorten(rp.batches, st.book, town.counts(b)), st.book)


def run(b: Bot, cfg: Mapping[str, Any], check_only: bool = False) -> None:
    """The Potion Maker, a bagful at a time: bring materials up to the bag's full mark, make, fuse,
    and with "store" on put what was made into the storehouse and go again until the targets (bag
    and storehouse together) are met. cfg: config()."""
    if not _wants(cfg) and cfg.get("fuse_up", "no") == "no":
        raise Stopped(_("set it up first: Tools, {tool}", tool=_("Potion Maker")))
    where = _where(cfg)
    if check_only:
        _check(b, cfg, where)
        return
    store = bool(cfg.get("store"))
    st = _Run(where, {k.lower(): v for k, v in town.counts(b).items()},
              stored_counts(b, _wants(cfg)) if store else {}, dict(BY_NAME), known_fees(b, where))
    for rnd in range(MOST_ROUNDS):
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        p = plan(town.counts(b), round_cfg(cfg, st.stored), st.book)
        if p.empty():
            if rnd == 0:
                b.log(_("the potions asked for are already there"), speak=True)
            break
        made = _make_round(b, cfg, st, p) if p.batches else 0
        fused = sum(fuse(b, where, name, n)
                    for name, n in plan(town.counts(b), round_cfg(cfg, st.stored), st.book, make=False).fuses)
        b.close_dialogs()
        put = store_made(b, cfg, st.kept, st.stored) if store else 0
        st.made, st.fused, st.put = st.made + made, st.fused + fused, st.put + put
        if not (made or fused or put):
            break                          # nothing changed: another round would do the same
        b.log(_("round {n}: {made} batches made, {fused} potions fused, {put} into the storehouse",
                n=rnd + 1, made=made, fused=fused, put=put))
    _finish(b, cfg, st)


def _finish(b: Bot, cfg: Mapping[str, Any], st: _Run) -> None:
    have = town.counts(b)
    wants = _wants(cfg)
    total = {n: have.get(n.lower(), 0) + st.stored.get(n, 0) for n in wants}
    short = {n: k - total[n] for n, k in wants.items() if total[n] < k}
    if short:
        b.log(_("still short of: {what}", what=", ".join("%d %s" % (k, n) for n, k in short.items())), speak=True)
    else:
        b.log(_("the Potion Maker is done: {made} batches made, {fused} potions fused, {put} into the storehouse",
                made=st.made, fused=st.fused, put=st.put), speak=True)


def names_for(kind: str) -> Sequence[str]:
    """The potions of one kind for the window's lists: "player" or "pet"."""
    return [r.name for r in RECIPES if r.name.startswith("Pet ") == (kind == "pet")]
