"""Tools, Buy collection items: the market's collection lots on the player's list, cheapest first, up to
a price a unit and the game's daily cap - then, if the player wants, sold to the town's buyers.

Collection items are what gathering brings (fish, ore, herbs). The market sells them for gold coins,
and the fish, ore and herb buyers take them for arnebia (town.sell_collection). What the game does
(docs/MARKET_TOOL_DESIGN.md, and the players' own scripts):
  - category COLLECTIONS, read in price order (market.CHEAPEST): the cheapest lot of the whole
    category comes first, so the first lot on the list that costs more than the most a unit allows
    ends the search - every later one costs as much or more;
  - "Please enter the purchase quantity" (a lot can be bought in part), then "successfully
    purchased" or a refusal: "Exceeded the maximum number of collected items that one player can
    purchase per day" (the daily cap, DAILY_MOST), "Not enough gold coins", "overweight", and for a
    lot that can't be had: "New listing goods cannot be traded within one hour", "Item does not exist";
  - the daily cap starts again with the game's day (00:00 GMT, daily.game_day).
The game's words are matched in English, as the agent gives them (gamelang), like market.BOUGHT_TEXT;
the refusals come from the players' notes and haven't been seen live yet - an answer not recognised
counts as a lot that can't be had (passed over, FAILS in a row end the run).

Done better than by hand: a lot is chosen by its listing id (the market's commands, in any language),
what arrives is counted from the bag, what was bought today is kept per character for the game's day
(BOUGHT_FILE), and a bag too heavy can be sold from and the buying taken up again.
"""

from __future__ import annotations

import copy
import json
import os
import re
import threading
from collections.abc import Mapping, Sequence
from typing import Any, TYPE_CHECKING, cast

from .. import daily, gamelang, jsonio, knowledge, paths, stats
from ..i18n import _
from . import market, town
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

COLLECTIONS = 102           # the market's category
DAILY_MOST = 500            # the game's cap a day
MOST_PAGES = 30             # pages read at most in one run
HEAVY_TRIPS = 5             # sales to make room, at most, in one run
FAILS = 10                  # lots in a row that couldn't be bought: something is wrong here
ARRIVE = 2.5                # seconds for a lot to show in the bag
WHEN_HEAVY = ("stop", "sell")
DEFAULTS: dict[str, Any] = {
    "items": [],            # the names to buy (as the game writes them)
    "max_price": 200,       # gold coins a unit, at most
    "daily": DAILY_MOST,    # items a day, at most (the game allows DAILY_MOST)
    "sell_after": False,    # sell the collection to the town's buyers when done
    "when_heavy": "stop",   # the bag too heavy: stop, or sell the collection and buy on
}
BOUGHT_FILE = "_bought_collection.json"     # in the profiles folder: {"day", "bought": {character: n}}

CAPPED = re.compile(r"(?i)exceeded the maximum number of collected items")
NO_GOLD = re.compile(r"(?i)not enough gold coins")
HEAVY = re.compile(r"(?i)\boverweight\b")
CANT_HAVE = re.compile(r"(?i)new listing goods cannot be traded|item does not exist")
BOUGHT, SKIP, CAP, GOLD, TOO_HEAVY, LOST = "bought", "skip", "cap", "gold", "heavy", "lost"

_lock = threading.Lock()


def _whole(v: object, default: int) -> int:
    return int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else default


def config(p: Mapping[str, Any]) -> dict[str, Any]:
    """The character's settings for the tool (profile buy_collection), checked: a daily of 1 to the
    game's DAILY_MOST, a price of at least 1, tidy names."""
    saved: object = p.get("buy_collection")
    cfg: dict[str, Any] = copy.deepcopy(DEFAULTS)
    if jsonio.is_object(saved):
        cfg.update(saved)
    cfg["daily"] = max(1, min(DAILY_MOST, _whole(cfg.get("daily"), DAILY_MOST)))
    cfg["max_price"] = max(1, _whole(cfg.get("max_price"), DEFAULTS["max_price"]))
    items: object = cfg.get("items")
    names: list[object] = list(cast("list[object]", items)) if isinstance(items, list) else []
    cfg["items"] = [str(n).strip() for n in names if str(n).strip()]
    if cfg.get("when_heavy") not in WHEN_HEAVY:
        cfg["when_heavy"] = "stop"
    return cfg


def known_items() -> list[str]:
    """The collection items the bot's data knows: what the fish, ore and herb buyers take."""
    roles = set(town.COLLECTION_BUYERS.values())
    names = {str(i.get("name") or "") for i in knowledge.data().get("items", [])
             if knowledge.buyer_role(i.get("name")) in roles}
    return sorted((n for n in names if n), key=str.lower)


# ---------------------------------------------------------------- what was bought today
def _path() -> str:
    return os.path.join(paths.PROFILES, BOUGHT_FILE)


def _today() -> dict[str, int]:
    try:
        with open(_path(), encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {}
    if not jsonio.is_object(got) or got.get("day") != daily.game_day():
        return {}
    bought = got.get("bought")
    if not jsonio.is_object(bought):
        return {}
    return {str(k): int(v) for k, v in bought.items() if isinstance(v, int) and not isinstance(v, bool)}


def bought_today(character: str) -> int:
    """Collection items this character bought today (the game's day)."""
    with _lock:
        return _today().get(character, 0)


def set_bought_today(character: str, n: int) -> None:
    """Keep what this character bought today (the player may set it in the tool's window too)."""
    with _lock:
        data = _today()
        data[character] = max(0, min(DAILY_MOST, int(n)))
        try:
            jsonio.save(_path(), {"day": daily.game_day(), "bought": data}, indent=1)   # (atomic: a crash keeps the old)
        except OSError:
            pass


def sale_kinds(names: Sequence[str]) -> tuple[str, ...]:
    """The buyers (town.COLLECTION_BUYERS' kinds) that take the listed items; all of them when the
    data knows none of the names. Not the character's own gathering kind only (town.sell_collection's
    choice without kinds): a fisher who buys ore must sell ore."""
    roles = {knowledge.buyer_role(n) for n in names}
    kinds = tuple(kind for kind, role in town.COLLECTION_BUYERS.items() if role in roles)
    return kinds or tuple(town.COLLECTION_BUYERS)


def _who(b: Bot) -> str:
    return str(getattr(b, "profile_key", None) or getattr(b, "character", None) or "")


# ---------------------------------------------------------------- buying
def _plain(text: str) -> str:
    return " ".join(text.split()).casefold()


def wanted(lot: market.Lot, names: Sequence[str]) -> bool:
    """A collection lot of one of the names on the list: the WHOLE name, any case, in English or as
    the player's language pack shows it (the market's own search holds a part of a name, so "Fish"
    would buy every fish there is)."""
    if lot.equip:
        return False
    have = {_plain(lot.name), _plain(gamelang.shown(lot.name))}
    return any(_plain(n) in have for n in names)


def _arrived(b: Bot, name: str, before: int, want: int) -> int:
    """How many of name the bag gained since before (the purchase's own count when the bag doesn't
    show it in ARRIVE seconds: the game said it was bought)."""
    end = b.clock.time() + ARRIVE
    while True:
        got = town.counts(b).get(name.lower(), 0) - before
        if got >= want or b.clock.time() >= end:
            return got if got > 0 else want
        b.sleep(0.1)




def _gained(b: Bot, name: str, before: int) -> int:
    """How many of name the bag gained since before, looked at for ARRIVE seconds (0 when none came)."""
    end = b.clock.time() + ARRIVE
    while True:
        got = town.counts(b).get(name.lower(), 0) - before
        if got > 0 or b.clock.time() >= end:
            return max(0, got)
        b.sleep(0.1)


def _buy_lot(b: Bot, pg: market.Page, lot: market.Lot, want: int) -> tuple[str, str]:
    """Choose the lot by its listing id, type how many, and read the game's answer: (outcome, what
    the game said)."""
    mark = market.said_mark(b)
    market.choose_lot(b, pg, lot)
    if not town.type_number(b, want):
        b.close_dialogs()
        return SKIP, _("the game's quantity box didn't take the number")
    heard = market.said_after(b, mark, lambda t: not market.SELLER_TEXT.search(t))
    if heard is None:
        return LOST, _("the game said nothing")
    if market.BOUGHT_TEXT.search(heard):
        return BOUGHT, heard
    for pattern, outcome in ((CAPPED, CAP), (NO_GOLD, GOLD), (HEAVY, TOO_HEAVY), (CANT_HAVE, SKIP)):
        if pattern.search(heard):
            return outcome, heard
    return SKIP, heard


NEXT, AGAIN, OVER = "next", "again", "over"     # what a page's lots say: the next page, this one again, the end


class _Run:
    """One run of the tool: what is left today, what was bought and spent, why it ended."""

    def __init__(self, b: Bot, cfg: Mapping[str, Any], check: bool) -> None:
        self.b, self.cfg, self.check = b, cfg, check
        self.who = _who(b)
        self.left = max(0, int(cfg["daily"]) - bought_today(self.who))
        self.bought = 0
        self.spent = 0
        self.fails = 0
        self.heavy_trips = 0
        self.passed: set[int] = set()     # listing ids not bought (refused, or read with check)
        self.why = ""                     # why it ended, for the last line

    def take(self, pg: market.Page) -> str:
        """The page's lots, cheapest first: NEXT (all seen), AGAIN (one was bought: the page changed,
        read it afresh) or OVER (the run is over: self.why says why)."""
        for lot in pg.lots:
            if lot.id in self.passed or not wanted(lot, self.cfg["items"]):
                continue
            if lot.price > int(self.cfg["max_price"]):
                return self.over(_("the next lot costs {price} gold coins a unit, more than the most you set ({most})",
                                   price=lot.price, most=self.cfg["max_price"]))
            if lot.opens_in:
                continue                    # its first hour on display: none can buy it yet
            if self.left <= 0:
                return self.over(_("today's {n} are bought", n=self.cfg["daily"]))
            got = self.one(pg, lot)
            if got != NEXT:
                return got
        return NEXT

    def one(self, pg: market.Page, lot: market.Lot) -> str:
        """Buy one lot (all of it, or what is left of today)."""
        want = min(lot.qty, self.left)
        cost = want * lot.price
        if self.check:
            self.b.log(_("would buy {n} {item} for {cost} gold coins", n=want, item=lot.name, cost=cost))
            self.passed.add(lot.id)
            self.left -= want
            self.bought += want
            self.spent += cost
            return NEXT
        before = town.counts(self.b).get(lot.name.lower(), 0)
        outcome, heard = _buy_lot(self.b, pg, lot, want)
        if outcome == LOST and _gained(self.b, lot.name, before) > 0:
            outcome = BOUGHT                # the game said nothing, but the bag tells: it was bought
        if outcome == BOUGHT:
            return self.bought_it(lot, want, before)
        return self.refused(lot, cost, outcome, heard)

    def bought_it(self, lot: market.Lot, want: int, before: int) -> str:
        got = _arrived(self.b, lot.name, before, want)
        self.left = max(0, self.left - got)
        self.bought += got
        self.spent += got * lot.price
        self.fails = 0
        set_bought_today(self.who, bought_today(self.who) + got)
        stats.moved(self.b, "bought", lot.name, got)
        self.b.log(_("bought {n} {item} for {cost} gold coins - {left} left to buy today",
                     n=got, item=lot.name, cost=got * lot.price, left=self.left))
        if self.left <= 0:
            return self.over(_("today's {n} are bought", n=self.cfg["daily"]))
        return AGAIN

    def refused(self, lot: market.Lot, cost: int, outcome: str, heard: str) -> str:
        if outcome == CAP:
            set_bought_today(self.who, DAILY_MOST)
            self.left = 0
            return self.over(_("the game says today's collection items are all bought"))
        if outcome == GOLD:
            return self.over(_("not enough gold coins for {cost}", cost=cost))
        if outcome == TOO_HEAVY:
            return self.heavy()
        if outcome == LOST:
            return self.over(_("the game said nothing about {item} - look in the bag", item=lot.name))
        self.passed.add(lot.id)
        self.fails += 1
        self.b.log(_("not bought: {item} ({why})", item=lot.name, why=heard))
        if self.fails >= FAILS:
            return self.over(_("{n} lots in a row couldn't be bought", n=self.fails))
        return AGAIN

    def heavy(self) -> str:
        """The bag is too heavy: sell the collection and buy on, when the player chose it."""
        if self.cfg["when_heavy"] != "sell" or self.heavy_trips >= HEAVY_TRIPS:
            return self.over(_("the bag is too heavy for more"))
        self.heavy_trips += 1
        self.b.close_dialogs()
        self.b.log(_("the bag is too heavy: selling the collection to make room"), speak=True)
        if not town.sell_collection(self.b, sale_kinds(self.cfg["items"])):
            return self.over(_("the bag is too heavy, and the buyers took nothing"))
        return AGAIN

    def over(self, why: str) -> str:
        self.why = why
        return OVER


def run(b: Bot, cfg: Mapping[str, Any], check: bool = False) -> int:
    """The tool. cfg: config(). How many items it bought (or would buy, with check)."""
    if not cfg.get("items"):
        raise Stopped(_("set it up first: Tools, {tool}", tool=_("Buy collection items")))
    r = _Run(b, cfg, check)
    if r.left <= 0:
        b.log(_("today's {n} collection items are bought already - nothing to buy until the game's day turns",
                n=cfg["daily"]), speak=True)
        return 0
    b.log(_("buying collection items: {items}, at most {most} gold coins a unit, {left} left today",
            items=", ".join(cfg["items"]), most=cfg["max_price"], left=r.left))
    page = 1
    try:
        # pages, and a page read again after each buy or refusal: a day's worth in lots of one, and more
        for _step in range(MOST_PAGES + DAILY_MOST + 2 * FAILS + 2 * HEAVY_TRIPS):
            b.check()
            pg = market.goto(b, COLLECTIONS, market.PRICE, page)
            if pg is None:
                r.why = _("no more lots on the list")
                break
            got = r.take(pg)
            if got == OVER:
                break
            if got == NEXT:
                if pg.page >= pg.last or page >= MOST_PAGES:
                    r.why = _("no more lots on the list")
                    break
                page += 1
        else:
            r.why = r.why or _("stopped: the market was looked at too many times in one run")
    finally:
        b.close_dialogs()
    _end(b, cfg, r)
    return r.bought


def _end(b: Bot, cfg: Mapping[str, Any], r: _Run) -> None:
    if r.check:
        b.log(_("collection: would buy {n} items for {spent} gold coins. {why}", n=r.bought, spent=r.spent,
                why=r.why), speak=True)
        return
    b.log(_("collection: bought {n} items for {spent} gold coins; {left} left to buy today. {why}",
            n=r.bought, spent=r.spent, left=r.left, why=r.why), speak=True)
    if cfg.get("sell_after") and r.bought:
        town.sell_collection(b, sale_kinds(cfg["items"]))
