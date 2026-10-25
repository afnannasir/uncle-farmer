# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""Coming to a player, following one, and the team menu - against a pretend map and players."""

import queue
import time

import pytest

from unclefarmer import atlas, messages
from unclefarmer.engine import follow, script, team
from unclefarmer.engine.bot import Bot, Stopped


class FlatMap:
    def nearest_walkable(self, x, y, r):
        return (x, y + 1)


@pytest.fixture(autouse=True)
def flat_maps(monkeypatch):
    monkeypatch.setattr(atlas, "model", lambda mid: FlatMap())
    monkeypatch.setattr(messages, "config", lambda: dict(messages.DEFAULTS, pm_trusted=["Friend"]))


class WalkingBot:
    """A bot on a flat map: walks one tile a step, sees players within 30 tiles."""

    def __init__(self, world):
        self.w, self.logs, self.walks, self.travels = world, [], [], []
        self.locate = lambda name: None
        self.last_seen = lambda name: None

    def check(self):
        pass

    def log(self, line, speak=False):
        self.logs.append(line)

    def map_id(self):
        return self.w["map"]

    def me(self):
        return dict(self.w["me"])

    def snap(self):
        me = self.w["me"]
        seen = [dict(e) for e in self.w.get("ents", []) if abs(e["x"] - me["x"]) < 30 and abs(e["y"] - me["y"]) < 30]
        return {"self": self.me(), "map": {"id": self.w["map"]}, "entities": seen}

    def walk_to(self, cell, near=None, **kw):
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

    def travel(self, mid, cell=None):
        self.travels.append((mid, cell))
        self.w["map"] = mid
        if cell:
            self.w["me"].update(x=cell[0] + 5, y=cell[1])

    def sleep(self, seconds):
        pass


def friend(x, y):
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
def test_parse_location(text, want):
    assert follow.parse_location(text) == want


def test_parse_location_with_empty_parts():
    assert follow.parse_location(" ; ;55;60;Wolf Prairie")[1] == (55, 60)


def test_script_come_keeps_map_and_cell():
    assert script.check('come "Friend" 1042 at 31,8\n') is None
    opts = script.parse('come "Friend" 1042 at 31,8\n')[0][0].opts
    assert opts.get("map") == 1042 and opts.get("cell") == (31, 8)


# ---------------------------------------------------------------- come
def test_come_walks_to_the_last_sighting():
    w = {"map": 1041, "me": {"x": 196.0, "y": 199.0}, "ents": [friend(150.0, 210.0)]}
    b = WalkingBot(w)
    b.last_seen = lambda name: (1041, 152.0, 205.0, 70.0)
    assert follow.come_to(b, "Friend") == "Here, next to you."
    assert any("last seen" in line for line in b.logs)
    assert follow._dist(b.me(), friend(150.0, 210.0)) <= follow.NEAR


def test_come_to_a_stale_sighting_looks_once():
    b = WalkingBot({"map": 1041, "me": {"x": 196.0, "y": 199.0}, "ents": []})
    b.last_seen = lambda name: (1041, 152.0, 205.0, 70.0)
    assert follow.come_to(b, "Friend").startswith("I went to where I last saw you")
    assert len(b.walks) == 1


def test_come_with_nothing_known():
    b = WalkingBot({"map": 1041, "me": {"x": 1.0, "y": 1.0}, "ents": []})
    assert follow.come_to(b, "Friend").startswith("I can't see you")


def test_come_travels_to_the_sightings_map():
    b = WalkingBot({"map": 1041, "me": {"x": 1.0, "y": 1.0}, "ents": []})
    b.last_seen = lambda name: (1042, 30.0, 40.0, 20.0)
    follow.come_to(b, "Friend")
    assert b.travels and b.travels[0][0] == 1042


def test_come_to_the_pasted_spot():
    w = {"map": 1042, "me": {"x": 100.0, "y": 100.0}, "ents": [friend(31.0, 8.0)]}
    b = WalkingBot(w)
    assert follow.come_to(b, "Friend", 1042, (31, 8)) == "Here, next to you."
    assert any("said they are" in line for line in b.logs)
    w["ents"], w["me"] = [friend(500.0, 8.0)], {"x": 100.0, "y": 100.0}
    assert "Press E again" in follow.come_to(WalkingBot(w), "Friend", 1042, (31, 8))


def test_walk_up_aims_again_when_the_player_moves_on():
    mover = friend(20.0, 0.0)
    w = {"map": 1, "me": {"x": 0.0, "y": 0.0}, "ents": [mover], "tick": lambda: mover.update(x=mover["x"] + 1.0)}
    assert follow._walk_up(WalkingBot(w), "Friend", (20, 0)) == "near"


# ---------------------------------------------------------------- MP with no character sheet
def test_mp_without_a_sheet():
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
    def __init__(self, run=None):
        self.run, self.calls = run, []

    def run_for(self, pid):
        return self.run

    def call(self, fn, *args):
        self.calls.append(args)

    def errand_for(self, *args):
        pass

    def character(self, pid):
        return "me"

    def log(self, *args):
        pass


class Run:
    def __init__(self, follows):
        self.follows = follows
        self.bot = type("B", (), {"requests": queue.Queue()})()


def test_come_while_following_keeps_following(monkeypatch):
    run = Run("friend")
    m = messages.Messenger(1, FakeHost(run))
    assert m.cmd_come(5, "Friend", "") == "Coming."
    monkeypatch.setattr(follow, "come_to", lambda *a: "Here, next to you.")
    run.bot.requests.get_nowait()(run.bot)           # no Stopped: the follow goes on
    assert m.outbox[0][2] == "Here, next to you."


def test_come_during_a_farming_run_ends_it_there(monkeypatch):
    run = Run(None)
    messages.Messenger(2, FakeHost(run)).cmd_come(5, "Friend", "")
    monkeypatch.setattr(follow, "come_to", lambda *a: "Here.")
    with pytest.raises(Stopped) as e:
        run.bot.requests.get_nowait()(run.bot)
    assert e.value.stay


def test_come_when_idle_starts_an_errand_with_the_cell():
    h = FakeHost()
    m = messages.Messenger(1, h)
    assert m.cmd_come(5, "Friend", "north;woods;31;8;Underground of Giant rock City") == "Coming."
    assert h.calls[0][1] == 'come "Friend" 1042 at 31,8'
    assert "Press E" in (m.cmd_come(5, "Friend", "nonsense words here") or "")


def test_trusted_players_in_view_are_remembered():
    m = messages.Messenger(1, FakeHost())

    class Link:
        def json(self, line):
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

    def __init__(self):
        self.p, self.keys, self.chosen, self.state = {}, [], [], "closed"

    def close_dialogs(self):
        self.state = "closed"

    def key(self, k):
        self.keys.append(k)
        if k == "lctrl+enter":
            self.state = "action"

    def wait_dialog(self, seconds=3.0):
        return {"open": 1, "top": self.top()} if self.state != "closed" else None

    def top(self):
        if self.state == "action":
            return {"row": 0, "col": 0, "rows": [
                [{"options": ["Action menu; lag 224"]}],
                [{"options": ["my mission: 1"]}],
                [{"options": ["Harvest team"]}, {"f": "Do you want to quit the Harvest team?", "options": ["quit"]}],
                [{"options": ["Example gang"]}, {"f": "Do you want to quit the Example gang?", "options": ["quit"]}]]}
        if self.state == "confirm":
            return {"row": 0, "col": 0, "rows": [[{"options": ["yes"]}, {"options": ["no"]}]]}
        return None

    def choose(self, pattern):
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

    def sleep(self, seconds):
        pass


def test_leave_picks_the_teams_quit_not_the_guilds():
    b = MenuBot()
    ok, _msg = team.leave(b)
    assert ok and b.chosen[0][0] == (2, 1)
    assert b.chosen[1][1] == ["yes"]
