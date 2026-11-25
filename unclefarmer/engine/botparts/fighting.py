"""Fighting: reach, targets, target modes, the fight loop, fighting back and defending.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from typing import Any

from ... import applog, atlas, jsonio
from ...settings import TARGET_MODES
from ...i18n import N_, _
from .. import beasts, bosses, combat, pets, picking, teamheal
from .base import (MOVE_SHOT_REACH, STOPPING, LosingFight, Respawned, Stopped, agent_strike, note_failure,
                   skill_things, snap_map)
from ...link import GameLink
from .parts import as_bot, Parts


# Why targets() left a monster in sight out (explain_left_out says how many for each).
LEFT_OUT = {
    "skip": N_("left alone for now (took no damage, couldn't be reached or selected)"),
    "kind": N_("of a kind that can't be attacked here"),
    "names": N_("left out by the only-fight / never-fight names"),
    "boss": N_("too close to a boss"),
    "elite": N_("demonized elites or too close to one, left alone as the character settings say"),
    "area": N_("outside the farming area"),
    "avoid": N_("in an elite or boss zone we keep out of"),
    "nogo": N_("in a no-go circle of this farming spot"),
    "far": N_("further to walk than the chase range"),
    # Replayed over two nights (turkey cave, Margit Rainforest, 2026-11-15): every one of these
    # could be walked to, by a way round longer than walk_dist's DIST_LIMIT. None was a hole in the map.
    "no way": N_("no way to walk there, or only a very long way round"),
    "doomed": N_("about to be killed by someone else"),
}

# Why a fight the farm loop picked ended without that monster counted as our kill: the "why" of
# its field-log record (logs/field-<day>.jsonl; nightwatch.py tallies them). Codes for whoever
# reads a night, never shown or spoken, so not translated. "?" in a record is an ending nobody
# named: a gap to close here.
NO_KILL = {
    "stuck": "the walk up to it got stuck",
    "map": "we were on another map",
    "taken": "gone before our fight began: someone else's",
    "doomed": "about to be killed by someone else",
    "another": "the walk stopped for another monster in reach, which was gone or out of reach by the next look",
    "unselectable": "the target keys wouldn't select it",
    "down": "reported dead, and the kill is counted at the next look",
    "gone": "gone during the fight with none of our hits on it",
    "pet": "our pet's attacker is fought first",
    "out of reach": "never got within reach of it",
    "no mp": "too little MP to hurt it",
    "no hit": "the game confirmed no hit of ours on it",
    "no damage": "it took no damage",
    "leash": "it moved past the fight's leash",
    "no shot": "it moved to where there is no clear shot",
    "not closing": "past our reach and we were not closing in",
    "follow stall": "the follow attack didn't walk us to it",
    "drifted": "it drifted past the target keys' range",
    "lost target": "the game lost our target and wouldn't take it back",
    "refused": "the game refused every cast at it from where we stood",
    "timeout": "the fight took too long",
    "died": "we died",
    "fled": "HP stayed low, so the fight was dropped to flee to town (flee_on_panic)",
    "stopped": "the run was stopped",
    "error": "the fight ended in an error",
}

# Seconds each of those leaves the monster alone (leave()). One not here either has its wait
# worked out where it happens (doomed, unselectable, refused) or holds nothing against the
# monster: the farm loop may pick it again at once.
LEFT_FOR = {"stuck": 120.0, "timeout": 120.0, "no damage": 120.0, "lost target": 30.0, "no mp": 20.0,
            "out of reach": 10.0, "no hit": 10.0, "follow stall": 10.0}


class FightingMixin(Parts):
    MP_EMPTY = 10         # MP percent under which our skills can't be cast: no damage is our own doing
    # A target another player stands nearer to than we do counts this many steps further when the
    # targets are put in order: a free one about as far comes first. Measured live (Underwater
    # World, an hour, 464 targets): 23% of the targets with a player nearer were lost to someone
    # else, 10% of the others. Leaving those targets out would cost more kills than it saves (most
    # are still ours, and only one or two monsters are in sight most of the time), so they are
    # only put after a free one, never left out.
    RIVAL_STEPS = 15

    POINTS_NAMES = {"Warrior": "rage", "Orc": "rage", "Hunter": "marked targets", "Assassin": "combos",
                    "Elf": "fire elements"}

    CHECK_EVERY = 0.4     # seconds between full readings of the world in a fight

    WALK_SPEED = 4.0      # tiles a second on foot, to tell how long a walk to a monster takes
    HP_WINDOW = 3.0       # seconds of a monster's HP readings that say how fast it is going down
    # Every note the bot keeps by monster id (Parts), in one place (master plan, Phase 5): an id is a
    # place in one map's list, so all of them go on a map change (forget_the_map); the ones that
    # hold something against a monster go with its death too (_forget_the_dead), since the game
    # gives the next monster that id. tests/test_fight_memory.py fails on an id-keyed note that is
    # in neither list nor in KEPT_NOTES.
    MAP_NOTES = ("skip", "mob_names", "counted", "walk_steps", "unselectable", "walk_failed", "hp_log",
                 "_attackers", "pet_suspects")
    DEATH_NOTES = ("skip", "unselectable", "walk_failed")
    KEPT_NOTES = {"phrases": "quick-phrase slots", "pets_refused": "our pets' ids", "beasts_refused": "our beasts' ids",
                  "beast_refusals": "our beasts' ids", "pet_described": "our pets' ids", "pet_mp_peak": "our pets' ids"}
    FOLLOW_LEASH = 12.0   # a melee character's follow attack chases a monster this far (straight line)
    FOLLOW_STALL = 2.0    # ... and is given up if it doesn't close in by a tile in this long
    FOLLOW_TRIES = 3      # stalls in a row, never reaching the monster, before we walk up ourselves
    DOOMED_SKIP = 10.0    # longest a monster someone else is about to kill is left alone
    LEFT_OUT_EVERY = 60.0   # seconds between two "none to fight" lines (explain_left_out)
    FIRST_HIT = 0.5       # seconds from being in reach to our first hit landing (press, cast, the game's answer)
    UNSELECTED_SKIP = 10.0  # seconds a monster the target keys wouldn't select is left alone...
    UNSELECTED_MAX = 300.0  # ... three times longer each time again, up to this
    UNSELECTED_MOVED = 3.0  # tiles it must move from where it couldn't be selected to be tried again
    REFUSED_RUN = 3       # bursts in a row with no cast, at a monster we never hurt, before leaving it
    REFUSED_RUN_INSIDE = 2    # ... in a dungeon, where another is always close: a refused run cost
                              # up to 5.3 s a monster in a 15-minute Scorpion Cave
    SELECT_RANGE = 20.0   # how far the game's target keys pick a monster at most, as far as we can
    MOVE_SHOT_REACH = MOVE_SHOT_REACH   # shooting on the way only for a character that hits from this far
    move_shots: bool = False   # Tools, Dungeons' "shoot while walking", for the visit
    burst_target: tuple[int, float] | None = None   # the fight's monster while a burst presses at it
    burst_check: Callable[[], bool] | None = None   # recheck the aim before retrying an unconfirmed press
    _speech: str
    select_refused: str = ""   # agent 41's reason the last `select` was refused ("" when it wasn't)
    _attackers: set[int]
    _pet_attackers: set[int]
    unselectable: dict[int, tuple[int, tuple[float, float], float]]
    walk_failed: dict[int, float]   # monster id -> until when: its walk got stuck (_walk_failed)
    no_way_at: list[list[float]]    # where the monsters targets() found no way to stood (sight_note)
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

    def shoots_moving(self) -> bool:
        """Shoot on the way: a character that fights from afar shoots whatever comes into reach
        while it walks up to its target, instead of stopping for each one - in a dungeon as Tools,
        Dungeons' "shoot while walking" says, farming in the field as the character's Fighting
        setting says. Hand runs cast 98% of their shots on the move, the bot stood for a third of
        its; simulated visits (caveloops) gave half as many kills again. In the field only the
        monsters the farm loop would fight are shot (targets: the farm area, no bosses, none
        another player is killing), and only while farming: a trip's fight back never shoots
        what isn't attacking us."""
        sb = self.skillbook
        if getattr(self, "in_instance", False):
            wanted = bool(getattr(self, "move_shots", False))
        else:
            wanted = (bool(self.p.get("shoot_while_walking", False))
                      and getattr(self, "farm_filter", None) is not None)
        return (wanted and getattr(self, "agent_act", False)
                and sb is not None and sb.ok and sb.confirm and self.reach() >= self.MOVE_SHOT_REACH
                and not self.follows())

    def move_shot(self, snap: dict[str, Any], me: dict[str, Any]) -> int | None:
        """One shot on the way, without stopping: at the nearest monster in reach with a clear line,
        when the global cooldown allows. The press goes while the walking keys stay held; the next
        look confirms it (or presses again). The id of the monster shot at, or None."""
        sb = self.skillbook
        if sb is None:
            return None
        rng = self.reach()
        here = snap_map(snap)
        area, only, skip, rects = self.farm_filter or (None, "", "", [])
        near = [e for e in self.targets(snap, area, only, skip, rects, limit=rng + 2)
                if math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= rng and self.clear_shot(me, e, here)]
        if not near:
            return None
        e = min(near, key=lambda e: math.hypot(e["x"] - me["x"], e["y"] - me["y"]))
        if sb.gcd_wait(self.clock.time()):
            return None
        if self.ui().get("target") != e["id"] and not self._select_by_id(e):
            return None
        sb.on_the_move = True     # instant skills only: a channel breaks as we walk on
        try:
            picked = sb.pick(snap, e)
        finally:
            sb.on_the_move = False
        if not isinstance(picked, tuple):
            return None
        self.key(picked[0])
        return e["id"]

    def downed(self, mid: int, since: float) -> bool:
        """A report since then left this monster at 0 HP - a cast of ours, our pet's or anyone's
        (agent 44 `targets`), or the game's death report: dead, though the world may not show it
        yet."""
        sb = self.skillbook
        return sb is not None and getattr(sb, "downed", {}).get(mid, -1.0) >= since

    def hit(self, me: dict[str, Any]) -> bool:
        """True when HP fell since the last step of a walk and fighting back is on (not for the
        walking assistant, nor while crossing Polson Wetland on a beast: neither stops to fight)."""
        hp = me.get("hp")
        dropped = self.last_hp is not None and hp is not None and hp < self.last_hp
        self.last_hp = hp
        return (dropped and self.p.get("fight_back", True) and not self.in_fight and not self.walk_only
                and not getattr(self, "crossing_poison", False))

    def fight_back(self) -> None:
        """Something hit us while walking: fight what is attacking us, then let the walk go on.
        Only monsters whose casts the agent saw count. A dip in HP with nothing casting is not an
        attack, and what merely stands nearby (a city's ducks and hens) is left alone: chasing
        those pulls a trip off its path and into the town's buildings. Without cast reports
        (an old agent) whatever is close is the best guess. The walking assistant never fights:
        it rides past on a Swift beast, which can't use skills anyway. Nor does a crossing of
        Polson Wetland without Poison Immunity: getting off the beast for a fight there poisons us
        (travel's crossing_poison)."""
        if self.walk_only or getattr(self, "crossing_poison", False):
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
        if snap is not None:
            # our pets' strikes are ours: a monster only they are at is no one else's (agent 44);
            # and a monster striking them is an attacker of ours (attackers)
            self.kill_tracker.own = pets.ours(as_bot(self), snap)
        tally = getattr(self, "tally", None)
        # The agent keeps the game's casts and deaths from before the run (a fight played by hand
        # an hour earlier came into a run's results): only this run's go into its damage and kills.
        begun = int(1000 * tally.started) if tally is not None else 0
        self.kill_tracker.begun = begun
        dead = self.kill_tracker.update(me["id"])
        if tally is not None:
            tally.combat.observe([c for c in self.kill_tracker.fresh if c["t"] >= begun], me["id"], self.mob_names,
                                 self.kill_tracker.own)
        self._forget_the_dead(getattr(self.kill_tracker, "died", []))
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

    def _forget_the_dead(self, died: Sequence[int]) -> None:
        """Monsters the game reported dead (anyone's kill): what we held against them goes with
        them. The game gives a respawned monster an id that just came free, so a skip kept past
        the death (120 s for one that took no damage or that we got stuck walking to) left the
        next monster of that id alone too - unless it attacked us. On a busy map, with other
        players killing what we had left, those reused ids added up to monsters all round that
        the bot walked past."""
        if not died:
            return
        for name in self.DEATH_NOTES:
            held: dict[int, Any] | None = getattr(self, name, None)
            if held:
                for mid in died:
                    held.pop(mid, None)

    def forget_the_map(self) -> None:
        """We are on another map (snap() saw it): everything held against a monster's id goes.
        An id is a place in one map's list, not a monster: -15 was a flame chicken on Flaming
        Mountain and something else on Thunder Mountain (live, 2026-11-10). So a skip made on the
        last map (up to 120 s) would leave a different monster alone here, and a hit of ours on
        the last map's -15 would make this map's -15 our kill when someone else killed it. (Read
        from the code, not seen live; the harm is bounded by how long each note lasts.)

        The kills are counted first, while the names of the monsters we left are still known.
        skip_names and killed_kinds stay: they are kept by map."""
        self.count_kills()
        held: dict[int, Any] | set[int] | None
        for name in self.MAP_NOTES:
            held = getattr(self, name, None)
            if held:
                held.clear()
        for name in ("hit", "struck", "pet_struck"):
            held = getattr(self.kill_tracker, name, None)
            if held:
                held.clear()
        sb = self.skillbook
        if sb is not None:
            sb.forget_the_map()

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
        if getattr(self, "following", None):
            return False        # following: the team's monster is ours to help with, however fast it dies
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
            return self._attackers | pets.suspects(as_bot(self))
        self._attackers_at = now
        r = self._game.json("casts %d" % int((now - 4) * 1000))
        data: dict[str, Any] = r if jsonio.is_object(r) else {}
        self.casts_seen = jsonio.is_object(r) and "error" not in data
        casts: list[dict[str, Any]] = data.get("casts", [])
        me: dict[str, Any] = getattr(self, "_snap_self", None) or getattr(self, "last_me", None) or {}
        own = self.kill_tracker.own
        striking = [c for c in casts if c["caster"] < 0 and self.at_us(c, me.get("id"), own)]
        self._attackers = {int(c["caster"]) for c in striking}
        self._pet_attackers = {int(c["caster"]) for c in casts
                               if c["caster"] < 0 and own and self.at_pet(c, own)}
        if striking:              # when the last one struck: the fusion beast's quiet time counts from it
            self.last_attacked = max(getattr(self, "last_attacked", 0.0), max(float(c.get("t") or now * 1000) for c in striking) / 1000.0)
        return self._attackers | pets.suspects(as_bot(self))

    def pet_attackers(self) -> set[int]:
        """The attackers (attackers()) whose casts struck our pet, and the monsters next to it
        while its HP falls fast with no cast to say who does it (pets.suspects)."""
        self.attackers()
        seen: set[int] = getattr(self, "_pet_attackers", set())
        return seen | pets.suspects(as_bot(self))

    @staticmethod
    def at_pet(c: dict[str, Any], pets: set[int]) -> bool:
        """A monster's cast struck our pet: it names the pet among whom it hit or struck."""
        rows: list[list[Any]] = list(c.get("hits") or []) + list(c.get("targets") or [])
        return any(row and row[0] in pets for row in rows)

    def at_us(self, c: dict[str, Any], me_id: int | None, pets: set[int]) -> bool:
        """A monster's cast struck us or our pet. From agent 44 every cast names whom it struck:
        a monster busy with another player is no attacker of ours. Counted as one, it was fought
        before everything else - from outside the farming area and past the walk limit - and
        fought back on the way, in any spot other players farm too. With no way to tell (an
        older agent, our id not read yet) every monster's cast counts, as before."""
        if me_id is None:
            return True
        hits: list[list[Any]] = c.get("hits") or []
        if any(h and (h[0] == me_id or h[0] in pets) for h in hits):
            return True               # it hit us or our pet, or we dodged it
        if "targets" in c:
            struck: list[list[Any]] = c["targets"]
            return any(row and (row[0] == me_id or row[0] in pets) for row in struck)
        return int(getattr(self, "agent_version", 0) or 0) < 44   # agent 44 names targets: none struck

    def targets(self, snap: dict[str, Any], area: Sequence[float] | None = None, only: str | None = None, skip: str | None = None, avoid_rects: Sequence[tuple[float, float, float, float]] = (), limit: float | None = None) -> list[dict[str, Any]]:
        """The monsters to fight, nearest first by walking distance - those attacking us before all
        others. limit: leave out any further than that many steps (not those attacking us). The
        choice itself is picking.choose; this gathers what the bot holds against the monsters in
        sight and keeps what the choice found (why monsters were left, the walks)."""
        now = self.clock.time()
        me: dict[str, Any] = snap["self"]
        hitting = self.attackers()
        rng = self.reach()
        self.watch_hp(snap)
        monsters = [e for e in snap["entities"] if e.get("kind") == "monster" and isinstance(e.get("id"), int)]
        unselectable: dict[int, Any] = getattr(self, "unselectable", {})
        held = [e for e in monsters if e["id"] in unselectable]
        notes = picking.Notes(
            now=now, hitting=frozenset(hitting), tagged=frozenset(self.kill_tracker.hit),
            skip=self.skip, kinds=self.skip_names, walk_failed=getattr(self, "walk_failed", {}),
            # (untrackable drops the note of one that has moved off the spot it was refused on)
            refused_again=frozenset(e["id"] for e in held if e["id"] in hitting and self._refused_again(e, now)),
            untrackable=frozenset(e["id"] for e in held if self.untrackable(e, now)),
            reach=rng)
        rules = picking.Rules(area=area, only=only, skip=skip, avoid_rects=avoid_rects, limit=limit,
                              no_go=tuple(getattr(self, "spot_no_go", ())),
                              boss_keep=self.p.get("boss_distance", 40),
                              skip_elites=bool(self.p.get("skip_demonized_elites")),
                              elites=bosses.elites_in_sight(as_bot(self), snap))
        players = [(p["x"], p["y"]) for p in snap["entities"]
                   if p.get("kind") == "player" and p.get("id") != me.get("id")
                   and " belongs to " not in str(p.get("name") or "") and picking.placed(p, {})]
        contested: set[int] = set()

        def order(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
            key = picking.nearest(e, steps, ctx)
            if e["id"] not in hitting and any(math.hypot(e["x"] - x, e["y"] - y) < key[3] for x, y in players):
                contested.add(e["id"])
                return key[0], key[1], steps + self.RIVAL_STEPS, key[3]
            return key

        got = picking.choose(snap, rules, notes,
                             walk=lambda e, lim: self.walk_dist(snap, e, lim),
                             doomed=lambda e, steps: self.doomed(e, me, steps, rng),
                             not_monsters=skill_things(), order=order)
        self.contested = contested   # for note_pick: a player stood nearer the target than we did
        for mid, kind in got.forgiven:
            self.skip.pop(mid, None)
            self.skip_names.pop(kind, None)
        self.mob_names.update(got.names)
        self.left_out = got.left           # for the farm loop's "nothing to fight" line (explain_left_out)
        self.no_way_at = got.no_way        # for the search walk's field record (sight_note)
        self.walk_steps = got.steps        # for note_pick: the walk the farm loop's choice would take
        if len(self.mob_names) > 2000:
            self.mob_names = {e["id"]: e["name"] for e in got.targets}
        return got.targets

    def explain_left_out(self, map_id: int | None = None) -> None:
        """The farm loop found nothing to fight: when monsters were in sight, say how many were left
        alone for each reason and on which map, at most once a minute, so a log shows why the bot
        walked past monsters instead of only that it did."""
        left: dict[str, int] = getattr(self, "left_out", {})
        if not left:
            return
        now = self.clock.time()
        if now - getattr(self, "_left_said_at", 0.0) < self.LEFT_OUT_EVERY:
            return
        self._left_said_at = now
        parts = ["%s %d" % (_(LEFT_OUT[k]), n) for k, n in sorted(left.items(), key=lambda kv: -kv[1])]
        if map_id:
            self.log(_("monsters in sight, none to fight ({map}): {why}", map=atlas.label(map_id),
                       why="; ".join(parts)))
        else:
            self.log(_("monsters in sight, none to fight: {why}", why="; ".join(parts)))

    def sight_note(self) -> dict[str, Any]:
        """What the last targets() left out, for the field record of the search walk that follows:
        how many monsters were in sight, how many for each reason (LEFT_OUT's keys), and where
        those with no way to walk to stood (the first few: places to fix the map data from).
        Empty with nothing in sight."""
        left: dict[str, int] = getattr(self, "left_out", {})
        if not left:
            return {}
        note: dict[str, Any] = {"sight": sum(left.values()), "left": dict(left)}
        where: list[list[float]] = getattr(self, "no_way_at", [])
        if where:
            note["no_way"] = [list(xy) for xy in where]
        return note

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
            "rival": mob["id"] in self.__dict__.get("contested", ()),
            "_at": self.clock.time(), "_kills": self.kills,
        }

    def fight(self, mob: dict[str, Any]) -> None:
        beasts.ensure(as_bot(self), beasts.FIGHT)
        self.in_fight = True
        rec = getattr(self, "picked", None)
        self.picked = None
        if rec is not None and rec["id"] != mob["id"]:
            rec = None            # a fight the farm loop didn't pick (fighting back on the way)
        # A fight on the way to this one (fighting back in the walk) runs inside it: the record
        # under way is put back when that one ends, so this fight's reach time and reason stay its own.
        outer = getattr(self, "field_record", None)
        self.field_record = rec
        try:
            self._fight(mob)
        except Respawned:
            self.ended("died")
            raise
        except LosingFight:
            self.ended("fled")
            raise
        except Stopped:
            self.ended("stopped")
            raise
        except Exception:
            self.ended("error")
            raise
        finally:
            self.field_record = outer
            self.in_fight = False
            self.last_combat = self.clock.time()
            self.last_hp = None
            if rec is not None:
                self._write_fight(rec)

    def ended(self, why: str) -> None:
        """Why the fight the farm loop picked ends without a kill (a key of NO_KILL), for its
        field record. The first reason given stands: the one closest to what happened. Nothing
        outside a picked fight, where there is no record."""
        rec = getattr(self, "field_record", None)
        if rec is not None and "why" not in rec:
            rec["why"] = why

    def leave(self, mid: int, why: str, seconds: float | None = None) -> float:
        """Leave this monster alone for a while and say why: the one way a fight holds anything
        against a monster's id. why is a key of NO_KILL; seconds is LEFT_FOR's for it unless
        given. Until when (the clock's time). targets() leaves it out till then ("skip") - unless
        it attacks us, dies or we change maps (picking.left_out, _forget_the_dead, forget_the_map)."""
        until = self.clock.time() + (LEFT_FOR[why] if seconds is None else seconds)
        self.skip[mid] = until
        self.ended(why)
        return until

    def _write_fight(self, rec: dict[str, Any]) -> None:
        at = rec.pop("_at")
        rec["kills"] = self.kills - rec.pop("_kills")
        # kills is every kill counted while this went on (another monster's too: damage over time,
        # an area skill); killed is this monster, or the one our hits moved the fight on to
        fought = [i for i in (rec["id"], rec.get("moved_to")) if i is not None]
        rec["killed"] = any(self.counted.get(i, -math.inf) >= at for i in fought)
        if rec["killed"]:
            rec.pop("why", None)
        else:
            rec.setdefault("why", "?")
        rec["end_s"] = round(self.clock.time() - at, 2)
        if "reach_at" in rec:
            rec["reach_s"] = round(rec.pop("reach_at") - at, 2)
        self._end_place(rec)
        applog.field(rec)

    def note_search(self, map_id: int, me: dict[str, Any], cell: tuple[float, float], start: float, result: str,
                    seen: dict[str, Any] | None = None) -> None:
        """A walk with nothing to fight, to the field log: from where to where, how long, and why
        it ended. seen (sight_note, taken when the walk was decided on): the monsters in sight
        then and why each was left out."""
        rec: dict[str, Any] = {"kind": "search", "who": getattr(self, "character", None), "map": map_id,
                               "me": [round(me["x"], 1), round(me["y"], 1)],
                               "to": [round(cell[0], 1), round(cell[1], 1)],
                               "s": round(self.clock.time() - start, 2), "result": result}
        rec.update(seen or {})
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
        held, so the look costs the press nothing. A fight's burst (burst_target: the monster and
        when the fight began) stops the moment a report says that monster died - by our pet,
        another player, anything - with False."""
        sb = self.skillbook
        if sb is None:          # only a fight with smart skills bursts (_fight_burst)
            raise RuntimeError("a burst needs the skill book")
        self.check()
        if self.slot_command(key) is None:
            self.wait_keys_free()
        start = self.clock.time()
        n = 0
        while self.clock.time() - start < longest:
            if n and self.burst_check is not None and not self.burst_check():
                return False
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
                aim = self.burst_target
                if aim is not None and self.downed(*aim):
                    return False          # dead already: no cast is coming, and none is wanted
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
            self.note_unselectable(mob)
            return
        mob = self._leap_in(mob, rng)
        with self.timed("fight"):
            self._hit_until_down(mob)

    LEAP_WAIT = 1.5       # seconds a leap is given to land us beside the monster

    def leap_ready(self, me: dict[str, Any], m: dict[str, Any], rng: float) -> bool:
        """A leap (Leap Slash) is ready and reaches this monster from here: past our reach, inside
        the leap's, with a clear line. Reads nothing from the game."""
        sb = self.skillbook
        if sb is None or not sb.ok or self.walk_only:
            return False
        d = math.hypot(m["x"] - me["x"], m["y"] - me["y"])
        if d <= rng + combat.LEAP_PAST_REACH:
            return False
        return sb.leap_for(d, self.clock.time()) is not None and self.clear_shot(me, m)

    def _leap_in(self, mob: dict[str, Any], rng: float) -> dict[str, Any]:
        """The monster, selected, is past our reach but inside a ready leap's: jump to it instead of
        walking (Phase 1b item 5; Leap Slash lands 1.0 tile from the target, measured on 251 casts).
        Not on a fusion beast (a beast casts nothing of ours). The monster as it is after."""
        me = self.me()
        sb = self.skillbook
        if sb is None or not self.leap_ready(me, mob, rng):
            return mob
        if any(r.get("active") for r in beasts.owned(as_bot(self))):
            return mob
        now = self.clock.time()
        leap = sb.leap_for(math.hypot(mob["x"] - me["x"], mob["y"] - me["y"]), now)
        press = sb.press_leap(leap, now) if leap is not None else None
        if press is None or self.dialog().get("open"):
            return mob
        self.log(_("{skill} to reach {name}", skill=press[1], name=mob["name"]))
        rec = getattr(self, "field_record", None)
        if rec is not None:
            rec["leap"] = press[1]
        self.key(press[0])
        end = now + self.LEAP_WAIT
        while self.clock.time() < end:
            snap = self.snap()
            m = next((e for e in snap["entities"] if e["id"] == mob["id"]), None)
            if m is None or m["hp"] <= 0:
                return mob
            me = snap["self"]
            if math.hypot(m["x"] - me["x"], m["y"] - me["y"]) <= rng:
                return m
            self.sleep(0.1)
        return mob

    def note_unselectable(self, mob: dict[str, Any]) -> None:
        """The game won't let us select this monster even from as close as we got: seen, but not
        one the target keys can track (behind a wall or in a rock, past a door edge - Skull Cave's
        pillars). It is left alone UNSELECTED_SKIP seconds, three times longer each time it fails
        again where it stands (up to UNSELECTED_MAX), so the farm loop doesn't walk back to it every
        ten seconds; once it has moved off that spot it is a fresh try (targets)."""
        now = self.clock.time()
        rows: dict[int, tuple[int, tuple[float, float], float]] = self.__dict__.setdefault("unselectable", {})
        n, at, _until = rows.get(mob["id"], (0, (mob["x"], mob["y"]), 0.0))
        if math.hypot(mob["x"] - at[0], mob["y"] - at[1]) >= self.UNSELECTED_MOVED:
            n = 0
        n += 1
        # Saturate before multiplying: an unbounded power overflows before min can cap it.
        wait = min(self.UNSELECTED_MAX, self.UNSELECTED_SKIP)
        for _step in range(n - 1):
            if wait >= self.UNSELECTED_MAX:
                break
            wait = min(self.UNSELECTED_MAX, wait * 3)
        rows[mob["id"]] = (n, (mob["x"], mob["y"]), now + wait)
        self.leave(mob["id"], "unselectable", wait)
        if len(rows) > 500:
            self.unselectable = {k: v for k, v in rows.items() if v[2] > now}
        if n == 1:
            self.log(_("the target keys wouldn't select {name} - next one", name=mob["name"]))
        else:
            self.log(_("the target keys still won't select {name} where it stands - leaving it {n} seconds",
                       name=mob["name"], n=int(wait)))

    def untrackable(self, e: dict[str, Any], now: float) -> bool:
        """Left alone for now by note_unselectable - unless it has moved off the spot where the
        game wouldn't select it, which is a new chance (the record is dropped)."""
        rows: dict[int, tuple[int, tuple[float, float], float]] = getattr(self, "unselectable", {})
        u = rows.get(e["id"])
        if u is None or u[2] <= now:
            return False
        if math.hypot(e["x"] - u[1][0], e["y"] - u[1][1]) < self.UNSELECTED_MOVED:
            return True
        del rows[e["id"]]
        self.skip.pop(e["id"], None)
        return False

    def _refused_again(self, e: dict[str, Any], now: float) -> bool:
        """Left alone by note_unselectable after more than one refusal where it stands."""
        u = getattr(self, "unselectable", {}).get(e["id"])
        return u is not None and u[0] >= 2 and self.untrackable(e, now)

    def _approach(self, mob: dict[str, Any], way: "_Approach", turned: bool = False) -> dict[str, Any] | None:
        """Walk up to the monster unless it is in reach already. The monster as it is now, or None
        when the farm loop should pick again (taken, about to die, stuck, another one on the way).
        turned: this walk is to one that attacked us on the way to another (it turns only once)."""
        mid = mob["id"]
        if way.reachable(self.me(), mob, way.engage) or self.leap_ready(self.me(), mob, way.rng):
            return mob
        self.approaching = True       # shields may go up on the way in (support_between_fights)
        try:
            with self.timed("approach"):
                walked = self.walk_to((mob["x"], mob["y"]), near=way.stop)
        finally:
            self.approaching = False
        if walked == "stuck":
            self._walk_failed(mid)
            return None
        snap = self.snap()
        if way.left_map(snap):
            self.ended("map")
            return None           # the walk ended on another map: the farm loop sorts that out
        if self.downed(mid, way.began):
            self.count_kills(snap)        # shot dead on the way: on to the next one at once
            self.ended("down")
            return None
        m = way.find(snap)
        if m is None or m["hp"] <= 0:
            self._taken(snap, mob)
            return None
        if way.doomed and not way.reachable(self.me(), m):
            # left alone until it should be dead: else the farm loop picks it again at once and
            # this turns it down again (live: the same monster 16 times a second, 8,201 in a night)
            self.leave(mid, "doomed", min(self.DOOMED_SKIP, self.dying_in(m) + 2.0))
            self.log(_("{name} is about to be killed by someone else - next one", name=mob["name"]))
            return None
        if walked == "near" and way.leap and m["hp"] > 0:
            return m              # stopped where a leap reaches it: _fight jumps the rest
        if walked == "near" and not way.reachable(self.me(), m, way.engage) and not way.opened_early:
            other = way.beside(snap, self.me())
            if other is not None and way.attacker:
                rec = getattr(self, "field_record", None)
                if rec is not None:
                    rec["moved_to"] = other["id"]
                self.log(_("{name} attacks us on the way - fighting it first", name=other["name"]))
                if turned:
                    return other if way.reachable(self.me(), other, way.engage) else None
                return self._approach(other, _Approach(self, other["id"], way.rng, way.engage, way.pull), True)
            if other is not None:
                # Stopped for another one in reach: that one is fought now. Handing it back to the
                # farm loop picked the same monster again whenever it was the nearer walk (round a
                # corner, no clear shot) - and this stopped for the other one again, at once: the
                # same pick up to 66 times in 3 seconds, live, until one of the two moved.
                rec = getattr(self, "field_record", None)
                if rec is not None:
                    rec["moved_to"] = other["id"]
                return other
            self.ended("another")
            return None           # the one it stopped for is gone or out of reach again: pick again
        return m

    def _walk_failed(self, mid: int) -> None:
        """The walk up to it got stuck: left alone 120 s, even while it attacks us (picking.left_out)."""
        until = self.leave(mid, "stuck")
        rows: dict[int, float] = self.__dict__.setdefault("walk_failed", {})
        rows[mid] = until
        if len(rows) > 500:
            self.walk_failed = {k: v for k, v in rows.items() if v > self.clock.time()}

    def _taken(self, snap: dict[str, Any], mob: dict[str, Any]) -> None:
        """It is gone before our fight began: counted if a hit of ours killed it, else someone else's."""
        self.count_kills(snap)
        if not self.counted_kill(mob["id"]):
            self.ended("taken")
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
            self._walk_failed(mid)
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
            self._fight_refresh(f, now)
            if f.seen is None or f.seen["hp"] <= 0 or self.downed(f.mid, f.start):
                # gone, at 0 HP, or reported dead by any cast or death report since the last look:
                # no press at it now (its HP in the last look can be up to 2 s old)
                if f.sb is not None and hasattr(f.sb, "forget_target"):
                    f.sb.forget_target(f.mid)
                if f.seen is not None and f.seen["hp"] > 0:
                    self.count_kills(f.snap)
                    self.ended("down")
                    return
                self._fight_over(f)
                return
            if f.look is not None and f.checked == now and self._pet_needs_us(f):
                return                # the farm loop takes the pet's attacker next (attackers first)
            casts = getattr(self.skillbook, "casts_done", 0) if self.skillbook is not None else 0
            if self._fight_hits_elsewhere(f, casts) or self._fight_no_damage(f, casts):
                return
            extended = f.sb is not None and f.sb.extended_mark_shot(f.dist, self.clock.time())
            if not extended and self._fight_moved_off(f):
                return
            step = self._fight_press(f, extended=extended)
            if step == "return":
                return
            if self.downed(f.mid, f.start):
                # One hit was enough: the cast report shows it at 0 HP. The fight's next look was
                # up to CHECK_EVERY away and the walk to the next one waited for it (0.45 s a kill,
                # 113 s of a 15-minute Scorpion Cave). The kill is counted when its death report
                # comes in (count_kills, here or at the farm loop's next look).
                if f.sb is not None and hasattr(f.sb, "forget_target"):
                    f.sb.forget_target(f.mid)
                self.count_kills(f.snap)
                self.ended("down")
                return
            if step == "again":
                continue
            if f.sb is not None and f.sb.confirm:
                self.sleep(0.05)      # the cooldown paces us now
            else:
                self.sleep(p["attack_gap"])
        self.log(_("{name}: fight took too long, leaving it", name=f.mob["name"]))
        self.leave(f.mid, "timeout")

    def _fight_refresh(self, f: "_Fight", now: float) -> None:
        """Fit upkeep between casts and refresh positions when an attack is due."""
        due = f.sb.ready_in(now) if f.sb is not None and f.look is not None else 1.0
        if f.look is None or (now >= f.check_at and (due > f.check_cost + 0.05 or now - f.checked > 2.0)):
            self._fight_look(f, now)
        elif f.sb is None or due <= 0:
            FightingMixin._fight_position(self, f, self.snap())

    def _fight_look(self, f: "_Fight", now: float) -> None:
        """A full look between presses: the world, our HP, pets, popups, kills, rage or marks, who
        attacks us, and how far the monster is (reached: we have been within reach of it)."""
        f.checked = now
        f.check_at = now + self.CHECK_EVERY
        snap = self.snap()
        FightingMixin._fight_position(self, f, snap)
        self.look_after(snap["self"])
        teamheal.look_after(as_bot(self), snap)       # only while following
        pets.look_after(as_bot(self), snap)
        self.clear_popups()
        self.count_kills(snap)
        self.read_points(snap["self"], now)
        f.hit_us = f.hit_us or f.mid in self.attackers()
        f.check_cost = 0.7 * f.check_cost + 0.3 * (self.clock.time() - now)

    def _fight_position(self, f: "_Fight", snap: dict[str, Any]) -> None:
        """Refresh the aim without doing the full between-cast upkeep."""
        f.look, f.seen, f.position_at = snap, f.find(snap), self.clock.time()
        if f.seen is not None:
            f.dist = math.hypot(f.seen["x"] - snap["self"]["x"], f.m["y"] - snap["self"]["y"])
            if f.dist <= f.rng + 0.5:
                if f.follow and not f.reached:
                    self.follow_stalls = 0      # the follow attack got us there
                f.reached = True

    PET_FINISH = 0.3      # a monster we fight this far down is finished before turning to the pet's attacker

    def _pet_needs_us(self, f: "_Fight") -> bool:
        """A monster is striking our pet while the one we fight attacks neither us nor the pet:
        end this fight, and the farm loop (attackers first) takes the pet's attacker on. Not when
        this one is nearly dead (finished first), and only for an attacker we may fight at all
        (targets(): not a boss, not one left alone). Said once per attacker."""
        biting = self.pet_attackers() - {f.mid}
        if not biting or f.mid in self.attackers():
            return False
        m = f.m
        if m.get("maxhp") and m["hp"] < self.PET_FINISH * m["maxhp"]:
            return False
        biter = next((e for e in self.targets(f.snap) if e["id"] in biting), None)
        if biter is None:
            return False
        told: set[int] = self.__dict__.setdefault("_told_pet_biters", set())
        if biter["id"] not in told:
            told.add(biter["id"])
            if len(told) > 200:
                told.clear()
            self.log(_("{name} is attacking our pet - fighting it first", name=biter["name"]))
        self.ended("pet")
        return True

    def _fight_over(self, f: "_Fight") -> None:
        """It is gone or dead: ours (counted), someone else's, or out of sight - or we are
        the ones who went: on another map (a timed area ended, the game moved us) the monster is
        where we left it, and neither a kill nor anyone else's."""
        mob = f.mob
        if f.left_map(f.snap):
            self.log(_("we are on another map now - leaving the fight with {name}", name=mob["name"]))
            self.ended("map")
            return
        if self.kill_tracker.live:
            # the game says whose it is: dead with a hit of ours on it (count_kills logs
            # it), else someone else's kill, or it walked out of sight
            self.count_kills(f.snap)
            if not self.counted_kill(f.mid):
                self.ended("gone" if f.i else "taken")
                if not self.kill_tracker.tagged(f.mid):
                    self.log(_("{name} is gone, and none of our hits landed on it", name=mob["name"])
                             if f.i else _("{name} was taken by someone else - next one", name=mob["name"]))
            return
        if f.i == 0 and f.last_hp >= mob["hp"]:
            # gone before we pressed a thing: other players farm here too. Not ours to
            # count; the farm loop takes the next one at once.
            self.ended("taken")
            self.log(_("{name} was taken by someone else - next one", name=mob["name"]))
            return
        self.kills += 1
        self.counted[f.mid] = self.clock.time()
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
                rec = getattr(self, "field_record", None)
                if rec is not None:
                    rec["moved_to"] = other       # the record's kill may be this one now
                f.mid, f.mob, f.seen = other, o, o
                FightingMixin._fight_position(self, f, f.snap)
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
            self.leave(mid, "out of reach")
            return True
        if not self._no_damage_yet(f, casts):
            return False
        if self.mp_share is not None and self.mp_share < self.MP_EMPTY:
            # Our skills fail with no MP, so nothing would take damage: the monster isn't
            # to blame (live: an out-of-blues Hunter wrote off whole kinds of monsters).
            self.log(_("MP {pct}% - too little to hurt {name}", pct=int(self.mp_share), name=mob["name"]),
                     speak=True)
            self.leave(mid, "no mp")
            return True
        if not self._landed_on(f):
            # The game confirmed no hit of ours on it this whole fight: we never really attacked
            # it (no lock on it, our casts went elsewhere or nowhere), so it says nothing about
            # the monster. Live in Dark pit City: six seconds "fighting" a Goblin cast only Soul
            # Purify on ourselves, and the Goblins - killed there minutes before - were written
            # off for half an hour while they chased and hit us. Another one now, this one soon.
            self.log(_("couldn't get to {name} - next one", name=mob["name"]))
            self.leave(mid, "no hit")
            return True
        self.log(_("{name} takes no damage, leaving it", name=mob["name"]))
        self.leave(mid, "no damage")
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

    def _landed_on(self, f: "_Fight") -> bool:
        """A hit of ours landed on the fought monster this fight, as the game reported it (agent
        v30 casts: undodged hits, kill_tracker.hit). Without those reports (an older agent) there
        is no telling, and it counts as landed - the verdict stays as it always was."""
        tracker = getattr(self, "kill_tracker", None)
        if tracker is None or not getattr(tracker, "live", False):
            return True
        return f.mid in tracker.hit_since(int(1000 * f.start))

    def _fight_moved_off(self, f: "_Fight") -> bool:
        """It moved off. With the follow attack the game walks after it, so we keep at it while it
        is in a straight line within the leash and we are closing in; else the farm loop picks the
        nearest again (a melee fight broke off and restarted this way 238 times in two nights).
        True when the fight ends here."""
        # The follow attack walks us the last stretch (up to its leash); standing still, a monster
        # past our own reach is the farm loop's to walk up again, not something to keep pressing at.
        if f.dist <= f.rng + (3.0 if f.follow else 0.5):
            f.closing = None
            return False
        if f.dist > f.leash:
            self.ended("leash")
            return True
        if not self.clear_shot(f.snap["self"], f.m, snap_map(f.snap)):
            self.leave(f.mid, "no shot", self.UNSELECTED_SKIP)
            return True
        if f.closing is None or f.dist < f.closing[1] - 1.0:
            f.closing = (self.clock.time(), f.dist)
            return False
        if self.clock.time() - f.closing[0] <= self.FOLLOW_STALL:
            return False
        if f.follow and not f.reached:
            # one corner the straight line hides (a ledge, a one-way door) can stop it;
            # only a few in a row say the follow attack doesn't walk us here at all
            self.leave(f.mid, "follow stall")
            self.follow_stalls = getattr(self, "follow_stalls", 0) + 1
            if self.follow_stalls >= self.FOLLOW_TRIES:
                self.follow_ok = False
                self.log(_("the follow attack doesn't walk us to the monster here - walking "
                           "up to them ourselves"))
        # Not following, out of every attack's range and not closing in: hand it back to the farm
        # loop, which walks up and starts the fight again. No skip: standing there pressing at it
        # (or pressing nothing) only reached the no-damage timeout and wrote the monster off.
        self.ended("not closing")
        return True

    def _fight_can_press(self, f: "_Fight", skill: combat.Skill | None = None, *,
                         follow: bool = False, retry: bool = False) -> bool:
        """Validate the intended attack against the latest world and cast reports."""
        now = self.clock.time()
        if retry or now - f.position_at >= combat.SLOT_GAP:
            FightingMixin._fight_position(self, f, self.snap())
        if f.seen is None or f.seen["hp"] <= 0 or self.downed(f.mid, f.start):
            f.press_block = "again"   # the loop accounts for the finished target
            return False
        in_range = (f.sb.reaches(skill, f.dist) if f.sb is not None and skill is not None
                    else f.dist <= f.rng)
        if not in_range and not follow:
            f.press_block = "wait" if skill is not None else "return"
            if skill is None:
                self.ended("out of reach")
            return False
        if retry and not self.clear_shot(f.snap["self"], f.m, snap_map(f.snap)):
            self.leave(f.mid, "no shot", self.UNSELECTED_SKIP)
            f.press_block = "return"
            return False
        if self.ui().get("target", f.mid) != f.mid:
            if self.select_target(f.m) and self.ui().get("target", f.mid) == f.mid:
                self._fight_position(f, self.snap())
                f.press_block = "again"   # reselection can take time, so pick from the new view
            else:
                f.press_block = "return"
                if f.dist >= self.SELECT_RANGE:
                    self.ended("drifted")
                else:
                    self.leave(f.mid, "lost target")
                    self.log(_("the game lost our target on {name} and won't take it back - next one",
                               name=f.mob["name"]))
            return False
        if self._fight_foreign_finish(f, skill, now):
            self.leave(f.mid, "doomed", self.FIRST_HIT)
            f.press_block = "return"
            return False
        return True

    def _fight_foreign_finish(self, f: "_Fight", skill: combat.Skill | None, now: float) -> bool:
        """Recent foreign hits put it below our expected time to land a hit."""
        tracker = self.kill_tracker
        rows = tracker.struck.get(f.mid)
        if not rows or tracker.tagged(f.mid) or getattr(self, "following", None):
            return False
        stamp, hp = rows[-1]
        if now - stamp / 1000.0 > self.FIRST_HIT or f.m["hp"] > hp:
            return False              # old damage, or the world already shows more HP
        rate = tracker.struck_rate(f.mid)
        cast = (skill or {}).get("cast") or 0.0
        if skill is not None and f.sb is not None and combat.elf_combos.unpenalized(
                skill, f.sb.available_to_channel_without_penalty):
            cast = 0.0
        return rate is not None and hp / rate < self.FIRST_HIT + cast

    def _fight_press(self, f: "_Fight", *, extended: bool = False) -> str | None:
        """Send a follow key, a picked skill, or a profile attack at a valid target."""
        sb, p = f.sb, self.p
        f.press_block = None
        unpenalized = sb is not None and getattr(sb, "available_to_channel_without_penalty", False)
        walking_in = f.follow and f.dist > f.rng + 0.5 and not extended
        follow_key = sb.follow_key() if sb is not None and walking_in else None
        if follow_key and not unpenalized:
            return self._fight_follow(f, follow_key)
        if sb is not None and (teamheal.look_after(as_bot(self), f.snap) or pets.heal_in_fight(as_bot(self), f.snap)):
            return None       # this press healed a teammate (while following) or the pet

        def target_check(skill: combat.Skill) -> bool:
            return self._fight_can_press(f, skill)

        picked = sb.pick(f.snap, f.m, target_check=target_check) if sb is not None else None
        if f.press_block is not None or (not isinstance(picked, tuple) and self.downed(f.mid, f.start)):
            return f.press_block or "again"
        if not picked and follow_key:
            return self._fight_follow(f, follow_key)
        if sb is not None and picked == "wait":
            # sleep until the cooldown ends, or until the next check is due, whichever first
            now = self.clock.time()
            self.sleep(max(0.02, min(sb.ready_in(now), f.check_at - now, 0.5)))
            return "again"
        if (sb is not None and isinstance(picked, tuple) and sb.confirm and p.get("press_burst", True)
                and not (sb.cls == "Elf" and picked[1] in ("Pyroblast", "Blazing Storm"))):
            if self._fight_burst(f, sb, picked):
                return "return"
            if f.press_block is not None:
                return f.press_block
        elif picked:
            self.key(picked[0])
        elif sb is None or not sb.ok:
            # An older agent or Smart skills off: use the profile's keys within its range.
            if not self._fight_can_press(f):
                return f.press_block
            self.key(p["attack_keys"][f.i % len(p["attack_keys"])])
        else:
            # A working book falls back only to skills that are allowed and within range.
            press = sb.fallback(self.clock.time(), lambda skill: sb.reaches(skill, f.dist),
                                target_check=target_check)
            if f.press_block is not None:
                return f.press_block
            if press:
                self.key(press[0])
            else:
                self.sleep(max(0.2, p.get("attack_gap", 0.35)))
        f.i += 1
        return None

    def _fight_follow(self, f: "_Fight", key: str) -> str | None:
        if not self._fight_can_press(f, follow=True):
            return f.press_block
        self.key(key)
        f.i += 1
        self.sleep(0.3)
        return "again"

    def _fight_burst(self, f: "_Fight", sb: combat.SkillBook, picked: combat.Press) -> bool:
        """Press the picked skill until the game casts it. True when the fight ends: refused again
        and again at a monster we never hurt."""
        self.burst_target = (f.mid, f.start)
        skill = sb.skill(picked[1]) or {"name": picked[1]}
        if skill.get("role") not in combat.CALM_ROLES:
            self.burst_check = lambda: self._fight_can_press(f, skill, retry=True)
        try:
            cast = self.burst(picked[0], f.snap["self"].get("id"), sb.burst_time(picked[1]))
        finally:
            self.burst_target = None
            self.burst_check = None
        if cast:
            f.refused_run = 0
            return False
        if f.press_block is not None or self.downed(f.mid, f.start):
            return False          # it died mid-burst: no refusal of ours (_hit_until_down ends it)
        if f.dist > f.rng + 0.5:
            return False      # out of reach the follow attack is still walking us in
        sb.refuse(picked[1], self.clock.time())
        if skill.get("role") in combat.CALM_ROLES:
            f.refused_run = 0     # a heal or buff the game wouldn't cast is not the monster's fault
            return False
        f.refused_run += 1
        limit = self.REFUSED_RUN_INSIDE if self.in_instance else self.REFUSED_RUN
        if (f.refused_run >= limit and f.m["hp"] >= f.mob["hp"]
                and not self.kill_tracker.hit_since(int(1000 * f.start)).get(f.mid)):
            # Not one cast went off at it: the server won't let us hit it from here
            # (live 2026-10-22: 221 fights waited the whole 6 s no-damage time for this).
            self.leave(f.mid, "refused", self.UNSELECTED_SKIP)
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
        self.map: int | None = getattr(bot, "notes_map", None)    # the map this fight is on
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
        self.position_at = self.start
        self.check_cost = 0.2
        self.follow = bot.follows()
        engage = max(self.rng, bot.FOLLOW_LEASH) if self.follow else self.rng
        pull = float(bot.p.get("pull_range", 0) or 0.0)
        self.leash = bot.leash_for(self.follow, self.rng, engage, pull)
        self.reached = False           # we have been within reach of it this fight
        self.refused_run = 0           # bursts in a row the game cast nothing for, within reach
        self.closing: tuple[float, float] | None = None   # (time, distance) while the follow attack walks us in
        self.dist = 0.0
        self.press_block: str | None = None


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

    def left_map(self, snap: dict[str, Any]) -> bool:
        """This look is of another map than the fight began on (0 is no map: a change in progress)."""
        return _other_map(self.map, snap)

    def find(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        if self.left_map(snap):
            return None           # whatever holds its id on this map is not our monster
        return next((e for e in snap["entities"] if e["id"] == self.mid), None)


def _other_map(began: int | None, snap: dict[str, Any]) -> bool:
    here = snap_map(snap)
    return bool(began) and isinstance(here, int) and here != 0 and here != began


class _Approach:
    """The walk up to one monster: when to stop walking, and what it noticed on the way (it opened
    fire early on a monster closing in, or the monster is about to die to someone else)."""

    def __init__(self, bot: FightingMixin, mid: int, rng: float, engage: float, pull: float) -> None:
        self.b, self.mid, self.rng, self.engage, self.pull = bot, mid, rng, engage, pull
        self.here = bot.map_id()
        self.began = bot.clock.time()
        self.closing: tuple[float, tuple[float, float], float] | None = None   # (when, where we were, how far) at the previous look
        self.opened_early = False      # we stopped short and started casting on the way in
        self.other: int | None = None  # another monster in reach that the walk stopped for (stop)
        self.attacker = False          # ...and it stopped for it because it attacks us, nearer than ours
        self.leap = False              # stopped where a ready leap reaches it (stop)
        self.doomed: list[dict[str, Any]] = []

    def left_map(self, snap: dict[str, Any]) -> bool:
        return _other_map(self.here, snap)

    def find(self, snap: dict[str, Any]) -> dict[str, Any] | None:
        if self.left_map(snap):
            return None           # whatever holds its id on this map is not our monster
        return next((e for e in snap["entities"] if e["id"] == self.mid), None)

    def reachable(self, me: dict[str, Any], e: dict[str, Any], within: float | None = None) -> bool:
        within = self.rng if within is None else within
        return math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= within and self.b.clear_shot(me, e, self.here)

    def stop(self, me: dict[str, Any]) -> bool:
        """Stop walking: it is in reach, gone (another player took it), about to die to someone
        else, or another one is in reach on the way (the farm loop takes that one - walking on
        past it wasted the trip). Shooting on the way (shoots_moving), that one is shot without
        stopping and the walk goes on. The picked one in reach ends the walk even when it was just
        shot: walking on gains nothing, since the game holds a character still for about 0.7 s
        after each cast (keys held, measured in Scorpion Cave 2026-10-30), and a shot the game
        dropped had the walk go on past it for a second (live: fewer kills, not more)."""
        b = self.b
        snap = b.snap()
        m = self.find(snap)
        moving = b.shoots_moving()
        if moving:
            b.move_shot(snap, me)
        if m is None or m["hp"] <= 0 or b.downed(self.mid, self.began) or self.reachable(me, m, self.engage):
            return True
        b.watch_hp(snap)
        if b.doomed(m, me):
            self.doomed.append(m)
            return True
        if not moving and b.leap_ready(me, m, self.rng):
            self.leap = True      # (a leap whose monster is about to die to someone else: no)
            return True
        if self.pull and self._closing_in(me, m):
            return True
        if self._attacked_by_a_nearer_one(snap, me, m):
            return True
        if b.farm_filter is not None and not moving:
            for e in b.targets(snap, *b.farm_filter, limit=self.rng + 2):
                if e["id"] != self.mid and self.reachable(me, e):
                    self.other = e["id"]
                    return True
        return False

    def _attacked_by_a_nearer_one(self, snap: dict[str, Any], me: dict[str, Any], m: dict[str, Any]) -> bool:
        """Another monster is attacking us (or our pet), nearer than the one we walk to, and the
        farm would fight it: stop and fight that one first. The fight's walk doesn't stop for hits
        (in_fight), and the "another one in reach" rule below counts only monsters strictly in reach
        with a clear shot - a monster chasing us trails a step behind, just outside a melee reach.
        Live 2026-11-17/18 (packets + field logs): a Warrior walked on 2-33 s while one hit it from
        0.6-5 tiles, to reach one 20-55 tiles off, 40 times a night; a Mage 257 times; a Hunter in
        Thunder Cave 110 times - players saw it leave the monsters round it for far ones."""
        b = self.b
        if self.attacker or not b.p.get("fight_back", True):
            return False
        hitting = b.attackers() - {self.mid}
        if not hitting:
            return False
        ours = math.hypot(m["x"] - me["x"], m["y"] - me["y"])
        near = [e for e in b.targets(snap) if e["id"] in hitting
                and math.hypot(e["x"] - me["x"], e["y"] - me["y"]) < ours]
        if not near:
            return False
        e = min(near, key=lambda e: math.hypot(e["x"] - me["x"], e["y"] - me["y"]))
        self.other, self.attacker = e["id"], True
        return True

    def beside(self, snap: dict[str, Any], me: dict[str, Any]) -> dict[str, Any] | None:
        """The other monster the walk stopped for (stop), as this look has it: alive, on this map
        and still in reach with a clear shot (one attacking us need only be alive and here) - else
        None."""
        if self.other is None or self.left_map(snap):
            return None
        e = next((e for e in snap["entities"] if e["id"] == self.other), None)
        if e is None or e.get("hp", 0) <= 0 or not (self.attacker or self.reachable(me, e)):
            return None
        return e

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
