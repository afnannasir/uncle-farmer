"""Walking on a map: key walks, getting unstuck, exits, monsters in the way, walking distances.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable
from typing import Any

from ... import atlas
from ...i18n import _
from .. import beasts
from .base import (DIRECTIONS, NPC_HP, STOPPING, Respawned, Stopped, note_failure, snap_map)
from .parts import as_bot, Parts


class WalkingMixin(Parts):
    NEAR_EVERY = 0.25     # seconds between "stop here?" checks while walking

    BLOCKING = 2.5        # a monster this close stands in our way, whichever side it is on

    AHEAD = 6.0           # ...and one this close in the direction we are walking

    ATTACK_LIMIT = 20     # times one walk may be broken off by a hit before it gives up as stuck

    DIST_LIMIT = 150      # steps the walking-distance search looks out to; further counts as out of reach
    # One round of a walk's readings taking this long means the game hung (a map loading, a pause
    # of its own), not that a wall is in the way: that time doesn't count towards a stuck. The
    # long-run simulator caught a hang of a few seconds mid-walk reading as a stuck and a step aside.
    GAME_STALL = 1.0
    _dists: tuple[int, tuple[int, int], Any, Any, bool] | None
    _unsticking: bool


    def npc_cells(self, dest: tuple[float, float], keep: float = 2.5) -> set[tuple[int, int]]:
        """The cells town NPCs stand on, with the cells around them - except those near dest (the
        NPC we may be walking up to)."""
        cells: set[tuple[int, int]] = set()
        try:
            ents = self.snap()["entities"]
        except STOPPING:
            raise                   # a stop, a death or a lost agent must not look like "no NPCs"
        except Exception as e:
            note_failure(self, "reading NPC positions", e)
            return cells
        for e in ents:
            if not (e.get("kind") == "npc" or (e.get("id", 0) < 0 and (e.get("maxhp") or 0) >= NPC_HP)):
                continue
            x, y = int(round(e["x"])), int(round(e["y"]))
            if math.hypot(x - dest[0], y - dest[1]) <= keep:
                continue
            cells.update((x + dx, y + dy) for dx in (-1, 0, 1) for dy in (-1, 0, 1))
        return cells

    def walk_diag(self, target: tuple[float, float], map_before: int | None = None, tol: float = 0.5, timeout: float = 60.0, near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        """Hold the east-west and the north-south key together, letting go of each once its
        coordinate is reached: on the slant while both are held, straight on after (the walk
        atlas diag_ok checked). If the game only follows one key at a time this is the old
        one-axis walk, so it never does worse. Returns what walk_axis returns."""
        me = self.look_after()
        held: dict[int, tuple[int, str]] = {}
        for axis in (0, 1):
            delta = target[axis] - (me["x"], me["y"])[axis]
            if abs(delta) > tol:
                sign = 1 if delta > 0 else -1
                held[axis] = (sign, self.move_key(DIRECTIONS[(sign, 0) if axis == 0 else (0, sign)]))
        if not held:
            return "arrived"
        self.last_hp = me.get("hp")
        for _sign, k in held.values():
            self.hold(k)
        try:
            start = self.clock.time()
            left = sum(abs(target[a] - (me["x"], me["y"])[a]) for a in held)
            best, best_at = left, start
            near_at = 0.0
            while self.clock.time() - max(start, self.resumed_at) < timeout:
                self.clock.wait(self.stop_event, 0.04)
                tick = self.clock.time()
                if map_before is not None and self.map_id() != map_before:
                    return "map"
                me = self.look_after()
                if self.hit(me):
                    return "attacked"
                if near and self.clock.time() >= near_at:
                    near_at = self.clock.time() + self.NEAR_EVERY
                    if near(me):
                        return "near"
                if self.clear_popups():
                    best_at = self.clock.time()
                pos = (me["x"], me["y"])
                self._let_go_reached(held, target, pos, tol)
                if not held:
                    return "arrived"
                left = sum(abs(target[a] - pos[a]) for a in held)
                if left < best - 0.1:
                    best, best_at = left, self.clock.time()
                else:
                    best_at, stuck = self._no_progress(tick, best_at)
                    if stuck:
                        return "stuck"
            return "stuck"
        finally:
            for _sign, k in held.values():
                self.let_go(k)

    def _no_progress(self, tick: float, best_at: float) -> tuple[float, bool]:
        """A walk round that got no closer: (when the walk last counted as moving, stuck?). A round
        whose readings took longer than GAME_STALL sat out a hung game - it starts the count again
        instead of adding to it; otherwise 1.5 s without getting closer is a stuck."""
        now = self.clock.time()
        if now - tick > self.GAME_STALL:
            return now, False
        return best_at, now - max(best_at, self.resumed_at) > 1.5

    def _let_go_reached(self, held: dict[int, tuple[int, str]], target: tuple[float, float],
                        pos: tuple[float, float], tol: float) -> None:
        """Let go of each held key whose coordinate is reached (it stays out of held)."""
        for axis in list(held):
            sign, k = held[axis]
            if (target[axis] - pos[axis]) * sign <= tol:
                self.let_go(k)
                del held[axis]

    def walk_axis(self, axis: int, target: float, map_before: int | None = None, tol: float = 0.5, timeout: float = 60.0, near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        """Hold one direction until that coordinate is reached.
        Returns 'arrived', 'stuck', 'map' (we changed map) or 'near' (near() said stop)."""
        me = self.look_after()
        pos = me["x"] if axis == 0 else me["y"]
        delta = target - pos
        if abs(delta) <= tol:
            return "arrived"
        sign = 1 if delta > 0 else -1
        k = self.move_key(DIRECTIONS[(sign, 0) if axis == 0 else (0, sign)])
        self.last_hp = me.get("hp")
        self.hold(k)
        try:
            start = self.clock.time()
            best, best_at = pos, start
            near_at = 0.0
            while self.clock.time() - max(start, self.resumed_at) < timeout:
                self.clock.wait(self.stop_event, 0.04)
                tick = self.clock.time()
                if map_before is not None and self.map_id() != map_before:
                    return "map"
                me = self.look_after()
                if self.hit(me):
                    return "attacked"
                # near() reads the whole world (is a monster in sight?): a few times a second is
                # plenty, where the position is read every step to stop on the right tile
                if near and self.clock.time() >= near_at:
                    near_at = self.clock.time() + self.NEAR_EVERY
                    if near(me):
                        return "near"
                if self.clear_popups():
                    best_at = self.clock.time()      # the time the window held us doesn't count as stuck
                pos = me["x"] if axis == 0 else me["y"]
                if (target - pos) * sign <= tol:
                    return "arrived"
                if (pos - best) * sign > 0.1:
                    best, best_at = pos, self.clock.time()
                else:
                    best_at, stuck = self._no_progress(tick, best_at)
                    if stuck:
                        return "stuck"
            return "stuck"
        finally:
            self.let_go(k)

    def walk_to(self, cell: tuple[float, float], map_change: bool = False, near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        """Walk to a cell on this map, around walls. Returns 'arrived', 'map', 'near' or 'stuck'."""
        mid = self.map_id()
        m = atlas.model(mid)
        cell = self._walk_goal(m, mid, cell, map_change)
        attempt = 0
        attacks = 0           # hits that broke this walk off (see ATTACK_LIMIT)
        # Both direction keys at once, across open ground on the slant (walk_diag) - unless the
        # profile turns it off, or this walk already got stuck once: then one axis at a time.
        diagonal = self.p.get("walk_diagonal", True)
        plan = m          # after a stuck: the map with the NPCs standing about made walls
        while attempt < 4:
            if getattr(self, "traveling", False) and not self.in_fight:
                beasts.ensure(as_bot(self), beasts.TRAVEL)   # back on the mount once a fight is behind us
            me = self.me()
            here = (int(me["x"]), int(me["y"]))
            if not m.ok(*here):
                # standing on a cell the map calls a wall (pushed against an edge, a fractional
                # position): plan from the nearest walkable one, or there is "no way" from here
                here = m.nearest_walkable(here[0], here[1], 4) or here
            hops = self._walk_hops(m, plan, mid, here, cell, diagonal)
            stuck = self._walk_hops_go(hops, diagonal, mid if map_change else None, near)
            if stuck in ("map", "near"):
                self.stuck_in_a_row = 0
                return stuck
            if stuck == "attacked":
                self.fight_back()
                attacks += 1
                if attacks < self.ATTACK_LIMIT:
                    continue      # re-plan from where the fight ended; not a stuck walk
                # Hit again and again with the fight never clearing the way: this used to re-plan
                # forever, so walk_to never returned. Give up the walk and let the caller plan.
                self.log(_("hit {n} times on the way - giving up this walk", n=attacks))
                return "stuck"
            if stuck == "stuck":
                diagonal = False
                # NPCs can't be walked through and the map doesn't know where they stand (the
                # survey stuck behind a smithy's three NPCs, 2026-10-11): plan round them now
                plan = m.without(self.npc_cells(cell))
            if not stuck:
                self.stuck_in_a_row = 0
                return "arrived"
            if self.clear_popups(force=True) or self.close_text_box():
                attempt += 1
                continue          # a window or a chat line held the keys, not a wall; walk again
            if self._walk_stuck(cell):
                return "stuck"
            attempt += 1
        return "stuck"

    @staticmethod
    def _walk_goal(m: Any, mid: int, cell: tuple[float, float], map_change: bool) -> tuple[int, int]:
        """The cell to walk to: a walkable one, and never a ledge unless the walk leaves the map."""
        goal: tuple[int, int] = (int(cell[0]), int(cell[1]))
        if not m.ok(*goal):
            alt = m.nearest_walkable(goal[0], goal[1], 20)
            if not alt:
                raise Stopped(_("{cell} can't be reached on {map}", cell="%d,%d" % goal, map=atlas.label(mid)))
            goal = alt
        if m.directional[goal[1], goal[0]] and not map_change:
            # never end a walk on a ledge (a cliff edge, a raised table's rim): on one only the
            # step across it moves us, so a stop there is a stuck
            goal = m.nearest_floor(goal[0], goal[1], 3) or goal
        return goal

    @staticmethod
    def _walk_hops(m: Any, plan: Any, mid: int, here: tuple[int, int], cell: tuple[int, int],
                   diagonal: bool) -> list[tuple[int, int]]:
        """The cells to walk through: straight there when the way is open, else round the walls."""
        corner = (cell[0], here[1])   # walk_axis goes east-west first, then north-south
        if diagonal and plan.diag_ok(here, cell):
            return [cell]
        if not diagonal and plan.line_ok(here, corner) and plan.line_ok(corner, cell):
            return [cell]
        # round the ledges if there is a way round, over them only when there isn't
        path = (plan.route(here, cell, avoid_directional=True) or plan.route(here, cell)
                or (m.route(here, cell) if plan is not m else None))
        if not path:
            raise Stopped(_("no way from {a} to {b} on {map}", a="%d,%d" % here,
                            b="%d,%d" % cell, map=atlas.label(mid)))
        return plan.diag_hops(path) if diagonal else plan.waypoints(path)

    def _walk_hops_go(self, hops: list[tuple[int, int]], diagonal: bool, leave: int | None,
                      near: Callable[[dict[str, Any]], bool] | None) -> str | bool:
        """Walk the hops: "map" or "near" (done early), "stuck" or "attacked" (broken off), or
        False (all walked). leave: the map id when the walk is meant to leave it."""
        for hop in hops:
            if self.walk_only:
                beasts.keep_riding(as_bot(self))   # thrown off or refused earlier: back on between hops
            if diagonal:
                results: Any = [self.walk_diag(hop, leave, near=near)]
            else:
                results = (self.walk_axis(axis, hop[axis], leave, near=near) for axis in (0, 1))
            for r in results:
                if r in ("map", "near", "stuck", "attacked"):
                    return r
        return False

    def _walk_stuck(self, cell: tuple[int, int]) -> bool:
        """A stuck walk: fight first, at every stuck (a monster in the way blocks us, and may be
        hitting us: turkey cave, two Orc Commanders stood on the character while the bot gave
        up), then step aside - or, after stuck_limit stucks in a row, the escape ladder. True when
        the walk ends here (the caller plans again from wherever we are)."""
        self.stuck_in_a_row += 1
        # a game that ran out of memory freezes the character where it stands: that is no stuck
        self.check_game(force=True)
        at = self.me()        # where the walk stopped, not where it began (the log misled us)
        at = "%d,%d" % (at["x"], at["y"])
        if not self.in_fight:
            self.fight_back()
            if self.walk_only:
                self.clear_way(cell)
        if self.stuck_in_a_row >= self.p["stuck_limit"]:
            if getattr(self, "_unsticking", False) or self.in_fight:
                return True          # get_unstuck tries its next way out / the fight skips this monster
            if self.get_unstuck(at):
                return True          # moved away: the caller plans again from here
            raise Stopped(_("stuck near {cell} and every way out failed - stopping", cell=at))
        self.log(_("stuck near {cell}, stepping aside", cell=at))
        self.key(self.move_key(random.choice(list(DIRECTIONS.values()))), 400)
        return False

    def get_unstuck(self, at: str) -> bool:
        """Stuck again and again in one place: fight what is near, then try every way out before
        giving up - push each way, walk to another spot on the map, leave the map by its nearest
        exit, and last the hearthstone. True once we are away from here (or on another map)."""
        self._unsticking = True
        try:
            self.log(_("stuck {n} times near {cell} - fighting, then trying to get out", n=self.stuck_in_a_row,
                       cell=at), speak=True)
            mid, start = self.map_id(), self.me()
            m = atlas.model(mid)
            steps = (lambda: self._push_out(mid, start), lambda: self._walk_out(m, mid, start),
                     lambda: self.leave_by_exit(m, start) and self._away(mid, start),
                     lambda: self.has_hearthstone() and self.go_home(tries=1))
            for step in steps:
                self.fight_back()
                try:
                    if step():
                        return True
                except Respawned:
                    raise
                except Stopped:
                    if self.stop_event.is_set():
                        raise           # the player pressed Stop
                    # this way out failed (no route, a key refused...): try the next one
            return False
        finally:
            self._unsticking = False
            self.stuck_in_a_row = 0

    def _away(self, mid: int, start: dict[str, Any]) -> bool:
        """On another map, or 6 tiles or more from where the stuck was."""
        if self.map_id() != mid:
            return True
        me = self.me()
        return math.hypot(me["x"] - start["x"], me["y"] - start["y"]) >= 6

    def _push_out(self, mid: int, start: dict[str, Any]) -> bool:
        for d in random.sample(list(DIRECTIONS.values()), 4):
            self.key(self.move_key(d), 1000)
            self.fight_back()
            if self._away(mid, start):
                self.log(_("got out by pushing {way}", way=d))
                return True
        return False

    def _walk_out(self, m: Any, mid: int, start: dict[str, Any]) -> bool:
        for _try in range(3):
            cell = self.escape_cell(m, start)
            if cell is None:
                return False
            self.stuck_in_a_row = 0
            self.walk_to(cell)
            if self._away(mid, start):
                self.log(_("got out: walked to {cell}", cell="%d,%d" % cell))
                return True
        return False

    def escape_cell(self, m: Any, me: dict[str, Any], near: int = 10, far: int = 30) -> tuple[int, int] | None:
        """A walkable cell 10-30 tiles away that we can reach and come back from, off every exit."""
        x0, y0 = int(me["x"]), int(me["y"])
        both = m.two_way(x0, y0) if m.ok(x0, y0) else None
        for _try in range(80):
            c = (x0 + random.randint(-far, far), y0 + random.randint(-far, far))
            if not m.ok(*c) or m.portal[c[1], c[0]] or math.hypot(c[0] - x0, c[1] - y0) < near:
                continue
            if both is not None and c[1] * m.W + c[0] not in both:
                continue
            return c
        return None

    def leave_by_exit(self, m: Any, me: dict[str, Any], why: str | None = None) -> bool:
        """Walk off this map through whichever exit is the shortest walk from here."""
        here = (int(me["x"]), int(me["y"]))
        best = None
        for e in atlas.maps().get(m.id, {}).get("exits", []):
            if e.get("dest") is None or not e.get("stand"):
                continue
            path = m.route(here, tuple(e["stand"]))
            if path and (best is None or len(path) < best[0]):
                best = (len(path), e)
        if best is None:
            return False
        e = best[1]
        self.log(why or _("leaving {map} for {dest} to get unstuck", map=atlas.label(m.id),
                          dest=atlas.label(e["dest"])))
        self.stuck_in_a_row = 0
        self.cross_exit({"map": m.id, "stand": e["stand"], "rect": e["rect"], "dest": e["dest"]})
        return True

    def blocking(self, me: dict[str, Any], e: dict[str, Any], toward: tuple[float, float] | None = None) -> bool:
        """Standing in our way: right against us, or a few steps ahead on the way to toward (a
        little zombie 3.3 steps ahead held the survey in corpse cave, 2026-10-11)."""
        dx, dy = e["x"] - me["x"], e["y"] - me["y"]
        d = math.hypot(dx, dy)
        if d <= self.BLOCKING:
            return True
        if toward is None or d > self.AHEAD:
            return False
        tx, ty = toward[0] - me["x"], toward[1] - me["y"]
        t = math.hypot(tx, ty)
        return t > 0.5 and (dx * tx + dy * ty) / (d * t) >= 0.5       # within 60 degrees of our way

    def clear_way(self, toward: tuple[float, float] | None = None) -> None:
        """Walking without fights (the walking assistant, the survey) and stuck: monsters standing
        right against us block the way as walls do, and riding past them doesn't work (corpse
        cave 2026-10-11: two zombies and two ghouls held the survey there for half an hour while
        fight_back, off for these walks, did nothing). Fight those - only those - and walk on."""
        was = self.walk_only
        self.walk_only = False
        try:
            for _round in range(6):
                snap = self.snap()
                me = snap["self"]
                close = [e for e in self.targets(snap) if self.blocking(me, e, toward)]
                if not close:
                    return
                if not self.modes_done:
                    self.modes_done = True
                    self.set_modes()
                self.log(_("{name} is in the way - fighting it", name=close[0]["name"]))
                self.fight(close[0])
        finally:
            self.walk_only = was

    def walk_dist(self, snap: dict[str, Any], e: dict[str, Any], limit: float | None = None) -> float:
        """Steps from us to the monster, around walls and one-way doors. In a maze the nearest one
        in a straight line can be a long walk round a wall (turkey cave), so targets are chosen by
        this. A straight walkable line needs no search; otherwise one search per cell we stand
        on, reused while we have moved less than two steps. math.inf when it is further than
        limit, or out of reach. Real steps (atlaslib steps), not the route search's cost: that adds
        up to 4 a step beside walls, and on patrol loops it left a third of the monsters within
        chase_range - most of them in caves - alone until they attacked."""
        me = snap["self"]
        straight = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        if limit is not None and straight > limit:
            return math.inf
        try:
            mid = snap_map(snap)
            m = atlas.model(mid)
        except STOPPING:
            raise
        except Exception as exc:
            note_failure(self, "the map data", exc)
            return straight
        here = (int(me["x"]), int(me["y"]))
        cell = (int(round(e["x"])), int(round(e["y"])))
        if not m.ok(*cell):
            cell = m.nearest_walkable(cell[0], cell[1], 3)
            if not cell:
                return straight     # standing where the map says wall: the map is wrong, not the monster
        if m.ok(*here) and m.line_ok(here, cell):
            return straight
        c = self._dists
        if not c or c[0] != mid or abs(c[1][0] - here[0]) + abs(c[1][1] - here[1]) > 2:
            src = here if m.ok(*here) else m.nearest_walkable(here[0], here[1], 4)
            if not src:
                return straight
            steps_arr = getattr(m, "steps_arr", None)
            field = steps_arr(src, limit=self.DIST_LIMIT) if steps_arr else m.steps(src, limit=self.DIST_LIMIT)
            c = self._dists = (int(mid), here, m, field, steps_arr is None)
        d = c[3].get(cell[1] * m.W + cell[0], -1) if c[4] else int(c[3][cell[1] * m.W + cell[0]])
        if d < 0:
            return math.inf
        return d if limit is None or d <= limit else math.inf
