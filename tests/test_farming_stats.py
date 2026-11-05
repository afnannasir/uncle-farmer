# pyright: reportPrivateUsage=false
# White-box tests: they check stats._clock, the hours:minutes formatter, directly.
"""A farming run's time and money as the Results window shows them while it runs and after.

Time used is farming time (no pause, no job on the way); Time remaining counts down to the
earliest end known - the plan's time limit, its stop time, the daily stop - which are clock times,
so a pause doesn't move them. Earnings are the arnebia that came in (picked up, sold, and the run's
own selling jobs), expenses the items used up at their stored prices, net earnings the one less
the other; a purchase is neither until what was bought is used up."""

from __future__ import annotations

import ast
import time
from typing import Any

from conftest import Clock, as_parts
from test_stats import FakeBot, bag, sheet
from unclefarmer import stats
from unclefarmer.engine import script

POTION = "Large Red Potion"


class Bot(FakeBot):
    """The test stats' bot, with the tally a run gives it (stats.moved and stats.reading use it)."""

    def __init__(self, rows: list[dict[str, Any]], tally: stats.Tally) -> None:
        super().__init__(rows=rows, me={"sheet": sheet()})
        self.tally = tally
        tally.update(as_parts(self), force=True)


def lines_of(r: dict[str, Any]) -> list[str]:
    return stats.report(r, "Wolves").splitlines()


def priced_tally(clock: Clock) -> stats.Tally:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.unit_prices = {POTION.lower(): (100, stats.SHOP), "green shard": (40, stats.SHARD)}
    return t


# ---------------------------------------------------------------- time used and time remaining
def test_time_used_and_remaining_both_show_and_follow_the_run(clock: Clock) -> None:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.deadline(clock.time() + 3600)                         # the plan's time limit: an hour from the start
    clock.advance(600)
    text = lines_of(t.result())
    assert "Time used: 00:10, 0 kills, 0 deaths" in text and "Time remaining: 00:50" in text
    clock.advance(30)
    text = lines_of(t.result())                             # 49.5 minutes left: rounded up
    assert "Time remaining: 00:50" in text and "Time used: 00:10, 0 kills, 0 deaths" in text
    clock.advance(60)
    assert "Time remaining: 00:49" in lines_of(t.result())


def test_a_pause_stops_time_used_but_not_the_clock_the_run_ends_by(clock: Clock) -> None:
    # The time limit and the stop time are clock times (a plan's `minutes` counts from the start,
    # pauses included): a pause doesn't move the end, so the time remaining keeps going down,
    # while the time used is farming time only. Neither starts again from zero.
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.deadline(clock.time() + 3600)
    clock.advance(600)
    t.pause(None)
    clock.advance(300)
    r = t.result()
    assert r["minutes"] == 10.0 and r["remaining_minutes"] == 45.0
    assert "Time used: 00:10, 0 kills, 0 deaths" in lines_of(r) and "Time remaining: 00:45" in lines_of(r)
    t.resume(None)
    clock.advance(120)
    r = t.result()
    assert r["minutes"] == 12.0 and r["remaining_minutes"] == 43.0 and r["paused_minutes"] == 5.0
    t.pause(None)                                            # paused twice: the pauses add up
    clock.advance(60)
    t.resume(None)
    with t.activity(as_parts(None), "gem_maker"):            # a job on the way: no farming time, same end
        clock.advance(60)
    clock.advance(60)
    r = t.result()
    assert r["minutes"] == 13.0 and r["remaining_minutes"] == 40.0 and r["paused_minutes"] == 6.0


def test_a_finished_run_shows_its_time_used_and_nothing_remaining(clock: Clock) -> None:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.deadline(clock.time() + 3600)
    clock.advance(1500)
    t.finish()
    clock.advance(900)                                       # read later: the same numbers
    text = lines_of(t.result())
    assert "Time used: 00:25, 0 kills, 0 deaths" in text and "Time remaining: 00:00" in text
    assert t.result()["remaining_minutes"] == 0.0


def test_a_run_with_no_end_says_so_until_it_ends(clock: Clock) -> None:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    clock.advance(120)
    assert "Time remaining: no end time set (no time limit, stop time or daily stop)" in lines_of(t.result())
    t.finish()
    assert "Time remaining: 00:00" in lines_of(t.result())


def test_the_earliest_end_counts_and_a_passed_one_is_zero(clock: Clock) -> None:
    t = stats.Tally("Hero", "Wolves", clock=clock)
    t.deadline(clock.time() + 7200)                          # the daily stop
    t.deadline(clock.time() + 1800)                          # the plan's stop time
    t.deadline(clock.time() + 1800)                          # (the same rule again: once)
    t.deadline(0.0)                                          # (no daily stop: nothing)
    assert t.deadlines == [clock.time() + 7200, clock.time() + 1800]
    assert "Time remaining: 00:30" in lines_of(t.result())
    clock.advance(2000)                                      # past it, the run still ending
    assert t.result()["remaining_minutes"] == 0.0


def test_hours_and_minutes() -> None:
    assert stats._clock(65) == "01:05" and stats._clock(42 * 60 + 10) == "42:10"
    assert stats._clock(0.4) == "00:00" and stats._clock(0.4, up=True) == "00:01"
    assert stats._clock(None) == "00:00" and stats._clock(-3, up=True) == "00:00"


def test_old_records_show_time_used_and_no_time_remaining() -> None:
    text = lines_of({"minutes": 125, "kills": 3, "deaths": 0})
    assert "Time used: 02:05, 3 kills, 0 deaths" in text
    assert not any(line.startswith("Time remaining") for line in text)


def test_a_saved_run_keeps_its_end_numbers(clock: Clock, monkeypatch: Any, tmp_path: Any) -> None:
    monkeypatch.setattr(stats, "RESULTS", str(tmp_path))
    b = Bot(bag(Arnebia=100), stats.Tally("Hero", "Wolves", clock=clock))
    b.tally.deadline(clock.time() + 3600)
    clock.advance(900)
    b.rows = bag(Arnebia=400)
    b.tally.finish(as_parts(b))
    b.tally.save()
    (saved,) = stats.runs("Hero")
    assert saved["remaining_minutes"] == 0.0 and saved["minutes"] == 15.0 and saved["arnebia_in"] == 300
    assert "Time remaining: 00:00" in lines_of(saved)


# ---------------------------------------------------------------- where the end comes from
def rule(text: str) -> ast.expr:
    return script.compile_expr(text, 1)


def test_a_plan_s_time_limit_ends_its_minutes_after_the_start() -> None:
    assert script.end_time(rule("minutes >= 300"), 1000.0, 5000.0) == 1000.0 + 300 * 60
    assert script.end_time(rule("minutes > 90"), 1000.0, 5000.0) == 1000.0 + 90 * 60


def test_a_plan_s_stop_time_is_the_next_time_the_clock_shows_it() -> None:
    now = time.mktime((2026, 11, 5, 20, 0, 0, 0, 0, -1))
    assert script.end_time(rule('clock == "23:30"'), now, now) == time.mktime((2026, 11, 5, 23, 30, 0, 0, 0, -1))
    assert script.end_time(rule('clock == "04:50"'), now, now) == time.mktime((2026, 11, 6, 4, 50, 0, 0, 0, -1))
    assert script.end_time(rule('clock == "20:00"'), now, now + 30) == now     # its own minute: now


def test_other_rules_have_no_known_end() -> None:
    for text in ("kills >= 300", "minutes < 5", "hp_pct < 20", 'clock == "25:00"', 'clock == "soon"',
                 "minutes >= 300 and kills > 1", "300 <= minutes"):
        assert script.end_time(rule(text), 1000.0, 5000.0) is None, text
    assert script.end_time(None, 1000.0, 5000.0) is None


def test_a_script_s_stop_rule_sets_the_results_end() -> None:
    class RunBot:
        """What a script's stop_when needs of the bot."""
        def __init__(self) -> None:
            self.started = 1000.0
            self.clock = Clock(1000.0)
            self.watches: list[Any] = []
            self.tally = stats.Tally("Hero", "Wolves", clock=self.clock)

    b = RunBot()
    run = script.Runner(b, "")                               # type: ignore[arg-type]
    node = script.parse("stop_when minutes >= 30 : time limit reached")[0][0]
    run._do_stop_when(node, b, node.opts)                    # type: ignore[arg-type]
    assert b.tally.ends_at() == 1000.0 + 1800 and len(b.watches) == 1


# ---------------------------------------------------------------- earnings, expenses, net earnings
def test_earnings_expenses_and_net_from_loot_purchases_and_potions_drunk(clock: Clock) -> None:
    b = Bot(bag(Arnebia=1000), priced_tally(clock))
    b.rows = bag(Arnebia=1500)                               # 500 picked up
    stats.reading(as_parts(b))                               # (as on the way to a shop)
    b.rows = bag(Arnebia=500, Large_Red_Potion=10)           # 10 bought at 100
    stats.moved(as_parts(b), "bought", POTION, 10)
    clock.advance(60)
    b.rows = bag(Arnebia=800, Large_Red_Potion=6)            # 300 picked up, 4 drunk
    b.tally.finish(as_parts(b))
    r = b.tally.result()
    assert r["arnebia"] == -200 and r["arnebia_in"] == 800 and r["arnebia_out"] == 1000
    assert r["used"] == {POTION: 4}                          # the 6 still in the bag cost nothing yet
    assert stats.earnings(r) == (800, 400, 400)              # the purchase is no earning and no expense
    text = lines_of(r)
    gross = "Earnings so far: 800 arnebia (picked up and sold)"
    spent = "Total expenses: 400 arnebia (items used up, at their stored prices)"
    net = "Net earnings: +400 arnebia"
    assert gross in text and spent in text and net in text
    assert text.index(gross) < text.index(spent) < text.index(net)
    assert any(line.startswith("Arnebia paid out at shops and for fares: 1,000") for line in text)


def test_loot_and_a_purchase_in_one_reading_are_kept_apart_by_the_trip_s_readings(clock: Clock) -> None:
    # One reading over loot and a purchase sees only their difference: that is why moved() and
    # town.visit read the bag. Here 500 picked up and 400 spent don't come out as 100 picked up.
    b = Bot(bag(Arnebia=1000), priced_tally(clock))
    b.rows = bag(Arnebia=1500)
    b.tally.update(as_parts(b))                              # throttled: not read
    stats.reading(as_parts(b))
    b.rows = bag(Arnebia=1100, Large_Red_Potion=4)
    stats.moved(as_parts(b), "bought", POTION, 4)
    b.tally.finish(as_parts(b))
    assert b.tally.result()["arnebia_in"] == 500 and b.tally.result()["arnebia_out"] == 400


def test_a_net_loss_reads_as_negative(clock: Clock) -> None:
    b = Bot(bag(Arnebia=1000, Large_Red_Potion=20), priced_tally(clock))
    b.rows = bag(Arnebia=1050, Large_Red_Potion=12)          # 50 picked up, 8 drunk (800)
    b.tally.finish(as_parts(b))
    assert stats.earnings(b.tally.result()) == (50, 800, -750)
    assert "Net earnings: -750 arnebia" in lines_of(b.tally.result())


def test_items_with_no_stored_price_are_named_and_not_counted(clock: Clock) -> None:
    b = Bot(bag(Arnebia=0, Large_Red_Potion=5, Mystery_Bread=3), priced_tally(clock))
    b.rows = bag(Arnebia=300, Large_Red_Potion=3, Mystery_Bread=1)
    b.tally.finish(as_parts(b))
    r = b.tally.result()
    assert stats.earnings(r) == (300, 200, 100)
    text = lines_of(r)
    assert ("Total expenses: 200 arnebia (items used up, at their stored prices)"
            " - 2 used up with no stored price, not counted") in text
    assert any(line.startswith("No price stored, not counted: 2 Mystery Bread") for line in text)


def test_nothing_priced_used_is_no_expense_and_shards_make_it_an_estimate(clock: Clock) -> None:
    assert stats.earnings({"arnebia_in": 90}) == (90, None, 90)
    assert "Total expenses: 0 arnebia (items used up, at their stored prices)" in lines_of({"arnebia_in": 90})
    b = Bot(bag(Arnebia=0, Green_Shard=10), priced_tally(clock))
    b.rows = bag(Arnebia=0, Green_Shard=8)                   # 2 shards used up (a boost, say)
    b.tally.finish(as_parts(b))
    assert ("Total expenses: 80 arnebia (items used up, at their stored prices) - estimated: it includes shards"
            in lines_of(b.tally.result()))


def test_the_run_s_own_selling_counts_as_earnings_and_other_jobs_do_not(clock: Clock) -> None:
    b = Bot(bag(Arnebia=1000, Green_Shard=0), priced_tally(clock))
    b.rows = bag(Arnebia=1200, Green_Shard=50)               # farmed: 200 arnebia, 50 shards
    clock.advance(600)
    with b.tally.activity(as_parts(b), "sell_shards"):
        b.rows = bag(Arnebia=3200, Green_Shard=0)            # sold for 2000
    with b.tally.activity(as_parts(b), "gem_maker"):
        b.rows = bag(Arnebia=2700)                           # a job's spending isn't farming's
    b.tally.finish(as_parts(b))
    farm = b.tally.result()
    assert farm["arnebia_in"] == 200 and farm["sales"] == 2000
    assert stats.earnings(farm) == (2200, None, 2200)


def test_what_the_player_does_while_paused_is_no_earning(clock: Clock) -> None:
    b = Bot(bag(Arnebia=100), stats.Tally("Hero", "Wolves", clock=clock))
    b.rows = bag(Arnebia=150)
    b.tally.pause(as_parts(b))
    b.rows = bag(Arnebia=5000)                               # the player, by hand
    b.tally.resume(as_parts(b))
    b.rows = bag(Arnebia=4990)
    b.tally.finish(as_parts(b))
    assert b.tally.result()["arnebia_in"] == 50 and b.tally.result()["arnebia_out"] == 10


def test_adding_up_keeps_earnings_and_says_when_old_runs_are_from_the_net() -> None:
    new: dict[str, Any] = {"kind": "farming", "minutes": 60, "arnebia": 300, "arnebia_in": 500, "arnebia_out": 200,
                           "sales": 100, "used": {POTION: 2},
                           "spent": {POTION: {"n": 2, "price": 100, "source": "shop", "cost": 200}}}
    old: dict[str, Any] = {"kind": "farming", "minutes": 30, "arnebia": 700}
    total = stats.add_up([new, old])
    assert total["arnebia_in"] == 1200 and total["arnebia_out"] == 200 and total["sales"] == 100
    assert stats.earnings(total) == (1300, 200, 1100)
    assert "Older runs kept only the bag's net arnebia change: their earnings are counted from that" in lines_of(total)
    assert stats.earnings(stats.upgrade({"arnebia": -40})) == (0, None, 0)
