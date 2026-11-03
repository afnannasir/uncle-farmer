# pyright: reportPrivateUsage=false
"""A monster at 0 HP is dead, whoever killed it: no press goes at it after the report.

The fight learnt of a death right away only from our own cast reports (agent 44 `targets`): a
monster our pet, another player or a damage over time finished stayed "alive" in the fight's last
look - up to 2 s old - and the next burst pressed at it until it gave up (BURST_MAX), then the
skill was held back for another 1.5 s as "pending". Now any report counts (anyone's cast leaving
it at 0 HP, the game's death list), a burst stops the moment its target is reported dead, and the
dead target's pending presses are forgotten."""

from __future__ import annotations

from typing import Any

from conftest import as_kind
from test_fighting import Clock, FakeTracker, Pick, monster, profile, until_bot
from unclefarmer.clock import CLOCK
import pytest
from unclefarmer.engine.combat import SkillBook
from unclefarmer.engine.kills import KillTracker

ME = 4242

@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    """The fighting tests' pretend clock (test_fighting.clock), here as this module's own fixture."""
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c



class Answers:
    def __init__(self, reply: dict[str, Any]) -> None:
        self.reply = reply

    def json(self, line: str) -> Any:
        return self.reply


def book(reply: dict[str, Any]) -> SkillBook:
    sb = SkillBook.__new__(SkillBook)
    sb.b = type("B", (), {"a": Answers(reply)})()     # type: ignore[assignment]
    sb.confirm = True
    sb.cast_seen = 0
    sb.pending, sb.pending_target = {}, {}
    return sb


def test_a_kill_by_our_pet_or_another_player_marks_the_monster_downed() -> None:
    sb = book({"casts": [{"t": 5000, "caster": 10001, "seq": 1, "skill": "Bite", "targets": [[-7, 0]]},
                         {"t": 5100, "caster": 777, "seq": 2, "skill": "Shot", "targets": [[-8, 1200]]}]})
    sb.read_casts(ME)
    assert sb.downed == {-7: 5.0}                       # -8 still has HP: not downed


def test_the_games_death_list_marks_it_downed_too() -> None:
    sb = book({"casts": [], "deaths": [[-9, 6000], [42, 6000]]})
    sb.read_casts(ME)
    assert sb.downed == {-9: 6.0}                       # only monsters


def test_a_dead_targets_pending_press_is_forgotten() -> None:
    sb = book({"casts": []})
    sb.pending = {"Arcane Shot": 10.0, "Aimed Shot": 10.0}
    sb.pending_target = {"Arcane Shot": -7, "Aimed Shot": -8}
    sb.forget_target(-7)
    assert sb.pending == {"Aimed Shot": 10.0} and sb.pending_target == {"Aimed Shot": -8}


class DiesMidBurst(Pick):
    """Its first reads of the casts find the target killed by someone else."""

    def __init__(self, clock: Clock, mid: int, after: int) -> None:
        super().__init__(want_follow=False, pick=("j", "Arcane Shot"))
        self.clock, self.mid, self.after, self.reads = clock, mid, after, 0

    def read_casts(self, me: Any) -> None:
        self.reads += 1
        if self.reads >= self.after:
            self.downed[self.mid] = self.clock.time()


def test_a_burst_stops_the_moment_its_target_is_reported_dead(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999, fight_timeout=5.0))
    sb = DiesMidBurst(clock, -1, after=1)
    b.skillbook = as_kind(SkillBook, sb)
    b.clock = clock                                     # each press's wait moves the pretend clock
    b.wait_keys_free = lambda: None
    def no_slot(key: str) -> bool:
        return False
    b.__dict__["press_slot"] = no_slot
    b.burst_target = (-1, clock.time() - 1)
    pressed: list[str] = []
    b.a.send = lambda line: pressed.append(line) or "tapped"     # type: ignore[method-assign]
    assert b.burst("j", ME, 1.2) is False
    assert len(pressed) <= 3                            # not 1.2 s worth of presses (24)


def test_a_monster_killed_by_someone_else_mid_fight_gets_no_more_presses(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999, fight_timeout=5.0))
    sb = DiesMidBurst(clock, -1, after=1)
    b.skillbook = as_kind(SkillBook, sb)
    bursts: list[str] = []

    def burst(key: str, me_id: Any, longest: float = 1.0) -> bool:
        bursts.append(key)
        sb.read_casts(me_id)                            # the burst's own look at the casts
        clock.advance(0.2)
        return False                                    # no cast of ours: it died first
    b.burst = burst
    b.ui = lambda: {"target": -1}
    mob = monster(-1, x=105.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    counted: list[Any] = []
    b.count_kills = lambda snap=None: counted.append(1) or []
    b._hit_until_down(mob)
    assert bursts == ["j"]                              # one burst, cut short; none after it
    assert sb.refused == []                             # its death is not the skill's fault
    assert counted                                      # the kill is looked for at once


def test_the_fight_ends_at_its_look_when_a_report_already_says_dead(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999, fight_timeout=5.0))
    sb = Pick(want_follow=False, pick=("j", "Arcane Shot"))
    b.skillbook = as_kind(SkillBook, sb)
    sb.downed[-1] = clock.time() + 0.01                  # a report since the fight began
    clock.advance(0.02)
    bursts: list[str] = []
    b.burst = lambda key, me_id, longest=1.0: bursts.append(key) or True
    b.ui = lambda: {"target": -1}
    mob = monster(-1, x=105.0)                           # the world still shows it with HP
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.kill_tracker = as_kind(KillTracker, FakeTracker(live=True))
    sb.downed[-1] = clock.time()
    b._hit_until_down(mob)
    assert bursts == []
