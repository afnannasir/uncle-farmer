"""Which monsters we killed, from the game's own reports (agent v30 `casts`).

A monster is ours when it dies after one of our hits landed on it, whoever struck last: the game
shares a kill among everyone who hurt the monster, so one killed by two players counts for both.
The area skills' kills count too, though the bot never selected those monsters, and so do our
pet's: the game's own count is the player's and the pet's together (Scorpion Cave 2026-10-27: the
game said 298, the pet struck the last blow on 22, and the bot without them said 286). Counting only the
selected target's death, the bot was 5-7% short of the game over a night (Oct 14-15 packets: 166,
342 and 235 kills an hour against 157, 325 and 226 counted).

Sources: our casts with their hit results (target, result, amount; result & 7 == 2 is a dodge,
which does no harm) and the deaths the game reports as "id,0" lines, both read from the agent.
From agent 44 every cast also names its targets with their HP after it, other players' too: who
else is on a monster, and how fast it goes down (fighting.doomed).
"""

KEEP = 60.0         # seconds one of our hits keeps a monster ours (a fight is far shorter)
DODGED = 2
STRUCK_KEEP = 10.0  # seconds someone else's strikes on a monster are remembered (agent 44 "targets")


class KillTracker:
    def __init__(self, link):
        self.a = link
        self.since = 0          # agent clock (ms) of the newest cast or death read
        self.hit = {}           # monster id -> agent ms of our last hit on it
        self.live = None        # None until read; False: the agent reports no deaths (before v30)
        self.seen = set()       # (kind, id, ms) read at the newest millisecond, so a reread skips them
        self.fresh = []         # casts read for the first time by the last update (all casters)
        self.died = []          # every monster death the last update read, ours or not (spawns.py)
        self.own = set()        # our pets' ids: their strikes are ours, not someone else's
        self.struck = {}        # monster id -> [(agent ms, HP after it)] of someone else's strikes
        self.pet_struck = {}    # monster id -> agent ms of our pet's last strike

    def update(self, me_id):
        """Read what happened since the last call: the ids of monsters that died with a hit of
        ours on them, each once. [] while the agent can't tell (self.live False). Someone else's
        strikes (agent 44) go to self.struck, our pets' to self.pet_struck."""
        self.fresh = []
        self.died = []
        # from one millisecond back: a cast or death noted in the same millisecond as the newest one
        # read, but just after that read, would otherwise be skipped for good
        r = self.a.json("casts %d" % (self.since - 1))
        if not isinstance(r, dict) or "error" in r:
            return []
        newest = self.since
        seen = set()
        for c in r.get("casts", []):
            newest = max(newest, c["t"])
            key = ("c", c.get("caster"), c.get("seq"), c["t"])
            seen.add(key)
            if key in self.seen:
                continue
            self.fresh.append(c)
            if c.get("caster") != me_id:
                self._note_struck(c)
                continue
            for h in c.get("hits", []):
                if len(h) >= 2 and h[0] < 0 and h[1] & 7 != DODGED:
                    self.hit[h[0]] = c["t"]
        if "deaths" not in r:
            self.live = False       # no deaths: before v30, but its casts still feed the stats
            self.since = newest
            self.seen = {k for k in seen if k[-1] == newest}
            return []
        self.live = True
        out = []
        for mid, t in r.get("deaths", []):
            newest = max(newest, t)
            seen.add(("d", mid, t))
            if ("d", mid, t) in self.seen:
                continue
            self.died.append(mid)
            self.struck.pop(mid, None)          # monster ids are reused: the next life starts clean
            pet = self.pet_struck.pop(mid, None)
            last = self.hit.pop(mid, None)
            if any(s is not None and t - s <= KEEP * 1000 for s in (last, pet)):
                out.append(mid)
        self.since = newest
        self.seen = {k for k in seen if k[-1] == newest}
        cutoff = newest - KEEP * 1000
        for mid in [m for m, t in self.hit.items() if t < cutoff]:
            del self.hit[mid]
        old = newest - STRUCK_KEEP * 1000
        for mid in [m for m, rows in self.struck.items() if rows[-1][0] < old]:
            del self.struck[mid]
        for mid in [m for m, t in self.pet_struck.items() if t < old]:
            del self.pet_struck[mid]
        return out

    def _note_struck(self, c):
        """A cast by someone else (or our pet) at monsters: who is on which one, and its HP after."""
        caster = c.get("caster")
        if not isinstance(caster, int) or caster < 0:
            return              # a monster's own cast
        for row in c.get("targets") or []:
            if len(row) < 2 or not isinstance(row[0], int) or row[0] >= 0:
                continue
            mid, hp = row[0], row[1]
            if caster in self.own:
                self.pet_struck[mid] = c["t"]
            elif isinstance(hp, (int, float)) and hp > 0:
                self.struck.setdefault(mid, []).append((c["t"], hp))

    def claimed(self, mid):
        """Someone else (another player or their pet) struck this monster lately."""
        return mid in self.struck

    def pets_alone(self, mid):
        """Only our pet has been at it lately: its HP going down is our own doing."""
        return mid in self.pet_struck and mid not in self.struck

    def struck_rate(self, mid):
        """HP a second it is losing to someone else, from their strikes' HP readings (two at
        least, half a second apart), or None. The server's own numbers, timed to the
        millisecond: known from the second strike on, where the world's readings need a while."""
        rows = self.struck.get(mid) or []
        if len(rows) < 2 or rows[-1][0] - rows[0][0] < 500:
            return None
        drop = rows[0][1] - rows[-1][1]
        return 1000.0 * drop / (rows[-1][0] - rows[0][0]) if drop > 0 else None

    def tagged(self, mid):
        """We have hurt this monster lately: it's partly ours, and finishing it gets the kill."""
        return mid in self.hit

    def hit_since(self, ms: int) -> "dict[int, int]":
        """The monsters our hits landed on from agent time ms on: {id: ms of the last hit}."""
        return {m: t for m, t in self.hit.items() if t >= ms}
