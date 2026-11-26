"""Use only the skills of page 1 (skill_page_one): a player saw the bot turn to skill page 2 for skills
they never use, a loss of time while farming. Ticked, the page-2 keys are on no key for the bot (no
fight, trip or guard picks them, so nothing turns the page there), and the empty keys it fills are
those of page 1 only. Off by default: both pages, as before."""

from __future__ import annotations

from typing import Any

from conftest import as_bot, as_parts

from test_combat_extra import TB, TLink
from test_keyslots import SB, Bot, Link, priest_rows

from unclefarmer import settings
from unclefarmer.engine import combat, keyslots
from unclefarmer.engine.combat import SkillBook


def book(page_one: bool, attack_keys: dict[str, int]) -> tuple[SkillBook, TB]:
    skills = {"combat": [{"id": 1, "name": "Fireball"}, {"id": 2, "name": "Sear"}, {"id": 3, "name": "Scorch"}],
              "attack_keys": attack_keys, "talents": []}
    bot = TB(p={"skill_page_one": page_one}, a=TLink(skills=skills, modes={"swap_attack": True}))
    return combat.SkillBook(as_parts(bot)), bot


def names(b: SkillBook) -> dict[int, list[str]]:
    return {pg: [x["name"] for x in row] for pg, row in b.slots.items()}


def test_off_by_default_both_pages_are_used() -> None:
    assert settings.PROFILE_DEFAULTS["skill_page_one"] is False
    b, bot = book(False, {"1": 1, "11": 2, "12": 3})
    assert names(b) == {0: ["Fireball"], 1: ["Sear", "Scorch"]}
    assert not any("page 2 left alone" in line for line in bot.lines)


def test_ticked_the_page_two_keys_are_on_no_key_for_the_bot() -> None:
    b, bot = book(True, {"1": 1, "11": 2, "12": 3})
    assert names(b) == {0: ["Fireball"]}
    assert b.skill("Sear") is None and b.skill("Fireball") is not None
    assert "skill page 2 left alone (only the skills of page 1 are used): Scorch, Sear" in bot.lines
    assert not any("skill page turns" in line for line in bot.lines)


def test_a_skill_on_both_pages_is_kept_from_page_one() -> None:
    b, _bot = book(True, {"1": 1, "11": 1, "12": 2})
    assert names(b) == {0: ["Fireball"]} and b.page_two == ["Fireball", "Sear"]
    assert b.skill("Fireball")["slot"] == 1        # type: ignore[index]


def test_with_nothing_on_page_one_the_bot_has_no_skill_keys() -> None:
    b, _bot = book(True, {"11": 1})
    assert b.slots == {} and not b.ok


def test_page_two_skills_are_not_called_on_no_key() -> None:
    """tell_missing names a heal learned but on no key: one left on page 2 on purpose isn't missing."""
    skills = {"combat": [{"id": 1, "name": "Heal"}, {"id": 2, "name": "Retribution"}],
              "attack_keys": {"1": 2, "11": 1}, "talents": []}
    bot = TB(p={"skill_page_one": True}, a=TLink(skills=skills, modes={"swap_attack": True}))
    combat.SkillBook(as_parts(bot))
    assert not any("learned but on no skill key" in line for line in bot.lines)
    bot = TB(p={"skill_page_one": True}, a=TLink(skills=dict(skills, attack_keys={"1": 2}),
                                                  modes={"swap_attack": True}))
    combat.SkillBook(as_parts(bot))
    assert any("learned but on no skill key" in line and "Heal" in line for line in bot.lines)


def keys_bot(page_one: bool, attack_keys: dict[str, int] | None = None) -> tuple[Bot, Link]:
    link = Link(priest_rows(), attack_keys=attack_keys)
    bot = Bot(link, SB())
    bot.p["skill_page_one"] = page_one
    return bot, link


def test_the_empty_keys_filled_are_those_of_page_one_only() -> None:
    taken = {str(i): 100 + i for i in range(1, 8)}           # keys 1-7 hold the player's own skills
    bot, link = keys_bot(True, taken)
    assert keyslots.skills(as_bot(bot)) is True
    placed = {k: v for k, v in link.attack_keys.items() if v < 100}
    assert sorted(int(k) for k in placed) == [8, 9, 10]      # the 3 empty keys of page 1, never 11-20
    assert bot.told_no_skill_room                            # the 4 others: no room, said once


def test_off_the_empty_keys_of_page_two_are_used_as_before() -> None:
    taken = {str(i): 100 + i for i in range(1, 11)}          # page 1 full
    bot, _link = keys_bot(False, taken)
    sb = bot.skillbook
    keyslots.skills(as_bot(bot))
    assert 1 in sb.flipped                                   # it turned to page 2 to put a skill there


def test_ticked_with_page_one_full_nothing_goes_on_page_two() -> None:
    taken = {str(i): 100 + i for i in range(1, 11)}
    bot, link = keys_bot(True, taken)
    sb = bot.skillbook
    assert keyslots.skills(as_bot(bot)) is False
    assert link.attack_keys == taken and 1 not in sb.flipped and bot.told_no_skill_room


def test_a_needed_skill_only_on_page_two_goes_on_page_one() -> None:
    """Heal sits on key 11: with page 1 only, it counts as on no key and gets an empty key of page 1."""
    bot, link = keys_bot(True, {"11": 1})
    assert keyslots.skills(as_bot(bot)) is True
    assert link.attack_keys["1"] == 1


def test_put_skill_counts_page_one_only() -> None:
    bot, link = keys_bot(True, {"11": 1})
    assert keyslots.put_skill(as_bot(bot), "Heal") is True
    assert link.attack_keys["1"] == 1
    bot, link = keys_bot(False, {"11": 1})
    assert keyslots.put_skill(as_bot(bot), "Heal") is True
    assert link.attack_keys == {"11": 1}                     # on a key already: left as it is


def test_the_last_slot_follows_the_tick() -> None:
    bot, _link = keys_bot(True)
    assert keyslots.last_slot(as_bot(bot)) == 10
    bot.p["skill_page_one"] = False
    assert keyslots.last_slot(as_bot(bot)) == 20


def test_with_every_skill_on_page_two_the_player_hears_why() -> None:
    b, bot = book(True, {"11": 1, "12": 2})
    assert not b.ok
    assert "skill page 2 left alone (only the skills of page 1 are used): Fireball, Sear" in bot.lines


def test_the_keyboard_managers_book_sees_both_pages() -> None:
    skills = {"combat": [{"id": 1, "name": "Fireball"}, {"id": 2, "name": "Sear"}],
              "attack_keys": {"1": 1, "11": 2}, "talents": []}
    bot = TB(p={"skill_page_one": True}, a=TLink(skills=skills, modes={"swap_attack": True}))
    b = combat.SkillBook(as_parts(bot), both_pages=True)
    assert names(b) == {0: ["Fireball"], 1: ["Sear"]} and b.page_two == []


def test_after_the_keyboard_manager_the_farm_gets_a_page_one_book() -> None:
    from unclefarmer.engine import keyboard

    class Holder:
        def __init__(self, book: object) -> None:
            self.skillbook = book

    both = type("Book", (), {"both_pages": True})()
    plain = type("Book", (), {"both_pages": False})()
    h = Holder(both)
    keyboard._farm_book(as_bot(h), True)          # pyright: ignore[reportPrivateUsage]
    assert h.skillbook is None                    # read again, without page 2, when next needed
    h = Holder(both)
    keyboard._farm_book(as_bot(h), False)         # pyright: ignore[reportPrivateUsage]
    assert h.skillbook is both                    # box off: both pages are the farm's anyway
    h = Holder(plain)
    keyboard._farm_book(as_bot(h), True)          # pyright: ignore[reportPrivateUsage]
    assert h.skillbook is plain


def test_put_skill_says_a_skill_is_only_on_page_two_when_page_one_is_full() -> None:
    taken = {str(i): 100 + i for i in range(1, 11)}
    taken["11"] = 1                                          # Heal, on page 2 only
    bot, link = keys_bot(True, taken)
    assert keyslots.put_skill(as_bot(bot), "Heal") is False
    assert bot.lines == ["Heal is only on skill page 2, which is left alone, and no key of page 1 is empty - "
                         "put it on page 1 to use it"]
    assert link.attack_keys == taken


def test_the_guard_is_hit_on_page_one_when_no_skill_book_reads() -> None:
    from unclefarmer.engine import xploss

    class NoBook:
        ok = False
        cls = None

    class Loser:
        def __init__(self, page_one: bool) -> None:
            self.p = {"skill_page_one": page_one, "attack_keys": ["1", "2"]}
            self.skillbook = NoBook()

    assert xploss.attack_key(as_bot(Loser(True))) == ("1", 0, None)
    assert xploss.attack_key(as_bot(Loser(False))) == ("1", None, None)


def test_the_profile_keys_wait_for_page_one(monkeypatch: Any) -> None:
    """With no skill book that works, the fight presses the profile's attack keys: on page 2 they
    would cast its skills, so page 1 comes up first (and nothing is pressed if it can't)."""
    from unclefarmer.engine.botparts.fighting import FightingMixin

    class Book:
        def __init__(self, up: int, flips: bool) -> None:
            self.up, self.flips, self.asked = up, flips, list[int]()

        def page(self) -> int:
            return self.up

        def flip_to(self, want: int) -> bool:
            self.asked.append(want)
            if self.flips:
                self.up = want
            return self.flips

    class Fighter:
        def __init__(self, page_one: bool) -> None:
            self.p = {"skill_page_one": page_one}

    on = FightingMixin._on_page_one                      # pyright: ignore[reportPrivateUsage]
    book2 = Book(1, True)
    assert on(as_bot(Fighter(True)), book2) and book2.asked == [0] and book2.up == 0
    stuck = Book(1, False)
    assert not on(as_bot(Fighter(True)), stuck)
    off = Book(1, True)
    assert on(as_bot(Fighter(False)), off) and off.asked == []     # box off: as before
    assert on(as_bot(Fighter(True)), None)                          # no book at all: nothing to turn


def test_reset_and_copy_carry_the_tick() -> None:
    from unclefarmer.engine import profilecopy
    from unclefarmer.gui import profile as gui
    assert "skill_page_one" in profilecopy.FIGHTING_KEYS and "skill_page_one" in gui.MAIN_FIGHTING
    theirs: dict[str, Any] = {"skill_page_one": True}
    assert profilecopy.fighting(theirs, with_skills=False) == {"skill_page_one": True}
