# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""Town errands: the storehouse, shops and buyers, restocking, shard selling, gem making and the
bag trip (unload). The bot under test is a small stand-in that exposes only what engine/town.py
touches; everything it calls elsewhere (travel, bagrules, knowledge, atlas) is monkeypatched, and
`town.visit` / `town.pick` are replaced so the menu flows can be driven without a real game.
"""

import json
import re
import threading

import pytest
from conftest import Clock

from unclefarmer.clock import CLOCK
from unclefarmer import knowledge, paths, settings
from unclefarmer.engine import bagrules, town
from unclefarmer.engine.botparts.base import Stopped


class Link:
    """The agent link: records every line, answers the game's input box and typed text."""

    def __init__(self):
        self.sent = []
        self.input = {}
        self.answers = {}

    def send(self, line):
        self.sent.append(line)
        return self.answers.get(line, "typed")

    def json(self, line):
        if line == "input":
            return dict(self.input)
        return {}


class TownBot:
    def __init__(self, bag=None, clock=None, profile=None):
        self.a = Link()
        self.p = dict(settings.PROFILE_DEFAULTS, **(profile or {}))
        self.bag = [dict(r) for r in (bag or [])]
        self.clock = clock or Clock()
        self.lines = []
        self.spoken = []
        self.choices = []
        self.closed = 0
        self.map = 1
        self.me_at = (0.0, 0.0)
        self.travels = []
        self.talks = []
        self._dialog = {"ok": True, "open": 0, "top": None}
        self.stop_event = threading.Event()

    # ---------------------------------------------------------- small surface
    def log(self, line, speak=False):
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def sleep(self, seconds):
        if self.clock:
            self.clock.advance(seconds)

    def bag_rows(self):
        return [dict(r) for r in self.bag]

    def add(self, name, n):
        for r in self.bag:
            if (r.get("name") or "").lower() == name.lower():
                r["count"] = int(r.get("count") or 0) + int(n)
                return
        self.bag.append({"name": name, "count": int(n)})

    def remove(self, name, n):
        for r in self.bag:
            if (r.get("name") or "").lower() == name.lower():
                r["count"] = max(0, int(r.get("count") or 0) - int(n))
                return int(n)
        return 0

    def map_id(self):
        return self.map

    def me(self):
        return {"x": self.me_at[0], "y": self.me_at[1]}

    def travel(self, mid, cell=None):
        self.travels.append((mid, cell))
        self.map = mid

    def talk(self, npc, at=None):
        self.talks.append((npc, at))
        return True

    # ---------------------------------------------------------- menus
    def dialog(self):
        return self._dialog

    @staticmethod
    def entry_texts(e):
        out = [e.get("text"), e.get("raw")] + [e.get(k) for k in ("f", "g", "h", "i")]
        return [t for t in out + list(e.get("options") or []) if t]

    def find_entry(self, top, pattern):
        for r, row in enumerate(top["rows"]):
            for c, e in enumerate(row):
                if e and any(re.search(pattern, t, re.I) for t in self.entry_texts(e)):
                    return r, c
        return None

    def choose(self, pattern):
        self.choices.append(pattern)
        top = self.dialog().get("top")
        if not top:
            return None
        where = self.find_entry(top, pattern)
        return top["rows"][where[0]][where[1]] if where else None

    def wait_dialog(self, seconds=3.0, want_open=True):
        d = self.dialog()
        return d if bool(d.get("open")) == want_open else None

    def close_dialogs(self):
        self.closed += 1
        self._dialog = {"ok": True, "open": 0, "top": None}

    def note_dialog(self, d, why):
        self.notes = getattr(self, "notes", [])
        self.notes.append(why)

    def refresh_sheet(self, force=False):
        self.sheet_calls = getattr(self, "sheet_calls", [])
        self.sheet_calls.append(force)
        return {"load": 0, "maxload": 100}

    def throw_item(self, name, n):
        return self.remove(name, n)


@pytest.fixture
def clock(monkeypatch):
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


@pytest.fixture
def no_visit(monkeypatch):
    monkeypatch.setattr(town, "visit", lambda *a, **k: "Npc")


def menu(*entries, row=0, col=0):
    return {"row": row, "col": col, "rows": [[e] if isinstance(e, dict) else e for e in entries]}


def opt(text):
    return {"options": [text]}


# ============================================================ counts
def test_counts_sums_by_lowercased_name():
    b = TownBot(bag=[{"name": "Junk", "count": 2}, {"name": "junk", "count": 3},
                     {"name": "Pelt", "count": 1}])
    assert town.counts(b) == {"junk": 5, "pelt": 1}


def test_counts_treats_a_missing_name_as_empty_and_missing_count_as_zero():
    b = TownBot(bag=[{"name": "", "count": 5}, {"name": "Thing"},
                     {"count": 9}, {"name": "Thing", "count": "4"}])
    assert town.counts(b) == {"": 14, "thing": 4}


# ============================================================ item_pattern
def test_item_pattern_matches_stock_and_buy_lines():
    p = town.item_pattern("Large Red Potion")
    assert re.search(p, "187 Large Red Potion")
    assert re.search(p, "Large Red Potion; Unit price 670 arnebia")
    assert re.search(p, "Large Red Potion")


def test_item_pattern_does_not_match_a_longer_name():
    p = town.item_pattern("Large Red Potion")
    assert not re.search(p, "Pet Large Red Potion")
    assert not re.search(p, "Large Red Potions")


def test_item_pattern_escapes_regex_metacharacters():
    p = town.item_pattern("A+B (x)")
    assert re.search(p, "5 A+B (x)")


# ============================================================ gem_of
@pytest.mark.parametrize("shard,gem", [("Level 1 Obsidian Shard", "Level 1 Obsidian"),
                                       ("level 3 ruby shard", "Level 3 ruby"),
                                       ("  Level 2 Moon Shard  ", "Level 2 Moon")])
def test_gem_of_makes_a_gem_name(shard, gem):
    assert town.gem_of(shard) == gem


def test_gem_of_rejects_crystals_and_nonsense():
    assert town.gem_of("Enhance Crystal Shard") is None
    assert town.gem_of(None) is None
    assert town.gem_of("Obsidian") is None


# ============================================================ needs
def test_needs_triggers_below_the_minimum():
    b = TownBot(bag=[{"name": "Red Potion", "count": 5}])
    r = {"item": "Red Potion", "min": 10, "stock": 100, "source": "both", "on": True}
    assert town.needs(b, [r]) == [(r, 5, 100)]


def test_needs_only_reaches_stock_at_the_start_of_a_run():
    b = TownBot(bag=[{"name": "Red Potion", "count": 50}])
    r = {"item": "Red Potion", "min": 10, "stock": 100, "source": "both", "on": True}
    assert town.needs(b, [r]) == []
    assert town.needs(b, [r], start=True) == [(r, 50, 100)]


def test_needs_skips_blank_items_and_off_rules():
    b = TownBot(bag=[{"name": "X", "count": 0}])
    rules = [{"item": "", "min": 5}, {"item": "X", "min": 5, "on": False}]
    assert town.needs(b, rules) == []


def test_needs_strips_the_rule_item_name():
    b = TownBot(bag=[{"name": "Red Potion", "count": 2}])
    r = {"item": "  red potion ", "min": 5, "stock": 10}
    out = town.needs(b, [r])
    assert out == [(r, 2, 10)]


# ============================================================ nearest
def test_nearest_picks_the_cheapest_reachable(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(knowledge, "service_npcs", lambda role: [(1, "A", 5, 5), (2, "B", 9, 9)])
    monkeypatch.setattr(town.travel, "plan", lambda *a, **k: (["leg"], {1: 10, 2: 3}[a[2]]))
    assert town.nearest(b, "storehouse") == (2, "B", 9, 9)


def test_nearest_skips_an_unreachable_npc(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(knowledge, "service_npcs", lambda role: [(1, "A", 5, 5), (2, "B", 9, 9)])

    def plan(*a, **k):
        return (None, None) if a[2] == 1 else (["leg"], 4)
    monkeypatch.setattr(town.travel, "plan", plan)
    assert town.nearest(b, "storehouse") == (2, "B", 9, 9)


def test_nearest_is_none_when_nothing_is_reachable(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(knowledge, "service_npcs", lambda role: [(1, "A", 5, 5)])
    monkeypatch.setattr(town.travel, "plan", lambda *a, **k: (None, None))
    assert town.nearest(b, "storehouse") is None


def test_nearest_uses_explicit_candidates(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(town.travel, "plan", lambda *a, **k: (["leg"], 1))
    assert town.nearest(b, "r", candidates=[(7, "C", 1, 2)]) == (7, "C", 1, 2)


def test_nearest_passes_the_profile_to_the_planner(monkeypatch):
    b = TownBot(profile={"use_transport": False, "vip": True, "use_travel_items": True,
                         "hearth_home": "sunset square"},
                bag=[{"name": "Hearthstone", "count": 1}])
    seen = {}

    def plan(*a, **k):
        seen.update(k)
        return (["leg"], 1)
    monkeypatch.setattr(town.travel, "plan", plan)
    monkeypatch.setattr(knowledge, "service_npcs", lambda role: [(1, "A", 1, 1)])
    town.nearest(b, "role")
    assert seen["transport"] is False and seen["vip"] is True
    assert seen["hearth_home"] == "sunset square"
    assert seen["bag"] == [{"name": "Hearthstone", "count": 1}]


# ============================================================ visit
def test_visit_goes_to_the_nearest_and_talks(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(town, "nearest", lambda bot, role, candidates=None: (3, "Storehouse", 10, 20))
    monkeypatch.setattr(town.atlas, "label", lambda mid: "Map %s" % mid)
    assert town.visit(b, "storehouse") == "Storehouse"
    assert b.travels == [(3, None)]
    assert b.talks == [("Storehouse", (10, 20))]
    assert any("going to Storehouse" in line for line in b.lines)


def test_visit_named_npc_comes_from_knowledge(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(knowledge, "describe_npc",
                        lambda name: {"map": 5, "name": name, "x": 1, "y": 2})
    monkeypatch.setattr(town.atlas, "label", lambda mid: "M")
    assert town.visit(b, "role", name="Bob") == "Bob"
    assert b.travels == [(5, None)] and b.talks == [("Bob", (1, 2))]


def test_visit_uses_candidates_for_nearest(monkeypatch):
    b = TownBot()
    calls = []
    monkeypatch.setattr(town, "nearest",
                        lambda bot, role, candidates=None: calls.append((role, candidates))
                        or (1, "N", 2, 3))
    monkeypatch.setattr(town.atlas, "label", lambda mid: "M")
    assert town.visit(b, "shard_market", candidates=[(1, "N", 2, 3)]) == "N"
    assert calls == [("shard_market", [(1, "N", 2, 3)])]


def test_visit_unknown_role_stops(monkeypatch):
    monkeypatch.setattr(town, "nearest", lambda *a, **k: None)
    b = TownBot()
    with pytest.raises(Stopped) as caught:
        town.visit(b, "storehouse")
    assert "I don't know where to find a storehouse" in str(caught.value)


def test_visit_unknown_name_stops(monkeypatch):
    monkeypatch.setattr(knowledge, "describe_npc", lambda name: None)
    b = TownBot()
    with pytest.raises(Stopped):
        town.visit(b, "storehouse", name="Ghost")


def test_visit_talk_failure_stops(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(town, "nearest", lambda *a, **k: (1, "N", 2, 3))
    monkeypatch.setattr(town.atlas, "label", lambda mid: "M")
    b.talk = lambda npc, at=None: False
    with pytest.raises(Stopped):
        town.visit(b, "storehouse")


# ============================================================ wait_input
def test_wait_input_returns_true_once_the_box_is_open(clock):
    b = TownBot(clock=clock)
    b.a.input = {"ok": True, "open": True}
    assert town.wait_input(b) is True


def test_wait_input_times_out_when_never_open(clock):
    b = TownBot(clock=clock)
    b.a.input = {"ok": True, "open": False}
    assert town.wait_input(b, True, 0.5) is False


def test_wait_input_waits_for_the_box_to_close(clock):
    b = TownBot(clock=clock)
    b.a.input = {"ok": True, "open": False}
    assert town.wait_input(b, False, 0.5) is True


def test_wait_input_ignores_a_bad_answer(clock):
    b = TownBot(clock=clock)
    b.a.input = {"ok": False, "open": True}
    assert town.wait_input(b, True, 0.5) is False


# ============================================================ type_text
def test_type_text_types_and_enters(monkeypatch):
    b = TownBot()
    waits = []
    monkeypatch.setattr(town, "wait_input",
                        lambda bot, want_open=True, seconds=3.0: waits.append(want_open) or True)
    assert town.type_text(b, "Bob") is True
    assert b.a.sent == ["typeclear", "type Bob", "typeenter"]
    assert waits == [True, False]


def test_type_text_without_a_box_returns_false(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(town, "wait_input", lambda *a, **k: False)
    assert town.type_text(b, "Bob") is False
    assert b.a.sent == []


def test_type_text_says_so_when_the_game_refuses(monkeypatch):
    b = TownBot()
    b.a.answers["type Bob"] = "refused"
    monkeypatch.setattr(town, "wait_input", lambda *a, **k: True)
    assert town.type_text(b, "Bob") is False
    assert any("couldn't type into the game" in line for line in b.spoken)


def test_type_text_box_never_closes(monkeypatch):
    b = TownBot()
    answers = iter([True, False])
    monkeypatch.setattr(town, "wait_input", lambda *a, **k: next(answers))
    assert town.type_text(b, "Bob") is False


# ============================================================ type_number
def test_type_number_success_without_a_confirm(monkeypatch):
    b = TownBot()
    waits = []
    monkeypatch.setattr(town, "wait_input",
                        lambda bot, want_open=True, seconds=3.0: waits.append(want_open) or True)
    b.dialog = lambda: {"open": 0, "top": None}
    assert town.type_number(b, 5) is True
    assert "type 5" in b.a.sent
    assert waits == [True, False]
    assert b.choices == []


def test_type_number_confirms_a_second_menu(monkeypatch):
    b = TownBot()
    monkeypatch.setattr(town, "wait_input", lambda *a, **k: True)
    b.dialog = lambda: {"open": 1, "top": menu(opt("yes"))}
    assert town.type_number(b, 5) is True
    assert b.choices == [town.CONFIRM]


def test_type_number_game_refuses_the_number(monkeypatch):
    b = TownBot()
    b.a.answers["type 5"] = "nope"
    monkeypatch.setattr(town, "wait_input", lambda *a, **k: True)
    assert town.type_number(b, 5) is False


def test_type_number_says_so_when_the_box_never_closes(monkeypatch):
    b = TownBot()
    answers = iter([True, False])
    monkeypatch.setattr(town, "wait_input", lambda *a, **k: next(answers))
    assert town.type_number(b, 5) is False
    assert any("didn't take the number" in line for line in b.spoken)


# ============================================================ pick / open_section
def test_pick_returns_false_when_the_entry_is_missing():
    b = TownBot()
    b.choose = lambda pattern: None
    assert town.pick(b, "x") is False


def test_pick_without_a_quantity_just_chooses():
    b = TownBot()
    b.choose = lambda pattern: "entry"
    assert town.pick(b, "x") is True


def test_pick_with_a_quantity_types_it(monkeypatch):
    b = TownBot()
    b.choose = lambda pattern: "entry"
    typed = []
    monkeypatch.setattr(town, "type_number", lambda bot, n: typed.append(n) or True)
    assert town.pick(b, "x", 7) is True
    assert typed == [7]


def test_open_section_walks_in(monkeypatch):
    b = TownBot()
    chosen = []
    b.dialog = lambda: {"open": 1, "top": menu(opt("item storage"))}
    b.choose = lambda pattern: chosen.append(pattern) or "entry"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    assert town.open_section(b, "item storage") is True
    assert chosen == ["item storage"]


def test_open_section_without_a_dialog():
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    assert town.open_section(b, "x") is False


def test_open_section_when_the_entry_is_absent():
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("other"))}
    assert town.open_section(b, "x") is False


# ============================================================ withdraw
def test_withdraw_takes_the_items(monkeypatch, no_visit):
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("item storage"), opt("Large Red Potion"))}
    monkeypatch.setattr(town, "open_section", lambda *a, **k: True)
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.add("Large Red Potion", q))
    assert town.withdraw(b, "Large Red Potion", 5) == 5
    assert b.closed == 1
    assert any("took 5 Large Red Potion" in line for line in b.lines)


def test_withdraw_without_item_storage(monkeypatch, no_visit):
    b = TownBot()
    monkeypatch.setattr(town, "open_section", lambda *a, **k: False)
    assert town.withdraw(b, "X", 1) == 0
    assert any("no item storage" in line for line in b.spoken)


def test_withdraw_when_the_item_is_absent(monkeypatch, no_visit):
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("item storage"))}
    monkeypatch.setattr(town, "open_section", lambda *a, **k: True)
    assert town.withdraw(b, "Nope", 1) == 0
    assert any("no Nope in the storehouse" in line for line in b.lines)


# ============================================================ deposit
def test_deposit_nothing_in_the_bag():
    b = TownBot()
    assert town.deposit(b, "X") == 0


def test_deposit_all_of_an_item(monkeypatch, no_visit):
    b = TownBot(bag=[{"name": "Junk", "count": 4}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("deposit item"), opt("Junk"))}
    monkeypatch.setattr(town, "open_section", lambda *a, **k: True)
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.remove("Junk", q) or True)
    assert town.deposit(b, "Junk") == 4
    assert any("put 4 Junk" in line for line in b.lines)


def test_deposit_partial_amount(monkeypatch, no_visit):
    b = TownBot(bag=[{"name": "Junk", "count": 10}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("deposit item"), opt("Junk"))}
    monkeypatch.setattr(town, "open_section", lambda *a, **k: True)
    seen = []
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: seen.append(q) or b.remove("Junk", q) or True)
    assert town.deposit(b, "Junk", 3) == 3
    assert seen == [3]


def test_deposit_without_a_deposit_menu(monkeypatch, no_visit):
    b = TownBot(bag=[{"name": "Junk", "count": 3}])
    monkeypatch.setattr(town, "open_section", lambda *a, **k: False)
    assert town.deposit(b, "Junk") == 0
    assert any("no deposit" in line for line in b.spoken)


def test_deposit_when_the_pick_fails(monkeypatch, no_visit):
    b = TownBot(bag=[{"name": "Junk", "count": 3}])
    monkeypatch.setattr(town, "open_section", lambda *a, **k: True)
    monkeypatch.setattr(town, "pick", lambda *a, **k: False)
    assert town.deposit(b, "Junk") == 0


# ============================================================ buy
def test_buy_unknown_item_stops(monkeypatch):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [])
    monkeypatch.setattr(knowledge, "shop_role", lambda item: None)
    with pytest.raises(Stopped):
        town.buy(TownBot(), "Thing", 1)


def test_buy_from_the_nearest_known_seller(monkeypatch):
    monkeypatch.setattr(knowledge, "traders",
                        lambda item, they: [(9, "Apothecary", 1, 2, 670, "arnebia")])
    monkeypatch.setattr(knowledge, "shop_role", lambda item: "potions")
    visited = []
    monkeypatch.setattr(town, "visit",
                        lambda bot, role, name=None, candidates=None: visited.append((role, candidates))
                        or "Apothecary")
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("187 Large Red Potion"))}
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.add("Large Red Potion", q))
    assert town.buy(b, "Large Red Potion", 3) == 3
    assert visited == [("potions", [(9, "Apothecary", 1, 2)])]
    assert any("bought 3" in line for line in b.lines)


def test_buy_enters_a_buy_page_first(monkeypatch, no_visit):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [(1, "Shop", 0, 0, 10, "arnebia")])
    monkeypatch.setattr(knowledge, "shop_role", lambda item: "potions")
    b = TownBot()
    dialogs = iter([{"open": 1, "top": menu(opt("welcome"))},
                    {"open": 1, "top": menu(opt("Large Red Potion"))}])
    b.dialog = lambda: next(dialogs)
    monkeypatch.setattr(town, "open_section", lambda bot, pattern: True)
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.add("Large Red Potion", q))
    assert town.buy(b, "Large Red Potion", 2) == 2


def test_buy_when_the_shop_does_not_list_it(monkeypatch, no_visit):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [(1, "Shop", 0, 0, 10, "arnebia")])
    monkeypatch.setattr(knowledge, "shop_role", lambda item: "potions")
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("nothing here"))}
    monkeypatch.setattr(town, "open_section", lambda *a, **k: False)
    assert town.buy(b, "Large Red Potion", 1) == 0
    assert any("doesn't list" in line for line in b.spoken)
    assert b.notes == ["looking for Large Red Potion to buy"]


def test_buy_by_role_when_no_seller_is_known(monkeypatch):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [])
    monkeypatch.setattr(knowledge, "shop_role", lambda item: "grocer")
    visited = []
    monkeypatch.setattr(town, "visit",
                        lambda bot, role, name=None, candidates=None: visited.append(candidates)
                        or "Grocer")
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("Large Red Potion"))}
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.add("Large Red Potion", q))
    assert town.buy(b, "Large Red Potion", 1) == 1
    assert visited == [None]


# ============================================================ sell
def test_sell_unknown_item_stops(monkeypatch):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [])
    monkeypatch.setattr(knowledge, "buyer_role", lambda item: None)
    with pytest.raises(Stopped):
        town.sell(TownBot(), "Thing")


def test_sell_nothing_in_the_bag(monkeypatch):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")])
    monkeypatch.setattr(knowledge, "buyer_role", lambda item: "fish_buyer")
    assert town.sell(TownBot(), "Milkfish") == 0


def test_sell_to_the_best_buyer(monkeypatch, no_visit):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")])
    monkeypatch.setattr(knowledge, "buyer_role", lambda item: "fish_buyer")
    b = TownBot(bag=[{"name": "Milkfish", "count": 4}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("101 Milkfish; Unit price 670 arnebia"))}
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.remove("Milkfish", q) or True)
    assert town.sell(b, "Milkfish") == 4
    assert any("sold 4" in line for line in b.lines)


def test_sell_opens_a_sell_page(monkeypatch, no_visit):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")])
    monkeypatch.setattr(knowledge, "buyer_role", lambda item: "fish_buyer")
    b = TownBot(bag=[{"name": "Milkfish", "count": 2}])
    dialogs = iter([{"open": 1, "top": menu(opt("welcome"))},
                    {"open": 1, "top": menu(opt("Milkfish"))}])
    b.dialog = lambda: next(dialogs)
    monkeypatch.setattr(town, "open_section", lambda bot, pattern: True)
    monkeypatch.setattr(town, "pick", lambda bot, pattern, q: b.remove("Milkfish", q) or True)
    assert town.sell(b, "Milkfish", 2) == 2


def test_sell_when_the_buyer_does_not_list_it(monkeypatch, no_visit):
    monkeypatch.setattr(knowledge, "traders", lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")])
    monkeypatch.setattr(knowledge, "buyer_role", lambda item: "fish_buyer")
    b = TownBot(bag=[{"name": "Milkfish", "count": 2}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("nothing"))}
    monkeypatch.setattr(town, "open_section", lambda *a, **k: False)
    assert town.sell(b, "Milkfish", 1) == 0
    assert any("isn't on the buyer's list" in line for line in b.spoken)


# ============================================================ restock
def test_restock_tell_only_says_it_once(monkeypatch):
    b = TownBot(bag=[{"name": "Potion", "count": 1}])
    rules = [{"item": "Potion", "min": 5, "stock": 50, "source": "tell", "on": True}]
    assert town.restock(b, rules) is False
    assert any("low on Potion" in line for line in b.spoken)
    assert b.supply_told == {"Potion"}
    n = len(b.spoken)
    town.restock(b, rules)
    assert len(b.spoken) == n


def test_restock_storage_then_buy_for_the_rest(monkeypatch):
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "both", "on": True}]
    calls = []
    monkeypatch.setattr(town, "withdraw", lambda bot, item, n: calls.append(("withdraw", item, n)) or 4)
    monkeypatch.setattr(town, "buy", lambda bot, item, n: calls.append(("buy", item, n)) or 6)
    assert town.restock(b, rules) is True
    assert calls == [("withdraw", "Potion", 10), ("buy", "Potion", 6)]


def test_restock_storage_only_never_buys(monkeypatch):
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "storage", "on": True}]
    calls = []
    monkeypatch.setattr(town, "withdraw", lambda bot, item, n: calls.append("w") or 10)
    monkeypatch.setattr(town, "buy", lambda bot, item, n: calls.append("b") or 0)
    assert town.restock(b, rules) is True
    assert calls == ["w"]


def test_restock_buy_only_never_withdraws(monkeypatch):
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "buy", "on": True}]
    calls = []
    monkeypatch.setattr(town, "withdraw", lambda *a, **k: calls.append("w") or 0)
    monkeypatch.setattr(town, "buy", lambda bot, item, n: calls.append(("b", n)) or 10)
    assert town.restock(b, rules) is True
    assert calls == [("b", 10)]


def test_restock_logs_when_still_short(monkeypatch):
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "both", "on": True}]
    monkeypatch.setattr(town, "withdraw", lambda *a, **k: 0)
    monkeypatch.setattr(town, "buy", lambda *a, **k: 0)
    assert town.restock(b, rules) is True
    assert any("still short after restocking" in line for line in b.spoken)


def test_restock_true_when_a_trip_happens_and_short_when_none():
    b = TownBot(bag=[{"name": "Potion", "count": 50}])
    rules = [{"item": "Potion", "min": 5, "stock": 100, "source": "both", "on": True}]
    assert town.restock(b, rules) is False


# ============================================================ shard_catalog
def test_shard_catalog_reads_the_data_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / "shards.json").write_text(
        json.dumps({"shards": {"A": {"price": 1, "makes": "B"}}, "markets": {"5": [1, 2]}}),
        encoding="utf-8")
    assert town.shard_catalog() == ({"A": {"price": 1, "makes": "B"}}, {"5": [1, 2]})


def test_shard_catalog_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    assert town.shard_catalog() == ({}, {})


def test_shard_catalog_broken_file(tmp_path, monkeypatch):
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / "shards.json").write_text("{not json", encoding="utf-8")
    assert town.shard_catalog() == ({}, {})


# ============================================================ market_candidates
def test_market_candidates_uses_the_nearest_survey_npc(monkeypatch):
    monkeypatch.setattr(knowledge, "data", lambda: {"npcs": [
        {"map": 5, "name": "Shard Broker", "x": 1, "y": 1, "role": "shard_market",
         "sources": ["survey x"]},
        {"map": 5, "name": "Other", "x": 9, "y": 9, "role": "shard_market", "sources": ["survey x"]},
        {"map": 5, "name": "NoSurvey", "x": 0, "y": 0, "role": "shard_market", "sources": []},
    ]})
    assert town.market_candidates({5: [1, 1]}) == [(5, "Shard Broker", 1, 1)]


def test_market_candidates_defaults_to_shard_broker(monkeypatch):
    monkeypatch.setattr(knowledge, "data", lambda: {"npcs": []})
    assert town.market_candidates({7: [3, 4]}) == [(7, "Shard Broker", 3, 4)]


# ============================================================ sell_shards
def test_sell_shards_with_none_in_the_bag(monkeypatch):
    monkeypatch.setattr(town, "shard_catalog",
                        lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]}))
    assert town.sell_shards(TownBot(), ["Level 1 Sapphire Shard"]) == 0


def test_sell_shards_without_a_market(monkeypatch):
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 3}])
    assert town.sell_shards(b, ["Level 1 Sapphire Shard"]) == 0
    assert any("don't know where a Shard Market is" in line for line in b.spoken)


def test_sell_shards_sells_in_steps_of_2000(monkeypatch):
    monkeypatch.setattr(town, "shard_catalog",
                        lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]}))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 3000}])
    b.dialog = lambda: {"open": 1,
                        "top": menu(opt("Level 1 Sapphire Shard | Unit price 20 arnebia"))}
    monkeypatch.setattr(town, "visit", lambda *a, **k: "Shard Broker")
    monkeypatch.setattr(town, "market_candidates", lambda markets: [(5, "Shard Broker", 1, 1)])
    steps = []
    monkeypatch.setattr(town, "pick",
                        lambda bot, pattern, q: (steps.append(q),
                                                 b.remove("Level 1 Sapphire Shard", q))[1])
    b.choose = lambda pattern: "entry"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    assert town.sell_shards(b, ["Level 1 Sapphire Shard"]) == 1
    assert steps == [2000, 1000]
    assert any("sold 3000" in line for line in b.lines)


def test_sell_shards_sells_only_the_rule_count(monkeypatch):
    monkeypatch.setattr(town, "shard_catalog",
                        lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]}))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 10}])
    b.dialog = lambda: {"open": 1,
                        "top": menu(opt("Level 1 Sapphire Shard | Unit price 20 arnebia"))}
    monkeypatch.setattr(town, "visit", lambda *a, **k: "Shard Broker")
    monkeypatch.setattr(town, "market_candidates", lambda markets: [(5, "Shard Broker", 1, 1)])
    steps = []
    monkeypatch.setattr(town, "pick",
                        lambda bot, pattern, q: (steps.append(q),
                                                 b.remove("Level 1 Sapphire Shard", q))[1])
    b.choose = lambda pattern: "entry"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    assert town.sell_shards(b, ["Level 1 Sapphire Shard"],
                            at_most={"level 1 sapphire shard": 3}) == 1
    assert steps == [3]


def test_sell_shards_stops_when_the_pick_fails(monkeypatch):
    monkeypatch.setattr(town, "shard_catalog",
                        lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]}))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 5}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("Level 1 Sapphire Shard | Unit price 20"))}
    monkeypatch.setattr(town, "visit", lambda *a, **k: "Shard Broker")
    monkeypatch.setattr(town, "market_candidates", lambda markets: [(5, "Shard Broker", 1, 1)])
    monkeypatch.setattr(town, "pick", lambda *a, **k: False)
    b.choose = lambda pattern: "entry"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    assert town.sell_shards(b, ["Level 1 Sapphire Shard"]) == 0


def test_sell_shards_when_the_market_does_not_list_it(monkeypatch):
    monkeypatch.setattr(town, "shard_catalog",
                        lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]}))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 5}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("nothing to sell"))}
    monkeypatch.setattr(town, "visit", lambda *a, **k: "Shard Broker")
    monkeypatch.setattr(town, "market_candidates", lambda markets: [(5, "Shard Broker", 1, 1)])
    assert town.sell_shards(b, ["Level 1 Sapphire Shard"]) == 0
    assert any("didn't list" in line for line in b.lines)


# ============================================================ _menu_text / shard synthesis
def test_menu_text_joins_every_entry_text():
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": {"rows": [[{"options": ["a"], "text": "t"}],
                                                   [{"raw": "r"}]]}}
    assert town._menu_text(b) == "t | a | r"


def test_menu_text_empty_without_a_menu():
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    assert town._menu_text(b) == ""


def test_open_shard_synthesis_enters_the_submenu():
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("shard synthesis"))}
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    assert town._open_shard_synthesis(b) is True
    assert b.choices == [r"^\s*shard synthesis"]


def test_open_shard_synthesis_already_in_the_list():
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("Level 1 Obsidian Shard42"))}
    assert town._open_shard_synthesis(b) is True
    assert b.choices == []


def test_open_shard_synthesis_uses_the_opener():
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    calls = []
    assert town._open_shard_synthesis(b, opener=lambda: calls.append(1)) is False
    assert len(calls) == 2 and b.closed == 2


def test_open_shard_synthesis_defaults_to_the_craftsman(monkeypatch):
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    visited = []
    monkeypatch.setattr(town, "visit", lambda bot, role: visited.append(role))
    assert town._open_shard_synthesis(b) is False
    assert visited == ["gem_craftsman", "gem_craftsman"]


# ============================================================ make_gems
def test_make_gems_with_nothing_to_do():
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 3}])   # fewer than four
    assert town.make_gems(b, {"Level 1 Obsidian Shard": 5}) == 0
    assert town.make_gems(TownBot(), {"Ruby": 9}) == 0


def test_make_gems_makes_a_gem(monkeypatch):
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100000}])
    monkeypatch.setattr(town, "_open_shard_synthesis", lambda bot, opener=None: True)
    b.dialog = lambda: {"open": 1, "top": menu(
        opt("Level 1 Obsidian Shard 8"),
        opt("synthesize 1 Level 1 Obsidian needs 4 Level 1 Obsidian Shard; fee 100 arnebia"))}
    b.choose = lambda pattern: "row"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    typed = []

    def type_number(bot, n):
        typed.append(n)
        b.add("Level 1 Obsidian", n)
        return True
    monkeypatch.setattr(town, "type_number", type_number)
    assert town.make_gems(b, {"Level 1 Obsidian Shard": 8}) == 2
    assert typed == [2]
    assert b.p["gem_fees"]["level 1 obsidian"] == 100
    assert any("made 2 Level 1 Obsidian" in line for line in b.lines)


def test_make_gems_stops_when_arnebia_is_short(monkeypatch):
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100}])
    monkeypatch.setattr(town, "_open_shard_synthesis", lambda bot, opener=None: True)
    b.dialog = lambda: {"open": 1, "top": menu(
        opt("synthesize 1 Level 1 Obsidian needs 4 Level 1 Obsidian Shard; fee 100000 arnebia"))}
    b.choose = lambda pattern: "row"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    typed = []
    monkeypatch.setattr(town, "type_number", lambda bot, n: typed.append(n) or True)
    assert town.make_gems(b, {"Level 1 Obsidian Shard": 8}) == 0
    assert typed == []
    assert any("not enough spare arnebia" in line for line in b.spoken)


def test_make_gems_asks_which_level(monkeypatch):
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100000}])
    monkeypatch.setattr(town, "_open_shard_synthesis", lambda bot, opener=None: True)
    b.dialog = lambda: {"open": 1, "top": menu(
        opt("synthesize Level 1 Obsidian"),
        opt("synthesize 1 Level 1 Obsidian needs 4 Level 1 Obsidian Shard; fee 100 arnebia"))}
    chosen = []
    b.choose = lambda pattern: chosen.append(pattern) or "row"
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    monkeypatch.setattr(town, "type_number",
                        lambda bot, n: (b.add("Level 1 Obsidian", n), True)[1])
    assert town.make_gems(b, {"Level 1 Obsidian Shard": 8}) == 2
    assert any("synthesize" in c for c in chosen)


def test_make_gems_stops_when_synthesis_wont_open(monkeypatch):
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8}])
    monkeypatch.setattr(town, "_open_shard_synthesis", lambda bot, opener=None: False)
    assert town.make_gems(b, {"Level 1 Obsidian Shard": 8}) == 0
    assert any("couldn't open the Gem Craftsman" in line for line in b.spoken)


def test_make_gems_when_the_craftsman_does_not_list_the_shard(monkeypatch):
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100000}])
    monkeypatch.setattr(town, "_open_shard_synthesis", lambda bot, opener=None: True)
    b.choose = lambda pattern: None
    b.wait_dialog = lambda seconds=3.0: {"open": 1}
    assert town.make_gems(b, {"Level 1 Obsidian Shard": 8}) == 0
    assert any("didn't list Level 1 Obsidian Shard" in line for line in b.lines)


# ============================================================ _load
def test_load_reads_the_sheet():
    b = TownBot()
    b.refresh_sheet = lambda force=False: {"load": 42, "maxload": 100}
    assert town._load(b) == 42


def test_load_none_without_a_limit():
    b = TownBot()
    b.refresh_sheet = lambda force=False: {"load": 42, "maxload": 0}
    assert town._load(b) is None


def test_load_none_when_the_sheet_is_missing():
    b = TownBot()
    b.refresh_sheet = lambda force=False: None
    assert town._load(b) is None


# ============================================================ unload
def _unload_bot(bag, profile=None):
    b = TownBot(bag=bag, profile=profile)
    b.refresh_sheet = lambda force=False: {"load": 100, "maxload": 100}
    return b


def test_unload_with_no_rules(monkeypatch):
    b = _unload_bot([{"name": "Rock", "count": 5}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [])
    assert town.unload(b) == 0
    assert any("no rule says to store" in line for line in b.spoken)


def test_unload_passes_the_bag_and_gained_to_the_planner(monkeypatch):
    b = _unload_bot([{"name": "Junk", "count": 5}], {"scan": {"hotkeys": [{"item": "Rock"}]}})
    b.bag_start = {"junk": 2}        # unload expects bag_start already lower-cased (counts() keys)
    captured = {}

    def plan(p, bag, hot, gained):
        captured.update(bag=bag, hot=hot, gained=gained)
        return []
    monkeypatch.setattr(bagrules, "plan", plan)
    assert town.unload(b) == 0
    assert captured["bag"] == {"Junk": 5}
    assert captured["hot"] == ["Rock"]
    assert captured["gained"] == {"junk": 3}


def test_unload_sells_shards_at_the_market(monkeypatch):
    b = _unload_bot([{"name": "Level 1 Sapphire Shard", "count": 5}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Level 1 Sapphire Shard", "sell", 5)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda name, before, after, n: 2.0)
    monkeypatch.setattr(town, "shard_catalog",
                        lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {}))
    sold = []
    monkeypatch.setattr(town, "_sell_one_shard", lambda bot, name, n: sold.append((name, n)) or 5)
    assert town.unload(b) == 1
    assert sold == [("Level 1 Sapphire Shard", 5)]
    assert any("weighs 2.0 each" in line for line in b.lines)


def test_unload_sells_other_things_to_a_buyer(monkeypatch):
    b = _unload_bot([{"name": "Long-age Turtle", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Long-age Turtle", "sell", 2)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 3.0)
    monkeypatch.setattr(bagrules, "buyer", lambda name: ("Buyer", 850, "arnebia"))
    monkeypatch.setattr(knowledge, "buyer_role", lambda name: None)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))
    calls = []
    monkeypatch.setattr(town, "sell", lambda bot, name, n: calls.append((name, n)) or 2)
    assert town.unload(b) == 1
    assert calls == [("Long-age Turtle", 2)]


def test_unload_stores_what_nobody_buys(monkeypatch):
    b = _unload_bot([{"name": "Odd Rock", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Odd Rock", "sell", 2)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 3.0)
    monkeypatch.setattr(bagrules, "buyer", lambda name: None)
    monkeypatch.setattr(knowledge, "buyer_role", lambda name: None)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))
    stored = []
    monkeypatch.setattr(town, "deposit", lambda bot, name, n: stored.append((name, n)) or 2)
    assert town.unload(b) == 1
    assert stored == [("Odd Rock", 2)]
    assert any("nobody I know buys Odd Rock" in line for line in b.lines)


def test_unload_stores_what_the_rules_say_to_store(monkeypatch):
    b = _unload_bot([{"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Pelt", "store", 3)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 1.0)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))
    stored = []
    monkeypatch.setattr(town, "deposit", lambda bot, name, n: stored.append((name, n)) or 3)
    assert town.unload(b) == 1
    assert stored == [("Pelt", 3)]


def test_unload_throws_away_last(monkeypatch):
    b = _unload_bot([{"name": "Junk", "count": 4}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Junk", "throw", 4)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 1.0)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))
    thrown = []
    b.throw_item = lambda name, n: thrown.append((name, n)) or 4
    assert town.unload(b) == 1
    assert thrown == [("Junk", 4)]


def test_unload_falls_back_to_storing_when_the_sale_stops(monkeypatch):
    b = _unload_bot([{"name": "Thing", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Thing", "sell", 2)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 1.0)
    monkeypatch.setattr(bagrules, "buyer", lambda name: ("Buyer", 1, "arnebia"))
    monkeypatch.setattr(knowledge, "buyer_role", lambda name: None)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))

    def boom(bot, name, n):
        raise Stopped("nope")
    monkeypatch.setattr(town, "sell", boom)
    stored = []
    monkeypatch.setattr(town, "deposit", lambda bot, name, n: stored.append((name, n)) or 2)
    assert town.unload(b) == 1
    assert stored == [("Thing", 2)]
    assert any("couldn't sell Thing" in line for line in b.lines)


def test_unload_reraises_a_sale_stop_when_the_run_is_ending(monkeypatch):
    b = _unload_bot([{"name": "Thing", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Thing", "sell", 2)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 1.0)
    monkeypatch.setattr(bagrules, "buyer", lambda name: ("Buyer", 1, "arnebia"))
    monkeypatch.setattr(knowledge, "buyer_role", lambda name: None)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))

    def boom(bot, name, n):
        raise Stopped("nope")
    monkeypatch.setattr(town, "sell", boom)
    b.stop_event.set()
    with pytest.raises(Stopped):
        town.unload(b)


def test_unload_reraises_a_store_stop_when_the_run_is_ending(monkeypatch):
    b = _unload_bot([{"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Pelt", "store", 3)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 1.0)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))

    def boom(bot, name, n):
        raise Stopped("nope")
    monkeypatch.setattr(town, "deposit", boom)
    b.stop_event.set()
    with pytest.raises(Stopped):
        town.unload(b)


def test_unload_logs_a_store_stop_when_the_run_goes_on(monkeypatch):
    b = _unload_bot([{"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", lambda *a, **k: [("Pelt", "store", 3)])
    monkeypatch.setattr(bagrules, "learn_weight", lambda *a, **k: 1.0)
    monkeypatch.setattr(town, "shard_catalog", lambda: ({}, {}))

    def boom(bot, name, n):
        raise Stopped("nope")
    monkeypatch.setattr(town, "deposit", boom)
    assert town.unload(b) == 0
    assert any("couldn't store Pelt" in line for line in b.lines)


# ============================================================ _sell_one_shard
def test_sell_one_shard_reports_how_many_left(monkeypatch):
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 5}])

    def fake(bot, names, at_most=None):
        bot.bag = [{"name": "Level 1 Sapphire Shard", "count": 2}]
        return 1
    monkeypatch.setattr(town, "sell_shards", fake)
    assert town._sell_one_shard(b, "Level 1 Sapphire Shard", 5) == 3
