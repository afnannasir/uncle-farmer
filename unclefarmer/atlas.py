"""The map knowledge: every map, its exits, where monsters spawn, and ready patrol loops.

Built from the game's own map table. Read-only data in the data folder.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from . import jsonio as json, paths
from .i18n import _

if TYPE_CHECKING:
    from .atlaslib import MapModel

_maps: dict[int, dict[str, Any]] | None = None
_farms: dict[int, dict[str, Any]] | None = None
_models: dict[int, "MapModel"] = {}
_lock = threading.Lock()


def maps() -> dict[int, dict[str, Any]]:
    global _maps
    if _maps is None:
        with open(os.path.join(paths.DATA, "atlas.json"), encoding="utf-8") as f:
            raw: dict[str, dict[str, Any]] = json.load(f)["maps"]
        _maps = {int(k): v for k, v in raw.items()}
    return _maps


def farms() -> dict[int, dict[str, Any]]:
    global _farms
    if _farms is None:
        with open(os.path.join(paths.DATA, "farms.json"), encoding="utf-8") as f:
            raw: dict[str, dict[str, Any]] = json.load(f)
        _farms = {int(k): v for k, v in raw.items()}
    return _farms


def name(mid: int | str) -> str:
    m = maps().get(int(mid))
    return m["name"] if m else str(mid)


def label(mid: int | str) -> str:
    """'Wolf Prairie (5121)' - names repeat across maps, so the id always goes with it."""
    return "%s (%s)" % (name(mid), mid)


def model(mid: int | str) -> "MapModel":
    """The walkable grid of a map (loads in about half a second, then cached).

    A long travel or survey meets every map on the way, and each model is several MB: only the
    few most recently used are kept, the oldest is dropped (a new run loads it again if needed)."""
    from . import atlaslib
    mid = int(mid)          # "5302" and 5302 are the same map: one cached model, not two
    with _lock:
        if mid not in _models:
            layer = maps().get(mid, {}).get("reading") == "layer"
            _models[mid] = atlaslib.MapModel(mid, layer_aware=layer)
            if len(_models) > 8:
                _models.pop(next(iter(_models)))
        return _models[mid]


def zones(mid: int | str, kinds: tuple[str, ...] = ("monster", "elite", "boss")) -> list[dict[str, Any]]:
    return [z for z in maps().get(int(mid), {}).get("spawn", {}).get("zones", []) if z["kind"] in kinds]


_kinds: dict[int, str] | None = None

def kind_words(k: str | None) -> str:
    """What a map kind means, for the player ("" for an ordinary farming field)."""
    return {"dungeon": _("dungeon - you leave by its chest, not on foot"),
            "dungeon?": _("probably a dungeon - no way out on foot"),
            "secret_room": _("secret room (timed practice)"), "gang": _("gang map"),
            "wedding": _("wedding map"), "arena": _("arena"), "fishing": _("fishing lake"),
            "test": _("test map"), "event": _("minigame map"),
            "instance": _("closed map - no way in or out on foot"),
            "town": _("town"), "transit": _("road")}.get(k or "", "")


def kind(mid: int | str) -> str | None:
    """What sort of map it is, from the game's own data:
    dungeon, dungeon?, field (an ordinary farming map), town, transit, secret_room, gang, wedding,
    arena, fishing, test or instance. None for a map not in the table."""
    global _kinds
    if _kinds is None:
        try:
            with open(os.path.join(paths.DATA, "map_kinds.json"), encoding="utf-8") as f:
                raw: dict[str, dict[str, Any]] = json.load(f)
            _kinds = {int(k): str(v["kind"]) for k, v in raw.items()}
        except (OSError, ValueError):
            _kinds = {}
    return _kinds.get(int(mid))


def farming_maps() -> list[tuple[int, str, str]]:
    """Maps that have monster zones, as (id, label, summary): ordinary farming fields first, then
    dungeons, secret rooms and the rest, each saying what it is."""
    out: list[tuple[int, str, str]] = []
    for mid, m in maps().items():
        zs = zones(mid)
        if not zs:
            continue
        sp = m["spawn"]
        bits: list[str] = []
        if kind_words(kind(mid)):
            bits.append(kind_words(kind(mid)))
        if sp.get("levels"):
            bits.append("lv " + ",".join(str(v) for v in sp["levels"][:6]))
        if sp.get("has_elite"):
            bits.append("elites")
        if sp.get("has_boss"):
            bits.append("boss")
        out.append((mid, label(mid), ", ".join(bits)))
    out.sort(key=lambda t: (kind(t[0]) not in (None, "field"), t[1].lower()))
    return out


def all_maps() -> list[tuple[int, str]]:
    return sorted(((mid, label(mid)) for mid in maps()), key=lambda t: t[1].lower())


def resolve(text: object) -> int | None:
    """A map id from '5121', 'Wolf Prairie' or 'Wolf Prairie (5121)'. None if unknown/ambiguous."""
    text = str(text).strip()
    if text.endswith(")") and "(" in text:
        text = text[text.rindex("(") + 1:-1]
    if text.isdigit():
        return int(text) if int(text) in maps() else None
    hits = [mid for mid, m in maps().items() if m["name"].strip().lower() == text.lower()]
    with_exits = [mid for mid in hits if maps()[mid].get("exits")]
    hits = with_exits or hits
    return hits[0] if len(hits) == 1 else None


def monster_area(mid: int | str) -> list[float] | None:
    """The bounding box of the map's ordinary monster zones, or of all zones if it has none."""
    zs = zones(mid, ("monster",)) or zones(mid)
    if not zs:
        return None
    return [min(z["rect"][0] for z in zs), min(z["rect"][1] for z in zs),
            max(z["rect"][2] for z in zs), max(z["rect"][3] for z in zs)]


_short: dict[str, dict[str, Any]] | None = None


def _cells(loop: list[str]) -> list[tuple[int, int]]:
    """Cells written as "x|y"."""
    out: list[tuple[int, int]] = []
    for c in loop:
        x, y = c.split("|")
        out.append((int(x), int(y)))
    return out


def patrol_loop(mid: int | str, variant: str) -> list[tuple[int, int]] | None:
    """A ready patrol lap ('full', 'quick' or 'short') as a list of cells, or None."""
    if variant == "short":
        global _short
        if _short is None:
            short: dict[str, dict[str, Any]]
            try:
                with open(os.path.join(paths.DATA, "short_loops.json"), encoding="utf-8") as f:
                    short = json.load(f)
            except OSError:
                short = {}
            _short = short
        v = _short.get(str(int(mid)))
        return _cells(v["loop"]) if v else None
    f = farms().get(int(mid))
    if not f:
        return None
    v = f.get("variants", {}).get(variant)
    if not v or not v.get("loop"):
        return None
    if v.get("bad_hops"):
        return None     # it crosses a one-way gate the wrong way somewhere: random patrol instead
    return _cells(v["loop"])


def edge_direction(mid: int, rect: tuple[float, float, float, float]) -> str | None:
    """Which way pushes off the map through an exit on its edge; None for an exit inside the map."""
    x0, y0, x1, y1 = rect
    w, h = maps()[mid]["w"], maps()[mid]["h"]
    if x1 - x0 <= y1 - y0:
        if x0 <= 3:
            return "left"
        if x1 >= w - 2:
            return "right"
    else:
        if y0 <= 3:
            return "up"
        if y1 >= h - 2:
            return "down"
    return None
