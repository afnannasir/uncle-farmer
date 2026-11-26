"""The server comes from the game's own choice (the sign-in screen's Server row), whatever the route.

The game lists a row per server for every route: "1 (Direct Connection )", "1 (Frankfurt Relay)",
"2: Journey Of Landscapes (Tokyo Relay-A)"... (read live, 2026-11-26). The agent (82) says which row
is up and which server it names; the connection's port is only for an older agent.
"""
from __future__ import annotations

from typing import Any

from unclefarmer.link import AgentError, server_name


class Link:
    def __init__(self, answer: object) -> None:
        self.answer = answer

    def json(self, line: str) -> Any:
        assert line == "server"
        if isinstance(self.answer, Exception):
            raise self.answer
        return self.answer


def test_a_relay_row_names_its_server() -> None:
    assert server_name(Link({"ok": True, "port": 9100, "chosen": 10, "chosen_server": 2})) == "S2"
    assert server_name(Link({"ok": True, "port": 8767, "chosen": 9, "chosen_server": 1})) == "S1"


def test_the_choice_wins_over_the_port() -> None:
    assert server_name(Link({"ok": True, "port": 8767, "chosen": 4, "chosen_server": 2})) == "S2"


def test_the_choice_counts_when_the_address_cant_be_read() -> None:
    answer = {"ok": False, "error": "the game's server address wasn't found", "chosen": 3, "chosen_server": 1}
    assert server_name(Link(answer)) == "S1"


def test_an_older_agent_still_goes_by_the_port() -> None:
    assert server_name(Link({"ok": True, "port": 8769})) == "S2"
    assert server_name(Link({"ok": True, "port": 9100})) == ""


def test_an_unknown_number_falls_back_to_the_port() -> None:
    assert server_name(Link({"ok": True, "port": 8767, "chosen": 40, "chosen_server": 7})) == "S1"
    assert server_name(Link({"ok": True, "port": 8767, "chosen_server": True})) == "S1"


def test_no_answer_is_no_server() -> None:
    assert server_name(Link(AgentError("closed"))) == ""
    assert server_name(Link("nonsense")) == ""
