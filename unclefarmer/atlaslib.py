#!/usr/bin/env python3
"""
The model of Mist World's maps: walls, exits, gates and routes.

SOURCE
------
Everything comes from the game's own map table, flattened into
data/map_geometry.csv.

WHAT A MAP IS
-------------
A stack of axis-aligned rectangles on a 1-based grid. x grows EAST,
y grows SOUTH (Polson Wetland's own area names put "Northwest" at
x1-133 y1-133 and "south" at y268-400). The game paints rectangles in
file order and the last one to cover a cell wins.

Row kinds, by terrain id:
    0          named area ("Northwest", "center", ...) or, at z=-3,
               an unnamed zone in front of an exit
    1-99       markers: arrival points, player spawn, monster respawn
               zones, NPCs, shops, boxes
    100-199    walkable ground
    200-226    SOLID (walls, trees, fences, rock, closed doors, ...)
    299        SOLID map edge
    301-332    doors (passable)
    333-339    cover / high-platform transitions (passable, directional)
    401-420    falls, climbs, water entry, banks (passable, directional)
    500-506    teleport pad, music notes, energy barrier, slide
    1000-99999 ambient sound regions - not terrain
    >=100000   TRANSITIONS: destination_map * 1000 + arrival_marker.
               z=-1 is a real exit to another map; z=-3 pointing back to
               its own map is a TOWN/VILLAGE ZONE (labelled White Sand
               City, holy blood city...), not a teleport - see _exits.
               9999999 is the gang-base "leave" exit (back to wherever
               you came from).

The trailing float is elevation on ground rows and a direction mask
(1/2/4/8 and sums) on door, fall and climb rows: 1 = west, 2 = north,
4 = east, 8 = south. On an unpaired door (301-332) the mask is the
direction you can NOT step off it - measured live on turkey cave's
Stone Gates, 2026-10-05 (see MapModel._gates); routes obey that. Falls
and climbs are not measured; the atlas flags routes that depend on an
unpaired one as possibly one-way.

MOVEMENT MODEL
--------------
4-way steps (the character moves N/E/S/W). A cell is walkable if the
winning terrain is not solid. Unpainted cells are treated as void.
"""
from collections.abc import Mapping
from typing import Any
import contextlib
import copy
import ctypes
import csv
import functools
import heapq
import math
import operator
import pathlib
import re
import threading

import numpy as np

from . import lru, native as _native
# The map data lives in the app's data folder.
from .paths import DATA as _APP_DATA  # noqa: E402

# The C searches (native/routing.c), or None: every search below then runs its Python body, which
# gives the same answers. Tests set this to None to compare the two.
NATIVE = _native.lib

DATA = pathlib.Path(_APP_DATA)
GEOMETRY = DATA / "map_geometry.csv"
DOORS = set(range(301, 333))
DIRECTIONAL = set(range(333, 340)) | (set(range(401, 421)) - {410})
EXIT_GANG = 9999999

# penalty for stepping onto a cell by its clearance (Chebyshev distance
# to the nearest non-walkable cell). Keeps routes off walls where the
# ground allows it, and still lets them through 1-wide corridors.
PEN = {1: 4.0, 2: 1.5, 3: 0.5}
_PEN_C = (ctypes.c_double * 16)(*[PEN.get(i, 0.0) for i in range(16)])   # for the C searches

_RETRY = -3              # routing.c: a cost its buckets can't hold (search again with the heap)
_NO_ROUTE = object()      # a route cache miss (None is a cached "no way")


def blocks(t):
    return 200 <= t <= 226 or t == 299


# ---------------------------------------------------------------- data
GEOMETRY_CACHE = 4      # maps whose parsed rows are kept; the rest are re-read from the file


class _Geometry(Mapping):
    """The geometry file as {map id: rows}, streaming it instead of holding every row.

    Keeping every parsed row of every map alive costs about 87 MiB (measured 2026-10-20), while
    the bot needs the ids, the names, and the rows of the one map it is on. One streaming pass
    records each map's name and the line range of its rows (the file is grouped by map: 375 maps,
    no map split by another), so a map's rows can be read back without parsing the other 90k."""

    _names: dict[int, str] | None = None
    _index: dict[int, tuple[int, int]] | None = None      # map id -> (byte offset, byte length)
    _header: list[str] | None = None

    @classmethod
    def _scan(cls) -> dict[int, tuple[int, int]]:
        """One pass over the file in bytes: where each map's rows start and how long they run, so
        rows_for() can seek straight to them. map_id is read off the front of each line without the
        csv module (it is a bare number); only a map's first row is parsed, for its name. 4x faster
        than parsing every row, and a map at the end of the file no longer reads the file up to it."""
        if cls._index is None:
            index: dict[int, list[Any]] = {}
            with GEOMETRY.open("rb") as fh:
                first = fh.readline()
                header = next(csv.reader([first.decode("utf-8-sig")]))
                column, name_col = header.index("map_id"), header.index("map_name")
                offset = len(first)
                last = None
                for raw in fh:
                    if column == 0 and b'"' not in raw[:raw.find(b",")]:
                        mid = int(raw[:raw.index(b",")])
                    else:
                        mid = int(next(csv.reader([raw.decode("utf-8")]))[column])
                    if mid == last:
                        index[mid][1] += len(raw)            # contiguous: just grow the range
                    else:
                        row = next(csv.reader([raw.decode("utf-8")]))
                        index[mid] = [offset, len(raw), row[name_col].strip()]
                        last = mid
                    offset += len(raw)
            cls._header = header
            cls._names = {mid: v[2] for mid, v in index.items()}
            cls._index = {mid: (v[0], v[1]) for mid, v in index.items()}
        assert cls._index is not None       # _scan() just filled it
        return cls._index

    @classmethod
    def names(cls) -> dict[int, str]:
        """id -> map name, from the one streaming pass (no rows kept)."""
        cls._scan()
        assert cls._names is not None       # _scan() just filled it
        return cls._names

    def __getitem__(self, mid):
        rows = rows_for(int(mid))
        if not rows:
            raise KeyError(mid)
        return rows

    def __iter__(self):
        return iter(self.names())

    def __len__(self):
        return len(self.names())

    def __contains__(self, mid):
        return int(mid) in self.names()


_GEOMETRY = _Geometry()
_names: dict[int, str] | None = None


@functools.lru_cache(maxsize=GEOMETRY_CACHE)
def rows_for(mid):
    """The geometry rows of one map, read from its byte range and parsed.

    Rows come back as a tuple, so a caller cannot edit the cache."""
    index = _Geometry._scan()
    start, length = index.get(int(mid), (0, 0))
    if not length:
        return ()
    with GEOMETRY.open("rb") as fh:
        fh.seek(start)
        chunk = fh.read(length).decode("utf-8")
    header = _Geometry._header
    assert header is not None          # _scan() read it, above
    return tuple(dict(zip(header, row)) for row in csv.reader(chunk.splitlines()))


def all_rows() -> Any:
    """{map id: rows}; use rows_for(mid) for a single map (kept, bounded)."""
    return _GEOMETRY


def map_names():
    """id -> name, for every map that has geometry."""
    global _names
    if _names is None:
        rows = all_rows()
        if isinstance(rows, _Geometry):
            _names = dict(rows.names())
        else:                                   # a stand-in mapping in tests
            _names = {mid: v[0]["map_name"].strip() for mid, v in rows.items() if v}
    return _names


BAD = re.compile(r'[<>:"/\\|?*]')


def safe(name):
    return BAD.sub("_", name).strip() or "unnamed"


def resolve(query):
    """Map id or (case-insensitive) name -> list of matching ids."""
    q = str(query).strip()
    if q.isdigit():
        return [int(q)] if int(q) in all_rows() else []
    ql = q.lower()
    names = map_names()
    exact = [m for m, n in names.items() if n.lower() == ql]
    if exact:
        return sorted(exact)
    return sorted(m for m, n in names.items() if ql in n.lower())


# ------------------------------------------------------------ glossary
GLOSS = [
    ("小怪刷新区", "minor monster respawn zone"), ("精英怪刷新区", "elite monster respawn zone"),
    ("超级怪刷新区", "super monster respawn zone"),
    ("怪刷新区", "monster respawn zone"), ("刷新区", "respawn zone"),
    ("精英怪", "elite monster"), ("超级怪", "super monster"),
    ("小怪", "minor monsters"), ("巡逻怪", "patrol monster"),
    ("追击怪", "chaser monster"), ("隐藏怪", "hidden monster"),
    ("随机怪", "random monster"), ("附加怪", "extra monster"),
    ("玩家出生点", "player spawn point"), ("玩家出生区", "player spawn area"),
    ("玩家复活点", "player revive point"), ("复活点", "revive point"),
    ("出生点", "spawn point"), ("玩家", "player "),
    ("怪复活点", "monster revive point"),
    ("洞内斜坡", "slope inside the cave"), ("斜坡", "slope"),
    ("附近", " (nearby)"), ("商店", "shop"), ("房间管家", "room steward"),
    ("管家", "steward"), ("接引使者", "guide envoy"), ("神父", "priest"),
    ("渔民", "fisherman"), ("村民", "villager"), ("小孩", "child"),
    ("人质", "hostage"), ("庄家", "dealer"), ("振民", "villager"),
    ("圣诞树", "christmas tree"), ("雪堆", "snowdrift"), ("花田", "flower field"),
    ("小河", "creek"), ("主堂前厅", "main hall front"), ("主堂后厅", "main hall back"),
    ("民居门口", "house doorway"), ("山谷口", "valley mouth"),
    ("荒漠", "desert"), ("岩浆", "lava"), ("巨蟒", "python"), ("幽灵", "ghost"),
    ("年兽", "nian beast"), ("家畜", "livestock"), ("羊圈", "sheep pen"),
    ("箱子", "box"), ("柜子", "cabinet"), ("道具", "item"), ("石棺", "sarcophagus"),
    ("大坝", "dam"), ("漩涡", "whirlpool"), ("开关", "switch"), ("梯子", "ladder"),
    ("阶梯", "stairs"), ("地窖", "cellar"), ("平台", "platform"),
    ("栅栏门", "fence gate"), ("石门", "stone gate"), ("石壁", "stone wall"),
    ("房门", "room door"), ("后院门", "backyard gate"), ("山门", "mountain gate"),
    ("关卡屏障", "stage barrier"), ("能量盾", "energy shield"), ("冰门", "ice door"),
    ("激光门", "laser gate"), ("遥控炸弹", "remote bomb"), ("基柱", "pillar base"),
    ("炮台", "turret"), ("炮", "cannon"), ("喷火", "flamethrower"),
    ("火球", "fireball"), ("接收", "receiving "), ("纵向走廊", "vertical corridor "),
    ("走廊", "corridor"), ("怪阵列", "monster array"), ("阵列", "array"),
    ("随机精英", "random elite"), ("支线", "side-quest "), ("主线", "main-quest "),
    ("遮挡", "cover"), ("填充", "filler"), ("巨石", "boulder"), ("漏洞", "gap"),
    ("山洞", "cave"), ("洞口", "cave mouth"), ("洞", "cave"),
    ("入口", "entrance"), ("出口", "exit"), ("岔路", "fork"), ("丁字口", "T-junction"),
    ("转弯", "turn"), ("山坡", "hillside"), ("小溪", "stream"), ("主路", "main road"),
    ("稀少区", "sparse zone"), ("怪群", "monster pack"), ("怪", "monster"),
    ("级", " level "), ("共", "total "), ("格", " cells"),
    ("房间", "room"), ("东北", "northeast"), ("西北", "northwest"),
    ("东南", "southeast"), ("西南", "southwest"), ("北侧", "north side"),
    ("南侧", "south side"), ("东侧", "east side"), ("西侧", "west side"),
    ("北", "north"), ("南", "south"), ("东", "east"), ("西", "west"),
    ("中心", "center"), ("中圈", "middle ring"), ("区", " zone"),
    ("第一组", "group 1 "), ("第二组", "group 2 "), ("第三组", "group 3 "),
    ("一", "1"), ("二", "2"), ("三", "3"), ("四", "4"), ("五", "5"),
    ("六", "6"), ("七", "7"), ("八", "8"), ("九", "9"), ("十", "10"),
]
_CJK = re.compile(r"[\u3400-\u9fff]")
# longest phrase first, so \u5c0f\u602a\u5237\u65b0\u533a is read whole instead of leaving \u5c0f
_GLOSS_ORDER = sorted(GLOSS, key=lambda kv: -len(kv[0]))


# ------------------------------------------------------ marker kinds
# Marker rows (ids 1-99) are zones the server uses: monster respawn
# areas, NPCs, chests, player spawn points, traps. The id says little
# (id 11 is "monster zone" on one map and "villager" on another); the
# label says what it is, so classify by label.
_PLAYER = ("玩家", "出生", "复活点")
_BOSS = ("boss", "超级怪", "超级精英")
_CHEST = ("箱子", "宝箱", "礼物箱", "礼盒", "宝物", "柜子随机")
_NPC = ("npc", "村民", "振民", "渔民", "神父", "小孩", "司仪", "接引使者", "管家",
        "村长", "商店", "兑换", "人质", "庄家", "家畜", "牛棚", "马棚", "羊圈", "鸭子")
_TRAP = ("喷火", "炮", "火球", "漩涡", "开关", "炸弹", "雪崩", "电流", "机关", "铡刀",
         "门", "石壁", "屏障", "能量盾", "涡轮", "阻挡", "轨道", "坑")
_CREATURE = ("怪", "幽灵", "玉兔", "年兽", "巨蟒", "飞蛾", "狗", "坦克", "蜂王", "食梦兽",
             "白蚁", "幻影", "元素", "教徒", "丛林", "矿工")


def marker_kind(label):
    """monster / elite / boss / chest / npc / player / trap / other."""
    s = label.lower()
    if not s:
        return "other"
    if re.fullmatch(r"\s*\d+\s*级\s*", s):
        return "monster"          # a zone labelled only by level, e.g. 60级
    if any(k in s for k in _PLAYER) and "怪" not in s:
        return "player"
    if any(k in s for k in _BOSS):
        return "boss"
    if "精英" in s:
        return "elite"
    if any(k in s for k in _CHEST):
        return "chest"
    if any(k in s for k in _NPC):
        return "npc"
    if "刷新区" in s and "怪" not in s and not any(k in s for k in _CREATURE):
        return "monster"          # bare "respawn zone", e.g. 刷新区3
    if any(k in s for k in _CREATURE) or "刷新区" in s:
        # a trap tagged with 怪 ("stone gate 3 monster") is still a monster spot
        return "monster"
    if any(k in s for k in _TRAP):
        return "trap"
    return "other"


# ---------------------------------------------------------------- model
def _astar_guess(W, turn, targets):
    """A*'s guess of the cost still to pay from cell c heading d: the steps to the targets'
    bounding box (each costs at least 1) plus a TURN for each turn still needed. Never more than
    the real cost, so the first target reached is the cheapest."""
    bx1, bx2 = min(t[0] for t in targets), max(t[0] for t in targets)
    by1, by2 = min(t[1] for t in targets), max(t[1] for t in targets)

    def guess(c, d):
        x, y = c % W, c // W
        togo, hx, hy = 0, -1, -1          # headings 0 east, 1 west, 2 south, 3 north
        if x < bx1:
            togo, hx = bx1 - x, 0
        elif x > bx2:
            togo, hx = x - bx2, 1
        if y < by1:
            togo, hy = togo + by1 - y, 2
        elif y > by2:
            togo, hy = togo + y - by2, 3
        ways = (hx >= 0) + (hy >= 0)
        if not ways:
            return 0.0
        return togo + turn * (ways - 1 if d in (hx, hy) else ways)
    return guess


class _Workspace:
    """Buffers the C route search keeps between searches (routing.c, uf_astar_rect): its costs and
    back-links for every (cell, heading) state, read only where this search's stamp marks them,
    so nothing is cleared per search. About 21 bytes a state: 13 MiB for a 400x400 map, per
    thread that routes on it."""

    STAMP_MAX = 2 ** 31 - 2          # 2 * stamp + 1 must fit in 32 bits

    def __init__(self, n):
        self.n = n
        self.dist = np.empty(4 * n, dtype=np.float64)
        self.prev = np.empty(4 * n, dtype=np.int32)
        self.mark = np.zeros(4 * n, dtype=np.uint32)
        self.wmark = np.zeros(n, dtype=np.uint32)
        self.out = np.empty(4 * n, dtype=np.int32)
        self.stats = np.zeros(2, dtype=np.int64)
        self.stamp = 0
        # dijkstra_targets: the same per cell (its marks are told apart by the same stamps)
        self.cdist = np.empty(n, dtype=np.float64)
        self.cprev = np.empty(n, dtype=np.int32)
        self.cmark = np.zeros(n, dtype=np.uint32)

    def next_stamp(self):
        self.stamp += 1
        if self.stamp > self.STAMP_MAX:          # every stamp used: start over on clean marks
            self.mark.fill(0)
            self.wmark.fill(0)
            self.cmark.fill(0)
            self.stamp = 1
        return self.stamp


def _flat_ids(cells, W):
    """The flat indices of cells as int32 for the C searches, or None when a cell is not a whole
    cell (the Python searches then run: they never match a fractional index)."""
    ids = [c[1] * W + c[0] for c in cells]
    arr = np.array(ids, dtype=np.int64).astype(np.int32) if ids else np.empty(0, dtype=np.int32)
    if ids and not all(i == j for i, j in zip(ids, arr.tolist())):
        return None
    return arr


class MapModel(object):
    """One map, painted, with walkability, labels and clearance.

    Arrays are (h+2) x (w+2), indexed [y][x]; row/column 0 and w+1/h+1
    are a void border so neighbour lookups need no bounds checks.
    """

    def __init__(self, mid, layer_aware=False):
        self._route_cache: dict[Any, Any] = {}
        self._route_loose: dict[Any, Any] = {}
        rows = all_rows().get(int(mid))
        if not rows:
            raise KeyError("map %s has no geometry" % mid)
        self.id = int(mid)
        self.name = rows[0]["map_name"].strip()
        self.layer_aware = layer_aware
        self.w = max(int(r["x2"]) for r in rows)
        self.h = max(int(r["y2"]) for r in rows)
        W, H = self.w + 2, self.h + 2
        self.W = W
        self.t = np.zeros((H, W), dtype=np.int32)          # 0 = unpainted
        self.lab = np.zeros((H, W), dtype=np.int32)        # index into labels
        self.zmask = np.zeros((H, W), dtype=np.int32)      # direction mask
        floor_z = np.full((H, W), np.nan)
        self.labels = [""]
        lab_ix = {"": 0}

        self.markers, self.transitions = [], []
        for r in rows:
            self._paint(r, lab_ix, floor_z)

        tt = self.t
        self.solid = ((tt >= 200) & (tt <= 226)) | (tt == 299)
        self.walk = (tt > 0) & ~self.solid
        self.walk[0, :] = self.walk[-1, :] = False
        self.walk[:, 0] = self.walk[:, -1] = False
        # An NPC stands on its cell and the character cannot walk through
        # it (WhaleUnified1.0 lists NPC names in obstacles= for exactly
        # this). The map table marks 438 single-cell NPC spots; treat each
        # as blocked so routes and wall files go round them. Wider NPC
        # markers (wandering livestock, villager crowds) are areas, not
        # positions, and are only reported.
        self.npc_cells = {}
        for mk in self.markers:
            if mk["x1"] == mk["x2"] and mk["y1"] == mk["y2"] and marker_kind(mk["label"]) == "npc":
                x, y = mk["x1"], mk["y1"]
                if 1 <= x <= self.w and 1 <= y <= self.h and self.walk[y, x]:
                    self.walk[y, x] = False
                    self.npc_cells[(x, y)] = mk["label"]
        self.directional = np.isin(tt, list(DIRECTIONAL))
        self._oneway()
        self._gates()
        self._clear()
        self._components()
        self._exits()
        # Exit rectangles move the character the moment it steps on one, so
        # routing treats them as no-go except as the chosen destination (or
        # where a route starts). Town zones are ordinary ground.
        self.portal = np.zeros(self.t.shape, dtype=bool)
        self.town = np.zeros(self.t.shape, dtype=bool)
        for e in self.towns:
            xa, xb = max(1, e["x1"]), min(self.w, e["x2"])
            ya, yb = max(1, e["y1"]), min(self.h, e["y2"])
            if xa <= xb and ya <= yb:
                self.town[ya:yb + 1, xa:xb + 1] = True
        for e in self.exits:
            xa, xb = max(1, e["x1"]), min(self.w, e["x2"])
            ya, yb = max(1, e["y1"]), min(self.h, e["y2"])
            if xa <= xb and ya <= yb:
                self.portal[ya:yb + 1, xa:xb + 1] = True
        self._cache_flat()

    def _paint(self, r, lab_ix, floor_z):
        """One row of the map table: a marker, a map transition, or an area painted onto the grid
        (a wall over a floor of another height is left out when layer_aware)."""
        t = int(r["type_id"])
        x1, y1, x2, y2 = (int(r[k]) for k in ("x1", "y1", "x2", "y2"))
        lab = r["label"].strip()
        try:
            z = float(r["z"])
        except ValueError:
            z = 0.0
        if t < 100:
            self.markers.append(dict(type=t, x1=x1, y1=y1, x2=x2, y2=y2,
                                     label=lab, z=z))
            return
        if t >= 100000:
            self.transitions.append(dict(code=t, x1=x1, y1=y1, x2=x2, y2=y2,
                                         label=lab, z=z))
            return
        if t >= 1000:
            return                # sound areas: nothing to walk around
        li = lab_ix.setdefault(lab, len(self.labels))
        if li == len(self.labels):
            self.labels.append(lab)
        xa, xb = max(1, x1), min(self.w, x2)
        ya, yb = max(1, y1), min(self.h, y2)
        if xa > xb or ya > yb:
            return
        sl = (slice(ya, yb + 1), slice(xa, xb + 1))
        if blocks(t) and self.layer_aware:
            fz = floor_z[sl]
            keep = np.isnan(fz) | (fz == z)
            self.t[sl] = np.where(keep, t, self.t[sl])
            self.lab[sl] = np.where(keep, li, self.lab[sl])
            return
        self.t[sl] = t
        self.lab[sl] = li
        if not blocks(t):
            floor_z[sl] = z
            if t in DOORS or t in DIRECTIONAL:
                self.zmask[sl] = int(z) if z > 0 else 0

    def _cache_flat(self):
        """Cache flat bytes for the routing loops (called once at init).

        bytes, not lists: indexing gives the same int per cell, at an eighth of the memory
        (six lists of a 402x402 map: 7.4 MiB; the same six bytes: 0.9 MiB)."""
        self._walk_list = self.walk.ravel().tobytes()
        self._clear_list = self.clear.ravel().tobytes()
        self._gate_list = self.gate.astype(np.uint8).ravel().tobytes()
        self._portal_list = self.portal.ravel().tobytes()
        self._oneway_list = self.oneway.ravel().tobytes()
        self._zero_list = bytes(len(self._gate_list))
        self._direc_list = self.directional.ravel().tobytes()
        W = self.W
        self._step_offs = ((1, 4), (-1, 1), (W, 8), (-W, 2))
        self._pen_arr = [PEN.get(i, 0.0) for i in range(16)]

    def _c(self):
        """The C searches when they can run on this model, else None (no DLL, or a stand-in model
        a test made without the flat bytes)."""
        if NATIVE is None or "_walk_list" not in self.__dict__ or "_direc_list" not in self.__dict__:
            return None
        return NATIVE

    # --------------------------------------------------------- basics
    def ok(self, x, y):
        return 1 <= x <= self.w and 1 <= y <= self.h and bool(self.walk[y, x])

    def without(self, cells):
        """A copy of the map that treats these cells as walls - NPCs standing in the way, which the
        map itself doesn't know about - for planning a walk around them."""
        mm = copy.copy(self)
        mm.walk = self.walk.copy()
        mm._route_cache = {}        # the cached paths may run through the cells now walled off
        mm._route_loose = {}
        for x, y in cells:
            if 1 <= x <= self.w and 1 <= y <= self.h:
                mm.walk[y, x] = False
        mm._walk_list = mm.walk.ravel().tobytes()
        return mm

    def _oneway(self):
        """Ledge/climb cells with no partner for the way back.

        The direction mask pairs up: horizontal strips carry 2 or 8,
        vertical strips 1 or 4, so 2<->8 and 1<->4 are opposites. Across
        the whole game 3,355 of 4,248 single-direction fall/climb rects
        touch a rect with the opposite bit - typically "fall to ground"
        beside "climb" - which makes that cliff two-way. A directional
        cell with no opposite-direction neighbour (or no mask at all,
        like the event "stage barriers") is the only kind that may be
        one-way, and only those are flagged.
        """
        zm = self.zmask
        opp = np.zeros_like(zm)
        for b, o in ((1, 4), (4, 1), (2, 8), (8, 2)):
            opp |= np.where((zm & b) != 0, o, 0)
        d = self.directional
        has = np.zeros_like(d)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            nm = np.roll(np.roll(zm, dy, 0), dx, 1)
            nd = np.roll(np.roll(d, dy, 0), dx, 1)
            has |= nd & ((nm & opp) != 0)
        has |= d & ((zm & opp) != 0)
        self.oneway = d & ~has

    def _gates(self):
        """One-way gates: the direction you can NOT step off each door cell (0 = none).

        Measured live 2026-10-05 on turkey cave's "Stone Gate" strips (tile
        329): the gate at x 56 (mask 4) lets you onto it but not on to the
        east, the one at x 33 (mask 1) not on to the west, the y 112 one
        (mask 2) not on to the north - the cave is a maze of one-way gates.
        Doors whose strip touches a door strip with the opposite bit come
        in pairs (towns, manors) and are left two-way, as before; only an
        unpaired single-direction door is treated as one-way.
        """
        door = np.isin(self.t, list(DOORS))
        gm = np.where(door & np.isin(self.zmask, (1, 2, 4, 8)), self.zmask, 0)
        opp = np.zeros_like(gm)
        for b, o in ((1, 4), (4, 1), (2, 8), (8, 2)):
            opp |= np.where((gm & b) != 0, o, 0)
        zd = np.where(door, self.zmask, 0)
        has = np.zeros(gm.shape, dtype=bool)
        for dy, dx in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            has |= (np.roll(np.roll(zd, dy, 0), dx, 1) & opp) != 0
        self.gate = np.where(has, 0, gm)

    # stepping by these flat-index offsets moves east, west, south, north
    STEP_BITS = (4, 1, 8, 2)

    def gate_blocks(self, a, b):
        """True when a one-way gate on cell a forbids the single step a -> b."""
        g = self.gate[a[1], a[0]]
        if not g:
            return False
        dx, dy = b[0] - a[0], b[1] - a[1]
        bit = 4 if dx > 0 else 1 if dx < 0 else 8 if dy > 0 else 2
        return bool(g & bit)

    def _clear(self):
        """Chebyshev distance to the nearest non-walkable cell, capped at 15."""
        cur = self.walk.copy()
        clear = np.zeros(cur.shape, dtype=np.int8)
        k = 0
        while cur.any() and k < 15:
            k += 1
            clear[cur] = k
            e = cur.copy()
            e[1:, :] &= cur[:-1, :]
            e[:-1, :] &= cur[1:, :]
            e[:, 1:] &= cur[:, :-1]
            e[:, :-1] &= cur[:, 1:]
            e[1:, 1:] &= cur[:-1, :-1]
            e[:-1, :-1] &= cur[1:, 1:]
            e[1:, :-1] &= cur[:-1, 1:]
            e[:-1, 1:] &= cur[1:, :-1]
            e[0, :] = e[-1, :] = False
            e[:, 0] = e[:, -1] = False
            cur = e
        self.clear = clear

    def _components(self):
        """comp: each cell's 4-connected region (1, 2, ... in order of their first cell, 0 = not
        walkable), as a list by flat index; comp_sizes: each region's size, from index 1."""
        if NATIVE is not None:
            walk_b = self.walk.ravel().tobytes()
            n = len(walk_b)
            comp = np.empty(n, dtype=np.int32)
            sizes = np.zeros(n + 2, dtype=np.int32)
            k = NATIVE.uf_components(self.W, n, walk_b, comp.ctypes.data, sizes.ctypes.data, n + 2)
            if k >= 0:
                self.comp = comp.tolist()
                self.comp_sizes = [0] + sizes[1:k + 1].tolist()
                return
        W = self.W
        walk = self.walk.ravel().tolist()
        comp = [0] * len(walk)
        sizes = [0]
        cid = 0
        for s in range(len(walk)):
            if not walk[s] or comp[s]:
                continue
            cid += 1
            comp[s] = cid
            stack = [s]
            n = 0
            while stack:
                c = stack.pop()
                n += 1
                for nb in (c + 1, c - 1, c + W, c - W):
                    if walk[nb] and not comp[nb]:
                        comp[nb] = cid
                        stack.append(nb)
            sizes.append(n)
        self.comp = comp
        self.comp_sizes = sizes

    def comp_of(self, x, y):
        return self.comp[y * self.W + x]

    def two_way(self, x, y):
        """Flat indices of every cell reachable from (x, y) AND able to walk back to it, obeying
        one-way gates: ground the bot can wander without being trapped behind a gate. Cached per
        such area. On a map without gates this is just the cell's region."""
        W = self.W
        c0 = y * W + x
        for area in getattr(self, "_two_way", []):
            if c0 in area:
                return area
        lib = self._c()
        if lib is not None and 0 <= c0 < len(self._walk_list):
            area = frozenset()
            if self._walk_list[c0]:
                fwd, back = self._bfs(lib, [c0], gates=True), self._bfs(lib, [c0], gates=True, reverse=True)
                area = frozenset(np.flatnonzero((fwd >= 0) & (back >= 0)).tolist())
            self._two_way = getattr(self, "_two_way", []) + [area]
            return area
        walk = self.walk.ravel().tolist()
        gate = self.gate.ravel().tolist()
        # (offset to the neighbour n, bit of the step c -> n, bit of the step n -> c)
        steps = ((1, 4, 1), (-1, 1, 4), (W, 8, 2), (-W, 2, 8))

        def spread(forward):
            seen = {c0}
            todo = [c0]
            while todo:
                c = todo.pop()
                for off, out_bit, back_bit in steps:
                    n = c + off
                    if n in seen or not walk[n]:
                        continue
                    if forward:
                        refused = gate[c] & out_bit      # can we walk c -> n?
                    else:
                        refused = gate[n] & back_bit     # can n walk onto c?
                    if refused:
                        continue
                    seen.add(n)
                    todo.append(n)
            return seen

        area = frozenset(spread(True) & spread(False)) if walk[c0] else frozenset()
        self._two_way = getattr(self, "_two_way", []) + [area]
        return area

    def nearest_walkable(self, x, y, radius=40, comp=None):
        for r in range(radius + 1):
            best, bd = None, None
            for dx in range(-r, r + 1):
                for dy in ((-r, r) if r else (0,)):
                    for px, py in ((x + dx, y + dy), (x + dy, y + dx)):
                        if not self.ok(px, py):
                            continue
                        if comp is not None and self.comp_of(px, py) != comp:
                            continue
                        d = abs(px - x) + abs(py - y)
                        if bd is None or d < bd:
                            best, bd = (px, py), d
            if best:
                return best
        return None

    def nearest_floor(self, x, y, radius=3, comp=None):
        """The nearest walkable cell that is not a ledge (directional) cell, or None. A ledge is
        crossed, never walked along or stood on: in corpse cave the survey's stops on the ledge
        strips round the raised stone tables held it for half an hour (2026-10-11) - on one,
        only the step across it moves the character."""
        for r in range(radius + 1):
            best, bd = None, None
            for dx in range(-r, r + 1):
                for dy in ((-r, r) if r else (0,)):
                    for px, py in ((x + dx, y + dy), (x + dy, y + dx)):
                        if not self.ok(px, py) or self.directional[py, px]:
                            continue
                        if comp is not None and self.comp_of(px, py) != comp:
                            continue
                        d = abs(px - x) + abs(py - y)
                        if bd is None or d < bd:
                            best, bd = (px, py), d
            if best:
                return best
        return None

    # ------------------------------------------------------- features
    def side_of(self, x1, y1, x2, y2):
        s = []
        if y1 <= 3:
            s.append("north")
        if y2 >= self.h - 2:
            s.append("south")
        if x2 >= self.w - 2:
            s.append("east")
        if x1 <= 3:
            s.append("west")
        return (" ".join(s) + " edge") if s else "inside the map"

    def _exits(self):
        """Real exits (z=-1 and the gang exit) and town zones (z=-3, own map)."""
        self.exits, self.towns = [], []
        for tr in self.transitions:
            code = tr["code"]
            if code == EXIT_GANG:
                dest, mark = None, None
            else:
                dest, mark = code // 1000, code % 1000
            x1, y1, x2, y2 = tr["x1"], tr["y1"], tr["x2"], tr["y2"]
            e: dict[str, Any] = dict(tr, dest=dest, marker=mark,
                                     side=self.side_of(x1, y1, x2, y2),
                                     cx=(x1 + x2) // 2, cy=(y1 + y2) // 2)
            cx, cy = int(e["cx"]), int(e["cy"])
            stand = self._nearest_in_rect(x1, y1, x2, y2, cx, cy)
            if stand is not None:
                e["stand"] = stand
                e["stand_inside"] = True
            else:
                e["stand"] = self.nearest_walkable(cx, cy, radius=8)
                e["stand_inside"] = False
            if tr["z"] == -3.0 and dest == self.id:
                # NOT a teleport: a town/village zone. Every "In the city"
                # position the game speaks falls inside one, and they
                # carry town names - holy
                # blood city, White Sand City, Giant Rock City. The character
                # walks through them; farming is disabled there.
                self.towns.append(e)
            else:
                self.exits.append(e)

    def _nearest_in_rect(self, x1, y1, x2, y2, cx, cy):
        """The walkable cell of the rectangle nearest (cx, cy) by steps, the first in x-then-y order
        on a tie (as min() over [(x, y) for x ... for y ...] picks it), or None. One numpy pass
        over the rectangle: exits are painted over whole strips, and a cell-by-cell walk of every
        exit was half of a big map's load time."""
        xa, xb = max(1, x1), min(self.w, x2)
        ya, yb = max(1, y1), min(self.h, y2)
        if xa > xb or ya > yb:
            return None
        xs, ys = np.nonzero(self.walk[ya:yb + 1, xa:xb + 1].T)     # x-major, like the loops
        if not xs.size:
            return None
        xs, ys = xs + xa, ys + ya
        i = int(np.argmin(np.abs(xs - cx) + np.abs(ys - cy)))
        return (int(xs[i]), int(ys[i]))

    # -------------------------------------------------------- routing
    def dijkstra(self, src, targets=(), avoid_directional=False, limit=None):
        """Wall-avoiding shortest paths from src. Stops once every target
        (set of (x,y)) is settled, or past limit steps. Returns (dist dict by
        flat index, prev). dijkstra_arr() is the same search as two arrays, without building
        the dicts (a whole-map search reaches 150,000 cells)."""
        arrs = self._dijkstra_c(src, targets, avoid_directional, limit)
        if arrs is None:
            return self._dijkstra_py(src, targets, avoid_directional, limit)
        dist, prev = arrs
        idx = np.flatnonzero(prev != -2)
        keys = idx.tolist()
        return dict(zip(keys, dist[idx].tolist())), dict(zip(keys, prev[idx].tolist()))

    def dijkstra_arr(self, src, targets=(), avoid_directional=False, limit=None):
        """dijkstra() as flat arrays: dist (float64, inf where the search never got) and prev
        (int32: -1 at src, -2 where never got)."""
        arrs = self._dijkstra_c(src, targets, avoid_directional, limit)
        if arrs is not None:
            return arrs
        d, p = self._dijkstra_py(src, targets, avoid_directional, limit)
        n = len(self._walk_list)
        dist = np.full(n, np.inf)
        prev = np.full(n, -2, dtype=np.int32)
        keys = np.fromiter(d.keys(), dtype=np.intp, count=len(d))
        dist[keys] = np.fromiter(d.values(), dtype=np.float64, count=len(d))
        prev[keys] = np.fromiter((p[k] for k in d), dtype=np.int32, count=len(d))
        return dist, prev

    def dijkstra_targets(self, src, targets, avoid_directional=False):
        """dijkstra(src, targets)'s distance to each target, as a float64 array (inf where the
        search never got): the same search without whole-map arrays. On the C search's kept
        workspace, a search that stops after a few hundred cells costs only those."""
        lib = self._c()
        W = self.W
        n = len(self._walk_list) if lib is not None else 0
        s = src[1] * W + src[0]
        want = _flat_ids(targets, W)
        if lib is None or want is None or s != int(s) or not 0 <= s < n:
            dist = self.dijkstra_arr(src, targets, avoid_directional)[0]
            ids = [t[1] * W + t[0] for t in targets]
            return np.array([dist[k] if 0 <= k < dist.size and k == int(k) else np.inf for k in ids],
                            dtype=np.float64)
        ws = self._workspace(n)
        out = np.empty(len(want), dtype=np.float64)
        r = lib.uf_dijkstra_to(W, n, self._walk_list, self._clear_list, self._gate_list,
                               self._oneway_list if avoid_directional else None, _PEN_C, int(s),
                               want.ctypes.data, len(want), 0, 0.0, ws.cdist.ctypes.data,
                               ws.cprev.ctypes.data, ws.cmark.ctypes.data, ws.wmark.ctypes.data,
                               ws.next_stamp(), out.ctypes.data, None)
        if r < 0:
            raise MemoryError("map search")
        return out

    def _dijkstra_c(self, src, targets, avoid_directional, limit):
        """The C search's (dist, prev), or None when it can't run here."""
        lib = self._c()
        if lib is None:
            return None
        W, n = self.W, len(self._walk_list)
        s = src[1] * W + src[0]
        want = _flat_ids(targets, W)
        if want is None or s != int(s) or not 0 <= s < n:
            return None              # off the grid or between cells: the Python search's own handling
        dist = np.empty(n, dtype=np.float64)
        prev = np.empty(n, dtype=np.int32)
        r = lib.uf_dijkstra(W, n, self._walk_list, self._clear_list, self._gate_list,
                            self._oneway_list if avoid_directional else None, _PEN_C,
                            int(s), want.ctypes.data, len(want), limit is not None,
                            float(limit) if limit is not None else 0.0,
                            dist.ctypes.data, prev.ctypes.data, None)
        if r < 0:
            raise MemoryError("map search")
        return dist, prev

    def _dijkstra_py(self, src, targets=(), avoid_directional=False, limit=None):
        """dijkstra() in Python: the reference the C search is checked against."""
        W = self.W
        walk = self._walk_list
        clear = self._clear_list
        direc = self._oneway_list if avoid_directional else None
        gate = self._gate_list
        step_offs = self._step_offs
        pen = self._pen_arr
        s = src[1] * W + src[0]
        want = {t[1] * W + t[0] for t in targets}
        dist = {s: 0.0}
        prev = {s: -1}
        heap = [(0.0, s)]
        done = set()
        while heap:
            d, c = heapq.heappop(heap)
            if c in done:
                continue
            if limit is not None and d > limit:
                break
            done.add(c)
            want.discard(c)
            if targets and not want:
                break
            gc = gate[c]
            for off, bit in step_offs:
                nb = c + off
                if not walk[nb] or nb in done or gc & bit:
                    continue
                if direc is not None and direc[nb]:
                    continue
                nd = d + 1.0 + pen[clear[nb]]
                if nd < dist.get(nb, 1e18):
                    dist[nb] = nd
                    prev[nb] = c
                    heapq.heappush(heap, (nd, nb))
        return dist, prev

    def steps_arr(self, src, limit=None):
        """steps() as a flat int32 array: distance by cell index, -1 where not reached. Layered
        BFS over numpy arrays (one vector step per ring) instead of a Python deque: 6-7x faster,
        same numbers (checked cell for cell on the big maps). The C search is a plain queue BFS:
        the same field again."""
        lib = self._c()
        s = src[1] * self.W + src[0]
        if lib is not None and 0 <= s < len(self._walk_list):
            return self._bfs(lib, [s], gates=True, limit=limit)
        return self._steps_arr_py(src, limit)

    def _bfs(self, lib, srcs, gates, limit=None, reverse=False):
        """The C BFS's int32 field from the flat cells srcs (-1 = not reached): one-way gates
        obeyed when gates (backwards when reverse: who can walk onto the cell), out to limit."""
        n = len(self._walk_list)
        seeds = np.asarray(srcs, dtype=np.int32)
        dist = np.empty(n, dtype=np.int32)
        lim = -1 if limit is None else max(0, math.ceil(limit))
        r = lib.uf_bfs(self.W, n, self._walk_list, self._gate_list if gates else None, bool(reverse),
                       seeds.ctypes.data, len(seeds), lim, dist.ctypes.data)
        if r < 0:
            raise MemoryError("map search")
        return dist

    def multi_steps(self, cells, limit=None):
        """Steps from the nearest of cells to every cell, IGNORING one-way gates, out to limit:
        int32 by flat index, -1 = not reached (shortloop's 'how close is the lap')."""
        lib = self._c()
        W = self.W
        seeds = sorted({c[1] * W + c[0] for c in cells})
        if lib is not None:
            return self._bfs(lib, seeds, gates=False, limit=limit)
        walk_flat = self.walk.ravel()
        seen = np.full(walk_flat.size, -1, dtype=np.int32)
        seen[seeds] = 0
        frontier = np.array(seeds, dtype=np.intp)
        d = 0
        while frontier.size and (limit is None or d < limit):
            d += 1
            rings = []
            for off in (1, -1, W, -W):
                nb = frontier + off
                nb = nb[walk_flat[nb] & (seen[nb] < 0)]
                seen[nb] = d
                rings.append(nb)
            frontier = np.concatenate(rings)
        return seen

    def _steps_arr_py(self, src, limit=None):
        """steps_arr() with numpy: the reference the C search is checked against."""
        W = self.W
        walk = self.walk.ravel()
        gate = self.gate.ravel()
        s = src[1] * W + src[0]
        dist = np.full(walk.size, -1, dtype=np.int32)
        dist[s] = 0
        frontier = np.array([s], dtype=np.intp)
        d = 0
        offs = (1, -1, W, -W)
        bits = self.STEP_BITS
        while frontier.size and (limit is None or d < limit):
            d += 1
            rings = []
            for off, bit in zip(offs, bits):
                nb = frontier[(gate[frontier] & bit) == 0] + off
                nb = nb[walk[nb] & (dist[nb] < 0)]
                dist[nb] = d
                rings.append(nb)
            frontier = np.concatenate(rings)
        return dist

    def steps(self, src, limit=None):
        """Walking steps from src to every cell it can reach, obeying one-way gates: dict by flat
        index. Real steps, with none of dijkstra's extra cost for walking beside walls - in a
        corridor that cost counted a monster 15 steps away as more than 40."""
        dist = self.steps_arr(src, limit)
        return {int(k): int(dist[k]) for k in np.nonzero(dist >= 0)[0]}

    def path(self, prev, dst):
        W = self.W
        c = dst[1] * W + dst[0]
        if c not in prev:
            return None
        out = []
        while c != -1:
            out.append((c % W, c // W))
            c = prev[c]
        return out[::-1]

    # ------------------------------------------------ the bot's own walk
    def diag_steps(self, p, q):
        """The steps (a, b) walking from p to q with both direction keys held: a 45-degree run while
        both axes still have ground to cover, then straight on along the longer one. A diagonal step
        is counted as both ways round its corner, so no wall corner is clipped. Whole cells only
        (a fractional one is cut down to its cell): stepping one cell at a time never lands on
        x.5, and the walk once ran on until it had taken all the memory there was."""
        x, y = int(p[0]), int(p[1])
        q = (int(q[0]), int(q[1]))
        sx = 1 if q[0] > x else -1
        sy = 1 if q[1] > y else -1
        steps = []
        while x != q[0] and y != q[1]:
            a, h, v, d = (x, y), (x + sx, y), (x, y + sy), (x + sx, y + sy)
            steps += [(a, h), (h, d), (a, v), (v, d)]
            x, y = d
        while x != q[0]:
            steps.append(((x, y), (x + sx, y)))
            x += sx
        while y != q[1]:
            steps.append(((x, y), (x, y + sy)))
            y += sy
        return steps

    def diag_ok(self, p, q):
        """The two-key walk from p to q stays on open ground: walkable, through no one-way gate the
        wrong way, onto no ledge, and over no exit before q itself. Whole cells (see diag_steps)."""
        p, q = (int(p[0]), int(p[1])), (int(q[0]), int(q[1]))
        lib = self._c()
        if lib is not None:
            return bool(lib.uf_diag_ok(self.W, len(self._walk_list), self.w, self.h, self._walk_list,
                                       self._gate_list, self._portal_list, self._direc_list,
                                       p[0], p[1], q[0], q[1]))
        for a, b in self.diag_steps(p, q):
            if not self.ok(*b) or self.directional[b[1], b[0]] or self.gate_blocks(a, b):
                return False
            if b != q and self.portal[b[1], b[0]]:
                return False
        return True

    def diag_hops(self, path, reach=300, stride=4):
        """A path cut into as few two-key walks as it can be: from each point, the furthest point
        along the path (at most reach cells on, looked at stride cells apart, then one by one)
        that diag_ok walks straight to. Corridors stay as they are; open ground is crossed on
        the slant (sampled routes: 7% less walking time in turkey cave, 17-19% in open maps)."""
        lib = self._c()
        if lib is not None and len(path) > 1:
            try:
                pts = np.array(path, dtype=np.int32).reshape(-1, 2)
            except (TypeError, ValueError):
                pts = None
            if pts is not None and pts.shape[0] == len(path) and np.array_equal(pts, np.asarray(path)):
                out = np.empty(len(path), dtype=np.int32)
                k = lib.uf_diag_hops(self.W, len(self._walk_list), self.w, self.h, self._walk_list,
                                     self._gate_list, self._portal_list, self._direc_list,
                                     pts.ctypes.data, len(path), int(reach), int(stride), out.ctypes.data)
                return [path[j] for j in out[:k].tolist()]
        hops = []
        i = 0
        while i < len(path) - 1:
            j = min(len(path) - 1, i + reach)
            while j > i + 1 and not self.diag_ok(path[i], path[j]):
                j -= stride if j - stride > i + 1 else 1
            hops.append(path[j])
            i = j
        return hops

    TURN = 12.0

    def exit_at(self, c):
        return next((e for e in self.exits
                     if e["x1"] <= c[0] <= e["x2"] and e["y1"] <= c[1] <= e["y2"]), None)

    def exit_cells_of(self, e):
        return [(x, y) for x in range(max(1, e["x1"]), min(self.w, e["x2"]) + 1)
                for y in range(max(1, e["y1"]), min(self.h, e["y2"]) + 1) if self.ok(x, y)]

    def dijkstra_rect(self, src, targets=(), avoid_directional=False, any_target=False, groups=None):
        """Wall-avoiding paths that also pay for every turn, so they come
        out as long straight east-west / north-south lanes down the middle
        of corridors - the shape of every hop in WhaleUnified1.0, which
        is the script that travels without getting stuck. State is
        (cell, heading). Returns (dist, prev, reached) where reached maps
        each target cell to the state it was settled in."""
        W = self.W
        walk = self._walk_list
        clear = self._clear_list
        direc = self._oneway_list if avoid_directional else None
        portal = self._portal_list
        gate = self._gate_list
        step_offs = self._step_offs
        pen = self._pen_arr
        s = src[1] * W + src[0]
        want = {t[1] * W + t[0] for t in targets}
        # Stepping on ANY exit cell crosses it, so an exit cell can only be
        # the last step of a route, and only if it is one of the targets.
        # The one exception is the exit a route starts in: it may walk out.
        own = set()
        e0 = self.exit_at(src)
        if e0 is not None:
            own = {y * W + x for x, y in self.exit_cells_of(e0)}
        # groups: each target may be a set of cells (a whole exit); the
        # search is done when every group has one cell reached
        pending = [set(g) for g in groups] if groups is not None else [{t} for t in want]
        reached = {}
        dist, prev, heap = {}, {}, []
        for d in range(4):
            dist[s * 4 + d] = 0.0
            prev[s * 4 + d] = -1
            heap.append((0.0, s * 4 + d))
        heapq.heapify(heap)
        done = set()
        while heap:
            cost, st = heapq.heappop(heap)
            if st in done:
                continue
            done.add(st)
            c, d = divmod(st, 4)
            if c in want and c not in reached:
                reached[c] = st
                pending = [g for g in pending if c not in g]
                if any_target or (targets and not pending):
                    break
            if portal[c] and c != s and c not in own:
                continue                      # on an exit cell: the walk ends here
            for nd, (off, bit) in enumerate(step_offs):
                nb = c + off
                if not walk[nb] or gate[c] & bit:
                    continue
                if portal[nb] and nb not in want and nb not in own:
                    continue
                if direc is not None and direc[nb]:
                    continue
                ns = nb * 4 + nd
                if ns in done:
                    continue
                nc = cost + 1.0 + pen[clear[nb]] + (self.TURN if nd != d else 0.0)
                if nc < dist.get(ns, 1e18):
                    dist[ns] = nc
                    prev[ns] = st
                    heapq.heappush(heap, (nc, ns))
        if s in want:
            reached[s] = s * 4
        return dist, prev, reached

    def astar_rect(self, src, targets, avoid_directional=False):
        """dijkstra_rect's route to the nearest of targets (same states, step costs and exit rules),
        found by A*: the search is pointed at the targets by a guess of the cost still to pay that
        never overestimates - the steps to the targets' bounding box (each costs at least 1) plus a
        TURN for each turn still needed - so the first target reached is as cheap as Dijkstra's
        (1,201 sampled routes on 66 maps: the same cost every time, 5.8x faster in all, the longest
        Dijkstra search 4.6 s). Equal guesses go to the state nearer the targets."""
        W = self.W
        walk = self._walk_list
        clear = self._clear_list
        direc = self._oneway_list if avoid_directional else None
        portal = self._portal_list
        gate = self._gate_list
        offs = (1, -1, W, -W)
        bits = self.STEP_BITS
        turn = self.TURN
        s = src[1] * W + src[0]
        want = {t[1] * W + t[0] for t in targets}
        reached = {}
        if not want:
            return {}, {}, reached
        own = set()
        e0 = self.exit_at(src)
        if e0 is not None:
            own = {y * W + x for x, y in self.exit_cells_of(e0)}
        guess = _astar_guess(W, turn, targets)

        dist, prev, heap = {}, {}, []
        for d in range(4):
            dist[s * 4 + d] = 0.0
            prev[s * 4 + d] = -1
            g = guess(s, d)
            heap.append((g, g, s * 4 + d))
        heapq.heapify(heap)
        done = set()
        while heap:
            _f, _g, st = heapq.heappop(heap)
            if st in done:
                continue
            done.add(st)
            c, d = divmod(st, 4)
            if c in want:
                reached[c] = st
                break
            if portal[c] and c != s and c not in own:
                continue                      # on an exit cell: the walk ends here
            cost = dist[st]
            for nd, off in enumerate(offs):
                nb = c + off
                if not walk[nb] or gate[c] & bits[nd]:
                    continue
                if portal[nb] and nb not in want and nb not in own:
                    continue
                if direc is not None and direc[nb]:
                    continue
                ns = nb * 4 + nd
                if ns in done:
                    continue
                nc = cost + 1.0 + PEN.get(clear[nb], 0.0) + (turn if nd != d else 0.0)
                if nc < dist.get(ns, 1e18):
                    dist[ns] = nc
                    prev[ns] = st
                    g = guess(nb, nd)
                    heapq.heappush(heap, (nc + g, g, ns))
        return dist, prev, reached

    def _astar_path(self, src, targets, avoid_directional=False):
        """path_rect(astar_rect(src, targets)) for the target reached, by the C search: the same
        route, without the dicts of every state it looked at. None when no target is reached."""
        lib = self._c()
        W, n = self.W, len(self._walk_list)
        s = src[1] * W + src[0]
        want = _flat_ids(targets, W)
        if lib is None or not targets or want is None or s != int(s) or not 0 <= s < n:
            _d, prev, reached = self.astar_rect(src, targets, avoid_directional)
            if not reached:
                return None
            k = next(iter(reached))
            return self.path_rect(prev, reached, (k % W, k // W))
        ox1, oy1, ox2, oy2 = 1, 1, 0, 0            # no exit of our own: an empty rectangle
        e0 = self.exit_at(src)
        if e0 is not None:
            ox1, oy1, ox2, oy2 = e0["x1"], e0["y1"], e0["x2"], e0["y2"]
        ws = self._workspace(n)
        got = ctypes.c_int32()
        k = _RETRY
        for heap in (0, 1):            # buckets; the heap only when a cost won't go in a bucket
            k = lib.uf_astar_rect(
                W, n, self._walk_list, self._clear_list, self._gate_list,
                self._oneway_list if avoid_directional else None, self._portal_list, _PEN_C,
                float(self.TURN), int(s), want.ctypes.data, len(want),
                min(t[0] for t in targets), max(t[0] for t in targets),
                min(t[1] for t in targets), max(t[1] for t in targets),
                ox1, oy1, ox2, oy2, ws.dist.ctypes.data, ws.prev.ctypes.data, ws.mark.ctypes.data,
                ws.wmark.ctypes.data, ws.next_stamp(), heap, ws.out.ctypes.data, len(ws.out),
                ctypes.byref(got), ws.stats.ctypes.data)
            if k != _RETRY:
                break
        if k < 0:
            raise MemoryError("map search")
        if k == 0:
            return None
        cells = ws.out[:k]
        return list(zip((cells % W).tolist(), (cells // W).tolist()))

    def _workspace(self, n):
        """This thread's buffers for the C route search on this map (see _Workspace). Kept per thread,
        so the farm and message threads never share one, and freed with the thread."""
        local = self.__dict__.get("_ws_local")
        if local is None:
            with MapModel._route_lock_guard:
                local = self.__dict__.setdefault("_ws_local", threading.local())
        ws = getattr(local, "ws", None)
        if ws is None or ws.n != n:
            ws = local.ws = _Workspace(n)
        return ws

    def path_rect(self, prev, reached, dst):
        W = self.W
        k = dst[1] * W + dst[0]
        if k not in reached:
            return None
        st = reached[k]
        out = []
        while st != -1:
            c = st // 4
            if not out or out[-1] != (c % W, c // W):
                out.append((c % W, c // W))
            st = prev.get(st, -1)
        return out[::-1]

    @contextlib.contextmanager
    def no_gates(self):
        """Plan as if every door were two-way (the reading before one-way gates)."""
        saved, self.gate = self.gate, np.zeros_like(self.gate)
        saved_list, self._gate_list = self._gate_list, self._zero_list
        self._gates_off = True
        try:
            yield
        finally:
            self.gate = saved
            self._gate_list = saved_list
            self._gates_off = False

    ROUTE_CACHE_MAX = 512

    _route_lock_guard = threading.Lock()

    def _route_mutex(self):
        """One lock per model, made on first use (a copy from without() shares it)."""
        lock = self.__dict__.get("_route_lock")
        if lock is None:
            with MapModel._route_lock_guard:
                lock = self.__dict__.setdefault("_route_lock", threading.RLock())
        return lock

    def route(self, a, b, avoid_directional=False):
        """Path a -> b, obeying one-way gates. On the few maps where the gate
        reading cuts ground off (9 of 149, unverified), a target it can't
        reach is planned the old way, ignoring the gates - that path is
        returned but never cached, or every later call would ignore them too.

        Results are kept on the map object, keyed by both cells and the
        directional flag: patrol walks and travel ask for the same hops again
        and again. Least recently used paths go first (lru). The cached
        path is immutable; callers get their own list. A path planned without
        the gates is kept in a cache of its own (_route_loose), handed out only
        for the same question, which would plan it the same way again.

        no_gates() swaps the model's gate arrays, so the search runs under the
        model's lock: the farm thread and the message thread share a model."""
        a, b = tuple(a), tuple(b)
        with self._route_mutex():
            if self.__dict__.get("_gates_off"):
                return self._route(a, b, avoid_directional)   # already planned gate-less
            key = (a, b, avoid_directional)
            cache = self.__dict__.setdefault("_route_cache", {})
            loose = self.__dict__.setdefault("_route_loose", {})
            for kept in (cache, loose):
                hit: Any = lru.get(kept, key, _NO_ROUTE)
                if hit is not _NO_ROUTE:
                    return list(hit) if hit is not None else None
            p = self._route(a, b, avoid_directional)
            if p is None and self.gate.any():
                with self.no_gates():
                    p = self._route(a, b, avoid_directional)
                # kept apart: the gate-less answer is only ever handed out for this same question
                lru.put(loose, key, tuple(p) if p is not None else None, self.ROUTE_CACHE_MAX)
                return list(p) if p is not None else None
            lru.put(cache, key, tuple(p) if p is not None else None, self.ROUTE_CACHE_MAX)
            return list(p) if p is not None else None

    def _route(self, a, b, avoid_directional=False):
        """Path a -> b. If b is inside an exit, the path ends on whichever
        cell of that exit it reaches first - that step is the crossing."""
        e = self.exit_at(b)
        if self._c() is not None:
            if e is not None and self.exit_at(a) is not e:
                return self._astar_path(a, self.exit_cells_of(e), avoid_directional)
            return self._astar_path(a, [b], avoid_directional)
        if e is not None and self.exit_at(a) is not e:
            cells = self.exit_cells_of(e)
            _d, prev, reached = self.astar_rect(a, cells, avoid_directional)
            if not reached:
                return None
            k = next(iter(reached))
            return self.path_rect(prev, reached, (k % self.W, k // self.W))
        _d, prev, reached = self.astar_rect(a, [b], avoid_directional)
        return self.path_rect(prev, reached, b)

    def line(self, a, b):
        """4-connected supercover line from a to b (the cells a 4-way
        walker would touch following the straight line)."""
        x, y = a
        x1, y1 = b
        dx, dy = abs(x1 - x), abs(y1 - y)
        sx = 1 if x1 > x else -1
        sy = 1 if y1 > y else -1
        cells = [(x, y)]
        ix = iy = 0
        while ix < dx or iy < dy:
            # step along whichever axis the true line crosses next
            if iy >= dy or (ix < dx and (1 + 2 * ix) * dy < (1 + 2 * iy) * dx):
                x += sx
                ix += 1
            else:
                y += sy
                iy += 1
            cells.append((x, y))
        return cells

    def line_ok(self, a, b, min_clear=1, allow_directional=False):
        """Every cell of line(a, b) walkable, at least min_clear from a wall, off ledges (unless
        allow_directional) and through no one-way gate the wrong way."""
        lib = self._c()
        if lib is not None:
            try:
                ax, ay, bx, by = (operator.index(v) for v in (a[0], a[1], b[0], b[1]))
                mc = operator.index(min_clear)
            except TypeError:
                pass                    # fractional cells: the Python walk below handles them
            else:
                return bool(lib.uf_line_ok(self.W, len(self._walk_list), self.w, self.h, self._walk_list,
                                           self._clear_list, self._gate_list, self._direc_list,
                                           ax, ay, bx, by, max(-128, min(127, mc)), bool(allow_directional)))
        prev = None
        for x, y in self.line(a, b):
            if not self.ok(x, y) or self.clear[y, x] < min_clear:
                return False
            if not allow_directional and self.directional[y, x]:
                return False
            if prev is not None and self.gate_blocks(prev, (x, y)):
                return False
            prev = (x, y)
        return True

    def waypoints(self, path, max_hop=None):
        """The corners of a path: every hop between consecutive points is a
        pure east-west or north-south line, so the bot's one-axis-at-a-time
        walk follows it exactly whichever axis it would pick. The start
        cell is not included; the last cell always is."""
        if not path or len(path) < 2:
            return list(path[1:])
        out = []
        for i in range(1, len(path) - 1):
            a, b, c = path[i - 1], path[i], path[i + 1]
            if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
                out.append(b)
        out.append(path[-1])
        if max_hop:
            split, prev = [], path[0]
            for p in out:
                n = abs(p[0] - prev[0]) + abs(p[1] - prev[1])
                k = (n + max_hop - 1) // max_hop
                for s in range(1, k):
                    split.append((prev[0] + (p[0] - prev[0]) * s // k,
                                  prev[1] + (p[1] - prev[1]) * s // k))
                split.append(p)
                prev = p
            out = split
        return out

