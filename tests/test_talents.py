# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""Farming talents (engine/talents.py): the Reset Talents menu as the game showed it live (2026-11-25,
Guard Captain Suke and Chancellor Bren), played by a pretend talent master."""

from __future__ import annotations

import re
import threading
from typing import Any

import pytest
from conftest import Clock, as_bot

from unclefarmer import settings, travel
from unclefarmer.engine import talents, town
from unclefarmer.engine.botparts.base import CONFIRM, Stopped

# An Elf with 200 talent points; the Elven Fire tree on
LEARNED = [("Turtle Breath", 5), ("Anti-Corrosion", 5), ("Elven Fire", 28), ("Hellfire", 40),
           ("Enhanced Fireworks", 32), ("Energized Fireball", 32), ("Elven Dance", 24), ("Blazing Combo", 32),
           ("Heart of Heat", 32)]
FIRE = {"Turtle Breath", "Elven Fire", "Enhanced Fireworks", "Elven Dance", "Blazing Combo"}
HELL = {"Turtle Breath", "Hellfire", "Energized Fireball", "Elven Dance", "Heart of Heat"}
GIANT_ROCK, TULIP = 1041, 1081


class Game:
    """The agent's answers: `mem skills` and `said`."""

    def __init__(self, bot: "MasterBot") -> None:
        self.bot = bot

    def json(self, line: str) -> dict[str, Any]:
        b = self.bot
        if line == "mem skills":
            return {"talents": [{"id": i, "name": n, "b": int(n in b.on)} for i, (n, _p) in enumerate(LEARNED)]}
        if line.startswith("said"):
            since = int(line.split()[1])
            return {"now": len(b.heard), "said": [{"text": t} for t in b.heard[since:]]}
        raise AssertionError(line)


class MasterBot:
    """A character at a talent master: the master's menu, Reset Talents, the confirmation."""

    def __init__(self, on: set[str], p: dict[str, Any] | None = None, arnebia: int = 10_000_000,
                 points: int = 200, mid: int = GIANT_ROCK, at: tuple[float, float] = (222.0, 170.0)) -> None:
        self.on = set(on)
        self.p: dict[str, Any] = dict(settings.PROFILE_DEFAULTS, **(p or {}))
        self.arnebia, self.points = arnebia, points
        self.map, self.at = mid, at
        self.menu: str | None = None
        self.picks: dict[str, bool] = {}
        self.heard: list[str] = []
        self.lines: list[str] = []
        self.chosen: list[str] = []
        self.visits: list[list[town.Npc]] = []
        self.deaf_rows: set[str] = set()       # rows Enter doesn't switch
        self.short = False                     # the server answers "not enough talent points"
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.a = Game(self)

    # the bot's side
    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def sleep(self, s: float) -> None:
        self.clock.sleep(s)

    def map_id(self) -> int:
        return self.map

    def me(self) -> dict[str, Any]:
        return {"x": self.at[0], "y": self.at[1]}

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": "Arnebia", "count": self.arnebia}]

    def top(self) -> dict[str, Any] | None:
        if self.menu == "master":
            rows = [["Warrior, are you here to reset your Talents or collect rewards?", [2]], ["Reset Talents", [4]],
                    ["Collect Hunting Bounty", [5]]]
        elif self.menu == "reset":
            rows = [["reset talent skills", [1]]]
            rows += [["%s; takes %d talent points" % (n, pts), [i, int(self.picks[n])]] for i, (n, pts) in enumerate(LEARNED)]
            rows += [["You have %d talent points, now it will take {3,1} points, it costs 200 arnebia for every "
                      "point taking" % self.points, None], ["submit", [0]]]
        elif self.menu == "sure":
            rows = [["Are you sure you want to reset?", None], ["Confirm", [-1]], ["cancel", [-2]]]
        else:
            return None
        return {"row": 0, "col": 0, "sig": hash(str(rows)), "rows": [[{"options": [t], "cmd": c}] for t, c in rows]}

    def dialog(self) -> dict[str, Any]:
        top = self.top()
        return {"ok": True, "open": int(top is not None), "top": top}

    def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if (d["open"] > 0) == want_open else None

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        for r, row in enumerate((top or {}).get("rows") or []):
            if any(travel.matches(pattern, t) for t in row[0]["options"]):
                return r, 0
        return None

    def close_dialogs(self) -> None:
        self.menu = None

    def choose(self, pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        top = self.top()
        where = self.find_entry(top, pattern)
        if top is None or where is None:
            self.menu = None
            return None
        text = top["rows"][where[0]][0]["options"][0]
        self.chosen.append(text)
        if self.menu == "master" and text == "Reset Talents":
            self.menu, self.picks = "reset", {n: n in self.on for n, _p in LEARNED}
        elif self.menu == "reset" and text == "submit":
            self.menu = "sure"
        elif self.menu == "reset":
            name = text.split(";")[0]
            if name not in self.deaf_rows:
                self.picks[name] = not self.picks[name]
        elif self.menu == "sure" and re.search(CONFIRM, text):
            self.menu = None
            if self.short:
                self.heard.append("not enough talent points")
                return top["rows"][where[0]][0]
            need = sum(p for n, p in LEARNED if self.picks[n])
            self.arnebia -= need * talents.COST_PER_POINT
            self.on = {n for n, v in self.picks.items() if v}
            self.heard.append("reset complete")
        return top["rows"][where[0]][0]


@pytest.fixture
def visit(monkeypatch: pytest.MonkeyPatch) -> list[Any]:
    """town.visit stood in: it 'walks' to the nearest of the candidates and opens the master's menu."""
    seen: list[Any] = []

    def fake(b: Any, role: str | None, name: str | None = None, candidates: Any = None, back_to: Any = None) -> str:
        seen.append((list(candidates or []), back_to))
        b.menu = "master"
        return "Chancellor Bren"
    monkeypatch.setattr(town, "visit", fake)
    return seen


def farm_profile(farm: set[str], usual: set[str] | None = None, back: bool = False) -> dict[str, Any]:
    return {"farm_talents": True, "farm_talent_list": sorted(farm), "talents_back": back,
            "usual_talent_list": sorted(usual or set())}


# ---------------------------------------------------------------- the menu as the game shows it
def test_the_menu_rows_say_each_talent_its_points_and_whether_it_is_on() -> None:
    b = MasterBot(FIRE)
    b.menu, b.picks = "reset", {n: n in FIRE for n, _p in LEARNED}
    rows, points = talents.menu_rows(b.top())
    assert points == 200
    assert [r.name for r in rows] == [n for n, _p in LEARNED]
    assert {r.name for r in rows if r.on} == FIRE
    assert next(r for r in rows if r.name == "Hellfire").points == 40


def test_the_plan_switches_only_the_rows_that_differ() -> None:
    rows = [talents.Row(i + 1, n, p, n in FIRE) for i, (n, p) in enumerate(LEARNED)]
    target, missing, switch = talents.plan(rows, sorted(HELL) + ["Rampage"])
    assert target == HELL
    assert missing == ["Rampage"]
    assert {r.name for r in switch} == FIRE ^ HELL


def test_nothing_is_asked_for_until_the_box_is_ticked() -> None:
    p = dict(settings.PROFILE_DEFAULTS, farm_talent_list=["Hellfire"])
    assert talents.wanted(p, "farm") is None
    p["farm_talents"] = True
    assert talents.wanted(p, "farm") == ["Hellfire"]
    assert talents.wanted(p, "back") is None             # its own box too


# ---------------------------------------------------------------- before farming
def test_the_talents_for_farming_are_put_on_and_charged_for(visit: list[Any]) -> None:
    b = MasterBot(HELL, farm_profile(FIRE))
    assert talents.for_farming(as_bot(b), back_to=(5281, None)) is True
    assert b.on == FIRE
    need = sum(p for n, p in LEARNED if n in FIRE)
    assert b.arnebia == 10_000_000 - need * 200
    assert visit[0][1] == (5281, None)                       # there and back to the farm
    assert {m[1] for m in visit[0][0]} >= {"Chancellor Bren", "Guard Captain Suke"}
    # only the rows that differ were switched: no untick-everything first
    switched = [c.split(";")[0] for c in b.chosen if ";" in c]
    assert sorted(switched) == sorted(FIRE ^ HELL)
    assert any("talents set" in line and f"{need * 200:,}" in line for line in b.lines)
    assert b.menu is None


def test_talents_already_on_cost_no_trip_and_no_arnebia(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile(FIRE))
    assert talents.for_farming(as_bot(b)) is True
    assert visit == [] and b.arnebia == 10_000_000
    assert any("already on" in line for line in b.lines)


def test_a_ticked_talent_the_character_never_learned_doesnt_cause_a_trip(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile(FIRE | {"Rampage"}))
    assert talents.for_farming(as_bot(b)) is True
    assert visit == []


def test_none_of_the_ticked_talents_learned_changes_nothing(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile({"Rampage"}))
    assert talents.for_farming(as_bot(b)) is False
    assert visit == [] and b.on == FIRE


def test_too_many_points_are_refused_before_paying(visit: list[Any]) -> None:
    b = MasterBot(HELL, farm_profile(FIRE | {"Hellfire"}), points=150)
    assert talents.for_farming(as_bot(b)) is False
    assert b.on == HELL and b.arnebia == 10_000_000
    assert "sure" not in (b.menu or "") and not any(c == "submit" for c in b.chosen)
    assert any("talent points" in line for line in b.lines)


def test_too_little_arnebia_is_refused_before_paying(visit: list[Any]) -> None:
    b = MasterBot(HELL, farm_profile(FIRE), arnebia=1000)
    assert talents.for_farming(as_bot(b)) is False
    assert b.on == HELL and b.arnebia == 1000


def test_the_game_s_not_enough_talent_points_is_said(visit: list[Any]) -> None:
    b = MasterBot(HELL, farm_profile(FIRE))
    b.short = True
    assert talents.for_farming(as_bot(b)) is False
    assert any("not enough talent points" in line for line in b.lines)


def test_a_row_enter_doesnt_switch_is_given_up_without_submitting(visit: list[Any]) -> None:
    b = MasterBot(HELL, farm_profile(FIRE))
    b.deaf_rows = {"Hellfire"}
    assert talents.for_farming(as_bot(b)) is False
    assert "submit" not in b.chosen and b.on == HELL
    assert b.menu is None                      # nothing left open


def test_no_talent_master_reached_farms_with_the_talents_as_they_are(monkeypatch: pytest.MonkeyPatch) -> None:
    def far(*a: Any, **k: Any) -> str:
        raise Stopped("none of the 10 places that could do it can be reached")
    monkeypatch.setattr(town, "visit", far)
    b = MasterBot(HELL, farm_profile(FIRE))
    assert talents.for_farming(as_bot(b)) is False
    assert b.on == HELL
    assert any("farming with the talents as they are" in line for line in b.lines)


# ---------------------------------------------------------------- when the run ends
def test_the_usual_talents_go_back_on_in_a_town_with_a_talent_master(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile(FIRE, HELL, back=True))
    assert talents.put_back(as_bot(b)) is True
    assert b.on == HELL
    assert [m[0] for m in visit[0][0]] == [GIANT_ROCK]        # this map's master only


def test_no_trip_to_put_them_back_from_a_map_with_no_talent_master(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile(FIRE, HELL, back=True), mid=5281, at=(50.0, 50.0))
    assert talents.put_back(as_bot(b)) is False
    assert visit == [] and b.on == FIRE
    assert any("next run" in line for line in b.lines)


def test_no_trip_from_outside_the_town_on_a_map_with_a_talent_master(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile(FIRE, HELL, back=True), mid=GIANT_ROCK, at=(30.0, 30.0))
    assert talents.put_back(as_bot(b)) is False
    assert visit == []


def test_putting_back_needs_its_own_box(visit: list[Any]) -> None:
    b = MasterBot(FIRE, farm_profile(FIRE, HELL, back=False))
    assert talents.put_back(as_bot(b)) is True
    assert visit == [] and b.on == FIRE


def test_the_usual_talents_already_on_cost_nothing(visit: list[Any]) -> None:
    b = MasterBot(HELL, farm_profile(FIRE, HELL, back=True))
    assert talents.put_back(as_bot(b)) is True
    assert visit == [] and b.arnebia == 10_000_000


def test_a_gang_talent_is_one_more_row_switched_and_charged_for_like_the_others() -> None:
    # live, 2026-11-26: Brutal (a gang talent, 12 points) is a row of Reset Talents and counts in the price
    rows = [talents.Row(i + 1, n, p, n in FIRE | {"Brutal"}) for i, (n, p) in enumerate(LEARNED + [("Brutal", 12)])]
    kept, _m, switch = talents.plan(rows, sorted(HELL | {"Brutal"}))
    assert "Brutal" in kept and "Brutal" not in {r.name for r in switch}
    dropped, _m, switch = talents.plan(rows, sorted(HELL))       # not ticked: switched off with the rest
    assert "Brutal" not in dropped and "Brutal" in {r.name for r in switch}


def test_every_talent_master_is_known_and_stands_in_a_town() -> None:
    from unclefarmer import atlas
    found = talents.masters()
    assert len(found) == len(talents.MASTERS)
    for mid, name, x, y in found:
        assert atlas.in_town(mid, x, y) or name == "Master's assistant", name
    assert any(m[0] == TULIP and m[1] == "Guard Captain Suke" for m in found)
