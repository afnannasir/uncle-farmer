"""Game chat while the bot farms: read every channel and write to any, without touching the keys.

The agent (v32) keeps the chat channels' lines in its memory (`chat`) and sends a line through the
game's own sender (`say`), the way the game does when the player types one - no chat box opens and
no key is pressed, so the bot's keys and the chat never get in each other's way.

Chat lives in memory only, as private as the game's own chat box: a few hundred lines per game,
never written to a file or a log, and gone when Uncle Farmer closes. Private messages are the Messenger's
(messages.py): they show here too, but it is the Messenger that speaks and sends them.
"""

import collections
from typing import Any
import threading
import time
import winsound

from .i18n import N_, _
from .link import AgentError, AgentLink

AGENT_CHAT = 32
AGENT_VOICE = 42           # voice messages: the "voice" of chat and pms rows, `voice play/record/send`
VOICE_CHANNELS = ("3", "4")   # the game sends voice to Gang and Team, and in private messages
KEEP = 500                 # the All buffer: the channels (notices and private messages left out)
KEEP_EACH = 300            # each channel's own buffer
KEEP_PRIVATE = 1000        # every private conversation together
WAIT_MS = 1000
# The agent drops a connection that has been quiet for 120 s (StateServer.IDLE_MS). The sending
# connection is quiet whenever the player only reads, and the next send or play then went into the
# closed connection ("[WinError 10053] An established connection was aborted"). One quiet this long
# is replaced before it is used, so a line is never sent into a dead connection, nor sent twice.
SENDER_IDLE = 90.0

# outgoing number -> name; a channel arrives as three times its number. The private channel is a
# channel players join, like the international rooms; private messages (one player to another) are
# something else: the Messenger's, kept under PRIVATE.
CHANNELS: "collections.OrderedDict[int, str]" = collections.OrderedDict([
    (0, N_("Global")), (1, N_("Map")), (2, N_("Nearby")), (3, N_("Gang")), (4, N_("Team")),
    (5, N_("International")), (6, N_("Private channel")),
])
CHANNEL_KEYS = [str(k) for k in CHANNELS]
PRIVATE = "private"
NOTICE = "notice"          # the game's broadcasts on other numbers: loot, events, team recruiting
SPEAK_DEFAULT = ["4", "3", "2"]     # team, gang, nearby (private messages: the Messenger's setting)


MOST = 200                 # the game's chat box; the agent (v35) sends a longer text in parts


def parts(text):
    """The parts a text is sent in, as the agent cuts it: at the last space before 200 characters."""
    out, rest = [], " ".join(text.split())
    while len(rest) > MOST:
        cut = rest.rfind(" ", 0, MOST + 1)
        if cut < MOST // 2:
            cut = MOST
        out.append(rest[:cut].strip())
        rest = rest[cut:].strip()
    if rest:
        out.append(rest)
    return out


def channel_of(incoming):
    """The channel key ("0".."5") of an incoming number, or NOTICE."""
    if incoming % 3 == 0 and incoming // 3 in CHANNELS:
        return str(incoming // 3)
    return NOTICE


def channel_name(key):
    if key == PRIVATE:
        return _("Private messages")
    if key == NOTICE:
        return _("Notice")
    return _(CHANNELS.get(int(key), str(key)))


class Feed(threading.Thread):
    """One game's chat. `on_line(feed, line)` is called (on this thread) for each new line:
    {"when", "channel", "name", "text", "mine"}, and "voice" (its name in the game) for a voice message."""

    def __init__(self, pid, on_line=None):
        super().__init__(daemon=True, name="unclefarmer-chat-%d" % pid)
        self.pid = pid
        self.on_line = on_line
        self.lines = collections.deque(maxlen=KEEP)          # every channel but the notices
        self.buffers = collections.defaultdict(lambda: collections.deque(maxlen=KEEP_EACH))
        self.buffers[PRIVATE] = collections.deque(maxlen=KEEP_PRIVATE)
        self.ids = {}                 # player name (lower case) -> id, from private messages: memory only
        self.room = 0
        self.halt = threading.Event()
        self.unsupported = False
        self.link = None
        self.send_lock = threading.Lock()
        self.sender = None
        self.sender_used = 0.0        # time.monotonic() of the sending connection's last answer
        self.version = 0
        self.voice_parts = 0

    def stop(self):
        self.halt.set()

    def buffer(self, key):
        """The lines of one buffer: "all", or a channel key."""
        return list(self.lines) if key == "all" else list(self.buffers.get(key, ()))

    def add(self, channel, name, text, mine=False, to=None, peer_id=0, voice=None):
        line = {"when": time.time(), "channel": channel, "name": name, "text": text, "mine": mine}
        if to:
            line["to"] = to
        if voice:
            line["voice"] = voice
        if channel == PRIVATE:
            # who the conversation is with: the other player, whichever way the message went
            line["peer"] = to or name
            line["peer_id"] = peer_id or self.ids.get((to or name or "").lower(), 0)
        if channel not in (NOTICE, PRIVATE):
            # All is the channels': notices would push real chat out of it, and private messages
            # have their own tab, one conversation per person
            self.lines.append(line)
        self.buffers[channel].append(line)
        if self.on_line:
            self.on_line(self, line)

    def run(self):
        try:
            self.link = AgentLink(pid=self.pid)
            v = self.link.send("version")
            if not v.startswith("mwagent ") or int(v.split()[1]) < AGENT_CHAT:
                self.unsupported = True        # an older agent: restart the game for chat
                return
            self.version = int(v.split()[1])
            last = self.link.json("chat 0").get("last", 0)       # only what comes from now on
            last_pm = self.link.json("pms 0").get("last", 0)
            while not self.halt.is_set():
                got = self.link.json("chat %d %d" % (last, WAIT_MS))
                last = got.get("last", last)
                self.room = got.get("room", self.room)
                for c in got.get("chat", []):
                    self.add(channel_of(c.get("channel", -1)), c.get("name") or "", c.get("text") or "",
                             voice=c.get("voice"))
                pms = self.link.json("pms %d" % last_pm)
                last_pm = pms.get("last", last_pm)
                for pm in pms.get("pms", []):
                    if pm.get("id") and pm.get("name"):
                        self.ids[pm["name"].lower()] = pm["id"]
                    if pm.get("mine"):
                        # what this character sent, in the game or from here: the other side's id and name
                        self.add(PRIVATE, "", pm.get("text") or "", mine=True,
                                 to=pm.get("name") or str(pm.get("id") or "?"), peer_id=pm.get("id") or 0,
                                 voice=pm.get("voice"))
                    else:
                        self.add(PRIVATE, pm.get("name") or "", pm.get("text") or "", peer_id=pm.get("id") or 0,
                                 voice=pm.get("voice"))
        except (AgentError, OSError, ValueError):
            pass                              # the game closed
        finally:
            if self.link:
                self.link.close()

    @staticmethod
    def peer_key(line):
        """One conversation per player: by the game's id when known, else by name."""
        return "id:%d" % line["peer_id"] if line.get("peer_id") else "name:" + (line.get("peer") or "?").lower()

    def conversations(self) -> list[dict[str, Any]]:
        """[{"key", "name", "id", "last", "lines"}] of every private conversation, the latest first."""
        convs: dict[str, dict[str, Any]] = {}
        for n, line in enumerate(self.buffers[PRIVATE]):
            k = self.peer_key(line)
            c = convs.setdefault(k, {"key": k, "name": line.get("peer") or "?", "id": line.get("peer_id") or 0,
                                     "last": 0.0, "lines": []})
            if line.get("peer") and not line["peer"].isdigit():
                c["name"] = line["peer"]        # a name beats an id shown as a number
            c["last"] = line["when"]
            c["order"] = n                   # arrival order: two messages can come in the same second
            c["lines"].append(line)
        return sorted(convs.values(), key=lambda c: -c["order"])

    def _ask(self, line):
        with self.send_lock:
            if self.sender is not None and time.monotonic() - self.sender_used > SENDER_IDLE:
                self.sender.close()          # the agent has dropped it, or is about to
                self.sender = None
            if self.sender is None:
                self.sender = AgentLink(pid=self.pid)
                self.sender.send("arm")
            reply = self.sender.send(line)
            self.sender_used = time.monotonic()
            return reply

    def id_of(self, who):
        """The game's id for a player: typed as a number, or a name this character messaged with,
        or one the game has shown (agent v34 `idof`). None when it isn't known."""
        who = who.strip()
        if who.isdigit():
            return int(who)
        hit = self.ids.get(who.lower())
        if hit:
            return hit
        try:
            n = int(self._ask("idof " + who))
        except (AgentError, OSError, ValueError):
            self.sender = None
            return None
        return n or None

    # ---------------------------------------------------------------- voice messages
    def voice_ready(self):
        """None when this game's agent does voice messages, else why not, for the player."""
        if self.unsupported or self.version < AGENT_VOICE:
            return _("Voice messages need the new agent in this game: restart the game.")
        return None

    def play_voice(self, name):
        """Plays a voice message (the agent decodes it with the game's own codec). None, or why not."""
        why = self.voice_ready()
        if why:
            return why
        try:
            r = self._ask("voice play " + name)
        except (AgentError, OSError) as e:
            self.sender = None
            return _("The game didn't play it: {why}", why=e)
        if not r.startswith("wav "):
            return _("The game didn't play it: {why}", why=r)
        path = r[4:].rsplit(" ", 1)[0]
        winsound.PlaySound(path, winsound.SND_FILENAME | winsound.SND_ASYNC | winsound.SND_NODEFAULT)
        return None

    def record_voice(self):
        """Starts recording from the game's microphone. None, or why not."""
        why = self.voice_ready()
        if why:
            return why
        try:
            r = self._ask("voice record")
        except (AgentError, OSError) as e:
            self.sender = None
            return _("The game didn't start recording: {why}", why=e)
        return None if r == "recording" else _("The game didn't start recording: {why}", why=r)

    def send_voice(self, channel=None, to=0):
        """Stops recording and sends it: to Team or Gang (channel "4" or "3"), or to the player with
        id to. None when sent, else why not; voice_parts then says in how many messages (the agent
        cuts a recording longer than 20 s into parts of about the same length, as the game takes
        no longer one)."""
        self.voice_parts = 0
        line = "voice send pm %d" % to if to else "voice send %s" % channel
        try:
            r = self._ask(line)
        except (AgentError, OSError) as e:
            self.sender = None
            return _("The game didn't send it: {why}", why=e)
        if r.startswith("sent"):
            bits = r.split()
            self.voice_parts = int(bits[1]) if len(bits) > 1 and bits[1].isdigit() else 1
            return None
        if "too short" in r:
            return _("That was too short to send - hold on a little longer.")
        if "nothing was recorded" in r:
            return _("Nothing was recorded: check the microphone the game uses.")
        return _("The game didn't send it: {why}", why=r)

    def cancel_voice(self):
        try:
            self._ask("voice cancel")
        except (AgentError, OSError):
            self.sender = None

    def say(self, channel, text):
        """A line on a chat channel (0-6). Returns None when sent, else why not, for the player."""
        text = " ".join(text.split())
        if not text:
            return _("Type a message first.")
        try:
            r = self._ask("say %d %s" % (channel, text))
        except (AgentError, OSError) as e:
            self.sender = None
            return _("The game didn't take it: {why}", why=e)
        if r == "sent":
            return None
        if "international" in r:
            return _("Join an international room in the game first.")
        if "private channel" in r:
            return _("Join a private channel in the game first.")
        return _("The game didn't take it: {why}", why=r)
