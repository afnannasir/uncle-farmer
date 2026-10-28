"""Fighting: reach, targets, target modes, the fight loop, fighting back and defending.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any

from ... import applog, atlas, jsonio, nearby
from ...settings import TARGET_MODES
from ...i18n import _
from .. import beasts, bosses, combat, pets, teamheal
from .base import (NPC_HP, STOPPING, Stopped, agent_strike, note_failure, skill_things, snap_map)
from ...link import GameLink
from .parts import as_bot, Parts


class FightingMixin(Parts):
    MP_EMPTY = 10         # MP percent under which our skills can't be cast: no damage is our own doing

    POINTS_NAMES = {"Warrior": "rage", "Orc": "rage", "Hunter": "marked targets", "Assassin": "combos",
                    "Elf": "fire elements"}

    CHECK_EVERY = 0.4     # seconds between full readings of the world in a fight

    WALK_SPEED = 4.0      # tiles a second on foot, to tell how long a walk to a monster takes
    HP_WINDOW = 3.0       # seconds of a monster's HP readings that say how fast it is going down
    FOLLOW_LEASH = 12.0   # a melee character's follow attack chases a monster this far (straight line)
    FOLLOW_STALL = 2.0    # ... and is given up if it doesn't close in by a tile in this long
    FOLLOW_TRIES = 3      # stalls in a row, never reaching the monster, before we walk up ourselves
    DOOMED_SKIP = 10.0    # longest a monster someone else is about to kill is left alone
    FIRST_HIT = 0.5       # seconds from being in reach to our first hit landing (press, cast, the game's answer)
    UNSELECTED_SKIP = 10.0  # seconds a monster the target keys wouldn't select is left alone
    REFUSED_RUN = 3       # bursts in a row with no cast, at a monster we never hurt, before leaving it
    SELECT_RANGE = 20.0   # how far the game's target keys pick a monster at most, as far as we can
    _speech: str
    select_refused: str = ""   # agent 41's reason the last `select` was refused ("" when it wasn't)
    _attackers: set[int]
    _attackers_at: float
    last_points_key: float
    mob_names: dict[int, str]
    counted: dict[int, float]
    hp_log: dict[int, list[tuple[float, float]]]
    follow_stalls: int
    follow_ok: bool

    @property
    def _game(self) -> GameLink:
        return self.a
                          # measure: live 2026-10-21 a Golden eagle at 25.6-32 yards was never taken
                          # (a nearer Alpaca was), while fights at our own reach (18 for a Hunter)
                          # select fine. pull_range stops within this: beyond it the stop only
                          # costs the target, which then has to be walked up to anyway.

    def read_points(self, me: dict[str, Any], now: float) -> None:
        """Rage, marked targets or combos for the skill picker, so a skill that spends them is
        pressed only when there are enough. In a fight, a class whose keys hold such skills presses
        the accumulation-points key (W_SELF_LJ, T) every points_every seconds; the server's answer
        comes back through the agent (v22). While the game may speak (it reads every answer out),
        only every 6 s."""
        sb = self.skillbook
        key = self.p.get("points_key")
        if not key or sb is None or not sb.ok or not sb.uses_points():
            return
        sb.take_points(me)
        every = float(self.p.get("points_every", 1.5))
        if now - getattr(self, "_speech_at", 0) > 15:
            self._speech_at = now
            self._speech = self._game.send("speech")
        if getattr(self, "_speech", "off") == "on":
            every = max(every, 6.0)
        if now - self.last_points_key < every:
            return
        if not self.last_points_key:
            self.log(_("reading {what} with the {key} key", what=self.POINTS_NAMES.get(sb.cls or "", _("points")), key=key))
        self.last_points_key = now
        self.key(key, 30)

    def defend(self, quiet: float = 10.0, limit: float = 180.0) -> None:
        """A run that stops on its own (stuck, an error, a stop rule) doesn't leave us standing there:
        drink, and fight whatever attacks, until nothing has hit us for `quiet` seconds (at most
        `limit`). Pressing Stop ends this at once."""
        self.watches = []
        end = self.clock.time() + limit
        calm = self.clock.time()
        said = False
        while self.clock.time() < end and self.clock.time() - calm < quiet:
            self.check()
            snap = self.snap()
            self.look_after(snap["self"])
            hitting = self.attackers()
            close = [e for e in self.targets(snap) if e["id"] in hitting]
            if close:
                if not said:
                    said = True
                    self.log(_("still being attacked - fighting back before stopping"), speak=True)
                self.fight(close[0])
                calm = self.clock.time()
            else:
                self.sleep(0.5)

    def select_target(self, mob: dict[str, Any], by_keys: bool = False) -> bool:
        """Press the target key for the monster's direction until the game has selected it.

        The game's target keys pick the nearest target west, north, south or east of us (in the
        current target mode), and repeated presses move on to the next one that way. True when the
        game has it selected - or when that can't be checked (no target keys, the game's target
        unreadable): then we attack as before rather than leave every monster. by_keys: the target
        keys even where the agent selects by id."""
        if getattr(self, "agent_act", False) and not by_keys:
            picked = self._select_by_id(mob)
            if picked is not None:
                return picked
        keys: dict[str, str] = self.p.get("target_keys") or {}
        if not any(keys.values()) or "target" not in self.ui():
            return True
        if self.ui().get("target") == mob["id"]:
            return True
        me = self.me()
        dx, dy = mob["x"] - me["x"], mob["y"] - me["y"]
        if abs(dx) >= abs(dy):
            order = ["east" if dx > 0 else "west", "south" if dy > 0 else "north"]
        else:
            order = ["south" if dy > 0 else "north", "east" if dx > 0 else "west"]
        # then the other two, a couple of presses each: a monster almost on top of us has no clear
        # direction, and the game's idea of "west" may not be ours
        order += [d for d in ("west", "north", "south", "east") if d not in order]
        keys: dict[str, str] = self.p.get("target_keys") or {}
        for n, direction in enumerate(order):
            k = keys.get(direction)
            if not k:
                continue
            for _press in range(4 if n < 2 else 2):
                self.key(k)
                self.sleep(0.15)
                if self.ui().get("target") == mob["id"]:
                    return True
        return False

    def _select_by_id(self, mob: dict[str, Any]) -> bool | None:
        """`select <id>` (agent 40): the monster becomes the game's target as the target keys
        would make it, with no presses landing on the next one that way. True when the game has
        it, False when it is gone from sight, None when the agent couldn't - the keys then."""
        reply = self.a.send("select %d" % mob["id"])
        self.select_refused = ""
        if reply == "selected":
            agent_strike(self, "select", True)
            return self.ui().get("target", mob["id"]) == mob["id"]
        if reply.startswith("refused:"):
            # agent 41: the target keys wouldn't take it (out of their reach, or no clear line by
            # the game's own sight check, door edges included): walking closer may change that
            self.select_refused = reply
            return False
        if reply.endswith("isn't in sight"):
            return False
        if not agent_strike(self, "select", False):
            return None                    # the keys this once; the agent again next time
        self.agent_act = False             # this game's agent can't: keys for the rest of the run
        self.log(_("bag items and targets go by keys again: {why}", why=reply))
        return None

    def hit(self, me: dict[str, Any]) -> bool:
        """True when HP fell since the last step of a walk and fighting back is on."""
        hp = me.get("hp")
        dropped = self.last_hp is not None and hp is not None and hp < self.last_hp
        self.last_hp = hp
        return (dropped and self.p.get("fight_back", True) and not self.in_fight and not self.walk_only)

    def fight_back(self) -> None:
        """Something hit us while walking: fight what is attacking us, then let the walk go on.
        Only monsters whose casts the agent saw count. A dip in HP with nothing casting is not an
        attack, and what merely stands nearby (a city's ducks and hens) is left alone: chasing
        those pulls a trip off its path and into the town's buildings. Without cast reports
        (an old agent) whatever is close is the best guess. The walking assistant never fights:
        it rides past on a Swift beast, which can't use skills anyway."""
        if self.walk_only:
            return
        for _round in range(6):
            snap = self.snap()
            me = snap["self"]
            hitting = self.attackers()
            blind = not getattr(self, "casts_seen", True)
            close = [e for e in self.targets(snap)
                     if e["id"] in hitting
                     or (blind and math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= self.reach() + 6)]
            if not close:
                return
            if not self.modes_done:       # only when there is something to fight: it presses keys
                self.modes_done = True
                self.set_modes()
            self.log(_("attacked by {name}, fighting back", name=close[0]["name"]))
            self.fight(close[0])

    def set_target_mode(self, want: int) -> bool:
        key = self.p.get("target_mode_key") or "v"
        for _press in range(len(TARGET_MODES) + 1):
            m = self._game.json("mem modes")
            if "error" in m:
                return False
            if m.get("target") == want:
                return True
            self.key(key)
            self.sleep(0.3)
        return False

    def target_enemies(self) -> None:
        """Before selecting a monster: the target keys must pick enemies. The keys follow the
        game's target mode (V): in "NPCs" they never reach a monster, in "all" they also reach
        townsfolk and critters (ducks, hens) the server won't let anyone attack. The game itself
        never changes the mode; talking to an NPC (talk) and a player pressing V do. So it is
        checked here, before every fight, not once per run."""
        if not self.p.get("set_modes", True):
            return
        want = self.p.get("target_mode", 2)
        m = self._game.json("mem modes")
        modes: dict[str, Any] = m if jsonio.is_object(m) else {}
        have = modes.get("target")
        if have is None or have == want:
            return
        self.log(_("the target key was on {mode} - back to {want}",
                   mode=TARGET_MODES[have] if 0 <= have < len(TARGET_MODES) else have, want=TARGET_MODES[want]))
        if not self.set_target_mode(want):
            self.log(_("couldn't set the mode to {mode} with the {key} key", mode=TARGET_MODES[want],
                       key=self.p.get("target_mode_key") or "v"), speak=True)

    def reach(self) -> float:
        """How far away we can fight from. "auto" (the default): the range of the class's base
        attack from its skill text, just inside it (combat.base_reach) - a Hunter's Arcane Shot
        reaches 20, so it shoots from 19.5 instead of walking into melee (Orcus 2026-10-04: with
        the old fixed 5 it walked up to archers that were already shooting it). A number in the
        profile is used as written."""
        r = self.p.get("attack_range", "auto")
        if isinstance(r, (int, float)) and r != 5.0:
            return float(r)
        if self.skillbook is None and self.p.get("smart_skills", True):
            self.skillbook = combat.SkillBook(self)
        sb = self.skillbook
        if sb and sb.ok:
            base: list[float] = [x["range"] for pg in sb.slots.values() for x in pg
                                 if x.get("role") == "base_attack" and x.get("range")]
            if base:
                return combat.base_reach(min(base))
        return 5.0 if r == "auto" else float(r)

    def clear_shot(self, me: dict[str, Any], mob: dict[str, Any], mid: int | None = None) -> bool:
        """A straight line over walkable ground from us to the monster (its own cell may be a wall).
        mid: the map, when the caller has it (saves asking the game)."""
        try:
            m = atlas.model(mid if mid is not None else self.map_id())
            line = list(m.line((int(me["x"]), int(me["y"])), (int(round(mob["x"])), int(round(mob["y"])))))
            return all(m.ok(x, y) for x, y in line[:-1])
        except STOPPING:
            raise
        except Exception as e:
            note_failure(self, "clearing a shot", e)
            return True

    def count_kills(self, snap: dict[str, Any] | None = None) -> list[int]:
        """Fold in the monsters that died with a hit of ours on them since the last look (agent
        v30): our selected target, what our area skills hit, and monsters other players were
        hitting too - the game shares those. Each is counted and logged once. Returns their ids;
        empty with an older agent, which can't tell (the fight loop then counts its target)."""
        me: dict[str, Any] = (snap or {}).get("self") or getattr(self, "last_me", None) or {}
        if "id" not in me:
            return []
        if snap is not None and me.get("name"):
            # our pets' strikes are ours: a monster only they are at is no one else's (agent 44)
            tail = nearby.OWNER + str(me["name"])
            entities: list[dict[str, Any]] = snap.get("entities", [])
            self.kill_tracker.own = {e["id"] for e in entities
                                     if e.get("kind") == "player" and str(e.get("name") or "").endswith(tail)}
        dead = self.kill_tracker.update(me["id"])
        tally = getattr(self, "tally", None)
        if tally is not None:
            tally.combat.observe(self.kill_tracker.fresh, me["id"], self.mob_names)
        now = self.clock.time()
        for mid in dead:
            self.kills += 1
            self.counted[mid] = now
            name = self.mob_names.get(mid) or _("a monster")
            if snap is not None and self.mob_names.get(mid):
                self.killed_kinds.add((snap_map(snap), name.strip().lower()))
            if tally is not None:
                tally.combat.note_kill(self.mob_names.get(mid))
            self.log(_("{name} down ({n} so far)", name=name, n=self.kills))
        if len(self.counted) > 500:
            self.counted = {k: t for k, t in self.counted.items() if now - t < 60}
        return dead

    def watch_hp(self, snap: dict[str, Any]) -> None:
        """Note every monster's HP with the time, for dying_in()."""
        now = self.clock.time()
        log: dict[int, list[tuple[float, float]]] = getattr(self, "hp_log", {})
        entities: list[dict[str, Any]] = snap["entities"]
        for e in entities:
            if e.get("kind") != "monster" or "hp" not in e:
                continue
            rows = log.setdefault(int(e["id"]), [])
            if rows and e["hp"] > rows[-1][1]:
                rows.clear()          # it healed: start the reading again
            if not rows or now > rows[-1][0]:
                rows.append((now, float(e["hp"])))
            while len(rows) > 1 and now - rows[0][0] > self.HP_WINDOW:
                rows.pop(0)
        self.hp_log = log
        if len(log) > 500:
            self.hp_log = {k: v for k, v in log.items() if v and now - v[-1][0] < 10}

    def dying_in(self, e: dict[str, Any]) -> float:
        """Seconds until this monster dies at the rate its HP has been going down lately, by
        whatever hurts it; math.inf when it isn't going down. Until the world's readings span the
        0.8 s that make a rate, someone else's strikes give one (agent 44): live, a monster other
        players took died a median 3.1 s after their first strike, so that second counts."""
        log: dict[int, list[tuple[float, float]]] = getattr(self, "hp_log", {})
        rows: list[Any] = log.get(int(e["id"])) or []
        if len(rows) < 2 or rows[-1][0] - rows[0][0] < 0.8:
            rate = self.kill_tracker.struck_rate(e["id"])
            return float(e["hp"]) / rate if rate else math.inf
        rate = (rows[0][1] - rows[-1][1]) / (rows[-1][0] - rows[0][0])
        return float(e["hp"]) / rate if rate > 0 else math.inf

    def doomed(self, e: dict[str, Any], me: dict[str, Any], steps: float | None = None, rng: float | None = None) -> bool:
        """Someone else (another player, a pet) is killing it faster than we can walk up and hit
        it: the walk would be wasted. Not one we already hurt (it's partly ours) and not one in
        reach now (a hit from here still earns a share). Live, one character's walks ended at a
        monster already dead 782 times in two nights, 83 minutes of walking.
        fight_locked goes for every one a hit of ours can still reach in time - one hit is all a
        share of the kill needs - and leaves only those that will be dead before it lands. It
        used to leave none: one night, a Hunter walked 1,007 times to a monster that was gone
        when it got there. Without it, half a second's slack on top leaves the close calls too."""
        if self.kill_tracker.tagged(e["id"]) or self.kill_tracker.pets_alone(e["id"]):
            return False        # partly ours, or our pet's alone: its HP going down is our doing
        rng = self.reach() if rng is None else rng
        dist = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        if dist <= rng:
            return False
        steps = dist if steps is None or steps == math.inf else steps
        slack = 0.0 if self.p.get("fight_locked", False) else 0.5
        return self.dying_in(e) < max(0.0, steps - rng) / self.WALK_SPEED + self.FIRST_HIT + slack

    def counted_kill(self, mid: int) -> bool:
        """The game reported this monster dead with a hit of ours on it (count_kills) lately."""
        return self.clock.time() - self.counted.get(mid, 0) < 30

    def attackers(self) -> set[int]:
        """Monsters that cast at us or our pet in the last few seconds (agent v9 `casts`): those
        are fought first, wherever they stand."""
        now = self.clock.time()
        if now - getattr(self, "_attackers_at", 0) < 0.5:
            return self._attackers
        self._attackers_at = now
        r = self._game.json("casts %d" % int((now - 4) * 1000))
        data: dict[str, Any] = r if jsonio.is_object(r) else {}
        self.casts_seen = jsonio.is_object(r) and "error" not in data
        casts: list[dict[str, Any]] = data.get("casts", [])
        me: dict[str, Any] = getattr(self, "_snap_self", None) or getattr(self, "last_me", None) or {}
        self._attackers = {int(c["caster"]) for c in casts
                           if c["caster"] < 0 and self.at_us(c, me.get("id"), self.kill_tracker.own)}
        return self._attackers

    def at_us(self, c: dict[str, Any], me_id: int | None, pets: set[int]) -> bool:
        """A monster's cast struck us or our pet. From agent 44 every cast names whom it struck:
        a monster busy with another player is no attacker of ours. Counted as one, it was fought
        before everything else - from outside the farming area and past the walk limit - and
        fought back on the way, in any spot other players farm too. With no way to tell (an
        older agent, our id not read yet) every monster's cast counts, as before."""
        if me_id is None:
            return True
        hits: list[list[Any]] = c.get("hits") or []
        if any(h and h[0] == me_id for h in hits):
            return True               # it hit us, or we dodged it
        if "targets" in c:
            struck: list[list[Any]] = c["targets"]
            return any(row and (row[0] == me_id or row[0] in pets) for row in struck)
        return int(getattr(self, "agent_version", 0) or 0) < 44   # agent 44 names targets: none struck

    def targets(self, snap: dict[str, Any], area: Sequence[float] | None = None, only: str | None = None, skip: str | None = None, avoid_rects: Sequence[tuple[float, float, float, float]] = (), limit: float | None = None) -> list[dict[str, Any]]:
        """The monsters to fight, nearest first by walking distance - those attacking us before all
        others. limit: leave out any further than that many steps (not those attacking us)."""
        now = self.clock.time()
        me: dict[str, Any] = snap["self"]
        hitting = self.attackers()
        out: list[dict[str, Any]] = []
        steps: dict[int, float] = {}
        tagged = self.kill_tracker.tagged
        rng = self.reach()
        near_tag = rng + 10
        self.watch_hp(snap)
        # bosses are never targets, nor is anything close to one: fighting there pulls the boss in
        keep = self.p.get("boss_distance", 40)
        boss_list = bosses.in_sight(snap) if keep else []
        entities: list[dict[str, Any]] = snap["entities"]
        for e in entities:
            if e["kind"] != "monster" or "hp" not in e or e["hp"] <= 0:
                continue
            if not e.get("name") or e["maxhp"] <= 1:        # drops and corpses
                continue
            self.mob_names[e["id"]] = e["name"]
            if e["maxhp"] >= NPC_HP or self.skip.get(e["id"], 0) > now:
                continue
            if self.skip_names.get((snap_map(snap), e["name"].strip().lower()), 0) > now:
                continue        # its kind took no damage and never attacked: a critter, not a monster
            if e["name"].strip().lower() in skill_things():
                continue        # our own (or another player's) trap or totem, not a monster
            if only and not re.search(only, e["name"], re.I):
                continue
            if skip and re.search(skip, e["name"], re.I):
                continue
            if boss_list and (bosses.is_boss(e) or bosses.too_close(e, boss_list, keep)):
                continue
            hitting_us = e["id"] in hitting
            if area and not hitting_us and not (area[0] - 3 <= e["x"] <= area[2] + 3 and area[1] - 3 <= e["y"] <= area[3] + 3):
                continue
            if any(r[0] <= e["x"] <= r[2] and r[1] <= e["y"] <= r[3] for r in avoid_rects):
                continue
            steps[e["id"]] = self.walk_dist(snap, e, None if hitting_us else limit)
            if steps[e["id"]] == math.inf and not hitting_us:
                continue          # no walkable way to it: never picked, whatever the patrol mode
            if not hitting_us and self.doomed(e, me, steps[e["id"]], rng):
                continue        # it will be dead before we get there
            out.append(e)
        self.walk_steps = steps        # for note_pick: the walk the farm loop's choice would take
        # Those attacking us first; then those we have already hurt, close by: our hits make them
        # partly ours, and finishing them gets the kill instead of leaving it to the next player.
        out.sort(key=lambda e: (e["id"] not in hitting,
                                not (tagged(e["id"]) and math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= near_tag),
                                steps[e["id"]], math.hypot(e["x"] - me["x"], e["y"] - me["y"])))
        if len(self.mob_names) > 2000:
            self.mob_names = {e["id"]: e["name"] for e in out}
        return out

    def next_target(self, snap: dict[str, Any], mobs: list[dict[str, Any]]) -> dict[str, Any]:
        """The monster to take on next: the nearest one (targets() sorts them nearest first,
        attackers and those we already hurt before all others). With nearest_monster off, a melee
        character takes the nearest one with few others round it instead of pulling a whole pack."""
        first = mobs[0]
        if first["id"] in self.attackers() or self.kill_tracker.tagged(first["id"]):
            return first          # targets() put a monster we already hurt here only when it is close
        if self.p.get("nearest_monster", True):
            return first
        sb = self.skillbook
        if sb is None or not sb.ok or sb.cls not in combat.MELEE:
            return first          # ranged: the nearest is as good as any
        me = snap["self"]

        def crowd(e: dict[str, Any]) -> int:
            return sum(1 for o in mobs if o is not e
                       and math.hypot(o["x"] - e["x"], o["y"] - e["y"]) <= combat.PACK_RADIUS)

        def dist(e: dict[str, Any]) -> float:
            return math.hypot(e["x"] - me["x"], e["y"] - me["y"])

        if crowd(first) < 3:
            return first
        calm = [e for e in mobs[1:8] if crowd(e) < 3 and dist(e) <= dist(first) + 25]
        return min(calm, key=dist) if calm else first

    def note_pick(self, snap: dict[str, Any], mob: dict[str, Any], mobs: list[dict[str, Any]]) -> None:
        """The farm loop picked mob: where we stand, how far it is and how many others were in
        sight or already in reach. fight() writes it to the field log when the fight is over, with
        how long the walk and the whole fight took - what a night's walking is measured from."""
        me: dict[str, Any] = snap["self"]
        rng = self.reach()

        def dist(e: dict[str, Any]) -> float:
            return math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        walked: dict[int, float] = getattr(self, "walk_steps", {})
        steps = walked.get(mob["id"], math.inf)
        self.picked = {
            "kind": "fight", "who": getattr(self, "character", None), "map": snap_map(snap),
            "me": [round(me["x"], 1), round(me["y"], 1)], "mob": [round(mob["x"], 1), round(mob["y"], 1)],
            "id": mob["id"], "name": mob["name"], "dist": round(dist(mob), 1),
            "steps": None if steps == math.inf else round(steps, 1), "reach": round(rng, 1),
            "sight": len(mobs), "in_reach": sum(1 for e in mobs if dist(e) <= rng),
            "hurt": mob["hp"] < mob["maxhp"], "attacking": mob["id"] in self.attackers(),
            "_at": self.clock.time(), "_kills": self.kills,
        }

    def fight(self, mob: dict[str, Any]) -> None:
        beasts.ensure(as_bot(self), beasts.FIGHT)
        self.in_fight = True
        rec = getattr(self, "picked", None)
        self.picked = None
        if rec is not None and rec["id"] != mob["id"]:
            rec = None            # a fight the farm loop didn't pick (fighting back on the way)
        self.field_record = rec
        try:
            self._fight(mob)
        finally:
            self.in_fight = False
            self.last_combat = self.clock.time()
            self.last_hp = None
            if rec is not None:
                self._write_fight(rec)

    def _write_fight(self, rec: dict[str, Any]) -> None:
        at = rec.pop("_at")
        rec["kills"] = self.kills - rec.pop("_kills")
        rec["end_s"] = round(self.clock.time() - at, 2)
        if "reach_at" in rec:
            rec["reach_s"] = round(rec.pop("reach_at") - at, 2)
        self._end_place(rec)
        applog.field(rec)

    def note_search(self, map_id: int, me: dict[str, Any], cell: tuple[float, float], start: float, result: str) -> None:
        """A walk with nothing in sight, to the field log: from where to where, how long, and why it ended."""
        rec: dict[str, Any] = {"kind": "search", "who": getattr(self, "character", None), "map": map_id,
                               "me": [round(me["x"], 1), round(me["y"], 1)],
                               "to": [round(cell[0], 1), round(cell[1], 1)],
                               "s": round(self.clock.time() - start, 2), "result": result}
        self._end_place(rec)
        applog.field(rec)

    def _end_place(self, rec: dict[str, Any]) -> None:
        """Where the last reading put us, as the record's end_me (left out before any reading)."""
        me: dict[str, Any] = getattr(self, "last_me", None) or {}
        if "x" in me:
            rec["end_me"] = [round(float(me["x"]), 1), round(float(me["y"]), 1)]

    def burst(self, key: str, me_id: int | None, longest: float = combat.BURST_MAX) -> bool:
        """Press until the game casts it: from just before the cooldown should end, every
        BURST_GAP, until the game reports a cast of ours (or longest passes: BURST_MAX, plus the
        channel of a channelled skill, whose report comes when the channel ends). Every press inside
        the cooldown is simply refused, so the first one after it ends is cast - the most casts a
        character can make, whatever the network or this loop's timing do. Nobody presses keys by
        hand while the bot fights, so the extra presses cost nothing. Through agent 45's `slot`
        the presses keep a key's pace, and each is followed by a look at the casts: no key is
        held, so the look costs the press nothing."""
        sb = self.skillbook
        if sb is None:          # only a fight with smart skills bursts (_fight_burst)
            raise RuntimeError("a burst needs the skill book")
        self.check()
        if self.slot_command(key) is None:
            self.wait_keys_free()
        start = self.clock.time()
        n = 0
        while self.clock.time() - start < longest:
            slotted = self.press_slot(key)
            if not slotted:
                r = self._game.send("key %s 30" % key)
                if not r.startswith("tapped"):
                    raise Stopped(_("key {key} didn't work: {why}", key=key, why=r), final=True)
            if self.clock.wait(self.stop_event, combat.SLOT_GAP if slotted else combat.BURST_GAP):
                raise Stopped(_("stopped by you"), final=True)
            n += 1
            if slotted or n % 2 == 0:     # a key's cast report is a round trip: every other press
                sb.read_casts(me_id)
                if sb.press_confirmed:
                    return True
        return False

    def follows(self) -> bool:
        """The plain attack keys walk us to the target (the game's follow attack: a melee class,
        or the profile's attack style), and that has worked in this run so far."""
        sb = self.skillbook
        return (sb is not None and sb.ok and getattr(sb, "want_follow", False)
                and getattr(self, "follow_ok", True))

    def _fight(self, mob: dict[str, Any]) -> None:
        rng = self.reach()
        # With the follow attack the game walks us the last stretch, already attacking: we stop
        # our own walk as soon as there is a straight way to the monster within the leash.
        engage = max(rng, self.FOLLOW_LEASH) if self.follows() else rng
        # pull_range: how many yards past our reach we may open fire on a monster that is clearly
        # coming to us. Walking 40% of a kill cycle (measured 2026-10-20) is what the approach
        # costs; a melee monster closes the last few yards itself, so shooting it on the way in
        # takes that off the cycle. Off by default: a stationary or ranged monster would only eat
        # refused casts, so it is only started while the monster is closing (see _Approach.stop).
        pull = float(self.p.get("pull_range", 0) or 0.0)
        got = self._approach(mob, _Approach(self, mob["id"], rng, engage, pull))
        if got is None:
            return
        mob = got
        rec = getattr(self, "field_record", None)
        if rec is not None:
            rec["reach_at"] = self.clock.time()
        with self.timed("select"):
            self.target_enemies()
            selected = self.select_target(mob)
        if not selected:
            got, selected = self._select_closer(mob)
            if got is None:
                return
            mob = got
        # Attacking anyway sends every skill with no target (or at whatever the game has
        # selected) and wastes the whole no-damage wait: leave it for one we can select.
        if not selected:
            self.skip[mob["id"]] = self.clock.time() + self.UNSELECTED_SKIP
            self.log(_("the target keys wouldn't select {name} - next one", name=mob["name"]))
            return
        with self.timed("fight"):
            self._hit_until_down(mob)

    def _approach(self, mob: dict[str, Any], way: "_Approach") -> dict[str, Any] | None:
        """Walk up to the monster unless it is in reach already. The monster as it is now, or None
        when the farm loop should pick again (taken, about to die, stuck, another one on the way)."""
        mid = mob["id"]
        if way.reachable(self.me(), mob, way.engage):
            return mob
        self.approaching = True       # shields may go up on the way in (support_between_fights)
        try:
            with self.timed("approach"):
                walked = self.walk_to((mob["x"], mob["y"]), near=way.stop)
        finally:
            self.approaching = False
        if walked == "stuck":
            self.skip[mid] = self.clock.time() + 120
            return None
        snap = self.snap()
        m = way.find(snap)
        if m is None or m["hp"] <= 0:
            self._taken(snap, mob)
            return None
        if way.doomed and not way.reachable(self.me(), m):
            # left alone until it should be dead: else the farm loop picks it again at once and
            # this turns it down again (live: the same monster 16 times a second, 8,201 in a night)
            self.skip[mid] = self.clock.time() + min(self.DOOMED_SKIP, self.dying_in(m) + 2.0)
            self.log(_("{name} is about to be killed by someone else - next one", name=mob["name"]))
            return None
        if walked == "near" and not way.reachable(self.me(), m, way.engage) and not way.opened_early:
            return None           # stopped beside another one: the farm loop picks again
        return m

    def _taken(self, snap: dict[str, Any], mob: dict[str, Any]) -> None:
        """It is gone before our fight began: counted if a hit of ours killed it, else someone else's."""
        self.count_kills(snap)
        if not self.counted_kill(mob["id"]):
            self.log(_("{name} was taken by someone else - next one", name=mob["name"]))

    def _select_closer(self, mob: dict[str, Any]) -> tuple[dict[str, Any] | None, bool]:
        """The target keys wouldn't take it from here. pull_range can stop us farther than they
        reach (live 2026-10-21: they would not take a Golden eagle at 25.6 yards, and every press
        selected the Alpaca closer in), and a door edge our map doesn't count as a wall can block
        the game's sight check: walk the rest of the way, or round towards it until the game would
        take it, and try once more. (the monster, selected), or (None, False) when it is gone."""
        mid = mob["id"]
        snap = self.snap()
        m = next((e for e in snap["entities"] if e["id"] == mid), None)
        me = snap["self"]
        blocked = "clear line" in self.select_refused
        if m is None or m["hp"] <= 0 or not (blocked or math.hypot(m["x"] - me["x"], m["y"] - me["y"]) >= self.SELECT_RANGE):
            return mob, False
        target = m

        def selectable(_me: dict[str, Any]) -> bool:
            return self.select_target(target)
        with self.timed("approach"):
            walked = self.walk_to((m["x"], m["y"]), near=selectable if blocked else None)
        snap = self.snap()
        m = next((e for e in snap["entities"] if e["id"] == mid), None)
        if walked == "stuck":
            self.skip[mid] = self.clock.time() + 120
            return None, False
        if m is None or m["hp"] <= 0:
            self._taken(snap, mob)
            return None, False
        return m, self.select_target(m)

    def leash_for(self, follow: bool, rng: float, engage: float, pull: float) -> float:
        """How far off a target may be and still be watched during a fight: our reach plus a step,
        the follow attack's reach, and any opening-fire range beyond it (pull_range works by
        starting the fight before the monster is in reach, so the leash has to include it)."""
        return max(rng + 3, engage + pull, self.FOLLOW_LEASH if follow else 0.0)

    def _hit_until_down(self, mob: dict[str, Any]) -> None:
        p = self.p
        self.log(_("fighting {name}", name=mob["name"]))
        if self.skillbook is None and p.get("smart_skills", True):
            self.skillbook = combat.SkillBook(self)
        f = _Fight(self, mob)
        while self.clock.time() - max(f.start, self.resumed_at) < p["fight_timeout"]:
            now = self.clock.time()
            due = f.sb.ready_in(now) if f.sb is not None and f.look is not None else 1.0
            # a check only starts when it will be done before the next press is due (it is timed),
            # and never more than two seconds apart
            if f.look is None or (now >= f.check_at and (due > f.check_cost + 0.05 or now - f.checked > 2.0)):
                self._fight_look(f, now)
            if f.seen is None or f.seen["hp"] <= 0:
                self._fight_over(f)
                return
            casts = getattr(self.skillbook, "casts_done", 0) if self.skillbook is not None else 0
            if self._fight_hits_elsewhere(f, casts) or self._fight_no_damage(f, casts) or self._fight_moved_off(f):
                return
            step = self._fight_press(f)
            if step == "return":
                return
            if step == "again":
                continue
            if f.sb is not None and f.sb.confirm:
                self.sleep(0.05)      # the cooldown paces us now
            else:
                self.sleep(p["attack_gap"])
        self.log(_("{name}: fight took too long, leaving it", name=f.mob["name"]))
        self.skip[f.mid] = self.clock.time() + 120

    def _fight_look(self, f: "_Fight", now: float) -> None:
        """A full look between presses: the world, our HP, pets, popups, kills, rage or marks, who
        attacks us, and how far the monster is (reached: we have been within reach of it)."""
        f.checked = now
        f.check_at = now + self.CHECK_EVERY
        snap = f.look = self.snap()
        self.look_after(snap["self"])
        teamheal.look_after(as_bot(self), snap)       # only while following
        pets.look_after(as_bot(self), snap)
        self.clear_popups()
        f.seen = f.find(snap)
        self.count_kills(snap)
        self.read_points(snap["self"], now)
        f.hit_us = f.hit_us or f.mid in self.attackers()
        if f.seen is not None:
            f.dist = math.hypot(f.seen["x"] - snap["self"]["x"], f.m["y"] - snap["self"]["y"])
            if f.dist <= f.rng + 0.5:
                if f.follow and not f.reached:
                    self.follow_stalls = 0      # the follow attack got us there
                f.reached = True
        f.check_cost = 0.7 * f.check_cost + 0.3 * (self.clock.time() - now)

    def _fight_over(self, f: "_Fight") -> None:
        """It is gone or dead: ours (counted), someone else's, or out of sight."""
        mob = f.mob
        if self.kill_tracker.live:
            # the game says whose it is: dead with a hit of ours on it (count_kills logs
            # it), else someone else's kill, or it walked out of sight
            self.count_kills(f.snap)
            if not self.counted_kill(f.mid):
                self.log(_("{name} is gone, and none of our hits landed on it", name=mob["name"])
                         if f.i else _("{name} was taken by someone else - next one", name=mob["name"]))
            return
        if f.i == 0 and f.last_hp >= mob["hp"]:
            # gone before we pressed a thing: other players farm here too. Not ours to
            # count; the farm loop takes the next one at once.
            self.log(_("{name} was taken by someone else - next one", name=mob["name"]))
            return
        self.kills += 1
        self.killed_kinds.add((snap_map(f.snap), mob["name"].strip().lower()))
        tally = getattr(self, "tally", None)
        if tally is not None:
            tally.combat.note_kill(mob["name"])
        self.log(_("{name} down ({n} so far)", name=mob["name"], n=self.kills))

    def _no_damage_yet(self, f: "_Fight", casts: int) -> bool:
        """No HP lost for the no-damage time, or for the no-damage number of casts."""
        p = self.p
        return (self.clock.time() - max(f.last_drop, self.resumed_at) > p.get("no_damage_timeout", 6.0)
                or casts - f.casts_at_drop >= p.get("no_damage_casts", 3))

    def _fight_hits_elsewhere(self, f: "_Fight", casts: int) -> bool:
        """Before writing a monster off: the game reports where our hits land (agent v30). Live,
        all 108 "takes no damage" checked in a night's packets had our hits landing, most on
        another monster - attack skills lock on to whatever is in front of us. Those reports are
        read afresh here: the casts were counted as the game confirmed them, but the last full
        reading can be older than all of them (a Warrior cast three times between two readings and
        walked off a monster at 2,700 HP of 45,000). True when our last casts killed it."""
        if not (f.m["hp"] >= f.last_hp and self._no_damage_yet(f, casts)):
            return False
        if f.mid in self.count_kills(f.snap):
            return True        # our last casts killed it (count_kills said so)
        landed = self.kill_tracker.hit_since(int(1000 * f.last_drop))
        if f.mid in landed:
            f.last_drop, f.casts_at_drop = self.clock.time(), casts      # it is hurt: our reading lags
        elif landed:
            other = max(landed, key=lambda k: landed[k])
            seen: list[dict[str, Any]] = f.snap["entities"]
            o = next((e for e in seen if e["id"] == other and e.get("hp", 0) > 0), None)
            if o is not None:
                self.log(_("our hits land on {name} - fighting that one", name=o["name"]))
                f.mid, f.mob, f.seen = other, o, o
                f.last_hp, f.last_drop, f.casts_at_drop = float(o["hp"]), self.clock.time(), casts
                f.hit_us = f.hit_us or f.mid in self.attackers()
        return False

    def _fight_no_damage(self, f: "_Fight", casts: int) -> bool:
        """HP going down keeps the fight on; else never reaching it, or no damage for too long,
        ends it. True when it ends."""
        mob, mid = f.mob, f.mid
        if f.m["hp"] < f.last_hp:
            f.last_hp, f.last_drop, f.casts_at_drop = float(f.m["hp"]), self.clock.time(), casts
            return False
        if not f.reached and self.clock.time() - max(f.last_drop, self.resumed_at) > self.p.get("no_damage_timeout", 6.0):
            # never got to it (the follow attack walked us short, or it kept away): not a
            # monster that can't be hurt, just one out of reach for now
            self.log(_("couldn't get to {name} - next one", name=mob["name"]))
            self.skip[mid] = self.clock.time() + 10
            return True
        if not self._no_damage_yet(f, casts):
            return False
        if self.mp_share is not None and self.mp_share < self.MP_EMPTY:
            # Our skills fail with no MP, so nothing would take damage: the monster isn't
            # to blame (live: an out-of-blues Hunter wrote off whole kinds of monsters).
            self.log(_("MP {pct}% - too little to hurt {name}", pct=int(self.mp_share), name=mob["name"]),
                     speak=True)
            self.skip[mid] = self.clock.time() + 20
            return True
        self.log(_("{name} takes no damage, leaving it", name=mob["name"]))
        self.skip[mid] = self.clock.time() + 120
        kind = (snap_map(f.snap), mob["name"].strip().lower())
        if not f.hit_us and f.last_hp >= mob["hp"] and kind not in self.killed_kinds and not self.in_instance:
            # Not a scratch and it never attacked: a town critter (ducks, hens) the server
            # won't let anyone attack. Leave every one of its kind on this map alone, or
            # the next one along starts the same chase. Not a kind we have killed here: one
            # walking back to where it spawns takes no damage on the way, and that wrote
            # off a dungeon's commonest monster for half an hour at a time. Never inside a
            # dungeon: its one kind of monster is what the visit is for, and one fight lost
            # at its start (out of reach, presses the game dropped) left a whole cave alone.
            self.skip_names[(snap_map(f.snap), mob["name"].strip().lower())] = self.clock.time() + 1800
            self.log(_("leaving every {name} here alone - it can't be attacked", name=mob["name"]))
        return True

    def _fight_moved_off(self, f: "_Fight") -> bool:
        """It moved off. With the follow attack the game walks after it, so we keep at it while it
        is in a straight line within the leash and we are closing in; else the farm loop picks the
        nearest again (a melee fight broke off and restarted this way 238 times in two nights).
        True when the fight ends here."""
        if f.dist <= f.rng + 3:
            f.closing = None
            return False
        if f.dist > f.leash or not self.clear_shot(f.snap["self"], f.m, snap_map(f.snap)):
            return True
        if f.closing is None or f.dist < f.closing[1] - 1.0:
            f.closing = (self.clock.time(), f.dist)
            return False
        if self.clock.time() - f.closing[0] <= self.FOLLOW_STALL:
            return False
        if not f.reached:
            # one corner the straight line hides (a ledge, a one-way door) can stop it;
            # only a few in a row say the follow attack doesn't walk us here at all
            self.skip[f.mid] = self.clock.time() + 10
            self.follow_stalls = getattr(self, "follow_stalls", 0) + 1
            if self.follow_stalls >= self.FOLLOW_TRIES:
                self.follow_ok = False
                self.log(_("the follow attack doesn't walk us to the monster here - walking "
                           "up to them ourselves"))
        return True

    def _fight_press(self, f: "_Fight") -> str | None:
        """The next press: the follow key while walking in, the picked skill (a burst until the
        game casts it), or the profile's attack keys. "again" (go round now), "return" (the fight
        ends), or None (pace the next press)."""
        sb, p, m, mid = f.sb, self.p, f.m, f.mid
        if sb is not None and f.follow and f.dist > f.rng + 0.5:     # (the follow attack needs the skill book)
            k = sb.follow_key()
            if k:
                # still walking in: no skill reaches yet, and the base attack's follow key keeps
                # the game walking us after it (a plain fallback key may be the standing one)
                self.key(k)
                f.i += 1
                self.sleep(0.3)
                return "again"
        picked = sb.pick(f.snap, m) if sb is not None else None
        if sb is not None and picked == "wait":
            # sleep until the cooldown ends, or until the next check is due, whichever first
            now = self.clock.time()
            self.sleep(max(0.02, min(sb.ready_in(now), f.check_at - now, 0.5)))
            return "again"
        if sb is not None and isinstance(picked, tuple) and sb.confirm and p.get("press_burst", True):
            if self._fight_burst(f, sb, picked):
                return "return"
        elif picked:
            self.key(picked[0])
        else:
            self.key(p["attack_keys"][f.i % len(p["attack_keys"])])
        f.i += 1
        if f.i % 3 == 0 and self.ui().get("target") != mid and not self.select_target(m):
            if f.dist >= self.SELECT_RANGE:
                # It drifted past the keys' reach (pull_range keeps the leash longer): end the
                # fight here and let the farm loop walk us up to it again instead of writing
                # the monster off for 30 s.
                return "return"
            self.skip[mid] = self.clock.time() + 30
            self.log(_("the game lost our target on {name} and won't take it back - next one",
                       name=f.mob["name"]))
            return "return"
        return None

    def _fight_burst(self, f: "_Fight", sb: combat.SkillBook, picked: combat.Press) -> bool:
        """Press the picked skill until the game casts it. True when the fight ends: refused again
        and again at a monster we never hurt."""
        if self.burst(picked[0], f.snap["self"].get("id"), sb.burst_time(picked[1])):
            f.refused_run = 0
            return False
        if f.dist > f.rng + 0.5:
            return False      # out of reach the follow attack is still walking us in
        sb.refuse(picked[1], self.clock.time())
        f.refused_run += 1
        if (f.refused_run >= self.REFUSED_RUN and f.m["hp"] >= f.mob["hp"]
                and not self.kill_tracker.hit_since(int(1000 * f.start)).get(f.mid)):
            # Not one cast went off at it: the server won't let us hit it from here
            # (live 2026-10-22: 221 fights waited the whole 6 s no-damage time for this).
            self.skip[f.mid] = self.clock.time() + self.UNSELECTED_SKIP
            self.log(_("the game won't let us hit {name} from here - next one", name=f.mob["name"]))
            return True
        return False


class _Fight:
    """One fight's running state, shared by _hit_until_down's steps.

    Presses go at the pace of the character's global cooldown (a live night showed one cast every
    3-4 s against a 1.0-1.2 s cooldown: each turn of the loop read the whole world first, and a
    press waited for three turns). The reading, healing and checks run at most every CHECK_EVERY,
    in the time the cooldown leaves; the press goes the moment it ends."""

    def __init__(self, bot: FightingMixin, mob: dict[str, Any]) -> None:
        self.mob, self.mid = mob, mob["id"]
        self.seen: dict[str, Any] | None = None   # the monster as the last look saw it (None: gone from view)
        self.look: dict[str, Any] | None = None   # the last look; the loop makes one before anything reads it
        self.rng = bot.reach()
        self.start = bot.clock.time()
        self.last_hp, self.last_drop = float(mob["hp"]), bot.clock.time()
        self.hit_us = False            # it cast at us during this fight
        self.sb = bot.skillbook
        self.casts_at_drop = getattr(self.sb, "casts_done", 0)
        self.i = 0
        self.check_at = self.checked = 0.0
        self.check_cost = 0.2
        self.follow = bot.follows()
        engage = max(self.rng, bot.FOLLOW_LEASH) if self.follow else self.rng
        pull = float(bot.p.get("pull_range", 0) or 0.0)
        self.leash = bot.leash_for(self.follow, self.rng, engage, pull)
        self.reached = False           # we have been within reach of it this fight
        self.refused_run = 0           # bursts in a row the game cast nothing for, within reach
        self.closing: tuple[float, float] | None = None   # (time, distance) while the follow attack walks us in
        self.dist = 0.0


    @property
    def m(self) -> dict[str, Any]:
        """The monster in view: read only once the loop has seen that it is."""
        if self.seen is None:
            raise RuntimeError("the fight read a monster that is out of view")
        return self.seen

    @property
    def snap(self) -> dict[str, Any]:
        """The last look: read only after the loop's first one."""
        if self.look is None:
            raise RuntimeError("the fight read the world before looking")
        return self.look

    def find(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        return next((e for e in snap["entities"] if e["id"] == self.mid), None)


class _Approach:
    """The walk up to one monster: when to stop walking, and what it noticed on the way (it opened
    fire early on a monster closing in, or the monster is about to die to someone else)."""

    def __init__(self, bot: FightingMixin, mid: int, rng: float, engage: float, pull: float) -> None:
        self.b, self.mid, self.rng, self.engage, self.pull = bot, mid, rng, engage, pull
        self.here = bot.map_id()
        self.closing: tuple[float, tuple[float, float], float] | None = None   # (when, where we were, how far) at the previous look
        self.opened_early = False      # we stopped short and started casting on the way in
        self.doomed: list[dict[str, Any]] = []

    def find(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        return next((e for e in snap["entities"] if e["id"] == self.mid), None)

    def reachable(self, me: dict[str, Any], e: dict[str, Any], within: float | None = None) -> bool:
        within = self.rng if within is None else within
        return math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= within and self.b.clear_shot(me, e, self.here)

    def stop(self, me: dict[str, Any]) -> bool:
        """Stop walking: it is in reach, gone (another player took it), about to die to someone
        else, or another one is in reach on the way (the farm loop takes that one - walking on
        past it wasted the trip)."""
        b = self.b
        snap = b.snap()
        m = self.find(snap)
        if m is None or m["hp"] <= 0 or self.reachable(me, m, self.engage):
            return True
        b.watch_hp(snap)
        if b.doomed(m, me):
            self.doomed.append(m)
            return True
        if self.pull and self._closing_in(me, m):
            return True
        if b.farm_filter is not None:
            return any(e["id"] != self.mid and self.reachable(me, e)
                       for e in b.targets(snap, *b.farm_filter, limit=self.rng + 2))
        return False

    def _closing_in(self, me: dict[str, Any], m: dict[str, Any]) -> bool:
        """The monster comes at us and is within the opening-fire range: fire early."""
        b = self.b
        d = math.hypot(m["x"] - me["x"], m["y"] - me["y"])
        now = b.clock.time()
        closing = self.closing
        if closing is not None and now - closing[0] < 1.0:
            our_step = math.hypot(me["x"] - closing[1][0], me["y"] - closing[1][1])
            # how much of the closing is the monster's own doing: our walking closes the
            # gap too, and only a monster that comes at us is worth shooting early
            if (d <= min(self.engage + self.pull, b.SELECT_RANGE) and closing[2] - d - our_step >= 0.1
                    and b.clear_shot(me, m, self.here)):
                self.opened_early = True
                return True
        self.closing = (now, (me["x"], me["y"]), d)
        return False
