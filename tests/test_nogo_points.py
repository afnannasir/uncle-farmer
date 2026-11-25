"""A farming spot's no-go circles and own patrol points: the choice of targets, the script and the
plan's line, and the plan editor's own text."""
from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_picking import choose, ids, mob, snap
from test_script import FakeBot

from unclefarmer.engine import picking, plan, script as S
from unclefarmer.gui import plan as plan_gui


def test_a_monster_in_a_no_go_circle_is_left_unless_it_attacks() -> None:
    s = snap(mob(-1, 60), mob(-2, 70, 50))
    rules = picking.Rules(no_go=((70.0, 50.0, 5.0),))
    c = choose(s, rules)
    assert ids(c) == [-1] and c.left == {"nogo": 1}
    hit = picking.Notes(now=100.0, hitting=frozenset({-2}))
    assert -2 in ids(choose(s, rules, hit))          # one that attacks us from there is still fought


def test_the_script_reads_points_and_circles() -> None:
    bot = FakeBot()
    S.Runner(as_bot(bot), "farm map=5281 points=120,80;150,90 nogo=26,219,45;300,40,10").run()
    _kind, mid, kw = bot.calls[0]
    assert mid == 5281 and kw["patrol"] == "points"          # points alone: go round them
    assert kw["points"] == [[120, 80], [150, 90]]
    assert kw["nogo"] == [[26, 219, 45], [300, 40, 10]]


@pytest.mark.parametrize("line, why", [
    ("farm map=5281 patrol=points", "patrol=points needs points"),
    ("farm map=5281 points=1,2,3", "points is x,y"),
    ("farm map=5281 nogo=26,219", "nogo is x,y,radius"),
    ("farm map=5281 points=" + ";".join("%d,1" % i for i in range(11)), "up to 10"),
    ("farm map=5281 patrol=circle", "patrol is random, full, quick, short or points"),
])
def test_a_wrong_line_says_why(line: str, why: str) -> None:
    with pytest.raises(S.ScriptError, match=why):
        S.parse(line)


def test_the_plan_writes_its_spot_s_points_and_circles() -> None:
    spot: dict[str, Any] = dict(plan.new_step(5281), patrol="points", points=[[120, 80], [150, 90]],
                                nogo=[[26, 219, 45]])
    line = plan.farm_line(spot, several=False)
    assert line == "farm map=5281 patrol=points points=120,80;150,90 nogo=26,219,45"
    S.parse(line)
    assert "points=" not in plan.farm_line(dict(spot, patrol="random"), several=False)   # kept, not used
    assert plan.Spot.model_validate(spot).points == [[120, 80], [150, 90]]
    assert plan.Spot.model_validate(plan.new_step(5281)).nogo == []      # an old plan's spot: none


def test_the_editor_reads_back_what_the_player_types() -> None:
    assert plan_gui.parse_cells("120,80; 150 90;", 2) == [[120, 80], [150, 90]]
    assert plan_gui.parse_cells("26,219,45", 3) == [[26, 219, 45]]
    assert plan_gui.parse_cells("", 3) == []
    assert plan_gui.parse_cells("26,219", 3) is None and plan_gui.parse_cells("a,b", 2) is None
    assert plan_gui.cells_text([[120, 80], [150, 90]]) == "120,80; 150,90"
    words = plan_gui.step_text(dict(plan.new_step(5281), patrol="points", points=[[1, 2], [3, 4]],
                                    nogo=[[26, 219, 45]]), several=False)
    assert "2 own points" in words and "1 no-go circles" in words


def test_patrol_points_that_can_t_be_walked_to_are_left_out() -> None:
    # taken as typed, a point in a wall or out of reach stopped the spot, one on a door walked us
    # off the map once a lap
    import numpy as np
    from unclefarmer import atlas
    from unclefarmer.engine.botparts.farming import usable_points
    m = atlas.model(5281)
    ys, xs = np.nonzero(m.walk & ~m.portal)
    here = {"x": int(xs[len(xs) // 2]), "y": int(ys[len(ys) // 2])}
    near = [int(xs[len(xs) // 2 + 1]), int(ys[len(ys) // 2 + 1])]
    door_y, door_x = (int(v[0]) for v in np.nonzero(m.portal & m.walk))
    far = [m.w + 500, m.h + 500]                          # off the map: nothing walkable near it
    good, bad = usable_points(5281, [near, [door_x, door_y], far], here)
    if m.comp_of(near[0], near[1]) == m.comp_of(here["x"], here["y"]):
        assert good == [tuple(near)]
    assert (door_x, door_y) in bad and tuple(far) in bad


def test_the_walks_that_aren_t_the_patrol_keep_out_of_the_player_s_circles() -> None:
    # the no-kill watchdog and walking off from a boss went straight through them
    from unclefarmer.engine import bosses, watchdog
    asked: list[Any] = []

    class B:
        spot_no_go = [(50.0, 50.0, 20.0)]

        def snap(self) -> dict[str, Any]:
            return {"self": {"x": 10, "y": 10}}

        def random_cell(self, map_id: int, area: Any, me: Any, radius: int = 40, *,
                        clear_of: Any = (), keep_out: Any = ()) -> tuple[int, int] | None:
            asked.append(list(clear_of))
            return (12, 12)
    b: Any = B()
    watchdog.elsewhere(b, 5281, None)
    bosses._furthest(b, 5281, None, {"x": 10, "y": 10}, [{"x": 0, "y": 0}], 5.0)   # pyright: ignore[reportPrivateUsage]
    assert asked and all(c == [(50.0, 50.0, 20.0)] for c in asked)
    assert bosses.player_no_go(object()) == []
