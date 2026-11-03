# pyright: reportPrivateUsage=false
"""The travel planner and the map searches with awkward input, and one unit for every leg.

  - Every leg of a plan is counted in the same steps: the walks from where we stand and to the
    goal cell are measured as the stored routes between ports were (the map's own route), not by
    the search's wall-weighted cost (up to 8% more).
  - An unknown map, with or without a goal cell, is "no plan" (it raised KeyError with a cell).
  - Bag rows with a null, text or fractional count don't stop the plan (TypeError).
  - The C searches and their Python twins answer the same for cells off the grid or between cells
    (the Python one raised, the C one ran from a truncated or wrapped cell)."""

from __future__ import annotations

import math
from collections.abc import Callable
from typing import Any, cast

import pytest

from unclefarmer import atlas, atlaslib, travel


@pytest.mark.parametrize("mid", [1041, 1061, 5121, 5302])
def test_the_walk_to_a_port_is_counted_as_the_stored_routes_are(mid: int) -> None:
    mm, m = atlas.maps()[mid], atlas.model(mid)
    ports = mm["ports"]
    for r in mm["routes"][:10]:
        a, b = ports[r["a"]], ports[r["b"]]
        got = travel._steps_uncached(m, (a["x"], a["y"]), [(b["x"], b["y"])]).get((b["x"], b["y"]))
        assert got == r["steps"], (mid, r)


def test_a_plans_cost_adds_up_from_its_legs_in_one_unit() -> None:
    # same map, cell to cell: the cost is exactly the route's length
    m = atlas.model(1041)
    a, b = (200, 205), (180, 230)
    legs, cost = travel.plan(1041, a, 1041, b, with_cost=True)
    path = m.route(a, b)
    assert legs and path and cost == len(path) - 1


@pytest.mark.parametrize("cell", [None, (5, 5)])
def test_an_unknown_goal_map_is_no_plan_with_or_without_a_cell(cell: Any) -> None:
    assert travel.plan(1041, (200, 205), 999999, cell) is None
    assert travel.plan(1041, (200, 205), 999999, cell, with_cost=True) == (None, None)


def test_an_unknown_map_to_start_from_is_no_plan() -> None:
    assert travel.plan(999999, (5, 5), 1041, (200, 205)) is None


@pytest.mark.parametrize("count", [None, "3", "lots", 1.5, float("nan"), True, -2])
def test_odd_bag_counts_never_stop_the_plan(count: Any) -> None:
    bag = [{"name": "Hearthstone", "count": count}, {"name": "Giant rock City Teleportation stone", "count": count}]
    legs = travel.plan(1061, (66, 136), 1041, bag=bag, hearth_home="giant rock square")
    assert legs                                             # walking or flying, a plan comes back


def test_counts_are_read_strictly() -> None:
    assert [travel.count_of({"count": v}) for v in (3, 3.0, "4", " 5 ", None, "x", 1.5, float("inf"), True)] == \
        [3, 3, 4, 5, 0, 0, 0, 0, 0]
    assert travel.count_of({}) == 1                         # no count at all: a single one


def test_a_text_count_still_offers_the_item_and_a_broken_one_does_not() -> None:
    good = travel.item_options([{"name": "Giant rock City Teleportation stone", "count": "2"}])
    bad = travel.item_options([{"name": "Giant rock City Teleportation stone", "count": None}])
    assert good and not bad


# ---------------------------------------------------------------- C and Python agree
MID = 5302


def both(fn: Callable[[], Any], monkeypatch: pytest.MonkeyPatch) -> tuple[Any, Any]:
    native = fn()
    with monkeypatch.context() as mp:
        mp.setattr(atlaslib, "NATIVE", None)
        python = fn()
    return native, python


def norm(v: Any) -> Any:
    """Search answers made comparable: arrays as lists (inf as None), tuples item by item."""
    kind = type(v).__name__
    if kind == "ndarray":
        values: list[float] = [float(x) for x in cast(list[Any], v.tolist())]
        return [None if not math.isfinite(x) else x for x in values]
    if kind == "tuple":
        items: tuple[Any, ...] = cast(tuple[Any, ...], v)
        return tuple(norm(x) for x in items)
    return v


@pytest.mark.parametrize("src", [(-1, 50), (50, -1), "far x", "far y", (40.5, 60.0), (float("nan"), 3), (None, 3)])
def test_the_c_and_python_searches_agree_off_the_grid(src: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    m = atlas.model(MID)
    good = m.nearest_walkable(m.w // 2, m.h // 2, 60)
    assert good is not None
    if src == "far x":
        src = (m.W + 3, good[1])
    elif src == "far y":
        src = (good[0], m.h + 9)
    jobs: list[Callable[[], Any]] = [
        lambda: m.dijkstra_targets(src, [good]),
        lambda: int((m.steps_arr(src, 30) >= 0).sum()),
        lambda: len(m.dijkstra(src, [good])[0]),
        lambda: m.route(src, good),
        lambda: m.route(good, src),
        lambda: m.dijkstra_targets(good, [src, good]),
    ]
    for job in jobs:
        native, python = both(job, monkeypatch)       # neither raises
        assert norm(native) == norm(python)


def test_a_fractional_cell_is_its_whole_cell_for_both(monkeypatch: pytest.MonkeyPatch) -> None:
    m = atlas.model(MID)
    good = m.nearest_walkable(m.w // 2, m.h // 2, 60)
    assert good is not None
    half: Any = (good[0] + 0.5, good[1] + 0.25)          # a position between cells
    native, python = both(lambda: int((m.steps_arr(half, 20) >= 0).sum()), monkeypatch)
    assert native == python == int((m.steps_arr(good, 20) >= 0).sum())


def test_an_off_grid_cell_never_wraps_round_to_the_next_row() -> None:
    m = atlas.model(MID)
    assert m.grid_cell((-1, 50)) is None and m.grid_cell((m.W, 50)) is None
    assert m.grid_cell((m.W - 1, m.h + 1)) == (m.W - 1, m.h + 1)
