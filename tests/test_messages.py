# pyright: reportAttributeAccessIssue=false
# The stand-ins in this file replace real methods and attributes with fakes on purpose;
# what they do is checked by running the tests, not by their types.
"""Private message commands: what counts as a command, help, and the buff switch while following."""

import collections

import pytest

from unclefarmer import messages


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
def test_parse_command(text, want):
    assert messages.parse_command(text) == want


def test_every_command_has_a_description_that_fits_one_message():
    d = messages.details()
    assert set(d) == set(messages.COMMANDS)
    assert all(len(text) <= 190 for text in d.values())


class Host:
    def __init__(self, run=None):
        self.run = run

    def run_for(self, pid):
        return self.run


def messenger(run=None):
    m = messages.Messenger.__new__(messages.Messenger)
    m.host, m.pid, m.outbox = Host(run), 1, collections.deque()
    return m


def test_help_alone_sends_the_list_then_a_hint():
    m = messenger()
    assert m.cmd_help(5, "Friend", "") is None
    lines = [x[2] for x in m.outbox]
    assert lines[0].startswith("Commands: ") and "help follow" in lines[1]


@pytest.mark.parametrize("rest, start", [
    ("follow", "follow:"), ("FOLLOWME", "follow:"), ("quit game", "quit game:"), ("buff", "buff:"),
])
def test_help_for_one_command(rest, start):
    answer = messenger().cmd_help(5, "Friend", rest)
    assert answer is not None and answer.startswith(start)


def test_help_for_something_unknown():
    answer = messenger().cmd_help(5, "Friend", "fly")
    assert answer is not None and answer.startswith("No command called fly.")


class Run:
    follows = "friend"

    def __init__(self):
        self.bot = type("B", (), {})()


def test_buff_switches_buffs_for_the_follow():
    run = Run()
    m = messenger(run)
    assert m.cmd_buff(5, "Friend", "") == "Buffs on." and run.bot.follow_buffs is True
    assert m.cmd_buff(5, "Friend", "off") == "Buffs off." and run.bot.follow_buffs is False
    assert "Send buff" in m.cmd_buff(5, "Friend", "maybe")


def test_buff_without_a_follow():
    assert "not following" in messenger().cmd_buff(5, "Friend", "")


def test_no_buffs_while_following_until_asked(clock):
    from conftest import Bot, Game

    from unclefarmer.engine import combat
    g = Game(clock, ["Arcane Shot", "Strong-Arm Aura"])
    b = Bot(g)
    sb = combat.SkillBook(b)
    me = {"hp": 1, "maxhp": 1}
    assert sb.buff_now(me, 1e9)                      # a normal run keeps its buffs up
    sb.buff_tried.clear()
    b.following = "Friend"
    assert sb.buff_now(me, 2e9) is None              # following: none
    b.follow_buffs = True
    assert sb.buff_now(me, 3e9)                      # after "buff"
