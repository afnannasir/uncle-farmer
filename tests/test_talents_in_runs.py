# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""Farming talents in a run: on before it sets out (a trip there and back to the farm, even from
the farm), the usual ones back at the end of a farming run that ended by itself - once."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from conftest import stand_in

from unclefarmer.engine import beasts, runner as R, talents, triggers
from unclefarmer.engine.botparts.base import GameLost, Stopped


def a_run(p: dict[str, Any], hp: int = 100, mid: int = 1081) -> tuple[R.Run, list[str]]:
    lines: list[str] = []

    def on_log(line: str, _speak: bool, _run: R.Run) -> None:
        lines.append(line)

    def on_done(_reason: str, _run: R.Run) -> None:
        pass
    run = R.Run("farm map=5281\n", "Night", 7, on_log, on_done)
    run.kind = "plan"
    run.ended_by_itself = True
    def bot_log(line: str, speak: bool = False) -> None:
        lines.append(line)
    run.bot = SimpleNamespace(farmed=True, p=p, me=lambda: {"hp": hp}, map_id=lambda: mid,  # pyright: ignore[reportAttributeAccessIssue]
                              log=bot_log)
    return run, lines


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    seen: list[Any] = []
    monkeypatch.setattr(talents, "put_back", stand_in(talents.put_back, lambda b: seen.append("back") or True))
    monkeypatch.setattr(talents, "for_farming",
                        stand_in(talents.for_farming, lambda b, back_to=None: seen.append(("farm", back_to)) or True))
    monkeypatch.setattr(beasts, "off", stand_in(beasts.off, lambda b: None))
    monkeypatch.setattr(R, "_write_log", stand_in(R._write_log, lambda line: None))
    return seen


ON = {"farm_talents": True, "talents_back": True}


def test_the_usual_talents_go_back_once_after_a_farming_run_that_ended_by_itself(calls: list[Any]) -> None:
    run, _lines = a_run(dict(ON))
    run._talents_back()
    run._talents_back()
    assert calls == ["back"]


@pytest.mark.parametrize("why", ["stopped", "not ended by itself", "dead", "box off", "back box off", "tool"])
def test_no_putting_back_after(calls: list[Any], why: str) -> None:
    p = dict(ON)
    if why == "box off":
        p["farm_talents"] = False
    if why == "back box off":
        p["talents_back"] = False
    run, _lines = a_run(p, hp=0 if why == "dead" else 100)
    if why == "stopped":
        run.stop()
    if why == "not ended by itself":
        run.ended_by_itself = False
    if why == "tool":
        run.kind = "tool"
    run._talents_back()
    assert calls == []


@pytest.mark.parametrize("first", [5281, 1081, triggers.HERE, None])
def test_the_talents_for_farming_trip_goes_there_and_back_to_the_first_farm(
        calls: list[Any], monkeypatch: pytest.MonkeyPatch, first: int | None) -> None:
    run, _lines = a_run(dict(ON))
    monkeypatch.setattr(triggers, "first_farm_map", stand_in(triggers.first_farm_map, lambda nodes, subs=None, done=None: first))
    R.Run._farming_talents(run.bot, SimpleNamespace(main=[], subs={}))      # type: ignore[arg-type]
    # from Tuneful Wetland to Margit: back to Margit; already on the farm (or farming here): nearest from here
    assert calls == [("farm", (5281, None) if first == 5281 else None)]


def test_the_talents_trip_is_made_from_the_farm_too(calls: list[Any]) -> None:
    run, _lines = a_run(dict(ON), mid=5281)
    R.Run._farming_talents(run.bot, SimpleNamespace(main=[], subs={}))      # type: ignore[arg-type]
    assert calls and calls[0][0] == "farm"


def test_a_death_on_the_way_to_the_master_lets_the_run_go_on(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    def died(b: Any, back_to: Any = None) -> bool:
        raise R.Respawned()
    monkeypatch.setattr(talents, "for_farming", stand_in(talents.for_farming, died))
    run, lines = a_run(dict(ON))
    R.Run._farming_talents(run.bot, SimpleNamespace(main=[], subs={}))   # type: ignore[arg-type]
    assert any("died on the way" in line for line in lines)


@pytest.mark.parametrize("error", [Stopped("the game's dialogs can't be read"), R.Respawned()])   # (a bug: carry_on, fatal in tests)
def test_putting_the_talents_back_never_costs_the_end_of_farming(monkeypatch: pytest.MonkeyPatch, calls: list[Any],
                                                                  error: Exception) -> None:
    def fails(b: Any) -> bool:
        raise error
    monkeypatch.setattr(talents, "put_back", stand_in(talents.put_back, fails))
    run, lines = a_run(dict(ON))
    run._talents_back()                                   # no raise: the run still ended by itself
    assert run.ended_by_itself and any("weren't put back" in line for line in lines)


def test_a_game_gone_while_putting_them_back_is_passed_on(monkeypatch: pytest.MonkeyPatch, calls: list[Any]) -> None:
    def gone(b: Any) -> bool:
        raise GameLost("the game closed")
    monkeypatch.setattr(talents, "put_back", stand_in(talents.put_back, gone))
    run, _lines = a_run(dict(ON))
    with pytest.raises(GameLost):
        run._talents_back()


def test_no_talent_trip_when_the_box_is_off(calls: list[Any]) -> None:
    run, _lines = a_run({"farm_talents": False})
    R.Run._farming_talents(run.bot, SimpleNamespace(main=[], subs={}))      # type: ignore[arg-type]
    assert calls == []
