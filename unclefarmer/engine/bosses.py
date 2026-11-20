"""Bosses: the named field bosses, which the bot keeps away from.


The game describes every monster as "<name>; level <n> <ordinary | elite | super> monster; ...".
Not every super monster is a field boss: most are dungeon bosses and event monsters. The field
bosses (Pig-Face Bat King, Man-wolf Caesar, Monkey King, Lich King...) are the super monsters the
game lists twice, a Demonized and a plain version, one per level band; data/bosses.json holds
them, by hand. Check its names against the game's text with

    python -m unclefarmer.engine.bosses

The game files say nothing about when a boss appears, so the bot goes by what it sees: a boss in
sight is never a target, nor is anything close to it, the patrol keeps clear of it, and the bot
walks off when one comes within boss_distance steps (0: off). A boss that keeps coming back (three
walk-offs in five minutes, or nowhere further off to go) is left behind for a while: out through a
door far from it, a wait on the other side (1 minute, then 2, 4, at most 5 when it is still there
on the way back), and back to farming.

With skip_demonized_elites, a demonized elite is kept away from the same way, closer in: no target
within ELITE_CLEAR steps of one, the patrol keeps clear of it, and a walk-off when it comes within
ELITE_KEEP steps - but never a trip off the map, and one that attacks us is fought.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Sequence
from typing import Any, TYPE_CHECKING

from .. import atlas, jsonio, paths
from ..i18n import _
from .botparts.base import snap_map

if TYPE_CHECKING:
    from .bot import Bot

DESCRIPTION = re.compile(r"^([^;]{2,60}); level (\d+) super monster;")
FILE = os.path.join(paths.DATA, "bosses.json")
_names: dict[str, int] | None = None

ELITE_KEEP = 30        # steps: a demonized elite closer than this is walked away from (skip_demonized_elites)
ELITE_CLEAR = 40       # steps: no target and no patrol cell this close to one
SEEN_FOR = 60.0        # seconds a boss or elite gone out of sight is still kept clear of by the patrol
FLEES_TO_LEAVE = 3     # walk-offs from bosses within FLEE_WINDOW before the map is left instead
FLEE_WINDOW = 300.0
AWAY_FIRST = 60.0      # seconds waited on the other side of the door the first time...
AWAY_MOST = 300.0      # ...doubled each time the boss is still there, up to this
AWAY_RESET = 900.0     # a boss left behind longer ago than this starts the wait over
LEAVE_KINDS = ("field", "transit", "town")   # maps a door may take us to for the wait


def _from_texts(path: str) -> dict[str, int]:
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out: dict[str, int] = {}

    def walk(x: object) -> None:
        if isinstance(x, str):
            m = DESCRIPTION.match(x)
            if m:
                out.setdefault(" ".join(m.group(1).split()).lower(), int(m.group(2)))
        elif jsonio.is_object(x):
            for v in x.values():
                walk(v)
        elif jsonio.is_array(x):
            for v in x:
                walk(v)
    walk(data.get("texts", data))
    return out


def names() -> dict[str, int]:
    """{boss name in lower case: level}."""
    global _names
    if _names is None:
        got: dict[str, int] = {}
        try:
            with open(FILE, encoding="utf-8") as f:
                got = json.load(f).get("bosses", {})
        except (OSError, ValueError):
            pass
        _names = got
    return _names


def is_boss(e: dict[str, Any]) -> bool:
    return e.get("kind") == "monster" and " ".join((e.get("name") or "").split()).lower() in names()


def is_demonized_elite(e: dict[str, Any]) -> bool:
    """A "demonized" monster that isn't one of the field bosses: the game's demonized elites
    (demonized Boar, black bear, Water Elf...), much stronger than the monsters around them. Some
    players leave them alone (skip_demonized_elites): one the same level as the character can kill
    it, depending on its gear."""
    name = " ".join((e.get("name") or "").split()).lower()
    return e.get("kind") == "monster" and name.startswith("demonized ") and name not in names()


def in_sight(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """The living bosses in sight."""
    return [e for e in snap.get("entities", ()) if e.get("hp", 0) > 0 and is_boss(e)]


def too_close(e: dict[str, Any], bosses: list[dict[str, Any]], keep: float) -> bool:
    """Within keep steps of any of these bosses (straight line)."""
    return any(math.hypot(e["x"] - b["x"], e["y"] - b["y"]) < keep for b in bosses)


def elites_in_sight(bot: Bot, snap: dict[str, Any]) -> list[dict[str, Any]]:
    """The living demonized elites in sight, when the profile leaves them alone (else none)."""
    if not bot.p.get("skip_demonized_elites"):
        return []
    return [e for e in snap.get("entities", ()) if e.get("hp", 0) > 0 and is_demonized_elite(e)]


def no_go(bot: Bot, snap: dict[str, Any]) -> list[tuple[float, float, float]]:
    """Circles (x, y, radius) the patrol keeps out of on this map: every boss (boss_distance) and,
    when elites are left alone, every demonized elite (ELITE_CLEAR), in sight or seen within the
    last SEEN_FOR seconds - one that walked out of sight is most likely still there."""
    now = bot.clock.time()
    mid = snap_map(snap)
    seen: dict[int, tuple[Any, float, float, float, float]] = bot.__dict__.setdefault("dangers_seen", {})
    keep = bot.p.get("boss_distance", 40)
    for b in in_sight(snap) if keep else []:
        seen[b["id"]] = (mid, b["x"], b["y"], float(keep), now)
    for e in elites_in_sight(bot, snap):
        seen[e["id"]] = (mid, e["x"], e["y"], float(ELITE_CLEAR), now)
    for k in [k for k, v in seen.items() if now - v[4] > SEEN_FOR or v[0] != mid]:
        del seen[k]
    return [(x, y, r) for _m, x, y, r, _t in seen.values()]


def clear_of(c: Sequence[float], circles: Sequence[tuple[float, float, float]]) -> bool:
    """Outside every one of these circles."""
    return all(math.hypot(c[0] - x, c[1] - y) >= r for x, y, r in circles)


def keep_away(bot: Bot, snap: dict[str, Any], map_id: int, area: Sequence[float] | None) -> bool:
    """A boss within boss_distance of us, or a demonized elite within ELITE_KEEP (skip_demonized_elites,
    and not one attacking us): walk to the reachable spot furthest from them (the farm area first).
    A boss we keep walking off from - FLEES_TO_LEAVE times within FLEE_WINDOW, or with nowhere
    further off to go - is left behind for a while instead (leave_for_a_while). True when we moved,
    so the farm loop looks again."""
    me = snap["self"]
    near = _near(bot, snap, me)
    if not near:
        return False
    def dist(t: tuple[dict[str, Any], float]) -> float:
        return math.hypot(t[0]["x"] - me["x"], t[0]["y"] - me["y"])
    _tell(bot, *min(near, key=dist), int(dist(min(near, key=dist))))
    boss = min((t for t in near if is_boss(t[0])), key=dist, default=None)
    now = bot.clock.time()
    flees: list[float] = [t for t in bot.__dict__.get("boss_flees", []) if now - t <= FLEE_WINDOW]
    bot.__dict__["boss_flees"] = flees
    if boss and len(flees) >= FLEES_TO_LEAVE and leave_for_a_while(bot, map_id, *boss):
        return True
    keep = max(k for _e, k in near)
    best = _furthest(bot, map_id, area, me, [t[0] for t in _near(bot, snap, me, everyone=True)], keep)
    if best is None:
        # nowhere further off within reach: leave a boss behind; an elite is farmed on, away from it
        return bool(boss) and leave_for_a_while(bot, map_id, *boss)
    if boss:
        flees.append(now)

    def far_enough(_me: dict[str, Any]) -> bool:
        s = bot.snap()
        return not _near(bot, s, s["self"])
    with bot.timed("search"):
        bot.walk_to(best, near=far_enough)
    return True


def _tell(bot: Bot, e: dict[str, Any], keep: float, n: int) -> None:
    """Say once for each boss or elite that it is close and kept away from."""
    told = bot.__dict__.setdefault("bosses_told", set())
    if e["id"] in told:
        return
    told.add(e["id"])
    if is_boss(e):
        bot.log(_("{boss} (a boss) is {n} steps away - keeping {keep} steps from it",
                  boss=e["name"], n=n, keep=int(keep)), speak=True)
    else:
        bot.log(_("{elite} (a demonized elite) is {n} steps away - keeping {keep} steps from it",
                  elite=e["name"], n=n, keep=int(keep)), speak=True)


def _near(bot: Bot, snap: dict[str, Any], me: dict[str, Any], everyone: bool = False) -> list[tuple[dict[str, Any], float]]:
    """(boss or elite, steps kept from it) for those closer than that - every one in sight with
    everyone. Elites only with skip_demonized_elites, and never one attacking us: that one is fought."""
    keep = bot.p.get("boss_distance", 40)
    out: list[tuple[dict[str, Any], float]] = [(b, keep) for b in in_sight(snap)] if keep else []
    elites = elites_in_sight(bot, snap)
    if elites:
        hitting = bot.attackers()
        out += [(e, ELITE_KEEP) for e in elites if e["id"] not in hitting]
    if everyone:
        return out
    return [(e, k) for e, k in out if math.hypot(e["x"] - me["x"], e["y"] - me["y"]) < k]


def _furthest(bot: Bot, map_id: int, area: Sequence[float] | None, me: dict[str, Any],
              threats: list[dict[str, Any]], keep: float) -> tuple[int, int] | None:
    """The reachable cell, of a few drawn at random, furthest from every threat - None when none is
    further from them than where we stand."""
    def spacing(c: Sequence[float]) -> float:
        return min(math.hypot(c[0] - x["x"], c[1] - x["y"]) for x in threats)
    radius = int(keep) + 20
    cells = [c for c in (bot.random_cell(map_id, area, me, radius=radius) for _i in range(12)) if c]
    if area:
        cells += [c for c in (bot.random_cell(map_id, None, me, radius=radius) for _i in range(8)) if c]
    if not cells:
        return None
    best = max(cells, key=spacing)
    return best if spacing(best) > spacing((me["x"], me["y"])) else None


def way_out(map_id: int, me: dict[str, Any], boss: dict[str, Any], keep: float) -> dict[str, Any] | None:
    """The nearest door we can walk to that is at least keep steps from the boss and leads to an
    ordinary map (a field, a road or a town: never a dungeon or a closed map)."""
    m = atlas.model(map_id)
    mx, my = int(me["x"]), int(me["y"])
    ours = m.comp_of(mx, my) if m.ok(mx, my) else None
    doors: list[dict[str, Any]] = []
    for e in m.exits:
        dest, stand = e.get("dest"), e.get("stand")
        if not dest or dest == map_id or not stand or atlas.kind(dest) not in LEAVE_KINDS:
            continue
        if ours is not None and m.comp_of(int(stand[0]), int(stand[1])) != ours:
            continue
        if math.hypot(stand[0] - boss["x"], stand[1] - boss["y"]) < keep:
            continue
        doors.append(e)
    return min(doors, key=lambda e: math.hypot(e["stand"][0] - me["x"], e["stand"][1] - me["y"]), default=None)


def leave_for_a_while(bot: Bot, map_id: int, boss: dict[str, Any], keep: float) -> bool:
    """Leave a boss behind: out through a door far from it, wait on the other side (fighting back
    what attacks us), then back to the farm. The wait is AWAY_FIRST, doubled each time the boss is
    still there on the way back, up to AWAY_MOST. Never in a dungeon (its visit can't be left) nor
    when no door is far enough from the boss. True when we left."""
    if getattr(bot, "in_instance", False):
        return False
    me = bot.snap()["self"]
    door = way_out(map_id, me, boss, keep)
    if door is None:
        return False
    now = bot.clock.time()
    last = bot.__dict__.get("boss_left_at")
    wait = AWAY_FIRST if last is None or now - last > AWAY_RESET else         min(AWAY_MOST, 2 * bot.__dict__.get("boss_wait", AWAY_FIRST))
    dest = int(door["dest"])
    bot.log(_("{boss} (a boss) keeps coming back - leaving for {map} for {n} s",
              boss=boss["name"], map=atlas.label(dest), n=int(wait)), speak=True)
    with bot.timed("search"):
        bot.travel(dest)
    if bot.map_id() == map_id:
        return False
    bot.__dict__["boss_left_at"] = now
    bot.__dict__["boss_wait"] = wait
    bot.__dict__["boss_flees"] = []
    end = bot.clock.time() + wait
    while bot.clock.time() < end:
        if bot.attackers():
            bot.fight_back()
        bot.sleep(1.0)
    bot.log(_("back to {map}", map=atlas.label(map_id)))
    with bot.timed("search"):
        bot.travel(map_id)
    bot.set_modes()
    return True


def main():
    """Say which names in data/bosses.json the game's text doesn't describe as a super monster."""
    known = _from_texts(os.path.join(paths.DATA, "game_texts.json"))
    if not known:
        raise SystemExit("no monster descriptions found in data/game_texts.json")
    missing = [n for n in names() if n not in known]
    print("%d bosses, %d not found in the game's text%s" % (len(names()), len(missing),
                                                            (": " + ", ".join(missing)) if missing else ""))


if __name__ == "__main__":
    main()
