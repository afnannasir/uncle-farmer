# pyright: reportPrivateUsage=false, reportUnusedImport=false
"""No trip to town and back when a run starts where it farms.

  - "Skip this list when the run starts on its first farming map" is ticked by default, and ticked
    once for profiles saved before (they all held the old default, unticked). A player who unticks
    it afterwards keeps it unticked.
  - Updating shard prices in a Before or After farming list doesn't walk to the Shard Broker again
    while the prices are less than town.SHARD_PRICES_FRESH (8 hours) old: it was the job that sent a
    run started on its farming map to town and back. The Tools menu and a script still read them."""

from __future__ import annotations

import json
import os
from pathlib import Path
from typing import Any

import pytest
from conftest import Clock, as_bot

from test_shard_prices import data, market, update  # noqa: F401 (data: a fixture)
from test_triggers import FarmHereRunner, said, ticks, triggered
from unclefarmer import paths, settings
from unclefarmer.engine import town, triggers

HOUR = 3600.0


# ---------------------------------------------------------------- the skip, ticked by default
def test_a_new_profile_skips_the_before_list_on_the_farm() -> None:
    assert settings.load_profile("Nobody Yet")["skip_before_on_farm"] is True


@pytest.mark.parametrize("saved", [False, None])
def test_an_older_profile_gets_the_skip_ticked_once(saved: bool | None) -> None:
    p: dict[str, Any] = {} if saved is None else {"skip_before_on_farm": saved}
    settings._upgrade(p)
    assert p["skip_before_on_farm"] is True and p["skip_before_ticked"] is True


def test_a_skip_unticked_after_the_upgrade_stays_unticked() -> None:
    p: dict[str, Any] = {"skip_before_on_farm": False, "skip_before_ticked": True}
    settings._upgrade(p)
    assert p["skip_before_on_farm"] is False


def test_a_run_on_its_farming_map_skips_the_list_without_any_setting(monkeypatch: pytest.MonkeyPatch,
                                                                    tmp_path: Path) -> None:
    profile = ticks(before=["update_shard_prices"])
    del profile["skip_before_on_farm"]                 # never saved: the default
    run, logs, jobs = triggered(monkeypatch, tmp_path, profile, runner=FarmHereRunner)
    run.run()
    assert jobs.ran == ["FARMING"]
    assert said(logs, "the Before farming list is skipped") == 1


# ---------------------------------------------------------------- shard prices: once in 8 hours
class Stage:
    def __init__(self) -> None:
        self.clock = Clock()
        self.lines: list[str] = []

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)


def read_at(at: float) -> None:
    with open(os.path.join(paths.PROFILES, town.SHARD_PRICES_READ), "w", encoding="utf-8") as f:
        json.dump({"at": at}, f)


def test_prices_read_minutes_ago_are_not_read_again_by_a_list() -> None:
    b = Stage()
    read_at(b.clock.time() - 20 * 60)
    assert triggers._prices_fresh(as_bot(b), "update_shard_prices", b.log) is True
    assert any("read 20 min ago - not going to the Shard Broker" in line for line in b.lines)


@pytest.mark.parametrize("age", [8 * HOUR, 9 * HOUR])
def test_prices_8_hours_old_are_read_again(age: float) -> None:
    b = Stage()
    read_at(b.clock.time() - age)
    assert triggers._prices_fresh(as_bot(b), "update_shard_prices", b.log) is False


def test_prices_never_read_or_from_the_future_are_read() -> None:
    b = Stage()
    assert triggers._prices_fresh(as_bot(b), "update_shard_prices", b.log) is False
    read_at(b.clock.time() + HOUR)                    # a clock set back since
    assert triggers._prices_fresh(as_bot(b), "update_shard_prices", b.log) is False
    with open(os.path.join(paths.PROFILES, town.SHARD_PRICES_READ), "w", encoding="utf-8") as f:
        f.write("not json")
    assert town.shard_prices_age(as_bot(b)) is None


def test_other_jobs_are_never_held_back() -> None:
    b = Stage()
    read_at(b.clock.time())
    assert triggers._prices_fresh(as_bot(b), "sell_shards", b.log) is False


def test_a_list_leaves_fresh_prices_and_goes_on(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    run, logs, jobs = triggered(monkeypatch, tmp_path, ticks(before=["update_shard_prices", "sell_shards"]))
    read_at(town.clock_for(None).time() - HOUR)
    run.run()
    assert jobs.ran == ["sell_shards", "FARMING"]
    assert said(logs, "not going to the Shard Broker") == 1
    assert said(logs, "before farming: done selling shards, updating shard prices") == 1


@pytest.mark.usefixtures("data")
def test_reading_the_market_notes_the_time() -> None:
    update(market([("Level 1 Ruby Shard", 2550, 2550)]))
    age = town.shard_prices_age(as_bot(object()))   # no clock: the system one, as update()
    assert age is not None and age < 60


@pytest.mark.usefixtures("data")
def test_a_market_that_shows_no_price_notes_nothing() -> None:
    update(market([]))
    assert town.shard_prices_age(as_bot(object())) is None
