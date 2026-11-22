"""The team at the start of a run and after it (engine/teamlead.py): out of a team another player
leads, our own team made (anywhere) or made again under its name, the safety watch when
another player disbands or takes the team, and staying in town to manage it."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_team_handover import HandGame
from test_team_offline import TeamBot
from test_team_requests import LEADER_ID, entry
from unclefarmer.engine import teamlead


class Done(Exception):
    pass


class StartGame(HandGame):
    def __init__(self, **kw: Any) -> None:
        super().__init__({"Bob": (555, True)}, **kw)
        self.box: str | None = None
        self.typed = ""
        self.ignore_creates = 0

    def top(self) -> dict[str, Any] | None:
        if self.menu in ("quit", "disband"):
            rows: list[list[dict[str, Any] | None]] = [[entry("Are you sure?")], [entry("Confirm", [-1])],
                                                       [entry("cancel", [-2])]]
            return {"rows": rows, "row": 0, "col": 0, "sig": len(self.opened)}
        return super().top()

    def choose(self, e: dict[str, Any]) -> None:
        text = e["options"][0]
        if self.menu == "action" and text == "Create a team":
            self.opened.append(text)
            self.menu, self.box = None, ""
        elif self.menu == "action" and text in ("quit", "disband"):
            self.opened.append(text)
            self.menu = text
        elif self.menu == "quit" and text == "Confirm":
            self.opened.append("quit Confirm")
            self.team, self.menu = None, None
            self.chat.append("Hero has quit the team")
        elif self.menu == "disband" and text == "Confirm":
            self.opened.append("disband Confirm")
            self.team, self.menu = None, None
            self.chat.append("Team disbanded")
        else:
            super().choose(e)


class StartAgent:
    def __init__(self, game: StartGame, inner: Any) -> None:
        self.game, self.inner = game, inner

    def json(self, line: str) -> dict[str, Any]:
        if line == "input":
            return {"ok": True, "open": self.game.box is not None, "mode": 3, "text": self.game.box or ""}
        return self.inner.json(line)

    def send(self, line: str) -> str:
        g = self.game
        if line == "typeclear":
            g.box = ""
            return "cleared"
        if line.startswith("type "):
            g.box = line[5:]
            return "typed"
        if line == "typeenter":
            if g.ignore_creates:              # the server says nothing and makes nothing
                g.ignore_creates -= 1
                g.box = None
                return "entered"
            g.team = str(g.box)
            g.leader, g.box = True, None
            g.chat.append("Hero set up a team named %s" % g.team)
            return "entered"
        return self.inner.send(line)


class StartBot(TeamBot):
    def __init__(self, game: StartGame, town: bool = True, **p: Any) -> None:
        super().__init__(game, **{"team_remove_offline": False, "team_create": True, "team_name": "Farmers",
                                  "team_remake": False, "team_leave_others": False, **p})
        self.a = StartAgent(game, self.a)  # type: ignore[assignment]
        self.town = town
        self.naps = 0
        self.nap_limit: int | None = None

    def me(self) -> dict[str, Any]:
        return {"id": LEADER_ID, "name": "Hero", "x": 10, "y": 10}

    def map_id(self) -> int:
        return 1 if self.town else 2

    def sleep(self, seconds: float) -> None:
        super().sleep(seconds)
        self.naps += 1
        if self.nap_limit is not None and self.naps >= self.nap_limit:
            raise Done()


def town_one(mid: int, x: float, y: float) -> bool:
    return mid == 1                       # map 1 is a town, map 2 isn't


@pytest.fixture(autouse=True)
def towns(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(teamlead.atlas, "in_town", town_one)


def test_no_team_in_a_town_it_is_made_with_its_name() -> None:
    game = StartGame(team=None)
    b = StartBot(game)
    teamlead.at_start(as_bot(b))
    assert game.team == "Farmers" and game.leader
    assert any("made the team Farmers" in line for line in b.lines)


def test_no_team_outside_a_town_it_is_made_there_too() -> None:
    game = StartGame(team=None)
    b = StartBot(game, town=False)           # the game makes a team anywhere
    teamlead.at_start(as_bot(b))
    assert game.team == "Farmers"


def test_our_team_under_another_name_is_made_again_outside_a_town_too() -> None:
    game = StartGame(team="Old")
    b = StartBot(game, town=False, team_remake=True)
    teamlead.at_start(as_bot(b))
    assert game.team == "Farmers"


def test_another_players_team_is_left_only_when_ticked() -> None:
    game = StartGame(team="Theirs", leader=False)
    b = StartBot(game)
    teamlead.at_start(as_bot(b))
    assert game.team == "Theirs" and "quit" not in game.opened
    assert any("another player leads" in line for line in b.lines)
    b = StartBot(game, team_leave_others=True)
    teamlead.at_start(as_bot(b))
    assert "quit Confirm" in game.opened and game.team == "Farmers" and game.leader


def test_a_creation_the_game_ignores_is_asked_for_once_more_after_a_pause() -> None:
    game = StartGame(team="Theirs", leader=False)
    game.ignore_creates = 1                   # live: asked for in the second we left, ignored silently
    b = StartBot(game, team_leave_others=True)
    start = b.clock.now
    teamlead.at_start(as_bot(b))
    assert game.team == "Farmers" and game.leader
    assert game.opened.count("Create a team") == 2
    assert b.clock.now - start >= 2 * teamlead.AFTER_DISBAND     # after leaving, and before trying again
    assert not any("didn't say the team" in line for line in b.lines)


def test_a_creation_ignored_twice_is_said() -> None:
    game = StartGame(team=None)
    game.ignore_creates = 2
    b = StartBot(game)
    teamlead.at_start(as_bot(b))
    assert game.team is None and game.opened.count("Create a team") == 2
    assert any("didn't say the team Farmers was made" in line for line in b.lines)


def test_our_team_under_another_name_is_made_again_only_when_ticked() -> None:
    game = StartGame(team="Old")
    b = StartBot(game)
    teamlead.at_start(as_bot(b))
    assert game.team == "Old" and "disband" not in game.opened
    b = StartBot(game, team_remake=True)
    teamlead.at_start(as_bot(b))
    assert "disband Confirm" in game.opened and game.team == "Farmers"


def test_our_team_under_its_own_name_is_left_alone() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, team_remake=True)
    teamlead.at_start(as_bot(b))
    assert game.opened == [] and game.team == "Farmers"


def test_nothing_ticked_not_even_the_menu() -> None:
    game = StartGame(team=None)
    b = StartBot(game, team_create=False)
    teamlead.at_start(as_bot(b))
    assert game.menu is None and game.opened == [] and game.team is None


def test_someone_else_disbanding_the_team_ends_the_managing() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, team_accept=True)
    teamlead.start_farm(as_bot(b))
    game.chat.append("Team disbanded")               # not by us
    game.applicants = [["Bob", 555, 0]]
    game.sounds.append(teamlead.REQUEST_SOUND)
    assert not teamlead.between_fights(as_bot(b))
    assert teamlead.state(as_bot(b)).off
    assert any("no more managing it" in line for line in b.lines)


def test_our_own_disband_is_not_taken_for_someone_elses() -> None:
    game = StartGame(team="Old")
    b = StartBot(game, team_remake=True, team_accept=True)
    teamlead.listen(as_bot(b))
    teamlead.at_start(as_bot(b))
    teamlead.watch(as_bot(b))
    assert not teamlead.state(as_bot(b)).off


def test_another_player_taking_the_team_ends_the_managing() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, team_accept=True)
    teamlead.start_farm(as_bot(b))
    game.chat.append("Bob has become the team leader")
    teamlead.watch(as_bot(b))
    assert teamlead.state(as_bot(b)).off


def test_staying_in_town_takes_requests_until_the_run_is_stopped() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, team_accept=True, team_stay_in_town=True)
    b.nap_limit = 40
    game.applicants = [["Bob", 555, 0]]
    with pytest.raises(Done):
        teamlead.at_end(as_bot(b))
    assert "Bob" in game.members
    assert any("staying here managing the team" in line for line in b.lines)


def test_manage_team_sets_the_team_up_first_then_manages_it() -> None:
    """`manage_team` alone: out of another player's team, ours made, then requests taken."""
    game = StartGame(team="Theirs", leader=False)
    b = StartBot(game, team_leave_others=True, team_accept=True)
    b.nap_limit = 60
    game.applicants = [["Bob", 555, 0]]
    with pytest.raises(Done):
        teamlead.manage(as_bot(b))
    assert "quit Confirm" in game.opened and game.team == "Farmers" and game.leader
    assert "Bob" in game.members


def test_manage_team_with_nothing_ticked_for_the_start_just_manages() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, team_create=False, team_accept=True)
    b.nap_limit = 40
    game.applicants = [["Bob", 555, 0]]
    with pytest.raises(Done):
        teamlead.manage(as_bot(b))
    assert "Create a team" not in game.opened and "Bob" in game.members


def test_staying_needs_a_town_and_something_to_manage() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, town=False, team_accept=True, team_stay_in_town=True)
    teamlead.at_end(as_bot(b))
    assert any("ended outside a town" in line for line in b.lines)
    b = StartBot(game, team_accept=False)
    teamlead.stay(as_bot(b))
    assert any("nothing to manage" in line for line in b.lines)


def test_a_team_handed_over_is_not_stayed_for() -> None:
    game = StartGame(team="Farmers")
    b = StartBot(game, team_accept=True, team_stay_in_town=True, team_hand_over=True, team_hand_to=["Bob"])
    teamlead.at_end(as_bot(b))                   # returns: no staying loop
    assert game.leader is False
    assert not any("staying here" in line for line in b.lines)
