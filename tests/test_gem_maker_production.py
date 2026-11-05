# pyright: reportPrivateUsage=false
# White-box tests: they count the Gem Maker's rounds (_gem_round) and read _combinable.
"""The Gem Maker through a whole job: buying and taking shards out, rounds of making, and how it
ends. Runs against the pretend Gem Craftsman of test_gem_maker_no_break, a pretend Shard Market
and a bag with a load limit.

What used to end a job half way, each with no reason given:
  - a round in which the bag showed nothing (the game's answer late, or a menu that didn't open)
    was taken for "nothing can be combined", and the job ended with gems still to combine;
  - a fixed 40 rounds, which a job buying a bagful at a time (2000 shards, however big the bag)
    used up long before it was done - the last round's shards bought and never made into gems.
Now a job ends when it is done, when nothing is left to make and no more shards come, or when
something stands in the way that won't pass by itself (arnebia, runes), and says which."""

from __future__ import annotations

import itertools
import random
from collections.abc import Callable, Iterator
from typing import Any

import pytest

from conftest import as_bot, returning, stand_in
from test_crafting import Bot, add, entry, menu
from test_gem_maker_no_break import Craftsman, count
from unclefarmer.engine import bagrules, crafting, town

PRICE = 50            # arnebia a Level 1 Topaz Shard at the pretend market
GEM_FEE = 100         # the craftsman's fee for a Level 1 Topaz from 4 shards
SHARD = "Level 1 Topaz Shard"


class Game(Bot):
    """The test bot with a bag that weighs: every Topaz shard or gem weighs 1, and the character
    sheet shows that load against maxload."""

    def __init__(self, bag: dict[str, int], arnebia: int, maxload: int) -> None:
        super().__init__(bag=[{"name": "Arnebia", "count": arnebia}] + [{"name": n, "count": k} for n, k in bag.items()])
        self.maxload = maxload
        self.late: list[tuple[int, dict[str, int]]] = []    # bag changes the game hasn't shown yet: (reads left, change)
        self.trades: list[int] = []                           # every batch asked of the market
        self.market_most: int | None = None                   # the market sells at most this many a batch

    def load(self) -> int:
        return sum(r["count"] for r in self.bag if "topaz" in r["name"].lower())

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        self.refresh_calls += 1
        return {"load": self.load(), "maxload": self.maxload}

    def bag_rows(self) -> list[dict[str, Any]]:
        shown: list[tuple[int, dict[str, int]]] = []
        for left, change in self.late:
            if left <= 1:
                for name, d in change.items():
                    add(self, name, d)
            else:
                shown.append((left - 1, change))
        self.late = shown
        return list(self.bag)


def coin(seed: int) -> Iterator[bool]:
    """Combines that work half the time, the same way on every run of the test. (A strict
    alternation lines up with the batch sizes and can make every try at one level fail.)"""
    r = random.Random(seed)
    return iter(lambda: r.random() < 0.5, None)


def job(**cfg: Any) -> dict[str, Any]:
    out: dict[str, Any] = {"colour": "Topaz", "level": 4, "count": 2, "amount": 2, "supply": "none", "where": "gang"}
    out.update(cfg)
    return out


@pytest.fixture
def game(monkeypatch: pytest.MonkeyPatch) -> Callable[..., tuple[Game, Craftsman, list[int]]]:
    """make(bag, outcomes, arnebia=, maxload=, late_reads=, dead_opens=) -> (bot, craftsman, rounds).

    late_reads: a batch's result shows in the bag only that many bag readings later.
    dead_opens: which of the craftsman's openings (1, 2...) show no menu (a walk cut short)."""
    def make(bag: dict[str, int], outcomes: Iterator[bool], arnebia: int = 10 ** 8, maxload: int = 6000,
             late_reads: int = 0, dead_opens: set[int] | frozenset[int] = frozenset()) -> tuple[Game, Craftsman, list[int]]:
        b = Game(bag, arnebia, maxload)
        c = Craftsman(b, "Topaz", outcomes)

        def opener(b: Any, role: str, where: str) -> str:
            got = c.open()
            if c.opened in dead_opens:
                b.close_dialogs()
            return got
        monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, opener))

        def quantity(n: int) -> bool:
            was = {r["name"]: r["count"] for r in b.bag}
            c.combine(n, boost_box=c.auto_box, protect=False, auto=True)
            if late_reads:
                now = {r["name"]: r["count"] for r in b.bag}
                change = {k: now.get(k, 0) - was.get(k, 0) for k in set(was) | set(now)}
                for name, d in change.items():
                    add(b, name, -d)                       # not in the bag yet
                b.late.append((late_reads, change))
            b.close_dialogs()
            return True

        def pick(bot: Bot, pattern: str, quantity_: int | None = None) -> bool:
            return bot.choose(pattern) is not None and (quantity_ is None or quantity(quantity_))
        monkeypatch.setattr(town, "pick", pick)

        def make_gems(bot: Bot, wanted: dict[str, int], opener: Callable[[], object] | None = None) -> int:
            made = 0
            bot.p.setdefault("gem_fees", {})["level 1 topaz"] = GEM_FEE
            for shard in wanted:
                k = min(count(bot, shard) // 4, max(0, crafting.spare_arnebia(as_bot(bot))) // GEM_FEE)
                if k:
                    add(bot, shard, -4 * k)
                    add(bot, shard[:-len(" Shard")], k)
                    add(bot, "Arnebia", -GEM_FEE * k)
                    made += k
            return made
        monkeypatch.setattr(town, "make_gems", make_gems)

        # the Shard Market: its list, and a trade that sells what the bag and the purse take
        monkeypatch.setattr(town, "shard_catalog", returning((dict[str, Any](), {1: [5, 5]})))
        monkeypatch.setattr(town, "market_candidates", returning([(1, "Shard Broker", 5, 5)]))

        def visit(bot: Bot, role: str | None, name: str | None = None, candidates: object = None) -> str:
            bot.top = menu([entry("%s; Unit price %d arnebia" % (SHARD, PRICE))])
            bot.open = True
            return "Shard Broker"
        monkeypatch.setattr(town, "visit", visit)

        def trade(b: Any, name: str, action: str, n: int) -> int:
            g: Game = b
            assert n <= town.TRADE_MOST, "the market's box takes at most 2000"
            g.trades.append(n)
            fits = int(g.maxload * g.p.get("bag_full_at", 100) / 100) - g.load()
            most = n if g.market_most is None else g.market_most
            sold = max(0, min(n, fits, count(g, "Arnebia") // PRICE, most))
            add(g, name, sold)
            add(g, "Arnebia", -PRICE * sold)
            return n
        monkeypatch.setattr(town, "shard_trade", stand_in(town.shard_trade, trade))
        monkeypatch.setattr(bagrules, "weights", returning({SHARD.lower(): 1.0}))

        seen: list[int] = []
        real = crafting._gem_round

        def counting(bot: Any, j: Any, blocked: list[str] | None = None) -> int:
            seen.append(1)
            return real(bot, j, blocked)
        monkeypatch.setattr(crafting, "_gem_round", counting)
        return b, c, seen
    return make


def said(b: Bot, text: str) -> bool:
    return any(text in line for line in b.lines)


# ---------------------------------------------------------------- reproduced: stopping half way
def test_a_late_bag_update_is_not_taken_for_the_end(game: Any) -> None:
    # Every batch's result shows in the bag only after its 2-second check. Before, the first round
    # ended the job with 200 gems still to combine: "made 0 of the 2 Level 4 Topaz asked for".
    b, _c, rounds = game({"Level 1 Topaz": 200}, itertools.cycle([True, False]), late_reads=12)
    crafting.gem_maker(as_bot(b), job())
    assert said(b, "showed no change after combining Level 1 Topaz")
    assert len(rounds) > 1
    j = crafting._GemJob(as_bot(b), job(), "Topaz")
    assert crafting._combinable(as_bot(b), j) == ""            # it went on until nothing was left to combine
    assert said(b, "done: made") or said(b, "nothing left to make Level 4 Topaz from")


def test_a_craftsman_menu_that_did_not_open_is_tried_again(game: Any) -> None:
    # The first opening shows no menu. Before, that round "made nothing", and with gems in the bag
    # the job ended on the spot, saying no more than "made 0 of the 2 asked for".
    b, _c, rounds = game({"Level 1 Topaz": 900}, coin(1), dead_opens={1})
    crafting.gem_maker(as_bot(b), job())
    assert said(b, "nothing came of this round although the bag still has 900 Level 1 Topaz - trying again")
    assert count(b, "Level 4 Topaz") >= 2 and said(b, "done: made")
    assert len(rounds) >= 2


@pytest.mark.parametrize("seed", [0, 4, 5])
def test_a_long_job_buying_shards_runs_to_its_end(game: Any, seed: int) -> None:
    # Three Level 6 gems from nothing take tens of thousands of shards. Before: a bagful was 2000
    # shards whatever the bag's room, and after 40 rounds the job ended at "made 1 of the 3" (or 2),
    # with the 2000 shards just bought left unused. These seeds take 73, 146 and 216 rounds.
    b, _c, rounds = game({}, coin(seed), maxload=6000)
    crafting.gem_maker(as_bot(b), job(level=6, count=3, amount=3, supply="buy"))
    assert count(b, "Level 6 Topaz") >= 3 and said(b, "done: made")
    assert len(rounds) > 40                                   # the old fixed limit, passed
    assert b.trades and all(n <= town.TRADE_MOST for n in b.trades)
    assert any(n == town.TRADE_MOST for n in b.trades)
    assert b.load() <= b.maxload


def test_as_many_as_can_be_made_ends_on_the_arnebia_and_says_so(game: Any) -> None:
    b, _c, _rounds = game({}, coin(2), arnebia=3_000_000, maxload=6000)
    crafting.gem_maker(as_bot(b), job(level=5, amount="most", supply="buy"))
    assert said(b, "spare arnebia") and said(b, "made ") and count(b, "Level 5 Topaz") > 0
    assert all(n <= town.TRADE_MOST for n in b.trades) and b.load() <= b.maxload
    assert not said(b, "stopped after")


# ---------------------------------------------------------------- genuine ends, said at once
def test_arnebia_at_the_reserve_ends_the_job_at_once_with_the_reason(game: Any) -> None:
    b, c, rounds = game({"Level 1 Topaz": 60}, itertools.cycle([True, False]), arnebia=20_100)
    crafting.gem_maker(as_bot(b), job())
    assert said(b, "the Gem Maker can't go on: not enough spare arnebia to raise Level 1 Topaz")
    assert len(rounds) == 1 and c.opened == 1                # no round again for it
    assert count(b, "Level 1 Topaz") == 60


def test_a_craftsman_that_never_answers_ends_after_a_few_rounds(game: Any) -> None:
    b, c, rounds = game({"Level 1 Topaz": 60}, itertools.cycle([True, False]), dead_opens=set(range(1, 100)))
    crafting.gem_maker(as_bot(b), job())
    assert len(rounds) == 1 + crafting.IDLE_ROUNDS and c.opened == len(rounds)
    assert said(b, "made nothing in 3 rounds in a row although the bag still has 60 Level 1 Topaz - stopping")
    assert said(b, "made 0 of the 2 Level 4 Topaz asked for")


def test_nothing_left_and_no_supply_says_why(game: Any) -> None:
    b, _c, _rounds = game({"Level 1 Topaz": 3}, itertools.repeat(True))
    crafting.gem_maker(as_bot(b), job())
    assert said(b, "nothing left to make Level 4 Topaz from, and getting more Level 1 Topaz Shard is off")


def test_a_market_that_sells_none_ends_the_job_with_the_reason(game: Any) -> None:
    b, _c, _rounds = game({}, itertools.repeat(True))
    b.market_most = 0
    crafting.gem_maker(as_bot(b), job(level=2, count=1, amount=1, supply="buy"))
    assert said(b, "the Shard Market sold 0 of the")
    assert said(b, "no more Level 1 Topaz Shard came into the bag - the Gem Maker stops here")
    assert len(b.trades) <= 2                                 # the weight's first small batch, never more


# ---------------------------------------------------------------- buying and taking out
def test_a_partial_market_batch_stops_the_buying(game: Any) -> None:
    b, _c, _rounds = game({}, itertools.repeat(True), maxload=10_000)
    b.market_most = 1200                                      # the market hands over fewer than asked
    got = crafting.buy_shards(as_bot(b), SHARD, 5000)
    assert got == 1200 and b.trades == [2000]
    assert said(b, "the Shard Market sold 1200 of the 2000 Level 1 Topaz Shard asked for - buying no more now")


def test_market_batches_follow_the_bag_s_room_and_the_load_limit(game: Any) -> None:
    b, _c, _rounds = game({"Level 1 Topaz": 500}, itertools.repeat(True), maxload=6000)
    b.p["bag_full_at"] = 90                                   # 5400 counts as full: room for 4900 shards
    assert crafting.fill_with(as_bot(b), SHARD, 1.0, "buy", 0) == 4900
    assert b.trades == [2000, 2000, 900] and b.load() == 5400


def test_the_purse_limits_each_batch(game: Any) -> None:
    # 20,000 kept back; 70,000 spare at 50 + 25 (the gem fee per shard) each: 933
    b, _c, _rounds = game({}, itertools.repeat(True), arnebia=90_000, maxload=10_000)
    b.p["gem_fees"] = {"level 1 topaz": GEM_FEE}
    assert crafting.buy_shards(as_bot(b), SHARD, 5000) == 933
    assert b.trades == [933]


def test_storehouse_takes_out_a_bagful_in_one_go(monkeypatch: pytest.MonkeyPatch, game: Any) -> None:
    # the storehouse has no 2000-a-time limit: what fits comes out at once
    b, _c, _rounds = game({}, itertools.repeat(True), maxload=6000)
    taken: list[int | None] = []

    def visit(bot: Bot, role: str | None, name: str | None = None, candidates: object = None) -> str:
        bot.top = menu(["item storage"], ["1; 9000 %s" % SHARD])
        bot.open = True
        return "Storehouse"
    monkeypatch.setattr(town, "visit", visit)
    monkeypatch.setattr(town, "open_section", returning(True))

    def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
        taken.append(quantity)
        add(bot, SHARD, quantity or 0)
        return True
    monkeypatch.setattr(town, "pick", pick)
    assert crafting.fill_with(as_bot(b), SHARD, 1.0, "storage", 0) == 6000
    assert taken == [6000] and b.trades == []
