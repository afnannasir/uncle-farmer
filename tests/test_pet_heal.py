"""A Priest or a Sorceress heals the pet with the class's own quick heal before any pet potion:
the pet selected by its id (agent 40), the heal pressed, our target selected again. A heal that
never raises the pet's HP is going elsewhere, and the bot falls back to pet potions."""

from __future__ import annotations

from test_pets import PET_ID, Bot, snap

from unclefarmer.engine import pets
from conftest import as_bot, Clock
from typing import Any

MONSTER = -321


class Book:
    """The skill book: one quick heal on key 3, pressed with the stationary key."""

    ok = True

    def __init__(self) -> None:
        self.heal = {"name": "Healing Wave", "key": "3", "still": "alt+3", "page": 1}
        self.cooling = False

    def healer(self) -> dict[str, Any]:
        return self.heal

    def gcd_wait(self, now: float) -> bool:
        return self.cooling

    def ready(self, x: dict[str, Any], now: float) -> bool:
        return True

    def press_support(self, x: dict[str, Any], now: float) -> tuple[str, str]:
        return x["still"], x["name"]


class Link:
    def __init__(self) -> None:
        self.sent: list[str] = []
        self.answer = "selected"

    def send(self, line: str) -> str:
        self.sent.append(line)
        return self.answer


READING = {"shift+r", "t"}         # the pet HP and points keys look_after presses to read the pet


def acts(b: Healer) -> list[Any]:
    """What was pressed to do something (heals, potions), not to read the pet."""
    return [k for k in b.pressed if k not in READING]


class Healer(Bot):
    def __init__(self, clock: Clock, **kw: Any) -> None:
        super().__init__(**kw)
        self.pet_heal_misses = 0
        self.clock = clock
        self.agent_act = True
        self.skillbook = Book()
        self.a = Link()
        self.p["pet_heal_with_skill"] = True
        self.p["pet_heal_below"] = 85

    def ui(self) -> dict[str, Any]:
        return {"target": MONSTER}


def test_the_pet_is_healed_with_the_skill_and_our_target_comes_back(clock: Clock) -> None:
    b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
    pets.look_after(as_bot(b), snap(hp=60000))
    assert b.a.sent == ["select %d" % PET_ID, "select %d" % MONSTER]
    assert acts(b) == ["alt+3"]                   # the heal, standing still; no pet potion
    assert any("heals the pet with Healing Wave" in line for line in b.lines)


def test_a_healthy_pet_is_not_healed(clock: Clock) -> None:
    b = Healer(clock)
    pets.look_after(as_bot(b), snap(hp=110000))           # 92%: above the 85% mark
    assert b.a.sent == [] and acts(b) == []


def test_no_heal_without_the_agent_selecting_by_id_or_with_it_off(clock: Clock) -> None:
    for off in ("agent", "setting"):
        b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
        if off == "agent":
            b.agent_act = False
        else:
            b.p["pet_heal_with_skill"] = False
        pets.look_after(as_bot(b), snap(hp=60000))
        assert b.a.sent == [] and acts(b) == ["5"]       # the pet potion, as before


def test_a_heal_that_never_reaches_the_pet_rests_then_is_tried_again(clock: Clock) -> None:
    # never given up for the run: the potions take over for a while, and the log says why
    b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
    for _round in range(pets.HEAL_MISSES + 1):
        pets.look_after(as_bot(b), snap(hp=60000))        # the pet's HP never moves
        clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    assert any("didn't raise Wild Eagle Sky's HP 3 times running" in line for line in b.lines), b.lines
    assert "5" in b.pressed                       # the pet potions meanwhile
    heals = acts(b).count("alt+3")
    clock.advance(pets.HEAL_REST)
    pets.look_after(as_bot(b), snap(hp=60000))
    assert acts(b).count("alt+3") == heals + 1    # rested: the heal again


def test_without_the_agent_selecting_the_log_says_why_the_pet_isn_t_healed(clock: Clock) -> None:
    b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
    b.agent_act = False
    for _round in range(3):
        pets.look_after(as_bot(b), snap(hp=60000))
        clock.advance(30)
    said = [line for line in b.lines if line.startswith("can't heal Wild Eagle Sky with Healing Wave")]
    assert len(said) == 1 and "restart the game" in said[0]       # once, not every look
    assert "5" in b.pressed


def test_a_refused_select_is_said_with_the_game_s_reason(clock: Clock) -> None:
    b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
    b.a.answer = "refused: %d isn't in a clear line" % PET_ID
    pets.look_after(as_bot(b), snap(hp=60000))
    assert any("the game wouldn't select it (isn't in a clear line)" in line for line in b.lines), b.lines
    assert acts(b) == ["5"]                       # the potion instead, this time
    b.a.answer = "selected"
    clock.advance(pets.HEAL_GAP + 1)
    pets.look_after(as_bot(b), snap(hp=60000))
    assert "alt+3" in acts(b)                     # and the heal is tried again


def test_a_heal_that_lands_keeps_the_count_of_misses_at_zero(clock: Clock) -> None:
    b = Healer(clock)
    pets.look_after(as_bot(b), snap(hp=60000))
    clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    pets.look_after(as_bot(b), snap(hp=70000))            # it rose: the heal landed; still low, so heal again
    assert b.pet_heal_misses == 0 and acts(b) == ["alt+3", "alt+3"]


def test_no_heal_while_the_last_one_is_still_landing_or_the_cooldown_runs(clock: Clock) -> None:
    b = Healer(clock)
    pets.look_after(as_bot(b), snap(hp=60000))
    pets.look_after(as_bot(b), snap(hp=60000))            # too soon to tell, and too soon to heal again
    assert acts(b) == ["alt+3"]
    clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    b.skillbook.cooling = True
    pets.look_after(as_bot(b), snap(hp=70000))
    assert acts(b) == ["alt+3"]                   # the global cooldown: not this time


def test_in_a_fight_the_pet_is_healed_when_our_press_is_due(clock: Clock) -> None:
    # the fight's look comes while the global cooldown runs; the press step is where a heal can go
    b = Healer(clock, in_fight=True)
    assert pets.heal_in_fight(as_bot(b), snap(hp=60000)) is True
    assert b.a.sent == ["select %d" % PET_ID, "select %d" % MONSTER]
    assert acts(b) == ["alt+3"]


def test_in_a_fight_no_pet_heal_while_our_own_hp_needs_it(clock: Clock) -> None:
    b = Healer(clock, in_fight=True)
    s = snap(hp=60000)
    s["self"]["hp"] = 30000                      # a third of ours: the picker heals us first
    assert pets.heal_in_fight(as_bot(b), s) is False
    assert b.a.sent == [] and acts(b) == []


def test_a_heal_in_a_fight_that_the_monsters_outdo_is_no_miss(clock: Clock) -> None:
    b = Healer(clock, in_fight=True)
    for _round in range(pets.HEAL_MISSES + 2):
        pets.heal_in_fight(as_bot(b), snap(hp=60000))     # the pet loses as much as each heal gives
        clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    assert b.pet_heal_misses == 0 and not any("didn't raise" in line for line in b.lines)
    assert acts(b) == ["alt+3"] * (pets.HEAL_MISSES + 2)
