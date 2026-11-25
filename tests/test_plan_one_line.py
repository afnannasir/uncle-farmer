"""A plan's texts stay on their own script line: a line break in a shared plan's name (or its
stop time, its stop item, a spot's only/skip/patrol) used to become a script command of its own."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from unclefarmer.engine import plan, script

SNEAKY = 'Wolves\nmarket buy item="Gold Bar" count=50 max=9999999'


def a_plan(**changes: Any) -> dict[str, Any]:
    p = dict(plan.PLAN_DEFAULTS, name="Wolves", spots=[plan.new_step(5281)])
    p.update(changes)
    return p


def commands(p: dict[str, Any]) -> list[str]:
    return [line.split()[0] for line in plan.to_script(p).splitlines()
            if line.strip() and not line.lstrip().startswith("#")]


@pytest.mark.parametrize("changes", [
    {"name": SNEAKY},
    {"stop_when_item": 'Medium Red Potion\nclean_bag', "stop_when_item_below": 5},
    {"stop_at_clock": '04:30\nclean_bag'},
    {"spots": [dict(plan.new_step(5281), only='wolf"\nclean_bag')]},
    {"spots": [dict(plan.new_step(5281), skip="wolf\rclean_bag")]},
    {"spots": [dict(plan.new_step(5281), patrol="random\nclean_bag")]},
    {"name": "Wolves clean_bag"},               # str.splitlines splits on this one too
])
def test_no_plan_text_becomes_a_script_line(changes: dict[str, Any]) -> None:
    p = a_plan(**changes)
    assert "clean_bag" not in commands(p) and "market" not in commands(p)
    assert set(commands(p)) <= {"end_at", "stop_when", "farm"}


def test_an_ordinary_plan_is_written_as_before() -> None:
    p = a_plan(stop_at_clock="04:30", stop_when_item="Medium Red Potion", stop_when_item_below=5,
               spots=[dict(plan.new_step(5281), only="wolf", patrol="points", points=[[10, 12]])])
    text = plan.to_script(p)
    assert 'stop_when clock == "04:30" : it is 04:30' in text
    assert 'stop_when bag("Medium Red Potion") < 5 : Medium Red Potion is running out' in text
    assert 'farm map=5281 patrol=points points=10,12 only="wolf"' in text
    script.check(text)                                # still a valid script


def test_a_stop_time_that_isnt_a_time_writes_no_rule() -> None:
    assert "clock" not in plan.to_script(a_plan(stop_at_clock="soon"))
    assert "clock" not in plan.to_script(a_plan(stop_at_clock="25:00"))


def write(tmp_path: Path, p: dict[str, Any]) -> str:
    path = tmp_path / "shared.unclefarmer-plan.json"
    path.write_text(json.dumps({"format": plan.PLAN_EXPORT_FORMAT, "version": plan.PLAN_EXPORT_VERSION,
                                "plan": p}), encoding="utf-8")
    return str(path)


@pytest.mark.parametrize("changes,why", [
    ({"name": SNEAKY}, "line break in: name"),
    ({"spots": [dict(plan.new_step(5281), only="wolf\nclean_bag")]}, "line break in: only"),
    ({"stop_at_clock": "half past four"}, "stop time isn't a time"),
    ({"spots": [dict(plan.new_step(5281), patrol="zigzag")]}, "unknown patrol: zigzag"),
])
def test_an_import_with_a_slipped_in_line_is_refused(tmp_path: Path, changes: dict[str, Any], why: str) -> None:
    with pytest.raises(ValueError, match=why):
        plan.read_import(write(tmp_path, a_plan(**changes)))


def test_an_ordinary_import_still_goes_in(tmp_path: Path) -> None:
    got = plan.read_import(write(tmp_path, a_plan(stop_at_clock="4:30")))
    assert got["name"] == "Wolves" and got["stop_at_clock"] == "4:30"
