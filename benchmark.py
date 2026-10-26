"""Benchmark the hot paths: routing, target selection, travel planning and shortloop building.

    python benchmark.py            # everything (about a minute)
    python benchmark.py --quick    # skip the slow shortloop builds

    set UNCLEFARMER_NO_NATIVE=1 && python benchmark.py     # the same with the Python searches only

Each line is the median / p95 / p99 over N repeats of one operation, in milliseconds, measured
with perf_counter_ns. Caches are cleared where the name says "cold"; "warm" lines measure the
answer that comes out of the cache the bot itself keeps between decisions.
"""
import os
import random
import statistics
import sys
import time
import tracemalloc

import numpy as np

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unclefarmer import atlas, atlaslib, shortloop, travel
from unclefarmer.engine.botparts.walking import WalkingMixin

MID = 1041            # Giant Rock: 400x400, open field, no gates worth caching around
CAVE = 1503           # a big, walled cave: what walk_dist's BFS is for
SMALL = 1047          # a small cave for shortloop's exact search
LARGE = 1503          # biggest cave shortloop still takes (just under MAX_CELLS)

RESULTS = []


def bench(name, fn, n=25, warmup=3):
    for _ in range(warmup):
        fn()
    times = []
    for _ in range(n):
        t0 = time.perf_counter_ns()
        fn()
        times.append((time.perf_counter_ns() - t0) / 1e6)
    times.sort()
    med = statistics.median(times)
    RESULT = {
        "name": name, "n": n, "med": med, "min": times[0], "max": times[-1],
        "p95": times[min(len(times) - 1, int(len(times) * 0.95))],
        "p99": times[min(len(times) - 1, int(len(times) * 0.99))],
    }
    RESULTS.append(RESULT)
    print("%-46s med=%9.3f ms  p95=%9.3f  p99=%9.3f  min=%8.3f  max=%9.3f" % (
        name, med, RESULT["p95"], RESULT["p99"], times[0], times[-1]))
    return med


def clear_routes(m):
    m.__dict__.pop("_route_cache", None)
    m.__dict__.pop("_route_loose", None)


def clear_plan():
    travel._plan_cache.clear()
    travel._steps_cache.clear()


class Walker(WalkingMixin):
    """The walking half of the bot, enough for walk_dist: no link, no map cache."""

    def __init__(self):
        self._dists = None


def decision_bench(mid, src):
    m = atlas.model(mid)
    w = Walker()
    src = m.nearest_walkable(src[0], src[1], 80) or src
    me = {"id": 1, "x": float(src[0]), "y": float(src[1]), "hp": 100.0, "maxhp": 100.0}
    snap = {"map": {"id": mid}, "self": me, "entities": []}
    near = [(x, y) for y in range(src[1] - 30, src[1] + 31)
            for x in range(src[0] - 30, src[0] + 31) if m.ok(x, y)]
    reach = random.Random(7).sample(near, min(30, len(near)))
    mobs = [{"id": i, "x": float(x) + 0.4, "y": float(y) + 0.4} for i, (x, y) in enumerate(reach)]
    behind = sum(1 for mob in mobs if not m.line_ok(src, (int(mob["x"]), int(mob["y"]))))
    print("      %d monsters within 30 cells on map %d (%dx%d, %d walkable), %d behind walls" % (
        len(mobs), mid, m.w, m.h, int(m.walk.sum()), behind))

    def select(reset):
        if reset:
            w._dists = None
        return min((w.walk_dist(snap, mob, limit=75), mob["id"]) for mob in mobs)

    bench("walk_dist x30, cold BFS (limit 75)", lambda: select(True), n=10)
    bench("walk_dist x30, warm field", lambda: select(False), n=25)
    walled = [mob for mob in mobs if not m.line_ok(src, (int(mob["x"]), int(mob["y"])))]
    if walled:
        mob = walled[0]
        bench("walk_dist one walled mob", lambda: w.walk_dist(snap, mob, limit=150), n=25)


def memory_bench():
    """What the map data holds: the geometry file, one map's rows, and the flat routing caches."""
    print("\n=== memory (what the map data holds) ===")
    started = not tracemalloc.is_tracing()
    if started:
        tracemalloc.start()
    try:
        tracemalloc.reset_peak()
        before = tracemalloc.get_traced_memory()[0]
        t0 = time.perf_counter_ns()
        names = atlaslib.map_names()
        dt = (time.perf_counter_ns() - t0) / 1e6
        after = tracemalloc.get_traced_memory()[0]
        print("%-46s %8.2f MiB  %8.1f ms  (%d maps)" % (
            "map names + line index (kept)", (after - before) / 2**20, dt, len(names)))
        atlaslib.rows_for.cache_clear()
        t0 = time.perf_counter_ns()
        rows = atlaslib.rows_for(MID)
        dt = (time.perf_counter_ns() - t0) / 1e6
        after = tracemalloc.get_traced_memory()[0]
        print("%-46s %8.2f MiB  %8.1f ms  (%d rows)" % (
            "rows_for(%d), one map (kept up to %d)" % (MID, atlaslib.GEOMETRY_CACHE),
            (after - before) / 2**20, dt, len(rows)))
        clear_routes(atlas.model(MID))
        m = atlas.model(MID)
        flat = sum(sys.getsizeof(getattr(m, n)) for n in (
            "_walk_list", "_clear_list", "_gate_list", "_portal_list", "_oneway_list", "_zero_list"))
        print("%-46s %8.2f MiB" % ("flat routing caches of one map, bytes", flat / 2**20))
        print("%-46s %8.2f MiB" % ("the same six as lists (if they were)", flat * 8 / 2**20))
        after = tracemalloc.get_traced_memory()[0]
        print("%-46s %8.2f MiB  (names + one painted map)" % ("traced total", after / 2**20))
    finally:
        if started:
            tracemalloc.stop()


def cache_bench():
    """Do the hand-rolled bounded dicts beat a real LRU container (cachebox)?"""
    print("\n=== cache containers: dict vs cachebox (ns/op, best of 3) ===")
    try:
        from cachebox import Cache, LRUCache
    except ImportError:
        print("      cachebox not installed - uv sync installs it (skipped)")
        return

    n = 300_000
    keys = [(str(i), i % 97, i % 13, i % 2) for i in range(512)]
    seq = [keys[i % len(keys)] for i in range(n)]
    path = [(i, i + 1) for i in range(100)]
    rnd = random.Random(7)
    skew = [(str(int(3000 * rnd.random() ** 2.2)), i % 97, i % 13, i % 2) for i in range(3000)]
    _cache_speeds(keys, seq, path, n, Cache, LRUCache)
    _cache_hit_rates(keys, skew, path, LRUCache)


def _per_op(fn, n):
    """ns per operation of fn's n operations, best of 3."""
    best = None
    for _ in range(3):
        t0 = time.perf_counter_ns()
        fn()
        dt = time.perf_counter_ns() - t0
        best = dt if best is None else min(best, dt)
    return best / n


def _cache_speeds(keys, seq, path, n, Cache, LRUCache):
    """ns per get or set: plain dicts against cachebox's containers."""
    def per_op(fn):
        return _per_op(fn, n)

    def dict_hits():
        d = {k: path for k in keys}
        get = d.get
        for k in seq:
            get(k)

    def dict_sets():
        d = {}
        for k in seq:
            d[k] = path
            if len(d) > len(keys):
                d.popitem()

    def lru_hits():
        c = LRUCache(len(keys))
        for k in keys:
            c[k] = path
        get = c.get
        for k in seq:
            get(k)

    def lru_sets():
        c = LRUCache(len(keys))
        for k in seq:
            c[k] = path

    def plain_hits():
        c = Cache(len(keys))
        for k in keys:
            c[k] = path
        get = c.get
        for k in seq:
            get(k)

    print("%-30s %7.1f ns" % ("dict, hit", per_op(dict_hits)))
    print("%-30s %7.1f ns" % ("dict, set + evict oldest", per_op(dict_sets)))
    print("%-30s %7.1f ns" % ("cachebox Cache, hit", per_op(plain_hits)))
    print("%-30s %7.1f ns" % ("cachebox LRUCache, hit", per_op(lru_hits)))
    print("%-30s %7.1f ns" % ("cachebox LRUCache, set (auto-evict)", per_op(lru_sets)))


def _cache_hit_rates(keys, skew, path, LRUCache):
    """How often a skewed trace finds its key: a dict working set against an LRU."""
    def hit_rate(put, get):
        for k in skew[:len(keys) // 2]:
            put(k, path)
        miss = 0
        for k in skew:
            if get(k) is None:
                put(k, path)
                miss += 1
        return 100.0 * (len(skew) - miss) / len(skew)

    d = {}
    print("%-30s %6.1f %%" % ("dict working set, skew trace", hit_rate(
        lambda k, v: d.__setitem__(k, v), d.get)))
    c = LRUCache(len(keys))
    print("%-30s %6.1f %%" % ("cachebox working set, skew trace", hit_rate(
        c.__setitem__, c.get)))


def walk_plan_bench():
    """What walk_to plans before every walk round a wall: route (ledges avoided first) then the
    two-key hops along it - cold, as on the first walk to a new cell, over 12 fixed cell pairs
    per map."""
    print("\n=== walk planning (route + diag_hops, cold) ===")
    for mid in (5302, 1503, 1001, MID):
        m = atlas.model(mid)
        ys, xs = m.walk.nonzero()
        big = max(range(1, len(m.comp_sizes)), key=lambda c: m.comp_sizes[c])
        cells = [c for c in zip(xs.tolist(), ys.tolist()) if m.comp_of(*c) == big]
        rnd = random.Random(1)
        pairs = [(rnd.choice(cells), rnd.choice(cells)) for _ in range(12)]

        def plan_all():
            clear_routes(m)
            m.__dict__.pop("_route_loose", None)
            for a, b in pairs:
                path = m.route(a, b, avoid_directional=True) or m.route(a, b)
                if path:
                    m.diag_hops(path)
        bench("map %d (%s), 12 walks" % (mid, m.name[:16]), plan_all, n=5, warmup=1)


def load_bench():
    """A map change: the map's rows and its model (walls, gates, regions, exits)."""
    print("\n=== map load (what a map change costs before the first decision) ===")
    for mid in (MID, 5302, 1001):
        def load():
            atlaslib.rows_for.cache_clear()
            return atlaslib.MapModel(mid)
        bench("model %d, rows read + built" % mid, load, n=5, warmup=1)
    t0 = time.perf_counter_ns()
    atlaslib._Geometry._index = None
    atlaslib._Geometry._scan()
    print("%-46s %9.3f ms  (once, at start)" % ("geometry index scan", (time.perf_counter_ns() - t0) / 1e6))


def link_bench():
    """One agent reply turned into Python objects: a 'mem state' answer of 30 monsters."""
    print("\n=== agent reply parsing ===")
    from unclefarmer import jsonio
    ents = [{"id": i, "kind": "npc", "name": "Wolf %d" % i, "x": 100.5 + i, "y": 80.25, "hp": 900,
             "maxhp": 1000, "level": 40, "target": 0} for i in range(30)]
    raw = jsonio.dumps({"ok": True, "map": {"id": 5121, "name": "Wolf Prairie"},
                        "self": {"id": 1, "x": 99.0, "y": 80.0, "hp": 5000, "maxhp": 5000},
                        "entities": ents}).encode("utf-8")
    print("      reply: %d bytes" % len(raw))
    bench("decode + parse (old send/json path)", lambda: jsonio.loads(raw.decode("utf-8")), n=2000)
    bench("parse bytes (new _ask/json path)", lambda: jsonio.loads(raw), n=2000)


def search_count_bench():
    """How much each C route search looks at: states settled and pushes (routing.c's stats)."""
    if atlaslib.NATIVE is None:
        return
    print("\n=== route search size (states settled / pushed, from routing.c) ===")
    m = atlas.model(MID)
    for name, a, b in (("near", (200, 200), (230, 225)), ("far", (200, 200), (60, 60))):
        clear_routes(m)
        m.route(a, b)
        ws = m._workspace(len(m._walk_list))
        print("%-46s %9d settled %9d pushed" % ("route %s %s -> %s" % (name, a, b), ws.stats[0], ws.stats[1]))


def route_cache_workload_bench():
    """The route cache on the bot's own traffic: patrol laps walked hop by hop, as walk_to plans
    them (round ledges first, then over them). Exact: every hop starts where the last one ended.
    Real: the character stops within a cell or so of a hop's end, so the start moves by -1..1."""
    print("\n=== route cache on patrol traffic (5 laps of each map's short loop) ===")
    rnd = random.Random(3)
    for mid in (5302, 1503, 1001):
        m = atlas.model(mid)
        loop = atlas.patrol_loop(mid, "short") or []
        if len(loop) < 3:
            continue
        for jitter in (0, 1):
            clear_routes(m)
            asked = searched = 0
            real = m._route

            def counting(a, b, avoid_directional=False):
                nonlocal searched
                searched += 1
                return real(a, b, avoid_directional)
            m._route = counting
            t0 = time.perf_counter_ns()
            try:
                for _lap in range(5):
                    here = loop[-1]
                    for hop in loop:
                        start = (here[0] + rnd.randint(-jitter, jitter), here[1] + rnd.randint(-jitter, jitter))
                        if not m.ok(*start):
                            start = here
                        asked += 1
                        m.route(start, hop, avoid_directional=True) or m.route(start, hop)
                        here = hop
            finally:
                del m._route
            ms = (time.perf_counter_ns() - t0) / 1e6
            print("%-46s %5d hops, %5.1f %% cache hits, %8.2f ms in all" % (
                "map %d, %s starts" % (mid, "real" if jitter else "exact"), asked,
                100.0 * (1 - searched / max(1, asked)), ms))


def lru_cache_bench():
    """The three ways a cache hit could be served, on the route cache's key shape: the plain dict
    (what the caches were), lru.get (what they are: a hit moves the entry to the end) and
    functools.lru_cache around the same lookup."""
    import functools
    from unclefarmer import lru
    print("\n=== cache hit cost: dict vs lru.get vs functools.lru_cache (ns/op, best of 3) ===")
    n = 300_000
    keys = [((i % 97, i % 89), (i % 83, i % 79), bool(i % 2)) for i in range(512)]
    seq = [keys[i % len(keys)] for i in range(n)]
    path = tuple((i, i + 1) for i in range(60))
    d = {k: path for k in keys}

    def dict_hits():
        get = d.get
        for k in seq:
            get(k)

    def lru_hits():
        for k in seq:
            lru.get(d, k)

    @functools.lru_cache(maxsize=512)
    def cached(k):
        return path

    for k in keys:
        cached(k)

    def functools_hits():
        for k in seq:
            cached(k)
    for name, fn in (("dict.get", dict_hits), ("lru.get", lru_hits), ("functools.lru_cache", functools_hits)):
        print("%-30s %7.1f ns" % (name, _per_op(fn, n)))


def numba_bench():
    """The same whole-map dijkstra JIT-compiled by numba, against the C one: compile time on the
    first call (every app start would pay it) and the steady run."""
    try:
        import numba
    except ImportError:
        print("\n(numba not installed: its comparison skipped)")
        return
    if atlaslib.NATIVE is None:
        return
    import heapq as hq

    @numba.njit(cache=False)
    def nb_dijkstra(W, walk, clear, gate, pen, src, dist):
        n = walk.size
        for i in range(n):
            dist[i] = np.inf
        done = np.zeros(n, np.uint8)
        offs = np.array([1, -1, W, -W])
        bits = np.array([4, 1, 8, 2])
        dist[src] = 0.0
        heap = [(0.0, src)]
        while heap:
            d, c = hq.heappop(heap)
            if done[c]:
                continue
            done[c] = 1
            for j in range(4):
                nb = c + offs[j]
                if nb < 0 or nb >= n or not walk[nb] or done[nb] or (gate[c] & bits[j]):
                    continue
                nd = d + 1.0 + pen[clear[nb]]
                if nd < dist[nb]:
                    dist[nb] = nd
                    hq.heappush(heap, (nd, nb))
        return dist

    print("\n=== numba against C: whole-map dijkstra, map %d ===" % MID)
    m = atlas.model(MID)
    walk = np.frombuffer(m._walk_list, dtype=np.uint8)
    clear = np.frombuffer(m._clear_list, dtype=np.uint8)
    gate = np.frombuffer(m._gate_list, dtype=np.uint8)
    pen = np.array(m._pen_arr, dtype=np.float64)
    src = 200 * m.W + 200
    out = np.empty(walk.size)
    t0 = time.perf_counter_ns()
    nb_dijkstra(m.W, walk, clear, gate, pen, src, out)
    print("%-46s %9.1f ms  (numba %s, once per run)" % ("numba compile + first call", (time.perf_counter_ns() - t0) / 1e6,
                                                        numba.__version__))
    c_dist = m.dijkstra_arr((200, 200))[0]
    print("      same distances as C: %s" % bool(np.array_equal(out, c_dist)))
    bench("numba dijkstra, full", lambda: nb_dijkstra(m.W, walk, clear, gate, pen, src, out), n=10)
    bench("C dijkstra_arr, full", lambda: m.dijkstra_arr((200, 200)), n=10)


def main():
    quick = "--quick" in sys.argv
    try:
        from unclefarmer import native
    except ImportError:            # a checkout from before the C searches: the baseline
        print("searches: Python (no native module in this checkout)")
    else:
        print("searches: %s" % ("C (%s)" % os.path.basename(native.dll_path() or "")
                                if atlaslib.NATIVE is not None else "Python (%s)" % native.why_not))
    m = atlas.model(MID)
    print("map %d: %dx%d, %d walkable cells" % (MID, m.w, m.h, int(m.walk.sum())))
    src, tgt = (200, 200), (100, 100)

    print("\n=== atlaslib routing (map %d) ===" % MID)
    bench("dijkstra, full, 1 target", lambda: m.dijkstra(src, [tgt]))
    bench("dijkstra, full, 3 targets", lambda: m.dijkstra(src, [(100, 100), (50, 50), (300, 300)]))
    bench("dijkstra, limit 150, 3 targets",
          lambda: m.dijkstra(src, [(x, y) for x, y in ((100, 100), (250, 300), (300, 300))], limit=150))
    bench("dijkstra, limit 25, 3 targets",
          lambda: m.dijkstra(src, [(x, y) for x, y in ((215, 210), (190, 230), (230, 190))], limit=25))
    bench("steps dict, full", lambda: m.steps(src))
    bench("steps dict, limit 150", lambda: m.steps(src, limit=150))
    bench("steps_arr, full", lambda: m.steps_arr(src))
    bench("steps_arr, limit 150", lambda: m.steps_arr(src, limit=150))
    bench("steps_arr, limit 40", lambda: m.steps_arr(src, limit=40))
    bench("astar_rect, 1 target", lambda: m.astar_rect(src, [tgt]))
    bench("nearest_walkable, 40", lambda: m.nearest_walkable(50, 50, 40))
    bench("nearest_floor, 3", lambda: m.nearest_floor(50, 50, 3))
    bench("two_way", lambda: m.two_way(200, 200))
    bench("diag_ok", lambda: m.diag_ok(src, tgt))
    bench("line", lambda: m.line(src, tgt))

    print("\n=== route cache (map %d) ===" % MID)
    near, far = (230, 225), (60, 60)

    def cold_route(a, b):
        def once():
            clear_routes(m)
            return m.route(a, b)
        return once

    def hot_route(a, b):
        def once():
            m.route(a, b)
            return m.route(a, b)
        return once

    bench("route near, cold", cold_route(src, near))
    bench("route far, cold", cold_route(src, far))
    bench("route far x2, miss then hit", hot_route(src, far))

    print("\n=== target selection (walk_dist, the farm loop's question) ===")
    decision_bench(CAVE, (src[0], src[1]))
    decision_bench(MID, src)

    print("\n=== travel planning ===")
    clear_plan()
    bench("plan cross map, all caches cold",
          lambda: (clear_plan(), travel.plan(MID, src, 1021, (73, 286))), n=5)
    bench("plan cross map, replan (new start cell)",
          lambda: (travel._plan_cache.clear(), travel.plan(MID, (src[0] + 1, src[1]), 1021,
                                                           (73, 286))), n=10)
    bench("plan cross map, same question cached",
          lambda: travel.plan(MID, src, 1021, (73, 286)))
    bench("plan far, replan (new start cell)",
          lambda: (travel._plan_cache.clear(), travel.plan(MID, (src[0] + 2, src[1]), 1181,
                                                           (225, 230))), n=10)
    bench("plan far, same question cached",
          lambda: travel.plan(MID, src, 1181, (225, 230)))
    bench("steps_from 1 target, cold",
          lambda: (travel._steps_cache.clear(), travel._steps_from(MID, src, [tgt])), n=10)
    bench("steps_from 1 target, cached", lambda: travel._steps_from(MID, src, [tgt]))
    bench("plan same map, 1 leg", lambda: travel.plan(MID, src, MID, tgt))

    walk_plan_bench()
    search_count_bench()
    route_cache_workload_bench()
    load_bench()
    link_bench()
    cache_bench()
    lru_cache_bench()
    memory_bench()
    numba_bench()

    if quick:
        print("\n(--quick: shortloop builds skipped)")
        return
    print("\n=== shortloop build (offline data generator, not on the bot's path) ===")
    bench("small cave %d build" % SMALL, lambda: shortloop.build(SMALL), n=8)
    bench("large cave %d build" % LARGE, lambda: shortloop.build(LARGE), n=3)


if __name__ == "__main__":
    main()
