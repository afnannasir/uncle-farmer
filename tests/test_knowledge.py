# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""What the bot knows about the game: the item table, the category/shop/buyer mapping (the table
plus name heuristics), cities and their NPCs, who trades what, and the fallback when the built file
is missing."""

from __future__ import annotations

import pytest

from unclefarmer import knowledge, paths
from pathlib import Path
from collections.abc import Iterator


@pytest.fixture(autouse=True)
def fresh_cache() -> Iterator[None]:
    knowledge._k = None
    yield
    knowledge._k = None


def test_data_loads_once_and_indexes_items() -> None:
    d = knowledge.data()
    assert d is knowledge.data()                                   # cached
    assert d["_items"]["small red potion"]["category"] == "hp_potion"
    assert len(d["cities"]) == 9
    assert d["npcs"]


def test_item_is_case_and_space_insensitive() -> None:
    item = knowledge.item("  SMALL RED POTION ")
    assert item is not None and item["name"] == "Small Red Potion"
    assert knowledge.item("no such thing") is None
    assert knowledge.item(None) is None


def test_category_uses_the_table_then_the_name() -> None:
    assert knowledge.category("Small Red Potion") == "hp_potion"
    assert knowledge.category("level 3 Ruby") == "gem"             # the table says "other"; the name decides
    assert knowledge.category("Wanderer shard") == "shard"
    assert knowledge.category("River crab") == "fish"
    assert knowledge.category("Magic herb") == "herb"
    assert knowledge.category("Iron ore") == "ore"
    assert knowledge.category("Gold Coin") == "other"


def test_shop_and_buyer_roles() -> None:
    assert knowledge.shop_role("Small Red Potion") == "potions"
    assert knowledge.shop_role("Steak") == "grocer"
    assert knowledge.shop_role("Iron ore") is None
    assert knowledge.buyer_role("Wanderer shard") == "shard_market"
    assert knowledge.buyer_role("level 3 Ruby") == "shard_market"
    assert knowledge.buyer_role("River crab") == "fish_buyer"
    assert knowledge.buyer_role("Magic herb") == "herb_buyer"
    assert knowledge.buyer_role("Iron ore") == "ore_buyer"
    assert knowledge.buyer_role("Gold Coin") is None


def test_cities_and_service_npcs() -> None:
    by_map = {c["map"] for c in knowledge.cities()}
    potions = knowledge.service_npcs("potions")
    assert potions
    assert potions[0] == (1021, "Pharmacist Dani", 56, 298)
    assert potions[0][0] in by_map                                 # city NPCs sort first
    assert knowledge.service_npcs("no such role") == []


def test_traders_lists_who_trades_an_item() -> None:
    assert knowledge.traders("Small Blue Potion", "sell") == \
        [(1041, "Apothecary Phillips", 180, 220, 60, "arnebia")]
    assert knowledge.traders("Puffer Fish", "buy") == \
        [(1041, "Aquaculture Buyer Luffy", 151, 192, 1300, "arnebia")]
    assert knowledge.traders("Puffer Fish", "sell") == []
    assert knowledge.traders("nothing at all", "buy") == []


def test_describe_npc() -> None:
    grocer = knowledge.describe_npc("grocer john")
    assert grocer is not None and grocer["role"] == "grocer"
    assert knowledge.describe_npc("nobody") is None
    assert knowledge.describe_npc(None) is None


def test_a_missing_file_gives_the_empty_shape(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    knowledge._k = None
    d = knowledge.data()
    assert d["npcs"] == [] and d["cities"] == [] and d["items"] == []
    assert d["_items"] == {}
    assert knowledge.item("anything") is None
    assert knowledge.cities() == []
    assert knowledge.traders("anything", "buy") == []
