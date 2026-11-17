# pyright: reportPrivateUsage=false
"""Free presses (Phase 1b item 3): skills outside the global cooldown - Cold Winter, Scorch,
Concentrated Will, Summon Spirit - go in the wait after a cast, so they cost no attack."""
from __future__ import annotations

from typing import Any

from conftest import ME, Bot, Clock, Game, as_parts, fight

from unclefarmer.engine import combat


def book(clock: Clock, skills: list[str], profile: dict[str, Any] | None = None,
         attackers: set[int] | None = None) -> tuple[Bot, combat.SkillBook]:
    b = Bot(Game(clock, skills), profile)
    b.attackers = lambda: set(attackers or ())  # type: ignore[method-assign]
    return b, combat.SkillBook(as_parts(b))


ME_FULL = {"id": ME, "hp": 100000, "maxhp": 100000}
ME_HURT = {"id": ME, "hp": 70000, "maxhp": 100000}


def test_which_skills_are_free_presses() -> None:
    info = {x["name"]: x for cls in combat.knowledge().values() for x in cls}
    assert combat.SkillBook.free_buff(info["Cold Winter"]) and combat.SkillBook.free_buff(info["Scorch"])
    assert combat.SkillBook.free_guard(info["Concentrated Will"]) and combat.SkillBook.free_guard(info["Summon Spirit"])
    # in the global cooldown, or not a damage reduction: not free presses
    assert not combat.SkillBook.free_guard(info["Undying Resolve"])
    assert not combat.SkillBook.free_guard(info["Break the Chain"])
    assert not combat.SkillBook.free_buff(info["Lava Armor"])


def test_a_free_buff_goes_in_any_fight_once_per_its_time(clock: Clock) -> None:
    _b, sb = book(clock, ["Magic Sphere", "Cold Winter"])
    got = sb.free_press(ME_FULL, clock.now)
    assert got is not None and got[1] == "Cold Winter"
    assert sb.free_press(ME_FULL, clock.now + 1) is None          # just pressed (and not cast yet)
    sb.used["Cold Winter"] = clock.now + 1                       # the game cast it
    assert sb.free_press(ME_FULL, clock.now + 25) is None        # its cooldown still runs
    got = sb.free_press(ME_FULL, clock.now + 32)
    assert got is not None and got[1] == "Cold Winter"


def test_a_free_guard_waits_for_a_monster_hitting_us_and_lost_hp(clock: Clock) -> None:
    _b, sb = book(clock, ["Shadow Arrow", "Summon Spirit"])
    assert sb.free_press(ME_HURT, clock.now) is None              # nobody hitting us
    _b, sb = book(clock, ["Shadow Arrow", "Summon Spirit"], attackers={-5})
    assert sb.free_press(ME_FULL, clock.now) is None              # hit, but no HP lost yet
    got = sb.free_press(ME_HURT, clock.now)
    assert got is not None and got[1] == "Summon Spirit"


def test_never_or_emergency_only_and_no_buffs_keep_them_off(clock: Clock) -> None:
    _b, sb = book(clock, ["Magic Sphere", "Cold Winter"], {"skill_use": {"Cold Winter": "never"}})
    assert sb.free_press(ME_FULL, clock.now) is None
    _b, sb = book(clock, ["Shadow Arrow", "Summon Spirit"], {"skill_use": {"Summon Spirit": "emergency"}},
                  attackers={-5})
    assert sb.free_press(ME_HURT, clock.now) is None
    _b, sb = book(clock, ["Magic Sphere", "Cold Winter"], {"use_buffs": False})
    assert sb.free_press(ME_FULL, clock.now) is None


def test_a_free_press_leaves_the_attack_s_press_timing_alone(clock: Clock) -> None:
    _b, sb = book(clock, ["Magic Sphere", "Cold Winter"])
    sb.last_press, sb.press_confirmed = 5.0, True
    x = sb.skill("Cold Winter")
    assert x is not None
    sb.pressed(x, clock.now)
    assert sb.last_press == 5.0 and sb.press_confirmed
    # its cast report neither confirms a waiting attack press nor times the global cooldown
    sb.press_confirmed, sb.last_press, sb.last_cast = False, clock.now, 1.0
    sb._time_cast("Cold Winter", clock.now + 0.1, x)
    assert not sb.press_confirmed and sb.last_cast == 1.0


def test_in_a_fight_the_mage_keeps_cold_winter_up_without_losing_attacks(clock: Clock) -> None:
    g, _b, _sb = fight(clock, ["Magic Sphere", "Cold Winter"], [], seconds=40)
    names = [c["skill"].removeprefix("Level 5 ") for c in g.casts if c["caster"] == ME]
    assert names.count("Cold Winter") == 2                       # 30 s cooldown in a 40 s fight
    g2, _b2, _sb2 = fight(clock, ["Magic Sphere"], [], seconds=40)
    spheres = [c["skill"].removeprefix("Level 5 ") for c in g2.casts if c["caster"] == ME].count("Magic Sphere")
    assert names.count("Magic Sphere") >= spheres - 1            # no attack lost to it
