"""The player's say over each skill (Advanced settings, Skills tab), fought out against the pretend
game: never, only when HP is low, whenever it's ready, and what happens to skills the list doesn't
name."""

from __future__ import annotations

from conftest import as_parts, fight, Bot, Game, ME

from unclefarmer.engine import combat
from conftest import Clock

WARRIOR = ["Bash", "Cleave", "Sweep Attack"]   # no Execute: the pretend game has no rage to hold it back


def test_the_bot_decides_by_default(clock: Clock):
    g, _b, _sb = fight(clock, WARRIOR, seconds=20)
    assert "Cleave" in g.log and "Bash" in g.log


def test_a_skill_set_to_never_is_never_pressed(clock: Clock):
    g, _b, _sb = fight(clock, WARRIOR, seconds=30, profile={"skill_use": {"Cleave": "never"}})
    assert "Cleave" not in g.log
    assert "Bash" in g.log


def test_only_the_skills_the_player_names(clock: Clock):
    g, _b, _sb = fight(clock, WARRIOR, seconds=30,
                       profile={"skill_use": {"Bash": "auto"}, "skill_use_others": "never"})
    assert g.log and set(g.log) == {"Bash"}


def test_whenever_ready_presses_a_skill_the_bot_would_leave(clock: Clock):
    # Rip and Tear is a control skill: the bot leaves those out when farming, on its own
    g, _b, _sb = fight(clock, ["Bash", "Rip and Tear"], seconds=40)
    assert "Rip and Tear" not in g.log
    g, _b, _sb = fight(clock, ["Bash", "Rip and Tear"], seconds=40,
                       profile={"skill_use": {"Rip and Tear": "ready"}})
    assert g.log.count("Rip and Tear") >= 2, g.log      # 30 s cooldown: at the start and again


def test_whenever_ready_goes_before_the_bots_choice(clock: Clock):
    g, _b, _sb = fight(clock, WARRIOR, seconds=6, profile={"skill_use": {"Sweep Attack": "ready"}})
    assert g.log[0] == "Sweep Attack", g.log


def test_emergency_only_skills_wait_for_low_hp(clock: Clock):
    g, _b, _sb = fight(clock, WARRIOR, seconds=30, profile={"skill_use": {"Cleave": "emergency"}})
    assert "Cleave" not in g.log           # full HP all fight


def emergency_pick(clock: Clock, use: str, hp: int):
    g = Game(clock, ["Bash", "Formless"])
    b = Bot(g, {"skill_use": {"Formless": use}})
    sb = combat.SkillBook(as_parts(b))
    me = {"id": ME, "hp": hp, "maxhp": 100000}
    return sb.emergency(me, clock.now)


def test_a_shield_kept_for_emergencies_comes_out_when_hp_is_low(clock: Clock):
    pick = emergency_pick(clock, "emergency", 30000)
    assert pick is not None and pick[1] == "Formless"
    pick = emergency_pick(clock, "auto", 30000)
    assert pick is not None and pick[1] == "Formless"
    assert emergency_pick(clock, "emergency", 90000) is None


def test_a_shield_set_to_never_stays_off_even_in_an_emergency(clock: Clock):
    assert emergency_pick(clock, "never", 20000) is None


def test_a_skill_set_to_never_is_not_reported_missing(clock: Clock):
    g = Game(clock, ["Bash"])
    b = Bot(g, {"skill_use": {"Formless": "never"}})
    sb = combat.SkillBook(as_parts(b))
    sb.tell_missing({"Bash", "Formless"})
    assert not any("Formless" in line for line in b.lines)
    b2 = Bot(g)
    sb2 = combat.SkillBook(as_parts(b2))
    sb2.tell_missing({"Bash", "Formless"})
    assert any("Formless" in line for line in b2.lines)


def test_an_unknown_choice_counts_as_the_bot_decides(clock: Clock):
    g = Game(clock, ["Bash"])
    sb = combat.SkillBook(as_parts(Bot(g, {"skill_use": {"Bash": "sometimes"}})))
    assert sb.use_of("Bash") == "auto"
