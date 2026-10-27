"""The cave simulator behind dungeon.CHASE (caveloops.py): it runs on the caves' real walls, the
same seed gives the same visit, and its numbers stay where the logged run put them."""

from unclefarmer import atlas, caveloops
from unclefarmer.engine import dungeon


def test_a_visit_is_the_same_for_the_same_seed_and_kills():
    loop = atlas.patrol_loop(1539, "quick")
    a = caveloops.Visit(1539, loop, 19.5, 1.36, 40, seed=5).run(minutes=2)
    b = caveloops.Visit(1539, loop, 19.5, 1.36, 40, seed=5).run(minutes=2)
    assert a == b and a > 10


def test_the_time_goes_where_the_logged_run_said():
    # the real run: most of the 15 minutes walking up to monsters and killing them, little on the lap
    v = caveloops.Visit(1539, atlas.patrol_loop(1539, "quick"), 19.5, 1.36, 40, seed=1)
    v.run(minutes=5)
    assert v.spent["walk_up"] + v.spent["kill"] > 3 * v.spent["lap"]


def test_every_cave_has_its_landing_and_monsters():
    for mid in caveloops.CAVES:
        m = atlas.model(mid)
        assert m.ok(*caveloops.LANDINGS[mid])
        assert dungeon.cave_of(mid) is not None
        assert len(caveloops.spawn_cells(mid)) > 1000
