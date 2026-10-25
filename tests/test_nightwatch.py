"""nightwatch.py's reading side: the day log into per-character counts, gaps and wasted targets,
resource samples into per-process numbers, py-spy's raw stacks into per-thread time, and the report
from all of it. The watcher's process facts are checked on this test's own process."""

import datetime as dt
import json
import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import nightwatch as nw  # noqa: E402

LOG = """\
23:59:58  Hero: fighting Old Wolf
00:00:02  Hero: Old Wolf down (10 so far)
00:00:05  Hero: fighting Old Wolf
00:00:09  Hero: Old Wolf down (11 so far)
00:00:20  Hero: the target keys wouldn't select Old Wolf - next one
00:00:30  Hero: Old Wolf was taken by someone else - next one
00:00:31  Hero: fighting Old Wolf
00:01:40  Hero: Old Wolf down (12 so far)
00:02:00  Hero: stuck near 10,12, stepping aside
00:02:10  Hero: died on Wolf Prairie (1 so far)
00:02:20  Hero: HP 100/400 (25%) - Large Red Potion
00:30:00  Hero: last 60 min: 300 kills (300 an hour), 12.0 s a kill - searching 30%, fighting 70%
00:30:01  Hero: last 60 min: contribution +4000 (4000 an hour)
00:40:00  Hero: STOPPED: drop time used up
00:41:00  Tools: reading the bag
"""


@pytest.fixture
def logs(tmp_path, monkeypatch):
    d = tmp_path / "logs"
    d.mkdir()
    (d / "2026-10-22.log").write_text("23:59:00  Hero: starting a plan as Hero\n" + LOG.split("\n", 1)[0] + "\n",
                                      encoding="utf-8")
    (d / "2026-10-23.log").write_text("\n".join(LOG.split("\n")[1:]), encoding="utf-8")
    monkeypatch.setattr(nw, "LOGS", str(d))
    monkeypatch.setattr(nw, "ROOT", str(tmp_path))
    return d


T0 = dt.datetime(2026, 10, 22, 23, 0)
T1 = dt.datetime(2026, 10, 23, 1, 0)


def test_the_log_is_read_across_midnight_per_character(logs):
    rows = nw.read_log(T0, T1)
    assert rows[0][0].date() == dt.date(2026, 10, 22) and rows[-1][0].date() == dt.date(2026, 10, 23)
    c = nw.characters(rows)
    assert set(c) == {"Hero"}                     # a character that never fought isn't a farmer
    h = c["Hero"]
    assert h["counts"]["kill"] == 3 and h["counts"]["death"] == 1 and h["counts"]["stuck"] == 1
    assert h["counts"]["hp_potion"] == 1 and h["stops"][0][1].startswith("STOPPED")
    assert h["contribution"] == [(dt.datetime(2026, 10, 23, 0, 30, 1), 4000, 60)]


def test_gaps_between_kills_and_what_the_wasted_targets_cost(logs):
    h = nw.characters(nw.read_log(T0, T1))["Hero"]
    assert h["gaps"] == [7.0, 91.0]
    assert h["gap_stats"]["over_60s"] == 1
    assert h["wasted"] == {"no_select": 1, "taken": 1}
    assert h["wasted_s"]["no_select"] == 11.0     # from the kill before it
    assert h["wasted_s"]["taken"] == 10.0         # from the waste before it
    longest = h["long_gaps"][0]
    assert longest["seconds"] == 91.0 and any("wouldn't select" in ln for ln in longest["lines"])


def test_resources_per_process_and_growth():
    t = 1_000_000.0
    samples = [{"t": t + i * 1800, "system": {"free_mb": 9000 - i},
                "bot": {"cpu_s": 18.0 * i, "private_mb": 100 + 10 * i, "handles": 300, "threads": 9},
                "games": {"42": {"cpu_s": 360.0 * i, "private_mb": 900 + 50 * i, "handles": 800, "threads": 40}}}
               for i in range(3)]
    r = nw.resources(samples)
    assert r["procs"]["bot"]["cpu_avg_pct"] == 1.0          # 18 s of CPU every 1800 s
    assert r["procs"]["bot"]["growth_mb_per_h"] == 20.0
    assert r["procs"]["game 42"]["cpu_avg_pct"] == 20.0
    assert r["free_min_mb"] == 8998


def test_profiles_split_by_thread_with_waiting_and_agent_time(tmp_path):
    raw = "\n".join([
        "thread (7);run (unclefarmer\\engine\\runner.py:120);fight (unclefarmer\\engine\\botparts\\fighting.py:500)"
        ";json (unclefarmer\\link.py:80);readinto (socket.py:720) 30",
        "thread (7);run (unclefarmer\\engine\\runner.py:120);sleep (unclefarmer\\clock.py:40) 50",
        "thread (7);run (unclefarmer\\engine\\runner.py:120);route (unclefarmer\\routing.py:300) 20",
        "thread (9);MainLoop (wx\\core.py:2100) 100",
    ])
    (tmp_path / "p.txt").write_text(raw + "\n", encoding="utf-8")
    t = nw.profiles(str(tmp_path), {"profiles": [{"file": "p.txt"}]}, {7: "run 1234 Hero"})
    run = t["run 1234 Hero"]
    assert run["samples"] == 100 and run["waiting"] == 80 and run["agent"] == 30 and run["agent_wait"] == 30
    assert run["funcs"]["fight (unclefarmer\\engine\\botparts\\fighting.py)"] == 30
    assert run["busy_leaf"] == {"route (routing.py)": 20}
    assert t["thread 9"]["waiting"] == 100


def test_a_thread_blocked_on_the_agent_is_waiting(tmp_path):
    # socket.recv is C code: py-spy's innermost Python frame is link._ask itself
    raw = "thread (7);run (unclefarmer\\chat.py:60);json (unclefarmer\\link.py:89);_ask (unclefarmer\\link.py:78) 40"
    (tmp_path / "p.txt").write_text(raw + "\n", encoding="utf-8")
    t = nw.profiles(str(tmp_path), {"profiles": [{"file": "p.txt"}]}, {})
    assert t["thread 7"]["waiting"] == 40 and t["thread 7"]["agent_wait"] == 40


def fight(at, me, mob, dist, reach_s=None, end_s=5.0, kills=1, in_reach=0, reach=10.0, **more):
    r = {"at": at, "kind": "fight", "who": "Hero", "map": 5302, "me": me, "mob": mob, "dist": dist,
         "reach": reach, "sight": 3, "in_reach": in_reach, "hurt": False, "attacking": False,
         "end_s": end_s, "kills": kills}
    if reach_s is not None:
        r["reach_s"] = reach_s
    r.update(more)
    return r


def test_walking_from_the_field_log(logs):
    recs = [
        fight("2026-10-22 23:59:00", [100, 100], [105, 100], 5.0, reach_s=0.0),            # in reach
        fight("2026-10-23 00:00:10", [100, 100], [130, 100], 30.0, reach_s=5.0, in_reach=1),  # passed one by
        fight("2026-10-23 00:00:30", [130, 100], [160, 100], 30.0, end_s=4.0, kills=0),     # never reached
        {"at": "2026-10-23 00:01:00", "kind": "search", "who": "Hero", "map": 5302, "me": [160, 100],
         "to": [190, 100], "s": 8.0, "result": "near"},
        fight("2026-10-23 05:00:00", [100, 100], [105, 100], 5.0, reach_s=0.0),            # after the night
    ]
    (logs / "field-2026-10-22.jsonl").write_text(json.dumps(recs[0]) + "\n", encoding="utf-8")
    (logs / "field-2026-10-23.jsonl").write_text("".join(json.dumps(r) + "\n" for r in recs[1:]), encoding="utf-8")
    got = nw.read_field(T0, T1)
    assert [r["kind"] for r in got["Hero"]] == ["fight", "fight", "fight", "search"]
    w = nw.walking(got["Hero"])
    assert w is not None
    assert w["fights"] == 3 and w["kills"] == 2 and w["in_reach_at_pick"] == 1 and w["passed_by"] == 1
    assert w["walk_s"] == 5.0 and w["never"] == 1 and w["never_s"] == 4.0
    assert w["searches"] == 1 and w["search_s"] == 8.0 and w["search_results"] == {"near": 1}
    mid, spot, share, actual, rows, reach = w["stand"]
    # the monsters stood at 105, 130 and 160: no place we stood (100 or 130) had two within 10 tiles
    assert mid == 5302 and rows == 3 and reach == 10.0 and actual == pytest.approx(1 / 3)
    assert share == pytest.approx(1 / 3)


def test_the_stand_point_finds_the_spot_with_the_most_in_reach():
    rows = [fight("x", [100, 100], [125, 100], 25.0), fight("x", [120, 100], [128, 100], 8.0),
            fight("x", [100, 100], [130, 100], 30.0)]
    st = nw.stand_point(rows)
    assert st is not None
    mid, spot, share, actual, n, reach = st
    assert spot == (120, 100) and share == 1.0 and actual == pytest.approx(1 / 3)


def test_thread_names_take_the_newest_record(logs):
    (logs / "threads.log").write_text("2026-10-22 20:00:00  7  run 1 Old\n2026-10-22 23:30:00  7  run 2 Hero\n"
                                      "2026-10-23 05:00:00  7  run 3 Later\n", encoding="utf-8")
    assert nw.thread_names(T1) == {7: "run 2 Hero"}


def test_the_report_puts_it_all_together(logs, tmp_path):
    night = tmp_path / "night"
    night.mkdir()
    (night / "watch.json").write_text(json.dumps({"started": "2026-10-22 23:00:00", "ended": "2026-10-23 01:00:00",
                                                  "profiles": []}), encoding="utf-8")
    (tmp_path / "results").mkdir()
    (tmp_path / "results" / "Hero.jsonl").write_text(json.dumps(
        {"character": "Hero", "run": "wolves", "start": "2026-10-22 23:59:00", "minutes": 41.0, "kills": 3,
         "contribution": 2050, "deaths": 1}) + "\n", encoding="utf-8")
    (logs / "field-2026-10-23.jsonl").write_text(json.dumps(fight(
        "2026-10-23 00:00:10", [100, 100], [130, 100], 30.0, reach_s=5.0)) + "\n", encoding="utf-8")
    text = open(nw.report(str(night)), encoding="utf-8").read()
    assert "| Hero | " in text and "| wolves | 23:59 | 41 | 3 | 2050 | 3000 | 1 |" in text
    assert "Longest gaps between kills" in text and "No profile." in text
    assert "### Walking (field log)" in text and "Where to stand: on map 5302" in text
    assert json.loads((night / "summary.json").read_text(encoding="utf-8"))["characters"]["Hero"]["kills"] == 3


@pytest.mark.skipif(os.name != "nt", reason="Windows process calls")
def test_process_facts_of_our_own_process():
    u = nw.usage(os.getpid())
    assert u and u["private_mb"] > 1 and u["handles"] > 0 and u["cpu_s"] >= 0
    assert os.getpid() in nw.processes()
    assert nw.system_memory()["free_mb"] > 0


def test_the_app_names_its_threads_for_the_watcher(tmp_path, monkeypatch):
    import threading

    from unclefarmer import applog, paths
    monkeypatch.setattr(paths, "LOGS", str(tmp_path))
    applog.thread("run 1234 Hero")
    tid, what = open(tmp_path / "threads.log", encoding="utf-8").read().rstrip("\n").split("  ")[1:]
    assert int(tid) == threading.get_native_id() and what == "run 1234 Hero"


def test_a_watch_cut_off_by_a_shutdown_ends_at_its_last_sample(logs, tmp_path):
    night = tmp_path / "cut"
    night.mkdir()
    (night / "watch.json").write_text(json.dumps({"started": "2026-10-22 23:00:00", "ended": None,
                                                  "last_sample": "2026-10-23 00:01:00", "profiles": []}),
                                      encoding="utf-8")
    text = open(nw.report(str(night)), encoding="utf-8").read()
    assert text.startswith("# Night 2026-10-22 23:00 to 00:01")
    assert "| Hero | " in text           # the lines up to the cut are all there
