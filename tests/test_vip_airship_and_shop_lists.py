"""An airship's single menu row taken whatever the VIP box says, and the town shops' lists (read live
2026-11-19): a listed item sold in every town, an unlisted one nowhere, and no trip for it."""

from __future__ import annotations

from typing import Any

from unclefarmer import knowledge, travel
from unclefarmer.engine import town

from conftest import as_bot

VIOLET = next(t for t in travel.transports() if t["kind"] == "airship" and t["dest"] == 1161 and t["map"] == 1081)
VIP_ROW = "Go to Violet city; VIP are free"                        # what a VIP's menu shows (live)
FARE_ROW = "Go to Violet city; costs 1000 arnebia"


def _takes(patterns: list[str], text: str) -> bool:
    return len(patterns) == 1 and travel.matches(patterns[0], text)


def test_an_airship_takes_its_one_row_with_a_fare_or_free_whatever_the_box() -> None:
    for free in (False, True):
        choose = travel.ride_entry(VIOLET, free)
        assert _takes(choose, VIP_ROW) and _takes(choose, FARE_ROW)
        assert not _takes(choose, "Go to Moon City; VIP are free")


def test_a_cable_car_still_takes_the_row_the_box_says() -> None:
    car = next(t for t in travel.transports() if t["kind"] == "cable_car" and t.get("choose_vip"))
    assert travel.ride_entry(car, True) == car["choose_vip"]
    assert travel.ride_entry(car, False) == car["choose"]


def test_a_vip_with_the_box_unticked_still_flies_from_tulip_city() -> None:
    legs = travel.plan(1081, (345, 219), 1161, None, transport=True, avoid=set(), vip=False, bag=[], items=False)
    assert legs is not None
    flight = next(leg for leg in legs if leg.get("do") == "npc" and leg.get("kind") == "airship")
    assert _takes(flight["choose"], VIP_ROW) and flight["vip"] is False


def test_the_shop_lists_cover_every_potion_and_grocer_good() -> None:
    lists = knowledge.shop_lists()
    assert lists["potions"]["large red potion"] == 450 and lists["potions"]["pet large blue potion"] == 1000
    assert lists["grocer"]["hearthstone"] == 10000 and lists["grocer"]["pet rice roll"] == 1000
    assert knowledge.unit_price("Small Plier") == 1000              # no menu in the game data: the list's
    assert knowledge.listed_role("Alloy Toolbox") == "grocer" and knowledge.listed_role("Steak") is None


def test_every_town_pharmacist_sells_the_potions_but_not_the_gang_camps() -> None:
    shops = town.shops_for("Large Blue Potion")
    assert (1081, "Pharmacist Evan", 356, 141) in shops
    assert all(town.listed_shop(npc, "Large Blue Potion") for npc in shops if npc[0] < 4000)
    assert not any(town.listed_shop(npc, "Large Blue Potion") for npc in knowledge.service_npcs("potions")
                   if npc[0] >= 4000)


class Shopper:
    def __init__(self) -> None:
        self.lines: list[str] = []

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": "Arnebia", "count": 1_000_000}]


def test_no_trip_for_what_no_shop_sells() -> None:
    why: list[str] = []
    b = as_bot(Shopper())
    assert town.possible(b, {"item": "Steak", "min": 5, "stock": 10, "source": "buy"}, why) is False
    assert why == ["no shop sells Steak"]
    why.clear()
    assert town.possible(b, {"item": "Large Red Potion", "min": 5, "stock": 10, "source": "buy"}, why) is True
