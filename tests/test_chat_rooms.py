"""Several international rooms and private channels at once: each room's lines apart, and a line
typed in a room's buffer goes into that room (agent 79)."""
from __future__ import annotations

from collections.abc import Iterator
from typing import TYPE_CHECKING, Any, cast

import pytest
import wx

from unclefarmer import chat, settings
from unclefarmer.gui import chat as gui
from conftest import returning

if TYPE_CHECKING:
    from unclefarmer.gui.mainparts.base import FrameParts

ROOMS = [{"channel": 5, "id": 30, "name": "French", "current": False},
         {"channel": 5, "id": 10, "name": "English", "current": True},
         {"channel": 6, "id": 116, "name": "Meadow", "current": True},
         {"channel": 6, "id": 136, "name": None, "current": False}]


def two_rooms() -> chat.Feed:
    feed = chat.Feed(77)
    feed.note_rooms(ROOMS)
    feed.add("5", "Ali", "salut", room=30)
    feed.add("5", "Bob", "hello", room=10)
    feed.add("6", "Kim", "yo", room=136)
    return feed


def test_each_room_keeps_its_own_lines_and_the_channel_has_them_all() -> None:
    feed = two_rooms()
    assert [x["text"] for x in feed.buffer("5@30")] == ["salut"]
    assert [x["text"] for x in feed.buffer("5@10")] == ["hello"]
    assert [x["text"] for x in feed.buffer("5")] == ["salut", "hello"]
    assert [x["text"] for x in feed.buffer("6@136")] == ["yo"]
    assert feed.rooms() == ["5@30", "5@10", "6@116", "6@136"]
    assert feed.current_rooms == {"5": "5@10", "6": "6@116"}


def test_a_room_is_named_once_the_game_shows_its_name() -> None:
    feed = two_rooms()
    assert feed.room_label("5@30") == "International: French"
    assert feed.room_label("6@136") == "Private channel: room 136"
    assert gui.line_text(feed.buffer("6")[-1]) == "Private channel: room 136, Kim: yo"
    feed.note_rooms([{"channel": 6, "id": 136, "name": "Garden", "current": True}])
    assert feed.room_label("6@136") == "Private channel: Garden"
    assert gui.line_text(feed.buffer("6")[-1]) == "Private channel: Garden, Kim: yo"   # heard before the name
    assert feed.current_rooms["6"] == "6@136"


def test_a_line_reads_with_its_room() -> None:
    feed = two_rooms()
    line = feed.buffer("5")[0]
    assert gui.line_text(line) == "International: French, Ali: salut"
    assert gui.line_text(line, where=False, room=True) == "French, Ali: salut"
    assert gui.line_text(line, where=False) == "Ali: salut"


def test_an_older_agent_s_lines_have_no_room() -> None:
    feed = chat.Feed(77)
    feed.add("5", "Ali", "salut")
    assert "room" not in feed.buffer("5")[0] and feed.rooms() == []
    assert gui.line_text(feed.buffer("5")[0]) == "International, Ali: salut"


class Sender:
    def __init__(self, **_kw: object) -> None:
        self.sent: list[str] = []

    def send(self, line: str) -> str:
        if line == "arm":
            return "armed"
        Sender.lines.append(line)
        return "error: not in that room" if " 99 " in line else "sent"

    def close(self) -> None:
        pass

    lines: list[str] = []


def test_a_line_goes_into_the_room_it_is_typed_for(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(chat, "AgentLink", Sender)
    Sender.lines = []
    feed = two_rooms()
    assert feed.say_to("5@30", "bonjour  tout le monde") is None
    assert feed.say_to("5", "hi") is None
    assert Sender.lines == ["sayin 5 30 bonjour tout le monde", "say 5 hi"]
    assert feed.say_to("5@99", "hi") == "You aren't in International: room 99 any more: join it again in the game."


@pytest.fixture
def window(monkeypatch: pytest.MonkeyPatch) -> Iterator[tuple[gui.ChatFrame, chat.Feed]]:
    app = wx.GetApp() or wx.App(False)
    host: Any = wx.Frame(None)
    host.s = settings.load_app()
    windows: dict[int, gui.ChatFrame] = {}
    host.chat_windows = windows
    feed = chat.Feed(77)
    host.chats = {77: feed}
    host.messengers = {}
    notices: list[str] = []
    host.notices = notices
    host.say = notices.append
    host.cached = returning(None)
    frame = gui.ChatFrame(cast("FrameParts", host), 77, "Hero")
    windows[77] = frame
    yield frame, feed
    frame.Destroy()
    host.Destroy()
    app.ProcessPendingEvents()


def arrive(frame: gui.ChatFrame, feed: chat.Feed, channel: str, text: str, room: int) -> None:
    feed.add(channel, "Ali", text, room=room)
    frame.add(feed.buffer(channel)[-1])


def pick(frame: gui.ChatFrame, key: str) -> None:
    frame.channel.SetSelection(frame.ch_keys.index(key))
    frame.show_channel()


def test_the_window_lists_each_room_after_the_fixed_channels(window: Any) -> None:
    frame, feed = window
    fixed = [k for k, _n in gui.channel_buffers()]
    assert frame.ch_keys == fixed                        # no room yet: Ctrl+1 .. Ctrl+9 as before
    feed.note_rooms(ROOMS[:2])
    arrive(frame, feed, "5", "salut", 30)
    assert frame.ch_keys == fixed + ["5@30", "5@10"]
    assert frame.channel.GetString(len(fixed)).startswith("International: French")
    pick(frame, "5@30")
    assert [x["text"] for x in frame.ch_lines] == ["salut"]
    arrive(frame, feed, "5", "hello", 10)                # another room's line: not in this one
    assert [x["text"] for x in frame.ch_lines] == ["salut"]
    assert frame.unread["5@10"] == 1 and frame.unread["5"] == 1
    pick(frame, "5")
    assert frame.ch_list.GetStrings() == ["French, Ali: salut, " + gui.stamp(feed.buffer("5")[0]["when"], "24"),
                                          "English, Ali: hello, " + gui.stamp(feed.buffer("5")[1]["when"], "24")]


def test_typing_in_a_room_s_buffer_sends_there(window: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    frame, feed = window
    feed.note_rooms(ROOMS[:2])
    arrive(frame, feed, "5", "salut", 30)
    pick(frame, "5@30")
    assert frame.channel_target() == "5@30"
    assert frame.ch_label.GetLabel() == "Messa&ge to International: French:"
    sent: list[tuple[str, str]] = []
    def say_to(k: str, t: str) -> None:
        sent.append((k, t))
    monkeypatch.setattr(feed, "say_to", say_to)
    frame.ch_text.SetValue("bonjour")
    frame.send()
    assert sent == [("5@30", "bonjour")]
    pick(frame, gui.ALL)
    frame.ch_list.SetSelection(0)
    assert frame.channel_target() == "5@30"              # in All, the selected line's own room
    frame.begin_reply(False, feed.buffer("5@30")[0])
    assert frame.channel_target() == "5@30"
