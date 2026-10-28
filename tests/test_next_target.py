"""Which monster the bot takes on next, including the "nearest_monster" option.

The option (Character settings: "Always fight the nearest monster") is off-by-default behaviour a
player can turn on for melee characters: instead of the nearest monster, take a calm one beside a
pack, so the character isn't pulled into a crowd. Its rules had no tests at all:

  * a monster that already attacks us, or that we already hurt, always comes first;
  * ranged classes never avoid packs (the nearest is as good as any);
  * with fewer than 3 monsters round the nearest one, it is simply taken;
  * otherwise the nearest CALM one among the next 7 is taken, if it is at most 25 further away;
  * if nothing calm is close enough, the nearest is taken anyway (never stand still).
"""

from __future__ import annotations

import pytest

from unclefarmer.engine import combat
from unclefarmer.engine.botparts.fighting import FightingMixin
from typing import Any

PACK = combat.PACK_RADIUS


class SkillBook:
    def __init__(self, cls: str="Warrior", ok: bool=True) -> None:
        self.cls, self.ok = cls, ok


class Tracker:
    def __init__(self, tagged: set[int] | tuple[()]=()) -> None:
        self._tagged = set(tagged)

    def tagged(self, mid: int) -> bool:
        return mid in self._tagged


class Stub:
    """Only what next_target() touches; the method under test is the real one."""

    next_target = FightingMixin.next_target

    def __init__(self, nearest: bool=False, book: SkillBook | None=None, attackers: set[int] | tuple[()]=(), tagged: set[int] | tuple[()]=()) -> None:
        self.p = {"nearest_monster": nearest}
        self.skillbook: SkillBook | None = book if book is not None else SkillBook()
        self.kill_tracker = Tracker(tagged)
        self._attackers = set(attackers)

    def attackers(self) -> set[int]:
        return self._attackers


def mob(mid: int, x: int, y: float | int=0.0) -> dict[str, Any]:
    return {"id": mid, "name": "m%d" % mid, "x": float(x), "y": float(y), "hp": 10}


def pick(stub: Stub, mobs: list[dict[str, Any]], me_x: float=0.0) -> int:
    snap = {"self": {"x": me_x, "y": 0.0}}
    return stub.next_target(snap, mobs)["id"]


def calm(mid: int, away: int) -> dict[str, Any]:
    """A monster `away` from us, off to the side of the pack so that nothing stands beside it."""
    return mob(mid, 0, away)


def crowd_around(first_x: int, start_id: int, n: int) -> list[dict[str, Any]]:
    """n monsters standing within the pack radius of a monster at first_x."""
    return [mob(start_id + i, first_x + 1 + i) for i in range(n)]


# ---------------------------------------------------------------- the default: nearest first
def test_the_default_is_always_the_nearest() -> None:
    mobs = [mob(1, 10)] + crowd_around(10, 2, 5)
    assert pick(Stub(nearest=True), mobs) == 1


def test_the_option_missing_from_an_old_profile_means_nearest() -> None:
    stub = Stub()
    stub.p = {}                                  # a profile saved before the option existed
    assert pick(stub, [mob(1, 10)] + crowd_around(10, 2, 5)) == 1


# ---------------------------------------------------------------- the option off: avoid packs
def test_a_melee_character_steps_round_a_pack_to_a_calm_monster() -> None:
    pack = [mob(1, 10)] + crowd_around(10, 2, 3)         # the nearest has 3 neighbours: a pack
    quiet = calm(9, 20)                                   # 10 further away, nobody beside it
    assert pick(Stub(), pack + [quiet]) == 9


def test_two_neighbours_are_not_yet_a_pack() -> None:
    mobs = [mob(1, 10)] + crowd_around(10, 2, 2) + [calm(9, 20)]
    assert pick(Stub(), mobs) == 1


@pytest.mark.parametrize("cls", sorted(combat.MELEE))
def test_every_melee_class_avoids_packs(cls: str) -> None:
    pack = [mob(1, 10)] + crowd_around(10, 2, 3)
    assert pick(Stub(book=SkillBook(cls)), pack + [calm(9, 20)]) == 9


@pytest.mark.parametrize("cls", ["Archer", "Mage", "Priest", "Unknown"])
def test_ranged_classes_take_the_nearest_whatever_stands_beside_it(cls: str) -> None:
    pack = [mob(1, 10)] + crowd_around(10, 2, 5)
    assert pick(Stub(book=SkillBook(cls)), pack + [calm(9, 20)]) == 1


@pytest.mark.parametrize("book", [None, SkillBook(ok=False)])
def test_without_a_readable_skill_book_the_nearest_is_taken(book: SkillBook | None) -> None:
    stub = Stub()
    stub.skillbook = book
    pack = [mob(1, 10)] + crowd_around(10, 2, 5)
    assert pick(stub, pack + [calm(9, 20)]) == 1


def test_a_calm_monster_too_far_beyond_the_nearest_is_not_worth_the_walk() -> None:
    pack = [mob(1, 10)] + crowd_around(10, 2, 3)
    far = calm(9, 10 + 25 + 1)                           # one step past the 25 allowed
    assert pick(Stub(), pack + [far]) == 1               # fight in the pack rather than walk across the map


def test_a_calm_monster_exactly_25_further_is_still_taken() -> None:
    pack = [mob(1, 10)] + crowd_around(10, 2, 3)
    assert pick(Stub(), pack + [calm(9, 10 + 25)]) == 9


def test_when_every_monster_is_in_a_crowd_the_nearest_is_taken() -> None:
    mobs = [mob(1, 10)] + crowd_around(10, 2, 3) + [mob(20, 20)] + crowd_around(20, 21, 3)   # two crowds
    assert pick(Stub(), mobs) == 1


def test_the_nearest_calm_monster_wins_among_several() -> None:
    pack = [mob(1, 10)] + crowd_around(10, 2, 3)
    assert pick(Stub(), pack + [calm(8, 30), calm(9, 20), calm(7, -25)]) == 9


def test_only_the_next_seven_monsters_are_considered() -> None:
    # targets() has sorted them nearest first; a calm monster beyond the first 8 is not looked at
    pack = [mob(1, 10)] + crowd_around(10, 2, 3)         # 4 monsters
    # Four more nearby, still inside the pack: eight targets in all, none of them calm. The calm
    # monster is ninth by distance, so the slice mobs[1:8] must not look at it.
    fillers = [mob(100 + i, 14 + i, 0.0) for i in range(4)]
    calm_ninth = calm(9, 20)
    mobs = pack + fillers + [calm_ninth]
    assert len(mobs) == 9
    assert pick(Stub(), mobs) == 1


# ---------------------------------------------------------------- already fighting: stay on it
def test_a_monster_already_attacking_us_comes_first_whatever_stands_beside_it() -> None:
    first = [mob(1, 10)] + crowd_around(10, 2, 5)
    assert pick(Stub(attackers={1}), first + [calm(9, 20)]) == 1


def test_a_monster_we_already_hurt_is_finished_before_a_calmer_one() -> None:
    first = [mob(1, 10)] + crowd_around(10, 2, 5)
    assert pick(Stub(tagged={1}), first + [calm(9, 20)]) == 1


def test_a_monster_that_is_not_ours_is_still_avoided_in_a_pack() -> None:
    first = [mob(1, 10)] + crowd_around(10, 2, 5)
    assert pick(Stub(attackers={77}, tagged={88}), first + [calm(9, 20)]) == 9


# ---------------------------------------------------------------- a single monster
def test_one_monster_is_the_only_choice() -> None:
    assert pick(Stub(), [mob(5, 3)]) == 5
