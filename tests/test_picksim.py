"""The forward simulation of target picking (picksim.py, master plan Phase 2): whole cave visits whose
every pick goes through the bot's own picking.choose with the order under test."""

from unclefarmer import atlas, picksim
from unclefarmer.engine import picking


def test_the_bot_order_through_choose_plays_like_the_cave_simulator() -> None:
    loop = atlas.patrol_loop(1539, "quick")
    assert loop is not None
    a = picksim.PickVisit(1539, loop, 19.5, 1.36, picksim.CHASE, seed=5).run(minutes=2)
    b = picksim.PickVisit(1539, loop, 19.5, 1.36, picksim.CHASE, seed=5).run(minutes=2)
    assert a == b and a > 10


def test_every_order_is_scored_and_the_crowd_is_counted() -> None:
    assert set(picksim.ORDERS) >= {"bot", "straight", "toward the crowd"}
    loop = atlas.patrol_loop(1539, "quick")
    assert loop is not None
    v = picksim.PickVisit(1539, loop, 19.5, 1.36, picksim.CHASE, seed=1)
    mobs = [e for e in v.snap()["entities"] if e["kind"] == "monster"]
    assert len(mobs) == 44 and all("crowd" in e for e in mobs)
    i = v._pick()  # pyright: ignore[reportPrivateUsage]
    assert i is not None and v.mobs[i] is not None
    assert picksim.score(1539, "caster", picking.nearest, seeds=[1], minutes=1) > 5
