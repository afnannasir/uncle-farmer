# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""While the player is in the game's own window: private messages aren't read out (the game shows
them), trusted players' commands follow the player's choice, and no away note goes out."""

import pytest

from unclefarmer import messages


class Host:
    def __init__(self, in_front):
        self.in_front, self.lines = in_front, []

    def game_in_front(self, pid):
        return self.in_front

    def run_for(self, pid):
        return object()               # a bot is running

    def log(self, line, speak=False, who=None, private=False):
        self.lines.append((line, speak))

    def character(self, pid):
        return "Hero"


def messenger(monkeypatch, in_front, **cfg):
    conf = dict(messages.DEFAULTS, pm_trusted=["Kim", "2001"], pm_speak="everyone", **cfg)
    monkeypatch.setattr(messages, "config", lambda: conf)
    m = messages.Messenger(77, Host(in_front))
    m.commands, m.sent = [], []
    m.command = lambda to, name, word, rest: m.commands.append(word)
    m.send = lambda to, name, text, when=0.0, first=False: m.sent.append(text)
    return m


@pytest.mark.parametrize("in_front", [False, True])
def test_messages_are_read_out_only_when_the_game_is_not_in_front(monkeypatch, in_front):
    m = messenger(monkeypatch, in_front)
    m.received({"id": 3005, "name": "Sara", "text": "hi"})
    assert m.host.lines[0] == ("message from Sara: hi", not in_front)


def test_commands_in_front_follow_the_choice(monkeypatch):
    m = messenger(monkeypatch, True)
    m.received({"id": 2001, "name": "Kim", "text": "status"})
    assert m.commands == ["status"]                       # on to start with
    m = messenger(monkeypatch, True, pm_commands_in_front=False)
    m.received({"id": 2001, "name": "Kim", "text": "status"})
    assert m.commands == [] and m.sent == []              # no command, no away note, no stranger reply
    m = messenger(monkeypatch, False, pm_commands_in_front=False)
    m.received({"id": 2001, "name": "Kim", "text": "status"})
    assert m.commands == ["status"]                       # the game in the background: commands work


def test_no_away_note_while_the_player_is_in_the_game(monkeypatch):
    m = messenger(monkeypatch, True)
    m.received({"id": 2001, "name": "Kim", "text": "are you there"})
    assert m.sent == []
    m = messenger(monkeypatch, False)
    m.received({"id": 2001, "name": "Kim", "text": "are you there"})
    assert m.sent and "Kim" in m.sent[0]
