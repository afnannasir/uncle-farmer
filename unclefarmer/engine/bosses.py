"""Bosses: the named field bosses, which the bot keeps away from.


The game describes every monster as "<name>; level <n> <ordinary | elite | super> monster; ...".
Not every super monster is a field boss: most are dungeon bosses and event monsters. The field
bosses (Pig-Face Bat King, Man-wolf Caesar, Monkey King, Lich King...) are the super monsters the
game lists twice, a Demonized and a plain version, one per level band; data/bosses.json holds
them, by hand. Check its names against the game's text with

    python -m unclefarmer.engine.bosses

The game files say nothing about when a boss appears, so the bot goes by what it sees: a boss in
sight is never a target, nor is anything close to it, the patrol keeps clear of it, and the bot
walks off when one comes within boss_distance steps (0: off). A boss that keeps coming back
(boss_walk_offs walk-offs in five minutes, 3 unless the player chose otherwise, or nowhere further
off to go) is left behind for a while, unless boss_leave is off: out through a door far from it, a
wait on the other side (boss_away_first seconds, doubled each time it is still there on the way
back, up to boss_away_most), and back to farming. With boss_away_farm the bot fights what it meets
on the way to the door and farms the map it waits on, near where it came onto it - a field or a road
whose monsters are no stronger than those of the farm: none with more HP than the farm's (by half
again), and not the map at all when the map data says its monsters are stronger. A wait used to be
a long stand by the door, doing nothing but fighting back.

With boss_notice_rooms, each boss that comes into sight is told to the chat rooms the player ticked
(team, gang, international, global), in English: its name, the map and where (announce) - or in the
player's own words (boss_notice_text, with %boss%, %map% and %pos%).

With skip_demonized_elites, a demonized elite is kept away from the same way, closer in: no target
within ELITE_CLEAR steps of one, the patrol keeps clear of it, and a walk-off when it comes within
ELITE_KEEP steps - but never a trip off the map, and one that attacks us is fought.
"""

from __future__ import annotations

import json
import math
import os
import re
from collections.abc import Callable, Sequence
from typing import Any, TYPE_CHECKING, cast

from .. import atlas, chat, jsonio, paths
from ..i18n import _
from ..link import AgentError
from .botparts.base import snap_map

if TYPE_CHECKING:
    from .bot import Bot

DESCRIPTION = re.compile(r"^([^;]{2,60}); level (\d+) super monster;")
FILE = os.path.join(paths.DATA, "bosses.json")
_names: dict[str, int] | None = None

ELITE_KEEP = 30        # steps: a demonized elite closer than this is walked away from (skip_demonized_elites)
ELITE_CLEAR = 40       # steps: no target and no patrol cell this close to one
SEEN_FOR = 60.0        # seconds a boss or elite gone out of sight is still kept clear of by the patrol
FLEES_TO_LEAVE = 3     # walk-offs from bosses within FLEE_WINDOW before the map is left (boss_walk_offs)
FLEE_WINDOW = 300.0
AWAY_FIRST = 60.0      # seconds waited on the other side of the door the first time (boss_away_first)...
AWAY_MOST = 300.0      # ...doubled each time the boss is still there, up to this (boss_away_most)
AWAY_RESET = 900.0     # back from a wait longer ago than this: the next wait starts over
LEAVE_KINDS = ("field", "transit", "town")   # maps a door may take us to for the wait
FARM_KINDS = ("field", "transit")            # ...and those of them the wait may farm
DOOR_DETOUR = 60.0     # steps further a door to a map we may farm may be than the nearest door


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


def player_no_go(bot: object) -> list[tuple[float, float, float]]:
    """The farming spot's own no-go circles (x, y, radius), drawn by the player: never walked into,
    whatever the walk is for - the patrol kept out of them, but the no-kill watchdog, walking off
    from a boss, the chest hunt and the walks to a chest or a corpse went straight through."""
    return list(getattr(bot, "spot_no_go", ()) or ())


def keep_away(bot: Bot, snap: dict[str, Any], map_id: int, area: Sequence[float] | None,
              done: Callable[[], bool] | None = None, leave: bool = True) -> bool:
    """A boss within boss_distance of us, or a demonized elite within ELITE_KEEP (skip_demonized_elites,
    and not one attacking us): walk to the reachable spot furthest from them (the farm area first).
    A boss we keep walking off from - boss_walk_offs times within FLEE_WINDOW, or with nowhere
    further off to go - is left behind for a while instead (leave_for_a_while), unless boss_leave
    is off: then it is farmed on, away from it, as an elite is. done: the farm's own end (its
    minutes, kills, until), which ends a wait away too. leave=False: a walk-off only, never a trip
    off the map nor a walk-off counted (the wait away, which is a trip already). True when we
    moved, so the farm loop looks again."""
    me = snap["self"]
    near = _near(bot, snap, me)
    if not near:
        return False
    def dist(t: tuple[dict[str, Any], float]) -> float:
        return math.hypot(t[0]["x"] - me["x"], t[0]["y"] - me["y"])
    _tell(bot, *min(near, key=dist), int(dist(min(near, key=dist))))
    boss = min((t for t in near if is_boss(t[0])), key=dist, default=None)
    leaves = leave and bool(bot.p.get("boss_leave", True))
    now = bot.clock.time()
    flees: list[float] = [t for t in bot.__dict__.get("boss_flees", []) if now - t <= FLEE_WINDOW]
    bot.__dict__["boss_flees"] = flees
    if boss and leaves and len(flees) >= walk_offs(bot.p) and leave_for_a_while(bot, map_id, *boss, done=done):
        return True
    keep = max(k for _e, k in near)
    best = _furthest(bot, map_id, area, me, [t[0] for t in _near(bot, snap, me, everyone=True)], keep)
    if best is None:
        # nowhere further off within reach: leave a boss behind; an elite is farmed on, away from it
        return bool(boss) and leaves and leave_for_a_while(bot, map_id, *boss, done=done)
    if boss and leave:
        flees.append(now)

    def far_enough(_me: dict[str, Any]) -> bool:
        s = bot.snap()
        return not _near(bot, s, s["self"])
    with bot.timed("search"):
        bot.walk_to(best, near=far_enough)
    return True


def _tell(bot: Bot, e: dict[str, Any], keep: float, n: int) -> None:
    """Say once for each boss that it is close and kept away from; an elite only goes in the log
    (they are common, and speech reaches every window)."""
    told = bot.__dict__.setdefault("bosses_told", set())
    if e["id"] in told:
        return
    told.add(e["id"])
    if is_boss(e):
        bot.log(_("{boss} (a boss) is {n} steps away - keeping {keep} steps from it",
                  boss=e["name"], n=n, keep=int(keep)), speak=True)
    else:
        bot.log(_("{elite} (a demonized elite) is {n} steps away - keeping {keep} steps from it",
                  elite=e["name"], n=n, keep=int(keep)))


# Telling other players of a boss seen (boss_notice_rooms): the chat channels it may go to, in the
# order the character window shows them - the team, the gang, the international room, the whole
# server. Never the map's channel: whoever is on the map sees the boss for themselves.
NOTICE_ROOMS = (4, 3, 5, 0)
NOTICE_EVERY = 600.0   # seconds: one notice a map at most this often (a boss that comes and goes)


def notice_rooms(p: dict[str, Any]) -> list[int]:
    """The chat channels the player ticked for boss notices, in NOTICE_ROOMS' order."""
    chosen: object = p.get("boss_notice_rooms") or []
    items: list[object] = list(cast("list[object]", chosen)) if isinstance(chosen, (list, tuple)) else []
    picked = set(items)
    return [c for c in NOTICE_ROOMS if c in picked]


_FIELD = re.compile(r"%(boss|map|pos)%", re.I)     # what a player's own notice may name


def notice_text(boss: dict[str, Any], map_id: int, own: str = "") -> str:
    """The notice. Without the player's own line (boss_notice_text), in English whatever the bot's
    language: the rooms hold players of every language, and the game's boss and map names are
    English anyway. The player's own line is theirs, with %boss%, %map% and %pos% (any case) put in
    for the boss's name, the map's name and where it stands ("105, 100"), on one line."""
    where = str(atlas.maps().get(int(map_id), {}).get("name") or map_id).strip()
    pos = "%d, %d" % (int(boss["x"]), int(boss["y"]))
    line = " ".join(str(own or "").split())
    if not line:
        return "%s (a boss) on %s at %s" % (boss["name"], where, pos)
    fields = {"boss": str(boss["name"]), "map": where, "pos": pos}
    return _FIELD.sub(lambda m: fields[m.group(1).lower()], line)


def gangless(bot: Bot, snap: dict[str, Any]) -> bool:
    """The character is in no gang: the game keeps the gang's id in our own side, and its gang
    attack mode wants it positive (as crafting.is_gangless reads it)."""
    side = snap["self"].get("side")
    return isinstance(side, int) and side <= 0


def refused_why(answer: str) -> str:
    """Why the agent didn't send a line, for the player (as chat.ChatSender.say says it)."""
    if "international" in answer:
        return _("Join an international room in the game first.")
    return _("The game didn't take it: {why}", why=answer)


def announce(bot: Bot, snap: dict[str, Any], map_id: int) -> None:
    """Tell the ticked chat rooms of each boss that comes into sight: once a boss on a map, and once
    a map every NOTICE_EVERY at most (a second boss within that time isn't told). The game takes
    any line for the team or the gang - one with no team goes nowhere, unsaid -; a character in
    no gang isn't sent one, and a refused international line is said in the log. The farm goes on."""
    rooms = notice_rooms(bot.p)
    if not rooms:
        return
    told: set[tuple[int, int]] = bot.__dict__.setdefault("boss_noticed", set())
    last: dict[int, float] = bot.__dict__.setdefault("boss_noticed_at", {})
    for boss in in_sight(snap):
        key = (int(map_id), boss["id"])        # (the game's monster ids are only the map's)
        if key in told:
            continue
        told.add(key)
        now = bot.clock.time()
        if now - last.get(int(map_id), -NOTICE_EVERY) < NOTICE_EVERY:
            continue
        last[int(map_id)] = now
        _tell_rooms(bot, snap, boss, notice_text(boss, map_id, str(bot.p.get("boss_notice_text") or "")), rooms)


def _tell_rooms(bot: Bot, snap: dict[str, Any], boss: dict[str, Any], text: str, rooms: list[int]) -> None:
    sent: list[str] = []
    for room in rooms:
        name = _(chat.CHANNELS[room])
        if room == 3 and gangless(bot, snap):
            bot.log(_("not telling {room} of {boss}: the character is in no gang", room=name, boss=boss["name"]))
            continue
        try:
            answer = bot.a.send("say %d %s" % (room, text))
        except (AgentError, OSError) as e:
            answer = str(e)
        if answer == "sent":
            sent.append(name)
        else:
            bot.log(_("couldn't tell {room} of {boss}: {why}", room=name, boss=boss["name"], why=refused_why(answer)))
    if sent:
        bot.log(_("told {rooms}: {line}", rooms=", ".join(sent), line=text))


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


def threatened(bot: Bot, snap: dict[str, Any]) -> bool:
    """Is a boss (or a demonized elite kept away from) closer to us than the steps kept from it?"""
    return bool(_near(bot, snap, snap["self"]))


def _furthest(bot: Bot, map_id: int, area: Sequence[float] | None, me: dict[str, Any],
              threats: list[dict[str, Any]], keep: float) -> tuple[int, int] | None:
    """The reachable cell, of a few drawn at random, furthest from every threat - None when none is
    further from them than where we stand."""
    def spacing(c: Sequence[float]) -> float:
        return min(math.hypot(c[0] - x["x"], c[1] - x["y"]) for x in threats)
    radius = int(keep) + 20
    mine = player_no_go(bot)
    cells = [c for c in (bot.random_cell(map_id, area, me, radius=radius, clear_of=mine) for _i in range(12)) if c]
    if area:
        cells += [c for c in (bot.random_cell(map_id, None, me, radius=radius, clear_of=mine) for _i in range(8)) if c]
    if not cells:
        return None
    best = max(cells, key=spacing)
    return best if spacing(best) > spacing((me["x"], me["y"])) else None


def walk_offs(p: dict[str, Any]) -> int:
    """How many walk-offs from a boss within FLEE_WINDOW before its map is left (0: at once)."""
    n = p.get("boss_walk_offs", FLEES_TO_LEAVE)
    return max(0, int(n)) if isinstance(n, (int, float)) and not isinstance(n, bool) else FLEES_TO_LEAVE


def away_times(p: dict[str, Any]) -> tuple[float, float]:
    """(seconds away the first time, the most it doubles up to): the player's, the most never under
    the first."""
    def seconds(key: str, default: float) -> float:
        v = p.get(key, default)
        return max(1.0, float(v)) if isinstance(v, (int, float)) and not isinstance(v, bool) else default
    first = seconds("boss_away_first", AWAY_FIRST)
    return first, max(first, seconds("boss_away_most", AWAY_MOST))


def _top_level(mid: int) -> int:
    """The strongest monster level of the map's spawns, 0 when the map data has none."""
    spawn: dict[str, Any] = atlas.maps().get(int(mid), {}).get("spawn") or {}
    found: list[object] = list(spawn.get("levels") or [])
    levels = [v for v in found if isinstance(v, int)]
    return max(levels, default=0)


def may_farm(bot: Bot, dest: int, home: int) -> bool:
    """May a wait away from a boss farm map dest? A field or a road (never a town or a dungeon) -
    unless the map data gives its monsters' levels and they are stronger than both the farm's own
    (home) and the character's level + atlas.LEVEL_ABOVE (the game's full-loot band): the map next
    door can be a much harder one, and the wait is no place to die. The data has levels for few
    maps, so the wait itself also leaves alone what has more HP than the monsters farmed at home
    (FarmingMixin.farm_away, AWAY_HP)."""
    if atlas.kind(dest) not in FARM_KINDS:
        return False
    top = _top_level(dest)
    if not top:
        return True
    from .repair import level
    lv = level(bot)
    return top <= max(_top_level(home), lv + atlas.LEVEL_ABOVE if lv > 0 else 0)


def way_out(map_id: int, me: dict[str, Any], boss: dict[str, Any], keep: float,
            prefer: Callable[[int], bool] | None = None) -> dict[str, Any] | None:
    """The nearest door we can walk to that is at least keep steps from the boss and leads to an
    ordinary map (a field, a road or a town: never a dungeon or a closed map). prefer(map): a door to
    such a map is taken instead when it is at most DOOR_DETOUR steps further (a map the wait can
    farm)."""
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

    def steps(e: dict[str, Any]) -> float:
        return math.hypot(e["stand"][0] - me["x"], e["stand"][1] - me["y"])
    nearest = min(doors, key=steps, default=None)
    if nearest is None or prefer is None or prefer(int(nearest["dest"])):
        return nearest
    liked = [e for e in doors if steps(e) <= steps(nearest) + DOOR_DETOUR and prefer(int(e["dest"]))]
    return min(liked, key=steps, default=nearest)


def _wait_for(bot: Bot, now: float) -> float:
    """This wait away: the first one, or the last one doubled while we keep coming back to the boss
    (back less than AWAY_RESET ago), up to the most (away_times)."""
    first, most = away_times(bot.p)
    back = bot.__dict__.get("boss_back_at")
    if back is None or now - back > AWAY_RESET:
        return first
    return min(most, 2 * bot.__dict__.get("boss_wait", first))


def leave_for_a_while(bot: Bot, map_id: int, boss: dict[str, Any], keep: float,
                      done: Callable[[], bool] | None = None) -> bool:
    """Leave a boss behind: out through a door far from it, wait on the other side, then back to
    the farm. The wait is boss_away_first seconds, doubled each time the boss is still there on the
    way back, up to boss_away_most. With boss_away_farm the walk to the door fights what it meets
    (none near a boss) and the wait farms the map, when may_farm; else the wait fights back only
    what attacks us. done (the farm's end) ends the wait early. Never in a dungeon (its visit can't
    be left) nor when no door is far enough from the boss. True when we left."""
    if getattr(bot, "in_instance", False):
        return False
    farm = bool(bot.p.get("boss_away_farm", True))
    me = bot.snap()["self"]
    door = way_out(map_id, me, boss, keep, prefer=(lambda d: may_farm(bot, d, map_id)) if farm else None)
    if door is None:
        return False
    now = bot.clock.time()
    wait = _wait_for(bot, now)
    dest = int(door["dest"])
    bot.log(_("{boss} (a boss) keeps coming back - leaving for {map} for {n} s",
              boss=boss["name"], map=atlas.label(dest), n=int(wait)), speak=True)
    cell = (door["stand"][0], door["stand"][1])
    with bot.timed("search"):
        # Through the door chosen above: travel alone takes the shortest way to that map, which on
        # a map with two doors to it may be the one by the boss. The planned way only if the walk
        # to the door didn't take us there.
        walked = bot.leave_fighting(cell) if farm else bot.walk_to(cell, map_change=True)
        if walked != "map" or bot.map_id() != dest:
            bot.travel(dest)
    if bot.map_id() == map_id:
        return False
    bot.__dict__["boss_wait"] = wait
    bot.__dict__["boss_flees"] = []
    try:
        # (on the map the door leads to: a travel that ended elsewhere waits there, unfarmed)
        if farm and bot.map_id() == dest and may_farm(bot, dest, map_id) and getattr(bot, "farm_top_hp", 0):
            bot.farm_away(map_id, wait, done)
        else:
            if farm and not may_farm(bot, dest, map_id):
                bot.log(_("not farming {map} meanwhile: it is a town, or the map data says its monsters are "
                          "too strong", map=atlas.label(dest)))
            elif farm and bot.map_id() == dest:
                bot.log(_("not farming {map} meanwhile: nothing fought on the farm yet to judge its "
                          "monsters by", map=atlas.label(dest)))
            _stand_by(bot, wait, done)
    finally:
        bot.__dict__["boss_back_at"] = bot.clock.time()
    if done is not None and done():
        return True                   # the farm is over: its own end takes it from here
    if bot.map_id() != map_id:        # (a trip to town during the wait came back already)
        bot.log(_("back to {map}", map=atlas.label(map_id)))
        with bot.timed("search"):
            bot.travel(map_id)
    bot.set_modes()
    return True


def _stand_by(bot: Bot, wait: float, done: Callable[[], bool] | None) -> None:
    """The wait without farming: fight back what attacks us, until the wait or the farm is over."""
    end = bot.clock.time() + wait
    while bot.clock.time() < end and not (done is not None and done()):
        if bot.attackers():
            bot.fight_back()
        bot.sleep(1.0)


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
