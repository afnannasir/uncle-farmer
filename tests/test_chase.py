"""Which monsters count as close enough to chase on a patrol loop: real walking steps, not the route
search's cost (which adds extra for walking beside walls)."""

from __future__ import annotations

import math

from unclefarmer import atlas
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.atlaslib import MapModel

CHASE = 40
MACHINE_FORTRESS = 1635       # a cave of narrow corridors: the wall cost counts double and more there


class Walker(WalkingMixin):
    def __init__(self) -> None:
        self._dists = None


Case = tuple[tuple[int, int], int]           # (monster cell, walking steps to it)


def corner_cases(m: MapModel, src: tuple[int, int], how_many: int=5) -> list[Case]:
    """Cells round a corner (no straight line) within CHASE real steps whose route cost is over it."""
    steps = m.steps(src, limit=CHASE)
    cost, _prev = m.dijkstra(src, limit=4 * CHASE)
    out: list[Case] = []
    for k, n in sorted(steps.items()):
        cell = (k % m.W, k // m.W)
        if 10 <= n <= CHASE and cost.get(k, math.inf) > CHASE and not m.line_ok(src, cell):
            out.append((cell, n))
            if len(out) == how_many:
                break
    return out


def test_steps_are_the_walk_along_an_open_line() -> None:
    mid = atlas.resolve("Wolf Prairie")
    assert mid is not None
    m = atlas.model(mid)
    ys, xs = m.walk.nonzero()
    for x, y in zip(xs[::997].tolist(), ys[::997].tolist()):
        steps = m.steps((x, y), limit=30)
        for dx in range(1, 30):
            if not m.ok(x + dx, y):
                break
            assert steps[y * m.W + x + dx] == dx


def test_steps_never_exceed_the_route_cost() -> None:
    m = atlas.model(MACHINE_FORTRESS)
    ys, xs = m.walk.nonzero()
    src = (int(xs[len(xs) // 2]), int(ys[len(ys) // 2]))
    steps = m.steps(src, limit=60)
    cost, _prev = m.dijkstra(src, limit=300)
    assert all(n <= cost[k] + 1e-9 for k, n in steps.items() if k in cost)


def test_a_monster_round_a_corner_within_chase_range_is_chased() -> None:
    m = atlas.model(MACHINE_FORTRESS)
    ys, xs = m.walk.nonzero()
    src: tuple[int, int] = (0, 0)
    cases: list[Case] = []
    for i in range(0, len(xs), 211):
        src = (int(xs[i]), int(ys[i]))
        cases = corner_cases(m, src)
        if cases:
            break
    assert cases, "no corridor case found on the map"
    w = Walker()
    me = {"x": src[0] + 0.5, "y": src[1] + 0.5}
    for (x, y), n in cases:
        snap = {"map": {"id": MACHINE_FORTRESS}, "self": me}
        d = w.walk_dist(snap, {"x": x, "y": y}, limit=CHASE)
        assert d == n, "a monster %d steps away counted as %s" % (n, d)
