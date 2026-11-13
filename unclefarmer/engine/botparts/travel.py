"""Getting from map to map: travel, teleports, the hearthstone, rides, the walking assistant.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import math
from typing import Any, cast

from ... import atlas, dungeons, jsonio, knowledge, travel
from ...i18n import _
from .. import beasts, combat, combos, keyslots, teleskill, xpcard
from .base import (CONFIRM, STOPPING, Stopped, note_failure)
from .parts import as_bot, Parts


WaysOut = dict[int, tuple[int, tuple[float, float]]]

# Polson Wetland's poisoned ground hurts whoever stands on it, unless the General talent Poison
# Immunity ("Immune to Poisoned Terrain Effect Damage") is activated - or the character is on a
# fusion beast, any kind. So without the talent a trip THROUGH it (the only way to the Underground
# Water Cave and the sailing port) gets on a beast first and stays on it, fighting nothing on the
# way; a trip that ends there (farming, gathering) goes, with a warning: fights there are on foot.
# Talents are read as combos.py reads them: `mem skills`, activated ones flagged b == 1.
POISON_MAP = 5141
POISON_TALENT = "Poison Immunity"
POISON_MOUNT_WAIT = 30.0    # seconds to wait for a beast to go on before giving up the crossing


def ways_out(b: object) -> WaysOut:
    """{map we can't plan out of: (the map its way out leads to, the cell)}, learned this run."""
    got: object = getattr(b, "ways_out", None)
    return cast(WaysOut, got) if isinstance(got, dict) else {}


def rides_free(b: object) -> bool:
    """Plan this character's rides as a VIP's, free: the profile says VIP, and no ride's free entry
    has been refused this run. The profile itself is never changed - a VIP that ran out is the
    player's to untick (or renew), and the next run says so again."""
    p: object = getattr(b, "p", None)
    told = isinstance(p, dict) and bool(cast("dict[str, Any]", p).get("vip", False))
    return told and not getattr(b, "vip_refused", False)


def note_way_out(b: object, inside: int, to_map: int, cell: tuple[float, float]) -> None:
    """Remember that leaving map `inside` puts us on to_map at cell (where we came in from)."""
    known = ways_out(b)
    known[int(inside)] = (int(to_map), (float(cell[0]), float(cell[1])))
    b.__dict__["ways_out"] = known


class TravelMixin(Parts):
    traveling: bool
    RIDE_TRIES = 2        # a transport NPC that fails this often in one trip is left out of it
    SPELL_TRIES = 3       # a teleport skill broken off (a hit, no menu) this often in one trip is left out
    # Longest wait on a ride map (atlas.is_ride) for the game to move us on: flights took about
    # 25 s and cable cars 37 s in the logs; well past that the ride has gone wrong.
    RIDE_WAIT = 180.0
    NPC_MODE = 3          # TARGET_MODES index: the target keys pick NPCs

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
        """Go to map dst (and cell) from wherever we are: on foot, by cable car, airship, a menu NPC,
        a teleport skill (Mage, Elf) or a travel item from the bag (teleport stones, the hearthstone). The route is planned again
        after every leg, so a wrong guess only costs a re-plan; whatever fails is left out after."""
        dst = int(dst)
        if dst == POISON_MAP and self.map_id() != POISON_MAP:
            self._warn_poison_stay()               # before anything moves (see POISON_MAP)
        if dst != self.map_id() and (getattr(self, "on_farm_run", False) or getattr(self, "farmed", False)):
            # a farming run leaving the monsters (town, back after a death, home): no experience
            # to make on the way, so a running experience card is paused (xp_card_pause)
            xpcard.away(as_bot(self), _("going to {map}", map=atlas.label(dst)))
        avoid: set[Any] = set(travel.kept_off(self.p))      # the rides the profile switches off
        was = getattr(self, "traveling", False)   # a chores run or a survey keeps it on round this
        self.traveling = True
        try:
            self._travel(dst, cell, avoid)
        finally:
            self.traveling = was

    def _travel(self, dst: int, cell: tuple[float, float] | list[float] | None, avoid: set[Any]) -> None:
        goal_cell = (cell[0], cell[1]) if cell is not None else None
        said = None
        failed: dict[Any, int] = {}       # transport id -> rides that didn't work this trip
        for _leg in range(40):
            mid = self.map_id()
            if atlas.is_ride(mid):
                # An airship's flight hall, a cable car: the game moves us on by itself. No plan,
                # no keys, no stuck checks here - wait for the real map change, then plan again
                # from wherever it put us (the destination is kept: dst, cell).
                self.wait_ride(mid)
                said = None
                continue
            if mid == dst and cell is None:
                return
            self.restock_on_the_way()
            if self.map_id() != mid:
                continue          # the restock walked us elsewhere: plan from there
            beasts.ensure(as_bot(self), beasts.TRAVEL)   # again each leg: mounting waits out combat
            legs = self._plan_legs(mid, dst, goal_cell, avoid)
            if not legs:
                return
            steps = travel.describe(legs)
            if steps != said and len(legs) > 1:
                self.log(_("route to {map}: {steps}", map=atlas.label(dst), steps=" / ".join(steps)))
            said = steps[1:]
            # a leg into Polson Wetland, or across it, on the way somewhere else: on a beast first
            crossing = dst != POISON_MAP and POISON_MAP in (mid, legs[0].get("dest"))
            if crossing:
                self._ride_into_poison(leaving=mid == POISON_MAP)
            self.crossing_poison = crossing
            try:
                if self._take_leg_noting(legs[0], avoid, failed):
                    return
            finally:
                self.crossing_poison = False
        raise Stopped(_("travel to {map} didn't arrive", map=atlas.label(dst)))

    def poison_immune(self) -> bool:
        """The Poison Immunity talent is activated (`mem skills` talents). False when the agent
        can't say: the trip is refused rather than risked."""
        try:
            sk = self.a.json("mem skills")
        except STOPPING:
            raise
        except Exception as exc:
            note_failure(self, "reading the talents", exc)
            return False
        return POISON_TALENT in combos.active_talents(sk if jsonio.is_object(sk) else None)

    def _warn_poison_stay(self) -> None:
        """A trip that ends in Polson Wetland without Poison Immunity: it goes, but say (aloud, once
        a run) that its ground hurts whenever we are off a fusion beast - every fight there."""
        if getattr(self, "warned_poison", False) or self.poison_immune():
            return
        self.warned_poison = True
        self.log(_("Polson Wetland: Poison Immunity isn't activated - its ground poisons us whenever "
                   "we are off a fusion beast, and every fight there is on foot"), speak=True)

    def _ride_into_poison(self, leaving: bool = False) -> None:
        """Crossing Polson Wetland without Poison Immunity: get on a fusion beast (any kind) before
        the step in, waiting out the server's combat time if a fight was just over. Stopped when
        none will go on: walking through on foot would poison us the whole way. Already on it
        (leaving): one try, and out either way - standing still waiting is worse."""
        if self.poison_immune():
            return
        if leaving:
            beasts.ride_any(as_bot(self))
            return
        end = self.clock.time() + POISON_MOUNT_WAIT
        while not beasts.ride_any(as_bot(self)):
            if self.clock.time() >= end:
                why = _("can't cross Polson Wetland: Poison Immunity isn't activated and no fusion beast "
                        "would go on (a fused character takes no poison damage there)")
                self.log(why, speak=True)
                raise Stopped(why)
            self.sleep(2.0)

    def _take_leg_noting(self, leg: dict[str, Any], avoid: set[Any], failed: dict[Any, int]) -> bool:
        """_take_leg, remembering how each map we can't plan out of (a gang base, a dungeon) was
        got into and where its way out put us: from inside one every place costs the same unknown
        leave, so the nearest NPC is judged from there instead (town.nearest)."""
        was, me = self.map_id(), self.me()
        done = self._take_leg(leg, avoid, failed)
        now = self.map_id()
        if now != was:
            if getattr(self, "gang_base", None) is not None:
                rem_map = self.gang_base[0] if isinstance(self.gang_base, tuple) else None
                if now != rem_map:
                    self.gang_base = None
            if travel.is_instance(now) and not travel.is_instance(was):
                note_way_out(self, now, was, (me.get("x", 0), me.get("y", 0)))
            elif travel.is_instance(was) and not travel.is_instance(now):
                at = self.me()
                note_way_out(self, was, now, (at.get("x", 0), at.get("y", 0)))
        return done

    def _plan_legs(self, mid: int, dst: int, goal_cell: tuple[float, float] | None, avoid: set[Any]) -> list[dict[str, Any]]:
        """The way from here, as the profile wants it planned; a Stopped when there is none."""
        me = self.me()
        rows = self.bag_rows()
        bag = rows if self.p.get("use_travel_items", False) else []
        purse = travel.arnebia_in(rows)       # no ride planned that we can't pay for
        legs = None
        if self.p.get("prefer_walking", False):
            # Optional: walk whenever the maps connect on foot (the default is the shortest way,
            # cable cars and airships included; bag items stay off unless use_travel_items).
            legs = travel.plan(mid, (me["x"], me["y"]), dst, goal_cell, transport=False, avoid=avoid,
                               bag=[], items=False)
        if legs is None:
            rides, off = travel.ride_rules(self.p)
            legs = travel.plan(mid, (me["x"], me["y"]), dst, goal_cell,
                           transport=rides, avoid=avoid | off,
                           vip=rides_free(self), bag=bag,
                           hearth_home=self.p.get("hearth_home") or None,
                           hearth_key=bool(self.p.get("hearthstone_key")),
                           items=self.p.get("use_travel_items", False), arnebia=purse,
                           spells=teleskill.options(as_bot(self)))
        if legs is None:
            if travel.gang_exit(mid) is not None:
                raise Stopped(_("{a}: the gang base's way out is in its outer hall, walled off from here - "
                                "walk out yourself and start again", a=atlas.label(mid)))
            raise Stopped(_("no way from {a} to {b} - go there yourself and start again",
                            a=atlas.label(mid), b=atlas.label(dst)))
        return legs

    def _take_leg(self, leg: dict[str, Any], avoid: set[Any], failed: dict[Any, int]) -> bool:
        """The route's first leg. What failed is left out of the next plan. True: the trip is done."""
        if leg["do"] == "exit":
            self.cross_exit(leg)
            if leg.get("dest") is None and self.map_id() == leg["map"]:
                failed["gang_exit"] = failed.get("gang_exit", 0) + 1
                if failed["gang_exit"] >= 2:
                    avoid.add("gang_exit")
                    self.log(_("{map}: walking out through its exit didn't take me out", map=atlas.label(leg["map"])),
                             speak=True)
        elif leg["do"] == "npc":
            free = rides_free(self)
            if not self.ride(leg):
                if free and not rides_free(self):
                    # the free entry was refused (_vip_refused): no fault of this NPC's, so it is
                    # neither counted against it nor left out - the next plan pays its fare
                    return False
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
        elif leg["do"] == "skill":
            self._take_spell(leg, avoid, failed)
        elif leg["do"] == "leave":
            if not self.leave_instance():
                avoid.add("leave")
        else:
            # "map": something moved us off this map on the last stretch - plan again from there
            return self.walk_to(tuple(leg["cell"])) != "map"
        return False

    def wait_ride(self, here: int) -> int:
        """Ride it out: on a map the game carries us off by itself (atlas.is_ride), wait for the
        game's own move to another map - not for a time, which only says the ride is due. Nothing
        is pressed and nothing is planned meanwhile. The map we land on, once its reads agree
        (settled_map); a ride that never moves us on in RIDE_WAIT seconds stops the run as it
        stands, rather than guess that it arrived or walk about in the hall."""
        self.let_go()                 # no walking key stays held across the ride
        self.log(_("on {map}: riding - waiting to arrive", map=atlas.label(here)))
        end = self.clock.time() + self.RIDE_WAIT
        while self.clock.time() < end:
            self.sleep(0.5)
            if self.map_id() == here:
                continue
            landed = self.settled_map()
            if landed == here:
                continue              # a read taken mid-change: still riding
            me = self.me()            # fresh: the position before the ride means nothing here
            self.log(_("arrived on {map} at {x},{y} - planning the way on from here", map=atlas.label(landed),
                       x=int(me.get("x", 0)), y=int(me.get("y", 0))))
            return landed
        self.log(_("still on {map} after {n} seconds - the ride didn't move us on", map=atlas.label(here),
                   n=int(self.RIDE_WAIT)), speak=True)
        raise Stopped(_("the ride from {map} never arrived", map=atlas.label(here)))

    def wait_map_change(self, mid: int, seconds: float) -> bool:
        self.last_hp = self.me().get("hp")       # only a hit from now on counts as an interruption
        end = self.clock.time() + seconds
        while self.clock.time() < end:
            if self.map_id() != mid:
                self.settled_map()
                self.sleep(1.0)
                if getattr(self, "gang_base", None) is not None:
                    rem_map = self.gang_base[0] if isinstance(self.gang_base, tuple) else None
                    if self.map_id() != rem_map:
                        self.gang_base = None
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
            choose: list[str] = leg.get("choose") or []
            for pick in choose:
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

    def _take_spell(self, leg: dict[str, Any], avoid: set[Any], failed: dict[Any, int]) -> None:
        """A teleport skill leg; what can't work is left out of the next plan: a place the skill
        doesn't have at once, the whole skill after SPELL_TRIES broken channels."""
        got = self.cast_teleport(leg)
        if got is False:
            avoid.add((leg["skill"], leg["place"]))
        elif got is None:
            key = ("skill", leg["skill"])
            failed[key] = failed.get(key, 0) + 1
            if failed[key] >= self.SPELL_TRIES:
                avoid.add(leg["skill"])
                self.log(_("leaving {skill} out of this trip and planning again", skill=leg["skill"]))

    def spell_slot(self, skill: str) -> tuple[combat.SkillBook, dict[str, Any]] | None:
        """The skill book and the key entry a skill is on, putting it on an empty skill key first
        if it is on none (keyslots.put_skill). None: it can't be cast from a key."""
        sb = self.skillbook or combat.SkillBook(as_bot(self))
        self.skillbook = sb
        for _try in range(2):
            for page in sorted(sb.slots):
                for x in sb.slots[page]:
                    if x["name"] == skill:
                        return sb, x
            if not keyslots.put_skill(as_bot(self), skill):
                return None
            sb = self.skillbook or combat.SkillBook(as_bot(self))
        return None

    def cast_teleport(self, leg: dict[str, Any]) -> bool | None:
        """A teleport skill leg (travel "skill"): out of the safe zone, nothing attacking, off the
        beast, press the skill's key, choose the place in its menu, and wait for the new map.
        True: arrived. False: this place or skill can't be used (no key, no MP, not in the menu) -
        left out of the trip. None: broken off (a hit during the channel, no menu): another try
        after the fight. A teleport is done only when the map changes: nothing says a channel broke."""
        skill, place, mid = leg["skill"], leg["place"], self.map_id()
        slot = self.spell_slot(skill)
        if slot is None:
            return False
        sb, x = slot
        me = self.me()
        if atlas.in_safe_zone(mid, me["x"], me["y"]):
            spot = travel.cast_spot(mid, (me["x"], me["y"]))
            if spot is None:
                self.log(_("no way out of the safe zone on {map} to cast {skill}", map=atlas.label(mid), skill=skill))
                return False
            self.log(_("walking out of the safe zone to cast {skill}", skill=skill))
            self.walk_to((float(spot[1][0]), float(spot[1][1])))
            me = self.me()
            if self.map_id() != mid:
                return None           # the walk took us elsewhere: plan again from there
            if atlas.in_safe_zone(mid, me["x"], me["y"]):
                return None
        mp = self.mp_now(me, self.clock.time())
        if mp is not None and mp[0] < teleskill.MANA * mp[1]:
            self.log(_("not enough MP for {skill} ({mp} of {top}) - going another way",
                       skill=skill, mp=int(mp[0]), top=int(mp[1])))
            return False
        if self.attackers():
            self.fight_back()         # a hit would only break the channel
            if self.attackers():
                return None
        beasts.off(as_bot(self))      # a Swift beast's skills are off
        if sb.page() != (x["slot"] - 1) // 10 and not sb.flip_to((x["slot"] - 1) // 10):
            return False
        self.log(_("casting {skill} to {place}", skill=skill, place=place))
        try:
            self.key(x.get("still") or x["key"])
            d = self.wait_dialog(4.0)
            if d is None or not d.get("top"):
                self.log(_("{skill} opened no menu (cooling down, or not enough MP?)", skill=skill))
                return None
            seen = teleskill.menu_places(d.get("top"))
            teleskill.learn(as_bot(self), skill, seen)
            if not any(travel.matches(leg["choose"][0], t) for t in seen):
                self.log(_("{skill} doesn't list {place} for this character", skill=skill, place=place))
                return False
            if self.choose(leg["choose"][0]) is None:
                return False
            if self.wait_map_change(mid, teleskill.CAST + teleskill.LOAD):
                me = self.me()
                self.log(_("arrived on {map} at {x},{y}", map=atlas.label(self.map_id()),
                           x=int(me.get("x", 0)), y=int(me.get("y", 0))))
                return True
            self.log(_("{skill} was broken off before it ended (something hit us) - fighting first",
                       skill=skill))
            self.fight_back()
            return None
        finally:
            sb.flip_to(0)
            if self.map_id() == mid:
                self.close_dialogs()

    def leave_instance(self) -> bool:
        """Leave a dungeon, secret room or event map: the Action menu (W_LOCAL_OPTION) has a
        'leave ...' entry there."""
        mid = self.map_id()
        if travel.gang_exit(mid) is not None:
            return self.leave_here()
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

    def leave_here(self) -> bool:
        """Out of the map we can't plan our way out of (a gang base: its exit; a dungeon, secret
        room or event: the Action menu), and no further. True when the map changed."""
        mid = self.map_id()
        me = self.me()
        legs, _cost = travel.way_out(mid, (me.get("x", 0), me.get("y", 0)))
        if not legs:
            if travel.gang_exit(mid) is not None:
                self.log(_("{map}: the gang base's way out is in its outer hall, walled off from here",
                           map=atlas.label(mid)), speak=True)
            return False
        if legs[0]["do"] == "leave":
            return self.leave_instance()
        self._take_leg_noting(legs[0], set(), {})
        return self.map_id() != mid

    def cross_exit(self, leg: dict[str, Any]) -> None:
        """Walk into a map exit, pushing off the edge if walking onto it isn't enough."""
        mid = leg["map"]
        if leg.get("dest") is None:
            self.log(_("{map}: walking out of the gang base", map=atlas.label(mid)))
        else:
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
        """An exit inside the map takes us through only as we step onto it, so standing on it
        does nothing: step off and back on. (Loki Union, where the bot once stood re-planning every
        4 s, is an airship's flight hall: travel now waits there instead, see wait_ride.) True
        when it moved."""
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
            swift = [r for r in beasts.owned(as_bot(self)) if r.get("kind") == "swift"]
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
            if not [r for r in beasts.owned(as_bot(self)) if r.get("kind") == "swift"]:
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
        # Transport NPCs answer the target keys only in NPC mode: switch before looking for it,
        # and put back whatever mode was on before (not the profile's, which the player may have
        # changed by hand) once the menu is done with - boarded, refused or not found alike.
        was = self.target_mode_now()
        if not self.set_target_mode(self.NPC_MODE):
            self.log(_("couldn't switch the target key to NPCs"), speak=True)
        try:
            return self._ride(leg, mid)
        finally:
            if self.map_id() == mid:
                self.close_dialogs()
            # V only: on a ride map this presses no walking key and plans nothing
            self.set_target_mode(was if was is not None else self.p.get("target_mode", 2))

    def target_mode_now(self) -> int | None:
        """The game's target mode right now (TARGET_MODES index), or None when it can't be read."""
        m = self.a.json("mem modes")
        mode = m.get("target") if jsonio.is_object(m) else None
        return mode if isinstance(mode, int) else None

    def _ride(self, leg: dict[str, Any], mid: int) -> bool:
        if not self.talk(leg["npc"], leg.get("at"), leg.get("stand")):
            return False
        choose = leg["choose"]
        steps: list[str] = choose if jsonio.is_array(choose) else [choose]
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
                if leg.get("vip"):
                    self._vip_refused(leg)
                return False
            self.choose(CONFIRM)
        end = self.clock.time() + 15
        while self.clock.time() < end:
            if self.map_id() != mid:
                here = self.settled_map()
                if here == mid:
                    continue
                self.log(_("now on {map}", map=atlas.label(here)))
                if atlas.is_ride(here):
                    # The vehicle carries us itself (cable car 1055 moved us to Tulip City's
                    # underground 37 s after boarding, every step in it refused; an airship's
                    # flight hall the same after about 25 s). Wait for that instead of walking.
                    self.wait_ride(here)
                else:
                    self.sleep(1.0)
                return True
            self.sleep(0.3)
        if leg.get("vip"):
            self._vip_refused(leg)
        else:
            self.log(_("the {npc} didn't take me anywhere", npc=leg["npc"]), speak=True)
        self.close_dialogs()
        return False

    def _vip_refused(self, leg: dict[str, Any]) -> None:
        """The menu's free entry for VIPs was chosen and no ride came of it: the profile says VIP
        and the game doesn't (it ran out, or the box was ticked for another character). Say so,
        and for the rest of this run plan every ride at its fare (rides_free) - without this the
        free entry was tried again, the NPC left out, and the trip walked the long way round.
        Told by what happened, not by the game's words: those change with its language."""
        self.vip_refused = True
        self.log(_("the {npc} didn't take me for free - check that your VIP is still active. "
                   "Paying the fares for the rest of this run", npc=leg["npc"]), speak=True)
