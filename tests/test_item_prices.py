"""The shops' list prices and the game's descriptions of five items (Pet Rice Roll, Hearthstone,
Small/Large/Alloy Toolbox), held on their item records in data/knowledge.json, and every place that
uses an item's price: a run's expenses, the bag manager's "sells it for", and restock buying."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import as_bot, stand_in
from test_town import TownBot, menu, opt

from unclefarmer import knowledge, paths, stats
from unclefarmer.engine import bagrules, town

PRICES = {"Pet Rice Roll": 1000, "Hearthstone": 10000, "Small Toolbox": 20000,
          "Large Toolbox": 40000, "Alloy Toolbox": 80000}
DESCRIPTIONS = {
    "Pet Rice Roll": "Increase Amiability by 30%.",
    "Hearthstone": "Each hearthstone is made by a Space Mage using a stone taken from a fireplace for a very long "
                   "time. It is made from stone that has fully absorbed smoke and fire, then infused with space "
                   "magic. You can use it to break the barriers of space and return to the last city where "
                   "you've stayed.",
    "Small Toolbox": "Can repair all currently outfitted equipment not higher than level 60.",
    "Large Toolbox": "Can repair all currently outfitted equipment level 80 and below.",
    "Alloy Toolbox": "Can repair all currently outfitted equipment.",
}
CATEGORIES = {"Pet Rice Roll": "pet_food", "Hearthstone": "hearthstone", "Small Toolbox": "repair_equipped",
              "Large Toolbox": "repair_equipped", "Alloy Toolbox": "repair_equipped"}


def records() -> list[dict[str, Any]]:
    with open(Path(paths.DATA) / "knowledge.json", encoding="utf-8") as f:
        items: list[dict[str, Any]] = json.load(f)["items"]
    return items


@pytest.mark.parametrize("name", sorted(PRICES))
def test_each_item_record_holds_its_price_and_description_once(name: str) -> None:
    rows = [r for r in records() if r["name"] == name]
    assert len(rows) == 1                                   # updated, not duplicated
    row = rows[0]
    assert row["price"] == PRICES[name] and row["description"] == DESCRIPTIONS[name]
    assert row["category"] == CATEGORIES[name]              # the rest of the record kept
    assert knowledge.item(name) == row


def test_the_toolboxes_keep_their_repair_effects() -> None:
    effects = {r["name"]: r.get("effect") for r in records()}
    assert effects["Small Toolbox"] == "repairs everything you wear up to level 60"
    assert effects["Alloy Toolbox"] == "repairs everything you wear, any level"
    assert effects["Hearthstone"] == "takes you home to your bound city"


@pytest.mark.parametrize("name", sorted(PRICES))
def test_unit_price(name: str) -> None:
    assert knowledge.unit_price(name) == PRICES[name]
    assert knowledge.unit_price(name.upper()) == PRICES[name]


def test_a_shop_menu_price_still_wins_and_unknown_items_have_none() -> None:
    assert knowledge.unit_price("Large Red Potion") == 450       # Apothecary Phillips's recorded menu
    assert knowledge.unit_price("Nothing Such") is None
    assert knowledge.unit_price(None) is None


def test_the_run_expenses_cost_used_up_items_at_these_prices() -> None:
    prices = stats.unit_prices()
    for name, price in PRICES.items():
        assert prices[name.lower()] == (price, stats.SHOP)
    spent = stats.spending({"Alloy Toolbox": 2, "Hearthstone": 1, "Pet Rice Roll": 3}, prices)
    assert spent["Alloy Toolbox"]["cost"] == 160000
    assert spent["Hearthstone"]["cost"] == 10000
    assert spent["Pet Rice Roll"]["cost"] == 3000


def test_the_bag_manager_names_the_shop_and_price() -> None:
    assert bagrules.seller("Alloy Toolbox") == ("a grocer", 80000, "arnebia")
    assert bagrules.seller("Hearthstone") == ("a grocer", 10000, "arnebia")
    assert "a grocer sells it for 20000 arnebia" in bagrules.about("Small Toolbox")


def test_a_pet_rice_roll_is_still_known_as_pet_food() -> None:
    from unclefarmer.engine import dungeon
    assert "Pet Rice Roll" in dungeon.known_items("pet_food")


# ---------------------------------------------------------------- restock buys what it can pay for
def test_no_shop_trip_without_arnebia_for_one() -> None:
    tb = TownBot(bag=[{"name": "arnebia", "count": 30000}])
    why: list[str] = []
    rule = {"item": "Alloy Toolbox", "source": "buy"}
    assert town.possible(as_bot(tb), rule, why) is False
    assert why == ["not enough arnebia to buy Alloy Toolbox: 80000 each, 30000 in the bag"]
    tb2 = TownBot(bag=[{"name": "arnebia", "count": 30000}])
    assert town.possible(as_bot(tb2), {"item": "Small Toolbox", "source": "buy"}, []) is True


def test_buy_takes_only_what_the_arnebia_pays_for(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[{"name": "arnebia", "count": 50000}])
    grocer = (1041, "Grocer John", 10.0, 10.0)
    monkeypatch.setattr(town, "shops_for", stand_in(town.shops_for, lambda item: [grocer]))
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, lambda b, role, candidates=None:
                                                  (candidates or [None])[0]))
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role, name=None, candidates=None: "Grocer John"))
    tb.dialog = lambda: {"open": 1, "top": menu(opt("Large Toolbox"))}
    asked: list[int | None] = []

    def pick(b: Any, pattern: str, quantity: int | None) -> bool:
        asked.append(quantity)
        tb.add("Large Toolbox", quantity or 0)
        return True
    monkeypatch.setattr(town, "pick", stand_in(town.pick, pick))
    assert town.buy(as_bot(tb), "Large Toolbox", 5) == 1
    assert asked == [1]                                     # 50000 arnebia: one at 40000
    assert any("buying 1 Large Toolbox, not 5" in line for line in tb.lines)
    broke = TownBot(bag=[{"name": "arnebia", "count": 9000}])
    assert town.buy(as_bot(broke), "Hearthstone", 1) == 0
    assert any("not enough arnebia for one Hearthstone: 10000 each" in line for line in broke.spoken)
