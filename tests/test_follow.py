# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Coming to a player, following one, and the team menu - against a pretend map and players."""

from __future__ import annotations

import queue
import time

import pytest

from unclefarmer import atlas, messages
from unclefarmer.engine import follow, script, team
from unclefarmer.engine.bot import Bot, Stopped
from collections.abc import Callable
from conftest import as_bot, as_kind, stand_in
from unclefarmer.engine.botparts.parts import Sighting, Where
from typing import Any, TypeVar, TypeVarTuple

from unclefarmer.engine.runner import Run

T = TypeVar("T")
Ts = TypeVarTuple("Ts")


class FlatMap:
    def nearest_walkable(self, x: int, y: int, r: int) -> tuple[int, int]:
        return (x, y + 1)


@pytest.fixture(autouse=True)
def flat_maps(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: FlatMap()))
    monkeypatch.setattr(messages, "config", stand_in(messages.config, lambda: dict(messages.DEFAULTS, pm_trusted=["Friend"])))


class WalkingBot:
    """A bot on a flat map: walks one tile a step, sees players within 30 tiles."""

    def __init__(self, world: dict[str, Any]) -> None:
        self.w = world
        self.logs: list[str] = []
        self.walks: list[tuple[float, float]] = []
        self.travels: list[tuple[int, tuple[float, float] | None]] = []
        self.locate: Callable[[str], Where | None] = lambda name: None
        self.last_seen: Callable[[str], Sighting | None] = lambda name: None

    def check(self) -> None:
        pass

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def map_id(self) -> int:
        return self.w["map"]

    def me(self) -> dict[str, Any]:
        return dict(self.w["me"])

    def snap(self) -> dict[str, Any]:
        me = self.w["me"]
        seen = [dict(e) for e in self.w.get("ents", []) if abs(e["x"] - me["x"]) < 30 and abs(e["y"] - me["y"]) < 30]
        return {"self": self.me(), "map": {"id": self.w["map"]}, "entities": seen}

    def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        self.walks.append(cell)
        for _ in range(400):
            m = self.w["me"]
            dx, dy = cell[0] - m["x"], cell[1] - m["y"]
            if abs(dx) < 0.5 and abs(dy) < 0.5:
                return "arrived"
            m["x"] += max(-1, min(1, dx))
            m["y"] += max(-1, min(1, dy))
            self.w.get("tick", lambda: None)()
            if near and near(dict(m)):
                return "near"
        return "arrived"

    def travel(self, mid: int, cell: tuple[float, float] | None=None) -> None:
        self.travels.append((mid, cell))
        self.w["map"] = mid
        if cell:
            self.w["me"].update(x=cell[0] + 5, y=cell[1])

    def sleep(self, seconds: float) -> None:
        pass


def friend(x: float, y: float) -> dict[str, Any]:
    return {"kind": "player", "name": "Friend", "x": x, "y": y, "id": 9}


# ---------------------------------------------------------------- where the player said they are
@pytest.mark.parametrize("text, want", [
    ("central plains", (1041, None)),
    ("1041", (1041, None)),
    ("north;woods;31;8;Underground of Giant rock City", (1042, (31, 8))),
    ("you are on central plains 12 40", (1041, None)),
    ("xyz;abc;10;20;qqq", (None, (10, 20))),
    ("", (None, None)),
])
def test_parse_location(text: str, want: tuple[int, None] | tuple[None, None] | tuple[int, tuple[int, int]] | tuple[None, tuple[int, int]]) -> None:
    assert follow.parse_location(text) == want


def test_parse_location_with_empty_parts() -> None:
    assert follow.parse_location(" ; ;55;60;Wolf Prairie")[1] == (55, 60)


def test_script_come_keeps_map_and_cell() -> None:
    assert script.check('come "Friend" 1042 at 31,8\n') is None
    opts = script.parse('come "Friend" 1042 at 31,8\n')[0][0].opts
    assert opts.get("map") == 1042 and opts.get("cell") == (31, 8)


# ---------------------------------------------------------------- come
def test_come_walks_to_the_last_sighting() -> None:
    w = {"map": 1041, "me": {"x": 196.0, "y": 199.0}, "ents": [friend(150.0, 210.0)]}
    b = WalkingBot(w)
    b.last_seen = lambda name: (1041, 152.0, 205.0, 70.0)
    assert follow.come_to(as_bot(b), "Friend") == "Here, next to you."
    assert any("last seen" in line for line in b.logs)
    assert follow._dist(b.me(), friend(150.0, 210.0)) <= follow.NEAR


def test_come_to_a_stale_sighting_looks_once() -> None:
    b = WalkingBot({"map": 1041, "me": {"x": 196.0, "y": 199.0}, "ents": []})
    b.last_seen = lambda name: (1041, 152.0, 205.0, 70.0)
    assert follow.come_to(as_bot(b), "Friend").startswith("I went to where I last saw you")
    assert len(b.walks) == 1


def test_come_with_nothing_known() -> None:
    b = WalkingBot({"map": 1041, "me": {"x": 1.0, "y": 1.0}, "ents": []})
    assert follow.come_to(as_bot(b), "Friend").startswith("I can't see you")


def test_come_travels_to_the_sightings_map() -> None:
    b = WalkingBot({"map": 1041, "me": {"x": 1.0, "y": 1.0}, "ents": []})
    b.last_seen = lambda name: (1042, 30.0, 40.0, 20.0)
    follow.come_to(as_bot(b), "Friend")
    assert b.travels and b.travels[0][0] == 1042


def test_come_to_the_pasted_spot() -> None:
    w = {"map": 1042, "me": {"x": 100.0, "y": 100.0}, "ents": [friend(31.0, 8.0)]}
    b = WalkingBot(w)
    assert follow.come_to(as_bot(b), "Friend", 1042, (31, 8)) == "Here, next to you."
    assert any("said they are" in line for line in b.logs)
    w["ents"], w["me"] = [friend(500.0, 8.0)], {"x": 100.0, "y": 100.0}
    assert "Press E again" in follow.come_to(as_bot(WalkingBot(w)), "Friend", 1042, (31, 8))


def test_walk_up_aims_again_when_the_player_moves_on() -> None:
    mover = friend(20.0, 0.0)
    w = {"map": 1, "me": {"x": 0.0, "y": 0.0}, "ents": [mover], "tick": lambda: mover.update(x=mover["x"] + 1.0)}
    assert follow._walk_up(as_bot(WalkingBot(w)), "Friend", (20, 0)) == "near"


# ---------------------------------------------------------------- MP with no character sheet
def test_mp_without_a_sheet() -> None:
    b = object.__new__(Bot)
    b.maxmp = b.mp_peak = 0
    b.mp_seen = b.mp_added = 0.0
    now = time.time()
    assert b.mp_now({"vitals": {"mp": 94619, "age_ms": 100}}, now) == (94619, 94619)
    assert b.mp_now({"vitals": {"mp": 40000, "age_ms": 100}}, now + 10) == (40000, 94619)
    sheet = {"maxmp": 120000, "mp": 40000, "age": 500}
    assert b.mp_now({"vitals": {"mp": 40000, "age_ms": 100}, "sheet": sheet}, now + 20) == (40000, 120000)


# ---------------------------------------------------------------- the messenger's come
class FakeHost:
    """The window's side of a Messenger: records what it is asked to run, starts nothing."""

    def __init__(self, run: FakeRun | None = None) -> None:
        self.run = run
        self.calls: list[tuple[object, ...]] = []

    def run_for(self, pid: int) -> Run | None:
        return None if self.run is None else as_kind(Run, self.run)

    def call(self, fn: Callable[[*Ts], T], *args: *Ts) -> T | str | None:
        self.calls.append(args)
        return None

    def start_bot(self, pid: int, plan: str | None) -> str | None:
        return None

    def stop_bot(self, pid: int) -> None:
        pass

    def log(self, line: str, speak: bool = False, who: str | None = None, private: bool = False) -> None:
        pass

    def character(self, pid: int) -> str | None:
        return "me"

    def errand_for(self, pid: int, text: str, title: str, tell: Callable[[str], None] | None = None,
                   follows: str | None = None) -> str | None:
        return None

    def follow_for(self, pid: int, leader: str, tell: Callable[[str], None] | None = None) -> str | None:
        return None

    def unfollow(self, pid: int) -> str | None:
        return None


class RunBot:
    """The part of a running Bot a command hands work to."""

    def __init__(self) -> None:
        self.requests: queue.Queue[Callable[[Bot], object]] = queue.Queue()


class FakeRun:
    def __init__(self, follows: str | None) -> None:
        self.follows = follows
        self.bot = RunBot()


def test_come_while_following_keeps_following(monkeypatch: pytest.MonkeyPatch) -> None:
    run = FakeRun("friend")
    m = messages.Messenger(1, FakeHost(run))
    assert m.cmd_come(5, "Friend", "") == "Coming."
    monkeypatch.setattr(follow, "come_to", stand_in(follow.come_to, lambda bot, name, map_id=None, cell=None: "Here, next to you."))
    run.bot.requests.get_nowait()(as_bot(run.bot))           # no Stopped: the follow goes on
    assert m.outbox[0][2] == "Here, next to you."


def test_come_during_a_farming_run_ends_it_there(monkeypatch: pytest.MonkeyPatch) -> None:
    run = FakeRun(None)
    messages.Messenger(2, FakeHost(run)).cmd_come(5, "Friend", "")
    monkeypatch.setattr(follow, "come_to", stand_in(follow.come_to, lambda bot, name, map_id=None, cell=None: "Here."))
    with pytest.raises(Stopped) as e:
        run.bot.requests.get_nowait()(as_bot(run.bot))
    assert e.value.stay


def test_come_when_idle_starts_an_errand_with_the_cell() -> None:
    h = FakeHost()
    m = messages.Messenger(1, h)
    assert m.cmd_come(5, "Friend", "north;woods;31;8;Underground of Giant rock City") == "Coming."
    assert h.calls[0][1] == 'come "Friend" 1042 at 31,8'
    assert "Press E" in (m.cmd_come(5, "Friend", "nonsense words here") or "")


def test_trusted_players_in_view_are_remembered() -> None:
    m = messages.Messenger(1, FakeHost())

    class Link:
        def send(self, line: str) -> str:
            return "ok"

        def close(self) -> None:
            pass

        def json(self, line: str) -> Any:
            return {"ok": True, "map": {"id": 1041}, "self": {"name": "me"},
                    "entities": [{"kind": "player", "name": "Friend", "x": 1.5, "y": 2.0},
                                 {"kind": "player", "name": "Stranger", "x": 3, "y": 4}]}
    m.link = Link()
    m.note_players()
    assert set(m.sightings) == {"friend"} and m.sightings["friend"][:3] == (1041, 1.5, 2.0)


# ---------------------------------------------------------------- leaving the team
class MenuBot:
    """The Action menu as the game shows it: the team's quit sits beside the team, the guild's
    beside the guild."""

    find_entry = Bot.find_entry
    entry_texts = staticmethod(Bot.entry_texts)

    def __init__(self) -> None:
        self.p: dict[str, Any] = {}
        self.keys: list[str] = []
        self.chosen: list[tuple[tuple[int, int], Any]] = []    # (row, col) chosen, and its options
        self.state = "closed"

    def close_dialogs(self) -> None:
        self.state = "closed"

    def key(self, k: str, ms: int = 60) -> None:
        self.keys.append(k)
        if k == "lctrl+enter":
            self.state = "action"

    def wait_dialog(self, seconds: float=3.0) -> dict[str, Any] | None:
        return {"open": 1, "top": self.top()} if self.state != "closed" else None

    def top(self) -> dict[str, Any] | None:
        if self.state == "action":
            return {"row": 0, "col": 0, "rows": [
                [{"options": ["Action menu; lag 224"]}],
                [{"options": ["my mission: 1"]}],
                [{"options": ["Harvest team"]}, {"f": "Do you want to quit the Harvest team?", "options": ["quit"]}],
                [{"options": ["Example gang"]}, {"f": "Do you want to quit the Example gang?", "options": ["quit"]}]]}
        if self.state == "confirm":
            return {"row": 0, "col": 0, "rows": [[{"options": ["yes"]}, {"options": ["no"]}]]}
        return None

    def choose(self, pattern: str) -> dict[str, Any] | None:
        top = self.top()
        if top is None:
            return None
        where = self.find_entry(top, pattern)
        if where is None:
            return None
        e = top["rows"][where[0]][where[1]]
        self.chosen.append((where, e.get("options")))
        self.state = "confirm" if e.get("options") == ["quit"] and self.state == "action" else "closed"
        return e

    def sleep(self, seconds: float) -> None:
        pass


def test_leave_picks_the_teams_quit_not_the_guilds() -> None:
    b = MenuBot()
    ok, _msg = team.leave(as_bot(b))
    assert ok and b.chosen[0][0] == (2, 1)
    assert b.chosen[1][1] == ["yes"]
