# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""Farming medals in a run: worn before it sets out, the usual ones again at the end of a farming
run that ended by itself, wherever it ended - once, never at the cost of the run's end."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import stand_in

from unclefarmer.engine import medals, runner as R
from unclefarmer.engine.botparts.base import GameLost, Stopped


def a_run(p: dict[str, Any], hp: int = 100) -> tuple[R.Run, list[str]]:
    lines: list[str] = []

    def on_log(line: str, _speak: bool, _run: R.Run) -> None:
        lines.append(line)

    def on_done(_reason: str, _run: R.Run) -> None:
        pass

    def bot_log(line: str, speak: bool = False) -> None:
        lines.append(line)
    run = R.Run("farm map=5281\n", "Night", 7, on_log, on_done)
    run.kind = "plan"
    run.ended_by_itself = True
    run.bot = SimpleNamespace(farmed=True, p=p, me=lambda: {"hp": hp}, log=bot_log)  # pyright: ignore[reportAttributeAccessIssue]
    return run, lines


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(medals, "put_back", stand_in(medals.put_back, lambda b: seen.append("back") or True))
    monkeypatch.setattr(medals, "for_farming", stand_in(medals.for_farming, lambda b: seen.append("farm") or True))
    monkeypatch.setattr(medals, "learn_bonuses", stand_in(medals.learn_bonuses, lambda b: seen.append("learn")))
    monkeypatch.setattr(R, "_write_log", stand_in(R._write_log, lambda line: None))
    return seen


ON = {"farm_medals": True, "medals_back": True}


def test_the_bonuses_are_read_and_the_farming_medals_worn_at_the_start(calls: list[str]) -> None:
    run, _lines = a_run(dict(ON))
    R.Run._farming_medals(run.bot)                       # type: ignore[arg-type]
    assert calls == ["learn", "farm"]


def test_with_the_box_off_only_the_bonuses_are_read(calls: list[str]) -> None:
    run, _lines = a_run({"farm_medals": False})
    R.Run._farming_medals(run.bot)                       # type: ignore[arg-type]
    assert calls == ["learn"]


def test_a_stop_while_the_farming_medals_go_on_is_passed_on(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    # the day's stop raised in a cooldown wait: swallowed, the run farmed on past the day's end
    def day_over(b: Any) -> bool:
        raise Stopped("it's 23:50 - the day's farming is over")
    monkeypatch.setattr(medals, "for_farming", stand_in(medals.for_farming, day_over))
    run, _lines = a_run(dict(ON))
    with pytest.raises(Stopped):
        R.Run._farming_medals(run.bot)                   # type: ignore[arg-type]


def test_a_hand_edited_list_doesn_t_stop_the_run(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def bad(b: Any) -> bool:
        raise ValueError("invalid literal for int() with base 10: 'x'")
    monkeypatch.setattr(medals, "for_farming", stand_in(medals.for_farming, bad))
    run, lines = a_run(dict(ON))
    R.Run._farming_medals(run.bot)                       # type: ignore[arg-type]
    assert any("weren't put on" in line for line in lines)


def test_the_usual_medals_go_back_once(calls: list[str]) -> None:
    run, _lines = a_run(dict(ON))
    run._medals_back()
    run._medals_back()
    assert calls == ["back"]


@pytest.mark.parametrize("why", ["stopped", "not ended by itself", "dead", "box off", "back box off", "tool"])
def test_no_putting_back_after(calls: list[str], why: str) -> None:
    p = dict(ON)
    if why == "box off":
        p["farm_medals"] = False
    if why == "back box off":
        p["medals_back"] = False
    run, _lines = a_run(p, hp=0 if why == "dead" else 100)
    if why == "stopped":
        run.stop()
    if why == "not ended by itself":
        run.ended_by_itself = False
    if why == "tool":
        run.kind = "tool"
    run._medals_back()
    assert calls == []


def test_a_failure_putting_them_back_is_only_said(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def fails(b: Any) -> bool:
        raise Stopped("the game's equipment can't be read")
    monkeypatch.setattr(medals, "put_back", stand_in(medals.put_back, fails))
    run, lines = a_run(dict(ON))
    run._medals_back()
    assert run.ended_by_itself and any("weren't put back" in line for line in lines)


def test_a_game_gone_is_passed_on(monkeypatch: pytest.MonkeyPatch, calls: list[str]) -> None:
    def gone(b: Any) -> bool:
        raise GameLost("the game closed")
    monkeypatch.setattr(medals, "put_back", stand_in(medals.put_back, gone))
    run, _lines = a_run(dict(ON))
    with pytest.raises(GameLost):
        run._medals_back()
