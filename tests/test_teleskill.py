# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""Teleport skills for travel (engine/teleskill.py, the "skill" leg in unclefarmer/travel.py and
TravelMixin.cast_teleport), shaped on what was recorded by hand 2026-11-13: press the skill's key,
the menu lists the places, choose one, a 10 s channel, the new map on that place's marker - and any
hit breaks the channel with nothing to say so; no skill works inside a safe zone."""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

import pytest
from conftest import stand_in

from unclefarmer import atlas, travel
from unclefarmer.engine import beasts, keyslots, teleskill
from test_travel_part import Bot as TravelBot


# ---------------------------------------------------------------- the planner
MAGE = [("Teleportation", p) for p in travel.SPELLS["Teleportation"]]
ELF = [("Elven Teleportation", p) for p in travel.SPELLS["Elven Teleportation"]]


def test_a_mage_in_a_city_walks_out_of_its_safe_zone_and_teleports() -> None:
    legs, cost = travel.plan(1041, (200, 205), 1121, with_cost=True, spells=MAGE)
    assert legs and legs[0]["do"] == "skill" and legs[0]["place"] == "holy blood square"
    spot = legs[0]["cast_at"]                       # Giant Rock City's zone is 136-265
    assert not atlas.in_safe_zone(1041, spot[0], spot[1])
    walked, _no = travel.plan(1041, (200, 205), 1121, with_cost=True)
    assert cost is not None and walked is not None and _no is not None and cost < _no


def test_outside_a_safe_zone_it_casts_where_it_stands() -> None:
    legs = travel.plan(1041, (134, 200), 1061, spells=MAGE)
    assert legs and legs[0]["do"] == "skill" and "cast_at" not in legs[0]
    assert legs[0]["dest"] == 1061 and legs[0]["lands"] == [66, 136]


def test_an_elf_reaches_the_high_lands() -> None:
    legs = travel.plan(1041, (134, 200), 1561, spells=ELF)
    assert legs and legs[0]["do"] == "skill" and legs[0]["skill"] == "Elven Teleportation"
    assert legs[0]["place"] == "mist gods ruins" and legs[0]["lands"] == [200, 197]


def test_what_failed_is_left_out() -> None:
    legs = travel.plan(1041, (134, 200), 1121, spells=MAGE, avoid={"Teleportation"})
    assert not legs or all(leg["do"] != "skill" for leg in legs)
    legs = travel.plan(1041, (134, 200), 1121, spells=MAGE, avoid={("Teleportation", "holy blood square")})
    assert not legs or all(leg.get("place") != "holy blood square" for leg in legs)


def test_a_place_with_no_known_landing_is_never_planned() -> None:
    assert travel.spell_landing("Teleportation", "Nowhere Square") is None
    assert travel.plan(1041, (134, 200), 1121, spells=[("Teleportation", "nowhere square")]) != \
        travel.plan(1041, (134, 200), 1121, spells=MAGE)
    assert travel.spell_landing("Teleportation", "Violet Square") == travel.SQUARES["violet square"]


def test_the_menu_pattern_takes_the_whole_name() -> None:
    east = travel.spell_choice("east sealing tower")
    assert re.search(east, "East Sealing Tower", re.I) and not re.search(east, "West Sealing Tower", re.I)


def test_the_recorded_places_sit_on_their_markers() -> None:
    # the code the game sends is map * 1000 + marker; the planner's landing is that marker's cell
    assert travel.SPELLS["Teleportation"]["giant rock square"] == (1041, 200, 205)
    assert travel.SPELLS["Elven Teleportation"]["west sealing tower"] == (1761, 100, 100)
    assert len(travel.SPELLS["Elven Teleportation"]) == 9 and len(travel.SPELLS["Teleportation"]) == 7


# ---------------------------------------------------------------- what a character has
class Skills:
    def __init__(self, names: list[str]) -> None:
        self.names = names

    def json(self, line: str) -> dict[str, Any]:
        return {"combat": [{"name": n, "id": i} for i, n in enumerate(self.names)]} if line == "mem skills" else {}


class Who:
    def __init__(self, names: list[str], **p: Any) -> None:
        self.a = Skills(names)
        self.p: dict[str, Any] = dict(p)
        self.character = "Someone"
        self.walk_only = False


@pytest.fixture(autouse=True)
def own_profiles(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(teleskill.paths, "PROFILES", str(tmp_path))
    return tmp_path


def test_options_are_the_learned_skills_places() -> None:
    w = Who(["Fireball", "Teleportation"])
    opts = teleskill.options(w)  # pyright: ignore[reportArgumentType]
    assert ("Teleportation", "giant rock square") in opts and all(s == "Teleportation" for s, _p in opts)
    assert teleskill.options(Who(["Fireball"])) == []  # pyright: ignore[reportArgumentType]
    assert teleskill.options(Who(["Teleportation"], use_teleport_skills=False)) == []  # pyright: ignore[reportArgumentType]


def test_the_walking_assistant_plans_with_the_teleport_skills_too() -> None:
    w = Who(["Elven Teleportation"])
    w.walk_only = True                  # the way to a dungeon, or Walk to
    assert ("Elven Teleportation", "west sealing tower") in teleskill.options(w)  # pyright: ignore[reportArgumentType]
    w.p["use_teleport_skills"] = False
    assert teleskill.options(w) == []  # pyright: ignore[reportArgumentType]


def test_the_way_to_white_bone_cave_starts_with_the_elves_teleport() -> None:
    # from Tulip City's field: West Sealing Tower lands on ancient Sealed Land, near the entrance
    spells = [("Elven Teleportation", p) for p in travel.SPELLS["Elven Teleportation"]]
    legs, cost = travel.plan(1081, (342, 161), 1761, (61, 110), with_cost=True, spells=spells)
    _walked, on_foot = travel.plan(1081, (342, 161), 1761, (61, 110), with_cost=True)
    assert legs and legs[0]["do"] == "skill" and legs[0]["place"] == "west sealing tower"
    assert cost is not None and on_foot is not None and cost < on_foot / 3


def test_the_menu_teaches_a_characters_own_list(own_profiles: Path) -> None:
    w = Who(["Teleportation"])
    top = {"rows": [[{"text": "Sunset Square"}], [{"text": "Giant rock square"}], [None]]}
    seen = teleskill.menu_places(top)
    assert seen == ["sunset square", "giant rock square"]
    teleskill.learn(w, "Teleportation", seen + ["somewhere unknown"])  # pyright: ignore[reportArgumentType]
    assert teleskill.places(w, "Teleportation") == ["sunset square", "giant rock square"]  # pyright: ignore[reportArgumentType]
    data = json.loads((own_profiles / teleskill.LEARNED).read_text(encoding="utf-8"))
    assert data == {"Someone": {"Teleportation": ["sunset square", "giant rock square"]}}


def test_the_game_puts_the_names_in_the_options(own_profiles: Path) -> None:
    """An Elf's menu as the game sent it (2026-11-20): every name in "options", text and raw empty."""
    names = ["Soul Burial Village", "Plague Abyss", "Orchid woods Village", "City of the Ancient tower",
             "mist gods Ruins", "West Sealing Tower", "Hermit Camp", "East Sealing Tower", "portal of the demon world"]
    rows = [[{"flags": 0, "raw": None, "text": None, "options": ["Please select a location to teleport"]}]]
    rows += [[{"flags": 264, "raw": None, "text": None, "options": [n], "cmd": [1261, 0]}] for n in names]
    seen = teleskill.menu_places({"kind": 5, "rows": rows})
    assert seen[1:] == [n.lower() for n in names]
    w = Who(["Elven Teleportation"])
    teleskill.learn(w, "Elven Teleportation", seen)  # pyright: ignore[reportArgumentType]
    assert teleskill.places(w, "Elven Teleportation") == [n.lower() for n in names]  # pyright: ignore[reportArgumentType]


# ---------------------------------------------------------------- casting it
class Book:
    """A skill book with Teleportation on page 2, key 3."""

    def __init__(self, skill: str = "Teleportation") -> None:
        self.slots: dict[int, list[dict[str, Any]]] = {1: [{"name": skill, "key": "e", "still": "e", "slot": 13}]}
        self.at = 0
        self.flips: list[int] = []

    def page(self) -> int:
        return self.at

    def flip_to(self, want: int) -> bool:
        self.flips.append(want)
        self.at = want
        return True


class Caster(TravelBot):
    def __init__(self) -> None:
        super().__init__()
        self.skillbook: Any = Book()
        self.mp: tuple[float, float] | None = (900.0, 1000.0)
        self.hitting: list[set[int]] = []
        self.character = "Someone"
        self._map = 1041
        self._me = {"x": 134.0, "y": 200.0, "hp": 100}
        self.menu: list[str] | None = ["Sunset Square", "Giant rock square", "holy blood square"]
        self.lands_on: int | None = 1121

        def wait_map_change(mid: int, seconds: float) -> bool:
            if self.lands_on is None:
                return False
            self._map = self.lands_on
            return True
        self.wait_map_change = wait_map_change

    def mp_now(self, me: dict[str, Any], now: float) -> tuple[float, float] | None:
        return self.mp

    def attackers(self) -> set[int]:
        return self.hitting.pop(0) if self.hitting else set()

    def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None:
        if self.menu is None:
            return None
        rows = [[{"flags": 0, "raw": None, "text": None, "options": ["Please select a location to teleport"]}]]
        rows += [[{"flags": 264, "raw": None, "text": None, "options": [t], "cmd": [1061, 0]}] for t in self.menu]
        return {"open": 1, "top": {"kind": 5, "rows": rows}}


@pytest.fixture(autouse=True)
def no_beasts(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    offs: list[str] = []
    monkeypatch.setattr(beasts, "off", stand_in(beasts.off, lambda b: offs.append("off")))
    return offs


LEG: dict[str, Any] = {"do": "skill", "skill": "Teleportation", "place": "holy blood square",
                       "choose": [travel.spell_choice("holy blood square")], "dest": 1121, "lands": [62, 165]}


def test_it_presses_the_key_chooses_the_place_and_arrives(no_beasts: list[str], own_profiles: Path) -> None:
    b = Caster()
    assert b.cast_teleport(dict(LEG)) is True
    assert b.keys == ["e"] and b.chosen == [LEG["choose"][0]] and b.map_id() == 1121
    assert b.skillbook.flips == [1, 0]               # its page, then back to the first
    assert no_beasts == ["off"]                     # a Swift beast's skills are off
    assert teleskill.places(b, "Teleportation") == ["sunset square", "giant rock square", "holy blood square"]  # pyright: ignore[reportArgumentType]


def test_in_a_safe_zone_it_walks_out_first(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Caster()
    b._me = {"x": 200.0, "y": 205.0, "hp": 100}      # Giant rock square

    def walk(cell: tuple[float, float], map_change: bool = False, near: Any = None) -> str:
        b.walked.append(tuple(cell))
        b._me = {"x": cell[0], "y": cell[1], "hp": 100}
        return "arrived"
    b.walk_to = walk
    assert b.cast_teleport(dict(LEG)) is True
    assert b.walked and not atlas.in_safe_zone(1041, *b.walked[0])


def test_a_hit_breaks_the_channel_and_it_fights_first() -> None:
    b = Caster()
    b.lands_on = None                               # the map never changes
    assert b.cast_teleport(dict(LEG)) is None
    assert "fight_back" in b.lines and b.closed >= 1


def test_attacked_before_casting_it_fights_and_waits() -> None:
    b = Caster()
    b.hitting = [{-5}, {-5}]                        # still on us after fighting back
    assert b.cast_teleport(dict(LEG)) is None
    assert b.keys == [] and "fight_back" in b.lines


def test_not_enough_mp_goes_another_way() -> None:
    b = Caster()
    b.mp = (200.0, 1000.0)                          # the cast costs 30%
    assert b.cast_teleport(dict(LEG)) is False and b.keys == []


def test_a_place_the_menu_doesnt_have_is_dropped(own_profiles: Path) -> None:
    b = Caster()
    b.menu = ["Sunset Square", "Giant rock square"]   # a lower skill level
    assert b.cast_teleport(dict(LEG)) is False and b.chosen == []
    assert "holy blood square" not in teleskill.places(b, "Teleportation")  # pyright: ignore[reportArgumentType]


def test_no_menu_is_another_try() -> None:
    b = Caster()
    b.menu = None
    assert b.cast_teleport(dict(LEG)) is None


def test_off_every_key_it_is_put_on_an_empty_one(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Caster()
    b.skillbook = Book("Fireball")
    asked: list[str] = []

    def put(bot: Any, name: str) -> bool:
        asked.append(name)
        bot.skillbook = Book()
        return True
    monkeypatch.setattr(keyslots, "put_skill", put)
    assert b.cast_teleport(dict(LEG)) is True and asked == ["Teleportation"]
    monkeypatch.setattr(keyslots, "put_skill", lambda bot, name: False)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    b = Caster()
    b.skillbook = Book("Fireball")
    assert b.cast_teleport(dict(LEG)) is False


def test_broken_tries_leave_the_skill_out_of_the_trip() -> None:
    b = Caster()
    b.lands_on = None
    avoid: set[Any] = set()
    failed: dict[Any, int] = {}
    for _i in range(Caster.SPELL_TRIES):
        b._take_spell(dict(LEG), avoid, failed)
    assert "Teleportation" in avoid
    b = Caster()
    b.menu = ["Sunset Square"]
    avoid = set()
    b._take_spell(dict(LEG), avoid, {})
    assert ("Teleportation", "holy blood square") in avoid


def test_a_skill_on_no_key_is_left_out_whole_at_the_first_try(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(keyslots, "put_skill", lambda bot, name: False)  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    b = Caster()
    b.skillbook = Book("Fireball")
    avoid: set[Any] = set()
    b._take_spell(dict(LEG), avoid, {})
    assert avoid == {"Teleportation"}


def test_out_of_a_gang_base_on_foot_before_any_teleport(monkeypatch: pytest.MonkeyPatch) -> None:
    leg = {"do": "exit", "map": 4001, "exit": 0, "dest": None}
    planned: list[Any] = []

    def plan(*a: object, **kw: object) -> list[dict[str, Any]]:
        planned.append(a)
        return [{"do": "map"}]
    monkeypatch.setattr(travel, "gang_exit", lambda mid: (0, {}))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    monkeypatch.setattr(travel, "way_out", lambda mid, cell, avoid=(): ([leg], 1.0))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    monkeypatch.setattr(travel, "plan", plan)
    b = Caster()
    assert b._plan_legs(4001, 1081, None, set()) == [leg] and not planned
    assert b._plan_legs(4001, 4001, (40.0, 40.0), set()) == [{"do": "map"}]    # a walk inside the base


def test_a_gang_exit_that_can_t_be_used_leaves_the_normal_plan(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(travel, "gang_exit", lambda mid: (0, {}))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    monkeypatch.setattr(travel, "way_out", lambda mid, cell, avoid=(): (None, None))  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    monkeypatch.setattr(travel, "plan", lambda *a, **kw: [{"do": "map"}])  # pyright: ignore[reportUnknownLambdaType, reportUnknownArgumentType]
    assert Caster()._plan_legs(4001, 1081, None, {"gang_exit"}) == [{"do": "map"}]


def test_travel_plans_with_the_characters_teleports(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Caster()
    b.a = Skills(["Teleportation"])                 # pyright: ignore[reportAttributeAccessIssue]
    seen: list[Any] = []

    def plan(*a: object, **kw: object) -> list[dict[str, Any]]:
        seen.append(kw.get("spells"))
        return []
    monkeypatch.setattr(travel, "plan", plan)
    b._travel(1121, None, set())
    assert seen and ("Teleportation", "holy blood square") in seen[0]  # pyright: ignore[reportOperatorIssue]
