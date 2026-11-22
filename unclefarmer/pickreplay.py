"""Replay a recorded night's target picks through picking.choose, with other ways of choosing.

    uv run python -m unclefarmer.pickreplay <packets log> <field jsonl> <character> [--chase 40]
        [--speed 4.0] [--me <our id>]

The recorder's packets (mwagent/out/packets.<pid>.log, plain or .gz) say what the game showed: the
monsters in view and where (IN op=32, 36/37, 33), their HP after each cast (IN op=41 "id,hp"
rows), who struck whom (op=41 hit rows), deaths (op=41 "id,0" rows, op=42 HP ticks to 0). The
field log (logs/field-<day>.jsonl) says where the character stood at each of its picks and what it
picked. At every pick this rebuilds the snapshot, asks picking.choose under each ORDER, and for
each one's first choice asks: how many steps away, how long to get in reach, and was it dead
(killed by someone else) before we would have got there.

First-order only: each pick is judged from where the bot really stood, not where a different
choice before it would have left it. What the bot held against monsters (skips, unselectable
spots) is not in the packets, so Notes carry only who struck us (the last 4 s) and whom we hurt
(kills.KEEP); the farming area is not known either. The "bot" order agreeing with the field log's
own picks says how far the rebuilt snapshot can be trusted.
"""

from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import math
import re
import sys
from collections import Counter, defaultdict
from collections.abc import Iterator, Sequence
from typing import Any

from .engine import kills, picking
from .engine.botparts.walking import WalkingMixin

LINE = re.compile(r"^(\d\d):(\d\d):(\d\d)\.(\d\d\d)\s+(IN|OUT)\s.*?\bop=(\d+)\s.*?hex=([0-9a-f]+)")
STAMP = bytes.fromhex("000001a1")        # the top half of the game's millisecond clock, late 2026
SEEN = 4.0               # a monster that struck us this long ago is no longer an attacker
HP_WINDOW = 3.0          # seconds of HP readings for a dying rate (FightingMixin.HP_WINDOW)
FIRST_HIT = 0.5          # FightingMixin.FIRST_HIT
DOOM_SLACK = 0.5         # fighting.doomed's slack without fight_locked
NO_REACH = 19.5          # the reach when a pick record carries none
MOMENTS = (0.0, 0.25, 0.5, 0.75, 1.0)   # seconds into a pick's whole second it is judged at
WARM_UP = 30.0           # picks this soon after the log begins are left out (the view isn't built yet)


def packets(path: str) -> Iterator[tuple[float, str, int, bytes]]:
    """(seconds of the day, direction, opcode, message bytes) per line; a day is added each time
    the clock wraps past midnight."""
    opener = gzip.open if path.endswith(".gz") else open
    day, last = 0, -1.0
    with opener(path, "rt", encoding="utf-8", errors="replace") as f:
        for line in f:
            m = LINE.match(line)
            if not m:
                continue
            h, mi, s, ms, way, op, hx = m.groups()
            t = int(h) * 3600 + int(mi) * 60 + int(s) + int(ms) / 1000.0
            if t < last - 3600:
                day += 1
            last = t
            try:
                yield t + 86400 * day, way, int(op), bytes.fromhex(hx)
            except ValueError:
                continue


def i32(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 4], "big", signed=True)


def u16(b: bytes, at: int) -> int:
    return int.from_bytes(b[at:at + 2], "big")


def cast_rows(m: bytes) -> tuple[int | None, list[tuple[int, int]], list[tuple[int, int]]]:
    """(caster or None, [(id, hp after)], [(target, result)]) of an IN op=41 message: the cast
    line, every "id,hp" line anywhere in it (the agent's World.combat reads them all: deaths come
    as hp 0), and the "target,result,amount" lines of the fifth section (World.hits)."""
    sections = m[1:].decode("utf-8", "replace").split("\r\n")
    rows = sections[0].split("\n")
    caster: int | None = None
    head = rows[0].split(",")
    if len(head) >= 3:
        try:
            caster = int(head[0])
        except ValueError:
            caster = None
    hp: list[tuple[int, int]] = []
    for r in m[1:].decode("utf-8", "replace").splitlines():
        f = r.split(",")
        if len(f) == 2:
            try:
                hp.append((int(f[0]), int(f[1])))
            except ValueError:
                pass
    hits: list[tuple[int, int]] = []
    if len(sections) > 4:
        for r in sections[4].split("\n"):
            f = r.split(",")
            if len(f) >= 3:
                try:
                    hits.append((int(f[0]), int(f[1])))
                except ValueError:
                    pass
    return caster, hp, hits


class World:
    """Monsters in view, as the packets say, with who struck whom."""

    def __init__(self, me: int | None) -> None:
        self.me = me
        self.mobs: dict[int, dict[str, Any]] = {}
        self.hp_log: dict[int, list[tuple[float, float]]] = defaultdict(list)
        self.struck_us: dict[int, float] = {}        # monster -> when it last struck us
        self.ours: dict[int, float] = {}             # monster -> when our hit last landed

    def feed(self, t: float, way: str, op: int, m: bytes) -> None:
        if way != "IN":
            return
        if op == 6:
            self.mobs.clear()
        elif op == 32 and len(m) >= 38:
            self._appear(t, m)
        elif op in (36, 37):
            e = self.mobs.get(i32(m, 1))
            if e is None:
                return
            at = m.find(STAMP, 1, 18)
            if at < 0 or at + 12 > len(m):
                return
            e["x"], e["y"] = u16(m, at + 8) / 100.0, u16(m, at + 10) / 100.0
        elif op == 33 and len(m) >= 5:
            self.drop(i32(m, 1))
        elif op == 41:
            self._cast(t, m)
        elif op == 42 and len(m) >= 9 and i32(m, 1) < 0 and i32(m, 5) <= 0:
            self.drop(i32(m, 1))

    def _appear(self, t: float, m: bytes) -> None:
        """IN op=32: a monster comes into view (or a corpse, a drop)."""
        mid = i32(m, 1)
        if mid >= 0:
            return
        end = 37
        while end < len(m) and m[end] != 0x2C and m[end] >= 0x20:
            end += 1
        name = m[37:end].decode("utf-8", "replace").strip()
        hp, maxhp = i32(m, 8), i32(m, 12)     # N.z: id, kind short, flags byte, then HP and max HP
        if hp <= 0 or maxhp <= 1:
            self.drop(mid)
            return
        self.mobs[mid] = {"id": mid, "name": name, "kind": "monster", "x": u16(m, 33) / 100.0,
                          "y": u16(m, 35) / 100.0, "hp": hp, "maxhp": maxhp}
        self.hp_log.pop(mid, None)
        self._hp(mid, t, hp)

    def _cast(self, t: float, m: bytes) -> None:
        """IN op=41: HP after a cast, deaths, who struck whom."""
        caster, hp, hits = cast_rows(m)
        for mid, left in hp:
            if mid < 0:
                if left <= 0:
                    self.drop(mid)
                elif mid in self.mobs:
                    self.mobs[mid]["hp"] = left
                    self._hp(mid, t, left)
        if caster is None:
            return
        for target, result in hits:
            if caster < 0 and target == self.me:
                self.struck_us[caster] = t
            elif caster == self.me and target < 0 and result & 7 != kills.DODGED:
                self.ours[target] = t

    def _hp(self, mid: int, t: float, hp: float) -> None:
        rows = self.hp_log[mid]
        if rows and hp > rows[-1][1]:
            rows.clear()
        rows.append((t, hp))
        while len(rows) > 1 and t - rows[0][0] > HP_WINDOW:
            rows.pop(0)

    def drop(self, mid: int) -> None:
        self.mobs.pop(mid, None)
        self.hp_log.pop(mid, None)
        self.ours.pop(mid, None)

    def dying_in(self, e: dict[str, Any]) -> float:
        rows = self.hp_log.get(e["id"]) or []
        if len(rows) < 2 or rows[-1][0] - rows[0][0] < 0.8:
            return math.inf
        rate = (rows[0][1] - rows[-1][1]) / (rows[-1][0] - rows[0][0])
        return float(e["hp"]) / rate if rate > 0 else math.inf


def deaths(path: str, me: int | None, shift: float) -> dict[int, list[tuple[float, bool]]]:
    """id -> [(when it died, whether a cast of ours did it)], from the whole log (ids are reused)."""
    out: dict[int, list[tuple[float, bool]]] = defaultdict(list)
    for t, way, op, m in packets(path):
        if way != "IN":
            continue
        if op == 41:
            caster, hp, _hits = cast_rows(m)
            for mid, left in hp:
                if mid < 0 and left <= 0:
                    out[mid].append((t + shift, caster == me))
        elif op == 42 and len(m) >= 9 and i32(m, 1) < 0 and i32(m, 5) <= 0:
            out[i32(m, 1)].append((t + shift, False))
    return out


def our_id(path: str) -> int | None:
    """Our character's id: the player whose cast reports most often follow our own cast requests
    (OUT op=15) within 0.6 s."""
    asked: list[float] = []
    seen: Counter[int] = Counter()
    for t, way, op, m in packets(path):
        if way == "OUT" and op == 15:
            asked.append(t)
        elif way == "IN" and op == 41 and asked and t - asked[-1] <= 0.6:
            caster, _hp, _hits = cast_rows(m)
            if caster is not None and caster > 0:
                seen[caster] += 1
    return seen.most_common(1)[0][0] if seen else None


class _Walker:
    """Just enough of a bot for WalkingMixin.walk_dist: its cache, its limit and its reach."""
    DIST_LIMIT = WalkingMixin.DIST_LIMIT
    walk_dist = WalkingMixin.walk_dist

    def __init__(self) -> None:
        self._dists: Any = None
        self.rng = NO_REACH

    def reach(self) -> float:
        return self.rng

    def log(self, text: str) -> None:
        print(text, file=sys.stderr)


def by_straight(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
    """Attackers first, then the nearest in a straight line (no walls)."""
    return (e["id"] not in ctx.notes.hitting, math.hypot(e["x"] - ctx.me["x"], e["y"] - ctx.me["y"]))


def ours_first(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
    """Attackers, then any monster we hurt (however far), then by steps."""
    return (e["id"] not in ctx.notes.hitting, e["id"] not in ctx.notes.tagged, steps)


def untouched_first(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
    """Attackers, then monsters at full HP (no one else on them), then by steps."""
    return (e["id"] not in ctx.notes.hitting, e["hp"] < e["maxhp"] and e["id"] not in ctx.notes.tagged, steps)


def in_reach_first(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
    """Attackers, then anything already in reach (no walk), then the bot's order."""
    straight = math.hypot(e["x"] - ctx.me["x"], e["y"] - ctx.me["y"])
    return (e["id"] not in ctx.notes.hitting, straight > ctx.notes.reach, steps)


ORDERS: dict[str, picking.Order] = {
    "bot": picking.nearest,
    "straight": by_straight,
    "ours first": ours_first,
    "untouched first": untouched_first,
    "in reach first": in_reach_first,
}


def at_seconds(at: str, base: dt.date) -> float:
    d = dt.datetime.strptime(at, "%Y-%m-%d %H:%M:%S")
    return (d.date() - base).days * 86400 + d.hour * 3600 + d.minute * 60 + d.second


def picks(field: str, who: str) -> tuple[list[dict[str, Any]], dt.date]:
    """The character's fight records (the farm loop's picks), with when each was picked (pick_t:
    seconds since the first record's midnight; the record's whole-second end time less the fight's
    length, so the pick came in the second from then on)."""
    rows = [json.loads(line) for line in open(field, encoding="utf-8") if line.startswith("{")]
    rows = [r for r in rows if r.get("who") == who and r.get("kind") == "fight" and "end_s" in r]
    if not rows:
        return [], dt.date.today()
    base = dt.datetime.strptime(rows[0]["at"], "%Y-%m-%d %H:%M:%S").date()
    for r in rows:
        r["pick_t"] = at_seconds(r["at"], base) - float(r["end_s"])
    rows.sort(key=lambda r: r["pick_t"])
    return rows, base


def judge(world: World, pick: dict[str, Any], died: dict[int, list[tuple[float, bool]]], t: float,
          walker: _Walker, chase: float | None, speed: float) -> dict[str, Any]:
    """Every order's first choice at this pick, and what became of it."""
    me = {"id": world.me, "x": float(pick["me"][0]), "y": float(pick["me"][1])}
    rng = float(pick.get("reach") or NO_REACH)
    walker.rng = rng
    snap: dict[str, Any] = {"map": pick["map"], "self": me, "entities": [me] + [dict(e) for e in world.mobs.values()]}
    notes = picking.Notes(now=t, hitting=frozenset(m for m, s in world.struck_us.items() if t - s <= SEEN),
                          tagged=frozenset(m for m, s in world.ours.items() if t - s <= kills.KEEP), reach=rng)
    rules = picking.Rules(limit=chase)

    def walk(e: dict[str, Any], limit: float | None) -> float:
        return walker.walk_dist(snap, e, limit)  # pyright: ignore[reportArgumentType]

    def doomed(e: dict[str, Any], steps: float) -> bool:
        if e["id"] in notes.tagged:
            return False
        dist = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        if dist <= rng:
            return False
        steps = dist if steps == math.inf else steps
        return world.dying_in(e) < max(0.0, steps - rng) / speed + FIRST_HIT + DOOM_SLACK

    out: dict[str, Any] = {"seen": pick["id"] in world.mobs}
    for name, order in ORDERS.items():
        got = picking.choose(snap, rules, notes, walk, doomed, order=order)
        if not got.targets:
            out[name] = None
            continue
        first = got.targets[0]
        steps = got.steps[first["id"]]
        straight = math.hypot(first["x"] - me["x"], first["y"] - me["y"])
        walk_s = max(0.0, (steps if steps < math.inf else straight) - rng) / speed
        arrive = t + walk_s + FIRST_HIT
        fate = next(((dt_, mine) for dt_, mine in died.get(first["id"], []) if dt_ >= t), None)
        taken = fate is not None and not fate[1] and fate[0] <= arrive
        out[name] = {"id": first["id"], "walk_s": walk_s, "taken": taken, "same": first["id"] == pick["id"]}
    return out


def report(results: Sequence[dict[str, Any]]) -> list[str]:
    """One line per order: how often it picked what the bot picked, how long its walks were, how
    often its monster was taken first, how often it found nothing."""
    seen = sum(1 for r in results if r["seen"])
    lines = ["%d picks replayed, the bot's own pick in view at %d of them" % (len(results), seen)]
    if results and seen < len(results) / 2:
        lines.append("  (the bot's picks are mostly not in this log's view: is it another character's log?)")
    for name in ORDERS:
        got = [r[name] for r in results if r["seen"]]
        some = [g for g in got if g]
        if not got:
            continue
        lines.append("  %-16s same as the bot %3d%%, walk %.1f min in all (%.2f s a pick), taken first %d, "
                     "nothing to fight %d" % (
                         name, 100 * sum(1 for g in some if g["same"]) // len(got),
                         sum(g["walk_s"] for g in some) / 60.0, sum(g["walk_s"] for g in some) / max(1, len(some)),
                         sum(1 for g in some if g["taken"]), len(got) - len(some)))
    return lines


def replay(packets_path: str, field: str, who: str, chase: float | None = 40.0, speed: float = 4.0,
           me: int | None = None) -> list[dict[str, Any]]:
    todo, _base = picks(field, who)
    if not todo:
        return []
    me = me if me is not None else our_id(packets_path)
    first = next(packets(packets_path))[0]
    shift = -86400.0 if first > 12 * 3600 else 0.0      # packet seconds -> seconds since the field log's midnight
    todo = [r for r in todo if r["pick_t"] >= first + shift + WARM_UP]
    died = deaths(packets_path, me, shift)
    world = World(me)
    walker = _Walker()
    # The field log's clock is whole seconds: each pick is judged at MOMENTS through its second,
    # and the moment kept is the first where the bot's own order picks what the bot picked (the
    # view then matches what the bot saw), else the middle one. Every order is judged at that
    # same moment, so the choice of moment favours none of them over another.
    events = sorted((r["pick_t"] + dt_, i, k) for i, r in enumerate(todo) for k, dt_ in enumerate(MOMENTS))
    got: list[list[dict[str, Any] | None]] = [[None] * len(MOMENTS) for _ in todo]
    e = 0
    for t, way, op, m in packets(packets_path):
        t += shift
        while e < len(events) and events[e][0] <= t:
            at, i, k = events[e]
            got[i][k] = judge(world, todo[i], died, at, walker, chase, speed)
            e += 1
        world.feed(t, way, op, m)
    results: list[dict[str, Any]] = []
    for row in got:
        done = [r for r in row if r is not None]
        if not done:
            continue
        match = next((r for r in done if r["bot"] and r["bot"]["same"]), None)
        results.append(match or done[min(len(done) - 1, len(MOMENTS) // 2)])
    return results


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m unclefarmer.pickreplay", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("packets")
    ap.add_argument("field")
    ap.add_argument("who", help="the character's name as the field log writes it")
    ap.add_argument("--chase", type=float, default=40.0, help="chase range in steps (0: none, as off a patrol loop)")
    ap.add_argument("--speed", type=float, default=4.0, help="tiles a second on foot")
    ap.add_argument("--me", type=int, default=None, help="our character's id (found from the log otherwise)")
    a = ap.parse_args(argv)
    results = replay(a.packets, a.field, a.who, a.chase or None, a.speed, a.me)
    for line in report(results):
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
