"""Chat sender names and the trusted list: a player trusted by id or by name changes nothing about
how anyone's name reads in the chat.

The "2magoestelar" names came from the agent, not from here: it found a channel line's name by
walking back from the newline over letters and digits and ran on into the sender's id whenever the
id's last byte was one (agent 54 reads the name from where the game's own reader puts it;
AgentTests.aChatSendersIdNeverJoinsTheirName). These tests pin down that the app's side - the
trusted list, the chat feed, the window's lines - passes every name through as the agent gives
it, the same way for a list of ids as for a list of names.
"""

from __future__ import annotations

from typing import Any

import pytest

from unclefarmer import chat, trust

wx = pytest.importorskip("wx")

from unclefarmer.gui import chat as gui_chat, main  # noqa: E402

# what the agent hands the app for three channel lines and a private message
FROM_AGENT: list[tuple[str, str, str, int]] = [
    (chat.channel_of(3), "magoestelar", "vete a escuchar musica feo persi", 0),
    (chat.channel_of(0), "2Fast4u", "a name that starts with digits", 0),
    (chat.channel_of(6), "Kim", "hello", 0),
    (chat.PRIVATE, "Ali", "come here", 2001)]


class Link:
    """An agent link for Trusted.has: answers `idof`, records every request."""

    def __init__(self, ids: dict[str, int]) -> None:
        self.ids = ids
        self.sent: list[str] = []

    def send(self, line: str) -> str:
        self.sent.append(line)
        return str(self.ids.get(line[len("idof "):].lower(), 0)) if line.startswith("idof ") else "ok"


class ChatWindow:
    def __init__(self) -> None:
        self.lines: list[chat.Line] = []

    def add(self, line: chat.Line) -> None:
        self.lines.append(line)


class Window:
    """The main window's chat side: its settings, one game, its open Chat window."""

    def __init__(self, trusted: list[str]) -> None:
        self.s: dict[str, Any] = {"pm_trusted": list(trusted), "chat_read": "never"}
        self.chat_windows = {7: ChatWindow()}

    def __getattr__(self, name: str) -> Any:
        attr = getattr(main.MainFrame, name)
        return attr.__get__(self) if callable(attr) else attr

    def character(self, pid: int) -> str:
        return "Me"

    def game_in_front(self, pid: int) -> bool:
        return False


def shown(trusted: list[str], monkeypatch: pytest.MonkeyPatch) -> tuple[list[str], list[str]]:
    """(each line as the Chat window shows it, the tones played) after the agent's lines arrive."""
    from unclefarmer import tones
    played: list[str] = []

    def play(tone: str) -> None:
        played.append(tone)
    monkeypatch.setattr(tones, "play", play)
    win: Any = Window(trusted)
    feed = chat.Feed(7, on_line=lambda f, line: win.chat_line(f.pid, line))
    for channel, name, text, pid in FROM_AGENT:
        feed.add(channel, name, text, peer_id=pid)
    lines: list[chat.Line] = win.chat_windows[7].lines
    return [gui_chat.line_text(line, where=False) for line in lines], played


# ================================================================ the two ways of trusting someone
def test_names_read_the_same_with_a_trusted_id_or_a_trusted_name(monkeypatch: pytest.MonkeyPatch) -> None:
    by_id, tones_id = shown(["2001"], monkeypatch)
    by_name, tones_name = shown(["Ali"], monkeypatch)
    nobody, _t = shown([], monkeypatch)
    assert by_id == by_name == nobody == ["magoestelar: vete a escuchar musica feo persi",
                                          "2Fast4u: a name that starts with digits", "Kim: hello", "Ali: come here"]
    assert tones_id == tones_name == ["chime"]          # Ali is trusted either way: by id 2001 or by name


def test_trusting_by_id_never_asks_the_agent_or_changes_a_name() -> None:
    link = Link({"ali": 2001})
    t = trust.Trusted(["2001"])
    assert t.has("Ali", 2001) and not t.has("magoestelar", 5)    # the id given: no question asked
    assert link.sent == []
    assert t.has("Ali", link=link) and link.sent == ["idof Ali"]  # only a name: the agent tells whose it is
    assert not t.has("magoestelar", link=link)
    assert link.ids == {"ali": 2001}                              # nothing was written anywhere


def test_both_ways_keep_ids_and_names_apart() -> None:
    by_id, by_name, mixed = trust.Trusted(["2001"]), trust.Trusted(["Ali"]), trust.Trusted([" Ali ", "2001", ""])
    assert (by_id.ids, by_id.names) == ({2001}, set())
    assert (by_name.ids, by_name.names) == (set(), {"ali"})
    assert (mixed.ids, mixed.names) == ({2001}, {"ali"})
    assert trust.Trusted([2001]).ids == {2001}                    # an id saved as a number reads the same


def test_a_name_beginning_with_digits_is_a_name_not_an_id() -> None:
    t = trust.Trusted(["2Fast4u"])
    assert t.names == {"2fast4u"} and t.ids == set()
    assert t.has("2Fast4u") and not t.has("Fast4u")


def test_removing_and_adding_again_reads_the_same(monkeypatch: pytest.MonkeyPatch) -> None:
    first, _a = shown(["2001"], monkeypatch)
    removed, _b = shown([], monkeypatch)
    again, _c = shown(["2001"], monkeypatch)
    assert first == removed == again


def test_the_feed_keeps_every_name_exactly_as_the_agent_gave_it() -> None:
    feed = chat.Feed(7)
    for channel, name, text, pid in FROM_AGENT:
        feed.add(channel, name, text, peer_id=pid)
    assert [line["name"] for line in feed.buffer("all")] == ["magoestelar", "2Fast4u", "Kim"]
    assert [line["name"] for line in feed.buffer(chat.PRIVATE)] == ["Ali"]
