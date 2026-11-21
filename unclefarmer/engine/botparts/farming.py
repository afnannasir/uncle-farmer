"""Farming a map, the day's drop time, and ending a run (safe spot, end place, restock).

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import math
import random
from collections.abc import Callable, Sequence
from typing import Any

from ... import atlas, travel
from ...settings import PK_MODES, TARGET_MODES
from ...i18n import _
from .. import beasts, bosses, chests, corpses, keyslots, pets, teamlead, xpcard
from .base import (MOVE_SHOT_REACH, LosingFight, Respawned, Stopped)
from .parts import as_bot, EndPlace, Parts


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
    said_level: bool
    said_level_inside: bool
    in_instance: bool = False     # farm(instance=True) sets these for its own run
    pack_min: int = 3
    MODE_WAIT = 0.3       # seconds a mode key's press has to show in the game's modes
    MODE_LOOK = 0.05      # ... looked at this often

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
                m = self._modes_after(field, m.get(field))
                if m.get(field) == want:
                    self.log(_("mode set: {mode}", mode=names[want]))
                    break
            else:
                self.log(_("couldn't set the mode to {mode} with the {key} key", mode=names[want], key=key),
                         speak=True)

    def _modes_after(self, field: str, was: object) -> dict[str, Any]:
        """The game's modes once a press of a mode key shows in them, looked at every MODE_LOOK
        seconds for MODE_WAIT at most. A fixed 0.3 s after every press made three presses (from
        "NPCs", where talking to a dungeon's entrance leaves the target key, round to "enemies")
        the first second of a cave's 15 minutes."""
        m: dict[str, Any] = {}
        for _look in range(max(1, round(self.MODE_WAIT / self.MODE_LOOK))):
            self.sleep(self.MODE_LOOK)
            m = self.a.json("mem modes")
            if m.get(field) != was:
                break
        return m

    def farm(self, map_id: int, area: Sequence[float] | None = None, minutes: float = 0, kills: int = 0, only: str = "", skip: str = "", patrol: str = "random",
             avoid_elites: bool = False, avoid_bosses: bool = True, until: Callable[[], bool] | None = None,
             instance: bool = False, pack: int | None = None, chase: int | None = None) -> None:
        """Farm a map until minutes or kills are reached (0 = no limit) or until() is true.
        instance: a dungeon we are standing in (engine/dungeon.py) - its visit can't be left and
        taken up again, so no trip out (restocking, a full bag, the secret room), no resting
        (potions instead: the timer runs), and being moved off the map ends it. pack: area skills
        from this many monsters close together (else 3). chase: on a patrol loop, chase what is
        within this many walking steps (else the profile's chase_range)."""
        map_id = int(map_id)
        if not self.restocked_once and not instance:
            # Before setting out: only what is down to its min (a full top-up of everything under
            # its stock made a trip to town for 199 potions of 200).
            self.restocked_once = True
            self.restock_now()
        if getattr(self, "bag_start", None) is None:
            from .. import town
            self.bag_start = town.counts(as_bot(self))   # what was ours before the run: never sold or stored
        if not instance:
            self.on_farm_run = True       # travel checks the restock rules on the way (restock_on_the_way)
            self.travel(map_id)
        self.farmed = True        # only a run that got to its map closes the game or the PC when it ends
        area = list(area) if area else atlas.monster_area(map_id)
        avoid: list[tuple[float, float, float, float]] = []
        if avoid_elites:
            avoid += [z["rect"] for z in atlas.zones(map_id, ("elite",))]
        if avoid_bosses:
            avoid += [z["rect"] for z in atlas.zones(map_id, ("boss",))]
        loop = atlas.patrol_loop(map_id, patrol) if patrol in ("full", "quick", "short") else None
        # On a patrol loop, only what is within chase_range walking steps is chased: the loop
        # passes near the rest (a short loop keeps nearly the whole map that close). Wandering at
        # random, the nearest is taken however far.
        steps = chase or self.p.get("chase_range", 40)
        chase = steps if loop else None
        self.farm_filter = (area, only, skip, avoid)
        if self.timing is None:
            self.timing = {"_since": self.clock.time(), "_kills": self.kills, "_contribution": self.contribution_now()}
        start, start_kills = self.clock.time(), self.kills
        self.log(_("farming {map}", map=atlas.label(map_id)))
        if not instance and self.p.get("shoot_while_walking", False):
            # once a farm: a character that fights from close by can't use it, and must hear why
            if self.reach() >= MOVE_SHOT_REACH:
                self.log(_("shooting monsters in reach on the way to the next one"))
            else:
                self.log(_("shooting while walking is on, but this character fights from close by - "
                           "it stops for each monster"))
        self.set_modes()
        self.modes_done = True

        def done():
            if minutes and (self.clock.time() - start) / 60.0 >= minutes:
                return True
            if kills and self.kills - start_kills >= kills:
                return True
            return bool(until and until())

        def sighted(_me: dict[str, Any]) -> bool:
            return bool(self.targets(self.snap(), area, only, skip, avoid, limit=chase))

        last_supply_check = self.clock.time()
        was = (self.in_instance, self.pack_min)
        self.in_instance, self.pack_min = instance, int(pack or 3)
        self.farming_on = None if instance else map_id      # where a lost fight may flee from (may_flee)
        tally = self._farm_begins(map_id, instance)
        try:
            self._farm_loop(map_id, area, only, skip, avoid, loop, chase, done, sighted, last_supply_check)
        finally:
            self._farm_ends(tally)
            self.farming_on = None
            self.on_farm_run = False
            self.in_instance, self.pack_min = was
            self.farm_filter = None
            self.timing_report(final=True)
            self.timing = None
        self.log(_("finished farming {map}", map=atlas.label(map_id)))

    def _farm_begins(self, map_id: int, instance: bool) -> Any:
        """A farm sets out: the results count this map from now (Tally.on_map, returned for the
        end), and outside a dungeon the team we lead hears requests and takes the waiting ones."""
        tally = getattr(self, "tally", None)
        if tally is not None:
            tally.on_map(map_id)          # the results, map by map
        if not instance:
            teamlead.start_farm(as_bot(self))
        return tally

    def _farm_ends(self, tally: Any) -> None:
        """The farm is over: its last reading (the kill that ended it) still counts for its map,
        then the map is closed. A reading that fails here (a stop, a lost game) leaves the rest to
        the run's own last reading, which counts for the run only."""
        if tally is None:
            return
        try:
            tally.update(self, force=True)
        except Exception:  # noqa: BLE001 - never in the way of what ended the farm
            pass
        tally.on_map(None)

    def _farm_loop(self, map_id: int, area: Sequence[float] | None, only: str, skip: str, avoid: list[tuple[float, float, float, float]], loop: list[tuple[int, int]] | None, chase: float | None, done: Callable[[], bool], sighted: Callable[[dict[str, Any]], bool], last_supply_check: float) -> None:
        loop_i = None
        while not done():
            try:
                self.timing_report()
                last_supply_check = self._restock_check(map_id, last_supply_check)
                if self.map_id() != map_id and self._off_map(map_id):
                    break
                snap = self.snap()
                self.count_kills(snap)     # what died of our hits since (area skills, damage over time)
                self.look_after(snap["self"])
                pets.keep_out(as_bot(self))
                pets.look_after(as_bot(self), snap)
                if self._between_fights(map_id):
                    continue          # we went to town: the loop travels back first
                if keyslots.refill(as_bot(self)):
                    continue
                xpcard.on_farm(as_bot(self))   # a paused experience card runs again, or a new one starts
                if bosses.keep_away(as_bot(self), snap, map_id, area):
                    continue
                if self._side_jobs(snap):
                    continue          # a chest opened, or a request to join our team taken
                if corpses.harvest(as_bot(self), snap):
                    continue          # Draw Blood, Skinning or Cut Flesh on a kill of ours
                mobs = self.targets(snap, area, only, skip, avoid, limit=chase)
                if mobs:
                    mob = self.next_target(snap, mobs)
                    if not self.in_instance and mob["id"] not in self.attackers() and self.rest_up(snap["self"]):
                        continue
                    self.note_pick(snap, mob, mobs)
                    self.fight(mob)
                    continue
                # Nothing to fight: keep moving (and say why, when monsters were in sight).
                self.explain_left_out(map_id)
                seen = self.sight_note()      # before the walk's own looks replace it
                me = snap["self"]
                no_go = bosses.no_go(as_bot(self), snap)   # bosses and elites, in sight or just seen
                if loop:
                    if loop_i is None:
                        loop_i = min(range(len(loop)), key=lambda i: math.hypot(loop[i][0] - me["x"], loop[i][1] - me["y"]))
                    cell, loop_i = _next_clear(loop, loop_i, no_go)
                else:
                    cell = (self.random_cell(map_id, area, me, clear_of=no_go, keep_out=avoid)
                            or self.random_cell(map_id, area, me))
                if cell is None:
                    self.sleep(1.0)
                    continue
                started = self.clock.time()
                with self.timed("search"):
                    result = self.walk_to(cell, near=sighted)
                self.note_search(map_id, me, cell, started, result, seen)
            except (Respawned, LosingFight) as setback:
                loop_i = None
                if self._set_back(map_id, setback):
                    break

    def _side_jobs(self, snap: dict[str, Any]) -> bool:
        """Between fights: a chest in sight, then a request to join the team we lead."""
        if chests.open_nearby(as_bot(self), snap):
            return True
        return not self.in_instance and teamlead.between_fights(as_bot(self))

    def _restock_check(self, map_id: int, last: float) -> float:
        """Every restock_check_every seconds, top up the restock rules and come back. Never in a
        dungeon: its visit can't be left and taken up again. Returns when it last looked."""
        if self.in_instance or self.clock.time() - last <= self.p.get("restock_check_every", 30):
            return last
        with self.timed("town"):
            if self.restock_now():
                self.log(_("back to {map}", map=atlas.label(map_id)))
                self.travel(map_id)
                self.set_modes()
        return self.clock.time()

    def _off_map(self, map_id: int) -> bool:
        """We are no longer on the farm's map. A dungeon is over (True: stop); elsewhere go back.
        After a trip of our own (the bag trip: away_reason) the log says so - it used to say "died
        or moved?" after every bag trip - and the restock rules are looked at before the town is
        left: whatever is down to its min is filled here, not on another trip later."""
        if self.in_instance:
            self.log(_("moved out of {map}", map=atlas.label(map_id)))
            return True
        why, self.away_reason = getattr(self, "away_reason", None), None
        with self.timed("town"):
            if why:
                self.restock_now()
                teamlead.after_trip(as_bot(self))    # members gone offline, when checked in town only
                self.log(_("back to {map} after {why}", map=atlas.label(map_id), why=why))
            else:
                self.log(_("left {map} (died or moved?) - going back", map=atlas.label(map_id)))
            self.travel(map_id)
        self.set_modes()
        return False

    def _between_fights(self, map_id: int) -> bool:
        """The bag, the sheet and the secret room between fights. True when a trip to town was made."""
        if self.in_instance:
            self.instance_reads()
            return False
        with self.timed("town"):
            return self.bag_check() or self.share_trip(map_id)

    FLEE_WINDOW = 30 * 60   # seconds: this many flees within it and the map is too hard (FLEE_MOST)
    FLEE_MOST = 3
    FLEE_RETRY = 60.0       # a hearthstone that didn't take us home isn't tried again sooner
    FLEE_HEALED = 90        # percent of HP to wait for in town...
    FLEE_HEAL_MOST = 180.0  # ...for at most this many seconds

    def _flee(self, map_id: int) -> None:
        """HP stayed low while farming (LosingFight): drop the fight, hearthstone to town, heal up
        there (FLEE_HEALED, potions as usual), look at the restock rules, and go back. A hearthstone
        that is interrupted every time (a hit stops its cast) leaves us fighting on. The FLEE_MOST-th
        flee within FLEE_WINDOW ends the run in town: the map is too hard for the character."""
        now = self.clock.time()
        self.flees = [t for t in getattr(self, "flees", []) if now - t < self.FLEE_WINDOW] + [now]
        self.log(_("HP stayed under {pct}% - leaving the fight: hearthstone to town", pct=self.p["panic_below"]),
                 speak=True)
        self.fleeing = True
        try:
            with self.timed("town"):
                if not self.go_home():
                    self.flee_wait_until = self.clock.time() + self.FLEE_RETRY
                    self.log(_("couldn't get away - fighting on"), speak=True)
                    return
                self._heal_in_town()
                if len(self.flees) >= self.FLEE_MOST:
                    self.log(_("fled {n} times in {m} minutes - this map is too hard for now; stopping here",
                               n=len(self.flees), m=self.FLEE_WINDOW // 60), speak=True)
                    stop = Stopped(_("fled too many lost fights"))
                    stop.stay = True
                    raise stop
                self.restock_now()
                self.log(_("back to {map} after fleeing a lost fight", map=atlas.label(map_id)))
                self.travel(map_id)
        except Respawned:
            return                # died after all: the farm loop's next round goes back
        finally:
            self.fleeing = False
        self.set_modes()

    def _heal_in_town(self) -> None:
        end = self.clock.time() + self.FLEE_HEAL_MOST
        while self.clock.time() < end:
            me = self.look_after()
            if 100.0 * me.get("hp", 0) / (me.get("maxhp") or 1) >= self.FLEE_HEALED:
                return
            self.sleep(1.0)

    def _set_back(self, map_id: int, setback: Exception) -> bool:
        """A death (Respawned) or a lost fight (LosingFight) broke into the farm loop. True: stop."""
        if isinstance(setback, LosingFight):
            self._flee(map_id)
            return False
        return self._respawned(map_id)

    def _respawned(self, map_id: int) -> bool:
        """After a death: back to the map. In a dungeon there is no going back once outside (True:
        stop); back on its spawn point inside, it goes on."""
        if self.in_instance:
            if self.map_id() != map_id:
                self.log(_("died and came back outside {map}", map=atlas.label(map_id)), speak=True)
                return True
            return False
        self.log(_("going back to {map}", map=atlas.label(map_id)))
        try:
            with self.timed("town"):
                self.travel(map_id)   # a no-op after a map respawn there
        except Respawned:
            return False          # died again on the way: the next round handles it
        self.set_modes()
        return False

    def instance_reads(self) -> None:
        """What bag_check reads between fights, without what it may do about it: inside a dungeon
        a full bag or the day's drop time can't send us out - the visit would be lost."""
        sheet = self.refresh_sheet()
        if self.tally:
            self.tally.update(self)
        if sheet:
            self.level_check(sheet, inside=True)

    def share_trip(self, map_id: int) -> bool:
        """Leave the farm for a short secret room visit when one is due (chores.trip_due: just
        before a half hour, so one visit shares arnebia in two of them), then go back. True when
        it went."""
        from .. import chores          # chores uses the town errands, which use the whole Bot
        if not chores.trip_due(as_bot(self)):
            return False
        self.log(_("off to the secret room to share arnebia"))
        me = self.me()
        farm = (map_id, (float(me.get("x", 0)), float(me.get("y", 0))))     # where the trip comes back to
        try:
            made, _where = chores.share_visit(as_bot(self), chores.config(self.p), back_to=farm)
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
        got = self.drop_to_farm(sheet)
        if got is None:
            return
        left, spare = got
        if spare > 0:
            if not getattr(self, "said_drop", False):
                self.said_drop = True
                mins = int(spare / 60)
                self.log(_("drop time: {h} h {m} min left; farming stops in about {n} minutes",
                           h=int(left // 3600), m=int(left % 3600 // 60), n=mins))
            return
        self.log(_("drop time is nearly used up ({m} min left) - going somewhere safe and stopping",
                   m=max(0, int(left // 60))), speak=True)
        self.finish()
        raise Stopped(_("drop time used up"))

    def drop_to_farm(self, sheet: dict[str, Any]) -> tuple[float, float] | None:
        """(drop time left, seconds of it still to farm) from the sheet: what is left past the
        time kept (this run's keep_drop, else the character's drop_keep_minutes) and the few
        minutes it keeps running after we leave (drop_tail_seconds). None when there is no such
        stop, or the sheet doesn't say."""
        plan_keep = getattr(self, "drop_keep", None)
        if plan_keep is None and not self.p.get("stop_on_drop_time", True):
            return None
        left = sheet.get("drop_seconds", -1)
        if not isinstance(left, (int, float)) or left < 0:
            return None
        left -= sheet.get("age", 0)
        keep = 60 * (plan_keep if plan_keep is not None else self.p.get("drop_keep_minutes", 0))
        return left, left - self.p.get("drop_tail_seconds", 300) - keep

    def drop_before_start(self) -> None:
        """Before a farming run sets out (with level_before_start): the drop time is already down
        to what is kept, so the whole way to the farm would be made for the first sheet there to
        send the run back. Say so and stay where we are, as for the level."""
        if getattr(self, "drop_keep", None) is None and not self.p.get("stop_on_drop_time", True):
            return                       # no such stop: no sheet to read
        got = self.drop_to_farm(self.refresh_sheet(force=True) or {})
        if got is None or got[1] > 0:
            return
        self.log(_("the drop time is nearly used up ({m} min left) - not starting to farm",
                   m=max(0, int(got[0] // 60))), speak=True)
        stop = Stopped(_("drop time used up"))
        stop.stay = True
        raise stop

    def level_goal(self) -> int | None:
        """The experience the next level must still need for farming to go on: this run's
        (keep_level from a plan or script, 0 = no such stop for this run), else the character's
        "Stop before the next level"; None when there is no such stop."""
        plan_keep = getattr(self, "level_keep", None)
        if plan_keep is not None:
            return int(plan_keep) or None
        if not self.p.get("stop_before_level", False):
            return None
        return int(self.p.get("level_keep_xp", 100000) or 0) or None

    def level_check(self, sheet: dict[str, Any], inside: bool = False) -> None:
        """End the run before the next level: the sheet's "upgrading requires N experience"
        (xp_needed, read after every kill from agent 43) is under the goal. For players who keep a
        character at its level (the XP loser in the After farming list can then put experience
        back). Inside a dungeon it only says so, once: leaving would lose the visit. Nothing at
        the top level (the sheet has no such line: -1)."""
        goal = self.level_goal()
        need = sheet.get("xp_needed")
        if not goal or not isinstance(need, (int, float)) or need < 0:
            return
        need = int(need)
        if need >= goal:
            if not getattr(self, "said_level", False):
                self.said_level = True
                self.log(_("the next level needs {n} experience; farming stops under {goal}", n=need, goal=goal))
            return
        if inside:
            if not getattr(self, "said_level_inside", False):
                self.said_level_inside = True
                self.log(_("the next level needs only {n} experience - the dungeon goes on, leaving would "
                           "lose the visit", n=need), speak=True)
            return
        self.log(_("the next level needs only {n} experience - going somewhere safe and stopping", n=need),
                 speak=True)
        self.finish()
        raise Stopped(_("stopped before the next level"))

    def level_before_start(self) -> None:
        """Before a farming run sets out (before the Before farming list): with the next level
        already closer than the goal, say so and don't start - otherwise the whole way to the farm
        is made for the first sheet there to send it back. Stays where it is: nothing ran, so no
        end place, no After farming list, no closing the game."""
        goal = self.level_goal()
        if not goal:
            return
        sheet = self.refresh_sheet(force=True) or {}
        need = sheet.get("xp_needed")
        if not isinstance(need, (int, float)) or not 0 <= need < goal:
            return
        self.log(_("the next level needs only {n} experience - not starting to farm", n=int(need)), speak=True)
        stop = Stopped(_("stopped before the next level"))
        stop.stay = True
        raise stop

    def leave_monsters(self) -> None:
        """End a run somewhere no monster spawns: if this map has monster zones, travel to the
        nearest map that has none (towns, transit maps). A "safe spot" inside a spawn map is not
        safe - monsters spawn and walk there too. Already in a city or village: that is the place
        (the game's table lists "monster" zones for Tulip City's own streets)."""
        here = self.map_id()
        if not atlas.zones(here):
            return
        if self._in_town():
            self.log(_("already in a town - staying here"))
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
        If the way there fails, the nearest map without monsters. After gathering (and nothing
        killed since), the way is walked without fights, as the walking assistant goes."""
        from .. import gathering      # here: gathering imports the Bot, which this is part of
        if gathering.going_home(as_bot(self)):
            with gathering.homeward(as_bot(self)):
                return self._finish_at()
        return self._finish_at()

    def _finish_at(self) -> None:
        how: EndPlace = self.end_place or ("start",)
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
                        self.log(_("where the run started wasn't read - ending on the nearest map without monsters"),
                                 speak=True)
                        return self._finish_safe()
                    mid, cell = self.start_place[0], (self.start_place[1], self.start_place[2])
                    if self._back_in_the_base(mid, (int(cell[0]), int(cell[1]))):
                        return self._finish_off()
                    if self.map_id() != mid and travel.is_instance(mid):
                        # a run started inside a dungeon that has moved us out: no way back in
                        self.log(_("the run started inside {map}, which can't be walked back into - ending "
                                   "on the nearest map without monsters", map=atlas.label(mid)))
                        return self._finish_safe()
                    if self.spawns_at(mid, cell):
                        # (a city in a field, such as central plains: a start outside its walls is
                        # monster ground, and the nearest map without monsters may be underground)
                        self.log(_("the run started where monsters spawn ({map} {x},{y}) - ending on the "
                                   "nearest map without them", map=atlas.label(mid), x=int(cell[0]), y=int(cell[1])))
                        return self._finish_safe()
                else:
                    if len(how) != 3:
                        return self._finish_safe()      # a place with no map in it
                    spot = how[2]
                    mid, cell = how[1], ((spot[0], spot[1]) if spot else None)
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

    def _in_town(self) -> bool:
        me = self.me()
        return atlas.in_town(self.map_id(), float(me.get("x", 0)), float(me.get("y", 0)))

    def _back_in_the_base(self, mid: int, cell: tuple[int, int]) -> bool:
        from .. import chores         # (chores needs the Bot this mixin is part of)
        return chores.back_to_gang_base(as_bot(self), mid, cell, getattr(self, "start_name", None))

    def _finish_off(self) -> None:
        beasts.off(as_bot(self))
        if self.p.get("pet_recall_at_end", False):
            pets.recall(as_bot(self))
        teamlead.at_end(as_bot(self))     # the team handed over, when the Team tab says so

    def _finish_safe(self) -> None:
        """The old ending: somewhere no monster spawns. The hearthstone is the shortest way there
        (the home city counts as safe); if it doesn't work, walk."""
        if self.p.get("end_safe", True) and atlas.zones(self.map_id()):
            if self._in_town():
                self.leave_monsters()         # (says so, and stays)
            elif self.p.get("end_with_hearthstone", True) and self.has_hearthstone():
                self.log(_("going home with the hearthstone"))
                if not self.go_home(tries=1):
                    self.leave_monsters()
            else:
                self.leave_monsters()
        self._finish_off()

    def restock_now(self, start: bool = False) -> bool:
        """Top up the profile's restock rules if they need it (storehouse first, then shops)."""
        rules: list[dict[str, Any]] = self.p.get("restock") or []
        if getattr(self, "restocking", False):
            if start:
                self.log(_("Restock now: a restock is already running."), speak=True)
            return False
        if not rules:
            if start:
                self.log(_("Restock now: no supply rules are configured in Character, Bag and supplies."), speak=True)
            return False
        from .. import town
        self.restocking = True            # its own walks to town must not restock again on the way
        try:
            return town.restock(as_bot(self), rules, start)
        finally:
            self.restocking = False

    def restock_on_the_way(self) -> None:
        """On a farm run's trip between maps, every restock_check_every seconds as the farm loop
        does: an item down to its min sends us to a town now, and the trip goes on from there (the
        travel loop plans again from wherever we are). Not on the walking assistant, not in a
        dungeon, not on a restock's own walk."""
        if not getattr(self, "on_farm_run", False) or self.walk_only or self.in_instance:
            return
        now = self.clock.time()
        if now - getattr(self, "restock_looked", 0.0) <= self.p.get("restock_check_every", 30):
            return
        self.restock_looked = now
        self.restock_now()

    def random_cell(self, map_id: int, area: Sequence[float] | None, me: dict[str, Any], radius: int = 40, *,
                    clear_of: Sequence[tuple[float, float, float]] = (),
                    keep_out: Sequence[tuple[float, float, float, float]] = ()) -> tuple[int, int] | None:
        """A walkable cell near us in the area that we can walk to: never on a map exit (stepping on
        one leaves the map mid-farm) and never in a part of the map walled off from ours (walk_to
        would find no way and stop the run). clear_of: circles (x, y, radius) it is never in - the
        bosses and elites the patrol keeps away from (bosses.no_go); keep_out: rectangles likewise
        (the map's boss and elite zones). None when no such cell turned up."""
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
            if clear_of and not bosses.clear_of(c, clear_of):
                continue
            if any(r[0] <= c[0] <= r[2] and r[1] <= c[1] <= r[3] for r in keep_out):
                continue
            if both is not None and c[1] * m.W + c[0] not in both:
                continue
            if both is None and ours is not None and m.comp_of(*c) != ours:
                continue
            return c
        return None


def _next_clear(loop: list[tuple[int, int]], i: int,
                no_go: Sequence[tuple[float, float, float]]) -> tuple[tuple[int, int], int]:
    """The next cell of a patrol loop from i that keeps clear of the no-go circles, and the index
    after it. Every cell in them: the next one anyway (keep_away deals with what comes close)."""
    for k in range(len(loop)):
        j = (i + k) % len(loop)
        if bosses.clear_of(loop[j], no_go):
            return loop[j], (j + 1) % len(loop)
    return loop[i], (i + 1) % len(loop)
