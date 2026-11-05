# pyright: reportPrivateUsage=false
# White-box tests: they count the Gem Maker's rounds (_gem_round) through a pretend craftsman.
"""The Gem Maker when no gem breaks. A combine that works gives no shard back - only a broken gem
leaves one - so a lucky streak leaves the bag without the boost shard the last round had, and
without the shards that a failure would have turned back into a gem. The Gem Maker must finish
(or go on) from the bag as it is, never ask for a shard that isn't there, and never make the same
round again and again when the game carried nothing out.

The pretend Gem Craftsman below works its menus as the game does (the gem list, the single and
batch synthesis pages, the boost and rune boxes, OK, the quantity box) and its rules: 3 gems make
one a level up when the try works; a failure shatters them into one shard of their level, or, with
a Gem Protection Rune, keeps them and takes the rune; a ticked boost box takes one shard."""

from __future__ import annotations

import itertools
from collections.abc import Iterator
from typing import Any

import pytest

from conftest import as_bot, stand_in
from test_crafting import Bot, add, entry
from unclefarmer.engine import crafting, town

FEE = 527


def count(b: Bot, name: str) -> int:
    return sum(r["count"] for r in b.bag if r["name"].lower() == name.lower())


def box(text: str, on: bool) -> dict[str, Any]:
    e = entry(text)
    e["cmd"] = [14, 1, FEE, int(on)]
    return e


class Craftsman:
    """The Gem Craftsman of one colour, behind the test bot's menus."""

    def __init__(self, b: Bot, colour: str, outcomes: Iterator[bool], sticky_boost: bool = False) -> None:
        self.b, self.colour, self.outcomes = b, colour, outcomes
        self.add_box = False          # "add 1 ... Shard" (single synthesis); the game keeps it as it was
        self.rune_box = False
        self.auto_box = False         # "Automatically use ... Shard" (batch)
        self.sticky_boost = sticky_boost   # the boost box can't be changed with no such shard in the bag
        self.page = ""
        self.level = 0
        self.opened = 0
        self.refused = 0
        b.on_choose = self.choose

    # ---------------------------------------------------------------- the menus
    def open(self) -> str:
        self.opened += 1
        self.show("main", [["gem synthesis"], ["shard synthesis"], ["Batch gem synthesis"]])
        return "gang"

    def show(self, page: str, rows: list[list[Any]]) -> None:
        self.page = page
        self.b.top = {"row": 0, "col": 0, "rows": [[entry(t) if isinstance(t, str) else t for t in r] for r in rows]}
        self.b.open = True

    def gem(self, lv: int) -> str:
        return "Level %d %s" % (lv, self.colour)

    def gem_list(self, page: str) -> None:
        rows = [["%s %d" % (self.gem(lv), count(self.b, self.gem(lv)))] for lv in range(1, 30)
                if count(self.b, self.gem(lv)) >= 3]
        if not rows:
            self.show(page, [["You have no gems available for synthesizing"]])
            return
        self.show(page, rows)

    def single_page(self) -> None:
        lv = self.level
        self.show("single", [["use 3 %s to Synthesize 1 %s; fee %d arnebia" % (self.gem(lv), self.gem(lv + 1), FEE)],
                             [box("add 1 %s Shard" % self.gem(lv), self.add_box)],
                             [box("use 1 Gem Protection Rune", self.rune_box)], ["OK to Synthesize"]])

    def batch_page(self) -> None:
        lv = self.level
        self.show("batch", [["It takes 3 %s and cost %d arnebia to synthesize each %s" % (self.gem(lv), FEE, self.gem(lv + 1))],
                            [box("Automatically use %s Shard in the backpack to increase the success rate" % self.gem(lv),
                                 self.auto_box)],
                            ["Please enter quantity"]])

    def choose(self, pattern: str) -> dict[str, Any] | None:
        b = self.b
        b.chooses.append(pattern)
        where = b.find_entry(b.top, pattern) if b.open and b.top else None
        if where is None:
            b.close_dialogs()
            return None
        r, c = where
        top: dict[str, Any] = b.top or {}
        e: dict[str, Any] = top["rows"][r][c]
        text = str(e["options"][0])
        if text in ("gem synthesis", "Batch gem synthesis"):
            self.gem_list("single list" if text == "gem synthesis" else "batch list")
        elif self.page.endswith("list") and text.startswith("Level "):
            self.level = int(text.split()[1])
            self.single_page() if self.page == "single list" else self.batch_page()
        elif text.startswith("add 1 "):
            shard = self.gem(self.level) + " Shard"
            if not (self.sticky_boost and count(b, shard) == 0):
                self.add_box = not self.add_box
            self.single_page()
        elif text.startswith("use 1 Gem Protection Rune"):
            self.rune_box = not self.rune_box if count(b, crafting.RUNE) else False
            self.single_page()
        elif text.startswith("Automatically use"):
            self.auto_box = not self.auto_box
            self.batch_page()
        elif text == "OK to Synthesize":
            self.combine(1, boost_box=self.add_box, protect=self.rune_box)
            self.gem_list("single list")          # back to the list, as after every try
        return e

    def quantity(self, n: int) -> bool:
        """The batch's "Please enter quantity": n tries, the shards used first while the box is ticked."""
        self.combine(n, boost_box=self.auto_box, protect=False, auto=True)
        self.b.close_dialogs()
        return True

    # ---------------------------------------------------------------- the rules
    def combine(self, n: int, boost_box: bool, protect: bool, auto: bool = False) -> None:
        b, gem, nxt = self.b, self.gem(self.level), self.gem(self.level + 1)
        shard = gem + " Shard"
        for _i in range(n):
            if count(b, gem) < 3:
                return
            if boost_box and count(b, shard) == 0 and not auto:
                self.refused += 1                  # a shard asked for that isn't there: nothing happens
                return
            if boost_box and count(b, shard):
                add(b, shard, -1)
            add(b, "Arnebia", -FEE)
            if protect:
                add(b, crafting.RUNE, -1)
            if next(self.outcomes):
                add(b, gem, -3)
                add(b, nxt, 1)
            elif not protect:
                add(b, gem, -3)
                add(b, shard, 1)                    # the shattered gem's shard: the only shard there is


@pytest.fixture
def craftsman(monkeypatch: pytest.MonkeyPatch) -> Any:
    """A bot with a bag and a pretend craftsman; make(bag, outcomes, ...) -> (bot, craftsman)."""
    def make(bag: dict[str, int], outcomes: Iterator[bool], colour: str = "Topaz",
             sticky_boost: bool = False) -> tuple[Bot, Craftsman]:
        b = Bot(bag=[{"name": "Arnebia", "count": 10 ** 8}] + [{"name": n, "count": k} for n, k in bag.items()])
        c = Craftsman(b, colour, outcomes, sticky_boost)
        monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: c.open()))

        def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
            return bot.choose(pattern) is not None and (quantity is None or c.quantity(quantity))
        monkeypatch.setattr(town, "pick", pick)

        def make_gems(bot: Bot, wanted: dict[str, int], opener: Any = None) -> int:
            made = 0
            for shard in wanted:
                k = count(bot, shard) // 4
                if k:
                    add(bot, shard, -4 * k)
                    add(bot, shard[:-len(" Shard")], k)
                    made += k
            return made
        monkeypatch.setattr(town, "make_gems", make_gems)
        return b, c
    return make


def rounds(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """How many times the Gem Maker went round (its _gem_round), counted as it runs."""
    seen: list[int] = []
    real = crafting._gem_round

    def counting(b: Any, job: Any) -> int:
        seen.append(1)
        return real(b, job)
    monkeypatch.setattr(crafting, "_gem_round", counting)
    return seen


ONE_KIND = {"colour": "Topaz", "level": 4, "count": 1, "amount": 1, "supply": "none", "where": "gang"}


# ---------------------------------------------------------------- every combine works, no shard back
@pytest.mark.parametrize("protect", [False, True])
def test_one_kind_reaches_its_gem_when_every_combine_works(craftsman: Any, monkeypatch: pytest.MonkeyPatch,
                                                           protect: bool) -> None:
    b, c = craftsman({"Level 1 Topaz": 27, crafting.RUNE: 50}, itertools.repeat(True))
    went = rounds(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(ONE_KIND, protect=protect))
    assert count(b, "Level 4 Topaz") == 1 and any("done: made 1 Level 4 Topaz" in line for line in b.lines)
    assert count(b, "Level 1 Topaz Shard") == count(b, "Level 2 Topaz Shard") == 0      # nothing broke
    assert len(went) <= 2 and c.refused == 0


def test_two_of_a_kind_over_several_rounds_when_every_combine_works(craftsman: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c = craftsman({"Level 1 Topaz Shard": 160}, itertools.repeat(True))
    went = rounds(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(ONE_KIND, level=3, count=2))
    # (the plan tries twice what it needs, counting on half to break: a lucky run makes more)
    assert count(b, "Level 3 Topaz") >= 2 and any("done: made" in line for line in b.lines)
    assert 1 <= len(went) <= 3


def test_one_kind_stops_short_cleanly_when_the_bag_runs_out_with_no_shards_back(craftsman: Any,
                                                                                 monkeypatch: pytest.MonkeyPatch) -> None:
    b, _c = craftsman({"Level 1 Topaz": 9}, itertools.repeat(True))      # 3 Level 2: one Level 3, no Level 4
    went = rounds(monkeypatch)
    crafting.gem_maker(as_bot(b), ONE_KIND)
    assert count(b, "Level 3 Topaz") == 1 and count(b, "Level 4 Topaz") == 0
    assert any("made 0 of the 1 Level 4 Topaz asked for" in line for line in b.lines)
    assert len(went) <= 2


def test_the_last_boost_shard_used_up_is_never_asked_for_again(craftsman: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # one Level 2 shard from an earlier break: the first Level 2 try takes it; every try works, so
    # none comes back, and the box must not ask for another
    b, c = craftsman({"Level 2 Topaz": 9, "Level 2 Topaz Shard": 1, crafting.RUNE: 20}, itertools.repeat(True))
    crafting.gem_maker(as_bot(b), dict(ONE_KIND, level=3, count=3, protect=True, boost=True))
    assert count(b, "Level 3 Topaz") == 3 and count(b, "Level 2 Topaz Shard") == 0
    assert c.refused == 0 and c.add_box is False


def test_a_combine_the_game_wont_carry_out_ends_the_gem_maker_instead_of_repeating(craftsman: Any,
                                                                                    monkeypatch: pytest.MonkeyPatch) -> None:
    # The boost box stays ticked from the try that used the last shard and the game won't change it
    # with none in the bag: every OK asks for a shard that isn't there and the game makes nothing.
    # Counted as failed tries, that was a round of progress each time: 40 rounds, each a walk back.
    b, c = craftsman({"Level 2 Topaz": 9, "Level 2 Topaz Shard": 1, crafting.RUNE: 20}, itertools.repeat(True),
                     sticky_boost=True)
    went = rounds(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(ONE_KIND, level=3, count=3, protect=True, boost=True))
    assert count(b, "Level 3 Topaz") == 1                  # the boosted try worked
    assert len(went) <= 2 and c.opened <= 3 and c.refused <= 2
    assert any("stays ticked with none in the bag" in line for line in b.lines)
    assert any("made nothing with Level 2 Topaz" in line for line in b.lines)
    assert any("made 1 of the 3 Level 3 Topaz asked for" in line for line in b.lines)


def test_make_the_most_of_the_bag_finishes_when_every_combine_works(craftsman: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, c = craftsman({"Level 1 Topaz": 27, "Level 1 Topaz Shard": 12}, itertools.repeat(True))
    crafting.make_most(as_bot(b), {"where": "gang", "up_to": 6, "keep": 0, "colours": [], "boost": True})
    assert count(b, "Level 4 Topaz") == 1 and count(b, "Level 2 Topaz") == 1    # 30 Level 1: 10 -> 3 + 1 -> 1 + 1
    assert any("as high as they go" in line for line in b.lines) and c.refused == 0


def test_make_the_most_of_the_bag_stops_when_a_round_changes_nothing(craftsman: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, c = craftsman({"Level 2 Topaz": 6}, itertools.repeat(True))
    calls: list[int] = []
    real = crafting.raise_gems

    def nothing_happens(bot: Any, colour: str, level: int, k: int, boost: bool, protect: bool, where: str,
                        check_only: bool = False) -> tuple[int, int]:
        calls.append(k)
        return 0, k                                          # an old count of "failed" tries, bag unchanged
    monkeypatch.setattr(crafting, "raise_gems", nothing_happens)
    crafting.make_most(as_bot(b), {"where": "gang", "up_to": 4, "keep": 0, "colours": []})
    assert calls == [2]                                      # one round, not twelve
    monkeypatch.setattr(crafting, "raise_gems", real)
    assert c.opened == 0


# ---------------------------------------------------------------- gems break and shards come back (as before)
@pytest.mark.parametrize("protect", [False, True])
def test_one_kind_still_reaches_its_gem_when_gems_break(craftsman: Any, monkeypatch: pytest.MonkeyPatch,
                                                        protect: bool) -> None:
    b, c = craftsman({"Level 1 Topaz": 60, crafting.RUNE: 80}, itertools.cycle([True, False]))
    went = rounds(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(ONE_KIND, level=3, protect=protect))
    assert count(b, "Level 3 Topaz") >= 1 and any("done: made 1 Level 3 Topaz" in line for line in b.lines)
    if not protect:
        assert len(went) >= 1 and c.refused == 0


def test_broken_gems_shards_come_back_into_gems_on_the_next_round(craftsman: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    # every other combine fails: the shards it leaves are made back into gems and combined again
    b, c = craftsman({"Level 1 Topaz": 30}, itertools.cycle([False, True]))
    went = rounds(monkeypatch)
    crafting.gem_maker(as_bot(b), dict(ONE_KIND, level=2, count=6))
    assert len(went) >= 2                                    # it went round again for the shards
    assert count(b, "Level 2 Topaz") >= 5 and c.refused == 0


def test_make_the_most_with_breaks_keeps_going_round(craftsman: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, c = craftsman({"Level 1 Topaz": 48}, itertools.cycle([True, False]))
    crafting.make_most(as_bot(b), {"where": "gang", "up_to": 5, "keep": 0, "colours": [], "boost": True})
    assert count(b, "Level 1 Topaz") < 3 and any("as high as they go" in line for line in b.lines)
    assert c.refused == 0
