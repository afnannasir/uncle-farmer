"""The short patrol loop builder (unclefarmer/shortloop.py).

build() only needs a handful of MapModel features - walkability, components, the two-way ground
behind one-way gates, walking distances and a route - so these tests drive it with a small hand-made
stand-in map and a stubbed atlas. main() is checked by pointing OUT at tmp_path and stubbing build.
Nothing here reads the shipped map geometry or talks to a game.
"""

from collections import deque

import numpy as np

from unclefarmer import jsonio, shortloop


class FakeMap:
    """Just enough of atlaslib.MapModel for shortloop.build: an interior grid plus a void border."""

    def __init__(self, w, h, walk):
        self.w = w
        self.h = h
        self.W = w + 2
        self.walk = walk
        self.portal = np.zeros_like(walk)
        self._two_way = []
        self._components()

    def _components(self):
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

    def ok(self, x, y):
        return 1 <= x <= self.w and 1 <= y <= self.h and bool(self.walk[y, x])

    def comp_of(self, x, y):
        return self.comp[y * self.W + x]

    def two_way(self, x, y):
        W = self.W
        c0 = y * W + x
        walk = self.walk.ravel().tolist()
        if not walk[c0]:
            return frozenset()
        for area in self._two_way:
            if c0 in area:
                return area
        seen = {c0}
        todo = [c0]
        while todo:
            c = todo.pop()
            for off in (1, -1, W, -W):
                n = c + off
                if walk[n] and n not in seen:
                    seen.add(n)
                    todo.append(n)
        area = frozenset(seen)
        self._two_way.append(area)
        return area

    def dijkstra(self, src, targets=(), avoid_directional=False, limit=None):
        W = self.W
        walk = self.walk.ravel().tolist()
        s = src[1] * W + src[0]
        dist = {s: 0.0}
        prev = {s: -1}
        q = deque([s])
        while q:
            c = q.popleft()
            for nb in (c + 1, c - 1, c + W, c - W):
                if walk[nb] and nb not in dist:
                    dist[nb] = dist[c] + 1.0
                    prev[nb] = c
                    q.append(nb)
        return dist, prev

    def _route(self, a, b, avoid_directional=False):
        W = self.W
        _dist, prev = self.dijkstra(a)
        c = b[1] * W + b[0]
        if c not in prev:
            return None
        out = []
        while c != -1:
            out.append((c % W, c // W))
            c = prev[c]
        return out[::-1]

    def waypoints(self, path, max_hop=None):
        if not path or len(path) < 2:
            return list(path[1:])
        out = []
        for i in range(1, len(path) - 1):
            a, b, c = path[i - 1], path[i], path[i + 1]
            if (b[0] - a[0], b[1] - a[1]) != (c[0] - b[0], c[1] - b[1]):
                out.append(b)
        out.append(path[-1])
        return out


def open_map(w=20, h=20):
    walk = np.zeros((h + 2, w + 2), dtype=bool)
    walk[1:h + 1, 1:w + 1] = True
    return FakeMap(w, h, walk)


def corridor(h=100, x0=2, x1=5):
    """A 4-wide, tall strip: wide enough for 50+ cells, long enough that one stop cannot cover it."""
    w = 6
    walk = np.zeros((h + 2, w + 2), dtype=bool)
    walk[1:h + 1, x0:x1 + 1] = True
    return FakeMap(w, h, walk)


def install(monkeypatch, model, farms=None, zones=None):
    monkeypatch.setattr(shortloop.atlas, "model", lambda mid: model)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: farms or {})
    monkeypatch.setattr(shortloop.atlas, "zones", lambda mid, kinds=(): zones or [])


def test_constants():
    assert shortloop.CHASE == 40
    assert shortloop.COVER == 0.95
    assert shortloop.STOP_R == 25
    assert shortloop.MAX_CELLS == 30000
    assert shortloop.OUT.endswith("short_loops.json")


def test_build_of_a_tiny_map_is_none(monkeypatch):
    install(monkeypatch, open_map(7, 7))            # 49 walkable cells: under the 50-cell floor
    assert shortloop.build(7) is None


def test_build_of_too_large_a_map_is_none(monkeypatch):
    install(monkeypatch, open_map(20, 20))          # 400 walkable cells
    monkeypatch.setattr(shortloop, "MAX_CELLS", 100)
    assert shortloop.build(7) is None


def test_build_without_monster_zones_is_none_when_all_is_portal(monkeypatch):
    m = open_map(20, 20)
    m.portal[:, :] = True                           # every candidate ground is an exit
    install(monkeypatch, m)
    assert shortloop.build(7) is None


def test_build_keeps_only_the_ground_inside_monster_zones(monkeypatch):
    install(monkeypatch, corridor(), zones=[{"rect": [2, 1, 5, 5]}])   # only 20 cells stay
    assert shortloop.build(7) is None


def test_build_uses_a_farms_start_when_it_is_walkable(monkeypatch):
    farms = {7: {"variants": {"full": {"start": [2, 50]}}}}
    install(monkeypatch, corridor(), farms=farms)
    r = shortloop.build(7)
    assert r is not None and r["loop"]


def test_build_falls_back_to_the_biggest_component_when_the_start_is_not_walkable(monkeypatch):
    farms = {7: {"variants": {"full": {"start": [0, 0]}}}}     # off the map
    install(monkeypatch, corridor(), farms=farms)
    r = shortloop.build(7)
    assert r is not None and r["loop"]


def test_build_of_an_open_small_map_makes_a_single_stop_and_no_loop(monkeypatch):
    # Documented behavior: on a small open map one stop keeps everything within STOP_R, so the lap
    # is a single point; build returns a zero-length loop. main() is what drops such a result.
    install(monkeypatch, open_map(20, 20))
    r = shortloop.build(7)
    assert r == {"loop": [], "steps": 0, "covered": 100.0, "stops": 1}


def test_build_of_a_long_corridor_makes_a_real_loop(monkeypatch):
    install(monkeypatch, corridor())
    r = shortloop.build(7)
    assert r is not None
    assert len(r["loop"]) >= 2
    assert r["steps"] >= 1
    assert r["stops"] >= 2
    assert 0.0 <= r["covered"] <= 100.0
    assert all(isinstance(c, str) and "|" in c for c in r["loop"])


def test_build_prefers_a_shorter_ready_variant(monkeypatch):
    farms = {7: {"variants": {"full": {"loop": ["2|1", "5|1", "5|2"], "steps": 3}}}}
    install(monkeypatch, corridor(), farms=farms)
    r = shortloop.build(7)
    assert r == {"loop": ["2|1", "5|1", "5|2"], "steps": 3, "covered": None, "from": "full"}


def test_build_ignores_a_shorter_variant_flagged_bad_hops(monkeypatch):
    farms = {7: {"variants": {"full": {"loop": ["2|1", "5|1", "5|2"], "steps": 3, "bad_hops": True}}}}
    install(monkeypatch, corridor(), farms=farms)
    r = shortloop.build(7)
    assert r is not None
    assert "from" not in r
    assert r["covered"] is not None


def test_build_ignores_a_variant_that_is_not_shorter(monkeypatch):
    farms = {7: {"variants": {"full": {"loop": ["2|1", "5|1"], "steps": 10 ** 9}}}}
    install(monkeypatch, corridor(), farms=farms)
    r = shortloop.build(7)
    assert r is not None
    assert "from" not in r
    assert r["loop"]


def _write(path, obj):
    with open(path, "w", encoding="utf-8") as f:
        jsonio.dump(obj, f, indent=0, sort_keys=True)


def _read(path):
    with open(path, encoding="utf-8") as f:
        return jsonio.load(f)


def test_main_writes_only_loops_with_at_least_two_points(tmp_path, monkeypatch):
    out = str(tmp_path / "short_loops.json")
    monkeypatch.setattr(shortloop, "OUT", out)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {111: {}, 222: {}})
    monkeypatch.setattr(shortloop.atlas, "name", lambda mid: "Map %d" % mid)
    made = {111: {"loop": ["1|1", "2|2"], "steps": 1, "covered": 100.0},
            222: {"loop": ["1|1"], "steps": 0, "covered": 100.0}}   # a single spot is no lap
    monkeypatch.setattr(shortloop, "build", lambda mid: made[mid])
    shortloop.main([])
    saved = _read(out)
    assert saved["111"]["loop"] == ["1|1", "2|2"]
    assert "222" not in saved


def test_main_reads_and_merges_the_existing_file(tmp_path, monkeypatch):
    out = str(tmp_path / "short_loops.json")
    _write(out, {"111": {"loop": ["9|9", "9|8"], "steps": 1, "covered": None, "from": "full"}})
    monkeypatch.setattr(shortloop, "OUT", out)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {111: {}, 222: {}})
    monkeypatch.setattr(shortloop.atlas, "name", lambda mid: "Map %d" % mid)
    monkeypatch.setattr(shortloop, "build",
                        lambda mid: {"loop": ["2|2", "3|3"], "steps": 4, "covered": 80.0})
    shortloop.main([])                                 # 111 is already done: only 222 is built
    saved = _read(out)
    assert saved["111"]["from"] == "full"
    assert saved["222"]["loop"] == ["2|2", "3|3"]


def test_main_with_ids_rebuilds_a_map_already_in_the_file(tmp_path, monkeypatch):
    out = str(tmp_path / "short_loops.json")
    _write(out, {"111": {"loop": ["9|9", "9|8"], "steps": 99, "covered": None, "from": "full"}})
    monkeypatch.setattr(shortloop, "OUT", out)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {111: {}})
    monkeypatch.setattr(shortloop.atlas, "name", lambda mid: "Map %d" % mid)
    monkeypatch.setattr(shortloop, "build",
                        lambda mid: {"loop": ["1|1", "2|2"], "steps": 5, "covered": 100.0})
    shortloop.main(["111"])
    saved = _read(out)
    assert saved["111"] == {"loop": ["1|1", "2|2"], "steps": 5, "covered": 100.0}


def test_main_reports_a_map_that_fails_and_keeps_going(tmp_path, monkeypatch, capsys):
    out = str(tmp_path / "short_loops.json")
    monkeypatch.setattr(shortloop, "OUT", out)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {111: {}, 222: {}})
    monkeypatch.setattr(shortloop.atlas, "name", lambda mid: "Map %d" % mid)

    def build(mid):
        if mid == 111:
            raise ValueError("no geometry")
        return {"loop": ["2|2", "3|3"], "steps": 4, "covered": 80.0}

    monkeypatch.setattr(shortloop, "build", build)
    shortloop.main([])
    saved = _read(out)
    assert "111" not in saved and saved["222"]["loop"] == ["2|2", "3|3"]
    assert "111 failed: no geometry" in capsys.readouterr().out


def test_main_with_an_id_missing_from_farms_still_reports_the_loop(tmp_path, monkeypatch, capsys):
    # An id given on the command line need not be in farms.json (a map nobody farms yet): the
    # report line used to read atlas.farms()[mid] and raise KeyError, stopping the whole run.
    out = str(tmp_path / "short_loops.json")
    monkeypatch.setattr(shortloop, "OUT", out)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {})
    monkeypatch.setattr(shortloop.atlas, "name", lambda mid: "Map %d" % mid)
    monkeypatch.setattr(shortloop, "build",
                        lambda mid: {"loop": ["1|1", "2|2"], "steps": 5, "covered": 100.0})
    shortloop.main(["7"])
    assert _read(out)["7"]["loop"] == ["1|1", "2|2"]
    assert "full loop None" in capsys.readouterr().out


def test_main_with_an_id_whose_loop_is_a_single_spot_does_not_report(tmp_path, monkeypatch):
    # The farms lookup is only reached for a real loop, so a single-spot result exits cleanly.
    out = str(tmp_path / "short_loops.json")
    monkeypatch.setattr(shortloop, "OUT", out)
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {})
    monkeypatch.setattr(shortloop, "build",
                        lambda mid: {"loop": ["1|1"], "steps": 0, "covered": 100.0})
    shortloop.main(["7"])
    assert _read(out) == {}


def test_main_with_no_maps_writes_nothing(tmp_path, monkeypatch):
    out = tmp_path / "short_loops.json"
    monkeypatch.setattr(shortloop, "OUT", str(out))
    monkeypatch.setattr(shortloop.atlas, "farms", lambda: {})
    shortloop.main([])
    assert not out.exists()
