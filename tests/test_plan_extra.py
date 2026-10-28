# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""What tests/test_plan.py leaves uncovered in engine/plan.py: the rest of upgrade()'s old-plan
rules, the ending line, turning a plan into a script, and the file helpers (names/delete/scripts)."""

from __future__ import annotations

import os

import pytest

from unclefarmer import paths
from unclefarmer.engine import plan
from pathlib import Path
from conftest import stand_in
from typing import Any


@pytest.fixture
def dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    plans = tmp_path / "plans"
    scripts = tmp_path / "scripts"
    plans.mkdir()
    scripts.mkdir()
    monkeypatch.setattr(paths, "PLANS", str(plans))
    monkeypatch.setattr(paths, "SCRIPTS", str(scripts))
    return plans, scripts


@pytest.fixture
def label(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plan.atlas, "label", stand_in(plan.atlas.label, lambda mid: "Map %s" % mid))


def a_plan(**over: Any) -> dict[str, Any]:
    p = dict(plan.PLAN_DEFAULTS)
    p["name"] = "Turkey cave"
    p["spots"] = [plan.new_step(5302)]
    p.update(over)
    return p


# ------------------------------------------------------------------ upgrade: the kill rule
def test_an_old_single_step_makes_its_kills_the_plan_kill_limit() -> None:
    p = {"name": "Old", "steps": [{"map": 5302, "kills": 7}], "repeat": False}
    got = plan.upgrade(p)
    assert got["stop_after_kills"] == 7
    assert got["rotation"] == "once"
    assert got["spots"] == [dict(plan.STEP_DEFAULTS, map=5302, minutes=0, kills=0)]


def test_an_old_plan_keeps_a_kill_limit_it_already_has() -> None:
    p = {"name": "Old", "steps": [{"map": 5302, "kills": 7}], "repeat": False,
         "stop_after_kills": 3}
    got = plan.upgrade(p)
    assert got["stop_after_kills"] == 3


def test_a_repeating_old_step_keeps_no_stop_rules() -> None:
    p = {"name": "Old", "steps": [{"map": 5302, "minutes": 45, "kills": 7}], "repeat": True}
    got = plan.upgrade(p)
    assert got.get("stop_after_minutes", 0) == 0 and got.get("stop_after_kills", 0) == 0
    assert got["rotation"] == "round"


def test_upgrade_leaves_a_new_plan_alone() -> None:
    p: dict[str, Any] = {"name": "New", "spots": []}
    assert plan.upgrade(p) == {"name": "New", "spots": []}


# ------------------------------------------------------------------ _q
def test_q_quotes_a_value() -> None:
    assert plan._q("Large Red Potion") == '"Large Red Potion"'


def test_q_turns_inner_double_quotes_into_single_ones() -> None:
    assert plan._q('a "b" c') == '"a \'b\' c"'


def test_q_stringifies_a_number() -> None:
    assert plan._q(5) == '"5"'


# ------------------------------------------------------------------ end_line
@pytest.mark.parametrize("how", ["start", "hearthstone", "safe", "here"])
def test_end_line_simple_endings(how: str) -> None:
    assert plan.end_line({"how": how}) == "end_at %s" % how


def test_end_line_place_without_a_map_falls_back_to_start() -> None:
    assert plan.end_line({"how": "place"}) == "end_at start"
    assert plan.end_line({"how": "place", "map": 0}) == "end_at start"


def test_end_line_place_with_a_map() -> None:
    assert plan.end_line({"how": "place", "map": 5302}) == "end_at 5302"


def test_end_line_place_with_a_cell() -> None:
    assert plan.end_line({"how": "place", "map": 5302, "cell": [11, 22]}) == "end_at 5302 at 11,22"


def test_end_line_unknown_ending_is_start() -> None:
    assert plan.end_line({"how": "somewhere"}) == "end_at start"


def test_end_line_none_is_start() -> None:
    assert plan.end_line(None) == "end_at start"


# ------------------------------------------------------------------ to_script
def test_to_script_a_single_spot(label: str) -> None:
    text = plan.to_script(a_plan())
    assert text == ("# Turkey cave\n"
                    "# Made with the plan editor. Saving it as a script lets you edit it by hand.\n"
                    "\n"
                    "end_at start\n"
                    "\n"
                    "# Map 5302\n"
                    "farm map=5302\n")


def test_to_script_names_a_plan_with_no_name(label: str) -> None:
    assert plan.to_script({"spots": []}).startswith("# plan\n")


def test_to_script_a_single_spot_omits_its_minutes_and_kills(label: str) -> None:
    p = a_plan()
    p["spots"][0]["minutes"] = 30
    p["spots"][0]["kills"] = 12
    text = plan.to_script(p)
    assert "farm map=5302\n" in text
    assert "minutes=" not in text and "kills=" not in text


def test_to_script_stop_rules(label: str) -> None:
    p = a_plan(stop_after_minutes=300, stop_at_clock="06:00", stop_after_kills=10,
               stop_when_item="Large Red Potion", stop_when_item_below=5)
    text = plan.to_script(p)
    assert "stop_when minutes >= 300 : time limit reached" in text
    assert 'stop_when clock == "06:00" : it is 06:00' in text
    assert "stop_when kills >= 10 : kill limit reached" in text
    assert 'stop_when bag("Large Red Potion") < 5 : Large Red Potion is running out' in text


def test_to_script_leaves_out_stop_rules_that_are_off(label: str) -> None:
    text = plan.to_script(a_plan())
    assert "stop_when" not in text


def test_to_script_several_spots_are_repeated_in_turn(label: str) -> None:
    p = a_plan(spots=[plan.new_step(5302), plan.new_step(5121)])
    text = plan.to_script(p)
    assert "repeat\n" in text
    assert "  # Map 5302\n  farm map=5302 minutes=30\n" in text
    assert "  # Map 5121\n  farm map=5121 minutes=30\n" in text
    assert text.endswith("end\n")


def test_to_script_several_spots_write_their_kill_limits(label: str) -> None:
    first = plan.new_step(5302)
    second = plan.new_step(5121)
    second["kills"] = 5
    text = plan.to_script(a_plan(spots=[first, second]))
    assert "farm map=5302 minutes=30" in text
    assert "farm map=5121 minutes=30 kills=5" in text


def test_to_script_several_spots_once_is_not_wrapped(label: str) -> None:
    p = a_plan(spots=[plan.new_step(5302), plan.new_step(5121)], rotation="once")
    text = plan.to_script(p)
    assert "repeat" not in text and "\nend\n" not in text
    assert "\n# Map 5302\nfarm map=5302 minutes=30\n" in text


def test_to_script_writes_every_spot_option(label: str) -> None:
    spot = plan.new_step(5121)
    spot.update(minutes=0, kills=0, patrol="full", area=[1, 2],
                only="Wolf", skip="Bear", elites="avoid")
    text = plan.to_script(a_plan(spots=[spot]))
    assert 'farm map=5121 patrol=full area=1,2 only="Wolf" skip="Bear" elites=avoid\n' in text


def test_to_script_elites_fight_is_the_default_and_left_out(label: str) -> None:
    spot = plan.new_step(5121)
    spot["elites"] = "fight"
    text = plan.to_script(a_plan(spots=[spot]))
    assert "elites=" not in text


def test_to_script_use_a_quote_inside_a_name(label: str) -> None:
    spot = plan.new_step(5121)
    spot["only"] = 'the "big" wolf'
    text = plan.to_script(a_plan(spots=[spot]))
    assert "only=\"the 'big' wolf\"" in text


# ------------------------------------------------------------------ names / delete
def test_names_lists_the_json_plans_sorted(dirs: tuple[Path, Path]) -> None:
    plans, _ = dirs
    (plans / "One.json").write_text("{}", encoding="utf-8")
    (plans / "two.json").write_text("{}", encoding="utf-8")
    (plans / "note.txt").write_text("x", encoding="utf-8")
    assert plan.names() == ["One", "two"]


def test_delete_removes_a_plan(dirs: tuple[Path, Path]) -> None:
    plans, _ = dirs
    (plans / "Gone.json").write_text("{}", encoding="utf-8")
    plan.delete("Gone")
    assert not (plans / "Gone.json").exists()


# ------------------------------------------------------------------ scripts
def test_script_path_is_the_safe_name_under_scripts(dirs: tuple[Path, Path]) -> None:
    _, scripts = dirs
    assert plan.script_path("My Plan") == os.path.join(str(scripts), "My Plan.mwf")


def test_script_path_tames_an_odd_name(dirs: tuple[Path, Path]) -> None:
    got = plan.script_path("a/b:c")
    assert got.endswith("a_b_c.mwf")


def test_script_path_of_an_empty_name() -> None:
    assert plan.script_path("").endswith("unnamed.mwf")


def test_script_names_lists_the_mwf_files_sorted(dirs: tuple[Path, Path]) -> None:
    _, scripts = dirs
    (scripts / "b.mwf").write_text("", encoding="utf-8")
    (scripts / "A.mwf").write_text("", encoding="utf-8")
    (scripts / "x.txt").write_text("", encoding="utf-8")
    assert plan.script_names() == ["A", "b"]


def test_to_script_keeps_the_plans_own_drop_time(label: str) -> None:
    p = a_plan()
    p["drop_keep_minutes"] = 60
    assert "end_at start\nkeep_drop 60\n" in plan.to_script(p)
    p["drop_keep_minutes"] = 0
    assert "keep_drop" not in plan.to_script(p)       # 0: the character's own setting
