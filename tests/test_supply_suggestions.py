"""The supplies the bag manager offers: one row for each thing the character uses, picked as a
dungeon run picks them, sized by kind so a row ticked by mistake can't spend a fortune (200 Alloy
Toolboxes were 16 million arnebia), and from the storehouse only when no shop sells the item. And
the one town shop that lacks part of its kind's list: Sunset City's pharmacist has no Large
potions (said by a player, 2026-11-27)."""

from __future__ import annotations

from typing import Any

import pytest

from unclefarmer import knowledge
from unclefarmer.engine import bagrules, town


def _scan(hotkeys: list[str], bag: dict[str, int] | None = None) -> dict[str, Any]:
    return {"hotkeys": [{"item": h} for h in hotkeys],
            "bag": [{"name": n, "count": c} for n, c in (bag or {}).items()]}


PET = {"pets": [{"name": "Frog", "use": True}]}


def _sizes(rows: list[dict[str, Any]]) -> dict[str, tuple[int, int, str]]:
    return {r["item"]: (r["min"], r["stock"], r["source"]) for r in rows}


def test_each_kind_is_offered_with_its_own_sizes() -> None:
    scan = _scan(["Large Red Potion", "Large Blue Potion", "Pet Large Red Potion",
                  "Pet Large Blue Potion", "Pet Rice Roll", "Alloy Toolbox"])
    assert _sizes(bagrules.suggest_supplies(scan, PET)) == {
        "Large Red Potion": (30, 200, "tell"), "Large Blue Potion": (30, 200, "tell"),
        "Pet Large Red Potion": (20, 100, "tell"), "Pet Large Blue Potion": (20, 100, "tell"),
        "Pet Rice Roll": (3, 30, "tell"), "Alloy Toolbox": (1, 5, "tell")}


def test_rows_come_in_the_order_of_use_and_once_each() -> None:
    scan = _scan(["Alloy Toolbox", "Large Blue Potion", "Large Red Potion", "Large Blue Potion"])
    assert [r["item"] for r in bagrules.suggest_supplies(scan)] == [
        "Large Red Potion", "Large Blue Potion", "Alloy Toolbox"]


def test_with_nothing_on_a_hotkey_the_strongest_in_the_bag_is_offered() -> None:
    scan = _scan([], {"Small Red Potion": 40, "Large Red Potion": 3, "Medium Blue Potion": 9,
                      "Small Toolbox": 2, "Large Toolbox": 1})
    assert [r["item"] for r in bagrules.suggest_supplies(scan)] == [
        "Large Red Potion", "Medium Blue Potion", "Large Toolbox"]


def test_a_potion_the_profile_names_wins_over_the_hotkey() -> None:
    rows = bagrules.suggest_supplies(_scan(["Large Red Potion"]), {"hp_potion": "Medium Red Potion"})
    assert rows[0]["item"] == "Medium Red Potion"


def test_no_blues_without_mp_and_no_pet_rows_without_a_pet() -> None:
    bag = {"Large Red Potion": 9, "Large Blue Potion": 9, "Pet Large Red Potion": 9, "Pet Rice Roll": 9}
    rows = bagrules.suggest_supplies(_scan([], bag), {"mp_use": False})
    assert [r["item"] for r in rows] == ["Large Red Potion"]


def test_the_pet_s_food_is_the_one_the_profile_feeds() -> None:
    rows = bagrules.suggest_supplies(_scan([]), {**PET, "pet_food": "Pet Rice Roll"})
    assert _sizes(rows) == {"Pet Rice Roll": (3, 30, "tell")}


def test_what_no_shop_sells_is_offered_from_the_storehouse_only() -> None:
    # Super potions are crafted or come in gifts; a Mist Potion on a hotkey is still offered
    scan = _scan(["Super Red Potion", "Pet Super Blue Potion", "Mist Potion"])
    assert _sizes(bagrules.suggest_supplies(scan, PET)) == {
        "Super Red Potion": (30, 200, "storage"), "Pet Super Blue Potion": (20, 100, "storage"),
        "Mist Potion": (5, 20, "storage")}


def test_pliers_on_a_hotkey_are_offered_sized_for_one_piece_at_a_time() -> None:
    assert _sizes(bagrules.suggest_supplies(_scan(["Alloy Plier"]))) == {"Alloy Plier": (10, 50, "tell")}


def test_what_is_no_supply_is_never_offered() -> None:
    scan = _scan(["Hearthstone", "Wolf Pelt", ""], {"Wolf Pelt": 5})
    assert bagrules.suggest_supplies(scan) == []
    assert bagrules.suggest_supplies(None) == [] and bagrules.suggest_supplies({}) == []


def test_a_supply_added_by_hand_is_sized_by_its_kind() -> None:
    assert bagrules.new_supply("Large Toolbox") == {
        "item": "Large Toolbox", "min": 1, "stock": 5, "source": "tell", "on": True}
    assert bagrules.new_supply("Steak")["source"] == "storage"        # the Chef makes it
    assert (bagrules.new_supply("Something New")["min"], bagrules.new_supply("Something New")["stock"]) == (5, 20)


def test_sunset_city_s_pharmacist_is_never_sent_to_for_large_potions() -> None:
    dani = next(n for n in knowledge.service_npcs("potions") if n[1] == "Pharmacist Dani")
    assert dani[0] == 1021
    for item in ("Large Red Potion", "Large Blue Potion", "Pet Large Red Potion", "Pet Large Blue Potion"):
        assert knowledge.listed_lacks(dani[0], dani[1], item)
        assert dani not in town.shops_for(item)
        assert not town.listed_shop(dani, item)
    for item in ("Small Red Potion", "Medium Blue Potion", "Pet Medium Red Potion"):
        assert dani in town.shops_for(item)
    assert any(n[1] != "Pharmacist Dani" for n in town.shops_for("Large Red Potion"))


def test_the_bag_window_keeps_only_the_rows_the_player_took_up(monkeypatch: Any) -> None:
    wx = pytest.importorskip("wx")
    from unclefarmer.gui import bagmanager
    saved: list[dict[str, Any]] = []
    def save(_w: object, _f: object, _who: str, p: dict[str, Any]) -> bool:
        saved.append(p)
        return True
    monkeypatch.setattr(bagmanager, "save_file", save)
    app = wx.GetApp() or wx.App(False)
    frame = wx.Frame(None)
    profile = {"scan": _scan(["Large Red Potion", "Super Blue Potion", "Alloy Toolbox"])}
    d = bagmanager.BagManagerDialog(frame, "Hero", profile)
    try:
        assert [r["on"] for r in d.supplies] == [False, False, False]      # offered, not ticked
        d.EndModal = lambda _code: None                                   # type: ignore[method-assign]
        d.on_ok(wx.CommandEvent())
        assert saved[-1]["restock"] == []                                 # suggestions only: none kept
        d.supplies[2]["on"] = True
        d.on_ok(wx.CommandEvent())
        assert [r["item"] for r in saved[-1]["restock"]] == ["Alloy Toolbox"]
        assert (saved[-1]["restock"][0]["min"], saved[-1]["restock"][0]["stock"]) == (1, 5)
    finally:
        d.Destroy()
        frame.Destroy()
        app.ProcessPendingEvents()
