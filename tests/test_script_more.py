"""Scripts can do more of what the bot already knows how to: write in a chat room (chat), use an item
from the bag (use), wait for a condition (wait_until), and read a field boss in view (boss,
boss_dist), a player's distance (near), whether we are in a dungeon (in_dungeon), how full the bag
is (bag_pct) and the run's deaths (deaths)."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import Clock, as_bot

from test_script import FakeBot, FakeLink
from unclefarmer.engine import script
from unclefarmer.engine.bot import Stopped
from unclefarmer.link import AgentError


class ChatLink(FakeLink):
    def __init__(self, answers: dict[int, str] | None = None) -> None:
        super().__init__()
        self.sent: list[str] = []
        self.answers = answers or {}

    def send(self, line: str) -> str:
        self.sent.append(line)
        return self.answers.get(int(line.split()[1]), "sent")


class Bot(FakeBot):
    def __init__(self, entities: list[dict[str, Any]] | None = None, side: int | None = 7) -> None:
        super().__init__()
        self.a = ChatLink()
        self.clock = Clock()
        self.entities = list(entities or [])
        self.mev: dict[str, Any] = dict(self.mev, side=side)
        self.items: dict[str, bool] = {"Big Red Potion": True}
        self.uses: list[str] = []
        self.deaths = 0
        self.in_instance = False
        self.on_sleep: Any = None

    def snap(self) -> dict[str, Any]:
        return {"self": self.mev, "entities": list(self.entities)}

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.clock.advance(seconds)
        if self.on_sleep:
            self.on_sleep()

    def use_item(self, name: str) -> bool:
        self.uses.append(name)
        return self.items.get(name, False)


def run(b: Bot, text: str) -> script.Runner:
    r = script.Runner(as_bot(b), text)
    r.run()
    return r


def lines(b: Bot) -> list[str]:
    return [line for line, _speak in b.logs]


def worm(x: float = 11.0, y: float = 2.0) -> dict[str, Any]:
    return {"id": -1, "kind": "monster", "side": -2, "name": "Death Worm", "x": x, "y": y, "hp": 9, "maxhp": 9}


# ------------------------------------------------------------------ chat
def test_chat_writes_on_the_room_with_its_values() -> None:
    b = Bot()
    run(b, "chat team HP {hp_pct}% on {map_name}\nchat global hello")
    assert b.a.sent == ["say 4 HP 50.0% on Wolf Prairie", "say 0 hello"]
    assert "Team: HP 50.0% on Wolf Prairie" in lines(b)


def test_chat_said_in_the_log_when_the_game_refuses_and_the_script_goes_on() -> None:
    b = Bot()
    b.a.answers = {5: "error: no international room joined yet"}
    run(b, "chat international hi\nset after = 1")
    assert any("not sent to International: Join an international room in the game first." in line
               for line in lines(b))
    def broken(line: str) -> str:
        raise AgentError("no game")
    b.a.send = broken                                       # type: ignore[method-assign]
    run(b, "chat team hi")
    assert any("not sent to Team: The game didn't take it: no game" in line for line in lines(b))


def test_a_character_in_no_gang_sends_nothing_to_the_gang() -> None:
    b = Bot(side=0)
    run(b, "chat gang hi")
    assert b.a.sent == [] and any("in no gang" in line for line in lines(b))


def test_chat_sends_one_line_a_room_every_few_seconds_at_most() -> None:
    """A loop with no wait would send many lines a second, and the game may mute a character for that."""
    b = Bot()
    start = b.clock.time()
    run(b, "repeat 3\n  chat team hi\nend\nchat gang hello")
    assert b.a.sent == ["say 4 hi"] * 3 + ["say 3 hello"]
    # (repeat's own short pause a turn on top) - and the gang's line didn't wait
    assert b.clock.time() - start == pytest.approx(2 * script.CHAT_GAP, abs=0.1)


def test_a_long_chat_line_is_cut() -> None:
    b = Bot()
    run(b, "chat team " + "x" * 500)
    assert len(b.a.sent[0]) == len("say 4 ") + script.CHAT_MOST
    assert any("cut to 300 characters" in line for line in lines(b))


@pytest.mark.parametrize("line", ["chat", "chat team", "chat shouting hello"])
def test_a_wrong_chat_line_is_said_when_the_script_is_read(line: str) -> None:
    with pytest.raises(script.ScriptError):
        script.parse(line)


# ------------------------------------------------------------------ use
def test_use_an_item_and_say_whether_it_went() -> None:
    b = Bot()
    r = run(b, 'use "Big Red Potion"')
    assert b.uses == ["Big Red Potion"] and r.vars["used"] is True
    r = run(b, "use Teleport Scroll\nif not used\n  say none left\nend")
    assert r.vars["used"] is False and "none left" in lines(b)
    assert any("couldn't use Teleport Scroll" in line for line in lines(b))
    with pytest.raises(script.ScriptError):
        script.parse("use")


# ------------------------------------------------------------------ wait_until
def test_wait_until_returns_when_the_condition_comes_true() -> None:
    b = Bot()

    def heal() -> None:
        if b.clock.time() - 1000.0 >= 3:
            b.mev["hp"] = 100
    b.on_sleep = heal
    r = run(b, "wait_until hp_pct >= 90 timeout=30")
    assert r.vars["wait_met"] is True and 3 <= b.clock.time() - 1000.0 < 4


def test_wait_until_gives_up_at_its_timeout() -> None:
    b = Bot()
    r = run(b, "wait_until hp_pct >= 90 timeout=5\nif not wait_met\n  say still low\nend")
    assert r.vars["wait_met"] is False and b.clock.time() - 1000.0 == pytest.approx(5)
    assert "still low" in lines(b)


def test_wait_until_waits_a_minute_unless_told() -> None:
    nodes = script.parse("wait_until near(\"Bob\") >= 0")[0]
    assert nodes[0].opts["secs"] == 60.0


def test_stop_ends_a_wait_until() -> None:
    b = Bot()
    calls = {"n": 0}

    def check() -> None:
        calls["n"] += 1
        if calls["n"] == 3:
            raise Stopped("stopped")
    b.check = check                                         # type: ignore[method-assign]
    with pytest.raises(Stopped):
        run(b, "wait_until hp_pct >= 90 timeout=600")


@pytest.mark.parametrize("line", ["wait_until", "wait_until hp > 1 timeout=0", "wait_until hp > 1 timeout=0.5",
                                  "wait_until hp > 1 timeout=99999999",
                                  "wait_until hp > 1 timeout=soon"])
def test_a_wrong_wait_until_is_said_when_the_script_is_read(line: str) -> None:
    with pytest.raises(script.ScriptError):
        script.parse(line)


# ------------------------------------------------------------------ what a script can read
def test_boss_names_the_nearest_field_boss_in_view() -> None:
    b = Bot([worm(x=11.0, y=2.0), dict(worm(x=4.0, y=2.0), id=-2)])
    run(b, "say {boss} at {boss_dist}\nif boss != \"\"\n  set seen = 1\nend")
    assert "Death Worm at 3.0" in lines(b)
    b = Bot()
    r = run(b, "set seen = 0\nif boss == \"\"\n  set seen = boss_dist\nend")
    assert r.vars["seen"] == -1


def test_boss_and_boss_dist_come_from_one_read() -> None:
    """In "boss != "" and boss_dist < 20" the boss can't vanish between the two."""
    b = Bot([worm(x=4.0, y=2.0)])
    reads = {"n": 0}
    real = b.snap

    def counted() -> dict[str, Any]:
        reads["n"] += 1
        snap = real()
        if reads["n"] > 1:
            snap["entities"] = []                 # gone by the second read
        return snap
    b.snap = counted                              # type: ignore[method-assign]
    r = run(b, 'set close = 0\nif boss != "" and boss_dist < 20\n  set close = 1\nend')
    assert r.vars["close"] == 1


def test_near_is_a_players_distance_or_minus_one() -> None:
    bob = {"id": 77, "kind": "player", "name": "Bob", "x": 4.0, "y": 6.0}
    b = Bot([bob])
    r = run(b, 'set d = near("bob")\nset gone = near("Alice")')
    assert r.vars["d"] == 5.0 and r.vars["gone"] == -1


def test_in_dungeon_bag_pct_and_deaths() -> None:
    b = Bot()
    b.mev["sheet"] = dict(b.mev["sheet"], load=150, maxload=200)
    b.deaths = 2
    r = run(b, "set a = in_dungeon\nset c = bag_pct\nset d = deaths")
    assert (r.vars["a"], r.vars["c"], r.vars["d"]) == (False, 75.0, 2)
    b.in_instance = True
    del b.mev["sheet"]["load"]
    r = run(b, "set a = in_dungeon\nset c = bag_pct")
    assert (r.vars["a"], r.vars["c"]) == (True, -1)


def test_the_new_values_are_the_games_and_cant_be_set() -> None:
    for name in ("boss", "boss_dist", "in_dungeon", "bag_pct", "deaths", "wait_met", "used"):
        with pytest.raises(script.ScriptError):
            script.parse("set %s = 1" % name)
