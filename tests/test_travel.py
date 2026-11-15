# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""travel.py: the graph and the legs plan() returns - walked off through an exit, ridden on an NPC
transport, landed by a travel item, or leaving an instance through the Action menu - plus the pure
helpers (SQUARES, item_options, is_instance, matches, describe). test_routes.py already covers the
A* vs Dijkstra route costs, so this file does not repeat them.
"""

from __future__ import annotations

import pytest

from unclefarmer import atlas, travel
from typing import Any

from conftest import stand_in


# ---------------------------------------------------------------- pure helpers
def test_squares_hold_the_nine_city_landings() -> None:
    assert len(travel.SQUARES) == 9
    assert travel.SQUARES["sunset square"] == (1021, 73, 286)
    assert travel.SQUARES["moon square"] == (1101, 200, 202)
    assert travel.SQUARES["dark night square"] == (1181, 225, 230)
    assert travel.SQUARES["giant rock square"] == (1041, 200, 205)


@pytest.mark.parametrize("text, want", [
    ("Please LEAVE now", True),
    ("Go to Tulip", True),
    ("", False),
    (None, False),
    ("nothing here", False),
])
def test_matches_is_case_insensitive_and_refuses_empty_text(text: str, want: bool) -> None:
    pattern = r"leave|go to"
    assert travel.matches(pattern, text) is want


# ---------------------------------------------------------------- item_options
def test_item_options_need_a_bag_with_a_stone() -> None:
    assert travel.item_options(None) == []
    assert travel.item_options([]) == []
    assert travel.item_options([{"name": "Sunset City Teleportation stone", "count": 0}]) == []


def test_a_bag_row_without_a_count_still_reads_as_one() -> None:
    opts = travel.item_options([{"name": "Sunset City Teleportation stone"}])
    assert opts == [("Sunset City Teleportation stone", [], (1021, 73, 286), travel.TELEPORT)]


def test_a_single_city_stone_lands_on_its_square_with_no_menu() -> None:
    bag = [{"name": "Sunset City Teleportation stone", "count": 1}]
    assert travel.item_options(bag) == [
        ("Sunset City Teleportation stone", [], travel.SQUARES["sunset square"], travel.TELEPORT)]


def test_platinum_offers_every_listed_city_with_a_menu_pick() -> None:
    bag = [{"name": "Platinum Teleportation stone", "count": 3}]
    opts = travel.item_options(bag)
    expected = travel.ITEMS["Platinum Teleportation stone"]
    assert len(opts) == len(expected) == 8
    assert [(o[1], o[2], o[3]) for o in opts] == [
        ([pick], travel.SQUARES[square], travel.TELEPORT) for pick, square in expected]


def test_garth_empire_stone_offers_the_two_cities_it_serves() -> None:
    bag = [{"name": "Garth Empire Teleportation stone", "count": 1}]
    opts = travel.item_options(bag)
    assert [(o[1], o[2]) for o in opts] == [
        (["holy blood square"], travel.SQUARES["holy blood square"]),
        (["gouge knife square"], travel.SQUARES["gouge knife square"])]


def test_black_gold_stone_lands_on_its_villages_and_ruins_at_a_higher_cost() -> None:
    bag = [{"name": "Black Gold Teleportation stone", "count": 1}]
    opts = travel.item_options(bag)
    lands = [land for _pick, land in travel.BLACK_GOLD]
    assert len(opts) == len(travel.BLACK_GOLD)
    assert all(o[3] == travel.TELEPORT + 50 and o[2] in lands for o in opts)


def test_the_hearthstone_needs_a_known_home_and_a_stone_or_a_key() -> None:
    bag = [{"name": "Hearthstone", "count": 1}]
    square = travel.SQUARES["sunset square"]
    assert travel.item_options(bag, hearth_home="sunset square") == [
        ("Hearthstone", [], square, travel.HEARTH)]
    assert travel.item_options(bag, hearth_home=None) == []
    # a hotkey is enough even with no stone in the bag
    assert travel.item_options([], hearth_home="Sunset Square", hearth_key=True) == [
        ("Hearthstone", [], square, travel.HEARTH)]
    # an unknown home is ignored, not guessed
    assert travel.item_options([], hearth_home="nowhere square", hearth_key=True) == []


def test_item_names_are_matched_case_insensitively_and_trimmed() -> None:
    bag = [{"name": "  HEARTHSTONE  ", "count": 1}]
    assert travel.item_options(bag, hearth_home="moon square") == [
        ("Hearthstone", [], travel.SQUARES["moon square"], travel.HEARTH)]


# ---------------------------------------------------------------- the graph's small readers
@pytest.mark.parametrize("mid, want", [(1001, False), (1041, False), (201, True), (202, True)])
def test_is_instance_maps_you_cannot_walk_out_of(mid: int, want: bool) -> None:
    assert travel.is_instance(mid) is want


def test_arrival_port_finds_the_landing_by_marker() -> None:
    idx = travel._arrival_port(1041, 1)
    assert idx is not None
    port = travel._ports(1041)[idx]
    assert port["kind"] == "arrival" and port["marker"] == 1
    assert travel._arrival_port(1041, 999) is None


def test_transports_are_numbered_for_avoiding_them() -> None:
    t = travel.transports()
    assert t
    assert [x["id"] for x in t] == list(range(len(t)))


def test_open_world_holds_the_hub_and_leaves_instances_out() -> None:
    om = travel.open_maps()
    assert isinstance(om, set)
    assert travel.HUB in om
    assert 201 not in om           # a practice secret room, closed


# ---------------------------------------------------------------- plan: same map
def test_plan_on_one_map_to_a_cell_is_a_single_walk_leg() -> None:
    assert travel.plan(1001, (50, 91), 1001, (57, 114)) == [
        {"do": "walk", "map": 1001, "cell": [57, 114]}]


def test_plan_on_one_map_with_no_cell_is_no_legs() -> None:
    assert travel.plan(1001, (50, 91), 1001) == []


def test_plan_on_one_map_carries_the_walking_cost() -> None:
    legs, cost = travel.plan(1001, (50, 91), 1001, (50, 91), with_cost=True)
    assert legs == [{"do": "walk", "map": 1001, "cell": [50, 91]}]
    assert cost == 0.0


def test_plan_on_one_map_to_an_unreachable_cell_costs_nothing() -> None:
    assert travel.plan(1001, (50, 91), 1001, (999, 999), with_cost=True) == (None, None)


def test_plan_on_one_map_without_cost_does_not_check_reachability() -> None:
    # plan()'s one-leg fast path returns the walk leg before looking at the map, so an
    # unreachable cell still yields a leg: only with_cost=True turns it into (None, None).
    assert travel.plan(1001, (50, 91), 1001, (999, 999)) == [
        {"do": "walk", "map": 1001, "cell": [999, 999]}]


# ---------------------------------------------------------------- plan: across the graph
def test_plan_across_a_map_returns_an_exit_leg() -> None:
    assert travel.plan(1024, (3, 3), 1041) == [
        {"do": "exit", "map": 1024, "exit": 0, "stand": [3, 3], "rect": [1, 1, 5, 5], "dest": 1041}]


def test_plan_takes_a_transport_npc() -> None:
    legs = travel.plan(1042, (30, 57), 1043)
    assert legs is not None
    leg, = legs
    assert leg["do"] == "npc"
    assert leg["map"] == 1042 and leg["dest"] == 1702 and leg["ride"] == 1043
    assert leg["npc"] == "Cable Car manager" and leg["kind"] == "cable_car"
    assert leg["choose"] and leg["id"] is not None


def test_plan_uses_a_bag_stone_when_there_is_no_walk() -> None:
    bag = [{"name": "Sunset City Teleportation stone", "count": 1}]
    assert travel.plan(1041, (200, 205), 1021, transport=False, bag=bag, items=True) == [
        {"do": "item", "item": "Sunset City Teleportation stone", "choose": [], "dest": 1021,
         "lands": [73, 286]}]


def test_plan_reports_the_cost_of_a_teleport() -> None:
    bag = [{"name": "Sunset City Teleportation stone", "count": 1}]
    legs, cost = travel.plan(1041, (200, 205), 1021, transport=False, bag=bag, items=True,
                             with_cost=True)
    assert legs is not None
    assert legs[0]["do"] == "item"
    assert cost == travel.TELEPORT


def test_plan_avoids_an_item_that_already_failed() -> None:
    bag = [{"name": "Sunset City Teleportation stone", "count": 1}]
    legs = travel.plan(1041, (200, 205), 1021, transport=False, bag=bag, items=True,
                       avoid=["Sunset City Teleportation stone"])
    assert legs and all(leg["do"] != "item" for leg in legs)


def test_plan_uses_the_hearthstone_key_when_home_is_known() -> None:
    legs = travel.plan(1041, (200, 205), 1021, transport=False, items=True,
                       hearth_home="sunset square", hearth_key=True)
    assert legs is not None
    assert legs[0]["do"] == "item" and legs[0]["item"] == "Hearthstone"


def test_plan_walks_when_transport_is_turned_off() -> None:
    legs = travel.plan(1041, (200, 205), 1021, transport=False, items=False)
    assert legs and all(leg["do"] in ("exit", "walk") for leg in legs)
    assert legs[-1]["do"] == "exit"


def test_plan_adds_the_final_walk_to_the_goal_cell() -> None:
    legs = travel.plan(1024, (3, 3), 1041, (200, 205))
    assert legs is not None
    assert legs[0]["do"] == "exit"
    assert legs[-1] == {"do": "walk", "map": 1041, "cell": [200, 205]}


def test_plan_cache_hands_out_a_copy() -> None:
    travel._plan_cache.clear()
    first = travel.plan(1001, (50, 91), 1001, (57, 114))
    assert first is not None
    first.append({"do": "walk", "map": 1001, "cell": [0, 0]})
    assert travel.plan(1001, (50, 91), 1001, (57, 114)) == [
        {"do": "walk", "map": 1001, "cell": [57, 114]}]


def test_plan_is_computed_once_for_the_same_question(monkeypatch: pytest.MonkeyPatch) -> None:
    travel._plan_cache.clear()
    calls: list[int] = []
    real = travel._plan
    def counted(*a: Any, **kw: Any) -> Any:
        calls.append(1)
        return real(*a, **kw)
    monkeypatch.setattr(travel, "_plan", counted)
    travel.plan(1001, (50, 91), 1001, (57, 114))
    travel.plan(1001, (50, 91), 1001, (57, 114))
    assert len(calls) == 1


def test_steps_from_is_cached_per_question(monkeypatch: pytest.MonkeyPatch) -> None:
    travel._steps_cache.clear()
    calls: list[int] = []
    real = travel._steps_uncached
    monkeypatch.setattr(travel, "_steps_uncached", stand_in(travel._steps_uncached, lambda m, cell, targets: calls.append(1) or real(m, cell, targets)))
    a = travel._steps_from(1001, (50, 91), [(57, 114)])
    b = travel._steps_from(1001, (50, 91), [(57, 114)])
    assert a == b and a
    assert len(calls) == 1
    a.clear()                                        # a copy went out, the cache is intact
    assert travel._steps_from(1001, (50, 91), [(57, 114)]) == b


# ---------------------------------------------------------------- plan: leaving an instance
def test_plan_leaves_an_instance_through_the_action_menu() -> None:
    assert travel.plan(201, (100, 100), 1041) == [{"do": "leave", "map": 201}]


def test_plan_leave_carries_a_fixed_penalty_cost() -> None:
    legs, cost = travel.plan(201, (100, 100), 1041, with_cost=True)
    assert legs == [{"do": "leave", "map": 201}]
    assert cost == 500.0


def test_plan_gives_up_when_leaving_is_avoided() -> None:
    assert travel.plan(201, (100, 100), 1041, avoid=["leave"]) is None


# ---------------------------------------------------------------- describe
def test_describe_writes_one_line_per_leg() -> None:
    out = travel.describe([
        {"do": "exit", "map": 1024, "dest": 1041},
        {"do": "walk", "map": 1041, "cell": [2, 3]},
        {"do": "npc", "map": 1042, "npc": "Cable Car manager", "kind": "cable_car", "dest": 1702,
         "cost": 2000},
        {"do": "item", "item": "Hearthstone", "choose": [], "dest": 1021},
        {"do": "leave", "map": 201},
    ])
    assert out[0] == "Central Kingdom (1024): walk out to central plains (1041)"
    assert out[1].endswith("walk to 2,3")
    assert "cable car" in out[2] and "2000 arnebia" in out[2]
    assert out[3].startswith("use Hearthstone")
    assert out[4].endswith("leave through the Action menu")


def test_describe_shows_the_menu_entry_for_multi_city_stones() -> None:
    out = travel.describe([{"do": "item", "item": "Platinum Teleportation stone",
                            "choose": ["sunset square"], "dest": 1021}])
    assert "(sunset square)" in out[0]


def test_describe_an_unknown_transport_kind_is_just_an_npc() -> None:
    out = travel.describe([{"do": "npc", "map": 1042, "npc": "Someone", "kind": "weird",
                            "dest": 1702, "cost": None}])
    assert "NPC with the Someone" in out[0]
    assert "arnebia" not in out[0]


# The Holy Blood City cable car to Giant Rock City, as the game shows its menu (read live on
# 2026-11-08): both rows go to Giant Rock City, so the entry must tell the VIP row from the paying one.
HOLY_BLOOD_CAR_MENU = ["Go to Giant Rock City; VIP are free", "Go to Giant Rock City; Costs 2000 Arnebia"]


def _row_chosen(patterns: list[str], rows: list[str]) -> int | None:
    """The row the bot's menu search (find_entry: the first row any pattern matches) would take."""
    for i, text in enumerate(rows):
        if any(travel.matches(p, text) for p in patterns):
            return i
    return None


@pytest.mark.parametrize(("vip", "row"), [(False, 1), (True, 0)])
def test_the_holy_blood_city_cable_car_takes_the_row_the_character_can_ride(vip: bool, row: int) -> None:
    legs = travel.plan(1122, (56, 57), 1042, None, transport=True, avoid=set(), vip=vip, bag=[], items=False)
    assert legs is not None
    car = next(leg for leg in legs if leg["do"] == "npc" and leg.get("id") == 6)
    assert _row_chosen(car["choose"], HOLY_BLOOD_CAR_MENU) == row


def test_every_paid_ride_has_its_own_vip_entry() -> None:
    """A paid ride's menu has a free VIP row next to the paying row (same destination), so one entry
    for both would take the VIP row for everyone - the Holy Blood City cable car did."""
    for t in travel.transports():
        if t.get("kind") in ("airship", "cable_car") and isinstance(t.get("cost"), int) and t["cost"] > 0:
            assert t.get("choose_vip"), "ride %s has no VIP entry" % t["id"]
            assert all("cost" in p.lower() for p in t["choose"]), "ride %s: %s" % (t["id"], t["choose"])


# ---------------------------------------------------------------- exits the map data names no landing for
def test_ancient_ruins_is_walked_through_not_looped_through_its_small_hall() -> None:
    # The small hall (1732)'s doors back had no landing in the data. Taken as "anywhere", the
    # hall looked like a short cut to the far exit, and the game put us back by its doors: a loop.
    legs = travel.plan(1731, (50, 397), 1761)
    assert legs is not None and [(leg["map"], leg["dest"]) for leg in legs] == [(1731, 1761)]


def test_the_measured_arrivals_get_their_ports_and_routes() -> None:
    # data/atlas.json carries the landings measured in the game (marker, sources, point, comp) and
    # nothing else: the port and its routes are worked out when the atlas is read
    ruins = atlas.maps()[1731]
    measured = {a["marker"]: tuple(a["point"]) for a in ruins["arrivals"] if a["marker"] > 100}
    assert measured == {101: (50, 259), 102: (51, 148), 201: (83, 300), 202: (102, 299),
                        203: (132, 303), 204: (133, 340)}
    for marker, at in measured.items():
        k = travel._arrival_port(1731, marker)
        assert k is not None and (travel._ports(1731)[k]["x"], travel._ports(1731)[k]["y"]) == at
        routes = [r for r in ruins["routes"] if r["a"] == k]
        assert routes and all(r["steps"] > 0 and r["back_steps"] > 0 for r in routes)
    assert travel.unknown_landing(1732, 0) is None and travel.unknown_landing(1733, 0) is None
    a = travel._arrival_port(1731, 201)
    door_a = next(r for r in ruins["routes"] if r["a"] == a and r["b"] == 0)
    assert door_a["steps"] <= 5                    # landed beside the door we came in by


def test_completing_the_arrivals_twice_adds_nothing() -> None:
    ruins = atlas.maps()[1731]
    ports, routes = len(travel._ports(1731)), len(ruins["routes"])
    travel._complete_arrivals(ruins)
    assert (len(ruins["ports"]), len(ruins["routes"])) == (ports, routes)


def test_a_door_with_no_landing_is_paired_with_the_door_it_stands_beside() -> None:
    hall, ruins = atlas.maps()[1732], atlas.maps()[1731]
    # each door back from the hall stands beside the arrival from one door of the ruins (the
    # landings measured since agree: each is a step or two from its twin)
    assert [travel._paired_doors(1732, hall["exits"][i], ruins) for i in range(4)] == [[0], [1], [3], [4]]
    assert travel._paired_doors(1732, {"dest": 1731, "stand": None}, ruins) == [0, 1, 3, 4]   # any door back
    assert travel._paired_doors(1732, hall["exits"][0], atlas.maps()[1041]) == []          # none back


def test_a_learned_landing_is_kept_on_disk_and_planned_with() -> None:
    # Pull Cave's way out to Brute Valley: no landing in the data, and no door back to pair with
    assert travel.unknown_landing(5042, 0) == (5041, 3)
    assert travel.unknown_landing(1731, 0) is None              # its landing is in the data
    assert travel.unknown_landing(5042, 99) is None
    assert not travel.learn_landing(5042, 0, 1041, (5, 5))      # landed somewhere else entirely
    assert travel.learn_landing(5042, 0, 5041, (396.6, 90.4))
    assert not travel.learn_landing(5042, 0, 5041, (397, 90))   # nothing new
    travel._landings = None                                     # as a new session reads it
    assert travel.landing(5041, 3) == (397, 90)
    assert travel.landing(5041, None) is None and travel.landing(5041, 999) is None
    legs = travel.plan(5042, (150, 80), 5041, (255, 297))
    assert legs is not None and legs[0]["map"] == 5042 and legs[-1]["map"] == 5041


def test_a_damaged_landings_file_is_read_as_far_as_it_makes_sense() -> None:
    import os
    from unclefarmer import paths
    with open(os.path.join(paths.PROFILES, travel.LANDINGS_FILE), "w", encoding="utf-8") as f:
        f.write('{"1731|201": [85, 301], "nonsense": [1, 2], "1731|202": "x", "1731|203": [true, 2]}')
    travel._landings = None
    assert travel.landings() == {(1731, 201): (85, 301)}
    with open(os.path.join(paths.PROFILES, travel.LANDINGS_FILE), "w", encoding="utf-8") as f:
        f.write("not json")
    travel._landings = None
    assert travel.landings() == {}


def test_an_exit_left_out_is_not_planned_through() -> None:
    travel._plan_cache.clear()
    legs = travel.plan(1731, (50, 397), 1761, avoid={("exit", 1731, 8), ("exit", 1731, 9)})
    assert legs is None or all((leg.get("map"), leg.get("exit")) not in ((1731, 8), (1731, 9)) for leg in legs)


def test_a_learned_landing_is_written_as_the_atlas_arrival_to_report() -> None:
    from unclefarmer import jsonio
    row = jsonio.loads(travel.arrival_row(5042, 0, (396.6, 90.4)))
    assert row == {"marker": 3, "sources": [5042], "point": [397, 90],
                   "comp": int(atlas.model(5041).comp_of(397, 90))}
    assert travel.arrival_row(1731, 0, (1, 1)) == ""             # its landing is in the data


def test_a_landing_far_from_the_atlas_is_given_as_the_arrival_to_report() -> None:
    from unclefarmer import jsonio
    assert travel.wrong_landing(1732, 0, 1731, (84, 301)) is None          # where the atlas says
    off = travel.wrong_landing(1732, 0, 1731, (120, 300))
    assert off is not None and off[0] == (83, 300)
    assert jsonio.loads(off[1])["point"] == [120, 300] and jsonio.loads(off[1])["marker"] == 201
    assert travel.wrong_landing(1732, 0, 1041, (120, 300)) is None         # not where that exit leads
    assert travel.wrong_landing(5042, 0, 5041, (10, 10)) is None           # no landing in the atlas
    assert travel.wrong_landing(1732, 99, 1731, (1, 1)) is None
