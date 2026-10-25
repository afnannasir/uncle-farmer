"""The C map searches (unclefarmer/native/routing.c) against the Python ones they replace.

Every C search must give exactly what its Python body gives - the same costs, the same paths (ties
between equal routes broken the same way), the same fields, the same yes or no - so these tests
run both on real maps (turkey cave's one-way gates, a village's exits, an open field) and compare.
They also check the DLL is the one built from routing.c, and that the searches fall back to
Python where the C ones can't run.
"""

import ctypes
import random
import threading

import numpy as np
import pytest

from unclefarmer import atlas, atlaslib, lru, native, shortloop, travel

C = atlaslib.NATIVE
needs_c = pytest.mark.skipif(C is None, reason="native searches not loaded: " + native.why_not)

GATES = 5302          # turkey cave: a maze of one-way gates
EXITS = 1001          # Karon Village: exits, gates, town zones
CAVE = 1503           # a walled cave without gates
FIELD = 1041          # central plains: open ground, 150,000 cells


def python(monkeypatch_or_none, fn):
    """fn() with the C searches switched off."""
    saved = atlaslib.NATIVE
    atlaslib.NATIVE = None
    try:
        return fn()
    finally:
        atlaslib.NATIVE = saved


def walkable(m):
    ys, xs = m.walk.nonzero()
    return list(zip(xs.tolist(), ys.tolist()))


def test_the_dll_is_built_from_this_routing_c():
    # A routing.c edited without `python build_native.py` would quietly run the Python searches.
    assert C is not None, native.why_not
    assert (native.dll_path() or "").endswith("_routing-%s%s" % (native.source_hash()[:12], native.EXT))


def test_a_dll_from_another_routing_c_is_refused(tmp_path):
    other = tmp_path / "routing.c"
    other.write_text("/* not the source the DLL was built from */\n")
    assert native.load(native.dll_path(), source=str(other)) is None
    assert "another routing.c" in native.why_not
    assert native.load(str(tmp_path / "missing.dll")) is None
    assert "not built" in native.why_not
    native.why_not = ""


def test_the_searches_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("UNCLEFARMER_NO_NATIVE", "1")
    assert native.load() is None
    assert "turned off" in native.why_not
    native.why_not = ""


def test_a_release_without_routing_c_takes_the_dll_beside_it(tmp_path):
    (tmp_path / ("_routing-aaa" + native.EXT)).write_bytes(b"")
    found = native.dll_path(source=str(tmp_path / "routing.c"), folder=str(tmp_path))
    assert found is not None and found.endswith("_routing-aaa" + native.EXT)
    assert native.dll_path(source=str(tmp_path / "routing.c"), folder=str(tmp_path / "none")) is None


@needs_c
@pytest.mark.slow
@pytest.mark.parametrize("mid", [GATES, EXITS, CAVE])
def test_dijkstra_is_the_same_search(mid):
    m = atlas.model(mid)
    cells = walkable(m)
    rnd = random.Random(mid)
    for _ in range(8):
        a = rnd.choice(cells)
        targets = [rnd.choice(cells) for _ in range(rnd.randint(0, 3))]
        limit = rnd.choice([None, 0, 7, 25.5, 150])
        avoid = rnd.random() < 0.4
        got = m.dijkstra(a, targets, avoid, limit)
        assert got == python(None, lambda: m.dijkstra(a, targets, avoid, limit)), (a, targets, limit)
        dist, prev = m.dijkstra_arr(a, targets, avoid, limit)
        pdist, pprev = python(None, lambda: m.dijkstra_arr(a, targets, avoid, limit))
        assert np.array_equal(dist, pdist) and np.array_equal(prev, pprev)


@needs_c
def test_dijkstra_with_a_target_off_the_map_searches_everything(monkeypatch):
    m = atlas.model(CAVE)
    a = walkable(m)[0]
    off = [(-5, -5), a]                     # in Python's set, never settled: no early stop
    assert m.dijkstra(a, off) == python(None, lambda: m.dijkstra(a, off))


@needs_c
@pytest.mark.slow
@pytest.mark.parametrize("mid", [GATES, EXITS, CAVE, FIELD])
def test_routes_are_the_same_paths(mid):
    m = atlas.model(mid)
    cells = walkable(m)
    rnd = random.Random(7 + mid)
    jobs = [(rnd.choice(cells), rnd.choice(cells)) for _ in range(6 if mid == FIELD else 15)]
    jobs += [(rnd.choice(cells), tuple(e["stand"])) for e in m.exits[:6] if e.get("stand")]
    jobs += [(tuple(e["stand"]), rnd.choice(cells)) for e in m.exits[:3] if e.get("stand")]
    for a, b in jobs:
        for avoid in (False, True):
            assert m._route(a, b, avoid) == python(None, lambda: m._route(a, b, avoid)), (a, b, avoid)
            if m.gate.any():
                with m.no_gates():
                    assert m._route(a, b, avoid) == python(None, lambda: m._route(a, b, avoid))


@needs_c
@pytest.mark.slow
@pytest.mark.parametrize("mid", [GATES, EXITS])
def test_steps_two_way_and_components_are_the_same(mid):
    m = atlas.model(mid)
    cells = walkable(m)
    rnd = random.Random(3)
    for _ in range(6):
        a = rnd.choice(cells)
        for limit in (None, 0, 3, 40, 12.5):
            assert np.array_equal(m.steps_arr(a, limit), python(None, lambda: m.steps_arr(a, limit)))
        lap = [rnd.choice(cells) for _ in range(5)]
        assert np.array_equal(m.multi_steps(lap, 40), python(None, lambda: m.multi_steps(lap, 40)))
        saved, m._two_way = getattr(m, "_two_way", []), []
        try:
            area = m.two_way(*a)
            m._two_way = []
            assert area == python(None, lambda: m.two_way(*a))
        finally:
            m._two_way = saved
    comp, sizes = m.comp, m.comp_sizes
    try:
        python(None, m._components)
        assert (m.comp, m.comp_sizes) == (comp, sizes)
    finally:
        m.comp, m.comp_sizes = comp, sizes


@needs_c
@pytest.mark.parametrize("mid", [GATES, EXITS, CAVE])
def test_line_and_diagonal_checks_are_the_same(mid):
    m = atlas.model(mid)
    cells = walkable(m)
    rnd = random.Random(11)
    for _ in range(300):
        p = rnd.choice(cells)
        q = rnd.choice(cells) if rnd.random() < 0.5 else (p[0] + rnd.randint(-15, 15), p[1] + rnd.randint(-15, 15))
        min_clear, ledges = rnd.choice([1, 2, 3, 0, 99]), rnd.random() < 0.5
        assert m.line_ok(p, q, min_clear, ledges) == python(None, lambda: m.line_ok(p, q, min_clear, ledges)), (p, q)
        assert m.diag_ok(p, q) == python(None, lambda: m.diag_ok(p, q)), (p, q)
    for _ in range(10):
        path = m._route(rnd.choice(cells), rnd.choice(cells))
        if path:
            assert m.diag_hops(path) == python(None, lambda: m.diag_hops(path))
            assert m.diag_hops(path, reach=20, stride=3) == python(None, lambda: m.diag_hops(path, 20, 3))


def test_the_tiny_map_answers_the_same_both_ways(monkeypatch):
    rows = [_row(105, 1, 1, 6, 5), _row(200, 3, 1, 3, 5), _row(301, 3, 3, 3, 3, 4.0),
            _row(333, 2, 2, 2, 2, 8.0), _row(1041101, 6, 1, 6, 1, z=-1.0)]
    monkeypatch.setattr(atlaslib, "all_rows", lambda: {9999: rows})
    m = atlaslib.MapModel(9999)
    for a in [(1, 1), (2, 3), (4, 3), (6, 1)]:
        for b in [(1, 5), (4, 3), (6, 1), (5, 5)]:
            assert m._route(a, b) == python(None, lambda: m._route(a, b))
            assert m.dijkstra(a, [b]) == python(None, lambda: m.dijkstra(a, [b]))
            assert m.line_ok(a, b) == python(None, lambda: m.line_ok(a, b))
            assert m.diag_ok(a, b) == python(None, lambda: m.diag_ok(a, b))


def _row(t, x1, y1, x2, y2, z=0.0):
    return {"map_id": "9999", "map_name": "Test Land", "type_id": str(t), "blocks": "",
            "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2), "label": "", "z": str(z)}


@needs_c
def test_fractional_cells_are_left_to_the_python_searches():
    m = atlas.model(CAVE)
    a = walkable(m)[0]
    half = (a[0] + 0.5, a[1])
    assert m.line_ok(a, half) == python(None, lambda: m.line_ok(a, half))
    assert m.dijkstra(a, [half]) == python(None, lambda: m.dijkstra(a, [half]))


def test_a_two_key_walk_to_a_fractional_cell_ends():
    # diag_steps stepped one cell at a time towards x.5 and never got there: the list of steps grew
    # until the machine ran out of memory. It works on whole cells now, in Python and in C alike.
    m = atlas.model(CAVE)
    a = walkable(m)[0]
    half = (a[0] + 2.5, a[1] + 0.5)
    whole = (a[0] + 2, a[1])
    assert m.diag_steps(a, half) == m.diag_steps(a, whole)
    assert m.diag_ok(a, half) == m.diag_ok(a, whole) == python(None, lambda: m.diag_ok(a, half))
    assert m.diag_hops([a, half]) == python(None, lambda: m.diag_hops([a, half])) == [half]


@needs_c
def test_a_search_from_off_the_grid_is_left_to_python():
    m = atlas.model(CAVE)
    far = (m.W * 3, m.h * 3)
    with pytest.raises(IndexError):
        m.dijkstra(far)                      # what the Python search does there
    assert m._dijkstra_c(far, (), False, None) is None


@needs_c
def test_the_route_workspace_starts_over_when_its_stamps_run_out():
    m = atlas.model(GATES)
    cells = walkable(m)
    rnd = random.Random(1)
    jobs = [(rnd.choice(cells), rnd.choice(cells)) for _ in range(6)]
    want = [python(None, lambda: m._route(a, b)) for a, b in jobs]
    ws = m._workspace(len(m._walk_list))
    ws.stamp = ws.STAMP_MAX - 2                # the next searches cross the wrap
    assert [m._route(a, b) for a, b in jobs] == want
    assert ws.stamp < 10


@needs_c
@pytest.mark.slow
def test_threads_routing_on_one_map_get_their_own_answers():
    m = atlas.model(EXITS)
    cells = walkable(m)
    rnd = random.Random(2)
    jobs = [(rnd.choice(cells), rnd.choice(cells)) for _ in range(12)]
    want = [m._route(a, b) for a, b in jobs]
    got, errors = {}, []

    def worker(k):
        try:
            for _ in range(3):
                got[k] = [m._route(a, b) for a, b in jobs[k::4]]
        except Exception as e:           # pragma: no cover - reported by the assert
            errors.append(e)

    threads = [threading.Thread(target=worker, args=(k,)) for k in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert not errors
    for k in range(4):
        assert got[k] == want[k::4]


@needs_c
def test_a_c_search_out_of_memory_is_an_error(monkeypatch):
    m = atlas.model(CAVE)

    class NoMemory:
        def __getattr__(self, name):
            return lambda *a: -1

    monkeypatch.setattr(atlaslib, "NATIVE", NoMemory())
    a = walkable(m)[0]
    with pytest.raises(MemoryError):
        m.dijkstra_arr(a)
    with pytest.raises(MemoryError):
        m.steps_arr(a)
    with pytest.raises(MemoryError):
        m._astar_path(a, [walkable(m)[5]])


def test_a_stand_in_model_without_flat_bytes_uses_python():
    m = atlaslib.MapModel.__new__(atlaslib.MapModel)
    assert m._c() is None


@pytest.mark.parametrize("mid", [EXITS, GATES, FIELD])
def test_exit_stand_cells_are_the_nearest_in_x_then_y_order(mid):
    m = atlas.model(mid)
    for e in m.exits + m.towns:
        cells = [(x, y) for x in range(e["x1"], e["x2"] + 1) for y in range(e["y1"], e["y2"] + 1) if m.ok(x, y)]
        if cells:
            want = min(cells, key=lambda c: abs(c[0] - e["cx"]) + abs(c[1] - e["cy"]))
            assert e["stand"] == want and e["stand_inside"]
        else:
            assert not e["stand_inside"]


def test_rows_of_a_map_late_in_the_file_are_read_by_offset():
    import csv
    with atlaslib.GEOMETRY.open(encoding="utf-8-sig", newline="") as fh:
        wanted = [r for r in csv.DictReader(fh) if r["map_id"] == str(GATES)]
    atlaslib.rows_for.cache_clear()
    assert list(atlaslib.rows_for(GATES)) == wanted
    assert atlaslib.rows_for(424242) == ()


def test_lru_drops_the_least_recently_used():
    c = {}
    for k in "abc":
        lru.put(c, k, k.upper(), 3)
    assert lru.get(c, "a") == "A"            # a is now the most recent
    lru.put(c, "d", "D", 3)
    assert list(c) == ["c", "a", "d"]        # b, the least recently used, went
    assert lru.get(c, "b", "none") == "none"


def test_the_route_cache_keeps_hot_routes_past_its_size(monkeypatch):
    m = atlas.model(CAVE)
    monkeypatch.setattr(m, "ROUTE_CACHE_MAX", 4)
    m._route_cache = {}
    cells = walkable(m)
    hot = (cells[0], cells[50])
    m.route(*hot)
    for k in range(1, 12):
        m.route(cells[0], cells[50 + k])
        m.route(*hot)                       # asked between every new route: never the oldest
        assert (hot[0], hot[1], False) in m._route_cache
    assert len(m._route_cache) == 4


def test_an_unreachable_route_on_a_gated_map_is_searched_once(monkeypatch):
    m = atlas.model(GATES)
    m._route_cache, m._route_loose = {}, {}
    calls = []
    real = m._route

    def counting(a, b, avoid_directional=False):
        calls.append(1)
        return real(a, b, avoid_directional)

    monkeypatch.setattr(m, "_route", counting)
    a, nowhere = walkable(m)[0], (1, 1)       # (1, 1) is the void border
    assert m.route(a, nowhere) is None
    assert m.route(a, nowhere) is None
    assert len(calls) == 2                    # gated, then without gates; the second ask is cached


def test_shortloop_cover_and_distances_match_the_set_based_ones():
    m = atlas.model(1047)
    f = atlas.farms().get(1047, {}).get("variants") or {}
    _start, walk, cells = shortloop._ground(m, 1047, f)

    class DictsOnly:
        """The model without its array searches: shortloop's original set and dict code."""
        def __init__(self, m):
            self._m = m

        def __getattr__(self, name):
            if name in ("dijkstra_arr", "multi_steps"):
                raise AttributeError(name)
            return getattr(self._m, name)

    stops = shortloop._cover(m, cells, walk)
    assert stops == shortloop._cover(DictsOnly(m), cells, walk)
    fast, slow = shortloop._Laps(m, cells), shortloop._Laps(DictsOnly(m), cells)
    fast.measure(stops)
    slow.measure(stops)
    assert fast.dists == slow.dists
    path = fast.lap(fast.nearest_first(stops, stops[0]))
    assert fast.covered(path) == slow.covered(path)


def test_travel_port_distances_match_the_dict_search():
    m = atlas.model(EXITS)
    ports = [(p["x"], p["y"]) for p in atlas.maps()[EXITS]["ports"]] + [(-3, -3), (1, 1)]
    start = walkable(m)[100]

    class DictsOnly:
        def __init__(self, m):
            self._m = m

        def __getattr__(self, name):
            if name in ("dijkstra_arr", "dijkstra_targets"):
                raise AttributeError(name)
            return getattr(self._m, name)

    class ArraysOnly(DictsOnly):
        def __getattr__(self, name):
            if name == "dijkstra_targets":
                raise AttributeError(name)
            return getattr(self._m, name)

    want = travel._steps_uncached(DictsOnly(m), start, ports)
    assert travel._steps_uncached(m, start, ports) == want
    assert travel._steps_uncached(ArraysOnly(m), start, ports) == want
    gated = atlas.model(GATES)
    start = walkable(gated)[40]
    ports = [(p["x"], p["y"]) for p in atlas.maps()[GATES]["ports"]]
    assert travel._steps_uncached(gated, start, ports) == travel._steps_uncached(DictsOnly(gated), start, ports)


@needs_c
def test_components_report_a_full_size_table():
    assert C is not None
    walk = np.zeros((4, 4), dtype=bool).ravel().tobytes()
    comp = np.empty(16, dtype=np.int32)
    sizes = np.zeros(1, dtype=np.int32)
    assert C.uf_components(4, 16, walk, comp.ctypes.data, sizes.ctypes.data, 1) == 0
    walk = np.ones(16, dtype=bool).tobytes()
    assert C.uf_components(4, 16, walk, comp.ctypes.data, sizes.ctypes.data, 1) == -2   # UF_SMALL


@needs_c
def test_a_route_longer_than_the_buffer_is_refused():
    assert C is not None
    m = atlas.model(CAVE)
    W, n = m.W, len(m._walk_list)
    a, b = walkable(m)[0], walkable(m)[-1]
    ws = m._workspace(n)
    want = np.array([b[1] * W + b[0]], dtype=np.int32)
    got = ctypes.c_int32()
    k = C.uf_astar_rect(W, n, m._walk_list, m._clear_list, m._gate_list, None, m._portal_list,
                        atlaslib._PEN_C, 12.0, a[1] * W + a[0], want.ctypes.data, 1, b[0], b[0], b[1], b[1],
                        1, 1, 0, 0, ws.dist.ctypes.data, ws.prev.ctypes.data, ws.mark.ctypes.data,
                        ws.wmark.ctypes.data, ws.next_stamp(), 0, ws.out.ctypes.data, 1, ctypes.byref(got), None)
    assert k in (-2, 0)                       # too long for one cell of buffer (or no way at all)


@needs_c
@pytest.mark.parametrize("mid", [GATES, EXITS, FIELD])
def test_targets_only_search_gives_the_full_searchs_distances(mid):
    m = atlas.model(mid)
    cells = walkable(m)
    rnd = random.Random(21)
    for _ in range(5):
        a = rnd.choice(cells)
        targets = [rnd.choice(cells) for _ in range(rnd.randint(1, 6))] + [(1, 1)]
        for avoid in (False, True):
            dist = python(None, lambda: m.dijkstra_arr(a, targets, avoid))[0]
            want = [dist[t[1] * m.W + t[0]] for t in targets]
            assert m.dijkstra_targets(a, targets, avoid).tolist() == want
            assert python(None, lambda: m.dijkstra_targets(a, targets, avoid)).tolist() == want


@needs_c
@pytest.mark.parametrize("mid", [GATES, EXITS])
def test_the_bucket_route_search_settles_like_the_heap(mid):
    m = atlas.model(mid)
    W, n = m.W, len(m._walk_list)
    cells = walkable(m)
    rnd = random.Random(4)
    ws = m._workspace(n)
    lib = C
    assert lib is not None

    def search(a, b, heap):
        want = np.array([b[1] * W + b[0]], dtype=np.int32)
        got = ctypes.c_int32()
        stats = np.zeros(2, dtype=np.int64)
        k = lib.uf_astar_rect(W, n, m._walk_list, m._clear_list, m._gate_list, None, m._portal_list,
                            atlaslib._PEN_C, 12.0, a[1] * W + a[0], want.ctypes.data, 1,
                            b[0], b[0], b[1], b[1], 1, 1, 0, 0, ws.dist.ctypes.data, ws.prev.ctypes.data,
                            ws.mark.ctypes.data, ws.wmark.ctypes.data, ws.next_stamp(), heap,
                            ws.out.ctypes.data, len(ws.out), ctypes.byref(got), stats.ctypes.data)
        return ws.out[:max(k, 0)].tolist(), k, stats.tolist()

    assert C is not None
    for _ in range(15):
        a, b = rnd.choice(cells), rnd.choice(cells)
        assert search(a, b, 0) == search(a, b, 1), (a, b)      # same path, same states settled


@needs_c
def test_costs_off_the_half_step_grid_still_match(monkeypatch):
    # PEN or TURN changed to values the buckets can't hold: the C searches use their heaps.
    m = atlas.model(GATES)
    pen = {1: 4.3, 2: 1.7, 3: 0.25}
    monkeypatch.setattr(atlaslib, "PEN", pen)
    monkeypatch.setattr(atlaslib, "_PEN_C", (ctypes.c_double * 16)(*[pen.get(i, 0.0) for i in range(16)]))
    monkeypatch.setattr(m, "_pen_arr", [pen.get(i, 0.0) for i in range(16)])
    monkeypatch.setattr(m, "TURN", 11.3)
    cells = walkable(m)
    rnd = random.Random(8)
    for _ in range(8):
        a, b = rnd.choice(cells), rnd.choice(cells)
        assert m._route(a, b) == python(None, lambda: m._route(a, b)), (a, b)
        assert m.dijkstra(a, [b]) == python(None, lambda: m.dijkstra(a, [b]))
