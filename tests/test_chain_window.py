# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
# White-box tests: the window's chain methods on a stand-in frame (no wx window).
"""Run, Character chain, driven by the main window (gui/mainparts/chain.py): log the game's character
out for the next one, start each row's run, go on at its end, stop with Stop, end as chosen."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import paths, settings
from unclefarmer.engine import chain
from unclefarmer.gui.mainparts import chain as part

A, B = "Hero\x1fS1\x1f1", "Bob\x1fS1\x1f2"
ROWS = [chain.Row(A, "plan", "Monkey"), chain.Row(B, "plan", "Mermaid")]


class FakeRun:
    def __init__(self, pid: int, profile: str) -> None:
        self.pid, self.profile = pid, profile
        self.stop_event = threading.Event()
        self.ended_by_itself = False
        self.game_lost = False
        self.drop_spare: float | None = None
        self.in_chain = False

    def stop(self) -> None:
        self.stop_event.set()


class Frame:
    """Just what the chain methods use."""
    destroyed = False

    def __init__(self, showing: str | None) -> None:
        self.games: list[tuple[int, str | None, Any]] = [(7, showing, None)]
        self.runs: dict[int, FakeRun] = {}
        self.items = [("plan", "Monkey"), ("plan", "Mermaid")]
        self.said: list[str] = []
        self.started: list[tuple[int, Any, str | None]] = []
        self.logged_out: list[tuple[int, str]] = []
        self.chain_state: chain.State | None = None
        self.chain_timer = None
        self.clock = __import__("conftest").Clock()

    def selected_pid(self) -> int | None:
        return 7

    def refresh_games(self) -> None:
        pass

    def say(self, text: str, run: Any = None) -> None:
        self.said.append(text)

    def item_label(self, item: Any) -> str:
        return "%s %s" % item

    def fill_runs(self, select: Any = None) -> None:
        pass

    def start(self, pid: int | None = None, item: Any = None, remote: bool = False, profile: str | None = None,
              **_k: Any) -> str:
        self.started.append((pid or 0, item, profile))
        self.runs[pid or 0] = FakeRun(pid or 0, profile or "")
        self.games = [(pid or 0, profile, None)]
        return "Starting"

    def chain_log_out(self, pid: int, key: str) -> None:
        self.logged_out.append((pid, key))


for name in ("start_chain", "cancel_chain", "chain_next", "chain_step", "chain_item", "chain_logged_out",
             "chain_run_ended", "chain_tick", "chain_over"):
    setattr(Frame, name, getattr(part.ChainMixin, name))


@pytest.fixture(autouse=True)
def files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Any]:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(chain, "CHAIN_FILE", str(tmp_path / "chain.json"))
    later: list[Any] = []
    monkeypatch.setattr(part.wx, "CallLater", lambda ms, fn, *a: later.append(fn))
    monkeypatch.setattr(part.wx, "CallAfter", lambda fn, *a: later.append(fn))
    monkeypatch.setattr(part.wx, "Timer", lambda owner: type("T", (), {"Start": lambda s, ms: None,
                                                                         "Stop": lambda s: None})())
    monkeypatch.setattr(settings, "is_profile_of", lambda game, key: game == key)
    monkeypatch.setattr(settings, "same_character", lambda a, b: a == b)
    monkeypatch.setattr(settings, "profile_display", lambda key: key.split("\x1f")[0])
    return later


def go(f: Frame, later: list[Any]) -> None:
    """Run what the chain put off (CallLater/CallAfter), as the window's loop would."""
    while later:
        later.pop(0)()


def bind(f: Frame) -> None:
    f.Bind = lambda *a, **k: None          # (the timer's handler)


def test_an_empty_chain_says_so() -> None:
    f = Frame(A)
    assert "empty" in f.start_chain()


def test_the_row_s_character_already_in_the_game_starts_at_once(files: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(A)
    bind(f)
    f.start_chain()
    go(f, files)
    assert f.started == [(7, ("plan", "Monkey"), A)] and f.logged_out == []
    assert f.runs[7].in_chain


def test_another_character_in_the_game_is_logged_out_first(files: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(B)
    bind(f)
    f.start_chain()
    go(f, files)
    assert f.logged_out == [(7, B)] and f.started == []
    f.games = [(7, None, None)]                  # at the login screen now
    f.chain_logged_out(None)
    go(f, files)
    assert f.started == [(7, ("plan", "Monkey"), A)]    # its run logs A in


def test_a_run_that_ended_by_itself_brings_the_next_character(files: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(A)
    bind(f)
    f.start_chain()
    go(f, files)
    run = f.runs.pop(7)
    run.ended_by_itself = True
    f.chain_run_ended(run)
    go(f, files)
    assert A in chain.done_today()
    assert f.logged_out == [(7, A)]                    # B's turn: A logged out first


def test_stop_ends_the_chain(files: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(A)
    bind(f)
    f.start_chain()
    go(f, files)
    run = f.runs.pop(7)
    run.stop()
    f.chain_run_ended(run)
    go(f, files)
    assert f.chain_state is None and f.logged_out == [] and chain.done_today() == {}


def test_next_by_private_message_moves_on(files: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(A)
    bind(f)
    f.start_chain()
    go(f, files)
    assert f.chain_next(7) is not None
    run = f.runs.pop(7)
    f.chain_run_ended(run)
    go(f, files)
    assert A in chain.done_today() and f.logged_out == [(7, A)]


def test_every_character_done_closes_the_game_when_asked(files: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
    chain.save(ROWS, "close")
    for r in ROWS:
        chain.mark(r.profile, 0.0)
    sent: list[str] = []

    class Link:
        def __init__(self, pid: int, timeout: float) -> None:
            pass

        def __enter__(self) -> "Link":
            return self

        def __exit__(self, *a: Any) -> None:
            pass

        def send(self, line: str) -> str:
            sent.append(line)
            return "ok"
    monkeypatch.setattr(part, "AgentLink", Link)
    f = Frame(A)
    bind(f)
    f.start_chain()
    go(f, files)
    assert sent == ["arm", "closegame"] and f.chain_state is None
    assert any("every character has farmed today" in s for s in f.said)


def test_a_row_that_doesn_t_start_counts_as_failed_and_the_chain_goes_on(files: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(None)
    bind(f)
    f.start = lambda *a, **k: "The game isn't there"      # type: ignore[method-assign]
    f.start_chain()
    for _round in range(10):
        if not files:
            break
        files.pop(0)()
    assert f.chain_state is None
    assert any("couldn't farm today: Hero, Bob" in s for s in f.said)    # not "every character has farmed"
