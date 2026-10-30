"""Healing teammates while following (the `follow` message): a Priest or a Sorceress keeps the
player it follows, and the rest of the team in sight, alive with the class's quick heal.

Who is on the team: the player we follow, and everyone the game named in answer to the quick
phrase "/6" (agent v27 `team`: each press targets the next teammate on this map). Their HP is the
game's own: a player in view carries hp and maxhp like any living thing. The weakest teammate in
reach under the mark is selected by id (agent 40 `select`), healed, and our target selected again,
the same way the pet is healed. A heal that raised nobody's HP TEAM_MISSES times running (out of
a fight) isn't landing, or the game isn't telling us their HP: then teammate heals rest TEAM_REST
seconds and are tried again - never given up for the run. Whatever keeps a heal from going off is
said once, plainly.
"""

from __future__ import annotations

import math
from typing import Any, TYPE_CHECKING

from .. import gamekeys
from .. import phrases as phrase_book
from ..clock import clock_for
from ..i18n import _
from . import combat, potions

if TYPE_CHECKING:
    from .bot import Bot

HEAL_GAP = 2.0        # seconds between two teammate heals: the global cooldown paces the rest
HEAL_CHECK = 2.5      # seconds after a heal before its effect counts as in
TEAM_MISSES = 5       # heals running that raised no HP: they aren't landing. More than the pet's:
                      # a teammate may lose more than one heal gives
TEAM_REST = 300.0     # seconds without teammate heals after TEAM_MISSES misses, before trying again
REACH = 15.0          # tiles: a heal's reach when the skill data has none
LEARN_EVERY = 45.0    # seconds between two "/6" rounds that learn who is on the team
LEARN_PRESSES = 5     # "/6" presses per round: each names the next teammate on the map


def cast_on(b: Bot, sb: Any, h: dict[str, Any], eid: int, now: float, refused: list[str] | None = None) -> bool:
    """Select eid, press the heal h, select our own target again. The game heals the ally that is
    selected (with a monster selected the same heal lands on us). True when the heal was pressed;
    else why not goes into refused, in words for the log."""
    ours = b.ui().get("target")
    reply = b.a.send("select %d" % eid)
    if reply != "selected":
        if refused is not None:
            # agent 41: "refused: <id> <why>" - the target keys' own rules (reach, line of sight)
            why = reply.split(" ", 2)[2] if reply.startswith("refused:") and reply.count(" ") >= 2 else reply
            refused.append(_("the game wouldn't select it ({why})", why=why.strip() or _("no answer")))
        return False
    pressed = sb.press_support(h, now)
    if pressed:
        b.key(pressed[0])
    elif refused is not None:
        refused.append(_("couldn't turn to the attack page {skill} is on", skill=h["name"]))
    if ours and ours != eid:
        b.a.send("select %d" % ours)       # back on the monster we were fighting
    return bool(pressed)


def _names(b: Bot) -> set[str]:
    """Lower-case names of the teammates: the player we follow, and the team the game named."""
    out: set[str] = set(getattr(b, "team_names", ()))
    following: str | None = getattr(b, "following", None)
    if following:
        out.add(following.strip().lower())
    return out


def mates(b: Bot, snap: dict[str, Any]) -> list[dict[str, Any]]:
    """The teammates in view, alive, with their HP known."""
    want = _names(b)
    me = snap["self"]
    return [e for e in snap.get("entities", [])
            if e.get("kind") == "player" and e.get("id") != me.get("id")
            and (e.get("name") or "").strip().lower() in want
            and (e.get("maxhp") or 0) > 0 and 0 < (e.get("hp") or 0) <= e["maxhp"]]


def learn(b: Bot) -> None:
    """Every LEARN_EVERY seconds outside fights, a round of "/6" presses: the game names each
    teammate on this map, so the ones we don't follow get healed too. Needs the "/6" phrase on a
    key (the run start puts it on an empty one)."""
    now = clock_for(b).time()
    if getattr(b, "agent_version", 0) < 27 or b.in_fight or now < getattr(b, "team_learn_at", 0.0):
        return
    b.team_learn_at = now + LEARN_EVERY
    slot = phrase_book.slot_of(getattr(b, "phrases", None) or {}, "/6")
    if not slot or not _healer(b):
        return
    key = gamekeys.phrase_key_list()[slot - 1]
    t0 = b.a.json("team").get("now", 0)
    seen: set[str] = set(getattr(b, "team_names", ()))
    for _press in range(LEARN_PRESSES):
        b.key(key)
        b.sleep(0.35)
        if any("no team" in (s.get("text") or "").lower() for s in b.a.json("said %d" % t0).get("said", [])):
            b.team_learn_at = now + 5 * LEARN_EVERY
            break
    for m in b.a.json("team").get("members", []):
        if m.get("name") and m.get("age_ms", 1e9) < 60000:
            seen.add(m["name"].strip().lower())
    b.team_names = seen


def _healer(b: Bot) -> dict[str, Any] | None:
    sb = getattr(b, "skillbook", None)
    if sb is None or not getattr(sb, "ok", False):
        return None
    return sb.healer()


def _problem(b: Bot, why: str) -> None:
    """Why a teammate heal didn't go off, said once per run for each reason (the first aloud): the
    follow goes on, and the heal is tried again."""
    told: set[str] = b.__dict__.setdefault("_told_team_heal_problems", set())
    if why in told:
        return
    b.log(why, speak=not told)
    told.add(why)


def _missed(b: Bot, h: dict[str, Any], team: list[dict[str, Any]], tried: tuple[float, int, int, bool],
            now: float) -> bool:
    """Judge the last heal by the teammate's HP. A miss counts only out of a fight (in one, the
    monsters may take more than the heal gave) and only with them in sight. True when misses ran
    to TEAM_MISSES: then the heals rest TEAM_REST seconds, said plainly."""
    mate = next((e for e in team if e["id"] == tried[1]), None)
    if mate is None:
        return False                      # out of sight: nothing learned either way
    if mate["hp"] > tried[2]:
        b.team_heal_misses = 0
        return False
    if tried[3]:
        return False
    b.team_heal_misses = getattr(b, "team_heal_misses", 0) + 1
    if b.team_heal_misses < TEAM_MISSES:
        return False
    b.team_heal_misses = 0
    b.team_heal_rest_until = now + TEAM_REST
    told = getattr(b, "told_team_heal_rest", False)
    b.told_team_heal_rest = True
    b.log(_("{skill} didn't raise {name}'s HP {n} times running - it may be landing on you, "
            "or the game isn't showing their HP. No teammate heals for {minutes} minutes, "
            "then {skill} again", skill=h["name"], name=mate.get("name", "").strip(),
            n=TEAM_MISSES, minutes=int(TEAM_REST / 60)), speak=not told)
    return True


def look_after(b: Bot, snap: dict[str, Any]) -> bool:
    """While following: heal the weakest teammate in reach whose HP is under the mark. Not when
    our own HP is in danger (that heal goes to us first). Called between presses and again when
    a fight's next press is due (a fight looks while the global cooldown runs, when no heal can go
    off). True when a heal was pressed."""
    if not getattr(b, "following", None) or not b.p.get("team_heal", True) or getattr(b, "walk_only", False):
        return False
    h = _healer(b)
    if not h:
        return False
    now = clock_for(b).time()
    if now < getattr(b, "team_heal_rest_until", 0.0):
        return False
    team = mates(b, snap)
    tried = getattr(b, "team_heal_try", None)
    if tried:
        if now - tried[0] < HEAL_CHECK:
            return False                  # the last heal's effect isn't in yet
        b.team_heal_try = None
        if _missed(b, h, team, tried, now):
            return False
    me = snap["self"]
    if combat.hp_pct(me) < max(b.p.get("emergency_below", 50), 50):
        return False
    below = b.p.get("team_heal_below", 70)
    below = 70.0 if potions.auto(below) else float(below)
    reach = (h.get("range") or REACH) + 0.5
    low = [e for e in team if 100.0 * e["hp"] / e["maxhp"] < below
           and math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= reach]
    if not low or now - getattr(b, "team_healed_at", 0.0) < HEAL_GAP:
        return False
    mate = min(low, key=lambda e: e["hp"] / e["maxhp"])
    name = mate.get("name", "").strip()
    if not getattr(b, "agent_act", False):
        _problem(b, _("can't heal {name} with {skill}: the bot can't select teammates in this game "
                      "(restart the game so Uncle Farmer's agent loads)", name=name, skill=h["name"]))
        return False
    sb = b.skillbook
    if sb is None or sb.gcd_wait(now) or not sb.ready(h, now):      # (_healer found one in it)
        return False
    refused: list[str] = []
    if not cast_on(b, sb, h, mate["id"], now, refused):
        b.team_healed_at = now            # not again at once: the next try after the heal's gap
        _problem(b, _("can't heal {name} with {skill}: {why} - the heal is tried again",
                      name=name, skill=h["name"], why=refused[0] if refused else _("no answer")))
        return False
    b.team_healed_at = now
    b.team_heal_try = (now, mate["id"], mate["hp"], bool(getattr(b, "in_fight", False)))
    told = b.__dict__.setdefault("_told_team_heal", set())
    if mate["id"] not in told:
        told.add(mate["id"])
        b.log(_("heals {name} with {skill} below {pct}% of their HP", name=name,
                skill=h["name"], pct="%g" % below))
    return True
