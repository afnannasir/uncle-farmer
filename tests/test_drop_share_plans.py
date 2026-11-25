"""Two maps in one day's drop time, and the results map by map.

A plan's spot can have a share of the day's drop time (engine/plan.py, DROP_DAY: 7 hours): the
spots share it out in their order, counted on the game's own drop clock (drop_left), so a run
restarted in the middle carries on where the day is and a spot whose share is used up is skipped.
The results count each farmed map on its own (stats.Tally.on_map), and say so map by map only when
a run farmed two maps or more.
"""

from __future__ import annotations

from typing import Any

from conftest import as_bot, as_parts, Clock
from test_script import FakeBot as ScriptBot

from unclefarmer import stats
from unclefarmer.engine import plan, script as S


def two_spots(first: int = 180, rotation: str = "round") -> dict[str, Any]:
    return {"name": "Two", "rotation": rotation,
            "spots": [dict(plan.new_step(5361), minutes=0, drop_minutes=first), dict(plan.new_step(5281), minutes=0)]}


# ---------------------------------------------------------------- the plan's script
def test_the_first_spot_farms_its_share_then_the_second_the_rest() -> None:
    text = plan.to_script(two_spots())
    farms = [line for line in text.splitlines() if line.startswith("farm ")]
    assert farms == ["farm map=5361 until=drop_left >= 0 and drop_left <= 240", "farm map=5281"]
    assert "repeat" not in text                  # shares run once, in order
    S.parse(text)


def test_three_spots_add_their_shares_up() -> None:
    p = two_spots(120)
    p["spots"].insert(1, dict(plan.new_step(5121), minutes=0, drop_minutes=100))
    farms = [line for line in plan.to_script(p).splitlines() if line.startswith("farm ")]
    assert farms[0].endswith("drop_left <= 300") and farms[1].endswith("drop_left <= 200")
    assert farms[2] == "farm map=5281"


def test_shares_over_the_day_end_at_no_drop_time_left() -> None:
    p = two_spots(500)
    assert plan.to_script(p).count("drop_left <= 0") == 1


def test_without_shares_or_with_one_spot_nothing_changes() -> None:
    p = two_spots(0)
    assert "drop_left" not in plan.to_script(p) and "repeat" in plan.to_script(p)
    one = {"name": "One", "spots": [dict(plan.new_step(5361), drop_minutes=180)]}
    assert plan.to_script(one).splitlines()[-1] == "farm map=5361"
    assert not plan.has_drop_shares(two_spots(0)) and plan.has_drop_shares(two_spots())


def test_a_run_restarted_later_in_the_day_skips_a_used_up_share() -> None:
    bot = ScriptBot()
    bot.mev["sheet"] = {"level": 10, "drop_seconds": 200 * 60, "age": 0}    # 200 minutes left
    S.Runner(as_bot(bot), plan.to_script(two_spots())).run()
    assert [c[1] for c in bot.calls if c[0] == "farm"] == [5281]


def test_an_unknown_drop_time_never_skips_the_first_spot() -> None:
    bot = ScriptBot()                              # no sheet reading of the drop time
    S.Runner(as_bot(bot), plan.to_script(two_spots())).run()
    assert [c[1] for c in bot.calls if c[0] == "farm"] == [5361, 5281]


# ---------------------------------------------------------------- the results, map by map
class Reader:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = [{"name": "Arnebia", "count": 0}]
        self.mid = 5361
        self.kills = 0
        self.deaths = 0

    def bag_rows(self) -> list[dict[str, Any]]:
        return self.rows

    def me(self) -> dict[str, Any]:
        return {}

    def map_id(self) -> int:
        return self.mid


def bag(arnebia: int, shards: int = 0) -> list[dict[str, Any]]:
    rows = [{"name": "Arnebia", "count": arnebia}]
    if shards:
        rows.append({"name": "Level 1 Ruby Shard", "count": shards})
    return rows


def test_each_farmed_map_counts_on_its_own() -> None:
    clock = Clock()
    t = stats.Tally("Hero", "Two", clock=clock)
    b = Reader()
    t.update(as_parts(b), force=True)
    t.on_map(5361)
    clock.advance(600)
    b.rows, b.kills = bag(1000, 4), 30
    t.update(as_parts(b), force=True)
    t.note_chest()
    b.mid = 1081                                  # a trip to town: neither its time nor its gains
    clock.advance(300)
    b.rows = bag(500, 4)
    t.update(as_parts(b), force=True)
    b.mid = 5361
    clock.advance(60)
    t.update(as_parts(b), force=True)
    t.on_map(None)
    t.on_map(5281)
    b.mid = 5281
    clock.advance(1200)
    b.rows, b.kills = bag(1300, 4), 40
    t.update(as_parts(b), force=True)
    by = t.result()["by_map"]
    assert by["5361"] == {"minutes": 11.0, "kills": 30, "deaths": 0, "chests": 1, "arnebia": 1000,
                          "shards": {"Level 1 Ruby Shard": 4}}
    assert by["5281"]["minutes"] == 20.0 and by["5281"]["kills"] == 10 and by["5281"]["arnebia"] == 800


def test_a_pause_is_not_the_maps_time() -> None:
    clock = Clock()
    t = stats.Tally("Hero", "One", clock=clock)
    b = Reader()
    t.on_map(5361)
    clock.advance(120)
    t.pause(as_parts(b))
    clock.advance(1000)
    t.resume(as_parts(b))
    clock.advance(60)
    assert t.result()["by_map"]["5361"]["minutes"] == 3.0


def test_map_by_map_is_said_only_for_two_maps_or_more() -> None:
    one: dict[str, Any] = {"minutes": 60, "kills": 400, "by_map": {"5361": {"minutes": 60, "kills": 400}}}
    assert "Map by map" not in stats.report(one, "Run")
    two: dict[str, Any] = {"minutes": 90, "kills": 500, "by_map": {
        "5361": {"minutes": 60, "kills": 400, "arnebia": 2000, "chests": 1},
        "5281": {"minutes": 30, "kills": 100, "deaths": 1}}}
    text = stats.report(two, "Run")
    assert "Map by map:" in text
    assert "    Underwater World (5361): 01:00, 400 kills (400 an hour), 2,000 arnebia, 1 chests" in text
    assert "    Margit Rainforest (5281): 00:30, 100 kills (200 an hour), 1 deaths" in text


def test_a_day_adds_the_maps_up() -> None:
    a: dict[str, Any] = {"kind": "farming", "by_map": {"5361": {"minutes": 60, "kills": 400, "shards": {"X Shard": 2}}}}
    b: dict[str, Any] = {"kind": "farming", "by_map": {"5361": {"minutes": 30, "kills": 100},
                                                    "5281": {"minutes": 10, "kills": 5}}}
    total = stats.add_up([a, b])["by_map"]
    assert total["5361"]["minutes"] == 90 and total["5361"]["kills"] == 500
    assert total["5361"]["shards"] == {"X Shard": 2} and total["5281"]["kills"] == 5


# ---------------------------------------------------------------- found in review
def test_the_kill_that_ends_a_spot_counts_for_its_map() -> None:
    """The farm read its last kill after closing its map: one kill in the run's total and none
    in the map's (replayed in review). The farm's end reads once more while the map still counts."""
    from unclefarmer.engine.botparts.farming import FarmingMixin
    clock = Clock()
    t = stats.Tally("Hero", "Two", clock=clock)
    b = Reader()
    t.update(as_parts(b), force=True)
    t.on_map(5361)
    clock.advance(60)
    b.kills = 1                                   # the kill that met the spot's limit
    FarmingMixin._farm_ends(b, t)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    t.update(as_parts(b), force=True)             # the run's own later reading
    r = t.result()
    assert r["kills"] == 1 and r["by_map"]["5361"]["kills"] == 1


def test_a_failing_last_reading_still_closes_the_map() -> None:
    from unclefarmer.engine.botparts.farming import FarmingMixin

    class Gone(Reader):
        def bag_rows(self) -> list[dict[str, Any]]:
            raise OSError("the game closed")
    t = stats.Tally("Hero", "Two", clock=Clock())
    t.on_map(5361)
    FarmingMixin._farm_ends(Gone(), t)  # type: ignore[arg-type]  # pyright: ignore[reportPrivateUsage]
    assert t.farm_map is None


def endless_first() -> dict[str, Any]:
    return {"name": "Two", "rotation": "round",
            "spots": [dict(plan.new_step(5361), minutes=0, kills=0, drop_minutes=0),
                      dict(plan.new_step(5281), minutes=0, drop_minutes=120)]}


def test_a_spot_that_never_ends_before_a_share_is_found() -> None:
    assert plan.endless_spot(endless_first()) == 0
    for fix in ({"minutes": 30}, {"kills": 100}, {"drop_minutes": 60}):
        p = endless_first()
        p["spots"][0].update(fix)
        assert plan.endless_spot(p) is None, fix
    last_free = two_spots()                       # the last spot may farm on: nothing waits for it
    assert plan.endless_spot(last_free) is None
    no_shares = endless_first()
    no_shares["spots"][1]["drop_minutes"] = 0     # no shares: the spots go round as before
    assert plan.endless_spot(no_shares) is None


def test_such_a_plan_is_not_imported(tmp_path: Any) -> None:
    import json

    import pytest
    src = tmp_path / "two.json"
    src.write_text(json.dumps(endless_first()), encoding="utf-8")
    with pytest.raises(ValueError, match="spot 1 never ends"):
        plan.read_import(str(src))


# ---------------------------------------------------------------- the Before list on the farm
def test_the_before_list_is_skipped_on_the_map_the_run_really_farms_first() -> None:
    # spot 1's share is used up: the run starts on spot 2's map. It went by spot 1 (the first
    # farm line): on spot 2's map the Before list ran, on spot 1's it was skipped - and then the
    # run went to spot 2 without its preparation
    from unclefarmer.engine.runner import Run
    bot = ScriptBot()
    bot.mev["sheet"] = {"level": 10, "drop_seconds": 200 * 60, "age": 0}    # 200 minutes left
    bot.p["skip_before_on_farm"] = True
    script = S.Runner(as_bot(bot), plan.to_script(two_spots()))
    bot.map_id = lambda: 5281                              # type: ignore[method-assign]
    assert Run._on_the_farm(as_bot(bot), script) is True  # pyright: ignore[reportPrivateUsage]
    bot.map_id = lambda: 5361                              # type: ignore[method-assign]
    assert Run._on_the_farm(as_bot(bot), script) is False  # pyright: ignore[reportPrivateUsage]
