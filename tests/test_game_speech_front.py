"""The games' own speech, per character or for all: "front" lets a game talk only while its window is
in front (players with several games open asked for it), "always" silences it once, and a run leaves
a "front" game's speech to the main window."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any, cast

import pytest

from unclefarmer import settings
from unclefarmer.gui.mainparts import gamespeech


def test_a_character_s_own_choice_wins_over_the_app_s() -> None:
    app = {"game_speech": "running"}
    assert settings.game_speech_mode(app, None) == "running"
    assert settings.game_speech_mode(app, {"game_speech": ""}) == "running"
    assert settings.game_speech_mode(app, {"game_speech": "front"}) == "front"
    assert settings.game_speech_mode({"game_speech": "front"}, {"game_speech": "never"}) == "never"
    assert settings.game_speech_mode({"game_speech": "loud"}, {"game_speech": "odd"}) == "running"
    assert settings.PROFILE_DEFAULTS["game_speech"] == ""


class Link:
    sent: list[tuple[int, str]] = []
    answers: dict[int, str] = {}

    def __init__(self, pid: int, timeout: float) -> None:
        self.pid = pid

    def __enter__(self) -> "Link":
        return self

    def __exit__(self, *_a: object) -> None:
        pass

    def send(self, line: str) -> str:
        if line == "speech":
            return Link.answers.get(self.pid, "on")
        Link.sent.append((self.pid, line))
        return line.split()[1]


class Now:
    """A thread that runs at once, so what was sent can be read right after."""

    def __init__(self, target: Callable[..., object], args: tuple[Any, ...] = (), daemon: bool | None = None) -> None:
        self.target, self.args = target, args

    def start(self) -> None:
        self.target(*self.args)

    def join(self, _timeout: float | None = None) -> None:
        pass


class Frame:
    def __init__(self, games: list[tuple[int, str | None]], app: str = "running") -> None:
        self.games = [(pid, profile, None) for pid, profile in games]
        self.s = {"game_speech": app}
        self.silenced: set[int] = set()
        self.destroyed = False

    def __getattr__(self, name: str) -> Any:
        return getattr(gamespeech.GameSpeechMixin, name).__get__(self)


@pytest.fixture(autouse=True)
def wired(monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    Link.sent, Link.answers = [], {}
    monkeypatch.setattr(gamespeech, "AgentLink", Link)
    monkeypatch.setattr(gamespeech.threading, "Thread", Now)
    def at_once(f: Callable[..., object], *a: object) -> None:
        f(*a)
    monkeypatch.setattr(gamespeech.wx, "CallAfter", at_once)
    yield


def own(name: str, mode: str) -> None:
    p = settings.load_profile(name)
    p["game_speech"] = mode
    settings.save_profile(name, p)


def front(monkeypatch: pytest.MonkeyPatch, f: Frame, pid: int | None) -> list[tuple[int, str]]:
    Link.sent = []
    monkeypatch.setattr(gamespeech, "foreground_pid", lambda: pid)
    f.follow_front()
    return Link.sent


def test_only_the_game_in_front_talks(monkeypatch: pytest.MonkeyPatch) -> None:
    f = Frame([(1, "Hero"), (2, "Bob"), (3, None)], app="front")
    f.keep_silent()
    assert sorted(front(monkeypatch, f, 1)) == [(1, "speech on"), (2, "speech off"), (3, "speech off")]
    assert front(monkeypatch, f, 1) == []                                   # nothing changed: nothing sent
    assert sorted(front(monkeypatch, f, 2)) == [(1, "speech off"), (2, "speech on")]
    assert sorted(front(monkeypatch, f, 999)) == [(2, "speech off")]       # another program in front: all quiet


def test_a_character_s_own_mode_keeps_its_game_out_of_it(monkeypatch: pytest.MonkeyPatch) -> None:
    own("Bob", "never")
    f = Frame([(1, "Hero"), (2, "Bob")], app="front")
    f.keep_silent()
    assert front(monkeypatch, f, 1) == [(1, "speech on")]                  # Bob always talks: left alone
    own("Hero", "running")
    f.keep_silent()                                                         # read again with the game list
    assert front(monkeypatch, f, 2) == []


def test_a_game_that_leaves_front_mode_talks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    own("Hero", "front")
    f = Frame([(1, "Hero"), (2, "Bob")])
    f.keep_silent()
    assert front(monkeypatch, f, 2) == [(1, "speech off")]
    Link.sent = []
    own("Hero", "")
    f.keep_silent()
    assert Link.sent == [(1, "speech on")]


def test_an_always_silent_game_is_silenced_once_and_not_given_speech_back(monkeypatch: pytest.MonkeyPatch) -> None:
    own("Bob", "always")
    f = Frame([(1, "Hero"), (2, "Bob")])
    f.keep_silent()
    f.keep_silent()
    assert Link.sent == [(2, "speech off")] and f.silenced == {2}


def test_an_older_agent_is_left_alone(monkeypatch: pytest.MonkeyPatch) -> None:
    Link.answers = {1: "unknown command"}
    f = Frame([(1, "Hero")], app="front")
    f.keep_silent()
    assert front(monkeypatch, f, None) == []


def test_closing_lets_the_games_silenced_for_front_talk(monkeypatch: pytest.MonkeyPatch) -> None:
    f = Frame([(1, "Hero"), (2, "Bob"), (3, "Ann")], app="front")
    f.keep_silent()
    front(monkeypatch, f, 1)
    f.front_busy.add(1)                      # a worker is telling it something just now
    Link.sent = []
    f.speech_back()
    assert sorted(Link.sent) == [(1, "speech on"), (2, "speech on"), (3, "speech on")]
    assert front(monkeypatch, f, 2) == []    # closing: nothing more is sent


def test_a_game_that_didn_t_answer_is_told_again(monkeypatch: pytest.MonkeyPatch) -> None:
    f = Frame([(1, "Hero"), (2, "Bob")], app="front")
    f.keep_silent()

    class Busy(Link):
        def __enter__(self) -> "Link":
            if self.pid == 2:
                raise OSError("timed out")
            return self
    monkeypatch.setattr(gamespeech, "AgentLink", Busy)
    assert front(monkeypatch, f, 1) == [(1, "speech on")]
    monkeypatch.setattr(gamespeech, "AgentLink", Link)
    assert front(monkeypatch, f, 1) == [(2, "speech off")]   # told again at the next look


def test_a_run_leaves_a_front_game_s_speech_to_the_window(tmp_path: Path) -> None:
    from unclefarmer.engine import runner
    run = cast(Any, runner.Run.__new__(runner.Run))
    assert runner.Run.speech_mode_for(run, {"game_speech": "running"}, {"game_speech": "front"}) == "front"
    assert run.speech_mode == "front"
    assert runner.Run.speech_mode_for(run, {"game_speech": "always"}, {}) == "always"
