"""Combos from activated talents and the skill texts, fought out against the pretend game."""

from conftest import fight, followed_by

from unclefarmer.engine import combos

WARRIOR = ["Bash", "Cleave", "Sweep Attack", "Leap Slash", "Execute"]
HUNTER = ["Arcane Shot", "Aimed Shot", "Multi Shot", "Rapid Arrows", "Marked Shot"]
MAGE = ["Magic Sphere", "Ice Lance", "Aqua Jet", "Ice Arrow", "Frost Nova", "Ice Storm"]
ELF = ["Fireball", "Fire Impact", "Pyroblast", "Ignition"]
PRIEST = ["Retribution", "Mind Whip", "Divine Nova", "Holy Light Wave"]
WITCHER = ["Shadow Arrow", "Corrosion Spell", "Skeleton Explosion", "Dead Toxin"]


def test_every_rule_has_one_kind_and_a_description():
    for r in combos.RULES:
        assert sum(k in r for k in combos.KINDS) == 1, r
        kind = next(k for k in combos.KINDS if k in r)
        assert combos.describe(dict(r, kind=kind))


def test_only_activated_talents_count():
    sk = {"talents": [{"name": "Cleave Combo", "b": 1}, {"name": "Steady Hands", "b": 0}]}
    assert combos.active_talents(sk) == {"Cleave Combo"}


def test_a_rule_needs_its_skills_on_keys():
    on = {"Aimed Shot", "Marked Shot"}          # one of the three "before" targets is enough
    kinds = [r["kind"] for r in combos.rules_for({"Steady Hands"}, on)]
    assert kinds == ["before"]
    assert combos.rules_for({"Steady Hands"}, {"Marked Shot"}) == []


def test_warrior_sweep_attack_follows_cleave(clock):
    g, b, sb = fight(clock, WARRIOR, ["Combat Stance", "Cleave Combo", "Powerful Leap Attack"], 30)
    hits, total = followed_by(g.log, "Cleave", "Sweep Attack")
    assert total >= 4 and hits == total, g.log
    assert "Leap Slash" in g.log
    assert any("Cleave then Sweep Attack" in line for line in b.lines)


def test_warrior_without_the_talents_does_neither(clock):
    g, b, sb = fight(clock, WARRIOR, ["Combat Stance"], 30)
    hits, total = followed_by(g.log, "Cleave", "Sweep Attack")
    assert "Leap Slash" not in g.log and hits < total


def test_warrior_next_skill_sooner_after_leap_slash(clock):
    g, b, sb = fight(clock, WARRIOR, ["Combat Stance", "Powerful Leap Attack"], 60)
    t = [c["t"] / 1000.0 for c in g.casts]
    gaps = [t[i + 1] - t[i] for i, n in enumerate(g.log[:-1]) if n == "Leap Slash"]
    assert gaps and all(gap < 0.75 * sb.gcd() for gap in gaps), (gaps, sb.gcd())
    assert sb.gcd() > 1.3                   # the short gap isn't taken for the usual cooldown


def test_hunter_rapid_arrows_after_an_arcane_shot_crit(clock):
    crit = lambda name, n: name == "Arcane Shot" and n % 4 == 0      # noqa: E731
    g, b, sb = fight(clock, HUNTER, ["Aspect of the Wild", "Call of the Wilderness"], 60, dist=12, crit=crit,
                     resets={"Arcane Shot": ("Call of the Wilderness", "Rapid Arrows")})
    crits = [i for i, n in enumerate(g.log[:-1]) if n == "Arcane Shot*"]
    after = [g.log[i + 1] for i in crits]
    assert len(crits) >= 3 and after.count("Rapid Arrows") >= len(crits) - 1, g.log
    assert g.log.count("Rapid Arrows") >= 4          # more than its 30 s cooldown allows


def test_crit_combos_wait_for_agent_28(clock):
    g, b, sb = fight(clock, HUNTER, ["Aspect of the Wild", "Call of the Wilderness"], 10, dist=12, version=27)
    assert sb.combos is None
    assert any("new agent" in line for line in b.lines)


def test_hunter_aimed_shot_before_rapid_arrows(clock):
    g, b, sb = fight(clock, HUNTER, ["Aspect of the Monkey", "Steady Hands"], 40, pack=4, dist=12)
    i = g.log.index("Rapid Arrows")
    assert i > 0 and g.log[i - 1] == "Aimed Shot", g.log[:12]


def test_mage_ice_finger_chain(clock):
    g, b, sb = fight(clock, MAGE, ["Ice Lineage", "Ice Finger"], 45, dist=6)
    xs = g.log
    chains = sum(1 for i in range(len(xs) - 2) if xs[i:i + 3] == ["Aqua Jet", "Ice Arrow", "Ice Lance"])
    assert chains >= 2, xs
    assert "Magic Sphere" not in xs and xs.count("Ice Arrow") >= 5      # Ice Arrow is the filler


def test_mage_ice_lance_on_a_frozen_pack(clock):
    g, b, sb = fight(clock, ["Magic Sphere", "Ice Lance", "Frost Nova", "Ice Storm"], [], 45, pack=4, dist=5)
    hits, total = followed_by(g.log, "Frost Nova", "Ice Lance")
    assert total >= 1 and hits == total, g.log


def test_mage_freezing_cold_shortens_ice_storm(clock):
    with_talent, _b, _s = fight(clock, ["Magic Sphere", "Ice Storm"], ["Cold Lineage", "Freezing Cold"], 45,
                                pack=4, cd_less={"Ice Storm": 1.5})
    without, _b, _s = fight(clock, ["Magic Sphere", "Ice Storm"], ["Cold Lineage"], 45, pack=4)
    assert with_talent.log.count("Ice Storm") > without.log.count("Ice Storm")


def test_elf_fire_impact_after_a_fireball_crit(clock):
    crit = lambda name, n: name == "Fireball" and n % 3 == 0          # noqa: E731
    g, b, sb = fight(clock, ELF, ["Hellfire", "Flame Combo"], 60, crit=crit,
                     resets={"Fireball": ("Flame Combo", "Fire Impact")})
    crits = [i for i, n in enumerate(g.log[:-1]) if n == "Fireball*"]
    assert len(crits) >= 3 and sum(g.log[i + 1] == "Fire Impact" for i in crits) >= len(crits) - 1, g.log


def test_elf_ignition_after_three_fire_elements(clock):
    g, b, sb = fight(clock, ["Fireball", "Fire Impact", "Blazing Storm", "Ignition"], [], 60, pack=4)
    i = g.log.index("Ignition")
    assert g.log[:i].count("Blazing Storm") >= 3 and g.log[i + 1] != "Ignition", g.log


def test_elf_blazing_combo_after_two_crits(clock):
    crit = lambda name, n: name == "Fire Impact" or n % 2 == 0        # noqa: E731
    g, b, sb = fight(clock, ELF, ["Elven Fire", "Blazing Combo"], 40, crit=crit)
    assert "Pyroblast" in g.log


def test_priest_divine_whip(clock):
    g, b, sb = fight(clock, PRIEST, ["Shelter", "Divine Whip"], 40, dist=8)
    assert g.log[0] == "Mind Whip" and 2 <= g.log.count("Mind Whip") <= 4, g.log
    assert g.log.count("Divine Nova") >= 2          # used on one target while it is whipped


def test_priest_wisdom_resets_divine_nova(clock):
    crit = lambda name, n: name == "Divine Nova"                      # noqa: E731
    g, b, sb = fight(clock, PRIEST, ["Justice", "Wisdom"], 30, dist=8, pack=4, crit=crit,
                     resets={"*": ("Wisdom", "Divine Nova")})
    assert g.log[:3].count("Divine Nova*") >= 2, g.log


def test_witcher_enhanced_explosion_and_dead_toxin(clock):
    crit = lambda name, n: name == "Shadow Arrow" and n % 3 == 0      # noqa: E731
    g, b, sb = fight(clock, WITCHER, ["Soul Link", "Enhanced Explosion", "Dead Toxin Surge"], 40, dist=8, crit=crit,
                     resets={"Shadow Arrow": ("Dead Toxin Surge", "Dead Toxin")})
    assert g.log[0] == "Corrosion Spell" and g.log.count("Skeleton Explosion") >= 5, g.log
    assert sum(1 for i in range(len(g.log) - 1) if g.log[i] == "Shadow Arrow*" and g.log[i + 1] == "Dead Toxin") >= 2


def test_combos_off_in_the_profile(clock):
    g, b, sb = fight(clock, WARRIOR, ["Combat Stance", "Cleave Combo", "Powerful Leap Attack"], 20,
                     profile={"use_combos": False})
    assert sb.combos is None and "Leap Slash" not in g.log
