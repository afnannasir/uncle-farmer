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
Costs are in walking steps; talking, riding and teleporting add fixed overheads so they are used
only when they really save walking (or when there's no other way). The plan is rebuilt after every
leg, so a guess that turns out wrong costs nothing more than a re-plan.

plan() returns a list of legs; the bot carries out only the first one and asks again:
  {"do": "exit", "map", "exit", "stand", "rect", "dest"}                walk off through a map exit
  {"do": "npc",  "map", "npc", "at", "stand", "choose": [...], "dest", "ride", "id", "kind"}
  {"do": "item", "item", "choose": [...], "dest", "lands"}             use a travel item
  {"do": "leave", "map"}                                                leave an instance (Action menu)
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

_transports: list[Row] | None = None


def transports() -> list[Row]:
    global _transports
    if _transports is None:
        path = os.path.join(paths.DATA, "transports.json")
        found: list[Row] = []
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                found = json.load(f)["transports"]
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


def _transport_edges(avoid: Collection[Hashable], vip: bool, team: bool = False) -> collections.defaultdict[Node, list[Edge]]:
    """Port -> NPC stand -> landing, for every usable transport (team dungeons only with a team)."""
    g: collections.defaultdict[Node, list[Edge]] = collections.defaultdict(list)
    for t in transports():
        if t["id"] in avoid or (t.get("team") and not team):
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
        choose: list[str] = t["choose_vip"] if vip and t.get("choose_vip") else t["choose"]
        leg: Leg = {"do": "npc", "map": mid, "npc": t["npc"], "at": t.get("at"), "stand": t.get("stand"),
               "choose": choose, "dest": t["dest"], "ride": t.get("ride"), "id": t["id"], "kind": t["kind"],
               "cost": t.get("cost")}
        node: Node = ("l", t["id"])
        g[stand].append((TALK.get(t["kind"], 60), node, leg))
        landed: dict[str, float] = land["ports"]
        for i, steps_ in landed.items():
            g[node].append((steps_, ("p", land["map"], int(i)), None))
    return g


def item_options(bag: Sequence[Row] | None, hearth_home: str | None = None, hearth_key: bool = False) -> list[ItemOption]:
    """Travel items we could use right now: [(item, choose list, (map, x, y), cost)]."""
    have: dict[str, int] = {(r.get("name") or "").strip().lower(): r.get("count", 1) for r in (bag or [])}
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
    if not m.ok(*cell):
        near = m.nearest_walkable(cell[0], cell[1], 6)
        if not near:
            return {}
        cell = near
    want: list[Point] = [(t[0], t[1]) for t in targets if m.ok(int(t[0]), int(t[1]))]
    if hasattr(m, "dijkstra_arr"):
        return _steps_arrays(m, cell, targets, want)
    dist, _prev = m.dijkstra(cell, want)
    if any(t[1] * m.W + t[0] not in dist for t in want) and m.gate.any():
        with m.no_gates():                     # see MapModel.route
            loose, _prev = m.dijkstra(cell, want)
        loose.update(dist)
        dist = loose
    out: dict[Point, float] = {}
    for t in targets:
        d = dist.get(int(t[1]) * m.W + int(t[0]))
        if d is not None:
            out[(t[0], t[1])] = d
    return out


def _steps_arrays(m: MapModel, cell: Cell, targets: Sequence[Point], want: list[Point]) -> dict[Point, float]:
    """_steps_uncached on the model's array search: the same distances, without turning a search
    that reaches the whole map into a dict first (inf = the search never got there)."""
    if hasattr(m, "dijkstra_targets"):
        got = m.dijkstra_targets(cell, want)
        if not np.isfinite(got).all() and m.gate.any():
            with m.no_gates():                 # see MapModel.route
                loose = m.dijkstra_targets(cell, want)
            got = np.where(np.isfinite(got), got, loose)
        reached: list[float] = got.tolist()
        by_cell = {(int(t[0]), int(t[1])): float(d) for t, d in zip(want, reached) if math.isfinite(d)}
        return {(t[0], t[1]): by_cell[(int(t[0]), int(t[1]))] for t in targets
                if (int(t[0]), int(t[1])) in by_cell}
    W = m.W
    dist, _prev = m.dijkstra_arr(cell, want)
    if any(not math.isfinite(dist[int(t[1]) * W + int(t[0])]) for t in want) and m.gate.any():
        with m.no_gates():                     # see MapModel.route
            loose, _prev = m.dijkstra_arr(cell, want)
        dist = np.where(np.isfinite(dist), dist, loose)
    out: dict[Point, float] = {}
    for t in targets:
        k = int(t[1]) * W + int(t[0])
        if 0 <= k < dist.size and math.isfinite(dist[k]):
            out[(t[0], t[1])] = float(dist[k])
    return out


# boxed, so a miss (None) is told apart from a cached "no way there"
_plan_cache: dict[tuple[Hashable, ...], tuple[Plan | CostedPlan]] = {}
PLAN_CACHE_MAX = 128


@overload
def plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
         transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
         hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False,
         with_cost: Literal[False] = False) -> Plan: ...
@overload
def plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
         transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
         hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False, *,
         with_cost: Literal[True]) -> CostedPlan: ...
def plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
         transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
         hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False,
         with_cost: bool = False) -> Plan | CostedPlan:
    """The legs from (here_map, here_cell) to goal_map (and goal_cell), or None if unreachable.

    bag: the bag rows ({"name", "count"}) so travel items can be used; hearth_home: the square the
    hearthstone takes us to (learned); avoid: transport ids and item names that failed.

    Cached by every input that affects the answer - the map graph is static, and only the items in
    the bag and the settings change between calls. The bot's travel loop asks for the same plan
    after each leg; a copy goes out so a caller cannot edit the cached legs."""
    here_map, goal_map = int(here_map), int(goal_map)
    have = frozenset((r.get("name") or "").strip().lower() for r in (bag or [])
                     if r.get("count", 1) > 0)
    key = (here_map, (int(here_cell[0]), int(here_cell[1])), goal_map,
           (int(goal_cell[0]), int(goal_cell[1])) if goal_cell is not None else None,
           bool(transport), frozenset(avoid), bool(vip), have, hearth_home, bool(hearth_key),
           bool(items), bool(team), bool(with_cost))
    boxed = lru.get(_plan_cache, key)
    if boxed is None:
        boxed = (_plan(here_map, here_cell, goal_map, goal_cell, transport, avoid, vip, bag,
                       hearth_home, hearth_key, items, team, with_cost),)
        lru.put(_plan_cache, key, boxed, PLAN_CACHE_MAX)
    return copy.deepcopy(boxed[0])


def _plan(here_map: int, here_cell: Sequence[float], goal_map: int, goal_cell: Sequence[float] | None = None,
          transport: bool = True, avoid: Collection[Hashable] = (), vip: bool = False, bag: Sequence[Row] | None = None,
          hearth_home: str | None = None, hearth_key: bool = False, items: bool = True, team: bool = False,
          with_cost: bool = False) -> Plan | CostedPlan:
    if here_map == goal_map:
        return _same_map(here_map, here_cell, goal_cell, with_cost)
    g = _static_graph()
    avoid = set(avoid)
    extra: Graph = _transport_edges(avoid, vip, team) if transport else {}

    def edges(node: Node) -> list[Edge]:
        return g.get(node, []) + (extra.get(node, []) if extra else [])

    # Start: walk from where we stand to every port and transport stand on this map, or use a
    # travel item from the bag, from right here.
    first = _walk_starts(here_map, here_cell, transport, avoid, team)
    item_edges: Graph = {}
    if items:
        first += _item_starts(here_map, bag, hearth_home, hearth_key, avoid, item_edges)
    at_goal = _goal_test(goal_map, goal_cell)
    found, best, prev = _search(first, edges, item_edges, at_goal)
    if found is None:
        # Stuck in a dungeon, secret room or event map with no item to get out: leave it through
        # the Action menu, then plan again from wherever that puts us.
        if is_instance(here_map) and "leave" not in avoid:
            left: list[Leg] = [{"do": "leave", "map": here_map}]
            return (left, 500.0) if with_cost else left
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
    return (legs, d) if d is not None else (None, None)


def _walk_starts(here_map: int, here_cell: Sequence[float], transport: bool, avoid: Collection[Hashable],
                 team: bool) -> list[Edge]:
    """(steps, node, None) to every port and transport stand on this map we can walk to."""
    first: list[Edge] = []
    ports = _ports(here_map)
    points: list[Point] = [(p["x"], p["y"]) for p in ports]
    stands = [t for t in transports() if transport and t["map"] == here_map and t["id"] not in avoid
              and (team or not t.get("team"))]
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
        if leg["do"] == "exit":
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
        elif leg["do"] == "leave":
            out.append("%s: leave through the Action menu" % atlas.label(leg["map"]))
        else:
            out.append("%s: walk to %d,%d" % (atlas.label(leg["map"]), leg["cell"][0], leg["cell"][1]))
    return out


def matches(choose: str, text: str | None) -> bool:
    return bool(text) and re.search(choose, text, re.I) is not None
