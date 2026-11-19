# pyright: reportPrivateUsage=false
"""The Gem Maker's care with money: nothing is spent on a page that isn't the gem's, a batch the game
refuses is asked for at half the size, and no more tries are made than the job needs."""

from __future__ import annotations

import itertools
from typing import Any

import pytest

from conftest import as_bot, returning, stand_in
from test_crafting import Bot, add, entry
from test_gem_maker_no_break import Craftsman, count
from unclefarmer.engine import crafting, town

JOB: dict[str, Any] = {"colour": "Topaz", "level": 2, "count": 1, "amount": 1, "supply": "none", "where": "gang",
                       "boost": True, "protect": False}


def game(monkeypatch: pytest.MonkeyPatch, bag: dict[str, int], works: bool = True) -> tuple[Bot, Craftsman, list[int]]:
    b = Bot(bag=[{"name": "Arnebia", "count": 10 ** 8}] + [{"name": n, "count": k} for n, k in bag.items()])
    c = Craftsman(b, "Topaz", itertools.repeat(works))
    asked: list[int] = []
    monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: c.open()))

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        if bot.choose(pattern) is None:
            return False
        if quantity is None:
            return True
        asked.append(quantity)
        return c.quantity(quantity)
    monkeypatch.setattr(town, "pick", pick)
    monkeypatch.setattr(town, "make_gems", returning(0))
    return b, c, asked


def test_nothing_is_spent_on_another_gems_page(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, asked = game(monkeypatch, {"Level 1 Topaz": 9})

    def old_page() -> None:
        c.show("batch", [["It takes 3 Level 7 Topaz and cost 1 arnebia to synthesize each Level 8 Topaz"],
                         ["Please enter quantity"]])
    monkeypatch.setattr(c, "batch_page", old_page)
    assert crafting.raise_gems(as_bot(b), "Topaz", 1, 3, False, False, "gang") == (0, 0)
    assert asked == [] and count(b, "Level 1 Topaz") == 9
    assert any("page for Level 1 Topaz didn't come up" in line for line in b.lines)


def test_a_refused_batch_is_asked_for_at_half_the_size(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, asked = game(monkeypatch, {"Level 1 Topaz": 12})
    real = c.quantity

    def picky(n: int) -> bool:
        if n > 1:
            b.close_dialogs()
            return True                  # taken, and nothing happens
        return real(n)
    monkeypatch.setattr(c, "quantity", picky)
    assert crafting.raise_gems(as_bot(b), "Topaz", 1, 4, False, False, "gang") == (1, 0)
    assert asked == [4, 2, 1]
    assert count(b, "Level 2 Topaz") == 1


def test_a_batch_refused_at_every_size_is_left(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, asked = game(monkeypatch, {"Level 1 Topaz": 24})

    def never(n: int) -> bool:
        b.close_dialogs()
        return True
    monkeypatch.setattr(c, "quantity", never)
    assert crafting.raise_gems(as_bot(b), "Topaz", 1, 8, False, False, "gang") == (0, 0)
    assert asked == [8, 4, 2]
    assert any("the bag showed no change" in line for line in b.lines)


def test_a_boost_box_that_wont_tick_is_said(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, _asked = game(monkeypatch, {"Level 2 Topaz": 3, "Level 2 Topaz Shard": 1})
    monkeypatch.setattr(crafting, "tick", returning(False))
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 1, True, False, "gang") == (1, 0)
    assert any("couldn't tick the box for Level 2 Topaz Shard" in line for line in b.lines)


def test_each_level_says_its_shards_before_and_after(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, _asked = game(monkeypatch, {"Level 2 Topaz": 6, "Level 2 Topaz Shard": 2})
    crafting.raise_gems(as_bot(b), "Topaz", 2, 2, True, False, "gang")
    assert "Level 2 Topaz Shard: 2 before, 0 after, 2 used" in b.lines


def test_the_gem_asked_for_gets_no_more_tries_than_are_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, asked = game(monkeypatch, {"Level 1 Topaz": 30})
    crafting.gem_maker(as_bot(b), dict(JOB, count=2))
    assert asked == [2] and count(b, "Level 2 Topaz") == 2
    assert count(b, "Level 1 Topaz") == 24


def test_failures_on_the_last_level_are_made_up_for(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, asked = game(monkeypatch, {"Level 1 Topaz": 30})
    c.outcomes = itertools.chain([False], itertools.repeat(True))
    crafting.gem_maker(as_bot(b), dict(JOB, count=2))
    assert asked == [2, 1] and count(b, "Level 2 Topaz") == 2


def test_a_total_counts_the_gems_in_the_bag(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, asked = game(monkeypatch, {"Level 1 Topaz": 30, "Level 2 Topaz": 3})
    crafting.gem_maker(as_bot(b), dict(JOB, amount="total", count=5))
    assert asked == [2] and count(b, "Level 2 Topaz") == 5


def test_a_total_already_reached_makes_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, asked = game(monkeypatch, {"Level 1 Topaz": 30, "Level 2 Topaz": 5})
    crafting.gem_maker(as_bot(b), dict(JOB, amount="total", count=5))
    assert asked == [] and count(b, "Level 1 Topaz") == 30
    assert any(line.startswith("done: made 0 Level 2 Topaz") for line in b.lines)


def test_a_failure_breaks_one_gem_into_a_shard(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, _asked = game(monkeypatch, {"Level 1 Topaz": 3}, works=False)
    assert crafting.raise_gems(as_bot(b), "Topaz", 1, 1, False, False, "gang") == (0, 1)
    assert count(b, "Level 1 Topaz") == 2 and count(b, "Level 1 Topaz Shard") == 1


def test_shards_are_not_made_up_on_another_page(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Level 1 Topaz Shard", "count": 8}, {"name": "Arnebia", "count": 100000}])
    b.open, b.top = True, {"row": 0, "col": 0, "rows": [[entry("Level 1 Topaz Shard 8")]]}
    b.on_choose = lambda pattern: {"text": "row"}
    monkeypatch.setattr(town, "_open_shard_synthesis", stand_in(town._open_shard_synthesis, lambda b, opener=None: True))
    typed: list[int] = []

    def type_number(bot: Any, n: int) -> bool:
        typed.append(n)
        return True
    monkeypatch.setattr(town, "type_number", type_number)
    assert town.make_gems(as_bot(b), {"Level 1 Topaz Shard": 8}) == 0
    assert typed == []


def making(monkeypatch: pytest.MonkeyPatch) -> list[list[str]]:
    """town.make_gems as the game does it, each call's shard names noted."""
    calls: list[list[str]] = []

    def make(bot: Bot, wanted: dict[str, int], opener: Any = None) -> int:
        calls.append(sorted(wanted))
        made = 0
        for shard in wanted:
            k = count(bot, shard) // 4
            if k:
                add(bot, shard, -4 * k)
                add(bot, shard[:-len(" Shard")], k)
                made += k
        return made
    monkeypatch.setattr(town, "make_gems", make)
    return calls


def test_shards_above_level_1_are_kept_for_the_boost(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, asked = game(monkeypatch, {"Level 2 Topaz": 6, "Level 2 Topaz Shard": 5})
    calls = making(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(JOB, level=3, count=1))
    assert calls == [] and asked == [1] and c.auto_box is True
    assert count(b, "Level 2 Topaz Shard") == 4 and count(b, "Level 3 Topaz") == 1


def test_without_the_boost_shards_become_gems_as_before(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, _asked = game(monkeypatch, {"Level 2 Topaz": 3, "Level 2 Topaz Shard": 4})
    calls = making(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(JOB, level=3, count=1, boost=False))
    assert calls[0] == ["Level 2 Topaz Shard"]


def test_kept_shards_become_gems_when_nothing_else_can_be_done(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, _asked = game(monkeypatch, {"Level 2 Topaz": 2, "Level 2 Topaz Shard": 4})
    calls = making(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(JOB, level=3, count=1))
    assert calls == [["Level 2 Topaz Shard"]] and count(b, "Level 3 Topaz") == 1


def test_shards_of_the_level_asked_for_become_its_gems(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, asked = game(monkeypatch, {"Level 3 Topaz Shard": 4})
    calls = making(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(JOB, level=3, count=1))
    assert calls == [["Level 3 Topaz Shard"]] and asked == [] and count(b, "Level 3 Topaz") == 1


def test_the_whole_bag_keeps_them_too_until_the_end(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c, _asked = game(monkeypatch, {"Level 2 Topaz": 3, "Level 2 Topaz Shard": 5})
    calls = making(monkeypatch)
    crafting.make_most(as_bot(b), {"up_to": 3, "boost": True})
    assert count(b, "Level 3 Topaz") == 1
    assert count(b, "Level 2 Topaz Shard") == 0 and count(b, "Level 2 Topaz") == 1   # the last 4 at the end
    assert [[n.lower() for n in c] for c in calls] == [["level 2 topaz shard"]]


def test_the_batch_box_is_ticked_with_no_shard_in_the_bag(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, asked = game(monkeypatch, {"Level 2 Topaz": 3})
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 1, True, False, "gang") == (1, 0)
    assert c.auto_box is True and asked == [1]


def test_a_batch_uses_the_shards_its_failures_leave(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, _asked = game(monkeypatch, {"Level 2 Topaz": 12, "Level 2 Topaz Shard": 1})
    c.outcomes = iter([False, True, True, True])
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 4, True, False, "gang") == (3, 1)
    assert "Level 2 Topaz Shard: 1 before, 0 after, 2 used" in b.lines


def test_a_refused_batch_drops_a_box_with_no_shard_behind_it(monkeypatch: pytest.MonkeyPatch) -> None:
    b, c, _asked = game(monkeypatch, {"Level 2 Topaz": 12})
    boxes: list[bool] = []
    real = c.quantity

    def picky(n: int) -> bool:
        boxes.append(c.auto_box)
        if c.auto_box:
            b.close_dialogs()
            return True
        return real(n)
    monkeypatch.setattr(c, "quantity", picky)
    assert crafting.raise_gems(as_bot(b), "Topaz", 2, 4, True, False, "gang") == (2, 0)
    assert boxes == [True, False]
