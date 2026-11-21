"""Taking requests to join our team (engine/teamlead.py), against a pretend game whose menus are
the ones read live on 2026-11-21: the Action menu's team row, "apply to join: N ", one row per
player with a selection flag in its cmd, then "agree"; "<name> joined the team" in the chat."""

from __future__ import annotations

from typing import Any

from conftest import Clock, as_bot
from unclefarmer import trust
from unclefarmer.engine import teamlead
from unclefarmer.engine.botparts.dialogs import DialogsMixin

LEADER_ID = 27000


def entry(text: str, cmd: list[int] | None = None, **more: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"options": [text], "raw": None, "text": None, "flags": 12}
    if cmd is not None:
        e["cmd"] = cmd
    e.update(more)
    return e


class Agent:
    def __init__(self, game: Game) -> None:
        self.game = game

    def json(self, line: str) -> dict[str, Any]:
        verb, _sp, arg = line.partition(" ")
        if verb == "chat":
            since = int(arg or 0)
            return {"last": len(self.game.chat),
                    "chat": [{"name": "", "channel": 46, "text": t} for t in self.game.chat[since:]]}
        if verb == "sounds":
            since = int(arg or 0)
            events = [{"seq": i + 1, "id": sid, "x": None, "y": None, "t": 0, "muted": False}
                      for i, sid in enumerate(self.game.sounds) if i + 1 > since]
            return {"now": len(self.game.sounds), "events": events, "lost": False}
        if verb == "soundlisten":
            self.game.listening = arg
            return {"hooked": True, "reason": "", "watch": "off", "mute": "off", "observed": 0, "dropped": 0,
                    "live_muted": 0, "unsupported": 0}
        raise AssertionError(line)

    def send(self, line: str) -> str:
        raise AssertionError(line)


class Game:
    """The leader's game: a team, players waiting to join, the chat."""

    def __init__(self, applicants: list[tuple[str, int]], leader: bool = True, team: str | None = "Farmers") -> None:
        self.applicants: list[list[Any]] = [[name, pid, 0] for name, pid in applicants]
        self.leader = leader
        self.team = team
        self.members: list[str] = []
        self.chat: list[str] = []
        self.sounds: list[int] = []
        self.listening = ""
        self.menu: str | None = None
        self.friends: list[tuple[str, int]] = []
        self.opened: list[str] = []

    def top(self) -> dict[str, Any] | None:
        rows: list[list[dict[str, Any] | None]]
        if self.menu == "action":
            team_row: list[dict[str, Any] | None]
            if self.team is None:
                team_row = [entry("Create a team", [1, 0])]
            elif self.leader:
                team_row = [entry("%s team" % self.team, [1, 3, 99])]
                if self.applicants:
                    team_row.append(entry("apply to join: %d " % len(self.applicants), [1, 5]))
                team_row += [entry("invite", [1, 13, -1]), entry("rename", [1, -1]),
                             entry("disband", [1, 2], f="Are you sure you want to disband the team?")]
            else:
                team_row = [entry("%s team" % self.team, [1, 3, 99]), entry("quit", [1, 8, LEADER_ID])]
            rows = [
                [entry("Action menu; lag 120", [0])], team_row, [entry("Disorder gang", [3, 3, 1])],
                [entry("friends", [32, 6]), entry("Application: 1", [32, 7])]]
        elif self.menu == "apply":
            rows = [[entry(str(name), [1, 6, int(pid), int(sel)]), entry(str(pid)),
                     entry("private chat", [int(pid)], h=name)] for name, pid, sel in self.applicants]
            last: list[list[dict[str, Any] | None]] = [[entry("agree", [1])], [entry("refuse", [0])]]
            rows += last
        elif self.menu == "friends":
            rows = [[entry("%s; Elf; female ; level 69" % name, [32, 10, pid, 0])] for name, pid in self.friends]
        else:
            return None
        return {"rows": rows, "row": 0, "col": 0, "sig": len(self.opened)}

    def choose(self, e: dict[str, Any]) -> None:
        text = e["options"][0]
        self.opened.append(text)
        if text.startswith("apply to join"):
            self.menu = "apply"
        elif text == "friends":
            self.menu = "friends"
        elif self.menu == "apply" and e.get("cmd", [None])[:2] == [1, 6]:
            for a in self.applicants:
                if a[0] == text:
                    a[2] = 1 - a[2]
        elif text == "agree":
            for a in [a for a in self.applicants if a[2]]:
                self.applicants.remove(a)
                self.members.append(a[0])
                self.chat.append("%s joined the team" % a[0])
            self.menu = None


class Bot:
    find_entry = DialogsMixin.find_entry
    find_in_row = DialogsMixin.find_in_row
    entry_texts = staticmethod(DialogsMixin.entry_texts)

    def __init__(self, game: Game, **p: Any) -> None:
        self.game = game
        self.a = Agent(game)
        self.clock = Clock()
        self.p: dict[str, Any] = {"team_accept": True, "team_accept_who": "trusted", "team_accept_list": [],
                                  "action_menu_key": "lctrl+enter", **p}
        self.trusted = trust.Trusted(["Bob", 555])
        self.lines: list[str] = []
        self.hit_by: set[int] = set()

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def sleep(self, seconds: float) -> None:
        self.clock.now += seconds

    def key(self, k: str, ms: int = 60) -> None:
        if k == self.p["action_menu_key"]:
            self.game.menu = "action"

    def close_dialogs(self) -> None:
        self.game.menu = None

    def dialog(self) -> dict[str, Any]:
        top = self.game.top()
        return {"open": 1 if top else 0, "top": top}

    def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if bool(d["open"]) == want_open else None

    def choose(self, pattern: str, row: str | None = None) -> dict[str, Any] | None:
        top = self.game.top()
        where = self.find_entry(top, pattern) if row is None else self.find_in_row(top, row, pattern)
        if top is None or where is None:
            self.game.menu = None
            return None
        e = top["rows"][where[0]][where[1]]
        self.game.choose(e)
        return e

    def attackers(self) -> set[int]:
        return self.hit_by


def test_a_trusted_player_is_let_in_and_a_stranger_left_waiting() -> None:
    game = Game([("Bob", 555), ("Mallory", 777)])
    b = Bot(game)
    assert teamlead.take_requests(as_bot(b)) == ["Bob"]
    assert game.members == ["Bob"]
    assert [a[0] for a in game.applicants] == ["Mallory"] and game.applicants[0][2] == 0   # never selected
    assert "refuse" not in game.opened                                                       # never refused
    assert any("Mallory (777)" in line and "left waiting" in line for line in b.lines)
    assert any("accepted into the team: Bob" in line for line in b.lines)


def test_a_stranger_is_named_once_however_often_the_list_is_read() -> None:
    game = Game([("Mallory", 777)])
    b = Bot(game)
    for _i in range(3):
        assert teamlead.take_requests(as_bot(b)) == []
    assert sum("Mallory" in line for line in b.lines) == 1
    assert "agree" not in game.opened and game.menu is None


def test_a_list_of_names_and_ids_says_who_joins() -> None:
    game = Game([("Bob", 555), ("Carol", 31), ("Dave", 40)])
    b = Bot(game, team_accept_who="list", team_accept_list=["dave", 31])
    assert sorted(teamlead.take_requests(as_bot(b))) == ["Carol", "Dave"]     # by name or by id
    assert [a[0] for a in game.applicants] == ["Bob"]


def test_the_games_friends_are_read_once_a_run() -> None:
    game = Game([("Erin", 61)])
    game.friends = [("Erin", 61), ("Frank", 62)]
    b = Bot(game, team_accept_who="friends")
    assert teamlead.take_requests(as_bot(b)) == ["Erin"]
    game.applicants = [["Frank", 62, 0]]
    assert teamlead.take_requests(as_bot(b)) == ["Frank"]
    assert game.opened.count("friends") == 1


def test_no_request_nothing_opened_past_the_action_menu() -> None:
    game = Game([])
    b = Bot(game)
    assert teamlead.take_requests(as_bot(b)) == []
    assert game.opened == [] and game.menu is None


def test_a_member_who_doesnt_lead_takes_nobody_and_says_so_once() -> None:
    for game in (Game([("Bob", 555)], leader=False), Game([("Bob", 555)], team=None)):
        b = Bot(game)
        teamlead.take_requests(as_bot(b))
        teamlead.take_requests(as_bot(b))
        assert game.members == [] and game.opened == []
        assert sum("doesn't lead a team" in line for line in b.lines) == 1


def test_the_friend_request_beside_friends_is_not_a_team_request() -> None:
    game = Game([])                   # "Application: 1" is in the menu, no "apply to join"
    b = Bot(game)
    teamlead.take_requests(as_bot(b))
    assert "Application: 1" not in game.opened


def test_the_request_sound_is_heard_between_fights_and_only_then_the_menus_open() -> None:
    game = Game([])
    b = Bot(game)
    teamlead.start_farm(as_bot(b))
    assert game.listening == "1057"
    assert not teamlead.between_fights(as_bot(b))          # nothing heard: no menu
    game.applicants = [["Bob", 555, 0]]
    game.sounds += [1012, teamlead.REQUEST_SOUND, 1015]
    b.hit_by = {9}
    assert not teamlead.between_fights(as_bot(b))          # something is hitting us: later
    b.hit_by = set()
    assert teamlead.between_fights(as_bot(b))
    assert game.members == ["Bob"]
    assert not teamlead.between_fights(as_bot(b))          # heard once, taken once


def test_requests_waiting_when_the_farm_starts_are_taken_at_once() -> None:
    game = Game([("Bob", 555)])
    b = Bot(game)
    teamlead.start_farm(as_bot(b))
    assert game.members == ["Bob"]


def test_off_by_default_nothing_is_listened_to_or_opened() -> None:
    game = Game([("Bob", 555)])
    b = Bot(game, team_accept=False)
    teamlead.start_farm(as_bot(b))
    game.sounds.append(teamlead.REQUEST_SOUND)
    assert not teamlead.between_fights(as_bot(b))
    assert game.listening == "" and game.opened == [] and game.members == []


def test_agree_with_nobody_joining_is_said() -> None:
    game = Game([("Bob", 555)])
    b = Bot(game)
    real = game.choose

    def lost(e: dict[str, Any]) -> None:
        if e["options"][0] == "agree":            # the server never answers
            game.opened.append("agree")
            game.menu = None
            return
        real(e)
    game.choose = lost  # type: ignore[method-assign]
    assert teamlead.take_requests(as_bot(b)) == []
    assert any("didn't say they joined" in line for line in b.lines)
