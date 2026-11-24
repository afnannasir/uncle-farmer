# pyright: reportPrivateUsage=false, reportUnusedImport=false
"""An NPC that doesn't answer skips an errand, never the run.

  - The farm loop's own restock (start=False: an item down to its min while farming, on the way
    between maps, after a bag trip) ended the whole run when a shop or the storehouse didn't
    answer: its _fill had no _try_trip around it, unlike "Restock now".
  - A storehouse that doesn't answer leaves the item to the shop; it used to skip the shop too.
  - A shop that doesn't answer is passed over for the next nearest one, and isn't remembered as
    a shop that doesn't sell the item.
  - A Shard Broker that doesn't answer on a bag trip skips the shards; the storing goes on.
  - Stop, and a final Stopped (the game unreadable), still end the run."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import pytest
from conftest import as_bot, returning, stand_in

from test_restock_rules import RULE, bot
from test_town import DANI, MAGNUS, Npc, TownBot, _unload_bot, menu, opt, shops  # noqa: F401 (a fixture)
from unclefarmer.engine import bagrules, town
from unclefarmer.engine.botparts.base import Stopped


def nothing(*_a: Any, **_k: Any) -> int:
    return 0


def silent(*_a: Any, **_k: Any) -> int:
    raise Stopped("couldn't talk to Somebody")


def test_the_farm_loop_s_restock_survives_a_shop_that_doesn_t_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bot(10, arnebia=50000)
    monkeypatch.setattr(town, "restock_town", stand_in(town.restock_town, returning(1041)))
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, lambda b, item, n: 0))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, silent))
    assert town.restock(as_bot(b), [RULE]) is True            # no Stopped: the run goes on
    assert any("couldn't restock Large Red Potion: couldn't talk to Somebody" in line for line in b.lines)
    assert "large red potion" in b.__dict__["restock_failed"]  # and it isn't tried again every 30 s
    assert b.__dict__["restock_town"] is None


def test_a_storehouse_that_doesn_t_answer_leaves_the_item_to_the_shop(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bot(10, arnebia=50000)
    monkeypatch.setattr(town, "restock_town", stand_in(town.restock_town, returning(1041)))
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, silent))
    bought: list[int] = []

    def buy(b: Any, item: str, n: int) -> int:
        bought.append(n)
        return n
    monkeypatch.setattr(town, "buy", stand_in(town.buy, buy))
    assert town.restock(as_bot(b), [RULE]) is True
    assert bought == [190]
    assert any("couldn't take Large Red Potion out of the storehouse" in line for line in b.lines)


@pytest.mark.parametrize("start", [False, True])
def test_stop_still_ends_the_run(monkeypatch: pytest.MonkeyPatch, start: bool) -> None:
    b = bot(10, arnebia=50000)
    monkeypatch.setattr(town, "restock_town", stand_in(town.restock_town, returning(1041)))
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, nothing))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, silent))
    b.stop_event.set()
    with pytest.raises(Stopped):
        town.restock(as_bot(b), [RULE], start=start)


def test_a_final_stop_still_ends_the_run(monkeypatch: pytest.MonkeyPatch) -> None:
    b = bot(10, arnebia=50000)
    monkeypatch.setattr(town, "restock_town", stand_in(town.restock_town, returning(1041)))

    def broken(*_a: Any, **_k: Any) -> int:
        raise Stopped("the game can't be read", final=True)
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, broken))
    with pytest.raises(Stopped):
        town.restock(as_bot(b), [RULE])


@pytest.mark.usefixtures("shops")
def test_a_shop_that_doesn_t_answer_is_passed_over_not_marked(monkeypatch: pytest.MonkeyPatch) -> None:
    went: list[Npc] = []

    def visit(b: Any, role: str | None, name: str | None = None, candidates: Sequence[Npc] | None = None) -> str:
        assert candidates
        went.append(candidates[0])
        if candidates[0] == MAGNUS:
            raise Stopped("couldn't talk to Potion Merchant Magnus")
        return candidates[0][1]
    monkeypatch.setattr(town, "visit", stand_in(town.visit, visit))
    tb = TownBot()
    tb.dialog = lambda: {"open": 1, "top": menu(opt("187 Large Red Potion"))}
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.add("Large Red Potion", quantity or 0)))
    assert town.buy(as_bot(tb), "Large Red Potion", 3) == 3
    assert went == [MAGNUS, DANI]
    assert any("Potion Merchant Magnus didn't answer" in line for line in tb.lines)
    assert MAGNUS in town.shops_for("Large Red Potion")        # still a potion seller next time


@pytest.mark.usefixtures("shops")
def test_a_shop_that_doesn_t_answer_still_stops_on_stop(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "visit", stand_in(town.visit, silent))
    tb = TownBot()
    tb.stop_event.set()
    with pytest.raises(Stopped):
        town.buy(as_bot(tb), "Large Red Potion", 3)


def test_a_bag_trip_goes_on_when_the_shard_broker_doesn_t_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Level 1 Sapphire Shard", "count": 5}, {"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning(
        [("Level 1 Sapphire Shard", "sell", 5), ("Pelt", "store", 3)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {})))
    monkeypatch.setattr(town, "_sell_one_shard", stand_in(town._sell_one_shard, silent))
    stored: list[tuple[str, int | None]] = []
    monkeypatch.setattr(town, "deposit", stand_in(town.deposit, lambda b, item, n: stored.append((item, n)) or 3))
    assert town.unload(as_bot(b)) == 1
    assert stored == [("Pelt", 3)]
    assert any("couldn't sell Level 1 Sapphire Shard" in line for line in b.lines)


@pytest.mark.usefixtures("shops")
def test_when_no_shop_answers_it_says_so_not_that_none_sells_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "visit", stand_in(town.visit, silent))
    tb = TownBot()
    with pytest.raises(Stopped, match="none of the shops that sell Large Red Potion answered"):
        town.buy(as_bot(tb), "Large Red Potion", 3)
    assert not any("no shop near enough lists" in line for line in tb.lines)
