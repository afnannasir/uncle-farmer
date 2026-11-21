# pyright: reportPrivateUsage=false
"""Channels between monsters' swings (Phase 1b item 1): a hit on us always breaks a channel, and a
monster swings at us on a fixed clock, so a channel goes only when it ends before the next swing."""
from __future__ import annotations

from typing import Any

import pytest
from conftest import ME, Clock, fight

from unclefarmer.engine import combat


def swing(t: float, caster: int = -9, result: int = 3) -> dict[str, Any]:
    """A monster's cast report naming us among whom it hit (result 3 a hit, 2 a dodge)."""
    return {"t": int(t * 1000), "caster": caster, "skill": "Bite", "hits": [[ME, result, 500]],
            "targets": [[ME, 90000]]}


def book(clock: Clock) -> combat.SkillBook:
    _g, _b, sb = fight(clock, ["Magic Sphere", "Ice Arrow"], [], 0.1)
    sb.swings.clear()
    return sb


ARROW = {"name": "Ice Arrow", "cast": 1.5}


def test_a_channel_right_after_a_swing_is_safe_but_not_halfway_to_the_next(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now
    sb._note_swing(swing(now - 2.02), ME)
    sb._note_swing(swing(now), ME)
    # next swing at now + 2.02: a 1.5 s channel starting at now + 0.15 ends at 1.65
    assert sb.channel_safe(ARROW, now)
    assert sb.channel_safe(ARROW, now + 0.3)
    assert not sb.channel_safe(ARROW, now + 0.5)
    assert not sb.channel_safe(ARROW, now + 1.5)
    assert sb.channel_safe({"name": "Magic Sphere"}, now + 1.0)    # no channel: always safe


def test_the_gap_is_the_monster_s_own_and_a_missed_swing_rolls_on(clock: Clock) -> None:
    sb = book(clock)
    t0 = clock.now
    for k in range(4):                                  # a slow monster: one swing every 3 s
        sb._note_swing(swing(t0 + 3.0 * k), ME)
    last = t0 + 9.0
    assert sb.channel_safe(ARROW, last + 1.0)           # next at last + 3: 1.15 + 1.5 < 3
    assert not sb.channel_safe(ARROW, last + 1.6)
    # past its next swing with no report of it: the swing after that counts
    assert sb.channel_safe(ARROW, last + 3.1)


def test_a_dodge_is_a_swing_and_others_casts_are_not(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now
    sb._note_swing(swing(now - 1.0, result=2), ME)
    assert not sb.channel_safe(ARROW, now)
    sb.swings.clear()
    other = {"t": int((now - 1.0) * 1000), "caster": -9, "skill": "Bite", "hits": [[12345, 3, 500]]}
    sb._note_swing(other, ME)                            # it bit someone else
    sb._note_swing(dict(swing(now - 1.0), caster=777), ME)   # a player, not a monster
    assert sb.channel_safe(ARROW, now)


def test_an_old_swing_or_a_dead_monster_no_longer_counts(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now
    sb._note_swing(swing(now - combat.SWING_SEEN - 0.1), ME)
    assert sb.channel_safe(ARROW, now)
    sb._note_swing(swing(now - 1.0, caster=-10), ME)
    assert not sb.channel_safe(ARROW, now)
    sb._note_downed([[-10, 0]], now - 0.5)
    assert sb.channel_safe(ARROW, now)


def test_every_attacker_must_be_clear(clock: Clock) -> None:
    sb = book(clock)
    now = clock.now
    sb._note_swing(swing(now - 0.05, caster=-9), ME)     # just swung: clear for 2 s
    assert sb.channel_safe(ARROW, now)
    sb._note_swing(swing(now - 1.0, caster=-10), ME)     # swings again in 1 s
    assert not sb.channel_safe(ARROW, now)


def test_the_map_change_forgets_the_swings(clock: Clock) -> None:
    sb = book(clock)
    sb._note_swing(swing(clock.now - 1.0), ME)
    sb.forget_the_map()
    assert sb.channel_safe(ARROW, clock.now)


def test_read_casts_notes_monsters_swinging_at_us(clock: Clock) -> None:
    g, _b, sb = fight(clock, ["Magic Sphere", "Ice Arrow"], [], 0.1)
    sb.swings.clear()
    clock.advance(0.5)
    g.casts.append(swing(clock.now))                     # reports read since the last one
    sb.read_casts(ME)
    assert sb.swings.get(-9)
    assert not sb.channel_safe(ARROW, clock.now + 1.0)


def test_the_mage_casts_magic_sphere_while_a_swing_would_break_the_arrow(clock: Clock) -> None:
    _g, _b, sb = fight(clock, ["Magic Sphere", "Ice Arrow"], [], 0.1)
    now = clock.now + 30
    sb.swings.clear()
    sb._note_swing(swing(now - 1.0), ME)
    names = {s["name"] for s in sb._usable(sb.page(), False, now)[0]}
    assert "Ice Arrow" not in names and "Magic Sphere" in names
    sb.swings.clear()
    sb._note_swing(swing(now - 0.05), ME)
    names = {s["name"] for s in sb._usable(sb.page(), False, now)[0]}
    assert "Ice Arrow" in names


def test_the_fallback_holds_back_a_channel_a_swing_would_break(clock: Clock) -> None:
    _g, _b, sb = fight(clock, ["Ice Arrow"], [], 0.1)
    now = clock.now + 30
    sb.swings.clear()
    sb._note_swing(swing(now - 1.0), ME)
    assert sb.fallback(now) is None
    sb.swings.clear()
    r = sb.fallback(now)
    assert r is not None and r[1] == "Ice Arrow"


@pytest.mark.parametrize("cls_channel", ["Pyroblast"])
def test_the_elf_s_combo_channels_keep_their_own_rules(clock: Clock, cls_channel: str) -> None:
    _g, _b, sb = fight(clock, ["Fireball", cls_channel], [], 0.1)
    sb.swings.clear()
    sb._note_swing(swing(clock.now - 1.0), ME)
    pyro = sb.skill(cls_channel)
    assert pyro is not None and pyro.get("cast")
    assert sb.cls == "Elf" and not sb._swing_breaks(pyro, clock.now)
