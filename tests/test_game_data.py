"""The game knowledge shipped in data/: maps, exits, routes, skills and the script guide's examples."""

from __future__ import annotations

import re

import pytest
from conftest import ROOT

from unclefarmer import atlas, travel
from unclefarmer.engine import combat, script


def test_every_map_is_there() -> None:
    assert len(atlas.maps()) == 375


def test_every_exit_leads_to_a_known_map() -> None:
    maps = atlas.maps()
    dangling = [(mid, e["dest"]) for mid, m in maps.items() for e in m["exits"]
                if e["dest"] is not None and e["dest"] not in maps]
    assert dangling == []


@pytest.mark.parametrize("name, mid", [("central plains", 1041), ("Orcus Tribe", 5301), ("Wolf Prairie", 5121)])
def test_maps_by_name(name: str, mid: int) -> None:
    assert atlas.resolve(name) == mid


def test_a_farming_map_has_a_monster_area() -> None:
    mid = atlas.resolve("Wolf Prairie")
    assert mid is not None
    area = atlas.monster_area(mid)
    assert area is not None
    x1, y1, x2, y2 = area
    assert x1 < x2 and y1 < y2


def test_a_route_across_the_world() -> None:
    legs = travel.plan(1041, (196, 199), 5301)
    assert legs
    assert "Orcus Tribe" in travel.describe(legs)[-1]


def test_every_class_has_its_skills() -> None:
    know = combat.knowledge()
    assert set(know) == {"Warrior", "Orc", "Assassin", "Hunter", "Mage", "Elf", "Priest", "Witcher"}
    for cls, skills in know.items():
        assert sum(1 for s in skills if s["role"] == "base_attack") == 1, cls


def test_talent_texts_are_there() -> None:
    texts = combat.talent_texts()
    assert "Cleave Combo" in texts and "Sweep Attack" in texts["Cleave Combo"]


def guide_examples() -> list[str]:
    """The script guide's example scripts: indented blocks that aren't syntax tables."""
    with open(f"{ROOT}/docs/SCRIPTING.md", encoding="utf-8") as f:
        text = f.read()
    blocks: list[str] = []
    cur: list[str] = []
    for line in text.split("\n"):
        if line.startswith("    ") or (cur and not line.strip()):
            cur.append(line[4:])
        elif cur:
            blocks.append("\n".join(cur).strip("\n"))
            cur: list[str] = []
    return [b for b in blocks if b.strip() and "<" not in b and not re.search(r"\s{3,}\S", b.split("\n")[0])]


@pytest.mark.parametrize("example", guide_examples())
def test_the_script_guides_examples_read(example: str) -> None:
    assert script.check(example + "\n") is None
