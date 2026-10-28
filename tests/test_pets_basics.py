# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Keeping a pet out and alive: the player's order, cooldowns and refusals, the pet potion at the
right HP and the bag fallback when none is on a hotkey."""

from __future__ import annotations

from conftest import as_bot, stand_in, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import settings
from unclefarmer.engine import pets
import pytest
from typing import Any


class Link:
    def __init__(self, rows: list[Any]) -> None:
        self.rows = rows
        self.accept = True
        self.bagfull: list[dict[str, Any]] = []

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem pets":
            return [dict(r) for r in self.rows]
        if line == "mem bagfull":
            return [dict(r) for r in self.bagfull]
        return {}


class Bot:
    def __init__(self, rows: list[Any]) -> None:
        self.pet_potion_at: dict[str, float] = {}   # when each pet potion was last drunk
        self.a = Link(rows)
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.pets_refused: dict[int, float] = {}
        self.lines: list[str] = []
        self.pet_checked: float = 0
        self.last_pet_hp_key = 0
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.cursor = -1
        self.panel = None
        self.in_fight = False
        self.used: list[str] = []
        self._potion: tuple[str | None, str | None] = (None, None)

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def panel_cursor(self, panel: int, index: int, size: int) -> bool:
        self.cursor, self.panel = index, panel
        return 0 <= index < len(self.a.rows)

    def key(self, k: str) -> None:
        self.keys.append(k)
        if self.a.accept and self.a.rows and 0 <= self.cursor < len(self.a.rows):
            r = self.a.rows[self.cursor]
            r["usable_at"] = 0 if (r["usable_at"] or 0) < 0 else -1

    def sleep(self, s: float) -> None:
        self.sleeps.append(s)

    def potion(self, kind: str) -> tuple[str | None, str | None]:
        return self._potion

    def use_item(self, name: str) -> bool:
        self.used.append(name)
        return True


def rows(*specs: tuple[str, float]) -> list[dict[str, Any]]:
    return [{"id": i + 1, "name": n, "usable_at": u} for i, (n, u) in enumerate(specs)]


# ---------------------------------------------------------------- state / chosen / rank
def test_state_out_cooldown_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(1000.0)
    monkeypatch.setattr(CLOCK, "time", clock)
    assert pets.state({"usable_at": -1}) == "out"
    assert pets.state({"usable_at": 5000}) == "cooldown"
    assert pets.state({"usable_at": 0}) == "ready"
    assert pets.state({"usable_at": 1000.0}) == "ready"      # exactly now is ready


def test_chosen_only_ticked_in_order() -> None:
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}, {"name": "Dog", "use": False}, {"name": "Bird", "use": True}]
    assert pets.chosen(as_bot(b)) == ["Cat", "Bird"]


def test_rank_of_a_ticked_and_an_unticked_pet() -> None:
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}, {"name": "Bird", "use": True}]
    assert pets._rank(as_bot(b), {"name": "Cat"}) == 0
    assert pets._rank(as_bot(b), {"name": "Bird"}) == 1
    assert pets._rank(as_bot(b), {"name": "Wolf"}) == 2              # unticked ranks last


def test_entity_finds_our_pet_in_the_world() -> None:
    b = Bot([])
    snap = {"self": {"name": "Hero"}, "entities": [
        {"name": "Wolf belongs to Enemy"}, {"id": 77, "kind": "player", "name": "Cat Fluffy belongs to Hero"}]}
    found = pets.entity(as_bot(b), snap)
    assert found is not None and found["name"] == "Cat Fluffy belongs to Hero"
    assert pets.entity(as_bot(b), {"self": {"name": "Hero"}, "entities": []}) is None


def test_wanted_picks_the_first_ready_ticked_pet(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(1000.0)
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}, {"name": "Bird", "use": True}]
    rs = rows(("Cat", 5000), ("Bird", 0))                    # Cat cooling down
    picked = pets.wanted(as_bot(b), rs)
    assert picked is not None and picked["name"] == "Bird"
    rs = rows(("Cat", 0), ("Bird", 0))
    picked = pets.wanted(as_bot(b), rs)
    assert picked is not None and picked["name"] == "Cat"


def test_wanted_skips_a_refused_pet(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock(1000.0)
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}]
    rs = rows(("Cat", 0))
    b.pets_refused[1] = clock.now + 50
    assert pets.wanted(as_bot(b), rs) is None


# ---------------------------------------------------------------- keep_out
def test_keep_out_does_nothing_without_a_chosen_pet(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", 0)))
    pets.keep_out(as_bot(b))
    assert b.keys == []


def test_keep_out_is_throttled(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", 0)))
    b.p["pets"] = [{"name": "Cat", "use": True}]
    b.pet_checked = clock.now
    pets.keep_out(as_bot(b))
    assert b.keys == []


def test_keep_out_summons_the_ready_pet(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", 0)))
    b.p["pets"] = [{"name": "Cat", "use": True}]
    pets.keep_out(as_bot(b))
    assert b.panel == 3 and b.keys == [b.p["use_key"]]
    assert any("summoned Cat" in line for line in b.lines)


def test_keep_out_keeps_the_best_one_already_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", -1), ("Bird", 0)))
    b.p["pets"] = [{"name": "Cat", "use": True}, {"name": "Bird", "use": True}]
    pets.keep_out(as_bot(b))
    assert b.keys == []                                      # Cat (rank 0) already out


def test_keep_out_swaps_a_better_pet_in(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", -1), ("Bird", 0)))
    b.p["pets"] = [{"name": "Bird", "use": True}, {"name": "Cat", "use": True}]
    pets.keep_out(as_bot(b))
    assert any("putting Cat away for it" in line for line in b.lines)
    assert any("summoned Bird" in line for line in b.lines)
    assert b.a.rows[0]["usable_at"] == 0                    # Cat put away
    assert b.a.rows[1]["usable_at"] == -1                   # Bird out


def test_keep_out_says_once_when_nothing_is_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", 5000)))
    b.p["pets"] = [{"name": "Cat", "use": True}]
    pets.keep_out(as_bot(b))
    pets.keep_out(as_bot(b))
    assert sum("no pet ready to summon yet" in line for line in b.lines) == 1


def test_keep_out_records_a_refusal(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", 0)))
    b.a.accept = False
    b.p["pets"] = [{"name": "Cat", "use": True}]
    pets.keep_out(as_bot(b))
    assert b.pets_refused[1] == clock.now + pets.REFUSED_FOR
    assert any("didn't come out" in line for line in b.lines)


def test_keep_out_keeps_the_out_pet_if_it_cannot_be_put_away(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", -1), ("Bird", 0)))
    b.p["pets"] = [{"name": "Bird", "use": True}, {"name": "Cat", "use": True}]
    # the first _press (away) fails, then we must not summon
    original = b.panel_cursor
    calls = {"n": 0}

    def cursor(panel: int, index: int, size: int) -> bool:
        calls["n"] += 1
        if calls["n"] == 1:                                  # the "away" press cannot land
            b.cursor, b.panel = index, panel
            return False
        return original(panel, index, size)

    b.panel_cursor = cursor
    pets.keep_out(as_bot(b))
    assert any("couldn't put Cat away" in line for line in b.lines)


# ---------------------------------------------------------------- look_after (pet potion)
def test_look_after_nothing_without_a_chosen_pet_or_pet_entity(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}]
    pets.look_after(as_bot(b), {"self": {"name": "Hero"}, "entities": []})
    assert b.keys == []
    b2 = Bot([])
    pets.look_after(as_bot(b2), {"self": {"name": "Hero"}, "entities": []})
    assert b2.keys == []


def test_look_after_presses_a_pet_potion_when_hp_is_low(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    monkeypatch.setattr(pets.potions, "info", stand_in(pets.potions.info, lambda name: None))
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}]
    b._potion = ("8", "Pet Large Red Potion")
    snap = {"self": {"name": "Hero"}, "entities": [
        {"id": 77, "kind": "player", "name": "Cat Fluffy belongs to Hero", "hp": 300, "maxhp": 1000}]}
    pets.look_after(as_bot(b), snap)
    assert b.keys == ["8"]
    assert b.pet_potion_at["pet_hp"] == clock.now


def test_look_after_leaves_a_healthy_pet_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}]
    b._potion = ("8", "Pet Large Red Potion")
    snap = {"self": {"name": "Hero"}, "entities": [
        {"id": 77, "kind": "player", "name": "Cat Fluffy belongs to Hero", "hp": 900, "maxhp": 1000}]}
    pets.look_after(as_bot(b), snap)
    assert b.keys == []


def test_look_after_waits_between_pet_potions(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    monkeypatch.setattr(pets.potions, "info", stand_in(pets.potions.info, lambda name: None))
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}]
    b._potion = ("8", "Pet Large Red Potion")
    b.pet_potion_at = {"pet_hp": clock.now - 1}             # within the 3 s gap
    snap = {"self": {"name": "Hero"}, "entities": [
        {"id": 77, "kind": "player", "name": "Cat Fluffy belongs to Hero", "hp": 100, "maxhp": 1000}]}
    pets.look_after(as_bot(b), snap)
    assert b.keys == []


def test_look_after_defers_the_bag_potion_in_a_fight(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    monkeypatch.setattr(pets.potions, "info", stand_in(pets.potions.info, lambda name: None))
    b = Bot([])
    b.p["pets"] = [{"name": "Cat", "use": True}]
    b._potion = (None, "Pet Large Red Potion")
    b.in_fight = True
    snap = {"self": {"name": "Hero"}, "entities": [
        {"id": 77, "kind": "player", "name": "Cat Fluffy belongs to Hero", "hp": 100, "maxhp": 1000}]}
    pets.look_after(as_bot(b), snap)
    assert b.used == []


# ---------------------------------------------------------------- recall
def test_recall_puts_the_out_pet_away(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", -1), ("Bird", 0)))
    pets.recall(as_bot(b))
    assert b.keys == [b.p["use_key"]]
    assert any("put Cat away" in line for line in b.lines)


def test_recall_nothing_when_no_pet_is_out(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock)
    b = Bot(rows(("Cat", 0)))
    pets.recall(as_bot(b))
    assert b.keys == []
