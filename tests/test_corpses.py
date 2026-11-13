"""Corpse harvesting (engine/corpses.py) against a pretend game and agent shaped like agent 62's
`cast <skill> <target>`: it casts a learned corpse skill only at a fresh corpse (0 HP) within 20
tiles, the cast comes back as our own cast report, each harvest costs Vitality, and the game says
"lack of vitality" when there is none left. No key is ever pressed and nothing is selected."""

from __future__ import annotations

import math
import threading
from typing import Any

from unclefarmer.engine import corpses
from unclefarmer.engine.bot import Stopped
from conftest import as_bot
from unclefarmer.clock import Waitable

ME = 1234
DRAW_BLOOD = 21
SKILLS: dict[str, Any] = {"combat": [], "talents": [],
                          "profession": [{"id": 13, "name": "Fishing"}, {"id": DRAW_BLOOD, "name": "Draw Blood"}]}


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
        g = self.g
        if line == "mem skills":
            return g.skills
        verb, _sp, rest = line.partition(" ")
        since = int(rest or 0)
        if verb == "casts":
            return {"casts": [c for c in g.casts if c["t"] > since]}
        if verb == "said":
            return {"said": [{"t": t, "text": s} for t, s in g.said if t > since]}
        return {"error": "unknown"}

    def send(self, line: str) -> str:
        self.g.sent.append(line)
        verb, _sp, rest = line.partition(" ")
        if verb == "cast":
            if self.g.agent_has_cast is False:
                return "unknown command: cast"
            skill, target = (int(v) for v in rest.split())
            return self.g.cast(skill, target)
        return "ok"


class Game:
    """What corpse harvesting uses of the Bot, and the game behind the agent's `cast`."""

    def __init__(self, vitality: float | None = 20.0) -> None:
        self.clock = Clock()
        self.p: dict[str, Any] = {"corpse_skill": True, "corpse_keep_vitality": 0}
        self.a = Agent(self)
        self.agent_version = 62
        self.agent_has_cast = True
        self.answers = True           # False: the server never answers a cast
        self.skills: dict[str, Any] = SKILLS
        self.stop_event = threading.Event()
        self.vitality = vitality
        self.entities: list[dict[str, Any]] = []
        self.counted: dict[int, float] = {}
        self.casts: list[dict[str, Any]] = []
        self.said: list[tuple[int, str]] = []
        self.lines: list[tuple[str, bool]] = []
        self.sent: list[str] = []
        self.hostile: set[int] = set()
        self.pet_hostile: set[int] = set()

    def ms(self) -> int:
        return int(self.clock.now * 1000)

    def kill(self, mid: int, x: float = 103.0, y: float = 100.0, name: str = "Wolf") -> None:
        """A monster we killed just now: its corpse at 0 HP, counted as ours."""
        self.entities.append({"id": mid, "name": name, "x": x, "y": y, "hp": 0, "maxhp": 500, "kind": "monster"})
        self.counted[mid] = self.clock.now

    def cast(self, skill: int, target: int) -> str:
        """Agent 62's checks (Memory.corpseRefusal), then the server's answer."""
        if skill < 21:
            return "refused: skill %d isn't a corpse skill" % skill
        if not any(r["id"] == skill for r in self.skills.get("profession", [])):
            return "refused: skill %d isn't one of our profession skills" % skill
        e = next((e for e in self.entities if e["id"] == target), None)
        if e is None:
            return "refused: %d isn't in sight" % target
        if e["hp"] != 0:
            return "refused: %d is no fresh corpse (harvested or dried)" % target
        if math.hypot(e["x"] - 100, e["y"] - 100) > 20:
            return "refused: %d is out of reach" % target
        if math.hypot(e["x"] - 100, e["y"] - 100) > 4.5:
            return "refused: %d is too far to harvest from here" % target
        if not self.answers:
            return "cast %d %d" % (skill, target)
        self.clock.now += 0.3                         # the server's round trip
        if self.vitality is not None and self.vitality <= 0:
            self.said.append((self.ms(), "lack of vitality"))
            return "cast %d %d" % (skill, target)
        if self.vitality is not None:
            self.vitality -= 1
        e["hp"] = -1
        self.casts.append({"t": self.ms(), "caster": ME, "skill": "Draw Blood", "targets": [[target, -1]]})
        return "cast %d %d" % (skill, target)

    # ---- the Bot surface
    def snap(self) -> dict[str, Any]:
        return {"self": {"id": ME, "x": 100.0, "y": 100.0, "hp": 100, "maxhp": 100},
                "entities": [dict(e) for e in self.entities]}

    def sleep(self, s: float) -> None:
        self.clock.now += s

    def check(self) -> None:
        if self.stop_event.is_set():
            raise Stopped("stopped by you")

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append((line, speak))

    def attackers(self) -> set[int]:
        return self.hostile

    def pet_attackers(self) -> set[int]:
        return self.pet_hostile

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        return {} if self.vitality is None else {"vitality": self.vitality}

    def text(self) -> str:
        return "\n".join(line for line, _speak in self.lines)

    def casts_sent(self) -> list[str]:
        return [s for s in self.sent if s.startswith("cast ")]


def harvest(g: Game) -> bool:
    return corpses.harvest(as_bot(g), g.snap())


def test_a_fresh_kill_gets_the_learned_skill_by_its_id() -> None:
    g = Game()
    g.kill(-7)
    assert harvest(g) is True
    assert g.sent == ["cast 21 -7"]                   # no key, no select: the skill's id and the corpse
    assert corpses.state(as_bot(g)).harvested == 1 and g.vitality == 19
    assert "Draw Blood on our kills" in g.text() and "Draw Blood: Wolf" in g.text()
    assert harvest(g) is False                        # once per corpse
    assert g.sent == ["cast 21 -7"]


def test_off_by_default_and_nothing_sent() -> None:
    g = Game()
    g.p = {}
    g.kill(-7)
    assert harvest(g) is False and g.sent == []


def test_only_our_own_fresh_kills_in_reach() -> None:
    g = Game()
    g.entities.append({"id": -1, "name": "Wolf", "x": 101, "y": 100, "hp": 0, "maxhp": 500, "kind": "monster"})
    g.kill(-2, x=100 + corpses.REACH + 1)             # too far
    g.kill(-3)
    g.entities[-1]["hp"] = -1                         # harvested or dried already
    g.kill(-4)
    g.clock.now += corpses.FRESH + 1                  # killed too long ago
    g.kill(-5, x=104)
    g.entities.append({"id": -6, "name": "Wolf", "x": 101, "y": 100, "hp": 300, "maxhp": 500, "kind": "monster"})
    g.counted[-6] = g.clock.now                       # alive (a reused id): no corpse
    assert harvest(g) is True and g.casts_sent() == ["cast 21 -5"]   # someone else's (-1) never


def test_the_nearest_corpse_first() -> None:
    g = Game()
    g.kill(-1, x=104)
    g.kill(-2, x=102)
    harvest(g)
    harvest(g)
    assert g.casts_sent() == ["cast 21 -2", "cast 21 -1"] and corpses.state(as_bot(g)).harvested == 2


def test_an_id_that_dies_again_is_a_new_corpse() -> None:
    g = Game()
    g.kill(-7)
    harvest(g)
    g.entities.clear()
    g.clock.now += 30
    g.kill(-7)                                        # the respawn with that id, killed again
    assert harvest(g) is True and corpses.state(as_bot(g)).harvested == 2


def test_fights_come_first() -> None:
    g = Game()
    g.kill(-7)
    g.hostile = {-9}
    assert harvest(g) is False
    g.hostile, g.pet_hostile = set(), {-9}
    assert harvest(g) is False
    g.pet_hostile = set()
    assert harvest(g) is True and g.casts_sent() == ["cast 21 -7"]


def test_a_refused_corpse_is_left_for_the_next() -> None:
    g = Game()
    g.kill(-7)
    g.cast = lambda skill, target: "refused: -7 isn't in a clear line"
    assert harvest(g) is True
    assert harvest(g) is False                        # not tried again
    assert corpses.state(as_bot(g)).misses == 0
    assert ("Draw Blood not cast at Wolf: isn't in a clear line", False) in g.lines


def test_each_kind_of_refusal_is_logged_once() -> None:
    g = Game()
    g.cast = lambda skill, target: "refused: %d isn't in a clear line" % target
    for mid in (-7, -8, -9):
        g.kill(mid)
        assert harvest(g) is True
    said = [line for line, _speak in g.lines if "not cast at" in line]
    assert said == ["Draw Blood not cast at Wolf: isn't in a clear line"]
    assert corpses.state(as_bot(g)).refusals == {"isn't in a clear line": 3}


def test_vitality_used_up_stops_it_for_the_run() -> None:
    g = Game(vitality=0)
    g.kill(-7)
    assert harvest(g) is False and g.sent == []
    st = corpses.state(as_bot(g))
    assert st.off and ("Vitality is used up for today - no more Draw Blood this run (0 done)", True) in g.lines
    g.vitality = 50                                   # never back on in this run
    g.kill(-8)
    assert harvest(g) is False


def test_the_players_vitality_mark() -> None:
    g = Game(vitality=12)
    g.p["corpse_keep_vitality"] = 10
    for mid in (-1, -2, -3):
        g.kill(mid, x=100 + abs(mid))
    assert harvest(g) and harvest(g)                  # 12 -> 11 -> 10
    assert harvest(g) is False and g.vitality == 10
    assert "Vitality is down to 10" in g.text() and "(2 done)" in g.text()


def test_the_games_lack_of_vitality_ends_it() -> None:
    g = Game(vitality=None)                           # the sheet doesn't say (an old agent)
    g.kill(-7)
    harvest(g)
    assert corpses.state(as_bot(g)).harvested == 1
    g.vitality = 0                                    # the sheet still says nothing ...
    g.refresh_sheet = lambda force=False: {}          # ... but the game is out of it
    g.kill(-8)
    assert harvest(g) is True
    assert corpses.state(as_bot(g)).off and "Vitality is used up for today" in g.text()


def test_casts_the_game_never_answers_are_said_and_tried_again_later() -> None:
    g = Game()
    g.answers = False
    for mid in range(1, corpses.MISSES + 2):
        g.kill(-mid, x=100 + mid * 0.5)
    for _i in range(corpses.MISSES):
        assert harvest(g) is True
    said = [(line, speak) for line, speak in g.lines if "never answered" in line]
    assert len(said) == 1 and said[0][1] and "4 corpses in a row" in said[0][0]
    assert harvest(g) is False                        # resting
    g.clock.now += corpses.REST + 1
    g.answers = True
    g.kill(-50)
    assert harvest(g) is True and corpses.state(as_bot(g)).harvested == 1   # never off for good


def test_no_corpse_skill_is_said_once() -> None:
    g = Game()
    g.skills = {"profession": [{"id": 13, "name": "Fishing"}]}
    g.kill(-7)
    assert harvest(g) is False
    g.kill(-8)
    assert harvest(g) is False
    said = [line for line, speak in g.lines if "no Draw Blood, Skinning or Cut Flesh" in line and speak]
    assert len(said) == 1 and g.sent == []


def test_an_old_agent_is_told_once_and_nothing_sent() -> None:
    g = Game()
    g.agent_version = 61
    g.kill(-7)
    assert harvest(g) is False and g.sent == []
    assert "agent 62 or newer (this game has 61)" in g.text()
    g.kill(-8)
    assert harvest(g) is False
    assert sum("agent 62" in line for line, _speak in g.lines) == 1


def test_an_agent_without_cast_ends_it_for_the_run() -> None:
    g = Game()
    g.agent_version = 0                               # not known: the command's answer tells
    g.agent_has_cast = False
    g.kill(-7)
    assert harvest(g) is False
    assert corpses.state(as_bot(g)).off and "restart the game so the new agent loads" in g.text()


def test_a_skill_forgotten_since_is_read_again() -> None:
    g = Game()
    g.kill(-7)
    harvest(g)
    g.skills = {"profession": [{"id": 22, "name": "Skinning"}]}     # changed at the Guru Elder
    g.kill(-8)
    assert harvest(g) is True                         # refused: not one of ours any more
    g.kill(-9)
    harvest(g)
    assert g.casts_sent()[-1] == "cast 22 -9"


def test_the_corpse_skills_by_name_with_their_ids() -> None:
    assert corpses.skill_of({"profession": [{"id": 22, "name": "Skinning"}]}) == ("Skinning", 22)
    assert corpses.skill_of({"profession": [{"id": 23, "name": "Cut Flesh"}]}) == ("Cut Flesh", 23)
    assert corpses.skill_of({"profession": [{"id": 11, "name": "Mining"}, {"id": 1, "name": "Snuggle"}]}) is None


def test_attacked_while_waiting_leaves_the_corpse() -> None:
    g = Game()
    g.answers = False
    g.kill(-7)
    calls = {"n": 0}

    def attackers() -> set[int]:
        calls["n"] += 1
        return {-9} if calls["n"] > 1 else set()      # nothing at first, then a monster comes
    g.attackers = attackers
    assert harvest(g) is True
    assert corpses.state(as_bot(g)).misses == 0       # not counted as a cast the game ignored


def test_a_corpse_out_of_harvesting_range_is_left_where_it_lies() -> None:
    """Live 2026-11-12: every harvest landed 1.1-4.5 tiles from the corpse; nothing is walked to."""
    g = Game()
    g.kill(-7, x=100 + corpses.REACH + 0.5)
    assert harvest(g) is False and g.sent == []
    g.kill(-8, x=100 + corpses.REACH - 0.2)
    assert harvest(g) is True and g.casts_sent() == ["cast 21 -8"]
