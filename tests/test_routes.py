# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Route planning by A*: the same route costs as the Dijkstra search it replaced, on a maze of
one-way doors (turkey cave), to cells and to exits."""

from __future__ import annotations

import random

import pytest

from unclefarmer import atlas
from unclefarmer.atlaslib import Cell


def cost(dist: dict[int, float], reached: dict[int, int]) -> float | None:
    return dist[next(iter(reached.values()))] if reached else None


@pytest.mark.slow
@pytest.mark.parametrize("avoid", [False, True])
def test_astar_costs_what_dijkstra_does(avoid: bool) -> None:
    m = atlas.model(5302)
    ys, xs = m.walk.nonzero()
    cells = list(zip(xs.tolist(), ys.tolist()))
    rnd = random.Random(5)
    jobs = [(rnd.choice(cells), rnd.choice(cells)) for _ in range(25)]
    jobs += [(rnd.choice(cells), tuple(e["stand"])) for e in m.exits if e.get("stand")]
    for a, b in jobs:
        e = m.exit_at(b)
        if e is not None and m.exit_at(a) is not e:
            targets, any_target = m.exit_cells_of(e), True
        else:
            targets, any_target = [b], False
        dd, _p, rd = m.dijkstra_rect(a, targets, avoid, any_target=any_target)
        da, _p, ra = m.astar_rect(a, targets, avoid)
        assert cost(dd, rd) == pytest.approx(cost(da, ra)) if rd else not ra, (a, b)


def test_a_route_is_a_walk_of_single_steps_to_the_target() -> None:
    m = atlas.model(5302)
    ys, xs = m.walk.nonzero()
    cells = list(zip(xs.tolist(), ys.tolist()))
    rnd = random.Random(9)
    found = 0
    for _ in range(20):
        a, b = rnd.choice(cells), rnd.choice(cells)
        p = m.route(a, b)
        if p is None:
            continue
        found += 1
        assert p[0] == a and p[-1] == b
        assert all(abs(x1 - x2) + abs(y1 - y2) == 1 for (x1, y1), (x2, y2) in zip(p, p[1:]))
        assert all(m.ok(x, y) for x, y in p)
    assert found >= 10


def test_a_gate_ignoring_fallback_is_not_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np
    from unclefarmer import atlaslib
    m = atlaslib.MapModel.__new__(atlaslib.MapModel)
    m.gate = np.ones((3, 3), dtype=np.int32)
    m._gate_list = b"\x01" * 9
    m._zero_list = bytes(9)
    calls: list[bool] = []

    def fake(a: Cell, b: Cell, avoid_directional: bool = False) -> list[Cell] | None:
        calls.append(bool(m.__dict__.get("_gates_off")))
        return None if len(calls) == 1 else [(1, 1), (2, 2)]

    monkeypatch.setattr(m, "gated_route", fake)
    assert m.route((1, 1), (2, 2)) == [(1, 1), (2, 2)]
    assert calls == [False, True]
    assert ((1, 1), (2, 2), False) not in m.__dict__.get("_route_cache", {})


def test_a_gate_respecting_route_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    import numpy as np
    from unclefarmer import atlaslib
    m = atlaslib.MapModel.__new__(atlaslib.MapModel)
    m.gate = np.zeros((3, 3), dtype=np.int32)      # no gates: no fallback
    m._gate_list = bytes(9)
    m._zero_list = bytes(9)
    calls: list[int] = []

    def fake(a: Cell, b: Cell, avoid_directional: bool = False) -> list[Cell] | None:
        calls.append(1)
        return [(1, 2), (2, 2)]

    monkeypatch.setattr(m, "gated_route", fake)
    assert m.route((1, 1), (2, 2)) == [(1, 2), (2, 2)]
    assert m.route((1, 1), (2, 2)) == [(1, 2), (2, 2)]
    assert calls == [1]
