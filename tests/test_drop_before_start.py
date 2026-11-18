# pyright: reportPrivateUsage=false
"""A farming run doesn't set out with the day's drop time already down to what is kept: the whole
way to the farm was made for the first sheet there to send the run back. It says so and stays where
it is, as for "Stop before the next level"."""

from __future__ import annotations

import pytest

from test_farming import Farm
from unclefarmer.engine.botparts.base import Stopped

KEEP = {"stop_on_drop_time": True, "drop_keep_minutes": 10, "drop_tail_seconds": 300}


def test_no_drop_time_left_does_not_set_out() -> None:
    b = Farm(profile=KEEP)
    b.refresh_sheet = lambda force=False: {"drop_seconds": 600, "age": 0}     # 10 min: all kept
    b.finish = lambda: pytest.fail("nothing ran: no end place")
    with pytest.raises(Stopped) as e:
        b.drop_before_start()
    assert e.value.stay is True
    assert any("drop time is nearly used up (10 min left) - not starting to farm" in line for line in b.spoken)


@pytest.mark.parametrize("profile, sheet", [
    (KEEP, {"drop_seconds": 3600, "age": 0}),                  # an hour left: worth going
    (KEEP, {}),                                                # the sheet doesn't say
    (KEEP, {"drop_seconds": -1}),
    (dict(KEEP, stop_on_drop_time=False), {"drop_seconds": 0}),   # no such stop
])
def test_drop_time_left_or_unknown_sets_out(profile: dict[str, object], sheet: dict[str, object]) -> None:
    b = Farm(profile=profile)
    b.refresh_sheet = lambda force=False: sheet
    b.drop_before_start()                                      # no Stopped
    assert b.spoken == []


def test_the_plans_keep_drop_counts_even_with_the_stop_off() -> None:
    b = Farm(profile=dict(KEEP, stop_on_drop_time=False))
    b.drop_keep = 60                                           # keep_drop 60 in the plan
    b.refresh_sheet = lambda force=False: {"drop_seconds": 3000, "age": 0}
    with pytest.raises(Stopped):
        b.drop_before_start()


def test_the_farm_check_still_stops_and_counts_the_same() -> None:
    b = Farm(profile=KEEP)
    b.finish = lambda: None
    b.drop_time_check({"drop_seconds": 7200, "age": 0})
    assert any("farming stops in about 105 minutes" in line for line in b.lines)
    with pytest.raises(Stopped):
        b.drop_time_check({"drop_seconds": 900, "age": 0})


def test_the_plans_keep_drop_is_read_before_the_check() -> None:
    from conftest import as_bot
    from unclefarmer.engine import runner as R, script as script_mod

    class Seen:
        drop_keep: int | None = None
        checked_with: int | None = None

        def level_before_start(self) -> None:
            pass

        def drop_before_start(self) -> None:
            self.checked_with = self.drop_keep
    b = Seen()
    main, _subs, _stop = script_mod.parse("keep_drop 45\nfarm minutes=5\n")
    R.Run._level_first(as_bot(b), main)
    assert b.checked_with == 45
