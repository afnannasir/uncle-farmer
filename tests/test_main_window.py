# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The main window's own logic, part by part (gui/mainparts), without opening a window.

Win is a stand-in window: the widgets are small fakes, and every method it doesn't define itself is
the real one from MainFrame, bound to it - so the parts call each other exactly as in the window.
"""

from __future__ import annotations

import threading
import types
from types import SimpleNamespace
from collections.abc import Callable
from typing import Any, ClassVar

import pytest

wx = pytest.importorskip("wx")

from unclefarmer import chat, messages, speech, stats, tones  # noqa: E402
from unclefarmer.gui import main  # noqa: E402
from unclefarmer.gui.mainparts import results as results_part, runs as runs_part, talk as talk_part  # noqa: E402
from unclefarmer.gui.mainparts.base import LOG_LIMIT, SNAP_FRESH  # noqa: E402

from conftest import returning, stand_in, Clock  # noqa: E402


class Widget:
    """The few widget calls the parts make: a value, a label, a selection, enabled or not."""

    def __init__(self, selection: int=0) -> None:
        self.value, self.label, self.selection, self.enabled = "", "", selection, True
        self.items: dict[tuple[int, int], str] = {}

    def ChangeValue(self, v: str) -> None:
        self.value = v

    def AppendText(self, t: str) -> None:
        self.value += t

    def GetValue(self) -> str:
        return self.value

    def SetInsertionPointEnd(self) -> None:
        pass

    def GetSelection(self) -> int:
        return self.selection

    def SetSelection(self, i: int) -> None:
        self.selection = i

    def GetLabel(self) -> str:
        return self.label

    def SetLabel(self, t: str) -> None:
        self.label = t

    def Enable(self, on: bool=True) -> None:
        self.enabled = on

    def GetNextItem(self, item: int, geometry: int=0, state: int=0) -> int:
        return self.selection if item < 0 else -1      # one selected row, as the Games list has

    def GetItemText(self, i: int, col: int) -> str:
        return self.items.get((i, col), "")

    def SetItem(self, i: int, col: int, text: str) -> None:
        self.items[(i, col)] = text


class Win:
    """A stand-in main window. Whatever it doesn't define is MainFrame's own method, bound here."""

    def __init__(self, games: list[tuple[int, str, str]] | tuple[()]=(), runs: dict[int, FakeRun] | None=None, s: dict[str, Any] | dict[str, int] | dict[str, str] | None=None) -> None:
        self.clock = Clock()
        self.s = dict(s or {})
        self.games = list(games)
        self.runs = dict(runs or {})
        self.messengers: dict[int, Any] = {}       # a game's messenger, or the test's stand-in for one
        self.chats: dict[int, Any] = {}
        self.chat_windows: dict[int, Any] = {}
        self.items: list[Any] = []
        self.log_lines: list[tuple[str, bool]] = []
        self.last_line: dict[int, str] = {}
        self.silenced: set[int] = set()
        self.destroyed = False
        self.snap: tuple[int, dict[str, Any], float] | None = None
        self.game_list, self.log_filter, self.log_box = Widget(0), Widget(0), Widget()
        self.fields = {"activity": Widget()}
        self.start_btn, self.stop_btn, self.pause_btn, self.what = Widget(), Widget(), Widget(), Widget()
        self.said: list[str] = []
        self.started: list[tuple[int | None, Any, bool]] = []     # (pid, what to run, asked remotely)
        self.stopped: list[int | None] = []
        self.shutdowns = 0

    def __getattr__(self, name: str) -> Any:
        attr = getattr(main.MainFrame, name)
        return attr.__get__(self) if callable(attr) and hasattr(attr, "__get__") else attr

    # what would touch the screen or the computer
    def say(self, text: str, run: FakeRun | None=None) -> None:
        self.said.append(text)

    def start(self, pid: int | None=None, item: str | None=None, remote: bool=False) -> str:
        self.started.append((pid, item, remote))
        return "started"

    def stop(self, pid: int | None=None) -> None:
        self.stopped.append(pid)

    def poll(self) -> None:
        pass

    def shut_down(self) -> None:
        self.shutdowns += 1


class FakeRun:
    def __init__(self, pid: int, title: str="wolves", character: str="Ali", bot: Any = None, tally: Any = None) -> None:
        self.pid, self.title, self.character, self.bot, self.tally = pid, title, character, bot, tally
        self.kind, self.started, self.stops = "plan", 0.0, 0
        self.ended_by_itself, self.after_stop, self._farmed, self.alive = False, "", False, True

    def stop(self) -> None:
        self.stops += 1

    def is_alive(self):
        return self.alive

    def farmed(self) -> bool:
        return self._farmed


def bot(kills: int=0, deaths: int=0, paused: bool=False) -> types.SimpleNamespace:
    b = SimpleNamespace(kills=kills, deaths=deaths, paused=threading.Event())
    if paused:
        b.paused.set()
    return b


@pytest.fixture
def spoken(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    out: list[str] = []
    monkeypatch.setattr(speech, "speak", stand_in(speech.speak, lambda text, interrupt=False: out.append(text) or True))
    return out


# ================================================================ games
def test_a_game_row_says_who_where_and_what_runs() -> None:
    w = Win(games=[(1, "Ali", "turkey cave"), (2, "", "")],
            runs={1: FakeRun(1, bot=bot())})
    assert w.game_row(w.games[0]) == ("Ali", "turkey cave", "Running wolves")
    assert w.game_row(w.games[1]) == ("Not logged in", "", "Stopped")
    w.runs[1].bot.paused.set()
    assert w.game_row(w.games[0])[2] == "Paused wolves"
    w.label_games()
    assert w.game_list.items[(0, 2)] == "Paused wolves"


def test_the_selected_game_is_the_list_s_selection() -> None:
    w = Win(games=[(1, "Ali", "a"), (2, "Sara", "b")])
    w.game_list.selection = 1
    assert (w.selected_pid(), w.selected_character()) == (2, "Sara")
    w.game_list.selection = -1                   # nothing selected
    assert w.selected_pid() is None and w.selected_character() is None


def test_a_reading_is_reused_only_for_its_game_and_while_fresh() -> None:
    w = Win()
    w.snap = (1, {"ok": True}, w.clock.time())
    assert w.cached(1) == {"ok": True}
    assert w.cached(2) is None
    w.clock.advance(SNAP_FRESH + 1)
    assert w.cached(1) is None


def test_the_run_text_says_how_long_and_how_it_went() -> None:
    w = Win()
    assert w.run_text(None) == "Not running"
    run = FakeRun(1, bot=bot(kills=12, deaths=1))
    w.clock.now = run.started + 3700
    assert w.run_text(run) == "1 h 1 min, 12 kills, 1 deaths"


# ================================================================ runs
def test_the_pause_key_acts_on_the_game_in_front_if_it_is_ours(monkeypatch: pytest.MonkeyPatch) -> None:
    w = Win(games=[(1, "Ali", ""), (2, "Sara", "")], runs={2: FakeRun(2)})
    monkeypatch.setattr(runs_part, "foreground_pid", stand_in(runs_part.foreground_pid, lambda: 2))
    w.toggle()
    assert w.stopped == [2] and w.started == []          # Sara's runs: Pause stops it
    monkeypatch.setattr(runs_part, "foreground_pid", stand_in(runs_part.foreground_pid, lambda: 999))     # not one of ours
    w.game_list.selection = 0
    w.toggle()
    assert w.started == [(1, None, False)]               # the selected game starts


def test_pause_and_go_on() -> None:
    run = FakeRun(1, bot=bot())
    w = Win(games=[(1, "Ali", "")], runs={1: run})
    w.toggle_pause(1)
    assert run.bot.paused.is_set() and w.said[-1] == "Paused"
    assert w.pause_btn.label == "Go &on"
    w.toggle_pause(1)
    assert not run.bot.paused.is_set() and w.said[-1] == "Going on"
    w.toggle_pause(5)
    assert w.said[-1] == "The bot isn't running."


def test_stop_every_bot() -> None:
    w = Win()
    w.stop_all()
    assert w.said == ["No bot is running."]
    runs = {1: FakeRun(1), 2: FakeRun(2)}
    w.runs = runs
    w.stop_all()
    assert [r.stops for r in runs.values()] == [1, 1]


def test_log_lines_say_whose_they_are_once_there_are_two_games() -> None:
    w = Win(games=[(1, "Ali", "")])
    assert not w.tagged()
    w.games.append((2, "Sara", ""))
    assert w.tagged()


# ================================================================ log
def test_a_log_line_is_kept_shown_and_spoken_when_it_matters(spoken: list[Any]) -> None:
    run = FakeRun(1, character="Ali")
    w = Win(games=[(1, "Ali", ""), (2, "Sara", "")], runs={1: run})
    w._append("12:00:00  wolf down", False, run)
    w._append("12:00:01  stuck near 4,5", True, run)
    assert w.log_lines == [("12:00:00  Ali: wolf down", False), ("12:00:01  Ali: stuck near 4,5", True)]
    assert w.last_line[1] == "stuck near 4,5"
    assert w.fields["activity"].value == "stuck near 4,5"     # the selected game's activity field
    assert spoken == ["Ali: stuck near 4,5"]


def test_the_log_keeps_its_last_lines_only() -> None:
    w = Win()
    for i in range(LOG_LIMIT + 1):
        w._append("line %d" % i, False)
    assert len(w.log_lines) < LOG_LIMIT and w.log_lines[-1][0] == "line %d" % LOG_LIMIT


def test_a_finished_run_leaves_and_a_farming_end_can_shut_the_computer_down() -> None:
    done = FakeRun(1)
    done.ended_by_itself, done._farmed, done.after_stop = True, True, "shutdown"
    other = FakeRun(2)
    w = Win(games=[(1, "Ali", ""), (2, "Sara", "")], runs={1: done, 2: other})
    w._done(done)
    assert 1 not in w.runs and w.shutdowns == 0
    assert w.said == ["The computer shuts down once the other bots have finished."]
    w._done(other)
    assert w.runs == {} and w.shutdowns == 1


def test_a_run_that_didn_t_farm_never_shuts_the_computer_down() -> None:
    run = FakeRun(1)
    run.ended_by_itself, run.after_stop = True, "shutdown"         # a tool or a walk: no farming
    w = Win(runs={1: run})
    w._done(run)
    assert w.shutdowns == 0


# ================================================================ private messages and chat
def test_our_characters_are_found_through_their_own_game() -> None:
    class Link:
        def json(self, line: str) -> Any:
            return {"ok": True, "map": {"id": 5302}, "self": {"x": 10.0, "y": 20.0}}

    w = Win(games=[(1, "Ali", ""), (2, "Sara", "")])
    w.messengers = {1: SimpleNamespace(link=Link()), 2: SimpleNamespace(link=None)}
    assert w.locate(" ali ") == (5302, 10.0, 20.0)
    assert w.locate("Sara") is None                       # her game has no link yet
    assert w.locate("Nobody") is None


def test_the_latest_sighting_of_a_player_counts_while_it_is_recent() -> None:
    w = Win()
    now = w.clock.time()
    w.messengers = {1: SimpleNamespace(sightings={"kim": (1041, 1, 2, now - 100)}),
                    2: SimpleNamespace(sightings={"kim": (5302, 3, 4, now - 10)})}
    assert w.last_seen("Kim") == (5302, 3, 4, 10)
    w.clock.advance(talk_part.TalkMixin.SEEN_FOR)
    assert w.last_seen("Kim") is None


def test_start_from_a_private_message_finds_the_plan() -> None:
    w = Win(games=[(1, "Ali", "")], s={"last_runs": {"Ali": "plan:wolves"}})
    w.items = [("plan", "wolves"), ("plan", "turkey cave"), ("script", "daily")]
    assert w.start_bot(1, "TURKEY") == "started" and w.started[-1] == (1, ("plan", "turkey cave"), True)
    assert w.start_bot(1, "") == "started" and w.started[-1][1] == ("plan", "wolves")    # the last one
    assert w.start_bot(1, "bears") == "No plan called bears."
    w.s = {}
    assert w.start_bot(1, "") == "Which plan? Send: start <plan name>"


def test_a_private_message_plays_its_tone_and_a_channel_line_is_read_when_wanted(monkeypatch: pytest.MonkeyPatch, spoken: list[Any]) -> None:
    played: list[str] = []
    monkeypatch.setattr(tones, "play", stand_in(tones.play, lambda name: played.append(name)))
    monkeypatch.setattr(talk_part, "foreground_pid", stand_in(talk_part.foreground_pid, lambda: None))
    w = Win(games=[(1, "Ali", "")], s={"pm_trusted": ["Boss"], "chat_read": "open", "chat_speak": ["4"]})
    w.chat_line(1, {"channel": chat.PRIVATE, "name": "Boss", "text": "hi"})
    w.chat_line(1, {"channel": chat.PRIVATE, "name": "Kim", "text": "hi"})
    assert played == ["chime", "ding"] and spoken == []

    team = {"channel": "4", "name": "Sara", "text": "need heals", "when": 0}
    w.chat_line(1, team)
    assert spoken == []                                    # read only while the Chat window is open
    shown: list[dict[str, Any]] = []
    w.chat_windows[1] = SimpleNamespace(add=shown.append)
    w.chat_line(1, team)
    w.chat_line(1, dict(team, channel="0"))                # Global isn't one to read
    w.chat_line(1, dict(team, name="ali"))                 # our own line
    assert spoken == ["Team, Sara: need heals"] and len(shown) == 3


def test_nothing_is_read_or_played_while_that_game_is_in_front(monkeypatch: pytest.MonkeyPatch, spoken: list[Any]) -> None:
    played: list[str] = []
    monkeypatch.setattr(tones, "play", stand_in(tones.play, lambda name: played.append(name)))
    monkeypatch.setattr(talk_part, "foreground_pid", stand_in(talk_part.foreground_pid, lambda: 1))
    w = Win(games=[(1, "Ali", "")], s={"chat_read": "always", "chat_speak": ["4"]})
    w.chat_line(1, {"channel": chat.PRIVATE, "name": "Kim", "text": "hi"})
    w.chat_line(1, {"channel": "4", "name": "Sara", "text": "hi", "when": 0})
    assert played == [] and spoken == []


def test_each_game_gets_one_messenger_and_one_chat_feed(monkeypatch: pytest.MonkeyPatch) -> None:
    class Fake:
        made: ClassVar[list[int]] = []

        def __init__(self, pid: int, *a: object, **k: object) -> None:
            self.pid, self.stopped, self.unsupported = pid, False, False
            Fake.made.append(pid)

        def start(self) -> None:
            pass

        def stop(self) -> None:
            self.stopped = True

        def is_alive(self):
            return not self.stopped

    monkeypatch.setattr(messages, "Messenger", Fake)
    monkeypatch.setattr(chat, "Feed", Fake)
    w = Win(games=[(1, "Ali", ""), (2, "Sara", "")])
    w.keep_messengers()
    w.keep_chats()
    assert sorted(w.messengers) == [1, 2] and sorted(w.chats) == [1, 2]
    gone = w.messengers[2]
    w.games = [(1, "Ali", "")]                             # Sara's game closed
    w.keep_messengers()
    w.keep_messengers()
    assert sorted(w.messengers) == [1] and gone.stopped
    assert Fake.made.count(1) == 2                         # one messenger and one feed, never again


# ================================================================ results and the game's voice
def test_shift_pause_speaks_the_running_results_else_the_last_run(monkeypatch: pytest.MonkeyPatch, spoken: list[Any]) -> None:
    monkeypatch.setattr(results_part, "foreground_pid", stand_in(results_part.foreground_pid, lambda: None))
    monkeypatch.setattr(stats, "short", stand_in(stats.short, lambda r: "%d kills" % r["kills"]))
    monkeypatch.setattr(stats, "runs", stand_in(stats.runs, lambda character: [{"kills": 7}] if character == "Sara" else []))
    tally = SimpleNamespace(result=lambda: {"kills": 40})
    w = Win(games=[(1, "Ali", ""), (2, "Sara", ""), (3, "New", "")], runs={1: FakeRun(1, tally=tally)})
    w.speak_results()
    w.game_list.selection = 1
    w.speak_results()
    w.game_list.selection = 2
    w.speak_results()
    assert spoken == ["Ali: 40 kills", "Sara, last run: 7 kills", "No results yet."]


def test_results_are_spoken_every_so_often_while_farming(monkeypatch: pytest.MonkeyPatch, spoken: list[Any]) -> None:
    monkeypatch.setattr(stats, "short", stand_in(stats.short, lambda r: "ok"))
    tally = SimpleNamespace(result=returning(dict[str, Any]()), spoken=0.0)
    w = Win(runs={1: FakeRun(1, tally=tally)}, s={"results_every": 30})
    w.clock.now = 29 * 60
    w.progress()
    assert spoken == []
    w.clock.now = 30 * 60
    w.progress()
    w.progress()
    assert spoken == ["Ali: ok"]
    w.s["results_every"] = 0
    w.clock.now = 120 * 60
    w.progress()
    assert spoken == ["Ali: ok"]


def test_every_game_is_muted_once_when_its_speech_is_always_off(monkeypatch: pytest.MonkeyPatch) -> None:
    started: list[Callable[[], object]] = []

    def thread(target: Callable[[], object], daemon: bool | None = None) -> SimpleNamespace:
        return SimpleNamespace(start=lambda: started.append(target))
    monkeypatch.setattr(results_part.threading, "Thread", thread)
    w = Win(games=[(1, "Ali", ""), (2, "Sara", "")], s={"game_speech": "always"})
    w.keep_silent()
    w.keep_silent()
    assert w.silenced == {1, 2} and len(started) == 1     # once per game
    w2 = Win(games=[(1, "Ali", "")], s={"game_speech": "running"})
    w2.keep_silent()
    assert w2.silenced == set()


def test_an_empty_game_folder_setting_is_filled_in_from_the_installed_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    from unclefarmer import gameinstall
    from unclefarmer.gui.mainparts import editing

    saved: list[dict[str, Any]] = []
    monkeypatch.setattr(gameinstall, "status", stand_in(gameinstall.status, lambda folder: "installed" if folder == "D:/Game" else "nogame"))
    monkeypatch.setattr(gameinstall, "find_installed", stand_in(gameinstall.find_installed, lambda remembered="": "D:/Game"))
    def save(parent: object, writer: Callable[..., object], data: dict[str, Any]) -> bool:
        saved.append(dict(data))
        return True
    monkeypatch.setattr(editing, "save_file", save)
    frame: Any = types.SimpleNamespace(s={"game_folder": ""})
    assert editing.EditingMixin.remember_game_folder(frame) == "D:/Game"
    assert frame.s["game_folder"] == "D:/Game" and saved == [{"game_folder": "D:/Game"}]
    assert editing.EditingMixin.remember_game_folder(frame) == "D:/Game" and len(saved) == 1   # once
