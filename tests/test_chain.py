"""Character chain (engine/chain.py): the rows, the game day's marks, the order, and what a run's end
means for the chain."""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import stand_in

from unclefarmer import daily, paths
from unclefarmer.engine import chain

A, B, C = "Hero\x1fS1\x1f1", "Bob\x1fS1\x1f2", "Ann\x1fS1\x1f3"
ROWS = [chain.Row(A, "plan", "Monkey"), chain.Row(B, "autopilot"), chain.Row(C, "plan", "Mermaid")]


@pytest.fixture(autouse=True)
def files(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(chain, "CHAIN_FILE", str(tmp_path / "chain.json"))


def test_the_rows_and_the_end_are_saved_and_read_back() -> None:
    chain.save(ROWS, "close")
    assert chain.load() == (ROWS, "close")


def test_nothing_saved_reads_as_an_empty_chain_that_stays() -> None:
    assert chain.load() == ([], "stay")


def test_the_first_row_not_done_today_comes_first() -> None:
    assert chain.next_row(ROWS, chain.done_today(), {}) == ROWS[0]
    chain.mark(A, 0.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) == ROWS[1]


def test_marks_hold_for_the_game_day_only(monkeypatch: pytest.MonkeyPatch) -> None:
    chain.mark(A, 0.0)
    assert A in chain.done_today()
    monkeypatch.setattr(daily, "game_day", stand_in(daily.game_day, lambda now=None: "2999-01-01"))
    assert chain.done_today() == {}


def test_every_row_done_ends_the_chain() -> None:
    for r in ROWS:
        chain.mark(r.profile, 0.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) is None


def test_a_row_done_with_drop_time_left_is_done_again_after_the_others_twice_at_most() -> None:
    chain.mark(A, 3600.0)                     # its plan ended with an hour of drop time left
    chain.mark(B, 0.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) == ROWS[2]
    chain.mark(C, 0.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) == ROWS[0]       # done again
    chain.mark(A, 3600.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) == ROWS[0]
    chain.mark(A, 3600.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) is None          # REDO_MOST


def test_a_little_drop_time_left_is_not_worth_another_run() -> None:
    for r in ROWS:
        chain.mark(r.profile, 120.0)
    assert chain.next_row(ROWS, chain.done_today(), {}) is None


def test_a_row_that_failed_twice_is_left_for_this_chain() -> None:
    failed = {A: chain.TRIES}
    assert chain.next_row(ROWS, chain.done_today(), failed) == ROWS[1]


def test_let_every_character_farm_again_today() -> None:
    chain.mark(A, 0.0)
    chain.clear()
    assert chain.done_today() == {}


# ---------------------------------------------------------------- a run's end
def state() -> chain.State:
    st = chain.State(42, list(ROWS))
    st.current, st.phase = ROWS[0], "run"
    return st


def test_a_run_that_ended_by_itself_marks_its_row_and_goes_on() -> None:
    st = state()
    assert chain.after_run(st, stopped=False, ended_by_itself=True, game_lost=False, spare=60.0) == "next"
    assert chain.done_today()[A]["spare"] == 60.0


def test_stop_ends_the_whole_chain_and_marks_nothing() -> None:
    st = state()
    assert chain.after_run(st, stopped=True, ended_by_itself=False, game_lost=False, spare=None) == "stop"
    assert chain.done_today() == {}


def test_next_by_private_message_marks_the_row_though_its_run_was_stopped() -> None:
    st = state()
    st.advance = True
    assert chain.after_run(st, stopped=True, ended_by_itself=False, game_lost=False, spare=None) == "next"
    assert A in chain.done_today() and not st.advance


def test_a_lost_game_waits_for_its_carried_on_run() -> None:
    st = state()
    assert chain.after_run(st, stopped=False, ended_by_itself=False, game_lost=True, spare=None) == "wait"
    assert chain.done_today() == {}


def test_a_failed_run_counts_against_its_row_and_goes_on() -> None:
    st = state()
    assert chain.after_run(st, stopped=False, ended_by_itself=False, game_lost=False, spare=None) == "next"
    assert st.failed == {A: 1} and chain.done_today() == {}


def test_a_hand_edited_chain_keeps_only_rows_it_can_read(tmp_path: Path) -> None:
    (tmp_path / "chain.json").write_text('{"rows": [{"profile": "Hero"}, {"profile": "Bob", "do": "plan"},'
                                         ' {"profile": "Ann", "do": "autopilot"}], "end": "explode"}', encoding="utf-8")
    assert chain.load() == ([chain.Row("Ann", "autopilot")], "stay")
