# pyright: reportPrivateUsage=false
"""Combos from activated talents and the skill texts, fought out against the pretend game."""

from __future__ import annotations

from collections.abc import Callable

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
    g, _b, _sb = fight(clock, ["Fireball", "Fire Impact", "Blazing Storm", "Ignition"], [], 60, pack=4, crit=crit)
    plain = [x.rstrip("*") for x in g.log]
    i = plain.index("Ignition")
    assert plain[:i].count("Blazing Storm") >= 3 and plain[i + 1] != "Ignition", g.log


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
    """14. Channeling permission resets only when the chain actually completes."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.available_to_channel_without_penalty = True
    clock.advance(1.5)
    sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Fireball", "hits": [[-5, 3, 7800]]}, ME)
    assert sb.available_to_channel_without_penalty is False


def test_scenario_15_interrupted_sequence_clears_stale_state(clock: Clock) -> None:
    """15. An interrupted or unrelated sequence clears pending channeling and streak, but saves channeling permission for the next monster."""
    g = Game(clock, ELF, ["Elven Fire", "Blazing Combo"], version=28)
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    sb.pending_channelling = True
    sb.available_to_channel_without_penalty = True
    sb.forget_target(-5)
    assert sb.pending_channelling is False
    assert sb.available_to_channel_without_penalty is True

    sb.pending_channelling = True
    sb.target_id = -5
    mob_new = {"id": -99, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, "entities": [
        {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}, mob_new,
    ]}
    sb.pick(snap, mob_new)
    assert sb.pending_channelling is False
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
    _g, _b, sb = fight(clock, ["Fireball", "Fire Impact", "Blazing Storm", "Ignition"], ["Elven Fire"], 0.1)
    assert sb.combos is not None
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    for n in (0, 1, 2):
        sb.combos.built = n
        assert sb.combos.pick(sb.slots[0], mob, clock.now, lambda s: True, elements=n) is None


def test_scenario_21_ignition_eligible_at_3_or_more_fire_elements(clock: Clock) -> None:
    """21. Ignition remains eligible at 3 or more Fire Elements, following existing release logic."""
    _g, _b, sb = fight(clock, ["Fireball", "Fire Impact", "Blazing Storm", "Ignition"], ["Elven Fire"], 0.1)
    assert sb.combos is not None
    assert combos.RELEASE_AT == 3
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    for n in (3, 4, 5):
        sb.combos.built = n
        picked = sb.combos.pick(sb.slots[0], mob, clock.now, lambda s: True, elements=n)
        assert picked is not None and picked["name"] == "Ignition"


def test_scenario_22_fireball_and_flame_combo_resets_fire_impact(clock: Clock) -> None:
    """22. Existing Fireball and Flame Combo behavior still works."""
    crit: Callable[[str, int], bool] = lambda name, n: name == "Fireball"
    g, _b, _sb = fight(clock, ["Fireball", "Fire Impact"], ["Flame Combo"], 30, crit=crit,
                       resets={"Fireball": ("Flame Combo", "Fire Impact")})
    assert g.log.count("Fireball*") >= 3
    assert g.log.count("Fire Impact") >= 3


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
