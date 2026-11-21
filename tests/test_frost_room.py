# pyright: reportPrivateUsage=false
"""Frost Nova to make room (Phase 1b item 6, the Mage's part): with two or more monsters striking,
freeze them; a frozen monster doesn't swing, so the channels that follow needn't wait for it."""
from __future__ import annotations

from typing import Any

from conftest import ME, Clock, fight

from unclefarmer.engine import combat

ARROW = {"name": "Ice Arrow", "cast": 1.5}


def swing(t: float, caster: int) -> dict[str, Any]:
    return {"t": int(t * 1000), "caster": caster, "skill": "Bite", "hits": [[ME, 3, 500]], "targets": [[ME, 90000]]}


def ours(t: float, skill: str, struck: list[int]) -> dict[str, Any]:
    return {"t": int(t * 1000), "caster": ME, "skill": skill, "hits": [[m, 3, 900] for m in struck],
            "targets": [[m, 40000] for m in struck]}


def book(clock: Clock) -> combat.SkillBook:
    _g, _b, sb = fight(clock, ["Magic Sphere", "Ice Arrow", "Frost Nova"], [], 0.1, pack=2)
    sb.swings.clear()
    return sb


def test_a_frozen_monster_doesnt_hold_back_a_channel(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now + 30
    sb._note_swing(swing(now - 2.6, -5), ME)
    sb._note_swing(swing(now - 0.6, -5), ME)
    assert not sb.channel_safe(ARROW, now + 0.8)            # its next swing lands inside the channel
    sb._fold_cast(ours(now - 0.5, "Frost Nova", [-5]), ME)  # frozen until now + 4.5
    assert sb.channel_safe(ARROW, now + 0.8)
    assert not sb.channel_safe(ARROW, now + 3.2)            # the freeze ends inside this one


def test_damage_or_a_swing_ends_the_freeze(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now + 30
    sb._note_swing(swing(now - 1.0, -5), ME)
    sb._fold_cast(ours(now - 0.5, "Frost Nova", [-5, -6]), ME)
    assert sb.channel_safe(ARROW, now)
    sb._fold_cast(ours(now - 0.1, "Magic Sphere", [-5]), ME)     # our hit breaks its freeze
    assert not sb.channel_safe(ARROW, now)
    sb._thaw(swing(now, -6))                                      # it struck: not frozen after all
    assert -6 not in sb.frozen


def test_two_attackers_in_reach_of_it_make_the_mage_freeze_them(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now + 30
    me = {"id": ME, "x": 50.0, "y": 50.0, "hp": 100000, "maxhp": 100000}
    mobs = [{"id": i, "x": 54.0, "y": 50.0 + k, "hp": 9000, "maxhp": 9000, "name": "Wolf", "kind": "monster", "side": 1}
            for k, i in enumerate((-5, -6))]
    snap = {"self": me, "entities": [me] + mobs}
    up = sb.page()
    skills = sb._usable(up, False, now)[0]

    def room() -> str | None:
        got = sb._room_choice(combat._PickView(sb, snap, mobs[0], skills, up, now))
        return got["name"] if got else None
    sb._note_swing(swing(now - 1.0, -5), ME)
    assert room() is None                                         # one attacker: not worth it
    sb._note_swing(swing(now - 0.5, -6), ME)
    assert room() == "Frost Nova"
    mobs[1]["x"] = 60.0                                           # 10 yards off: out of its 8
    assert room() is None
