# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Results bookkeeping: bag and sheet tracking, level-ups, the saved record and the reports.

Nothing here reads a real game: a fake bot hands the tally bag rows, a character sheet and counters.
"""

from __future__ import annotations

import time

import pytest

from unclefarmer import stats
from pathlib import Path
from conftest import as_parts, Clock
from typing import Any


class FakeBot:
    def __init__(self, rows: list[dict[str, Any]] | None = None, me: dict[str, Any] | None = None, mid: int = 0,
                 kills: int = 0, deaths: int = 0) -> None:
        self.rows = rows or []
        self.mine = me or {}
        self.mid = mid
        self.kills = kills
        self.deaths = deaths

    def bag_rows(self) -> list[dict[str, Any]]:
        return self.rows

    def me(self) -> dict[str, Any]:
        return self.mine

    def map_id(self) -> int:
        return self.mid


def sheet(level: int=10, contribution: int=100, xp_needed: int=1000, merit: int=5) -> dict[str, int]:
    return {"level": level, "contribution": contribution, "xp_needed": xp_needed, "merit": merit}


# ---------------------------------------------------------------- reading
def test_tally_uses_its_clock(clock: Clock) -> None:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    clock.advance(90)
    t.finish()
    assert t.result()["minutes"] == 1.5


def test_update_is_throttled_until_forced() -> None:
    b = FakeBot(rows=[{"name": "Arnebia", "count": 3}], me={"sheet": sheet()}, mid=5302, kills=1)
    t = stats.Tally("Hero", "Wolves")
    t.update(as_parts(b))
    assert t.bag == {"Arnebia": 3} and t.bag0 == {"Arnebia": 3}
    b.rows = [{"name": "Arnebia", "count": 9}]
    t.update(as_parts(b))                                   # too soon: nothing reread
    assert t.bag == {"Arnebia": 3}
    t.update(as_parts(b), force=True)
    assert t.bag == {"Arnebia": 9}


def test_update_swallows_a_broken_reading(allow_ignored_failures: None) -> None:
    class Bad(FakeBot):
        def bag_rows(self) -> list[dict[str, Any]]:
            raise RuntimeError("the game closed")

    t = stats.Tally("Hero", "Wolves")
    t.update(as_parts(Bad()), force=True)
    assert t.bag == {} and t.bag0 is None and t.sheet is None


def test_update_collects_each_map_once() -> None:
    b = FakeBot(me={"sheet": sheet()}, mid=42)
    t = stats.Tally("Hero", "Wolves")
    t.update(as_parts(b), force=True)
    t.update(as_parts(b), force=True)
    assert t.maps == [42]


def test_finish_updates_with_the_bot_even_when_throttled() -> None:
    b = FakeBot(rows=[{"name": "Arnebia", "count": 4}], me={"sheet": sheet()}, mid=1, kills=2)
    t = stats.Tally("Hero", "Wolves")
    t.last_update = time.time()                   # a normal update would now be skipped
    t.finish(as_parts(b))
    assert t.bag == {"Arnebia": 4} and t.kills == 2 and t.ended is not None


# ---------------------------------------------------------------- the sheet
def test_note_sheet_tracks_experience_and_levels() -> None:
    t = stats.Tally("Hero", "Wolves")
    t.note_sheet(None)
    t.note_sheet({"level": -1})
    assert t.sheet is None and t.sheet0 is None
    t.note_sheet(sheet(level=5, xp_needed=1000))
    assert t.sheet0 == {"contribution": 100, "xp_needed": 1000, "level": 5, "merit": 5}
    assert t.xp == 0 and t.levels == 0
    t.note_sheet(sheet(level=5, xp_needed=1000))  # unchanged: nothing counted twice
    assert t.xp == 0
    t.note_sheet(sheet(level=5, xp_needed=800))   # experience came in
    assert t.xp == 200 and t.levels == 0
    t.note_sheet(sheet(level=6, xp_needed=500))   # a level up adds the old level's remaining cost
    assert t.levels == 1 and t.xp == 1000
    t.note_sheet(sheet(level=5, xp_needed=700))   # a level going down only changes the sheet
    assert t.sheet is not None and t.levels == 1 and t.xp == 1000 and t.sheet["level"] == 5


def test_note_sheet_ignores_unknown_cost() -> None:
    t = stats.Tally("Hero", "Wolves")
    t.note_sheet(sheet(level=5, xp_needed=-1))
    t.note_sheet(sheet(level=5, xp_needed=100))   # prev cost unknown: no experience guessed
    assert t.xp == 0


# ---------------------------------------------------------------- result
def test_result_adds_up_bag_and_sheet() -> None:
    b = FakeBot(rows=[{"name": "Arnebia", "count": 5}, {"name": "Green Shard", "count": 2},
                      {"name": "Bread", "count": 1}],
                me={"sheet": sheet(level=10, contribution=100, xp_needed=1000, merit=5)},
                mid=5302, kills=3, deaths=1)
    t = stats.Tally("Hero", "Wolves")
    t.prices = {"green shard": 50}
    t.update(as_parts(b), force=True)
    b.rows = [{"name": "Arnebia", "count": 2}, {"name": "Green Shard", "count": 3},
              {"name": "Bread", "count": 1}, {"name": "Elixir", "count": 2}]
    b.mine = {"sheet": sheet(level=10, contribution=250, xp_needed=600, merit=9)}
    b.kills, b.deaths = 7, 2
    t.update(as_parts(b), force=True)
    r = t.result()
    assert r["character"] == "Hero" and r["run"] == "Wolves"
    assert r["arnebia"] == -3                     # spent: net
    assert r["shards"] == {"Green Shard": 1}
    assert r["items"] == {"Elixir": 2}            # bread unchanged: not listed
    assert r["shard_value"] == 50
    assert r["contribution"] == 150 and r["merit"] == 4
    assert r["experience"] == 400 and r["levels"] == 0
    assert r["level"] == 10 and r["xp_needed"] == 600
    assert r["kills"] == 7 and r["deaths"] == 2 and r["maps"] == [5302]


def test_result_uses_the_latest_bag_after_a_reset() -> None:
    b = FakeBot(rows=[{"name": "Arnebia", "count": 5}], me={"sheet": sheet()})
    t = stats.Tally("Hero", "Wolves")
    t.update(as_parts(b), force=True)
    b.rows = []
    t.update(as_parts(b), force=True)                        # an empty reading keeps the last bag
    assert t.result()["arnebia"] == 0


@pytest.mark.parametrize("name, want", [
    ("Green Shard", True), ("SHARDY", True), ("shard", True), ("Arnebia", False), ("", False),
])
def test_is_shard(name: str, want: bool) -> None:
    assert stats.is_shard(name) is want


def test_prices_come_from_the_shard_file_and_survive_a_broken_one(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    prices = stats._prices()
    assert isinstance(prices, dict) and prices
    assert all(isinstance(v, int) for v in prices.values())
    monkeypatch.setattr(stats.paths, "DATA", str(tmp_path))
    (tmp_path / "shards.json").write_text("{not json", encoding="utf-8")
    assert stats._prices() == {}
    (tmp_path / "shards.json").unlink()
    assert stats._prices() == {}


# ---------------------------------------------------------------- saving
def test_save_keeps_quiet_when_nothing_happened(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    t = stats.Tally("Hero Two", "Wolves")
    t.finish()
    assert t.ended is not None
    t.started = t.ended                            # under half a minute, nothing gained
    t.save()
    assert stats.characters() == []
    assert stats.runs("Hero Two") == []


def test_save_appends_runs_and_they_can_be_read_back(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    t = stats.Tally("Hero Two", "Wolves")
    t.finish()
    assert t.ended is not None
    t.started = t.ended
    t.parts["farming"].kills = 12
    t.parts["farming"].changes = {"Arnebia": 7}
    t.save()
    assert stats.characters() == ["Hero Two"]
    got = stats.runs("Hero Two")
    assert len(got) == 1 and got[0]["kills"] == 12 and got[0]["arnebia"] == 7
    t.save()
    assert len(stats.runs("Hero Two")) == 2


def test_characters_without_a_results_folder(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path / "missing"))
    assert stats.characters() == []


def test_runs_skips_corrupt_lines_and_filters_by_time(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    (tmp_path / "Hero.jsonl").write_text(
        "{not json\n" + stats.json.dumps({"start": "2020-01-01 00:00:00", "kills": 1}) + "\n",
        encoding="utf-8")
    assert [r["kills"] for r in stats.runs("Hero")] == [1]
    assert stats.runs("Hero", since=time.time()) == []
    assert stats.runs("Nobody") == []


# ---------------------------------------------------------------- adding up and reports
def test_add_up_merges_and_keeps_the_last_level() -> None:
    a = {"minutes": 1.0, "kills": 2, "shards": {"Green Shard": 1}, "items": {"Bread": 2},
         "level": 5, "xp_needed": 900}
    b = {"minutes": 2.0, "kills": 3, "deaths": 1, "shards": {"Green Shard": 2, "Ruby Shard": 1},
         "items": {"Bread": -1}, "level": 6, "xp_needed": 500}
    t = stats.add_up([a, b])
    assert t["runs"] == 2 and t["minutes"] == 3.0 and t["kills"] == 5 and t["deaths"] == 1
    assert t["shards"] == {"Green Shard": 3, "Ruby Shard": 1}
    assert t["items"] == {"Bread": 1}
    assert (t["level"], t["xp_needed"]) == (6, 500)
    assert stats.add_up([])["runs"] == 0


def test_rate_is_only_known_after_five_minutes() -> None:
    assert stats._rate(10, 4.9) is None
    assert stats._rate(10, 5) == stats._n(120)
    assert stats._n(1234567) == "1,234,567"


def test_report_lists_what_matters() -> None:
    r = {"minutes": 65, "kills": 10, "deaths": 1, "runs": 3, "arnebia": 2500, "contribution": 400,
         "experience": 12000, "levels": 2, "level": 42, "xp_needed": 8000, "merit": 30,
         "shard_value": 4000, "shards": {"Green Shard": 3, "Ruby Shard": -1},
         "items": {"Bread": 4, "Elixir": -2}}
    text = stats.report(r, "Wolves")
    assert text.startswith("Wolves")
    assert "Time: 1 h 5 min, 10 kills, 1 deaths" in text
    assert "Runs: 3" in text
    assert "Arnebia: +2,500" in text and "an hour" in text
    assert "Levels gained: 2, now level 42" in text
    assert "Next level needs 8,000 more experience" in text
    assert "Shards picked up: 3" in text and "worth about 4,000 arnebia" in text
    assert "    +3 Green Shard" in text
    assert "Other items picked up:" in text and "    +4 Bread" in text
    assert "Used up:" in text and "    2 Elixir" in text and "    1 Ruby Shard" in text   # an old record
    assert text.index("Contribution") < text.index("Arnebia") < text.index("Shards picked up")


def test_report_of_nothing_says_so() -> None:
    text = stats.report({"minutes": 0, "kills": 0, "deaths": 0}, "Idle")
    assert text.startswith("Idle")
    assert "Nothing gained yet." in text


def test_report_hides_a_zero_rate_for_a_short_run() -> None:
    text = stats.report({"minutes": 4, "kills": 10, "arnebia": 1}, "Quick")
    assert "Arnebia: +1" in text
    assert "an hour" not in text                   # too short to be meaningful


def test_short_speaks_only_what_changed() -> None:
    assert stats.short({"kills": 0}) == "0 kills"
    s = stats.short({"kills": 5, "arnebia": 100, "shards": {"Green Shard": 2, "Ruby Shard": -1},
                     "contribution": 10, "experience": 20, "levels": 1})
    assert "5 kills" in s and "100 arnebia" in s and "2 shards" in s
    assert "10 contribution" in s and "20 experience" in s and "1 level up" in s
    assert "2 levels up" in stats.short({"kills": 1, "levels": 2})


# ---------------------------------------------------------------- combat damage
ME = 1001


def cast(caster: int, hits: list[Any | list[int]], skill: str="Level 5 Aimed Shot", t: int=1000) -> dict[str, Any]:
    return {"t": t, "caster": caster, "seq": 1, "skill": skill, "hits": hits}


def test_combat_counts_crits_breaks_normals_and_misses() -> None:
    c = stats.CombatStats()
    c.observe([cast(ME, [[-1, 3, 100], [-2, 5, 400], [-1, 4, 250], [-3, 2, 0]])], ME)
    r = c.result()
    assert r is not None
    got = r["given"]
    assert (got["hits"], got["misses"], got["crits"], got["breaks"], got["normals"]) == (3, 1, 1, 1, 1)
    assert got["damage"] == 750
    assert got["best"] == {"damage": 400, "kind": "crit", "skill": "Aimed Shot", "target": {"id": -2, "name": None}}
    assert got["best_normal"]["damage"] == 100 and got["best_crit"]["damage"] == 400
    assert got["best_break"]["damage"] == 250
    assert got["last"]["damage"] == 250 and got["last_crit"]["damage"] == 400
    assert got["last_break"]["damage"] == 250


def test_combat_counts_what_monsters_do_to_us_by_name() -> None:
    c = stats.CombatStats()
    c.observe([cast(-1, [[ME, 3, 90], [ME, 5, 300], [ME, 2, 0]], skill="Fire Needles")],
              ME, {-1: "cactus"})
    r = c.result()
    assert r is not None
    got = r["received"]
    assert (got["hits"], got["misses"], got["crits"], got["damage"]) == (2, 1, 1, 390)
    assert got["best"]["target"] == {"id": -1, "name": "cactus"}
    assert got["by_monster"]["cactus"]["damage"] == 390
    assert got["by_monster"]["cactus"]["crits"] == 1


def test_combat_counts_an_empty_hit_list_as_a_miss() -> None:
    c = stats.CombatStats()
    c.observe([cast(ME, [], skill="Strong-Arm Aura")], ME)
    r = c.result()
    assert r is not None
    got = r["given"]
    assert got["hits"] == 0 and got["misses"] == 1
    c.observe([{"t": 1, "caster": ME, "seq": 1, "skill": "Fishing"}], ME)
    assert c.result() is not None               # still the empty hit list is data
    c2 = stats.CombatStats()
    c2.observe([{"t": 1, "caster": ME, "seq": 1, "skill": "Fishing"}], ME)
    assert c2.result() is None


def test_tally_carries_combat_and_the_last_kill() -> None:
    t = stats.Tally("Hero", "Wolves")
    assert t.result()["combat"] is None
    t.combat.observe([cast(ME, [[-1, 3, 50]])], ME)
    t.combat.note_kill("Wolf")
    got = t.result()["combat"]
    assert got["given"]["damage"] == 50 and got["last_kill"]["name"] == "Wolf"


def test_add_up_merges_combat_and_keeps_the_biggest_hit() -> None:
    a = {"combat": {"given": {"hits": 1, "misses": 0, "crits": 0, "breaks": 0, "normals": 1,
                              "damage": 100, "best": {"damage": 100, "kind": "normal"},
                              "best_normal": {"damage": 100, "kind": "normal"},
                              "best_crit": None, "best_break": None,
                              "last": {"damage": 100}, "last_crit": None, "last_break": None,
                              "by_monster": {}},
                   "received": {"hits": 0, "misses": 0, "crits": 0, "breaks": 0, "normals": 0,
                                "damage": 0, "best": None, "best_normal": None, "best_crit": None,
                                "best_break": None, "last": None, "last_crit": None,
                                "last_break": None, "by_monster": {"cactus": {"hits": 1, "damage": 5}}},
                   "last_kill": {"name": "Wolf"}}}
    b = {"combat": {"given": {"hits": 1, "misses": 1, "crits": 1, "breaks": 0, "normals": 0,
                              "damage": 500, "best": {"damage": 500, "kind": "crit"},
                              "best_normal": None, "best_crit": {"damage": 500, "kind": "crit"},
                              "best_break": None, "last": {"damage": 500},
                              "last_crit": {"damage": 500}, "last_break": None, "by_monster": {}},
                   "received": {"hits": 1, "misses": 0, "crits": 0, "breaks": 0, "normals": 1,
                                "damage": 7, "best": {"damage": 7}, "best_normal": {"damage": 7},
                                "best_crit": None, "best_break": None, "last": {"damage": 7},
                                "last_crit": None, "last_break": None, "by_monster": {}},
                   "last_kill": {"name": "Bear"}}}
    got = stats.add_up([a, b])["combat"]
    assert got["given"]["hits"] == 2 and got["given"]["misses"] == 1 and got["given"]["damage"] == 600
    assert got["given"]["best"]["damage"] == 500 and got["given"]["last_crit"]["damage"] == 500
    assert got["received"]["damage"] == 7 and got["received"]["by_monster"]["cactus"]["damage"] == 5
    assert got["last_kill"]["name"] == "Bear"


def test_report_shows_combat_or_null() -> None:
    r: dict[str, Any] = {"minutes": 10, "kills": 1, "deaths": 0, "combat": None}
    assert "Damage data: null" in stats.report(r, "Idle")
    r["combat"] = {"given": {"hits": 2, "misses": 1, "crits": 1, "breaks": 0, "normals": 1,
                             "damage": 1500, "best": {"damage": 1000, "kind": "crit",
                                                      "skill": "Aimed Shot",
                                                      "target": {"id": -1, "name": "Wolf"}},
                             "best_normal": {"damage": 500, "kind": "normal"},
                             "best_crit": {"damage": 1000, "kind": "crit"}, "best_break": None,
                             "last": {"damage": 500, "kind": "normal"},
                             "last_crit": {"damage": 1000, "kind": "crit"}, "last_break": None},
                    "received": {"hits": 1, "misses": 0, "crits": 1, "breaks": 0, "normals": 0,
                                 "damage": 300, "best": {"damage": 300, "kind": "crit",
                                                         "skill": "Fire Needles",
                                                         "target": {"id": -1, "name": "cactus"}},
                                 "best_normal": None, "best_crit": {"damage": 300, "kind": "crit"},
                                 "best_break": None, "last": {"damage": 300, "kind": "crit"},
                                 "last_crit": {"damage": 300, "kind": "crit"}, "last_break": None,
                                 "by_monster": {"cactus": {"hits": 1, "misses": 0, "crits": 1,
                                                           "breaks": 0, "normals": 0, "damage": 300,
                                                           "best": None, "best_normal": None,
                                                           "best_crit": None, "best_break": None,
                                                           "last": None, "last_crit": None,
                                                           "last_break": None}}},
                    "last_kill": {"name": "Wolf"}}
    text = stats.report(r, "Wolves")
    assert "Damage given: 1,500 (2 hits, 1 misses)" in text
    assert "Critical hits: 1, defence breaks: 0, normal hits: 1" in text
    assert "Best hit: 1,000 (critical) with Aimed Shot on Wolf" in text
    assert "Best normal hit: 500 (normal)" in text
    assert "Last critical hit: 1,000 (critical)" in text
    assert "Last defence break: null" in text
    assert "Monster hits: 1 hits (1 critical, 0 defence breaks, 0 normal), 0 misses" in text
    assert "Worst hit received: 300 (critical) with Fire Needles from cactus" in text
    assert "cactus: 300 damage (1 hits, 1 critical, 0 defence breaks)" in text
    assert "Last monster killed: Wolf" in text


# ---------------------------------------------------------------- what the run was doing
def bag(**items: int) -> list[dict[str, Any]]:
    return [{"name": n.replace("_", " "), "count": c} for n, c in items.items()]


def test_a_pause_counts_nothing_the_player_does_by_hand(clock: Clock) -> None:
    b = FakeBot(rows=bag(Arnebia=100, Green_Shard=0), me={"sheet": sheet(contribution=100)}, kills=0)
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.update(as_parts(b), force=True)
    b.rows, b.kills = bag(Arnebia=150, Green_Shard=5), 4        # farming
    b.mine = {"sheet": sheet(contribution=140)}
    clock.advance(600)
    t.pause(as_parts(b))
    b.rows, b.kills = bag(Arnebia=900, Green_Shard=50), 30      # the player, by hand
    b.mine = {"sheet": sheet(contribution=500)}
    clock.advance(300)
    t.resume(as_parts(b))
    b.rows, b.kills = bag(Arnebia=910, Green_Shard=52), 31      # farming again
    clock.advance(60)
    t.finish(as_parts(b))
    r = t.result()
    assert r["arnebia"] == 60 and r["picked"] == {"Green Shard": 7}
    assert r["kills"] == 5 and r["contribution"] == 40
    assert r["minutes"] == 11.0 and r["paused_minutes"] == 5.0
    assert "Paused: 5 min, not counted" in stats.report(r, "Wolves")


def test_casts_while_paused_or_on_a_job_are_no_farming_damage(clock: Clock) -> None:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.pause(None)
    t.combat.observe([cast(ME, [[-1, 3, 50]])], ME)
    t.combat.note_kill("Wolf")
    assert t.result()["combat"] is None
    t.resume(None)
    with t.activity(as_parts(None), "chores"):
        t.combat.observe([cast(ME, [[-1, 3, 70]])], ME)
    t.combat.observe([cast(ME, [[-1, 3, 20]])], ME)
    assert t.result()["combat"]["given"]["damage"] == 20


def test_a_job_inside_a_farming_run_is_its_own_record(clock: Clock) -> None:
    b = FakeBot(rows=bag(Arnebia=1000, Level_1_Ruby_Shard=8), me={"sheet": sheet()})
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.update(as_parts(b), force=True)
    b.rows = bag(Arnebia=1100, Level_1_Ruby_Shard=12)          # farmed 4 shards, 100 arnebia
    clock.advance(120)
    with t.activity(as_parts(b), "gem_maker"):
        b.rows = bag(Arnebia=600, Level_1_Ruby_Shard=0, Level_2_Ruby=3)
        clock.advance(60)
    clock.advance(120)
    t.finish(as_parts(b))
    farm, gems = t.results()
    assert farm["kind"] == "farming" and farm["picked"] == {"Level 1 Ruby Shard": 4} and farm["arnebia"] == 100
    assert farm["minutes"] == 4.0
    assert gems["kind"] == "tool" and gems["tool"] == "gem_maker" and gems["run"] == "Gem Maker"
    assert gems["used"] == {"Level 1 Ruby Shard": 12} and gems["picked"] == {"Level 2 Ruby": 3}
    assert gems["arnebia"] == -500 and gems["minutes"] == 1.0 and "combat" not in gems
    text = stats.report(gems, "Gem Maker")
    assert "Made or received:" in text and "    +3 Level 2 Ruby" in text and "    12 Level 1 Ruby Shard" in text
    assert "kills" not in text


def test_a_tools_menu_run_is_a_job_not_farming(clock: Clock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    b = FakeBot(rows=bag(Arnebia=1000, Level_1_Ruby_Shard=8), me={"sheet": sheet()}, kills=0)
    t = stats.Tally("Hero", "Gem Maker", clock=clock, tool="gem_maker")
    t.update(as_parts(b), force=True)
    b.rows, b.kills = bag(Arnebia=800, Level_2_Ruby=2), 3      # a monster on the way is no farming
    clock.advance(120)
    t.finish(as_parts(b))
    t.save()
    assert stats.runs("Hero") == []
    jobs = stats.runs("Hero", kind=stats.TOOL)
    assert len(jobs) == 1 and jobs[0]["kills"] == 0 and jobs[0]["picked"] == {"Level 2 Ruby": 2}
    text = stats.report_tools(jobs, "Hero, today")
    assert text.startswith("Hero, today") and "Gem Maker" in text and "Runs: 1" in text


def test_shards_thrown_sold_or_stored_were_still_picked_up(clock: Clock) -> None:
    b = FakeBot(rows=bag(Arnebia=0, Green_Shard=0, Ruby_Shard=0), me={"sheet": sheet()})
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.prices = {"green shard": 10, "ruby shard": 100}
    t.update(as_parts(b), force=True)
    b.rows = bag(Arnebia=0, Green_Shard=9, Ruby_Shard=6)
    t.update(as_parts(b), force=True)
    t.moved("thrown", "Green Shard", 9)                        # the bag manager said so
    t.moved("sold", "Ruby Shard", 2)
    t.moved("stored", "Ruby Shard", 3)
    b.rows = bag(Arnebia=500, Green_Shard=2, Ruby_Shard=1)
    t.finish(as_parts(b))
    r = t.result()
    assert r["picked"] == {"Green Shard": 11, "Ruby Shard": 6}
    assert r["shards"] == {"Green Shard": 2, "Ruby Shard": 1}  # the net change stays too
    assert r["shard_value"] == 11 * 10 + 6 * 100
    text = stats.report(r, "Wolves")
    assert "Shards picked up: 17" in text
    assert "    +11 Green Shard (9 thrown away)" in text
    assert "    +6 Ruby Shard (2 sold, 3 stored)" in text
    assert "Shards thrown away: 9" in text and "Shards sold: 2" in text
    assert "Shards put into the storehouse: 3" in text
    assert "17 shards (9 thrown away)" in stats.short(r)


def test_bought_and_withdrawn_supplies_are_not_loot(clock: Clock) -> None:
    b = FakeBot(rows=bag(Large_Red_Potion=10), me={"sheet": sheet()})
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.update(as_parts(b), force=True)
    t.moved("bought", "Large Red Potion", 200)
    t.moved("withdrawn", "Large Red Potion", 50)
    b.rows = bag(Large_Red_Potion=60)                          # 200 drunk
    t.finish(as_parts(b))
    r = t.result()
    assert r["picked"] == {} and r["used"] == {"Large Red Potion": 200}
    text = stats.report(r, "Wolves")
    assert "Bought:" in text and "    200 Large Red Potion" in text
    assert "Taken out of the storehouse:" in text and "Other items picked up" not in text


def test_old_records_of_a_tool_read_as_jobs(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    rows = [{"start": "2026-01-01 10:00:00", "run": "Gem Maker", "kills": 0, "minutes": 5,
             "shards": {"Ruby Shard": -8}, "items": {"Level 2 Ruby": 2}, "arnebia": -500},
            {"start": "2026-01-01 11:00:00", "run": "Daily chores check", "kills": 0, "minutes": 1},
            {"start": "2026-01-01 12:00:00", "run": "monkeys", "kills": 40, "minutes": 60,
             "shards": {"Ruby Shard": 5}, "items": {}}]
    (tmp_path / "Hero.jsonl").write_text("".join(stats.json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    assert [r["run"] for r in stats.runs("Hero")] == ["monkeys"]
    jobs = stats.runs("Hero", kind=stats.TOOL)
    assert [r["tool"] for r in jobs] == ["gem_maker", "chores"]
    assert jobs[0]["used"] == {"Ruby Shard": 8} and jobs[0]["picked"] == {"Level 2 Ruby": 2}
    assert len(stats.runs("Hero", kind=None)) == 3
    assert stats.add_up(stats.runs("Hero"))["picked"] == {"Ruby Shard": 5}


def test_a_script_that_only_ran_a_job_saves_no_empty_farming_record(clock: Clock, monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    b = FakeBot(rows=bag(Arnebia=100), me={"sheet": sheet()})
    t = stats.Tally("Hero", "errands", clock=clock)
    t.update(as_parts(b), force=True)
    clock.advance(60)
    with t.activity(as_parts(b), "chores"):
        b.rows = bag(Arnebia=5000)
        clock.advance(120)
    t.finish(as_parts(b))
    t.save()
    assert stats.runs("Hero") == []
    assert [r["arnebia"] for r in stats.runs("Hero", kind=stats.TOOL)] == [4900]


def test_doing_and_moved_leave_a_run_without_results_alone() -> None:
    class NoTally:
        tally = None
    with stats.doing(as_parts(NoTally()), "chores"):
        stats.moved(as_parts(NoTally()), "thrown", "Green Shard", 3)
