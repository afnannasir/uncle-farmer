"""Removing members who went offline (engine/teamlead.py): the info key on each member of the list,
an answer "(1(<name>)1)|||((,<id>;))|||..." for one who is online and nothing for one who isn't
(live 2026-11-21), asked twice before anyone is removed."""

from __future__ import annotations

from typing import Any

from conftest import as_bot
from test_team_requests import LEADER_ID, Bot, Game, entry
from unclefarmer.engine import teamlead


class TeamGame(Game):
    def __init__(self, members: dict[str, tuple[int, bool]], **kw: Any) -> None:
        super().__init__([], **kw)
        self.member_ids = {name: pid for name, (pid, _on) in members.items()}
        self.on = {name: on for name, (_pid, on) in members.items()}
        self.said: list[str] = []
        self.cursor: int | None = None
        self.asked: list[str] = []
        self.slow: set[str] = set()       # answers only the second time

    def member_rows(self) -> list[tuple[str, int]]:
        return [("Hero", LEADER_ID)] + list(self.member_ids.items())

    def top(self) -> dict[str, Any] | None:
        if self.menu == "members":
            rows: list[list[dict[str, Any] | None]] = []
            for name, pid in self.member_rows():
                if pid == LEADER_ID:
                    rows.append([{"options": ["%s;%d" % (name, pid), "leader", "Elf"], "cmd": [pid]}])
                else:
                    rows.append([{"options": ["%s;%d" % (name, pid), "Elf"], "cmd": [pid]},
                                 entry("Transfer team leader", [1, 9, pid]), entry("remove", [1, 8, pid])])
            return {"rows": rows, "row": self.cursor or 0, "col": 0, "sig": len(self.opened)}
        if self.menu == "confirm":
            rows = [[entry("Are you sure you want to remove this team member?")], [entry("Confirm", [-1])],
                    [entry("cancel", [-2])]]
            return {"rows": rows, "row": 0, "col": 0, "sig": len(self.opened)}
        return super().top()

    def choose(self, e: dict[str, Any]) -> None:
        text = e["options"][0]
        if self.menu == "action" and text == "%s team" % self.team:
            self.opened.append(text)
            self.menu = "members"
        elif self.menu == "members" and text == "remove":
            self.opened.append(text)
            self.removing = next(n for n, p in self.member_ids.items() if p == e["cmd"][2])
            self.menu = "confirm"
        elif self.menu == "confirm" and text == "Confirm":
            self.opened.append(text)
            del self.member_ids[self.removing]
            self.chat.append("%s has been removed from the team" % self.removing)
            self.menu = None
        else:
            super().choose(e)

    def info(self) -> None:
        if self.menu != "members" or self.cursor is None:
            return
        name, pid = self.member_rows()[self.cursor]
        self.asked.append(name)
        if name in self.slow and self.asked.count(name) == 1:
            return
        if pid == LEADER_ID or self.on.get(name):
            self.said.append("(1(%s)1)|||((,%d;))|||Elf|||,|||(1(female )1)|||(3(; level 69)3)" % (name, pid))


class TeamAgent:
    def __init__(self, game: TeamGame, inner: Any) -> None:
        self.game, self.inner = game, inner

    def json(self, line: str) -> dict[str, Any]:
        verb, _sp, arg = line.partition(" ")
        if verb == "said":
            since = int(arg or 0)
            return {"now": len(self.game.said),
                    "said": [{"t": i + 1, "text": s} for i, s in enumerate(self.game.said) if i + 1 > since]}
        return self.inner.json(line)


class TeamBot(Bot):
    def __init__(self, game: TeamGame, **p: Any) -> None:
        super().__init__(game, **{"team_accept": False, "team_remove_offline": True, "team_offline_every": 10,
                                  "team_offline_town_only": False, **p})
        self.game: TeamGame = game
        self.a = TeamAgent(game, self.a)  # type: ignore[assignment]

    def me(self) -> dict[str, Any]:
        return {"id": LEADER_ID}

    def key(self, k: str, ms: int = 60) -> None:
        if k == "g":
            self.game.info()
        else:
            super().key(k, ms)

    def cursor_to(self, top: dict[str, Any], r: int, c: int) -> dict[str, Any] | None:
        self.game.cursor = r
        return self.game.top()


def test_the_member_who_says_nothing_is_removed_and_the_one_who_answers_stays() -> None:
    game = TeamGame({"Bob": (555, True), "Carol": (31, False)})
    b = TeamBot(game)
    assert teamlead.remove_offline(as_bot(b)) == ["Carol"]
    assert list(game.member_ids) == ["Bob"]
    assert game.asked.count("Carol") == 2            # asked twice before being removed
    assert "Hero" not in game.asked                   # never ourselves
    assert any("removed Carol from the team: offline" in line for line in b.lines)
    assert game.menu is None


def test_a_slow_answer_costs_nobody_their_place() -> None:
    game = TeamGame({"Bob": (555, True)})
    game.slow = {"Bob"}
    b = TeamBot(game)
    assert teamlead.remove_offline(as_bot(b)) == []
    assert list(game.member_ids) == ["Bob"] and "remove" not in game.opened


def test_several_offline_members_go_one_list_at_a_time() -> None:
    game = TeamGame({"Bob": (555, False), "Carol": (31, True), "Dave": (40, False)})
    b = TeamBot(game)
    assert teamlead.remove_offline(as_bot(b)) == ["Bob", "Dave"]
    assert list(game.member_ids) == ["Carol"]
    assert game.asked.count("Carol") == 1             # answered: not asked again in the same check


def test_the_wait_grows_with_the_lag_within_bounds() -> None:
    assert teamlead.answer_wait(None) == teamlead.INFO_LEAST
    assert teamlead.answer_wait(120) == teamlead.INFO_LEAST
    assert teamlead.answer_wait(500) == 2.5
    assert teamlead.answer_wait(5000) == teamlead.INFO_MOST


def test_the_spoken_answer_without_markup() -> None:
    line = "(1(Bob)1)|||((,555;))|||Elf|||,|||(1(female )1)|||(3(; level 69)3)|||(4( holding Epic >||<Staff)4)"
    assert teamlead.plain(line) == "Bob,555;Elf,female ; level 69 holding Epic Staff"


def test_the_check_comes_back_every_so_many_minutes_between_fights() -> None:
    game = TeamGame({"Bob": (555, True)})
    b = TeamBot(game, team_offline_every=5)
    assert teamlead.between_fights(as_bot(b))         # never checked: now
    assert not teamlead.between_fights(as_bot(b))
    b.clock.now += 5 * 60
    b.hit_by = {3}
    assert not teamlead.between_fights(as_bot(b))     # not while something hits us
    b.hit_by = set()
    assert teamlead.between_fights(as_bot(b))


def test_only_in_town_the_farm_never_checks_but_the_way_back_does() -> None:
    game = TeamGame({"Bob": (555, False)})
    b = TeamBot(game, team_offline_town_only=True)
    assert not teamlead.between_fights(as_bot(b))
    assert game.member_ids == {"Bob": 555}
    teamlead.after_trip(as_bot(b))
    assert game.member_ids == {}


def test_off_by_default_nobody_is_asked_about() -> None:
    game = TeamGame({"Bob": (555, False)})
    b = TeamBot(game, team_remove_offline=False)
    teamlead.start_farm(as_bot(b))
    teamlead.after_trip(as_bot(b))
    assert not teamlead.between_fights(as_bot(b))
    assert game.asked == [] and game.member_ids == {"Bob": 555}


def test_a_member_of_someone_elses_team_removes_nobody() -> None:
    game = TeamGame({"Bob": (555, False)}, leader=False)
    b = TeamBot(game)
    assert teamlead.remove_offline(as_bot(b)) == []
    assert game.asked == [] and any("doesn't lead a team" in line for line in b.lines)
