"""Bosses: the named field bosses, which the bot keeps away from.

The game describes every monster as "<name>; level <n> <ordinary | elite | super> monster; ...".
Not every super monster is a field boss: most are dungeon bosses and event monsters. The field
bosses (Pig-Face Bat King, Man-wolf Caesar, Monkey King, Lich King...) are the super monsters the
game lists twice, a Demonized and a plain version, one per level band; data/bosses.json holds
them, by hand. Check its names against the game's text with

    python -m unclefarmer.engine.bosses

The game files say nothing about when a boss appears, so the bot goes by what it sees: a boss in
sight is never a target, nor is anything close to it, and the bot walks off when one comes within
boss_distance steps (0: off).
"""

import json
import math
import os
import re
from typing import Any

from .. import paths
from ..i18n import _

DESCRIPTION = re.compile(r"^([^;]{2,60}); level (\d+) super monster;")
FILE = os.path.join(paths.DATA, "bosses.json")
_names = None


def _from_texts(path):
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    out = {}

    def walk(x):
        if isinstance(x, str):
            m = DESCRIPTION.match(x)
            if m:
                out.setdefault(" ".join(m.group(1).split()).lower(), int(m.group(2)))
        elif isinstance(x, dict):
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)
    walk(data.get("texts", data))
    return out


def names():
    """{boss name in lower case: level}."""
    global _names
    if _names is None:
        got = {}
        try:
            with open(FILE, encoding="utf-8") as f:
                got = json.load(f).get("bosses", {})
        except (OSError, ValueError):
            pass
        _names = got
    return _names


def is_boss(e: dict[str, Any]) -> bool:
    return e.get("kind") == "monster" and " ".join((e.get("name") or "").split()).lower() in names()


def in_sight(snap: dict[str, Any]) -> list[dict[str, Any]]:
    """The living bosses in sight."""
    return [e for e in snap.get("entities", ()) if e.get("hp", 0) > 0 and is_boss(e)]


def too_close(e: dict[str, Any], bosses: list[dict[str, Any]], keep: float) -> bool:
    """Within keep steps of any of these bosses (straight line)."""
    return any(math.hypot(e["x"] - b["x"], e["y"] - b["y"]) < keep for b in bosses)


def keep_away(bot: Any, snap: dict[str, Any], map_id: Any, area: Any) -> bool:
    """A boss within boss_distance of us: walk to the reachable spot furthest from every boss in
    sight (the farm area first). True when we moved, so the farm loop looks again."""
    keep = bot.p.get("boss_distance", 40)
    if not keep:
        return False
    me = snap["self"]
    bosses = in_sight(snap)
    near = [b for b in bosses if math.hypot(b["x"] - me["x"], b["y"] - me["y"]) < keep]
    if not near:
        return False
    told = bot.__dict__.setdefault("bosses_told", set())
    b = min(near, key=lambda x: math.hypot(x["x"] - me["x"], x["y"] - me["y"]))
    if b["id"] not in told:
        told.add(b["id"])
        bot.log(_("{boss} (a boss) is {n} steps away - keeping {keep} steps from it",
                  boss=b["name"], n=int(math.hypot(b["x"] - me["x"], b["y"] - me["y"])), keep=keep), speak=True)

    def spacing(c):
        return min(math.hypot(c[0] - x["x"], c[1] - x["y"]) for x in bosses)
    cells = [c for c in (bot.random_cell(map_id, area, me, radius=keep + 20) for _i in range(12)) if c]
    if area:
        cells += [c for c in (bot.random_cell(map_id, None, me, radius=keep + 20) for _i in range(8)) if c]
    if not cells:
        return False
    best = max(cells, key=spacing)
    if spacing(best) <= spacing((me["x"], me["y"])):
        return False                 # nowhere further off within reach: farm on, away from it

    def far_enough(_me):
        s = bot.snap()
        here = s["self"]
        return not any(math.hypot(x["x"] - here["x"], x["y"] - here["y"]) < keep for x in in_sight(s))
    with bot.timed("search"):
        bot.walk_to(best, near=far_enough)
    return True


def main():
    """Say which names in data/bosses.json the game's text doesn't describe as a super monster."""
    known = _from_texts(os.path.join(paths.DATA, "game_texts.json"))
    if not known:
        raise SystemExit("no monster descriptions found in data/game_texts.json")
    missing = [n for n in names() if n not in known]
    print("%d bosses, %d not found in the game's text%s" % (len(names()), len(missing),
                                                            (": " + ", ".join(missing)) if missing else ""))


if __name__ == "__main__":
    main()
