"""Which monsters to fight, nearest first, and why every other one in sight is left alone.

One pure function (choose), so that a recorded night can be fed through it with other policies
(pickreplay.py) and a test can ask it directly. It reads nothing from the bot: what the bot holds
against monsters comes in as Notes, the player's filters as Rules, and the two things that need
the map or the monsters' history as functions (walk: steps to a monster; doomed: someone else
kills it before we get there). It changes nothing either: the attackers whose old skips are to be
dropped come back in Choice.forgiven for the bot to drop (fighting.targets does that).
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable, Mapping, Sequence, Set
from dataclasses import dataclass, field
from typing import Any

from .. import atlas, jsonio
from . import bosses

Rect = tuple[float, float, float, float]
# How targets are put in order: (monster, its walking steps, the choice's context) -> sort key.
Order = Callable[[dict[str, Any], float, "Context"], tuple[Any, ...]]

NPC_HP = 20000000        # a town NPC's HP (botparts.base.NPC_HP: the same number)
NEAR_TAG = 10            # a monster we hurt this far past our reach still comes before nearer ones


@dataclass(frozen=True)
class Rules:
    """The player's filters for this choice (a plan's spot, the walk's fight back...)."""
    area: Sequence[float] | None = None      # x0, y0, x1, y1: the farming area (3 tiles of slack)
    only: str | None = None                  # only monsters whose name matches (regex)
    skip: str | None = None                  # never monsters whose name matches (regex)
    avoid_rects: Sequence[Rect] = ()         # elite and boss zones kept out of
    limit: float | None = None               # the chase range in steps (not for attackers)
    boss_keep: float = 40                    # keep this far from a boss (0: bosses aren't looked for)
    skip_elites: bool = False                # leave demonized elites alone (skip_demonized_elites)
    elites: Sequence[dict[str, Any]] = ()    # with skip_elites: the elites in sight, kept ELITE_CLEAR from


@dataclass(frozen=True)
class Notes:
    """What the bot holds against the monsters in sight at this moment."""
    now: float
    hitting: Set[int] = frozenset()          # striking us or our pet lately (Bot.attackers)
    tagged: Set[int] = frozenset()           # hurt by us lately: partly ours
    skip: Mapping[int, float] = field(default_factory=dict[int, float])     # id -> left alone until
    kinds: Mapping[tuple[Any, str], float] = field(default_factory=dict[tuple[Any, str], float])
    walk_failed: Mapping[int, float] = field(default_factory=dict[int, float])  # id -> stuck walk until
    untrackable: Set[int] = frozenset()      # the target keys won't take it where it stands
    refused_again: Set[int] = frozenset()    # ... after more than one refusal there
    reach: float = 0.0                       # how far we hit from


@dataclass
class Context:
    """What an Order sees besides the monster: where we stand and the notes."""
    me: dict[str, Any]
    notes: Notes


@dataclass
class Choice:
    targets: list[dict[str, Any]]            # to fight, in order
    left: dict[str, int]                     # why the others were left (fighting.LEFT_OUT keys) -> how many
    no_way: list[list[float]]                # where the first few with no way to walk to stood
    steps: dict[int, float]                  # walking steps to each target (and to attackers)
    names: dict[int, str]                    # id -> name of every monster seen
    forgiven: list[tuple[int, tuple[Any, str]]]   # attackers whose skip and kind skip go (id, kind key)


def nearest(e: dict[str, Any], steps: float, ctx: Context) -> tuple[Any, ...]:
    """The bot's order: those attacking us first; then those we have already hurt, close by (our
    hits make them partly ours, and finishing them gets the kill instead of leaving it to the next
    player); then by walking steps, then by straight distance."""
    me, n = ctx.me, ctx.notes
    straight = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
    return (e["id"] not in n.hitting,
            not (e["id"] in n.tagged and straight <= n.reach + NEAR_TAG),
            steps, straight)


def placed(e: dict[str, Any], size: Mapping[str, Any]) -> bool:
    """The monster has a real position: numbers, finite, on the map (when its size is known).
    A reading mid-spawn or mid-despawn can carry none, and walking or aiming at it fails."""
    x, y = e.get("x"), e.get("y")
    if not isinstance(x, (int, float)) or not isinstance(y, (int, float)) or isinstance(x, bool):
        return False
    if not (math.isfinite(x) and math.isfinite(y)):
        return False
    w, h = size.get("w"), size.get("h")
    return not (isinstance(w, int) and isinstance(h, int)) or (0 <= x <= w + 1 and 0 <= y <= h + 1)


def map_of(snap: dict[str, Any]) -> Any:
    """The map id of a snapshot (botparts.base.snap_map)."""
    m = snap.get("map")
    return m.get("id") if jsonio.is_object(m) else m


def choose(snap: dict[str, Any], rules: Rules, notes: Notes,
           walk: Callable[[dict[str, Any], float | None], float],
           doomed: Callable[[dict[str, Any], float], bool],
           not_monsters: Set[str] = frozenset(), order: Order = nearest) -> Choice:
    """The monsters to fight in this snapshot, in order (attackers first), and why the others were
    left. walk(e, limit): steps to e, math.inf past limit or with no way there. doomed(e, steps):
    someone else will have killed it before we get there. not_monsters: lower-case names of
    things that come in as monsters but aren't (our traps and totems)."""
    me: dict[str, Any] = snap["self"]
    map_id = map_of(snap)
    size = atlas.maps().get(map_id, {})
    boss_list = bosses.in_sight(snap) if rules.boss_keep else []
    out: list[dict[str, Any]] = []
    steps: dict[int, float] = {}
    left: dict[str, int] = {}
    no_way: list[list[float]] = []
    names: dict[int, str] = {}
    forgiven: list[tuple[int, tuple[Any, str]]] = []
    entities: list[dict[str, Any]] = snap["entities"]
    for e in entities:
        if e.get("kind") != "monster" or "hp" not in e or e["hp"] <= 0:
            continue
        if not e.get("name") or e["maxhp"] <= 1 or not placed(e, size):
            continue        # drops and corpses; or seen with no position we can walk to or aim at
        names[e["id"]] = e["name"]
        if e["maxhp"] >= NPC_HP or e["name"].strip().lower() in not_monsters:
            continue        # a town NPC; our own (or another player's) trap or totem
        why = left_out(e, map_id, rules, notes, boss_list, forgiven)
        if why is None:
            hitting_us = e["id"] in notes.hitting
            steps[e["id"]] = walk(e, None if hitting_us else rules.limit)
            if steps[e["id"]] == math.inf and not hitting_us:
                # no walkable way to it, or further than the chase: never picked, whatever the patrol mode
                why = "far" if rules.limit is not None and walk(e, None) < math.inf else "no way"
            elif not hitting_us and doomed(e, steps[e["id"]]):
                why = "doomed"  # it will be dead before we get there
        if why is not None:
            left[why] = left.get(why, 0) + 1
            if why == "no way" and len(no_way) < NO_WAY_KEPT:
                no_way.append([round(e["x"], 1), round(e["y"], 1)])
            continue
        out.append(e)
    ctx = Context(me, notes)
    out.sort(key=lambda e: order(e, steps[e["id"]], ctx))
    return Choice(out, left, no_way, steps, names, forgiven)


NO_WAY_KEPT = 5          # places of monsters with no way to walk to that a choice keeps


def left_out(e: dict[str, Any], map_id: Any, rules: Rules, notes: Notes, boss_list: list[dict[str, Any]],
             forgiven: list[tuple[int, tuple[Any, str]]]) -> str | None:
    """Why this monster is left out before any walking distance is worked out (a key of
    fighting.LEFT_OUT), or None when it may be a target. An attacker's old skips are forgiven
    (added to forgiven for the caller to drop)."""
    mid = e["id"]
    hit = mid in notes.hitting
    kind = (map_id, e["name"].strip().lower())
    now = notes.now
    if hit and mid in notes.refused_again:
        # Striking us from a spot where the target keys have refused it twice (Machining Plant's
        # Hay Cutter blades): trying again at once fails at once, and the walk's fight back would
        # spin on it many times a second. It is tried again when its growing wait runs out.
        return "skip"
    if hit and notes.walk_failed.get(mid, 0) <= now:
        # It is hitting us or our pet: what the bot held against it (or its kind) before is stale.
        # The one thing a fight must never do is stand there and take it. A walk to it that got
        # stuck keeps its skip: one shooting at us from where we can't walk would be picked again
        # at once, stuck again at once, and the farm loop would spin on it.
        forgiven.append((mid, kind))
    elif mid in notes.untrackable or notes.skip.get(mid, 0) > now:
        return "skip"
    elif notes.kinds.get(kind, 0) > now:
        return "kind"       # its kind took no damage and never attacked: a critter, not a monster
    name = e["name"]
    if (rules.only and not re.search(rules.only, name, re.I)) or (rules.skip and re.search(rules.skip, name, re.I)):
        return "names"
    if boss_list and (bosses.is_boss(e) or bosses.too_close(e, boss_list, rules.boss_keep)):
        return "boss"
    if not hit and rules.skip_elites and (
            bosses.is_demonized_elite(e) or bosses.too_close(e, list(rules.elites), bosses.ELITE_CLEAR)):
        return "elite"      # it, or a monster it would join the fight for; one that attacks us is still fought
    area = rules.area
    if area and not hit and not (area[0] - 3 <= e["x"] <= area[2] + 3 and area[1] - 3 <= e["y"] <= area[3] + 3):
        return "area"
    if any(r[0] <= e["x"] <= r[2] and r[1] <= e["y"] <= r[3] for r in rules.avoid_rects):
        return "avoid"
    return None
