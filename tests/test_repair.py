"""Keeping the gear repaired: reading the agent's wear warnings, choosing the cheapest toolbox
that fits the character's level, and the branches of the repair dialog."""

from __future__ import annotations

import pytest
from conftest import as_bot, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import settings
from unclefarmer.engine import repair
from unclefarmer.engine.botparts.base import Stopped
from typing import Any


class Link:
    def __init__(self, equipment: list[dict[str, Any]] | None = None, wear: list[dict[str, Any]] | None = None) -> None:
        self.equipment = [dict(r) for r in (equipment or [])]
        self.wear = wear                    # None: an agent with no `wear` support
        self.wear_seen = 0

    def json(self, line: str) -> Any:
        if line == "mem equipment":
            return [dict(r) for r in self.equipment]
        if line.startswith("wear "):
            if self.wear is None:
                return {}
            since = int(line.split()[1])
            return {"wear": [dict(w) for w in self.wear if w["t"] > since]}
        return {}


class Bot:
    def __init__(self, link: Link, p: dict[str, Any] | None = None) -> None:
        self.repairing = False
        self.a = link
        self.gear = repair.Gear()
        self.p = dict(settings.PROFILE_DEFAULTS, **(p or {}))
        self.held: set[str] = set()
        self.in_fight = False
        self.last_me = {}
        self.bag: list[dict[str, Any]] = []
        self.hotkeys: dict[str, str] = {}
        self.lines: list[str] = []
        self.keys: list[str] = []
        self.dialog_out = {"open": False}
        self.wait_dialog_out: dict[str, Any] | None = None
        self.sleeps: list[float] = []
        self.finished = False
        self.used = None
        self.chosen: list[str] = []
        self.choose_ret: dict[str, Any] | None = {"text": "picked"}
        self.closed = 0
        self.notes: list[str] = []

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def bag_rows(self) -> list[dict[str, Any]]:
        return [dict(r) for r in self.bag]

    def hotkey_of(self, name: str) -> str | None:
        return self.hotkeys.get(name.lower())

    def dialog(self) -> dict[str, Any]:
        return self.dialog_out

    def key(self, k: str) -> None:
        self.keys.append(k)

    def use_item(self, name: str) -> bool:
        self.used = name
        return True

    def wait_dialog(self, t: float) -> dict[str, Any] | None:
        return self.wait_dialog_out

    def entry_texts(self, e: dict[str, Any]) -> list[str]:
        return [e] if isinstance(e, str) else list(e)

    def note_dialog(self, d: dict[str, Any], why: str) -> None:
        self.notes.append(why)

    def close_dialogs(self) -> None:
        self.closed += 1

    def choose(self, pat: str) -> dict[str, Any] | None:
        self.chosen.append(pat)
        return self.choose_ret

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)

    def finish(self) -> None:
        self.finished = True


def dlg(*texts: str) -> dict[str, Any]:
    return {"open": True, "top": {"rows": [[t] for t in texts]}}


# ---------------------------------------------------------------- Gear defaults
def test_gear_starts_clean() -> None:
    g = repair.Gear()
    assert g.wear_seen == 0 and g.warned == {} and g.repaired_at == 0.0
    assert g.said_no_tool is g.said_old_agent is g.said_wait is False


# ---------------------------------------------------------------- equipment / broken
def test_equipment_returns_rows_or_empty() -> None:
    assert repair.equipment(as_bot(Bot(Link([{"name": "Sword", "equipped": 1}])))) == [{"name": "Sword", "equipped": 1}]
    assert repair.equipment(as_bot(Bot(Link()))) == []


def test_equipment_non_list_is_empty() -> None:
    b = Bot(Link())
    b.a.json = lambda line: {"not": "a list"}
    assert repair.equipment(as_bot(b)) == []


def test_broken_lists_negative_equipped_and_question_mark() -> None:
    b = Bot(Link([{"name": "Sword", "equipped": -1}, {"name": "Shield", "equipped": 3}, {"equipped": -1}]))
    assert repair.broken(as_bot(b)) == ["Sword", "?"]


# ---------------------------------------------------------------- read_wear
def test_read_wear_false_on_an_old_agent() -> None:
    b = Bot(Link([{"uid": 1, "name": "Sword"}], wear=None))
    assert repair.read_wear(as_bot(b), b.gear) is False


def test_read_wear_records_low_and_broken_and_skips_foreign_items() -> None:
    equip = [{"uid": 1, "name": "Sword"}, {"uid": 2, "name": "Shield"}]
    wear = [{"uid": 1, "t": 20000, "broken": False},
            {"uid": 2, "t": 21000, "broken": True},
            {"uid": 9, "t": 22000, "broken": False}]       # not ours
    b = Bot(Link(equip, wear))
    assert repair.read_wear(as_bot(b), b.gear) is True
    assert b.gear.warned == {1: "low", 2: "broken"}
    assert b.gear.wear_seen == 22000
    assert "Sword is about to break" in b.lines
    assert "Shield is broken" in b.lines


def test_read_wear_ignores_a_late_low_warning_after_a_repair() -> None:
    b = Bot(Link([{"uid": 1, "name": "Sword"}], [{"uid": 1, "t": 5000, "broken": False}]))
    b.gear.repaired_at = 100.0                              # warning at agent 5 s, right after the repair
    assert repair.read_wear(as_bot(b), b.gear) is True
    assert b.gear.warned == {} and b.lines == []


def test_read_wear_does_not_repeat_the_same_state() -> None:
    wear = [{"uid": 1, "t": 20000, "broken": False}]
    b = Bot(Link([{"uid": 1, "name": "Sword"}], wear))
    repair.read_wear(as_bot(b), b.gear)
    repair.read_wear(as_bot(b), b.gear)                             # same warning again
    assert b.lines.count("Sword is about to break") == 1


# ---------------------------------------------------------------- level / toolboxes
def test_level_from_the_sheet_then_the_scan() -> None:
    b = Bot(Link())
    b.last_me = {"sheet": {"level": 55}}
    b.p["scan"] = {"level": 10}
    assert repair.level(as_bot(b)) == 55
    b.last_me = {}
    assert repair.level(as_bot(b)) == 10
    b.p["scan"] = {}
    assert repair.level(as_bot(b)) == 0


def test_toolboxes_orders_by_fit_then_hotkey() -> None:
    b = Bot(Link())
    b.last_me = {"sheet": {"level": 50}}
    b.bag = [{"name": "Small Toolbox", "count": 1},
             {"name": "Large Toolbox", "count": 2},
             {"name": "Alloy Toolbox", "count": 1}]
    b.hotkeys = {"large toolbox": "3"}
    assert repair.toolboxes(as_bot(b)) == [("Large Toolbox", "3", 2),
                                   ("Small Toolbox", None, 1),
                                   ("Alloy Toolbox", None, 1)]


def test_toolboxes_puts_non_fitting_last_and_strongest_first() -> None:
    b = Bot(Link())
    b.last_me = {"sheet": {"level": 90}}
    b.bag = [{"name": "Small Toolbox", "count": 1},
             {"name": "Large Toolbox", "count": 2},
             {"name": "Alloy Toolbox", "count": 1}]
    assert repair.toolboxes(as_bot(b)) == [("Alloy Toolbox", None, 1),
                                   ("Large Toolbox", None, 2),
                                   ("Small Toolbox", None, 1)]


def test_toolboxes_no_level_fits_everything_and_sums_rows() -> None:
    b = Bot(Link())
    b.bag = [{"name": "Small Toolbox", "count": 1},
             {"name": " small toolbox ", "count": 4},        # same item, cased/whitespaced
             {"name": "Pliers", "count": 9},
             {"name": "Large Toolbox", "count": 0}]          # none left
    # observed: names are stripped, and rows of the same item are summed
    assert repair.toolboxes(as_bot(b)) == [("small toolbox", None, 5)]


# ---------------------------------------------------------------- repair branches
def test_repair_succeeds_with_a_hotkey_toolbox() -> None:
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": 1}], None))
    b.hotkeys = {"small toolbox": "4"}
    b.bag = [{"name": "Small Toolbox", "count": 2}]
    b.wait_dialog_out = dlg("Do you want to repair all the equipment that is already equipped?")
    b.gear.warned = {1: "low"}
    assert repair.repair(as_bot(b)) is True
    assert b.keys == ["4"] and b.gear.warned == {} and b.gear.repaired_at > 0
    assert b.notes == ["repair with Small Toolbox"]
    assert b.finished is False


def test_repair_no_toolbox_logs_once() -> None:
    b = Bot(Link())
    assert repair.repair(as_bot(b)) is False
    assert b.gear.said_no_tool is True and b.gear.failed_at > 0
    assert any("no toolbox" in line for line in b.lines)


def test_repair_no_toolbox_with_broken_gear_finishes_and_stops() -> None:
    b = Bot(Link())
    b.in_fight = False
    with pytest.raises(Stopped):
        repair.repair(as_bot(b), gone=["Sword"])
    assert b.finished is True


def test_repair_broken_gear_in_a_fight_does_not_stop() -> None:
    b = Bot(Link())
    b.in_fight = True
    assert repair.repair(as_bot(b), gone=["Sword"]) is False
    assert b.finished is False


def test_repair_bag_only_toolbox_waits_out_a_fight() -> None:
    b = Bot(Link())
    b.in_fight = True
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.gear.warned = {1: "low"}
    assert repair.repair(as_bot(b)) is False
    assert b.gear.said_wait is True
    assert any("only in the bag" in line for line in b.lines)


def test_repair_bag_toolbox_used_between_fights() -> None:
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": 1}], None))
    b.in_fight = False
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = dlg("Do you want to repair all the equipment that is already equipped?")
    assert repair.repair(as_bot(b)) is True
    assert b.used == "Small Toolbox"


def test_repair_declines_when_a_dialog_is_open() -> None:
    b = Bot(Link())
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.dialog_out = {"open": True}
    assert repair.repair(as_bot(b)) is False
    assert b.gear.failed_at == 0.0


def test_repair_fails_when_no_question_opens() -> None:
    b = Bot(Link())
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = None
    assert repair.repair(as_bot(b)) is False
    assert any("didn't open its question" in line for line in b.lines)


def test_repair_nothing_to_do_clears_the_warning() -> None:
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": 1}], None))
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = dlg("No repairable equipment")
    b.gear.warned = {1: "low"}
    assert repair.repair(as_bot(b)) is False
    assert b.gear.warned == {} and b.gear.repaired_at > 0


def test_repair_too_high_fails() -> None:
    b = Bot(Link())
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = dlg("Level 61 equipment is too high to be repaired")
    assert repair.repair(as_bot(b)) is False
    assert any("can't repair this gear" in line for line in b.lines)


def test_repair_choose_confirmation_returns_none() -> None:
    b = Bot(Link())
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = dlg("Do you want to repair all the equipment that is already equipped?")
    b.choose_ret = None
    assert repair.repair(as_bot(b)) is False
    assert b.gear.failed_at > 0


def test_repair_still_broken_warns() -> None:
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": -1}], None))
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = dlg("Do you want to repair all the equipment that is already equipped?")
    assert repair.repair(as_bot(b)) is False
    assert any("still broken after the repair" in line for line in b.lines)


# ---------------------------------------------------------------- check
def test_check_off_leaves_gear_alone() -> None:
    b = Bot(Link())
    b.p["repair_with"] = "off"
    assert repair.check(as_bot(b)) is False


def test_check_skips_while_holding_keys_or_repairing() -> None:
    b = Bot(Link())
    b.held = {"w"}
    assert repair.check(as_bot(b)) is False
    b.held = set()
    b.repairing = True
    assert repair.check(as_bot(b)) is False


def test_check_throttled_within_check_every(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(Link())
    b.gear.checked_at = clock.now
    assert repair.check(as_bot(b)) is False


def test_check_reports_an_old_agent_once(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(Link([], wear=None))
    assert repair.check(as_bot(b)) is False
    assert b.gear.said_old_agent is True
    assert sum("can't warn before gear breaks" in line for line in b.lines) == 1
    clock.advance(3)
    assert repair.check(as_bot(b)) is False            # gone again: not logged twice
    assert sum("can't warn before gear breaks" in line for line in b.lines) == 1


def test_check_repairs_on_a_warning(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": 1}],
                 [{"uid": 1, "t": 20000, "broken": False}]))
    b.hotkeys = {"small toolbox": "4"}
    b.bag = [{"name": "Small Toolbox", "count": 1}]
    b.wait_dialog_out = dlg("Do you want to repair all the equipment that is already equipped?")
    assert repair.check(as_bot(b)) is True
    assert b.gear.repaired_at == clock.now
    # right after a repair, another warning does not trigger a second repair
    b.a.wear = [{"uid": 1, "t": 30000, "broken": False}]
    clock.advance(2.5)
    assert repair.check(as_bot(b)) is False


def test_check_repairs_again_after_the_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": 1}],
                 [{"uid": 1, "t": 20000, "broken": False}]))
    b.hotkeys = {"small toolbox": "4"}
    b.bag = [{"name": "Small Toolbox", "count": 2}]
    b.wait_dialog_out = dlg("Do you want to repair all the equipment that is already equipped?")
    assert repair.check(as_bot(b)) is True
    clock.advance(repair.AFTER_REPAIR + 1)
    b.a.wear = [{"uid": 1, "t": 1_020_000, "broken": False}]   # agent clock well past the repair
    assert repair.check(as_bot(b)) is True


def test_check_waits_after_a_failed_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(Link([{"uid": 1, "name": "Sword", "equipped": 1}],
                 [{"uid": 1, "t": 20000, "broken": False}]))
    b.gear.failed_at = clock.now
    assert repair.check(as_bot(b)) is False
    assert b.gear.warned == {} or True         # no repair attempted
    assert b.gear.repaired_at == 0.0
