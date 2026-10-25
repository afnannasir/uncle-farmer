"""Private messages: polite replies to strangers, commands and answers for trusted players.

One Messenger thread per game client (the main window starts and stops them). It reads the
private messages the agent collects (agent v15 `pms`) once a second and answers through the
game's own private chat (`pm`) at once - the only wait is the game's own rule of one line per 2 s,
which matters only when several answers are due together. Nothing is checked on a timer: the
messenger keeps a request open (`pms <after> <ms>`) that the agent answers the instant a private
message packet arrives - the game's own data, so it works with the game's speech silenced too.

Strangers: while a bot runs (or always, or never - Setup), each gets a few lines from the reply
list at once, never the same line twice, then one goodbye line and silence for a while. Several messages in a row get one answer, not several.

Trusted players (names in the list) can instead send a command as the whole message:
    help  status  where  results  today  bag  drop  pause  resume  stop  start [plan]
    home  add  leader  leave  mute  unmute  quit game
Anything else they write is treated as a chat between people and answered at most with a short
"away" note while a bot runs. Team invites from trusted players are accepted (engine/team.py).
"""

import collections
import os
import random
import threading
import time
from typing import Any

from . import applog, jsonio, nearby, paths, settings, stats, trust
from .clock import CLOCK, Clock
from .engine import follow, team
from .engine.bot import Bot, Stopped
from .errors import carry_on
from .i18n import _
from .link import AgentError, AgentLink

MESSAGES_DIR = os.path.join(paths.APP, "messages")
REPLIES_FILE = os.path.join(MESSAGES_DIR, "replies.txt")
GOODBYES_FILE = os.path.join(MESSAGES_DIR, "goodbyes.txt")
AGENT_PMS = 16
GAP = 2.1                    # seconds between two of our lines: the game refuses a second within 2 s
LOOK_EVERY = 2.0             # seconds between looks for trusted players in view (`come` finds them later)
WAIT = 1.0                   # longest a "tell me when a message comes" request is held open

DEFAULTS = {
    "pm_replies": "running",     # answer strangers: "running" (while a bot runs), "always", "never"
    "pm_trusted": [],            # names (any case) or game ids allowed to give commands
    "pm_call_by_name": True,     # a reply line without {name} starts with the player's name
    "pm_commands": True,
    "pm_commands_in_front": True,  # take trusted players' commands while the game's window is in front too
    "pm_max_replies": 4,         # lines to one stranger before the goodbye
    "pm_quiet_minutes": 60,      # after the goodbye, silence for this long
    "pm_delay": [0, 0],          # seconds before answering a stranger (0 = at once)
    "pm_speak": "trusted",       # speak arriving messages: "never", "trusted", "everyone"
    "pm_away_note": True,        # trusted chat while a bot runs: one short note every half hour
    "pm_accept_invites": True,   # accept team invites from trusted players
}

DEFAULT_REPLIES = """Hi {name}! I'm away from the keyboard right now.
Sorry, I can't chat at the moment.
I'm busy right now. Try again later.
Not around at the moment, back later.
"""

DEFAULT_GOODBYES = """I'll be back later. Bye for now.
Talk to you later.
"""

# command word -> (takes words after it, help text)
COMMANDS = collections.OrderedDict([
    ("help", (True, "help [command]: this list, or what one command does")),
    ("status", (False, "what I'm doing")),
    ("where", (False, "map and place")),
    ("players", (False, "who is near me")),
    ("pet", (False, "my pet's HP and MP")),
    ("results", (False, "this run's results")),
    ("today", (False, "today's results")),
    ("bag", (False, "load and potions")),
    ("drop", (False, "drop time left")),
    ("pause", (False, "hold still")),
    ("resume", (False, "go on")),
    ("stop", (False, "end the run somewhere safe")),
    ("start", (True, "start [plan]")),
    ("home", (False, "back to where farming started, then stop")),
    ("come", (True, "come [your map, or the E key's text copied with Shift+C]: walk to you")),
    ("follow", (False, "stay with you and help you fight")),
    ("unfollow", (False, "stop following")),
    ("buff", (True, "buff (or buff off): use buffs while following you")),
    ("add", (False, "take your team application")),
    ("leader", (False, "make you team leader")),
    ("leave", (True, "leave (or quit): leave the team")),
    ("mute", (False, "silence the game")),
    ("unmute", (False, "the game talks again")),
    ("quit", (True, "quit game: close the game")),
])
ALIASES = {"stats": "results", "commands": "help", "pos": "where", "accept": "add", "tfl": "leader",
           "comehere": "come", "followme": "follow", "stopfollow": "unfollow", "who": "players",
           "nearby": "players"}


def details():
    """What each command does, for "help <command>": one private message each (200 letters at
    most), in the words a player would use."""
    return {
        "help": _("help: lists the commands. help and a command's name says what that one does, for example: help follow"),
        "status": _("status: what I'm doing (running, paused or idle), the run, its minutes and kills, my map, HP and MP."),
        "where": _("where: the map I'm on and my x, y on it."),
        "players": _("players: how many other players I can see and who they are, nearest first, with how many steps away."),
        "pet": _("pet: my pet's name, HP and MP (MP once the bot has read it in a run)."),
        "results": _("results: what this run has got so far (kills, arnebia, shards, experience), or the last run's if none is going."),
        "today": _("today: everything all of today's runs got, added up, with how many runs and minutes."),
        "bag": _("bag: how full my backpack is and how many potions of each kind I have."),
        "drop": _("drop: how much drop time I have left today."),
        "pause": _("pause: I stop where I am and hold still until you send resume. The run is not ended."),
        "resume": _("resume: I go on with the paused run."),
        "stop": _("stop: I end the run the safe way, as if you pressed Stop in Uncle Farmer."),
        "start": _("start: I start the plan I ran last. start and a plan's name starts that plan, for example: start wolves"),
        "home": _("home: I go back to where my farming started, then the run stops. If I don't know where that "
                  "was, the hearthstone takes me home."),
        "come": _("come: I walk to you. Best: press E, copy it with Shift+C and send come and what you copied. come and a map's name works too."),
        "follow": _("follow: I stay with you, help with your fights and go where you go. A Priest or Sorceress heals you and the team. No buffs unless you send buff. After a minute without seeing you I stop."),
        "unfollow": _("unfollow: I stop following you."),
        "buff": _("buff: while I follow you I keep my buffs up. buff off stops them again. Each new follow starts without buffs."),
        "add": _("add: I accept your application to join my team."),
        "leader": _("leader: I make you the leader of my team."),
        "leave": _("leave (or quit team): I leave the team I'm in."),
        "mute": _("mute: the game stops talking (only my game)."),
        "unmute": _("unmute: the game talks again."),
        "quit": _("quit game: I close the game. Any run stops first. A plain quit means leave the team."),
    }


# ---------------------------------------------------------------- settings and texts
def config():
    s = settings.load_app()
    return {k: s.get(k, v) for k, v in DEFAULTS.items()}


def _lines(path, default):
    if not os.path.exists(path):
        jsonio.save_text(path, default)
    with open(path, encoding="utf-8") as f:
        return [l.strip() for l in f if l.strip() and not l.lstrip().startswith("#")]


def address(line, name, cfg):
    """A reply line said to someone: {name} in it becomes their name; a line without it starts with
    their name when pm_call_by_name is on ("Kim, I'm away right now")."""
    if "{name}" in line:
        return line.replace("{name}", name)
    if cfg.get("pm_call_by_name", True) and name and name != "?":
        return "%s, %s" % (name, line)
    return line


def replies():
    return _lines(REPLIES_FILE, DEFAULT_REPLIES)


def goodbyes():
    return _lines(GOODBYES_FILE, DEFAULT_GOODBYES)


def save_lines(path, lines):
    """Rewrite a replies/goodbyes file in one atomic step: the message thread may be reading it."""
    jsonio.save_text(path, "\n".join(l.strip() for l in lines if l.strip()) + "\n")


def parse_command(text):
    """(command, rest) when the whole message is a command, else None. "stop it lol" is chat."""
    words = text.strip().split()
    if not words:
        return None
    word = words[0].lower().strip("!/.,?")
    word = ALIASES.get(word, word)
    if word not in COMMANDS:
        return None
    takes_more, _help = COMMANDS[word]
    rest = " ".join(words[1:])
    if rest and not takes_more:
        return None
    if word in ("quit", "leave"):
        # "quit game" closes the game and needs the whole phrase; a plain "quit", "quit team" or
        # "leave team" means the team (a plain quit used to be taken for chat and did nothing)
        if word == "quit" and rest.lower() == "game":
            return word, ""
        if rest.lower() in ("", "team"):
            return "leave", ""
        return None
    return word, rest


# ---------------------------------------------------------------- stranger replies
class ReplyBook:
    """Who has been answered, how often, and which lines are left for them."""

    def __init__(self):
        self.people = {}

    def answer(self, name, cfg, now=None, clock: Clock = CLOCK):
        """The line for name's message now, or None to stay silent."""
        now = now or clock.time()
        key = name.lower()
        p = self.people.get(key)
        quiet = cfg["pm_quiet_minutes"] * 60
        if p is None or now - p["last"] > quiet and now >= p["silent_until"]:
            p = self.people[key] = {"count": 0, "last": now, "silent_until": 0, "left": []}
        if now < p["silent_until"]:
            return None
        p["last"] = now
        p["count"] += 1
        if p["count"] <= cfg["pm_max_replies"]:
            if not p["left"]:
                p["left"] = replies()
                random.shuffle(p["left"])
            return p["left"].pop() if p["left"] else None
        p["silent_until"] = now + quiet
        lines = goodbyes()
        return random.choice(lines) if lines else None

    def forget(self):
        self.people.clear()


# ---------------------------------------------------------------- the messenger
class Messenger(threading.Thread):
    """Private messages of one game. `host` is the main window's side of things:
    host.run_for(pid) -> Run or None, host.call(fn, *a) runs fn on the window's thread and returns
    its result, host.start_bot(pid, plan) / host.stop_bot(pid), host.log(line, speak, who),
    host.character(pid)."""

    clock: Clock = CLOCK

    def __init__(self, pid, host, clock: Clock = CLOCK):
        super().__init__(daemon=True, name="unclefarmer-pm-%d" % pid)
        self.pid, self.host = pid, host
        self.clock = clock
        self.halt = threading.Event()
        self.outbox = collections.deque()      # (id, name, text, not before)
        self.book = ReplyBook()
        self.pending = set()                   # strangers with an answer already waiting
        self.away_noted = {}
        self.last_sent = 0.0
        self.link: Any = None
        self.ready = False
        self.unsupported = False               # the game runs an agent without private messages
        self.sightings = {}                    # trusted player (lower case) -> (map id, x, y, when): memory only

    def stop(self):
        self.halt.set()

    # -------------------------------------------------- the loop
    def run(self):
        applog.thread("messages %d" % self.pid)
        try:
            self.link = AgentLink(pid=self.pid)
            v = self.link.send("version")
            if not v.startswith("mwagent ") or int(v.split()[1]) < AGENT_PMS:
                self.unsupported = True         # an older agent: no private messages here
                return
            self.link.send("arm")
            last = self.link.json("pms 0").get("last", 0)   # what came before we started isn't ours
            self.ready = True
            next_dialog = next_look = 0.0
            while not self.halt.is_set():
                # The agent answers this the moment a message arrives (nothing runs on a timer);
                # it comes back empty after WAIT, or sooner when a queued line is due.
                got = self.link.json("pms %d %d" % (last, int(1000 * self.wait_for())))
                last = got.get("last", last)
                for pm in got.get("pms", []):
                    if not pm.get("mine"):        # our own (agent v34): what we sent, not ours to answer
                        self.received(pm)
                self.flush()
                if self.clock.time() >= next_dialog:
                    next_dialog = self.clock.time() + 3.0
                    self.idle_invites()
                if self.clock.time() >= next_look:
                    next_look = self.clock.time() + LOOK_EVERY
                    self.note_players()
        except (AgentError, OSError, ValueError):
            pass                                # the game closed; the window starts a new one later
        except Exception as e:
            carry_on(e, "the message thread")
            applog.crash("message thread %s" % self.pid)   # a bug here must leave a traceback
        finally:
            self.ready = False
            if self.link:
                self.link.close()

    def note_players(self):
        """Remember where each trusted player in view was last seen. A player walks out of view
        within a minute, and `come` sent after that found nobody; any of our games having seen
        them a little earlier is enough to walk there and look again."""
        s = self.snap()
        if not s:
            return
        trusted = trust.Trusted(config()["pm_trusted"])
        mid, now = (s.get("map") or {}).get("id"), self.clock.time()
        for e in s.get("entities", []):
            name = (e.get("name") or "").strip().lower()
            if e.get("kind") == "player" and mid is not None and trusted.has(name, e.get("id")):
                self.sightings[name] = (mid, e["x"], e["y"], now)

    def say(self, line, speak=False, private=False):
        """private: a line with someone's message in it - shown and spoken, never written to a file."""
        self.host.log(line, speak, self.host.character(self.pid), private)

    # -------------------------------------------------- arriving
    def received(self, pm):
        cfg = config()
        name, text, pid_from = pm.get("name") or "?", pm.get("text") or "", pm.get("id", 0)
        trusted = trust.Trusted(cfg["pm_trusted"]).has(name, pid_from)
        # the player in the game's own window reads it there: shown in the log, not read out
        in_front = bool(getattr(self.host, "game_in_front", lambda pid: False)(self.pid))
        speak = not in_front and (cfg["pm_speak"] == "everyone" or (cfg["pm_speak"] == "trusted" and trusted))
        if pm.get("voice"):
            self.say(_("voice message from {name} - play it in Chat (Ctrl+T)", name=name), speak, private=True)
        else:
            self.say(_("message from {name}: {words}", name=name, words=text), speak, private=True)
        if not pid_from:
            return
        if trusted:
            # never the strangers' replies: a command (unless commands are off, or off while the
            # player is in the game's window), else at most the away note
            cmd = parse_command(text) if cfg["pm_commands"] and (cfg["pm_commands_in_front"] or not in_front) else None
            if cmd:
                self.command(pid_from, name, cmd[0], cmd[1])
                return
            run = self.host.run_for(self.pid)
            if (run and not in_front and cfg["pm_commands"] and cfg["pm_away_note"]
                    and self.clock.time() - self.away_noted.get(name.lower(), 0) > 1800):
                self.away_noted[name.lower()] = self.clock.time()
                self.send(pid_from, name, address(_("I'm away, the bot is running. Send help for commands."),
                                                  name, cfg))
            return
        mode = cfg["pm_replies"]
        if mode == "never" or (mode == "running" and not self.host.run_for(self.pid)):
            return
        if name.lower() in self.pending:
            return                              # one answer for a burst of messages
        line = self.book.answer(name, cfg, clock=self.clock)
        if line:
            lo, hi = (list(cfg["pm_delay"]) + [0, 0])[:2]
            self.pending.add(name.lower())
            self.send(pid_from, name, address(line, name, cfg), self.clock.time() + random.uniform(lo, max(lo, hi)))

    def wait_for(self):
        """Seconds the next message request may wait: until the next queued line is due."""
        if not self.outbox:
            return WAIT
        due = min(max(when, self.last_sent + GAP) for _to, _n, _t, when in self.outbox)
        return min(WAIT, max(0.0, due - self.clock.time()))

    def send(self, to, name, text, when=0.0, first=False):
        """Queue a line. first: a trusted player's answer goes ahead of strangers' replies. A line
        longer than the game's 200 characters is sent in parts by the agent (v35), in turn."""
        item = (to, name, text, when)
        if first:
            self.outbox.appendleft(item)
        else:
            self.outbox.append(item)

    def flush(self):
        now = self.clock.time()
        for i, (to, name, text, when) in enumerate(self.outbox):
            if when <= now and now - self.last_sent >= GAP:
                del self.outbox[i]
                self.pending.discard(name.lower())
                r = self.link.send("pm %d %s" % (to, text.replace("\n", " ")))
                self.last_sent = now
                self.say(_("message to {name}: {words}", name=name, words=text) if r == "sent"
                         else _("couldn't message {name}: {why}", name=name, why=r), private=True)
                return

    # -------------------------------------------------- trusted players
    def command(self, to, name, word, rest):
        self.say(_("{name} asks: {what}", name=name, what=(word + " " + rest).strip()), private=True)
        try:
            answer = getattr(self, "cmd_" + word)(to, name, rest)
        except Exception as e:          # an answer must never take the messenger down
            carry_on(e, "a private command")
            applog.crash("pm command %s" % word)
            answer = _("that didn't work: {err}", err=e)
        if answer:
            self.send(to, name, answer, first=True)

    def snap(self):
        s = self.link.json("mem")
        return s if s.get("ok") else None

    def cmd_help(self, to, name, rest):
        word = rest.strip().lower().strip("!/.,?")
        if not word:
            self.send(to, name, _("Commands: {list}", list=", ".join(COMMANDS)), first=True)
            hint = _("Send help and a command's name to hear what it does, for example: help follow")
            self.outbox.insert(1, (to, name, hint, 0.0))      # right after the list, ahead of other replies
            return None
        word = ALIASES.get(word) or word
        if word == "quit" or word.startswith("quit "):
            word = "quit"
        text = details().get(word)
        if text is None:
            return _("No command called {word}. Send help for the list.", word=rest.strip()[:40])
        names = [a for a, w in ALIASES.items() if w == word]
        return text + (" " + _("Also: {names}", names=", ".join(names)) if names else "")

    def cmd_status(self, to, name, rest):
        s = self.snap()
        if not s:
            return _("Not logged in.")
        me = s["self"]
        run = self.host.run_for(self.pid)
        hp = int(100 * me.get("hp", 0) / max(1, me.get("maxhp", 1)))
        vit = me.get("vitals") or {}
        sheet = me.get("sheet") or {}
        mp = vit.get("mp", sheet.get("mp", -1))
        mp_part = ", MP %d%%" % int(100 * mp / sheet["maxmp"]) if mp >= 0 and sheet.get("maxmp") else ""
        where = s["map"]["name"]
        p = nearby.pet(s)
        if p and p["maxhp"]:
            mp_part += _(", pet HP {pct}%", pct=int(100 * p["hp"] / p["maxhp"]))
        if run:
            mins = int((self.clock.time() - run.started) / 60) if getattr(run, "started", 0) else 0
            kills = run.tally.result()["kills"] if run.tally else 0
            state = _("paused") if run.bot and run.bot.paused.is_set() else _("running")
            return _("{state} {what} for {m} min on {map}, HP {hp}%{mp}, {k} kills", state=state, what=run.title,
                     m=mins, map=where, hp=hp, mp=mp_part, k=kills)
        return _("Idle on {map}, HP {hp}%{mp}.", map=where, hp=hp, mp=mp_part)

    def cmd_players(self, to, name, rest):
        s = self.snap()
        if not s:
            return _("Not logged in.")
        return _("On {map}: {who}", map=s["map"]["name"], who=nearby.players_text(s))

    def cmd_pet(self, to, name, rest):
        s = self.snap()
        if not s:
            return _("Not logged in.")
        return nearby.pet_text(s)

    def cmd_where(self, to, name, rest):
        s = self.snap()
        if not s:
            return _("Not logged in.")
        return _("{map} at {x}, {y}", map=s["map"]["name"], x=int(s["self"]["x"]), y=int(s["self"]["y"]))

    def cmd_results(self, to, name, rest):
        run = self.host.run_for(self.pid)
        if run and run.tally:
            return _("This run: {r}", r=stats.short(run.tally.result()))
        past = stats.runs(self.host.character(self.pid) or "")
        return _("Last run: {r}", r=stats.short(past[-1])) if past else _("No results yet.")

    def cmd_today(self, to, name, rest):
        who = self.host.character(self.pid) or ""
        since = time.mktime(time.strptime(time.strftime("%Y-%m-%d"), "%Y-%m-%d"))
        rs = stats.runs(who, since)
        run = self.host.run_for(self.pid)
        if run and run.tally:
            rs += [r for r in run.tally.results() if r["kind"] == stats.FARMING]
        if not rs:
            return _("Nothing today yet.")
        t = stats.add_up(rs)
        return _("Today, {n} runs, {m} min: {r}", n=t["runs"], m=int(t["minutes"]), r=stats.short(t))

    def cmd_bag(self, to, name, rest):
        rows = self.link.json("mem bag")
        rows = rows if isinstance(rows, list) else []
        pots = ["%s %d" % (r["name"], r["count"]) for r in rows if "potion" in (r.get("name") or "").lower()]
        s = self.snap() or {}
        sheet = (s.get("self") or {}).get("sheet") or {}
        load = (_("load {a} of {b}", a=sheet["load"], b=sheet["maxload"]) + ", ") if sheet.get("maxload") else ""
        return load + (", ".join(pots[:6]) if pots else _("no potions"))

    def cmd_drop(self, to, name, rest):
        s = self.snap() or {}
        sheet = (s.get("self") or {}).get("sheet") or {}
        left = sheet.get("drop_seconds", -1)
        if left < 0:
            return _("Drop time not read yet.")
        left = max(0, left - sheet.get("age", 0))
        return _("Drop time left: {h} h {m} min", h=left // 3600, m=left % 3600 // 60)

    def cmd_pause(self, to, name, rest):
        run = self.host.run_for(self.pid)
        if not run or not run.bot:
            return _("The bot isn't running.")
        run.bot.paused.set()
        return _("Paused. Send resume to go on.")

    def cmd_resume(self, to, name, rest):
        run = self.host.run_for(self.pid)
        if not run or not run.bot:
            return _("The bot isn't running.")
        run.bot.paused.clear()
        return _("Going on.")

    def cmd_stop(self, to, name, rest):
        if not self.host.run_for(self.pid):
            return _("The bot isn't running.")
        self.host.call(self.host.stop_bot, self.pid)
        return _("Stopping.")

    def cmd_start(self, to, name, rest):
        if self.host.run_for(self.pid):
            return _("Already running.")
        return self.host.call(self.host.start_bot, self.pid, rest.strip() or None)

    def cmd_mute(self, to, name, rest):
        return _("The game is quiet.") if self.link.send("speech off") == "off" else _("Couldn't.")

    def cmd_unmute(self, to, name, rest):
        return _("The game talks again.") if self.link.send("speech on") == "on" else _("Couldn't.")

    def cmd_quit(self, to, name, rest):
        if self.host.run_for(self.pid):
            self.host.call(self.host.stop_bot, self.pid)
        self.send(to, name, _("Closing the game."))
        self.flush_now()
        r = self.link.send("closegame")
        return None if r == "closing" else _("Couldn't close it: {why}", why=r)

    # chores that press keys: done by the running bot between steps, or right here when idle
    def cmd_home(self, to, name, rest):
        """Back to where farming started: the running farm's own start (its end goes there, walking
        and riding as travel does), else where this game's last farming run started (an errand of
        its own, as `come` is). The hearthstone only when neither is known."""
        run = self.host.run_for(self.pid)
        bot = getattr(run, "bot", None) if run else None
        place = getattr(bot, "start_place", None) if run and not getattr(run, "walk", False) else None
        if place:
            def back(b):
                b.end_place = ("place", place[0], [place[1], place[2]])
                done = Stopped(_("going back to where farming started, as {name} asked", name=name))
                done.asked = True          # asked for: not the end of farming (no closing the game)
                raise done
            return self.chore(back, _("Going back to where farming started."), to, name)
        place = None if run else getattr(self.host, "farm_start_for", lambda pid: None)(self.pid)
        if place:
            def tell(text):
                self.send(to, name, text, first=True)
            text = "go %d at %d,%d" % (place[0], place[1], place[2])
            busy = self.host.call(self.host.errand_for, self.pid, text, _("going back to where farming started"),
                                  tell)
            return busy or _("Going back to where farming started.")

        def job(b):
            ok = b.go_home(tries=2)
            self.send(to, name, _("Home.") if ok else _("The hearthstone didn't work."))
            if ok and b is not self.idle_bot_ref:
                done = Stopped(_("went home as {name} asked", name=name))
                done.asked = True
                raise done
        return self.chore(job, _("Going home."), to, name)

    def cmd_come(self, to, name, rest):
        """come, come <map>, or come <the location key E's text, copied with Shift+C>."""
        mid, cell = follow.parse_location(rest)
        if rest.strip() and mid is None:
            return _("I can't tell the map from that. Press E in the game, copy it with Shift+C and send: "
                     "come <what you copied>")

        def closer(bot):
            self.send(to, name, follow.come_to(bot, name, mid, cell), first=True)

        def job(bot):
            closer(bot)
            done = Stopped(_("went to {name} as they asked", name=name))
            done.stay = True              # the run ends beside them, not at its end place
            raise done

        def tell(text):
            self.send(to, name, text, first=True)
        run = self.host.run_for(self.pid)
        if run and getattr(run, "follows", None) == name.lower():
            # already following them: walk up and keep following (ending the run here stopped
            # the following they had asked for)
            return self.chore(closer, _("Coming."), to, name)
        if run:
            return self.chore(job, _("Coming."), to, name)    # the running bot goes, then stops there
        # idle: a run of its own, so it can be stopped like any other and this thread stays free
        text = 'come "%s"' % name + (" %d" % mid if mid else "") + (" at %d,%d" % cell if cell and mid else "")
        busy = self.host.call(self.host.errand_for, self.pid, text, _("going to {name}", name=name), tell)
        return busy or _("Coming.")

    def cmd_follow(self, to, name, rest):
        def tell(text):
            self.send(to, name, text, first=True)
        return self.host.call(self.host.follow_for, self.pid, name, tell)

    def cmd_unfollow(self, to, name, rest):
        return self.host.call(self.host.unfollow, self.pid)

    def cmd_buff(self, to, name, rest):
        """Buffs while following: off unless asked for, "buff" turns them on, "buff off" off."""
        word = rest.strip().lower()
        if word not in ("", "on", "off", "stop", "no"):
            return _("Send buff, or buff off.")
        run = self.host.run_for(self.pid)
        bot = getattr(run, "bot", None) if run else None
        if not run or not getattr(run, "follows", None) or bot is None:
            return _("I'm not following anyone. Send follow first, then buff.")
        bot.follow_buffs = word in ("", "on")
        return _("Buffs on.") if bot.follow_buffs else _("Buffs off.")

    def cmd_add(self, to, name, rest):
        return self.chore(lambda bot: self.send(to, name, team.accept_application(bot, name)[1]),
                          _("Looking for your application."), to, name)

    def cmd_leader(self, to, name, rest):
        return self.chore(lambda bot: self.send(to, name, team.make_leader(bot, name)[1]), None, to, name)

    def cmd_leave(self, to, name, rest):
        return self.chore(lambda bot: self.send(to, name, team.leave(bot)[1]), None, to, name)

    idle_bot_ref = None

    def chore(self, job, first, to, name):
        """A running bot does it between two steps; with no run, this thread does it now."""
        run = self.host.run_for(self.pid)
        if run and run.bot:
            run.bot.requests.put(job)
            return first
        bot = self.idle_bot()
        if bot is None:
            return _("Not logged in.")
        if first:
            self.send(to, name, first)
            self.flush_now()
        job(bot)
        return None

    def idle_bot(self):
        """A bot on this messenger's own connection, for chores while no run is going."""
        s = self.snap()
        if not s:
            return None
        profile = settings.load_profile(s["self"]["name"])
        b = Bot(self.link, profile, lambda m, speak=False: self.say(m, speak), threading.Event(),
                clock=self.clock)
        b.trusted = trust.Trusted(config()["pm_trusted"])
        b.locate = getattr(self.host, "locate", None)
        b.last_seen = getattr(self.host, "last_seen", None)
        self.idle_bot_ref = b
        return b

    def flush_now(self):
        end = self.clock.time() + 6
        while self.outbox and self.clock.time() < end:
            self.flush()
            self.clock.sleep(0.2)

    # -------------------------------------------------- invites while no bot runs
    def idle_invites(self):
        cfg = config()
        if not cfg["pm_accept_invites"] or self.host.run_for(self.pid):
            return                              # a running bot handles its own popups
        d = self.link.json("dialog")
        if not d.get("ok") or not d.get("open"):
            return
        texts = [e.get("text") or e.get("raw") or "" for row in (d.get("top") or {}).get("rows", []) for e in row if e]
        inv = team.invite_in(texts)
        if inv and trust.Trusted(cfg["pm_trusted"]).has(inv[0], link=self.link):
            bot = self.idle_bot()
            if bot and team.accept_invite(bot):
                self.say(_("joined {who}'s team ({team}): {who} is trusted", who=inv[0], team=inv[1]), True)
