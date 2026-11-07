# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""Leaving a gang base, choosing the nearest NPC from where we stand (this city first), the Shard
Markets of every city, and the Tools actions "Sell shards now" and "Clean the bag now".

The travel tests use the real map data (atlas, knowledge, shards.json): which city is "nearest" is a
question about that data, so a stand-in planner would prove nothing.
"""

from __future__ import annotations

import threading
from collections.abc import Iterable
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import as_bot, failing, returning, stand_in, Clock

from unclefarmer import knowledge, settings, travel
from unclefarmer.engine import crafting, script as S, town
from unclefarmer.engine.botparts.base import Stopped
from unclefarmer.engine.botparts.travel import TravelMixin, note_way_out, ways_out

GANG = 4004                       # a gang base: every one has the same layout (4001-4006)
OUTER = (59, 93)                  # beside the Gem Craftsman, in the outer hall
INNER = (40, 40)                  # the inner hall, walled off from the exit
CENTRAL, MARIO, HOLY = 1041, 1021, 1121
ENTRANCE_1041 = (253.0, 181.0)    # just south of central plains' Gang Entrance

# The Action menu as the game showed it inside a gang base (dialogs.log): no "leave" entry at all.
GANG_ACTION_MENU: dict[str, Any] = {"open": 1, "top": {"rows": [
    [{"flags": 4194304, "options": ["Action menu; lag 255"], "cmd": [0]}],
    [{"flags": 12, "options": ["my mission: 1"], "cmd": [12, 1]}],
    [{"flags": 140, "raw": "Please enter a team name", "text": "Please enter a team name",
      "options": ["Create a team"], "cmd": [1, 0]}],
    [{"flags": 12, "options": ["Some gang"], "cmd": [3, 3, 4321]},
     {"flags": 14, "f": "Do you want to quit the Some gang?", "options": ["quit"], "cmd": [3, 8, 1234]}],
    [{"flags": 12, "options": ["friends"], "cmd": [32, 6]}],
    [{"flags": 12, "options": ["Enter the mall"], "cmd": [14, 13]},
     {"flags": 12, "options": ["recharge"], "cmd": [14, 11]}]]}}
LEAVE_PATTERN = r"^\s*leave\s|return to|open the treasure chest and return"


# ================================================================ the gang base's way out
def test_the_logged_gang_action_menu_has_no_leave_entry() -> None:
    import re
    rows: list[list[dict[str, Any]]] = GANG_ACTION_MENU["top"]["rows"]
    texts: list[str] = []
    for row in rows:
        for e in row:
            texts += [str(t) for t in [e.get("text"), e.get("f"), *e.get("options", [])] if t]
    assert texts and not any(re.search(LEAVE_PATTERN, t, re.I) for t in texts)


@pytest.mark.parametrize("mid", [4001, 4002, 4003, 4004, 4005, 4006])
def test_every_gang_base_has_its_exit_on_the_outer_hall_s_south_edge(mid: int) -> None:
    found = travel.gang_exit(mid)
    assert found is not None
    _i, e = found
    assert e["dest"] is None and e["stand"] == [40, 118]


@pytest.mark.parametrize("mid", [1001, 1041, 201, 202])
def test_other_maps_have_no_gang_exit(mid: int) -> None:
    assert travel.gang_exit(mid) is None


def test_from_the_outer_hall_travel_walks_out_instead_of_the_action_menu() -> None:
    legs = travel.plan(GANG, OUTER, CENTRAL, (252, 187))
    assert legs is not None
    assert legs[0]["do"] == "exit" and legs[0]["dest"] is None and legs[0]["stand"] == [40, 118]
    assert travel.describe(legs[:1]) == ["%s: walk out of the gang base" % travel.atlas.label(GANG)]


def test_from_the_inner_hall_there_is_no_way_on_foot() -> None:
    assert travel.plan(GANG, INNER, CENTRAL, (252, 187)) is None
    assert travel.plan(GANG, INNER, CENTRAL, (252, 187), with_cost=True) == (None, None)


def test_a_dungeon_still_leaves_through_the_action_menu() -> None:
    legs = travel.plan(201, (10, 10), CENTRAL, (252, 187))
    assert legs == [{"do": "leave", "map": 201}]


class TravelBot(TravelMixin):
    """Just enough of the Bot for leave_instance / leave_here / _take_leg_noting on a gang base."""

    def __init__(self, mid: int = GANG, at: tuple[float, float] = OUTER) -> None:
        self._map, self._me = mid, {"x": at[0], "y": at[1], "hp": 100}
        self.p: dict[str, Any] = {"action_menu_key": "lctrl+enter"}
        self.lines: list[str] = []
        self.keys: list[str] = []
        self.crossed: list[dict[str, Any]] = []
        self.lands_on: tuple[int, tuple[float, float]] | None = (CENTRAL, ENTRANCE_1041)
        self.stop_event = threading.Event()

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def map_id(self) -> int:
        return self._map

    def me(self) -> dict[str, Any]:
        return dict(self._me)

    def key(self, k: str, ms: int | None = None) -> None:
        self.keys.append(k)

    def close_dialogs(self) -> None:
        pass

    def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None:
        return GANG_ACTION_MENU

    def choose(self, pattern: str) -> dict[str, Any] | None:
        return None                      # nothing in that menu matches a "leave" pattern

    def cross_exit(self, leg: dict[str, Any]) -> None:
        self.crossed.append(leg)
        if self.lands_on:
            self._map = self.lands_on[0]
            self._me = {"x": self.lands_on[1][0], "y": self.lands_on[1][1], "hp": 100}


def test_leave_instance_in_a_gang_base_walks_out_and_never_opens_the_action_menu() -> None:
    b = TravelBot()
    assert b.leave_instance() is True
    assert b.keys == []                                   # no Action menu
    assert b.crossed and b.crossed[0]["dest"] is None
    assert b.map_id() == CENTRAL
    assert ways_out(b)[GANG] == (CENTRAL, ENTRANCE_1041)  # where the way out led is learned


def test_leave_instance_in_the_inner_hall_says_why_and_presses_nothing() -> None:
    b = TravelBot(at=INNER)
    assert b.leave_instance() is False
    assert b.keys == [] and b.crossed == []
    assert any("walled off" in line for line in b.lines)


def test_travel_from_the_inner_hall_stops_with_a_clear_reason() -> None:
    b = TravelBot(at=INNER)
    with pytest.raises(Stopped, match="walled off"):
        b._plan_legs(GANG, CENTRAL, (252, 187), set())


def test_a_gang_exit_that_keeps_failing_is_left_out() -> None:
    b = TravelBot()
    b.lands_on = None                                     # the exit doesn't take us anywhere
    avoid: set[Any] = set()
    failed: dict[Any, int] = {}
    legs = travel.plan(GANG, OUTER, CENTRAL, (252, 187))
    assert legs is not None
    leg = legs[0]
    b._take_leg(leg, avoid, failed)
    assert "gang_exit" not in avoid
    b._take_leg(leg, avoid, failed)
    assert "gang_exit" in avoid
    assert travel.plan(GANG, OUTER, CENTRAL, (252, 187), avoid=avoid) is None


# ================================================================ the nearest NPC, this city first
class PlaceBot:
    """A character standing somewhere: map_id, me, the profile, travel/talk recorded."""

    def __init__(self, mid: int, at: tuple[float, float]) -> None:
        self.map, self.at = mid, at
        self.p: dict[str, Any] = dict(settings.PROFILE_DEFAULTS)
        self.lines: list[str] = []
        self.travels: list[int] = []
        self.talks: list[str] = []
        self.left = 0
        self.leave_to: tuple[int, tuple[float, float]] | None = None
        self.stop_event = threading.Event()

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def map_id(self) -> int:
        return self.map

    def me(self) -> dict[str, Any]:
        return {"x": self.at[0], "y": self.at[1]}

    def bag_rows(self) -> list[dict[str, Any]]:
        return []

    def travel(self, mid: int, cell: tuple[float, float] | None = None) -> None:
        self.travels.append(mid)
        self.map = mid

    def talk(self, npc: str, at: tuple[float, float] | None = None) -> bool:
        self.talks.append(npc)
        return True

    def close_dialogs(self) -> None:
        pass

    def leave_here(self) -> bool:
        self.left += 1
        if self.leave_to is None:
            return False
        note_way_out(self, self.map, *self.leave_to)
        self.map, self.at = self.leave_to
        return True


def first_map(npc: town.Npc | None) -> int:
    assert npc is not None
    return npc[0]


def city_craftsmen() -> list[town.Npc]:
    return [n for n in knowledge.service_npcs("gem_craftsman") if crafting.atlas.kind(n[0]) != "gang"]


@pytest.mark.parametrize("city", [1021, 1041, 1061, 1081, 1101, 1121])
def test_the_gem_craftsman_of_the_city_we_stand_in_is_chosen(city: int) -> None:
    here = next(n for n in city_craftsmen() if n[0] == city)
    b = PlaceBot(city, (here[2], here[3] + 3))
    assert first_map(town.nearest(as_bot(b), None, city_craftsmen())) == city


def test_out_of_town_the_nearest_city_s_craftsman_is_chosen() -> None:
    b = PlaceBot(1122, (60, 60))           # a field map next to Holy Blood City
    got = town.nearest(as_bot(b), None, city_craftsmen())
    assert got is not None
    costs = {n: travel.plan(1122, (60, 60), n[0], (n[2], n[3] + 1), with_cost=True)[1] for n in city_craftsmen()}
    reachable = {n: c for n, c in costs.items() if c is not None}
    assert got == min(reachable, key=lambda n: reachable[n])


def test_inside_a_gang_base_the_nearest_is_judged_from_the_entrance_we_came_in_by() -> None:
    b = PlaceBot(GANG, OUTER)
    # Before: every city cost the same unknown "leave", and the data's first (Mario Basin) won.
    assert first_map(town.nearest(as_bot(b), None, city_craftsmen())) == MARIO
    note_way_out(b, GANG, CENTRAL, ENTRANCE_1041)
    assert first_map(town.nearest(as_bot(b), None, city_craftsmen())) == CENTRAL


def test_visit_from_a_gang_base_with_no_known_way_out_steps_out_first() -> None:
    b = PlaceBot(GANG, OUTER)
    b.leave_to = (CENTRAL, ENTRANCE_1041)
    npc = town.visit(as_bot(b), "storehouse")
    assert b.left == 1
    assert b.travels == [CENTRAL]
    assert npc == next(n[1] for n in knowledge.service_npcs("storehouse") if n[0] == CENTRAL)


def test_visit_from_a_dungeon_leaves_it_to_travel() -> None:
    b = PlaceBot(201, (10, 10))
    b.leave_to = (CENTRAL, ENTRANCE_1041)
    town.visit(as_bot(b), "storehouse")
    assert b.left == 0 and len(b.travels) == 1


def test_visit_on_the_same_map_doesnt_step_out() -> None:
    b = PlaceBot(GANG, OUTER)
    b.leave_to = (CENTRAL, ENTRANCE_1041)
    town.visit(as_bot(b), None, candidates=[(GANG, "Gem Craftsman", 59, 93)])
    assert b.left == 0 and b.talks == ["Gem Craftsman"]


def test_the_city_fallback_of_the_gem_maker_stays_in_the_entrance_city() -> None:
    b = PlaceBot(GANG, INNER)
    note_way_out(b, GANG, CENTRAL, ENTRANCE_1041)
    assert crafting.open_craftsman(as_bot(b), "gem_craftsman", "city") == "city"
    assert b.travels == [CENTRAL]


def test_a_visit_with_no_reachable_candidate_says_so() -> None:
    b = PlaceBot(GANG, INNER)
    with pytest.raises(Stopped, match="can be reached"):
        town.visit(as_bot(b), None, candidates=[(9999, "Nobody", 1, 1)])


# ================================================================ every city's Shard Market
def test_every_city_with_a_shard_broker_is_a_market() -> None:
    _s, markets = town.shard_catalog()
    maps = {m for m, *_rest in town.market_candidates(markets)}
    for mid in (MARIO, CENTRAL, 1061, 1081, 1101, HOLY, 1141, 1161):
        assert mid in maps
    assert len(maps) == len(town.market_candidates(markets))   # one per city
    assert not any(crafting.atlas.kind(m) == "gang" for m in maps)


def test_an_npc_beside_a_blocked_cell_on_this_map_is_still_reachable() -> None:
    # central plains' Shard Broker stands with a counter south of it: the cell the planner aims at
    # (x, y+1) isn't walkable. From another map that was always fine; on its own map it was "no way".
    assert not travel.atlas.model(CENTRAL).ok(245, 213)
    legs, cost = travel.plan(CENTRAL, (240, 220), CENTRAL, (245, 213), with_cost=True)
    assert legs is not None and cost is not None and cost < 30


@pytest.mark.parametrize("city", [MARIO, HOLY, CENTRAL, 1081])
def test_shards_are_sold_in_the_city_we_stand_in(city: int) -> None:
    _s, markets = town.shard_catalog()
    cands = town.market_candidates(markets)
    here = next(n for n in cands if n[0] == city)
    b = PlaceBot(city, (here[2], here[3] + 3))
    assert first_map(town.nearest(as_bot(b), None, cands)) == city


def test_shards_are_sold_at_the_nearest_market_when_this_map_has_none() -> None:
    _s, markets = town.shard_catalog()
    cands = town.market_candidates(markets)
    b = PlaceBot(1122, (60, 60))
    got = town.nearest(as_bot(b), None, cands)
    costs = {n: travel.plan(1122, (60, 60), n[0], (n[2], n[3] + 1), with_cost=True)[1] for n in cands}
    reachable = {n: c for n, c in costs.items() if c is not None}
    assert got == min(reachable, key=lambda n: reachable[n])


# ================================================================ Tools: sell shards / clean the bag
class BagBot:
    """A bag, the profile's bag rules, and the throw key."""

    def __init__(self, bag: dict[str, int], rules: dict[str, dict[str, Any]]) -> None:
        self.bag = dict(bag)
        self.p: dict[str, Any] = dict(settings.PROFILE_DEFAULTS, bag_rules=rules)
        self.lines: list[str] = []
        self.spoken: list[str] = []
        self.thrown: list[tuple[str, int]] = []
        self.refuse: set[str] = set()
        self.stop_event = threading.Event()
        self.clock = Clock()

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": k, "count": v} for k, v in self.bag.items() if v]

    def throw_item(self, name: str, n: int) -> int:
        if name in self.refuse:
            return 0
        self.thrown.append((name, n))
        self.bag[name] -= n
        return n


RUBY1, TOPAZ1, RUBY2 = "Level 1 Ruby Shard", "Level 1 Topaz Shard", "Level 2 Ruby Shard"


def test_sell_shards_now_sells_only_the_shards_set_to_sell_keeping_what_the_rule_keeps(
        monkeypatch: pytest.MonkeyPatch) -> None:
    bot = BagBot({RUBY1: 50, TOPAZ1: 30, "Large Red Potion": 100, "Wolf Fur": 9},
                 {RUBY1.lower(): {"action": "sell", "keep": 10}, TOPAZ1.lower(): {"action": "keep", "keep": 0},
                  "wolf fur": {"action": "sell", "keep": 0}})
    asked: list[tuple[list[str], dict[str, int] | None]] = []

    def sell(b: town.Bot, names: Iterable[str], at_most: dict[str, int] | None = None) -> int:
        names = list(names)
        asked.append((names, at_most))
        for n in names:
            bot.bag[n] -= (at_most or {})[n.lower()]
        return len(names)
    monkeypatch.setattr(town, "sell_shards", stand_in(town.sell_shards, sell))
    assert town.sell_shards_now(as_bot(bot)) == 1
    assert asked == [([RUBY1], {RUBY1.lower(): 40})]       # not the Topaz (keep), not the fur (no shard)
    assert bot.bag[TOPAZ1] == 30 and bot.bag["Wolf Fur"] == 9
    assert any("sold 40 Level 1 Ruby Shard" in s for s in bot.spoken)
    assert not any("arnebia came in" in s for s in bot.spoken)


def test_sell_shards_now_with_nothing_to_sell_says_so_and_goes_nowhere(monkeypatch: pytest.MonkeyPatch) -> None:
    b = BagBot({TOPAZ1: 30}, {TOPAZ1.lower(): {"action": "keep", "keep": 0}})
    monkeypatch.setattr(town, "sell_shards", failing("went to sell"))
    assert town.sell_shards_now(as_bot(b)) == 0
    assert any("no shards to sell" in s for s in b.spoken)


def test_sell_shards_now_skips_a_shard_the_market_doesnt_buy(monkeypatch: pytest.MonkeyPatch) -> None:
    b = BagBot({RUBY2: 8}, {RUBY2.lower(): {"action": "sell", "keep": 0}})
    monkeypatch.setattr(town, "sell_shards", failing("went to sell"))
    assert town.sell_shards_now(as_bot(b)) == 0
    assert any("doesn't buy it" in line for line in b.lines)


def test_sell_shards_now_says_what_didnt_sell(monkeypatch: pytest.MonkeyPatch) -> None:
    b = BagBot({RUBY1: 5}, {RUBY1.lower(): {"action": "sell", "keep": 0}})
    monkeypatch.setattr(town, "sell_shards", returning(0))
    assert town.sell_shards_now(as_bot(b)) == 0
    assert any("not sold: Level 1 Ruby Shard" in s for s in b.spoken)


def test_clean_bag_throws_only_what_is_on_the_throw_away_list() -> None:
    b = BagBot({RUBY1: 5, "Wolf Fur": 3, TOPAZ1: 4, "Large Red Potion": 100},
               {RUBY1.lower(): {"action": "throw", "keep": 2}, "wolf fur": {"action": "throw", "keep": 0},
                TOPAZ1.lower(): {"action": "sell", "keep": 0}, "old boot": {"action": "throw", "keep": 0}})
    assert town.clean_bag(as_bot(b)) == 2
    assert sorted(b.thrown) == [(RUBY1, 3), ("Wolf Fur", 3)]
    assert b.bag[TOPAZ1] == 4 and b.bag["Large Red Potion"] == 100 and b.bag[RUBY1] == 2
    assert any("threw away" in s for s in b.spoken)


def test_clean_bag_with_an_empty_bag_throws_nothing() -> None:
    b = BagBot({}, {RUBY1.lower(): {"action": "throw", "keep": 0}})
    assert town.clean_bag(as_bot(b)) == 0
    assert b.thrown == [] and any("bag is empty" in s for s in b.spoken)


def test_clean_bag_with_an_empty_list_throws_nothing() -> None:
    b = BagBot({RUBY1: 5}, {RUBY1.lower(): {"action": "sell", "keep": 0}})
    assert town.clean_bag(as_bot(b)) == 0
    assert b.thrown == [] and any("throw-away list is empty" in s for s in b.spoken)


def test_clean_bag_when_nothing_listed_is_in_the_bag() -> None:
    b = BagBot({TOPAZ1: 5}, {RUBY1.lower(): {"action": "throw", "keep": 0}})
    assert town.clean_bag(as_bot(b)) == 0
    assert b.thrown == [] and any("nothing in the bag is on the throw-away list" in s for s in b.spoken)


def test_clean_bag_says_what_the_game_wouldnt_throw() -> None:
    b = BagBot({RUBY1: 5, "Wolf Fur": 3},
               {RUBY1.lower(): {"action": "throw", "keep": 0}, "wolf fur": {"action": "throw", "keep": 0}})
    b.refuse = {"Wolf Fur"}
    assert town.clean_bag(as_bot(b)) == 1
    assert b.bag["Wolf Fur"] == 3
    assert any("couldn't throw away: Wolf Fur" in s for s in b.spoken)


def test_clean_bag_stops_when_asked() -> None:
    b = BagBot({RUBY1: 5}, {RUBY1.lower(): {"action": "throw", "keep": 0}})
    b.stop_event.set()
    with pytest.raises(Stopped):
        town.clean_bag(as_bot(b))
    assert b.thrown == []


def test_throw_list_is_the_player_s_throw_rules() -> None:
    p = {"bag_rules": {"b": {"action": "throw", "keep": 3}, "a": {"action": "throw"}, "c": {"action": "sell"}}}
    assert town.throw_list(p) == [("a", 0), ("b", 3)]
    assert town.throw_list({}) == []


# ================================================================ the script commands behind the menu
def test_sell_shards_and_clean_bag_are_script_commands(monkeypatch: pytest.MonkeyPatch) -> None:
    main, _subs, _stop = S.parse("sell_shards\nclean_bag\n")
    assert [n.kind for n in main] == ["sell_shards", "clean_bag"]
    called: list[str] = []
    monkeypatch.setattr(town, "sell_shards_now", stand_in(town.sell_shards_now, lambda b: called.append("sell") or 0))
    monkeypatch.setattr(town, "clean_bag", stand_in(town.clean_bag, lambda b: called.append("clean") or 0))
    run = S.Runner.__new__(S.Runner)
    fake = as_bot(SimpleNamespace())
    run._do_sell_shards(main[0], fake, {})
    run._do_clean_bag(main[1], fake, {})
    assert called == ["sell", "clean"]


# ================================================================ the Tools menu
wx = pytest.importorskip("wx")


class Seen:
    """What the Tools menu did: errands started, questions asked, messages shown."""

    def __init__(self) -> None:
        self.errands: list[str] = []
        self.asked: list[str] = []
        self.told: list[str] = []
        self.said: list[str] = []
        self.win: Any = None


def tools_window(monkeypatch: pytest.MonkeyPatch, rules: dict[str, dict[str, Any]], answer: bool,
                 busy: bool = False) -> Seen:
    from unclefarmer.gui import main
    from unclefarmer.gui.mainparts import editing
    seen = Seen()

    def ask(parent: object, msg: str, title: str | None = None) -> bool:
        seen.asked.append(msg)
        return answer

    def tell(parent: object, msg: str, title: str | None = None) -> None:
        seen.told.append(msg)
    monkeypatch.setattr(editing, "character_of", returning("Hero"))
    monkeypatch.setattr(editing.settings, "load_profile", returning({"bag_rules": rules}))
    monkeypatch.setattr(editing, "ask", ask)
    monkeypatch.setattr(editing, "info", tell)
    monkeypatch.setattr(editing, "error", tell)

    class Win:
        runs = {1: object()} if busy else {}

        def __getattr__(self, name: str) -> Any:
            attr = getattr(main.MainFrame, name)
            return attr.__get__(self) if callable(attr) else attr

        def snapshot(self) -> dict[str, Any]:
            return {}

        def selected_pid(self) -> int:
            return 1

        def say(self, text: str) -> None:
            seen.said.append(text)

        def run_errand(self, text: str, title: str, walk: bool = False) -> None:
            seen.errands.append(text)

        def log(self, line: str, speak: bool = False, who: str | None = None, private: bool = False) -> None:
            seen.said.append(line)
    seen.win = Win()
    return seen


def test_tools_sell_shards_now_runs_the_sell_shards_job(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = tools_window(monkeypatch, {}, True)
    seen.win.sell_shards_now()
    assert seen.errands == ["sell_shards\n"]
    assert seen.said == ["Sell shards now: started by hand"]


def test_tools_clean_bag_asks_first_showing_the_list(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = tools_window(monkeypatch, {"wolf fur": {"action": "throw", "keep": 2}, "x": {"action": "sell"}}, True)
    seen.win.clean_bag_now()
    assert seen.errands == ["clean_bag\n"] and seen.said == ["Clean the bag now: started by hand"]
    assert "wolf fur, keeping 2" in seen.asked[0] and "x" not in seen.asked[0].split("\n")


def test_tools_clean_bag_does_nothing_when_the_player_says_no(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = tools_window(monkeypatch, {"wolf fur": {"action": "throw", "keep": 0}}, False)
    seen.win.clean_bag_now()
    assert seen.errands == []


def test_tools_clean_bag_with_an_empty_list_explains_and_runs_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = tools_window(monkeypatch, {"x": {"action": "sell"}}, True)
    seen.win.clean_bag_now()
    assert seen.errands == [] and seen.asked == [] and "empty" in seen.told[0]


def test_tools_clean_bag_while_the_bot_runs_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    seen = tools_window(monkeypatch, {"wolf fur": {"action": "throw", "keep": 0}}, True, busy=True)
    seen.win.clean_bag_now()
    assert seen.errands == [] and seen.asked == [] and seen.said == ["Stop the bot first."]
