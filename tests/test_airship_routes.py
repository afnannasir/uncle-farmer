"""Routes that mix walking with rides (cable cars, airships), on the real travel graph.

The planner is Dijkstra over walking edges and ride edges alike, so a ride is taken wherever it is
the cheaper way, and only when it can be: transports allowed, a fare the character can pay (VIP
rides are free), not a ride that failed (avoid), never a broken transport row. The routes below
come from the real data (atlas.json, transports.json); the tests check what the routes are made of
and what they cost, not a route written out by hand.
"""

from __future__ import annotations

import itertools
import random
from typing import Any

import pytest

from conftest import as_bot
from unclefarmer import atlas, travel
from unclefarmer.engine.botparts.travel import TravelMixin

CENTRAL = 1041                          # central plains, Giant Rock City
SQUARE = (200, 205)                     # its square


def rides(legs: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    return [leg for leg in legs or [] if leg["do"] == "npc"]


def maps_on_the_way(legs: list[dict[str, Any]] | None) -> list[int]:
    """The map each leg starts on, in order."""
    return [int(leg["map"]) for leg in legs or [] if "map" in leg]


def costed(goal: int, start: int = CENTRAL, cell: tuple[float, float] | None = None, **kw: Any
           ) -> tuple[list[dict[str, Any]] | None, float | None]:
    if cell is None:
        cell = SQUARE if start == CENTRAL else (travel._ports(start)[0]["x"], travel._ports(start)[0]["y"])  # pyright: ignore[reportPrivateUsage]
    return travel.plan(start, cell, goal, with_cost=True, items=False, **kw)


@pytest.fixture(autouse=True)
def fresh_plans() -> None:
    travel._plan_cache.clear()          # pyright: ignore[reportPrivateUsage]


# ================================================================ the examples
@pytest.mark.parametrize("goal, underground", [(1081, 1082), (1121, 1122)])
def test_from_central_plains_the_ride_from_its_underground_beats_walking(goal: int, underground: int) -> None:
    """1041 -> walk down to 1042 -> ride to the goal city's underground -> walk up to the goal."""
    legs, cost = costed(goal)
    walk_legs, walk_cost = costed(goal, transport=False)
    assert legs and cost is not None and walk_cost is not None and cost < walk_cost
    used = rides(legs)
    assert len(used) == 1 and used[0]["map"] == 1042 and used[0]["dest"] == underground
    assert maps_on_the_way(legs)[0] == CENTRAL and legs[0]["dest"] == 1042       # walking first
    assert legs[-1]["dest"] == goal                                                # then up on foot
    assert not rides(walk_legs)


def test_a_route_can_ride_twice_and_walk_between() -> None:
    """Moon City from Giant Rock: a cable car, a walk, then an airship - found, not written down."""
    legs, cost = costed(1101)
    _w, walk_cost = costed(1101, transport=False)
    kinds = [leg["kind"] for leg in rides(legs)]
    assert kinds == ["cable_car", "airship"]
    assert cost is not None and walk_cost is not None and cost < walk_cost / 2
    first, second = rides(legs)
    assert first["dest"] != second["map"]            # landed, then walked on to the airship's map


@pytest.mark.parametrize("start, goal", [(1081, 1101), (1101, 1081), (1081, 1161), (CENTRAL, 1061)])
def test_an_airship_is_taken_where_it_saves_walking(start: int, goal: int) -> None:
    legs, cost = costed(goal, start=start)
    _w, walk_cost = costed(goal, start=start, transport=False)
    assert [leg["kind"] for leg in rides(legs)] == ["airship"]
    assert cost is not None and walk_cost is not None and cost < walk_cost


@pytest.mark.parametrize("goal", [1042, 1501, 1601])
def test_no_ride_where_it_would_not_help(goal: int) -> None:
    legs, cost = costed(goal)
    _w, walk_cost = costed(goal, transport=False)
    assert rides(legs) == [] and cost == walk_cost


# ================================================================ when rides can't be taken
def test_with_transports_off_the_route_is_on_foot_as_before() -> None:
    legs, _c = costed(1101, transport=False)
    assert legs and rides(legs) == []


def test_a_fare_the_character_can_t_pay_is_left_out() -> None:
    poor, poor_cost = costed(1081, arnebia=500)                 # the cable car costs 2000
    rich, rich_cost = costed(1081, arnebia=5000)
    _w, walk_cost = costed(1081, transport=False)
    assert rides(poor) == [] and poor_cost == walk_cost
    assert rides(rich) and rich_cost is not None and walk_cost is not None and rich_cost < walk_cost


def test_vip_rides_are_free_whatever_is_in_the_bag() -> None:
    legs, _c = costed(1081, arnebia=0, vip=True)
    used = rides(legs)
    assert used and used[0]["choose"] == travel.transports()[used[0]["id"]]["choose_vip"]


def test_not_knowing_the_purse_leaves_every_ride_in() -> None:
    assert rides(costed(1081, arnebia=None)[0])
    assert travel.can_pay({"cost": 2000}, False, None)


def test_a_fare_the_data_doesn_t_know_is_not_counted_against_the_ride() -> None:
    t = {"cost": "unknown", "choose": ["go"]}
    assert travel.fare(t) is None and travel.can_pay(t, False, 0)
    assert travel.fare({"cost": 0}) == 0 and travel.can_pay({"cost": 0}, False, 0)
    assert travel.fare({"cost": True}) is None and travel.fare({"cost": -5}) is None


def test_a_ride_that_failed_is_avoided_and_the_next_best_way_taken() -> None:
    legs, cost = costed(1101)
    airship = rides(legs)[-1]
    other, other_cost = costed(1101, avoid={airship["id"]})
    assert airship["id"] not in [leg["id"] for leg in rides(other)]
    assert other is not None and other_cost is not None and cost is not None and other_cost > cost


def test_team_circles_are_only_for_a_team() -> None:
    circles = [t for t in travel.transports() if t.get("team")]
    assert circles
    alone = travel._transport_edges(set(), False, team=False)       # pyright: ignore[reportPrivateUsage]
    team = travel._transport_edges(set(), False, team=True)         # pyright: ignore[reportPrivateUsage]
    stands_alone = {n for out in alone.values() for _c, n, _l in out}
    stands_team = {n for out in team.values() for _c, n, _l in out}
    for t in circles:
        assert ("t", t["id"]) not in stands_alone and ("t", t["id"]) in stands_team


def test_a_map_reached_only_by_riding_has_no_way_on_foot() -> None:
    """Loki Union (1162): the Violet City airship lands there (from 1041, no walk reaches it)."""
    assert costed(1162, transport=False)[0] is None
    legs, _c = costed(1162)
    assert legs and rides(legs)
    assert costed(1162, arnebia=0)[0] is None                         # every way there is a paid ride


def test_an_unknown_map_is_unreachable() -> None:
    assert costed(999999) == (None, None)


# ================================================================ costs and optimality
def test_a_ride_costs_its_talk_and_ride_overhead_over_the_walks_either_side() -> None:
    edges = travel._transport_edges(set(), False)                   # pyright: ignore[reportPrivateUsage]
    for t in travel.transports():
        if not travel.usable(t) or t.get("team"):
            continue
        out = edges.get(("t", t["id"]), [])
        assert [(c, n) for c, n, _l in out] == [(travel.TALK[t["kind"]], ("l", t["id"]))]


def test_more_ways_never_cost_more_and_fewer_never_cost_less() -> None:
    random.seed(11)
    maps = sorted(travel.open_maps())
    for start, goal in itertools.islice(((a, b) for a in random.sample(maps, 12) for b in random.sample(maps, 6) if a != b), 60):
        port = travel._ports(start)[0]                                # pyright: ignore[reportPrivateUsage]
        cell = (port["x"], port["y"])
        _a, full = costed(goal, start, cell)
        _b, poor = costed(goal, start, cell, arnebia=0)
        _c, foot = costed(goal, start, cell, transport=False)
        values = [v if v is not None else float("inf") for v in (full, poor, foot)]
        assert values[0] <= values[1] <= values[2]


def test_the_search_finds_the_cheapest_path_even_round_a_loop() -> None:
    """A small graph checked against every simple path: a long walk, a ride shortcut, and a loop
    the search must not go round."""
    a: travel.Node = ("p", 1, 0)
    b: travel.Node = ("p", 1, 1)
    c: travel.Node = ("p", 2, 0)
    d: travel.Node = ("p", 3, 0)
    stand: travel.Node = ("t", 0)
    land: travel.Node = ("l", 0)
    ride: dict[str, Any] = {"do": "npc", "id": 0}
    graph: dict[travel.Node, list[travel.Edge]] = {
        a: [(10, b, None), (3, stand, None)], b: [(1, a, None), (50, c, {"do": "exit"})],
        stand: [(5, land, ride)], land: [(2, c, None), (4, stand, None)],     # back on board: a loop
        c: [(7, d, {"do": "exit"}), (1, land, None)], d: []}
    first: list[travel.Edge] = [(0, a, None)]

    def at_goal(node: travel.Node) -> float | None:
        return 0.0 if node == d else None
    found, best, prev = travel._search(first, lambda n: graph.get(n, []), {}, at_goal)   # pyright: ignore[reportPrivateUsage]
    assert found is not None and best[found] == 3 + 5 + 2 + 7

    def cheapest(node: travel.Node, seen: frozenset[travel.Node]) -> float:
        if node == d:
            return 0.0
        return min((cost + cheapest(n, seen | {n}) for cost, n, _l in graph.get(node, []) if n not in seen),
                   default=float("inf"))
    assert best[found] == cheapest(a, frozenset({a}))
    path: list[travel.Node] = []
    node = found
    while node in prev:
        node, _leg = prev[node]
        path.append(node)
    assert path.count(stand) == 1 and len(path) == len(set(path))          # boarded once, no loop


def test_no_route_boards_the_same_ride_twice() -> None:
    random.seed(5)
    maps = sorted(travel.open_maps())
    for goal in random.sample(maps, 25):
        legs, _c = costed(goal)
        ids = [leg["id"] for leg in rides(legs)]
        assert len(ids) == len(set(ids))


# ================================================================ broken transport data
def test_a_transport_row_missing_what_a_ride_needs_is_left_out(monkeypatch: pytest.MonkeyPatch) -> None:
    good = [dict(t) for t in travel.transports()]
    broken: list[dict[str, Any]] = [
        {"kind": "airship", "map": CENTRAL, "dest": 1101, "npc": "Airship Administrator", "choose": ["x"]},   # no landing
        {"kind": "airship", "map": 424242, "dest": 1101, "npc": "A", "choose": ["x"],
         "lands": {"map": 1101, "ports": {"0": 5}}},                                                       # unknown map
        {"kind": "rocket", "map": CENTRAL, "dest": 1101, "npc": "A", "choose": ["x"],
         "lands": {"map": 1101, "ports": {"0": 5}}},                                                       # unknown kind
        {"kind": "airship", "map": CENTRAL, "dest": 1101, "npc": "A", "choose": [],
         "lands": {"map": 1101, "ports": {"0": 5}}},                                                       # nothing to choose
        {"kind": "airship", "map": CENTRAL, "dest": 1101, "npc": "A", "choose": ["x"],
         "lands": {"map": 1101, "ports": "everywhere"}}]                                                   # bad landing ports
    rows = good + broken
    for i, t in enumerate(rows):
        t["id"] = i
    assert not any(travel.usable(t) for t in broken)
    monkeypatch.setattr(travel, "_transports", rows)
    travel._plan_cache.clear()                                         # pyright: ignore[reportPrivateUsage]
    legs, _c = costed(1101)
    assert legs and all(leg["id"] < len(good) for leg in rides(legs))


def test_a_broken_transport_file_means_walking(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    from unclefarmer import paths
    atlas.maps()                       # the map data stays the real one, read before the folder moves
    (tmp_path / "transports.json").write_text("{not json", encoding="utf-8")
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    monkeypatch.setattr(travel, "_transports", None)
    assert travel.transports() == []
    (tmp_path / "transports.json").write_text('{"transports": [7, "x", {"kind": "airship"}]}', encoding="utf-8")
    monkeypatch.setattr(travel, "_transports", None)
    assert [t["kind"] for t in travel.transports()] == ["airship"]
    legs, _c = costed(1081)
    assert legs and rides(legs) == []


# ================================================================ the purse
def test_the_purse_is_read_from_the_bag_and_unknown_without_an_arnebia_row() -> None:
    assert travel.arnebia_in([{"name": "Arnebia", "count": 1500}, {"name": "Wolf Fur", "count": 3}]) == 1500
    assert travel.arnebia_in([{"name": "Wolf Fur", "count": 3}]) is None
    assert travel.arnebia_in([]) is None and travel.arnebia_in(None) is None


class Traveller:
    """What TravelMixin._plan_legs reads: where we are, the bag, the profile."""

    def __init__(self, purse: int | None, vip: bool = False) -> None:
        self.p: dict[str, Any] = {"use_transport": True, "vip": vip}
        self.purse = purse
        self.vip_refused = False

    def me(self) -> dict[str, Any]:
        return {"x": SQUARE[0], "y": SQUARE[1]}

    def bag_rows(self) -> list[dict[str, Any]]:
        return [] if self.purse is None else [{"name": "Arnebia", "count": self.purse}]


@pytest.mark.parametrize("purse, vip, rides_it", [(500, False, False), (5000, False, True), (0, True, True),
                                                  (None, False, True)])
def test_the_bot_plans_only_rides_it_can_pay_for(purse: int | None, vip: bool, rides_it: bool) -> None:
    plan_legs: Any = TravelMixin._plan_legs        # pyright: ignore[reportPrivateUsage]
    legs: list[dict[str, Any]] = plan_legs(as_bot(Traveller(purse, vip)), CENTRAL, 1081, None, set())
    assert bool(rides(legs)) is rides_it


@pytest.mark.parametrize("purse, rides_it", [(5000, True), (500, False)])
def test_after_a_refused_free_ride_the_bot_plans_the_fare(purse: int, rides_it: bool) -> None:
    plan_legs: Any = TravelMixin._plan_legs        # pyright: ignore[reportPrivateUsage]
    who = Traveller(purse, vip=True)
    free = rides(plan_legs(as_bot(who), CENTRAL, 1081, None, set()))
    row = travel.transports()[int(free[0]["id"])]
    assert free[0]["vip"] is True and free[0]["choose"] == row["choose_vip"]
    who.vip_refused = True                         # what TravelMixin._vip_refused leaves behind
    paid = rides(plan_legs(as_bot(who), CENTRAL, 1081, None, set()))
    assert bool(paid) is rides_it                  # the same ride at its fare, if the bag has it
    if paid:
        assert paid[0]["id"] == free[0]["id"] and paid[0]["vip"] is False
        assert paid[0]["choose"] == row["choose"]
    assert who.p["vip"] is True


# ================================================================ "Use airships" off
def test_use_airships_off_leaves_every_airship_out() -> None:
    off = travel.kept_off({"use_airships": False})
    assert off == {t["id"] for t in travel.transports() if t["kind"] == "airship"} and off
    assert travel.kept_off({}) == set() and travel.kept_off({"use_airships": True}) == set()
    legs, _c = costed(1101, avoid=off)
    assert legs and "airship" not in [leg["kind"] for leg in rides(legs)]


class Shopper:
    """What town.nearest reads: where we are, the bag, the profile."""

    def __init__(self, airships: bool) -> None:
        self.p: dict[str, Any] = {"use_transport": True, "use_airships": airships}
        port = travel._ports(1081)[0]                                  # pyright: ignore[reportPrivateUsage]
        self.at = (float(port["x"]), float(port["y"]))

    def map_id(self) -> int:
        return 1081

    def me(self) -> dict[str, Any]:
        return {"x": self.at[0], "y": self.at[1]}

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": "Arnebia", "count": 100000}]


def test_the_nearest_place_is_judged_with_the_rides_the_bot_will_take() -> None:
    """From Tulip City an NPC in Moon City is nearest by airship, one a map away on foot is
    nearest without: with "Use airships" off, the airship mustn't decide where the bot goes."""
    from unclefarmer.engine import town
    candidates: list[tuple[int, str, float, float]] = []
    for mid in (1101, 1501):
        port = travel._ports(mid)[0]                                   # pyright: ignore[reportPrivateUsage]
        candidates.append((mid, "Someone", float(port["x"]), float(port["y"]) - 1))

    def cost(mid: int, x: float, y: float, airships: bool) -> float:
        here = Shopper(airships)
        got = travel.plan(1081, here.at, mid, (x, y + 1), with_cost=True, items=False,
                          avoid=travel.kept_off(here.p))[1]
        return float("inf") if got is None else got
    for airships in (True, False):
        want = min(candidates, key=lambda c: cost(c[0], c[2], c[3], airships))
        assert town.nearest(as_bot(Shopper(airships)), None, candidates) == want
    assert town.nearest(as_bot(Shopper(True)), None, candidates) != town.nearest(as_bot(Shopper(False)), None, candidates)


# ================================================================ the two ride switches
LOKI = 1162                             # Loki Union: an airship lands there, no walk reaches it
MARIO = 1021                            # Mario Basin: an airship from central plains, or a walk
AIRSHIPS: set[int] = {t["id"] for t in travel.transports() if t.get("kind") == "airship"}
OTHERS: set[int] = {t["id"] for t in travel.transports() if t.get("kind") != "airship"}


@pytest.mark.parametrize("use_transport, use_airships, rides_on, off", [
    (True, True, True, set[int]()),
    (False, True, True, OTHERS),          # cable cars and circles off: airships still in
    (True, False, True, AIRSHIPS),        # airships off: cable cars and circles still in
    (False, False, False, AIRSHIPS | OTHERS),
])
def test_each_switch_turns_off_only_its_own_rides(use_transport: bool, use_airships: bool,
                                                  rides_on: bool, off: set[int]) -> None:
    p = {"use_transport": use_transport, "use_airships": use_airships}
    assert travel.ride_rules(p) == (rides_on, off)
    assert travel.kept_off(p) == off
    assert AIRSHIPS and OTHERS


def plan_with(goal: int, **switches: bool) -> list[dict[str, Any]]:
    """_plan_legs from central plains' square, with the profile switches given."""
    t = Traveller(None)
    t.p.update(switches)
    plan_legs: Any = TravelMixin._plan_legs        # pyright: ignore[reportPrivateUsage]
    legs: list[dict[str, Any]] = plan_legs(as_bot(t), CENTRAL, goal, None, set())
    return legs


def only_airships(legs: list[dict[str, Any]]) -> bool:
    return bool(rides(legs)) and all(leg["kind"] == "airship" for leg in rides(legs))


def test_walking_first_walks_where_a_walk_leads() -> None:
    """"Walk whenever the maps connect on foot": Mario Basin is an airship from central plains,
    but a walk leads there too, so the route is on foot."""
    assert rides(costed(MARIO)[0])                       # the shortest way flies
    legs = plan_with(MARIO, prefer_walking=True, use_transport=True, use_airships=True)
    assert legs and not rides(legs)


def test_walking_first_flies_where_no_walk_leads() -> None:
    """No walk reaches Loki Union: walking first falls back to the full plan, airship included."""
    assert costed(LOKI, transport=False)[0] is None
    legs = plan_with(LOKI, prefer_walking=True, use_transport=True, use_airships=True)
    assert "airship" in [leg["kind"] for leg in rides(legs)]


@pytest.mark.parametrize("prefer_walking", [False, True])
def test_cable_cars_off_still_flies_by_airship(prefer_walking: bool) -> None:
    """Unticking "Use cable cars and teleport circles" used to plan with no rides at all."""
    assert only_airships(plan_with(LOKI, prefer_walking=prefer_walking, use_transport=False, use_airships=True))


def test_both_switches_off_means_no_ride() -> None:
    from unclefarmer.engine.botparts.base import Stopped
    with pytest.raises(Stopped):
        plan_with(LOKI, use_transport=False, use_airships=False)        # Loki Union: only by airship
    legs = plan_with(1081, use_transport=False, use_airships=False)
    assert legs and not rides(legs)
