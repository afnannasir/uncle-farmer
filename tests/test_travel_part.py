# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""engine/botparts/travel.py: the Bot's traveling part - choosing and riding a transport,
paying/confirming its menu, using a travel item (teleport stone, hearthstone), leaving an instance
through the Action menu, crossing a map exit, and the walking assistant (go / go_dungeon).

This is the *Bot mixin*; tests/test_travel.py already covers the pure graph/plan builder in
unclefarmer/travel.py and is not repeated here. Everything is driven through a stand-in Bot that
carries only the few methods TravelMixin calls (log, map_id, me, walk_to, choose, dialog, ...),
with the slow/outside pieces monkeypatched: beasts.ensure, atlas.label/name, travel.plan.
"""

import types
import threading

import pytest
from conftest import Clock

from unclefarmer.clock import CLOCK
from unclefarmer import atlas, dungeons, knowledge
from unclefarmer import travel as travel_mod
from unclefarmer.engine import beasts
from unclefarmer.engine.botparts.base import CONFIRM, Stopped
from unclefarmer.engine.botparts.travel import TravelMixin


# ---------------------------------------------------------------- the pretend bot
def entry(text, cmd=0):
    return {"flags": 12, "options": [text], "cmd": [cmd]}


class Bot(TravelMixin):
    """Only the methods TravelMixin reaches for; every outside one is overridable per test."""

    def __init__(self, clock=None):
        self.clock = clock or Clock()
        self.p = {"hearthstone_item": "Hearthstone", "action_menu_key": "lctrl+enter", "target_mode": 2}
        self.lines = []
        self.keys = []
        self.sleeps = []
        self.walked = []
        self.held = []
        self.released = []
        self.used = []
        self.chosen = []
        self.notes = []
        self.target_modes = []
        self.saved = []
        self.closed = 0
        self._map = 1001
        self._me = {"x": 0.0, "y": 0.0, "hp": 100}
        self.settled = None
        self.entities = []
        self.stop_event = threading.Event()
        self.hit_value = False
        self.dialog_value = {"open": 0, "top": None}
        self.wait_dialog_value = None
        self.approach_value = None
        self.talk_value = True
        self.walk_result = "arrived"
        self.choose_ok = True
        self.use_item_result = True
        self.bag = []
        self.hotkeys = {}
        self.walk_only = False
        self.traveling = False
        self.save_profile = lambda p: self.saved.append(dict(p))

    def log(self, line, speak=False):
        self.lines.append(line)

    def map_id(self):
        return self._map

    def me(self):
        return dict(self._me)

    def snap(self):
        return {"map": {"id": self._map}, "self": self.me(), "entities": list(self.entities)}

    def sleep(self, seconds):
        self.sleeps.append(seconds)
        if self.clock:
            self.clock.advance(seconds)

    def check(self):
        pass

    def key(self, k, ms=None):
        self.keys.append(k)

    def hold(self, k):
        self.held.append(k)

    def let_go(self, k):
        self.released.append(k)

    def move_key(self, direction):
        return direction

    def hit(self, me):
        return self.hit_value

    def fight_back(self):
        self.lines.append("fight_back")

    def settled_map(self):
        return self.settled if self.settled is not None else self._map

    def walk_to(self, cell, near=None, map_change=False, **kw):
        self.walked.append(tuple(cell))
        return self.walk_result

    def use_item(self, item):
        self.used.append(item)
        return self.use_item_result

    def bag_rows(self):
        return self.bag

    def hotkey_of(self, name):
        return self.hotkeys.get(name)

    def set_target_mode(self, mode):
        self.target_modes.append(mode)

    def close_dialogs(self):
        self.closed += 1

    def dialog(self):
        return self.dialog_value

    def wait_dialog(self, seconds=3.0):
        return self.wait_dialog_value

    def choose(self, pattern):
        self.chosen.append(pattern)
        return {"options": ["x"]} if self.choose_ok else None

    def find_entry(self, top, pattern):
        return (0, 0)

    def note_dialog(self, d, why):
        self.notes.append(why)

    def approach(self, npc, at=None, stand=None):
        return self.approach_value

    def talk(self, npc, at=None, stand=None):
        return self.talk_value


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


@pytest.fixture(autouse=True)
def quiet(monkeypatch):
    """No mounting, and readable map names instead of loading the atlas."""
    monkeypatch.setattr(beasts, "ensure", lambda b, purpose: None)
    monkeypatch.setattr(atlas, "label", lambda mid: "map%d" % int(mid))
    monkeypatch.setattr(atlas, "name", lambda mid: "map%d" % int(mid))


# ---------------------------------------------------------------- has_hearthstone
def test_has_hearthstone_by_the_hotkey_setting():
    b = Bot()
    b.p["hearthstone_key"] = "h"
    assert b.has_hearthstone() is True


def test_has_hearthstone_by_a_bag_hotkey():
    b = Bot()
    b.hotkeys["Hearthstone"] = "h"
    assert b.has_hearthstone() is True


def test_has_hearthstone_in_the_bag_case_insensitively():
    b = Bot()
    b.bag = [{"name": "hearthstone", "count": 1}]
    assert b.has_hearthstone() is True
    b.bag = [{"name": "Hearthstone", "count": 0}]
    assert b.has_hearthstone() is False


def test_has_hearthstone_does_not_trim_the_bag_row():
    # OBSERVED: has_hearthstone() compares the raw, lower-cased name, so a padded row is missed;
    # travel.item_options() does strip its names. Pinned, not fixed.
    b = Bot()
    b.bag = [{"name": "  Hearthstone ", "count": 1}]
    assert b.has_hearthstone() is False


def test_has_hearthstone_swallows_an_unreadable_bag(allow_ignored_failures):
    b = Bot()

    def boom():
        raise RuntimeError("no bag")

    b.bag_rows = boom
    assert b.has_hearthstone() is False


# ---------------------------------------------------------------- learn_hearth_home
def test_learn_hearth_home_remembers_the_nearest_square():
    b = Bot()
    b._map, b._me = 1021, {"x": 73.0, "y": 286.0, "hp": 1}
    b.learn_hearth_home()
    assert b.p["hearth_home"] == "sunset square"
    assert b.saved and any("sunset square" in line for line in b.lines)


def test_learn_hearth_home_leaves_a_known_home_alone():
    b = Bot()
    b._map, b._me = 1021, {"x": 73.0, "y": 286.0, "hp": 1}
    b.p["hearth_home"] = "sunset square"
    b.learn_hearth_home()
    assert b.saved == [] and b.lines == []


def test_learn_hearth_home_needs_a_square_close_by():
    b = Bot()
    b._map, b._me = 1021, {"x": 0.0, "y": 0.0, "hp": 1}       # ~295 steps from sunset square
    b.learn_hearth_home()
    assert "hearth_home" not in b.p
    assert b.saved == []


def test_learn_hearth_home_on_a_map_with_no_square():
    b = Bot()
    b._map, b._me = 9999, {"x": 0.0, "y": 0.0, "hp": 1}
    b.learn_hearth_home()
    assert "hearth_home" not in b.p


# ---------------------------------------------------------------- spawns_at
def test_spawns_at_a_town_is_not_a_spawn_zone(monkeypatch):
    monkeypatch.setattr(knowledge, "cities", lambda: [{"map": 1001, "town_rect": [0, 0, 10, 10]}])
    monkeypatch.setattr(atlas, "zones", lambda mid: [{"kind": "monster"}])
    assert Bot.spawns_at(1001, (5, 5)) is False


def test_spawns_at_a_field_holds_monsters(monkeypatch):
    monkeypatch.setattr(knowledge, "cities", lambda: [])
    monkeypatch.setattr(atlas, "zones", lambda mid: [{"kind": "monster"}])
    assert Bot.spawns_at(1001, (5, 5)) is True


def test_spawns_at_a_quiet_map(monkeypatch):
    monkeypatch.setattr(knowledge, "cities", lambda: [])
    monkeypatch.setattr(atlas, "zones", lambda mid: [])
    assert Bot.spawns_at(1001, (5, 5)) is False


def test_spawns_at_ignores_a_town_on_another_map(monkeypatch):
    monkeypatch.setattr(knowledge, "cities", lambda: [{"map": 2, "town_rect": [0, 0, 10, 10]}])
    monkeypatch.setattr(atlas, "zones", lambda mid: [{"kind": "monster"}])
    assert Bot.spawns_at(1001, (5, 5)) is True


# ---------------------------------------------------------------- travel() wrapper
def test_travel_avoids_airships_when_switched_off(monkeypatch):
    b = Bot()
    b.p["use_airships"] = False
    monkeypatch.setattr(travel_mod, "transports",
                        lambda: [{"id": 0, "kind": "airship"}, {"id": 1, "kind": "cable_car"}])
    seen = {}

    def fake_travel(dst, cell, avoid):
        seen.update(dst=dst, cell=cell, avoid=set(avoid), traveling=b.traveling)
        raise RuntimeError("stop here")

    b._travel = fake_travel
    with pytest.raises(RuntimeError):
        b.travel("1041", (5, 6))
    assert seen["dst"] == 1041                # the map id is coerced to int
    assert seen["cell"] == (5, 6) and seen["avoid"] == {0} and seen["traveling"] is True
    assert b.traveling is False               # cleared even when a leg blows up


def test_travel_keeps_airships_by_default(monkeypatch):
    b = Bot()
    monkeypatch.setattr(travel_mod, "transports",
                        lambda: [{"id": 0, "kind": "airship"}])
    seen = {}
    b._travel = lambda dst, cell, avoid: seen.update(avoid=set(avoid))
    b.travel(1041)
    assert seen["avoid"] == set()


# ---------------------------------------------------------------- _travel: the leg loop
def plan_seq(legs):
    """A travel.plan that returns each list in turn, then []."""
    it = iter(legs)

    def fake(*a, **kw):
        try:
            return next(it)
        except StopIteration:
            return []

    return fake


def test_travel_returns_at_once_when_already_on_the_goal_with_no_cell(monkeypatch):
    b = Bot()
    b._map = 1041
    monkeypatch.setattr(travel_mod, "plan", lambda *a, **kw: pytest.fail("plan() should not run"))
    b._travel(1041, None, set())


def test_travel_walks_the_last_stretch_and_stops(monkeypatch):
    b = Bot()
    monkeypatch.setattr(travel_mod, "plan",
                        lambda *a, **kw: [{"do": "walk", "map": 1041, "cell": [7, 8]}])
    b._travel(1041, None, set())
    assert b.walked == [(7, 8)]


def test_travel_returns_on_an_empty_plan(monkeypatch):
    b = Bot()
    monkeypatch.setattr(travel_mod, "plan", lambda *a, **kw: [])
    b._travel(1041, None, set())
    assert b.walked == []


def test_travel_gives_up_when_there_is_no_way(monkeypatch):
    b = Bot()
    monkeypatch.setattr(travel_mod, "plan", lambda *a, **kw: None)
    with pytest.raises(Stopped) as e:
        b._travel(1041, None, set())
    assert "no way from map1001 to map1041" in str(e.value)


def test_travel_crosses_an_exit_then_replans(monkeypatch):
    b = Bot()
    crossed = []
    b.cross_exit = lambda leg: crossed.append(leg)
    monkeypatch.setattr(travel_mod, "plan", plan_seq([
        [{"do": "exit", "map": 1001, "exit": 0, "stand": [3, 3], "rect": [1, 1, 5, 5], "dest": 1041}]]))
    b._travel(1041, None, set())
    assert len(crossed) == 1 and crossed[0]["dest"] == 1041


def test_travel_leaves_out_a_transport_that_did_not_ride(monkeypatch):
    b = Bot()
    b.ride = lambda leg: False
    b.map_id = lambda: 1001
    seen = []
    leg = {"do": "npc", "map": 1001, "npc": "Cable Car manager", "kind": "cable_car", "dest": 2222, "id": 3,
           "choose": ["go"]}
    results = iter([[leg], [leg], []])

    def fake(*a, **kw):
        seen.append(set(kw.get("avoid", ())))
        return next(results)

    monkeypatch.setattr(travel_mod, "plan", fake)
    b._travel(1041, None, set())
    # tried twice (a walk the long way costs minutes), then left out
    assert seen == [set(), set(), {3}]
    assert any("trying the Cable Car manager once more" in line for line in b.lines)
    assert any("leaving out the Cable Car manager" in line for line in b.lines)


def test_travel_leaves_out_a_travel_item_that_failed(monkeypatch):
    b = Bot()
    b.teleport = lambda leg: False
    seen = []
    results = iter([[{"do": "item", "item": "Sunset City Teleportation stone", "choose": [],
                      "dest": 1021, "lands": [73, 286]}], []])
    monkeypatch.setattr(travel_mod, "plan",
                        lambda *a, **kw: (seen.append(set(kw.get("avoid", ()))), next(results))[1])
    b._travel(1041, None, set())
    assert seen[1] == {"Sunset City Teleportation stone"}


def test_travel_leaves_out_leaving_an_instance_that_failed(monkeypatch):
    b = Bot()
    b.leave_instance = lambda: False
    seen = []
    results = iter([[{"do": "leave", "map": 201}], []])
    monkeypatch.setattr(travel_mod, "plan",
                        lambda *a, **kw: (seen.append(set(kw.get("avoid", ()))), next(results))[1])
    b._travel(1041, None, set())
    assert seen[1] == {"leave"}


def test_travel_passes_over_the_bag_only_when_travel_items_are_on(monkeypatch):
    b = Bot()
    b.bag = [{"name": "Hearthstone", "count": 1}]
    seen = []
    monkeypatch.setattr(travel_mod, "plan",
                        lambda *a, **kw: (seen.append(kw.get("bag")), [])[1])
    b._travel(1041, None, set())
    assert seen == [[]]
    b.p["use_travel_items"] = True
    b._travel(1041, None, set())
    assert seen[-1] == b.bag


def test_travel_prefer_walking_plans_on_foot_and_without_items(monkeypatch):
    b = Bot()
    b.p["prefer_walking"] = True
    seen = {}
    monkeypatch.setattr(travel_mod, "plan",
                        lambda *a, **kw: (seen.update(kw), [])[1])
    b._travel(1041, None, set())
    assert seen["transport"] is False and seen["items"] is False and seen["bag"] == []


def test_travel_gives_up_after_forty_legs(monkeypatch):
    b = Bot()
    b.ride = lambda leg: True
    monkeypatch.setattr(travel_mod, "plan", lambda *a, **kw: [
        {"do": "npc", "map": 1001, "npc": "Manager", "kind": "menu", "dest": 2000,
         "id": 1, "choose": ["go"]},
        {"do": "walk", "map": 2000, "cell": [1, 1]}])
    with pytest.raises(Stopped) as e:
        b._travel(1041, None, set())
    assert "travel to map1041 didn't arrive" in str(e.value)


def test_travel_logs_a_multileg_route_once_per_new_route(monkeypatch):
    # `said` holds the legs still to come, so taking one leg (the map changes and plan() answers
    # the rest of the same route) must not print the route again. An older version of this test
    # used a plan() that never advanced - a bot that never moves re-plans the same route, which
    # is what printed the line forty times and looked like a fault in the source.
    b = Bot()
    legs = [{"do": "npc", "map": 1001, "npc": "Manager", "kind": "menu", "dest": 2000,
             "id": 1, "choose": ["go"]},
            {"do": "npc", "map": 2000, "npc": "Captain", "kind": "menu", "dest": 2001,
             "id": 2, "choose": ["go"]},
            {"do": "walk", "map": 2001, "cell": [1, 1]}]
    monkeypatch.setattr(travel_mod, "plan",
                        lambda mid, *a, **kw: {1001: legs, 2000: legs[1:], 2001: legs[2:]}.get(mid, []))

    def ride(leg):
        b._map = leg["dest"]           # the ride lands us on the next map
        return True

    b.ride = ride
    b._travel(2001, [1, 1], set())
    routes = [line for line in b.lines if line.startswith("route to ")]
    assert len(routes) == 1
    assert routes[0].count(" / ") == 2          # all three legs, once


# ---------------------------------------------------------------- wait_map_change
def test_wait_map_change_true_when_the_map_changes(clock):
    b = Bot(clock)
    b._map = 2
    assert b.wait_map_change(1, 5.0) is True
    assert 1.0 in b.sleeps


def test_wait_map_change_false_when_attacked(clock):
    b = Bot(clock)
    b._map = 1
    b.hit_value = True
    assert b.wait_map_change(1, 5.0) is False


def test_wait_map_change_times_out(clock):
    b = Bot(clock)
    b._map = 1
    assert b.wait_map_change(1, 0.5) is False
    assert b.sleeps


# ---------------------------------------------------------------- teleport
def test_teleport_uses_the_item_and_lands(monkeypatch):
    b = Bot()
    b._map = 1001

    def lands(mid, seconds):
        b._map = 1021
        return True

    b.wait_map_change = lands
    leg = {"do": "item", "item": "Sunset City Teleportation stone", "choose": [],
           "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is True
    assert b.used == ["Sunset City Teleportation stone"]
    assert b.closed == 0                        # the map changed: the dialog is left open
    assert any("using Sunset City Teleportation stone" in line for line in b.lines)


def test_teleport_feeds_the_pick_and_learns_home_on_a_hearthstone():
    b = Bot()
    b.p["hearthstone_item"] = "Hearthstone"
    b.wait_dialog_value = {"open": 1, "top": {}}
    b.p["hearthstone_key"] = "h"
    learned = []
    b.learn_hearth_home = lambda: learned.append(True)

    def lands(mid, seconds):
        b._map = 1021
        return True

    b.wait_map_change = lands
    leg = {"do": "item", "item": "Hearthstone", "choose": [], "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is True
    assert b.keys == ["h"] and b.used == []     # the hotkey beats the bag
    assert learned == [True]


def test_teleport_chooses_the_destination_from_a_menu():
    b = Bot()
    b.wait_dialog_value = {"open": 1, "top": {}}

    def lands(mid, seconds):
        b._map = 1021
        return True

    b.wait_map_change = lands
    leg = {"do": "item", "item": "Platinum Teleportation stone", "choose": ["sunset square"],
           "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is True
    assert b.chosen == ["sunset square"]


def test_teleport_gives_up_when_the_menu_never_opens():
    b = Bot()
    b.wait_dialog_value = None
    leg = {"do": "item", "item": "Platinum Teleportation stone", "choose": ["sunset square"],
           "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is False
    assert b.chosen == []
    assert b.closed == 1                        # the try/finally still closes the doomed menu


def test_teleport_gives_up_when_the_wrong_entry_is_there():
    b = Bot()
    b.wait_dialog_value = {"open": 1, "top": {}}
    b.choose_ok = False
    leg = {"do": "item", "item": "Platinum Teleportation stone", "choose": ["sunset square"],
           "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is False
    assert b.closed == 1                        # still on the map: the dialog is closed


def test_teleport_gives_up_when_the_item_wont_use():
    b = Bot()
    b.use_item_result = False
    leg = {"do": "item", "item": "Sunset City Teleportation stone", "choose": [],
           "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is False
    assert b.used == ["Sunset City Teleportation stone"] and b.closed == 0


def test_teleport_says_so_when_it_lands_nowhere():
    b = Bot()
    b.wait_map_change = lambda mid, seconds: False
    leg = {"do": "item", "item": "Sunset City Teleportation stone", "choose": [],
           "dest": 1021, "lands": [73, 286]}
    assert b.teleport(leg) is False
    assert any("didn't take me anywhere" in line for line in b.lines)
    assert b.closed == 1                        # still on the map: the dialog is closed


# ---------------------------------------------------------------- leave_instance
def test_leave_instance_presses_the_action_menu_and_leaves():
    b = Bot()
    b._map = 201
    b.wait_dialog_value = {"open": 1, "top": {"rows": [[entry("leave the dungeon")]]}}
    b.dialog_value = {"open": 0, "top": None}
    b.wait_map_change = lambda mid, seconds: setattr(b, "_map", 1041) or True
    assert b.leave_instance() is True
    assert b.keys == ["lctrl+enter"]
    assert b.closed == 1                        # once before opening the menu


def test_leave_instance_gives_up_when_no_menu_opens():
    b = Bot()
    b.wait_dialog_value = None
    assert b.leave_instance() is False
    assert b.closed == 1


def test_leave_instance_gives_up_when_there_is_no_leave_row():
    b = Bot()
    b.wait_dialog_value = {"open": 1, "top": {"rows": [[entry("something else")]]}}
    b.choose_ok = False
    assert b.leave_instance() is False
    assert b.closed == 1


def test_leave_instance_answers_the_are_you_sure_prompt():
    b = Bot()
    b._map = 201
    b.wait_dialog_value = {"open": 1, "top": {"rows": [[entry("leave the dungeon")]]}}
    dialogs = iter([{"open": 1, "top": {"rows": [[entry("yes")]]}}, {"open": 0, "top": None}])
    b.dialog = lambda: next(dialogs)
    b.wait_map_change = lambda mid, seconds: setattr(b, "_map", 1041) or True
    assert b.leave_instance() is True
    assert CONFIRM in b.chosen


def test_leave_instance_closes_the_dialog_when_it_did_not_leave():
    b = Bot()
    b.wait_dialog_value = {"open": 1, "top": {"rows": [[entry("leave")]]}}
    b.dialog_value = {"open": 0, "top": None}
    b.wait_map_change = lambda mid, seconds: False
    assert b.leave_instance() is False
    assert b.closed == 2                        # once before, once after the failure


def test_leave_instance_leaves_when_there_is_nothing_to_confirm():
    b = Bot()
    b._map = 201
    b.wait_dialog_value = {"open": 1, "top": {"rows": [[entry("leave the dungeon")]]}}
    b.dialog_value = {"open": 1, "top": {"rows": [[entry("share the loot?")]]}}
    b.find_entry = lambda top, pattern: None
    b.wait_map_change = lambda mid, seconds: setattr(b, "_map", 1041) or True
    assert b.leave_instance() is True


# ---------------------------------------------------------------- cross_exit
def test_cross_exit_walking_onto_it_is_enough(monkeypatch):
    b = Bot()
    b.walk_result = "map"
    b.cross_exit({"do": "exit", "map": 1001, "dest": 1041, "stand": [3, 3], "rect": [1, 1, 5, 5]})
    assert b.walked == [(3, 3)] and b.held == []
    assert b.sleeps == [1.0]


def test_cross_exit_pushes_off_the_edge_when_walking_short(monkeypatch):
    b = Bot()
    monkeypatch.setattr(atlas, "edge_direction", lambda mid, rect: "left")
    b.walk_result = "arrived"
    calls = {"n": 0}

    def map_id():
        calls["n"] += 1
        return 1001 if calls["n"] == 1 else 1041

    b.map_id = map_id
    b.cross_exit({"do": "exit", "map": 1001, "dest": 1041, "stand": [3, 3], "rect": [1, 1, 5, 5]})
    assert b.held == ["left"] and b.released == ["left"]
    assert b.sleeps == [1.0]


def test_cross_exit_sleeps_when_the_exit_is_inside_the_map_with_no_room_to_step(monkeypatch):
    b = Bot()
    monkeypatch.setattr(atlas, "edge_direction", lambda mid, rect: None)
    monkeypatch.setattr(atlas, "model", lambda mid: types.SimpleNamespace(ok=lambda x, y: False))
    b.walk_result = "arrived"
    b.cross_exit({"do": "exit", "map": 1001, "dest": 1041, "stand": [3, 3], "rect": [1, 1, 5, 5]})
    assert b.held == [] and b.sleeps == [2.0, 1.0]


def test_cross_exit_inside_the_map_steps_off_and_back_on(monkeypatch):
    # live 2026-10-23: the secluded valley airship lands on Loki Union's exit itself, and standing
    # there the bot re-planned every 4 s for 20 s
    b = Bot()
    monkeypatch.setattr(atlas, "edge_direction", lambda mid, rect: None)
    monkeypatch.setattr(atlas, "model", lambda mid: types.SimpleNamespace(ok=lambda x, y: (x, y) == (3, 4)))
    b.walk_result = "arrived"
    b.cross_exit({"do": "exit", "map": 1162, "dest": 1081, "stand": [3, 3], "rect": [3, 3, 3, 3]})
    assert b.walked == [(3, 3), (3, 4), (3, 3)]
    assert b.sleeps == [1.0]


def test_the_secluded_valley_airship_lands_on_loki_union():
    t = next(t for t in travel_mod.transports() if t["kind"] == "airship" and t["map"] == 1161)
    assert t["dest"] == 1162 and t["lands"]["map"] == 1162


# ---------------------------------------------------------------- go (walking assistant)
def test_go_to_a_cell_says_when_there_is_no_swift_beast(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    calls = []
    b.travel = lambda dst, cell=None: calls.append((dst, cell))
    b.go(1041, (5, 6))
    assert calls == [(1041, (5, 6))]
    assert b.walk_only is False
    assert any("no Swift fusion beast" in line for line in b.lines)
    assert any(line.startswith("arrived on ") for line in b.lines)


def test_go_to_an_npc_walks_then_talks(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [{"kind": "swift"}])
    b.approach_value = {"name": "Guard", "x": 4.0, "y": 4.0}
    approached = []
    b.approach = lambda npc, at=None, stand=None: approached.append((npc, at)) or b.approach_value
    talked = []
    b.talk = lambda npc, at=None, stand=None: talked.append((npc, at)) or True
    b.travel = lambda *a, **kw: None
    b.go(1041, (4, 5), npc="Guard", talk=True)
    assert approached == [("Guard", (4, 5))]
    assert talked == [("Guard", (4, 5))]


def test_go_gives_up_when_the_npc_cannot_be_reached(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    b.approach_value = None
    b.travel = lambda *a, **kw: None
    with pytest.raises(Stopped) as e:
        b.go(1041, (4, 5), npc="Guard")
    assert "couldn't get to Guard" in str(e.value)
    assert b.walk_only is False                 # restored in the finally


def test_go_gives_up_when_the_npc_will_not_talk(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    b.approach_value = {"name": "Guard"}
    b.talk_value = False
    b.travel = lambda *a, **kw: None
    with pytest.raises(Stopped) as e:
        b.go(1041, (4, 5), npc="Guard", talk=True)
    assert "couldn't talk to Guard" in str(e.value)


# ---------------------------------------------------------------- go_dungeon
class FakeModel:
    def ok(self, x, y):
        return True

    def nearest_walkable(self, x, y, r, comp=None):
        return (int(x), int(y))


def test_go_dungeon_to_a_known_entrance_is_just_go(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    calls = []
    b.go = lambda dst, cell=None, npc=None, talk=False: calls.append((dst, cell, npc, talk))
    b.go_dungeon({"name": "Cave", "map": 5, "npc": "Guard", "at": [3, 4]}, talk=True)
    assert calls == [(5, (3, 4), "Guard", True)]


def test_go_dungeon_finds_remembers_and_approaches_the_circle(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: FakeModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: True)
    learned = []
    monkeypatch.setattr(dungeons, "learn", lambda e, name, x, y: learned.append((name, x, y)))
    b.entities = [{"id": -1, "name": "Cave circle", "x": 10.2, "y": 10.9}]
    b.approach = lambda npc, at=None, stand=None: {"name": npc}
    b.talk = lambda npc, at=None, stand=None: True
    b.travel = lambda *a, **kw: None
    b.go_dungeon({"name": "Cave", "map": 5, "spots": [[10, 11]]}, talk=True)
    assert learned == [("Cave circle", 10.2, 10.9)]
    assert any("found it" in line for line in b.lines)


def test_go_dungeon_walks_each_marked_spot_until_the_circle_shows(monkeypatch):
    b = Bot()
    b._map = 5
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: FakeModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: True)
    monkeypatch.setattr(dungeons, "learn", lambda *a: None)
    walked = []

    def walk_to(cell, near=None, **kw):
        walked.append(tuple(cell))
        b.entities = [{"id": -1, "name": "Cave circle", "x": 30.0, "y": 30.0}]

    b.walk_to = walk_to
    b.approach = lambda npc, at=None, stand=None: {"name": npc}
    b.talk = lambda npc, at=None, stand=None: True
    b.travel = lambda *a, **kw: None
    b.go_dungeon({"name": "Cave", "map": 5, "spots": [[31, 31]]})
    assert walked == [(31, 31)]


def test_go_dungeon_gives_up_when_no_spot_is_the_circle(monkeypatch):
    b = Bot()
    b._map = 5
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: FakeModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: False)
    b.entities = []
    b.travel = lambda *a, **kw: None
    with pytest.raises(Stopped) as e:
        b.go_dungeon({"name": "Cave", "map": 5, "spots": [[31, 31]]})
    assert "none of the entrances marked on map5 is the Cave circle" in str(e.value)


def test_go_dungeon_stops_if_it_leaves_the_map_while_looking(monkeypatch):
    b = Bot()
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: FakeModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: False)
    b.entities = []
    b.travel = lambda *a, **kw: setattr(b, "_map", 999)
    with pytest.raises(Stopped) as e:
        b.go_dungeon({"name": "Cave", "map": 5, "spots": [[31, 31]]})
    assert "left map5 while looking for the Cave circle" in str(e.value)
    assert b.walk_only is False


def test_go_dungeon_skips_a_spot_it_cannot_even_reach(monkeypatch):
    b = Bot()
    b._map = 5
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: NoWalkModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: False)
    b.entities = []
    b.travel = lambda *a, **kw: None
    with pytest.raises(Stopped) as e:
        b.go_dungeon({"name": "Cave", "map": 5, "spots": [[31, 31]]})
    assert "none of the entrances marked on map5 is the Cave circle" in str(e.value)


class NoWalkModel:
    def ok(self, x, y):
        return False

    def nearest_walkable(self, x, y, r, comp=None):
        return None


def test_go_dungeon_gives_up_when_it_cannot_approach_the_circle(monkeypatch):
    b = Bot()
    b._map = 5
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: FakeModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: True)
    monkeypatch.setattr(dungeons, "learn", lambda *a: None)
    b.entities = [{"id": -1, "name": "Cave circle", "x": 1.0, "y": 1.0}]
    b.approach = lambda npc, at=None, stand=None: None
    b.travel = lambda *a, **kw: None
    with pytest.raises(Stopped) as e:
        b.go_dungeon({"name": "Cave", "map": 5, "spots": [[1, 1]]})
    assert "couldn't get to Cave circle" in str(e.value)


def test_go_dungeon_gives_up_when_the_circle_will_not_talk(monkeypatch):
    b = Bot()
    b._map = 5
    monkeypatch.setattr(beasts, "owned", lambda b: [])
    monkeypatch.setattr(atlas, "model", lambda mid: FakeModel())
    monkeypatch.setattr(dungeons, "is_entrance", lambda e, name: True)
    monkeypatch.setattr(dungeons, "learn", lambda *a: None)
    b.entities = [{"id": -1, "name": "Cave circle", "x": 1.0, "y": 1.0}]
    b.approach = lambda npc, at=None, stand=None: {"name": npc}
    b.talk = lambda npc, at=None, stand=None: False
    b.travel = lambda *a, **kw: None
    with pytest.raises(Stopped) as e:
        b.go_dungeon({"name": "Cave", "map": 5, "spots": [[1, 1]]}, talk=True)
    assert "couldn't talk to Cave circle" in str(e.value)


# ---------------------------------------------------------------- ride
def npc_leg(choose=("destination",), npc="Cable Car manager", kind="cable_car", dest=2000):
    return {"do": "npc", "map": 1001, "npc": npc, "kind": kind, "dest": dest, "id": 3,
            "choose": list(choose), "at": None, "stand": None}


def test_ride_talks_chooses_and_lands():
    b = Bot()
    b.talk_value = True
    state = {"changed": False}

    def map_id():
        return 2000 if state["changed"] else 1001

    b.map_id = map_id
    b.settled_map = lambda: 2000

    def choose(pattern):
        b.chosen.append(pattern)
        state["changed"] = True
        return {"options": ["x"]}

    b.choose = choose
    assert b.ride(npc_leg()) is True
    assert b.chosen == ["destination"]
    assert b.target_modes == [2]                # the target mode is restored afterwards
    assert any("now on map2000" in line for line in b.lines)


def test_ride_gives_up_when_the_npc_will_not_talk():
    b = Bot()
    b.talk_value = False
    assert b.ride(npc_leg()) is False
    assert b.closed == 1 and b.target_modes == [2]


def test_ride_gives_up_when_the_destination_row_is_missing():
    b = Bot()
    b.choose_ok = False
    assert b.ride(npc_leg()) is False
    assert b.chosen == ["destination"]


def test_ride_gives_up_when_the_next_menu_does_not_open():
    b = Bot()
    b.wait_dialog_value = None
    assert b.ride(npc_leg(choose=("first", "second"))) is False
    assert b.chosen == ["first"]
    assert any("next menu didn't open" in line for line in b.lines)


def test_ride_refuses_a_follow_up_that_is_not_a_confirmation():
    b = Bot()
    b.wait_dialog_value = {"open": 1, "top": {}}
    b.dialog_value = {"open": 1, "top": {"rows": [[entry("no chance")]]}}
    b.find_entry = lambda top, pattern: None
    assert b.ride(npc_leg()) is False
    assert b.notes and any("said no" in line for line in b.lines)
    assert b.closed == 2                        # _ride closes, then ride()'s finally closes again


def test_ride_accepts_a_confirm_prompt():
    b = Bot()
    b.wait_dialog_value = {"open": 1, "top": {}}
    dialogs = iter([{"open": 1, "top": {"rows": [[entry("yes")]]}}, {"open": 0, "top": None}])
    b.dialog = lambda: next(dialogs)
    state = {"changed": False}

    def map_id():
        return 2000 if state["changed"] else 1001

    b.map_id = map_id
    b.settled_map = lambda: 2000

    def choose(pattern):
        b.chosen.append(pattern)
        if pattern == CONFIRM:                  # only the confirm moves us on
            state["changed"] = True
        return {"options": ["x"]}

    b.choose = choose
    assert b.ride(npc_leg()) is True
    assert CONFIRM in b.chosen


def test_ride_waits_for_the_cable_car_to_carry_us(clock, monkeypatch):
    b = Bot(clock)
    b.settled_map = lambda: 2000
    state = {"changed": False}

    def map_id():
        return 2000 if state["changed"] else 1001

    b.map_id = map_id

    def choose(pattern):
        state["changed"] = True
        return {"options": ["x"]}

    b.choose = choose
    b.wait_map_change = lambda mid, seconds: True
    calls = {"n": 0}

    def name(mid):
        calls["n"] += 1
        return "Cable Car Station" if calls["n"] == 1 else "map%d" % int(mid)

    monkeypatch.setattr(atlas, "name", name)
    assert b.ride(npc_leg()) is True
    assert any("riding - waiting to arrive" in line for line in b.lines)
    assert any(line.startswith("arrived on ") for line in b.lines)


def test_ride_gives_up_when_the_transport_never_moves_us(clock):
    b = Bot(clock)
    b.dialog_value = {"open": 0, "top": None}
    assert b.ride(npc_leg()) is False
    assert any("didn't take me anywhere" in line for line in b.lines)
    assert 0.3 in b.sleeps


def test_ride_waits_out_a_reload_that_leaves_us_on_the_same_map():
    b = Bot()
    state = {"changed": False}

    def map_id():
        return 2000 if state["changed"] else 1001

    b.map_id = map_id
    settled = {"n": 0}

    def settled_map():                          # first reading: still the old map, then the new one
        settled["n"] += 1
        return 1001 if settled["n"] == 1 else 2000

    b.settled_map = settled_map

    def choose(pattern):
        state["changed"] = True
        return {"options": ["x"]}

    b.choose = choose
    assert b.ride(npc_leg()) is True
    assert settled["n"] == 2


# ---------------------------------------------------------------- go_home
def test_go_home_uses_the_key_and_learns_the_square(clock):
    b = Bot(clock)
    b.p["hearthstone_key"] = "h"
    calls = {"n": 0}

    def map_id():
        calls["n"] += 1
        return 1 if calls["n"] == 1 else 2

    b.map_id = map_id
    b.settled = 999
    learned = []
    b.learn_hearth_home = lambda: learned.append(True)
    assert b.go_home() is True
    assert b.keys == ["h"] and learned == [True]
    assert any("home: map2" in line for line in b.lines)


def test_go_home_falls_back_to_the_bag(clock):
    b = Bot(clock)
    calls = {"n": 0}

    def map_id():
        calls["n"] += 1
        return 1 if calls["n"] == 1 else 2

    b.map_id = map_id
    b.settled = 999
    assert b.go_home() is True
    assert b.used == ["Hearthstone"]


def test_go_home_gives_up_when_the_item_will_not_use(clock):
    b = Bot(clock)
    b.use_item_result = False
    assert b.go_home() is False
    assert b.keys == []


def test_go_home_fights_back_when_hit_and_gives_up(clock):
    b = Bot(clock)
    b.hit_value = True
    b.map_id = lambda: 1
    assert b.go_home() is False
    assert b.lines.count("fight_back") == 3
    assert any("didn't take me home" in line for line in b.lines)


def test_go_home_ignores_a_reload_on_the_same_square(clock):
    b = Bot(clock)
    b.p["hearthstone_key"] = "h"
    calls = {"n": 0}

    def map_id():
        calls["n"] += 1
        if calls["n"] == 1:
            return 1
        if calls["n"] <= 3:
            return 2                            # changed for a moment...
        return 1                                # ...then back to start

    b.map_id = map_id
    b.settled = 1                               # settled back on the starting map, same square
    assert b.go_home(tries=1) is False
    assert any("didn't take me home" in line for line in b.lines)
