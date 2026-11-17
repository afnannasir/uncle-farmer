"""Watch the games for chests (and anything else that isn't an ordinary monster), and write down
what they look like to the agent, so the bot can be taught to open them.

    python chestwatch.py                              # the games started with this checkout's agent
    python chestwatch.py --uf "C:\\Games\\Uncle Farmer"  # the games started with a release's agent

Then farm as usual, by hand or with the bot, for as long as it takes a chest to show up; leave
this window open. Ctrl+C ends it and prints a summary.

It only reads (`mem` and `said`, never `arm`): no key, no walk, no packet out. It writes to
logs\\chestwatch-<day>.log, one line per thing seen:
  NAME      the first time a name is seen on a map, whatever it is: a chest with an
            unexpected name still shows up here once
  APPEAR    an object worth a look came into sight: no hp (drops, chests, doors), at most 1 max
            hp, or a name like a chest; with everything the agent says about it
  GONE      that object left sight: how long it stayed and how far from us it was last
  SAID      a line the game spoke (not chat): what pressing Enter on a chest answers
  NEWS      a game broadcast about a chest ("There's a Shining Small Chest in Margit Rainforest.
            Go find it...", channel 37); players' own chat is never read into the log
  BAG       what the bag gained: a chest's loot (11 or more shards at once, and a pile of arnebia)
  MAP       we changed map

The log carries the character's name and map, not chat; check it before sharing it.
"""

from __future__ import annotations

import argparse
import datetime as dt
import math
import os
import re
import sys
import threading
import time
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from unclefarmer import jsonio as json, link, paths  # noqa: E402

# Names that look like a chest, in the languages the game ships and the map files' Chinese.
CHEST = re.compile(r"chest|box|treasure|coffre|cofre|caja|ba[uú]l|宝箱|箱", re.I)
# The field maps chests are known to turn up on.
CHEST_MAPS = {5361, 5341, 5281, 5601, 5321, 5121, 5181, 5061, 5081, 5221, 5041, 5661, 5141,
              5261, 5301, 5201, 5841, 5241, 5761, 5801, 5721}

write_lock = threading.Lock()


def worth_a_look(e: dict[str, Any]) -> bool:
    """Not an ordinary monster, player or NPC: no hp, at most 1 max hp, or a chest's name. Nameless
    things are left out: skill effects come and go by the hundred (one hp, five seconds, no name)."""
    name = e.get("name") or ""
    if CHEST.search(name):
        return True
    if not name.strip() or e.get("kind") == "player":
        return False
    return "hp" not in e or e.get("maxhp", 0) <= 1


class Log:
    """The day's file, shared by every game's watcher."""

    def __init__(self) -> None:
        self.path = os.path.join(paths.LOGS, "chestwatch-%s.log" % dt.date.today().isoformat())
        self.counts: dict[str, int] = {}

    def line(self, game: str, what: str, text: str, data: Any = None) -> None:
        stamp = dt.datetime.now().strftime("%H:%M:%S")
        row = "%s %s %-7s %s" % (stamp, game, what, text)
        if data is not None:
            row += "  " + json.dumps(data, ensure_ascii=False)
        with write_lock:
            self.counts[what] = self.counts.get(what, 0) + 1
            with open(self.path, "a", encoding="utf-8") as f:
                f.write(row + "\n")
            if what != "NAME":
                print(row if len(row) < 200 else row[:197] + "...", flush=True)


class Watcher(threading.Thread):
    """One game: polls its agent until the game closes or we are told to stop."""

    def __init__(self, pid: int, run_dir: str, log: Log, every: float, stop: threading.Event) -> None:
        super().__init__(daemon=True, name="chestwatch-%d" % pid)
        self.pid, self.run_dir, self.log, self.every, self.stop = pid, run_dir, log, every, stop
        self.tag = "[%d]" % pid
        self.map_id: Any = None
        self.names: set[tuple[Any, str, str]] = set()
        self.seen: dict[int, dict[str, Any]] = {}       # id -> {"e": entity, "at": first seen, "dist": last}
        self.bag: dict[str, int] | None = None
        self.said_since = 0
        self.chat_after = 0
        self.next_bag = 0.0

    def run(self) -> None:
        try:
            with link.AgentLink(pid=self.pid, run_dir=self.run_dir, timeout=5) as a:
                self.said_since = int(a.json("said 0").get("now", 0))
                self.chat_after = int(a.json("chat 0").get("last", 0))
                me = a.json("mem self")
                who = me.get("name") if json.is_object(me) else None
                if who:
                    self.tag = "[%s]" % who
                self.log.line(self.tag, "START", "watching game %d" % self.pid)
                while not self.stop.is_set():
                    self.look(a)
                    self.stop.wait(self.every)
        except link.AgentError as e:
            self.log.line(self.tag, "END", "the game's agent went away (%s)" % e)

    def look(self, a: link.AgentLink) -> None:
        now = time.time()
        m = a.json("mem map")
        if not json.is_object(m) or "id" not in m:
            return          # loading, or between maps
        if m["id"] != self.map_id:
            self.map_id = m["id"]
            self.seen.clear()
            chests = "  (a chest map)" if m["id"] in CHEST_MAPS else ""
            self.log.line(self.tag, "MAP", "%s (%s)%s" % (m.get("name"), m["id"], chests))
        me = a.json("mem self")
        ents = a.json("mem entities")
        if not json.is_object(me) or "x" not in me or not json.is_array(ents):
            return
        here: set[int] = set()
        for e in ents:
            if not json.is_object(e):
                continue
            key = (self.map_id, e.get("kind", ""), e.get("name") or "")
            if key not in self.names:
                self.names.add(key)
                self.log.line(self.tag, "NAME", "%s %r" % (key[1], key[2]), e)
            if not worth_a_look(e):
                continue
            here.add(e["id"])
            dist = round(math.hypot(e["x"] - me["x"], e["y"] - me["y"]), 1)
            if e["id"] not in self.seen:
                self.seen[e["id"]] = {"e": e, "at": now, "dist": dist}
                mark = "  *** looks like a chest ***" if CHEST.search(e.get("name") or "") else ""
                self.log.line(self.tag, "APPEAR", "%r at %s,%s, %s steps from us (we are at %s,%s)%s"
                              % (e.get("name"), e["x"], e["y"], dist, me["x"], me["y"], mark), e)
            else:
                self.seen[e["id"]]["dist"] = dist
        for gone in [i for i in self.seen if i not in here]:
            s = self.seen.pop(gone)
            self.log.line(self.tag, "GONE", "%r after %.0f s, last %s steps from us"
                          % (s["e"].get("name"), now - s["at"], s["dist"]))
        self.read_lines(a)
        if now >= self.next_bag:
            self.next_bag = now + 2.0
            self.check_bag(a)

    def read_lines(self, a: link.AgentLink) -> None:
        """What the game spoke, and its broadcasts about chests, since the last look."""
        said = a.json("said %d" % self.said_since)
        if json.is_object(said):
            for row in said.get("said", []):
                self.log.line(self.tag, "SAID", repr(row.get("text")))
                self.said_since = max(self.said_since, int(row.get("t", 0)))
        chat = a.json("chat %d" % self.chat_after)
        if json.is_object(chat):
            for row in chat.get("chat", []):
                # broadcasts come with no sender; a player's line never goes in the log
                if not row.get("name") and CHEST.search(row.get("text") or ""):
                    self.log.line(self.tag, "NEWS", "channel %s: %r" % (row.get("channel"), row.get("text")))
            self.chat_after = max(self.chat_after, int(chat.get("last", 0)))

    def check_bag(self, a: link.AgentLink) -> None:
        rows = a.json("mem bag")
        if not json.is_array(rows):
            return
        bag: dict[str, int] = {}
        for r in rows:
            if json.is_object(r) and r.get("name"):
                bag[r["name"]] = bag.get(r["name"], 0) + int(r.get("count") or 1)
        if self.bag is not None:
            gained = {n: c - self.bag.get(n, 0) for n, c in bag.items() if c > self.bag.get(n, 0)}
            if gained:
                self.log.line(self.tag, "BAG", ", ".join("+%d %s" % (c, n) for n, c in gained.items()))
        self.bag = bag


def main() -> None:
    ap = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("--uf", help="the Uncle Farmer release folder whose agent the games loaded")
    ap.add_argument("--every", type=float, default=0.5, help="seconds between looks (default 0.5)")
    args = ap.parse_args()
    run_dir = os.path.join(args.uf, "agent", "run") if args.uf else paths.AGENT_RUN
    log = Log()
    stop = threading.Event()
    watchers: dict[int, Watcher] = {}
    print("chestwatch: writing to %s\nlooking for games in %s; Ctrl+C to end" % (log.path, run_dir), flush=True)
    said_none = False
    try:
        while True:
            for _port, pid, _secret in link.agents(run_dir):
                w = watchers.get(pid)
                if w is None or not w.is_alive():
                    watchers[pid] = Watcher(pid, run_dir, log, args.every, stop)
                    watchers[pid].start()
            if not watchers and not said_none:
                said_none = True
                print("no game with the agent yet: start Mist World (or check --uf)", flush=True)
            time.sleep(5)
    except KeyboardInterrupt:
        stop.set()
        for w in watchers.values():
            w.join(3)
        print("\nchestwatch ended. Lines written: %s\nlog: %s" % (
            ", ".join("%s %d" % kv for kv in sorted(log.counts.items())) or "none", log.path))


if __name__ == "__main__":
    main()
