"""Trusted players by name or by id, and auto replies that call the player by name."""

from __future__ import annotations

import pytest

from conftest import Clock, returning

from unclefarmer import messages, trust


class Link:
    def send(self, line: str) -> str:
        return {"idof Sara": "3005", "idof Kim": "2001"}.get(line, "0")


def test_names_and_ids_mixed_in_one_list():
    t = trust.Trusted(["Kim", " 3005 ", "", "ALI"])
    assert t.names == {"kim", "ali"} and t.ids == {3005}
    assert t.has("kim") and t.has("Ali")
    assert t.has("Sara", pid=3005)                 # a message from id 3005, whatever the name
    assert not t.has("Bob", pid=4000)
    assert t.has("Sara", link=Link())              # only a name (a team invite): the game knows her id
    assert not t.has("Bob", link=Link())
    assert not trust.Trusted([])


def test_replies_call_the_player_by_name():
    cfg = {"pm_call_by_name": True}
    assert messages.address("Hi {name}! I'm away.", "Kim", cfg) == "Hi Kim! I'm away."
    assert messages.address("I'm busy right now.", "Kim", cfg) == "Kim, I'm busy right now."
    assert messages.address("I'm busy right now.", "Kim", {"pm_call_by_name": False}) == "I'm busy right now."
    assert messages.address("{name}, back soon, {name}", "Kim", {"pm_call_by_name": False}) == "Kim, back soon, Kim"


# A name repeats from one server to the next (and an id isn't known to be unique across them): a
# stranger named like the trusted player on another server could give commands.
def test_an_entry_naming_its_server_counts_on_that_server_only():
    entries = ["Kim@S1", "2001@S2", "Ali"]
    on_s1, on_s2 = trust.Trusted(entries, "S1"), trust.Trusted(entries, "s2")
    assert on_s1.has("Kim") and not on_s2.has("Kim")
    assert on_s2.has("Bob", pid=2001) and not on_s1.has("Bob", pid=2001)
    assert on_s1.has("Ali") and on_s2.has("Ali")                   # no server: every server, as before
    unknown = trust.Trusted(entries)                               # the game's server not known
    assert not unknown.has("Kim") and unknown.has("Ali")


def test_names_added_in_the_window_get_the_selected_character_s_server():
    before = ["Ali", "Kim@S1"]
    typed = ["Ali", "Kim@S1", "Sara", "3005", "Lee@S2"]
    assert trust.with_server(typed, before, "S1") == ["Ali", "Kim@S1", "Sara@S1", "3005@S1", "Lee@S2"]
    assert trust.with_server(typed, before, "") == typed           # no character selected: as typed


def test_an_at_sign_without_a_name_is_just_a_name():
    assert trust.split("@S1") == ("@S1", "") and trust.split("Kim @ S1") == ("Kim", "S1")


def test_the_messenger_trusts_by_its_game_s_server(monkeypatch: pytest.MonkeyPatch) -> None:
    m = messages.Messenger.__new__(messages.Messenger)
    m.clock = Clock()
    m.link = object()                                              # type: ignore[assignment]
    monkeypatch.setattr(messages, "server_name", returning("S2"))
    monkeypatch.setattr(messages, "config", returning({"pm_trusted": ["Kim@S1", "Ali@S2"]}))
    assert m.trusted().has("Ali") and not m.trusted().has("Kim")
