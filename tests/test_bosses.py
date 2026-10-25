"""Field bosses are never targets, nor is anything near one, and the bot
walks off when one comes within boss_distance steps."""

import contextlib

from conftest import ME
from test_wrong_target import Fighter

from unclefarmer.engine import bosses


def mob(i, name, x, y=100.0, hp=1000):
    return {"id": i, "kind": "monster", "side": -2, "name": name, "x": x, "y": y, "hp": hp, "maxhp": hp}


def test_the_boss_list_is_the_named_field_bosses_only():
    n = bosses.names()
    assert n["pig-face bat king"] == 20
    for boss in ("man-wolf caesar", "monkey king wukong", "demonized barbarian tyrant", "lich king",
                 "vampire lord dracula", "dark priest", "meteor queen elsa", "fire lord", "pharaoh tar",
                 "ice queen los", "elven king erth", "whale monster bigger"):
        assert boss in n, boss
    for other in ("year beast", "golden pig", "super pharaoh", "captain", "hell wraith"):
        assert other not in n, other      # event and dungeon super monsters
    assert bosses.is_boss(mob(-1, "Pig-Face  Bat King", 0))
    assert not bosses.is_boss(mob(-1, "Crimson Scorpion", 0))
    assert not bosses.is_boss({"kind": "player", "name": "Death Worm"})


def test_no_boss_and_nothing_near_one_is_a_target():
    near_boss = mob(-2, "Wolf", 112.0)
    far = mob(-3, "Wolf", 60.0)
    boss = mob(-1, "Death Worm", 120.0, hp=900000)
    b = Fighter([boss, near_boss, far], {"id": ME, "x": 100.0, "y": 100.0})
    b.p["boss_distance"] = 40
    b.walk_dist = lambda snap, e, limit=None: abs(e["x"] - 100.0)
    assert [e["id"] for e in b.targets(b.snap())] == [-3]
    b.p["boss_distance"] = 0                              # keeping away switched off
    assert {e["id"] for e in b.targets(b.snap())} >= {-2, -3}


class Walker:
    def __init__(self, snap, cells):
        self.p = {"boss_distance": 40}
        self.s, self.cells = snap, list(cells)
        self.lines, self.walked = [], []

    def log(self, line, speak=False):
        self.lines.append(line)

    def snap(self):
        return self.s

    def random_cell(self, map_id, area, me, radius=40):
        return self.cells.pop(0) if self.cells else None

    def walk_to(self, cell, near=None):
        self.walked.append(cell)
        return "arrived"

    def timed(self, phase):
        return contextlib.nullcontext()


def test_a_boss_close_by_sends_us_to_the_spot_furthest_from_it():
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 110.0, hp=900000)]}
    w = Walker(snap, [(105, 100), (40, 100), (90, 100)])
    assert bosses.keep_away(w, snap, 5121, None)
    assert w.walked == [(40, 100)]
    assert w.lines == ["Death Worm (a boss) is 10 steps away - keeping 40 steps from it"]


def test_a_boss_far_off_or_the_option_off_changes_nothing():
    snap = {"self": {"id": ME, "x": 100.0, "y": 100.0},
            "entities": [mob(-1, "Death Worm", 150.0, hp=900000)]}
    w = Walker(snap, [(40, 100)])
    assert not bosses.keep_away(w, snap, 5121, None)
    snap["entities"][0]["x"] = 105.0
    w.p["boss_distance"] = 0
    assert not bosses.keep_away(w, snap, 5121, None) and w.walked == []
