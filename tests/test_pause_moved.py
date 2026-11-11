"""Pausing a run against the pretend game (tests/simgame.py): a pause longer than the agent's idle
limit keeps the run's link, and a character the player moved a couple of tiles while paused goes
on from where it now stands - in a farm and in the middle of a long walk."""

from __future__ import annotations

from collections.abc import Callable

import pytest

import simgame

IDLE = 120.0          # the agent's idle limit (StateServer.IDLE_MS), in game seconds
PAUSE = 5.0           # minutes each pause lasts: well past it


def pause_and_move(game: simgame.SimGame, s: simgame.Soak, at: float, steps: int, moved: list[int]) -> None:
    """At game minute `at`, pause; PAUSE minutes later move the character `steps` tiles the first
    open way (east, west, south, north, then the slants) and go on; each move made goes into
    `moved` (a spot walled in on every side stays put). The pretend game only sees time pass as
    it is asked something, so the going on is itself proof the paused run kept asking."""
    def pause() -> None:
        bot = s.run.bot
        assert bot is not None
        bot.paused.set()

    def move_and_go_on() -> None:
        bot = s.run.bot
        assert bot is not None
        w = game.world
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1), (1, 1), (-1, -1), (1, -1), (-1, 1)):
            nx, ny = w.x + dx * steps, w.y + dy * steps
            if w.model.ok(simgame.tile(nx), simgame.tile(ny)):
                w.x, w.y = nx, ny
                moved.append(steps if dy == 0 or dx == 0 else round(steps * 2 ** 0.5))
                break
        bot.paused.clear()
    s.mark(at, pause)
    s.mark(at + PAUSE, move_and_go_on)


@pytest.mark.slow
def test_a_farm_paused_for_minutes_and_moved_goes_on(sim_env: Callable[..., simgame.SimGame]) -> None:
    game = sim_env(seed=7)
    game.idle_drop = IDLE
    s = simgame.Soak(game, "end_at here\nfarm minutes=30", watchdog=120)
    moved: list[int] = []
    for minute in (1.0, 7.03, 13.11):
        pause_and_move(game, s, minute, 2, moved)
    s.go()
    assert s.reason == "finished", list(s.lines)[-25:]
    assert len([line for line in s.said("going on") if line.endswith("  going on")]) == 3
    assert moved                                  # the test did move the character
    assert len(s.said("tiles while paused - going on from here")) == len(moved), (moved, list(s.lines)[-25:])
    assert s.said("lost the connection") == [] and not s.timed_out
    assert game.held_keys() == set()


@pytest.mark.slow
@pytest.mark.parametrize("seed", [3, 7])
def test_a_long_walk_paused_and_moved_plans_again_from_there(sim_env: Callable[..., simgame.SimGame], seed: int) -> None:
    game = sim_env(seed=seed, monsters=0)
    game.idle_drop = IDLE
    m = game.world.model
    start = (simgame.tile(game.world.x), simgame.tile(game.world.y))
    cells = [(x, y) for y in range(m.h) for x in range(m.w) if m.ok(x, y)]
    far = max(cells, key=lambda c: abs(c[0] - start[0]) + abs(c[1] - start[1]))
    s = simgame.Soak(game, "walk %d %d\nwalk %d %d" % (far + start), watchdog=120)
    moved: list[int] = []
    for sec in (6, 60 * PAUSE + 12):          # both on the way out, mid-hop
        pause_and_move(game, s, sec / 60, 2, moved)
    s.go()
    assert s.reason == "finished", list(s.lines)[-25:]
    assert moved
    assert len(s.said("tiles while paused - going on from here")) == len(moved), (moved, list(s.lines)[-25:])
    assert s.said("stuck") == [] and s.said("lost the connection") == []
    w = game.world
    assert (simgame.tile(w.x), simgame.tile(w.y)) == start      # both walks still got there
