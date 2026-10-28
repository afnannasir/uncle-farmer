"""Where each dungeon is entered: the NPC (a teleport circle, a cave-mouth NPC, a king's challenge
menu), the map it stands on, and its cell - or, where the game's files mark several entrances on one
map without saying which is which, the marked spots to look at. Built from the game's own data into
data/dungeons.json (read-only here). A circle the bot picks out by its name is remembered in
profiles/_learned_entrances.json, so the next walk goes straight to it."""

from __future__ import annotations

from typing import Any
import os
import re
import threading

from . import jsonio as json, paths

LEARNED = os.path.join(paths.PROFILES, "_learned_entrances.json")
_d: dict[str, Any] | None = None
_lock = threading.Lock()


def _norm(s: str | None) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip().lower())
    return re.sub(r"^the ", "", s)


def _key(e: dict[str, Any]) -> str:
    return "%s|%d" % (_norm(e["name"]), e["map"])


def _learned() -> dict[str, dict[str, Any]]:
    try:
        with open(LEARNED, encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def data() -> dict[str, Any]:
    global _d
    with _lock:
        if _d is None:
            d: dict[str, Any]
            try:
                with open(os.path.join(paths.DATA, "dungeons.json"), encoding="utf-8") as f:
                    d = json.load(f)
            except (OSError, ValueError):
                d = {"entrances": [], "not_found": []}
            _d = d
        return _d


def entrances() -> list[dict[str, Any]]:
    """Every known entrance: {name, npc, map, at [x, y] or None, spots [[x, y, about]],
    dungeon_maps, team, about}, by name. What the bot learned fills in npc and at."""
    learned = _learned()
    out: list[dict[str, Any]] = []
    for e in data().get("entrances", []):
        e = dict(e)
        got = learned.get(_key(e))
        if got and not e.get("at"):
            e["npc"], e["at"] = got["npc"], got["at"]
        out.append(e)
    return sorted(out, key=lambda e: (e["name"].lower(), e["map"]))


def not_found() -> list[dict[str, Any]]:
    """Dungeons whose entrance isn't in the game's files: [{name, maps}]."""
    return data().get("not_found", [])


def find(name: str | None) -> dict[str, Any] | None:
    """The entrance for a dungeon name (or its NPC's name); None if unknown or ambiguous."""
    want = _norm(name)
    hits = [e for e in entrances() if want in (_norm(e["name"]), _norm(e.get("npc")))]
    return hits[0] if len(hits) == 1 else None


def is_entrance(e: dict[str, Any] | None, name: str | None) -> bool:
    """True when an NPC the game shows (by name) is this dungeon's entrance: its listed name, or a
    name that starts with the dungeon's ("Lisa's Ukulele Teleportation circle")."""
    if e is None:
        return False
    n = _norm(name)
    return bool(n) and (n == _norm(e.get("npc")) or n.startswith(_norm(e["name"])))


def learn(e: dict[str, Any], npc_name: str, x: float, y: float) -> None:
    """Remember which marked spot turned out to be this dungeon's circle."""
    with _lock:
        got = _learned()
        got[_key(e)] = {"npc": npc_name, "at": [int(round(x)), int(round(y))]}
        try:
            with open(LEARNED, "w", encoding="utf-8") as f:
                json.dump(got, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
