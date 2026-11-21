"""Handing the team over when the run ends (engine/teamlead.py): the first of the player's list who
is in the team gets it ("Transfer team leader", confirmed; "<name> has become the team leader")."""

from __future__ import annotations

from typing import Any

from conftest import as_bot
from test_team_offline import TeamBot, TeamGame
from test_team_requests import entry
from unclefarmer.engine import teamlead


class HandGame(TeamGame):
    def top(self) -> dict[str, Any] | None:
        if self.menu == "transfer":
            rows: list[list[dict[str, Any] | None]] = [
                [entry("Are you sure you want to transfer the team leader to this member?")],
                [entry("Confirm", [-1])], [entry("cancel", [-2])]]
            return {"rows": rows, "row": 0, "col": 0, "sig": len(self.opened)}
        return super().top()

    def choose(self, e: dict[str, Any]) -> None:
        text = e["options"][0]
        if self.menu == "members" and text == "Transfer team leader":
            self.opened.append(text)
            self.heir = next(n for n, p in self.member_ids.items() if p == e["cmd"][2])
            self.menu = "transfer"
        elif self.menu == "transfer" and text == "Confirm":
            self.opened.append(text)
            self.chat.append("%s has become the team leader" % self.heir)
            self.leader = False
            self.menu = None
        else:
            super().choose(e)


def bot(game: HandGame, to: list[str | int]) -> TeamBot:
    return TeamBot(game, team_remove_offline=False, team_hand_over=True, team_hand_to=to)


def test_the_first_on_the_list_who_is_in_the_team_gets_it() -> None:
    game = HandGame({"Bob": (555, True), "Carol": (31, True)})
    b = bot(game, ["Zed", "carol", "Bob"])          # Zed isn't in it; names match whatever the case
    assert teamlead.hand_over(as_bot(b)) == "Carol"
    assert game.leader is False and game.heir == "Carol"


def test_an_id_on_the_list_finds_its_member() -> None:
    game = HandGame({"Bob": (555, True), "Carol": (31, True)})
    b = bot(game, [555])
    assert teamlead.hand_over(as_bot(b)) == "Bob"


def test_nobody_on_the_list_the_team_stays_and_it_is_said() -> None:
    game = HandGame({"Bob": (555, True)})
    b = bot(game, ["Zed"])
    assert teamlead.hand_over(as_bot(b)) is None
    assert game.leader is True and "Transfer team leader" not in game.opened
    assert any("stays with this character" in line for line in b.lines)


def test_the_end_of_the_run_hands_it_over_only_when_ticked() -> None:
    game = HandGame({"Bob": (555, True)})
    b = bot(game, ["Bob"])
    b.p["team_hand_over"] = False
    teamlead.at_end(as_bot(b))
    assert game.leader is True and game.opened == []
    b.p["team_hand_over"] = True
    teamlead.at_end(as_bot(b))
    assert game.leader is False


def test_a_member_of_someone_elses_team_hands_nothing_over() -> None:
    game = HandGame({"Bob": (555, True)}, leader=False)
    b = bot(game, ["Bob"])
    assert teamlead.hand_over(as_bot(b)) is None
    assert "Transfer team leader" not in game.opened
