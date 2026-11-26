# pyright: reportPrivateUsage=false, reportAttributeAccessIssue=false, reportUnknownMemberType=false, reportUnknownVariableType=false, reportUnknownArgumentType=false, reportUnknownLambdaType=false
# White-box tests: the window's chain methods on a stand-in frame (no wx window).
"""Run, Character chain, driven by the main window (gui/mainparts/chain.py): log the game's character
out for the next one, start each row's run, go on at its end, stop with Stop, end as chosen - and keep
the chain's game to the chain."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import Clock

from unclefarmer import daily, paths, settings
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
        self.end_kind = ""

    def stop(self) -> None:
        self.stop_event.set()


class Frame:
    """Just what the chain methods use; start() as RunsMixin.start does for the chain's checks."""
    destroyed = False
    chain_starting = False

    def __init__(self, showing: str | None) -> None:
        self.games: list[tuple[int, str | None, Any]] = [(7, showing, None)]
        self.runs: dict[int, FakeRun] = {}
        self.items = [("plan", "Monkey"), ("plan", "Mermaid")]
        self.said: list[str] = []
        self.started: list[tuple[int, Any, str | None]] = []
        self.logged_out: list[tuple[int, str]] = []
        self.chain_state: chain.State | None = None
        self.chain_timer = None
        self.carry_queue: list[Any] = []
        self.clock = Clock()
        self.fail_start: str | None = None

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

    def Bind(self, *a: Any, **k: Any) -> None:
        pass

    def start(self, pid: int | None = None, item: Any = None, remote: bool = False, profile: str | None = None,
              **_k: Any) -> str:
        pid = pid or 0
        if pid in self.runs:
            return "Already running."
        if self.chain_owns(pid, profile):
            return "That game is in a character chain"
        if self.fail_start:
            return self.fail_start
        self.started.append((pid, item, profile))
        run = FakeRun(pid, profile or "")
        self.chain_adopt(run)
        self.runs[pid] = run
        self.games = [(pid, profile, None)]
        return "Starting"

    def chain_log_out(self, st: Any, key: str) -> None:
        self.logged_out.append((st.pid, key))


for name in ("start_chain", "cancel_chain", "chain_next", "chain_step", "chain_item", "chain_logged_out",
             "chain_run_ended", "chain_tick", "chain_over", "chain_owns", "chain_adopt", "chain_later",
             "chain_active", "chain_revived"):
    setattr(Frame, name, getattr(part.ChainMixin, name))


@pytest.fixture(autouse=True)
def later(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> list[Any]:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(chain, "CHAIN_FILE", str(tmp_path / "chain.json"))
    put_off: list[Any] = []
    monkeypatch.setattr(part.wx, "CallLater", lambda ms, fn, *a: put_off.append(fn))
    monkeypatch.setattr(part.wx, "CallAfter", lambda fn, *a: put_off.append(fn))
    monkeypatch.setattr(part.wx, "Timer", lambda owner: type("T", (), {"Start": lambda s, ms: None,
                                                                         "Stop": lambda s: None})())
    monkeypatch.setattr(daily, "plan", lambda app: (None, None))
    monkeypatch.setattr(settings, "is_profile_of", lambda game, key: game == key)
    monkeypatch.setattr(settings, "same_character", lambda a, b: a == b)
    monkeypatch.setattr(settings, "profile_display", lambda key: key.split("\x1f")[0])
    return put_off


def go(put_off: list[Any]) -> None:
    """Run what the chain put off (CallLater/CallAfter), as the window's loop would."""
    for _round in range(20):
        if not put_off:
            return
        put_off.pop(0)()


def going(later: list[Any], showing: str | None = A, end: str = "stay") -> Frame:
    chain.save(ROWS, end)
    f = Frame(showing)
    f.start_chain()
    go(later)
    return f


def ended(f: Frame, later: list[Any], **flags: Any) -> None:
    run = f.runs.pop(7)
    for k, v in flags.items():
        setattr(run, k, v)
    f.chain_run_ended(run)
    go(later)


def test_an_empty_chain_says_so() -> None:
    assert "empty" in Frame(A).start_chain()


def test_the_row_s_character_already_in_the_game_starts_at_once(later: list[Any]) -> None:
    f = going(later)
    assert f.started == [(7, ("plan", "Monkey"), A)] and f.logged_out == []
    assert f.runs[7].in_chain


def test_another_character_in_the_game_is_logged_out_first(later: list[Any]) -> None:
    f = going(later, showing=B)
    assert f.logged_out == [(7, B)] and f.started == []
    f.games = [(7, None, None)]                  # at the login screen now
    f.chain_logged_out(f.chain_state, None)
    go(later)
    assert f.started == [(7, ("plan", "Monkey"), A)]    # its run logs A in


def test_a_run_that_ended_by_itself_brings_the_next_character(later: list[Any]) -> None:
    f = going(later)
    ended(f, later, ended_by_itself=True)
    assert A in chain.done_today()
    assert f.logged_out == [(7, A)]                    # B's turn: A logged out first


def test_stop_ends_the_chain(later: list[Any]) -> None:
    f = going(later)
    f.runs[7].stop()
    ended(f, later)
    assert f.chain_state is None and f.logged_out == [] and chain.done_today() == {}


@pytest.mark.parametrize("kind", ["called_away", "player"])
def test_come_home_or_the_player_s_own_character_end_the_chain(later: list[Any], kind: str) -> None:
    # come/home: the character stays with whoever called it; another character came in: the game is theirs
    f = going(later)
    ended(f, later, end_kind=kind)
    assert f.chain_state is None and f.logged_out == []


def test_the_daily_stop_ends_the_chain_with_its_own_reason(later: list[Any]) -> None:
    f = going(later)
    ended(f, later, end_kind="day_over")
    assert f.chain_state is None and any("the day's farming is over" in s for s in f.said)


def test_drop_time_already_used_up_counts_as_done(later: list[Any]) -> None:
    f = going(later)
    ended(f, later, end_kind="done")
    assert A in chain.done_today() and f.logged_out == [(7, A)]


def test_next_by_private_message_moves_on(later: list[Any]) -> None:
    f = going(later)
    assert f.chain_next(7) is not None
    ended(f, later)
    assert A in chain.done_today() and f.logged_out == [(7, A)]


def test_another_run_can_t_take_the_chain_s_game(later: list[Any]) -> None:
    f = going(later, showing=B)                         # logging B out: no run in the game meanwhile
    assert f.chain_owns(7, B)
    assert "character chain" in f.start(7, ("plan", "Mermaid"), profile=B)
    assert f.started == []


def test_the_carried_on_run_of_the_row_is_the_row_s_again(later: list[Any]) -> None:
    f = going(later)
    f.carry_queue = [(A, {}, ("plan", "Monkey"), 0.0)]  # revive is opening A's game again
    ended(f, later, game_lost=True)
    st = f.chain_state
    assert st is not None and st.waiting
    f.games = [(9, None, None)]
    f.start(9, ("plan", "Monkey"), profile=A)            # the carry-on, in the game opened again
    assert f.runs[9].in_chain and st.pid == 9 and not st.waiting


def test_a_lost_game_revive_won_t_open_again_moves_on(later: list[Any]) -> None:
    f = going(later)
    ended(f, later, game_lost=True)                     # nothing queued: no wait
    st = f.chain_state
    assert st is not None and not st.waiting and st.failed == {A: 1}


def test_a_failed_logout_stops_the_chain_and_says_why(later: list[Any]) -> None:
    f = going(later, showing=B)
    f.chain_logged_out(f.chain_state, "the game's dialogs can't be read")
    assert f.chain_state is None and any("dialogs can't be read" in s for s in f.said)


def test_steps_of_a_stopped_chain_don_t_drive_a_new_one(later: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(B)
    f.start_chain()
    old = f.chain_state
    f.cancel_chain()
    f.start_chain()                                     # started again before the old step came
    go(later)
    assert len(f.logged_out) == 1                       # one logout, for the new chain
    assert old is not f.chain_state
    f.chain_logged_out(old, None)                       # the old chain's logout answering late
    go(later)
    assert f.started == []


def test_a_slow_game_list_is_read_again_before_the_chain_gives_up(later: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(A)
    f.games = []
    f.start_chain()
    go(later)
    assert f.chain_state is None                        # gone after MISSING_MOST readings, not one
    assert sum(1 for s in f.said if "isn't running any more" in s) == 1


def test_every_character_done_closes_the_game_when_asked(later: list[Any], monkeypatch: pytest.MonkeyPatch) -> None:
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
    f = going(later, end="close")
    assert sent == ["arm", "closegame"] and f.chain_state is None
    assert any("every character has farmed today" in s for s in f.said)


def test_a_row_that_doesn_t_start_counts_as_failed_and_the_chain_goes_on(later: list[Any]) -> None:
    chain.save(ROWS, "stay")
    f = Frame(None)
    f.fail_start = "The game isn't there"
    f.start_chain()
    go(later)
    assert f.chain_state is None
    assert any("couldn't farm today: Hero, Bob" in s for s in f.said)    # not "every character has farmed"


def test_the_watch_moves_on_from_a_run_that_never_showed_up(later: list[Any]) -> None:
    f = going(later)
    st = f.chain_state
    assert st is not None
    f.runs.clear()                                      # its end was never heard
    f.clock.advance(part.START_MOST + 1)
    f.chain_tick()
    go(later)
    assert st.failed.get(A) == 1
