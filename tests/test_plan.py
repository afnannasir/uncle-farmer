"""Plans: saved and read back whole, an old one upgraded, and a broken one refused by Pydantic."""

from __future__ import annotations

import json

import pytest

from unclefarmer import paths
from unclefarmer.engine import plan
from pathlib import Path


@pytest.fixture
def plans_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(paths, "PLANS", str(tmp_path))
    return tmp_path


def test_same_file_ignores_case_so_a_case_only_rename_keeps_the_file(plans_dir: Path) -> None:
    assert plan.same_file(plan.path("Turkey cave"), plan.path("TURKEY CAVE"))
    assert not plan.same_file(plan.path("One"), plan.path("Two"))


def test_a_plan_round_trips(plans_dir: Path) -> None:
    p = dict(plan.PLAN_DEFAULTS, name="Turkey cave", spots=[plan.new_step(5302)],
             stop_after_minutes=300, end={"how": "start"})
    plan.save(p)
    got = plan.load("Turkey cave")
    assert got["name"] == "Turkey cave"
    assert got["stop_after_minutes"] == 300
    assert got["spots"] == [dict(plan.STEP_DEFAULTS, map=5302)]


def test_an_old_plan_is_upgraded(plans_dir: Path) -> None:
    old = {"name": "Old", "steps": [{"map": 5302, "minutes": 45}], "repeat": False}
    with open(plans_dir / "Old.json", "w", encoding="utf-8") as f:
        json.dump(old, f)
    got = plan.load("Old")
    assert got["rotation"] == "once"
    assert got["stop_after_minutes"] == 45
    assert got["spots"] == [dict(plan.STEP_DEFAULTS, map=5302, minutes=0, kills=0)]


def test_a_plan_of_the_wrong_shape_is_refused(plans_dir: Path) -> None:
    with open(plans_dir / "Bad.json", "w", encoding="utf-8") as f:
        json.dump({"name": "Bad", "spots": 5}, f)
    with pytest.raises(ValueError):
        plan.load("Bad")
