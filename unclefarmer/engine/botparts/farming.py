# pyright: strict
"""Farming a map, the day's drop time, and ending a run (safe spot, end place, restock).

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

import math
import random
from collections.abc import Callable, Sequence
from typing import Any, cast

from ... import atlas
from ...settings import PK_MODES, TARGET_MODES
from ...i18n import _
from .. import beasts, bosses, keyslots, pets
from .base import (Respawned, Stopped)
from .parts import Parts


def _near(lo: int, hi: int, at: int, radius: int) -> tuple[int, int]:
    """The span [lo, hi] narrowed to radius around `at` - all of it when `at` stands further out
    than that. Standing outside the area (a walk that left it, or an area that is a small corner
    of the map) must not invert the range: random.randint(low, high) raises on that, and a patrol
    is no reason to stop a run."""
    low, high = max(lo, at - radius), min(hi, at + radius)
    return (lo, hi) if low > high else (low, high)


class FarmingMixin(Parts):
    restocked_once: bool
    farmed: bool
    bag_start: dict[str, int] | None
    said_drop: bool

    def set_modes(self) -> None:
        """Press the mode keys until the game's PK mode and target mode are the profile's."""
        p = self.p
        if not p.get("set_modes", True):
            return
        m = self.a.json("mem modes")
        if "error" in m:
            self.log(_("can't read the game's modes ({why}) - not changing them", why=m["error"]))
            return
        jobs = (("pk", p["pk_key"], p["pk_mode"], PK_MODES, len(PK_MODES)),
                ("target", p["target_mode_key"], p["target_mode"], TARGET_MODES, len(TARGET_MODES)))
        for field, key, want, names, size in jobs:
            if field not in m or m[field] == want:
                continue
            if field == "pk" and m.get("pk_locked"):
                self.log(_("the PK mode is locked here - leaving it as {mode}", mode=names[m[field]]))
                continue
            for _press in range(size + 1):
                self.key(key)
                self.sleep(0.3)
                m = self.a.json("mem modes")
                if m.get(field) == want:
                    self.log(_("mode set: {mode}", mode=names[want]))
                    break
            else:
                self.log(_("couldn't set the mode to {mode} with the {key} key", mode=names[want], key=key),
                         speak=True)

    def farm(self, map_id: int, area: Sequence[float] | None = None, minutes: float = 0, kills: int = 0, only: str = "", skip: str = "", patrol: str = "random",
             avoid_elites: bool = False, avoid_bosses: bool = True, until: Callable[[], bool] | None = None) -> None:
        """Farm a map until minutes or kills are reached (0 = no limit) or until() is true."""
        map_id = int(map_id)
        if not self.restocked_once:
            self.restocked_once = True
            self.restock_now(start=True)
        if getattr(self, "bag_start", None) is None:
            from .. import town
            self.bag_start = town.counts(self)   # what was ours before the run: never sold or stored
        self.travel(map_id)
        self.farmed = True        # only a run that got to its map closes the game or the PC when it ends
        area = list(area) if area else atlas.monster_area(map_id)
        avoid: list[tuple[float, float, float, float]] = []
        if avoid_elites:
            avoid += [cast(tuple[float, float, float, float], z["rect"]) for z in atlas.zones(map_id, ("elite",))]
        if avoid_bosses:
            avoid += [cast(tuple[float, float, float, float], z["rect"]) for z in atlas.zones(map_id, ("boss",))]
        loop = cast(list[tuple[int, int]] | None, atlas.patrol_loop(map_id, patrol)) if patrol in ("full", "quick", "short") else None
        # On a patrol loop, only what is within chase_range walking steps is chased: the loop
        # passes near the rest (a short loop keeps nearly the whole map that close). Wandering at
        # random, the nearest is taken however far.
        chase = self.p.get("chase_range", 40) if loop else None
        self.farm_filter = (area, only, skip, avoid)
        if self.timing is None:
            self.timing = {"_since": self.clock.time(), "_kills": self.kills, "_contribution": self.contribution_now()}
        start, start_kills = self.clock.time(), self.kills
        self.log(_("farming {map}", map=atlas.label(map_id)))
        self.set_modes()
        self.modes_done = True

        def done():
            if minutes and (self.clock.time() - start) / 60.0 >= minutes:
                return True
            if kills and self.kills - start_kills >= kills:
                return True
            return bool(until and until())

        def sighted(_me: dict[str, Any]) -> bool:
            return bool(self.targets(cast(dict[str, Any], self.snap()), area, only, skip, avoid, limit=chase))

        last_supply_check = self.clock.time()
        try:
            self._farm_loop(map_id, area, only, skip, avoid, loop, chase, done, sighted, last_supply_check)
        finally:
            self.farm_filter = None
            self.timing_report(final=True)
            self.timing = None
        self.log(_("finished farming {map}", map=atlas.label(map_id)))

    def _farm_loop(self, map_id: int, area: Sequence[float] | None, only: str, skip: str, avoid: list[tuple[float, float, float, float]], loop: list[tuple[int, int]] | None, chase: float | None, done: Callable[[], bool], sighted: Callable[[dict[str, Any]], bool], last_supply_check: float) -> None:
        loop_i = None
        while not done():
            try:
                self.timing_report()
                if self.clock.time() - last_supply_check > self.p.get("restock_check_every", 30):
                    last_supply_check = self.clock.time()
                    with self.timed("town"):
                        if self.restock_now():
                            self.log(_("back to {map}", map=atlas.label(map_id)))
                            self.travel(map_id)
                            self.set_modes()
                if self.map_id() != map_id:
                    self.log(_("left {map} (died or moved?) - going back", map=atlas.label(map_id)))
                    with self.timed("town"):
                        self.travel(map_id)
                    self.set_modes()
                snap = self.snap()
                self.count_kills(snap)     # what died of our hits since (area skills, damage over time)
                self.look_after(snap["self"])
                pets.keep_out(self)
                pets.look_after(self, snap)
                with self.timed("town"):
                    went = self.bag_check() or self.share_trip(map_id)
                if went:
                    continue          # we went to town: the loop travels back first
                if keyslots.refill(self):
                    continue
                if bosses.keep_away(self, snap, map_id, area):
                    continue
                mobs = self.targets(snap, area, only, skip, avoid, limit=chase)
                if mobs:
                    mob = self.next_target(snap, mobs)
                    if mob["id"] not in self.attackers() and self.rest_up(snap["self"]):
                        continue
                    self.note_pick(snap, mob, mobs)
                    self.fight(mob)
                    continue
                # Nothing in sight: keep moving.
                me = snap["self"]
                if loop:
                    if loop_i is None:
                        loop_i = min(range(len(loop)), key=lambda i: math.hypot(loop[i][0] - me["x"], loop[i][1] - me["y"]))
                    cell = loop[loop_i]
                    loop_i = (loop_i + 1) % len(loop)
                else:
                    cell = self.random_cell(map_id, area, me)
                if cell is None:
                    self.sleep(1.0)
                    continue
                started = self.clock.time()
                with self.timed("search"):
                    result = self.walk_to(cell, near=sighted)
                self.note_search(map_id, me, cell, started, result)
            except Respawned:
                loop_i = None
                self.log(_("going back to {map}", map=atlas.label(map_id)))
                try:
                    with self.timed("town"):
                        self.travel(map_id)   # a no-op after a map respawn there
                except Respawned:
                    continue              # died again on the way: the next round handles it
                self.set_modes()

    def share_trip(self, map_id: int) -> bool:
        """Leave the farm for a short secret room visit when one is due (chores.trip_due: just
        before a half hour, so one visit shares arnebia in two of them), then go back. True when
        it went."""
        from .. import chores          # chores uses the town errands, which use the whole Bot
        if not chores.trip_due(self):
            return False
        self.log(_("off to the secret room to share arnebia"))
        try:
            made, _where = chores.share_visit(self, chores.config(self.p))
            self.log(_("shared arnebia {n} of {all} times today", n=made, all=chores.SHARES_A_DAY))
        except Stopped as e:
            if self.stop_event.is_set() or getattr(e, "final", False):
                raise
            self.log(_("no arnebia shared this time: {why}", why=e))
        self.log(_("back to {map}", map=atlas.label(map_id)))
        self.travel(map_id)
        self.set_modes()
        return True

    def drop_time_check(self, sheet: dict[str, Any]) -> None:
        """End the run when the day's drop time ("public area drop 6 hours 48 minutes ... remaining"
        in the sheet) is down to what the player wants to keep. It keeps running for a few minutes
        after we leave the monsters (about 4 minutes, measured by the player), so we stop that much
        earlier. Off with stop_on_drop_time - unless the plan or script says how much to keep
        (keep_drop): then this run keeps that much, whatever the character's setting."""
        plan_keep = getattr(self, "drop_keep", None)
        if plan_keep is None and not self.p.get("stop_on_drop_time", True):
            return
        left = sheet.get("drop_seconds", -1)
        if left is None or left < 0:
            return
        left -= sheet.get("age", 0)
        keep = 60 * (plan_keep if plan_keep is not None else self.p.get("drop_keep_minutes", 0))
        tail = self.p.get("drop_tail_seconds", 300)
        if left - tail > keep:
            if not getattr(self, "said_drop", False):
                self.said_drop = True
                mins = int((left - tail - keep) / 60)
                self.log(_("drop time: {h} h {m} min left; farming stops in about {n} minutes",
                           h=int(left // 3600), m=int(left % 3600 // 60), n=mins))
            return
        self.log(_("drop time is nearly used up ({m} min left) - going somewhere safe and stopping",
                   m=max(0, int(left // 60))), speak=True)
        self.finish()
        raise Stopped(_("drop time used up"))

    def leave_monsters(self) -> None:
        """End a run somewhere no monster spawns: if this map has monster zones, travel to the
        nearest map that has none (towns, transit maps). A "safe spot" inside a spawn map is not
        safe - monsters spawn and walk there too."""
        here = self.map_id()
        if not atlas.zones(here):
            return
        dist = {}
        todo = [here]
        dist[here] = 0
        goal = None
        while todo and goal is None:
            nxt: list[int] = []
            for mid in todo:
                for e in atlas.maps().get(int(mid), {}).get("exits", []):
                    d = e.get("dest")
                    if d is None or d in dist:
                        continue
                    dist[d] = dist[mid] + 1
                    if not atlas.zones(d):
                        goal = d
                        break
                    nxt.append(d)
                if goal is not None:
                    break
            todo = nxt
        if goal is None:
            self.log(_("no map without monsters near {map}", map=atlas.label(here)), speak=True)
            return
        self.log(_("done here - going to {map}, where no monsters spawn", map=atlas.label(goal)))
        self.travel(goal)

    def finish(self) -> None:
        """The end of a run - the plan's time is up, the drop time or the bag ended it, the last
        death, broken gear - goes where the plan (or the script's end_at) says, then off the beast:
          start        back where the run started (if monsters spawn there: the nearest map
                       without monsters instead - a run started inside the cave isn't ended there)
          place        a map and spot the player chose, walking and riding as travel does
          hearthstone  home with the hearthstone (if that fails, the nearest map without monsters)
          safe         the nearest map without monsters (the hearthstone first, if the profile says)
          here         stay
        If the way there fails, the nearest map without monsters."""
        how: tuple[Any, ...] = self.end_place or ("start",)
        kind = how[0]
        try:
            if kind == "here":
                pass
            elif kind == "hearthstone":
                if not (self.has_hearthstone() and self.go_home(tries=1)):
                    self.leave_monsters()
            elif kind in ("start", "place"):
                if kind == "start":
                    if not self.start_place:
                        return self._finish_safe()
                    mid, cell = self.start_place[0], tuple(self.start_place[1:3])
                    if self.spawns_at(mid, cell):
                        self.log(_("the run started where monsters spawn - ending on the nearest map without them"))
                        return self._finish_safe()
                else:
                    mid, cell = how[1], (tuple(how[2]) if how[2] else None)
                here = self.me()
                if self.map_id() != mid or not cell or math.hypot(here["x"] - cell[0], here["y"] - cell[1]) > 3:
                    self.log(_("going back to {map}{at}", map=atlas.label(mid),
                               at=(" (%d,%d)" % cell) if cell else ""), speak=True)
                    self.travel(mid, cell)
            else:
                return self._finish_safe()
        except Respawned:
            raise
        except Stopped:
            if self.stop_event.is_set():
                raise
            self.log(_("couldn't get there - going to the nearest map without monsters"), speak=True)
            self.leave_monsters()
        self._finish_off()

    def _finish_off(self) -> None:
        beasts.off(self)
        if self.p.get("pet_recall_at_end", False):
            pets.recall(self)

    def _finish_safe(self) -> None:
        """The old ending: somewhere no monster spawns. The hearthstone is the shortest way there
        (the home city counts as safe); if it doesn't work, walk."""
        if self.p.get("end_safe", True) and atlas.zones(self.map_id()):
            if self.p.get("end_with_hearthstone", True) and self.has_hearthstone():
                self.log(_("going home with the hearthstone"))
                if not self.go_home(tries=1):
                    self.leave_monsters()
            else:
                self.leave_monsters()
        self._finish_off()

    def restock_now(self, start: bool = False) -> bool:
        """Top up the profile's restock rules if they need it (storehouse first, then shops)."""
        rules = cast(list[dict[str, Any]], self.p.get("restock") or [])
        if not rules:
            return False
        from .. import town
        return town.restock(self, rules, start)

    def random_cell(self, map_id: int, area: Sequence[float] | None, me: dict[str, Any], radius: int = 40) -> tuple[int, int] | None:
        """A walkable cell near us in the area that we can walk to: never on a map exit (stepping on
        one leaves the map mid-farm) and never in a part of the map walled off from ours (walk_to
        would find no way and stop the run)."""
        m = atlas.model(map_id)
        x0, y0, x1, y1 = (int(v) for v in (area or (1, 1, atlas.maps()[map_id]["w"], atlas.maps()[map_id]["h"])))
        mx, my = int(me["x"]), int(me["y"])
        ours = m.comp_of(mx, my) if m.ok(mx, my) else None
        # On a map with one-way gates, only ground we can also walk back from: a cell past a gate
        # the wrong way would leave us stuck behind it (turkey cave is a maze of them).
        both = m.two_way(mx, my) if ours is not None and m.gate.any() else None
        for _try in range(60):
            c = (random.randint(*_near(x0, x1, mx, radius)),
                 random.randint(*_near(y0, y1, my, radius)))
            if not m.ok(*c) or m.portal[c[1], c[0]]:
                continue
            if both is not None and c[1] * m.W + c[0] not in both:
                continue
            if both is None and ours is not None and m.comp_of(*c) != ours:
                continue
            return c
        return None
