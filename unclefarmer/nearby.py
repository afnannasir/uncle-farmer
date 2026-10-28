"""Who is around us and how our pet is doing, from one world snapshot (`mem`). Shared by the main
window's status and the private-message commands.

Other players are entities of kind "player" under their own name. Pets are players too, to the
game, but their names end in "belongs to <owner>"; they are left out of the players and our own
is the pet.
"""

from __future__ import annotations

import math
from typing import Any

from .i18n import _

OWNER = " belongs to "


def players(snap: dict[str, Any]) -> list[tuple[str, float]]:
    """[(name, tiles away)] of the other players in sight, nearest first."""
    me = snap["self"]
    out: list[tuple[str, float]] = []
    entities: list[dict[str, Any]] = snap.get("entities", [])
    for e in entities:
        name = " ".join((e.get("name") or "").split())
        if e.get("kind") != "player" or not name or OWNER in name or e.get("id") == me.get("id"):
            continue
        out.append((name, math.hypot(e["x"] - me["x"], e["y"] - me["y"])))
    return sorted(out, key=lambda x: x[1])


def players_text(snap: dict[str, Any], most: int | None = None) -> str:
    """"3: Ali 4, Sara 12, Kim 30" (tiles away), or that nobody is in sight."""
    near = players(snap)
    if not near:
        return _("Nobody in sight")
    shown = near if most is None else near[:most]
    text = "%d: %s" % (len(near), ", ".join("%s %d" % (n, round(d)) for n, d in shown))
    if len(shown) < len(near):
        text += ", " + _("{n} more", n=len(near) - len(shown))
    return text


def pet(snap: dict[str, Any]) -> dict[str, Any] | None:
    """{"name", "hp", "maxhp", "mp", "maxmp"} of our pet in sight (MP only once the bot has read it
    with the pet HP key), or None."""
    me = snap["self"]
    tail = OWNER + (me.get("name") or "")
    entities: list[dict[str, Any]] = snap.get("entities", [])
    for e in entities:
        name = " ".join((e.get("name") or "").split())
        if e.get("kind") == "player" and me.get("name") and name.endswith(tail):
            out: dict[str, Any] = {"name": name[:-len(tail)], "hp": e.get("hp"), "maxhp": e.get("maxhp"), "mp": None, "maxmp": None}
            v: dict[str, Any] = me.get("pet_vitals") or {}
            info: dict[str, Any] = me.get("pet_info") or {}
            if v.get("mp", -1) >= 0 and v.get("age_ms", 1e9) < 120000:
                out["mp"] = v["mp"]
            if info.get("maxmp"):
                out["maxmp"] = info["maxmp"]
            return out
    return None


def pet_text(snap: dict[str, Any]) -> str:
    """"Wild Eagle Sky, HP 80%, MP 52%", or that no pet is out."""
    p = pet(snap)
    if p is None:
        return _("No pet out")
    bits = [p["name"]]
    if p["maxhp"]:
        bits.append(_("HP {pct}%", pct=int(100 * p["hp"] / p["maxhp"])))
    if p["mp"] is not None and p["maxmp"]:
        bits.append(_("MP {pct}%", pct=int(100 * p["mp"] / p["maxmp"])))
    return ", ".join(bits)
