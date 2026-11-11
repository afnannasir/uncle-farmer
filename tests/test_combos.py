# pyright: reportPrivateUsage=false
"""Combos from activated talents and the skill texts, fought out against the pretend game."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from conftest import as_parts, Bot, Clock, fight, followed_by, Game, ME

from unclefarmer.engine import combat, combos

WARRIOR = ["Bash", "Cleave", "Sweep Attack", "Leap Slash", "Execute"]
HUNTER = ["Arcane Shot", "Aimed Shot", "Multi Shot", "Rapid Arrows", "Marked Shot"]
MAGE = ["Magic Sphere", "Ice Lance", "Aqua Jet", "Ice Arrow", "Frost Nova", "Ice Storm"]
ELF = ["Fireball", "Fire Impact", "Pyroblast", "Ignition"]
PRIEST = ["Retribution", "Mind Whip", "Divine Nova", "Holy Light Wave"]
WITCHER = ["Shadow Arrow", "Corrosion Spell", "Skeleton Explosion", "Dead Toxin"]


def test_every_rule_has_one_kind_and_a_description() -> None:
    for r in combos.RULES:
        assert sum(k in r for k in combos.KINDS) == 1, r
        kind = next(k for k in combos.KINDS if k in r)
        assert combos.describe(dict(r, kind=kind))


def test_only_activated_talents_count() -> None:
    sk = {"talents": [{"name": "Cleave Combo", "b": 1}, {"name": "Steady Hands", "b": 0}]}
    assert combos.active_talents(sk) == {"Cleave Combo"}


def test_a_rule_needs_its_skills_on_keys() -> None:
    on = {"Aimed Shot", "Marked Shot"}          # one of the three "before" targets is enough
    kinds = [r["kind"] for r in combos.rules_for({"Steady Hands"}, on)]
    assert kinds == ["before"]
    assert combos.rules_for({"Steady Hands"}, {"Marked Shot"}) == []


def test_warrior_sweep_attack_follows_cleave(clock: Clock) -> None:
    g, b, _sb = fight(clock, WARRIOR, ["Combat Stance", "Cleave Combo", "Powerful Leap Attack"], 30)
    hits, total = followed_by(g.log, "Cleave", "Sweep Attack")
    assert total >= 4 and hits == total, g.log
    assert "Leap Slash" in g.log
    assert any("Cleave then Sweep Attack" in line for line in b.lines)


def test_warrior_without_the_talents_does_neither(clock: Clock) -> None:
    g, _b, _sb = fight(clock, WARRIOR, ["Combat Stance"], 30)
    hits, total = followed_by(g.log, "Cleave", "Sweep Attack")
    assert "Leap Slash" not in g.log and hits < total


def test_warrior_next_skill_sooner_after_leap_slash(clock: Clock) -> None:
    g, _b, sb = fight(clock, WARRIOR, ["Combat Stance", "Powerful Leap Attack"], 60)
    t = [c["t"] / 1000.0 for c in g.casts]
    gaps = [t[i + 1] - t[i] for i, n in enumerate(g.log[:-1]) if n == "Leap Slash"]
    assert gaps and all(gap < 0.75 * sb.gcd() for gap in gaps), (gaps, sb.gcd())
    assert sb.gcd() > 1.3                   # the short gap isn't taken for the usual cooldown


def test_hunter_rapid_arrows_after_an_arcane_shot_crit(clock: Clock) -> None:
    crit: Callable[[str, int], bool] = lambda name, n: name == "Arcane Shot" and n % 4 == 0
    g, _b, _sb = fight(clock, HUNTER, ["Aspect of the Wild", "Call of the Wilderness"], 60, dist=12, crit=crit,
                     resets={"Arcane Shot": ("Call of the Wilderness", "Rapid Arrows")})
    crits = [i for i, n in enumerate(g.log[:-1]) if n == "Arcane Shot*"]
    after = [g.log[i + 1] for i in crits]
    assert len(crits) >= 3 and after.count("Rapid Arrows") >= len(crits) - 1, g.log
    assert g.log.count("Rapid Arrows") >= 4          # more than its 30 s cooldown allows


def test_crit_combos_wait_for_agent_28(clock: Clock) -> None:
    _g, b, sb = fight(clock, HUNTER, ["Aspect of the Wild", "Call of the Wilderness"], 10, dist=12, version=27)
    assert sb.combos is None
    assert any("new agent" in line for line in b.lines)


def test_hunter_aimed_shot_before_rapid_arrows(clock: Clock) -> None:
    g, _b, _sb = fight(clock, HUNTER, ["Aspect of the Monkey", "Steady Hands"], 40, pack=4, dist=12)
    i = g.log.index("Rapid Arrows")
    assert i > 0 and g.log[i - 1] == "Aimed Shot", g.log[:12]


def test_mage_ice_finger_chain(clock: Clock) -> None:
    g, _b, _sb = fight(clock, MAGE, ["Ice Lineage", "Ice Finger"], 45, dist=6)
    xs = g.log
    chains = sum(1 for i in range(len(xs) - 2) if xs[i:i + 3] == ["Aqua Jet", "Ice Arrow", "Ice Lance"])
    assert chains >= 2, xs
    assert "Magic Sphere" not in xs and xs.count("Ice Arrow") >= 5      # Ice Arrow is the filler


def test_mage_ice_lance_on_a_frozen_pack(clock: Clock) -> None:
    g, _b, _sb = fight(clock, ["Magic Sphere", "Ice Lance", "Frost Nova", "Ice Storm"], [], 45, pack=4, dist=5)
    hits, total = followed_by(g.log, "Frost Nova", "Ice Lance")
    assert total >= 1 and hits == total, g.log


def test_mage_freezing_cold_shortens_ice_storm(clock: Clock) -> None:
    with_talent, _b, _s = fight(clock, ["Magic Sphere", "Ice Storm"], ["Cold Lineage", "Freezing Cold"], 45,
                                pack=4, cd_less={"Ice Storm": 1.5})
    without, _b, _s = fight(clock, ["Magic Sphere", "Ice Storm"], ["Cold Lineage"], 45, pack=4)
    assert with_talent.log.count("Ice Storm") > without.log.count("Ice Storm")


def test_elf_fire_impact_after_a_fireball_crit(clock: Clock) -> None:
    crit: Callable[[str, int], bool] = lambda name, n: name == "Fireball" and n % 3 == 0
    g, _b, _sb = fight(clock, ELF, ["Hellfire", "Flame Combo"], 60, crit=crit,
                     resets={"Fireball": ("Flame Combo", "Fire Impact")})
    crits = [i for i, n in enumerate(g.log[:-1]) if n == "Fireball*"]
    assert len(crits) >= 3 and sum(g.log[i + 1] == "Fire Impact" for i in crits) >= len(crits) - 1, g.log


def test_elf_ignition_after_three_fire_elements(clock: Clock) -> None:
    crit: Callable[[str, int], bool] = lambda name, n: True
    g, _b, _sb = fight(clock, ["Fireball", "Fire Impact", "Blazing Storm", "Ignition"],
                     ["Blazing Combo"], 60, pack=4, crit=crit)
    plain = [x.rstrip("*") for x in g.log]
    i = plain.index("Ignition")
    assert plain[:i].count("Blazing Storm") >= 3, g.log
    assert all(plain[j + 1] == "Blazing Storm" for j, name in enumerate(plain[:-1]) if name == "Ignition"), g.log


def test_elf_blazing_combo_after_two_crits(clock: Clock) -> None:
    crit: Callable[[str, int], bool] = lambda name, n: name == "Fire Impact" or n % 2 == 0
    g, _b, _sb = fight(clock, ELF, ["Elven Fire", "Blazing Combo"], 40, crit=crit)
    assert "Pyroblast" in g.log


def test_priest_divine_whip(clock: Clock) -> None:
    g, _b, _sb = fight(clock, PRIEST, ["Shelter", "Divine Whip"], 40, dist=8)
    assert g.log[0] == "Mind Whip" and 2 <= g.log.count("Mind Whip") <= 4, g.log
    assert g.log.count("Divine Nova") >= 2          # used on one target while it is whipped


def test_priest_wisdom_resets_divine_nova(clock: Clock) -> None:
    crit: Callable[[str, int], bool] = lambda name, n: name == "Divine Nova"
    g, _b, _sb = fight(clock, PRIEST, ["Justice", "Wisdom"], 30, dist=8, pack=4, crit=crit,
                     resets={"*": ("Wisdom", "Divine Nova")})
    assert g.log[:3].count("Divine Nova*") >= 2, g.log


def test_witcher_enhanced_explosion_and_dead_toxin(clock: Clock) -> None:
    crit: Callable[[str, int], bool] = lambda name, n: name == "Shadow Arrow" and n % 3 == 0
    g, _b, _sb = fight(clock, WITCHER, ["Soul Link", "Enhanced Explosion", "Dead Toxin Surge"], 40, dist=8, crit=crit,
                     resets={"Shadow Arrow": ("Dead Toxin Surge", "Dead Toxin")})
    assert g.log[0] == "Corrosion Spell" and g.log.count("Skeleton Explosion") >= 5, g.log
    assert sum(1 for i in range(len(g.log) - 1) if g.log[i] == "Shadow Arrow*" and g.log[i + 1] == "Dead Toxin") >= 2


def test_combos_off_in_the_profile(clock: Clock) -> None:
    g, _b, sb = fight(clock, WARRIOR, ["Combat Stance", "Cleave Combo", "Powerful Leap Attack"], 20,
                     profile={"use_combos": False})
    assert sb.combos is None and "Leap Slash" not in g.log


# ---------------------------------------------------------------- Elf Fire Combos and Channeling (Scenarios 1-22)

def test_scenario_01_fire_impact_normal_usability_when_not_pending(clock: Clock) -> None:
    """1. Fire Impact remains usable through normal skill selection when no sequence is pending."""
    g, _b, sb = fight(clock, ["Fireball", "Fire Impact"], ["Elven Fire"], 15, crit=lambda name, n: False)
    assert not sb.pending_channelling
    assert "Fire Impact" in g.log
    assert "Fireball" in g.log


def test_scenario_02_fire_impact_no_forced_priority_without_crit(clock: Clock) -> None:
    """2. Fire Impact does not receive forced priority without a qualifying critical hit."""
    g, _b, sb = fight(clock, ["Fireball", "Fire Impact", "Fire Surge"], ["Elven Fire"], 2, pack=4, crit=lambda name, n: False)
    assert not sb.pending_channelling
    assert g.log[0] == "Fire Surge"


def test_scenario_03_non_channeling_skill_crit_sets_pending_channelling(clock: Clock) -> None:
    """3. An eligible non-channeling skill other than Fire Impact critically hits and sets pending_channelling = True."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    assert not sb.pending_channelling
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 5, 5000]]}, ME)
    assert sb.pending_channelling is True


def test_scenario_04_blazing_storm_crit_sets_pending_channelling(clock: Clock) -> None:
    """4. Blazing Storm can trigger pending_channelling = True after a qualifying critical cast."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.pending_channelling = False
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Blazing Storm", "hits": [[-5, 5, 12000]]}, ME)
    assert sb.pending_channelling is True


def test_scenario_05_pyroblast_crit_sets_pending_channelling(clock: Clock) -> None:
    """5. Pyroblast can trigger pending_channelling = True after a qualifying critical cast."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.pending_channelling = False
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-5, 5, 15000]]}, ME)
    assert sb.pending_channelling is True


def test_scenario_06_fire_impact_highest_priority_when_pending(clock: Clock) -> None:
    """6. Fire Impact becomes the highest-priority eligible skill while pending."""
    g = Game(clock, ["Fireball", "Fire Impact", "Fire Surge"], ["Elven Fire"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000},
        mob,
        {"id": -6, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1},
        {"id": -7, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1},
        {"id": -8, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1},
    ]}
    pick_before = sb.pick(snap, mob)
    assert isinstance(pick_before, tuple) and pick_before[1] == "Fire Surge"
    sb.pending_channelling = True
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick_after = sb.pick(snap, mob)
    assert isinstance(pick_after, tuple) and pick_after[1] == "Fire Impact"


def test_scenario_07_fire_impact_retains_priority_when_temporarily_unavailable(clock: Clock) -> None:
    """7. Fire Impact retains priority if temporarily unavailable and is selected when it becomes usable."""
    g = Game(clock, ["Fireball", "Fire Impact"], ["Elven Fire"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    sb.used["Fire Impact"] = clock.now
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob,
    ]}
    pick1 = sb.pick(snap, mob)
    assert isinstance(pick1, tuple) and pick1[1] == "Fireball"
    assert sb.pending_channelling is True
    clock.advance(7.0)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick2 = sb.pick(snap, mob)
    assert isinstance(pick2, tuple) and pick2[1] == "Fire Impact"


def test_scenario_08_fire_impact_completes_sequence_enables_channeling(clock: Clock) -> None:
    """8. Fire Impact completes the sequence and sets available_to_channel_without_penalty = True."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    assert not sb.available_to_channel_without_penalty
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is True


def test_scenario_09_fire_impact_guaranteed_crit_does_not_loop_priority(clock: Clock) -> None:
    """9. Fire Impact does not start another sequence or cause an endless priority loop."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    assert sb.pending_channelling is False
    clock.advance(7.0)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    assert sb.pending_channelling is False


def test_scenario_10_channeling_skills_restricted_before_sequence_completed(clock: Clock) -> None:
    """10. Blazing Storm and Pyroblast cannot be selected before Fire Impact completes the sequence."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    assert not sb.available_to_channel_without_penalty
    skills, wanted = sb._usable(0, False, clock.now)
    usable_names = {s["name"] for s in skills}
    wanted_names = {s["name"] for s in wanted}
    assert "Pyroblast" not in usable_names and "Pyroblast" not in wanted_names
    assert "Blazing Storm" not in usable_names and "Blazing Storm" not in wanted_names
    sb.used["Fireball"] = clock.now
    sb.used["Fire Impact"] = clock.now
    fb = sb.fallback(clock.now)
    assert fb is None or fb[1] not in ("Pyroblast", "Blazing Storm")


def test_scenario_11_channeling_skills_eligible_after_permission_granted(clock: Clock) -> None:
    """11. Channeling skills become eligible after permission is granted, subject to normal usability checks."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    skills, _wanted = sb._usable(0, False, clock.now)
    usable_names = {s["name"] for s in skills}
    assert "Pyroblast" in usable_names
    assert "Blazing Storm" in usable_names


def test_scenario_12_channeling_skills_can_retrigger_fire_impact_priority(clock: Clock) -> None:
    """12. Channeling skills can trigger Fire Impact priority again after permission has been granted."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.pending_channelling = False
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Blazing Storm", "hits": [[-5, 5, 10000]]}, ME)
    assert sb.pending_channelling is True


def test_scenario_13_channeling_permission_active_throughout_chain(clock: Clock) -> None:
    """13. Channeling permission remains active throughout the Elven Fire chain."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-5, 5, 12000]]}, ME)
    assert sb.available_to_channel_without_penalty is True


def test_scenario_14_channeling_permission_resets_on_chain_completion(clock: Clock) -> None:
    """14. Channeling permission is preserved through normal filler casts and resets when the channeled skill completes the chain."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    clock.advance(1.5)
    # A non-channeling skill cast (e.g. Fireball) does not wipe the permission
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 3, 7800]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    # Casting the channeled skill (Pyroblast) without crit completes the chain and consumes permission
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-5, 3, 30000]]}, ME)
    assert sb.available_to_channel_without_penalty is False


def test_scenario_15_interrupted_sequence_clears_stale_state(clock: Clock) -> None:
    """15. Target transition preserves pending sequence and channeling permission; reset_chain clears state."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    sb.available_to_channel_without_penalty = True
    sb.forget_target(-5)
    assert sb.pending_channelling is True
    assert sb.available_to_channel_without_penalty is True

    sb.pending_channelling = True
    sb.target_id = -5
    mob_new = {"id": -99, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob_new,
    ]}
    sb.pick(snap, mob_new)
    assert sb.pending_channelling is True
    assert sb.available_to_channel_without_penalty is True
    if sb.combos:
        sb.combos.reset_chain()
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False


def test_scenario_16_17_18_blazing_storm_multi_hit_cast_level_folding(clock: Clock) -> None:
    """16, 17, 18. Blazing Storm multi-hit cast counts as one cast-level event and sets pending once."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.pending_channelling = False
    multi_hits = [
        [-5, 5, 2500],
        [-6, 5, 2500],
        [-7, 5, 2500],
        [-8, 5, 2500],
    ]
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Blazing Storm", "hits": multi_hits}, ME)
    assert sb.pending_channelling is True
    assert sb.combos is not None and sb.combos.streak == 1


def test_scenario_19_channeling_skills_do_not_bypass_fire_impact_priority(clock: Clock) -> None:
    """19. Channeling skills do not bypass the normal Fire Impact priority mechanism."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.pending_channelling = True
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000},
        mob,
        {"id": -6, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1},
    ]}
    pick = sb.pick(snap, mob)
    assert isinstance(pick, tuple) and pick[1] == "Fire Impact"


def test_scenario_20_ignition_unavailable_below_3_fire_elements(clock: Clock) -> None:
    """20. Ignition remains unavailable with 0, 1, or 2 Fire Elements."""
    _g, _b, sb = fight(clock, ELF, ["Blazing Combo"], 0)
    assert sb.combos is not None
    sb.available_to_channel_without_penalty = True
    for n in (0, 1, 2):
        sb.combos.built = n
        assert not sb.combos.ignition_before({"name": "Pyroblast"}, elements=None)


@pytest.mark.parametrize(("permission", "planned", "eligible"), [
    (True, "Pyroblast", True),
    (True, "Blazing Storm", True),
    (False, "Pyroblast", False),
    (False, "Blazing Storm", False),
    (True, "Fireball", False),
    (True, "Fire Impact", False),
])
def test_scenario_21_ignition_requires_permission_and_a_planned_channel(
    clock: Clock, permission: bool, planned: str, eligible: bool,
) -> None:
    _g, _b, sb = fight(clock, [*ELF, "Blazing Storm"], ["Blazing Combo"], 0)
    assert sb.combos is not None
    assert combos.RELEASE_AT == 3
    sb.available_to_channel_without_penalty = permission
    for n in (3, 4, 5):
        sb.combos.built = n
        assert sb.combos.ignition_before({"name": planned}, elements=None) is eligible


def test_scenario_22_fireball_and_flame_combo_resets_fire_impact(clock: Clock) -> None:
    """Confirmed Fireball resets allow alternation even when Fire Impact itself crits."""
    g, _b, _sb = fight(clock, ["Fireball", "Fire Impact"], ["Flame Combo"], 30,
                       crit=lambda _name, _n: True, resets={"Fireball": ("Flame Combo", "Fire Impact")})
    assert g.log[:8] == ["Fire Impact*", "Fireball*"] * 4


@pytest.mark.parametrize(("active", "result", "cooldown_age"), [
    (True, 5, 1.5),
    (True, 5, None),
    (True, 5, 6.1),
    (True, 1, 1.5),
    (False, 5, 1.5),
])
def test_flame_combo_resets_cooldown_without_prioritizing_fire_impact(
    clock: Clock, active: bool, result: int, cooldown_age: float | None,
) -> None:
    g = Game(clock, ["Fireball", "Fire Impact"], ["Flame Combo"] if active else [])
    sb = combat.SkillBook(as_parts(Bot(g, {"skill_use": {"Fireball": "ready"}})))
    assert sb.combos is not None
    impact = sb.skill("Fire Impact")
    assert impact is not None
    if cooldown_age is not None:
        sb.used["Fire Impact"] = clock.now - cooldown_age
    old_cooldown = sb.used.get("Fire Impact")
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, result, 10000]]}, ME)
    reset = active and result == 5
    assert "Fire Impact" not in sb.combos.primed
    assert sb.used.get("Fire Impact") == (None if reset else old_cooldown)
    assert sb.ready(impact, clock.now) is (reset or cooldown_age is None)

    clock.advance(2)
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    me = {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}
    snap = {"self": me, "entities": [me, mob]}
    picked = sb.pick(snap, mob)
    assert isinstance(picked, tuple) and picked[1] == "Fireball"
    # Normal selection can use the reset skill immediately when Fireball is cooling down.
    clock.advance(2)
    sb.used["Fireball"] = clock.now
    picked = sb.pick(snap, mob)
    if reset or cooldown_age is None or cooldown_age >= 6:
        assert isinstance(picked, tuple) and picked[1] == "Fire Impact"
    else:
        assert picked in (None, "wait")


@pytest.mark.parametrize("already_unlocked", [False, True])
@pytest.mark.parametrize(("pack", "channel"), [(1, "Pyroblast"), (2, "Blazing Storm")])
def test_flame_combo_and_blazing_combo_share_one_fire_impact(
    clock: Clock, already_unlocked: bool, pack: int, channel: str,
) -> None:
    g = Game(clock, [*ELF, "Blazing Storm"], ["Flame Combo", "Blazing Combo"])
    sb = combat.SkillBook(as_parts(Bot(g)))
    assert sb.combos is not None
    sb.available_to_channel_without_penalty = already_unlocked
    sb.used["Fire Impact"] = clock.now - 1.5
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 5, 10000]]}, ME)
    assert "Fire Impact" not in sb.used
    assert "Fire Impact" not in sb.combos.primed
    assert sb.pending_channelling is not already_unlocked
    assert sb.available_to_channel_without_penalty is already_unlocked
    assert sb.combos.streak == (0 if already_unlocked else 1)

    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    me = {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}
    mobs = [mob] if pack == 1 else [mob, dict(mob, id=-6, y=51)]
    snap = {"self": me, "entities": [me, *mobs]}
    clock.advance(2)
    picked = sb.pick(snap, mob)
    assert isinstance(picked, tuple) and picked[1] == (channel if already_unlocked else "Fire Impact")
    if already_unlocked:
        return

    clock.advance(0.25)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 12000]]}, ME)
    assert "Fire Impact" not in sb.combos.primed
    assert not sb.pending_channelling
    assert sb.available_to_channel_without_penalty
    clock.advance(2)
    picked = sb.pick(snap, mob)
    assert isinstance(picked, tuple) and picked[1] == channel


# ---------------------------------------------------------------- Solution 3 Tests (Scenarios 1-12)

def test_solution3_01_and_02_fire_impact_first_skill_and_no_self_trigger(clock: Clock) -> None:
    """1, 2. Fire Impact can cast first and does not trigger pending_channelling on itself."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False
    # Fire Impact lands guaranteed crit first
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    # Does not trigger itself: pending_channelling remains False
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False
    assert sb.combos is not None and sb.combos.fire_impact_crit is True


def test_solution3_03_subsequent_crit_after_fire_impact_grants_channeling(clock: Clock) -> None:
    """3. Subsequent eligible skill crit after Fire Impact grants channeling permission."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    assert sb.available_to_channel_without_penalty is False
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 5, 6000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    assert sb.pending_channelling is False


def test_solution3_04_fire_impact_priority_after_another_skill_crits(clock: Clock) -> None:
    """4. Fire Impact priority still works when another eligible skill crits first."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 5, 6000]]}, ME)
    assert sb.pending_channelling is True
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob,
    ]}
    clock.advance(1.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick = sb.pick(snap, mob)
    assert isinstance(pick, tuple) and pick[1] == "Fire Impact"


def test_solution3_05_fire_impact_cannot_create_priority_loop(clock: Clock) -> None:
    """5. Fire Impact completing the sequence does not re-prime itself or create a priority loop."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 9400]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is True
    assert sb.combos is not None and sb.combos.fire_impact_crit is False
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob,
    ]}
    clock.advance(1.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick = sb.pick(snap, mob)
    assert isinstance(pick, tuple) and pick[1] == "Pyroblast"


def test_solution3_06_and_07_talent_activation_required(clock: Clock) -> None:
    """6, 7. Required Talent (Blazing Combo) must be activated; inactive Talent prevents combo."""
    g_with = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b_with = Bot(g_with)
    sb_with = combat.SkillBook(as_parts(b_with))
    sb_with._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 5, 6000]]}, ME)
    assert sb_with.pending_channelling is True

    g_without = Game(clock, ELF, ["Elven Fire"], version=28)
    b_without = Bot(g_without)
    sb_without = combat.SkillBook(as_parts(b_without))
    sb_without._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 5, 6000]]}, ME)
    assert sb_without.pending_channelling is False
    assert sb_without.available_to_channel_without_penalty is False


def test_solution3_08_pyroblast_not_wasted_on_nearly_dying_monster(clock: Clock) -> None:
    """8. Pyroblast is not wasted on a monster expected to die from normal attacks."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    mob_dying = {"id": -5, "x": 54, "y": 50, "hp": 2000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob_dying,
    ]}
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick = sb.pick(snap, mob_dying)
    assert isinstance(pick, tuple) and pick[1] == "Fireball"
    assert sb.available_to_channel_without_penalty is True


def test_solution3_09_pyroblast_saved_for_next_monster(clock: Clock) -> None:
    """9. Pyroblast is saved and used on the next monster after current dying monster dies."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    mob_dying = {"id": -5, "x": 54, "y": 50, "hp": 1500, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    mob_next = {"id": -6, "x": 55, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap1 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob_dying, mob_next,
    ]}
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick1 = sb.pick(snap1, mob_dying)
    assert isinstance(pick1, tuple) and pick1[1] == "Fireball"

    # Fallback normal attack lands on dying target without critting; permission preserved
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 1, 1500]]}, ME)
    assert sb.available_to_channel_without_penalty is True

    sb.forget_target(-5)
    assert sb.available_to_channel_without_penalty is True

    snap2 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob_next,
    ]}
    clock.advance(1.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick2 = sb.pick(snap2, mob_next)
    assert isinstance(pick2, tuple) and pick2[1] == "Pyroblast"

    # Casting Pyroblast on fresh target consumes the permission
    clock.advance(4.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-6, 1, 30000]]}, ME)
    assert sb.available_to_channel_without_penalty is False


def test_solution3_channeling_priority_over_wanted_skills(clock: Clock) -> None:
    """Channeling permission takes priority over wanted ('ready') skills on fresh target."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    # Mark Fireball and Fire Impact as 'ready' (wanted) skills
    for row in sb.slots.values():
        for s in row:
            if s["name"] in ("Fireball", "Fire Impact"):
                s["use"] = "ready"
    sb.available_to_channel_without_penalty = True
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    mob = {"id": -6, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob,
    ]}
    pick = sb.pick(snap, mob)
    assert isinstance(pick, tuple) and pick[1] == "Pyroblast"


def test_solution3_10_pyroblast_respects_normal_restrictions(clock: Clock) -> None:
    """10. Pyroblast still respects cooldown, range, affordability, and channeling restrictions."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    assert not sb.available_to_channel_without_penalty
    skills, _ = sb._usable(0, False, clock.now)
    assert "Pyroblast" not in {s["name"] for s in skills}

    sb.available_to_channel_without_penalty = True
    sb.used["Pyroblast"] = clock.now
    skills, _ = sb._usable(0, False, clock.now)
    assert "Pyroblast" not in {s["name"] for s in skills}


def test_solution3_11_ignition_threshold_remains_3_fire_elements(clock: Clock) -> None:
    """11. Ignition threshold remains unchanged at 3 Fire Elements."""
    assert combos.RELEASE_AT == 3


def test_solution3_pack_vs_single_target_channeling(clock: Clock) -> None:
    """Solution 3 picks Blazing Storm on packs (>= 2 foes) and Pyroblast on single target."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0

    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap_single = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob,
    ]}
    pick_single = sb.pick(snap_single, mob)
    assert isinstance(pick_single, tuple) and pick_single[1] == "Pyroblast"

    clock.advance(2.0)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0

    snap_pack = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000},
        mob,
        {"id": -6, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1},
        {"id": -7, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1},
    ]}
    pick_pack = sb.pick(snap_pack, mob)
    assert isinstance(pick_pack, tuple) and pick_pack[1] == "Blazing Storm"


def test_solution3_pyroblast_crit_primes_fire_impact(clock: Clock) -> None:
    """When Pyroblast crits, permission remains active throughout chain and primes Fire Impact."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True

    # Pyroblast deals a critical hit
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-6, 5, 45000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    assert sb.pending_channelling is True


def test_solution3_blazing_storm_consumes_permission(clock: Clock) -> None:
    """When Blazing Storm hits, channeling permission is consumed."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True

    # Blazing Storm deals non-crit hit
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Blazing Storm", "hits": [[-6, 1, 20000]]}, ME)
    assert sb.available_to_channel_without_penalty is False
    assert sb.pending_channelling is False


def test_solution3_blazing_storm_saved_on_single_dying_monster(clock: Clock) -> None:
    """Blazing Storm on a single nearly dying monster is saved for the next target."""
    g = Game(clock, ["Fireball", "Fire Impact", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0

    mob_dying = {"id": -5, "x": 54, "y": 50, "hp": 1500, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob_dying,
    ]}
    pick = sb.pick(snap, mob_dying)
    assert isinstance(pick, tuple) and pick[1] == "Fireball"
    assert sb.saving_pyroblast_for_next is True


def test_solution3_full_cross_monster_combo_lifecycle(clock: Clock) -> None:
    """Full lifecycle: Mob 1 dies to Fireball crit -> Mob 2 receives Fire Impact -> Mob 2 crits -> Mob 3 receives Pyroblast."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Flame Combo", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))

    mob1 = {"id": -1, "x": 54, "y": 50, "hp": 2000, "maxhp": 2000, "name": "Wolf", "kind": "monster", "side": 1}
    mob2 = {"id": -2, "x": 54, "y": 50, "hp": 5000, "maxhp": 5000, "name": "Wolf", "kind": "monster", "side": 1}
    mob3 = {"id": -3, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}

    # Step 1: Fire Impact is on cooldown, so Fireball is cast on mob1
    sb.used["Fire Impact"] = clock.now
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    snap1 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob1]}
    p1 = sb.pick(snap1, mob1)
    assert isinstance(p1, tuple) and p1[1] == "Fireball"

    # Step 2: Fireball crits on mob1 and kills it (1-shot)
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 46000]]}, ME)
    assert sb.pending_channelling is True
    assert sb.available_to_channel_without_penalty is False

    # Mob 1 dies and is forgotten
    sb.forget_target(-1)
    assert sb.pending_channelling is True  # Preserved across death

    # Step 3: Switch to mob2; Fire Impact must be prioritized
    clock.advance(1.0)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    snap2 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob2]}
    p2 = sb.pick(snap2, mob2)
    assert isinstance(p2, tuple) and p2[1] == "Fire Impact"

    # Step 4: Fire Impact lands crit on mob2, completing 2 crits
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-2, 5, 35000]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is True

    # Step 5: Mob 2 is dying from that hit; finish with basic attack, save Pyroblast for mob3
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    mob2_low = dict(mob2, hp=500)
    snap2_low = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob2_low]}
    p2_fin = sb.pick(snap2_low, mob2_low)
    assert isinstance(p2_fin, tuple) and p2_fin[1] == "Fireball"
    assert sb.saving_pyroblast_for_next is True
    assert sb.available_to_channel_without_penalty is True

    # Mob 2 dies
    clock.advance(1.0)
    sb.forget_target(-2)
    assert sb.available_to_channel_without_penalty is True

    # Step 6: Mob 3 targeted; Pyroblast selected without channeling
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    snap3 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob3]}
    p3 = sb.pick(snap3, mob3)
    assert isinstance(p3, tuple) and p3[1] == "Pyroblast"


def test_solution3_filler_non_crit_does_not_wipe_channeling_or_pending(clock: Clock) -> None:
    """Non-crit filler attacks during approach do not wipe channeling permission or pending priority."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Flame Combo", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))

    # Scenario A: Channeling permission active, follow Fireball non-crits on approach
    sb.available_to_channel_without_penalty = True
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-2, 3, 25000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    # Now in range of mob, Pyroblast is picked
    clock.advance(1.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    mob = {"id": -2, "x": 54, "y": 50, "hp": 60000, "maxhp": 60000, "name": "Skeleton", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob]}
    assert sb.pick(snap, mob) == ("h", "Pyroblast")

    # Scenario B: Fire Impact unavailable (on cooldown); fallback normal Fireball misses/hits normally -> whole sequence restarts
    sb.available_to_channel_without_penalty = False
    sb.pending_channelling = True
    sb.used["Fire Impact"] = clock.now  # Cooldown active
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-2, 3, 25000]]}, ME)
    # Sequence restarted because fallback skill did not crit
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False


def test_solution3_monster_to_monster_crit_sequence_14_monsters(clock: Clock) -> None:
    """Monster-to-Monster Crit Sequence Logic across 14 dummy monsters:
    - 14 dummy monsters with healths: 60000, 45000, 55000, 98000, 250000, 10000, 400000.
    - Groups and individual encounters with spawning delays using clock fixture.
    - Preserves pending_channelling across monster death when 1 crit remaining.
    - Prioritizes Fire Impact on next fresh monster.
    - Handles missed hits by resetting pending_channelling = False without granting channeling.
    - Handles successful Fire Impact crits by setting available_to_channel_without_penalty = True.
    - Continues rotation: Pyroblast on single target, Blazing Storm on groups, without channeling penalty.
    - Saves Pyroblast on dying target (< base attack damage) and uses normal attack fallback.
    """
    import random
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Flame Combo", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.gaps = [1.2, 1.2, 1.2, 1.2]

    # 14 dummy monsters with requested health values: 60000, 45000, 55000, 98000, 250000, 10000, 400000
    hps = [60000, 45000, 55000, 98000, 250000, 10000, 400000] * 2
    assert len(hps) == 14
    rng = random.Random(1337)

    # --- Round 1: Individual Mob 0 (HP: 10000) ---
    sb.used["Fire Impact"] = clock.now
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    mob0 = {"id": -1, "x": 54, "y": 50, "hp": 10000, "maxhp": 10000, "name": "Dummy0", "kind": "monster", "side": 1}
    snap0 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob0]}
    assert sb.pick(snap0, mob0) == ("y", "Fireball")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    assert sb.pending_channelling is True
    assert sb.available_to_channel_without_penalty is False

    # Requirement 1: Pending channel state preserved across death
    sb.forget_target(-1)
    assert sb.pending_channelling is True

    clock.advance(rng.uniform(1.5, 3.0) + 4.5)  # Delay between spawns, Fire Impact CD elapses

    # --- Round 2: Individual Mob 1 (HP: 60000) ---
    mob1 = {"id": -2, "x": 54, "y": 50, "hp": 60000, "maxhp": 60000, "name": "Dummy1", "kind": "monster", "side": 1}
    snap1 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob1]}
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0

    # Requirement 2: Cast Fire Impact on next monster
    assert sb.pick(snap1, mob1) == ("u", "Fire Impact")

    # Requirement 3: Missed Fire Impact resets pending_channelling = False
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-2, 2, 0]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False

    clock.advance(1.2)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap1, mob1) == ("y", "Fireball")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-2, 5, 25000]]}, ME)
    assert sb.pending_channelling is True
    sb.forget_target(-2)
    assert sb.pending_channelling is True

    clock.advance(rng.uniform(1.5, 3.0) + 4.5)

    # --- Round 3: Individual Mob 2 (HP: 250000) ---
    mob2 = {"id": -3, "x": 54, "y": 50, "hp": 250000, "maxhp": 250000, "name": "Dummy2", "kind": "monster", "side": 1}
    snap2 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob2]}
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap2, mob2) == ("u", "Fire Impact")

    # Requirement 4: Successful crit sets available_to_channel_without_penalty = True
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-3, 5, 38000]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is True

    # Requirement 5: Cast Pyroblast on next fresh monster without penalty
    clock.advance(1.2)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap2, mob2) == ("h", "Pyroblast")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-3, 1, 65000]]}, ME)
    assert sb.available_to_channel_without_penalty is False
    sb.forget_target(-3)

    clock.advance(rng.uniform(1.5, 3.0) + 4.5)

    # --- Round 4: Group 1 (Mob 3 [HP: 98000] and Mob 4 [HP: 55000], pack = 2) ---
    mob3 = {"id": -4, "x": 54, "y": 50, "hp": 98000, "maxhp": 98000, "name": "Dummy3", "kind": "monster", "side": 1}
    mob4 = {"id": -5, "x": 55, "y": 51, "hp": 55000, "maxhp": 55000, "name": "Dummy4", "kind": "monster", "side": 1}
    snap_grp1 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob3, mob4]}

    sb.used["Fire Impact"] = clock.now
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_grp1, mob3) == ("y", "Fireball")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-4, 5, 45000]]}, ME)
    assert sb.pending_channelling is True
    sb.forget_target(-4)

    clock.advance(5.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_grp1, mob4) == ("u", "Fire Impact")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-5, 5, 38000]]}, ME)
    assert sb.available_to_channel_without_penalty is True

    # Group target -> Blazing Storm
    clock.advance(1.2)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_grp1, mob4) == ("j", "Blazing Storm")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Blazing Storm", "hits": [[-5, 1, 30000]]}, ME)
    assert sb.available_to_channel_without_penalty is False
    sb.forget_target(-5)

    clock.advance(rng.uniform(1.5, 3.0) + 4.5)

    # --- Round 5: Group 2 (Mob 5 [HP: 400000] and Mob 6 [HP: 45000], pack = 2) ---
    mob5 = {"id": -6, "x": 54, "y": 50, "hp": 400000, "maxhp": 400000, "name": "Dummy5", "kind": "monster", "side": 1}
    mob6 = {"id": -7, "x": 55, "y": 50, "hp": 45000, "maxhp": 45000, "name": "Dummy6", "kind": "monster", "side": 1}
    snap_grp2 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob5, mob6]}

    sb.used["Fire Impact"] = clock.now
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_grp2, mob5) == ("y", "Fireball")
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-6, 5, 45000]]}, ME)
    assert sb.pending_channelling is True

    sb.forget_target(-6)
    assert sb.pending_channelling is True

    clock.advance(5.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_grp2, mob6) == ("u", "Fire Impact")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-7, 5, 38000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    sb.forget_target(-7)

    clock.advance(rng.uniform(1.5, 3.0))

    # --- Round 6: Individual Mob 7 (HP: 10000, low HP target) ---
    mob7 = {"id": -8, "x": 54, "y": 50, "hp": 5000, "maxhp": 10000, "name": "Dummy7", "kind": "monster", "side": 1}
    snap_mob7 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob7]}
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0

    # Pyroblast preserved, Fireball used to finish off low-HP Mob 7
    assert sb.pick(snap_mob7, mob7) == ("y", "Fireball")
    assert sb.saving_pyroblast_for_next is True
    assert sb.available_to_channel_without_penalty is True

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-8, 1, 7800]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    sb.forget_target(-8)
    assert sb.available_to_channel_without_penalty is True

    clock.advance(rng.uniform(1.5, 3.0))

    # --- Round 7: Individual Mob 8 (HP: 400000, healthy) ---
    mob8 = {"id": -9, "x": 54, "y": 50, "hp": 400000, "maxhp": 400000, "name": "Dummy8", "kind": "monster", "side": 1}
    snap_mob8 = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [mob8]}
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_mob8, mob8) == ("h", "Pyroblast")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-9, 1, 65000]]}, ME)
    assert sb.available_to_channel_without_penalty is False
    sb.forget_target(-9)

    # --- Round 8: Group 3 (Mobs 9, 10, 11, 12, 13: remaining HPs [60000, 45000, 55000, 98000, 250000]) ---
    remaining_hps = [60000, 45000, 55000, 98000, 250000]
    mobs_rem = [
        {"id": -10 - i, "x": 54 + i * 0.5, "y": 50, "hp": hp_val, "maxhp": hp_val, "name": f"Dummy{9+i}", "kind": "monster", "side": 1}
        for i, hp_val in enumerate(remaining_hps)
    ]
    assert len(mobs_rem) + 9 == 14

    snap_rem = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": mobs_rem}
    clock.advance(rng.uniform(1.5, 3.0) + 4.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0

    sb.used["Fire Impact"] = clock.now
    assert sb.pick(snap_rem, mobs_rem[0]) == ("y", "Fireball")
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-10, 5, 45000]]}, ME)
    assert sb.pending_channelling is True
    sb.forget_target(-10)

    clock.advance(5.5)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_rem, mobs_rem[1]) == ("u", "Fire Impact")

    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-11, 5, 38000]]}, ME)
    assert sb.available_to_channel_without_penalty is True

    clock.advance(1.2)
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    assert sb.pick(snap_rem, mobs_rem[1]) == ("j", "Blazing Storm")


def test_follow_key_overwrites_with_pyroblast_and_falls_back_on_miss(clock: Clock) -> None:
    """When available_to_channel_without_penalty is True, follow_key returns Pyroblast's key;
    if Pyroblast misses, available_to_channel_without_penalty becomes False and follow_key falls back
    to Fireball."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    pyro_key = next(s["key"] for s in sb.slots[0] if s["name"] == "Pyroblast")
    base_key = next(s["key"] for s in sb.slots[0] if s["name"] == "Fireball")

    # Initial state: base attack follow key
    assert sb.available_to_channel_without_penalty is False
    assert sb.follow_key() == base_key

    # Fireball crits -> pending_channelling becomes True
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 20000]]}, ME)
    assert sb.pending_channelling is True
    assert sb.available_to_channel_without_penalty is False

    # Fire Impact deals critical hit -> completes sequence and available_to_channel_without_penalty becomes True
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-1, 5, 20000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    # follow_key is overwritten with Pyroblast's key
    assert sb.follow_key() == pyro_key

    # Pyroblast misses (hit result code 2 = dodged)
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-1, 2, 0]]}, ME)
    assert sb.available_to_channel_without_penalty is False
    # follow_key falls back to base attack (Fireball)
    assert sb.follow_key() == base_key


def test_unpenalized_channeling_allows_sb_pick_at_long_range(clock: Clock) -> None:
    """When available_to_channel_without_penalty is True, sb.pick is allowed to select Pyroblast
    even when distance is beyond normal base reach (e.g. 25 yards)."""
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))

    mob_far: dict[str, Any] = {"id": -2, "x": 75, "y": 50, "hp": 66000, "maxhp": 66000}
    snap_far: dict[str, Any] = {
        "self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000},
        "entities": [mob_far],
    }
    # Initially without permission: not in range at 25 yards
    assert sb.available_to_channel_without_penalty is False
    assert sb.pick(snap_far, mob_far) is None

    # Trigger unpenalized state via Fireball crit + Fire Impact crit
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    clock.advance(1.2)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-1, 5, 37000]]}, ME)
    assert sb.available_to_channel_without_penalty is True

    # In unpenalized state: sb.pick is allowed at long range and returns Pyroblast
    clock.advance(1.2)
    pick = sb.pick(snap_far, mob_far)
    assert isinstance(pick, tuple) and pick[1] == "Pyroblast"


def test_elf_crit_across_monsters_and_no_time_limit(clock: Clock) -> None:
    """Elf has no time limit (RESET_WINDOW does not expire combo).
    Crit on monster A (monster A dies), crit on monster B (monster B dies),
    cast Pyroblast on monster C because available_to_channel_without_penalty = True.
    """
    g = Game(clock, ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm"], ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))

    mob_c: dict[str, Any] = {"id": -3, "x": 55, "y": 50, "hp": 50000, "maxhp": 50000}

    # 1. Crit on monster A
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    assert sb.pending_channelling is True
    assert sb.available_to_channel_without_penalty is False

    # Monster A dies
    sb.forget_target(-1)
    assert sb.pending_channelling is True

    # 45 seconds pass (way beyond 15s reset window): state must NOT expire!
    clock.advance(45.0)
    assert sb.pending_channelling is True

    # 2. Crit on monster B (Fire Impact lands 2nd crit)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-2, 5, 37000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    assert sb.pending_channelling is False

    # Monster B dies
    sb.forget_target(-2)
    assert sb.available_to_channel_without_penalty is True

    # Another 60 seconds pass: state must NOT expire!
    clock.advance(60.0)
    assert sb.available_to_channel_without_penalty is True

    # 3. Monster C engaged: Pyroblast is cast!
    snap_c: dict[str, Any] = {
        "self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000},
        "entities": [mob_c],
    }
    pick = sb.pick(snap_c, mob_c)
    assert isinstance(pick, tuple) and pick[1] == "Pyroblast"
    assert sb.follow_key() == pick[0]

    # Non-crit Pyroblast completes cast: consumes permission
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Pyroblast", "hits": [[-3, 1, 30000]]}, ME)
    assert sb.available_to_channel_without_penalty is False


def test_fire_impact_unavailable_fallback_skill_crit_unlocks_and_miss_resets(clock: Clock) -> None:
    """When Fire Impact is unavailable, casting normally unlocks on crit and restarts sequence on miss/normal hit."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))

    # Test Miss / Normal Hit: Whole sequence restarts
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    assert sb.pending_channelling is True

    clock.advance(1.5)
    # Fire Impact unavailable, Fireball cast misses / hits normally -> sequence restarts!
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 1, 15000]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False

    # Test Crit: Channeling unlocks
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    assert sb.pending_channelling is True

    clock.advance(1.5)
    # Fire Impact unavailable, Fireball cast lands a CRIT -> unlocks channeling!
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is True


def test_fire_impact_first_crit_prevents_loop_and_unlocks_on_next_other_crit(clock: Clock) -> None:
    """When Fire Impact crits first, it cannot chain into itself; another skill crit completes the sequence."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))

    # 1. Fire Impact crits first
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fire Impact", "hits": [[-1, 5, 35000]]}, ME)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is False
    assert sb.combos is not None and sb.combos.fire_impact_crit is True

    # Check that Fire Impact is NOT picked again (no self-loop)
    mob = {"id": -1, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    clock.advance(1.5)
    skills, _ = sb._usable(0, False, clock.now)
    assert sb.combos is not None
    picked = sb.combos.pick(skills, mob, clock.now, lambda s: True, elements=0)
    assert picked is None or picked["name"] != "Fire Impact"

    # 2. Another skill (Fireball) crits -> sequence complete!
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-1, 5, 25000]]}, ME)
    assert sb.available_to_channel_without_penalty is True
    assert sb.combos.fire_impact_crit is False


def test_pyroblast_selected_on_moderate_hp_target(clock: Clock) -> None:
    """When channeling is unlocked, Pyroblast is cast on alive targets (e.g. 33k HP in Skull Cave) without being diverted."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True

    # Monster with 33,000 HP (typical Skull Cave mob after initial hit)
    mob = {"id": -5, "x": 54, "y": 50, "hp": 33000, "maxhp": 60000, "name": "Skeleton", "kind": "monster", "side": 1}
    snap = {
        "self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000},
        "entities": [mob],
    }
    sb.press_confirmed = True
    sb.last_cast = clock.now - 2.0
    pick = sb.pick(snap, mob)
    assert isinstance(pick, tuple) and pick[1] == "Pyroblast"






