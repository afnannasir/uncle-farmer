# pyright: strict
"""Getting from map to map: travel, teleports, the hearthstone, rides, the walking assistant.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

import math
import re
from typing import Any, cast

from ... import atlas, dungeons, knowledge, travel
from ...i18n import _
from .. import beasts
from .base import (CONFIRM, STOPPING, Stopped, note_failure)
from .parts import Parts


class TravelMixin(Parts):
    traveling: bool
    RIDE_TRIES = 2        # a transport NPC that fails this often in one trip is left out of it

    def has_hearthstone(self) -> bool:
        """A hearthstone on a hotkey, or one in the bag."""
        if self.p.get("hearthstone_key") or self.hotkey_of(self.p.get("hearthstone_item") or "Hearthstone"):
            return True
        item = (self.p.get("hearthstone_item") or "Hearthstone").lower()
        try:
            return any((r.get("name") or "").lower() == item and r.get("count", 0) > 0 for r in self.bag_rows())
        except STOPPING:
            raise
        except Exception as exc:
            note_failure(self, "reading the bag", exc)
            return False

    def go_home(self, tries: int = 3) -> bool:
        """Use the hearthstone and make sure it worked: the map changes, or - when home is on the
        map we stand on (a character that respawned there) - the game reloads it and puts us far
        from where we stood."""
        item = self.p.get("hearthstone_item") or "Hearthstone"
        key = self.p.get("hearthstone_key") or self.hotkey_of(item)    # a hotkey beats the bag
        for _attempt in range(tries):
            start = self.map_id()
            was = self.me()
            if key:
                self.log(_("using the hearthstone key"))
                self.key(key)
            elif not self.use_item(item):
                return False
            self.last_hp = self.me().get("hp")   # a hit from now on interrupts the cast, not an older one
            deadline = self.clock.time() + 25
            while self.clock.time() < deadline:
                self.sleep(0.5)
                if self.map_id() != start:
                    if self.settled_map() == start:
                        me = self.me()
                        if math.hypot(me["x"] - was["x"], me["y"] - was["y"]) < 15:
                            continue
                    self.log(_("home: {map}", map=atlas.label(self.map_id())), speak=True)
                    self.learn_hearth_home()
                    return True
                if self.hit(self.me()):
                    break            # the cast was interrupted by an attack
            self.fight_back()
        self.log(_("the hearthstone didn't take me home"), speak=True)
        return False

    @staticmethod
    def spawns_at(mid: int, cell: tuple[float, float]) -> bool:
        """True when monsters spawn on that map, so a run that started there ends elsewhere. A town
        is the exception: the atlas marks a whole city as a spawn zone for the ducks and hens that
        wander its streets, and a run started in the town goes back there."""
        x, y = cell
        for c in knowledge.cities():
            r = c.get("town_rect")
            if c.get("map") == mid and r and r[0] <= x <= r[2] and r[1] <= y <= r[3]:
                return False
        return bool(atlas.zones(mid))

    def learn_hearth_home(self) -> None:
        """Remember which square the hearthstone lands on, so travel can plan with it."""
        mid = self.map_id()
        me = self.me()
        best = None
        for name, (sm, sx, sy) in travel.SQUARES.items():
            if sm == mid:
                d = math.hypot(sx - me["x"], sy - me["y"])
                if best is None or d < best[0]:
                    best = (d, name)
        if best and best[0] < 40 and self.p.get("hearth_home") != best[1]:
            self.p["hearth_home"] = best[1]
            self.log(_("the hearthstone takes me to {square} - remembered", square=best[1]))
            if self.save_profile:
                self.save_profile(self.p)

    def travel(self, dst: int | str, cell: tuple[float, float] | None = None) -> None:
        """Go to map dst (and cell) from wherever we are: on foot, by cable car, airship, a menu NPC
        or a travel item from the bag (teleport stones, the hearthstone). The route is planned again
        after every leg, so a wrong guess only costs a re-plan; whatever fails is left out after."""
        dst = int(dst)
        avoid: set[Any] = set()
        if not self.p.get("use_airships", True):
            avoid |= {t["id"] for t in travel.transports() if t["kind"] == "airship"}
        self.traveling = True
        try:
            self._travel(dst, cell, avoid)
        finally:
            self.traveling = False

    def _travel(self, dst: int, cell: tuple[float, float] | list[float] | None, avoid: set[Any]) -> None:
        goal_cell = cast(tuple[float, float], tuple(cell)) if cell is not None else None
        said = None
        failed: dict[Any, int] = {}       # transport id -> rides that didn't work this trip
        for _leg in range(40):
            mid = self.map_id()
            if mid == dst and cell is None:
                return
            beasts.ensure(self, beasts.TRAVEL)   # again each leg: mounting waits out combat
            legs = self._plan_legs(mid, dst, goal_cell, avoid)
            if not legs:
                return
            steps = travel.describe(legs)
            if steps != said and len(legs) > 1:
                self.log(_("route to {map}: {steps}", map=atlas.label(dst), steps=" / ".join(steps)))
            said = steps[1:]
            if self._take_leg(legs[0], avoid, failed):
                return
        raise Stopped(_("travel to {map} didn't arrive", map=atlas.label(dst)))

    def _plan_legs(self, mid: int, dst: int, goal_cell: tuple[float, float] | None, avoid: set[Any]) -> list[dict[str, Any]]:
        """The way from here, as the profile wants it planned; a Stopped when there is none."""
        me = self.me()
        bag = self.bag_rows() if self.p.get("use_travel_items", False) else []
        legs = None
        if self.p.get("prefer_walking", False):
            # Optional: walk whenever the maps connect on foot (the default is the shortest way,
            # cable cars and airships included; bag items stay off unless use_travel_items).
            legs = cast(list[dict[str, Any]] | None, travel.plan(mid, (me["x"], me["y"]), dst, goal_cell, transport=False, avoid=avoid,
                               bag=[], items=False))
        if legs is None:
            legs = cast(list[dict[str, Any]] | None, travel.plan(mid, (me["x"], me["y"]), dst, goal_cell,
                           transport=self.p.get("use_transport", True), avoid=avoid,
                           vip=self.p.get("vip", False), bag=bag,
                           hearth_home=self.p.get("hearth_home") or None,
                           hearth_key=bool(self.p.get("hearthstone_key")),
                           items=self.p.get("use_travel_items", False)))
        if legs is None:
            raise Stopped(_("no way from {a} to {b} - go there yourself and start again",
                            a=atlas.label(mid), b=atlas.label(dst)))
        return legs

    def _take_leg(self, leg: dict[str, Any], avoid: set[Any], failed: dict[Any, int]) -> bool:
        """The route's first leg. What failed is left out of the next plan. True: the trip is done."""
        if leg["do"] == "exit":
            self.cross_exit(leg)
        elif leg["do"] == "npc":
            if not self.ride(leg):
                failed[leg["id"]] = failed.get(leg["id"], 0) + 1
                if failed[leg["id"]] < self.RIDE_TRIES and self.map_id() == leg["map"]:
                    # once more before walking the long way round: a walk across several maps
                    # costs minutes, another try at the NPC seconds
                    self.log(_("trying the {npc} once more", npc=leg["npc"]))
                    return False
                avoid.add(leg["id"])
                self.log(_("leaving out the {npc} on {map} and planning again",
                           npc=leg["npc"], map=atlas.label(leg["map"])))
        elif leg["do"] == "item":
            if not self.teleport(leg):
                avoid.add(leg["item"])
        elif leg["do"] == "leave":
            if not self.leave_instance():
                avoid.add("leave")
        else:
            self.walk_to(tuple(leg["cell"]))
            return True
        return False

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        self.last_hp = self.me().get("hp")       # only a hit from now on counts as an interruption
        end = self.clock.time() + seconds
        while self.clock.time() < end:
            if self.map_id() != mid:
                self.settled_map()
                self.sleep(1.0)
                return True
            if self.hit(self.me()):
                return False      # attacked: the cast was interrupted
            self.sleep(0.3)
        return False

    def teleport(self, leg: dict[str, Any]) -> bool:
        """Use a travel item from the bag; choose the destination if it asks; wait to land."""
        mid = self.map_id()
        self.log(_("using {item} to go to {map}", item=leg["item"], map=atlas.label(leg["dest"])))
        if leg["item"] == "Hearthstone" and self.p.get("hearthstone_key"):
            self.key(self.p["hearthstone_key"])
        elif not self.use_item(leg["item"]):
            return False
        try:
            for pick in cast(list[str], leg.get("choose") or []):
                if not self.wait_dialog(4.0) or self.choose(pick) is None:
                    return False
            ok = self.wait_map_change(mid, 30 if leg["item"] == "Hearthstone" else 15)
            if ok and leg["item"] == "Hearthstone":
                self.learn_hearth_home()
            if not ok:
                self.log(_("{item} didn't take me anywhere", item=leg["item"]), speak=True)
            return ok
        finally:
            if self.map_id() == mid:
                self.close_dialogs()

    def leave_instance(self) -> bool:
        """Leave a dungeon, secret room or event map: the Action menu (W_LOCAL_OPTION) has a
        'leave ...' entry there."""
        mid = self.map_id()
        self.log(_("{map}: leaving through the Action menu", map=atlas.label(mid)))
        self.close_dialogs()
        self.key(self.p.get("action_menu_key", "lctrl+enter"))
        if not self.wait_dialog(3.0):
            return False
        if self.choose(r"^\s*leave\s|return to|open the treasure chest and return") is None:
            return False
        for _i in range(2):      # "are you sure?"
            d = self.dialog()
            if d["open"] == 0 or self.map_id() != mid:
                break
            if self.find_entry(d["top"], CONFIRM) is None:
                break
            self.choose(CONFIRM)
        ok = self.wait_map_change(mid, 15)
        if not ok:
            self.close_dialogs()
        return ok

    def cross_exit(self, leg: dict[str, Any]) -> None:
        """Walk into a map exit, pushing off the edge if walking onto it isn't enough."""
        mid = leg["map"]
        self.log(_("{map}: heading for the exit to {dest}", map=atlas.label(mid), dest=atlas.label(leg["dest"])))
        if self.walk_to(tuple(leg["stand"]), map_change=True) != "map":
            edge = atlas.edge_direction(mid, leg["rect"])
            if edge:
                k = self.move_key(edge)
                self.hold(k)
                try:
                    for _i in range(75):
                        self.clock.wait(self.stop_event, 0.04)
                        self.check()
                        if self.map_id() != mid:
                            break
                finally:
                    self.let_go(k)
            elif not self._step_on_again(mid, (int(leg["stand"][0]), int(leg["stand"][1]))):
                self.sleep(2.0)
        self.sleep(1.0)

    def _step_on_again(self, mid: int, stand: tuple[int, int]) -> bool:
        """An exit inside the map (Loki Union's is the very cell the airship lands on) takes us
        through only as we step onto it, so standing on it does nothing: step off and back on.
        Live 2026-10-23 the bot stood there re-planning every 4 s for 20 s. True when it moved."""
        try:
            m = atlas.model(mid)
        except Exception as e:
            note_failure(self, "stepping onto an exit", e)
            return False
        for dx, dy in ((1, 0), (-1, 0), (0, 1), (0, -1)):
            cell = (stand[0] + dx, stand[1] + dy)
            if m.ok(*cell):
                if self.walk_to(cell, map_change=True) == "map":
                    return True
                self.walk_to(stand, map_change=True)
                return True
        return False

    def go(self, dst: int, cell: tuple[float, float] | None = None, npc: str | None = None, talk: bool = False) -> None:
        """The walking assistant: to map dst (a cell on it, or up to the NPC named npc, standing
        near cell) the shortest way, riding the fastest Swift fusion beast the whole way - through
        towns and through monster ground alike - and never stopping to fight: a Swift beast can't
        use skills, and it outruns what it passes. Potions still keep HP up. talk: open the NPC's
        menu on arrival (the player chooses from it)."""
        was = self.walk_only
        self.walk_only = True
        try:
            swift = [r for r in beasts.owned(self) if r.get("kind") == "swift"]
            if not swift:
                self.log(_("no Swift fusion beast on this character - walking on foot"), speak=True)
            if npc:
                self.travel(dst)
                if self.approach(npc, cell) is None:
                    raise Stopped(_("couldn't get to {npc}", npc=npc))
                self.log(_("arrived next to {npc}", npc=npc), speak=True)
                if talk and not self.talk(npc, cell):
                    raise Stopped(_("couldn't talk to {npc}", npc=npc))
            else:
                self.travel(dst, cell)
                self.log(_("arrived on {map}", map=atlas.label(dst)), speak=True)
        finally:
            self.walk_only = was

    def go_dungeon(self, entry: dict[str, Any], talk: bool = False) -> None:
        """The walking assistant to a dungeon's entrance (an entry from dungeons.entrances()). With
        its cell known, that is go() to its NPC. Where the game's files only mark several entrances
        on the map, walk to each marked spot until the game shows this dungeon's circle, remember
        where it stands, and go up to it."""
        if entry.get("at"):
            return self.go(entry["map"], tuple(entry["at"]), entry["npc"], talk)
        was = self.walk_only
        self.walk_only = True
        try:
            if not [r for r in beasts.owned(self) if r.get("kind") == "swift"]:
                self.log(_("no Swift fusion beast on this character - walking on foot"), speak=True)
            mid = entry["map"]
            self.travel(mid)
            m = atlas.model(mid)

            def seen(_me: Any = None) -> dict[str, Any] | None:
                return next((e for e in self.snap()["entities"]
                             if e.get("id", 0) < 0 and dungeons.is_entrance(entry, e.get("name"))), None)

            npc = seen()
            todo = [tuple(s[:2]) for s in entry.get("spots", [])]
            if not npc and todo:
                self.log(_("looking for the {name} circle at the {n} entrances marked on {map}",
                           name=entry["name"], n=len(todo), map=atlas.label(mid)))
            while npc is None and todo:
                me = self.me()
                spot = min(todo, key=lambda c: math.hypot(c[0] - me["x"], c[1] - me["y"]))
                todo.remove(spot)
                cell = spot if m.ok(*spot) else m.nearest_walkable(spot[0], spot[1], 10)
                if not cell:
                    continue
                def near_entrance(_me: Any) -> bool:
                    return seen() is not None
                self.walk_to(cell, near=near_entrance)
                if self.map_id() != mid:
                    raise Stopped(_("left {map} while looking for the {name} circle", map=atlas.label(mid),
                                    name=entry["name"]))
                npc = seen()
            if npc is None:
                raise Stopped(_("none of the entrances marked on {map} is the {name} circle",
                                map=atlas.label(mid), name=entry["name"]))
            dungeons.learn(entry, npc["name"], npc["x"], npc["y"])
            self.log(_("found it: {npc} at {x},{y} - remembered", npc=npc["name"], x=int(npc["x"]),
                       y=int(npc["y"])))
            at = (int(round(npc["x"])), int(round(npc["y"])))
            if self.approach(npc["name"], at) is None:
                raise Stopped(_("couldn't get to {npc}", npc=npc["name"]))
            self.log(_("arrived next to {npc}", npc=npc["name"]), speak=True)
            if talk and not self.talk(npc["name"], at):
                raise Stopped(_("couldn't talk to {npc}", npc=npc["name"]))
        finally:
            self.walk_only = was

    def ride(self, leg: dict[str, Any]) -> bool:
        """Talk to a transport NPC, choose the destination, confirm, and wait to be moved."""
        mid = leg["map"]
        self.log(_("{map}: taking the {npc} to {dest}", map=atlas.label(mid), npc=leg["npc"],
                   dest=atlas.label(leg["dest"])))
        try:
            return self._ride(leg, mid)
        finally:
            if self.map_id() == mid:
                self.close_dialogs()
            self.set_target_mode(self.p.get("target_mode", 2))

    def _ride(self, leg: dict[str, Any], mid: int) -> bool:
        if not self.talk(leg["npc"], leg.get("at"), leg.get("stand")):
            return False
        steps = cast(list[str], leg["choose"]) if isinstance(leg["choose"], list) else cast(list[str], [leg["choose"]])
        for i, pick in enumerate(steps):
            if i and not self.wait_dialog(3.0):
                self.log(_("the {npc}'s next menu didn't open", npc=leg["npc"]), speak=True)
                return False
            if self.choose(pick) is None:
                return False
        # Some menus ask again (pay? sure?): accept that, never anything else.
        for _i in range(3):
            if self.map_id() != mid:
                break
            d = self.dialog()
            if d["open"] == 0:
                break
            if self.find_entry(d["top"], CONFIRM) is None:
                self.note_dialog(d, "after choosing, no confirm entry")
                self.log(_("the {npc} said no - see logs\\dialogs.log", npc=leg["npc"]), speak=True)
                self.close_dialogs()
                return False
            self.choose(CONFIRM)
        end = self.clock.time() + 15
        while self.clock.time() < end:
            if self.map_id() != mid:
                here = self.settled_map()
                if here == mid:
                    continue
                self.log(_("now on {map}", map=atlas.label(here)))
                self.sleep(1.0)
                if re.search(r"cable car|airship", atlas.name(here), re.I):
                    # The vehicle carries us itself (live 2026-10-04: cable car 1055 moved us to
                    # Tulip City's underground 37 s after boarding; every step in it was refused,
                    # IN op=101). Wait for that instead of walking at the door.
                    self.log(_("riding - waiting to arrive"))
                    if self.wait_map_change(here, 120):
                        self.log(_("arrived on {map}", map=atlas.label(self.map_id())))
                return True
            self.sleep(0.3)
        self.log(_("the {npc} didn't take me anywhere", npc=leg["npc"]), speak=True)
        self.close_dialogs()
        return False
