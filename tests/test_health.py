# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The look-after step: which potion is chosen, when HP/MP is drunk or healed, resting, and the
panic / watch / out-of-potions stop rules. Own-HP key readings feed MP."""

from __future__ import annotations

from collections.abc import Generator

import contextlib
import queue

import pytest
from conftest import returning, stand_in, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import gamekeys, settings
from unclefarmer.engine.botparts import health
from unclefarmer.engine.botparts.base import Stopped
from unclefarmer.engine.botparts.health import HealthMixin
from typing import Any


class Link:
    def __init__(self) -> None:
        self.slots: dict[str, list[int]] = {}
        self.bagfull: list[dict[str, Any]] = []
        self.sent: list[str] = []

    def send(self, line: str) -> str:
        self.sent.append(line)
        return "ok"

    def json(self, line: str) -> Any:
        if line == "mem slots":
            return dict(self.slots)
        if line == "mem bagfull":
            return [dict(r) for r in self.bagfull]
        return {}


class H(HealthMixin):
    def __init__(self, profile: dict[str, Any] | None=None) -> None:
        self.link = Link()
        self.a = self.link
        self.p = dict(settings.PROFILE_DEFAULTS, **(profile or {}))
        self.lines: list[str] = []
        self.keys_pressed: list[str] = []
        self.sleeps: list[float] = []
        self.in_fight = False
        self.walk_only = False
        self._attackers = set()
        self.requests = queue.Queue()             # no chores asked for
        self.watches = []
        self.skillbook = None
        self._heals = False
        self.deaths = 0
        self._use_item: list[str] = []
        self._potions_stopped = False
        self._potions_had = {}
        self._potions_warned = set()
        self._potions_warned_out = set()
        self._potions_out = {}
        self.keys_to_fill = False
        self._told_potion = {}
        self.last_hp_key = 0.0
        self.last_mp_key = 0.0
        self.last_vitals_key = 0.0
        self.last_sheet = 0.0
        self.mp_share = None
        self.mp_mark = 80.0
        self.mp_added = 0.0
        self.mp_seen = 0.0
        self.mp_peak = 0
        self.maxmp = 0
        self.load_share = None
        self.low_since = None
        self.resting = False
        self.no_rest_until = 0.0
        self._me: dict[str, Any] = {}

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def check(self) -> None:
        pass

    def me(self) -> dict[str, Any]:
        return self._me

    def handle_death(self) -> None:  # pyright: ignore[reportIncompatibleMethodOverride]  (the real one never returns)
        pass

    def heal_with_skill(self, me: dict[str, Any], share: float, now: float) -> bool:
        return self._heals

    def support_between_fights(self, me: dict[str, Any], now: float) -> None:
        pass

    def attackers(self) -> set[int]:
        return set(self._attackers)

    def key(self, k: str, ms: int | None=None) -> None:
        self.keys_pressed.append(k)

    def use_item(self, name: str) -> bool:
        self._use_item.append(name)
        return True

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    @contextlib.contextmanager
    def timed(self, phase: str) -> Generator[None, None, None]:
        yield


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c)
    monkeypatch.setattr(health.repair, "check", stand_in(health.repair.check, returning(False)))
    monkeypatch.setattr(gamekeys, "slot_key_list", stand_in(gamekeys.slot_key_list, lambda: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="]))
    return c


def with_red_on_key(h: H, key: str="5") -> H:
    h.link.slots = {"2": [10, 1]}
    h.link.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10}]
    h.p["hp_key"] = "auto"
    h.p["hp_potion"] = "auto"
    return h


# ---------------------------------------------------------------- potion()
def test_potion_explicit_key_wins(clock: Clock) -> None:
    h = H({"hp_key": "5", "hp_potion": "Large Red Potion"})
    assert h.potion("hp") == ("5", "Large Red Potion")
    h.p["hp_potion"] = "auto"
    assert h.potion("hp") == ("5", None)


def test_potion_auto_finds_the_strongest_on_a_hotkey(clock: Clock) -> None:
    h = H()
    h.link.slots = {"1": [9, 90], "2": [10, 1]}
    h.link.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10},
                   {"uid": 90, "name": "Small Red Potion", "count": 5, "type": 9}]
    assert h.potion("hp") == ("2", "Large Red Potion")
    assert any("HP potion: Large Red Potion on key 2" in line for line in h.lines)


def test_potion_auto_falls_back_to_the_bag(clock: Clock) -> None:
    h = H()
    h.link.slots = {}
    h.link.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10}]
    assert h.potion("hp") == (None, "Large Red Potion")       # used from the bag


def test_potion_auto_nothing_anywhere(clock: Clock) -> None:
    h = H()
    h.link.slots = {}
    h.link.bagfull = []
    assert h.potion("hp") == (None, None)


def test_potion_pet_kind_falls_back_to_the_pet_potion_in_the_bag(clock: Clock) -> None:
    h = H()
    h.link.slots = {}
    h.link.bagfull = [{"uid": 1, "name": "Pet Large Red Potion", "count": 5, "type": 10}]
    assert h.potion("pet_hp") == (None, "Pet Large Red Potion")


def test_potion_explicit_name_off_hotkey_but_in_bag(clock: Clock) -> None:
    h = H({"hp_potion": "Large Red Potion"})
    h.link.slots = {}
    h.link.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10}]
    assert h.potion("hp") == (None, "Large Red Potion")
    h.link.bagfull = []
    clock.advance(61)                                         # the slot cache is re-read every minute
    assert h.potion("hp") == (None, None)


def test_hotkey_of_reads_the_slot_map(clock: Clock) -> None:
    h = with_red_on_key(h=H())
    assert h.hotkey_of("Large Red Potion") == "2"
    assert h.hotkey_of("Nope") is None


# ---------------------------------------------------------------- _read_potion_slots / keys lost
def test_read_slots_uses_the_item_type_when_the_uid_is_stale(clock: Clock) -> None:
    h = H()
    h.link.slots = {"2": [10, 999]}                             # uid gone, type still right
    h.link.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10}]
    assert h.potion("hp") == ("2", "Large Red Potion")


def test_read_slots_skips_empty_and_out_of_range_slots(clock: Clock) -> None:
    h = H()
    h.link.slots = {"2": [10, 1], "14": [11, 2], "3": [12, 3]}
    h.link.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10},
                   {"uid": 2, "name": "Large Blue Potion", "count": 0, "type": 11},
                   {"uid": 3, "name": "Large Blue Potion", "count": 5, "type": 12}]
    cache = h._read_potion_slots(None)
    assert cache[1] == {"hp": ("2", "Large Red Potion"), "mp": ("3", "Large Blue Potion")}


def test_potion_keys_lost_flags_a_refill(clock: Clock) -> None:
    h = H()
    h.potion_keys_lost({"hp": ("2", "Large Red Potion")}, {}, {"large red potion": 5})
    assert any("no longer on key 2" in line for line in h.lines)
    assert h.keys_to_fill is True


def test_potion_keys_lost_says_used_up(clock: Clock) -> None:
    h = H()
    h.potion_keys_lost({"hp": ("2", "Large Red Potion")}, {}, {})
    assert any("are used up" in line for line in h.lines)
    assert h.keys_to_fill is False                          # nothing in the bag to refill with


def test_potion_keys_lost_ignores_an_unchanged_kind(clock: Clock) -> None:
    h = H()
    h.potion_keys_lost({"hp": ("2", "Large Red Potion")}, {"hp": ("2", "Large Red Potion")}, {})
    assert h.lines == []


def test_read_slots_reports_a_lost_potion(clock: Clock) -> None:
    h = with_red_on_key(H())
    h._read_potion_slots(None)                               # builds the first cache
    h.link.slots = {}                                           # the game took the key
    h._read_potion_slots(h.potion_slots)
    assert any("no longer on key" in line or "used up" in line for line in h.lines)


# ---------------------------------------------------------------- strongest_in_bag / stock
def test_strongest_in_bag_is_the_biggest_red(clock: Clock) -> None:
    bag = {"small red potion": 5, "large red potion": 2, "super red potion": 1,
           "large blue potion": 9, "pet large red potion": 3}
    assert H.strongest_in_bag("hp", bag) == "Super Red Potion"
    assert H.strongest_in_bag("mp", bag) == "Large Blue Potion"
    assert H.strongest_in_bag("hp", {}) is None


def test_potion_stock_none_before_a_reading_and_sums_after(clock: Clock) -> None:
    h = H()
    assert h.potion_stock("hp") is None
    h.potion_slots = (0.0, {}, {}, {"small red potion": 3, "large red potion": 2, "large blue potion": 7})
    assert h.potion_stock("hp") == 5
    assert h.potion_stock("mp") == 7


# ---------------------------------------------------------------- watch_potions
def test_watch_potions_off_does_nothing(clock: Clock) -> None:
    h = H({"stop_when_out_of_potions": False})
    h.watch_potions(clock.now)


def test_watch_potions_warns_when_low(clock: Clock) -> None:
    h = H()
    h.potion_slots = (0.0, {}, {}, {"large red potion": 5})
    h.watch_potions(clock.now)
    assert any("only 5 red potions left" in line for line in h.lines)
    assert h._potions_had["hp"] is True
    n = len(h.lines)
    h.watch_potions(clock.now)
    assert len(h.lines) == n                                 # warned once


def test_watch_potions_ignores_mp_when_turned_off(clock: Clock) -> None:
    h = H({"mp_use": False})
    h.potion_slots = (0.0, {}, {}, {"large blue potion": 0})
    h._potions_had["mp"] = True
    h.watch_potions(clock.now)
    assert h._potions_stopped is False


def test_watch_potions_stop_after_reading_twice(clock: Clock) -> None:
    h = H()
    h.potion_slots = (0.0, {}, {}, {"large red potion": 0})
    h._potions_had["hp"] = True
    h.watch_potions(clock.now)                               # first sight: read the bag again
    assert h._potions_out["hp"] == clock.now
    assert h._potions_stopped is False
    clock.advance(4)
    with pytest.raises(Stopped):
        h.watch_potions(clock.now)
    assert h._potions_stopped is True


def test_watch_potions_leaves_restocked_potions_alone(clock: Clock) -> None:
    h = H({"restock": [{"item": "Large Red Potion"}]})
    h.potion_slots = (0.0, {}, {}, {"large red potion": 0})
    h._potions_had["hp"] = True
    h.watch_potions(clock.now)
    assert h._potions_stopped is False


def test_watch_potions_healer_only_warns(clock: Clock) -> None:
    h = H()
    h._heals = True
    h.potion_slots = (0.0, {}, {}, {"large red potion": 0})
    h._potions_had["hp"] = True
    h.watch_potions(clock.now)
    clock.advance(4)
    h.watch_potions(clock.now)
    assert h._potions_stopped is False
    assert any("healing with skills only" in line for line in h.lines)


def test_watch_potions_never_had_any_does_nothing(clock: Clock) -> None:
    h = H()
    h.potion_slots = (0.0, {}, {}, {"large red potion": 0})
    h.watch_potions(clock.now)
    assert h._potions_stopped is False and h._potions_out == {}


# ---------------------------------------------------------------- potion_plan
def test_potion_plan_auto_from_the_potion_text(clock: Clock) -> None:
    h = H({"hp_key": "5", "hp_potion": "Large Red Potion"})
    below, gap, _inf = h.potion_plan("hp", 100000)
    assert below == pytest.approx(100 - 100 * (0.15 * 100000 + 1500) / 100000)
    assert gap == pytest.approx(11.3)
    assert any("HP:" in line for line in h.lines)


def test_potion_plan_uses_explicit_numbers(clock: Clock) -> None:
    h = H({"hp_below": 70, "hp_cooldown": 4})
    below, gap, _inf = h.potion_plan("hp", 10000)
    assert (below, gap) == (70.0, 4.0)


def test_potion_plan_falls_back_for_an_unknown_potion(clock: Clock) -> None:
    h = H({"hp_below": "auto", "hp_cooldown": "auto", "hp_potion": "Mystery Potion"})
    below, gap, inf = h.potion_plan("hp", 10000)
    assert below == 80.0 and gap == 3.0 and inf is None


def test_potion_plan_logs_once_per_change(clock: Clock) -> None:
    h = H({"hp_below": 80, "hp_cooldown": 3, "hp_potion": "Large Red Potion"})
    h.potion_plan("hp", 10000)
    n = len(h.lines)
    h.potion_plan("hp", 10000)
    assert len(h.lines) == n


# ---------------------------------------------------------------- drink
def test_drink_presses_the_key(clock: Clock) -> None:
    h = H()
    assert h.drink("hp", "Large Red Potion", "5") is True
    assert h.keys_pressed == ["5"]


def test_drink_from_the_bag_between_fights(clock: Clock) -> None:
    h = H()
    assert h.drink("hp", "Large Red Potion", None) is True
    assert h._use_item == ["Large Red Potion"]


def test_drink_from_the_bag_in_a_fight_is_not_done(clock: Clock) -> None:
    h = H()
    h.in_fight = True
    assert h.drink("hp", "Large Red Potion", None) is False
    assert h._use_item == []


# ---------------------------------------------------------------- mp_now
def test_mp_now_from_a_fresh_vitals_reading(clock: Clock) -> None:
    h = H()
    pair = h.mp_now({"vitals": {"mp": 400, "age_ms": 1000}}, clock.now)
    assert pair is not None
    mp, top = pair
    assert (mp, top) == (400, 400)                           # no max seen: highest read stands in


def test_mp_now_prefers_the_sheet_max(clock: Clock) -> None:
    h = H()
    pair = h.mp_now({"sheet": {"maxmp": 1000, "mp": 400, "age": 0}}, clock.now)
    assert pair is not None
    mp, top = pair
    assert (mp, top) == (400, 1000)
    assert h.maxmp == 1000


def test_mp_now_none_without_a_reading(clock: Clock) -> None:
    h = H()
    assert h.mp_now({}, clock.now) is None
    assert h.mp_now({"vitals": {"mp": -1, "age_ms": 1}}, clock.now) is None


# ---------------------------------------------------------------- check_mp
def test_check_mp_presses_the_own_hp_key(clock: Clock) -> None:
    h = H({"vitals_key": "r"})
    h.mp_share = 30.0
    h.check_mp(clock.now)
    assert h.keys_pressed == ["r"]
    h.keys_pressed.clear()
    h.check_mp(clock.now)                                    # too soon
    assert h.keys_pressed == []


def test_check_mp_is_quiet_when_mp_is_high(clock: Clock) -> None:
    h = H({"vitals_key": "r"})
    h.mp_share = 95.0
    h.last_vitals_key = clock.now - 15
    h.check_mp(clock.now)                                    # every is stretched to 30
    assert h.keys_pressed == []


def test_check_mp_without_a_key_does_nothing(clock: Clock) -> None:
    h = H()
    h.check_mp(clock.now)
    assert h.keys_pressed == []


# ---------------------------------------------------------------- look_after
def low_red(h: H, hp: int=500, top: int=1000) -> H:
    h.p.update(hp_key="5", hp_potion="Large Red Potion", hp_below=80, hp_cooldown=3)
    h._me = {"hp": hp, "maxhp": top}
    return h


def test_look_after_drinks_an_hp_potion_below_the_mark(clock: Clock) -> None:
    h = low_red(H())
    h.look_after()
    assert h.keys_pressed == ["5"]
    assert h.last_hp_key == clock.now


def test_look_after_leaves_high_hp_alone(clock: Clock) -> None:
    h = low_red(H(), hp=950)
    h.look_after()
    assert h.keys_pressed == []


def test_look_after_drinks_an_mp_potion(clock: Clock) -> None:
    h = H({"mp_key": "6", "mp_potion": "Large Blue Potion", "mp_below": 80, "mp_cooldown": 3})
    h.maxmp = 1000                                           # a sheet read set the pool earlier
    h._me = {"hp": 900, "maxhp": 1000, "vitals": {"mp": 400, "age_ms": 500}}
    h.look_after()
    assert h.keys_pressed == ["6"]
    assert h.last_mp_key == clock.now


def test_look_after_healer_drinks_red_only_below_the_backup(clock: Clock) -> None:
    h = low_red(H(), hp=700)                                 # 70%
    h._heals = True
    h.p["heal_potion_below"] = 50
    h.look_after()
    assert h.keys_pressed == []                              # the heal skill covers 70%


def test_look_after_stops_on_panic(clock: Clock) -> None:
    h = low_red(H({"stop_on_panic": True, "panic_below": 25, "panic_after": 10}), hp=200)
    h.low_since = clock.now - 20
    with pytest.raises(Stopped):
        h.look_after()


def test_look_after_panic_resets_when_hp_recovers(clock: Clock) -> None:
    h = low_red(H({"stop_on_panic": True, "panic_below": 25, "panic_after": 10}), hp=900)
    h.low_since = clock.now - 100
    h.look_after()
    assert h.low_since is None


def test_look_after_watch_stops_the_run(clock: Clock) -> None:
    h = low_red(H(), hp=950)
    h.watches = [(lambda: True, "a stop rule fired")]
    with pytest.raises(Stopped):
        h.look_after()


# ---------------------------------------------------------------- resting
def test_rest_returns_false_when_hp_is_fine(clock: Clock) -> None:
    h = H({"rest_below": 60})
    assert h._rest_up({"hp": 900, "maxhp": 1000}) is False


def test_rest_returns_false_with_attackers(clock: Clock) -> None:
    h = H({"rest_below": 60})
    h._attackers = {-7}                              # a monster hitting us
    assert h._rest_up({"hp": 400, "maxhp": 1000}) is False


def test_rest_returns_false_just_after_a_useless_rest(clock: Clock) -> None:
    h = H({"rest_below": 60})
    h.no_rest_until = clock.now + 50
    assert h._rest_up({"hp": 400, "maxhp": 1000}) is False


def test_rest_waits_until_hp_comes_back(clock: Clock) -> None:
    h = H({"rest_below": 60, "rest_until": 90})
    h.sleep = lambda seconds: clock.advance(1)
    h._me = {"hp": 400, "maxhp": 1000}                       # 40%

    def look(me: dict[str, Any] | None = None) -> dict[str, Any]:
        h._me = {"hp": 950, "maxhp": 1000}                   # a potion brings it back
        return h._me

    h.look_after = look
    assert h._rest_up({"hp": 400, "maxhp": 1000}) is True


def test_rest_gives_up_when_hp_does_not_come_back(clock: Clock) -> None:
    h = H({"rest_below": 60, "rest_until": 90})
    h.sleep = lambda seconds: clock.advance(5)
    h._me = {"hp": 400, "maxhp": 1000}

    def look(me: dict[str, Any] | None = None) -> dict[str, Any]:
        return h._me                                        # stays at 40%

    h.look_after = look
    assert h._rest_up({"hp": 400, "maxhp": 1000}) is False
    assert h.no_rest_until > clock.now
    assert any("isn't coming back while resting" in line for line in h.lines)


def test_rest_up_sets_and_clears_the_resting_flag(clock: Clock) -> None:
    h = H({"rest_below": 60})
    seen: list[tuple[str, bool]] = []

    @contextlib.contextmanager
    def timed(phase: str) -> Generator[None, None, None]:
        seen.append((phase, h.resting))
        yield

    h.timed = timed
    h._rest_up = lambda me: False
    assert h.rest_up({"hp": 400}) is False
    assert seen == [("rest", True)]
    assert h.resting is False


def test_a_potion_off_the_hotkeys_is_drunk_in_a_fight_when_the_agent_uses_it_by_uid() -> None:
    from unclefarmer.engine.botparts.health import HealthMixin

    class Drinker(HealthMixin):
        def __init__(self) -> None:
            self.in_fight, self.agent_act = True, True
            self.used: list[str] = []

        def use_item(self, name: str) -> bool:
            self.used.append(name)
            return True

    b = Drinker()
    assert b.drink("hp", "Large Red Potion", None) is True
    assert b.used == ["Large Red Potion"]
    b.agent_act = False                    # the bag by arrow keys: never in a fight
    assert b.drink("hp", "Large Red Potion", None) is False
    b.in_fight = False
    assert b.drink("hp", "Large Red Potion", None) is True
