"""Kills from the game's own reports: monsters that die with a hit of ours on them, whoever struck
last, area-skill kills included; and monsters we already hurt are taken before fresh ones."""

from __future__ import annotations

import json
import time

from conftest import ME

from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.engine.kills import KillTracker
from typing import Any

DODGED, CRIT, HIT = 2, 5, 3


class Link:
    """The agent's `casts` answer, fed by the test."""

    def __init__(self, deaths: bool=True) -> None:
        self.casts: list[dict[str, Any]] = []
        self.deaths: list[list[int]] = []           # [map id... or monster, when]
        self.with_deaths = deaths

    def cast(self, t: int, caster: int, hits: list[tuple[int, int]]) -> None:
        self.casts.append({"t": t, "caster": caster, "seq": len(self.casts), "skill": "Level 5 Multi Shot",
                           "hits": [[target, result, 100] for target, result in hits]})

    def die(self, t: int, mid: int) -> None:
        self.deaths.append([mid, t])

    def send(self, line: str) -> str:      # nothing here sends; the bot's link has it
        return "ok"

    def json(self, line: str) -> dict[str, Any]:
        since = int(line.split()[1])
        r: dict[str, Any] = {"now": 0, "casts": [c for c in self.casts if c["t"] > since]}
        if self.with_deaths:
            r["deaths"] = [d for d in self.deaths if d[1] > since]
        return r


def test_an_area_skill_kills_every_monster_it_hit() -> None:
    a = Link()
    k = KillTracker(a)
    a.cast(1000, ME, [(-1, HIT), (-2, CRIT), (-3, HIT)])
    a.die(1500, -1)
    a.die(1600, -3)
    assert sorted(k.update(ME)) == [-3, -1]
    a.die(2000, -2)
    assert k.update(ME) == [-2]
    assert k.update(ME) == []                # each once


def test_a_kill_shared_with_another_player_is_ours() -> None:
    a = Link()
    k = KillTracker(a)
    a.cast(1000, ME, [(-7, HIT)])
    a.cast(1200, 4242, [(-7, HIT)])          # another player strikes last
    a.die(1300, -7)
    assert k.update(ME) == [-7]


def test_not_ours_without_a_landed_hit() -> None:
    a = Link()
    k = KillTracker(a)
    a.cast(1000, ME, [(-4, DODGED)])
    a.cast(1000, 4242, [(-5, HIT)])
    a.die(1100, -4)
    a.die(1100, -5)
    a.die(1100, -6)
    assert k.update(ME) == []


def test_an_old_hit_doesnt_make_a_kill_ours() -> None:
    a = Link()
    k = KillTracker(a)
    a.cast(1000, ME, [(-8, HIT)])
    k.update(ME)
    a.die(1000 + 120 * 1000, -8)
    assert k.update(ME) == []


def test_an_older_agent_says_it_cant_tell() -> None:
    a = Link(deaths=False)
    k = KillTracker(a)
    a.cast(1000, ME, [(-1, HIT)])
    assert k.update(ME) == [] and k.live is False


class Fighter(FightingMixin, WalkingMixin):
    """What targets() and count_kills() use of the bot."""

    def __init__(self, link: Link) -> None:
        self.a = link
        self.p = {"attack_range": 10.0}
        self.skillbook = None
        self.kill_tracker = KillTracker(link)
        self.mob_names, self.counted, self.kills = {}, {}, 0
        self.killed_kinds = set()
        self.skip = {}
        self.skip_names = {}
        self._dists = None
        self._attackers, self._attackers_at = set(), time.time()
        self.lines: list[str] = []

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)


def monster(mid: int, x: int, y: int, name: str="Wolf") -> dict[str, Any]:
    return {"id": mid, "kind": "monster", "side": -2, "name": name, "x": x, "y": y, "hp": 500, "maxhp": 900}


def test_a_monster_we_already_hurt_comes_before_a_nearer_fresh_one() -> None:
    a = Link()
    b = Fighter(a)
    me = {"id": ME, "x": 100.0, "y": 100.0, "hp": 1, "maxhp": 1}
    snap = {"map": {"id": 5121}, "self": me,
            "entities": [monster(-1, 103, 100, "Fresh Wolf"), monster(-2, 112, 100, "Hurt Wolf"),
                         monster(-3, 140, 100, "Far Hurt Wolf")]}
    assert [e["id"] for e in b.targets(snap)] == [-1, -2, -3]
    a.cast(1000, ME, [(-2, HIT), (-3, HIT)])
    b.count_kills(snap)
    # the hurt one close by goes first; one hurt but far off (it ran) waits its turn by distance
    assert [e["id"] for e in b.targets(snap)] == [-2, -1, -3]


def test_kills_are_counted_and_logged_once_by_name() -> None:
    a = Link()
    b = Fighter(a)
    me = {"id": ME, "x": 100.0, "y": 100.0, "hp": 1, "maxhp": 1}
    snap = {"map": {"id": 5121}, "self": me, "entities": [monster(-1, 103, 100), monster(-2, 105, 100, "Bear")]}
    b.targets(snap)                          # names learned from what we saw
    a.cast(1000, ME, [(-1, HIT), (-2, HIT)])
    a.die(1100, -1)
    a.die(1100, -2)
    assert sorted(b.count_kills(snap)) == [-2, -1]
    assert b.kills == 2 and b.counted_kill(-1) and b.counted_kill(-2)
    assert sorted(b.lines) == ["Bear down (2 so far)", "Wolf down (1 so far)"]
    assert b.count_kills(snap) == [] and b.kills == 2


def test_a_death_in_the_same_millisecond_as_the_last_read_is_still_counted_once() -> None:
    a = Link()
    k = KillTracker(a)
    a.cast(1000, ME, [(-1, HIT), (-2, HIT)])
    a.die(1500, -1)
    assert k.update(ME) == [-1]
    a.die(1500, -2)                          # noted just after that read, in the same millisecond
    assert k.update(ME) == [-2]
    assert k.update(ME) == []


def test_fresh_casts_are_only_the_new_ones_and_include_monsters() -> None:
    a = Link()
    k = KillTracker(a)
    a.cast(1000, ME, [(-1, HIT)])
    k.update(ME)
    assert [c["caster"] for c in k.fresh] == [ME]
    assert k.update(ME) == [] and k.fresh == []
    a.cast(1100, 4242, [(-1, HIT)])
    k.update(ME)
    assert [c["caster"] for c in k.fresh] == [4242]


def test_count_kills_feeds_the_tally_with_both_sides() -> None:
    from unclefarmer import stats

    a = Link()
    b = Fighter(a)
    b.tally = stats.Tally("Hero", "Wolves")
    me = {"id": ME, "x": 100.0, "y": 100.0, "hp": 1, "maxhp": 1}
    snap = {"map": {"id": 5121}, "self": me, "entities": [monster(-1, 103, 100, "Wolf")]}
    b.targets(snap)
    a.cast(1000, ME, [(-1, HIT)])            # our hit: 100 damage
    a.cast(1001, -1, [(ME, HIT)])            # the wolf's hit on us: 100 damage
    b.count_kills(snap)
    got = b.tally.combat.result()
    assert got is not None
    assert got["given"]["damage"] == 100
    assert got["received"]["damage"] == 100
    assert got["received"]["by_monster"]["Wolf"]["damage"] == 100


# ------------------------------------------------------------------ someone else's strikes (agent 44)
def strike(a: Link, t: int, caster: int, targets: list[list[int]]) -> None:
    a.casts.append({"t": t, "caster": caster, "seq": len(a.casts), "skill": "Mind Whip", "targets": targets})


def test_the_agents_cast_targets_are_read_as_its_contract_says() -> None:
    from test_ask_vitals import contract
    a = Link()
    k = KillTracker(a)
    strike(a, 1000, 4152, json.loads(contract()["cast_targets"]))
    k.update(ME)
    # the one struck to 0 is dead, not someone else's to take: only the living one is claimed
    assert k.claimed(-17) and not k.claimed(-6)


def test_someone_elses_strikes_give_a_rate_from_the_second_on() -> None:
    a = Link()
    k = KillTracker(a)
    strike(a, 1000, 4152, [[-5, 40000]])
    k.update(ME)
    assert k.claimed(-5) and k.struck_rate(-5) is None       # one strike: no rate yet
    strike(a, 2000, 4152, [[-5, 30000]])
    k.update(ME)
    assert k.struck_rate(-5) == 10000.0                      # 10,000 HP a second
    a.die(2500, -5)
    k.update(ME)
    assert not k.claimed(-5)                                 # its id's next life starts clean


def test_our_pets_strikes_are_ours() -> None:
    a = Link()
    k = KillTracker(a)
    k.own = {10008112}
    strike(a, 1000, 10008112, [[-8, 40000]])
    k.update(ME)
    assert k.pets_alone(-8) and not k.claimed(-8)
    strike(a, 1200, 4152, [[-8, 35000]])                     # then another player joins in
    k.update(ME)
    assert not k.pets_alone(-8) and k.claimed(-8)


def test_a_monster_our_pet_finished_alone_is_our_kill() -> None:
    # the game counts the pet's kills as the player's (Scorpion Cave: 298 said the game, 22 of
    # them the pet's last blow on monsters we never hit)
    a = Link()
    k = KillTracker(a)
    k.own = {10008112}
    strike(a, 1000, 10008112, [[-8, 0]])
    a.die(1100, -8)
    strike(a, 1000, 4152, [[-9, 0]])                         # someone else's alone: not ours
    a.die(1100, -9)
    assert k.update(ME) == [-8]


def test_old_strikes_are_forgotten() -> None:
    a = Link()
    k = KillTracker(a)
    strike(a, 1000, 4152, [[-9, 40000]])
    k.update(ME)
    strike(a, 12000, 4152, [[-10, 40000]])                   # 11 s later, at another one
    k.update(ME)
    assert not k.claimed(-9) and k.claimed(-10)


def test_count_kills_knows_our_pets_by_name() -> None:
    a = Link()
    b = Fighter(a)
    me = {"id": ME, "name": "Hero", "x": 100.0, "y": 100.0, "hp": 1, "maxhp": 1}
    pet = {"id": 10008112, "kind": "player", "name": "Pixie belongs to Hero", "x": 101.0, "y": 100.0}
    other = {"id": 10005483, "kind": "player", "name": "Imp belongs to Sam", "x": 120.0, "y": 100.0}
    b.count_kills({"map": {"id": 5121}, "self": me, "entities": [pet, other, monster(-1, 103, 100)]})
    assert b.kill_tracker.own == {10008112}


def test_a_monster_someone_else_is_killing_fast_is_doomed_from_their_second_strike() -> None:
    a = Link()
    b = Fighter(a)
    me = {"id": ME, "name": "Hero", "x": 100.0, "y": 100.0, "hp": 1, "maxhp": 1}
    far = monster(-3, 140, 100)          # 30 tiles past a reach of 10: 7.5 s of walk
    far["hp"] = 20000
    snap = {"map": {"id": 5121}, "self": me, "entities": [far]}
    strike(a, 1000, 4152, [[-3, 30000]])
    strike(a, 2000, 4152, [[-3, 20000]])
    b.count_kills(snap)
    assert b.dying_in(far) == 2.0        # 20,000 HP left at 10,000 a second
    assert b.doomed(far, me)
    b.kill_tracker.struck.clear()
    assert not b.doomed(far, me)         # nobody else on it: walk over


def test_a_monster_only_our_pet_is_killing_is_never_doomed() -> None:
    a = Link()
    b = Fighter(a)
    me = {"id": ME, "x": 100.0, "y": 100.0, "hp": 1, "maxhp": 1}
    far = monster(-4, 140, 100)
    b.kill_tracker.pet_struck[-4] = 1000
    b.dying_in = lambda e: 0.5
    assert not b.doomed(far, me)
