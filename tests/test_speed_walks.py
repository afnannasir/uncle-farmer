"""Speed skills on long walks (Phase 1b item 4): Wind Step, Swallow Dance, Haste and Shadow Steps
were never pressed (the picker never offers mobility skills); a long walk on foot now starts with
the fastest one that is up."""
from __future__ import annotations

from typing import Any

from conftest import Bot, Clock, Game, as_parts

from unclefarmer.engine import combat
from unclefarmer.engine.botparts.walking import WalkingMixin as Walking


def book(clock: Clock, skills: list[str], profile: dict[str, Any] | None = None) -> combat.SkillBook:
    return combat.SkillBook(as_parts(Bot(Game(clock, skills), profile)))


def test_which_skills_are_speed_skills() -> None:
    info = {x["name"]: x for cls in combat.knowledge().values() for x in cls}
    for name in ("Wind Step", "Swallow Dance", "Haste", "Shadow Steps"):
        assert combat.SkillBook.speed_skill(info[name]), name
    assert combat.speed_share(info["Haste"]) == 30 and combat.speed_share(info["Wind Step"]) == 50
    assert not combat.SkillBook.speed_skill(info["Escape"])        # a jump, not a speed buff
    assert not combat.SkillBook.speed_skill(info["Leap Slash"])


def test_the_fastest_ready_one_goes_once_per_cooldown(clock: Clock) -> None:
    sb = book(clock, ["Bash", "Wind Step"])
    got = sb.speed_now(clock.now)
    assert got is not None and got[1] == "Wind Step"
    assert sb.speed_now(clock.now + 1) is None                    # just pressed
    sb.used["Wind Step"] = clock.now + 1                          # the game cast it
    assert sb.speed_now(clock.now + 20) is None                   # 30 s cooldown
    got = sb.speed_now(clock.now + 32)
    assert got is not None and got[1] == "Wind Step"


def test_never_keeps_it_off_and_no_speed_skill_is_none(clock: Clock) -> None:
    assert book(clock, ["Bash", "Wind Step"], {"skill_use": {"Wind Step": "never"}}).speed_now(clock.now) is None
    assert book(clock, ["Bash", "Formless"]).speed_now(clock.now) is None


def test_hops_length_counts_steps_the_game_walks() -> None:
    assert Walking.hops_length((0, 0), [(10, 4), (10, 20)]) == 26
    assert Walking.hops_length((5, 5), []) == 0
    assert combat.SPEED_WALK_MIN <= 30
