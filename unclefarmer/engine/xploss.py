"""Tools, XP loser: die to a guard on purpose until the next level needs as much experience as the
player wants, for players who don't want to level up.

What the game says and does (its help pages, the NPC texts, and game.i read with javap 2026-11-11):
  - Dying costs experience ("When killed, they lose less experience..."); the character sheet says
    how much the next level still needs ("upgrading requires 30000 experience", `mem self` sheet
    xp_needed; none at the top level).
  - Guards ("Guard; level 100 elite monster; Guards protecting the city", in towns, at city gates
    and in villages) are on side 0 (the agent's kind "other"), like other players' summons.
  - Whom an attack may hit is the game's hostility check, game.i.a(ak, int, ak): never a creature
    with the protected bit (type & 2); in the peaceful PK modes only side < 0 (monsters); in "all
    mode" (PK mode 5) everything else too. So guards are hit in all mode, and a guard hit strikes
    back - a level 100 elite kills in moments.
  - Towns and villages are safe zones (the map's z=-3 rows, atlas.safe_zones): the client takes no
    attack key while we stand in one ("Safe zone disabled skills"), and the guards patrolling the
    streets can't be fought. Only a guard outside the zone will do - those in front of the city
    gates - and we must stand outside it too, as a player does it by hand.
  - Black-name players (Morality -1000 and below) risk their worn gear on every death (20%); the
    sheet's "Morality 2000;white name" (agent 60) is read first, and a black name doesn't run.

The run: put the pet away (it would fight the guard), switch the PK mode to all, then for each
death: find a guard - in sight, else a spot where one was seen before (profiles/_learned_guards.json,
filled whenever a guard is in sight), else the nearest town - walk into reach, outside the safe zone
(right up to it when a wall is in the way), get off the fusion beast, select it and press the first attack key once; drink nothing and fight nothing until dead; respawn in the city; read
the sheet again. A guard that doesn't strike back is hit again, then left for another. It ends when
the next level needs at least the goal, after max_deaths deaths, or when deaths stop costing
experience (the level's start: the game takes no level away). The PK mode the game had is put back
at the end, whatever ended it.
"""

from __future__ import annotations

import copy
import math
import os
import re
import threading
from collections.abc import Collection
from typing import Any

from .. import atlas, jsonio as json, paths
from ..i18n import N_, _
from ..settings import PK_MODES
from . import beasts, pets, town
from .bot import Bot, Respawned, Stopped

MODES = ("until", "lose")
MODE_NAMES = (N_("until the next level needs at least"), N_("lose this much experience"))
DEFAULTS: dict[str, Any] = {
    "mode": "until",          # "until": the next level needs at least `until_needed`; "lose": lose `lose` XP
    "until_needed": 0,
    "lose": 0,
    "max_deaths": 30,         # never more deaths than this in one run
}
ALL_MODE = PK_MODES.index("all mode")
GUARD = re.compile(r"\bguard\b", re.I)
BLACK_NAME = -1000         # Morality at or below this: a black name, which can lose worn gear on death
F_PROTECTED = 2           # game.B.f bit 2: the hostility check never counts it (Memory.F_PROTECTED)
GUARDS_SEEN = "_learned_guards.json"   # in the profiles folder: the player's own, never in a release
SPOTS_PER_MAP = 12
SAME_SPOT = 6.0           # tiles: a guard seen this close to a known spot is that spot
HIT_WAIT = 10.0           # seconds a guard has to strike back after a hit
HITS = 3                  # hits on one guard before trying another
SKIP = 120.0              # seconds a guard that wouldn't fight is left alone
STALLS = 2                # deaths in a row that cost no experience: none left to lose on this level
SEARCH = 240.0            # seconds spent walking around a map looking for a guard
SEARCH_WALKS = 12         # ... and never more walks than this (a walk that ends at once can't spin)
LOOK = 0.5
GATE_LOOK = 8             # tiles around the middle of a town's side to find walkable ground outside it
CLOSE = 1.5              # tiles: right next to a guard, for when a wall is in the way from further off
_seen_lock = threading.Lock()
# For the run, over the profile: the city's respawn and no crystals (handle_death), no potions, heal
# skills, buffs or fighting back (they would only keep the guard busy longer), and no stop for HP or
# deaths - dying is the point.
DYING: dict[str, Any] = {"respawn": "city", "use_resurrection_crystal": False, "stop_on_deaths": False,
                         "stop_on_panic": False, "hp_below": 0, "smart_skills": False, "fight_back": False}


def config(p: dict[str, Any]) -> dict[str, Any]:
    """The profile's XP loser settings, with every setting filled in."""
    mine: dict[str, Any] = p.get("xp_loser") or {}
    return {**copy.deepcopy(DEFAULTS), **mine}


def goal(cfg: dict[str, Any], needed_now: int) -> int:
    """The experience the next level should need when we are done."""
    if cfg.get("mode") == "lose":
        return needed_now + max(0, int(cfg.get("lose") or 0))
    return max(0, int(cfg.get("until_needed") or 0))


# ---------------------------------------------------------------------------------------- guards
def is_guard(e: dict[str, Any]) -> bool:
    """A living guard the all mode lets us hit: side 0, "Guard" in its name, not protected."""
    return (e.get("kind") == "other" and e.get("side") == 0 and int(e.get("hp", 0) or 0) > 0
            and bool(GUARD.search(str(e.get("name") or ""))) and not int(e.get("type", 0) or 0) & F_PROTECTED)


def guards(snap: dict[str, Any], skip: dict[int, float] | None = None, now: float = 0.0,
           mid: int | None = None) -> list[dict[str, Any]]:
    """The guards in sight, nearest first, without those left alone for now and (with the map
    id) without those in a safe zone: the city's own patrols can't be fought, only the ones in
    front of the gates and outside the villages (live 2026-11-13: one walking the city streets
    was chased for a minute, one just past the gate killed us at once)."""
    me: dict[str, Any] = snap.get("self") or {}
    rows: list[dict[str, Any]] = snap.get("entities", [])
    found = [e for e in rows if is_guard(e) and (skip or {}).get(int(e["id"]), 0.0) <= now
             and not (mid and atlas.in_safe_zone(mid, e["x"], e["y"]))]
    if "x" in me:
        found.sort(key=lambda e: math.hypot(e["x"] - me["x"], e["y"] - me["y"]))
    return found


def _seen() -> dict[str, list[list[float]]]:
    try:
        with open(os.path.join(paths.PROFILES, GUARDS_SEEN), encoding="utf-8") as f:
            got = json.load(f)
        return got if json.is_object(got) else {}
    except (OSError, ValueError):
        return {}


def note(mid: int, found: list[dict[str, Any]]) -> None:
    """Remember where guards stand on this map (a few spots per map, the newest kept)."""
    if not found or not mid:
        return
    with _seen_lock:
        seen = _seen()
        spots: list[list[float]] = [s for s in seen.get(str(mid), []) if len(s) == 2]
        changed = False
        for e in found:
            x, y = round(float(e["x"]), 1), round(float(e["y"]), 1)
            if any(math.hypot(x - s[0], y - s[1]) < SAME_SPOT for s in spots):
                continue
            spots.append([x, y])
            changed = True
        if not changed:
            return
        seen[str(mid)] = spots[-SPOTS_PER_MAP:]
        try:
            with open(os.path.join(paths.PROFILES, GUARDS_SEEN), "w", encoding="utf-8") as f:
                json.dump(seen, f, ensure_ascii=False, indent=1)
        except OSError:
            pass


def spots(skip_maps: Collection[int] = ()) -> list[town.Npc]:
    """Every remembered guard spot, as the (map, name, x, y) rows town.nearest takes."""
    out: list[town.Npc] = []
    for k, rows in _seen().items():
        try:
            mid = int(k)
        except ValueError:
            continue
        if mid in skip_maps or mid not in atlas.maps():
            continue
        out += [(mid, "Guard", float(s[0]), float(s[1])) for s in rows
                if len(s) == 2 and not atlas.in_safe_zone(mid, s[0], s[1])]   # older notes kept city patrols
    return out


def gate_cells(mid: int, me: dict[str, Any]) -> list[tuple[int, int]]:
    """When we stand in a safe zone: a walkable cell just outside the middle of each of its four
    sides, nearest first - city gates sit mid-side, and the guards that can be fought patrol in
    front of them. Empty outside a safe zone (or with no map data)."""
    x, y = float(me.get("x", 0)), float(me.get("y", 0))
    cx, cy = int(x + 0.5), int(y + 0.5)
    towns = [(int(r[0]), int(r[1]), int(r[2]), int(r[3])) for r in atlas.safe_zone_rows(mid)
             if len(r) >= 5 and r[4] == "town"]
    zone = next(((x1, y1, x2, y2) for x1, y1, x2, y2 in towns if x1 <= cx <= x2 and y1 <= cy <= y2), None)
    if zone is None:
        return []
    try:
        m = atlas.model(mid)
    except (OSError, ValueError, KeyError):
        return []
    x1, y1, x2, y2 = zone
    mx, my = (x1 + x2) // 2, (y1 + y2) // 2
    ours = m.comp_of(cx, cy) if m.ok(cx, cy) else None     # only ground we can walk to
    out: list[tuple[int, int]] = []
    for px, py in ((mx, y1 - 2), (x2 + 2, my), (mx, y2 + 2), (x1 - 2, my)):
        c = m.nearest_walkable(px, py, radius=GATE_LOOK, comp=ours)
        if c is not None and not atlas.in_safe_zone(mid, c[0], c[1]) and not m.portal[c[1], c[0]]:
            out.append((int(c[0]), int(c[1])))
    out.sort(key=lambda c: math.hypot(c[0] - x, c[1] - y))
    return out


# ---------------------------------------------------------------------------------------- the game's numbers
def xp_needed(b: Bot) -> int | None:
    """The experience the next level still needs, read now: -1 at the top level, None unknown."""
    sheet: dict[str, Any] = b.refresh_sheet(force=True) or {}
    v = sheet.get("xp_needed")
    return int(v) if isinstance(v, (int, float)) else None


def morality(b: Bot) -> int | None:
    """The sheet's Morality (agent 60; "Morality 2000;white name"), or None when not known."""
    sheet: dict[str, Any] = b.refresh_sheet() or {}
    v = sheet.get("morality")
    return int(v) if isinstance(v, (int, float)) else None


def pk_name(mode: object) -> str:
    """A PK mode's name as the game says it, or "?"."""
    if isinstance(mode, int) and 0 <= mode < len(PK_MODES):
        name: str = PK_MODES[mode]
        return _(name)
    return "?"


def modes(b: Bot) -> dict[str, Any]:
    m = b.a.json("mem modes")
    return m if json.is_object(m) and "error" not in m else {}


def set_pk(b: Bot, want: int) -> bool:
    """Press the PK mode key until the game's mode is `want`. False when it is locked or won't change."""
    m = modes(b)
    if m.get("pk") == want:
        return True
    if not m or m.get("pk_locked"):
        return False
    for _i in range(len(PK_MODES) + 1):
        b.key(b.p.get("pk_key") or "b")
        b.sleep(0.3)
        if modes(b).get("pk") == want:
            return True
    return False


# ---------------------------------------------------------------------------------------- the run
class _Run:
    def __init__(self, b: Bot, cfg: dict[str, Any]) -> None:
        self.b = b
        self.cfg = cfg
        self.deaths0 = int(getattr(b, "deaths", 0) or 0)
        self.skip: dict[int, float] = {}
        self.locked: set[int] = set()       # maps where the PK mode can't be changed

    # -- finding a guard
    def look(self) -> dict[str, Any] | None:
        """The nearest guard in sight we may try now; never one on a map where all mode is locked."""
        b = self.b
        snap = b.snap()
        me: dict[str, Any] = snap.get("self") or {}
        if int(me.get("hp", 1) or 0) <= 0:
            self.died()
        found = guards(snap, self.skip, b.clock.time(), b.map_id())
        note(b.map_id(), found)
        if b.map_id() in self.locked:
            return None
        return found[0] if found else None

    def find(self) -> dict[str, Any] | None:
        """A guard in sight; else walk to where one was seen; else the nearest town; else around."""
        g = self.look()
        if g is not None:
            return g
        b = self.b
        here = b.map_id()
        tried: set[int] = set(self.locked)
        for _leg in range(4):
            rows = [s for s in spots(tried) if s[0] != here or self._far(s)]
            spot = town.nearest(b, None, candidates=rows) if rows else None
            if spot is None:
                break
            b.log(_("going to where a guard was seen: {map}", map=atlas.label(spot[0])))
            b.travel(spot[0], (int(spot[2]), int(spot[3])))
            g = self.look()
            if g is not None:
                return g
            tried.add(spot[0])
            here = b.map_id()
        # this map is searched even when a remembered spot on it came up empty: the guards there
        # may only have been out of sight from the spot (a live run stopped right there, 2026-11-13)
        if here not in self.locked:
            g = self.search()
            if g is not None:
                return g
        place = town.nearest(b, "storehouse")       # every town has one, and guards guard towns
        if place is not None and place[0] != b.map_id() and place[0] not in self.locked:
            b.log(_("no guard here - going to the nearest town, {map}", map=atlas.label(place[0])))
            b.travel(place[0], (int(place[2]), int(place[3]) + 1))
            return self.look() or self.search()
        return None

    def _far(self, spot: town.Npc) -> bool:
        me = self.b.me()
        return math.hypot(spot[2] - me.get("x", 0), spot[3] - me.get("y", 0)) > SAME_SPOT

    def search(self) -> dict[str, Any] | None:
        """Walk around this map for a while, looking for a guard."""
        b = self.b
        mid = b.map_id()
        end = b.clock.time() + SEARCH
        b.log(_("looking for a guard on {map}", map=atlas.label(mid)))
        gates = gate_cells(mid, b.me())      # in a town: its edges first, where the gate guards stand
        for _walk in range(SEARCH_WALKS):
            if b.clock.time() >= end:
                break
            me = b.me()
            cell = gates.pop(0) if gates else b.random_cell(mid, None, me)
            if cell is None:
                return None
            result = b.walk_to(cell, near=lambda _me: self.look() is not None)
            g = self.look()
            if g is not None:
                return g
            if result == "map" or b.map_id() != mid:
                return None
        return None

    # -- getting killed
    def approach(self, g: dict[str, Any], reach: float) -> bool:
        """Walk until the guard is within `reach` tiles and we stand outside every safe zone (the
        game takes no attack key in one). False when it is gone from sight or into a safe zone."""
        b = self.b
        mid = b.map_id()

        def there(m: dict[str, Any]) -> bool:
            return (math.hypot(g["x"] - m["x"], g["y"] - m["y"]) <= reach
                    and not atlas.in_safe_zone(mid, m["x"], m["y"]))
        for _try in range(3):
            if there(b.me()):
                return True
            b.walk_to((g["x"], g["y"]), near=there)
            now = next((e for e in guards(b.snap(), mid=mid) if e["id"] == g["id"]), None)
            if now is None:
                return False
            g.update(now)
        return there(b.me())

    def strike(self, g: dict[str, Any]) -> bool:
        """Into reach, select it, one press of the first attack key. False when it can't be hit."""
        b = self.b
        reach = max(1.5, min(float(b.reach()), 18.0) - 0.5)
        if not self.approach(g, reach):
            return False
        # off the fusion beast first, as a player does it by hand (recorded 2026-11-13: ride up,
        # put the beast away, then attack); the travel there leaves us on the Swift one, whose
        # skills are off, so the attack key did nothing
        beasts.off(b)
        reply = b.a.send("select %d" % g["id"])
        if reply != "selected" and reach > CLOSE and ("clear line" in reply or "reach" in reply):
            # guards stand at gates and walls: from the attack's range a wall is often in between
            # (live 2026-11-13: "refused: -39 isn't in a clear line"), so walk right up to it
            if not self.approach(g, CLOSE):
                return False
            reply = b.a.send("select %d" % g["id"])
        if reply != "selected":
            b.log(_("can't select the guard: {why}", why=reply))
            return False
        keys: list[str] = list(b.p.get("attack_keys") or [])
        if not keys:
            raise Stopped(_("no attack key in Character settings - the guard can't be hit"), final=True)
        b.key(keys[0])
        return True

    def wait_for_death(self, g: dict[str, Any]) -> bool:
        """After a hit: wait, drinking nothing. True when the guard is striking (or we died)."""
        b = self.b
        hp0 = int(b.me().get("hp", 0) or 0)
        end = b.clock.time() + HIT_WAIT
        while b.clock.time() < end:
            b.check()
            me = b.me()
            if int(me.get("hp", 1) or 0) <= 0:
                self.died()
            if int(me.get("hp", 0) or 0) < hp0:
                end = max(end, b.clock.time() + HIT_WAIT)   # it is hitting: keep waiting
            b.sleep(LOOK)
        return int(b.me().get("hp", 0) or 0) < hp0

    @property
    def deaths(self) -> int:
        """Deaths this run, however they came (a walk's own look also answers the respawn menu)."""
        return int(getattr(self.b, "deaths", 0) or 0) - self.deaths0

    def died(self) -> None:
        """Dead: the respawn menu as handle_death answers it (the city, no crystals)."""
        self.b.handle_death()     # raises Respawned once we are alive again

    def all_mode(self) -> bool:
        """The PK mode to all here. False (and this map marked locked) when it can't be changed."""
        b = self.b
        mid = b.map_id()
        if mid in self.locked:
            return False
        if set_pk(b, ALL_MODE):
            return True
        self.locked.add(mid)
        b.log(_("the PK mode can't be changed on {map} - looking for guards elsewhere", map=atlas.label(mid)))
        return False

    def run(self, target: int, start: int) -> str:
        b = self.b
        needed, stalls = start, 0
        while True:
            b.check()
            if needed >= target:
                return "done"
            if self.deaths >= int(self.cfg.get("max_deaths") or DEFAULTS["max_deaths"]):
                return "deaths"
            try:
                # all mode BEFORE looking: only then does the game count guards as enemies (its
                # enemy list and target keys); checked every time, a respawn or new map may reset it
                self.all_mode()
                g = self.find()
                if g is None:
                    return "locked" if self.locked else "no guard"
                if not self.all_mode():
                    continue              # walked onto a map where it is locked: look elsewhere
                for _hit in range(HITS):
                    if not self.strike(g):
                        break
                    while self.wait_for_death(g):
                        pass              # it is striking: wait on until we fall (Respawned)
                self.skip[int(g["id"])] = b.clock.time() + SKIP
                b.log(_("the guard didn't strike back - trying another"))
            except Respawned:
                now = xp_needed(b)
                if now is None:
                    raise Stopped(_("can't read the experience from the character sheet any more"), final=True)
                if now <= needed:
                    stalls += 1
                    if stalls >= STALLS:
                        return "stalled"
                else:
                    stalls = 0
                b.log(_("died {n} times: the next level needs {xp} experience now (goal {goal})",
                        n=self.deaths, xp=now, goal=target))
                needed = now


def run(b: Bot, cfg: dict[str, Any], check_only: bool = False) -> str:
    """Tools, XP loser. Returns why it ended: "done", "deaths", "stalled", "no guard", "locked",
    or "checked"."""
    start = xp_needed(b)
    if start is None:
        raise Stopped(_("can't read the experience from the character sheet - restart the game so the new agent loads"),
                      final=True)
    if start < 0:
        raise Stopped(_("this character is at the top level: there is no next level to stay away from"), final=True)
    honour = morality(b)
    if honour is not None and honour <= BLACK_NAME:
        raise Stopped(_("Morality is {n} (a black name): a death can cost worn gear, so the XP loser won't run",
                        n=honour), final=True)
    target = goal(cfg, start)
    if start >= target:
        b.log(_("the next level already needs {xp} experience (goal {goal}) - nothing to lose", xp=start, goal=target),
              speak=True)
        return "done"
    m = modes(b)
    if check_only:
        snap = b.snap()
        found = guards(snap, mid=b.map_id())
        note(b.map_id(), found)
        b.log(_("check: the next level needs {xp} experience, goal {goal}; {n} guards in sight here, "
                "{k} remembered spots; PK mode {pk}{locked}",
                xp=start, goal=target, n=len(found), k=len(spots()),
                pk=pk_name(m.get("pk")),
                locked=_(" (locked here)") if m.get("pk_locked") else ""), speak=True)
        return "checked"
    was_pk = m.get("pk")
    saved = b.p
    b.p = {**saved, **DYING}
    b.dying_on_purpose = True
    r = _Run(b, cfg)
    b.log(_("XP loser: the next level needs {xp} experience; dying to guards until it needs {goal}",
            xp=start, goal=target))
    why = "?"
    try:
        pets.recall(b)
        why = r.run(target, start)
    finally:
        b.dying_on_purpose = False
        b.p = saved
        if isinstance(was_pk, int) and was_pk != ALL_MODE:
            try:
                if not set_pk(b, was_pk):
                    b.log(_("couldn't put the PK mode back to {mode} - please set it by hand",
                            mode=pk_name(was_pk)), speak=True)
            except Stopped:
                b.log(_("the PK mode is still all mode - please set it back by hand"), speak=True)
    end = {
        "done": _("done: the next level needs at least {goal} experience", goal=target),
        "deaths": _("stopped after {n} deaths, as you set", n=r.deaths),
        "stalled": _("dying costs no more experience on this level - stopped"),
        "no guard": _("no guard found here, at the spots where guards were seen, or in the nearest town"),
        "locked": _("the PK mode can't be changed where the guards are"),
    }[why]
    b.log(_("XP loser: {end} ({n} deaths)", end=end, n=r.deaths), speak=True)
    if why in ("no guard", "locked"):
        raise Stopped(end, final=True)
    return why
