"""Tools, Gem Breaker (engine/gembreak.py), against a pretend Gem Craftsman that follows the game: a
gem gives 3 of the level below for nothing, a Level 1 gem 3 shards, a crystal 20 shards for a fee."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot, returning, stand_in
from test_crafting import Bot, add, entry
from unclefarmer.engine import chores, crafting, gembreak, script, town
from unclefarmer.engine.bot import Stopped

FEE = 357


def count(b: Bot, name: str) -> int:
    return sum(r["count"] for r in b.bag if r["name"].lower() == name.lower())


class Craftsman:
    def __init__(self, b: Bot) -> None:
        self.b = b
        self.item = ""
        self.asked: list[tuple[str, int]] = []
        self.said: list[str] = []
        self.refuse_above = 10 ** 9
        self.wrong_page = False
        self.heavy_above: int | None = None
        b.on_choose = self.choose

    def show(self, rows: list[str]) -> None:
        self.b.top = {"row": 0, "col": 0, "rows": [[entry(t)] for t in rows]}
        self.b.open = True

    def open(self) -> str:
        self.show(["gem synthesis", "shard synthesis", "break into shards", "Batch gem synthesis"])
        return "gang"

    def gives(self, name: str) -> tuple[int, str, int]:
        words = name.split()
        level, kind = int(words[1]), " ".join(words[2:])
        if "Crystal" in kind:
            return 20, kind + " Shard", FEE
        return 3, "Level %d %s" % (level - 1, kind) if level > 1 else "Level 1 %s Shard" % kind, 0

    def choose(self, pattern: str) -> dict[str, Any] | None:
        b = self.b
        where = b.find_entry(b.top, pattern) if b.open and b.top else None
        if where is None:
            return None
        e: dict[str, Any] = (b.top or {})["rows"][where[0]][where[1]]
        text = str(e["options"][0])
        if text == "break into shards":
            self.show(["%s %d" % (r["name"], r["count"]) for r in b.bag
                       if r["count"] > 0 and gembreak.parse(r["name"])])
        elif text.startswith("Level "):
            self.item = text.rsplit(" ", 1)[0]
            k, product, fee = self.gives(self.item)
            shown = "Level 9 Other" if self.wrong_page else self.item
            self.show(["1 %s can break into %d %s; fee %d arnebia" % (shown, k, product, fee),
                       "Press here to enter the number of %s to break" % self.item])
        return e

    def quantity(self, n: int) -> bool:
        self.asked.append((self.item, n))
        self.b.close_dialogs()
        if self.heavy_above is not None and n > self.heavy_above:
            self.said.append("overweight")
            return True
        if n > self.refuse_above:
            return True
        k, product, fee = self.gives(self.item)
        add(self.b, self.item, -n)
        add(self.b, product, n * k)
        add(self.b, "Arnebia", -n * fee)
        return True


@pytest.fixture
def game(monkeypatch: pytest.MonkeyPatch) -> Any:
    def make(bag: dict[str, int], arnebia: int = 10 ** 7) -> tuple[Bot, Craftsman]:
        b = Bot(bag=[{"name": "Arnebia", "count": arnebia}] + [{"name": n, "count": k} for n, k in bag.items()])
        c = Craftsman(b)
        monkeypatch.setattr(crafting, "open_craftsman", stand_in(crafting.open_craftsman, lambda b, role, where: c.open()))

        def open_section(bot: Bot, pattern: str) -> bool:
            return bot.choose(pattern) is not None
        monkeypatch.setattr(town, "open_section", open_section)

        def pick(bot: Bot, pattern: str, quantity: int | None = None) -> bool:
            return bot.choose(pattern) is not None and quantity is not None and c.quantity(quantity)
        monkeypatch.setattr(town, "pick", pick)

        def said_mark(bot: Any) -> int:
            return len(c.said)

        def said_since(bot: Any, since: int) -> list[str]:
            return c.said[since:]
        monkeypatch.setattr(chores, "said_mark", said_mark)
        monkeypatch.setattr(chores, "said_since", said_since)
        return b, c
    return make


def job(**over: Any) -> dict[str, Any]:
    return {**gembreak.DEFAULTS, "colours": ["Ruby"], "where": "gang", **over}


def test_what_counts_as_something_to_break() -> None:
    assert gembreak.parse("Level 2 Tiger Eye Stone") == gembreak.Item("level 2 tiger eye stone", 2, "tiger eye stone", None)
    item = gembreak.parse("Level 4 Weapon Enhance Crystal")
    assert item is not None and item.crystal == "weapon"
    for name in ("Level 1 Ruby Shard", "Equipment Enhance Crystal Shard", "Arnebia", "Level 2 Odd Crystal"):
        assert gembreak.parse(name) is None


def test_gems_go_all_the_way_to_shards_from_the_allowed_level(game: Any) -> None:
    b, c = game({"Level 3 Ruby": 1, "Level 2 Ruby": 2, "Level 1 Ruby": 1})
    gembreak.run(as_bot(b), job())
    assert c.asked == [("Level 2 Ruby", 2), ("Level 1 Ruby", 7)]
    assert count(b, "Level 1 Ruby Shard") == 21
    assert count(b, "Level 3 Ruby") == 1                   # above the limit: left alone


def test_what_a_break_leaves_is_waited_for(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, c = game({"Level 2 Ruby": 2, "Level 1 Ruby": 2})
    real = c.quantity
    owed: list[tuple[str, int]] = []

    def late(n: int) -> bool:
        before = count(b, c.gives(c.item)[1])
        real(n)
        product = c.gives(c.item)[1]
        owed.append((product, count(b, product) - before))
        add(b, product, -owed[-1][1])
        return True

    def arrive(seconds: float) -> None:
        while owed:
            name, k = owed.pop()
            add(b, name, k)
    monkeypatch.setattr(c, "quantity", late)
    monkeypatch.setattr(b, "sleep", arrive)
    gembreak.run(as_bot(b), job())
    assert c.asked == [("Level 2 Ruby", 2), ("Level 1 Ruby", 8)]
    assert count(b, "Level 1 Ruby Shard") == 24


def test_only_the_ticked_colours_are_touched(game: Any) -> None:
    b, c = game({"Level 1 Ruby": 1, "Level 1 Topaz": 5})
    gembreak.run(as_bot(b), job())
    assert c.asked == [("Level 1 Ruby", 1)] and count(b, "Level 1 Topaz") == 5


def test_it_can_stop_at_a_gem_level(game: Any) -> None:
    b, c = game({"Level 3 Ruby": 2})
    gembreak.run(as_bot(b), job(top=3, stop="level", stop_level=2))
    assert c.asked == [("Level 3 Ruby", 2)] and count(b, "Level 2 Ruby") == 6


def test_it_keeps_as_many_as_asked_of_each_level(game: Any) -> None:
    b, _c = game({"Level 2 Ruby": 3})
    gembreak.run(as_bot(b), job(keep=1))
    assert count(b, "Level 2 Ruby") == 1 and count(b, "Level 1 Ruby") == 1
    assert count(b, "Level 1 Ruby Shard") == 15


def test_crystals_stay_unless_ticked(game: Any) -> None:
    b, c = game({"Level 1 Equipment Enhance Crystal": 2, "Level 1 Ruby": 1})
    gembreak.run(as_bot(b), job())
    assert c.asked == [("Level 1 Ruby", 1)]


def test_crystals_pay_their_fee_from_the_page_and_respect_the_reserve(game: Any) -> None:
    b, c = game({"Level 2 Equipment Enhance Crystal": 5}, arnebia=20_000 + 2 * FEE)
    gembreak.run(as_bot(b), job(colours=[], equipment=True))
    assert c.asked == [("Level 2 Equipment Enhance Crystal", 2)]
    assert count(b, "Equipment Enhance Crystal Shard") == 40 and count(b, "Arnebia") == 20_000
    assert any("not enough spare arnebia to break" in line for line in b.lines)


def test_crystals_have_their_own_level_limit(game: Any) -> None:
    b, c = game({"Level 3 Weapon Enhance Crystal": 1, "Level 2 Weapon Enhance Crystal": 1})
    gembreak.run(as_bot(b), job(colours=[], weapon=True, crystal_top=2, top=30))
    assert c.asked == [("Level 2 Weapon Enhance Crystal", 1)]


def test_nothing_is_typed_on_another_items_page(game: Any) -> None:
    b, c = game({"Level 1 Ruby": 2})
    c.wrong_page = True
    gembreak.run(as_bot(b), job())
    assert c.asked == [] and count(b, "Level 1 Ruby") == 2
    assert any("didn't come up" in line for line in b.lines)


def test_a_refused_amount_is_halved(game: Any) -> None:
    b, c = game({"Level 1 Ruby": 8})
    c.refuse_above = 2
    gembreak.run(as_bot(b), job())
    assert [n for _i, n in c.asked[:3]] == [8, 4, 2]
    assert count(b, "Level 1 Ruby") == 0                   # the rest in the next steps


def test_overweight_asks_for_less_down_to_one_then_stops_everything(game: Any) -> None:
    b, c = game({"Level 2 Equipment Enhance Crystal": 16, "Level 1 Ruby": 1})
    c.heavy_above = 0
    gembreak.run(as_bot(b), job(equipment=True))
    assert [n for _i, n in c.asked] == [16, 8, 4, 2, 1]        # and the Ruby isn't tried
    assert any("too heavy to break any more" in line for line in b.lines)


def test_overweight_at_some_amount_still_breaks_what_fits(game: Any) -> None:
    b, c = game({"Level 2 Equipment Enhance Crystal": 8})
    c.heavy_above = 4
    gembreak.run(as_bot(b), job(colours=[], equipment=True))
    assert count(b, "Level 2 Equipment Enhance Crystal") == 0
    assert c.asked[:2] == [("Level 2 Equipment Enhance Crystal", 8), ("Level 2 Equipment Enhance Crystal", 4)]


def test_it_gives_up_on_what_the_game_wont_break(game: Any) -> None:
    b, c = game({"Level 1 Ruby": 4})
    c.refuse_above = 0
    gembreak.run(as_bot(b), job())
    assert [n for _i, n in c.asked] == [4, 2, 1] and count(b, "Level 1 Ruby") == 4
    assert any("nothing to break" in line for line in b.lines)


def test_a_check_breaks_nothing_and_reads_what_a_crystal_costs(game: Any) -> None:
    b, c = game({"Level 2 Ruby": 1, "Level 1 Weapon Enhance Crystal": 2})
    gembreak.run(as_bot(b), job(weapon=True), check_only=True)
    assert c.asked == [] and count(b, "Level 2 Ruby") == 1
    text = "\n".join(b.lines)
    assert "would break 1 Level 2 Ruby" in text and "that would leave 9 Level 1 Ruby Shard" in text
    assert "each gives 20 Weapon Enhance Crystal Shard for 357 arnebia" in text


def test_it_sells_by_the_bag_rules_when_done(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    sold: list[int] = []

    def sell(bot: Any) -> int:
        sold.append(1)
        return 1
    monkeypatch.setattr(town, "sell_shards_now", sell)
    b, _c = game({"Level 1 Ruby": 1})
    gembreak.run(as_bot(b), job(sell=True))
    assert sold == [1]


def test_nothing_is_sold_unless_asked(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    sold: list[int] = []

    def sell(bot: Any) -> int:
        sold.append(1)
        return 1
    monkeypatch.setattr(town, "sell_shards_now", sell)
    b, _c = game({"Level 1 Ruby": 1})
    gembreak.run(as_bot(b), job())
    assert sold == [] and count(b, "Level 1 Ruby Shard") == 3


def test_too_heavy_sells_and_goes_on(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, c = game({"Level 1 Ruby": 1})
    c.heavy_above = 0
    sold: list[int] = []

    def sell(bot: Any) -> int:
        sold.append(1)
        c.heavy_above = None
        return 1
    monkeypatch.setattr(town, "sell_shards_now", sell)
    gembreak.run(as_bot(b), job(sell=True))
    assert count(b, "Level 1 Ruby") == 0 and len(sold) == 2    # for room, then at the end


def test_too_heavy_with_nothing_to_sell_stops_and_says_why(game: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    b, c = game({"Level 1 Ruby": 1})
    c.heavy_above = 0
    monkeypatch.setattr(town, "sell_shards_now", returning(0))
    gembreak.run(as_bot(b), job(sell=True))
    assert count(b, "Level 1 Ruby") == 1
    assert any("too heavy to break any more" in line for line in b.lines)
    assert not any("nothing to break" in line for line in b.lines)


def test_gems_and_crystals_keep_their_own_numbers(game: Any) -> None:
    b, c = game({"Level 2 Equipment Enhance Crystal": 5, "Level 1 Ruby": 3})
    gembreak.run(as_bot(b), job(equipment=True, keep=1, crystal_keep=3))
    assert ("Level 2 Equipment Enhance Crystal", 2) in c.asked and ("Level 1 Ruby", 2) in c.asked
    assert count(b, "Level 2 Equipment Enhance Crystal") == 3 and count(b, "Level 1 Ruby") == 1


def test_the_plan_counts_the_whole_way_down() -> None:
    gone, made = gembreak.outcome({"Level 2 Ruby": 2, "Level 1 Ruby": 1}, job())
    assert gone == {"level 2 ruby": 2, "level 1 ruby": 7}
    assert made == {"level 1 ruby shard": 21}


def test_it_must_be_set_up_first(game: Any) -> None:
    b, _c = game({"Level 1 Ruby": 1})
    with pytest.raises(Stopped, match="set it up first"):
        gembreak.run(as_bot(b), dict(gembreak.DEFAULTS))


def test_the_default_is_careful() -> None:
    d = gembreak.config({})
    assert d["top"] == 2 and d["colours"] == [] and not d["weapon"] and not d["equipment"] and not d["sell"]


@pytest.mark.parametrize("text", ["gem_breaker", "gem_breaker check"])
def test_the_script_command(text: str) -> None:
    main, _subs, _stop = script.parse(text)
    assert main[0].kind == "gem_breaker" and main[0].opts["check"] is (text != "gem_breaker")
