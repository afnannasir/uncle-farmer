"""Tools, XP loser (engine/xploss.py) against a pretend game shaped like game.i's hostility check:
a guard (side 0, "Guard", not protected) can only be hit in all mode (PK mode 5), and a guard that
was hit strikes back until we fall; each death costs experience, so the sheet's "upgrading
requires N experience" grows - until the level's start, where dying costs nothing more."""

from __future__ import annotations

import json
import math
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from unclefarmer.engine import script, triggers, xploss
from unclefarmer.engine.bot import Respawned, Stopped
from conftest import as_bot, returning
from unclefarmer.clock import Waitable

ME = 1234
REAL_SAFE_ZONES = xploss.atlas.safe_zones   # the game's own map table, for the one test that reads it
CITY = 1041


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def time(self) -> float:
        return self.now

    def wait(self, event: Waitable, seconds: float) -> bool:
        self.now += seconds
        return False


class Agent:
    def __init__(self, game: Game) -> None:
        self.g = game

    def json(self, line: str) -> dict[str, Any]:
        if line == "mem modes":
            return {"pk": self.g.pk, "pk_locked": self.g.pk_locked, "target": 2}
        return {"error": "unknown"}

    def send(self, line: str) -> str:
        verb, _sp, rest = line.partition(" ")
        if verb == "select":
            mid = int(rest)
            e = next((e for e in self.g.entities if e["id"] == mid), None)
            if e is None:
                return "error: %d isn't in sight" % mid
            if self.g.wall_beyond is not None and math.hypot(e["x"] - self.g.x, e["y"] - self.g.y) > self.g.wall_beyond:
                return "refused: %d isn't in a clear line" % mid
            self.g.target = mid
            return "selected"
        return "ok"


def guard(mid: int = -40, x: float = 110.0, y: float = 100.0, **over: Any) -> dict[str, Any]:
    e: dict[str, Any] = {"id": mid, "kind": "other", "side": 0, "name": "Guard", "x": x, "y": y,
                         "hp": 900000, "maxhp": 900000, "type": 0}
    e.update(over)
    return e


class Game:
    """What the XP loser uses of the Bot, with guards that answer a hit in all mode."""

    def __init__(self, xp: int = 1000, loss: int = 500, floor_xp: int = 10**9) -> None:
        self.clock = Clock()
        self.p: dict[str, Any] = {"attack_keys": ["j", "y"], "pk_key": "b", "respawn": "map",
                                  "use_resurrection_crystal": True, "stop_on_deaths": True, "max_deaths": 3,
                                  "hp_below": "auto", "smart_skills": True, "fight_back": True}
        self.a = Agent(self)
        self.stop_event = threading.Event()
        self.xp = xp                       # the sheet's xp_needed
        self.morality: int | None = 2000   # the sheet's Morality (agent 60); None: not on it
        self.loss = loss                   # what one death costs
        self.most = floor_xp               # the level's whole need: dying adds no more past it
        self.pk = 1
        self.pk_locked = False
        self.mid = CITY
        self.x, self.y = 100.0, 100.0
        self.hp = 1000
        self.entities: list[dict[str, Any]] = [guard()]
        self.target: int | None = None
        self.angry: set[int] = set()       # guards striking us
        self.sullen: set[int] = set()      # guards that never strike back
        self.deaths = 0
        self.lines: list[tuple[str, bool]] = []
        self.pressed: list[str] = []
        self.travelled: list[tuple[int, Any]] = []
        self.p_during: list[dict[str, Any]] = []
        self.dying_on_purpose = False
        self.wall_beyond: float | None = None   # select refused (a wall between) further off than this
        self.guards_need_all = False            # guards only in sight in all mode
        self.my_reach = 2.0
        self.riding = False                     # on a Swift beast: its skills are off
        self.hit_from: list[tuple[float, float]] = []   # where we stood at each attack press

    # time passing: angry guards strike
    def sleep(self, s: float) -> None:
        self.clock.now += s
        if self.angry and self.hp > 0:
            self.hp = max(0, self.hp - int(400 * s))

    def check(self) -> None:
        if self.stop_event.is_set():
            raise Stopped("stopped by you")

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append((line, speak))

    def map_id(self) -> int:
        return self.mid

    def me(self) -> dict[str, Any]:
        return {"id": ME, "x": self.x, "y": self.y, "hp": self.hp, "maxhp": 1000}

    def snap(self) -> dict[str, Any]:
        rows = [dict(e) for e in self.entities if self.pk == 5 or not self.guards_need_all]
        return {"self": self.me(), "map": {"id": self.mid}, "entities": rows}

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        sheet: dict[str, Any] = {"xp_needed": self.xp}
        if self.morality is not None:
            sheet["morality"] = self.morality
        return sheet

    def reach(self) -> float:
        return self.my_reach

    def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        self.x, self.y = cell[0] - 1.0, cell[1]
        return "arrived"

    def random_cell(self, map_id: int, area: Any, me: dict[str, Any], radius: int = 40) -> tuple[int, int] | None:
        return (int(me["x"]) + 10, int(me["y"]))

    def travel(self, dst: int, cell: Any = None) -> None:
        self.travelled.append((dst, cell))
        self.mid = dst
        if cell is not None:
            self.x, self.y = float(cell[0]), float(cell[1])

    def key(self, k: str, ms: int = 60) -> None:
        self.pressed.append(k)
        self.p_during.append(dict(self.p))
        if k == self.p["pk_key"]:
            if not self.pk_locked:
                self.pk = (self.pk + 1) % 6
            return
        if k != self.p["attack_keys"][0] or self.target is None or self.riding:
            return
        self.hit_from.append((self.x, self.y))
        if xploss.atlas.in_safe_zone(self.mid, self.x, self.y):
            return                         # "Safe zone disabled skills"
        e = next((e for e in self.entities if e["id"] == self.target), None)
        if e is None or math.hypot(e["x"] - self.x, e["y"] - self.y) > 2.0:
            return
        if self.pk == 5 and not e["type"] & 2 and e["id"] not in self.sullen:
            self.angry.add(e["id"])

    def handle_death(self) -> None:
        self.deaths += 1
        self.log("died on the map (%d so far)" % self.deaths, speak=not self.dying_on_purpose)
        assert self.p["respawn"] == "city" and not self.p["use_resurrection_crystal"]
        self.xp = min(self.most, self.xp + self.loss)
        self.hp, self.angry, self.target = 1000, set(), None
        self.x, self.y = 100.0, 100.0
        raise Respawned()

    def text(self) -> str:
        return "\n".join(line for line, _speak in self.lines)


@pytest.fixture(autouse=True)
def isolated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xploss.paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(xploss.pets, "recall", returning(None))
    monkeypatch.setattr(xploss.town, "nearest", returning(None))

    def off(b: Any) -> None:
        b.riding = False
    monkeypatch.setattr(xploss.beasts, "off", off)
    monkeypatch.setattr(xploss.atlas, "safe_zones", returning([]))   # no towns unless a test sets one


def lose(g: Game, **cfg: Any) -> str:
    return xploss.run(as_bot(g), {**xploss.DEFAULTS, **cfg})


def test_dies_to_the_guard_until_the_next_level_needs_the_goal() -> None:
    g = Game(xp=1000, loss=500)
    assert lose(g, mode="until", until_needed=2500) == "done"
    assert g.deaths == 3 and g.xp == 2500
    assert g.pk == 1                                   # put back as it was
    assert g.p["respawn"] == "map" and g.p["use_resurrection_crystal"] is True   # the profile untouched
    assert "done: the next level needs at least 2500 experience" in g.text()
    assert not any(speak for line, speak in g.lines if line.startswith("died on"))   # deaths aren't news


def test_no_potions_heals_or_fighting_back_while_dying() -> None:
    g = Game()
    lose(g, until_needed=1500)
    during = g.p_during[g.pressed.index("j")]         # the settings at the moment of the hit
    assert during["hp_below"] == 0 and during["smart_skills"] is False and during["fight_back"] is False
    assert during["stop_on_deaths"] is False and during["stop_on_panic"] is False
    assert g.p["hp_below"] == "auto" and g.p["smart_skills"] is True


def test_only_all_mode_lets_the_hit_land() -> None:
    g = Game()
    lose(g, until_needed=1500)
    pk_presses = g.pressed.count("b")
    assert pk_presses == 4 + 2                         # 1 -> 5 for the guard, then 5 -> 1 back
    assert g.pressed.count("j") == 1                   # one hit was enough


def test_lose_this_much() -> None:
    g = Game(xp=1000, loss=400)
    assert lose(g, mode="lose", lose=1000) == "done"
    assert g.xp == 2200 and g.deaths == 3              # 1000 more than at the start, at least


def test_already_there_does_nothing() -> None:
    g = Game(xp=5000)
    assert lose(g, until_needed=3000) == "done"
    assert g.pressed == [] and g.deaths == 0
    assert "nothing to lose" in g.text()


def test_stops_after_the_most_deaths_allowed() -> None:
    g = Game(xp=1000, loss=100)
    assert lose(g, until_needed=10_000, max_deaths=4) == "deaths"
    assert g.deaths == 4 and "stopped after 4 deaths, as you set" in g.text()


def test_stops_when_dying_costs_nothing_more() -> None:
    g = Game(xp=1000, loss=500, floor_xp=1500)         # the level's start: 1500 is all it needs
    assert lose(g, until_needed=5000) == "stalled"
    assert g.deaths == 1 + xploss.STALLS
    assert g.pk == 1


def test_top_level_and_unreadable_sheets_stop_at_once() -> None:
    g = Game(xp=-1)
    with pytest.raises(Stopped, match="top level"):
        lose(g, until_needed=100)
    g = Game()
    g.refresh_sheet = lambda force=False: {}
    with pytest.raises(Stopped, match="character sheet"):
        lose(g, until_needed=100)
    assert g.pressed == []


def test_a_black_name_never_dies_on_purpose() -> None:
    g = Game()
    g.morality = -1000                     # a black name can lose worn gear on death
    with pytest.raises(Stopped, match="black name"):
        lose(g, until_needed=g.xp + 1000)
    assert g.pressed == [] and g.pk == 1
    g = Game()
    g.morality = None                      # an older agent: the window's warning has to do
    lose(g, until_needed=g.xp + 1000)
    assert g.pressed


def test_a_guard_that_wont_fight_is_left_for_another() -> None:
    g = Game()
    g.entities = [guard(-40, x=103), guard(-41, x=130)]
    g.sullen = {-40}
    assert lose(g, until_needed=1500) == "done"
    assert g.pressed.count("j") == xploss.HITS + 1
    assert "the guard didn't strike back - trying another" in g.text()


def test_all_mode_comes_before_looking_for_guards() -> None:
    g = Game()
    g.guards_need_all = True               # the game lists guards as enemies only in all mode
    assert lose(g, until_needed=1500) == "done"
    assert g.deaths == 1 and g.pk == 1


def test_gets_off_the_beast_before_the_hit() -> None:
    g = Game()
    g.riding = True                        # the travel to the guard left us on the horse
    assert lose(g, until_needed=1500) == "done"
    assert g.deaths == 1 and g.pressed.count("j") == 1


def test_a_wall_in_the_way_walks_right_up_to_the_guard() -> None:
    g = Game()
    g.my_reach = 10.0                      # a caster: it would hit from 9.5 tiles
    g.entities = [guard(x=120.0)]
    g.wall_beyond = 3.0                    # from further off a wall is in the line
    assert lose(g, until_needed=1500) == "done"
    assert g.deaths == 1 and "can't select" not in g.text()


def test_a_spot_on_this_map_that_comes_up_empty_still_searches_here(monkeypatch: pytest.MonkeyPatch) -> None:
    xploss.note(CITY, [guard(x=150.0, y=100.0)])       # remembered far across this map
    g = Game()
    g.entities = []

    def nearest(b: Any, role: str | None, candidates: Any = None, back_to: Any = None) -> Any:
        return candidates[0] if candidates else None
    monkeypatch.setattr(xploss.town, "nearest", nearest)
    walks: list[Any] = []
    orig = g.walk_to

    def walk_to(cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        walks.append(cell)
        if not g.entities:
            g.entities = [guard(x=cell[0] + 1.0, y=cell[1])]  # the search walk comes upon one
        return orig(cell, map_change, near)
    g.walk_to = walk_to
    assert lose(g, until_needed=1500) == "done"
    assert g.travelled[0][0] == CITY and walks and g.deaths == 1


def test_city_patrols_are_left_and_the_gate_guard_is_hit_from_outside(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xploss.atlas, "safe_zones", returning([(90, 90, 120, 120)]))
    g = Game()                             # we stand in the town, at 100,100
    g.entities = [guard(-40, x=104), guard(-41, x=125)]   # one walks the streets, one is past the edge
    assert lose(g, until_needed=1500) == "done"
    assert g.deaths == 1 and g.angry == set()
    assert g.hit_from and all(not xploss.atlas.in_safe_zone(CITY, x, y) for x, y in g.hit_from)
    assert xploss.spots() == [(CITY, "Guard", 125.0, 100.0)]   # the street patrol isn't remembered


def test_only_guards_in_a_town_says_none_found(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xploss.atlas, "safe_zones", returning([(90, 90, 120, 120)]))
    monkeypatch.setattr(xploss, "gate_cells", returning([]))
    g = Game()
    g.entities = [guard(-40, x=104)]
    with pytest.raises(Stopped, match="no guard found"):
        lose(g, until_needed=1500)
    assert "j" not in g.pressed


def test_safe_zones_and_town_gates_from_the_map_data(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(xploss.atlas, "safe_zones", REAL_SAFE_ZONES)
    # Giant Rock City on central plains, as the game's map table has it; the gate guards seen
    # live stood just outside its east (x 267) and west (x 134) edges
    assert (136, 136, 265, 265) in xploss.atlas.safe_zones(1041)
    assert [38, 2, 59, 5, "exit_front", ""] in xploss.atlas.safe_zone_rows(1041)   # every z=-3 row, not only towns
    assert xploss.atlas.in_safe_zone(1041, 200, 200) and xploss.atlas.in_safe_zone(1041, 265.4, 200)
    assert not xploss.atlas.in_safe_zone(1041, 267, 204) and not xploss.atlas.in_safe_zone(1041, 134.4, 198.4)
    gates = xploss.gate_cells(1041, {"x": 250.0, "y": 200.0})
    assert len(gates) == 4 and gates[0] == (267, 200)          # the nearest edge first
    assert all(not xploss.atlas.in_safe_zone(1041, x, y) for x, y in gates)
    assert xploss.gate_cells(1041, {"x": 100.0, "y": 100.0}) == []   # not in a town


def test_no_guard_anywhere_says_so() -> None:
    g = Game()
    g.entities = []
    with pytest.raises(Stopped, match="no guard found"):
        lose(g, until_needed=1500)
    assert g.pk == 1 and g.deaths == 0


def test_a_locked_pk_mode_is_said() -> None:
    g = Game()
    g.pk_locked = True
    with pytest.raises(Stopped, match="PK mode can't be changed"):
        lose(g, until_needed=1500)
    assert "j" not in g.pressed


def test_guards_seen_are_remembered_and_visited(monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game()
    lose(g, until_needed=1500)                         # the guard in the city is noted
    assert xploss.spots() == [(CITY, "Guard", 110.0, 100.0)]
    g2 = Game()
    g2.mid = 1561
    g2.entities = []
    chosen: list[Any] = []

    def nearest(b: Any, role: str | None, candidates: Any = None, back_to: Any = None) -> Any:
        if candidates:
            chosen.append(candidates)
            return candidates[0]
        return None
    monkeypatch.setattr(xploss.town, "nearest", nearest)
    orig = g2.travel

    def travel(dst: int, cell: Any = None) -> None:
        orig(dst, cell)
        if dst == CITY:
            g2.entities = [guard()]
    g2.travel = travel
    assert lose(g2, until_needed=1500) == "done"
    assert g2.travelled[0] == (CITY, (110, 100)) and chosen


def test_the_nearest_town_when_nothing_is_known(monkeypatch: pytest.MonkeyPatch) -> None:
    g = Game()
    g.mid, g.entities = 1561, []

    def nearest(b: Any, role: str | None, candidates: Any = None, back_to: Any = None) -> Any:
        return (CITY, "Storehouse", 50.0, 60.0) if role == "storehouse" else None
    monkeypatch.setattr(xploss.town, "nearest", nearest)
    orig = g.travel

    def travel(dst: int, cell: Any = None) -> None:
        orig(dst, cell)
        g.entities = [guard(x=52, y=61)]
    g.travel = travel
    assert lose(g, until_needed=1500) == "done"
    assert g.travelled == [(CITY, (50, 61))]


def test_what_counts_as_a_guard() -> None:
    assert xploss.is_guard(guard())
    assert xploss.is_guard(guard(name="City Guard"))
    assert not xploss.is_guard(guard(type=2))                        # protected: never hit
    assert not xploss.is_guard(guard(side=-2, kind="monster", name="Gate Guard"))   # a bandit monster
    assert not xploss.is_guard(guard(name="Guardian"))
    assert not xploss.is_guard(guard(hp=0))
    assert not xploss.is_guard(guard(name="Summoned Wolf"))


def test_check_only_reads_and_changes_nothing() -> None:
    g = Game()
    assert xploss.run(as_bot(g), {**xploss.DEFAULTS, "until_needed": 3000}, check_only=True) == "checked"
    assert g.pressed == [] and g.deaths == 0
    assert "1 guards in sight" in g.text() and "goal 3000" in g.text()


def test_the_remembered_spots_file_keeps_a_few_per_map(tmp_path: Path) -> None:
    for i in range(xploss.SPOTS_PER_MAP + 5):
        xploss.note(CITY, [guard(x=10.0 * i, y=0)])
    xploss.note(CITY, [guard(x=1.0, y=1.0)])            # beside a known spot: the same one
    data = json.loads((tmp_path / xploss.GUARDS_SEEN).read_text(encoding="utf-8"))
    assert len(data[str(CITY)]) == xploss.SPOTS_PER_MAP


def test_a_trigger_job_and_a_script_command() -> None:
    assert ("lose_xp", "losing experience") in triggers.JOBS
    assert triggers.setting("lose_xp", "after") in triggers.SETTINGS
    main, _subs, _stop = script.parse("lose_xp\nlose_xp check\n")
    assert main[0].kind == "lose_xp" and main[0].opts["check"] is False and main[1].opts["check"] is True
    with pytest.raises(script.ScriptError):
        script.parse("lose_xp now\n")
    g = Game()
    g.p["xp_loser"] = {"mode": "until", "until_needed": 1500}
    assert triggers.run_job(as_bot(g), "lose_xp") == "done" and g.deaths == 1


def test_the_goal() -> None:
    assert xploss.goal({"mode": "until", "until_needed": 900}, 300) == 900
    assert xploss.goal({"mode": "lose", "lose": 900}, 300) == 1200
    assert xploss.goal({"mode": "lose", "lose": -5}, 300) == 300
