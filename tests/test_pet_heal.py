# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""A Priest or a Sorceress heals the pet with the class's own quick heal before any pet potion:
the pet selected by its id (agent 40), the heal pressed, our target selected again. A heal that
never raises the pet's HP is going elsewhere, and the bot falls back to pet potions."""

from test_pets import PET_ID, Bot, snap

from unclefarmer.engine import pets

MONSTER = -321


class Book:
    """The skill book: one quick heal on key 3, pressed with the stationary key."""

    ok = True

    def __init__(self):
        self.heal = {"name": "Healing Wave", "key": "3", "still": "alt+3", "page": 1}
        self.cooling = False

    def healer(self):
        return self.heal

    def gcd_wait(self, now):
        return self.cooling

    def ready(self, x, now):
        return True

    def press_support(self, x, now):
        return x["still"], x["name"]


class Link:
    def __init__(self):
        self.sent = []

    def send(self, line):
        self.sent.append(line)
        return "selected"


READING = {"shift+r", "t"}         # the pet HP and points keys look_after presses to read the pet


def acts(b):
    """What was pressed to do something (heals, potions), not to read the pet."""
    return [k for k in b.pressed if k not in READING]


class Healer(Bot):
    def __init__(self, clock, **kw):
        super().__init__(**kw)
        self.clock = clock
        self.agent_act = True
        self.skillbook = Book()
        self.a = Link()
        self.p["pet_heal_with_skill"] = True
        self.p["pet_heal_below"] = 85

    def ui(self):
        return {"target": MONSTER}


def test_the_pet_is_healed_with_the_skill_and_our_target_comes_back(clock):
    b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
    pets.look_after(b, snap(hp=60000))
    assert b.a.sent == ["select %d" % PET_ID, "select %d" % MONSTER]
    assert acts(b) == ["alt+3"]                   # the heal, standing still; no pet potion
    assert any("heals the pet with Healing Wave" in line for line in b.lines)


def test_a_healthy_pet_is_not_healed(clock):
    b = Healer(clock)
    pets.look_after(b, snap(hp=110000))           # 92%: above the 85% mark
    assert b.a.sent == [] and acts(b) == []


def test_no_heal_without_the_agent_selecting_by_id_or_with_it_off(clock):
    for off in ("agent", "setting"):
        b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
        if off == "agent":
            b.agent_act = False
        else:
            b.p["pet_heal_with_skill"] = False
        pets.look_after(b, snap(hp=60000))
        assert b.a.sent == [] and acts(b) == ["5"]       # the pet potion, as before


def test_a_heal_that_never_reaches_the_pet_hands_over_to_the_potions(clock):
    b = Healer(clock, hotkeys={"Pet Large Red Potion": "5"})
    for _round in range(pets.HEAL_MISSES + 1):
        pets.look_after(b, snap(hp=60000))        # the pet's HP never moves
        clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    assert b.pet_heal_off is True
    assert any("doesn't reach the pet" in line for line in b.lines)
    assert "5" in b.pressed                       # then the pet potions do it


def test_a_heal_that_lands_keeps_the_count_of_misses_at_zero(clock):
    b = Healer(clock)
    pets.look_after(b, snap(hp=60000))
    clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    pets.look_after(b, snap(hp=70000))            # it rose: the heal landed; still low, so heal again
    assert b.pet_heal_misses == 0 and acts(b) == ["alt+3", "alt+3"]


def test_no_heal_while_the_last_one_is_still_landing_or_the_cooldown_runs(clock):
    b = Healer(clock)
    pets.look_after(b, snap(hp=60000))
    pets.look_after(b, snap(hp=60000))            # too soon to tell, and too soon to heal again
    assert acts(b) == ["alt+3"]
    clock.advance(pets.HEAL_CHECK + pets.HEAL_GAP)
    b.skillbook.cooling = True
    pets.look_after(b, snap(hp=70000))
    assert acts(b) == ["alt+3"]                   # the global cooldown: not this time
