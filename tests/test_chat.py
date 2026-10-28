"""Game chat while farming: every channel read and written through the agent, kept in memory only."""

from __future__ import annotations

import threading

from unclefarmer import chat
from unclefarmer.gui.chat import line_text
import pytest
from conftest import stand_in
from unclefarmer.chat import Feed
from typing import Any, ClassVar


class Agent:
    """A pretend game: two chat lines, one private message, and whatever `say` is sent."""

    said: ClassVar[list[str]] = []                     # every `say`, across the instances a test makes
    done: ClassVar[threading.Event] = threading.Event()   # set once the feed has read past the lines

    def __init__(self, pid: int | None=None, **_kw: object) -> None:
        self.sent: list[str] = []
        self.calls = 0

    def send(self, line: str) -> str:
        if line == "version":
            return "mwagent 32"
        if line.startswith("say "):
            Agent.said.append(line)
            return "sent"
        return "armed" if line == "arm" else "ok"

    def json(self, line: str) -> Any:
        self.calls += 1
        if line == "chat 0":
            return {"ok": True, "last": 7, "chat": []}
        if line == "pms 0":
            return {"ok": True, "last": 2, "pms": []}
        if line.startswith("chat 7"):
            return {"ok": True, "last": 9, "room": 150, "chat": [
                {"n": 8, "channel": 12, "name": "Ali", "text": "need heals"},
                {"n": 9, "channel": 33, "name": "", "text": "Sara looted a gem"}]}
        if line.startswith("chat"):
            Agent.done.set()
            return {"ok": True, "last": 9, "chat": []}
        if line == "pms 2":
            return {"ok": True, "last": 3, "pms": [{"n": 3, "id": 2001, "name": "Kim", "text": "hi"}]}
        return {"ok": True, "last": 3, "pms": []}

    def close(self) -> None:
        pass


def test_channels_arrive_as_three_times_their_number() -> None:
    assert chat.channel_of(0) == "0" and chat.channel_of(12) == "4" and chat.channel_of(15) == "5"
    assert chat.channel_of(18) == "6"                 # the private channel, not private messages
    assert chat.channel_of(33) == chat.NOTICE


def test_the_feed_reads_every_channel_and_private_messages(monkeypatch: pytest.MonkeyPatch) -> None:
    Agent.said, Agent.done = [], threading.Event()
    monkeypatch.setattr(chat, "AgentLink", Agent)
    got: list[dict[str, Any]] = []
    feed = chat.Feed(77, lambda f, line: got.append(line))
    feed.start()
    assert Agent.done.wait(5)
    feed.stop()
    feed.join(5)
    assert [(x["channel"], x["name"], x["text"]) for x in got] == [
        ("4", "Ali", "need heals"), (chat.NOTICE, "", "Sara looted a gem"), (chat.PRIVATE, "Kim", "hi")]
    assert feed.ids == {"kim": 2001} and feed.room == 150
    assert len(feed.lines) == 1                   # All is the channels': notices and private messages apart
    assert [x["text"] for x in feed.buffer(chat.PRIVATE)] == ["hi"]
    assert [x["text"] for x in feed.buffer(chat.NOTICE)] == ["Sara looted a gem"]


def test_saying_a_line_goes_through_the_game_s_sender(monkeypatch: pytest.MonkeyPatch) -> None:
    Agent.said = []
    monkeypatch.setattr(chat, "AgentLink", Agent)
    feed = chat.Feed(77)
    assert feed.say(3, "  hello   gang ") is None
    assert Agent.said == ["say 3 hello gang"]
    assert feed.say(3, "   ") is not None          # nothing to send


def test_a_sending_connection_left_quiet_is_replaced_before_it_is_used(monkeypatch: pytest.MonkeyPatch) -> None:
    # The agent drops a connection quiet for 120 s: the next line sent into it failed with
    # WinError 10053. One quiet past SENDER_IDLE is opened afresh first, and nothing goes twice.
    Agent.said = []
    opened: list[Agent] = []

    class Counted(Agent):
        def __init__(self, pid: int | None=None, **kw: object) -> None:
            super().__init__(pid, **kw)
            opened.append(self)

    clock = [1000.0]
    monkeypatch.setattr(chat, "AgentLink", Counted)
    monkeypatch.setattr(chat.time, "monotonic", stand_in(chat.time.monotonic, lambda: clock[0]))
    feed = chat.Feed(77)
    assert feed.say(3, "one") is None
    clock[0] += chat.SENDER_IDLE - 1
    assert feed.say(3, "two") is None
    assert len(opened) == 1                       # still fresh: the same connection
    clock[0] += chat.SENDER_IDLE + 1
    assert feed.say(3, "three") is None
    assert len(opened) == 2                       # quiet too long: a new one, before sending
    assert Agent.said == ["say 3 one", "say 3 two", "say 3 three"]


def test_a_line_reads_well_for_a_screen_reader() -> None:
    import time
    when = time.mktime((2026, 10, 21, 16, 5, 0, 0, 0, -1))
    line = {"when": when, "channel": "4", "name": "Ali", "text": "need heals"}
    assert line_text(line) == "Team, Ali: need heals"                            # spoken: no time
    assert line_text(line, clock="24") == "Team, Ali: need heals, 16:05"         # the time after the words
    assert line_text(line, clock="12") == "Team, Ali: need heals, 4:05 PM"
    assert line_text(line, where=False, clock="24") == "Ali: need heals, 16:05"  # in the Team buffer
    sent = {"when": when, "channel": chat.PRIVATE, "name": "Hero", "text": "omw", "to": "Kim"}
    assert line_text(sent) == "Private messages, to Kim: omw"
    room = {"when": when, "channel": "6", "name": "Ali", "text": "hi all"}
    assert line_text(room) == "Private channel, Ali: hi all"


class Talk(Agent):
    """Private messages both ways: one from Kim, one this character sent to Kim (agent v34)."""

    def json(self, line: str) -> Any:
        if line == "pms 2":
            return {"ok": True, "last": 4, "pms": [
                {"n": 3, "id": 2001, "name": "Kim", "text": "hi"},
                {"n": 4, "id": 2001, "name": "Kim", "text": "hello back", "mine": True}]}
        return super().json(line)

    def send(self, line: str) -> str:
        if line.startswith("idof "):
            return "3005" if line[5:].lower() == "sara" else "0"
        return super().send(line)


def test_a_conversation_shows_both_sides(monkeypatch: pytest.MonkeyPatch) -> None:
    Agent.said, Agent.done = [], threading.Event()
    monkeypatch.setattr(chat, "AgentLink", Talk)
    feed = chat.Feed(77)
    feed.start()
    assert Agent.done.wait(5)
    feed.stop()
    feed.join(5)
    pm = feed.buffer(chat.PRIVATE)
    assert [(x["name"], x.get("to"), x["text"], x["mine"]) for x in pm] == [
        ("Kim", None, "hi", False), ("", "Kim", "hello back", True)]
    assert line_text(pm[1]) == "Private messages, to Kim: hello back"
    assert line_text(pm[1], where=False) == "you: hello back"


def test_a_private_message_goes_to_an_id_or_a_name_the_game_has_shown(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "AgentLink", Talk)
    feed = chat.Feed(77)
    feed.ids["kim"] = 2001
    assert feed.id_of("4242") == 4242
    assert feed.id_of("Kim") == 2001
    assert feed.id_of("sara") == 3005             # seen in the game: the agent knows her id
    assert feed.id_of("Nobody") is None


def test_the_messenger_never_answers_our_own_lines() -> None:
    import inspect

    from unclefarmer import messages
    assert 'if not pm.get("mine")' in inspect.getsource(messages.Messenger.run)


def test_private_messages_make_one_conversation_per_person() -> None:
    feed = chat.Feed(77)
    feed.ids["kim"] = 2001
    feed.add(chat.PRIVATE, "Kim", "hi", peer_id=2001)
    feed.add(chat.PRIVATE, "", "hello back", mine=True, to="Kim", peer_id=2001)
    feed.add(chat.PRIVATE, "Ali", "yo", peer_id=3005)
    feed.add(chat.PRIVATE, "", "who are you", mine=True, to="4242", peer_id=4242)
    feed.add("4", "Ali", "team line")                     # a channel: never a conversation
    convs = feed.conversations()
    assert [c["name"] for c in convs] == ["4242", "Ali", "Kim"]          # the latest first
    kim = convs[2]
    assert [line_text(x, where=False) for x in kim["lines"]] == ["Kim: hi", "you: hello back"]


def test_a_tone_name_nobody_knows_plays_nothing() -> None:
    from unclefarmer import tones
    tones.play("")
    tones.play("no such tone")
    assert set(tones.ORDER) - {""} == set(tones.TONES)


def test_a_long_text_is_cut_where_the_agent_cuts_it() -> None:
    text = " ".join("word%d" % i for i in range(60))
    got = chat.parts(text)
    assert [len(p) for p in got] == [199, 195, 13]          # the agent's own cut (checked in Java)
    assert " ".join(got) == text
    assert chat.parts("hello   there") == ["hello there"]
    assert [len(p) for p in chat.parts("x" * 450)] == [200, 200, 50]


# ---------------------------------------------------------------- voice messages
VOICE = "0123456789abcdef0123456789abcdef"


class VoiceAgent:
    """A game whose agent does voice messages (42): records, sends and decodes into a WAV."""

    sent: ClassVar[list[str]] = []
    send_reply: ClassVar[str] = ""

    def __init__(self, pid: int | None=None, **_kw: object) -> None:
        pass

    def send(self, line: str) -> str:
        VoiceAgent.sent.append(line)
        if line == "arm":
            return "armed"
        if line.startswith("voice play "):
            return "wav C:/tmp/%s.wav 1760" % line.split()[2]
        if line == "voice record":
            return "recording"
        if line.startswith("voice send"):
            return VoiceAgent.send_reply
        return "ok"

    def close(self) -> None:
        pass


def voice_feed(monkeypatch: pytest.MonkeyPatch, version: int=42) -> Feed:
    VoiceAgent.sent, VoiceAgent.send_reply = [], "sent 1 1760"
    monkeypatch.setattr(chat, "AgentLink", VoiceAgent)
    feed = chat.Feed(77)
    feed.version = version
    return feed


def test_a_voice_message_is_played_through_the_game_s_codec(monkeypatch: pytest.MonkeyPatch) -> None:
    feed = voice_feed(monkeypatch)
    played: list[str | None] = []
    def play(sound: str | None, flags: int) -> None:     # PlaySound is overloaded: stand_in can't take it
        played.append(sound)
    monkeypatch.setattr(chat.winsound, "PlaySound", play)
    assert feed.play_voice(VOICE) is None
    assert VoiceAgent.sent[-1] == "voice play " + VOICE and played == ["C:/tmp/%s.wav" % VOICE]


def test_a_voice_message_is_recorded_and_sent_to_team_or_a_player(monkeypatch: pytest.MonkeyPatch) -> None:
    feed = voice_feed(monkeypatch)
    assert feed.record_voice() is None
    assert feed.send_voice(channel="4") is None
    assert feed.send_voice(to=2001) is None
    assert VoiceAgent.sent[1:4] == ["voice record", "voice send 4", "voice send pm 2001"]
    assert feed.voice_parts == 1
    VoiceAgent.send_reply = "sent 3 45000"         # 45 s went as three messages
    assert feed.send_voice(channel="4") is None and feed.voice_parts == 3
    VoiceAgent.send_reply = "error: too short"
    assert "too short" in (feed.send_voice(channel="3") or "")


def test_an_older_agent_says_the_game_needs_restarting_for_voice(monkeypatch: pytest.MonkeyPatch) -> None:
    feed = voice_feed(monkeypatch, version=41)
    assert "restart the game" in (feed.play_voice(VOICE) or "")
    assert "restart the game" in (feed.record_voice() or "")
    assert VoiceAgent.sent == []


def test_a_voice_line_reads_as_a_voice_message() -> None:
    feed = chat.Feed(77)
    got: list[chat.Line] = []

    def on_line(f: chat.Feed, line: chat.Line) -> None:
        got.append(line)
    feed.on_line = on_line
    feed.add("4", "Ali", "", voice=VOICE)
    assert got[0]["voice"] == VOICE
    assert line_text(got[0]) == "Team, Ali: voice message"
