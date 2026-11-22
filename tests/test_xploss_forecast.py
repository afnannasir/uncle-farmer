"""Tools, XP loser, what a death costs (engine/xploss.py): a share of the experience earned inside
the level, so the run can say how long it will take and stop for a goal the level can't reach."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from test_xploss import Game, lose
from test_xploss import isolated as isolated   # the same pretend world (autouse)
from conftest import as_bot
from unclefarmer.engine import xploss
from unclefarmer.engine.botparts.health import HealthMixin

SHARE = 0.0181
TOP = 3_734_600


class Level(Game):
    """A game whose deaths cost what the real one's do, on a character with a name and a level."""

    def __init__(self, xp: int, top: int = TOP) -> None:
        super().__init__(xp=xp, floor_xp=top)

    def me(self) -> dict[str, Any]:
        return {**super().me(), "name": "Hero"}

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        return {**(super().refresh_sheet(force) or {}), "level": 90}

    def handle_death(self) -> None:
        self.loss = int((self.most - self.xp) * SHARE)
        super().handle_death()


def spoken(g: Game) -> list[str]:
    return [line for line, speak in g.lines if speak]


def test_the_share_and_the_top_come_from_three_losses() -> None:
    rate = xploss.fit_rate([61215, 60107, 59019])
    assert rate == pytest.approx(SHARE, abs=0.0002)
    assert xploss.top_of(352_565, 61215, SHARE) == pytest.approx(TOP, rel=0.001)


def test_the_measured_run_is_predicted() -> None:
    assert xploss.deaths_to(290_221, 3_500_000, TOP, SHARE) == 148
    assert xploss.deaths_to(5000, 4000, TOP, SHARE) == 0


@pytest.mark.parametrize("losses", [[500, 500, 500], [900, 800], [100, 98, 96], [9000, 4000, 1000]])
def test_losses_that_dont_fit_give_no_forecast(losses: list[int]) -> None:
    assert xploss.fit_rate(losses) is None


def test_the_rounded_tail_doesnt_spoil_the_share() -> None:
    losses = [int(4100 * (1 - SHARE) ** i) for i in range(300)]
    assert xploss.fit_rate(losses) == pytest.approx(SHARE, abs=0.0003)


def test_a_goal_past_the_level_ends_after_three_deaths() -> None:
    g = Level(3_000_000)
    assert lose(g, until_needed=4_100_000, max_deaths=999) == "impossible"
    assert g.deaths == xploss.FIT_AFTER and g.pk == 1
    assert "so the goal of 4100000 can't be reached" in g.text()


def test_the_run_says_how_long_it_will_take_once() -> None:
    g = Level(1_000_000)
    assert lose(g, until_needed=1_300_000, max_deaths=999) == "done"
    said = [s for s in spoken(g) if "more deaths" in s]
    assert len(said) == 1 and "min, to reach 1300000" in said[0]


def test_the_limit_on_deaths_is_warned_of_early() -> None:
    g = Level(290_221)
    assert lose(g, until_needed=3_500_000, max_deaths=30) == "deaths"
    assert "the limit of 30 deaths will stop the run before that" in spoken(g)


def test_it_stops_when_deaths_cost_too_little() -> None:
    g = Level(3_710_000)
    assert lose(g, until_needed=3_730_000, stop_small=True, min_loss=500, max_deaths=999) == "small"
    assert g.deaths == xploss.SMALLS
    assert "a death now costs less than 500 experience" in g.text()


def test_without_the_choice_small_deaths_go_on_to_the_goal() -> None:
    g = Level(3_700_000)
    assert lose(g, until_needed=3_710_000, stop_small=False, max_deaths=999) == "done"


def as_set_by_default(g: Game, goal: int) -> str:
    return xploss.run(as_bot(g), {**xploss.DEFAULTS, "where": "guard", "until_needed": goal, "max_deaths": 999})


def test_by_default_a_low_level_goes_on_to_its_goal() -> None:
    assert xploss.DEFAULTS["stop_small"] is False
    g = Level(5000, top=20_000)                        # every death here costs under 500
    assert as_set_by_default(g, 15_000) == "done"
    assert g.xp >= 15_000 and 55 <= g.deaths <= 70


def test_by_default_a_goal_close_to_the_top_is_reached() -> None:
    g = Level(3_600_000)
    assert as_set_by_default(g, 3_720_000) == "done"
    assert g.xp >= 3_720_000 and 115 <= g.deaths <= 130


def test_one_small_loss_doesnt_stop_the_run() -> None:
    g = Game(xp=1000, loss=100)
    cfg = {**xploss.DEFAULTS, "stop_small": True, "min_loss": 500}
    r = xploss._Run(as_bot(g), cfg)  # pyright: ignore[reportPrivateUsage]
    r.start = 1000
    assert r.after_death(1000, 1100, 9000) is None
    assert r.after_death(1100, 1800, 9000) is None     # a big one between: the count starts again
    assert r.after_death(1800, 1900, 9000) is None
    assert r.after_death(1900, 2000, 9000) == "small"


def test_the_next_run_remembers_and_doesnt_die(tmp_path: Path) -> None:
    g = Level(TOP - 50)
    assert lose(g, until_needed=4_100_000) == "stalled"
    assert json.loads((tmp_path / xploss.LEVEL_TOPS).read_text(encoding="utf-8"))["Hero|90"]["top"] == g.xp
    again = Level(g.xp)
    assert lose(again, until_needed=4_100_000) == "impossible"
    assert again.deaths == 0 and again.pressed == []


def test_a_remembered_level_also_skips_deaths_worth_too_little() -> None:
    first = Level(1_000_000)
    assert lose(first, until_needed=1_300_000, max_deaths=999) == "done"
    late = Level(TOP - 20_000)
    assert lose(late, until_needed=TOP - 19_000, stop_small=True, min_loss=500) == "small"
    assert late.deaths == 0


def test_a_remembered_top_is_kept_until_the_level_changes_and_a_lower_one_is_ignored() -> None:
    first = Level(TOP - 50)
    lose(first, until_needed=4_100_000)
    old = Level(first.xp)
    old.clock.now += 14 * 86400.0                      # two weeks later, the same level: still known
    assert lose(old, until_needed=4_100_000) == "impossible" and old.deaths == 0
    higher = Level(TOP + 10_000, top=TOP + 500_000)
    assert lose(higher, until_needed=TOP + 20_000, max_deaths=999) == "done"


def test_a_check_says_what_it_knows_and_changes_nothing() -> None:
    first = Level(1_000_000)
    lose(first, until_needed=1_300_000, max_deaths=999)
    g = Level(1_100_000)
    cfg = {**xploss.DEFAULTS, "until_needed": 1_500_000, "stop_small": False}
    assert xploss.run(as_bot(g), cfg, check_only=True) == "checked"
    assert g.deaths == 0 and "more deaths to reach 1500000" in g.text()


def test_a_guard_out_of_reach_isnt_called_one_that_didnt_strike_back(monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game()

    def no_hit(self: Any, guard: dict[str, Any]) -> None:
        return None
    monkeypatch.setattr(xploss._Run, "strike", no_hit)  # pyright: ignore[reportPrivateUsage]
    with pytest.raises(xploss.Stopped):
        lose(g, until_needed=1500)                     # the only guard left alone: none to fight
    assert "couldn't get a hit in on that guard" in g.text()
    assert "didn't strike back" not in g.text()


class Drinker:
    dying_on_purpose = True
    p: dict[str, Any] = {"mp_use": True}
    mp_share: float | None = 50.0

    def potion(self, kind: str) -> tuple[str, str]:
        raise AssertionError("no potion is looked at while dying")

    def mp_now(self, me: dict[str, Any], now: float) -> tuple[int, int]:
        raise AssertionError("no MP reading for a potion while dying")


def test_no_potion_while_dying_on_purpose() -> None:
    d = Drinker()
    HealthMixin._hp_potion(as_bot(d), 10, 1000, 1.0, 100.0, None)  # pyright: ignore[reportPrivateUsage]
    HealthMixin._mp_potion(as_bot(d), {}, 100.0)  # pyright: ignore[reportPrivateUsage]
    assert d.mp_share is None


# ---------------------------------------------------------------- "worth": the bot decides
WORTH_GOAL = TOP - -(-xploss.WORTH_SHARE * TOP // SHARE)     # where a death takes 0.05% of the level


def test_worth_decides_its_goal_and_its_deaths_after_three() -> None:
    g = Level(1_000_000)
    assert lose(g, mode="worth", max_deaths=30) == "done"      # 30 would stop it: the bot decided more
    assert abs(g.xp - WORTH_GOAL) < 3000 and g.deaths > 30
    assert "set after 3 deaths" in g.text()
    assert "a death would now take less than 0.05% of the level" in g.text()
    assert not any("the limit of" in s for s in spoken(g))


def test_worth_remembers_the_level_and_sets_its_goal_at_once() -> None:
    lose(Level(1_000_000), mode="worth", max_deaths=30)
    again = Level(2_000_000)
    assert lose(again, mode="worth", max_deaths=30) == "done"
    assert any("dying to guards until it needs" in line for line, _speak in again.lines)   # known at once
    assert abs(again.xp - WORTH_GOAL) < 3000


def test_worth_past_its_goal_has_nothing_to_lose() -> None:
    first = Level(1_000_000)
    lose(first, mode="worth", max_deaths=30)
    done = Level(first.xp + 10)
    assert lose(done, mode="worth") == "done" and done.deaths == 0
    assert "nothing to lose" in done.text()


def test_worth_that_cant_learn_the_level_keeps_the_settings_limit() -> None:
    g = Game(xp=1000, loss=500)                  # the same loss every time: no share fits
    assert lose(g, mode="worth", max_deaths=6) == "deaths" and g.deaths == 6


def test_worth_allows_the_forecast_and_a_margin() -> None:
    r = xploss._Run(as_bot(Level(10)), {**xploss.DEFAULTS, "mode": "worth"})   # pyright: ignore[reportPrivateUsage]
    r.start, r.top, r.rate = 1_000_000, TOP, SHARE
    goal = r.decide(1_000_000)
    n = xploss.deaths_to(1_000_000, goal, TOP, SHARE)
    assert r.most == n + max(xploss.WORTH_EXTRA, -(-n * xploss.WORTH_MARGIN // 1)) <= xploss.DEATHS_MOST


def test_a_check_in_worth_says_the_goal_comes_later() -> None:
    g = Level(1_000_000)
    assert xploss.run(as_bot(g), {**xploss.DEFAULTS, "mode": "worth"}, check_only=True) == "checked"
    assert "goal set after 3 deaths" in g.text() and g.deaths == 0


def test_the_level_is_kept_under_the_character_and_an_older_row_still_counts(tmp_path: Path) -> None:
    rows = {"Hero|90": {"top": TOP, "rate": SHARE, "at": 0}}
    (tmp_path / xploss.LEVEL_TOPS).write_text(json.dumps(rows), encoding="utf-8")
    g = Level(1_000_000)
    g.profile_key = "Hero\x1fS1\x1f7"            # pyright: ignore[reportAttributeAccessIssue]
    assert lose(g, until_needed=1_300_000, max_deaths=999) == "done"
    saved = json.loads((tmp_path / xploss.LEVEL_TOPS).read_text(encoding="utf-8"))
    assert "Hero\x1fS1\x1f7|90" in saved
