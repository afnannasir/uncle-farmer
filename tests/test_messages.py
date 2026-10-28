"""Private message commands: what counts as a command, help, and the buff switch while following."""

from __future__ import annotations

import collections
from types import SimpleNamespace

import pytest

from unclefarmer import messages
from conftest import HostStub, as_kind, as_parts, Clock
from unclefarmer.engine.runner import Run as RealRun
from unclefarmer.messages import Messenger


@pytest.mark.parametrize("text, want", [
    ("help", ("help", "")),
    ("help follow", ("help", "follow")),
    ("buff", ("buff", "")),
    ("buff off", ("buff", "off")),
    ("stop", ("stop", "")),
    ("stop it lol", None),               # chat, not a command
    ("quit", ("leave", "")),
    ("quit team", ("leave", "")),
    ("quit game", ("quit", "")),
    ("followme", ("follow", "")),
    ("hello there", None),
])
def test_parse_command(text: str, want: tuple[str, str] | None) -> None:
    assert messages.parse_command(text) == want


def test_every_command_has_a_description_that_fits_one_message() -> None:
    d = messages.details()
    assert set(d) == set(messages.COMMANDS)
    assert all(len(text) <= 190 for text in d.values())


class Host(HostStub):
    def __init__(self, run: FakeRun | None=None) -> None:
        self.run = run

    def run_for(self, pid: int) -> RealRun | None:
        return None if self.run is None else as_kind(RealRun, self.run)


def messenger(run: FakeRun | None=None) -> Messenger:
    m = messages.Messenger.__new__(messages.Messenger)
    m.host, m.pid, m.outbox = Host(run), 1, collections.deque()
    return m


def test_help_alone_sends_the_list_then_a_hint() -> None:
    m = messenger()
    assert m.cmd_help(5, "Friend", "") is None
    lines = [x[2] for x in m.outbox]
    assert lines[0].startswith("Commands: ") and "help follow" in lines[1]


@pytest.mark.parametrize("rest, start", [
    ("follow", "follow:"), ("FOLLOWME", "follow:"), ("quit game", "quit game:"), ("buff", "buff:"),
])
def test_help_for_one_command(rest: str, start: str) -> None:
    answer = messenger().cmd_help(5, "Friend", rest)
    assert answer is not None and answer.startswith(start)


def test_help_for_something_unknown() -> None:
    answer = messenger().cmd_help(5, "Friend", "fly")
    assert answer is not None and answer.startswith("No command called fly.")


class FakeRun:
    follows = "friend"

    def __init__(self) -> None:
        self.bot = SimpleNamespace()          # the running bot: the command sets its follow_buffs


def test_buff_switches_buffs_for_the_follow() -> None:
    run = FakeRun()
    m = messenger(run)
    assert m.cmd_buff(5, "Friend", "") == "Buffs on." and run.bot.follow_buffs is True
    assert m.cmd_buff(5, "Friend", "off") == "Buffs off." and run.bot.follow_buffs is False
    assert "Send buff" in (m.cmd_buff(5, "Friend", "maybe") or "")


def test_buff_without_a_follow() -> None:
    assert "not following" in (messenger().cmd_buff(5, "Friend", "") or "")


def test_no_buffs_while_following_until_asked(clock: Clock) -> None:
    from conftest import Bot, Game

    from unclefarmer.engine import combat
    g = Game(clock, ["Arcane Shot", "Strong-Arm Aura"])
    b = Bot(g)
    sb = combat.SkillBook(as_parts(b))
    me = {"hp": 1, "maxhp": 1}
    assert sb.buff_now(me, 1e9)                      # a normal run keeps its buffs up
    sb.buff_tried.clear()
    b.following = "Friend"
    assert sb.buff_now(me, 2e9) is None              # following: none
    b.follow_buffs = True
    assert sb.buff_now(me, 3e9)                      # after "buff"
