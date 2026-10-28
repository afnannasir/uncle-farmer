# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The fighting part of the bot, on its own: reach, target modes, choosing and switching targets,
fighting back while walking, the fight loop's decisions (doomed monsters, no damage, lost target,
hits landing on another monster), the target keys, the T-key points reading and the burst press.

Only the pieces test_kills / test_approach / test_chase / test_wrong_target / test_combos don't
already cover are tested here; that of fighting.py is exercised with a stand-in Bot inheriting the
fighting and walking mixins, a fake agent link and a clock the test moves by hand.
"""

from __future__ import annotations

import contextlib
import json
import math
import os
import threading
import types
from typing import Any

import pytest

from unclefarmer.clock import CLOCK, StopFlag, Waitable
from unclefarmer import paths, settings
from unclefarmer.engine.botparts import fighting
from unclefarmer.engine.botparts.base import NPC_HP
from unclefarmer.engine.botparts.fighting import FightingMixin
from unclefarmer.engine.botparts.keys import KeysMixin
from unclefarmer.engine.botparts.walking import WalkingMixin
from unclefarmer.engine.kills import KillTracker
from collections.abc import Callable, Iterable, Iterator
from conftest import as_kind, stand_in
from unclefarmer.engine.combat import Press, SkillBook
from contextlib import AbstractContextManager

ME = 1001


# ------------------------------------------------------------------ helpers and fakes
def profile(**over: Any) -> dict[str, Any]:
    p: dict[str, Any] = {
        "attack_range": 4.0,
        "attack_keys": ["j", "y", "."],
        "attack_gap": 0.35,
        "target_keys": {},
        "target_mode_key": "v",
        "target_mode": 2,
        "set_modes": True,
        "points_key": "",
        "points_every": 1.5,
        "pets": [],
        "fight_timeout": 30.0,
        "no_damage_timeout": 6.0,
        "no_damage_casts": 3,
        "press_burst": True,
        "smart_skills": False,
        "fight_back": True,
    }
    p.update(over)
    return p


def monster(mid: int, x: float=110.0, y: float=100.0, name: str="Wolf", hp: int=500, maxhp: int=900, kind: str="monster", side: int=-2) -> dict[str, Any]:
    return {"id": mid, "kind": kind, "side": side, "name": name, "x": x, "y": y, "hp": hp, "maxhp": maxhp}


class Clock:
    def __init__(self, now: float=1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now

    def time(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.now += seconds

    def wait(self, event: Waitable, timeout: float | None=None) -> bool:
        if event.is_set():
            return True
        if hasattr(event, "clock"):
            return bool(event.wait(timeout))
        if timeout:
            self.now += timeout
        return False

    def advance(self, seconds: float) -> None:
        self.now += seconds


class WaitEvent:
    """A stop_event whose wait() moves the clock (and can say Stop was pressed)."""

    def __init__(self, clock: Clock, stop: bool=False, step: float | None=None) -> None:
        self.clock, self.stop, self.step = clock, stop, step

    def wait(self, timeout: float | None = None) -> bool:
        self.clock.advance((timeout or 0.0) if self.step is None else self.step)
        return self.stop

    def is_set(self) -> bool:
        return self.stop

    def set(self) -> None:
        self.stop = True


class Link:
    def __init__(self, send: Callable[[str], str] | str = "tapped", json: Callable[[str], Any] | None = None) -> None:
        self.sent: list[str] = []
        self.calls: list[str] = []
        self._send = send
        self._json: Callable[[str], Any] = json or (lambda line: {})

    def send(self, line: str) -> str:
        self.sent.append(line)
        return self._send(line) if callable(self._send) else self._send

    def json(self, line: str) -> Any:
        self.calls.append(line)
        return self._json(line)


class Pick:
    """The SkillBook methods fighting.py calls, scripted by the test."""

    def __init__(self, confirm: bool=True, want_follow: bool=False, ready: float=1.0, pick: Press | str | None=None, casts: int=0,
                 follow_key: str | None=None, cls: str="Warrior", ok: bool=True) -> None:
        self.confirm, self.want_follow, self.ok, self.cls = confirm, want_follow, ok, cls
        self._ready, self._pick = ready, pick
        self._follow_key, self.casts_done = follow_key, casts
        self.press_confirmed = False
        self.picked: list[Any] = []
        self.refused: list[str] = []

    def ready_in(self, now: float) -> float:
        return self._ready

    def pick(self, snap: dict[str, Any], target: dict[str, Any]) -> Press | str | None:
        self.picked.append(target["id"])
        return self._pick

    def follow_key(self) -> str | None:
        return self._follow_key

    def burst_time(self, name: str) -> float:
        return 0.5

    def refuse(self, name: str, now: float) -> None:
        self.refused.append(name)

    def read_casts(self, me: dict[str, Any]) -> None:
        pass


class FakeTracker:
    def __init__(self, live: bool=False, landed: dict[int, int] | None=None, tagged: Iterable[int]=()) -> None:
        self.live = live
        self._landed = dict(landed or {})
        self._tagged = set(tagged)

    def update(self, me_id: int) -> list[int]:
        return []

    def tagged(self, mid: int) -> bool:
        return mid in self._tagged

    def hit_since(self, ms: int) -> dict[int, int]:
        return dict(self._landed)


class Bot(FightingMixin, WalkingMixin):
    """Only the collaborators; every fighting method under test is the real one."""

    # the real slot presses (agent 45): burst() goes through them; off unless a test turns them on
    agent_slots = False
    _link = KeysMixin._link
    slot_command = KeysMixin.slot_command
    press_slot = KeysMixin.press_slot
    attack_slot = KeysMixin.attack_slot
    switch_wait = KeysMixin.switch_wait
    note_page_turn = KeysMixin.note_page_turn
    last_attack: tuple[str, float] | None = None
    in_instance = False

    def __init__(self, link: Link | None=None, prof: dict[str, Any] | None=None, entities: list[dict[str, Any]] | None=None, me: dict[str, Any] | None=None, map_id: int=5121) -> None:
        self.link = link or Link()
        self.a = self.link
        self.p = prof or profile()
        self.skillbook = None
        self.kill_tracker = KillTracker(self.a)
        self.mob_names = {}
        self.counted = {}
        self.kills = 0
        self.killed_kinds = set()
        self.skip = {}
        self.skip_names = {}
        self._dists = None
        self._attackers = set()
        self._attackers_at = 1e12
        self.farm_filter = None
        self.in_fight = False
        self.walk_only = False
        self.resumed_at = 0.0
        self.mp_share = None
        self.last_hp = None
        self.last_points_key = 0.0
        self._speech_at = 0.0
        self.modes_done = True
        self.stop_event: StopFlag = threading.Event()        # never set unless a test sets its own
        self._attack_keys: list[str] | None = None
        self.walked: list[tuple[float, float]] = []
        self.selects: list[int] = []
        self.hit_calls: list[int] = []
        self.entities = entities if entities is not None else []
        self.self_ = me or {"id": ME, "x": 100.0, "y": 100.0, "hp": 100.0, "maxhp": 100.0}
        self._map_id = map_id
        self.keys_pressed: list[str] = []
        self.slept: list[float] = []
        self.lines: list[str] = []

    # collaborators only
    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def key(self, k: str, ms: int | None=None) -> None:
        self.keys_pressed.append(k)

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)

    def ui(self) -> dict[str, Any]:
        return {}

    def me(self) -> dict[str, Any]:
        return self.self_

    def map_id(self) -> int:
        return self._map_id

    def snap(self) -> dict[str, Any]:
        return {"map": {"id": self._map_id}, "self": dict(self.self_),
                "entities": [dict(e) for e in self.entities]}

    def look_after(self, me: dict[str, Any] | None=None) -> dict[str, Any]:
        return me if me is not None else self.me()

    def clear_popups(self, force: bool = False) -> bool:
        return False

    def check(self) -> None:
        pass

    def wait_keys_free(self) -> None:
        pass

    def set_modes(self) -> None:
        self.modes_done = True

    def timed(self, phase: str) -> AbstractContextManager[None]:
        return contextlib.nullcontext()


def straight_walk(b: Bot) -> None:
    b.walk_dist = lambda snap, e, limit=None: (
        math.inf if limit is not None and math.hypot(e["x"] - snap["self"]["x"], e["y"] - snap["self"]["y"]) > limit
        else math.hypot(e["x"] - snap["self"]["x"], e["y"] - snap["self"]["y"]))


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


# ------------------------------------------------------------------ read_points
class FakeSB:
    def __init__(self, ok: bool=True, points: bool=True, cls: str="Warrior") -> None:
        self.ok, self.cls, self._points = ok, cls, points
        self.took: list[dict[str, Any]] = []

    def uses_points(self) -> bool:
        return self._points

    def take_points(self, me: dict[str, Any]) -> None:
        self.took.append(me)


def test_read_points_does_nothing_without_the_key(clock: Clock) -> None:
    b = Bot(prof=profile(points_key=""))
    sb = FakeSB()
    b.skillbook = as_kind(SkillBook, sb)
    b.read_points(b.self_, clock.now)
    assert b.keys_pressed == [] and sb.took == []


def test_read_points_does_nothing_without_a_book(clock: Clock) -> None:
    b = Bot(prof=profile(points_key="t"))
    b.skillbook = None
    b.read_points(b.self_, clock.now)
    assert b.keys_pressed == []


def test_read_points_only_for_classes_that_spend_them(clock: Clock) -> None:
    b = Bot(prof=profile(points_key="t"))
    sb = FakeSB(points=False)
    b.skillbook = as_kind(SkillBook, sb)
    b.read_points(b.self_, clock.now)
    assert b.keys_pressed == [] and sb.took == []
    sb = FakeSB(ok=False)
    b.skillbook = as_kind(SkillBook, sb)
    b.read_points(b.self_, clock.now)
    assert b.keys_pressed == []


def test_read_points_presses_the_key_and_logs_once(clock: Clock) -> None:
    b = Bot(prof=profile(points_key="t", points_every=1.5))
    sb = FakeSB(cls="Warrior")
    b.skillbook = as_kind(SkillBook, sb)
    b.read_points(b.self_, clock.now)
    assert sb.took and b.keys_pressed == ["t"]
    assert b.last_points_key == clock.now
    assert "reading rage with the t key" in b.lines


def test_read_points_waits_out_points_every(clock: Clock) -> None:
    b = Bot(prof=profile(points_key="t", points_every=1.5))
    sb = FakeSB()
    b.skillbook = as_kind(SkillBook, sb)
    b.last_points_key = clock.now
    b.read_points(b.self_, clock.now + 0.5)
    assert sb.took                 # the reading is always folded in
    assert b.keys_pressed == []             # but the key isn't pressed yet


def test_read_points_reads_speech_only_every_15_seconds(clock: Clock) -> None:
    b = Bot(link=Link(send="on"), prof=profile(points_key="t", points_every=1.5))
    b.skillbook = as_kind(SkillBook, FakeSB())
    b.read_points(b.self_, 1000.0)          # reads the speech, sees it on
    b.read_points(b.self_, 1000.1)          # not again
    assert b.link.sent.count("speech") == 1


def test_read_points_while_the_game_may_speak_slows_to_six_seconds(clock: Clock) -> None:
    b = Bot(link=Link(send="on"), prof=profile(points_key="t", points_every=1.5))
    b.skillbook = as_kind(SkillBook, FakeSB())
    b._speech_at = clock.now                # already read: no trip for the speech
    b._speech = "on"
    b.last_points_key = clock.now - 5.0
    b.read_points(b.self_, clock.now)
    assert b.keys_pressed == []             # 5 s is under the 6 s the talking game forces


# ------------------------------------------------------------------ select_target
def test_select_target_without_keys_is_no_check() -> None:
    b = Bot(prof=profile(target_keys={}))
    b.ui = lambda: {"target": -5}
    assert b.select_target({"id": -1, "x": 120.0, "y": 100.0}) is True
    assert b.keys_pressed == []


def test_select_target_without_a_readable_target_is_no_check() -> None:
    b = Bot(prof=profile(target_keys={"east": ";"}))
    b.ui = lambda: {}
    assert b.select_target({"id": -1, "x": 120.0, "y": 100.0}) is True
    assert b.keys_pressed == []


def test_select_target_already_selected_presses_nothing() -> None:
    b = Bot(prof=profile(target_keys={"east": ";"}))
    b.ui = lambda: {"target": -1}
    assert b.select_target({"id": -1, "x": 120.0, "y": 100.0}) is True
    assert b.keys_pressed == []


def test_select_target_false_when_the_key_never_picks_it() -> None:
    b = Bot(prof=profile(target_keys={"east": ";"}))
    b.ui = lambda: {"target": -2}
    b.sleep = lambda seconds: None
    assert b.select_target({"id": -1, "x": 120.0, "y": 100.0}) is False
    assert b.keys_pressed                    # it did try


# ------------------------------------------------------------------ hit
def test_hit_true_only_on_a_fresh_hp_drop() -> None:
    b = Bot(prof=profile(fight_back=True))
    b.last_hp = 100.0
    assert b.hit({"hp": 90.0}) is True and b.last_hp == 90.0
    assert b.hit({"hp": 90.0}) is False      # no further drop
    assert b.hit({"hp": 95.0}) is False      # it healed: not a hit


def test_hit_is_off_for_the_first_step_the_walk_assistant_and_when_off(clock: Clock) -> None:
    b = Bot(prof=profile(fight_back=True))
    assert b.hit({"hp": 50.0}) is False      # no previous reading
    b.last_hp = 100.0
    b.walk_only = True
    assert b.hit({"hp": 50.0}) is False
    b.walk_only = False
    b.in_fight = True
    assert b.hit({"hp": 40.0}) is False
    b.in_fight = False
    b.p["fight_back"] = False
    assert b.hit({"hp": 30.0}) is False


# ------------------------------------------------------------------ fight_back
def test_fight_back_does_nothing_for_the_walk_assistant() -> None:
    b = Bot()
    b.walk_only = True
    touched: list[int] = []
    b.snap = lambda: touched.append(1) or {}
    b.fight_back()
    assert touched == []


def test_fight_back_returns_when_nothing_is_close() -> None:
    b = Bot()
    b.targets = lambda snap, area=None, only=None, skip=None, avoid_rects=(), limit=None: []
    fights: list[dict[str, Any]] = []
    b.fight = lambda mob: fights.append(mob)
    b.fight_back()
    assert fights == []


def test_fight_back_sets_modes_once_and_fights_the_first() -> None:
    b = Bot()
    b.modes_done = False
    modes: list[int] = []
    b.set_modes = lambda: modes.append(1)
    b.attackers = lambda: {-1}
    seq: Iterator[list[dict[str, Any]]] = iter([[{"id": -1, "name": "Wolf", "x": 101.0, "y": 100.0}], []])
    b.targets = lambda snap, area=None, only=None, skip=None, avoid_rects=(), limit=None: next(seq, list[dict[str, Any]]())
    fights: list[dict[str, Any]] = []
    b.fight = lambda mob: fights.append(mob)
    b.fight_back()
    assert modes == [1] and len(fights) == 1
    assert b.modes_done is True
    assert "attacked by Wolf, fighting back" in b.lines


def test_fight_back_without_cast_reports_uses_distance() -> None:
    b = Bot(prof=profile(attack_range=4.0))
    b.casts_seen = False
    b.attackers = lambda: set()
    near = monster(-1, x=103.0, y=100.0)     # 3 tiles: within reach + 6
    far = monster(-2, x=120.0, y=100.0)
    seq: Iterator[list[dict[str, Any]]] = iter([[near, far], []])
    b.targets = lambda snap, area=None, only=None, skip=None, avoid_rects=(), limit=None: next(seq, list[dict[str, Any]]())
    fights: list[dict[str, Any]] = []
    b.fight = lambda mob: fights.append(mob)
    b.fight_back()
    assert [m["id"] for m in fights] == [-1]


# ------------------------------------------------------------------ set_target_mode
class ModeKeys:
    """The target-mode key presses a mode_bot saw, and how many."""

    def __init__(self) -> None:
        self.i = 0
        self.keys: list[str] = []


def mode_bot(modes: list[int]) -> tuple[Bot, ModeKeys]:
    b = Bot(prof=profile(target_mode_key="v"))
    state = ModeKeys()
    b.a.json = lambda line: {"target": modes[min(state.i, len(modes) - 1)]}

    def key(k: str, ms: int | None = None) -> None:
        state.keys.append(k)
        state.i += 1
    b.key = key
    return b, state


def test_set_target_mode_gives_up_when_the_game_errors() -> None:
    b = Bot(prof=profile(target_mode_key="v"))
    b.a.json = lambda line: {"error": "no modes"}
    assert b.set_target_mode(2) is False
    assert b.keys_pressed == []


def test_set_target_mode_is_a_no_op_when_already_there() -> None:
    b, state = mode_bot([2])
    assert b.set_target_mode(2) is True
    assert state.keys == []


def test_set_target_mode_cycles_with_the_key() -> None:
    b, state = mode_bot([0, 1, 2])
    assert b.set_target_mode(2) is True
    assert state.keys == ["v", "v"]


def test_set_target_mode_gives_up_after_a_full_cycle() -> None:
    b, state = mode_bot([0])
    assert b.set_target_mode(2) is False
    assert len(state.keys) == len(settings.TARGET_MODES) + 1


# ------------------------------------------------------------------ target_enemies
def test_target_enemies_off_in_the_profile() -> None:
    b = Bot(prof=profile(set_modes=False))
    calls: list[str] = []
    b.a.json = lambda line: calls.append(line) or {"target": 0}
    b.target_enemies()
    assert calls == []


def test_target_enemies_leaves_an_unreadable_mode_alone() -> None:
    b = Bot(prof=profile())
    b.a.json = lambda line: {}               # no "target" in the answer
    b.set_target_mode = lambda want: pytest.fail("must not try")
    b.target_enemies()
    assert b.lines == []


def test_target_enemies_keeps_a_matching_mode() -> None:
    b = Bot(prof=profile(target_mode=2))
    b.a.json = lambda line: {"target": 2}
    b.set_target_mode = lambda want: pytest.fail("must not try")
    b.target_enemies()
    assert b.lines == []


def test_target_enemies_sets_a_wrong_mode_back() -> None:
    b = Bot(prof=profile(target_mode=2))
    b.a.json = lambda line: {"target": 0}
    asked: list[int] = []
    b.set_target_mode = lambda want: asked.append(want) or True
    b.target_enemies()
    assert asked == [2]
    assert any("back to" in line for line in b.lines)


def test_target_enemies_says_when_it_couldnt_set_the_mode() -> None:
    b = Bot(prof=profile(target_mode=2))
    b.a.json = lambda line: {"target": 0}
    b.set_target_mode = lambda want: False
    b.target_enemies()
    assert any("couldn't set the mode" in line for line in b.lines)


# ------------------------------------------------------------------ reach
def test_reach_a_fixed_number_is_used_as_written() -> None:
    b = Bot(prof=profile(attack_range=8.0))
    assert b.reach() == 8.0


def test_reach_five_is_treated_as_the_auto_default() -> None:
    b = Bot(prof=profile(attack_range=5.0))
    assert b.reach() == 5.0                  # smart_skills off: no book to read


def test_reach_auto_fights_from_just_inside_the_base_attack_range() -> None:
    b = Bot(prof=profile(attack_range="auto"))
    b.skillbook = as_kind(SkillBook, types.SimpleNamespace(ok=True, slots={0: [{"role": "base_attack", "range": 20}]}))
    assert b.reach() == 19.5


def test_reach_auto_takes_the_shortest_base_attack() -> None:
    b = Bot(prof=profile(attack_range="auto"))
    b.skillbook = as_kind(SkillBook, types.SimpleNamespace(
        ok=True, slots={0: [{"role": "base_attack", "range": 20}], 1: [{"role": "base_attack", "range": 12}]}))
    assert b.reach() == 11.5


def test_reach_auto_never_goes_under_four() -> None:
    b = Bot(prof=profile(attack_range="auto"))
    b.skillbook = as_kind(SkillBook, types.SimpleNamespace(ok=True, slots={0: [{"role": "base_attack", "range": 4}]}))
    assert b.reach() == 4.0
    b.skillbook = as_kind(SkillBook, types.SimpleNamespace(ok=True, slots={0: [{"role": "base_attack", "range": 5}]}))
    assert b.reach() == 4.5             # melee: just inside its 5


def test_reach_auto_falls_back_without_a_base_attack() -> None:
    b = Bot(prof=profile(attack_range="auto"))
    b.skillbook = as_kind(SkillBook, types.SimpleNamespace(ok=True, slots={0: [{"role": "single_target", "range": 30}]}))
    assert b.reach() == 5.0


def test_reach_auto_builds_the_skillbook_lazily(monkeypatch: pytest.MonkeyPatch) -> None:
    built: list[object] = []
    monkeypatch.setattr(fighting.combat, "SkillBook",
                        stand_in(fighting.combat.SkillBook, lambda bot: built.append(bot) or types.SimpleNamespace(ok=True, slots={})))
    b = Bot(prof=profile(attack_range="auto", smart_skills=True))
    assert b.reach() == 5.0
    assert built == [b]


# ------------------------------------------------------------------ clear_shot
class FakeModel:
    def __init__(self, blocked: set[tuple[int, int]] | tuple[()]=()) -> None:
        self.blocked = set(blocked)

    def ok(self, x: int, y: int) -> bool:
        return (x, y) not in self.blocked

    def line(self, a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
        (x0, y0), (x1, y1) = a, b
        n = int(max(abs(x1 - x0), abs(y1 - y0))) + 1
        return [(round(x0 + (x1 - x0) * i / max(1, n - 1)), round(y0 + (y1 - y0) * i / max(1, n - 1)))
                for i in range(n)]


def test_clear_shot_true_over_open_ground(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fighting.atlas, "model", stand_in(fighting.atlas.model, lambda mid: FakeModel()))
    b = Bot()
    assert b.clear_shot({"x": 100.2, "y": 100.4}, {"x": 105.0, "y": 100.0}, 5121) is True


def test_clear_shot_false_when_a_wall_is_in_the_way(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(fighting.atlas, "model", stand_in(fighting.atlas.model, lambda mid: FakeModel({(102, 100)})))
    b = Bot()
    assert b.clear_shot({"x": 100.0, "y": 100.0}, {"x": 105.0, "y": 100.0}, 5121) is False


def test_clear_shot_asks_for_the_map_when_mid_is_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[int | str] = []
    monkeypatch.setattr(fighting.atlas, "model", stand_in(fighting.atlas.model, lambda mid: (seen.append(mid), FakeModel())[1]))
    b = Bot(map_id=777)
    b.clear_shot({"x": 100.0, "y": 100.0}, {"x": 101.0, "y": 100.0})
    assert seen == [777]


def test_clear_shot_assumes_clear_when_the_map_is_unreadable(monkeypatch: pytest.MonkeyPatch, allow_ignored_failures: None) -> None:
    def boom(mid: int):
        raise ValueError("no map")
    monkeypatch.setattr(fighting.atlas, "model", boom)
    b = Bot()
    assert b.clear_shot({"x": 0, "y": 0}, {"x": 0, "y": 0}) is True


# ------------------------------------------------------------------ count_kills / counted_kill
def test_count_kills_needs_a_character_id() -> None:
    b = Bot()
    assert b.count_kills({"self": {}}) == []
    assert b.count_kills(None) == []


def test_counted_kill_window_is_thirty_seconds(clock: Clock) -> None:
    b = Bot()
    b.counted = {-1: clock.now - 10}
    assert b.counted_kill(-1) is True
    b.counted[-1] = clock.now - 31
    assert b.counted_kill(-1) is False
    assert b.counted_kill(-2) is False


# ------------------------------------------------------------------ watch_hp
def test_watch_hp_ignores_non_monsters_and_reads_the_rest(clock: Clock) -> None:
    b = Bot()
    snap = {"self": b.self_, "entities": [
        {"id": 2, "kind": "npc", "hp": 10},
        monster(-1, hp=100),
        {"id": -2, "kind": "monster"},        # no HP: skipped
    ]}
    b.watch_hp(snap)
    assert list(b.hp_log) == [-1] and b.hp_log[-1] == [(clock.now, 100)]


def test_watch_hp_starts_over_when_a_monster_heals(clock: Clock) -> None:
    b = Bot()
    snap = {"self": b.self_, "entities": [monster(-1, hp=100)]}
    b.watch_hp(snap)
    clock.advance(1.0)
    snap["entities"] = [monster(-1, hp=100)]
    b.watch_hp(snap)
    assert b.hp_log[-1] == [(1000.0, 100), (1001.0, 100)]
    clock.advance(1.0)
    snap["entities"] = [monster(-1, hp=150)]     # healed
    b.watch_hp(snap)
    assert b.hp_log[-1] == [(1002.0, 150)]


def test_watch_hp_drops_readings_older_than_the_window(clock: Clock) -> None:
    b = Bot()
    b.watch_hp({"self": b.self_, "entities": [monster(-1, hp=100)]})
    for _ in range(int(b.HP_WINDOW / 0.5) + 3):
        clock.advance(0.5)
        b.watch_hp({"self": b.self_, "entities": [monster(-1, hp=100)]})
    assert all(clock.now - r[0] <= b.HP_WINDOW for r in b.hp_log[-1])


# ------------------------------------------------------------------ attackers
def test_attackers_are_the_negative_casters(clock: Clock) -> None:
    def j(line: str):
        return {"casts": [{"caster": -1}, {"caster": 55}, {"caster": -2}]}
    b = Bot(link=Link(json=j))
    b._attackers_at = 0
    assert b.attackers() == {-1, -2}
    assert b.casts_seen is True


def test_attackers_are_cached_for_half_a_second(clock: Clock) -> None:
    calls: list[str] = []
    b = Bot(link=Link(json=lambda line: calls.append(line) or {"casts": []}))
    b._attackers_at = 0
    b.attackers()
    b.attackers()
    assert len(calls) == 1
    clock.advance(0.6)
    b.attackers()
    assert len(calls) == 2


def test_attackers_says_the_agent_cant_report(clock: Clock) -> None:
    b = Bot(link=Link(json=lambda line: {"error": "old agent"}))
    b._attackers_at = 0
    assert b.attackers() == set() and b.casts_seen is False
    b.a.json = lambda line: []               # not even a dict
    b._attackers_at = 0
    assert b.attackers() == set() and b.casts_seen is False


# ------------------------------------------------------------------ targets
def target_bot(entities: list[Any], **prof: Any) -> Bot:
    b = Bot(entities=entities, prof=profile(**prof))
    straight_walk(b)
    b._attackers_at = 1e12
    b._attackers = set()
    return b


def ids(out: list[Any]) -> list[Any]:
    return [e["id"] for e in out]


def test_targets_nearest_first(clock: Clock) -> None:
    b = target_bot([monster(-1, x=110.0), monster(-2, x=105.0)])
    assert ids(b.targets(b.snap())) == [-2, -1]


def test_targets_leave_out_everything_that_is_not_a_monster(clock: Clock) -> None:
    b = target_bot([
        monster(-1),
        monster(2, kind="npc"),
        monster(-3, hp=0),
        monster(-4, name=""),
        monster(-5, maxhp=1),
        monster(-6, maxhp=NPC_HP),
        monster(-7, name="Bash"),             # our own trap: named after a skill
    ])
    assert ids(b.targets(b.snap())) == [-1]


def test_targets_honour_the_skip_maps(clock: Clock) -> None:
    b = target_bot([monster(-1), monster(-2)])
    b.skip = {-1: clock.now + 100}
    b.skip_names[(5121, "wolf")] = clock.now + 100
    assert ids(b.targets(b.snap())) == []


def test_targets_honour_only_and_skip_name_filters(clock: Clock) -> None:
    b = target_bot([monster(-1, name="Wolf"), monster(-2, name="Bear")])
    assert ids(b.targets(b.snap(), only="wolf")) == [-1]
    assert ids(b.targets(b.snap(), skip="wolf")) == [-2]


def test_targets_with_a_limit_leave_the_far_ones_out(clock: Clock) -> None:
    b = target_bot([monster(-1, x=105.0), monster(-2, x=130.0)])
    assert ids(b.targets(b.snap(), limit=10)) == [-1]


def test_targets_with_an_area_leave_the_rest_out(clock: Clock) -> None:
    b = target_bot([monster(-1, x=105.0), monster(-2, x=130.0)])
    assert ids(b.targets(b.snap(), area=(95, 95, 110, 105))) == [-1]


def test_targets_avoid_rects(clock: Clock) -> None:
    b = target_bot([monster(-1, x=105.0), monster(-2, x=130.0)])
    assert ids(b.targets(b.snap(), avoid_rects=[(129, 99, 131, 101)])) == [-1]


def test_doomed_leaves_a_monster_another_player_is_killing_alone(clock: Clock) -> None:
    b = target_bot([monster(-1, x=130.0)])
    b.dying_in = lambda e: 1.0
    snap = b.snap()
    assert b.doomed(snap["entities"][0], snap["self"])


def test_fight_locked_fights_a_monster_another_player_is_killing(clock: Clock) -> None:
    # 30 tiles off, reach 10: 20 tiles of walk (5 s) and the first hit (0.5 s) before a share
    b = target_bot([monster(-1, x=130.0)], fight_locked=True, attack_range=10.0)
    b.dying_in = lambda e: 5.8
    snap = b.snap()
    assert not b.doomed(snap["entities"][0], snap["self"])
    assert ids(b.targets(snap)) == [-1]


def test_without_fight_locked_the_close_call_is_left(clock: Clock) -> None:
    b = target_bot([monster(-1, x=130.0)], attack_range=10.0)
    b.dying_in = lambda e: 5.8
    snap = b.snap()
    assert b.doomed(snap["entities"][0], snap["self"])


def test_fight_locked_leaves_one_that_is_dead_before_we_get_there(clock: Clock) -> None:
    # the walk alone outlasts it: no hit of ours could land, so there is no share to walk for
    b = target_bot([monster(-1, x=130.0)], fight_locked=True, attack_range=10.0)
    b.dying_in = lambda e: 4.0
    snap = b.snap()
    assert b.doomed(snap["entities"][0], snap["self"])
    assert ids(b.targets(snap)) == []


def test_fight_locked_shoots_one_in_reach_however_soon_it_dies(clock: Clock) -> None:
    b = target_bot([monster(-1, x=108.0)], fight_locked=True, attack_range=10.0)
    b.dying_in = lambda e: 0.1
    snap = b.snap()
    assert not b.doomed(snap["entities"][0], snap["self"])


def test_targets_put_those_attacking_us_first(clock: Clock) -> None:
    b = target_bot([monster(-1, x=110.0), monster(-2, x=105.0)])
    b._attackers = {-1}
    assert ids(b.targets(b.snap())) == [-1, -2]


def test_targets_prune_the_name_memory_when_it_overflows(clock: Clock) -> None:
    b = target_bot([monster(-1)])
    b.mob_names = {i: "Wolf" for i in range(2001)}
    out = b.targets(b.snap())
    assert b.mob_names == {-1: "Wolf"} and ids(out) == [-1]


# ------------------------------------------------------------------ next_target
def next_bot(cls: str="Warrior", skillbook: bool=True, **prof: Any) -> Bot:
    b = Bot(prof=profile(**prof))
    b._attackers_at = 1e12
    b._attackers = set()
    b.skillbook = as_kind(SkillBook, types.SimpleNamespace(ok=True, cls=cls)) if skillbook else None
    b.clear_shot = lambda me, mob, mid=None: True
    return b


def snap_of(b: Bot, mobs: list[dict[str, Any]]) -> dict[str, Any]:
    return {"map": {"id": 5121}, "self": b.self_, "entities": mobs}


def test_next_target_an_attacker_comes_first() -> None:
    b = next_bot()
    mobs = [monster(-1, x=130.0), monster(-2, x=105.0)]
    b._attackers = {-1}
    assert b.next_target(snap_of(b, mobs), mobs)["id"] == -1


def test_next_target_a_monster_we_hurt_comes_first() -> None:
    b = next_bot()
    mobs = [monster(-1, x=130.0), monster(-2, x=105.0)]
    b.kill_tracker.hit[-1] = 1
    assert b.next_target(snap_of(b, mobs), mobs)["id"] == -1


def test_next_target_without_a_book_takes_the_nearest() -> None:
    b = next_bot(skillbook=False, attack_range=10.0)
    mobs = [monster(-1, x=105.0), monster(-2, x=130.0)]
    assert b.next_target(snap_of(b, mobs), mobs)["id"] == -1


def test_next_target_without_a_book_falls_back_to_the_first() -> None:
    b = next_bot(skillbook=False, attack_range=2.0)
    mobs = [monster(-1, x=130.0), monster(-2, x=105.0)]
    assert b.next_target(snap_of(b, mobs), mobs)["id"] == -1


def test_next_target_a_melee_takes_the_nearest() -> None:
    b = next_bot(cls="Warrior")
    first = monster(-1, x=130.0, y=100.0)
    crowd = [monster(-2, x=131.0, y=100.0), monster(-3, x=130.0, y=101.0), monster(-4, x=129.0, y=100.0)]
    calm = monster(-5, x=105.0, y=100.0)
    mobs = [calm] + crowd + [first]
    assert b.next_target(snap_of(b, mobs), mobs)["id"] == -5


def test_next_target_a_melee_takes_a_lone_first() -> None:
    b = next_bot(cls="Warrior")
    mobs = [monster(-1, x=105.0), monster(-2, x=130.0)]
    assert b.next_target(snap_of(b, mobs), mobs)["id"] == -1


# ------------------------------------------------------------------ fight / burst / follows
def test_fight_sets_the_fighting_state_and_keeps_the_beast(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    ensured: list[Any] = []
    monkeypatch.setattr(fighting.beasts, "ensure", stand_in(fighting.beasts.ensure, lambda b, purpose: ensured.append(purpose)))
    b = Bot()
    seen: dict[str, bool] = {}

    def fake_fight(mob: dict[str, Any]) -> None:
        seen["in_fight"] = b.in_fight
    b._fight = fake_fight
    b.fight({"id": -1, "name": "Wolf"})
    assert seen["in_fight"] is True
    assert b.in_fight is False
    assert ensured == [fighting.beasts.FIGHT]
    assert b.last_hp is None and b.last_combat == clock.now


def read_field_log() -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name in sorted(os.listdir(paths.LOGS)):
        if name.startswith("field-") and name.endswith(".jsonl"):
            with open(os.path.join(paths.LOGS, name), encoding="utf-8") as f:
                rows.extend(json.loads(line) for line in f)
    return rows


def test_a_picked_fight_goes_to_the_field_log(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    monkeypatch.setattr(fighting.beasts, "ensure", stand_in(fighting.beasts.ensure, lambda b, purpose: None))
    b = target_bot([monster(-1, x=130.0, hp=900), monster(-2, x=105.0)], attack_range=10.0)
    b.character = "Tester"
    snap = b.snap()
    mobs = b.targets(snap)
    far = next(e for e in mobs if e["id"] == -1)
    b.note_pick(snap, far, mobs)

    def fake_fight(mob: dict[str, Any]) -> None:
        clock.now += 2.0
        rec = b.field_record
        assert rec is not None
        rec["reach_at"] = clock.now                 # what _fight does once the walk got it in reach
        clock.now += 3.0
        b.kills += 1
    b._fight = fake_fight
    b.fight(far)
    [rec] = read_field_log()
    assert rec["kind"] == "fight" and rec["who"] == "Tester" and rec["id"] == -1
    assert rec["dist"] == 30.0 and rec["reach"] == 10.0
    assert rec["sight"] == 2 and rec["in_reach"] == 1 and rec["hurt"] is False
    assert rec["reach_s"] == 2.0 and rec["end_s"] == 5.0 and rec["kills"] == 1
    assert "_at" not in rec and "_kills" not in rec


def test_a_fight_the_farm_loop_did_not_pick_writes_nothing(monkeypatch: pytest.MonkeyPatch, clock: Clock) -> None:
    monkeypatch.setattr(fighting.beasts, "ensure", stand_in(fighting.beasts.ensure, lambda b, purpose: None))
    b = target_bot([monster(-1), monster(-2)])
    snap = b.snap()
    b.note_pick(snap, snap["entities"][0], snap["entities"])
    b._fight = lambda mob: None
    b.fight(snap["entities"][1])          # fighting back at another one on the way
    assert read_field_log() == []


def test_a_search_walk_goes_to_the_field_log(clock: Clock) -> None:
    b = target_bot([])
    start = clock.now
    clock.now += 4.0
    b.note_search(5121, {"x": 100.0, "y": 100.0}, (140, 120), start, "near")
    [rec] = read_field_log()
    assert rec["kind"] == "search" and rec["to"] == [140, 120] and rec["s"] == 4.0 and rec["result"] == "near"


def test_burst_true_when_the_game_confirms(clock: Clock) -> None:
    b = Bot()
    sb = Pick()
    sb.read_casts = lambda me: setattr(sb, "press_confirmed", True)
    b.skillbook = as_kind(SkillBook, sb)
    b.stop_event = WaitEvent(clock)
    assert b.burst("j", ME, longest=1.2) is True


def test_burst_stops_when_the_key_is_refused(clock: Clock) -> None:
    b = Bot(link=Link(send="refused"))
    b.skillbook = as_kind(SkillBook, Pick())
    b.stop_event = WaitEvent(clock)
    with pytest.raises(fighting.Stopped):
        b.burst("j", ME, longest=1.2)


def test_burst_stops_when_the_player_presses_stop(clock: Clock) -> None:
    b = Bot()
    b.skillbook = as_kind(SkillBook, Pick())
    b.stop_event = WaitEvent(clock, stop=True)
    with pytest.raises(fighting.Stopped):
        b.burst("j", ME, longest=1.2)


def test_burst_gives_up_after_the_longest_time(clock: Clock) -> None:
    b = Bot()
    b.skillbook = as_kind(SkillBook, Pick())
    b.stop_event = WaitEvent(clock, step=0.5)
    assert b.burst("j", ME, longest=1.0) is False


GAME_KEYS = ["y", "u", "h", "j", "n", "m", "i", "comma", "dot", "slash"]   # input.ini's W_ATTACK1..10


def slot_bot(send: Callable[[str], str] | str = "pressed 4", **kw: Any) -> Bot:
    b = Bot(link=Link(send=send), **kw)
    b.agent_slots = True
    b._attack_keys = list(GAME_KEYS)
    return b


def test_attack_keys_become_slots_and_others_stay_keys() -> None:
    b = slot_bot()
    assert b.slot_command("j") == "slot 4"
    assert b.slot_command("alt+j") == "slot 4 sustain"          # W_SUSTAIN_ATTACK: the other attack style
    assert b.slot_command("lctrl+slash") == "slot 10 lock"      # W_LOCK_ATTACK
    assert b.slot_command("shift+j") is None                    # W_SELF_ATTACK: no slot for it
    assert b.slot_command("r") is None                          # not an attack key at all
    b.agent_slots = False
    assert b.slot_command("j") is None                          # an older agent: keys


def test_a_burst_through_slots_keeps_a_keys_pace_and_looks_every_press(clock: Clock) -> None:
    b = slot_bot()
    sb = Pick()
    looks: list[float] = []

    def read(me: dict[str, Any]):
        looks.append(clock.now)
        sb.press_confirmed = len(looks) == 2
    sb.read_casts = read
    b.skillbook = as_kind(SkillBook, sb)
    b.stop_event = WaitEvent(clock)
    assert b.burst("j", ME, longest=1.2) is True
    assert b.link.sent == ["slot 4", "slot 4"]               # no key at all
    assert looks[1] - looks[0] == pytest.approx(0.08)    # a key's pace: no more cast requests than keys


def test_an_agent_that_refuses_a_slot_goes_back_to_keys_and_says_so(clock: Clock) -> None:
    b = slot_bot(send=lambda line: "error: the attack page can't be read" if line.startswith("slot") else "tapped")
    sb = Pick()
    sb.read_casts = lambda me: setattr(sb, "press_confirmed", True)
    b.skillbook = as_kind(SkillBook, sb)
    b.stop_event = WaitEvent(clock)
    assert b.burst("j", ME, longest=1.2) is True
    assert b.link.sent[0] == "slot 4" and b.link.sent[1] == "key j 30"     # this press goes by key
    assert b.agent_slots is True                                      # one odd answer isn't the end
    b.burst("j", ME, longest=1.2)
    b.burst("j", ME, longest=1.2)
    assert b.agent_slots is False and any("skills go by keys again" in ln for ln in b.lines)


def test_a_switch_to_another_attack_key_waits_out_the_games_switch_gap(clock: Clock) -> None:
    # the game drops a press within 80-90 ms of a press on another attack key (OUT op=17)
    b = slot_bot()
    clock.now = 10.0
    b.press_slot("j")
    clock.now = 10.05
    b.press_slot("j")
    assert b.slept == []                                 # the same key again is never dropped
    b.press_slot("u")
    assert b.slept == [pytest.approx(0.12)]              # 0.12 s after the last press, not the first
    b.slept.clear()
    clock.now = 10.5
    b.press_slot("shift+u")                              # the self-attack path is another slot...
    assert b.slept == []                                 # ...but long enough after
    clock.now = 10.52
    b.press_slot("r")                                    # not an attack key: no wait, not counted
    b.press_slot("alt+u")                                # Alt presses u's own slot
    assert b.slept == [pytest.approx(0.10)]


def test_a_page_turn_makes_the_next_attack_press_wait(clock: Clock) -> None:
    b = slot_bot()
    clock.now = 5.0
    b.press_slot("j")
    b.note_page_turn()                                   # Caps Lock: j is on another slot now
    b.press_slot("j")
    assert b.slept == [pytest.approx(0.12)]


def test_a_good_slot_answer_forgives_the_odd_refusal(clock: Clock) -> None:
    answers = iter(["error: busy", "error: busy", "pressed 4", "error: busy", "error: busy"])
    b = slot_bot(send=lambda line: next(answers) if line.startswith("slot") else "tapped")
    for _press in range(5):
        b.press_slot("j")
    assert b.agent_slots is True          # never three wrong answers in a row


def test_follows_needs_a_book_and_the_follow_style() -> None:
    b = Bot()
    assert b.follows() is False
    sb = types.SimpleNamespace(ok=True, want_follow=True)
    b.skillbook = as_kind(SkillBook, sb)
    assert b.follows() is True
    b.follow_ok = False
    assert b.follows() is False
    b.follow_ok = True
    sb.ok = False
    assert b.follows() is False
    sb.ok = True
    sb.want_follow = False
    assert b.follows() is False


# ------------------------------------------------------------------ defend
def test_defend_fights_attackers_then_stops(clock: Clock) -> None:
    b = Bot(prof=profile(attack_range=6.0))
    hits: Iterator[set[int]] = iter([{-1}, set[int](), set[int]()])
    b.attackers = lambda: next(hits, set[int]())
    b.targets = lambda snap, area=None, only=None, skip=None, avoid_rects=(), limit=None: [{"id": -1, "name": "Wolf", "x": 101.0, "y": 100.0}]
    b.look_after = lambda me=None: me or {}
    b.sleep = lambda seconds: clock.advance(seconds)
    fights: list[dict[str, Any]] = []
    b.fight = lambda mob: fights.append(mob)
    b.defend(quiet=1.0, limit=100.0)
    assert [m["id"] for m in fights] == [-1]
    assert any("fighting back before stopping" in line for line in b.lines)


# ------------------------------------------------------------------ _fight
def near_walk(b: Bot) -> None:
    """walk_to that reports reaching the near-stop: what _fight passes as near=."""
    def walk_to(cell: tuple[float, float], map_change: bool=False, near: Any = None):
        assert callable(near)
        return (near(b.self_), "near")[1]
    b.walk_to = walk_to


def fight_bot(**prof: Any) -> Bot:
    b = Bot(prof=profile(**prof))
    b.clear_shot = lambda me, mob, mid=None: True
    b.timed = lambda phase: contextlib.nullcontext()
    b.walked = []
    b.selects = []
    b.hit_calls = []
    b.target_enemies = lambda: None
    b.select_target = lambda mob, by_keys=False: (b.selects.append(mob["id"]), True)[1]
    b._hit_until_down = lambda mob: b.hit_calls.append(mob["id"])
    b.walk_to = lambda cell, map_change=False, near=None: "arrived"
    return b


def test_fight_in_reach_selects_and_hits() -> None:
    b = fight_bot(attack_range=10.0)
    b._fight(monster(-1, x=105.0))
    assert b.selects == [-1] and b.hit_calls == [-1] and b.walked == []


def test_fight_walks_in_then_selects_and_hits() -> None:
    b = fight_bot(attack_range=10.0)
    b.walk_to = lambda cell, map_change=False, near=None: (b.walked.append(cell), "arrived")[1]
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [monster(-1, x=105.0)]}
    b._fight(monster(-1, x=120.0))
    assert b.walked == [(120.0, 100.0)]
    assert b.selects == [-1] and b.hit_calls == [-1]


def test_fight_skips_a_monster_it_could_not_walk_to(clock: Clock) -> None:
    b = fight_bot()                          # reach 4: the monster at 120 is out of reach
    b.walk_to = lambda cell, map_change=False, near=None: "stuck"
    b._fight(monster(-1, x=120.0))
    assert b.skip[-1] == clock.now + 120 and b.hit_calls == []


def test_fight_notes_a_monster_taken_before_we_get_there(clock: Clock) -> None:
    b = fight_bot()
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": []}
    b.count_kills = lambda snap=None: []
    b.counted_kill = lambda mid: False
    b._fight(monster(-1, x=120.0))
    assert any("taken by someone else" in line for line in b.lines)
    assert b.hit_calls == []


def test_fight_says_nothing_when_the_kill_was_ours(clock: Clock) -> None:
    b = fight_bot()
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": []}
    b.count_kills = lambda snap=None: []
    b.counted_kill = lambda mid: True
    b._fight(monster(-1, x=120.0))
    assert not any("taken by someone else" in line for line in b.lines)


def test_fight_leaves_a_doomed_monster_alone(clock: Clock) -> None:
    b = fight_bot()
    near_walk(b)
    b.doomed = lambda e, me, steps=None, rng=None: True
    b.dying_in = lambda e: 3.0
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [monster(-1, x=120.0)]}
    b._fight(monster(-1, x=120.0))
    assert b.skip[-1] == clock.now + 5.0
    assert any("about to be killed by someone else" in line for line in b.lines)
    assert b.hit_calls == []


def test_fight_skips_a_monster_the_keys_wont_select(clock: Clock) -> None:
    b = fight_bot(attack_range=10.0)
    b.select_target = lambda mob, by_keys=False: False
    b._fight(monster(-1, x=105.0))
    assert b.skip[-1] == clock.now + 10
    assert any("wouldn't select" in line for line in b.lines)
    assert b.hit_calls == []


def test_fight_walks_up_when_the_pull_stopped_beyond_the_keys_reach(clock: Clock) -> None:
    b = fight_bot(attack_range=10.0, pull_range=15.0)
    mob = monster(-1, x=140.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    selects: list[Any] = []

    def select(mob: dict[str, Any], by_keys: bool = False) -> bool:
        selects.append(mob["x"])
        return len(selects) > 1

    def walk(cell: tuple[float, float], map_change: bool = False,
             near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        if near is None:
            b.walked.append(cell)          # the second, close-in walk: no early pull stop
            return "arrived"
        for _ in range(40):
            mob["x"] -= 1.0                # it runs at us, a tile a step
            if near(b.self_):
                return "near"
        return "arrived"

    b.select_target = select
    b.walk_to = walk
    b._fight(mob)
    assert b.walked == [(120.0, 100.0)]     # stopped at the keys' reach, then walked the rest
    assert len(selects) == 2                # tried at 20 yards, then again up close
    assert -1 not in b.skip
    assert b.hit_calls == [-1]


def test_fight_stops_beside_another_monster_the_farm_filter_takes() -> None:
    b = fight_bot(attack_range=4.0)
    b.farm_filter = (None, "", "", [])
    other = monster(-2, x=103.0, y=100.0)
    b.targets = lambda snap, area=None, only=None, skip=None, avoid_rects=(), limit=None: [other]
    b.doomed = lambda e, me, steps=None, rng=None: False
    near_walk(b)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_),
                      "entities": [monster(-1, x=120.0), other]}
    b._fight(monster(-1, x=120.0))
    assert b.walked == []                    # the near() stop happened inside walk_to
    assert b.hit_calls == [] and b.selects == []


def test_pull_range_opens_fire_while_the_monster_closes_in(clock: Clock) -> None:
    b = fight_bot(attack_range=10.0, pull_range=6.0)
    mob = monster(-1, x=140.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    stopped_at: list[Any] = []

    def walk(cell: tuple[float, float], map_change: bool = False,
             near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        assert near is not None
        for _ in range(40):
            mob["x"] -= 2.0                     # it runs at us, 2 tiles a step
            clock.advance(0.2)
            if near(b.self_):
                stopped_at.append(mob["x"])
                return "near"
        return "arrived"

    b.walk_to = walk
    b._fight(mob)
    assert stopped_at == [116.0]                # opened fire 16 tiles out (reach 10 + pull 6)
    assert b.hit_calls == [-1]
    assert b.walked == []


def test_pull_range_stops_within_the_games_target_reach(clock: Clock) -> None:
    b = fight_bot(attack_range=10.0, pull_range=30.0)
    mob = monster(-1, x=200.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    stopped_at: list[Any] = []

    def walk(cell: tuple[float, float], map_change: bool = False,
             near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        assert near is not None
        for _ in range(100):
            mob["x"] -= 1.0
            if near(b.self_):
                stopped_at.append(mob["x"])
                return "near"
        return "arrived"

    b.walk_to = walk
    b._fight(mob)
    assert stopped_at == [120.0]                # 20 tiles: the keys' reach, not reach + 30
    assert b.hit_calls == [-1]


def test_pull_range_extends_the_fight_leash() -> None:
    b = fight_bot(attack_range=10.0, pull_range=6.0)
    assert b.leash_for(False, 10.0, 10.0, 6.0) == 16.0
    assert b.leash_for(True, 10.0, 12.0, 6.0) == 18.0
    assert b.leash_for(False, 10.0, 10.0, 0.0) == 13.0


def test_pull_range_does_not_fire_at_a_monster_that_stands_still(clock: Clock) -> None:
    b = fight_bot(attack_range=10.0, pull_range=6.0)
    mob = monster(-1, x=140.0)                  # it never moves: all the closing is our walking
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    stopped_at: list[Any] = []

    def walk(cell: tuple[float, float], map_change: bool = False,
             near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        assert near is not None
        for _ in range(40):
            b.self_["x"] += 2.0                 # our own steps
            clock.advance(0.2)
            if near(b.self_):
                stopped_at.append(mob["x"] - b.self_["x"])
                return "near"
        return "arrived"

    b.walk_to = walk
    b._fight(mob)
    assert stopped_at == [10.0]                 # in reach, not early
    assert b.hit_calls == [-1]


def test_pull_range_off_walks_until_reach(clock: Clock) -> None:
    b = fight_bot(attack_range=10.0, pull_range=0.0)
    mob = monster(-1, x=140.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    stopped_at: list[Any] = []

    def walk(cell: tuple[float, float], map_change: bool = False,
             near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        assert near is not None
        for _ in range(40):
            mob["x"] -= 2.0
            clock.advance(0.2)
            if near(b.self_):
                stopped_at.append(mob["x"])
                return "near"
        return "arrived"

    b.walk_to = walk
    b._fight(mob)
    assert stopped_at == [110.0]                # only when it is within reach


# ------------------------------------------------------------------ _hit_until_down
def until_bot(prof: dict[str, Any] | None=None, sb: Pick | None=None, tracker: FakeTracker | None=None) -> Bot:
    b = Bot(prof=prof or profile(attack_range=10.0))
    b.clear_shot = lambda me, mob, mid=None: True
    b.count_kills = lambda snap=None: []
    b.skillbook = as_kind(SkillBook, sb if sb is not None else Pick(want_follow=False))
    b.kill_tracker = as_kind(KillTracker, tracker if tracker is not None else FakeTracker(live=False))
    return b


def test_hit_until_down_leaves_a_monster_that_takes_no_damage(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=0.0, no_damage_casts=99))
    mob = monster(-1, x=105.0)

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + 120
    assert any("takes no damage, leaving it" in line for line in b.lines)
    assert any("leaving every Wolf here alone" in line for line in b.lines)


def test_in_a_dungeon_a_monster_with_no_damage_never_writes_off_its_kind(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=0.0, no_damage_casts=99))
    b.in_instance = True          # its one kind of monster is what the visit is for
    mob = monster(-1, x=105.0)

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + 120            # this one is still left for a while
    assert b.skip_names == {}
    assert not any("leaving every Wolf here alone" in line for line in b.lines)


def test_hit_until_down_blames_our_mp_when_it_is_empty(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=0.0, no_damage_casts=99))
    b.mp_share = 5
    mob = monster(-1, x=105.0)

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + 20
    assert any("too little to hurt" in line for line in b.lines)


def test_hit_until_down_gives_up_when_it_never_got_there(clock: Clock) -> None:
    b = until_bot(profile(attack_range=2.0, no_damage_timeout=0.0))
    mob = monster(-1, x=105.0)               # 5 tiles: out of reach 2

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + 10
    assert any("couldn't get to" in line for line in b.lines)


def test_hit_until_down_leaves_when_the_fight_took_too_long(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, fight_timeout=0.0))
    b.skillbook = None
    mob = monster(-1, x=105.0)
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + 120
    assert any("fight took too long" in line for line in b.lines)


def test_hit_until_down_notes_a_monster_taken_before_the_first_press(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0))
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": []}
    mob = monster(-1, x=105.0)
    b._hit_until_down(mob)
    assert any("taken by someone else" in line for line in b.lines)


def test_hit_until_down_counts_the_kill(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999))
    b.skillbook = as_kind(SkillBook, Pick(want_follow=False, pick=None))
    mob = monster(-1, x=105.0)
    state = {"n": 0}

    def snap():
        clock.advance(0.5)
        state["n"] += 1
        e = dict(mob)
        if state["n"] >= 2:
            e["hp"] = 0
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [e]}
    b.snap = snap
    b._hit_until_down(mob)
    assert b.kills == 1
    assert any("Wolf down (1 so far)" in line for line in b.lines)


def test_hit_until_down_follows_our_hits_to_another_monster(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=0.0, no_damage_casts=99))
    b.skillbook = as_kind(SkillBook, Pick(want_follow=False, pick=None))
    b.kill_tracker = as_kind(KillTracker, FakeTracker(live=False, landed={-7: 5000}))
    old = monster(-1, x=105.0)
    other = monster(-7, x=106.0, name="Orc")
    state = {"n": 0}

    def snap():
        clock.advance(0.5)
        state["n"] += 1
        o, t = dict(old), dict(other)
        if state["n"] >= 2:
            t["hp"] = 0
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [o, t]}
    b.snap = snap
    b._hit_until_down(old)
    assert any("our hits land on Orc - fighting that one" in line for line in b.lines)
    assert b.kills == 1


def test_hit_until_down_gives_up_a_lost_target(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999))
    b.skillbook = as_kind(SkillBook, Pick(want_follow=False, pick=None))
    b.select_target = lambda mob, by_keys=False: False
    b.ui = lambda: {"target": -99}
    mob = monster(-1, x=105.0)

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + 30
    assert b.keys_pressed == ["j", "y", "."]   # a plain attack key each turn
    assert any("lost our target" in line for line in b.lines)   # the give-up says why


def test_hit_until_down_lets_the_farm_loop_walk_up_a_target_out_of_reach(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, pull_range=15.0, no_damage_timeout=999,
                          no_damage_casts=999))
    b.skillbook = as_kind(SkillBook, Pick(want_follow=False, pick=None))
    b.select_target = lambda mob, by_keys=False: False
    b.ui = lambda: {"target": -99}
    mob = monster(-1, x=120.0)              # within the pull leash, beyond the keys' reach

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert -1 not in b.skip                  # not written off: the farm loop walks up again
    assert not any("lost our target" in line for line in b.lines)


def test_hit_until_down_presses_the_follow_key_while_walking_in(clock: Clock) -> None:
    b = until_bot(profile(attack_range=4.0, no_damage_timeout=999, no_damage_casts=999))
    b.skillbook = as_kind(SkillBook, Pick(want_follow=True, pick="js", follow_key="F"))
    b.follow_ok = True
    mob = monster(-1, x=108.0, y=100.0)      # within the leash, out of reach
    b.ui = lambda: {"target": -1}

    def snap():
        clock.advance(0.5)
        return {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b.snap = snap
    b._hit_until_down(mob)
    assert "F" in b.keys_pressed
    assert b.follow_stalls == 1 and b.skip[-1] == clock.now + 10


def test_hit_until_down_leaves_a_monster_the_game_casts_nothing_at(clock: Clock) -> None:
    # live 2026-10-22: in turkey cave the server refused every cast at monsters behind door edges,
    # and each one waited the whole no-damage time (221 in a night) - three refused bursts do now
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999))
    sb = Pick(want_follow=False, pick=("j", "Arcane Shot"))
    b.skillbook = as_kind(SkillBook, sb)
    b.burst = lambda key, me_id, longest=1.0: (clock.advance(1.0), False)[1]
    b.ui = lambda: {"target": -1}
    mob = monster(-1, x=105.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b._hit_until_down(mob)
    assert b.skip[-1] == clock.now + b.UNSELECTED_SKIP
    assert sb.refused == ["Arcane Shot"] * b.REFUSED_RUN
    assert any("won't let us hit Wolf from here" in line for line in b.lines)
    assert not any("takes no damage" in line for line in b.lines)


def test_refused_casts_at_a_monster_we_have_hurt_are_not_a_reason_to_leave(clock: Clock) -> None:
    b = until_bot(profile(attack_range=10.0, no_damage_timeout=999, no_damage_casts=999, fight_timeout=6.0))
    b.skillbook = as_kind(SkillBook, Pick(want_follow=False, pick=("j", "Arcane Shot")))
    b.burst = lambda key, me_id, longest=1.0: (clock.advance(1.0), False)[1]
    b.kill_tracker = as_kind(KillTracker, FakeTracker(live=False, landed={-1: 5000}))
    b.ui = lambda: {"target": -1}
    mob = monster(-1, x=105.0)
    b.snap = lambda: {"map": {"id": 5121}, "self": dict(b.self_), "entities": [dict(mob)]}
    b._hit_until_down(mob)
    assert not any("won't let us hit" in line for line in b.lines)


def test_only_a_monster_that_struck_us_or_our_pet_is_an_attacker() -> None:
    b: Any = types.SimpleNamespace(agent_version=44)
    old: Any = types.SimpleNamespace(agent_version=30)
    at_us = FightingMixin.at_us
    me, pet = 1001, 5005
    assert at_us(b, {"caster": -3, "hits": [[me, 3, 19]]}, me, {pet})                 # hit us
    assert at_us(b, {"caster": -3, "hits": [[me, 2, 0]]}, me, {pet})                  # we dodged it
    assert at_us(b, {"caster": -3, "targets": [[pet, 9000]]}, me, {pet})              # at our pet
    assert not at_us(b, {"caster": -3, "targets": [[2002, 9000]]}, me, {pet})         # another player's fight
    assert not at_us(b, {"caster": -3, "targets": [[-4, 500]]}, me, {pet})            # healing a friend
    assert not at_us(b, {"caster": -3}, me, {pet})          # agent 44 names targets: this struck none
    assert at_us(old, {"caster": -3}, me, set())           # an older agent: as before
    assert at_us(b, {"caster": -3, "targets": [[2002, 1]]}, None, set())             # our id unknown yet
