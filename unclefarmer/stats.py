"""Results: what each run earned, kept per character so a day or a week can be added up.

Nothing here presses a key. The bag (arnebia, shards, every other item) is read from the game's
memory, and contribution, experience and battlefield merit come from the character sheet the bot
reads anyway for the bag's load. A run's record is one JSON line in results/<Character>.jsonl.

Numbers are net: arnebia spent on fares or potions comes off what was picked up, a potion drunk
counts as an item used. Experience is counted from the sheet's "upgrading requires N experience":
it falls as experience comes in, and a level-up adds what was left of the old level (what was
earned into the new one shows up from the next reading on). At the top level there is no such
line; contribution goes up instead.
"""

import contextlib
import os
import re
import time
from typing import Any

from . import jsonio as json, paths
from .clock import CLOCK, Clock
from .engine.botparts.base import STOPPING, note_failure
from .errors import carry_on
from .i18n import N_, _

RESULTS = os.path.join(paths.APP, "results")
UPDATE_EVERY = 20.0          # seconds between bag readings while running
ARNEBIA = "arnebia"
FARMING = "farming"          # a record's kind: farming, or a job that isn't (TOOL)
TOOL = "tool"
# The jobs kept apart from farming, by the script command that runs them: on their own from the
# Tools menu, or inside a farming run (chores before farming, `gem_maker` in a script).
TOOLS: dict[str, str] = {"gem_maker": N_("Gem Maker"), "enhance": N_("Equipment Enhancer"), "chores": N_("Daily chores")}
MOVES = ("thrown", "sold", "stored", "bought", "withdrawn")
OUT = ("thrown", "sold", "stored")      # left the bag after being picked up
IN = ("bought", "withdrawn")            # came into the bag without being picked up

DODGE = 2                    # hit result & 7: the game's own codes (the combat announcer reads them)
BREAK = 4
CRIT = 5
HEAL = 6


def _kind(result):
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


def _blank_side():
    return {"hits": 0, "misses": 0, "crits": 0, "breaks": 0, "normals": 0, "damage": 0,
            "best": None, "best_normal": None, "best_crit": None, "best_break": None,
            "last": None, "last_crit": None, "last_break": None}


class _Side:
    """The hits one direction, with the records a player asks for: best, last, by kind."""

    def __init__(self):
        self.__dict__.update(_blank_side())
        self.by_monster = {}

    def miss(self):
        self.misses += 1

    def add(self, kind, amount, skill, other, by=None):
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
            was = getattr(self, name)
            if was is None or amount > was["damage"]:
                setattr(self, name, hit)
        if by is not None:
            self.by_monster.setdefault(by, _Side()).add(kind, amount, skill, other)

    def result(self):
        out = {k: getattr(self, k) for k in _blank_side()}
        if self.by_monster:
            out["by_monster"] = {n: s.result() for n, s in self.by_monster.items()}
        return out


class CombatStats:
    """Damage the game reported, both ways, from the casts it sends the agent.

    A cast's "hits" are [target, result, amount]; result & 7 is the game's own code (2 dodged,
    4 defence break, 5 critical, 6 heal). Our casts hitting a monster are damage given; a
    monster's cast hitting us is damage received. No "hits" at all (an older agent) leaves the
    whole block null rather than pretending zero.
    """

    def __init__(self, clock: Clock = CLOCK):
        self.given = _Side()
        self.received = _Side()
        self.last_kill = None
        self.live = False
        self.on = True               # off while the run does something else than farming, or is paused
        self.clock = clock

    def observe(self, casts, me_id, names=None):
        if not self.on:
            return
        names = names or {}
        for c in casts or []:
            hits = c.get("hits")
            if not isinstance(hits, list):
                continue
            self.live = True
            mine = c.get("caster") == me_id
            skill = re.sub(r"^Level \d+ ", "", str(c.get("skill") or "")).strip()
            if not hits:
                # An attack packet with no hit line is the game's miss (a buff has no "hits" key
                # at all, so it never gets here).
                (self.given if mine else self.received).miss()
                continue
            for h in hits:
                if not isinstance(h, (list, tuple)) or len(h) < 2 or not isinstance(h[1], int):
                    continue
                target = h[0]
                amount = int(h[2]) if len(h) > 2 and isinstance(h[2], (int, float)) else 0
                kind = _kind(h[1])
                if mine and target != me_id:
                    self.given.add(kind, amount, skill, {"id": target, "name": names.get(target)})
                elif not mine and target == me_id:
                    who = c.get("caster")
                    name = names.get(who)
                    self.received.add(kind, amount, skill, {"id": who, "name": name},
                                      by=name or "monster %s" % who)

    def note_kill(self, name):
        if not self.on:
            return
        self.last_kill = {"name": name or None,
                          "at": time.strftime("%H:%M:%S", time.localtime(self.clock.time()))}

    def result(self):
        if not self.live:
            return None
        return {"given": self.given.result(), "received": self.received.result(),
                "last_kill": self.last_kill}



def _prices():
    try:
        with open(os.path.join(paths.DATA, "shards.json"), encoding="utf-8") as f:
            return {k.lower(): v.get("price", 0) for k, v in json.load(f).get("shards", {}).items()}
    except (OSError, ValueError):
        return {}


def is_shard(name):
    return "shard" in name.lower()


class _Part:
    """What one activity of a run did (farming, or a job like the Gem Maker): its time, the bag's
    changes, what the bot itself moved in or out of the bag, and what the sheet gained."""

    def __init__(self):
        self.seconds = 0.0
        self.changes = {}            # item -> net change of the bag while this activity ran
        self.moved = {m: {} for m in MOVES}
        self.gains = {"contribution": 0, "merit": 0}
        self.xp = self.levels = 0
        self.kills = self.deaths = 0

    def busy(self):
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

    def __init__(self, character, title, clock: Clock = CLOCK, tool=None):
        """tool: the TOOLS key of a run that is that job on its own (Tools menu), not farming."""
        self.character, self.title = character, title
        self.clock = clock
        self.started = self.clock.time()
        self.ended = None
        self.main = tool or FARMING      # what the run is: the activity its own record is about
        self.doing = self.main           # where gains go now; None while paused
        self.parts = {self.main: _Part()}
        self.since = self.started        # when `doing` began
        self.paused_s = 0.0
        self.before_pause = self.main
        self.bag0 = None             # {item: count} at the first reading
        self.bag = {}                # ... at the last one
        self.sheet0 = None           # {contribution, xp_needed, level, merit} at the first sheet
        self.sheet = None
        self.counters = (0, 0)       # the bot's kills and deaths at the last reading
        self.maps = []
        self.combat = CombatStats(clock)
        self.combat.on = self.main == FARMING
        self.last_update = 0.0
        self.prices = _prices()
        self.spoken = self.clock.time()    # for the spoken progress lines (results_every)

    # the main activity's numbers, as the run's own record has them
    kills = property(lambda self: self.parts[self.main].kills)
    deaths = property(lambda self: self.parts[self.main].deaths)
    xp = property(lambda self: self.parts[self.main].xp)
    levels = property(lambda self: self.parts[self.main].levels)

    def _part(self):
        return self.parts.get(self.doing) if self.doing is not None else None

    # ------------------------------------------------------------ reading
    def update(self, bot, force=False):
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
        bag = {}
        for r in rows:
            n = (r.get("name") or "").strip()
            if n:
                bag[n] = bag.get(n, 0) + int(r.get("count") or 0)
        if bag:
            if self.bag0 is None:
                self.bag0 = dict(bag)
            elif part is not None:
                for n in set(bag) | set(self.bag):
                    d = bag.get(n, 0) - self.bag.get(n, 0)
                    if d:
                        part.changes[n] = part.changes.get(n, 0) + d
            self.bag = bag
        self.note_sheet(me.get("sheet"))
        kills, deaths = int(bot.kills or 0), int(bot.deaths or 0)
        if part is not None:
            if self.doing == FARMING:
                part.kills += max(0, kills - self.counters[0])
            part.deaths += max(0, deaths - self.counters[1])
        self.counters = (kills, deaths)
        if mid and mid not in self.maps and self.doing == FARMING:
            self.maps.append(mid)

    def note_sheet(self, sheet):
        if not sheet or sheet.get("level", -1) < 0:
            return
        cur = {k: sheet.get(k, -1) for k in ("contribution", "xp_needed", "level", "merit")}
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

    def moved(self, how, item, n):
        """The bot moved n of item itself: how is one of MOVES (thrown, sold, stored, bought,
        withdrawn)."""
        part = self._part() or self.parts[self.main]
        if n > 0 and how in part.moved:
            part.moved[how][item] = part.moved[how].get(item, 0) + int(n)

    # ------------------------------------------------------------ what the run is doing
    def switch(self, bot, doing):
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

    def pause(self, bot):
        if self.doing is not None:
            self.before_pause = self.doing
            self.switch(bot, None)

    def resume(self, bot):
        if self.doing is None:
            self.switch(bot, self.before_pause)

    @contextlib.contextmanager
    def activity(self, bot, name):
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
    def result(self):
        """The run's own record (farming, or the job it was), a plain dict: what gets saved,
        added up and shown."""
        return self._record(self.main)

    def results(self):
        """Every record of the run: its own, then one for each other job it did on the way
        (chores before farming, the Gem Maker inside a script)."""
        out = [self.result()]
        for key, part in self.parts.items():
            if key != self.main and part.busy():
                out.append(self._record(key))
        return out

    def _record(self, key):
        part = self.parts.get(key) or _Part()
        end = self.ended or self.clock.time()
        seconds = part.seconds + (end - self.since if self.doing == key else 0.0)
        changes = {n: d for n, d in part.changes.items() if d}
        moved = {m: {n: v for n, v in items.items() if v} for m, items in part.moved.items()}
        moved = {m: items for m, items in moved.items() if items}
        picked, used = {}, {}
        for n in set(changes) | {n for items in moved.values() for n in items}:
            if n.lower() == ARNEBIA:
                continue
            v = (changes.get(n, 0) + sum(moved.get(m, {}).get(n, 0) for m in OUT)
                 - sum(moved.get(m, {}).get(n, 0) for m in IN))
            if v > 0:
                picked[n] = v
            elif v < 0:
                used[n] = -v
        s1 = self.sheet or {}
        farming = key == FARMING
        rec = {
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
            "contribution": part.gains["contribution"], "merit": part.gains["merit"],
            "experience": part.xp, "levels": part.levels,
            "level": s1.get("level", -1), "xp_needed": s1.get("xp_needed", -1),
        }
        if farming:
            rec["maps"] = list(self.maps)
            rec["combat"] = self.combat.result()
            rec["shard_value"] = sum(v * self.prices.get(n.lower(), 0) for n, v in picked.items() if is_shard(n))
        else:
            rec["tool"] = key
        if key == self.main:
            paused = self.paused_s + (end - self.since if self.doing is None else 0.0)
            rec["paused_minutes"] = round(paused / 60.0, 1)
        return rec

    def finish(self, bot=None):
        if bot is not None:
            self.update(bot, force=True)
        self.ended = self.clock.time()

    def save(self):
        records = self.results()
        lines = []
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


def doing(bot: Any, name: str) -> Any:
    """`with stats.doing(bot, "gem_maker"):` - what the block does goes to that job, not to
    farming (a run that keeps no results: nothing)."""
    tally = getattr(bot, "tally", None)
    return tally.activity(bot, name) if tally is not None else contextlib.nullcontext()


def moved(bot: Any, how: str, item: str, n: int) -> None:
    """The bot moved n of item itself (thrown, sold, stored, bought, withdrawn): tell the results."""
    tally = getattr(bot, "tally", None)
    if tally is not None and n:
        tally.moved(how, item, n)


# ---------------------------------------------------------------- history
def characters():
    try:
        return sorted(f[:-6] for f in os.listdir(RESULTS) if f.endswith(".jsonl"))
    except OSError:
        return []


def runs(character, since=None, kind: str | None = FARMING):
    """Saved records of a character, oldest first; since = a clock time limit; kind = FARMING,
    TOOL or None for both."""
    out = []
    try:
        with open(os.path.join(RESULTS, paths.safe_name(character) + ".jsonl"), encoding="utf-8") as f:
            for line in f:
                try:
                    r = upgrade(json.loads(line))
                except (ValueError, AttributeError):
                    continue
                if kind is not None and r["kind"] != kind:
                    continue
                if since is None or time.mktime(time.strptime(r["start"], "%Y-%m-%d %H:%M:%S")) >= since:
                    out.append(r)
    except OSError:
        pass
    return out


def tool_name(key: str) -> str:
    """A job's name as the player reads it ("Gem Maker" for "gem_maker")."""
    return _(TOOLS.get(key) or key)


def tool_of(title):
    """The TOOLS key of a run title ("Gem Maker", "Gem Maker check"), or None."""
    t = (title or "").strip().lower()
    for key, name in TOOLS.items():
        if any(t.startswith(n.lower()) for n in (name, _(name))):
            return key
    return None


def upgrade(r):
    """A record as this version writes it. Records from before kept no kind (a Gem Maker run
    read as farming) and only the bag's net change (picked up = gained, used up = lost)."""
    if "kind" not in r:
        tool = tool_of(r.get("run"))
        r["kind"] = TOOL if tool else FARMING
        if tool:
            r["tool"] = tool
    if "picked" not in r:
        net = dict(r.get("shards") or {})
        net.update(r.get("items") or {})
        r["picked"] = {n: d for n, d in net.items() if d > 0}
        r["used"] = {n: -d for n, d in net.items() if d < 0}
        r["moved"] = {}
    return r


def _merge_side(dst, src):
    for k in ("hits", "misses", "crits", "breaks", "normals", "damage"):
        dst[k] = (dst.get(k) or 0) + (src.get(k) or 0)
    for k in ("best", "best_normal", "best_crit", "best_break"):
        if src.get(k) and (not dst.get(k) or src[k]["damage"] > dst[k]["damage"]):
            dst[k] = src[k]
    for k in ("last", "last_crit", "last_break"):
        if src.get(k):
            dst[k] = src[k]
    for name, side in (src.get("by_monster") or {}).items():
        _merge_side(dst.setdefault("by_monster", {}).setdefault(name, _blank_side()), side)


def _add(dst, src):
    for n, d in (src or {}).items():
        dst[n] = dst.get(n, 0) + d


def add_up(results):
    """One result from many (a day, a week) of one kind."""
    results = [upgrade(dict(r)) for r in results]
    total = {"runs": len(results), "minutes": 0.0, "paused_minutes": 0.0, "kills": 0, "deaths": 0,
             "arnebia": 0, "shards": {}, "shard_value": 0, "contribution": 0, "merit": 0, "experience": 0,
             "levels": 0, "items": {}, "picked": {}, "used": {}, "moved": {}, "level": -1, "xp_needed": -1,
             "combat": None, "kind": results[0]["kind"] if results else FARMING}
    for r in results:
        for k in ("minutes", "paused_minutes", "kills", "deaths", "arnebia", "shard_value", "contribution",
                  "merit", "experience", "levels"):
            total[k] += r.get(k, 0) or 0
        for k in ("shards", "items", "picked", "used"):
            _add(total[k], r.get(k))
        for how, items in (r.get("moved") or {}).items():
            _add(total["moved"].setdefault(how, {}), items)
        if r.get("level", -1) >= 0:
            total["level"], total["xp_needed"] = r["level"], r.get("xp_needed", -1)
        c = r.get("combat")
        if c:
            if total["combat"] is None:
                total["combat"] = {"given": _blank_side(), "received": _blank_side(), "last_kill": None}
            _merge_side(total["combat"]["given"], c.get("given") or {})
            _merge_side(total["combat"]["received"], c.get("received") or {})
            if c.get("last_kill"):
                total["combat"]["last_kill"] = c["last_kill"]
    total["minutes"] = round(total["minutes"], 1)
    total["paused_minutes"] = round(total["paused_minutes"], 1)
    if total["kind"] == TOOL and len({r.get("tool") for r in results}) == 1:
        total["tool"] = results[0].get("tool")
    return total


def _n(v):
    return "{:,}".format(int(v))


def _rate(v, minutes):
    return _n(v * 60.0 / minutes) if minutes >= 5 else None


def _kind_word(kind):
    return {"crit": _("critical"), "break": _("defence break"), "normal": _("normal"),
            "miss": _("missed"), "heal": _("heal")}.get(kind, kind)


def _hit(h, by=False):
    if not h:
        return "null"
    t = h.get("target") or {}
    name = t.get("name") if t.get("name") is not None else t.get("id")
    bits = [_n(h.get("damage", 0)), "(%s)" % _kind_word(h.get("kind"))]
    if h.get("skill"):
        bits.append(_("with {skill}", skill=h["skill"]))
    if name is not None:
        bits.append(_("from {name}", name=name) if by else _("on {name}", name=name))
    return " ".join(bits)


def _combat_lines(c):
    if c is None:
        return [_("Damage data: null (no hit data for this run)")]
    out = []
    g = c.get("given") or {}
    out.append(_("Damage given: {dmg} ({hits} hits, {misses} misses)", dmg=_n(g.get("damage", 0)),
                 hits=g.get("hits", 0), misses=g.get("misses", 0)))
    out.append(_("Critical hits: {crits}, defence breaks: {breaks}, normal hits: {normals}",
                 crits=g.get("crits", 0), breaks=g.get("breaks", 0), normals=g.get("normals", 0)))
    out.append(_("Best hit: {hit}", hit=_hit(g.get("best"))))
    out.append(_("Best normal hit: {hit}", hit=_hit(g.get("best_normal"))))
    out.append(_("Last hit: {hit}", hit=_hit(g.get("last"))))
    out.append(_("Last critical hit: {hit}", hit=_hit(g.get("last_crit"))))
    out.append(_("Last defence break: {hit}", hit=_hit(g.get("last_break"))))
    r = c.get("received") or {}
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
    monsters = r.get("by_monster") or {}
    if monsters:
        out.append(_("Monsters that hit us:"))
        for name, s in sorted(monsters.items(), key=lambda kv: -(kv[1].get("damage") or 0))[:5]:
            out.append("    " + _("{name}: {dmg} damage ({hits} hits, {crits} critical, {breaks} defence breaks)",
                                  name=name, dmg=_n(s.get("damage", 0)), hits=s.get("hits", 0),
                                  crits=s.get("crits", 0), breaks=s.get("breaks", 0)))
    kill = c.get("last_kill") or {}
    out.append(_("Last monster killed: {name}", name=kill.get("name") or "null"))
    return out


def _biggest(d):
    return sorted(((n, v) for n, v in (d or {}).items() if v), key=lambda kv: (-kv[1], kv[0].lower()))


def _fate(name, moved):
    """Where some of a picked-up item went: "9 thrown away, 4 sold"."""
    words = {"thrown": _("{n} thrown away"), "sold": _("{n} sold"), "stored": _("{n} stored")}
    bits = [words[how].format(n=_n(moved[how][name])) for how in OUT if (moved.get(how) or {}).get(name)]
    return ", ".join(bits)


def _moved_lines(moved, hows):
    labels = {"thrown": _("Thrown away:"), "sold": _("Sold:"), "stored": _("Put into the storehouse:"),
              "bought": _("Bought:"), "withdrawn": _("Taken out of the storehouse:")}
    out = []
    for how in hows:
        items = _biggest(moved.get(how))
        if items:
            out.append(labels[how])
            out += ["    %s %s" % (_n(v), n) for n, v in items]
    return out


def report(r, title):
    """The full text, one fact per line, what matters most first: time and kills, contribution,
    experience and arnebia, then the loot - every shard picked up, and how many of it were thrown
    away, sold or stored - what was bought or used up, and last the fighting. A job that isn't
    farming (the Gem Maker...) has its own shorter text."""
    r = upgrade(dict(r))
    if r.get("kind") == TOOL:
        return _tool_report(r, title)
    m = r.get("minutes", 0) or 0
    lines = [title]
    hours, mins = divmod(int(round(m)), 60)
    lines.append(_("Time: {h} h {m} min, {kills} kills, {deaths} deaths", h=hours, m=mins,
                   kills=r.get("kills", 0), deaths=r.get("deaths", 0)))
    if "runs" in r:
        lines.append(_("Runs: {n}", n=r["runs"]))
    if r.get("paused_minutes"):
        lines.append(_("Paused: {n} min, not counted", n=_n(r["paused_minutes"])))

    def line(label, v):
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

    picked, moved = r.get("picked") or {}, r.get("moved") or {}
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
    if len(lines) <= 2:
        lines.append(_("Nothing gained yet."))
    if "combat" in r:
        lines.append(_("Fighting:"))
        lines += _combat_lines(r.get("combat"))
    return "\n".join(lines)


def _tool_report(r, title):
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
    lines += _moved_lines(r.get("moved") or {}, IN + OUT)
    if len(lines) <= 2:
        lines.append(_("Nothing changed in the bag."))
    return "\n".join(lines)


def report_tools(results, title):
    """The jobs that aren't farming over a period: one text per job (all Gem Maker runs added
    up, then the Equipment Enhancer's...)."""
    by = {}
    for r in results:
        r = upgrade(dict(r))
        by.setdefault(r.get("tool") or tool_of(r.get("run")) or r.get("run") or "?", []).append(r)
    if not by:
        return title + "\n" + _("No Gem Maker, Equipment Enhancer or chores runs.")
    parts = [title]
    for key in sorted(by, key=lambda k: tool_name(k).lower()):
        parts.append(_tool_report(add_up(by[key]), tool_name(key)))
    return "\n\n".join(parts)


def short(r):
    """One line to speak: only what changed."""
    r = upgrade(dict(r))
    if r.get("kind") == TOOL:
        bits = [r.get("run") or tool_name(r.get("tool") or "?")]
        made, used = sum((r.get("picked") or {}).values()), sum((r.get("used") or {}).values())
        if made:
            bits.append(_("{n} made or received", n=_n(made)))
        if used:
            bits.append(_("{n} used up", n=_n(used)))
        if r.get("arnebia"):
            bits.append(_("{n} arnebia", n=_n(r["arnebia"])))
        return ", ".join(bits)
    bits = [_("{n} kills", n=r.get("kills", 0))]
    g = (r.get("combat") or {}).get("given") or {}
    if g.get("damage"):
        bits.append(_("{n} damage", n=_n(g["damage"])))
    if g.get("crits"):
        bits.append(_("{n} critical hits", n=g["crits"]))
    if r.get("arnebia"):
        bits.append(_("{n} arnebia", n=_n(r["arnebia"])))
    got = sum(v for n, v in (r.get("picked") or {}).items() if is_shard(n))
    if got:
        thrown = sum(v for n, v in ((r.get("moved") or {}).get("thrown") or {}).items() if is_shard(n))
        bits.append(_("{n} shards", n=got) + (_(" ({n} thrown away)", n=thrown) if thrown else ""))
    if r.get("contribution"):
        bits.append(_("{n} contribution", n=_n(r["contribution"])))
    if r.get("experience"):
        bits.append(_("{n} experience", n=_n(r["experience"])))
    if r.get("levels"):
        bits.append(_("1 level up") if r["levels"] == 1 else _("{n} levels up", n=r["levels"]))
    return ", ".join(bits)
