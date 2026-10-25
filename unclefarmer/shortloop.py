"""Short patrol loops: the shortest lap that keeps nearly all of a map within chasing distance.

The full and quick loops in farms.json walk close to every cell (turkey cave: 1408 steps, every
cell within 17). A farming bot doesn't need that: anything within CHASE walking steps of the lap
is fought on the way past (bot.farm chases what comes that close). So this keeps COVER of the
map within CHASE steps of the lap and makes the lap as short as it can - turkey cave: 452 steps.

    1. stops   greedy cover: stops until every cell is within STOP_R steps of one
    2. order   nearest neighbour, then 2-opt, on real walking distances (walls, one-way doors)
    3. prune   drop the stop that saves the most walking while the lap still covers COVER

Built offline into data/short_loops.json (python -m unclefarmer.shortloop); maps with more than
MAX_CELLS walkable cells are left out - open ground is farmed better by wandering.
"""

import math
import os
import sys
import time

import numpy as np

from . import atlas, jsonio as json, paths

CHASE = 40          # bot.farm's default chase_range: the lap must keep the map this close
COVER = 0.95        # share of the map's walkable cells that must be within CHASE of the lap
STOP_R = 25
MAX_CELLS = 30000
OUT = os.path.join(paths.DATA, "short_loops.json")


def build(mid):
    """{"loop": ["x|y", ...], "steps": n, "covered": pct} for the map, or None."""
    m = atlas.model(mid)
    f = atlas.farms().get(int(mid), {}).get("variants") or {}
    start, walkable, cells = _ground(m, mid, f)
    if len(cells) < 50 or len(walkable) > MAX_CELLS:
        return None
    lb = _Laps(m, cells)
    stops = _cover(m, cells, walkable)
    lb.measure(stops)
    order = lb.nearest_first(stops, start)
    if lb.lap(order) is None:
        return None
    order = lb.prune(order)
    path = lb.lap(order)
    if path is None:
        return None
    pts = m.waypoints(path, max_hop=20)
    out = {"loop": ["%d|%d" % p for p in pts], "steps": len(path) - 1,
           "covered": round(100 * lb.covered(path), 1), "stops": len(order)}
    # a ready loop that is shorter still (small maps) is the short one
    for name, v in sorted(f.items(), key=lambda kv: kv[1].get("steps") or 1e9):
        if v.get("loop") and not v.get("bad_hops") and (v.get("steps") or 1e9) < out["steps"]:
            return {"loop": v["loop"], "steps": v["steps"], "covered": None, "from": name}
    return out


def _ground(m, mid, f):
    """(start, walkable, cells): where the lap starts, the ground we can wander and always walk
    back from (one-way doors), off every exit, and the part of it that must be kept near - where
    monsters spawn (as for the full and quick loops)."""
    W = m.W
    start = next((tuple(v["start"]) for v in f.values() if v.get("start")), None)
    ys, xs = np.nonzero(m.walk)
    if start is None or not m.ok(*start):
        sizes = m.comp_sizes
        big = max(range(1, len(sizes)), key=lambda c: sizes[c])
        start = next((int(x), int(y)) for x, y in zip(xs, ys) if m.comp_of(int(x), int(y)) == big)
    ground = m.two_way(*start)
    walkable = [(int(x), int(y)) for x, y in zip(xs, ys)
                if y * W + x in ground and not m.portal[y, x]]
    rects = [z["rect"] for z in atlas.zones(mid, ("monster",))]
    cells = [c for c in walkable if not rects
             or any(r[0] <= c[0] <= r[2] and r[1] <= c[1] <= r[3] for r in rects)] or walkable
    return start, walkable, cells


def _cover(m, cells, walkable):
    """Greedy cover: stops until every cell is within STOP_R steps of one. It only needs what is
    within STOP_R of a stop: a search bounded to that radius costs 1-2 ms where the whole-map
    search costs 70 ms on a big cave (299 candidates there, 21 s of the build). The distances are
    the same inside the radius."""
    W = m.W
    step = max(5, int((len(walkable) / 300) ** 0.5))
    cand = [c for c in walkable if c[0] % step == 0 and c[1] % step == 0] or walkable[::step]
    if hasattr(m, "dijkstra_arr"):
        return _cover_matrix(m, cells, cand)
    need = {y * W + x for x, y in cells}
    near = {c: {k for k, d in m.dijkstra(c, limit=STOP_R)[0].items() if d <= STOP_R} & need
            for c in cand}
    stops = []
    while need:
        best = max(cand, key=lambda c: len(near[c] & need))
        got = near[best] & need
        if not got:
            break
        stops.append(best)
        need -= got
    return stops


def _cover_matrix(m, cells, cand):
    """_cover's greedy cover as one boolean matrix, candidates by cells: each round's "which
    candidate covers the most of what is still needed" is one matrix-vector product instead of a
    set intersection per candidate. The same stops in the same order: argmax takes the first
    of equal counts, as max() over the candidates does."""
    W = m.W
    ids = np.array(sorted({y * W + x for x, y in cells}), dtype=np.intp)
    col = np.full(m.walk.size, -1, dtype=np.intp)
    col[ids] = np.arange(ids.size)
    near = np.zeros((len(cand), ids.size), dtype=bool)
    for i, c in enumerate(cand):
        dist, _prev = m.dijkstra_arr(c, limit=STOP_R)
        hit = col[np.flatnonzero(dist <= STOP_R)]
        near[i, hit[hit >= 0]] = True
    counts_of = near.astype(np.int32)
    need = np.ones(ids.size, dtype=bool)
    stops = []
    while need.any():
        counts = counts_of @ need.astype(np.int32)
        best = int(np.argmax(counts))
        if not counts[best]:
            break
        stops.append(cand[best])
        need &= ~near[best]
    return stops


class _Laps:
    """Ordering and pruning the stops: walking distances between them (one full distance field
    per chosen stop, about 20-40 of them, not one per candidate), routes obeying one-way doors,
    and how much of the map a lap keeps within CHASE."""

    def __init__(self, m, cells):
        self.m, self.W, self.cells = m, m.W, cells
        self.dists = {}
        self.routes = {}
        self.cell_ids = np.array([y * m.W + x for x, y in cells], dtype=np.intp)
        self.walk_flat = m.walk.ravel()

    def measure(self, stops):
        """Walking distances between every two stops (1e9: no way), from one whole-map search per
        stop. Only stop-to-stop distances are ever asked for, so they are kept as a plain dict
        instead of the searches themselves."""
        W = self.W
        self.dists = {}
        for a in stops:
            if hasattr(self.m, "dijkstra_arr"):
                dist = self.m.dijkstra_arr(a)[0]
                row = {b: float(dist[b[1] * W + b[0]]) for b in stops}
                row = {b: d if d != math.inf else 1e9 for b, d in row.items()}
            else:
                field = self.m.dijkstra(a)[0]
                row = {b: field.get(b[1] * W + b[0], 1e9) for b in stops}
            for b, d in row.items():
                self.dists[a, b] = d

    def wd(self, a, b):
        return self.dists[a, b]

    def route(self, a, b):
        if (a, b) not in self.routes:
            self.routes[a, b] = self.m._route(a, b)       # obeying one-way doors: no fallback
        return self.routes[a, b]

    def two_opt(self, o):
        wd = self.wd
        better = True
        while better:
            better = False
            for i in range(1, len(o) - 1):
                for j in range(i + 1, len(o)):
                    a, b, c, d = o[i - 1], o[i], o[j], o[(j + 1) % len(o)]
                    if wd(a, c) + wd(b, d) < wd(a, b) + wd(c, d) - 0.5:
                        o[i:j + 1] = o[i:j + 1][::-1]
                        better = True
        return o

    def lap(self, o):
        path = []
        for a, b in zip(o, o[1:] + o[:1]):
            p = self.route(a, b)
            if not p:
                return None
            path += p[1:] if path else p
        return path

    def covered(self, path):
        """The share of the spawn cells within CHASE steps of the lap. Layered BFS over numpy
        arrays from every lap cell at once: the pruning loop asks this 50-100 times, and a Python
        BFS each time was a third of the build."""
        if not path:
            return 0.0
        if hasattr(self.m, "multi_steps"):
            seen = self.m.multi_steps(path, limit=CHASE)
            return float((seen[self.cell_ids] >= 0).sum()) / len(self.cells)
        W, walk_flat = self.W, self.walk_flat
        seen = np.full(walk_flat.size, -1, dtype=np.int32)
        seeds = sorted({p[1] * W + p[0] for p in path})
        seen[seeds] = 0
        frontier = np.array(seeds, dtype=np.intp)
        d = 0
        while frontier.size and d < CHASE:
            d += 1
            rings = []
            for off in (1, -1, W, -W):
                nb = frontier + off
                nb = nb[walk_flat[nb] & (seen[nb] < 0)]
                seen[nb] = d
                rings.append(nb)
            frontier = np.concatenate(rings)
        return float((seen[self.cell_ids] >= 0).sum()) / len(self.cells)

    def nearest_first(self, stops, start):
        """Nearest neighbour from the stop nearest the start, then 2-opt."""
        left = list(stops)
        order = [min(left, key=lambda c: abs(c[0] - start[0]) + abs(c[1] - start[1]))]
        left.remove(order[0])
        while left:
            n = min(left, key=lambda c: self.wd(order[-1], c))
            left.remove(n)
            order.append(n)
        return self.two_opt(order)

    def saving(self, o, i):
        a, s, b = o[i - 1], o[i], o[(i + 1) % len(o)]
        return self.wd(a, s) + self.wd(s, b) - self.wd(a, b)

    def prune(self, order):
        """Drop the stop that saves the most walking while the lap still covers COVER, again and
        again."""
        pruned = True
        while pruned and len(order) > 3:
            pruned = False
            for i in sorted(range(len(order)), key=lambda i: -self.saving(order, i)):
                trial = self.two_opt(order[:i] + order[i + 1:])
                path = self.lap(trial)
                if path and self.covered(path) >= COVER:
                    order, pruned = trial, True
                    break
        return order


def main(argv):
    only = [int(a) for a in argv if a.isdigit()]
    out = {}
    if os.path.exists(OUT):
        with open(OUT, encoding="utf-8") as f:
            out = json.load(f)
    todo = only or sorted(atlas.farms())
    for mid in todo:
        if str(mid) in out and not only:
            continue
        t = time.perf_counter()          # developer CLI timing, not the app clock
        try:
            r = build(mid)
        except Exception as e:          # a map the atlas can't model
            # Developer-only generator: report this map and keep building the rest. carry_on()
            # would abort the whole batch, which is not useful when regenerating the data.
            print(mid, "failed:", e, flush=True)
            continue
        if r and len(r["loop"]) >= 2:      # a single spot is no lap: wander there instead
            out[str(mid)] = r
            # An id given on the command line need not be in farms.json (a map nobody farms yet):
            # say the full loop is unknown instead of raising KeyError and stopping the run.
            farm = atlas.farms().get(mid) or {}
            full = (farm.get("variants") or {}).get("full", {}).get("steps")
            print("%d %s: %d steps (full loop %s), %s%% covered, %.0fs %s" % (
                mid, atlas.name(mid), r["steps"], full, r["covered"], time.perf_counter() - t,
                r.get("from", "")), flush=True)
        with open(OUT + ".tmp", "w", encoding="utf-8") as f:
            json.dump(out, f, indent=0, sort_keys=True)
        os.replace(OUT + ".tmp", OUT)


if __name__ == "__main__":
    main(sys.argv[1:])
