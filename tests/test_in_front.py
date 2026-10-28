"""While the player is in the game's own window: private messages aren't read out (the game shows
them), trusted players' commands follow the player's choice, and no away note goes out."""

from __future__ import annotations

import pytest

from unclefarmer import messages
from collections.abc import Callable
from typing import Any, TypeVar, TypeVarTuple

from conftest import as_kind, stand_in
from unclefarmer.engine.runner import Run
from unclefarmer.messages import Messenger

T = TypeVar("T")
Ts = TypeVarTuple("Ts")


class Host:
    def __init__(self, in_front: bool) -> None:
        self.in_front = in_front
        self.lines: list[tuple[str, bool]] = []

    def game_in_front(self, pid: int) -> bool:
        return self.in_front

    def run_for(self, pid: int) -> Run | None:
        return as_kind(Run, object())       # a bot is running

    def call(self, fn: Callable[[*Ts], T], *args: *Ts) -> T | str | None:
        return None

    def start_bot(self, pid: int, plan: str | None) -> str | None:
        return None

    def stop_bot(self, pid: int) -> None:
        pass

    def log(self, line: str, speak: bool=False, who: str | None=None, private: bool=False) -> None:
        self.lines.append((line, speak))

    def character(self, pid: int) -> str:
        return "Hero"

    def errand_for(self, pid: int, text: str, title: str, tell: Callable[[str], None] | None = None,
                   follows: str | None = None) -> str | None:
        return None

    def follow_for(self, pid: int, leader: str, tell: Callable[[str], None] | None = None) -> str | None:
        return None

    def unfollow(self, pid: int) -> str | None:
        return None


class Recorded(Messenger):
    """A Messenger that records the commands it would run and the lines it would send."""

    def __init__(self, host: Host) -> None:
        super().__init__(77, host)
        self.fake_host = host
        self.commands: list[str] = []
        self.sent: list[str] = []

    def command(self, to: int, name: str, word: str, rest: str) -> None:
        self.commands.append(word)

    def send(self, to: int, name: str, text: str, when: float = 0.0, first: bool = False) -> None:
        self.sent.append(text)


def messenger(monkeypatch: pytest.MonkeyPatch, in_front: bool, **cfg: Any) -> Recorded:
    conf = dict(messages.DEFAULTS, pm_trusted=["Kim", "2001"], pm_speak="everyone", **cfg)
    monkeypatch.setattr(messages, "config", stand_in(messages.config, lambda: conf))
    return Recorded(Host(in_front))


@pytest.mark.parametrize("in_front", [False, True])
def test_messages_are_read_out_only_when_the_game_is_not_in_front(monkeypatch: pytest.MonkeyPatch, in_front: bool) -> None:
    m = messenger(monkeypatch, in_front)
    m.received({"id": 3005, "name": "Sara", "text": "hi"})
    assert m.fake_host.lines[0] == ("message from Sara: hi", not in_front)


def test_commands_in_front_follow_the_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    m = messenger(monkeypatch, True)
    m.received({"id": 2001, "name": "Kim", "text": "status"})
    assert m.commands == ["status"]                       # on to start with
    m = messenger(monkeypatch, True, pm_commands_in_front=False)
    m.received({"id": 2001, "name": "Kim", "text": "status"})
    assert m.commands == [] and m.sent == []              # no command, no away note, no stranger reply
    m = messenger(monkeypatch, False, pm_commands_in_front=False)
    m.received({"id": 2001, "name": "Kim", "text": "status"})
    assert m.commands == ["status"]                       # the game in the background: commands work


def test_no_away_note_while_the_player_is_in_the_game(monkeypatch: pytest.MonkeyPatch) -> None:
    m = messenger(monkeypatch, True)
    m.received({"id": 2001, "name": "Kim", "text": "are you there"})
    assert m.sent == []
    m = messenger(monkeypatch, False)
    m.received({"id": 2001, "name": "Kim", "text": "are you there"})
    assert m.sent and "Kim" in m.sent[0]
