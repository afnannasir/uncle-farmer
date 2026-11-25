"""Corpse harvesting: Draw Blood, Skinning or Cut Flesh on the monsters we kill, while Vitality lasts.

What the game says and does (its Profession Skill Introduction, and game.i.a(int, boolean,
boolean) and game.i.a(int, short, int, boolean) read with javap 2026-11-11):
  - A character learns one Corpse Harvesting skill (Draw Blood, Skinning or Cut Flesh); it is in
    the bag's Profession skills list (`mem skills` profession, with its id). Using it costs
    Vitality, a daily allowance shown on the character sheet; more Vitality costs diamonds, which
    the bot never spends.
  - A player binds it to Z, X or C and presses that key at the selected target. The key's task
    checks the target - a creature at exactly 0 HP (a fresh corpse: the other dead states, -1 and
    -2, are a corpse already harvested or dried) within 20 tiles - and ends in the game's cast
    method, which sends the cast request (OUT op=15).
  - The bot uses none of that: the agent's `cast <skill id> <corpse id>` makes the same checks and
    calls that same cast method, so no key has to hold the skill and no target is selected. It
    holds the cast as the key does - sent again every 60 ms, then let go - since agent 61: the
    cast marks our character as attacking until the key comes back up, and agents 59 and 60, which
    never let go, made every walk fail until the next fight (night 2026-11-12: 607 stucks).
  - The server takes the skill only from close by: measured live 2026-11-12, every harvest
    (ours and another player's) landed 1.1-4.5 tiles from the corpse, and casts from further off
    were never answered. The key walks up to a far corpse (up to 20 tiles) before it casts; the
    agent always casts standing, at corpses within REACH (agent 62 checks the same range), so the
    bot does the walking itself. A corpse someone else harvested shows -1 HP at once and is left.
  - Standing only was too little (logs of 2026-11-15 to 11-18): a character that fights from afar
    kills about 18 tiles off, and only 1-5% of its corpses lay within REACH (a mage: 12 harvests
    in some 1,150 kills, with Vitality left over); even a melee character ended its fights about
    5 tiles from where the next one began. So by default the bot walks up to a corpse as the key
    does (corpse_walk), within WALK_REACH and WALK_STEPS; the player can turn that off.
  - The harvest comes back as our own cast of the skill (IN op=41); the game says "lack of
    vitality" when the day's Vitality is used up.

So, between fights and with nothing attacking us or our pet: the nearest fresh corpse of a
monster we killed in the last minute gets the skill, once per corpse - one within harvesting range
from where we stand, else (corpse_walk) one we walk up to, not near a boss. Corpses left because
they are too far are logged once per run, so a run that harvests little says why. It stops for the run when
Vitality is down to the player's mark (corpse_keep_vitality; 0: until it runs out) or the game says
it ran out. Casts the game never answers are said once and tried again after a rest: it never
turns itself off for that.
"""

from __future__ import annotations

import math
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import jsonio as json
from ..i18n import N_, _
from . import bosses

if TYPE_CHECKING:
    from .bot import Bot

SKILLS = (N_("Draw Blood"), N_("Skinning"), N_("Cut Flesh"))
AGENT = 62                # the agent that takes only corpses in harvesting range, and only its skill's report
REACH = 4.5               # tiles to a corpse the server takes the skill from (live: 1.1-4.5), cast standing
WALK_REACH = 20.0         # tiles (straight) to a corpse we walk up to: as far as the game's own key goes
WALK_STEPS = 30           # walking steps (round walls) we go out of our way for a corpse
CLOSE = 1.5               # tiles from the corpse a walk up to it ends at (our own harvests landed at 1.1)
FRESH = 60.0              # seconds after our kill that its corpse is still taken
WAIT = 3.0                # seconds a cast has to bring its report
LOOK = 0.25               # seconds between looks while waiting
MISSES = 4                # casts in a row the game never answered: say so and rest
REST = 300.0              # seconds before trying again after that, or after the agent couldn't


@dataclass
class Harvest:
    """One run's corpse harvesting (b.corpses)."""
    skill: str | None = None      # the character's corpse skill, once read
    skill_id: int = 0             # ... its id in the game
    off: str = ""                 # why it is over for this run ("" while on)
    done: dict[int, float] = field(default_factory=lambda: {})   # corpse id -> when we tried it
    misses: int = 0               # casts in a row that brought no report
    rest_until: float = 0.0
    harvested: int = 0
    told: bool = False            # the run's first line (skill, Vitality) was said
    refusals: dict[str, int] = field(default_factory=lambda: {})   # why the agent sent nothing -> times
    walked: int = 0               # corpses we walked up to
    far: dict[int, float] = field(default_factory=lambda: {})    # corpse id -> its kill, left as too far
    left: dict[str, int] = field(default_factory=lambda: {})     # why a corpse of ours was left -> times


def state(b: Bot) -> Harvest:
    st: Harvest | None = getattr(b, "corpses", None)
    if st is None:
        st = Harvest()
        b.corpses = st
    return st


def skill_of(skills: dict[str, Any]) -> tuple[str, int] | None:
    """(name, id) of the character's corpse skill from a `mem skills` answer, or None."""
    rows: list[dict[str, Any]] = skills.get("profession") or []
    for r in rows:
        if r.get("name") in SKILLS and r.get("id") is not None:
            return str(r["name"]), int(r["id"])
    return None


def walks(b: Bot) -> bool:
    """We walk up to corpses out of harvesting range (profile corpse_walk, on by default)."""
    return bool(b.p.get("corpse_walk", True))


def corpse(b: Bot, snap: dict[str, Any], st: Harvest, now: float) -> dict[str, Any] | None:
    """The fresh corpse (0 HP) of a monster we killed in the last FRESH seconds, not tried since
    that kill: the nearest within REACH, cast at from where we stand; else, when we walk to
    corpses, the one fewest steps away within WALK_REACH and WALK_STEPS, not near a boss. Corpses
    of ours left as too far are noted (once each, see _left)."""
    me: dict[str, Any] = snap.get("self") or {}
    if "x" not in me:
        return None
    fresh = sorted(_fresh(b, snap, st, now), key=lambda de: de[0])
    if fresh and fresh[0][0] <= REACH:
        return fresh[0][1]
    return _to_walk_to(b, snap, st, now, fresh)


def _fresh(b: Bot, snap: dict[str, Any], st: Harvest, now: float) -> list[tuple[float, dict[str, Any]]]:
    """(tiles from us, corpse) for each fresh corpse of our kills not tried since that kill."""
    me: dict[str, Any] = snap["self"]
    counted: dict[int, float] = getattr(b, "counted", {}) or {}
    out: list[tuple[float, dict[str, Any]]] = []
    rows: list[dict[str, Any]] = snap.get("entities", [])
    for e in rows:
        if e.get("id") is None:
            continue
        mid = int(e["id"])
        killed = counted.get(mid)
        if killed is None or now - killed > FRESH or e.get("hp") != 0 or e.get("kind") == "npc":
            continue
        if st.done.get(mid, -math.inf) >= killed:
            continue              # tried since this kill (an id the game reuses dies again later)
        out.append((math.hypot(e["x"] - me["x"], e["y"] - me["y"]), e))
    return out


def _to_walk_to(b: Bot, snap: dict[str, Any], st: Harvest, now: float,
                further: list[tuple[float, dict[str, Any]]]) -> dict[str, Any] | None:
    """Of the corpses out of harvesting range (nearest first), the one we walk up to: fewest steps
    within WALK_REACH and WALK_STEPS, not near a boss; None when walking to corpses is off. The
    others are noted, once each (_left)."""
    walk = walks(b)
    counted: dict[int, float] = getattr(b, "counted", {}) or {}
    keep = b.p.get("boss_distance", 40)
    boss_list = bosses.in_sight(snap) if walk and keep else []
    mine = bosses.player_no_go(b)
    near: tuple[float, dict[str, Any]] | None = None
    for d, e in further:
        if not walk:
            why = _("too far to harvest from where we stand, and walking to corpses is off")
        elif d > WALK_REACH:
            why = _("further off than we walk for a corpse")
        elif boss_list and bosses.too_close(e, boss_list, keep):
            why = _("near a boss")
        elif mine and not bosses.clear_of((e["x"], e["y"]), mine):
            why = _("in a no-go circle of this spot")
        else:
            steps = b.walk_dist(snap, e, WALK_STEPS)
            if steps <= WALK_STEPS:
                if near is None or steps < near[0]:
                    near = (steps, e)
                continue
            why = _("further off than we walk for a corpse")
        mid = int(e["id"])
        killed = counted.get(mid, now)
        if st.far.get(mid, -math.inf) < killed:
            st.far[mid] = killed
            _left(b, st, why, e)
    return near[1] if near else None


def _ready(b: Bot, st: Harvest, now: float) -> bool:
    """The agent can cast, the character has a corpse skill, and Vitality is above the player's mark."""
    if st.skill is None:
        version = int(getattr(b, "agent_version", 0) or 0)
        if version and version < AGENT:
            st.off = "old agent"
            b.log(_("corpse harvesting needs the game's agent {need} or newer (this game has {have}) - "
                    "restart the game so the new agent loads", need=AGENT, have=version), speak=True)
            return False
        sk = b.a.json("mem skills")
        if not json.is_object(sk) or "error" in sk:
            st.rest_until = now + REST
            why = sk.get("error", "?") if json.is_object(sk) else _("no answer")
            b.log(_("can't read the profession skills ({why}) - corpse harvesting tries again in 5 minutes",
                    why=why))
            return False
        found = skill_of(sk)
        if found is None:
            st.off = "no skill"
            b.log(_("corpse harvesting is on, but this character has no Draw Blood, Skinning or Cut Flesh - "
                    "learn one (Learn Profession Skill) from the gang's Guru Elder or Master Pepper"), speak=True)
            return False
        st.skill, st.skill_id = found
    from . import gathering       # here: gathering imports the Bot, which farming (our caller) is part of
    v = gathering.vitality(b)
    keep = float(b.p.get("corpse_keep_vitality", 0) or 0)
    if v is not None and v <= keep:
        _over(b, st, _("Vitality is down to {n}", n=v) if keep else _("Vitality is used up for today"))
        return False
    if not st.told:
        st.told = True
        b.log(_("{skill} on our kills while Vitality lasts ({n} now)", skill=_(st.skill),
                n=v if v is not None else _("unknown")))
    return True


def _over(b: Bot, st: Harvest, why: str) -> None:
    st.off = why
    skill = _(st.skill) if st.skill else _("corpse harvesting")
    b.log(_("{why} - no more {skill} this run ({n} done)", why=why, skill=skill, n=st.harvested), speak=True)


def harvest(b: Bot, snap: dict[str, Any]) -> bool:
    """Between fights: the corpse skill on the nearest fresh corpse of ours, when the profile
    says (corpse_skill). True when a corpse was tried (the farm loop looks again)."""
    p = b.p
    if not p.get("corpse_skill", False):
        return False
    st = state(b)
    now = b.clock.time()
    if st.off or now < st.rest_until:
        return False
    if b.attackers() or b.pet_attackers():
        return False              # fights first
    got = corpse(b, snap, st, now)
    if got is None or not _ready(b, st, now):
        return False
    st.done[got["id"]] = now
    if len(st.done) > 500:
        st.done = {k: t for k, t in st.done.items() if now - t < FRESH}
    if len(st.far) > 500:
        st.far = {k: t for k, t in st.far.items() if now - t < FRESH}
    me: dict[str, Any] = snap.get("self") or {}
    if math.hypot(got["x"] - me["x"], got["y"] - me["y"]) > REACH:
        walked = _walk_up(b, st, got)
        if walked is None:
            return True           # gone, or not reached: the next one
        got, me = walked
    since = int(b.clock.time() * 1000)   # the agent's casts and said lines are stamped in ms (after any walk)
    reply = b.a.send("cast %d %d" % (st.skill_id, got["id"]))
    if reply.startswith("refused:"):
        if "profession skills" in reply:
            st.skill = None       # forgotten since we read the list: read it again next time
        _refused(b, st, reply, got)
        return True               # out of reach or sight, or harvested already: the next one
    if not reply.startswith("cast "):
        return _agent_failed(b, st, reply)
    _await(b, st, got, since, me.get("id"))
    return True


def _await(b: Bot, st: Harvest, got: dict[str, Any], since: int, me_id: int | None) -> None:
    """After a cast: our report of it (a harvest), the game's "lack of vitality" (over for the run),
    an attack (the fight first), or nothing within WAIT (a miss; MISSES in a row rest)."""
    from . import gathering       # (see _ready)
    skill = st.skill or ""
    end = b.clock.time() + WAIT
    while b.clock.time() < end:
        b.sleep(LOOK)
        for line in gathering.said(b, since):
            if gathering.VITALITY.search(line):
                _over(b, st, _("Vitality is used up for today"))
                return
        if gathering.own_casts(b, skill, since, me_id):
            st.harvested += 1
            st.misses = 0
            b.harvests = getattr(b, "harvests", 0) + 1
            b.log(_("{skill}: {name}", skill=_(skill), name=got.get("name") or _("a monster")))
            return
        if b.attackers():
            return                # something came at us: the fight first, this corpse is left
    st.misses += 1
    if st.misses >= MISSES:
        st.misses = 0
        st.rest_until = b.clock.time() + REST
        b.log(_("{skill} went out at {n} corpses in a row and the game never answered - trying again in 5 minutes",
                skill=_(skill), n=MISSES), speak=True)


def _walk_up(b: Bot, st: Harvest, got: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]] | None:
    """Walk to within CLOSE of the corpse, as the game's key does before it casts. (the corpse,
    us) as they are once there; None when it is gone (harvested by someone else, dried) or the
    walk didn't get us within harvesting range. A fight on the way (fight back) comes first and
    the walk goes on after it."""
    cx, cy = float(got["x"]), float(got["y"])

    def close(me: dict[str, Any]) -> bool:
        return math.hypot(me["x"] - cx, me["y"] - cy) <= CLOSE

    with b.timed("approach"):
        b.walk_to((cx, cy), near=close)
    snap = b.snap()
    me: dict[str, Any] = snap.get("self") or {}
    now_e = next((e for e in snap.get("entities", []) if e.get("id") == got["id"]), None)
    if now_e is None or now_e.get("hp") != 0:
        _left(b, st, _("harvested by someone else, or dried, before we got there"), got)
        return None
    if "x" not in me or math.hypot(now_e["x"] - me["x"], now_e["y"] - me["y"]) > REACH:
        _left(b, st, _("not reached on foot"), got)
        return None
    st.walked += 1
    return now_e, me


def _left(b: Bot, st: Harvest, why: str, got: dict[str, Any]) -> None:
    """A corpse of ours we didn't cast at (too far, gone before we got there): each kind of reason
    is logged the first time in a run (not spoken)."""
    st.left[why] = st.left.get(why, 0) + 1
    if st.left[why] == 1:
        b.log(_("left the corpse of {name}: {why}", name=got.get("name") or _("a monster"), why=why))


def _refused(b: Bot, st: Harvest, reply: str, got: dict[str, Any]) -> None:
    """A corpse the agent sent nothing for: each kind of reason is logged the first time (not
    spoken), so a run that harvests little says why."""
    why = reply[len("refused:"):].strip()
    head, _sep, rest = why.partition(" ")
    if head.lstrip("-").isdigit() and rest:
        why = rest                # "-7 isn't in sight" -> "isn't in sight": one line per kind
    st.refusals[why] = st.refusals.get(why, 0) + 1
    if st.refusals[why] == 1:
        b.log(_("{skill} not cast at {name}: {why}", skill=_(st.skill or ""),
                name=got.get("name") or _("a monster"), why=why))


def _agent_failed(b: Bot, st: Harvest, reply: str) -> bool:
    """The agent didn't cast: an agent without `cast` (an older game) is over for the run; anything
    else rests and tries again."""
    if reply.startswith("unknown command"):
        st.off = "old agent"
        b.log(_("corpse harvesting needs the game's agent {need} or newer - restart the game so the new agent loads",
                need=AGENT), speak=True)
        return False
    st.rest_until = b.clock.time() + REST
    b.log(_("couldn't cast {skill} ({why}) - trying again in 5 minutes", skill=_(st.skill or ""), why=reply))
    return False
