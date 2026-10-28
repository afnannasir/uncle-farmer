"""The whole bot against the pretend game (tests/simgame.py), briefly: every normal test run.

The soak tests (test_soak.py, --soak) run it for hours of game time; this runs ten game minutes
in a couple of seconds, so a change that breaks the run end to end - the connection, the scan,
the setup, the fights, the results, the end - shows up on every run of the suite."""

from __future__ import annotations

import os

import pytest

import simgame
from unclefarmer import paths
from conftest import stand_in
from unclefarmer.engine.runner import Run
from collections.abc import Callable


def errors_logged():
    path = os.path.join(paths.LOGS, "errors.log")
    return open(path, encoding="utf-8").read() if os.path.exists(path) else ""


@pytest.mark.slow
def test_ten_game_minutes_of_farming_end_to_end(sim_env: Callable[..., simgame.SimGame]):
    game = sim_env()
    s = simgame.Soak(game, "end_at here\nfarm minutes=10", watchdog=120).go()
    w = game.world
    assert s.reason == "finished", list(s.lines)[-15:]
    assert not s.timed_out and not game.errors and not game.unknown
    r = s.results()
    # the results are what the game did: the last kill and its contribution included
    assert (r["kills"], r["contribution"]) == (w.truth["kills"], w.truth["contribution"])
    assert w.truth["kills"] >= 10
    assert not game.closed_holding and game.held_keys() == set()     # the run let go of its keys
    assert errors_logged() == ""


@pytest.mark.slow
def test_a_chat_line_opening_mid_walk_costs_no_stuck(sim_env: Callable[..., simgame.SimGame]):
    game = sim_env(seed=41)
    game.world.events.chatbox = 0.5
    s = simgame.Soak(game, "end_at here\nfarm minutes=20", watchdog=120).go()
    assert game.world.events.happened.get("chatbox")
    assert game.world.truth.get("chat_closed", 0) >= game.world.events.happened["chatbox"] - 1
    assert s.said("stuck") == []
    assert s.reason == "finished"


@pytest.mark.slow
def test_a_lost_connection_ends_the_run_cleanly(sim_env: Callable[..., simgame.SimGame]):
    game = sim_env(seed=3)
    game.faults.drop = 0.001
    s = simgame.Soak(game, "end_at here\nfarm minutes=30", watchdog=120).go()
    assert game.faults.done.get("drop")
    assert "connection" in s.reason
    assert not s.timed_out and game.held_keys() == set() and errors_logged() == ""


@pytest.mark.slow
def test_drop_time_to_save_is_left_unused(sim_env: Callable[..., simgame.SimGame]):
    # Bag and supplies, Full bag: "Drop time to save (minutes)". With 60 saved, the run stops while
    # 60 minutes and the tail (the drop time keeps running for a while after we leave) are left.
    from unclefarmer import settings
    settings.save_profile(simgame.NAME, dict(settings.PROFILE_DEFAULTS, drop_keep_minutes=60))
    game = sim_env()
    game.world.drop_seconds = 70 * 60
    s = simgame.Soak(game, "end_at here\nfarm minutes=60", watchdog=120)
    at_stop: list[float] = []
    log = s.run.on_log

    def on_log(line: str, speak: bool, run: Run) -> None:
        if "drop time is nearly used up" in line:
            at_stop.append(game.world.drop_seconds)      # what was left when the bot decided
        log(line, speak, run)
    s.run.on_log = on_log
    s.go()
    tail = settings.PROFILE_DEFAULTS["drop_tail_seconds"]
    assert s.reason == "drop time used up", list(s.lines)[-10:]
    # the sheet is read every few seconds (agent 43) and checked between fights: the stop comes
    # within one fight of the mark
    assert 60 * 60 + tail - 30 <= at_stop[0] <= 60 * 60 + tail, at_stop


@pytest.mark.slow
def test_a_dungeon_visit_fights_until_the_timer_end_to_end(sim_env: Callable[..., simgame.SimGame], monkeypatch: pytest.MonkeyPatch):
    # Tools, Dungeons on Spider cave: the entrance and the supplies are tested on their own
    # (test_dungeon.py); here the visit itself runs - the farming fight in dungeon mode for the
    # cave's 15 minutes, no trips out, then out through the Action menu when the game doesn't
    # move us (the pretend game has no timer of its own).
    from unclefarmer import settings
    from unclefarmer.engine import dungeon
    from unclefarmer.engine.bot import Bot
    settings.save_profile(simgame.NAME, dict(settings.PROFILE_DEFAULTS, dungeon={"caves": ["Spider cave"]}))
    monkeypatch.setattr(dungeon, "get_supplies", stand_in(dungeon.get_supplies, lambda b, cfg, check_only=False: []))
    monkeypatch.setattr(dungeon, "level_of", stand_in(dungeon.level_of, lambda b: 80))
    monkeypatch.setattr(dungeon, "enter", stand_in(dungeon.enter, lambda b, cave: True))
    left: list[float] = []
    monkeypatch.setattr(Bot, "leave_instance", stand_in(Bot.leave_instance, lambda self: left.append(self.clock.time()) or True))
    game = sim_env()
    began = game.clock.time()
    s = simgame.Soak(game, "end_at here\ndungeon", watchdog=120).go()
    assert s.reason == "finished", list(s.lines)[-15:]
    assert not s.timed_out and not game.errors and not game.unknown
    assert len(left) == 1
    inside = (left[0] - began) / 60
    assert dungeon.MINUTES + dungeon.OVERTIME <= inside <= dungeon.MINUTES + dungeon.OVERTIME + 1, inside
    assert game.world.truth["kills"] >= 15
    said = [line for line in s.lines if "Spider cave: " in line and " kills in " in line]
    assert said, [line for line in s.lines if "Spider cave" in line]
    assert not [line for line in s.lines if "resting before" in line or "restocking" in line]
    assert dungeon.done_today(simgame.NAME) == ["Spider cave"]
    assert errors_logged() == ""
