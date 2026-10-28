# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Less walking for nothing: monsters someone else is about to kill are left alone, and a melee
character's follow attack chases a monster that steps away instead of breaking the fight off."""

from __future__ import annotations

import json
import threading

import pytest
from conftest import ME, data

from unclefarmer import settings
from unclefarmer.clock import CLOCK, Waitable
from unclefarmer.engine import combat
from unclefarmer.engine.bot import Bot
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.engine.kills import KillTracker
from typing import Any


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float):
        self.now += seconds

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Waitable, timeout: float | None=None) -> bool:
        if event.is_set():
            return True
        if timeout:
            self.now += timeout
        return False


class Link:
    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> Any:
        return {"casts": [], "deaths": []}


class Fighter(FightingMixin, WalkingMixin):
    def __init__(self) -> None:
        self.a = Link()
        self.p = {"attack_range": 4.0}
        self.skillbook = None
        self.kill_tracker = KillTracker(self.a)
        self.mob_names, self.counted, self.kills = {}, {}, 0
        self.killed_kinds = set()
        self.skip = {}
        self.skip_names = {}
        self._dists = None
        self._attackers, self._attackers_at = set(), 1e12


def monster(mid: int, x: float, hp: int, y: float=100.0) -> dict[str, Any]:
    return {"id": mid, "kind": "monster", "side": -2, "name": "Wolf", "x": x, "y": y, "hp": hp, "maxhp": 1000}


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    monkeypatch.setattr(CLOCK, "sleep", c.sleep)
    monkeypatch.setattr(CLOCK, "wait", c.wait)
    return c


def seen(b: Fighter, clock: Clock, hps: list[int], x: float=120.0, every: float=0.5) -> dict[str, Any]:
    """The monster's HP over a few readings, `every` seconds apart; returns the last snapshot."""
    me = {"id": ME, "x": 100.0, "y": 100.0}
    snap: dict[str, Any] = {}
    for hp in hps:
        snap = {"map": {"id": 5121}, "self": me, "entities": [monster(-1, x, hp)]}
        b.watch_hp(snap)
        clock.now += every
    clock.now -= every
    return snap


def test_a_monster_dying_fast_far_off_is_doomed(clock: Clock) -> None:
    b = Fighter()
    snap = seen(b, clock, [1000, 800, 600])            # 400 HP a second: dead in 1.5 s
    m, me = snap["entities"][0], snap["self"]
    assert b.dying_in(m) == pytest.approx(1.5)
    assert b.doomed(m, me)                              # 20 tiles: about 5 s to get there and hit
    assert b.targets(snap) == []


def test_one_that_holds_its_hp_is_worth_the_walk(clock: Clock) -> None:
    b = Fighter()
    snap = seen(b, clock, [1000, 1000, 1000])
    assert not b.doomed(snap["entities"][0], snap["self"])
    assert [e["id"] for e in b.targets(snap)] == [-1]


def test_a_slow_death_is_still_worth_the_walk(clock: Clock) -> None:
    b = Fighter()
    snap = seen(b, clock, [1000, 980, 960])             # 40 a second: 24 s left
    assert not b.doomed(snap["entities"][0], snap["self"])


def test_one_in_reach_or_already_hurt_by_us_is_never_doomed(clock: Clock) -> None:
    b = Fighter()
    snap = seen(b, clock, [1000, 800, 600], x=103.0)    # in reach: a hit from here earns a share
    assert not b.doomed(snap["entities"][0], snap["self"])
    b = Fighter()
    b.kill_tracker.hit[-1] = 1                          # we hit it lately: partly ours
    snap = seen(b, clock, [1000, 800, 600])
    assert not b.doomed(snap["entities"][0], snap["self"])


def test_a_monster_that_healed_starts_its_reading_again(clock: Clock) -> None:
    b = Fighter()
    snap = seen(b, clock, [1000, 600, 1000, 990])
    assert not b.doomed(snap["entities"][0], snap["self"])


# ---------------------------------------------------------------- the follow attack, fought out
class FollowWorld:
    """A pretend game for one melee fight: the monster steps 6 tiles away after the first hit;
    with follow True the base attack's key walks us after it at 4.1 tiles a second (the game's
    follow attack), with follow False it does nothing out of reach. A cast lands only within 4.5
    tiles and takes 1000 HP; one cast every 0.6 s."""

    def __init__(self, clock: Clock, follow: bool) -> None:
        self.clock = clock
        self.snapshot = json.dumps(data("town_snapshot.json")["snapshot"])
        self.follow, self.chasing = follow, False
        self.pos, self.mob = [60.0, 60.0], [63.0, 60.0]
        self.mob_hp, self.hits, self.stepped = 4000, 0, False
        self.casts: list[float] = []
        self.last_cast, self.moved = 0.0, clock.now
        self.lock = threading.Lock()

    def me(self) -> dict[str, Any]:
        now = self.clock.now
        with self.lock:
            if self.chasing and self.mob[0] - self.pos[0] > 2.0:
                self.pos[0] = min(self.mob[0] - 2.0, self.pos[0] + 4.1 * (now - self.moved))
            self.moved = now
        return {"id": ME, "name": "Hero", "hp": 95000, "maxhp": 100000, "x": self.pos[0], "y": self.pos[1]}

    def press(self) -> None:
        self.me()
        with self.lock:
            t = self.clock.now
            if self.mob[0] - self.pos[0] > 4.5:
                self.chasing = self.follow
                return
            if t - self.last_cast < 0.6:
                return
            self.last_cast = t
            self.casts.append(t)
            self.mob_hp -= 1000
            self.hits += 1
            if not self.stepped:
                self.stepped = True
                self.mob[0] += 6.0

    def send(self, line: str) -> str:
        w = line.split()
        if w[0] == "key":
            if w[1] == "j":
                self.press()
            return "tapped"
        if w[0] in ("keydown", "keyup", "keysup"):
            return "ok"
        return "mwagent 20" if w[0] == "version" else "ok"

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem":
            s = json.loads(self.snapshot)
            s["self"] = self.me()
            s["entities"].insert(0, {"id": -9, "kind": "monster", "side": -2, "name": "Orc",
                                     "hp": max(0, self.mob_hp), "maxhp": 4000, "x": self.mob[0], "y": 60.0})
            return s
        if line.startswith("casts"):
            since = int(line.split()[1])
            with self.lock:
                return {"casts": [{"caster": ME, "t": int(t * 1000) + 50, "seq": 0, "skill": "Level 7 Bash"}
                                  for t in self.casts if int(t * 1000) + 50 > since and t + 0.05 <= self.clock.now]}
        return {"mem self": self.me(), "mem map": {"id": 5302},
                "mem ui": {"target": -9, "panel": 0, "cursor": 0, "attack_page": 0},
                "input": {"ok": True, "open": False}, "dialog": {"ok": True, "open": 0}}.get(
            line, list[Any]() if line in ("mem equipment", "mem bag", "mem bagfull") else dict[str, Any]())


def warrior(world: FollowWorld, clock: Clock) -> tuple[Bot, list[Any]]:
    p = settings.load_profile("_test_")
    p.update(vitals_key="", mp_use=False, fight_timeout=12.0, points_key="", no_damage_timeout=6.0)
    lines: list[str] = []
    b = Bot(world, p, lambda line, speak=False: lines.append(line), threading.Event(), clock)
    b.potion = lambda kind: (None, None)
    b.select_target = lambda mob, by_keys=False: True
    b.reach = lambda: 4.0
    b.clear_shot = lambda me, mob, mid=None: True
    bash = dict({s["name"]: s for s in combat.knowledge()["Warrior"]}["Bash"], key="j", still="alt+j", use="auto")
    sb = combat.SkillBook.__new__(combat.SkillBook)
    sb.__dict__.update(b=b, ok=True, used={}, pending={}, gaps=[], marked_at=0.0, last_cast=0.0, last_press=0.0,
                       can_flip=True, confirm=True, press_confirmed=True, cast_seen=0, cls="Warrior",
                       want_follow=True, buff_tried={}, dispel_try=None, no_dispel=None, pending_target={},
                       landed={}, slots={0: [bash]})
    b.skillbook = sb
    return b, lines


def orc(world: FollowWorld) -> dict[str, Any]:
    return {"id": -9, "kind": "monster", "side": -2, "name": "Orc", "hp": world.mob_hp, "maxhp": 4000,
            "x": world.mob[0], "y": 60.0}


def test_the_follow_attack_chases_a_monster_that_steps_away(clock: Clock) -> None:
    world = FollowWorld(clock, follow=True)
    b, lines = warrior(world, clock)
    b._hit_until_down(orc(world))
    assert world.mob_hp <= 0, lines
    assert world.stepped and world.chasing
    assert lines.count("fighting Orc") == 1             # one fight, not broken off and started again
    assert getattr(b, "follow_ok", True)


def test_a_follow_attack_that_doesnt_walk_us_gives_the_monster_back(clock: Clock) -> None:
    world = FollowWorld(clock, follow=False)
    b, _lines = warrior(world, clock)
    began = clock.now
    b._hit_until_down(orc(world))
    assert world.mob_hp > 0 and world.hits == 1
    assert clock.now - began < 5.0, "it stood there pressing keys"
    assert getattr(b, "follow_ok", True), "it did reach the monster once: the follow attack isn't to blame"


def test_a_follow_attack_that_never_gets_us_there_is_given_up_for_the_run(clock: Clock) -> None:
    world = FollowWorld(clock, follow=False)
    world.mob = [69.0, 60.0]                            # 9 tiles: within the leash, out of reach
    b, lines = warrior(world, clock)
    b._hit_until_down(orc(world))
    assert world.hits == 0
    assert b.follow_stalls == 1 and b.follows()          # one stall: maybe a corner; skipped for a moment
    assert b.skip.get(-9, 0) > clock.now
    for _try in range(2):
        world.mob_hp = 4000
        b._hit_until_down(orc(world))
    assert b.follow_ok is False and not b.follows()     # three in a row: from now on we walk up ourselves
    assert any("follow attack doesn't walk us" in line for line in lines)
