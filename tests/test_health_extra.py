# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""The branches of the look-after step tests/test_health.py doesn't reach: death and respawn,
the class's own heal, support between fights, the character sheet, and the potion-watch edges.

Kept in its own file so the existing test_health.py stand-in (which stubs handle_death,
heal_with_skill and support_between_fights) can stay as it is; here the mixin's own methods run.
"""

from typing import Any
import contextlib
import re

import pytest
from conftest import Clock

from unclefarmer.clock import CLOCK
from unclefarmer import gamekeys, settings
from unclefarmer.engine.botparts import health
from unclefarmer.engine.botparts.base import CONFIRM, RESPAWN, Respawned, Stopped
from unclefarmer.engine.botparts.health import HealthMixin


class Link:
    def __init__(self):
        self.slots = {}
        self.bagfull = []

    def json(self, line):
        if line == "mem slots":
            return dict(self.slots)
        if line == "mem bagfull":
            return [dict(r) for r in self.bagfull]
        return {}


class Empty:
    def empty(self):
        return True


class NonEmpty:
    def empty(self):
        return False


class FakeBook:
    """Just enough of combat.SkillBook for the health mixin's calls."""

    def __init__(self, ok=True, heal=None, support=None, emergency=None):
        self.ok = ok
        self._heal = heal
        self.support_result = support
        self.emergency_result = emergency
        self.read_calls = []

    def healer(self):
        return self._heal

    def read_casts(self, me_id):
        self.read_calls.append(me_id)

    def support(self, me, now, fight=None):
        return self.support_result

    def emergency(self, me, now):
        return self.emergency_result


class H(HealthMixin):
    """A stand-in that does NOT stub the methods under test, so their real bodies run."""

    def __init__(self, profile=None):
        self.a = Link()
        self.p = dict(settings.PROFILE_DEFAULTS, **(profile or {}))
        self.lines = []
        self.keys_pressed = []
        self.sleeps = []
        self.in_fight = False
        self.walk_only = False
        self.approaching = False
        self.modes_done = False
        self._attackers = set()
        self.requests = Empty()
        self.watches = []
        self.skillbook = None
        self._heals = False
        self.deaths = 0
        self._use_item = []
        self._potions_stopped = False
        self._potions_had = {}
        self._potions_warned = set()
        self._potions_warned_out = set()
        self._potions_out = {}
        self._keys_to_fill = False
        self._told_potion = {}
        self._support_at = 0.0
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
        self._me = {}
        self._dlg = {"open": 0, "top": None}
        self.chosen = []
        self.let_go_calls = 0
        self.run_requests_calls = 0
        self.close_dialogs_calls = 0

    def log(self, line, speak=False):
        self.lines.append(line)

    def check(self):
        pass

    def me(self):
        return self._me

    def attackers(self):
        return set(self._attackers)

    def key(self, k, ms=None):
        self.keys_pressed.append(k)

    def use_item(self, name):
        self._use_item.append(name)
        return True

    def sleep(self, s):
        self.sleeps.append(s)

    def map_id(self):
        return 5302

    def let_go(self, k=None):
        self.let_go_calls += 1

    def dialog(self):
        return self._dlg

    @staticmethod
    def entry_texts(e):
        out = [e.get("text"), e.get("raw")] + [e.get(k) for k in ("f", "g", "h", "i")]
        return [t for t in out + list(e.get("options") or []) if t]

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        if not top:
            return None
        for r, row in enumerate(top["rows"]):
            for c, e in enumerate(row):
                if e and any(re.search(pattern, t, re.I) for t in self.entry_texts(e)):
                    return r, c
        return None

    def choose(self, pattern: str) -> dict[str, Any] | None:
        self.chosen.append(pattern)
        return {}

    def close_dialogs(self):
        self.close_dialogs_calls += 1

    def run_requests(self):
        self.run_requests_calls += 1

    @contextlib.contextmanager
    def timed(self, phase):
        yield


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c)
    monkeypatch.setattr("unclefarmer.engine.botparts.health.repair.check", lambda b: False)
    monkeypatch.setattr(gamekeys, "slot_key_list",
                        lambda: ["1", "2", "3", "4", "5", "6", "7", "8", "9", "0", "-", "="])
    return c


# ---------------------------------------------------------------- potion() branch 43
def test_potion_explicit_name_found_on_a_hotkey(clock):
    h = H({"hp_potion": "Large Red Potion"})
    h.a.slots = {"2": [10, 1]}
    h.a.bagfull = [{"uid": 1, "name": "Large Red Potion", "count": 5, "type": 10}]
    assert h.potion("hp") == ("2", "Large Red Potion")


# ---------------------------------------------------------------- _read_potion_slots branch 84
def test_read_slots_keeps_a_non_potion_item_by_name_only(clock):
    h = H()
    h.a.slots = {"1": [5, 1]}
    h.a.bagfull = [{"uid": 1, "name": "Toolbox", "count": 1, "type": 5}]
    h._read_potion_slots(None)
    assert h.hotkey_of("Toolbox") == "1"          # looked up for toolboxes too
    assert h.potion("hp") == (None, None)         # but never read as a potion


# ---------------------------------------------------------------- watch_potions branch 167
def test_watch_potions_waits_before_stopping(clock):
    h = H()
    h._potion_keys = (0.0, {}, {}, {"large red potion": 0})
    h._potions_had["hp"] = True
    h.watch_potions(clock.now)                    # first sight: reads the bag again
    assert h._potions_out["hp"] == clock.now
    h.watch_potions(clock.now)                    # less than 3 s later: keep waiting
    assert h._potions_stopped is False


# ---------------------------------------------------------------- heal_with_skill
def test_heal_with_skill_off_switches(clock):
    h = H({"heal_with_skill": False})
    assert h.heal_with_skill({}, 10, clock.now) is False
    h = H({"smart_skills": False})
    assert h.heal_with_skill({}, 10, clock.now) is False
    h = H()
    h.walk_only = True
    assert h.heal_with_skill({}, 10, clock.now) is False


def test_heal_with_skill_high_hp_reuses_the_flag(clock):
    h = H()
    assert h.heal_with_skill({}, 90, clock.now) is False     # share >= heal_below, not resting
    h._heals = True
    assert h.heal_with_skill({}, 90, clock.now) is True      # remembered from a fight


def test_heal_with_skill_low_hp_builds_the_book(clock, monkeypatch):
    monkeypatch.setattr(health.combat, "SkillBook", lambda bot: FakeBook(ok=True, heal={"name": "Heal"}))
    h = H()
    assert h.heal_with_skill({}, 10, clock.now) is True
    assert h._heals is True
    assert any("heals with Heal" in line for line in h.lines)


def test_heal_with_skill_without_a_heal(clock):
    h = H()
    h.skillbook = FakeBook(ok=True, heal=None)
    assert h.heal_with_skill({}, 10, clock.now) is False
    h.skillbook = FakeBook(ok=False, heal={"name": "Heal"})
    assert h.heal_with_skill({}, 10, clock.now) is False


def test_heal_with_skill_logs_only_once(clock):
    h = H()
    h._heals = True
    h.skillbook = FakeBook(ok=True, heal={"name": "Heal"})
    n = len(h.lines)
    assert h.heal_with_skill({}, 10, clock.now) is True
    assert len(h.lines) == n


# ---------------------------------------------------------------- support_between_fights
def test_support_between_fights_off(clock):
    h = H()
    h.walk_only = True
    h.support_between_fights({}, clock.now)
    assert h.keys_pressed == []
    h = H({"smart_skills": False})
    h.support_between_fights({}, clock.now)
    assert h.keys_pressed == []


def test_support_between_fights_waits_inside_a_fight(clock):
    h = H()
    h.in_fight = True
    h.support_between_fights({}, clock.now)
    assert h.keys_pressed == [] and h.skillbook is None


def test_support_between_fights_throttles(clock):
    h = H()
    h._support_at = clock.now
    h.support_between_fights({}, clock.now)
    assert h.keys_pressed == []


def test_support_between_fights_high_hp_reads_nothing_early(clock):
    h = H()
    h.support_between_fights({"hp": 90, "maxhp": 100}, clock.now)
    assert h.skillbook is None


def test_support_between_fights_low_hp_builds_the_book(clock, monkeypatch):
    made = []
    monkeypatch.setattr(health.combat, "SkillBook", lambda bot: made.append(bot) or FakeBook(ok=False))
    h = H()
    h.support_between_fights({}, clock.now)              # hp 0 < heal_below
    assert made == [h]


def test_support_between_fights_presses_what_support_returns(clock):
    h = H()
    h.skillbook = FakeBook(ok=True, support=("7", "Heal"))
    h.support_between_fights({"id": ME}, clock.now)
    assert h.keys_pressed == ["7"]
    assert h.skillbook.read_calls == [ME]


def test_support_between_fights_does_not_press_into_a_dialog(clock):
    h = H()
    h.skillbook = FakeBook(ok=True, support=("7", "Heal"))
    h._dlg = {"open": 1, "top": None}
    h.support_between_fights({}, clock.now)
    assert h.keys_pressed == []


def test_support_between_fights_uses_emergency_while_approaching(clock):
    h = H()
    h.in_fight = True
    h.approaching = True
    h.skillbook = FakeBook(ok=True, emergency=("8", "Formless"))
    h.support_between_fights({}, clock.now)
    assert h.keys_pressed == ["8"]


ME = 1001


# ---------------------------------------------------------------- handle_death
def test_handle_death_respawns_on_the_map(clock, monkeypatch):
    monkeypatch.setattr(health.atlas, "label", lambda m: "Field")
    h = H({"respawn": "map"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "Map Respawn"}]]}}
    h._me = {"hp": 100}
    with pytest.raises(Respawned):
        h.handle_death()
    assert h.deaths == 1
    assert h.let_go_calls == 1
    assert any("died on Field" in line for line in h.lines)
    assert h.chosen == [RESPAWN["map"]]


def test_handle_death_answers_the_confirmation(clock, monkeypatch):
    monkeypatch.setattr(health.atlas, "label", lambda m: "Field")
    h = H({"respawn": "map"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "Map Respawn"}, {"text": "OK"}]]}}
    h._me = {"hp": 100}
    with pytest.raises(Respawned):
        h.handle_death()
    assert h.chosen[0] == RESPAWN["map"]
    assert CONFIRM in h.chosen                            # "Are you sure ...?"


def test_handle_death_too_many_deaths_stops_in_the_city(clock, monkeypatch):
    monkeypatch.setattr(health.atlas, "label", lambda m: "Field")
    h = H({"max_deaths": 3, "stop_on_deaths": True})
    h.deaths = 3
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "back to the City"}]]}}
    h._me = {"hp": 100}
    with pytest.raises(Stopped) as e:
        h.handle_death()
    assert "died 4 times" in str(e.value)
    assert h.chosen[0] == RESPAWN["city"]


def test_handle_death_menu_never_opens_stops(clock):
    h = H()
    h._dlg = {"open": 0, "top": None}
    with pytest.raises(Stopped) as e:
        h.handle_death()
    assert "respawn menu never opened" in str(e.value)


def test_handle_death_could_not_respawn_stops(clock):
    h = H({"respawn": "map"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "Please select your respawn method"}]]}}
    h._me = {"hp": 0}
    with pytest.raises(Stopped) as e:
        h.handle_death()
    assert "couldn't respawn" in str(e.value)


def test_handle_death_uses_the_resurrection_crystal_first(clock, monkeypatch):
    monkeypatch.setattr(health.atlas, "label", lambda m: "Field")
    h = H({"use_resurrection_crystal": True, "respawn": "map"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "Please select your respawn method"}],
                                           [{"text": "Respawn On-Spot with Resurrection Crystal"}]]}}
    h._me = {"hp": 100}
    with pytest.raises(Respawned):
        h.handle_death()
    assert h.chosen[0] == RESPAWN["crystal_spot"]


def test_handle_death_choice_that_fails_is_skipped(clock):
    h = H({"respawn": "map"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "Map Respawn"}]]}}
    h.choose = lambda pattern: None
    with pytest.raises(Stopped) as e:
        h.handle_death()
    assert "couldn't respawn" in str(e.value)


def test_handle_death_waits_while_still_dead(clock):
    h = H({"respawn": "map"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "Map Respawn"}]]}}
    h._me = {"hp": 0}
    with pytest.raises(Stopped) as e:
        h.handle_death()
    assert "couldn't respawn" in str(e.value)
    assert h.sleeps.count(0.5) == 40


def test_handle_death_respawn_in_city_setting(clock, monkeypatch):
    monkeypatch.setattr(health.atlas, "label", lambda m: "Field")
    h = H({"respawn": "city"})
    h._dlg = {"open": 1, "top": {"rows": [[{"text": "back to the City"}]]}}
    h._me = {"hp": 100}
    with pytest.raises(Respawned):
        h.handle_death()
    assert h.chosen[0] == RESPAWN["city"]


# ---------------------------------------------------------------- refresh_sheet
def test_refresh_sheet_without_a_key(clock):
    h = H({"sheet_key": ""})
    h._me = {"sheet": {"age": 0}}
    assert h.refresh_sheet() == {"age": 0}
    assert h.keys_pressed == []


def test_refresh_sheet_too_soon(clock):
    h = H({"sheet_key": "`"})
    h.last_sheet = clock.now - 5
    h._me = {"sheet": {"age": 0}}
    assert h.refresh_sheet() == {"age": 0}
    assert h.keys_pressed == []


def test_refresh_sheet_forced_reads_and_closes(clock):
    h = H({"sheet_key": "`"})
    h._me = {"sheet": {"age": 0}}
    assert h.refresh_sheet(force=True) == {"age": 0}
    assert h.keys_pressed == ["`"]
    assert h.close_dialogs_calls == 1


def test_refresh_sheet_sheet_never_becomes_fresh(clock):
    h = H({"sheet_key": "`"})
    h._me = {}
    assert h.refresh_sheet() is None
    assert h.close_dialogs_calls == 1


def test_refresh_sheet_reads_sooner_when_the_bag_is_full(clock):
    h = H({"sheet_key": "`", "sheet_every": 600, "bag_full_at": 100})
    h.load_share = 99
    h.last_sheet = clock.now - 31
    h._me = {"sheet": {"age": 0}}
    h.refresh_sheet()
    assert h.keys_pressed == ["`"]


def test_refresh_sheet_reads_sooner_near_full(clock):
    h = H({"sheet_key": "`", "sheet_every": 600, "bag_full_at": 100})
    h.load_share = 97
    h.last_sheet = clock.now - 61
    h._me = {"sheet": {"age": 0}}
    h.refresh_sheet()
    assert h.keys_pressed == ["`"]


def test_refresh_sheet_reads_sooner_when_getting_full(clock):
    h = H({"sheet_key": "`", "sheet_every": 600, "bag_full_at": 100})
    h.load_share = 90
    h.last_sheet = clock.now - 181
    h._me = {"sheet": {"age": 0}}
    h.refresh_sheet()
    assert h.keys_pressed == ["`"]


def test_refresh_sheet_reads_sooner_for_mp_without_a_vitals_key(clock):
    h = H({"sheet_key": "`", "sheet_every": 600, "vitals_key": "", "mp_use": True})
    h.mp_share = 10.0
    h.mp_mark = 80.0
    h.last_sheet = clock.now - 16
    h._me = {"sheet": {"age": 0}}
    h.refresh_sheet()
    assert h.keys_pressed == ["`"]


# ---------------------------------------------------------------- mp_now branch 371
def test_mp_now_none_when_no_pool_is_known(clock):
    h = H()
    # a reading of 0 MP gives no peak and no max: nothing to measure against
    assert h.mp_now({"vitals": {"mp": 0, "age_ms": 1000}}, clock.now) is None


# ---------------------------------------------------------------- look_after branches 398, 447
def test_look_after_handles_a_death(clock):
    h = H()
    h.skillbook = FakeBook(ok=False)
    h._me = {"hp": 0, "maxhp": 1000}
    calls = []
    h.handle_death = lambda: calls.append(1)
    h.look_after()
    assert calls == [1]


def test_look_after_runs_requests(clock):
    h = H()
    h.skillbook = FakeBook(ok=False)
    h._me = {"hp": 900, "maxhp": 1000}
    h.requests = NonEmpty()
    calls = []
    h.run_requests = lambda: calls.append(1)
    h.look_after()
    assert calls == [1]
