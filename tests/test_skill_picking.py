"""How the skill picker chooses, and what it learns from the game's cast reports."""

from __future__ import annotations

import pytest
from conftest import as_parts, data, fight, Bot

from unclefarmer.engine import combat
from unclefarmer.engine.combat import Skill
from conftest import Clock
from typing import Any


def test_fireball_fills_not_the_long_pyroblast(clock: Clock) -> None:
    g, _b, _sb = fight(clock, ["Fireball", "Fire Impact", "Pyroblast"], [], 40)
    assert "Pyroblast" not in g.log and g.log.count("Fireball") > 10, g.log


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
