# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Town errands: the storehouse, shops and buyers, restocking, shard selling, gem making and the
bag trip (unload). The bot under test is a small stand-in that exposes only what engine/town.py
touches; everything it calls elsewhere (travel, bagrules, knowledge, atlas) is monkeypatched, and
`town.visit` / `town.pick` are replaced so the menu flows can be driven without a real game.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import json
import re
import threading

import pytest
from conftest import as_bot, returning, stand_in, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import knowledge, paths, settings
from unclefarmer.engine import bagrules, town
from unclefarmer.engine.botparts.base import Stopped
from pathlib import Path
from typing import Any


class Link:
    """The agent link: records every line, answers the game's input box and typed text."""

    def __init__(self) -> None:
        self.sent: list[str] = []
        self.input: dict[str, Any] = {}
        self.answers: dict[str, str] = {}

    def send(self, line: str) -> str:
        self.sent.append(line)
        return self.answers.get(line, "typed")

    def json(self, line: str) -> Any:
        if line == "input":
            return dict(self.input)
        return {}


class TownBot:
    def __init__(self, bag: list[dict[str, Any]] | None = None, clock: Clock | None = None,
                 profile: dict[str, Any] | None = None) -> None:
        self.link = Link()
        self.a = self.link
        self.p = dict(settings.PROFILE_DEFAULTS, **(profile or {}))
        self.supply_told: set[str] = set()              # supplies said to be low (town.restock's own start)
        self.bag_start: dict[str, int] | None = None
        self.bag = [dict(r) for r in (bag or [])]
        self.clock = clock or Clock()
        self.lines: list[str] = []
        self.spoken: list[str] = []
        self.choices: list[str] = []
        self.closed = 0
        self.map = 1
        self.me_at = (0.0, 0.0)
        self.travels: list[tuple[int, tuple[float, float] | None]] = []
        self.talks: list[tuple[str, tuple[float, float] | None]] = []
        self._dialog = {"ok": True, "open": 0, "top": None}
        self.stop_event = threading.Event()

    # ---------------------------------------------------------- small surface
    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def sleep(self, seconds: float) -> None:
        if self.clock:
            self.clock.advance(seconds)

    def bag_rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.bag]

    def add(self, name: str, n: int) -> None:
        for r in self.bag:
            if (r.get("name") or "").lower() == name.lower():
                r["count"] = int(r.get("count") or 0) + int(n)
                return
        self.bag.append({"name": name, "count": int(n)})

    def remove(self, name: str, n: int) -> int:
        for r in self.bag:
            if (r.get("name") or "").lower() == name.lower():
                r["count"] = max(0, int(r.get("count") or 0) - int(n))
                return int(n)
        return 0

    def map_id(self) -> int:
        return self.map

    def me(self) -> dict[str, Any]:
        return {"x": self.me_at[0], "y": self.me_at[1]}

    def travel(self, mid: int, cell: tuple[float, float] | None=None) -> None:
        self.travels.append((mid, cell))
        self.map = mid

    def talk(self, npc: str, at: tuple[float, float] | None=None) -> bool:
        self.talks.append((npc, at))
        return True

    # ---------------------------------------------------------- menus
    def dialog(self) -> dict[str, Any]:
        return self._dialog

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
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
        self.choices.append(pattern)
        top = self.dialog().get("top")
        if not top:
            return None
        where = self.find_entry(top, pattern)
        return top["rows"][where[0]][where[1]] if where else None

    def wait_dialog(self, seconds: float=3.0, want_open: bool=True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if bool(d.get("open")) == want_open else None

    def close_dialogs(self) -> None:
        self.closed += 1
        self._dialog = {"ok": True, "open": 0, "top": None}

    def note_dialog(self, d: dict[str, Any], why: str) -> None:
        self.notes = getattr(self, "notes", [])
        self.notes.append(why)

    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        self.sheet_calls = getattr(self, "sheet_calls", [])
        self.sheet_calls.append(force)
        return {"load": 0, "maxload": 100}

    def throw_item(self, name: str, n: int) -> int:
        return self.remove(name, n)


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


@pytest.fixture
def no_visit(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Npc")))


def menu(*entries: Any, row: int = 0, col: int = 0) -> dict[str, Any]:
    return {"row": row, "col": col, "rows": [[e] if isinstance(e, dict) else e for e in entries]}


def opt(text: str) -> dict[str, list[str]]:
    return {"options": [text]}


# ============================================================ counts
def test_counts_sums_by_lowercased_name() -> None:
    b = TownBot(bag=[{"name": "Junk", "count": 2}, {"name": "junk", "count": 3},
                     {"name": "Pelt", "count": 1}])
    assert town.counts(as_bot(b)) == {"junk": 5, "pelt": 1}


def test_counts_treats_a_missing_name_as_empty_and_missing_count_as_zero() -> None:
    b = TownBot(bag=[{"name": "", "count": 5}, {"name": "Thing"},
                     {"count": 9}, {"name": "Thing", "count": "4"}])
    assert town.counts(as_bot(b)) == {"": 14, "thing": 4}


# ============================================================ item_pattern
def test_item_pattern_matches_stock_and_buy_lines() -> None:
    p = town.item_pattern("Large Red Potion")
    assert re.search(p, "187 Large Red Potion")
    assert re.search(p, "Large Red Potion; Unit price 670 arnebia")
    assert re.search(p, "Large Red Potion")


def test_item_pattern_does_not_match_a_longer_name() -> None:
    p = town.item_pattern("Large Red Potion")
    assert not re.search(p, "Pet Large Red Potion")
    assert not re.search(p, "Large Red Potions")


def test_item_pattern_escapes_regex_metacharacters() -> None:
    p = town.item_pattern("A+B (x)")
    assert re.search(p, "5 A+B (x)")


# ============================================================ gem_of
@pytest.mark.parametrize("shard,gem", [("Level 1 Obsidian Shard", "Level 1 Obsidian"),
                                       ("level 3 ruby shard", "Level 3 ruby"),
                                       ("  Level 2 Moon Shard  ", "Level 2 Moon")])
def test_gem_of_makes_a_gem_name(shard: str, gem: str) -> None:
    assert town.gem_of(shard) == gem


def test_gem_of_rejects_crystals_and_nonsense() -> None:
    assert town.gem_of("Enhance Crystal Shard") is None
    assert town.gem_of(None) is None
    assert town.gem_of("Obsidian") is None


# ============================================================ needs
def test_needs_triggers_below_the_minimum() -> None:
    b = TownBot(bag=[{"name": "Red Potion", "count": 5}])
    r = {"item": "Red Potion", "min": 10, "stock": 100, "source": "both", "on": True}
    assert town.needs(as_bot(b), [r]) == [(r, 5, 100)]


def test_needs_only_reaches_stock_at_the_start_of_a_run() -> None:
    b = TownBot(bag=[{"name": "Red Potion", "count": 50}])
    r = {"item": "Red Potion", "min": 10, "stock": 100, "source": "both", "on": True}
    assert town.needs(as_bot(b), [r]) == []
    assert town.needs(as_bot(b), [r], start=True) == [(r, 50, 100)]


def test_needs_skips_blank_items_and_off_rules() -> None:
    b = TownBot(bag=[{"name": "X", "count": 0}])
    rules = [{"item": "", "min": 5}, {"item": "X", "min": 5, "on": False}]
    assert town.needs(as_bot(b), rules) == []


def test_needs_strips_the_rule_item_name() -> None:
    b = TownBot(bag=[{"name": "Red Potion", "count": 2}])
    r = {"item": "  red potion ", "min": 5, "stock": 10}
    out = town.needs(as_bot(b), [r])
    assert out == [(r, 2, 10)]


# ============================================================ nearest
def test_nearest_picks_the_cheapest_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: [(1, "A", 5, 5), (2, "B", 9, 9)]))
    def costs(here_map: int, here_cell: Sequence[float], goal_map: int, *a: object, **k: object) -> tuple[list[str], int]:
        return (["leg"], {1: 10, 2: 3}[goal_map])
    monkeypatch.setattr(town.travel, "plan", costs)
    assert town.nearest(as_bot(b), "storehouse") == (2, "B", 9, 9)


def test_nearest_skips_an_unreachable_npc(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: [(1, "A", 5, 5), (2, "B", 9, 9)]))

    def plan(here_map: int, here_cell: Sequence[float], goal_map: int, *a: object,
             **k: object) -> tuple[list[str] | None, int | None]:
        return (None, None) if goal_map == 1 else (["leg"], 4)
    monkeypatch.setattr(town.travel, "plan", plan)
    assert town.nearest(as_bot(b), "storehouse") == (2, "B", 9, 9)


def test_nearest_is_none_when_nothing_is_reachable(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: [(1, "A", 5, 5)]))
    monkeypatch.setattr(town.travel, "plan", stand_in(town.travel.plan, returning((None, None))))
    assert town.nearest(as_bot(b), "storehouse") is None


def test_nearest_uses_explicit_candidates(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(town.travel, "plan", stand_in(town.travel.plan, returning((["leg"], 1))))
    assert town.nearest(as_bot(b), "r", candidates=[(7, "C", 1, 2)]) == (7, "C", 1, 2)


def test_nearest_passes_the_profile_to_the_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(profile={"use_transport": False, "vip": True, "use_travel_items": True,
                         "hearth_home": "sunset square"},
                bag=[{"name": "Hearthstone", "count": 1}])
    seen: dict[str, object] = {}

    def plan(here_map: int, here_cell: Sequence[float], goal_map: int, *a: object, **k: object) -> tuple[list[str], int]:
        seen.update(k)
        return (["leg"], 1)
    monkeypatch.setattr(town.travel, "plan", plan)
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(knowledge.service_npcs, lambda role: [(1, "A", 1, 1)]))
    town.nearest(as_bot(b), "role")
    assert seen["transport"] is False and seen["vip"] is True
    assert seen["hearth_home"] == "sunset square"
    assert seen["bag"] == [{"name": "Hearthstone", "count": 1}]


# ============================================================ visit
def test_visit_goes_to_the_nearest_and_talks(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, lambda b, role, candidates=None: (3, "Storehouse", 10, 20)))
    monkeypatch.setattr(town.atlas, "label", stand_in(town.atlas.label, lambda mid: "Map %s" % mid))
    assert town.visit(as_bot(b), "storehouse") == "Storehouse"
    assert b.travels == [(3, None)]
    assert b.talks == [("Storehouse", (10, 20))]
    assert any("going to Storehouse" in line for line in b.lines)


def test_visit_named_npc_comes_from_knowledge(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(knowledge, "describe_npc",
                        stand_in(knowledge.describe_npc, lambda name: {"map": 5, "name": name, "x": 1, "y": 2}))
    monkeypatch.setattr(town.atlas, "label", stand_in(town.atlas.label, lambda mid: "M"))
    assert town.visit(as_bot(b), "role", name="Bob") == "Bob"
    assert b.travels == [(5, None)] and b.talks == [("Bob", (1, 2))]


def test_visit_uses_candidates_for_nearest(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    calls: list[tuple[str | None, Sequence[tuple[int, str, float, float]] | None]] = []
    monkeypatch.setattr(town, "nearest",
                        stand_in(town.nearest, lambda b, role, candidates=None: calls.append((role, candidates))
                        or (1, "N", 2, 3)))
    monkeypatch.setattr(town.atlas, "label", stand_in(town.atlas.label, lambda mid: "M"))
    assert town.visit(as_bot(b), "shard_market", candidates=[(1, "N", 2, 3)]) == "N"
    assert calls == [("shard_market", [(1, "N", 2, 3)])]


def test_visit_unknown_role_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, returning(None)))
    b = TownBot()
    with pytest.raises(Stopped) as caught:
        town.visit(as_bot(b), "storehouse")
    assert "I don't know where to find a storehouse" in str(caught.value)


def test_visit_unknown_name_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "describe_npc", stand_in(knowledge.describe_npc, lambda name: None))
    b = TownBot()
    with pytest.raises(Stopped):
        town.visit(as_bot(b), "storehouse", name="Ghost")


def test_visit_talk_failure_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(town, "nearest", stand_in(town.nearest, returning((1, "N", 2, 3))))
    monkeypatch.setattr(town.atlas, "label", stand_in(town.atlas.label, lambda mid: "M"))
    b.talk = lambda npc, at=None: False
    with pytest.raises(Stopped):
        town.visit(as_bot(b), "storehouse")


# ============================================================ wait_input
def test_wait_input_returns_true_once_the_box_is_open(clock: Clock) -> None:
    b = TownBot(clock=clock)
    b.a.input = {"ok": True, "open": True}
    assert town.wait_input(as_bot(b)) is True


def test_wait_input_times_out_when_never_open(clock: Clock) -> None:
    b = TownBot(clock=clock)
    b.a.input = {"ok": True, "open": False}
    assert town.wait_input(as_bot(b), True, 0.5) is False


def test_wait_input_waits_for_the_box_to_close(clock: Clock) -> None:
    b = TownBot(clock=clock)
    b.a.input = {"ok": True, "open": False}
    assert town.wait_input(as_bot(b), False, 0.5) is True


def test_wait_input_ignores_a_bad_answer(clock: Clock) -> None:
    b = TownBot(clock=clock)
    b.a.input = {"ok": False, "open": True}
    assert town.wait_input(as_bot(b), True, 0.5) is False


# ============================================================ type_text
def test_type_text_types_and_enters(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    waits: list[bool] = []
    monkeypatch.setattr(town, "wait_input",
                        stand_in(town.wait_input, lambda b, want_open=True, seconds=3.0: waits.append(want_open) or True))
    assert town.type_text(as_bot(b), "Bob") is True
    assert b.a.sent == ["typeclear", "type Bob", "typeenter"]
    assert waits == [True, False]


def test_type_text_without_a_box_returns_false(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(False)))
    assert town.type_text(as_bot(b), "Bob") is False
    assert b.a.sent == []


def test_type_text_says_so_when_the_game_refuses(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    b.link.answers["type Bob"] = "refused"
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(True)))
    assert town.type_text(as_bot(b), "Bob") is False
    assert any("couldn't type into the game" in line for line in b.spoken)


def test_type_text_box_never_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    answers = iter([True, False])
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open=True, seconds=3.0: next(answers)))
    assert town.type_text(as_bot(b), "Bob") is False


# ============================================================ type_number
def test_type_number_success_without_a_confirm(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    waits: list[bool] = []
    monkeypatch.setattr(town, "wait_input",
                        stand_in(town.wait_input, lambda b, want_open=True, seconds=3.0: waits.append(want_open) or True))
    b.dialog = lambda: {"open": 0, "top": None}
    assert town.type_number(as_bot(b), 5) is True
    assert "type 5" in b.a.sent
    assert waits == [True, False]
    assert b.choices == []


def test_type_number_confirms_a_second_menu(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(True)))
    b.dialog = lambda: {"open": 1, "top": menu(opt("yes"))}
    assert town.type_number(as_bot(b), 5) is True
    assert b.choices == [town.CONFIRM]


def test_type_number_game_refuses_the_number(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    b.link.answers["type 5"] = "nope"
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(True)))
    assert town.type_number(as_bot(b), 5) is False


def test_type_number_says_so_when_the_box_never_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    answers = iter([True, False])
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, lambda b, want_open=True, seconds=3.0: next(answers)))
    assert town.type_number(as_bot(b), 5) is False
    assert any("didn't take the number" in line for line in b.spoken)


# ============================================================ pick / open_section
def test_pick_returns_false_when_the_entry_is_missing() -> None:
    b = TownBot()
    b.choose = lambda pattern: None
    assert town.pick(as_bot(b), "x") is False


def test_pick_without_a_quantity_just_chooses() -> None:
    b = TownBot()
    b.choose = lambda pattern: {"text": "entry"}
    assert town.pick(as_bot(b), "x") is True


def test_pick_with_a_quantity_types_it(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    b.choose = lambda pattern: {"text": "entry"}
    typed: list[int] = []
    monkeypatch.setattr(town, "type_number", stand_in(town.type_number, lambda b, n: typed.append(n) or True))
    assert town.pick(as_bot(b), "x", 7) is True
    assert typed == [7]


def test_open_section_walks_in(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    chosen: list[str] = []
    b.dialog = lambda: {"open": 1, "top": menu(opt("item storage"))}
    b.choose = lambda pattern: chosen.append(pattern) or {"text": "entry"}
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    assert town.open_section(as_bot(b), "item storage") is True
    assert chosen == ["item storage"]


def test_open_section_without_a_dialog() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    assert town.open_section(as_bot(b), "x") is False


def test_open_section_when_the_entry_is_absent() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("other"))}
    assert town.open_section(as_bot(b), "x") is False


# ============================================================ withdraw
def test_withdraw_takes_the_items(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    tb = TownBot()
    tb.dialog = lambda: {"open": 1, "top": menu(opt("item storage"), opt("Large Red Potion"))}
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(True)))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.add("Large Red Potion", quantity or 0)))
    assert town.withdraw(as_bot(tb), "Large Red Potion", 5) == 5
    assert tb.closed == 1
    assert any("took 5 Large Red Potion" in line for line in tb.lines)


def test_withdraw_without_item_storage(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    b = TownBot()
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(False)))
    assert town.withdraw(as_bot(b), "X", 1) == 0
    assert any("no item storage" in line for line in b.spoken)


def test_withdraw_when_the_item_is_absent(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("item storage"))}
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(True)))
    assert town.withdraw(as_bot(b), "Nope", 1) == 0
    assert any("no Nope in the storehouse" in line for line in b.lines)


# ============================================================ deposit
def test_deposit_nothing_in_the_bag() -> None:
    b = TownBot()
    assert town.deposit(as_bot(b), "X") == 0


def test_deposit_all_of_an_item(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    tb = TownBot(bag=[{"name": "Junk", "count": 4}])
    tb.dialog = lambda: {"open": 1, "top": menu(opt("deposit item"), opt("Junk"))}
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(True)))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.remove("Junk", quantity or 0) or True))
    assert town.deposit(as_bot(tb), "Junk") == 4
    assert any("put 4 Junk" in line for line in tb.lines)


def test_deposit_partial_amount(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    tb = TownBot(bag=[{"name": "Junk", "count": 10}])
    tb.dialog = lambda: {"open": 1, "top": menu(opt("deposit item"), opt("Junk"))}
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(True)))
    seen: list[int | None] = []
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: seen.append(quantity) or tb.remove("Junk", quantity or 0) or True))
    assert town.deposit(as_bot(tb), "Junk", 3) == 3
    assert seen == [3]


def test_deposit_without_a_deposit_menu(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    b = TownBot(bag=[{"name": "Junk", "count": 3}])
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(False)))
    assert town.deposit(as_bot(b), "Junk") == 0
    assert any("no deposit" in line for line in b.spoken)


def test_deposit_when_the_pick_fails(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    b = TownBot(bag=[{"name": "Junk", "count": 3}])
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(True)))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, returning(False)))
    assert town.deposit(as_bot(b), "Junk") == 0


# ============================================================ buy
Npc = tuple[int, str, float, float]
PHILLIPS: Npc = (1041, "Apothecary Phillips", 180, 220)      # the one shop the game data has a menu of
MAGNUS: Npc = (1121, "Potion Merchant Magnus", 40, 50)        # next door to where we farm
DANI: Npc = (1021, "Pharmacist Dani", 70, 80)


@pytest.fixture
def shops(monkeypatch: pytest.MonkeyPatch) -> dict[Npc, float]:
    """Phillips known to sell potions, Magnus and Dani only known as potion sellers; nearest() goes by
    the travel costs in the returned dict (change them in a test)."""
    cost: dict[Npc, float] = {MAGNUS: 10.0, DANI: 50.0, PHILLIPS: 300.0}
    monkeypatch.setattr(knowledge, "traders", stand_in(
        knowledge.traders, lambda item, they: [PHILLIPS + (450, "arnebia")] if they == "sell" else []))
    monkeypatch.setattr(knowledge, "shop_role", stand_in(knowledge.shop_role, lambda name: "potions"))
    monkeypatch.setattr(knowledge, "service_npcs", stand_in(
        knowledge.service_npcs, lambda role: [PHILLIPS, MAGNUS, DANI] if role == "potions" else []))
    monkeypatch.setattr(town, "nearest", stand_in(
        town.nearest, lambda b, role, candidates=None: min(candidates or [], key=lambda n: cost[n], default=None)))
    return cost


def shop_visits(monkeypatch: pytest.MonkeyPatch) -> list[Npc]:
    went: list[Npc] = []

    def visit(b: Any, role: str | None, name: str | None = None, candidates: Sequence[Npc] | None = None) -> str:
        assert candidates
        went.append(candidates[0])
        return candidates[0][1]
    monkeypatch.setattr(town, "visit", stand_in(town.visit, visit))
    return went


def test_buy_unknown_item_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: []))
    monkeypatch.setattr(knowledge, "shop_role", stand_in(knowledge.shop_role, lambda name: None))
    with pytest.raises(Stopped):
        town.buy(as_bot(TownBot()), "Thing", 1)


def test_buy_goes_to_the_nearest_seller_not_the_one_on_record(monkeypatch: pytest.MonkeyPatch,
                                                              shops: dict[Npc, float]) -> None:
    # farming Flaming Mountain, the bot crossed to Giant Rock City for potions every time: only
    # Phillips's menu was in the game data
    went = shop_visits(monkeypatch)
    tb = TownBot()
    tb.dialog = lambda: {"open": 1, "top": menu(opt("187 Large Red Potion"))}
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.add("Large Red Potion", quantity or 0)))
    assert town.buy(as_bot(tb), "Large Red Potion", 3) == 3
    assert went == [MAGNUS]
    assert any("bought 3" in line for line in tb.lines)
    assert "large red potion" in town._shops_seen()["1121|potion merchant magnus"]["sells"]


def test_buy_enters_a_buy_page_first(monkeypatch: pytest.MonkeyPatch, shops: dict[Npc, float]) -> None:
    shop_visits(monkeypatch)
    tb = TownBot()
    dialogs = iter([{"open": 1, "top": menu(opt("welcome"))},
                    {"open": 1, "top": menu(opt("Large Red Potion"))}])
    tb.dialog = lambda: next(dialogs)
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.add("Large Red Potion", quantity or 0)))
    assert town.buy(as_bot(tb), "Large Red Potion", 2) == 2


def test_a_shop_without_it_is_remembered_and_the_next_nearest_tried(monkeypatch: pytest.MonkeyPatch,
                                                                    shops: dict[Npc, float]) -> None:
    went = shop_visits(monkeypatch)
    tb = TownBot()
    tb.dialog = lambda: ({"open": 1, "top": menu(opt("Large Red Potion"))} if went[-1] == DANI
                         else {"open": 1, "top": menu(opt("Bread"))})
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(False)))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.add("Large Red Potion", quantity or 0)))
    assert town.buy(as_bot(tb), "Large Red Potion", 1) == 1
    assert went == [MAGNUS, DANI]
    assert any("Potion Merchant Magnus doesn't sell Large Red Potion" in line for line in tb.lines)
    assert MAGNUS not in town.shops_for("Large Red Potion")       # next time: not tried again
    assert DANI in town.shops_for("Large Red Potion")


def test_buy_when_no_shop_near_enough_lists_it(monkeypatch: pytest.MonkeyPatch, shops: dict[Npc, float]) -> None:
    went = shop_visits(monkeypatch)
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("nothing here"))}
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(False)))
    assert town.buy(as_bot(b), "Large Red Potion", 1) == 0
    assert went == [MAGNUS, DANI, PHILLIPS][:town.BUY_TRIES]
    assert any("no shop near enough lists" in line for line in b.spoken)
    assert b.notes == ["looking for Large Red Potion to buy"] * len(went)


def test_buy_by_role_when_no_seller_is_known(monkeypatch: pytest.MonkeyPatch, shops: dict[Npc, float]) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: []))
    went = shop_visits(monkeypatch)
    tb = TownBot()
    tb.dialog = lambda: {"open": 1, "top": menu(opt("Large Red Potion"))}
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.add("Large Red Potion", quantity or 0)))
    assert town.buy(as_bot(tb), "Large Red Potion", 1) == 1
    assert went == [MAGNUS]


def test_the_shop_record_moves_an_item_from_lacks_to_sells() -> None:
    town.note_shop(MAGNUS, "Large Red Potion", False)
    assert "large red potion" in town._shops_seen()["1121|potion merchant magnus"]["lacks"]
    town.note_shop(MAGNUS, "Large Red Potion", True)          # it was out of stock, or a new menu
    row = town._shops_seen()["1121|potion merchant magnus"]
    assert row == {"sells": ["large red potion"], "lacks": []}


# ============================================================ sell
def test_sell_unknown_item_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: []))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: None))
    with pytest.raises(Stopped):
        town.sell(as_bot(TownBot()), "Thing")


def test_sell_nothing_in_the_bag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")]))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: "fish_buyer"))
    assert town.sell(as_bot(TownBot()), "Milkfish") == 0


def test_sell_to_the_best_buyer(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")]))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: "fish_buyer"))
    tb = TownBot(bag=[{"name": "Milkfish", "count": 4}])
    tb.dialog = lambda: {"open": 1, "top": menu(opt("101 Milkfish; Unit price 670 arnebia"))}
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.remove("Milkfish", quantity or 0) or True))
    assert town.sell(as_bot(tb), "Milkfish") == 4
    assert any("sold 4" in line for line in tb.lines)


def test_sell_opens_a_sell_page(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")]))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: "fish_buyer"))
    tb = TownBot(bag=[{"name": "Milkfish", "count": 2}])
    dialogs = iter([{"open": 1, "top": menu(opt("welcome"))},
                    {"open": 1, "top": menu(opt("Milkfish"))}])
    tb.dialog = lambda: next(dialogs)
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, lambda b, pattern: True))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, lambda b, pattern, quantity: tb.remove("Milkfish", quantity or 0) or True))
    assert town.sell(as_bot(tb), "Milkfish", 2) == 2


def test_sell_when_the_buyer_does_not_list_it(monkeypatch: pytest.MonkeyPatch, no_visit: None) -> None:
    monkeypatch.setattr(knowledge, "traders", stand_in(knowledge.traders, lambda item, they: [(1, "Buyer", 0, 0, 10, "arnebia")]))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: "fish_buyer"))
    b = TownBot(bag=[{"name": "Milkfish", "count": 2}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("nothing"))}
    monkeypatch.setattr(town, "open_section", stand_in(town.open_section, returning(False)))
    assert town.sell(as_bot(b), "Milkfish", 1) == 0
    assert any("isn't on the buyer's list" in line for line in b.spoken)


# ============================================================ restock
def test_restock_tell_only_says_it_once(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Potion", "count": 1}])
    rules = [{"item": "Potion", "min": 5, "stock": 50, "source": "tell", "on": True}]
    assert town.restock(as_bot(b), rules) is False
    assert any("low on Potion" in line for line in b.spoken)
    assert b.supply_told == {"Potion"}
    n = len(b.spoken)
    town.restock(as_bot(b), rules)
    assert len(b.spoken) == n


def test_restock_storage_then_buy_for_the_rest(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "both", "on": True}]
    calls: list[tuple[str, str, int]] = []
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, lambda b, item, n: calls.append(("withdraw", item, n)) or 4))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, lambda b, item, n: calls.append(("buy", item, n)) or 6))
    assert town.restock(as_bot(b), rules) is True
    assert calls == [("withdraw", "Potion", 10), ("buy", "Potion", 6)]


def test_restock_storage_only_never_buys(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "storage", "on": True}]
    calls: list[str] = []
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, lambda b, item, n: calls.append("w") or 10))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, lambda b, item, n: calls.append("b") or 0))
    assert town.restock(as_bot(b), rules) is True
    assert calls == ["w"]


def test_restock_buy_only_never_withdraws(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "buy", "on": True}]
    calls: list[tuple[str, int] | str] = []
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, lambda b, item, n: calls.append("w") or 0))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, lambda b, item, n: calls.append(("b", n)) or 10))
    assert town.restock(as_bot(b), rules) is True
    assert calls == [("b", 10)]


def test_restock_logs_when_still_short(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Potion", "count": 0}])
    rules = [{"item": "Potion", "min": 5, "stock": 10, "source": "both", "on": True}]
    monkeypatch.setattr(town, "withdraw", stand_in(town.withdraw, returning(0)))
    monkeypatch.setattr(town, "buy", stand_in(town.buy, returning(0)))
    assert town.restock(as_bot(b), rules) is True
    assert any("still short after restocking" in line for line in b.spoken)


def test_restock_true_when_a_trip_happens_and_short_when_none() -> None:
    b = TownBot(bag=[{"name": "Potion", "count": 50}])
    rules = [{"item": "Potion", "min": 5, "stock": 100, "source": "both", "on": True}]
    assert town.restock(as_bot(b), rules) is False


# ============================================================ shard_catalog
def test_shard_catalog_reads_the_data_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / "shards.json").write_text(
        json.dumps({"shards": {"A": {"price": 1, "makes": "B"}}, "markets": {"5": [1, 2]}}),
        encoding="utf-8")
    assert town.shard_catalog() == ({"A": {"price": 1, "makes": "B"}}, {"5": [1, 2]})


def test_shard_catalog_missing_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    assert town.shard_catalog() == ({}, {})


def test_shard_catalog_broken_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "DATA", str(tmp_path))
    (tmp_path / "shards.json").write_text("{not json", encoding="utf-8")
    assert town.shard_catalog() == ({}, {})


# ============================================================ market_candidates
def test_market_candidates_uses_the_nearest_survey_npc(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": [
        {"map": 5, "name": "Shard Broker", "x": 1, "y": 1, "role": "shard_market",
         "sources": ["survey x"]},
        {"map": 5, "name": "Other", "x": 9, "y": 9, "role": "shard_market", "sources": ["survey x"]},
        {"map": 5, "name": "NoSurvey", "x": 0, "y": 0, "role": "shard_market", "sources": []},
    ]}))
    assert town.market_candidates({"5": [1, 1]}) == [(5, "Shard Broker", 1, 1)]


def test_market_candidates_defaults_to_shard_broker(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": []}))
    assert town.market_candidates({"7": [3, 4]}) == [(7, "Shard Broker", 3, 4)]


# ============================================================ sell_shards
def test_sell_shards_with_none_in_the_bag(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]})))
    assert town.sell_shards(as_bot(TownBot()), ["Level 1 Sapphire Shard"]) == 0


def test_sell_shards_without_a_market(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 3}])
    assert town.sell_shards(as_bot(b), ["Level 1 Sapphire Shard"]) == 0
    assert any("don't know where a Shard Market is" in line for line in b.spoken)


def test_sell_shards_sells_in_steps_of_2000(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]})))
    tb = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 3000}])
    tb.dialog = lambda: {"open": 1,
                        "top": menu(opt("Level 1 Sapphire Shard | Unit price 20 arnebia"))}
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Shard Broker")))
    monkeypatch.setattr(town, "market_candidates", stand_in(town.market_candidates, lambda markets: [(5, "Shard Broker", 1, 1)]))
    steps: list[int | None] = []
    monkeypatch.setattr(town, "pick",
                        stand_in(town.pick, lambda b, pattern, quantity: (steps.append(quantity),
                                                 tb.remove("Level 1 Sapphire Shard", quantity or 0))[1]))
    tb.choose = lambda pattern: {"text": "entry"}
    tb.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    assert town.sell_shards(as_bot(tb), ["Level 1 Sapphire Shard"]) == 1
    assert steps == [2000, 1000]
    assert any("sold 3000" in line for line in tb.lines)


def test_sell_shards_sells_only_the_rule_count(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]})))
    tb = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 10}])
    tb.dialog = lambda: {"open": 1,
                        "top": menu(opt("Level 1 Sapphire Shard | Unit price 20 arnebia"))}
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Shard Broker")))
    monkeypatch.setattr(town, "market_candidates", stand_in(town.market_candidates, lambda markets: [(5, "Shard Broker", 1, 1)]))
    steps: list[int | None] = []
    monkeypatch.setattr(town, "pick",
                        stand_in(town.pick, lambda b, pattern, quantity: (steps.append(quantity),
                                                 tb.remove("Level 1 Sapphire Shard", quantity or 0))[1]))
    tb.choose = lambda pattern: {"text": "entry"}
    tb.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    assert town.sell_shards(as_bot(tb), ["Level 1 Sapphire Shard"],
                            at_most={"level 1 sapphire shard": 3}) == 1
    assert steps == [3]


def test_sell_shards_stops_when_the_pick_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]})))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 5}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("Level 1 Sapphire Shard | Unit price 20"))}
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Shard Broker")))
    monkeypatch.setattr(town, "market_candidates", stand_in(town.market_candidates, lambda markets: [(5, "Shard Broker", 1, 1)]))
    monkeypatch.setattr(town, "pick", stand_in(town.pick, returning(False)))
    b.choose = lambda pattern: {"text": "entry"}
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    assert town.sell_shards(as_bot(b), ["Level 1 Sapphire Shard"]) == 0


def test_sell_shards_when_the_market_does_not_list_it(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {5: [1, 1]})))
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 5}])
    b.dialog = lambda: {"open": 1, "top": menu(opt("nothing to sell"))}
    monkeypatch.setattr(town, "visit", stand_in(town.visit, returning("Shard Broker")))
    monkeypatch.setattr(town, "market_candidates", stand_in(town.market_candidates, lambda markets: [(5, "Shard Broker", 1, 1)]))
    assert town.sell_shards(as_bot(b), ["Level 1 Sapphire Shard"]) == 0
    assert any("didn't list" in line for line in b.lines)


# ============================================================ menu_text / shard synthesis
def test_menu_text_joins_every_entry_text() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": {"rows": [[{"options": ["a"], "text": "t"}],
                                                   [{"raw": "r"}]]}}
    assert town.menu_text(as_bot(b)) == "t | a | r"


def test_menu_text_empty_without_a_menu() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    assert town.menu_text(as_bot(b)) == ""


def test_open_shard_synthesis_enters_the_submenu() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("shard synthesis"))}
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    assert town._open_shard_synthesis(as_bot(b)) is True
    assert b.choices == [r"^\s*shard synthesis"]


def test_open_shard_synthesis_already_in_the_list() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 1, "top": menu(opt("Level 1 Obsidian Shard42"))}
    assert town._open_shard_synthesis(as_bot(b)) is True
    assert b.choices == []


def test_open_shard_synthesis_uses_the_opener() -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    calls: list[int] = []
    assert town._open_shard_synthesis(as_bot(b), opener=lambda: calls.append(1)) is False
    assert len(calls) == 2 and b.closed == 2


def test_open_shard_synthesis_defaults_to_the_craftsman(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot()
    b.dialog = lambda: {"open": 0, "top": None}
    visited: list[str | None] = []
    monkeypatch.setattr(town, "visit", stand_in(town.visit, lambda b, role: visited.append(role)))
    assert town._open_shard_synthesis(as_bot(b)) is False
    assert visited == ["gem_craftsman", "gem_craftsman"]


# ============================================================ make_gems
def test_make_gems_with_nothing_to_do() -> None:
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 3}])   # fewer than four
    assert town.make_gems(as_bot(b), {"Level 1 Obsidian Shard": 5}) == 0
    assert town.make_gems(as_bot(TownBot()), {"Ruby": 9}) == 0


def test_make_gems_makes_a_gem(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100000}])
    monkeypatch.setattr(town, "_open_shard_synthesis", stand_in(town._open_shard_synthesis, lambda b, opener=None: True))
    b.dialog = lambda: {"open": 1, "top": menu(
        opt("Level 1 Obsidian Shard 8"),
        opt("synthesize 1 Level 1 Obsidian needs 4 Level 1 Obsidian Shard; fee 100 arnebia"))}
    b.choose = lambda pattern: {"text": "row"}
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    typed: list[int] = []

    def type_number(bot: TownBot, n: int) -> bool:
        typed.append(n)
        b.add("Level 1 Obsidian", n)
        return True
    monkeypatch.setattr(town, "type_number", type_number)
    assert town.make_gems(as_bot(b), {"Level 1 Obsidian Shard": 8}) == 2
    assert typed == [2]
    assert b.p["gem_fees"]["level 1 obsidian"] == 100
    assert any("made 2 Level 1 Obsidian" in line for line in b.lines)


def test_make_gems_stops_when_arnebia_is_short(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100}])
    monkeypatch.setattr(town, "_open_shard_synthesis", stand_in(town._open_shard_synthesis, lambda b, opener=None: True))
    b.dialog = lambda: {"open": 1, "top": menu(
        opt("synthesize 1 Level 1 Obsidian needs 4 Level 1 Obsidian Shard; fee 100000 arnebia"))}
    b.choose = lambda pattern: {"text": "row"}
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    typed: list[int] = []
    monkeypatch.setattr(town, "type_number", stand_in(town.type_number, lambda b, n: typed.append(n) or True))
    assert town.make_gems(as_bot(b), {"Level 1 Obsidian Shard": 8}) == 0
    assert typed == []
    assert any("not enough spare arnebia" in line for line in b.spoken)


def test_make_gems_asks_which_level(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100000}])
    monkeypatch.setattr(town, "_open_shard_synthesis", stand_in(town._open_shard_synthesis, lambda b, opener=None: True))
    tb.dialog = lambda: {"open": 1, "top": menu(
        opt("synthesize Level 1 Obsidian"),
        opt("synthesize 1 Level 1 Obsidian needs 4 Level 1 Obsidian Shard; fee 100 arnebia"))}
    chosen: list[str] = []
    tb.choose = lambda pattern: chosen.append(pattern) or {"text": "row"}
    tb.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    monkeypatch.setattr(town, "type_number",
                        stand_in(town.type_number, lambda b, n: (tb.add("Level 1 Obsidian", n), True)[1]))
    assert town.make_gems(as_bot(tb), {"Level 1 Obsidian Shard": 8}) == 2
    assert any("synthesize" in c for c in chosen)


def test_make_gems_stops_when_synthesis_wont_open(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8}])
    monkeypatch.setattr(town, "_open_shard_synthesis", stand_in(town._open_shard_synthesis, lambda b, opener=None: False))
    assert town.make_gems(as_bot(b), {"Level 1 Obsidian Shard": 8}) == 0
    assert any("couldn't open the Gem Craftsman" in line for line in b.spoken)


def test_make_gems_when_the_craftsman_does_not_list_the_shard(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Level 1 Obsidian Shard", "count": 8},
                     {"name": "Arnebia", "count": 100000}])
    monkeypatch.setattr(town, "_open_shard_synthesis", stand_in(town._open_shard_synthesis, lambda b, opener=None: True))
    b.choose = lambda pattern: None
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1}
    assert town.make_gems(as_bot(b), {"Level 1 Obsidian Shard": 8}) == 0
    assert any("didn't list Level 1 Obsidian Shard" in line for line in b.lines)


# ============================================================ _load
def test_load_reads_the_sheet() -> None:
    b = TownBot()
    b.refresh_sheet = lambda force=False: {"load": 42, "maxload": 100}
    assert town.load_now(as_bot(b)) == 42


def test_load_none_without_a_limit() -> None:
    b = TownBot()
    b.refresh_sheet = lambda force=False: {"load": 42, "maxload": 0}
    assert town.load_now(as_bot(b)) is None


def test_load_none_when_the_sheet_is_missing() -> None:
    b = TownBot()
    b.refresh_sheet = lambda force=False: None
    assert town.load_now(as_bot(b)) is None


# ============================================================ unload
def _unload_bot(bag: list[dict[str, int | str]], profile: dict[str, Any] | None=None) -> TownBot:
    b = TownBot(bag=bag, profile=profile)
    b.refresh_sheet = lambda force=False: {"load": 100, "maxload": 100}
    return b


def test_unload_with_no_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Rock", "count": 5}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([])))
    assert town.unload(as_bot(b)) == 0
    assert any("no rule says to store" in line for line in b.spoken)


def test_unload_passes_the_bag_and_gained_to_the_planner(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Junk", "count": 5}], {"scan": {"hotkeys": [{"item": "Rock"}]}})
    b.bag_start = {"junk": 2}        # unload expects bag_start already lower-cased (counts() keys)
    captured: dict[str, object] = {}

    def plan(p: dict[str, Any], bag: dict[str, int], hot: Sequence[str] = (),
             gained: dict[str, int] | None = None) -> list[tuple[str, str, int]]:
        captured.update(bag=bag, hot=hot, gained=gained)
        return []
    monkeypatch.setattr(bagrules, "plan", plan)
    assert town.unload(as_bot(b)) == 0
    assert captured["bag"] == {"Junk": 5}
    assert captured["hot"] == ["Rock"]
    assert captured["gained"] == {"junk": 3}


def test_unload_sells_shards_at_the_market(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Level 1 Sapphire Shard", "count": 5}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Level 1 Sapphire Shard", "sell", 5)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, lambda name, load_before, load_after, n: 2.0))
    monkeypatch.setattr(town, "shard_catalog",
                        stand_in(town.shard_catalog, lambda: ({"Level 1 Sapphire Shard": {"price": 20, "makes": "x"}}, {})))
    sold: list[tuple[str, int]] = []
    monkeypatch.setattr(town, "_sell_one_shard", stand_in(town._sell_one_shard, lambda b, name, n: sold.append((name, n)) or 5))
    assert town.unload(as_bot(b)) == 1
    assert sold == [("Level 1 Sapphire Shard", 5)]
    assert any("weighs 2.0 each" in line for line in b.lines)


def test_unload_sells_other_things_to_a_buyer(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Long-age Turtle", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Long-age Turtle", "sell", 2)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(3.0)))
    monkeypatch.setattr(bagrules, "buyer", stand_in(bagrules.buyer, lambda name: ("Buyer", 850, "arnebia")))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: None))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    calls: list[tuple[str, int | None]] = []
    monkeypatch.setattr(town, "sell", stand_in(town.sell, lambda b, item, n: calls.append((item, n)) or 2))
    assert town.unload(as_bot(b)) == 1
    assert calls == [("Long-age Turtle", 2)]


def test_unload_stores_what_nobody_buys(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Odd Rock", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Odd Rock", "sell", 2)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(3.0)))
    monkeypatch.setattr(bagrules, "buyer", stand_in(bagrules.buyer, lambda name: None))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: None))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    stored: list[tuple[str, int | None]] = []
    monkeypatch.setattr(town, "deposit", stand_in(town.deposit, lambda b, item, n: stored.append((item, n)) or 2))
    assert town.unload(as_bot(b)) == 1
    assert stored == [("Odd Rock", 2)]
    assert any("nobody I know buys Odd Rock" in line for line in b.lines)


def test_unload_stores_what_the_rules_say_to_store(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Pelt", "store", 3)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    stored: list[tuple[str, int | None]] = []
    monkeypatch.setattr(town, "deposit", stand_in(town.deposit, lambda b, item, n: stored.append((item, n)) or 3))
    assert town.unload(as_bot(b)) == 1
    assert stored == [("Pelt", 3)]


def test_unload_throws_away_last(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Junk", "count": 4}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Junk", "throw", 4)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))
    thrown: list[tuple[str, int]] = []
    b.throw_item = lambda name, n: thrown.append((name, n)) or 4
    assert town.unload(as_bot(b)) == 1
    assert thrown == [("Junk", 4)]


def test_unload_falls_back_to_storing_when_the_sale_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Thing", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Thing", "sell", 2)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(bagrules, "buyer", stand_in(bagrules.buyer, lambda name: ("Buyer", 1, "arnebia")))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: None))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))

    def boom(bot: TownBot, name: str, n: int | None = None) -> int:
        raise Stopped("nope")
    monkeypatch.setattr(town, "sell", boom)
    stored: list[tuple[str, int | None]] = []
    monkeypatch.setattr(town, "deposit", stand_in(town.deposit, lambda b, item, n: stored.append((item, n)) or 2))
    assert town.unload(as_bot(b)) == 1
    assert stored == [("Thing", 2)]
    assert any("couldn't sell Thing" in line for line in b.lines)


def test_unload_reraises_a_sale_stop_when_the_run_is_ending(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Thing", "count": 2}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Thing", "sell", 2)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(bagrules, "buyer", stand_in(bagrules.buyer, lambda name: ("Buyer", 1, "arnebia")))
    monkeypatch.setattr(knowledge, "buyer_role", stand_in(knowledge.buyer_role, lambda name: None))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))

    def boom(bot: TownBot, name: str, n: int | None = None) -> int:
        raise Stopped("nope")
    monkeypatch.setattr(town, "sell", boom)
    b.stop_event.set()
    with pytest.raises(Stopped):
        town.unload(as_bot(b))


def test_unload_reraises_a_store_stop_when_the_run_is_ending(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Pelt", "store", 3)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))

    def boom(bot: TownBot, name: str, n: int | None = None) -> int:
        raise Stopped("nope")
    monkeypatch.setattr(town, "deposit", boom)
    b.stop_event.set()
    with pytest.raises(Stopped):
        town.unload(as_bot(b))


def test_unload_logs_a_store_stop_when_the_run_goes_on(monkeypatch: pytest.MonkeyPatch) -> None:
    b = _unload_bot([{"name": "Pelt", "count": 3}])
    monkeypatch.setattr(bagrules, "plan", stand_in(bagrules.plan, returning([("Pelt", "store", 3)])))
    monkeypatch.setattr(bagrules, "learn_weight", stand_in(bagrules.learn_weight, returning(1.0)))
    monkeypatch.setattr(town, "shard_catalog", stand_in(town.shard_catalog, lambda: ({}, {})))

    def boom(bot: TownBot, name: str, n: int | None = None) -> int:
        raise Stopped("nope")
    monkeypatch.setattr(town, "deposit", boom)
    assert town.unload(as_bot(b)) == 0
    assert any("couldn't store Pelt" in line for line in b.lines)


# ============================================================ _sell_one_shard
def test_sell_one_shard_reports_how_many_left(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TownBot(bag=[{"name": "Level 1 Sapphire Shard", "count": 5}])

    def fake(bot: TownBot, names: Iterable[str], at_most: dict[str, int] | None = None) -> int:
        bot.bag = [{"name": "Level 1 Sapphire Shard", "count": 2}]
        return 1
    monkeypatch.setattr(town, "sell_shards", fake)
    assert town._sell_one_shard(as_bot(b), "Level 1 Sapphire Shard", 5) == 3
