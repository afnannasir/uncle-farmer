"""Soak tests: the whole bot against a pretend game (tests/simgame.py) for hours of game time.

    python -m pytest --soak -m soak -n 0 tests/test_soak.py         (a few minutes of real time)
    set UNCLEFARMER_SOAK_HOURS=8 && python -m pytest --soak ...     (a longer night)

Skipped in the normal run (conftest: --soak). Each test runs the real Run - connection, scan,
setup, script, fights, the end - with the game's clock running as fast as the bot can go, and
measures instead of only running:

  - memory (tracemalloc) at the start, the middle and the end: it must level off, not climb;
  - threads and open handles: back to where they were after every start and stop;
  - the results (Tally) against what the game really did: no drift;
  - the logs: nothing new in errors.log, no stuck walks;
  - keys: every run lets go of its own keys before its connection closes.

Faults are injected (a dropped connection, a stalled or garbled answer, the character killed,
the bag filled) and the run must stop or recover safely. The game's events are problems real
nights ran into; each new long-run bug from the real game becomes another event, so it stays
covered:

  critter   town ducks and hens that take no damage (a night of chasing them, 2026-10-16)
  popup     a team invite turning the walking keys into its cursor (six stucks in a row)
  chatbox   a chat line left open, swallowing every key the bot pressed
  busy      readings failing for a moment while a map loads ("busy:", "noplayer:")
  freeze    the game out of memory and frozen (2026-10-22: read as a stuck walk, then a shutdown)
  pack      several monsters at once (overnight deaths, 2026-10-07)
"""

from __future__ import annotations

import collections
import gc
import os
import threading
import tracemalloc

import pytest

import simgame
from collections.abc import Callable

from unclefarmer import paths

pytestmark = pytest.mark.soak

HOURS = float(os.environ.get("UNCLEFARMER_SOAK_HOURS", "3"))
MAP = 1503                 # Spider cave: walls to walk round, monsters in its own zones
# the app's own code: what it keeps is what must level off (not the test's or the simulator's)
APP_CODE = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "unclefarmer", "*")


@pytest.fixture
def soak_env(sim_env: Callable[..., simgame.SimGame]) -> Callable[..., simgame.SimGame]:
    return sim_env           # conftest.sim_env: a pretend game maker, the app as the player runs it


def errors_logged() -> str:
    path = os.path.join(paths.LOGS, "errors.log")
    if not os.path.exists(path):
        return ""
    with open(path, encoding="utf-8") as f:
        return f.read()


def farm(minutes: float) -> str:
    return "end_at here\nfarm minutes=%g" % minutes


def assert_clean(s: simgame.Soak, game: simgame.SimGame, dropped: bool = False) -> None:
    """What every run must leave behind, however it ended."""
    assert not s.timed_out, "the run never ended: last lines %s" % s.lines[-10:]
    assert not game.errors, game.errors
    assert not game.unknown, "commands the simulator can't answer: %s" % game.unknown
    if not dropped:
        assert not game.closed_holding, "keys still held when the run's connection closed: %s" % game.closed_holding
    assert game.held_keys() == set()
    assert not s.said("stuck"), s.said("stuck")[:5]
    assert errors_logged() == ""


def assert_no_drift(s: simgame.Soak, game: simgame.SimGame) -> None:
    r, truth = s.results(), game.world.truth
    assert (r["kills"], r["contribution"], r["deaths"]) == (truth["kills"], truth["contribution"], truth["deaths"])


def settle(baseline_threads: int, seconds: float=3.0) -> int:
    """Threads the run started (the game's connection threads among them) take a moment to end."""
    import time
    end = time.monotonic() + seconds
    while threading.active_count() > baseline_threads and time.monotonic() < end:
        time.sleep(0.05)
    gc.collect()
    return threading.active_count()


def settled_handles() -> int:
    """Open handles once closed sockets and finished threads have been let go of."""
    import time
    time.sleep(0.3)
    gc.collect()
    return simgame.handles()


# ---------------------------------------------------------------- a night
def test_a_night_of_farming_levels_off(soak_env: Callable[..., simgame.SimGame], monkeypatch: pytest.MonkeyPatch):
    # The bounded caches fill slowly: 512 routes take longer than a test night. Small bounds fill
    # in minutes, so whatever still grows in the second half is a leak, not a cache filling up.
    from unclefarmer import atlaslib
    monkeypatch.setattr(atlaslib.MapModel, "ROUTE_CACHE_MAX", 32)
    game = soak_env()
    s = simgame.Soak(game, farm(60 * HOURS), watchdog=1800)
    s.lines = collections.deque(maxlen=200)
    readings: dict[str, tuple[int, tracemalloc.Snapshot, int, int]] = {}
    threads0, handles0 = threading.active_count(), simgame.handles()
    tracemalloc.start(15)

    def measure(name: str):
        def take():
            gc.collect()
            snap = tracemalloc.take_snapshot().filter_traces([tracemalloc.Filter(True, APP_CODE)])
            readings[name] = (sum(st.size for st in snap.statistics("filename")), snap,
                              threading.active_count(), simgame.handles())
        return take
    for name, at in (("start", 10), ("middle", 30 * HOURS), ("end", 60 * HOURS - 5)):
        s.mark(at, measure(name))
    try:
        s.go()
    finally:
        tracemalloc.stop()
    print("\n%.1f game hours in %.0f s: %d kills, %s" % (HOURS, s.wall, s.results().get("kills", 0), game.world.truth))
    for name in ("start", "middle", "end"):
        mem, _snap, th, h = readings[name]
        print("  %-6s %7.3f MiB held by the app, %d threads, %d handles" % (name, mem / 2**20, th, h))
    grew = readings["end"][1].compare_to(readings["middle"][1], "lineno")[:8]
    print("  most grown, middle to end:\n    " + "\n    ".join(str(g) for g in grew))
    assert s.reason == "finished", s.lines[-15:]
    assert_clean(s, game)
    assert_no_drift(s, game)
    assert game.world.truth["kills"] > 60 * HOURS, "a working night kills more than one a minute"
    # Memory levels off: with the caches full early, the second half of the night may add next
    # to nothing to what the app holds.
    _start, middle, end = (readings[k][0] for k in ("start", "middle", "end"))
    assert end - middle < 192 * 1024, "memory still climbing: +%.0f KiB in the second half" % (
        (end - middle) / 1024)
    assert readings["end"][2] <= readings["start"][2], "threads grew during the night"
    assert readings["end"][3] <= readings["start"][3] + 20, "handles grew during the night"
    assert settle(threads0) <= threads0
    assert simgame.handles() <= handles0 + 20


# ---------------------------------------------------------------- starting and stopping
def test_start_and_stop_again_and_again(soak_env: Callable[..., simgame.SimGame]):
    """The player starts and stops runs all evening: each must give back its threads, its
    connection and its handles."""
    threads0, handles0 = threading.active_count(), simgame.handles()
    seen: list[tuple[int, int]] = []
    for i in range(8):
        game = soak_env(seed=20 + i)
        s = simgame.Soak(game, farm(3), watchdog=120).go()
        assert s.reason == "finished", s.lines[-10:]
        assert_clean(s, game)
        assert_no_drift(s, game)
        game.close()
        seen.append((settle(threads0), settled_handles()))
    print("\nthreads and handles after each run:", seen, "baseline", (threads0, handles0))
    assert all(t <= threads0 for t, _h in seen)
    assert seen[-1][1] <= seen[0][1] + 2, "handles climb with every start and stop: %s" % seen


# ---------------------------------------------------------------- faults
@pytest.mark.parametrize("fault, chance", [
    ("drop", 0.0005),          # the connection gone (a server kick, the game closed)
    ("delay", 0.002),          # the game stalls for seconds
    ("garble", 0.0005),        # an answer that isn't one
    ("kill", 0.00003),         # our character dies
    ("fill_bag", 0.0002),      # the bag suddenly full
])
def test_a_fault_stops_or_recovers_safely(soak_env: Callable[..., simgame.SimGame], fault: str, chance: float):
    game = soak_env(seed=31)
    setattr(game.faults, fault, chance)
    threads0 = threading.active_count()
    s = simgame.Soak(game, farm(60), watchdog=300).go()
    print("\n%s: %s, ended %r after %.1f game min" % (fault, game.faults.done, s.reason,
                                                   (game.clock.time() - s.run.started) / 60))
    assert game.faults.done.get(fault), "the fault never happened: raise its chance"
    assert_clean(s, game, dropped=fault in ("drop", "garble"))
    if fault == "drop":
        assert "connection" in s.reason
    elif fault == "garble":
        assert s.reason          # stopped, with a reason, and nothing crashed (errors.log is empty)
    elif fault == "delay":
        assert s.reason == "finished"
        assert_no_drift(s, game)
    elif fault == "kill":
        assert game.world.truth["deaths"] >= 1
        assert s.results()["deaths"] == game.world.truth["deaths"]
        assert game.world.dead is False              # every death answered at the respawn window
        # back to farming after each one - or, after a few, the bot's own rule ends the run
        assert s.reason == "finished" or "this place may be too hard" in s.reason, s.lines[-15:]
    elif fault == "fill_bag":
        assert s.reason == "the bag is full"
    assert settle(threads0) <= threads0


# ---------------------------------------------------------------- the game's own surprises
def test_a_night_of_the_games_surprises(soak_env: Callable[..., simgame.SimGame]):
    game = soak_env(seed=41)
    ev = game.world.events
    ev.critter, ev.popup, ev.chatbox, ev.busy, ev.pack = 0.3, 0.15, 0.15, 0.3, 0.1
    s = simgame.Soak(game, farm(120), watchdog=600).go()
    w = game.world
    print("\nevents %s, truth %s, ended %r" % (ev.happened, w.truth, s.reason))
    assert all(ev.happened.get(k) for k in ("critter", "popup", "chatbox", "busy", "pack")), ev.happened
    assert s.reason == "finished", s.lines[-15:]
    assert_clean(s, game)
    assert_no_drift(s, game)
    assert w.dialog is None or w.dialog["kind"] == "respawn"
    assert w.truth.get("windows_closed", 0) >= ev.happened["popup"] - 1     # every invite closed
    assert w.truth.get("chat_closed", 0) >= ev.happened["chatbox"] - 1      # every chat line closed
    assert not w.chat_open or w.truth.get("chat_closed", 0)
    assert w.truth["kills"] > 60, "the surprises must not stop the farming"


def test_a_frozen_game_ends_the_run_as_frozen(soak_env: Callable[..., simgame.SimGame]):
    game = soak_env(seed=51)
    game.world.events.freeze = 0.05
    s = simgame.Soak(game, farm(240), watchdog=300).go()
    print("\nfroze after %.1f game min, ended %r" % ((game.clock.time() - s.run.started) / 60, s.reason))
    assert game.world.events.happened.get("freeze")
    assert "ran out of memory" in s.reason
    assert_clean(s, game)
