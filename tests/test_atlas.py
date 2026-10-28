# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The map knowledge (atlas) against the shipped data, and the map model's pure parsing/graph
helpers (atlaslib) on a small hand-made map so the walkability, one-way gates, exits and routing
can be checked exactly."""

from __future__ import annotations

import pytest

from unclefarmer import atlas, atlaslib, paths
from pathlib import Path
from conftest import stand_in
from unclefarmer.atlaslib import MapModel
from unclefarmer.atlaslib import Cell


# ------------------------------------------------------------------- atlas
def test_maps_are_keyed_by_int_and_cached() -> None:
    maps = atlas.maps()
    assert maps is atlas.maps()
    assert all(isinstance(k, int) for k in maps)
    assert maps[5121]["name"] == "Wolf Prairie"


def test_name_and_label() -> None:
    assert atlas.name(5121) == "Wolf Prairie"
    assert atlas.name(999999) == "999999"                 # an unknown id is its own string
    assert atlas.label(5121) == "Wolf Prairie (5121)"
    assert atlas.label("5121") == "Wolf Prairie (5121)"


def test_kind_and_kind_words() -> None:
    assert atlas.kind(5121) == "field"
    assert atlas.kind(3) == "field"
    assert atlas.kind(999999) is None
    assert atlas.kind_words("dungeon") == "dungeon - you leave by its chest, not on foot"
    assert atlas.kind_words("field") == ""                 # an ordinary field says nothing
    assert atlas.kind_words("nonsense") == ""


def test_kind_without_the_file_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    monkeypatch.setattr(atlas, "_kinds", None)
    assert atlas.kind(5121) is None


def test_zones_and_monster_area() -> None:
    zs = atlas.zones(5121)
    assert zs and all(z["kind"] in ("monster", "elite", "boss") for z in zs)
    assert atlas.zones(5121, ("boss",)) == []
    assert atlas.monster_area(5121) == [41, 41, 360, 360]


def test_farming_maps_are_ordered_fields_first() -> None:
    fm = atlas.farming_maps()
    assert len(fm) == 250
    assert all(isinstance(mid, int) and isinstance(lbl, str) and isinstance(summ, str)
               for mid, lbl, summ in fm)
    assert len({mid for mid, _l, _s in fm}) == len(fm)
    assert atlas.kind(fm[0][0]) in (None, "field")
    nonfield = [i for i, (mid, _l, _s) in enumerate(fm) if atlas.kind(mid) not in (None, "field")]
    assert nonfield and min(nonfield) > 0                  # every ordinary field comes first


def test_all_maps_sorted_by_label() -> None:
    am = atlas.all_maps()
    assert len(am) == len(atlas.maps())
    assert am == sorted(am, key=lambda t: t[1].lower())


def test_patrol_loops() -> None:
    full = atlas.patrol_loop(4714, "full")
    short = atlas.patrol_loop(1001, "short")
    assert full is not None and short is not None
    assert full[:2] == [(42, 10), (82, 10)]
    assert short[:2] == [(64, 90), (84, 90)]
    assert atlas.patrol_loop(4714, "nope") is None
    assert atlas.patrol_loop(999999, "full") is None
    assert atlas.patrol_loop(5882, "full") is None         # flagged bad_hops: random patrol instead


def test_short_patrol_loop_without_the_file_is_none(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    monkeypatch.setattr(atlas, "_short", None)
    assert atlas.patrol_loop(1001, "short") is None


def test_model_is_cached_per_numeric_id() -> None:
    m = atlas.model(1024)
    assert m.id == 1024 and m.name == "Central Kingdom"
    assert atlas.model(1024) is m
    assert atlas.model("1024") is m                        # a string id is the same cached model


def test_edge_direction() -> None:
    assert atlas.edge_direction(5121, (1, 1, 5, 300)) == "left"
    assert atlas.edge_direction(5121, (400, 1, 420, 300)) == "right"
    assert atlas.edge_direction(5121, (200, 1, 220, 300)) is None
    assert atlas.edge_direction(5121, (1, 1, 300, 5)) == "up"
    assert atlas.edge_direction(5121, (1, 395, 300, 399)) == "down"
    assert atlas.edge_direction(5121, (100, 100, 120, 120)) is None


# ----------------------------------------------------------------- atlaslib
def test_blocks_and_safe() -> None:
    assert atlaslib.blocks(200) and atlaslib.blocks(226) and atlaslib.blocks(299)
    assert not atlaslib.blocks(199) and not atlaslib.blocks(100) and not atlaslib.blocks(300)
    assert atlaslib.safe("a/b:c") == "a_b_c"
    assert atlaslib.safe("   ") == "unnamed"
    assert atlaslib.safe("x<y>z") == "x_y_z"


def test_resolve_and_map_names() -> None:
    assert atlaslib.resolve("1024") == [1024]
    assert atlaslib.resolve("central kingdom") == [1024, 1053, 1054, 1069]
    assert atlaslib.resolve("nope map here") == []
    assert 5121 in atlaslib.resolve("wolf")                # a partial name matches too
    assert atlaslib.map_names()[1024] == "Central Kingdom"


def test_marker_kind_branches() -> None:
    assert atlaslib.marker_kind("") == "other"
    assert atlaslib.marker_kind("60级") == "monster"       # a zone labelled only by level
    assert atlaslib.marker_kind("玩家出生点") == "player"
    assert atlaslib.marker_kind("超级怪") == "boss"
    assert atlaslib.marker_kind("精英怪") == "elite"
    assert atlaslib.marker_kind("宝箱") == "chest"
    assert atlaslib.marker_kind("村民") == "npc"
    assert atlaslib.marker_kind("喷火") == "trap"
    assert atlaslib.marker_kind("怪刷新区") == "monster"
    assert atlaslib.marker_kind("刷新区") == "monster"     # a bare respawn zone counts too
    assert atlaslib.marker_kind("nothing in particular") == "other"


def _row(t: int, x1: int, y1: int, x2: int, y2: int, label: str="", z: float=0.0) -> dict[str, str]:
    return {"map_id": "9999", "map_name": "Test Land", "type_id": str(t), "blocks": "",
            "x1": str(x1), "y1": str(y1), "x2": str(x2), "y2": str(y2), "label": label, "z": str(z)}


@pytest.fixture
def tiny(monkeypatch: pytest.MonkeyPatch) -> MapModel:
    """A 6x5 map: floor, a wall down the middle, an unpaired one-way door through it, a paired
    (two-way) door, a lone ledge, two exits, a town zone, an NPC cell and a monster marker."""
    rows = [
        _row(105, 1, 1, 6, 5, "floor"),
        _row(200, 3, 1, 3, 5, "wall"),
        _row(301, 3, 3, 3, 3, "door", 4.0),        # unpaired: you cannot step east off it
        _row(301, 5, 1, 5, 1, "door", 4.0),        # paired with the next row: stays two-way
        _row(301, 5, 2, 5, 2, "door", 1.0),
        _row(333, 2, 2, 2, 2, "ledge", 8.0),       # directional, no partner: one-way
        _row(1041101, 6, 1, 6, 1, "", -1.0),       # exit to map 1041
        _row(atlaslib.EXIT_GANG, 6, 5, 6, 5, "", -1.0),
        _row(9999001, 1, 1, 1, 1, "town", -3.0),   # z=-3 back to its own map: a town zone
        _row(1, 5, 5, 5, 5, "村民"),
        _row(11, 4, 4, 4, 4, "60级"),
    ]
    monkeypatch.setattr(atlaslib, "all_rows", stand_in(atlaslib.all_rows, lambda: {9999: rows}))
    return atlaslib.MapModel(9999)


def test_a_map_without_geometry_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlaslib, "all_rows", stand_in(atlaslib.all_rows, lambda: {}))
    with pytest.raises(KeyError):
        atlaslib.MapModel(9999)


def test_model_reads_geometry(tiny: MapModel) -> None:
    assert tiny.name == "Test Land"
    assert (tiny.w, tiny.h) == (6, 5)
    assert tiny.ok(1, 1)
    assert not tiny.ok(3, 1)          # wall
    assert not tiny.ok(0, 0)          # the void border


def test_without_makes_cells_walls(tiny: MapModel) -> None:
    assert tiny.ok(3, 3)
    assert not tiny.without([(3, 3)]).ok(3, 3)
    assert tiny.ok(3, 3)              # the original map is untouched


def test_exits_and_towns(tiny: MapModel) -> None:
    assert [(e["dest"], e["marker"]) for e in tiny.exits] == [(1041, 101), (None, None)]
    assert all(e["stand_inside"] for e in tiny.exits)
    assert [(e["dest"], e["marker"]) for e in tiny.towns] == [(9999, 1)]
    town_exit, leave = tiny.exit_at((6, 1)), tiny.exit_at((6, 5))
    assert town_exit is not None and town_exit["dest"] == 1041
    assert leave is not None and leave["dest"] is None    # the gang "leave" exit
    assert tiny.exit_at((2, 2)) is None
    assert tiny.exit_cells_of(tiny.exits[0]) == [(6, 1)]


def test_one_way_gates(tiny: MapModel) -> None:
    assert tiny.gate[3, 3] == 4
    assert tiny.gate_blocks((3, 3), (4, 3))
    assert not tiny.gate_blocks((3, 3), (2, 3))
    assert not tiny.gate_blocks((2, 3), (3, 3))
    assert tiny.gate[1, 5] == 0 and tiny.gate[2, 5] == 0   # a paired door stays two-way
    assert tiny.oneway[2, 2]                               # a lone directional ledge
    with tiny.no_gates():
        assert not tiny.gate.any()
    assert tiny.gate[3, 3] == 4                            # restored afterwards


def test_a_single_cell_npc_blocks_its_cell(tiny: MapModel) -> None:
    assert tiny.npc_cells == {(5, 5): "村民"}
    assert not tiny.ok(5, 5)
    assert tiny.ok(4, 4)              # a monster marker does not block its cell


def test_clearance_and_components(tiny: MapModel) -> None:
    assert tiny.comp_sizes == [0, 25]
    assert tiny.comp_of(6, 1) == 1
    assert tiny.clear[3, 3] >= 1


def test_route_obeys_gates_and_falls_back(tiny: MapModel) -> None:
    assert tiny.gated_route((2, 3), (4, 3)) is None             # the gate forbids the only way through
    assert tiny.route((2, 3), (4, 3)) == [(2, 3), (3, 3), (4, 3)]   # retried ignoring the gates
    assert tiny.route((4, 1), (4, 2)) == [(4, 1), (4, 2)]


def test_dijkstra_steps_and_path(tiny: MapModel) -> None:
    dist, prev = tiny.dijkstra((2, 3))
    assert tiny.path(prev, (3, 3)) == [(2, 3), (3, 3)]
    assert tiny.path(prev, (4, 3)) is None                 # gated off
    assert 4 * tiny.W + 3 not in dist
    st = tiny.steps((2, 3))
    assert st[3 * tiny.W + 3] == 1
    assert 4 * tiny.W + 3 not in st


def test_astar_rect_and_path_rect(tiny: MapModel) -> None:
    _d, _p, reached = tiny.astar_rect((2, 3), [(4, 3)])
    assert reached == {}
    with tiny.no_gates():
        _d, prev, reached = tiny.astar_rect((2, 3), [(4, 3)])
    assert reached and tiny.path_rect(prev, reached, (4, 3)) == [(2, 3), (3, 3), (4, 3)]


def test_steps_arr_is_the_same_search_as_steps(tiny: MapModel) -> None:
    for limit in (None, 1, 2, 3, 4):
        arr = tiny.steps_arr((2, 3), limit=limit)
        by_cell = tiny.steps((2, 3), limit=limit)
        assert {int(k) for k in (arr >= 0).nonzero()[0]} == set(by_cell)
        for k, d in by_cell.items():
            assert int(arr[k]) == d
    assert tiny.steps_arr((2, 3))[3 * tiny.W + 4] == -1      # gated off from where we start


def test_steps_arr_matches_the_plain_bfs_on_a_real_cave() -> None:
    from collections import deque

    m = atlas.model(1047)                   # a walled cave: searching actually has work to do

    def reference(src: Cell, limit: int | None) -> dict[int, int]:
        start = src[1] * m.W + src[0]
        dist = {start: 0}
        q = deque([start])
        while q:
            c = q.popleft()
            d = dist[c]
            if limit is not None and d >= limit:
                continue
            for off, bit in ((1, 4), (-1, 1), (m.W, 8), (-m.W, 2)):
                nb = c + off
                if nb in dist or not m._walk_list[nb] or m._gate_list[c] & bit:
                    continue
                dist[nb] = d + 1
                q.append(nb)
        return dist

    src = (46, 46)
    for limit in (10, 40, 150, None):
        arr = m.steps_arr(src, limit=limit)
        ref = reference(src, limit)
        assert {int(k) for k in (arr >= 0).nonzero()[0]} == set(ref)
        assert all(int(arr[k]) == d for k, d in ref.items())


def test_route_is_cached_but_callers_get_their_own_list(tiny: MapModel) -> None:
    first = tiny.route((2, 3), (3, 3))
    assert first == [(2, 3), (3, 3)]
    assert first is not None
    first.append((9, 9))                       # a caller editing its path...
    assert tiny.route((2, 3), (3, 3)) == [(2, 3), (3, 3)]     # ...does not corrupt the cache


def test_route_cache_cannot_leak_a_path_planned_without_gates(tiny: MapModel) -> None:
    with tiny.no_gates():
        assert tiny.route((2, 3), (4, 3)) == [(2, 3), (3, 3), (4, 3)]
    assert not tiny.__dict__.get("_route_cache")     # nothing stored while the gates were off


def test_without_starts_with_no_cached_routes(tiny: MapModel) -> None:
    assert tiny.route((2, 3), (3, 3)) == [(2, 3), (3, 3)]
    walled = tiny.without([(3, 3)])
    assert walled.route((2, 3), (3, 3)) is None       # the cached path ran through a wall now
    assert tiny.route((2, 3), (3, 3)) == [(2, 3), (3, 3)]


def test_dijkstra_rect_reaches_its_target(tiny: MapModel) -> None:
    _d, prev, reached = tiny.dijkstra_rect((4, 1), [(4, 3)])
    assert list(reached) == [3 * tiny.W + 4]
    assert tiny.path_rect(prev, reached, (4, 3)) == [(4, 1), (4, 2), (4, 3)]


def test_two_way_area_stops_at_the_gate(tiny: MapModel) -> None:
    area = tiny.two_way(3, 3)
    assert 3 * tiny.W + 3 in area
    assert 4 * tiny.W + 3 not in area                      # beyond the one-way gate


def test_line_and_line_ok(tiny: MapModel) -> None:
    assert tiny.line((4, 1), (6, 3)) == [(4, 1), (4, 2), (5, 2), (5, 3), (6, 3)]
    assert tiny.line_ok((4, 1), (4, 2))
    assert not tiny.line_ok((2, 1), (2, 3))                # crosses the ledge
    assert tiny.line_ok((2, 1), (2, 3), allow_directional=True)


def test_waypoints_cuts_corners(tiny: MapModel) -> None:
    assert tiny.waypoints([(1, 1), (1, 2), (1, 3), (2, 3), (2, 4)]) == [(1, 3), (2, 3), (2, 4)]
    assert tiny.waypoints([]) == []
    assert tiny.waypoints([(1, 1)]) == []


def test_diag_helpers(tiny: MapModel) -> None:
    assert tiny.diag_steps((1, 1), (2, 2)) == [((1, 1), (2, 1)), ((2, 1), (2, 2)),
                                               ((1, 1), (1, 2)), ((1, 2), (2, 2))]
    assert tiny.diag_ok((1, 3), (2, 4))
    assert not tiny.diag_ok((2, 1), (2, 3))                # through the ledge
    hops = tiny.diag_hops([(1, 1), (2, 1), (3, 1), (3, 2), (3, 3)])
    assert hops[-1] == (3, 3)


def test_nearest_walkable_and_floor(tiny: MapModel) -> None:
    assert tiny.nearest_walkable(0, 0) == (1, 1)
    assert tiny.nearest_floor(0, 0) == (1, 1)
    assert tiny.nearest_walkable(0, 0, comp=2) is None     # no walkable cell in that component


def test_side_of(tiny: MapModel) -> None:
    assert tiny.side_of(1, 1, 1, 1) == "north west edge"
    assert tiny.side_of(2, 2, 3, 3) == "north south west edge"
    assert tiny.side_of(4, 2, 5, 3) == "north south east edge"


def test_side_of_inside_a_large_map(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlaslib, "all_rows", stand_in(atlaslib.all_rows, lambda: {9999: [_row(105, 1, 1, 20, 20, "floor")]}))
    m = atlaslib.MapModel(9999)
    assert m.side_of(5, 5, 10, 10) == "inside the map"


# ------------------------------------------------------- what the geometry keeps in memory
def test_all_rows_is_a_mapping_that_keeps_no_rows() -> None:
    import collections.abc

    assert isinstance(atlaslib.all_rows(), collections.abc.Mapping)
    assert atlaslib.all_rows()[1041] == atlaslib.rows_for(1041)
    assert 1041 in atlaslib.all_rows()
    assert 999999 not in atlaslib.all_rows()
    assert not hasattr(atlaslib, "_rows")            # the old whole-file dict is gone


def test_rows_for_is_shared_and_bounded() -> None:
    atlaslib.rows_for.cache_clear()
    rows = atlaslib.rows_for(1041)
    assert isinstance(rows, tuple)                   # a caller cannot edit the cache
    assert rows and rows[0]["map_name"].strip() == "central plains"
    assert atlaslib.rows_for(1041) is rows
    for mid in list(atlaslib.map_names())[:8]:
        atlaslib.rows_for(mid)
    info = atlaslib.rows_for.cache_info()
    assert info.maxsize == atlaslib.GEOMETRY_CACHE and info.currsize <= atlaslib.GEOMETRY_CACHE


def test_rows_for_reads_the_same_rows_as_a_plain_csv_parse() -> None:
    import csv

    with atlaslib.GEOMETRY.open(encoding="utf-8-sig", newline="") as fh:
        wanted = [r for r in csv.DictReader(fh) if int(r["map_id"]) == 1041]
    assert list(atlaslib.rows_for(1041)) == wanted


def test_the_flat_routing_caches_are_bytes() -> None:
    m = atlas.model(1041)
    for name in ("_walk_list", "_clear_list", "_gate_list", "_portal_list", "_oneway_list",
                 "_zero_list"):
        flat = getattr(m, name)
        assert isinstance(flat, bytes), name
        assert len(flat) == m.W * (m.h + 2)
    assert set(m._gate_list) <= {0, 1, 2, 4, 8}      # gate bits still index as ints
    assert not any(m._zero_list)


def test_a_copy_with_cells_walled_off_keeps_the_bytes() -> None:
    m = atlas.model(1041)
    before = len(m._walk_list)
    mm = m.without([(200, 200)])
    assert isinstance(mm._walk_list, bytes) and len(mm._walk_list) == before
    assert not mm._walk_list[200 * m.W + 200]
    assert m._walk_list[200 * m.W + 200]              # the original is untouched
