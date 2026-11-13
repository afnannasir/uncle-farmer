# pyright: reportPrivateUsage=false
"""How the skill picker chooses, and what it learns from the game's cast reports."""

from __future__ import annotations

import pytest
from conftest import as_parts, data, fight, Bot, Game, ME

from unclefarmer.engine import combat
from unclefarmer.engine.combat import Skill
from conftest import Clock
from typing import Any, cast


def test_fireball_fills_not_the_long_pyroblast(clock: Clock) -> None:
    g, _b, _sb = fight(clock, ["Fireball", "Fire Impact", "Pyroblast"], [], 40)
    assert "Pyroblast" not in g.log and g.log.count("Fireball") > 10, g.log


@pytest.fixture
def elf_ignition(clock: Clock) -> tuple[combat.SkillBook, dict[str, Any], dict[str, Any]]:
    skills = ["Fireball", "Fire Impact", "Pyroblast", "Blazing Storm", "Ignition"]
    sb = combat.SkillBook(as_parts(Bot(Game(clock, skills, ["Blazing Combo"]))))
    mob = {"id": -5, "x": 54, "y": 50, "hp": 50000, "maxhp": 50000, "name": "Wolf", "kind": "monster", "side": 1}
    me = {"id": ME, "x": 50, "y": 50, "hp": 100000, "maxhp": 100000}
    return sb, {"self": me, "entities": [me, mob]}, mob


@pytest.mark.parametrize(("pack", "channel"), [(1, "Pyroblast"), (2, "Blazing Storm")])
@pytest.mark.parametrize("elements", [0, 1, 2, 3, 5])
def test_ignition_sets_up_the_planned_channel(
    clock: Clock, elf_ignition: tuple[combat.SkillBook, dict[str, Any], dict[str, Any]],
    pack: int, channel: str, elements: int,
) -> None:
    sb, snap, mob = elf_ignition
    assert sb.combos is not None
    sb.available_to_channel_without_penalty = True
    sb.combos.built = 5  # a fresh T-key reading takes precedence over the fallback count
    sb.pts, sb.pts_at = elements, clock.now
    if pack == 2:
        snap["entities"].append(dict(mob, id=-6, y=51))

    picked = sb.pick(snap, mob)
    if elements >= 3:
        assert isinstance(picked, tuple) and picked[1] == "Ignition"
        clock.advance(0.25)
        sb._fold_cast({"t": int(clock.now * 1000), "caster": ME, "skill": "Ignition"}, ME)
        assert sb.points(clock.now) == sb.combos.built == 0
        assert sb.available_to_channel_without_penalty
        picked = sb.pick(snap, mob)
    assert isinstance(picked, tuple) and picked[1] == channel


@pytest.mark.parametrize("use", ["auto", "ready"])
def test_ignition_cannot_bypass_permission_through_ordinary_picks(
    clock: Clock, elf_ignition: tuple[combat.SkillBook, dict[str, Any], dict[str, Any]], use: str,
) -> None:
    sb, snap, mob = elf_ignition
    ignition = sb.skill("Ignition")
    assert sb.combos is not None and ignition is not None
    sb.combos.built = 5
    sb.combos.primed["Ignition"] = clock.now + 5
    ignition.update(use=use, role="single_target", attack=100000)
    skills, _wanted = sb._usable(0, False, clock.now)
    assert sb.combos.pick(skills, mob, clock.now, lambda _s: True, elements=5) is None
    picked = sb.pick(snap, mob)
    assert isinstance(picked, tuple) and picked[1] != "Ignition"
    fallback = sb.fallback(clock.now)
    assert fallback is not None and fallback[1] != "Ignition"


@pytest.mark.parametrize(("blocked_by", "expected"), [
    ("channel cooldown", "Fireball"),
    ("low target HP", "Fireball"),
    ("pending Fire Impact", "Fire Impact"),
    ("Ignition cooldown", "Pyroblast"),
    ("Ignition range", "Pyroblast"),
    ("Ignition affordability", "Pyroblast"),
    ("Ignition disabled", "Pyroblast"),
])
def test_ignition_requires_a_usable_setup_and_channel(
    clock: Clock, elf_ignition: tuple[combat.SkillBook, dict[str, Any], dict[str, Any]],
    blocked_by: str, expected: str,
) -> None:
    sb, snap, mob = elf_ignition
    ignition = sb.skill("Ignition")
    assert sb.combos is not None and ignition is not None
    sb.available_to_channel_without_penalty = True
    sb.combos.built = 3
    ignition["use"] = "ready"
    if blocked_by == "channel cooldown":
        sb.used.update(Pyroblast=clock.now, **{"Blazing Storm": clock.now, "Fire Impact": clock.now})
    elif blocked_by == "low target HP":
        mob["hp"] = 100
    elif blocked_by == "pending Fire Impact":
        sb.pending_channelling = True
    elif blocked_by == "Ignition cooldown":
        sb.used["Ignition"] = clock.now
    elif blocked_by == "Ignition range":
        ignition["range"] = 1.0
    elif blocked_by == "Ignition affordability":
        ignition["combo_cost"] = 5
        sb.pts, sb.pts_at = 3, clock.now
    else:
        ignition["use"] = "never"
    picked = sb.pick(snap, mob)
    assert isinstance(picked, tuple) and picked[1] == expected


def test_a_running_dot_is_not_cast_again(clock: Clock) -> None:
    # a big target is a long fight: damage over time goes on it, but not again while it runs
    g, _b, _sb = fight(clock, ["Retribution", "Mind Whip"], ["Shelter"], 40, dist=8, mob_hp=10 ** 9)
    assert 1 <= g.log.count("Mind Whip") <= 4, g.log


def test_the_orc_uses_bloodthirsty(clock: Clock) -> None:
    g, _b, _sb = fight(clock, ["Valiant Strike", "Reckless Strike", "Bloodthirsty"], ["Rampage Stance"], 20)
    assert "Bloodthirsty" in g.log


def test_a_channel_gets_its_channel_time_to_report(clock: Clock) -> None:
    _g, _b, sb = fight(clock, ["Magic Sphere", "Ice Arrow"], [], 1)
    assert sb.burst_time("Ice Arrow") == pytest.approx(combat.BURST_MAX + 1.5)
    assert sb.burst_time("Magic Sphere") == pytest.approx(combat.BURST_MAX)


def test_presses_keep_the_global_cooldown_pace(clock: Clock) -> None:
    g, _b, _sb = fight(clock, ["Arcane Shot"], [], 40, dist=12)
    t = [c["t"] / 1000.0 for c in g.casts][3:]          # after the cooldown has been learned
    mean = (t[-1] - t[0]) / (len(t) - 1)
    assert g.gcd / mean > 0.75, mean                     # the pretend game reports 0.2 s late


class Replay:
    """An agent link that hands over a recorded fight's casts."""

    SKILLS = ["Arcane Shot", "Aimed Shot", "Multi Shot", "Rapid Arrows", "Marked Shot", "Strong-Arm Aura", "Escape"]

    def __init__(self, recorded: dict[str, Any], talents: list[str] | tuple[()]=()) -> None:
        self.casts, self.talents = recorded["casts"], talents

    def send(self, line: str) -> str:
        return "mwagent 28" if line == "version" else "tapped"

    def json(self, line: str) -> Any:
        if line == "mem skills":
            return {"combat": [{"id": i + 1, "name": n} for i, n in enumerate(self.SKILLS)],
                    "attack_keys": {str(i + 1): i + 1 for i in range(len(self.SKILLS))},
                    "talents": [{"id": 900 + i, "b": 1, "name": t} for i, t in enumerate(self.talents)]}
        if line == "mem modes":
            return {"swap_attack": True}
        if line.startswith("casts"):
            since = int(line.split()[1])
            return {"casts": [c for c in self.casts if c["t"] > since]}
        return {}


@pytest.fixture
def recorded() -> dict[str, Any]:
    return data("hunter_fight_casts.json")


def test_real_fight_global_cooldown(recorded: dict[str, Any]) -> None:
    sb = combat.SkillBook(as_parts(Bot(Replay(recorded))))
    sb.read_casts(recorded["me"])
    assert sb.cls == "Hunter"
    # a Hunter's global cooldown is about 1.2 s; network bunching and Escape (outside the global
    # cooldown) make some gaps shorter, and the estimate must not follow them
    assert 1.15 < sb.gcd() < 1.3, sorted(sb.gaps)[:5]


def test_real_fight_critical_hits(recorded: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    sb = combat.SkillBook(as_parts(Bot(Replay(recorded, ["Aspect of the Wild", "Call of the Wilderness"]))))
    seen: list[tuple[str, bool, float]] = []
    assert sb.combos is not None
    real = sb.combos.cast

    def spy(name: str, t: float, target: int | None, crit: bool, hits: int | None, info: Skill) -> tuple[str, float] | None:
        seen.append((name, crit, t))
        return real(name, t, target, crit, hits, info)

    monkeypatch.setattr(sb.combos, "cast", spy)
    sb.read_casts(recorded["me"])
    own = [c for c in recorded["casts"] if c["caster"] == recorded["me"]]
    assert len(seen) == len(own)
    assert [n for n, crit, _t in seen if crit] == ["Aimed Shot", "Arcane Shot", "Aimed Shot"]


def test_real_fight_arcane_crit_primes_rapid_arrows(recorded: dict[str, Any]) -> None:
    """Call of the Wilderness: the recorded Arcane Shot crit makes Rapid Arrows the next pick."""
    casts = recorded["casts"]
    crit_at = next(i for i, c in enumerate(casts) if c["caster"] == recorded["me"] and "Arcane" in c["skill"]
                   and any(h[1] & 7 == 5 for h in c.get("hits", [])))
    upto = dict(recorded, casts=casts[:crit_at + 1])
    sb = combat.SkillBook(as_parts(Bot(Replay(upto, ["Aspect of the Wild", "Call of the Wilderness"]))))
    sb.read_casts(recorded["me"])
    assert sb.combos is not None and "Rapid Arrows" in sb.combos.primed


def test_real_fight_records_where_casts_landed(recorded: dict[str, Any]) -> None:
    sb = combat.SkillBook(as_parts(Bot(Replay(recorded))))
    sb.read_casts(recorded["me"])
    hit = {h[0] for c in recorded["casts"] if c["caster"] == recorded["me"] for h in c.get("hits", [])}
    assert sb.landed and {target for _skill, target in sb.landed} <= hit    # where the game says each landed


def test_real_fight_one_bunched_gap_doesnt_fool_the_estimate(recorded: dict[str, Any]) -> None:
    """Early in the recording two Arcane Shots arrive 0.77 s apart (network bunching): one odd
    gap must not drag the global cooldown down to it."""
    early = dict(recorded, casts=recorded["casts"][:58])
    sb = combat.SkillBook(as_parts(Bot(Replay(early))))
    sb.read_casts(recorded["me"])
    assert min(x for x in sb.gaps if x > 0.3) < 0.8           # the odd gap is in there
    assert 1.1 < sb.gcd() < 1.3


def test_a_skill_is_not_picked_past_its_own_range(clock: Clock) -> None:
    """A Hunter's Arcane Shot reaches 20 yards. At 20 it is cast; past 20 the server refuses it,
    so it must not be picked (the old +0.5 let it be pressed at 20.5)."""
    g = Game(clock, ["Arcane Shot"])
    sb = combat.SkillBook(as_parts(Bot(g)))
    me = {"id": ME, "x": 50.0, "y": 50.0, "hp": 1000, "maxhp": 1000}
    mob = {"id": -5, "x": 70.0, "y": 50.0, "hp": 500, "maxhp": 500, "name": "Wolf", "kind": "monster", "side": 1}
    snap: dict[str, Any] = {"self": me, "entities": [me, mob]}
    assert isinstance(sb.pick(snap, mob), tuple)          # exactly at its range: still a shot
    clock.advance(1.0)                                    # its press and cooldown are behind us
    mob["x"] = 70.4                                       # it stepped back 0.4 yards
    assert sb.pick(snap, mob) is None
    assert sb.fallback(clock.now, lambda s: sb.reaches(s, 20.4)) is None


# ---------------------------------------------------------------------------------- area skills in front
MULTI: Skill = {"name": "Multi Shot", "role": "aoe", "targets": 4, "range": 20.0,
                "text": "Attack 4 targets within 20 yards and 90 degrees, have a chance to mark the enemy"}
RAPID: Skill = {"name": "Rapid Arrows", "role": "aoe", "targets": 8, "range": 15.0,
                "text": "Attack 8 targets within 15 yards and 180 degrees"}


class _Book:
    combos = None

    def gcd(self) -> float:
        return 1.2


def _view(*at: tuple[float, float]) -> combat._PickView:
    """We stand at 0,0 facing east; the first position is the target."""
    foes = [{"id": -1 - i, "name": "Scorpion", "hp": 100, "maxhp": 100, "x": x, "y": y} for i, (x, y) in enumerate(at)]
    snap = {"self": {"id": 7, "x": 0.0, "y": 0.0, "hp": 100, "maxhp": 100}, "entities": foes}
    return combat._PickView(cast(combat.SkillBook, _Book()), snap, foes[0], [], 0, 0.0)


def test_an_aimed_area_skill_counts_what_stands_in_its_arc_not_round_the_target() -> None:
    # Scorpions 12 tiles apart in front of us: none within 8 of the target (the old pack count),
    # but all three in Multi Shot's 90 degrees out to 20 yards
    v = _view((18.0, 0.0), (8.0, 6.0), (12.0, -9.0))
    assert v.pack == 1
    assert v.area_hits(MULTI) == 3
    assert v.area_hits(RAPID) == 2                    # 15 yards: the target is out of its reach


def test_what_stands_behind_us_or_too_far_is_not_counted() -> None:
    v = _view((18.0, 0.0), (-10.0, 0.0), (0.0, 12.0), (30.0, 0.0))
    assert v.area_hits(MULTI) == 1                    # behind, square to the side, past 20 yards
    assert v.area_hits(RAPID) == 1                    # 180 degrees takes the one at the side; the target is past 15


def _skill(name: str, text: str, targets: int | None, reach: float) -> Skill:
    return {"name": name, "role": "aoe", "targets": targets, "range": reach, "text": text}


def test_every_class_s_area_shapes_are_counted_their_own_way() -> None:
    sweep = _skill("Sweep Attack", "Sweep through 2 enemies 5 yards ahead, 4 seconds CD", 2, 5.0)
    whirl = _skill("Whirlwind Slash", "Deal a powerful strike to 4 enemies within a 5-yard radius", 4, 5.0)
    nova = _skill("Divine Nova", "a holy light blast around your, dealing divine damage to all enemy targets "
                  "within a 12-yard radius", 99, 12.0)
    blizzard = _skill("Blizzard", "summon ice shards to fall from the sky in an area of 10-yard radius", None, 10.0)
    skeleton = _skill("Skeleton Explosion", "throw a skeleton at 1 enemy within 10 yards and cause an explosion "
                      "on contact. The explosion deals damage to up to 5 additional targets within an "
                      "8-yard radius", 1, 10.0)
    # close in: the target 4 east, one 3 behind us, one 12 east
    v = _view((4.0, 0.0), (-3.0, 0.0), (12.0, 0.0))
    assert v.area_hits(sweep) == 1                    # 5 yards ahead: the target; behind us never
    assert v.area_hits(whirl) == 2                    # round us, 5 yards: the target and the one behind
    # further out: the target 10 east, one 4 behind us, one 6 past the target, one 6 beside it
    v = _view((10.0, 0.0), (-4.0, 0.0), (16.0, 0.0), (10.0, 6.0))
    assert v.area_hits(nova) == 3                     # round us, 12 yards: not the one 16 out
    assert v.area_hits(blizzard) == 3                 # round where it lands: not the one behind us
    assert v.area_hits(skeleton) == 3                 # round the target, 8 yards (1 + up to 5 more)


def test_a_shot_on_the_way_never_picks_a_channel(clock: Clock) -> None:
    _g, _b, sb = fight(clock, ["Magic Sphere", "Ice Arrow"], [], 1)
    names = {s["name"] for s in sb._usable(sb.page(), False, clock.now + 30)[0]}
    assert "Ice Arrow" in names
    sb.on_the_move = True
    names = {s["name"] for s in sb._usable(sb.page(), False, clock.now + 30)[0]}
    assert "Ice Arrow" not in names and "Magic Sphere" in names


WITCHER = ["Shadow Arrow", "Corrosion Spell", "Incessant Pain", "Skeleton Explosion", "Dead Toxin"]
ELF = ["Fireball", "Fire Impact", "Fire Surge", "Blazing Storm"]


def test_area_skills_go_on_two_monsters_in_the_field(clock: Clock) -> None:
    # field farming's pack size is 3, but two monsters already make an area skill the harder hit:
    # a Witcher, with no single-target skill, fired only Shadow Arrow out in the field
    g, _b, _sb = fight(clock, WITCHER, [], 30, pack=2, dist=6.0, mob_hp=250000)   # pack_min: 3
    assert "Skeleton Explosion" in g.log and "Dead Toxin" in g.log, g.log


def test_one_monster_gets_the_hardest_single_hit_not_a_weaker_area_skill(clock: Clock) -> None:
    g, _b, _sb = fight(clock, WITCHER, [], 30, pack=1, dist=6.0, mob_hp=250000)
    assert set(g.log) == {"Shadow Arrow"}, g.log      # 6000 beats Skeleton Explosion's 4100 on one


def test_area_skills_are_weighed_by_damage_not_by_how_many_they_could_take(clock: Clock) -> None:
    # Blazing Storm takes 8 targets but is a 3-second channel; Fire Surge (6) does twice the damage
    # for the time: an Elf in a pack cast only Blazing Storm
    g, _b, _sb = fight(clock, ELF, [], 30, pack=4, dist=6.0, mob_hp=250000)
    assert g.log.count("Fire Surge") >= 3, g.log         # it was never cast at all
    assert "Blazing Storm" in g.log, g.log            # the filler while Fire Surge cools down


def test_an_area_skill_with_no_damage_figure_still_waits_for_a_pack(clock: Clock) -> None:
    # Icicle's text gives no damage: it goes on a pack of pack_min, never on fewer
    two, _b, _sb = fight(clock, ["Magic Sphere", "Icicle"], [], 30, pack=2, dist=6.0, mob_hp=250000)
    three, _b, _sb = fight(clock, ["Magic Sphere", "Icicle"], [], 30, pack=3, dist=6.0, mob_hp=250000)
    assert "Icicle" not in two.log and "Icicle" in three.log, (two.log, three.log)
