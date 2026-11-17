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

from . import applog, jsonio as json, paths

LEARNED = os.path.join(paths.PROFILES, "_learned_entrances.json")
_d: dict[str, Any] | None = None
_lock = threading.Lock()


def _norm(s: str | None) -> str:
    s = re.sub(r"\s+", " ", (s or "").strip().lower())
    return re.sub(r"^the ", "", s)


def _key(e: dict[str, Any]) -> str:
    return "%s|%d" % (_norm(e["name"]), e["map"])


_last_learned: dict[str, dict[str, Any]] = {}   # the last good reading, for a moment the file can't be read


def _read_learned() -> dict[str, dict[str, Any]]:
    """The learned file as it is: {} when there is none yet. OSError when it is there but can't be
    read, ValueError when it is broken - never an empty answer for those, since learn() saves what
    it gets back and would write every earlier entrance out of the file."""
    raw: object = json.read(LEARNED, {}, encoding="utf-8")
    if not json.is_object(raw):
        raise ValueError("%s isn't a JSON object" % LEARNED)
    return raw


def _learned() -> dict[str, dict[str, Any]]:
    """What the bot learned, for planning. Read under the file's lock, so never half a save. A
    file that can't be read just now gives the last good reading (an empty plan once lost every
    learned entrance until the next look); a broken one gives nothing (learn() keeps a copy)."""
    global _last_learned
    with json.lock_for(LEARNED):
        try:
            got = _read_learned()
        except ValueError:
            return {}
        except OSError as e:
            applog.write("couldn't read %s, using the last reading: %s" % (LEARNED, e), "WARNING")
            return dict(_last_learned)
        _last_learned = dict(got)
        return got


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
    if not want:
        return None           # an empty name would match every entrance with no NPC listed
    hits = [e for e in entrances() if want in (_norm(e["name"]), _norm(e.get("npc")))]
    return hits[0] if len(hits) == 1 else None


def is_entrance(e: dict[str, Any] | None, name: str | None) -> bool:
    """True when an NPC the game shows (by name) is this dungeon's entrance: its listed name, or a
    name that starts with the dungeon's ("Lisa's Ukulele Teleportation circle")."""
    if e is None:
        return False
    n = _norm(name)
    return bool(n) and (n == _norm(e.get("npc")) or n.startswith(_norm(e["name"])))


def learn(e: dict[str, Any], npc_name: str, x: float, y: float) -> bool:
    """Remember which marked spot turned out to be this dungeon's circle. The file is read, changed
    and saved under its lock, and saved atomically (a failed save keeps every earlier entrance).
    True when it was saved; False when the file couldn't be read or written (said in the log)."""
    global _last_learned
    with json.lock_for(LEARNED):
        try:
            got = _read_learned()
        except ValueError:
            json.copy_aside(LEARNED)       # broken: kept as .corrupt, then started again
            got = {}
        except OSError as err:
            applog.write("couldn't read %s, so %s wasn't remembered: %s" % (LEARNED, e.get("name"), err), "WARNING")
            return False
        got[_key(e)] = {"npc": npc_name, "at": [int(round(x)), int(round(y))]}
        try:
            json.save(LEARNED, got, indent=1)
        except OSError as err:
            applog.write("couldn't save %s: %s" % (LEARNED, err), "WARNING")
            return False
        _last_learned = dict(got)
        return True
