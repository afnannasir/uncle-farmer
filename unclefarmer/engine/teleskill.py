"""Teleport skills for travel: a Mage's Teleportation (city squares) and an Elf's Elven Teleportation
(the high-level lands), recorded by hand 2026-11-13 (docs/GAME_GUIDE.md, "Teleport skills").

The game's way: press the skill's key; the server sends the places the skill's level gives and the
client lists them ("Please select a location to teleport"); choosing one starts a 10-second channel
costing 30% mana; when it ends the new map loads on that place's marker. Any hit on the caster
breaks the channel (nothing says so: the map just never changes), and no skill works inside a safe
zone.

What a character can reach is learned from that menu the first time the bot opens it, and kept
per character in profiles/_learned_teleports.json (the player's own, never in a release). Before
that the planner uses the places recorded for the skill (travel.SPELLS); a place the menu turns
out not to have is dropped then, and the trip is planned again.
"""

from __future__ import annotations

import os
import threading
from typing import TYPE_CHECKING, Any

from .. import jsonio as json, paths, travel
from ..errors import carry_on

if TYPE_CHECKING:
    from .bot import Bot

LEARNED = "_learned_teleports.json"
CAST = 10.0            # seconds the channel takes
LOAD = 3.0             # and the new map coming in after it
MANA = 0.30            # share of max MP the cast costs
_lock = threading.Lock()


def _read() -> dict[str, dict[str, list[str]]]:
    try:
        with open(os.path.join(paths.PROFILES, LEARNED), encoding="utf-8") as f:
            got = json.load(f)
        return got if json.is_object(got) else {}
    except (OSError, ValueError):
        return {}


def _who(b: Bot) -> str:
    return str(getattr(b, "profile_key", None) or getattr(b, "character", None) or "")


def skills(b: Bot) -> list[str]:
    """The teleport skills this character has learned (`mem skills`), read once a run."""
    have: list[str] | None = b.__dict__.get("teleport_skills")
    if have is None:
        link = getattr(b, "a", None)
        if link is None:               # no game link (a stand-in with no agent): nothing to cast
            return []
        sk = link.json("mem skills")
        rows: list[dict[str, Any]] = sk.get("combat", []) if json.is_object(sk) else []
        names = {str(r.get("name")) for r in rows}
        have = [s for s in travel.SPELLS if s in names]
        b.__dict__["teleport_skills"] = have
    return have


def places(b: Bot, skill: str) -> list[str]:
    """The places this character's skill lists: learned from its menu, else the recorded ones."""
    mine = _read().get(_who(b), {}).get(skill)
    if mine:
        return [p for p in mine if travel.spell_landing(skill, p)]
    return list(travel.SPELLS.get(skill, {}))


def options(b: Bot) -> list[travel.Spell]:
    """(skill, place) pairs travel may plan with, or none when the profile switches them off."""
    if not b.p.get("use_teleport_skills", True) or getattr(b, "walk_only", False):
        return []
    try:
        return [(s, p) for s in skills(b) for p in places(b, s)]
    except Exception as e:                  # a skill list that can't be read: plan without them
        carry_on(e, "reading the teleport skills")
        return []


def menu_places(top: dict[str, Any] | None) -> list[str]:
    """The place names in the teleport menu, lower case, in its order."""
    out: list[str] = []
    for row in (top or {}).get("rows", []):
        for e in row:
            name = e and (e.get("text") or e.get("raw"))
            if name and str(name).strip():
                out.append(str(name).strip().lower())
    return out


def learn(b: Bot, skill: str, seen: list[str]) -> None:
    """Keep the places this character's skill listed (those with a known landing)."""
    keep = [p for p in seen if travel.spell_landing(skill, p)]
    who = _who(b)
    if not keep or not who:
        return
    with _lock:
        data = _read()
        if data.get(who, {}).get(skill) == keep:
            return
        data.setdefault(who, {})[skill] = keep
        try:
            with open(os.path.join(paths.PROFILES, LEARNED), "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=1)
        except OSError:
            pass
