# pyright: reportPrivateUsage=false
"""Target refusal delays saturate before arithmetic can overflow."""
from __future__ import annotations

import pytest

from conftest import Clock
from test_fighting import Bot, Link, monster
from unclefarmer import atlas


@pytest.mark.parametrize("mid", [5302, 5303, 1503, 5121])
def test_long_running_selection_refusals_keep_the_capped_delay(mid: int) -> None:
    model = atlas.model(mid)
    cell = model.nearest_walkable(model.w // 2, model.h // 2, 100)
    assert cell is not None
    x, y = cell
    mob = monster(-5, float(x), float(y), name="Stationary refused target")
    b = Bot(Link(send="refused: unavailable"), entities=[mob],
            me={"id": 1001, "x": float(x), "y": float(y), "hp": 100, "maxhp": 100}, map_id=mid)
    b.clock = Clock()
    b.agent_act = True
    for attempt in range(1, 1001):
        b._fight(mob)  # previously raised on attempt648
        expected = (10, 30, 90, 270)[attempt - 1] if attempt <= 4 else 300
        assert b.skip[mob["id"]] - b.clock.time() == pytest.approx(expected)
        assert b.unselectable[mob["id"]][0] == attempt
        b.clock.now = b.skip[mob["id"]] + 0.01


@pytest.mark.parametrize("movement", [2.0, 3.0, 4.0])
def test_moving_a_long_refused_target_resets_only_at_the_existing_threshold(movement: float) -> None:
    b = Bot()
    b.clock = Clock()
    mob = monster(-1, 100.0, 100.0)
    b.unselectable = {-1: (1000, (100.0, 100.0), b.clock.time())}
    mob["x"] += movement
    b.note_unselectable(mob)
    reset = movement >= b.UNSELECTED_MOVED
    assert b.unselectable[-1][0] == (1 if reset else 1001)
    assert b.skip[-1] - b.clock.time() == (b.UNSELECTED_SKIP if reset else b.UNSELECTED_MAX)


def test_very_large_existing_counter_does_not_require_an_unbounded_power() -> None:
    b = Bot()
    b.clock = Clock()
    mob = monster(-1, 100.0, 100.0)
    b.unselectable = {-1: (1_000_000, (100.0, 100.0), b.clock.time())}
    b.note_unselectable(mob)
    assert b.skip[-1] == b.clock.time() + b.UNSELECTED_MAX


def test_refusal_delays_are_independent_for_each_target() -> None:
    b = Bot()
    b.clock = Clock()
    first, second = monster(-1), monster(-2)
    for _ in range(20):
        b.note_unselectable(first)
    b.note_unselectable(second)
    assert b.skip[-1] - b.clock.time() == b.UNSELECTED_MAX
    assert b.skip[-2] - b.clock.time() == b.UNSELECTED_SKIP
