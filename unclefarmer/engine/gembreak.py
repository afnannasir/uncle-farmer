"""Tools, Gem Breaker: the Gem Craftsman's "break into shards", for the gems and Enhance Crystals the
player chooses. It can't be undone, so nothing is broken that wasn't asked for by name and level.

What the game does (its menus and the bag, seen live 2026-11-18):
  - the list holds gems and crystals together, in no order: "Level 6 Turquoise 1", "Level 4 Equipment
    Enhance Crystal 4";
  - a page says what one gives and costs: "1 Level 3 Spinel can break into 3 Level 2 Spinel; fee 0
    arnebia", "1 Level 1 Spinel can break into 3 Level 1 Spinel Shard; fee 0 arnebia", "1 Level 4
    Equipment Enhance Crystal can break into 20 Equipment Enhance Crystal Shard; fee 357 arnebia",
    then "Press here to enter the number of Level 3 Spinel to break";
  - gems are free, and go down one level at a time: only a Level 1 gem gives shards, so a gem of
    level N is 3^N Level 1 shards. Crystals cost arnebia and turn straight into shards;
  - the game says nothing afterwards: the bag is what tells.

The numbers are read from each page. It works from the highest level down, one kind at a time, the
gems a break leaves being broken in their turn while their level is allowed.
"""

from __future__ import annotations

import copy
import re
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from ..clock import clock_for
from ..i18n import _
from . import chores, crafting, town
from .bot import Bot, Stopped

STOPS = ("shards", "level")       # how far down: to the shards, or to gems of `stop_level`
DEFAULTS: dict[str, Any] = {
    "colours": [],                # the gems that may be broken, by colour
    "top": 2,                     # the highest gem level that may be broken
    "stop": "shards",
    "stop_level": 1,
    "keep": 0,                    # of each gem colour and level, this many stay
    "weapon": False,              # Weapon Enhance Crystals may be broken
    "equipment": False,           # Equipment Enhance Crystals may be broken
    "crystal_top": 2,             # the highest crystal level that may be broken
    "crystal_keep": 0,            # of each crystal and level, this many stay
    "sell": False,                # sell by the bag manager's rules: when done, and when too heavy
}
CRYSTALS = {"weapon enhance crystal": "weapon", "equipment enhance crystal": "equipment"}
PAGE_WAIT = 3.0                   # seconds an item's page is given to come up
SIZES = 3                         # amounts tried (each half the last) when the bag doesn't change
FAILS = 3                         # kinds in a row that couldn't be broken: something is wrong here
MOST_STEPS = 500                  # a safety net: every step that counts empties a kind
GIVES = 3                         # what one gem gives of the level below
OVERWEIGHT = re.compile(r"\boverweight\b", re.I)   # the game's answer when what comes out won't fit


class TooHeavy(Exception):
    """Not even one more fits: what a break leaves weighs more than the bag can take."""


@dataclass(frozen=True)
class Item:
    name: str                     # lower case, as town.counts has it
    level: int
    kind: str                     # the colour (lower case), or the crystal's name
    crystal: str | None           # "weapon" / "equipment"; None for a gem


def config(p: Mapping[str, Any]) -> dict[str, Any]:
    return {**copy.deepcopy(DEFAULTS), **(p.get("gem_breaker") or {})}


def pretty(name: str) -> str:
    return name.title().replace("'S", "'s")


def parse(name: str) -> Item | None:
    """A gem or Enhance Crystal by its bag name; None for anything else (shards included)."""
    low = name.strip().lower()
    m = crafting.GEM.match(low)
    if m is None or low.endswith(" shard"):
        return None
    kind = m.group(2).strip()
    if "crystal" in kind and kind not in CRYSTALS:
        return None
    return Item(low, int(m.group(1)), kind, CRYSTALS.get(kind))


def allowed(item: Item, cfg: Mapping[str, Any]) -> bool:
    if item.crystal:
        return bool(cfg.get(item.crystal)) and item.level <= int(cfg.get("crystal_top", 0))
    if item.kind not in {str(c).lower() for c in cfg.get("colours", [])} or item.level > int(cfg.get("top", 0)):
        return False
    return cfg.get("stop") != "level" or item.level > int(cfg.get("stop_level", 1))


def todo(have: Mapping[str, int], cfg: Mapping[str, Any]) -> list[tuple[Item, int]]:
    """What may be broken now and how many of each, the highest level first."""
    out: list[tuple[Item, int]] = []
    for name, n in have.items():
        item = parse(name)
        if item is None:
            continue
        keep = max(0, int(cfg.get("crystal_keep" if item.crystal else "keep", 0)))
        if n > keep and allowed(item, cfg):
            out.append((item, n - keep))
    return sorted(out, key=lambda row: (-row[0].level, row[0].name))


def below(item: Item) -> str:
    """What a gem breaks into."""
    if item.level > 1:
        return "level %d %s" % (item.level - 1, item.kind)
    return "level 1 %s shard" % item.kind


def outcome(have: Mapping[str, int], cfg: Mapping[str, Any]) -> tuple[dict[str, int], dict[str, int]]:
    """What the job would do to this bag: ({what goes: how many}, {what the gems leave: how many}).
    Crystals are in the first only: what they give is on their page."""
    bag = {k.lower(): int(v) for k, v in have.items()}
    gone: dict[str, int] = {}
    made: dict[str, int] = {}
    for _step in range(MOST_STEPS):
        rows = todo(bag, cfg)
        if not rows:
            break
        item, n = rows[0]
        bag[item.name] -= n
        gone[item.name] = gone.get(item.name, 0) + n
        if item.name in made:
            made[item.name] = max(0, made[item.name] - n)
        if item.crystal is None:
            lower = below(item)
            bag[lower] = bag.get(lower, 0) + n * GIVES
            made[lower] = made.get(lower, 0) + n * GIVES
    return gone, {k: v for k, v in made.items() if v > 0}


@dataclass
class Page:
    gives: int
    product: str
    fee: int


def _names(b: Bot) -> dict[str, str]:
    return {str(r.get("name") or "").strip().lower(): str(r.get("name") or "").strip() for r in b.bag_rows()}


def _open_page(b: Bot, where: str, name: str) -> Page | None:
    """The craftsman's page for one item, read; None (said) when it doesn't come up."""
    crafting.open_craftsman(b, "gem_craftsman", where)
    if not town.open_section(b, r"^\s*break into shards"):
        b.log(_("the Gem Craftsman's menu has no way to break gems"), speak=True)
        return None
    if b.choose(r"^\s*" + re.escape(name) + r"\s*\d+\s*$") is None or not b.wait_dialog(3.0):
        b.log(_("the Gem Craftsman didn't list {item}", item=name))
        return None
    rx = r"\b1\s+" + re.escape(name) + r"\s+can break into\s+(\d+)\s+(.+?);\s*fee\s+(\d+)\s+arnebia"
    m, _text = crafting.wait_text(b, rx, PAGE_WAIT)
    if m is None:
        b.log(_("the Gem Craftsman's page for {gem} didn't come up - nothing was spent", gem=name), speak=True)
        return None
    return Page(int(m.group(1)), m.group(2).strip(), int(m.group(3)))


def _left(b: Bot, name: str, was: int) -> int:
    """How many of name the bag holds, given its update a moment to show fewer than `was`."""
    clock = clock_for(b)
    end = clock.time() + crafting.SETTLE
    while True:
        now = town.counts(b).get(name.lower(), 0)
        if now < was or clock.time() >= end:
            return now
        b.sleep(0.25)


def _landed(b: Bot, product: str, want: int) -> None:
    """Give the bag its moment to show what a break left: the item goes first and the product comes
    a little later (seen live: the next step counted 2 gems where the craftsman listed 8, and the
    results missed the last shards)."""
    clock = clock_for(b)
    end = clock.time() + crafting.SETTLE
    while town.counts(b).get(product, 0) < want and clock.time() < end:
        b.sleep(0.25)


def _break(b: Bot, where: str, item: Item, n: int) -> int:
    """Break up to n of one item, asking for half when the bag shows nothing came of it. How many
    went. TooHeavy: the game said "overweight" even for one."""
    name = _names(b).get(item.name) or pretty(item.name)
    sizes = 0
    while True:
        page = _open_page(b, where, name)
        if page is None:
            return 0
        if page.fee:
            n = min(n, max(0, crafting.spare_arnebia(b)) // page.fee)
            if n <= 0:
                b.log(_("not enough spare arnebia to break {item} (fee {fee} each)", item=name, fee=page.fee),
                      speak=True)
                return 0
        have = town.counts(b)
        was, product = have.get(item.name, 0), page.product.lower()
        had = have.get(product, 0)
        since = chores.said_mark(b)
        if not town.pick(b, r"enter the number of " + re.escape(name) + r" to break", n):
            b.log(_("the Gem Craftsman didn't ask how many {item} to break", item=name))
            return 0
        gone = was - _left(b, item.name, was)
        b.close_dialogs()
        if gone > 0:
            _landed(b, product, had + gone * page.gives)
            b.log(_("broke {n} {item} into {k} {product}", n=gone, item=name, k=gone * page.gives,
                    product=page.product))
            return gone
        heavy = any(OVERWEIGHT.search(line) for line in chores.said_since(b, since))
        n //= 2
        sizes += 1
        if n < 1 and heavy:
            raise TooHeavy
        if n < 1 or (sizes >= SIZES and not heavy):
            break
        b.log(_("nothing came of that - asking to break {n} {item} instead", n=n, item=name))
    b.log(_("the bag showed no change after breaking {item}", item=name), speak=True)
    return 0


def _check(b: Bot, cfg: Mapping[str, Any], where: str) -> None:
    """Check only: what would go and what it would leave; each crystal's page is read, nothing typed."""
    have = town.counts(b)
    gone, made = outcome(have, cfg)
    for name, n in gone.items():
        b.log(_("would break {n} {item}", n=n, item=pretty(name)))
    for name, n in made.items():
        b.log(_("that would leave {n} {item}", n=n, item=pretty(name)))
    for item, n in todo(have, cfg):
        if item.crystal:
            name = _names(b).get(item.name) or pretty(item.name)
            page = _open_page(b, where, name)
            if page is not None:
                b.log(_("{n} {item}: each gives {k} {product} for {fee} arnebia", n=n, item=name, k=page.gives,
                        product=page.product, fee=page.fee))
            b.close_dialogs()
    if not any(i.crystal for i, _n in todo(have, cfg)):
        crafting.open_craftsman(b, "gem_craftsman", where)
    b.log(_("the Gem Craftsman is open; nothing was broken (check only)"), speak=True)
    b.close_dialogs()


def _sell(b: Bot) -> int:
    """Sell what the bag manager's rules say to sell, as Tools, Sell shards now does: gem and crystal
    shards alike, and nothing the player keeps or stores. How many kinds went."""
    return int(town.sell_shards_now(b) or 0)


def _work(b: Bot, cfg: Mapping[str, Any], where: str) -> tuple[int, bool]:
    """Break all there is to break: (how many went, the bag was left too heavy to go on). With
    selling on, a bag too heavy is sold from and the work goes on, as long as something sells."""
    left_out: set[str] = set()
    broken = fails = 0
    for _step in range(MOST_STEPS):
        if b.stop_event.is_set():
            raise Stopped(_("stopped"))
        rows = [(i, n) for i, n in todo(town.counts(b), cfg) if i.name not in left_out]
        if not rows or fails >= FAILS:
            break
        item, n = rows[0]
        try:
            gone = _break(b, where, item, n)
        except TooHeavy:
            if cfg.get("sell") and _sell(b):
                continue
            b.close_dialogs()
            b.log(_("the bag is too heavy to break any more: make room and start again"), speak=True)
            return broken, True
        broken += gone
        fails = 0 if gone else fails + 1
        if not gone:
            left_out.add(item.name)
    b.close_dialogs()
    if fails >= FAILS:
        b.log(_("the Gem Breaker stopped: {n} kinds in a row couldn't be broken", n=fails), speak=True)
    return broken, False


def run(b: Bot, cfg: Mapping[str, Any], check_only: bool = False) -> None:
    """The Gem Breaker. cfg: config()."""
    if not (cfg.get("colours") or cfg.get("weapon") or cfg.get("equipment")):
        raise Stopped(_("set it up first: Tools, {tool}", tool=_("Gem Breaker")))
    where = crafting.place(dict(cfg))
    if check_only:
        _check(b, cfg, where)
        return
    broken, heavy = _work(b, cfg, where)
    if broken:
        b.log(_("broke {n} gems and crystals", n=broken), speak=True)
    elif not heavy:
        b.log(_("nothing to break with these settings"), speak=True)
    if cfg.get("sell") and not heavy:
        _sell(b)
