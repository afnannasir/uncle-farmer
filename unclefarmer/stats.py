"""Results: what each run earned, kept per character so a day or a week can be added up.

Nothing here presses a key. The bag (arnebia, shards, every other item) is read from the game's
memory, and contribution, experience and battlefield merit come from the character sheet the bot
reads anyway for the bag's load. A run's record is one JSON line in results/<Character>.jsonl.

The bag's arnebia is kept two ways: what came in (picked up, sold) and what went out (shops,
fares), each reading's change on its own side, with a reading on both sides of every trade and
trip so a purchase never cancels out loot. Earnings are what came in, plus what the run's selling
jobs (selling shards, cleaning the bag) brought; expenses are the items used up, at their stored
prices; net earnings are the one less the other. A purchase is neither: a potion bought is an
expense once it is drunk. Experience is counted from the sheet's "upgrading requires N experience":
it falls as experience comes in, and a level-up adds what was left of the old level (what was
earned into the new one shows up from the next reading on). At the top level there is no such
line; contribution goes up instead.
"""

from __future__ import annotations

import contextlib
import math
import os
import re
import time
from collections.abc import Collection, Generator, Iterable, Mapping
from typing import TYPE_CHECKING, Any, TypeGuard

from . import jsonio as json, knowledge, paths
from .clock import CLOCK, Clock
from .engine.botparts.base import STOPPING, note_failure
from .errors import carry_on
from .i18n import N_, _

if TYPE_CHECKING:
    from .engine.botparts.parts import Parts

Record = dict[str, Any]       # one run's results, as saved in results/<Character>.jsonl
Hit = dict[str, Any]          # one recorded hit: damage, kind, skill, target

RESULTS = os.path.join(paths.APP, "results")
UPDATE_EVERY = 20.0          # seconds between bag readings while running
ARNEBIA = "arnebia"
FARMING = "farming"          # a record's kind: farming, or a job that isn't (TOOL)
TOOL = "tool"
# The jobs kept apart from farming, by the script command that runs them: on their own from the
# Tools menu, or inside a farming run (chores before farming, `gem_maker` in a script).
TOOLS: dict[str, str] = {"gem_maker": N_("Gem Maker"), "enhance": N_("Equipment Enhancer"), "chores": N_("Daily chores"),
                         "gather": N_("Gathering"), "sell_collection": N_("Selling the collection"),
                         "sell_shards": N_("Selling shards"), "clean_bag": N_("Cleaning the bag"),
                         "update_shard_prices": N_("Updating shard prices"), "store_items": N_("Storing items"),
                         "restock": N_("Restocking supplies")}
# The jobs that turn a farming run's loot into arnebia: what they bring counts in its earnings.
SELLING = ("sell_shards", "clean_bag")
MOVES = ("thrown", "sold", "stored", "bought", "withdrawn")
OUT = ("thrown", "sold", "stored")      # left the bag after being picked up
IN = ("bought", "withdrawn")            # came into the bag without being picked up

DODGE = 2                    # hit result & 7: the game's own codes (the combat announcer reads them)
BREAK = 4
CRIT = 5
HEAL = 6


def _kind(result: int) -> str:
    """What the game called one hit: a miss, a defence break, a critical hit, a heal or a plain hit."""
    r = result & 7
    if r == DODGE:
        return "miss"
    if r == BREAK:
        return "break"
    if r == CRIT:
        return "crit"
    if r == HEAL:
        return "heal"
    return "normal"


def _blank_side() -> dict[str, Any]:
    return {"hits": 0, "misses": 0, "crits": 0, "breaks": 0, "normals": 0, "damage": 0,
            "best": None, "best_normal": None, "best_crit": None, "best_break": None,
            "last": None, "last_crit": None, "last_break": None}


class _Side:
    """The hits one direction, with the records a player asks for: best, last, by kind."""

    def __init__(self) -> None:
        # the fields of _blank_side(), which result() writes out
        self.hits = self.misses = self.crits = self.breaks = self.normals = self.damage = 0
        self.best: Hit | None = None
        self.best_normal: Hit | None = None
        self.best_crit: Hit | None = None
        self.best_break: Hit | None = None
        self.last: Hit | None = None
        self.last_crit: Hit | None = None
        self.last_break: Hit | None = None
        self.by_monster: dict[str, _Side] = {}

    def miss(self) -> None:
        self.misses += 1

    def add(self, kind: str, amount: int, skill: str, other: dict[str, Any], by: str | None = None) -> None:
        if kind == "heal":
            return
        if kind == "miss":
            self.misses += 1
            return
        self.hits += 1
        if kind == "crit":
            self.crits += 1
        elif kind == "break":
            self.breaks += 1
        else:
            self.normals += 1
        self.damage += amount
        hit = {"damage": amount, "kind": kind, "skill": skill, "target": other}
        self.last = hit
        if kind == "crit":
            self.last_crit = hit
        if kind == "break":
            self.last_break = hit
        for name in ("best", "best_" + kind):
            was: Hit | None = getattr(self, name)
            if was is None or amount > was["damage"]:
                setattr(self, name, hit)
        if by is not None:
            self.by_monster.setdefault(by, _Side()).add(kind, amount, skill, other)

    def result(self) -> dict[str, Any]:
        out: dict[str, Any] = {k: getattr(self, k) for k in _blank_side()}
        if self.by_monster:
            out["by_monster"] = {n: s.result() for n, s in self.by_monster.items()}
        return out


HEAL_TARGETS = ("self", "pet", "others")     # whom our heals landed on


def _blank_skill() -> dict[str, int]:
    return {"uses": 0, "hits": 0, "misses": 0, "crits": 0, "damage": 0, "healed": 0}


def _blank_heals() -> dict[str, dict[str, int]]:
    return {who: {"amount": 0, "times": 0} for who in HEAL_TARGETS}


def skill_name(raw: object) -> str:
    """A cast's skill as the player knows it: the game's "Level 3 " in front taken off."""
    return re.sub(r"^Level \d+ ", "", str(raw or "")).strip()


class CombatStats:
    """Damage the game reported, both ways, from the casts it sends the agent.

    A cast's "hits" are [target, result, amount]; result & 7 is the game's own code (2 dodged,
    4 defence break, 5 critical, 6 heal). Our casts hitting a monster are damage given; a
    monster's cast hitting us is damage received. No "hits" at all (an older agent) leaves the
    whole block null rather than pretending zero.

    Every cast of ours also counts for its skill (skills: how often it was cast, buffs too, the
    damage and healing it did), and a heal of ours counts for whom it landed on (heals): us, our
    pet, or someone else. Only skills: a potion is no cast, so the HP it gives is not in here.
    """

    def __init__(self, clock: Clock = CLOCK) -> None:
        self.given = _Side()
        self.received = _Side()
        self.skills: dict[str, dict[str, int]] = {}
        self.heals = _blank_heals()
        self.last_kill: dict[str, Any] | None = None
        self.live = False
        self.on = True               # off while the run does something else than farming, or is paused
        self.clock = clock

    def observe(self, casts: Iterable[dict[str, Any]] | None, me_id: int,
                names: Mapping[int, str] | None = None, pets: Collection[int] = ()) -> None:
        """Fold in casts read for the first time. pets: our pets' ids, for the heals they get."""
        if not self.on:
            return
        names = names or {}
        for c in casts or []:
            mine = c.get("caster") == me_id
            row = self.skills.setdefault(skill_name(c.get("skill")), _blank_skill()) if mine else None
            if row is not None:
                row["uses"] += 1        # a buff too: it has no "hits" key, but it was cast
            hits = c.get("hits")
            if not json.is_array(hits):
                continue
            self.live = True
            skill = skill_name(c.get("skill"))
            if not hits:
                # An attack packet with no hit line is the game's miss (a buff has no "hits" key
                # at all, so it never gets here).
                (self.given if mine else self.received).miss()
                if row is not None:
                    row["misses"] += 1
                continue
            for h in hits:
                if not json.is_array(h) or len(h) < 2 or not isinstance(h[1], int):
                    continue
                target = h[0]
                amount = int(h[2]) if len(h) > 2 and isinstance(h[2], (int, float)) else 0
                kind = _kind(h[1])
                if row is not None:
                    self._count(row, kind, abs(amount), target, me_id, pets)
                if mine and target != me_id:
                    self.given.add(kind, amount, skill, {"id": target, "name": names.get(target)})
                elif not mine and target == me_id:
                    who = c.get("caster")
                    name = names.get(who) if isinstance(who, int) else None
                    self.received.add(kind, amount, skill, {"id": who, "name": name},
                                      by=name or "monster %s" % who)

    def _count(self, row: dict[str, int], kind: str, amount: int, target: object, me_id: int,
               pets: Collection[int]) -> None:
        """One hit of our cast, into its skill's row and, for a heal, into whom it healed."""
        if kind == "heal":
            row["healed"] += amount
            who = "self" if target == me_id else "pet" if target in pets else "others"
            self.heals[who]["amount"] += amount
            self.heals[who]["times"] += 1
        elif target == me_id:
            return              # a line about us on our own cast is no hit on anything
        elif kind == "miss":
            row["misses"] += 1
        else:
            row["hits"] += 1
            row["damage"] += amount
            if kind == "crit":
                row["crits"] += 1

    def note_kill(self, name: str | None) -> None:
        if not self.on:
            return
        self.last_kill = {"name": name or None,
                          "at": time.strftime("%H:%M:%S", time.localtime(self.clock.time()))}

    def result(self) -> dict[str, Any] | None:
        if not self.live:
            return None
        return {"given": self.given.result(), "received": self.received.result(),
                "skills": {k: dict(v) for k, v in self.skills.items()},
                "heals": {k: dict(v) for k, v in self.heals.items()},
                "last_kill": self.last_kill}



def _prices() -> dict[str, int]:
    try:
        with open(os.path.join(paths.DATA, "shards.json"), encoding="utf-8") as f:
            shards: dict[str, dict[str, Any]] = json.load(f).get("shards", {})
        return {k.lower(): v.get("price", 0) for k, v in shards.items()}
    except (OSError, ValueError):
        return {}


def is_shard(name: str) -> bool:
    return "shard" in name.lower()


# Where an item's unit price comes from, for what a run's used-up items cost.
SHOP = "shop"        # a shop's fixed price, from the game data (the cheapest NPC selling it for arnebia)
SHARD = "shard"      # the Shard Market's price last read (data/shards.json): it changes, so an estimate
Price = tuple[int, str]          # (arnebia each, SHOP or SHARD)


def unit_prices() -> dict[str, Price]:
    """{item (lower case): (arnebia each, where the price comes from)} for every item a price is
    stored for. Shop prices are the game data's own, never changed here: a shop menu's price, else
    the item record's; shard prices are the Shard Market's last reading (the game menu's Update shard prices)."""
    out: dict[str, Price] = {}
    npcs: list[dict[str, Any]] = knowledge.data().get("npcs", [])
    for npc in npcs:
        goods: list[dict[str, Any]] = npc.get("goods") or []
        for g in goods:
            price = g.get("price")
            if g.get("they") != "sell" or g.get("currency") != ARNEBIA or not _whole(price) or price <= 0:
                continue
            key = str(g.get("item") or "").strip().lower()
            if key and (key not in out or price < out[key][0]):
                out[key] = (price, SHOP)
    items: list[dict[str, Any]] = knowledge.data().get("items", [])
    for it in items:
        # the item's own list price (toolboxes, hearthstones, Pet Rice Rolls: the grocers' menus
        # were never recorded), where no shop menu gave one
        key = str(it.get("name") or "").strip().lower()
        price = it.get("price")
        if key and key not in out and _whole(price) and price > 0:
            out[key] = (price, SHOP)
    for name, price in _prices().items():
        if _whole(price) and price > 0:
            out[name] = (price, SHARD)
    return out


def _whole(v: object) -> TypeGuard[int]:
    return isinstance(v, int) and not isinstance(v, bool)


def spending(used: Mapping[str, int], prices: Mapping[str, Price]) -> dict[str, dict[str, Any]]:
    """What the used-up items cost: {item: {"n": how many were used, "price": arnebia each or None,
    "source": SHOP | SHARD | None, "cost": n x price or None}}. An item with no stored price keeps
    its count and a price of None: it is shown, never counted as free."""
    out: dict[str, dict[str, Any]] = {}
    for name, n in used.items():
        if not _whole(n) or n <= 0:
            continue
        known = prices.get(name.strip().lower())
        if known is None:
            out[name] = {"n": n, "price": None, "source": None, "cost": None}
        else:
            out[name] = {"n": n, "price": known[0], "source": known[1], "cost": n * known[0]}
    return out


def _add_spending(dst: dict[str, dict[str, Any]], src: Mapping[str, Any] | None) -> None:
    """Add one record's spending to a total: counts and costs add up; the unit price is the last
    record's, and "varied" says the records had different ones (the cost is still exact)."""
    for name, s in (src or {}).items():
        if not json.is_object(s) or not _whole(s.get("n")):
            continue
        d = dst.setdefault(name, {"n": 0, "price": None, "source": None, "cost": None})
        d["n"] += s["n"]
        price: object = s.get("price")
        cost: object = s.get("cost")
        if _whole(price) and _whole(cost):
            if d["price"] is not None and d["price"] != price:
                d["varied"] = True
            d["price"], d["source"] = price, s.get("source")
            before: int = d["cost"] or 0
            d["cost"] = before + cost
        else:
            d["unpriced"] = d.get("unpriced", 0) + s["n"]


class _Part:
    """What one activity of a run did (farming, or a job like the Gem Maker): its time, the bag's
    changes, what the bot itself moved in or out of the bag, and what the sheet gained."""

    def __init__(self) -> None:
        self.seconds = 0.0
        self.changes: dict[str, int] = {}     # item -> net change of the bag while this activity ran
        self.moved: dict[str, dict[str, int]] = {m: {} for m in MOVES}
        self.arnebia_in = self.arnebia_out = 0   # the bag's arnebia: what came in, what went out
        self.gains = {"contribution": 0, "merit": 0}
        self.xp = self.levels = 0
        self.kills = self.deaths = 0

    def add_changes(self, was: Mapping[str, int], now: Mapping[str, int]) -> None:
        """One bag reading's change since the last: into the net changes, and the arnebia also
        into what came in or went out."""
        for n in set(now) | set(was):
            d = now.get(n, 0) - was.get(n, 0)
            if not d:
                continue
            self.changes[n] = self.changes.get(n, 0) + d
            if n.lower() == ARNEBIA:
                if d > 0:
                    self.arnebia_in += d
                else:
                    self.arnebia_out -= d

    def busy(self) -> bool:
        return bool(any(self.changes.values()) or any(any(v.values()) for v in self.moved.values())
                    or any(self.gains.values()) or self.xp or self.kills or self.deaths)


class Tally:
    """One run. update() is called by the bot between fights; result() can be read any time.

    A run's gains go to what it is doing: farming, or a job that isn't farming (TOOLS) - the Gem
    Maker turning shards into gems is no loot, and the chores' walk is no farming time. While the
    run is paused the game is the player's, so nothing that happens then is counted at all: the
    bag and the sheet are read on the way into a pause and out of it, and only the change between
    two readings inside one activity counts. Kills, damage and the time an hour is worked out
    from are farming's alone.

    The bag alone can't tell loot that was thrown away from loot never picked up, so the bot tells
    the tally what it moved itself (moved(): thrown away, sold, stored, bought, taken out of the
    storehouse); picked up = what the bag gained + what went out - what came in other ways."""

    def __init__(self, character: str, title: str, clock: Clock = CLOCK, tool: str | None = None) -> None:
        """tool: the TOOLS key of a run that is that job on its own (Tools menu), not farming."""
        self.character, self.title = character, title
        self.clock = clock
        self.started = self.clock.time()
        self.ended: float | None = None
        self.main = tool or FARMING      # what the run is: the activity its own record is about
        self.doing: str | None = self.main      # where gains go now; None while paused
        self.parts = {self.main: _Part()}
        self.since = self.started        # when `doing` began
        self.paused_s = 0.0
        self.before_pause = self.main
        self.bag0: dict[str, int] | None = None     # {item: count} at the first reading
        self.bag: dict[str, int] = {}               # ... at the last one
        self.sheet0: dict[str, int] | None = None   # {contribution, xp_needed, level, merit} at the first sheet
        self.sheet: dict[str, int] | None = None
        self.drop_left: int | None = None   # seconds of the day's drop time left, at the last sheet seen
        self.counters = (0, 0)       # the bot's kills and deaths at the last reading
        self.maps: list[int] = []
        self.combat = CombatStats(clock)
        self.combat.on = self.main == FARMING
        self.last_update = 0.0
        self.prices = _prices()
        self.unit_prices = unit_prices()   # what used-up items cost, as stored when the run began
        self.saved = False                 # save() writes a run's records once, however often it is called
        self.spoken = self.clock.time()    # for the spoken progress lines (results_every)
        self.deadlines: list[float] = []   # clock times the run ends at the latest (deadline())

    # the main activity's numbers, as the run's own record has them
    @property
    def kills(self) -> int:
        return self.parts[self.main].kills

    @property
    def deaths(self) -> int:
        return self.parts[self.main].deaths

    @property
    def xp(self) -> int:
        return self.parts[self.main].xp

    @property
    def levels(self) -> int:
        return self.parts[self.main].levels

    def _part(self) -> _Part | None:
        return self.parts.get(self.doing) if self.doing is not None else None

    # ------------------------------------------------------------ reading
    def update(self, bot: Parts, force: bool = False) -> None:
        now = self.clock.time()
        if not force and now - self.last_update < UPDATE_EVERY:
            return
        self.last_update = now
        try:
            rows = bot.bag_rows()
            me = bot.me()
            mid = bot.map_id()
        except STOPPING:
            raise                       # a stop, a death or a lost agent must not read as "no stats"
        except Exception as e:          # a reading is a convenience; never stop a run for it
            note_failure(bot, "the run's statistics", e)
            return
        part = self._part()
        bag: dict[str, int] = {}
        for r in rows:
            n = (r.get("name") or "").strip()
            if n:
                bag[n] = bag.get(n, 0) + int(r.get("count") or 0)
        if bag:
            if self.bag0 is None:
                self.bag0 = dict(bag)
            elif part is not None:
                part.add_changes(self.bag, bag)
            self.bag = bag
        self.note_sheet(me.get("sheet"))
        kills, deaths = int(bot.kills or 0), int(getattr(bot, "deaths", 0) or 0)
        if part is not None:
            if self.doing == FARMING:
                part.kills += max(0, kills - self.counters[0])
            part.deaths += max(0, deaths - self.counters[1])
        self.counters = (kills, deaths)
        if mid and mid not in self.maps and self.doing == FARMING:
            self.maps.append(mid)

    def note_sheet(self, sheet: dict[str, Any] | None) -> None:
        left = drop_left(sheet)
        if left is not None:
            self.drop_left = left
        if not sheet or sheet.get("level", -1) < 0:
            return
        cur: dict[str, int] = {k: sheet.get(k, -1) for k in ("contribution", "xp_needed", "level", "merit")}
        prev = self.sheet
        part = self._part()
        if prev is None:
            self.sheet0 = dict(cur)
        elif cur == prev:
            return
        elif part is not None:
            for k in ("contribution", "merit"):
                if cur[k] >= 0 and prev[k] >= 0:
                    part.gains[k] += cur[k] - prev[k]
            if cur["level"] == prev["level"]:
                if cur["xp_needed"] >= 0 and prev["xp_needed"] >= 0:
                    part.xp += max(0, prev["xp_needed"] - cur["xp_needed"])
            elif cur["level"] > prev["level"]:
                part.levels += cur["level"] - prev["level"]
                if prev["xp_needed"] >= 0:
                    part.xp += prev["xp_needed"]
        self.sheet = cur

    def moved(self, how: str, item: str, n: int) -> None:
        """The bot moved n of item itself: how is one of MOVES (thrown, sold, stored, bought,
        withdrawn)."""
        part = self._part() or self.parts[self.main]
        if n > 0 and how in part.moved:
            part.moved[how][item] = part.moved[how].get(item, 0) + int(n)

    def deadline(self, at: float) -> None:
        """The run ends at clock time `at` at the latest: its time limit, the clock time its plan
        stops at, or the daily stop. The earliest one is what Time remaining counts down to."""
        if at > 0 and at not in self.deadlines:
            self.deadlines.append(at)

    def ends_at(self) -> float | None:
        return min(self.deadlines) if self.deadlines else None

    # ------------------------------------------------------------ what the run is doing
    def switch(self, bot: Parts | None, doing: str | None) -> None:
        """From now on gains go to `doing` (None: nowhere, as while paused). The bag and sheet are
        read first, so what came before goes to the activity it came in."""
        if doing == self.doing:
            return
        if bot is not None:
            self.update(bot, force=True)
        now = self.clock.time()
        if self.doing is None:
            self.paused_s += now - self.since
        else:
            self.parts[self.doing].seconds += now - self.since
        self.since = now
        self.doing = doing
        if doing is not None:
            self.parts.setdefault(doing, _Part())
        self.combat.on = doing == FARMING

    def pause(self, bot: Parts | None) -> None:
        if self.doing is not None:
            self.before_pause = self.doing
            self.switch(bot, None)

    def resume(self, bot: Parts | None) -> None:
        if self.doing is None:
            self.switch(bot, self.before_pause)

    @contextlib.contextmanager
    def activity(self, bot: Parts, name: str) -> Generator[None, None, None]:
        """Inside the with block gains go to name (a TOOLS key), then back to where they went."""
        before = self.doing
        self.switch(bot, name)
        try:
            yield
        finally:
            try:
                self.switch(bot, before)
            except Exception as e:      # stopping, or the game gone: switch back without a reading
                carry_on(e, "the results after " + name)
                self.switch(None, before)

    # ------------------------------------------------------------ results
    def result(self) -> Record:
        """The run's own record (farming, or the job it was), a plain dict: what gets saved,
        added up and shown."""
        return self._record(self.main)

    def results(self) -> list[Record]:
        """Every record of the run: its own, then one for each other job it did on the way
        (chores before farming, the Gem Maker inside a script)."""
        out = [self.result()]
        for key, part in self.parts.items():
            if key != self.main and part.busy():
                out.append(self._record(key))
        return out

    def _record(self, key: str) -> Record:
        part = self.parts.get(key) or _Part()
        end = self.ended or self.clock.time()
        seconds = part.seconds + (end - self.since if self.doing == key else 0.0)
        changes = {n: d for n, d in part.changes.items() if d}
        moved = {m: {n: v for n, v in items.items() if v} for m, items in part.moved.items()}
        moved = {m: items for m, items in moved.items() if items}
        picked: dict[str, int] = {}
        used: dict[str, int] = {}
        for n in set(changes) | {n for items in moved.values() for n in items}:
            if n.lower() == ARNEBIA:
                continue
            v = (changes.get(n, 0) + sum(moved.get(m, {}).get(n, 0) for m in OUT)
                 - sum(moved.get(m, {}).get(n, 0) for m in IN))
            if v > 0:
                picked[n] = v
            elif v < 0:
                used[n] = -v
        s1: dict[str, Any] = self.sheet or {}
        farming = key == FARMING
        rec: Record = {
            "character": self.character,
            "run": self.title if key == self.main else tool_name(key),
            "kind": FARMING if farming else TOOL,
            "start": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.started)),
            "minutes": round(seconds / 60.0, 1),
            "kills": part.kills, "deaths": part.deaths,
            "arnebia": sum(d for n, d in changes.items() if n.lower() == ARNEBIA),
            "shards": {n: d for n, d in changes.items() if is_shard(n)},
            "items": {n: d for n, d in changes.items() if n.lower() != ARNEBIA and not is_shard(n)},
            "picked": picked, "used": used, "moved": moved,
            "arnebia_in": part.arnebia_in, "arnebia_out": part.arnebia_out,
            "spent": spending(used, self.unit_prices),
            "contribution": part.gains["contribution"], "merit": part.gains["merit"],
            "experience": part.xp, "levels": part.levels,
            "level": s1.get("level", -1), "xp_needed": s1.get("xp_needed", -1),
        }
        if farming:
            # what the run's own selling jobs brought in, from loot farming picked up
            rec["sales"] = sum(p.arnebia_in for k, p in self.parts.items() if k in SELLING)
            rec["maps"] = list(self.maps)
            rec["combat"] = self.combat.result()
            rec["shard_value"] = sum(v * self.prices.get(n.lower(), 0) for n, v in picked.items() if is_shard(n))
        else:
            rec["tool"] = key
        if key == self.main:
            paused = self.paused_s + (end - self.since if self.doing is None else 0.0)
            rec["paused_minutes"] = round(paused / 60.0, 1)
            # Time remaining: to the earliest end (a pause doesn't move it - a plan's time limit
            # and its stop time are both clock times), none known: None; a finished run: 0.
            ends = self.ends_at()
            if self.ended is not None:
                rec["remaining_minutes"] = 0.0
            elif ends is not None:
                rec["remaining_minutes"] = round(max(0.0, ends - end) / 60.0, 2)
            else:
                rec["remaining_minutes"] = None
        return rec

    def finish(self, bot: Parts | None = None) -> None:
        if bot is not None:
            self.update(bot, force=True)
        self.ended = self.clock.time()

    def save(self) -> None:
        if self.saved:
            return                       # once only: a second save would count the run's usage twice
        self.saved = True
        records = self.results()
        lines: list[str] = []
        for r in records:
            empty = not (r["kills"] or r["deaths"] or r["arnebia"] or r["picked"] or r["used"]
                         or r["contribution"] or r["experience"])
            if empty and (r["minutes"] < 0.5 or len(records) > 1):
                continue                 # nothing happened (a script that only ran the chores); don't clutter
            lines.append(json.dumps(r, ensure_ascii=False) + "\n")
        if lines:
            os.makedirs(RESULTS, exist_ok=True)
            with open(os.path.join(RESULTS, paths.safe_name(self.character) + ".jsonl"), "a", encoding="utf-8") as f:
                f.writelines(lines)


def doing(bot: Parts, name: str) -> contextlib.AbstractContextManager[None]:
    """`with stats.doing(bot, "gem_maker"):` - what the block does goes to that job, not to
    farming (a run that keeps no results: nothing)."""
    tally: Tally | None = getattr(bot, "tally", None)
    return tally.activity(bot, name) if tally is not None else contextlib.nullcontext()


def moved(bot: Parts, how: str, item: str, n: int) -> None:
    """The bot moved n of item itself (thrown, sold, stored, bought, withdrawn): tell the results.
    The bag is read at once, so the arnebia the move took or brought is counted apart from
    anything that comes after."""
    tally: Tally | None = getattr(bot, "tally", None)
    if tally is not None and n:
        reading(bot)
        tally.moved(how, item, n)


def reading(bot: Parts) -> None:
    """Read the bag for the results now, so what happens next is counted on its own (around a
    trip or a trade: a fare or a purchase never cancels out loot picked up before it)."""
    tally: Tally | None = getattr(bot, "tally", None)
    if tally is not None:
        tally.update(bot, force=True)          # (a failed reading is noted there, never raised)


# ---------------------------------------------------------------- history
def characters() -> list[str]:
    try:
        return sorted(f[:-6] for f in os.listdir(RESULTS) if f.endswith(".jsonl"))
    except OSError:
        return []


def runs(character: str, since: float | None = None, kind: str | None = FARMING,
         until: float | None = None) -> list[Record]:
    """Saved records of a character, oldest first; since/until = the clock times a run must have
    started in (from since, before until); kind = FARMING, TOOL or None for both."""
    out: list[Record] = []
    seen: set[str] = set()
    try:
        # utf-8-sig: a byte order mark at the start made the first record unreadable, and it was lost
        with open(os.path.join(RESULTS, paths.safe_name(character) + ".jsonl"), encoding="utf-8-sig") as f:
            for line in f:
                r = _record(line)
                if r is None or (kind is not None and r.get("kind") != kind):
                    continue
                if since is not None or until is not None:
                    started = start_time(r)
                    if started is None or (since is not None and started < since) \
                            or (until is not None and started >= until):
                        continue        # (a record whose start can't be read can't be in a period)
                key = line.strip()
                if key in seen:
                    continue            # the same record written twice: its kills and usage count once
                seen.add(key)
                out.append(r)
    except OSError:
        pass
    return out


def days(character: str, kind: str | None = FARMING) -> list[str]:
    """The days ("2026-11-08") a character has runs on, newest first: the day each run started."""
    found = {str(r.get("start"))[:10] for r in runs(character, kind=kind) if start_time(r) is not None}
    return sorted(found, reverse=True)


def day_bounds(day: str) -> tuple[float, float]:
    """(start, end) of a day ("2026-11-08") as clock times: its local midnight and the next one
    (mktime evens out a day with a clock change in it)."""
    t = time.strptime(day, "%Y-%m-%d")
    end = time.mktime((t.tm_year, t.tm_mon, t.tm_mday + 1, 0, 0, 0, 0, 0, -1))
    return time.mktime(t), end


def _record(line: str) -> Record | None:
    """One line of a results file as a record, or None for one that isn't one: not JSON, not an
    object, or with fields of the wrong shape. One bad line costs only itself, never the report."""
    try:
        raw = json.loads(line)
    except ValueError:
        return None
    if not json.is_object(raw):
        return None
    try:
        return upgrade(raw)
    except (ValueError, AttributeError, TypeError):
        return None


def start_time(r: Record) -> float | None:
    """When the run started, as a clock time, or None when its "start" can't be read."""
    try:
        return time.mktime(time.strptime(str(r.get("start")), "%Y-%m-%d %H:%M:%S"))
    except (ValueError, OverflowError):
        return None


def tool_name(key: str) -> str:
    """A job's name as the player reads it ("Gem Maker" for "gem_maker")."""
    return _(TOOLS.get(key) or key)


def tool_of(title: str | None) -> str | None:
    """The TOOLS key of a run title ("Gem Maker", "Gem Maker check"), or None."""
    t = (title or "").strip().lower()
    for key, name in TOOLS.items():
        if any(t.startswith(n.lower()) for n in (name, _(name))):
            return key
    return None


def upgrade(r: Record) -> Record:
    """A record as this version writes it. Records from before kept no kind (a Gem Maker run
    read as farming) and only the bag's net change (picked up = gained, used up = lost)."""
    if "kind" not in r:
        tool = tool_of(r.get("run"))
        r["kind"] = TOOL if tool else FARMING
        if tool:
            r["tool"] = tool
    if "picked" not in r:
        net: dict[str, int] = dict(r.get("shards") or {})
        net.update(r.get("items") or {})
        r["picked"] = {n: d for n, d in net.items() if d > 0}
        r["used"] = {n: -d for n, d in net.items() if d < 0}
        r["moved"] = {}
    if "spent" not in r:
        # from before spending was kept: costed at the prices stored now (shown as estimates anyway)
        used: dict[str, int] = r.get("used") or {}
        r["spent"] = spending(used, _current_prices())
    if "arnebia_in" not in r:
        # from before the arnebia was kept both ways: only its net change is known
        change: object = r.get("arnebia", 0)
        whole = change if _whole(change) else 0
        r["arnebia_in"], r["arnebia_out"] = max(0, whole), max(0, -whole)
        r["earnings_from_net"] = True
    return r


_price_cache: dict[str, Price] | None = None


def _current_prices() -> dict[str, Price]:
    """unit_prices(), read once rather than once per old record."""
    global _price_cache
    if _price_cache is None:
        _price_cache = unit_prices()
    return _price_cache


def forget_prices() -> None:
    """After the prices changed (the game menu's Update shard prices): old records are costed afresh."""
    global _price_cache
    _price_cache = None


def _merge_side(dst: dict[str, Any], src: dict[str, Any]) -> None:
    for k in ("hits", "misses", "crits", "breaks", "normals", "damage"):
        dst[k] = (dst.get(k) or 0) + (src.get(k) or 0)
    for k in ("best", "best_normal", "best_crit", "best_break"):
        if src.get(k) and (not dst.get(k) or src[k]["damage"] > dst[k]["damage"]):
            dst[k] = src[k]
    for k in ("last", "last_crit", "last_break"):
        if src.get(k):
            dst[k] = src[k]
    by_monster: dict[str, dict[str, Any]] = src.get("by_monster") or {}
    for name, side in by_monster.items():
        _merge_side(dst.setdefault("by_monster", {}).setdefault(name, _blank_side()), side)


def _merge_skills(dst: dict[str, Any], src: Mapping[str, Any]) -> None:
    """Add one record's skill rows and heals to a total. Records from before they were kept have
    neither and add nothing; a total has none only when none of its records had them."""
    skills: object = src.get("skills")
    if json.is_object(skills):
        into: dict[str, dict[str, int]] = dst.setdefault("skills", {})
        for name, row in skills.items():
            if not json.is_object(row):
                continue
            d = into.setdefault(str(name), _blank_skill())
            for k in d:
                v: object = row.get(k)
                d[k] += v if _whole(v) else 0
    heals: object = src.get("heals")
    if json.is_object(heals):
        total: dict[str, dict[str, int]] = dst.setdefault("heals", _blank_heals())
        for who in HEAL_TARGETS:
            h: object = heals.get(who)
            if json.is_object(h):
                for k in ("amount", "times"):
                    n: object = h.get(k)
                    total[who][k] += n if _whole(n) else 0


def _add(dst: dict[str, int], src: Mapping[str, int] | None) -> None:
    for n, d in (src or {}).items():
        dst[n] = dst.get(n, 0) + d


def add_up(results: Iterable[Record]) -> Record:
    """One result from many (a day, a week) of one kind."""
    results = [upgrade(dict(r)) for r in results]
    total: Record = {"runs": len(results), "minutes": 0.0, "paused_minutes": 0.0, "kills": 0, "deaths": 0,
             "arnebia": 0, "arnebia_in": 0, "arnebia_out": 0, "sales": 0,
             "shards": {}, "shard_value": 0, "contribution": 0, "merit": 0, "experience": 0,
             "levels": 0, "items": {}, "picked": {}, "used": {}, "moved": {}, "spent": {}, "level": -1, "xp_needed": -1,
             "combat": None, "kind": results[0]["kind"] if results else FARMING}
    for r in results:
        for k in ("minutes", "paused_minutes", "kills", "deaths", "arnebia", "arnebia_in", "arnebia_out", "sales",
                  "shard_value", "contribution", "merit", "experience", "levels"):
            v = r.get(k, 0)
            total[k] += v if isinstance(v, (int, float)) and not isinstance(v, bool) else 0
        for k in ("shards", "items", "picked", "used"):
            _add(total[k], r.get(k))
        _add_spending(total["spent"], r.get("spent"))
        moved: dict[str, dict[str, int]] = r.get("moved") or {}
        for how, items in moved.items():
            _add(total["moved"].setdefault(how, {}), items)
        if r.get("earnings_from_net"):
            total["earnings_from_net"] = True
        level = r.get("level")
        if isinstance(level, int) and not isinstance(level, bool) and level >= 0:
            total["level"], total["xp_needed"] = level, r.get("xp_needed", -1)
        c: dict[str, Any] | None = r.get("combat")
        if c:
            combat: dict[str, Any] | None = total["combat"]
            if combat is None:
                combat = total["combat"] = {"given": _blank_side(), "received": _blank_side(), "last_kill": None}
            _merge_side(combat["given"], c.get("given") or {})
            _merge_side(combat["received"], c.get("received") or {})
            _merge_skills(combat, c)
            if c.get("last_kill"):
                combat["last_kill"] = c["last_kill"]
    total["minutes"] = round(total["minutes"], 1)
    total["paused_minutes"] = round(total["paused_minutes"], 1)
    if total["kind"] == TOOL and len({r.get("tool") for r in results}) == 1:
        total["tool"] = results[0].get("tool")
    return total


def _n(v: float) -> str:
    return "{:,}".format(int(v))


def _clock(minutes: object, up: bool = False) -> str:
    """Minutes as hours:minutes ("03:05"); up: rounded up, for time still to go (it says 00:00
    only at the end)."""
    v = float(minutes) if isinstance(minutes, (int, float)) and not isinstance(minutes, bool) else 0.0
    whole = math.ceil(v - 1e-9) if up else int(round(v))
    h, m = divmod(max(0, whole), 60)
    return "%02d:%02d" % (h, m)


def earnings(r: Record) -> tuple[int, int | None, int]:
    """(earnings so far, total expenses, net earnings) of a farming record, in arnebia. Earnings:
    the arnebia that came into the bag (picked up, sold) and what the run's selling jobs brought;
    expenses: the items used up at their stored prices (None when nothing used up has a price);
    items with no stored price are left out, as in the spending lines. Purchases are neither."""
    def num(v: object) -> int:
        return v if _whole(v) else 0
    gross = num(r.get("arnebia_in")) + num(r.get("sales"))
    spent: dict[str, dict[str, Any]] = r.get("spent") or {}
    costs = [s["cost"] for s in spent.values() if json.is_object(s) and _whole(s.get("cost"))]
    expenses = sum(costs) if costs else None
    return gross, expenses, gross - (expenses or 0)


def _time_lines(r: Record) -> list[str]:
    """Time used (farming time) and kills; a single run's Time remaining, a period's number of runs;
    the time paused."""
    out = [_("Time used: {used}, {kills} kills, {deaths} deaths", used=_clock(r.get("minutes", 0) or 0),
             kills=r.get("kills", 0), deaths=r.get("deaths", 0))]
    if "runs" in r:
        out.append(_("Runs: {n}", n=r["runs"]))
    elif "remaining_minutes" in r:
        left = r["remaining_minutes"]
        out.append(_("Time remaining: {left}", left=_clock(left, up=True)) if left is not None
                   else _("Time remaining: no end time set (no time limit, stop time or daily stop)"))
    if r.get("paused_minutes"):
        out.append(_("Paused: {n} min, not counted", n=_n(r["paused_minutes"])))
    return out


def _earnings_lines(r: Record) -> list[str]:
    gross, expenses, net = earnings(r)
    spent: dict[str, dict[str, Any]] = r.get("spent") or {}
    shards = any(json.is_object(s) and s.get("source") == SHARD and _whole(s.get("cost")) for s in spent.values())
    unpriced = sum(s.get("unpriced", 0) if _whole(s.get("cost")) else (s.get("n") or 0)
                   for s in spent.values() if json.is_object(s))
    out = [_("Earnings so far: {n} arnebia (picked up and sold)", n=_n(gross))]
    line = _("Total expenses: {n} arnebia (items used up, at their stored prices)", n=_n(expenses or 0))
    if shards:
        line += _(" - estimated: it includes shards")
    if unpriced:
        line += _(" - {n} used up with no stored price, not counted", n=_n(unpriced))
    out.append(line)
    out.append(_("Net earnings: {n} arnebia", n=("+" if net > 0 else "") + _n(net)))
    paid = r.get("arnebia_out")
    if _whole(paid) and paid > 0:
        out.append(_("Arnebia paid out at shops and for fares: {n} (a bought item is an expense once it is used up)",
                     n=_n(paid)))
    if r.get("earnings_from_net"):
        out.append(_("Older runs kept only the bag's net arnebia change: their earnings are counted from that"))
    return out


def _rate(v: float, minutes: float) -> str | None:
    return _n(v * 60.0 / minutes) if minutes >= 5 else None


def _kind_word(kind: str) -> str:
    return {"crit": _("critical"), "break": _("defence break"), "normal": _("normal"),
            "miss": _("missed"), "heal": _("heal")}.get(kind, kind)


def _hit(h: Hit | None, by: bool = False) -> str:
    if not h:
        return "null"
    t: dict[str, Any] = h.get("target") or {}
    name = t.get("name") if t.get("name") is not None else t.get("id")
    bits = [_n(h.get("damage", 0)), "(%s)" % _kind_word(str(h.get("kind")))]
    if h.get("skill"):
        bits.append(_("with {skill}", skill=h["skill"]))
    if name is not None:
        bits.append(_("from {name}", name=name) if by else _("on {name}", name=name))
    return " ".join(bits)


def _combat_lines(c: dict[str, Any] | None) -> list[str]:
    if c is None:
        return [_("Damage data: null (no hit data for this run)")]
    out: list[str] = []
    g: dict[str, Any] = c.get("given") or {}
    out.append(_("Damage given: {dmg} ({hits} hits, {misses} misses)", dmg=_n(g.get("damage", 0)),
                 hits=g.get("hits", 0), misses=g.get("misses", 0)))
    out.append(_("Critical hits: {crits}, defence breaks: {breaks}, normal hits: {normals}",
                 crits=g.get("crits", 0), breaks=g.get("breaks", 0), normals=g.get("normals", 0)))
    out.append(_("Best hit: {hit}", hit=_hit(g.get("best"))))
    out.append(_("Best normal hit: {hit}", hit=_hit(g.get("best_normal"))))
    out.append(_("Last hit: {hit}", hit=_hit(g.get("last"))))
    out.append(_("Last critical hit: {hit}", hit=_hit(g.get("last_crit"))))
    out.append(_("Last defence break: {hit}", hit=_hit(g.get("last_break"))))
    r: dict[str, Any] = c.get("received") or {}
    out.append(_("Damage received: {dmg} ({hits} hits, {misses} misses)", dmg=_n(r.get("damage", 0)),
                 hits=r.get("hits", 0), misses=r.get("misses", 0)))
    out.append(_("Monster hits: {hits} hits ({crits} critical, {breaks} defence breaks, {normals} normal), "
                 "{misses} misses", hits=r.get("hits", 0), crits=r.get("crits", 0),
                 breaks=r.get("breaks", 0), normals=r.get("normals", 0), misses=r.get("misses", 0)))
    out.append(_("Worst hit received: {hit}", hit=_hit(r.get("best"), by=True)))
    out.append(_("Worst normal hit received: {hit}", hit=_hit(r.get("best_normal"), by=True)))
    out.append(_("Last hit received: {hit}", hit=_hit(r.get("last"), by=True)))
    out.append(_("Last critical hit received: {hit}", hit=_hit(r.get("last_crit"), by=True)))
    out.append(_("Last defence break received: {hit}", hit=_hit(r.get("last_break"), by=True)))
    monsters: dict[str, Any] = r.get("by_monster") or {}
    if monsters:
        out.append(_("Monsters that hit us:"))
        for name, s in sorted(monsters.items(), key=lambda kv: -(kv[1].get("damage") or 0))[:5]:
            out.append("    " + _("{name}: {dmg} damage ({hits} hits, {crits} critical, {breaks} defence breaks)",
                                  name=name, dmg=_n(s.get("damage", 0)), hits=s.get("hits", 0),
                                  crits=s.get("crits", 0), breaks=s.get("breaks", 0)))
    kill: dict[str, Any] = c.get("last_kill") or {}
    out.append(_("Last monster killed: {name}", name=kill.get("name") or "null"))
    return out


def _share(part: int, whole: int) -> str:
    return "%d%%" % round(100.0 * part / whole) if whole > 0 else "0%"


def _skill_lines(c: dict[str, Any] | None, minutes: float) -> list[str]:
    """Each skill we cast: how often, the damage it did and its share of all our skills' damage,
    its critical hits and misses, and what it healed. The skills that did the most damage first,
    then the most cast. Runs from before skills were counted have none, and say so."""
    if c is None:
        return []
    skills: dict[str, dict[str, int]] = c.get("skills") or {}
    if "skills" not in c:
        return [_("Skills used: not counted in these runs (only runs from this version on count them)")]
    rows = [(n, s) for n, s in skills.items() if s.get("uses") or s.get("hits") or s.get("healed")]
    if not rows:
        return [_("Skills used: none")]
    total = sum(s.get("damage", 0) for _n_, s in rows)
    casts = sum(s.get("uses", 0) for _n_, s in rows)
    out = [_("Skills used: {casts} casts, {dmg} damage in all", casts=_n(casts), dmg=_n(total))]
    rate = _rate(casts, minutes)
    if rate:
        out[-1] += _(" ({rate} casts an hour)", rate=rate)
    rows.sort(key=lambda kv: (-kv[1].get("damage", 0), -kv[1].get("uses", 0), kv[0].lower()))
    for name, s in rows:
        bits = [_("cast {n} times", n=_n(s.get("uses", 0)))]
        if s.get("damage"):
            hits = s.get("hits", 0)
            bits.append(_("{dmg} damage ({share} of all)", dmg=_n(s["damage"]), share=_share(s["damage"], total)))
            if hits:
                bits.append(_("{n} a hit on average", n=_n(s["damage"] / hits)))
        if s.get("crits"):
            bits.append(_("{n} critical", n=_n(s["crits"])))
        if s.get("misses"):
            bits.append(_("{n} missed", n=_n(s["misses"])))
        if s.get("healed"):
            bits.append(_("healed {n}", n=_n(s["healed"])))
        out.append("    " + (name or _("a skill with no name")) + ": " + ", ".join(bits))
    return out


def _heal_lines(c: dict[str, Any] | None, minutes: float) -> list[str]:
    """What our own healing skills healed, by whom it landed on: us, our pet, anyone else. Only
    when something was healed: a class with no heals gets no empty lines. Potions are no skills."""
    heals: dict[str, dict[str, int]] = (c or {}).get("heals") or {}
    if not any((heals.get(w) or {}).get("amount") for w in HEAL_TARGETS):
        return []
    labels = {"self": _("Healed ourselves"), "pet": _("Healed our pet"), "others": _("Healed others")}
    out = [_("Healing by our skills (potions not counted):")]
    for who in HEAL_TARGETS:
        h = heals.get(who) or {}
        if not h.get("amount"):
            continue
        line = _("{label}: {hp} HP in {n} heals", label=labels[who], hp=_n(h["amount"]), n=_n(h.get("times", 0)))
        rate = _rate(h["amount"], minutes)
        out.append("    " + line + (_(" ({rate} an hour)", rate=rate) if rate else ""))
    return out


def _biggest(d: Mapping[str, int] | None) -> list[tuple[str, int]]:
    return sorted(((n, v) for n, v in (d or {}).items() if v), key=lambda kv: (-kv[1], kv[0].lower()))


def _fate(name: str, moved: dict[str, dict[str, int]]) -> str:
    """Where some of a picked-up item went: "9 thrown away, 4 sold"."""
    words = {"thrown": _("{n} thrown away"), "sold": _("{n} sold"), "stored": _("{n} stored")}
    bits = [words[how].format(n=_n(moved[how][name])) for how in OUT if (moved.get(how) or {}).get(name)]
    return ", ".join(bits)


def _moved_lines(moved: dict[str, dict[str, int]], hows: Iterable[str]) -> list[str]:
    labels = {"thrown": _("Thrown away:"), "sold": _("Sold:"), "stored": _("Put into the storehouse:"),
              "bought": _("Bought:"), "withdrawn": _("Taken out of the storehouse:")}
    out: list[str] = []
    for how in hows:
        items = _biggest(moved.get(how))
        if items:
            out.append(labels[how])
            out += ["    %s %s" % (_n(v), n) for n, v in items]
    return out


def _spent_line(name: str, s: dict[str, Any]) -> str:
    each = _("about {price}", price=_n(s["price"])) if s.get("varied") else _n(s["price"])
    line = _("{item}: {n} used x {price} = {cost} arnebia", item=name, n=_n(s["n"]), price=each, cost=_n(s["cost"]))
    if s.get("unpriced"):
        line += " " + _("(and {n} more with no price then)", n=_n(s["unpriced"]))
    return "    " + line


def _spending_lines(spent: dict[str, dict[str, Any]] | None) -> list[str]:
    """What the used-up items cost, from the prices stored when the run was saved: shop items at
    the shop's fixed price, shards at the Shard Market price last read - which changes, so those
    are said to be estimates before they are listed. Items used without a stored price are named,
    never counted as free. Bought items are not here: buying is not using."""
    spent = spent or {}
    priced = {n: s for n, s in spent.items() if _whole(s.get("cost")) and _whole(s.get("price")) and s.get("n")}
    unpriced = sorted((n for n, s in spent.items() if n not in priced and s.get("n")), key=str.lower)
    if not priced and not unpriced:
        return []
    out = [_("Arnebia spent on items used up (from the stored prices; bought items are not counted here):")]
    shop = _biggest({n: s["cost"] for n, s in priced.items() if s.get("source") != SHARD})
    shards = _biggest({n: s["cost"] for n, s in priced.items() if s.get("source") == SHARD})
    if shop:
        out.append(_("At the shops' fixed prices:"))
        out += [_spent_line(n, priced[n]) for n, _c in shop]
    if shards:
        out.append(_("Shard prices change at the Shard Market, so these prices are estimated:"))
        out += [_spent_line(n, priced[n]) for n, _c in shards]
    if priced:
        total = sum(s["cost"] for s in priced.values())
        out.append(_("Total arnebia spent on items used: {n}", n=_n(total)) +
                   (_(" (estimated: it includes shards)") if shards else ""))
    if unpriced:
        out.append(_("No price stored, not counted: {list}", list=", ".join(
            "%s %s" % (_n(spent[n]["n"]), n) for n in unpriced)))
    return out


def report(r: Record, title: str) -> str:
    """The full text, one fact per line, what matters most first: time and kills, contribution,
    experience and arnebia, then the loot - every shard picked up, and how many of it were thrown
    away, sold or stored - what was bought or used up, and last the fighting. A job that isn't
    farming (the Gem Maker...) has its own shorter text."""
    r = upgrade(dict(r))
    if r.get("kind") == TOOL:
        return _tool_report(r, title)
    m = r.get("minutes", 0) or 0
    lines = [title] + _time_lines(r)
    head = len(lines)

    def line(label: str, v: float) -> None:
        if not v:
            return
        rate = _rate(v, m)
        lines.append(label + ": " + ("+" if v > 0 else "") + _n(v) +
                     (_(" ({rate} an hour)", rate=rate) if rate else ""))

    line(_("Contribution"), r.get("contribution", 0))
    line(_("Experience"), r.get("experience", 0))
    if r.get("levels"):
        lines.append(_("Levels gained: {n}, now level {level}", n=r["levels"], level=r.get("level")))
    if r.get("xp_needed", -1) > 0:
        lines.append(_("Next level needs {n} more experience", n=_n(r["xp_needed"])))
    line(_("Arnebia"), r.get("arnebia", 0))
    line(_("Battlefield merit"), r.get("merit", 0))
    gained = len(lines) > head
    lines += _earnings_lines(r)
    loot = len(lines)

    picked: dict[str, int] = r.get("picked") or {}
    moved: dict[str, dict[str, int]] = r.get("moved") or {}
    shards = {n: v for n, v in picked.items() if is_shard(n)}
    if shards:
        lines.append(_("Shards picked up: {n}", n=_n(sum(shards.values()))) +
                     (_(", worth about {v} arnebia at the Shard Market", v=_n(r["shard_value"]))
                      if r.get("shard_value") else ""))
        for n, v in _biggest(shards):
            fate = _fate(n, moved)
            lines.append("    +%s %s%s" % (_n(v), n, " (%s)" % fate if fate else ""))
        gone = {how: sum(v for n, v in (moved.get(how) or {}).items() if is_shard(n)) for how in OUT}
        words = {"thrown": _("Shards thrown away: {n}"), "sold": _("Shards sold: {n}"),
                 "stored": _("Shards put into the storehouse: {n}")}
        lines += [words[how].format(n=_n(gone[how])) for how in OUT if gone[how]]
    other = _biggest({n: v for n, v in picked.items() if not is_shard(n)})
    if other:
        lines.append(_("Other items picked up:"))
        for n, v in other:
            fate = _fate(n, moved)
            lines.append("    +%s %s%s" % (_n(v), n, " (%s)" % fate if fate else ""))
    lines += _moved_lines(moved, IN)
    used = _biggest(r.get("used"))
    if used:
        lines.append(_("Used up:"))
        lines += ["    %s %s" % (_n(v), n) for n, v in used]
    lines += _spending_lines(r.get("spent"))
    if not gained and len(lines) == loot:
        lines.append(_("Nothing gained yet."))
    if "combat" in r:
        lines.append(_("Fighting:"))
        lines += _combat_lines(r.get("combat"))
        lines += _skill_lines(r.get("combat"), m)
        lines += _heal_lines(r.get("combat"), m)
    return "\n".join(lines)


def _tool_report(r: Record, title: str) -> str:
    """A job that isn't farming: how long, what it cost, what it made and what it used."""
    lines = [title]
    hours, mins = divmod(int(round(r.get("minutes", 0) or 0)), 60)
    lines.append(_("Time: {h} h {m} min", h=hours, m=mins))
    if "runs" in r:
        lines.append(_("Runs: {n}", n=r["runs"]))
    for label, k in ((_("Arnebia"), "arnebia"), (_("Contribution"), "contribution"),
                     (_("Experience"), "experience")):
        v = r.get(k, 0)
        if v:
            lines.append(label + ": " + ("+" if v > 0 else "") + _n(v))
    made = _biggest(r.get("picked"))
    if made:
        lines.append(_("Made or received:"))
        lines += ["    +%s %s" % (_n(v), n) for n, v in made]
    used = _biggest(r.get("used"))
    if used:
        lines.append(_("Used up:"))
        lines += ["    %s %s" % (_n(v), n) for n, v in used]
    lines += _spending_lines(r.get("spent"))
    lines += _moved_lines(r.get("moved") or {}, IN + OUT)
    if len(lines) <= 2:
        lines.append(_("Nothing changed in the bag."))
    return "\n".join(lines)


def report_tools(results: Iterable[Record], title: str) -> str:
    """The jobs that aren't farming over a period: one text per job (all Gem Maker runs added
    up, then the Equipment Enhancer's...)."""
    by: dict[str, list[Record]] = {}
    for r in results:
        r = upgrade(dict(r))
        by.setdefault(r.get("tool") or tool_of(r.get("run")) or r.get("run") or "?", []).append(r)
    if not by:
        return title + "\n" + _("No Gem Maker, Equipment Enhancer or chores runs.")
    parts = [title]
    for key in sorted(by, key=lambda k: tool_name(k).lower()):
        parts.append(_tool_report(add_up(by[key]), tool_name(key)))
    return "\n\n".join(parts)


def drop_left(sheet: Mapping[str, Any] | None) -> int | None:
    """Seconds of the day's public area drop time left, from a character sheet reading (`mem self`
    sheet: drop_seconds, and how many seconds old that reading is); None when the sheet hasn't
    said yet."""
    if not sheet:
        return None
    left: object = sheet.get("drop_seconds")
    if not _whole(left) or left < 0:
        return None
    age: object = sheet.get("age", 0)
    return max(0, left - (age if _whole(age) else 0))


def spoken_time(seconds: float) -> str:
    """A time to speak: "2 h 15 min", "15 min"."""
    m = int(max(0.0, seconds) // 60)
    return _("{h} h {m} min", h=m // 60, m=m % 60) if m >= 60 else _("{m} min", m=m)


def short(r: Record, drop: int | None = None) -> str:
    """One line to speak. Farming: the time farmed and the day's drop time left first (drop:
    seconds, when a sheet reading has it), then kills and what the sheet gained - the numbers a
    night is judged by. A job: what it made, used and earned."""
    r = upgrade(dict(r))
    if r.get("kind") == TOOL:
        bits = [r.get("run") or tool_name(r.get("tool") or "?")]
        picked: dict[str, int] = r.get("picked") or {}
        spent: dict[str, int] = r.get("used") or {}
        made, used = sum(picked.values()), sum(spent.values())
        if made:
            bits.append(_("{n} made or received", n=_n(made)))
        if used:
            bits.append(_("{n} used up", n=_n(used)))
        if r.get("arnebia"):
            bits.append(_("{n} arnebia", n=_n(r["arnebia"])))
        return ", ".join(bits)
    minutes: object = r.get("minutes", 0)
    bits = [_("farmed {time}", time=spoken_time(60 * (minutes if isinstance(minutes, (int, float)) else 0)))]
    if drop is not None:
        bits.append(_("drop time left {time}", time=spoken_time(drop)))
    bits.append(_("{n} kills", n=r.get("kills", 0)))
    if r.get("contribution"):
        bits.append(_("{n} contribution", n=_n(r["contribution"])))
    if r.get("experience"):
        bits.append(_("{n} experience", n=_n(r["experience"])))
    if r.get("levels"):
        bits.append(_("1 level up") if r["levels"] == 1 else _("{n} levels up", n=r["levels"]))
    return ", ".join(bits)
