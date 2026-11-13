"""Go anywhere: the shortest way from where we stand to a map (and cell) - on foot, by NPC transport,
or with a travel item from the bag.

The graph:
  nodes   every port of every map (atlas.json: exits and arrival points), every transport's stand
          cell next to its NPC, every landing point (rides, flights, squares)
  edges   walking between ports of one map (atlas routes, in steps)
          stepping through an exit -> the arrival port with the same marker on the next map (when
            the map data doesn't say where you land, every port of the next map, at a guessed cost)
          walking to a transport NPC, talking, choosing, riding -> where it lands (transports.json)
          from where we stand: a travel item (hearthstone, teleport stones) -> a city square
          from where we stand (or the nearest cell outside a safe zone): a teleport skill (a
            Mage's Teleportation, an Elf's Elven Teleportation) -> one of the places it lists
Costs are in walking steps; talking, riding and teleporting add fixed overheads so they are used
only when they really save walking (or when there's no other way). The plan is rebuilt after every
leg, so a guess that turns out wrong costs nothing more than a re-plan.

Rides (cable cars, airships, teleport circles) are edges like any other, so a route mixes walking
and riding wherever that is shortest. A ride is in the graph only when it can be taken: the
profile allows transports, a team circle only with a team, and a fare the character can pay (the
arnebia on hand, when the caller says how much; VIP rides are free). A transport row that is
missing what a ride needs (its map, where it lands) is left out, never a crash.

The search is Dijkstra over that graph - A* with an estimate of zero, which never overestimates
and is consistent, so the route it returns is the cheapest one under these costs, rides included.
(A map-level lower bound - the cheapest exits and rides alone - was tried: it returned the same
routes but skipped only about 5% of the nodes, because nearly all of a route's cost is walking
inside maps, and working it out made every plan slower.)

plan() returns a list of legs; the bot carries out only the first one and asks again:
  {"do": "exit", "map", "exit", "stand", "rect", "dest"}                walk off through a map exit
  {"do": "npc",  "map", "npc", "at", "stand", "choose": [...], "dest", "ride", "id", "kind"}
  {"do": "item", "item", "choose": [...], "dest", "lands"}             use a travel item
  {"do": "skill", "skill", "place", "choose": [...], "dest", "lands", "cast_at"?}   cast a teleport skill
  {"do": "leave", "map"}                                                leave an instance (Action menu)
  {"do": "exit", ..., "dest": None}                                     walk out of a gang base (gang_exit)
  {"do": "walk", "map", "cell"}                                         the last stretch to a cell
"""

from __future__ import annotations

import collections
import copy
import heapq
import math
import os
import re
from collections.abc import Callable, Collection, Hashable, Mapping, Sequence
from typing import Any, Literal, TypeAlias, overload

import numpy as np


from . import atlas, jsonio as json, lru, paths
from .atlaslib import Cell, MapModel, Point

Leg = dict[str, Any]                 # one leg of a plan, shaped as the module notes say
Row = dict[str, Any]                 # a row of the map data or transports.json
# The travel graph's nodes, told apart by their first item:
PortNode = tuple[Literal["p"], int, int]                 # (map, port index)
StandNode = tuple[Literal["t"], int]                     # beside a transport's NPC (transport id)
LandNode = tuple[Literal["l"], int]                      # where a transport lands
ItemNode = tuple[Literal["i"], str, int, int, int]       # a travel item used: (item, map, x, y)
StartNode = tuple[Literal["s"]]                          # where we stand
Node: TypeAlias = "PortNode | StandNode | LandNode | ItemNode | StartNode | tuple[Literal['z'], Node]"  # z: at the goal
Edge = tuple[float, Node, Leg | None]                    # (cost, next node, the leg it takes or None)
Graph = dict[Node, list[Edge]]
ItemOption = tuple[str, list[str], tuple[int, int, int], int]   # (item, choose list, (map, x, y), cost)
Plan = list[Leg] | None
CostedPlan = tuple[Plan, float | None]

CROSSING = 6            # stepping through an exit and the map loading, in steps
TALK = {"cable_car": 60, "airship": 120, "circle": 40, "menu": 60}   # talking + riding/flying
UNSEEN_NPC = 40         # extra walking assumed when we don't know where the NPC stands yet
TELEPORT = 250          # using a travel item: casting, loading, and the item it costs
HEARTH = 200            # the hearthstone is free to reuse, but has a long cast
SPELL = 120             # a teleport skill: a 10 s channel, the map loading and the mana it costs

# Where each square is: teleport stones, the hearthstone and city respawns land here (marker 1 of
# the city's map; checked against the atlas reports).
SQUARES: dict[str, tuple[int, int, int]] = {"giant rock square": (1041, 200, 205), "white sand square": (1061, 66, 136),
           "sunset square": (1021, 73, 286), "tulip square": (1081, 338, 165), "moon square": (1101, 200, 202),
           "violet square": (1161, 248, 107), "holy blood square": (1121, 62, 165),
           "gouge knife square": (1141, 84, 87), "dark night square": (1181, 225, 230)}

# Travel items -> (menu entry to choose or None, landing). Single-city stones need no menu.
ITEMS: dict[str, list[tuple[str | None, str]]] = {
    "Sunset City Teleportation stone": [(None, "sunset square")],
    "Giant rock City Teleportation stone": [(None, "giant rock square")],
    "White Sands City Teleportation stone": [(None, "white sand square")],
    "Tulip City Teleportation stone": [(None, "tulip square")],
    "Moon City Teleportation stone": [(None, "moon square")],
    "holy blood city Teleportation stone": [(None, "holy blood square")],
    "gouging knife City Teleportation stone": [(None, "gouge knife square")],
    "Violet City Teleportation stone": [(None, "violet square")],
    "Night City Teleportation stone": [(None, "dark night square")],
    "Platinum Teleportation stone": [(re.escape(s), s) for s in (
        "sunset square", "giant rock square", "white sand square", "tulip square", "moon square",
        "holy blood square", "gouge knife square", "violet square")],
    "Garth Empire Teleportation stone": [("holy blood square", "holy blood square"),
                                         ("gouge knife square", "gouge knife square")],
}
# Black Gold stone: villages and ruins, landing not measured yet (re-planned on arrival)
BLACK_GOLD: list[tuple[str, tuple[int, int, int]]] = [("soul burial village", (1531, 97, 184)), ("plague abyss", (1733, 60, 60)),
              ("orchid woods village", (1631, 150, 155)), ("mist gods ruins", (1561, 200, 200))]

# Teleport skills -> the places their menu lists -> where each lands: the place's marker cell in the
# map table (the code the game sends is map * 1000 + marker). Recorded 2026-11-13: a level-5
# Teleportation (Mage) listed the seven squares below, and a top-level (9) Elven Teleportation (Elf)
# the nine places below; landings measured on Giant rock square and mist gods Ruins. A character's
# own list depends on the skill's level: the bot learns it from the menu (botparts.travel) and
# plans with this only before it has.
SPELLS: dict[str, dict[str, tuple[int, int, int]]] = {
    "Teleportation": {name: SQUARES[name] for name in (
        "sunset square", "giant rock square", "white sand square", "tulip square", "moon square",
        "holy blood square", "gouge knife square")},
    "Elven Teleportation": {
        "soul burial village": (1531, 85, 198), "plague abyss": (1731, 50, 223),
        "orchid woods village": (1631, 151, 119), "city of the ancient tower": (1731, 166, 75),
        "mist gods ruins": (1561, 200, 197), "west sealing tower": (1761, 100, 100),
        "hermit camp": (1661, 76, 87), "east sealing tower": (1761, 400, 100),
        "portal of the demon world": (1762, 45, 129)},
}
Spell = tuple[str, str]       # (teleport skill, place in its menu)


def spell_landing(skill: str, place: str) -> tuple[int, int, int] | None:
    """Where a teleport skill's place lands (map, x, y): the skill's own table, else a city square
    (a higher-level Teleportation may list more of them), else None (not known: not planned)."""
    name = place.strip().lower()
    return SPELLS.get(skill, {}).get(name) or SQUARES.get(name)


def spell_choice(place: str) -> str:
    """The menu pattern for a place: the whole name ("east sealing tower" never picks the west one)."""
    return r"\b%s\b" % re.escape(place.strip())


_transports: list[Row] | None = None


def transports() -> list[Row]:
    global _transports
    if _transports is None:
        path = os.path.join(paths.DATA, "transports.json")
        found: list[Row] = []
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    raw: object = json.load(f)
            except (OSError, ValueError):
                raw = {}
            rows: object = raw.get("transports") if json.is_object(raw) else None
            found = [r for r in rows if json.is_object(r)] if json.is_array(rows) else []
            for i, t in enumerate(found):
                t["id"] = i
        _transports = found
    return _transports


def _ports(mid: int) -> list[Row]:
    return atlas.maps().get(int(mid), {}).get("ports", [])


def _arrival_port(mid: int, marker: object) -> int | None:
    for i, p in enumerate(_ports(mid)):
        if p["kind"] == "arrival" and p.get("marker") == marker:
            return i
    return None


def is_instance(mid: int) -> bool:
    """A map you can't walk out of: a dungeon, secret room, event or gang map."""
    m = atlas.maps().get(int(mid), {})
    exits: list[Row] = m.get("exits", [])
    return not [e for e in exits if e.get("dest") is not None]


LEAVE_COST = 500.0      # leaving an instance: where it puts us isn't known, so a plan can't go on


def gang_exit(mid: int) -> tuple[int, Row] | None:
    """A gang base's way out: (exit index, exit). The map data marks one exit on the south edge of
    the OUTER hall with no destination - the game sends you back to where you came in from - and
    the base has no "leave" in its Action menu (only dungeons and the like do). None on any other
    map."""
    m = atlas.maps().get(int(mid), {})
    exits: list[Row] = m.get("exits", [])
    if any(e.get("dest") is not None for e in exits):
        return None
    for i, e in enumerate(exits):
        if e.get("dest") is None and e.get("stand"):
            return i, e
    return None


def way_out(here_map: int, here_cell: Sequence[float], avoid: Collection[Hashable] = ()) -> CostedPlan:
    """Out of a map you can't plan your way out of: a gang base's own exit when we can walk to it
    (its inner hall is walled off from it), else - dungeons, secret rooms, events - the Action
    menu's "leave". ([leg], cost) or (None, None)."""
    found = gang_exit(here_map)
    if found is not None:
        i, e = found
        if "gang_exit" in avoid:
            return None, None
        stand = (e["stand"][0], e["stand"][1])
        steps = _steps_from(here_map, here_cell, [stand]).get(stand)
        if steps is None:
            return None, None          # in the inner hall: no way to the exit on foot
        leg: Leg = {"do": "exit", "map": int(here_map), "exit": i, "stand": list(e["stand"]),
                    "rect": list(e["rect"]), "dest": None}
        return [leg], steps + CROSSING + LEAVE_COST
    if is_instance(here_map) and "leave" not in avoid:
        return [{"do": "leave", "map": int(here_map)}], LEAVE_COST
    return None, None


_static: Graph | None = None


def _static_graph() -> Graph:
    """Edges that don't depend on where we start: node -> [(cost, node, leg)]."""
    global _static
    if _static is not None:
        return _static
    g: collections.defaultdict[Node, list[Edge]] = collections.defaultdict(list)
    for mid, m in atlas.maps().items():
        ports: list[Row] = m.get("ports", [])
        routes: list[Row] = m.get("routes", [])
        for r in routes:
            a, b, steps = r["a"], r["b"], r["steps"]
            g[("p", mid, a)].append((steps, ("p", mid, b), None))
            # one-way gates can make the way back longer (atlas 2026-10-05 writes it separately)
            g[("p", mid, b)].append((r.get("back_steps", steps), ("p", mid, a), None))
        for i, p in enumerate(ports):
            if p["kind"] != "exit":
                continue
            e = m["exits"][p["exit"]]
            if e.get("dest") is None or not e.get("stand"):
                continue
            leg: Leg = {"do": "exit", "map": mid, "exit": p["exit"], "stand": e["stand"], "rect": e["rect"],
                   "dest": e["dest"]}
            j = _arrival_port(e["dest"], e.get("marker"))
            if j is not None:
                g[("p", mid, i)].append((CROSSING, ("p", e["dest"], j), leg))
                continue
            # The map data names no landing point for this exit (the country halls, Ancient Ruins,
            # Pull Cave...). Land "somewhere" on the next map: every port, at a guessed walk.
            d = atlas.maps().get(e["dest"])
            if not d:
                continue
            guess = CROSSING + (d["w"] + d["h"]) // 4
            landings: list[Row] = d.get("ports", [])
            for k, _q in enumerate(landings):
                g[("p", mid, i)].append((guess, ("p", e["dest"], k), leg))
    _static = g
    return g


_open: set[int] | None = None
HUB = 1041            # central plains: the open world is everything joined to it both ways


def open_maps() -> set[int]:
    """The open world: maps you can get to from central plains on foot or by the public transports
    (cable cars, airships; not team dungeons) AND get back from - a set of map ids. What is left
    out is closed: dungeons and the maps inside them, closed maps, secret rooms, wedding, gang,
    arena, event and test maps, the class starting zones, cut scenes and the halls a transport
    passes through."""
    global _open
    if _open is not None:
        return _open
    g: collections.defaultdict[Node, list[Node]] = collections.defaultdict(list)
    for k, v in _static_graph().items():
        g[k] += [b for _c, b, _l in v]
    for k, v in _transport_edges((), False).items():
        g[k] += [b for _c, b, _l in v]
    back: collections.defaultdict[Node, list[Node]] = collections.defaultdict(list)
    for a, bs in g.items():
        for b in bs:
            back[b].append(a)
    hub_ports: list[Row] = atlas.maps().get(HUB, {}).get("ports", [])
    start: list[Node] = [("p", HUB, i) for i in range(len(hub_ports))]

    def reach(edges: Mapping[Node, list[Node]]) -> set[int]:
        seen, todo = set(start), list(start)
        while todo:
            for b in edges.get(todo.pop(), []):
                if b not in seen:
                    seen.add(b)
                    todo.append(b)
        return {n[1] for n in seen if n[0] == "p"}
    _open = reach(g) & reach(back)
    return _open


_landing_cache: dict[tuple[int, Point], dict[int, float]] = {}


def _landing_ports(mid: int, cell: Point) -> dict[int, float]:
    """Walking steps from a landing cell to every port of its map (cached)."""
    key = (mid, (cell[0], cell[1]))
    if key not in _landing_cache:
        pts: list[Point] = [(p["x"], p["y"]) for p in _ports(mid)]
        reach = _steps_from(mid, cell, pts)
        _landing_cache[key] = {i: reach[pt] for i, pt in enumerate(pts) if pt in reach}
    return _landing_cache[key]


def fare(t: Row) -> int | None:
    """What the ride costs in arnebia: 0 for a free one, None when the data doesn't know."""
    cost = t.get("cost")
    if isinstance(cost, bool) or not isinstance(cost, (int, float)) or cost < 0:
        return None
    return int(cost)


def can_pay(t: Row, vip: bool, arnebia: int | None) -> bool:
    """The character can pay for the ride: VIP rides are free, a fare not known is not counted
    against it (the NPC's own menu says no if it is too dear, and the ride is left out after
    that), and with the arnebia on hand unknown (None) every ride counts as payable."""
    if vip and t.get("choose_vip"):
        return True
    price = fare(t)
    return arnebia is None or price is None or price <= arnebia


def usable(t: Row) -> bool:
    """A transport row with what a ride needs: its map and where it lands, both on maps the data
    has, and a menu entry to choose. Anything else is left out of the plan."""
    land: object = t.get("lands")
    maps = atlas.maps()
    choose: object = t.get("choose")
    return (isinstance(t.get("map"), int) and t["map"] in maps and isinstance(t.get("dest"), int)
            and json.is_object(land) and isinstance(land.get("map"), int) and land["map"] in maps
            and json.is_object(land.get("ports")) and bool(t.get("npc")) and bool(choose)
            and t.get("kind") in TALK)


def _transport_edges(avoid: Collection[Hashable], vip: bool, team: bool = False,
                     arnebia: int | None = None) -> collections.defaultdict[Node, list[Edge]]:
    """Port -> NPC stand -> landing, for every transport that can be taken: not avoided, a team
    circle only with a team, a fare the character can pay (can_pay) and a complete row (usable)."""
    g: collections.defaultdict[Node, list[Edge]] = collections.defaultdict(list)
    for t in transports():
        if t["id"] in avoid or (t.get("team") and not team) or not usable(t) or not can_pay(t, vip, arnebia):
            continue
        mid = t["map"]
        stand: Node = ("t", t["id"])
        near: dict[str, float] = t.get("ports") or {}
        if near:
            for i, steps in near.items():
                g[("p", mid, int(i))].append((steps, stand, None))
        else:
            # Not seen yet: the bot looks for it by name on arrival. Assume it is a short walk
            # from any port of its map.
            for i, _p in enumerate(_ports(mid)):
                g[("p", mid, i)].append((UNSEEN_NPC, stand, None))
        land: Row = t["lands"]
        free = bool(vip and t.get("choose_vip"))      # the menu's free entry, for VIPs
        choose: list[str] = t["choose_vip"] if free else t["choose"]
        leg: Leg = {"do": "npc", "map": mid, "npc": t["npc"], "at": t.get("at"), "stand": t.get("stand"),
               "choose": choose, "dest": t["dest"], "ride": t.get("ride"), "id": t["id"], "kind": t["kind"],
               "cost": t.get("cost"), "vip": free}
        node: Node = ("l", t["id"])
        g[stand].append((TALK.get(t["kind"], 60), node, leg))
        landed: dict[str, float] = land["ports"]
        for i, steps_ in landed.items():
            g[node].append((steps_, ("p", land["map"], int(i)), None))
    return g


def ride_rules(p: Mapping[str, Any]) -> tuple[bool, set[int]]:
    """(transport, avoid) for plan(), from the profile's two switches: "Use airships" decides the
    airships, "Use cable cars and teleport circles" every other ride. Every planner call that
    takes the profile's rides takes them from here, so one switch never turns off the other's
    rides. (The walk-first plan under "Walk whenever the maps connect on foot" takes none.)"""
    airships = bool(p.get("use_airships", True))
    others = bool(p.get("use_transport", True))
    off = {t["id"] for t in transports()
           if not (airships if t.get("kind") == "airship" else others)}
    return airships or others, off


def kept_off(p: Mapping[str, Any]) -> set[int]:
    """The transports a profile leaves out: the airships when "Use airships" is off, every other
    ride when "Use cable cars and teleport circles" is (ride_rules)."""
    return ride_rules(p)[1]


def arnebia_in(bag: Sequence[Row] | None) -> int | None:
    """The arnebia in a bag reading, or None when the reading has no Arnebia row at all (an
    empty or failed reading must not look like an empty purse and leave every paid ride out)."""
    rows = [r for r in bag or [] if str(r.get("name") or "").strip().lower() == "arnebia"]
    return sum(count_of(r) for r in rows) if rows else None


def count_of(row: Row) -> int:
    """How many a bag row holds: a whole number from the game, 1 when it says nothing (an item
    with no count is a single one). A count that isn't a number - null, text, a fraction, not
    finite - counts as none: a broken row must not make the planner count on an item it may not
    have (it used to stop the whole plan with a TypeError)."""
    if "count" not in row:
        return 1
    v = row.get("count")
    if isinstance(v, bool):
        return 0
    if isinstance(v, int):
        return v
    if isinstance(v, float) and math.isfinite(v) and v.is_integer():
        return int(v)
    if isinstance(v, str) and v.strip().isdigit():
        return int(v.strip())
    return 0


def item_options(bag: Sequence[Row] | None, hearth_home: str | None = None, hearth_key: bool = False) -> list[ItemOption]:
    """Travel items we could use right now: [(item, choose list, (map, x, y), cost)]."""
    have: dict[str, int] = {str(r.get("name") or "").strip().lower(): count_of(r) for r in (bag or [])}
    out: list[ItemOption] = []
    for item, choices in ITEMS.items():
        if have.get(item.lower(), 0) <= 0:
            continue
        for pick, square in choices:
            out.append((item, [pick] if pick else [], SQUARES[square], TELEPORT))
    if have.get("black gold teleportation stone", 0) > 0:
        for pick, land in BLACK_GOLD:
            out.append(("Black Gold Teleportation stone", [pick], land, TELEPORT + 50))
    if hearth_home and (hearth_key or have.get("hearthstone", 0) > 0):
        sq = SQUARES.get(str(hearth_home).lower())
        if sq:
            out.append(("Hearthstone", [], sq, HEARTH))
    return out


_steps_cache: dict[tuple[int, Cell, tuple[Cell, ...]], dict[Point, float]] = {}
STEPS_CACHE_MAX = 256      # (map, cell, targets) -> walking distances; static map data, never stale


def _steps_from(mid: int, cell: Sequence[float], targets: Sequence[Point]) -> dict[Point, float]:
    """Walking steps from cell to each target cell on map mid (missing = unreachable).

    Cached: the map does not change, so the same question always has the same answer. The bot
    rebuilds its travel plan after every leg, which asks for the goal map's port distances again
    and again."""
    m = atlas.model(mid)
    here = (int(cell[0]), int(cell[1]))
    key = (int(mid), here, tuple(sorted((int(t[0]), int(t[1])) for t in targets)))
    out = lru.get(_steps_cache, key)
    if out is None:
        out = _steps_uncached(m, here, targets)
        lru.put(_steps_cache, key, out, STEPS_CACHE_MAX)
    return dict(out)


def _steps_uncached(m: MapModel, cell: Cell, targets: Sequence[Point]) -> dict[Point, float]:
    """Walking steps from cell to each target, counted the way the stored routes between ports
    (atlas routes) are: the length of the map's own route there (MapModel.route - round walls and
    one-way gates, ending on the first cell of an exit). The walks from where we stand and to the
    goal cell used to be the wall-avoiding search's cost instead, which adds a little for every step
    beside a wall: one plan added up two kinds of number, up to 8% apart on central plains, and a
    port near a wall looked further than it was."""
    if not m.ok(*cell):
        near = m.nearest_walkable(cell[0], cell[1], 6)
        if not near:
            return {}
        cell = near
    out: dict[Point, float] = {}
    for t in targets:
        goal = (int(t[0]), int(t[1]))
        if not m.ok(*goal):
            continue
        path = m.route(cell, goal)
        if path is not None:
            out[(t[0], t[1])] = float(len(path) - 1)
    return out


# boxed, so a miss (None) is told apart from a cached "no way there"
_plan_cache: dict[tuple[Hashable, ...], tuple[Plan | CostedPlan]] = {}
PLAN_CACHE_MAX = 128


@overload
def plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
         transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
         hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False, arnebia: int | None = None,
         with_cost: Literal[False] = False, spells: Sequence[Spell] = ()) -> Plan: ...
@overload
def plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
         transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
         hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False, arnebia: int | None = None, *,
         with_cost: Literal[True], spells: Sequence[Spell] = ()) -> CostedPlan: ...
def plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
         transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
         hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False,
         arnebia: int | None = None, with_cost: bool = False, spells: Sequence[Spell] = ()) -> Plan | CostedPlan:
    """The legs from (here_map, here_cell) to goal_map (and goal_cell), or None if unreachable.

    bag: the bag rows ({"name", "count"}) so travel items can be used; hearth_home: the square the
    hearthstone takes us to (learned); spells: the (teleport skill, place) pairs the character can
    cast to; avoid: transport ids, item names and spells that failed; arnebia:
    the arnebia on hand, so no ride is planned that the character can't pay for (None: not known,
    every ride counts as payable).

    Cached by every input that affects the answer - the map graph is static, and only the items in
    the bag and the settings change between calls. The bot's travel loop asks for the same plan
    after each leg; a copy goes out so a caller cannot edit the cached legs."""
    here_map, goal_map = int(here_map), int(goal_map)
    if here_map not in atlas.maps() or goal_map not in atlas.maps():
        # a map the data doesn't have: no way to plan there (with or without a goal cell alike -
        # a goal cell used to reach the map model first and raise KeyError)
        return (None, None) if with_cost else None
    have = frozenset(str(r.get("name") or "").strip().lower() for r in (bag or []) if count_of(r) > 0)
    key = (here_map, (int(here_cell[0]), int(here_cell[1])), goal_map,
           (int(goal_cell[0]), int(goal_cell[1])) if goal_cell is not None else None,
           bool(transport), frozenset(avoid), bool(vip), have, hearth_home, bool(hearth_key),
           bool(items), bool(team), bool(with_cost), _fare_band(arnebia, vip), frozenset(spells))
    boxed = lru.get(_plan_cache, key)
    if boxed is None:
        boxed = (_plan(here_map, here_cell, goal_map, goal_cell, transport, avoid, vip, bag,
                       hearth_home, hearth_key, items, team, with_cost, arnebia, spells),)
        lru.put(_plan_cache, key, boxed, PLAN_CACHE_MAX)
    return copy.deepcopy(boxed[0])


def _fare_band(arnebia: int | None, vip: bool) -> tuple[int, ...] | None:
    """Which fares the arnebia on hand covers, for the plan cache: two amounts that pay for the
    same rides plan alike (the bag's arnebia changes after every kill)."""
    if arnebia is None:
        return None
    return tuple(sorted({f for t in transports() if not (vip and t.get("choose_vip"))
                         for f in [fare(t)] if f is not None and f <= arnebia}))


def _plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
          transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
          hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False,
          with_cost: bool = False, arnebia: int | None = None, spells: Sequence[Spell] = ()) -> Plan | CostedPlan:
    if here_map == goal_map:
        return _same_map(here_map, here_cell, goal_cell, with_cost)
    g = _static_graph()
    avoid = set(avoid)
    extra: Graph = _transport_edges(avoid, vip, team, arnebia) if transport else {}

    def edges(node: Node) -> list[Edge]:
        return g.get(node, []) + (extra.get(node, []) if extra else [])

    # Start: walk from where we stand to every port and transport stand on this map, or use a
    # travel item from the bag, from right here.
    first = _walk_starts(here_map, here_cell, transport, avoid, team)
    item_edges: Graph = {}
    if items:
        first += _item_starts(here_map, bag, hearth_home, hearth_key, avoid, item_edges)
    if spells:
        first += _spell_starts(here_map, here_cell, spells, avoid, item_edges)
    at_goal = _goal_test(goal_map, goal_cell)
    found, best, prev = _search(first, edges, item_edges, at_goal)
    if found is None:
        # Stuck in a dungeon, secret room, event map or gang base with no item to get out: leave it
        # (the Action menu, or a gang base's own exit), then plan again from wherever that puts us.
        left, cost = way_out(here_map, here_cell, avoid)
        if left is not None:
            return (left, cost) if with_cost else left
        return (None, None) if with_cost else None
    legs: list[Leg] = []
    node = found
    while node in prev:
        node, leg = prev[node]
        if leg:
            legs.append(leg)
    legs.reverse()
    if goal_cell is not None:
        legs.append({"do": "walk", "map": goal_map, "cell": list(goal_cell)})
    return (legs, best[found]) if with_cost else legs


START: Node = ("s",)


def _same_map(here_map: int, here_cell: Sequence[float], goal_cell: Sequence[float] | None,
              with_cost: bool) -> Plan | CostedPlan:
    """Already on the goal map: a walk to the goal cell, if there is one."""
    legs: list[Leg] = [] if goal_cell is None else [{"do": "walk", "map": here_map, "cell": list(goal_cell)}]
    if not with_cost:
        return legs
    if goal_cell is None:
        return legs, 0.0
    goal = (goal_cell[0], goal_cell[1])
    d = _steps_from(here_map, here_cell, [goal]).get(goal)
    if d is None:
        # The cell beside an NPC can be a wall or a counter (central plains' and Tulip City's Shard
        # Brokers). From another map the goal is reached at the nearest walkable cell (_goal_test
        # walks from it); here it was "no way", so the NPC in the city we stand in lost to one in
        # another city.
        near = atlas.model(here_map).nearest_walkable(int(goal[0]), int(goal[1]), 6)
        if near is not None:
            d = _steps_from(here_map, here_cell, [near]).get((near[0], near[1]))
    return (legs, d) if d is not None else (None, None)


def _walk_starts(here_map: int, here_cell: Sequence[float], transport: bool, avoid: Collection[Hashable],
                 team: bool) -> list[Edge]:
    """(steps, node, None) to every port and transport stand on this map we can walk to."""
    first: list[Edge] = []
    ports = _ports(here_map)
    points: list[Point] = [(p["x"], p["y"]) for p in ports]
    stands = [t for t in transports() if transport and t.get("map") == here_map and t["id"] not in avoid
              and (team or not t.get("team")) and usable(t)]
    points += [(t["stand"][0], t["stand"][1]) for t in stands if t.get("stand")]
    reach: dict[Point, float] = _steps_from(here_map, here_cell, points) if ports or stands else {}
    for i, p in enumerate(ports):
        d = reach.get((p["x"], p["y"]))
        if d is not None:
            first.append((d, ("p", here_map, i), None))
    for t in stands:
        if t.get("stand"):
            d = reach.get((t["stand"][0], t["stand"][1]))
            if d is not None:
                first.append((d, ("t", t["id"]), None))
        else:
            first.append((UNSEEN_NPC, ("t", t["id"]), None))
    return first


def _item_starts(here_map: int, bag: Sequence[Row] | None, hearth_home: str | None, hearth_key: bool,
                 avoid: Collection[Hashable], item_edges: Graph) -> list[Edge]:
    """(cost, node, leg) for each travel item in the bag; item_edges gets the walks from where
    each lands to that map's ports."""
    first: list[Edge] = []
    for item, pick, (lm, lx, ly), cost in item_options(bag, hearth_home, hearth_key):
        if item in avoid or lm == here_map:
            continue
        node: Node = ("i", item, lm, lx, ly)
        leg: Leg = {"do": "item", "item": item, "choose": pick, "dest": lm, "lands": [lx, ly]}
        first.append((cost, node, leg))
        for i, steps in _landing_ports(lm, (lx, ly)).items():
            item_edges.setdefault(node, []).append((steps, ("p", lm, int(i)), None))
    return first


_spot_cache: dict[tuple[int, Cell], tuple[tuple[int, Cell] | None]] = {}
SPOT_CACHE_MAX = 64


def cast_spot(mid: int, cell: Sequence[float]) -> tuple[int, Cell] | None:
    """Where a skill can be cast from: (steps there, cell). Here (0 steps) outside a safe zone;
    in one, the nearest walkable cell outside every safe zone (never an exit, which would leave
    the map). None: no such cell can be walked to."""
    here: Cell = (int(cell[0] + 0.5), int(cell[1] + 0.5))
    if not atlas.in_safe_zone(mid, cell[0], cell[1]):
        return 0, here
    key = (int(mid), here)
    boxed = lru.get(_spot_cache, key)
    if boxed is None:
        boxed = (_nearest_unsafe(mid, here),)
        lru.put(_spot_cache, key, boxed, SPOT_CACHE_MAX)
    return boxed[0]


def _nearest_unsafe(mid: int, here: Cell) -> tuple[int, Cell] | None:
    m = atlas.model(mid)
    dist = m.steps_arr(here)
    w = m.W
    h = len(dist) // w
    blocked = np.zeros((h, w), dtype=bool)
    for x1, y1, x2, y2 in atlas.safe_zones(mid):
        blocked[max(0, y1):min(h, y2 + 1), max(0, x1):min(w, x2 + 1)] = True
    blocked |= np.asarray(m.portal, dtype=bool)[:h, :w]
    ok = (dist >= 0) & ~blocked.ravel()
    if not ok.any():
        return None
    i = int(np.argmin(np.where(ok, dist, np.iinfo(np.int32).max)))
    return int(dist[i]), (i % w, i // w)


def _spell_starts(here_map: int, here_cell: Sequence[float], spells: Sequence[Spell],
                  avoid: Collection[Hashable], item_edges: Graph) -> list[Edge]:
    """(cost, node, leg) for each place a teleport skill can take us, cast here or (standing in a
    safe zone, where no skill works) from the nearest cell outside it; item_edges gets the walks
    from each landing to that map's ports."""
    spot = cast_spot(here_map, here_cell)
    if spot is None:
        return []
    steps, at = spot
    first: list[Edge] = []
    for skill, place in spells:
        land = spell_landing(skill, place)
        if land is None or skill in avoid or (skill, place) in avoid or land[0] == here_map:
            continue
        lm, lx, ly = land
        node: Node = ("i", "%s: %s" % (skill, place), lm, lx, ly)
        leg: Leg = {"do": "skill", "skill": skill, "place": place, "choose": [spell_choice(place)],
                    "dest": lm, "lands": [lx, ly]}
        if steps:
            leg["cast_at"] = [at[0], at[1]]
        first.append((SPELL + steps, node, leg))
        for i, walk in _landing_ports(lm, (lx, ly)).items():
            item_edges.setdefault(node, []).append((walk, ("p", lm, int(i)), None))
    return first


def _goal_test(goal_map: int, goal_cell: Sequence[float] | None) -> Callable[[Node], float | None]:
    """at_goal(node): the steps still to walk from a node on goal_map (to the goal cell, if there
    is one), or None for a node elsewhere or one with no way to the cell."""
    goal_cost: dict[Node, float] = {}
    if goal_cell is not None:
        gp = _ports(goal_map)
        back = _steps_from(goal_map, goal_cell, [(p["x"], p["y"]) for p in gp])
        for i, p in enumerate(gp):
            if (p["x"], p["y"]) in back:
                goal_cost[("p", goal_map, i)] = back[(p["x"], p["y"])]

    def at_goal(node: Node) -> float | None:
        if node[0] == "p" and node[1] == goal_map:
            return goal_cost.get(node, 0 if goal_cell is None else None)
        if node[0] == "i" and node[2] == goal_map:
            if goal_cell is None:
                return 0
            goal = (goal_cell[0], goal_cell[1])
            return _steps_from(goal_map, (node[3], node[4]), [goal]).get(goal)
        return None
    return at_goal


def _search(first: list[Edge], edges: Callable[[Node], list[Edge]], item_edges: Graph,
            at_goal: Callable[[Node], float | None]) -> tuple[Node | None, dict[Node, float], dict[Node, tuple[Node, Leg | None]]]:
    """Dijkstra from START over the travel graph to a goal node ("z", node). (found or None, best
    cost by node, prev node and leg by node)."""
    best: dict[Node, float] = {START: 0.0}
    prev: dict[Node, tuple[Node, Leg | None]] = {}
    heap: list[tuple[float, int, Node]] = [(0.0, 0, START)]
    tie = 1
    done: set[Node] = set()
    while heap:
        d, _t, node = heapq.heappop(heap)
        if node in done:
            continue
        done.add(node)
        if node[0] == "z":
            return node, best, prev
        g_extra = at_goal(node)
        if g_extra is not None:
            z: Node = ("z", node)
            nd = d + g_extra
            if nd < best.get(z, 1e18):
                best[z], prev[z] = nd, (node, None)
                heapq.heappush(heap, (nd, tie, z))
                tie += 1
        out = first if node == START else edges(node) + item_edges.get(node, [])
        for cost, nxt, leg in out:
            nd = d + cost
            if nd < best.get(nxt, 1e18):
                best[nxt], prev[nxt] = nd, (node, leg)
                heapq.heappush(heap, (nd, tie, nxt))
                tie += 1
    return None, best, prev


def describe(legs: list[dict[str, Any]]) -> list[str]:
    """One line per leg, for logs and for checking a plan by ear."""
    out: list[str] = []
    for leg in legs:
        if leg["do"] == "exit" and leg.get("dest") is None:
            out.append("%s: walk out of the gang base" % atlas.label(leg["map"]))
        elif leg["do"] == "exit":
            out.append("%s: walk out to %s" % (atlas.label(leg["map"]), atlas.label(leg["dest"])))
        elif leg["do"] == "npc":
            how = {"cable_car": "cable car", "airship": "airship", "circle": "teleport circle",
                   "menu": "menu"}.get(leg["kind"], "NPC")
            cost = " (%s arnebia)" % leg["cost"] if isinstance(leg.get("cost"), int) and leg["cost"] else ""
            out.append("%s: %s with the %s to %s%s" % (atlas.label(leg["map"]), how, leg["npc"],
                                                       atlas.label(leg["dest"]), cost))
        elif leg["do"] == "item":
            out.append("use %s%s to %s" % (leg["item"], (" (%s)" % leg["choose"][0]) if leg["choose"] else "",
                                           atlas.label(leg["dest"])))
        elif leg["do"] == "skill":
            out.append("%s to %s (%s)" % (leg["skill"], leg["place"], atlas.label(leg["dest"])))
        elif leg["do"] == "leave":
            out.append("%s: leave through the Action menu" % atlas.label(leg["map"]))
        else:
            out.append("%s: walk to %d,%d" % (atlas.label(leg["map"]), leg["cell"][0], leg["cell"][1]))
    return out


def matches(choose: str, text: str | None) -> bool:
    return bool(text) and re.search(choose, text, re.I) is not None
