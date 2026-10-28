# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Monsters the bot gave up on for nothing: one someone else is about to kill is left alone for a
moment (not turned down over and over), a fight whose hits land on another monster follows that
one instead of writing ours off as "takes no damage", and the target keys try every direction."""

from __future__ import annotations

import contextlib
import json
import threading

from conftest import ME, data

from unclefarmer import settings
from unclefarmer.clock import CLOCK, Waitable
from unclefarmer.engine import combat
from unclefarmer.engine.bot import Bot
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.engine.kills import KillTracker
import pytest
from collections.abc import Callable
from typing import Any
from contextlib import AbstractContextManager


# ---------------------------------------------------------------- one about to die to someone else
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
    def __init__(self, entities: list[dict[str, Any]], me: dict[str, Any]) -> None:
        self.a = Link()
        self.p = {"attack_range": 4.0, "target_keys": {}}
        self.skillbook = None
        self.kill_tracker = KillTracker(self.a)
        self.mob_names, self.counted, self.kills = {}, {}, 0
        self.killed_kinds = set()
        self.skip = {}
        self.skip_names = {}
        self._dists = None
        self._attackers, self._attackers_at = set(), 1e12
        self.farm_filter = None
        self.entities, self.self_ = entities, me
        self.lines: list[str] = []

    def log(self, line: str, speak: bool=False):
        self.lines.append(line)

    def snap(self) -> dict[str, Any]:
        return {"map": {"id": 5121}, "self": self.self_, "entities": [dict(e) for e in self.entities]}

    def me(self) -> dict[str, Any]:
        return self.self_

    def map_id(self) -> int:
        return 5121

    def clear_shot(self, me: dict[str, Any], mob: dict[str, Any], mid: int | None=None) -> bool:
        return True

    def timed(self, phase: str) -> AbstractContextManager[None]:
        return contextlib.nullcontext()

    def walk_to(self, cell: tuple[float, float], map_change: bool=False, near: Callable[[dict[str, Any]], bool] | None=None) -> str:
        return "near" if near is not None and near(self.self_) else "arrived"


def test_a_doomed_monster_is_left_alone_until_it_should_be_dead(monkeypatch: pytest.MonkeyPatch) -> None:
    clock = Clock()
    monkeypatch.setattr(CLOCK, "time", clock.time)
    monkeypatch.setattr(CLOCK, "sleep", clock.sleep)
    monkeypatch.setattr(CLOCK, "wait", clock.wait)
    wolf = {"id": -1, "kind": "monster", "side": -2, "name": "Wolf", "x": 130.0, "y": 100.0,
            "hp": 1000, "maxhp": 1000}
    b = Fighter([wolf], {"id": ME, "x": 100.0, "y": 100.0})
    for hp in (1000, 800, 600):                          # someone takes 400 a second off it
        wolf["hp"] = hp
        b.watch_hp(b.snap())
        clock.now += 0.5
    b._fight(dict(wolf))
    assert b.lines == ["Wolf is about to be killed by someone else - next one"]
    assert clock.now < b.skip[-1] <= clock.now + b.DOOMED_SKIP
    assert b.targets(b.snap()) == []                     # the farm loop doesn't pick it straight back


# ---------------------------------------------------------------- the target keys
def test_the_target_keys_try_the_other_directions_too() -> None:
    b = Fighter([], {"id": ME, "x": 100.0, "y": 100.0})
    b.p["target_keys"] = {"west": "k", "north": "o", "south": "l", "east": ";"}
    b.sleep = lambda seconds: None
    pressed: list[str] = []
    ui: dict[str, int | None] = {"target": None}     # what the game's target key reports

    def key(k: str, ms: int = 60) -> None:
        pressed.append(k)
        if k == "k":                                      # only "west" reaches it, though it is east of us
            ui["target"] = -1
    b.key = key
    b.ui = lambda: dict(ui)
    mob = {"id": -1, "x": 100.4, "y": 100.2}
    assert b.select_target(mob)
    assert pressed[-1] == "k" and pressed.count(";") == 4 and pressed.count("l") == 4


# ---------------------------------------------------------------- hits that land on another monster
class TwoMonsters:
    """A pretend game: our pick (-9, Orc) and another monster (-7, Wolf) both within reach. Every
    press of the j key casts Bash once 0.5 s have passed, and the cast lands on `struck` for 1000;
    the game reports it with its hit result, and reports the death at 0 HP. With lag, our pick's HP
    in memory only catches up with its real HP every 2 s."""

    def __init__(self, clock: Clock, struck: int, lag: bool=False) -> None:
        self.clock = clock
        self.snapshot = json.dumps(data("town_snapshot.json")["snapshot"])
        self.struck, self.lag = struck, lag
        self.hp = {-9: 4000, -7: 4000}
        self.shown = dict(self.hp)
        self.shown_at = clock.now
        self.casts: list[tuple[float, int]] = []          # (when, who was struck)
        self.deaths: list[list[int]] = []                  # [who, when in ms]
        self.last_cast = 0.0
        self.lock = threading.Lock()

    def press(self) -> None:
        with self.lock:
            t = self.clock.now
            if t - self.last_cast < 0.5 or self.hp[self.struck] <= 0:
                return
            self.last_cast = t
            self.hp[self.struck] -= 1000
            self.casts.append((t, self.struck))
            if self.hp[self.struck] <= 0:
                self.deaths.append([self.struck, int(t * 1000) + 60])

    def send(self, line: str) -> str:
        w = line.split()
        if w[0] == "key":
            if w[1] == "j":
                self.press()
            return "tapped"
        return "mwagent 30" if w[0] == "version" else "ok"

    def mem(self) -> dict[str, Any]:
        with self.lock:
            now = self.clock.now
            if not self.lag or now - self.shown_at >= 2.0 or self.hp[-9] <= 0:
                self.shown, self.shown_at = dict(self.hp), now
            shown = dict(self.shown)
            shown[-7] = self.hp[-7]
        s = json.loads(self.snapshot)
        s["self"] = {"id": ME, "name": "Hero", "hp": 95000, "maxhp": 100000, "x": 60.0, "y": 60.0}
        s["entities"] = [{"id": i, "kind": "monster", "side": -2, "name": n, "hp": shown[i], "maxhp": 4000,
                          "x": x, "y": 60.0} for i, n, x in ((-9, "Orc", 62.0), (-7, "Wolf", 61.0))
                         if shown[i] > 0]
        return s

    def json(self, line: str) -> Any:
        if line == "mem":
            return self.mem()
        if line.startswith("casts"):
            since = int(line.split()[1])
            now = self.clock.now
            with self.lock:
                casts = [{"caster": ME, "t": int(t * 1000) + 50, "seq": n, "skill": "Level 7 Bash",
                          "hits": [[who, 3, 1000]]} for n, (t, who) in enumerate(self.casts)
                         if int(t * 1000) + 50 > since and t + 0.05 <= now]
                deaths = [d for d in self.deaths if d[1] > since and d[1] <= now * 1000]
            return {"casts": casts, "deaths": deaths}
        return {"mem self": self.mem()["self"], "mem map": {"id": 5302},
                "mem ui": {"target": -9, "panel": 0, "cursor": 0, "attack_page": 0},
                "input": {"ok": True, "open": False}, "dialog": {"ok": True, "open": 0}}.get(
            line, [] if line in ("mem equipment", "mem bag", "mem bagfull") else {})


def bot(world: TwoMonsters, clock: Clock) -> tuple[Bot, list[Any]]:
    p = settings.load_profile("_test_")
    p.update(vitals_key="", mp_use=False, fight_timeout=15.0, points_key="", no_damage_timeout=6.0,
             no_damage_casts=3)
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
                       want_follow=False, buff_tried={}, dispel_try=None, no_dispel=None, pending_target={},
                       landed={}, slots={0: [bash]})
    b.skillbook = sb
    return b, lines


def orc() -> dict[str, int | str | float]:
    return {"id": -9, "kind": "monster", "side": -2, "name": "Orc", "hp": 4000, "maxhp": 4000, "x": 62.0, "y": 60.0}


def test_hits_landing_on_another_monster_move_the_fight_to_it(clock: Clock) -> None:
    world = TwoMonsters(clock, struck=-7)
    b, lines = bot(world, clock)
    b._hit_until_down(orc())
    assert world.hp[-7] <= 0, lines
    assert not any("takes no damage" in line for line in lines), lines
    assert "our hits land on Wolf - fighting that one" in lines
    assert -9 not in b.skip                              # our pick is still there to be fought


def test_hits_landing_on_our_monster_keep_the_fight_going_while_its_hp_lags(clock: Clock) -> None:
    world = TwoMonsters(clock, struck=-9, lag=True)
    b, lines = bot(world, clock)
    b._hit_until_down(orc())
    assert world.hp[-9] <= 0, lines
    assert not any("takes no damage" in line for line in lines), lines


def test_no_damage_is_judged_on_fresh_cast_reports_not_on_the_last_full_reading(clock: Clock) -> None:
    """Live, the fight loop counts each cast the moment the game confirms it, but reads the world
    (the monster's HP, where our hits landed) only when the cooldown leaves time for it. A Warrior
    cast three times between two readings, the stale reading said "no damage", and the bot walked
    away from monsters our hits had taken from 45,000 HP down to 2,700. Here the world is read once
    only: the casts' own reports must keep the fight going until the monster dies."""
    world = TwoMonsters(clock, struck=-9)
    b, lines = bot(world, clock)
    b.CHECK_EVERY = 1000.0            # no reading after the first
    b._hit_until_down(orc())
    assert world.hp[-9] <= 0, lines
    assert not any("takes no damage" in line for line in lines), lines
    assert any(line.endswith("down (1 so far)") for line in lines), lines
    assert -9 not in b.skip
