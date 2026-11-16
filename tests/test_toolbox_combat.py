# pyright: reportPrivateUsage=false
"""Toolbox repair across attacks and late questions; no live server behavior is assumed.

Reuse the repair and health fixtures with real health, repair, wait, sleep,
check, popup cleanup and fight-look methods. Only game responses are simulated.
"""
from __future__ import annotations

import threading
from typing import Any

import pytest

from conftest import Clock, as_bot, returning
from test_health import H
from test_toolbox_tiers import ASK_80, GameBot
from test_fighting import monster
from test_repair import Link as RepairLink
from unclefarmer.clock import Flag, StopFlag
from unclefarmer.link import GameLink
from unclefarmer.engine.botparts import fighting
from unclefarmer.engine.botparts.dialogs import DialogsMixin
from unclefarmer.engine.botparts.health import HealthMixin
from unclefarmer.engine.botparts.keys import KeysMixin
from unclefarmer.engine.botparts.reading import ReadingMixin


class ScenarioLink(RepairLink):
    def __init__(self, b: ToolboxScenario, previous: RepairLink) -> None:
        super().__init__(previous.equipment, previous.wear)
        self.owner = b
        self.input_state: dict[str, Any] = {"ok": True, "open": 0}
        self.sent: list[str] = []

    def json(self, line: str) -> Any:
        return dict(self.input_state) if line == "input" else super().json(line)

    def send(self, line: str) -> str:
        self.sent.append(line)
        if line == "typeesc":
            self.owner.record("agent typeesc")
            self.input_state = {"ok": True, "open": 0}
            return "escaped"
        assert line.startswith("key "), line
        return "tapped"


class ToolboxScenario(GameBot, H):
    CHECK_EVERY = fighting.FightingMixin.CHECK_EVERY

    def __init__(self, delay: float, hotkey: bool, in_fight: bool, attackers: int) -> None:
        H.__init__(self)
        GameBot.__init__(self, 75, {"Large Toolbox": 2},
                         hotkeys={"Large Toolbox": "6"} if hotkey else {})
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.paused = threading.Event()
        self.daily_stop_at = 0.0
        self.daily_done = False
        self.p.update(hp_key="1", hp_potion="Large Red Potion", hp_below=55, hp_cooldown=11,
                      mp_use=False, stop_when_out_of_potions=False, close_popups=True)
        self.in_fight = in_fight
        self.attacker_ids = {-n - 5 for n in range(attackers)}
        self.hp = 61753
        self.top_hp = 120508
        self.next_damage = self.clock.time() + 0.1
        self.delay = delay
        self.requested_at: float | None = None
        self.answered = False
        self.cancelled = False
        self.shown = False
        self.last_popup_check = 0.0
        self.trace: list[tuple[float, str]] = []
        self.damage_count = 0
        self.external_escape_at: float | None = None
        self.gear.warned = {1: "broken"}
        self.scenario_link = ScenarioLink(self, self.link)
        self.link = self.scenario_link
        self.a = self.scenario_link

    @property
    def _stop(self) -> StopFlag:
        return self.stop_event

    @property
    def _pause(self) -> Flag:
        return self.paused

    @property
    def _link(self) -> GameLink:
        return as_bot(self).a

    def press_slot(self, k: str) -> bool:
        return False  # these item hotkeys and Escape are not attack-slot keys

    def wait_keys_free(self) -> None:
        ReadingMixin.wait_keys_free(as_bot(self))

    def record(self, event: str) -> None:
        self.trace.append((round(self.clock.time(), 4), event))

    def log(self, line: str, speak: bool = False) -> None:
        super().log(line, speak)
        if "didn't open its question" in line:
            self.record("repair timeout")

    def check(self) -> None:
        ReadingMixin.check(as_bot(self))

    def check_game(self, force: bool = False) -> None:
        # Simulate incoming damage while the real polling sleep advances time.
        if self.attacker_ids and self.clock.time() >= self.next_damage:
            self.hp -= 100 * len(self.attacker_ids)
            self.damage_count += 1
            self.next_damage = self.clock.time() + 0.1
            self.record("damage")
        if self.external_escape_at is not None and self.clock.time() >= self.external_escape_at:
            self.external_escape_at = None
            self.record("injected external Escape")
            self.key("esc")

    def sleep(self, s: float) -> None:  # pyright: ignore[reportIncompatibleMethodOverride] (repair/health fixtures name this argument differently)
        ReadingMixin.sleep(as_bot(self), s)

    def me(self) -> dict[str, Any]:
        return {"id": 1001, "x": 100.0, "y": 100.0, "hp": self.hp, "maxhp": self.top_hp}

    def snap(self) -> dict[str, Any]:
        return {"map": {"id": 1561}, "self": self.me(),
                "entities": [monster(mid, 101.0, 100.0) for mid in self.attacker_ids]}

    def attackers(self) -> set[int]:
        return set(self.attacker_ids)

    def look_after(self, me: dict[str, Any] | None = None) -> dict[str, Any]:
        self.record("look_after")
        return HealthMixin.look_after(as_bot(self), me)

    def key(self, k: str, ms: int | None = None) -> None:
        KeysMixin.key(as_bot(self), k, 60 if ms is None else ms)
        self.record("key " + k)
        if k == "esc":
            self.keys.append(k)
            self.cancelled = True
        elif k == "1":
            self.keys.append(k)
            self.hp += 20000
        else:
            GameBot.key(self, k)
            self.requested_at = self.clock.time()

    def use_item(self, name: str) -> bool:
        self.record("use " + name)
        self.requested_at = self.clock.time()
        return GameBot.use_item(self, name)

    def dialog(self) -> dict[str, Any]:
        if (self.requested_at is None or self.answered or self.cancelled
                or self.clock.time() < self.requested_at + self.delay):
            return {"open": 0, "top": None}
        if not self.shown:
            self.shown = True
            self.record("question visible")
        return {"open": 1, "top": {"rows": [[{"options": [text]}] for text in (ASK_80, "Confirm", "cancel")],
                                   "row": 0, "col": 0}}

    def entry_texts(self, e: dict[str, Any]) -> list[str]:  # pyright: ignore[reportIncompatibleMethodOverride] (repair fixture uses an instance method; Parts declares it static)
        return DialogsMixin.entry_texts(e)

    def wait_dialog(self, t: float) -> dict[str, Any] | None:  # pyright: ignore[reportIncompatibleMethodOverride] (repair fixture's timeout parameter is t)
        return DialogsMixin.wait_dialog(as_bot(self), t)

    def choose(self, pat: str) -> dict[str, Any] | None:  # pyright: ignore[reportIncompatibleMethodOverride] (repair fixture's pattern parameter is pat)
        assert DialogsMixin.find_entry(as_bot(self), self.dialog()["top"], pat) is not None
        self.record("confirm repair")
        entry = GameBot.choose(self, pat)
        self.answered = True
        return entry

    def close_dialogs(self) -> None:
        self.record("close_dialogs")
        DialogsMixin.close_dialogs(as_bot(self))

    def clear_popups(self, force: bool = False) -> bool:
        self.record("clear_popups")
        return DialogsMixin.clear_popups(as_bot(self), force)


@pytest.mark.parametrize("attackers", [0, 1, 3])
@pytest.mark.parametrize("in_fight", [False, True])
@pytest.mark.parametrize("hotkey", [False, True])
def test_fast_toolbox_question_is_not_escaped_by_damage_or_polling(
    attackers: int, in_fight: bool, hotkey: bool,
) -> None:
    b = ToolboxScenario(0.2, hotkey, in_fight, attackers)
    b.look_after()
    if in_fight and not hotkey:
        assert b.uses == [] and b.counts["Large Toolbox"] == 2
        assert b.gear.said_wait and b.gear.failed_at == 0
        b.in_fight = False
        b.sleep(2.1)
        b.look_after()
    assert b.counts["Large Toolbox"] == 1 and b.link.equipment[0]["equipped"] == 1
    assert len(b.uses) == 1
    assert "esc" not in b.keys
    assert not any(event == "clear_popups" for _t, event in b.trace)
    assert "1" in b.keys  # same preceding HP potion as Prinse's log
    if attackers:
        assert b.damage_count > 0


def fight_look(monkeypatch: pytest.MonkeyPatch, b: ToolboxScenario) -> None:
    """Use the real _fight_look ordering, excluding unrelated pet/points handling."""
    monkeypatch.setattr(fighting.teamheal, "look_after", returning(False))
    monkeypatch.setattr(fighting.pets, "look_after", returning(None))
    monkeypatch.setattr(b, "reach", returning(16.0), raising=False)
    monkeypatch.setattr(b, "follows", returning(False), raising=False)
    monkeypatch.setattr(b, "leash_for", returning(16.0), raising=False)
    monkeypatch.setattr(b, "count_kills", returning([]), raising=False)
    monkeypatch.setattr(b, "read_points", returning(None), raising=False)
    f = fighting._Fight(as_bot(b), monster(-5, 101.0, 100.0))
    fighting.FightingMixin._fight_look(as_bot(b), f, b.clock.time())


@pytest.mark.parametrize("attackers", [1, 3])
def test_fight_cleanup_runs_after_successful_repair(
    monkeypatch: pytest.MonkeyPatch, attackers: int,
) -> None:
    b = ToolboxScenario(0.2, True, True, attackers)
    fight_look(monkeypatch, b)
    events = [event for _t, event in b.trace]
    assert events.index("confirm repair") < events.index("clear_popups")
    assert "esc" not in b.keys and b.counts["Large Toolbox"] == 1


@pytest.mark.parametrize("attackers", [1, 3])
@pytest.mark.parametrize("in_fight", [False, True])
def test_late_question_is_resumed_on_subsequent_fight_look(
    monkeypatch: pytest.MonkeyPatch, attackers: int, in_fight: bool,
) -> None:
    b = ToolboxScenario(3.3, True, in_fight, attackers)
    if in_fight:
        fight_look(monkeypatch, b)
    else:
        b.look_after()
    assert any("didn't open its question - not repaired" in line for line in b.lines)
    assert "esc" not in b.keys and b.chosen == []
    b.sleep(0.5)
    b.in_fight = True
    fight_look(monkeypatch, b)
    assert "esc" not in b.keys and not b.cancelled
    assert b.counts["Large Toolbox"] == 1 and b.link.equipment[0]["equipped"] == 1
    assert len(b.uses) == 1  # resuming never sends a second item-use request
    assert b.gear.repaired_at > 0 and b.gear.failed_at == 0 and not b.gear.warned
    events = [event for _t, event in b.trace]
    assert events.index("repair timeout") < events.index("confirm repair")


def test_popup_race_preserves_late_question_until_repair_check(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ToolboxScenario(3.3, True, True, 1)
    b.look_after()
    b.sleep(0.5)
    assert not b.clear_popups(force=True)
    assert b.dialog()["open"] and "esc" not in b.keys
    fight_look(monkeypatch, b)
    assert b.counts["Large Toolbox"] == 1 and b.gear.pending is None


def test_unrelated_popup_is_not_protected_by_pending_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ToolboxScenario(3.3, True, True, 1)
    b.look_after()
    notice = {"open": 1, "top": {"rows": [[{"text": "An unrelated game notice"}]]}}
    monkeypatch.setattr(b, "dialog", lambda: {"open": 0, "top": None} if b.cancelled else notice)
    assert b.clear_popups(force=True)
    assert "esc" in b.keys and b.gear.pending is not None


def test_pending_repair_ownership_expires(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ToolboxScenario(3.3, True, True, 1)
    b.look_after()
    b.sleep(31)
    assert b.clear_popups(force=True)
    assert b.gear.pending is None and not b.chosen


def test_resuming_a_late_question_does_not_open_the_character_sheet() -> None:
    b = ToolboxScenario(3.3, True, False, 0)
    b.last_me = {}
    b.p["scan"] = {}
    b.look_after()
    assert b.sheet_reads == 1
    b.sleep(0.5)
    b.look_after()
    assert b.sheet_reads == 1 and b.counts["Large Toolbox"] == 1


def test_external_escape_control_can_reproduce_the_timeout() -> None:
    b = ToolboxScenario(0.05, True, True, 1)
    b.external_escape_at = b.clock.time() + 0.1
    b.look_after()
    assert b.cancelled and not b.chosen
    assert any("didn't open its question - not repaired" in line for line in b.lines)
    assert any(event == "injected external Escape" for _t, event in b.trace)
    assert b.counts["Large Toolbox"] == 2


def test_chat_escape_happens_before_toolbox_key_not_after_opening() -> None:
    b = ToolboxScenario(0.2, True, True, 1)
    b.p.update(hp_key="", hp_potion="auto")
    b.scenario_link.input_state = {"ok": True, "open": 1, "mode": 5}
    b.look_after()
    events = [event for _t, event in b.trace]
    assert events.index("agent typeesc") < events.index("key 6") < events.index("confirm repair")
    assert b.scenario_link.sent[0] == "typeesc"
    assert "esc" not in b.keys and b.counts["Large Toolbox"] == 1
