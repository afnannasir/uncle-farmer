# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The parts of follow.py the other follow test does not reach: the helpers that tolerate missing
or broken bot methods, the team "/6" phrase, Shift+P teleporting, helping with the player's fight,
following a player around (the _follow loop) and the public follow() wrapper, the exits lookup and
come_to's locate / teammate / travel branches. A pretend bot and a hand-moved clock stand in for the
game; nothing here walks a real map or waits for real time.
"""

from __future__ import annotations

import pytest

from unclefarmer.clock import CLOCK, Waitable
from unclefarmer import gamekeys
from unclefarmer.engine import follow
from unclefarmer.engine.bot import Stopped
from unclefarmer.engine.botparts.parts import Sighting, Where
from collections.abc import Callable, Sequence
from typing import Any
from conftest import as_bot, returning, stand_in


class FlatMap:
    def nearest_walkable(self, x: int, y: int, r: int) -> tuple[int, int]:
        return (x, y + 1)


@pytest.fixture(autouse=True)
def flat_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow.atlas, "model", stand_in(follow.atlas.model, lambda mid: FlatMap()))


class Clock:
    def __init__(self, start: float=1000.0) -> None:
        self.now = start

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


@pytest.fixture
def fclock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    return c


class Link:
    def send(self, line: str) -> str:
        return "ok"

    def json(self, line: str) -> Any:
        return {}


def world(x: float=0.0, y: float=0.0, mid: int=1041, entities: list[dict[str, Any]] | tuple[()]=()) -> dict[str, Any]:
    return {"self": {"x": x, "y": y}, "map": {"id": mid}, "entities": list(entities)}


def other(name: str="Friend", x: float=0.0, y: float=0.0, id: int=9) -> dict[str, Any]:
    return {"kind": "player", "name": name, "x": x, "y": y, "id": id}


def mob(x: float=0.0, y: float=0.0, id: int=-5, hp: int=5, maxhp: int=10) -> dict[str, Any]:
    return {"id": id, "x": x, "y": y, "hp": hp, "maxhp": maxhp}


class Stop(Exception):
    """Raised by a scripted bot whose snapshots have run out, to end a while-True loop."""


class FollowBot:
    def __init__(self, snaps: list[dict[str, Any]] | Callable[[], dict[str, Any]], map_id: int = 1041,
                 locate: Callable[[str], Where | None] | None = None, last_seen: Callable[[str], Sighting | None] | None = None,
                 targets: Callable[[dict[str, Any]], list[dict[str, Any]]] | None = None,
                 attackers: Callable[[], set[int]] | None = None) -> None:
        self.following: str | None = None   # the player a follow run stays with, once it starts
        self._script = snaps if callable(snaps) else None
        self.snaps = None if callable(snaps) else list(snaps)
        self.n = 0
        self._map = map_id
        self.logs: list[str] = []
        self.walks: list[tuple[float, float]] = []
        self.travels: list[tuple[int, tuple[float, float] | None]] = []
        self.fights: list[dict[str, Any]] = []
        self.crosses: list[dict[str, Any]] = []
        self.sleeps: list[float] = []
        self.locate: Callable[[str], Where | None] = locate or (lambda name: None)
        self.last_seen: Callable[[str], Sighting | None] = last_seen or (lambda name: None)
        self._targets: Callable[[dict[str, Any]], list[dict[str, Any]]] = targets or (lambda snap: [])
        self._attackers: Callable[[], set[int]] = attackers or (lambda: set())
        self.on_sleep: Callable[[float], object] | None = None
        self.a = Link()
        self.agent_version = 0
        self.in_fight = False
        self.phrases: dict[int, str] = {}
        self.p: dict[str, Any] = {}

    def check(self) -> None:
        pass

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def map_id(self) -> int:
        return self._map

    def snap(self) -> dict[str, Any]:
        if self._script is not None:
            return self._script()
        snaps = self.snaps or []
        if self.n >= len(snaps):
            raise Stop()
        s = snaps[self.n]
        self.n += 1
        return s

    def look_after(self, me: dict[str, Any]) -> dict[str, Any]:
        return dict(me)

    def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        self.walks.append(cell)
        return "arrived"

    def travel(self, mid: int, cell: tuple[float, float] | None=None) -> None:
        self.travels.append((mid, cell))
        self._map = mid

    def fight(self, m: dict[str, Any]) -> None:
        self.fights.append(m)

    def cross_exit(self, leg: dict[str, Any]) -> None:
        self.crosses.append(leg)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        if self.on_sleep:
            self.on_sleep(seconds)

    def targets(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        return self._targets(snap)

    def attackers(self) -> set[int]:
        return self._attackers()


# ---------------------------------------------------------------- _locate / _last_seen
def test_locate_without_the_method() -> None:
    assert follow._locate(as_bot(object()), "Friend") is None


def test_locate_swallows_errors(allow_ignored_failures: None) -> None:
    class B:
        def locate(self, name: str):
            raise RuntimeError("no sheet")

    assert follow._locate(as_bot(B()), "Friend") is None


def test_locate_returns_the_position() -> None:
    class B:
        def locate(self, name: str):
            return (1042, 5, 6)

    assert follow._locate(as_bot(B()), "Friend") == (1042, 5, 6)


def test_last_seen_without_the_method() -> None:
    assert follow._last_seen(as_bot(object()), "Friend") is None


def test_last_seen_swallows_errors(allow_ignored_failures: None) -> None:
    class B:
        def last_seen(self, name: str):
            raise RuntimeError("gone")

    assert follow._last_seen(as_bot(B()), "Friend") is None


def test_last_seen_returns_the_sighting() -> None:
    class B:
        def last_seen(self, name: str):
            return (1042, 5, 6, 3.0)

    assert follow._last_seen(as_bot(B()), "Friend") == (1042, 5, 6, 3.0)


# ---------------------------------------------------------------- _walk_up
def test_walk_up_stops_when_the_player_vanishes() -> None:
    class B:
        def __init__(self) -> None:
            self.near_said: list[bool] = []         # what the walk's stop-early check answered

        def snap(self) -> dict[str, Any]:
            return {"entities": []}

        def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                    near: Callable[[dict[str, Any]], bool] | None = None) -> str:
            assert near is not None
            self.near_said.append(near({"x": 0, "y": 0}))
            return "arrived"

    b = B()
    follow._walk_up(as_bot(b), "Friend", (0, 0))
    assert b.near_said == [False]


# ---------------------------------------------------------------- teammate_position
class TeamLink:
    def __init__(self, team: dict[str, Any], said: list[dict[str, str]] | tuple[()]=()) -> None:
        self.team, self.said = team, list(said)

    def json(self, line: str) -> dict[str, Any]:
        if line == "team":
            return self.team
        if line.startswith("said"):
            return {"now": self.team.get("now", 0), "said": self.said}
        return {}


class TeamBot:
    def __init__(self, team: dict[str, Any], said: list[dict[str, str]] | tuple[()]=()) -> None:
        self.agent_version = 27
        self.team_ask_at = 0.0               # follow.py's default: ask at once
        self.in_fight = False
        self.phrases = {3: "/6"}
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.a = TeamLink(team, said)

    def key(self, k: str) -> None:
        self.keys.append(k)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


def test_teammate_position_finds_the_member(monkeypatch: pytest.MonkeyPatch, fclock: Clock) -> None:
    monkeypatch.setattr(gamekeys, "phrase_key_list", stand_in(gamekeys.phrase_key_list, lambda: ["shift+%d" % i for i in range(1, 13)]))
    team = {"now": 500, "members": [{"name": "Friend", "age_ms": 100, "x": 5, "y": 6}]}
    b = TeamBot(team)
    assert follow.teammate_position(as_bot(b), "friend") == (5, 6)
    assert b.keys == ["shift+3"] and b.sleeps == [0.35]


def test_teammate_position_without_the_phrase() -> None:
    b = TeamBot({"now": 0, "members": [{"name": "Friend", "age_ms": 1, "x": 5, "y": 6}]})
    b.phrases = {}
    assert follow.teammate_position(as_bot(b), "Friend") is None
    assert b.keys == []


def test_teammate_position_reports_no_team(fclock: Clock) -> None:
    b = TeamBot({"now": 0, "members": [{"name": "Other", "age_ms": 1, "x": 1, "y": 1}]},
                said=[{"text": "You are in no team."}])
    assert follow.teammate_position(as_bot(b), "Friend") is None
    assert b.team_ask_at == 1000.0 + 60


def test_teammate_position_exhausts_its_presses(monkeypatch: pytest.MonkeyPatch, fclock: Clock) -> None:
    monkeypatch.setattr(gamekeys, "phrase_key_list", stand_in(gamekeys.phrase_key_list, lambda: ["shift+%d" % i for i in range(1, 13)]))
    b = TeamBot({"now": 0, "members": []})
    assert follow.teammate_position(as_bot(b), "Friend") is None
    assert len(b.keys) == 5 and b.keys == ["shift+3"] * 5


def test_teammate_position_ignores_stale_members(fclock: Clock) -> None:
    b = TeamBot({"now": 0, "members": [{"name": "Friend", "age_ms": 99999, "x": 5, "y": 6}]})
    assert follow.teammate_position(as_bot(b), "Friend") is None


def test_teammate_position_old_agent(fclock: Clock) -> None:
    b = TeamBot({"now": 0, "members": [{"name": "Friend", "age_ms": 1, "x": 5, "y": 6}]})
    b.agent_version = 26
    assert follow.teammate_position(as_bot(b), "Friend") is None
    assert b.keys == []


def test_teammate_position_in_fight(fclock: Clock) -> None:
    b = TeamBot({"now": 0, "members": [{"name": "Friend", "age_ms": 1, "x": 5, "y": 6}]})
    b.in_fight = True
    assert follow.teammate_position(as_bot(b), "Friend") is None


def test_teammate_position_waits_between_asks(fclock: Clock) -> None:
    b = TeamBot({"now": 0, "members": [{"name": "Friend", "age_ms": 1, "x": 5, "y": 6}]})
    b.team_ask_at = 1000.0 + 10
    assert follow.teammate_position(as_bot(b), "Friend") is None
    assert b.keys == []


# ---------------------------------------------------------------- teleport
class TeleLink:
    def __init__(self, bot: TeleBot) -> None:
        self.bot = bot

    def json(self, line: str) -> dict[str, Any]:
        if line.startswith("said"):
            return {"now": 0, "said": self.bot.said}
        return {}


class TeleBot:
    def __init__(self, x: float=0.0, y: float=0.0) -> None:
        self._teleport: dict[str, Any] = {"off": None, "next": 0.0, "not_leader": set()}   # follow.py's own start
        self.agent_version = 25
        self.in_fight = False
        self.p = {"teleport_key": "shift+p"}
        self._pos = {"x": x, "y": y}
        self.entities: list[dict[str, Any]] = []
        self.said: list[dict[str, Any]] = []
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.logs: list[str] = []
        self.on_key: Callable[[], object] | None = None
        self.a = TeleLink(self)

    def key(self, k: str) -> None:
        self.keys.append(k)
        if self.on_key:
            self.on_key()

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def me(self) -> dict[str, Any]:
        return dict(self._pos)

    def snap(self) -> dict[str, Any]:
        return {"self": dict(self._pos), "map": {"id": 1041}, "entities": self.entities}


def test_teleport_when_we_lead(fclock: Clock) -> None:
    b = TeleBot()
    b.said = [{"text": "The leader cannot teleport."}]
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b._teleport["off"] == "leader"


def test_teleport_without_a_team(fclock: Clock) -> None:
    b = TeleBot()
    b.said = [{"text": "You are in no team."}]
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b._teleport["off"] == "no team"


@pytest.mark.parametrize("text", ["wait 30 seconds teleport cooldown",
                                  "wait 30 seconds; teleport cooldown"])
def test_teleport_cooldown_is_obeyed(text: str, fclock: Clock) -> None:
    b = TeleBot()
    b.said = [{"text": text}]
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b._teleport["next"] == 1000.0 + 30 + 0.5


def test_teleport_marks_a_far_absent_leader(fclock: Clock) -> None:
    b = TeleBot()
    b.said = [{"text": "You are not on this map."}]
    them = {"x": 1000.0, "y": 1000.0}
    assert follow.teleport(as_bot(b), "Friend", them) is False
    assert "friend" in b._teleport["not_leader"]
    assert b._teleport["next"] == 1010.0


def test_teleport_within_20_yards_does_not_mark_them(fclock: Clock) -> None:
    b = TeleBot()
    b.said = [{"text": "You must be within 20 yards."}]
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b._teleport["not_leader"] == set()
    assert b._teleport["next"] == 1010.0


def test_teleport_that_did_not_move_us(fclock: Clock) -> None:
    b = TeleBot()
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b._teleport["next"] == 1010.0
    assert len(b.sleeps) == 8


def test_teleport_reaches_the_leader(fclock: Clock) -> None:
    b = TeleBot()
    b.on_key = lambda: b._pos.update(x=10.0)
    b.entities = [other(x=12.0, y=0.0)]
    assert follow.teleport(as_bot(b), "Friend") is True
    assert b.keys == ["shift+p"]
    assert any("team leader" in line for line in b.logs)


def test_teleport_moved_us_to_someone_else(fclock: Clock) -> None:
    b = TeleBot()
    b.on_key = lambda: b._pos.update(x=10.0)
    assert follow.teleport(as_bot(b), "Friend") is False
    assert "friend" in b._teleport["not_leader"]
    assert any("isn't the leader" in line for line in b.logs)


def test_teleport_turned_off(fclock: Clock) -> None:
    b = TeleBot()
    b._teleport = {"off": "leader", "next": 0.0, "not_leader": set()}
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b.keys == []


def test_teleport_before_its_next_try(fclock: Clock) -> None:
    b = TeleBot()
    b._teleport = {"off": None, "next": 2000.0, "not_leader": set()}
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b.keys == []


def test_teleport_skips_a_known_non_leader(fclock: Clock) -> None:
    b = TeleBot()
    b._teleport = {"off": None, "next": 0.0, "not_leader": {"friend"}}
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b.keys == []


def test_teleport_while_fighting(fclock: Clock) -> None:
    b = TeleBot()
    b.in_fight = True
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b.keys == []


def test_teleport_old_agent(fclock: Clock) -> None:
    b = TeleBot()
    b.agent_version = 24
    assert follow.teleport(as_bot(b), "Friend") is False
    assert b.keys == []


# ---------------------------------------------------------------- help_target
class HelpBot:
    def __init__(self, targets: list[dict[str, Any]] | tuple[()]=(), attackers: list[int] | tuple[()]=()) -> None:
        self._targets = list(targets)
        self._attackers = set(attackers)

    def targets(self, snap: dict[str, Any]) -> list[dict[str, Any]]:
        return self._targets

    def attackers(self) -> set[int]:
        return self._attackers


def test_help_target_nothing_to_help() -> None:
    p = other(x=0.0, y=0.0)
    assert follow.help_target(as_bot(HelpBot()), world(), p) is None


def test_help_target_wounded_nearest_first() -> None:
    p = other(x=0.0, y=0.0)
    near = mob(x=2.0, y=0.0, id=-1)
    far = mob(x=5.0, y=0.0, id=-2)
    b = HelpBot(targets=[far, near])
    assert follow.help_target(as_bot(b), world(), p) is near


def test_help_target_ignores_a_healthy_bystander() -> None:
    p = other(x=0.0, y=0.0)
    healthy = mob(x=1.0, y=0.0, id=-1, hp=10, maxhp=10)
    assert follow.help_target(as_bot(HelpBot(targets=[healthy])), world(), p) is None


def test_help_target_ignores_a_monster_far_from_the_player() -> None:
    p = other(x=0.0, y=0.0)
    far = mob(x=50.0, y=0.0, id=-1)
    assert follow.help_target(as_bot(HelpBot(targets=[far])), world(), p) is None


def test_help_target_helps_what_attacks_us() -> None:
    p = other(x=0.0, y=0.0)
    healthy = mob(x=1.0, y=0.0, id=-7, hp=10, maxhp=10)
    b = HelpBot(targets=[healthy], attackers=[-7])
    assert follow.help_target(as_bot(b), world(), p) is healthy


# ---------------------------------------------------------------- _exit_taken
def test_exit_taken_returns_the_nearest(monkeypatch: pytest.MonkeyPatch) -> None:
    exits = [{"dest": 1042, "stand": [20, 20], "rect": [0, 0, 9, 9]},
             {"dest": 1043, "stand": [5, 1], "rect": [0, 0, 9, 9]}]
    monkeypatch.setattr(follow.atlas, "maps", stand_in(follow.atlas.maps, lambda: {1041: {"exits": exits}}))
    taken = follow._exit_taken(1041, (4, 0))
    assert taken is not None and taken["dest"] == 1043


def test_exit_taken_skips_a_destination_less_exit(monkeypatch: pytest.MonkeyPatch) -> None:
    exits = [{"dest": None, "stand": [4, 0], "rect": [0, 0, 9, 9]}]
    monkeypatch.setattr(follow.atlas, "maps", stand_in(follow.atlas.maps, lambda: {1041: {"exits": exits}}))
    assert follow._exit_taken(1041, (4, 0)) is None


def test_exit_taken_skips_an_exit_without_a_stand(monkeypatch: pytest.MonkeyPatch) -> None:
    exits = [{"dest": 1042, "stand": None, "rect": [0, 0, 9, 9]}]
    monkeypatch.setattr(follow.atlas, "maps", stand_in(follow.atlas.maps, lambda: {1041: {"exits": exits}}))
    assert follow._exit_taken(1041, (4, 0)) is None


def test_exit_taken_none_close_by(monkeypatch: pytest.MonkeyPatch) -> None:
    exits = [{"dest": 1042, "stand": [80, 80], "rect": [0, 0, 9, 9]}]
    monkeypatch.setattr(follow.atlas, "maps", stand_in(follow.atlas.maps, lambda: {1041: {"exits": exits}}))
    assert follow._exit_taken(1041, (4, 0)) is None


def test_exit_taken_no_exits_at_all(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow.atlas, "maps", stand_in(follow.atlas.maps, lambda: {1041: {}}))
    assert follow._exit_taken(1041, (4, 0)) is None


# ---------------------------------------------------------------- the follow() wrapper
def test_follow_sets_and_clears_the_followed_name(monkeypatch: pytest.MonkeyPatch) -> None:
    b = FollowBot([])
    seen: dict[str, str | None] = {}

    def fake(bot: FollowBot, name: str) -> None:
        seen["during"] = bot.following

    monkeypatch.setattr(follow, "_follow", fake)
    follow.follow(as_bot(b), "Friend")
    assert seen["during"] == "Friend" and b.following is None
    assert any("following" in line for line in b.logs)


def test_follow_clears_the_name_on_an_error(monkeypatch: pytest.MonkeyPatch) -> None:
    b = FollowBot([])

    def boom(bot: FollowBot, name: str) -> None:
        raise Stopped("stop")

    monkeypatch.setattr(follow, "_follow", boom)
    with pytest.raises(Stopped):
        follow.follow(as_bot(b), "Friend")
    assert b.following is None


# ---------------------------------------------------------------- come_to's extra branches
class ComeBot:
    def __init__(self, x: float = 0.0, y: float = 0.0, map_id: int = 1041, entities: Sequence[dict[str, Any]] = (),
                 locate: Callable[[str], Where | None] | None = None, last_seen: Callable[[str], Sighting | None] | None = None) -> None:
        self._pos = {"x": x, "y": y}
        self._map = map_id
        self.entities = list(entities)
        self.logs: list[str] = []
        self.walks: list[tuple[float, float]] = []
        self.travels: list[tuple[int, tuple[float, float] | None]] = []
        self.locate: Callable[[str], Where | None] = locate or (lambda name: None)
        self.last_seen: Callable[[str], Sighting | None] = last_seen or (lambda name: None)
        self.on_walk: Callable[[], object] | None = None
        self.a = Link()
        self.agent_version = 0
        self.in_fight = False
        self.phrases: dict[int, str] = {}
        self.p: dict[str, Any] = {}

    def check(self) -> None:
        pass

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def map_id(self) -> int:
        return self._map

    def me(self) -> dict[str, Any]:
        return dict(self._pos)

    def snap(self) -> dict[str, Any]:
        return {"self": dict(self._pos), "map": {"id": self._map}, "entities": self.entities}

    def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        self.walks.append(cell)
        if self.on_walk:
            self.on_walk()
        return "arrived"

    def travel(self, mid: int, cell: tuple[float, float] | None=None) -> None:
        self.travels.append((mid, cell))
        self._map = mid

    def sleep(self, seconds: float) -> None:
        pass


def test_come_to_a_player_who_vanishes_while_walking() -> None:
    b = ComeBot(entities=[other(x=50.0, y=0.0)])
    b.on_walk = lambda: b.entities.clear()
    assert follow.come_to(as_bot(b), "Friend").startswith("I can't see you")
    assert len(b.walks) == 1


def test_come_locate_on_another_map_travels() -> None:
    calls = {"n": 0}

    def locate(name: str):
        calls["n"] += 1
        return (1042, 5, 6) if calls["n"] == 1 else None

    b = ComeBot(locate=locate)
    assert follow.come_to(as_bot(b), "Friend").startswith("I can't see you")
    assert b.travels == [(1042, (5, 6))]
    assert any("going to" in line for line in b.logs)


def test_come_locate_on_our_map_walks_there() -> None:
    b = ComeBot(locate=lambda name: (1041, 5, 6))
    follow.come_to(as_bot(b), "Friend")
    assert b.walks == [(5, 6)] * 4 and b.travels == []


def test_come_uses_the_teammate_position(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ComeBot()
    monkeypatch.setattr(follow, "teammate_position", stand_in(follow.teammate_position, lambda bot, name: (7, 8)))
    follow.come_to(as_bot(b), "Friend")
    assert b.walks == [(7, 8)] * 4
    assert any("is on this map" in line for line in b.logs)


def test_come_teleports_to_a_far_teammate(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ComeBot()
    monkeypatch.setattr(follow, "teammate_position", stand_in(follow.teammate_position, lambda bot, name: (100, 100)))
    monkeypatch.setattr(follow, "teleport", stand_in(follow.teleport, returning(True)))
    follow.come_to(as_bot(b), "Friend")
    assert b.walks == []
    assert not any("is on this map" in line for line in b.logs)


def test_come_travels_to_the_given_map() -> None:
    b = ComeBot()
    result = follow.come_to(as_bot(b), "Friend", 1042, None)
    assert b.travels == [(1042, None)]
    assert "but can't see you" in result


def test_come_ends_on_the_given_map_we_are_already_on() -> None:
    b = ComeBot(map_id=1041)
    assert "I'm on" in follow.come_to(as_bot(b), "Friend", 1041, None)


# ---------------------------------------------------------------- the _follow loop
def test_follow_helps_with_the_players_fight() -> None:
    m = mob(x=3.0, y=0.0)
    b = FollowBot([world(0.0, 0.0, entities=[other(x=2.0, y=0.0)])], targets=lambda s: [m])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.fights == [m]


def test_follow_teleports_when_the_player_is_far(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, dict[str, Any] | None]] = []
    monkeypatch.setattr(follow, "teleport", stand_in(follow.teleport, lambda bot, name, them=None: calls.append((name, them)) or True))
    p = other(x=100.0, y=0.0)
    b = FollowBot([world(0.0, 0.0, entities=[p])])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert calls == [("Friend", p)]


def test_follow_walks_up_when_the_player_moves_off() -> None:
    b = FollowBot([world(0.0, 0.0, entities=[other(x=10.0, y=0.0)])])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == [(10, 1)]


def test_follow_sleeps_when_beside_the_player() -> None:
    b = FollowBot([world(0.0, 0.0, entities=[other(x=2.0, y=0.0)])])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.sleeps == [0.3]


def test_follow_locates_the_player_on_another_map() -> None:
    b = FollowBot([world(0.0, 0.0)], locate=lambda name: (1042, 5, 6))
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.travels == [(1042, (5, 6))]
    assert any("going too" in line for line in b.logs)


def test_follow_locates_the_player_on_our_map() -> None:
    b = FollowBot([world(0.0, 0.0)], locate=lambda name: (1041, 5, 6))
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == [(5, 6)]


def test_follow_locates_a_far_player_and_teleports(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow, "teleport", stand_in(follow.teleport, returning(True)))
    b = FollowBot([world(0.0, 0.0)], locate=lambda name: (1041, 100, 100))
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == []


def test_follow_walks_to_a_teammate_position(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow, "teammate_position", stand_in(follow.teammate_position, lambda bot, name: (7, 8)))
    b = FollowBot([world(0.0, 0.0)])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == [(7, 8)]


def test_follow_teleports_to_a_far_teammate(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow, "teammate_position", stand_in(follow.teammate_position, lambda bot, name: (100, 100)))
    monkeypatch.setattr(follow, "teleport", stand_in(follow.teleport, returning(True)))
    b = FollowBot([world(0.0, 0.0)])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == []


def test_follow_searches_where_we_last_saw_them() -> None:
    b = FollowBot([world(0.0, 0.0)], last_seen=lambda name: (1041, 3, 4, 5.0))
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == [(3, 4)]
    assert any("was last seen" in line for line in b.logs)


def test_follow_takes_the_exit_the_player_took(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow.atlas, "maps",
                        stand_in(follow.atlas.maps, lambda: {1041: {"exits": [{"dest": 1042, "stand": [5, 1], "rect": [0, 0, 10, 10]}]}}))
    b = FollowBot([world(0.0, 0.0, entities=[other(x=2.0, y=0.0)]), world(0.0, 0.0)])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.crosses and b.crosses[0]["dest"] == 1042
    assert any("took the exit" in line for line in b.logs)


def test_follow_crossing_an_exit_without_a_rect_raises(monkeypatch: pytest.MonkeyPatch) -> None:
    # _exit_taken only screens exits on dest and stand, but _follow then reads e["rect"] directly,
    # so an exit that carries a stand and a dest but no rect crashes rather than being ignored.
    monkeypatch.setattr(follow.atlas, "maps",
                        stand_in(follow.atlas.maps, lambda: {1041: {"exits": [{"dest": 1042, "stand": [5, 1]}]}}))
    b = FollowBot([world(0.0, 0.0, entities=[other(x=2.0, y=0.0)]), world(0.0, 0.0)])
    with pytest.raises(KeyError):
        follow._follow(as_bot(b), "Friend")


def test_follow_walks_back_to_where_they_were(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(follow.atlas, "maps", stand_in(follow.atlas.maps, lambda: {1041: {}}))
    b = FollowBot([world(0.0, 0.0, entities=[other(x=2.0, y=0.0)]), world(10.0, 0.0)])
    with pytest.raises(Stop):
        follow._follow(as_bot(b), "Friend")
    assert b.walks == [(2, 0)]


def test_follow_gives_up_the_lost_player(fclock: Clock) -> None:
    b = FollowBot(lambda: world(0.0, 0.0))
    b.on_sleep = lambda seconds: fclock.advance(100)
    with pytest.raises(Stopped):
        follow._follow(as_bot(b), "Friend")
    assert b.sleeps == [0.5]


