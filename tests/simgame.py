"""A pretend Mist World for long runs: a small server that speaks the agent's protocol.

The bot connects to it exactly as to a real game - AgentLink finds its port file, authenticates,
sends one line and reads one line back (link.py, StateServer.java) - so the whole run is real:
the scan, the setup, the script, the fights, the end. Behind the protocol is a small world:

  - one character on a real map (atlas), walking with the keys the bot holds, against the map's
    walls (MapModel.ok), at a walking speed;
  - monsters that spawn in the map's own monster zones, wander, fight back when we are close,
    die, and drop loot into the bag;
  - our skills on the attack keys, with their cooldowns and the global cooldown, reported as the
    agent reports them (`casts`: who cast what, whom it hit, which monsters died);
  - HP and MP, potions on hotkeys, the character sheet (`ask sheet`) with contribution, experience,
    bag load and drop time.

Time is the run's own clock (SimClock): waiting moves it on at once, so a night's farming runs in
minutes. Every request also moves it on a little, as a real round trip does, so a loop that only
polls the clock still gets somewhere.

`truth` counts what really happened (kills we made, loot, contribution, deaths), to hold the
bot's results against. `faults` injects trouble: drop the connection, delay a reply, garble a
line, kill the character, fill the bag. `events` are the game's occasional surprises, grown from
problems seen in real runs (see test_soak.py)."""

from __future__ import annotations

import json
import math
import os
import random
import secrets
import socket
import threading
from typing import Any

from unclefarmer import atlas, gamekeys, settings

ME = 4242
NAME = "Simchar"
SPEED = 4.0             # tiles a second, walking
ROUND_TRIP = 0.0003     # seconds of game time one request takes
SHEET_DELAY = 0.3       # the server answers the sheet request this much later
GCD = 1.2
SKILLS = [               # (name, cooldown s, damage, reach in tiles)
    ("Heavy Strike", 0.0, 2600, 2.5),
    ("Rending Blow", 6.0, 5200, 2.5),
    ("Crushing Leap", 12.0, 8800, 6.0),
    ("Whirling Cut", 9.0, 4100, 3.0),
]
ATTACK_KEYS = gamekeys.attack_key_list()   # the game's attack keys without an input.ini: what the scan reads
PK_KEY, TARGET_KEY = settings.PROFILE_DEFAULTS["pk_key"], settings.PROFILE_DEFAULTS["target_mode_key"]
MOVE = {"w": (0, -1), "s": (0, 1), "a": (-1, 0), "d": (1, 0)}
def tile(v: float) -> int:
    """The tile a position is on: the nearest whole number, as the game rounds (the agent's sight
    check takes (raw + 500) / 1000). Not int(): at x 85.8 the character stands on tile 86."""
    return int(math.floor(v + 0.5))


LOOT = ["Level 1 Garnet Shard", "Level 1 Opal Shard", "Wolf Fur", "Arnebia"]
HOTKEYS = {"1": "Large Red Potion", "2": "Large Blue Potion"}     # on the hotkey bar from the start
RESPAWN_ROWS = ["Please select your respawn method", "Map Respawn", "Respawn back to the City"]


class SimClock:
    """The run's clock: game time that only moves when someone waits (or asks the game)."""

    def __init__(self, start: float):
        self._now = start
        self._lock = threading.Lock()

    def time(self) -> float:
        with self._lock:
            return self._now

    def advance(self, seconds: float) -> None:
        if seconds > 0:
            with self._lock:
                self._now += seconds

    def sleep(self, seconds: float) -> None:
        self.advance(seconds)

    def wait(self, event: Any, timeout: float | None = None) -> bool:
        if event.is_set():
            return True
        self.advance(timeout or 0.0)
        return bool(event.is_set())


class Monster:
    def __init__(self, mid: int, x: float, y: float, zone: tuple[float, float, float, float], level: int):
        self.id = mid
        self.x, self.y = x, y
        self.zone = zone
        self.maxhp = self.hp = 30000 + 400 * level
        self.name = "Sim Wolf"
        self.hit_at = 0.0          # when it last hit us
        self.died_at: float | None = None
        self.hit_by_us = False
        self.critter = False       # takes no damage and never attacks (a town's ducks and hens)


class World:
    """The game state behind the protocol. Every method runs under the server's lock."""

    def __init__(self, map_id: int, clock: SimClock, seed: int = 7, monsters: int = 12):
        self.clock = clock
        self.rnd = random.Random(seed)
        self.map_id = map_id
        self.model = atlas.model(map_id)
        self.zones = [tuple(z["rect"]) for z in atlas.zones(map_id, ("monster",))] or [
            (1, 1, self.model.w, self.model.h)]
        self.x, self.y = self._start_cell()
        self.maxhp, self.maxmp = 60000, 40000
        self.hp, self.mp = self.maxhp, self.maxmp
        self.level = 60
        self.contribution = 100000
        self.xp_needed = 900000
        self.drop_seconds = 7 * 3600
        self.load, self.maxload = 1200, 4000
        self.bag: dict[str, int] = {"Large Red Potion": 400, "Large Blue Potion": 400, "Arnebia": 5000}
        self.types: dict[str, int] = {}          # item name -> its type id (hotkeys point at types)
        self.held: dict[int, set[str]] = {}      # connection -> keys it holds
        self.target: int | None = None
        self.monsters: dict[int, Monster] = {}
        self.want_monsters = monsters
        self._next_id = -100
        self.casts: list[dict[str, Any]] = []
        self.deaths: list[list[int]] = []
        self.seq = 0
        self.cooldown: dict[str, float] = {}
        self.gcd_end = 0.0
        self.last = clock.time()
        self.sheet_seen: float | None = None
        self.sheet: dict[str, Any] | None = None     # the last sheet that arrived, as it was then
        self.sheet_due: float | None = None          # an asked-for sheet on its way
        self.vitals_seen: float | None = None
        self.dead = False
        self.dialog: dict[str, Any] | None = None     # an open window: {"kind", "rows"}
        self.pk_mode = 0
        self.target_mode = 0        # "all": the bot sets it to "enemies" with the v key
        self.follow: int | None = None    # the follow attack: walking after this target
        self.truth = {"kills": 0, "loot": 0, "contribution": 0, "deaths": 0, "potions": 0, "casts": 0}
        self.events = Events()
        self.chat_open = False      # the game's chat line has the keys (input mode 5)
        self.busy_until = 0.0
        self.frozen = False
        for _i in range(monsters):
            self._spawn()

    # ------------------------------------------------------------------ the world moving on
    def _start_cell(self) -> tuple[float, float]:
        z = self.zones[0]
        cx, cy = int((z[0] + z[2]) / 2), int((z[1] + z[3]) / 2)
        c = self.model.nearest_walkable(cx, cy, 60) or self.model.nearest_walkable(self.model.w // 2,
                                                                                    self.model.h // 2, 400)
        if c is None:
            raise RuntimeError("the pretend game's map has no walkable cell")
        return float(c[0]), float(c[1])

    def _spawn(self, near: bool = False, critter: bool = False) -> None:
        for _try in range(40):
            z = self.rnd.choice(self.zones)
            if near:
                x, y = self.x + self.rnd.uniform(-5, 5), self.y + self.rnd.uniform(-5, 5)
            else:
                x, y = self.rnd.uniform(z[0], z[2]), self.rnd.uniform(z[1], z[3])
            if self.model.ok(tile(x), tile(y)):
                mid = self._free_id()
                m = Monster(mid, x, y, z, self.rnd.randint(50, 62))
                if critter:
                    m.name, m.critter = "Sim Duck", True
                self.monsters[mid] = m
                return

    def _free_id(self) -> int:
        """A monster id the way the game hands them out: from a small pool, reused once its last
        holder is gone (a respawned monster comes back under an old id), new only when every one
        is taken. So whatever the bot keeps per monster id stays as small as in a real night."""
        for mid in range(-101, self._next_id - 1, -1):
            if mid not in self.monsters:
                return mid
        self._next_id -= 1
        return self._next_id

    def ask_sheet(self) -> None:
        """The server answers `ask sheet` about 0.3 s later, with the sheet as it is then."""
        if self.sheet_due is None:
            self.sheet_due = self.clock.time() + SHEET_DELAY

    def _sheet_arrives(self, now: float) -> None:
        if self.sheet_due is None or now < self.sheet_due:
            return
        self.sheet_due = None
        self.sheet_seen = now
        self.sheet = {"level": self.level, "mp": int(self.mp), "maxmp": self.maxmp, "hp": int(self.hp),
                      "maxhp": self.maxhp, "load": self.load, "maxload": self.maxload, "fusion": 99.0,
                      "drop_seconds": int(self.drop_seconds), "counter": 0, "counter_name": "",
                      "contribution": self.contribution, "xp_needed": self.xp_needed, "merit": 0}

    def update(self) -> None:
        now = self.clock.time()
        self._sheet_arrives(now)
        dt = min(5.0, now - self.last)
        self.last = now
        if dt <= 0:
            return
        self._walk(dt)
        for m in list(self.monsters.values()):
            if m.died_at is not None:
                if now - m.died_at > 3.0:
                    del self.monsters[m.id]
                continue
            self._monster(m, now, dt)
        self._events(now, dt)
        alive = sum(1 for m in self.monsters.values() if m.died_at is None and not m.critter)
        if alive < self.want_monsters and self.rnd.random() < dt / 4.0:
            self._spawn()
        self.drop_seconds = max(0.0, self.drop_seconds - dt)
        if not self.dead:
            self.hp = min(self.maxhp, self.hp + 120 * dt)
            self.mp = min(self.maxmp, self.mp + 60 * dt)

    def _events(self, now: float, dt: float) -> None:
        ev = self.events
        for name in ("critter", "popup", "chatbox", "busy", "freeze", "pack"):
            chance = getattr(ev, name)
            if not chance or self.rnd.random() >= chance * dt / 60.0:
                continue
            ev.happened[name] = ev.happened.get(name, 0) + 1
            if name == "critter":
                self._spawn(near=True, critter=True)
            elif name == "popup" and self.dialog is None and not self.dead:
                self.open_window("popup", ["Sim Friend invites you to join the team Sim Team", "Accept", "Decline"])
            elif name == "chatbox":
                self.chat_open = True
            elif name == "busy":
                self.busy_until = now + self.rnd.uniform(0.5, 3.0)
            elif name == "freeze":
                self.frozen = True
            elif name == "pack":
                for _i in range(4):
                    self._spawn(near=True)

    def _walk(self, dt: float) -> None:
        if self.dead:
            return
        keys: set[str] = (set[str]().union(*self.held.values()) if self.held and self.dialog is None
                          and not self.chat_open else set())
        dx = sum(MOVE[k][0] for k in keys if k in MOVE)
        dy = sum(MOVE[k][1] for k in keys if k in MOVE)
        if dx or dy:
            self.follow = None        # walking keys take over from the follow attack
        elif self.follow is not None:
            self._follow(dt)
            return
        if not dx and not dy:
            return
        step = SPEED * dt
        nx, ny = self.x + dx * step, self.y + dy * step
        if self.model.ok(tile(nx), tile(self.y)):
            self.x = nx
        if self.model.ok(tile(self.x), tile(ny)):
            self.y = ny

    def _follow(self, dt: float) -> None:
        """The game's follow attack: it walks us at the target until it is in reach."""
        t = self.monsters.get(self.follow) if self.follow is not None else None
        if t is None or t.died_at is not None:
            self.follow = None
            return
        d = math.hypot(t.x - self.x, t.y - self.y)
        if d <= 1.5:
            self.follow = None
            return
        step = min(d - 1.2, SPEED * dt)
        nx, ny = self.x + (t.x - self.x) / d * step, self.y + (t.y - self.y) / d * step
        if self.model.ok(tile(nx), tile(ny)):
            self.x, self.y = nx, ny
        else:
            self.follow = None        # a wall in the way: the follow attack gives up

    def _monster(self, m: Monster, now: float, dt: float) -> None:
        d = math.hypot(m.x - self.x, m.y - self.y)
        if d < 6 and not self.dead and not m.critter:
            if d > 1.2:          # charge
                step = min(d - 1.0, 3.0 * dt)
                nx, ny = m.x + (self.x - m.x) / d * step, m.y + (self.y - m.y) / d * step
                if self.model.ok(tile(nx), tile(ny)):
                    m.x, m.y = nx, ny
            elif now - m.hit_at >= 2.0:
                m.hit_at = now
                amount = int(900 * self.rnd.uniform(0.7, 1.3))
                self.seq += 1
                self.casts.append({"t": int(now * 1000), "caster": m.id, "seq": self.seq,
                                   "skill": "Level 1 Bite", "hits": [[ME, 3, amount]]})
                self._hurt(amount)
        elif self.rnd.random() < dt / 3.0:
            nx = min(max(m.x + self.rnd.uniform(-2, 2), m.zone[0]), m.zone[2])
            ny = min(max(m.y + self.rnd.uniform(-2, 2), m.zone[1]), m.zone[3])
            if self.model.ok(tile(nx), tile(ny)):
                m.x, m.y = nx, ny

    def _hurt(self, amount: float) -> None:
        if self.dead:
            return
        self.hp -= amount
        if self.hp <= 0:
            self.die()

    def die(self) -> None:
        """Our character falls: HP 0 and the respawn window, as the game opens it."""
        self.hp = 0
        self.dead = True
        self.truth["deaths"] += 1
        self.held = {c: set() for c in self.held}
        self.follow = None
        self.target = None
        self.open_window("respawn", RESPAWN_ROWS, row=1)

    def open_window(self, kind: str, rows: list[str], row: int = 0) -> None:
        """A game window with one entry a row; the cursor keys move it, Enter picks, Esc closes."""
        self.dialog = {"kind": kind, "rows": [[t] for t in rows], "row": row, "col": 0}

    def respawn(self) -> None:
        self.dead = False
        self.hp, self.mp = self.maxhp, self.maxmp
        self.x, self.y = self._start_cell()
        self.dialog = None

    # ------------------------------------------------------------------ what the bot does
    def press(self, key: str) -> None:
        now = self.clock.time()
        if self.chat_open:
            return                    # the chat line has the keys: every press is lost
        if self.dialog is not None:
            self.window_key(key)
            return
        if self.dead:
            return
        if key == PK_KEY:
            self.pk_mode = (self.pk_mode + 1) % len(settings.PK_MODES)
        elif key == TARGET_KEY:
            self.target_mode = (self.target_mode + 1) % len(settings.TARGET_MODES)
        elif key in ATTACK_KEYS:
            self._cast(ATTACK_KEYS.index(key), now)
        elif key in ("1", "2", "3", "4", "5", "6", "7", "8", "9", "0"):
            self._hotkey(key)

    def window_key(self, key: str) -> None:
        """An open window takes the keys: the movement keys move its cursor (no walking)."""
        d = self.dialog
        assert d is not None
        if key == "w":
            d["row"] = max(0, d["row"] - 1)
        elif key == "s":
            d["row"] = min(len(d["rows"]) - 1, d["row"] + 1)
        elif key == "esc":
            if d["kind"] != "respawn":     # the respawn window can't be closed, only answered
                self.dialog = None
                self.truth["windows_closed"] = self.truth.get("windows_closed", 0) + 1
        elif key == "enter":
            picked = d["rows"][d["row"]][0]
            if d["kind"] == "respawn" and "Respawn" in picked and "select" not in picked:
                self.respawn()

    def _cast(self, slot: int, now: float) -> None:
        if slot >= len(SKILLS):
            return
        name, cd, dmg, reach = SKILLS[slot]
        if now < self.gcd_end or now < self.cooldown.get(name, 0):
            return
        t = self.monsters.get(self.target) if self.target is not None else None
        if t is None or t.died_at is not None:
            return            # no target: the game refuses the cast, nothing is reported
        if math.hypot(t.x - self.x, t.y - self.y) > reach + 0.5:
            self.follow = t.id    # out of reach: the follow attack walks us there, no cast yet
            return
        self.gcd_end = now + GCD
        self.cooldown[name] = now + cd
        self.seq += 1
        amount = 0 if t.critter else int(dmg * self.rnd.uniform(0.8, 1.2))
        crit = not t.critter and self.rnd.random() < 0.1
        if crit:
            amount *= 2
        t.hp -= amount
        t.hit_by_us = True
        if t.critter:             # the server takes the cast and does nothing: no damage
            self.casts.append({"t": int((now + 0.2) * 1000), "caster": ME, "seq": self.seq,
                               "skill": "Level 5 " + name, "hits": [[t.id, 2, 0]]})
            return
        at = int((now + 0.2) * 1000)
        self.casts.append({"t": at, "caster": ME, "seq": self.seq, "skill": "Level 5 " + name,
                           "hits": [[t.id, 5 if crit else 3, amount]]})
        self.truth["casts"] += 1
        if t.hp <= 0:
            t.hp = 0
            t.died_at = now
            self.deaths.append([t.id, at])
            self.truth["kills"] += 1
            self.contribution += 5
            self.truth["contribution"] += 5
            self.xp_needed = max(0, self.xp_needed - 3000)
            if self.rnd.random() < 0.6:
                item = self.rnd.choice(LOOT)
                n = 100 if item == "Arnebia" else 1
                self.bag[item] = self.bag.get(item, 0) + n
                self.truth["loot"] += 1
                self.load = min(self.maxload, self.load + 1)
            if self.target == t.id:
                self.target = None
        self.casts = self.casts[-256:]
        self.deaths = self.deaths[-256:]

    def _hotkey(self, key: str) -> None:
        item = HOTKEYS.get(key)
        if item and self.bag.get(item, 0) > 0:
            self.bag[item] -= 1
            self.truth["potions"] += 1
            if "Red" in item:
                self.hp = min(self.maxhp, self.hp + 20000)
            else:
                self.mp = min(self.maxmp, self.mp + 15000)

    def select(self, mid: int) -> str:
        m = self.monsters.get(mid)
        if m is None or m.died_at is not None:
            return "error: %d isn't in sight" % mid
        if math.hypot(m.x - self.x, m.y - self.y) > 14:
            return "refused: %d is out of the target keys' reach" % mid
        self.target = mid
        return "selected"

    # ------------------------------------------------------------------ what the bot reads
    def entity(self, m: Monster) -> dict[str, Any]:
        return {"id": m.id, "kind": "monster", "side": -2, "name": m.name, "x": round(m.x, 3),
                "y": round(m.y, 3), "type": 0, "facing": 0, "hp": int(m.hp), "maxhp": m.maxhp}

    def me(self) -> dict[str, Any]:
        now = self.clock.time()
        me: dict[str, Any] = {"id": ME, "kind": "player", "side": 1, "name": NAME, "x": round(self.x, 3),
                              "y": round(self.y, 3), "type": 0, "facing": 0, "hp": int(self.hp),
                              "maxhp": self.maxhp}
        if self.sheet is not None and self.sheet_seen is not None:
            me["sheet"] = dict(self.sheet, age=int(now - self.sheet_seen))
        if self.vitals_seen is not None:
            me["vitals"] = {"hp": int(self.hp), "mp": int(self.mp), "buffs": 0, "debuffs": 0,
                            "age_ms": int(1000 * (now - self.vitals_seen))}
        return me

    def entities(self) -> list[dict[str, Any]]:
        return [self.entity(m) for m in self.monsters.values()
                if math.hypot(m.x - self.x, m.y - self.y) < 40]

    def state(self) -> dict[str, Any]:
        return {"ok": True, "map": {"id": self.map_id, "name": self.model.name}, "self": self.me(),
                "entities": self.entities()}

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": k, "count": v} for k, v in self.bag.items() if v > 0]

    def type_of(self, name: str) -> int:
        return self.types.setdefault(name, 5000 + len(self.types))

    def bag_full_rows(self) -> list[dict[str, Any]]:
        """`mem bagfull`: each stack with its uid and item type (what hotkeys point at)."""
        return [{"uid": 900000 + self.type_of(k), "type": self.type_of(k), "name": k, "count": v, "flag": 0}
                for k, v in self.bag.items() if v > 0]

    def slots(self) -> dict[str, list[int]]:
        """`mem slots`: hotkey slot -> [item type, bag uid], for the potions on the bar."""
        return {k: [self.type_of(n), 900000 + self.type_of(n)] for k, n in HOTKEYS.items() if self.bag.get(n)}


class Events:
    """The game's own surprises, each a chance per game minute (0 = never). Every one is a problem
    a real night ran into; test_soak.py says which. A new long-run bug becomes a new event here."""

    def __init__(self):
        self.critter = 0.0        # a monster nobody can damage turns up (town ducks and hens)
        self.popup = 0.0          # a window the bot didn't open: a team invite
        self.chatbox = 0.0        # the game's chat line opens and swallows every key
        self.busy = 0.0           # the game is busy for a moment (a map loading): readings fail
        self.freeze = 0.0         # the game runs out of memory and freezes (agent 36 `health`)
        self.pack = 0.0           # several monsters at once on us
        self.happened: dict[str, int] = {}


class Faults:
    """Trouble on demand, each a chance per request (0 = never). The server applies them."""

    def __init__(self, seed: int = 11):
        self.rnd = random.Random(seed)
        self.drop = 0.0           # close the connection instead of answering
        self.delay = 0.0          # the game stalls: game time jumps 1-4 s before the answer
        self.garble = 0.0         # an answer that isn't JSON
        self.kill = 0.0           # our character dies
        self.fill_bag = 0.0       # the bag is suddenly full
        self.done: dict[str, int] = {}

    def roll(self, name: str) -> bool:
        chance = getattr(self, name)
        if chance and self.rnd.random() < chance:
            self.done[name] = self.done.get(name, 0) + 1
            return True
        return False


class SimGame:
    """The server: one port file in run_dir, one thread per connection, one lock for the world."""

    def __init__(self, run_dir: str, map_id: int = 1503, clock: SimClock | None = None, seed: int = 7,
                 monsters: int = 12):
        import time
        self.clock = clock or SimClock(time.time())
        self.world = World(map_id, self.clock, seed=seed, monsters=monsters)
        self.faults = Faults(seed + 4)
        self.lock = threading.RLock()
        self.secret = secrets.token_hex(8)
        self.run_dir = run_dir
        self.unknown: dict[str, int] = {}          # commands this simulator can't answer yet
        self.requests: dict[str, int] = {}
        self.connections: list[socket.socket] = []
        self.errors: list[str] = []
        self.closed_holding: list[list[str]] = []    # keys a connection still held when it closed
        self.threads: list[threading.Thread] = []     # one per connection
        self._next_conn = 0
        self.server = socket.create_server(("127.0.0.1", 0))
        self.port = self.server.getsockname()[1]
        os.makedirs(run_dir, exist_ok=True)
        self.port_file = os.path.join(run_dir, "agent.%d.port" % os.getpid())
        with open(self.port_file, "w", encoding="utf-8") as f:
            f.write("%d %d %s" % (self.port, os.getpid(), self.secret))
        self._stop = threading.Event()
        self.at: list[tuple[float, Any]] = []      # (game time, callback): measurements during a run
        self.thread = threading.Thread(target=self._accept, daemon=True, name="simgame-accept")
        self.thread.start()

    def close(self) -> None:
        self._stop.set()
        try:
            self.server.close()
        except OSError:
            pass
        for c in list(self.connections):
            try:
                c.close()
            except OSError:
                pass
        try:
            os.remove(self.port_file)
        except OSError:
            pass
        # Joined, not just ended: since Python 3.13 a Thread keeps its OS thread handle until it is
        # joined (or freed), so a test holding finished games would read as a handle leak.
        for t in [self.thread] + self.threads:
            t.join(2.0)

    def held_keys(self) -> set[str]:
        with self.lock:
            return set[str]().union(*self.world.held.values()) if self.world.held else set()

    # ------------------------------------------------------------------ connections
    def _accept(self) -> None:
        while not self._stop.is_set():
            try:
                conn, _addr = self.server.accept()
            except OSError:
                return
            self._next_conn += 1
            self.connections.append(conn)
            t = threading.Thread(target=self._serve, args=(conn, self._next_conn), daemon=True,
                                 name="simgame-conn")
            self.threads.append(t)
            t.start()

    def _serve(self, conn: socket.socket, cid: int) -> None:
        f = conn.makefile("rb")
        line = "?"                         # the request being answered, for an error report
        try:
            first = f.readline().decode("utf-8").strip()
            if first != "auth " + self.secret:
                conn.sendall(b"refused: send 'auth <secret>' first\n")
                return
            conn.sendall(b"ok\n")
            with self.lock:
                self.world.held[cid] = set()
            for raw in f:
                line = raw.decode("utf-8").strip()
                reply = self._answer(line, cid)
                if reply is None:          # a dropped connection
                    return
                conn.sendall((reply + "\n").encode("utf-8"))
        except OSError:
            pass
        except Exception as e:             # a bug in the simulator: the test reports it
            self.errors.append("%s: %r" % (line, e))
        finally:
            try:
                f.close()            # the reader holds the socket open until it is closed too
            except OSError:
                pass
            with self.lock:
                left = self.world.held.pop(cid, None)    # the agent lets go of a closed connection's keys
                if left:
                    self.closed_holding.append(sorted(left))  # ... but the bot should have, itself
            try:
                conn.close()
            except OSError:
                pass
            if conn in self.connections:
                self.connections.remove(conn)

    def _answer(self, line: str, cid: int) -> str | None:
        self.clock.advance(ROUND_TRIP)
        while self.at and self.clock.time() >= self.at[0][0]:
            _t, call = self.at.pop(0)
            call()
        verb = line.split(" ", 1)[0]
        self.requests[verb] = self.requests.get(verb, 0) + 1
        with self.lock:
            w = self.world
            if self.faults.roll("drop"):
                return None
            if self.faults.roll("delay"):
                # the game hangs for seconds: time passes, but nothing in it moves meanwhile
                w.update()
                self.clock.advance(w.rnd.uniform(1.0, 4.0))
                w.last = self.clock.time()
            if self.faults.roll("kill"):
                w.die()
            if self.faults.roll("fill_bag"):
                w.load = w.maxload
            if not w.frozen:          # a frozen game: nothing moves any more
                w.update()
            reply = self._handle(line, cid, w)
            if self.faults.roll("garble"):
                return reply[: max(1, len(reply) // 2)] + "\x00garbled"
            return reply

    # ------------------------------------------------------------------ the commands
    # Fixed answers: what the agent says to these whatever the game is doing.
    PLAIN = {"version": "mwagent 43", "arm": "armed", "keys": "ready (sim)", "phrase": "set"}

    def _handle(self, line: str, cid: int, w: World) -> str:
        parts = line.split()
        verb = parts[0] if parts else ""
        arg = parts[1] if len(parts) > 1 else ""
        if verb in self.PLAIN:
            return self.PLAIN[verb]
        do = getattr(self, "_cmd_" + verb, None)
        if do is None:
            self.unknown[line[:40]] = self.unknown.get(line[:40], 0) + 1
            return json.dumps({"ok": False, "error": "unknown command (simulator): " + verb})
        return do(arg, cid, w)

    def _ms(self) -> int:
        return int(self.clock.time() * 1000)

    def _cmd_guardmods(self, arg: str, cid: int, w: World) -> str:
        return "off" if arg == "off" else "on"

    def _cmd_speech(self, arg: str, cid: int, w: World) -> str:
        return "on" if arg in ("", "on") else "off"

    def _cmd_health(self, arg: str, cid: int, w: World) -> str:
        return json.dumps({"ok": True, "oom": w.frozen, "oom_where": "Timer-1" if w.frozen else "", "threads": 40})

    def _cmd_mem(self, arg: str, cid: int, w: World) -> str:
        if self.clock.time() < w.busy_until and arg in ("", "state", "self", "map"):
            return json.dumps({"ok": False, "error": "busy: the map is loading"})
        return self._mem(arg, w)

    def _cmd_casts(self, arg: str, cid: int, w: World) -> str:
        since = int(arg or 0)
        return json.dumps({"now": self._ms(), "casts": [c for c in w.casts if c["t"] > since],
                           "deaths": [d for d in w.deaths if d[1] > since]})

    def _cmd_key(self, arg: str, cid: int, w: World) -> str:
        w.press(arg)
        return "tapped %s" % arg

    def _cmd_keydown(self, arg: str, cid: int, w: World) -> str:
        if w.dialog is not None:      # a window has the keys: held, but it doesn't walk
            w.window_key(arg)
        w.held.setdefault(cid, set()).add(arg)
        return "down %s" % arg

    def _cmd_keyup(self, arg: str, cid: int, w: World) -> str:
        w.held.setdefault(cid, set()).discard(arg)
        return "up %s" % arg

    def _cmd_keysup(self, arg: str, cid: int, w: World) -> str:
        n = len(w.held.get(cid, set()))
        w.held[cid] = set()
        return "released %d" % n

    def _cmd_select(self, arg: str, cid: int, w: World) -> str:
        return w.select(int(arg))

    def _cmd_ask(self, arg: str, cid: int, w: World) -> str:
        if arg == "vitals":
            w.vitals_seen = self.clock.time()
        elif arg == "sheet":
            w.ask_sheet()
        elif arg != "petvitals":
            return "error: ask vitals|petvitals|sheet"
        return "asked"

    def _cmd_dialog(self, arg: str, cid: int, w: World) -> str:
        if w.dialog is None:
            return json.dumps({"ok": True, "open": 0})
        rows = [[{"text": t} for t in row] for row in w.dialog["rows"]]
        return json.dumps({"ok": True, "open": 1, "top": {"rows": rows, "row": w.dialog["row"], "col": 0}})

    def _cmd_input(self, arg: str, cid: int, w: World) -> str:
        return json.dumps({"ok": True, "open": w.chat_open, "mode": 5 if w.chat_open else 0})

    def _cmd_said(self, arg: str, cid: int, w: World) -> str:
        return json.dumps({"now": self._ms(), "said": []})

    def _cmd_team(self, arg: str, cid: int, w: World) -> str:
        return json.dumps({"now": self._ms(), "members": []})

    def _cmd_wear(self, arg: str, cid: int, w: World) -> str:
        return json.dumps({"now": self._ms(), "wear": []})

    def _cmd_typeesc(self, arg: str, cid: int, w: World) -> str:
        if w.chat_open:
            w.chat_open = False
            w.truth["chat_closed"] = w.truth.get("chat_closed", 0) + 1
        return "escaped"

    def _cmd_pms(self, arg: str, cid: int, w: World) -> str:
        return json.dumps({"last": 0, "pms": []})

    def _mem(self, which: str, w: World) -> str:
        j = json.dumps
        if which in ("", "state"):
            s = w.state()
            if which == "":
                s.update(bag=w.bag_rows(), slots=w.slots(), modes=self._modes(w), ui=self._ui(w))
            return j(s)
        if which == "self":
            return j(w.me())
        if which == "map":
            return j({"id": w.map_id, "name": w.model.name})
        if which == "entities":
            return j(w.entities())
        if which == "bag":
            return j(w.bag_rows())
        if which == "slots":
            return j(w.slots())
        if which == "modes":
            return j(self._modes(w))
        if which == "ui":
            return j(self._ui(w))
        if which == "skills":
            return j({"combat": [{"id": i + 1, "name": s[0]} for i, s in enumerate(SKILLS)],
                      "attack_keys": {str(i + 1): i + 1 for i in range(len(SKILLS))}, "talents": []})
        if which == "bagfull":
            return j(w.bag_full_rows())
        if which in ("beasts", "equipment", "pets"):
            return j([])
        self.unknown["mem " + which] = self.unknown.get("mem " + which, 0) + 1
        return j({"ok": False, "error": "unknown section: " + which})

    @staticmethod
    def _modes(w: World) -> dict[str, Any]:
        return {"target": w.target_mode, "pk": w.pk_mode, "pk_locked": False, "options": 0,
                "swap_attack": False, "auto_lock_target": False, "skip_enemy_corpses": False}

    @staticmethod
    def _ui(w: World) -> dict[str, Any]:
        return {"panel": 0, "category": 0, "cursor": 0, "target": w.target, "dialogs": 1 if w.dialog else 0,
                "attack_page": 0}


# ---------------------------------------------------------------------- the soak harness
class Soak:
    """One run of the real bot (engine.runner.Run, the script given) against a SimGame, with a
    real-time watchdog: a run that hasn't ended after `watchdog` seconds is stopped and counted
    as hung. Measurements: `mark(minutes, callback)` calls back at that game minute."""

    def __init__(self, game: SimGame, script: str, watchdog: float = 300.0):
        from unclefarmer.engine.runner import Run
        self.game = game
        import collections
        # the last lines, and every line worth asserting on; not all of a night's lines, or the
        # harness itself would be the memory that grows
        self.lines: Any = collections.deque(maxlen=2000)
        self.flagged: list[str] = []
        self.reasons: list[str] = []
        self.timed_out = False
        self.run = Run(script, "soak", os.getpid(), on_log=lambda line, speak, r: self._log(line),
                       on_done=lambda why, r: self.reasons.append(why), clock=game.clock)
        self.watchdog = watchdog
        self.wall = 0.0

    FLAG = ("stuck", "unexpected error", "couldn't", "lost the connection", "Traceback")

    def _log(self, line: str) -> None:
        self.lines.append(line)
        if any(f in line for f in self.FLAG):
            self.flagged.append(line)

    def mark(self, minutes: float, call: Any) -> None:
        self.game.at.append((self.run.started + 60 * minutes, call))
        self.game.at.sort(key=lambda a: a[0])

    def go(self) -> Soak:
        import time

        def hung():
            self.timed_out = True
            self.run.stop()
        timer = threading.Timer(self.watchdog, hung)
        timer.daemon = True
        timer.start()
        t0 = time.perf_counter()
        try:
            self.run.run()            # in this thread: what the Run thread does, start to end
        finally:
            timer.cancel()
            self.wall = time.perf_counter() - t0
        return self

    @property
    def reason(self) -> str:
        return self.reasons[-1] if self.reasons else ""

    def said(self, text: str) -> list[str]:
        """Lines with text in them: every one for a FLAG word, else among the last 2000."""
        pool = self.flagged if any(f in text for f in self.FLAG) else self.lines
        return [line for line in pool if text in line]

    def results(self) -> dict[str, Any]:
        return self.run.tally.result() if self.run.tally else {}


def handles() -> int:
    """This process's open handles (Windows) or file descriptors: what a leak would grow."""
    if os.name == "nt":
        import ctypes
        from ctypes import wintypes
        k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        k32.GetCurrentProcess.restype = wintypes.HANDLE
        k32.GetProcessHandleCount.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        k32.GetProcessHandleCount.restype = wintypes.BOOL
        n = wintypes.DWORD()
        if not k32.GetProcessHandleCount(k32.GetCurrentProcess(), ctypes.byref(n)):
            raise OSError(ctypes.get_last_error(), "GetProcessHandleCount failed")
        return int(n.value)
    return len(os.listdir("/proc/self/fd"))
