"""Leading a team (the character window's Team tab): taking requests to join by ourselves,
removing members who went offline, handing the team over at the end, making the team at the start
(leaving one another player leads), and staying in town to manage it. Each only when ticked.

What the game shows, read live on 2026-11-21 with two characters:

* The Action menu (W_LOCAL_OPTION) opens on "Action menu; lag <ms>". Its team row is "Create a
  team" without a team, else "<name> team" and beside it "quit" for a member, or "invite",
  "rename" and "disband" for the leader. While players wait to join, the leader's row also holds
  "apply to join: <n> " right after the team.
* A request plays sound 1057 for the leader alone, a second after the player sent it, with the
  game window in the background too.
* "apply to join" lists one row per player: their name (cmd [1, 6, id, 0]; the last number turns
  1 once Enter selects them), their id, class and level, then "agree" and "refuse". "agree"
  takes in the selected players: "<name> joined the team" (chat, a game line) for everyone.
* "Application: <n>" beside "friends" is a FRIEND request, never a team one.

* The member list (Enter on "<name> team") has a row per member, "<name>;<id>" first, and for
  the leader "Transfer team leader" and "remove" beside each other member. The info key (W_INFO)
  on a member makes the server answer "(1(<name>)1)|||((,<id>;))|||..." when they are online,
  and nothing when they aren't.
* Each choice asks "Are you sure ...?" with "Confirm" and "cancel"; then, in the chat for everyone:
  "<name> has been removed from the team", "<name> has become the team leader", "<name> has quit
  the team", "Team disbanded" (no name) and "<name> set up a team named <team>".

Only players the profile lets in are selected (trusted players, the game's friends or a list of
names and ids); the others are left waiting, never refused, and the log names them. When another
player disbands the team or takes it over, it isn't managed any more for the rest of the run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, cast

from .. import atlas, gamekeys, sounds, trust
from ..i18n import _
from . import team

if TYPE_CHECKING:
    from .bot import Bot

REQUEST_SOUND = 1057
LAG = re.compile(r"^\s*Action menu\s*;\s*lag\s*(\d+)", re.I)
NO_TEAM = r"^\s*Create a team\s*$"
TEAM_ROW = r"^\s*\S.* team\s*$"
APPLY = r"^\s*apply to join\s*:\s*\d+"
DISBAND = r"^\s*disband\s*$"
AGREE = r"^\s*agree\s*$"
FRIENDS = r"^\s*friends\s*$"
JOINED = re.compile(r"^\s*(.+?) joined the team", re.I)
JOIN_WAIT = 3.0             # "joined the team" came at once live; a slow server gets three seconds
MOST_APPLICANTS = 15        # rows looked at in one list (the script's bound too)
MEMBER = re.compile(r"^\s*([^;,]+);(\d+)\s*$")     # a member list row: "<name>;<id>"
REMOVE = r"^\s*remove\s*$"
CONFIRM = r"^\s*Confirm\s*$"
INFO_LEAST, INFO_MOST = 1.5, 6.0
MOST_OPENINGS = 8           # member lists opened in one offline check (one per removal)
QUIT = r"^\s*quit\s*$"
QUIT_DONE = re.compile(r"has quit the team", re.I)
DISBANDED = re.compile(r"^\s*Team disbanded", re.I)
NEW_LEADER = re.compile(r"^\s*(.+?) has become the team leader", re.I)
AFTER_DISBAND = 4.0         # the script's pause between disbanding and making the team again
STAY_LOOK = 2.0             # seconds between looks while staying in town
LOOK_EVERY = 1.0            # the farm loop reads the sounds and the chat this often at most
STAY_EVERY_LEAST = 5        # minutes between offline checks while staying, at least


@dataclass
class TeamLead:
    """What this run knows of the team it leads."""
    sound_from: int | None = None      # the sound cursor; None until the request sound is listened to
    pending: bool = False              # a request was heard and not looked at yet
    left_waiting: set[str] = field(default_factory=set[str])   # not allowed in: said once each
    friends: trust.Trusted | None = None                       # the game's friends, read once a run
    offline_at: float | None = None    # when members were last checked for being offline
    chat_from: int | None = None       # the chat cursor the safety watch reads from
    own_disband: bool = False          # the next "Team disbanded" is ours
    handed: bool = False               # we handed the team over ourselves
    off: bool = False                  # someone else disbanded or took the team: hands off
    looked_at: float = -1e9            # when the farm loop last read the sounds and the chat
    told: set[str] = field(default_factory=set[str])


Row = list[dict[str, Any] | None]


def rows(top: dict[str, Any] | None) -> list[Row]:
    return cast(list[Row], (top or {}).get("rows") or [])


def cmd_of(e: dict[str, Any] | None) -> list[int]:
    return cast(list[int], (e or {}).get("cmd") or [])


def state(b: Bot) -> TeamLead:
    lead = b.__dict__.get("team_lead")
    if not isinstance(lead, TeamLead):
        lead = TeamLead()
        b.__dict__["team_lead"] = lead
    return lead


def _once(b: Bot, key: str, text: str) -> None:
    told = state(b).told
    if key not in told:
        told.add(key)
        b.log(text, speak=True)


# ------------------------------------------------------------------ reading the game
def chat_mark(b: Bot) -> int:
    try:
        return int(b.a.json("chat 0").get("last", 0))
    except Exception:              # an agent without it: nothing heard, never a crash
        return 0


def game_chat(b: Bot, since: int) -> tuple[int, list[str]]:
    """(the chat cursor now, the game's own lines - no player's name - after since), in one reading."""
    try:
        got = b.a.json("chat %d" % since)
    except Exception:
        return since, []
    lines: list[dict[str, Any]] = got.get("chat", [])
    return (int(got.get("last", since) or since),
            [str(m.get("text") or "").strip() for m in lines if not m.get("name") and m.get("text")])


def game_lines(b: Bot, since: int) -> list[str]:
    """The game's own chat lines (no player's name) after since."""
    return game_chat(b, since)[1]


def listen(b: Bot) -> None:
    """Hear the request sound from now on (this connection only; the agent drops it with the
    connection). A script's own sound wait sets its ids in its place and turns them off at its
    end: the next farm listens again."""
    lead = state(b)
    if lead.chat_from is None:
        lead.chat_from = chat_mark(b)
    try:
        lead.sound_from = sounds.events(b.a, 0)["now"]
        sounds.listen(b.a, str(REQUEST_SOUND))
    except Exception as e:         # an agent without sounds: requests are still seen at each farm start
        lead.sound_from = None
        _once(b, "no sound", _("can't hear team requests ({err}): they are looked at when farming starts", err=e))


def watch(b: Bot) -> None:
    """Another player disbanded the team, or became its leader: the team is no longer ours to
    manage, for the rest of the run (said once)."""
    lead = state(b)
    if lead.off:
        return
    if lead.chat_from is None:
        lead.chat_from, lead.own_disband = chat_mark(b), False
        return
    lead.chat_from, lines = game_chat(b, lead.chat_from)
    for line in lines:
        if DISBANDED.search(line) and not lead.own_disband:
            lead.off = True
        m = NEW_LEADER.match(line)
        if m and not lead.handed and m.group(1).strip().lower() != str(b.me().get("name", "")).lower():
            lead.off = True
        if DISBANDED.search(line):
            lead.own_disband = False
    if lead.off:
        b.log(_("someone else disbanded the team or took it over: no more managing it this run"), speak=True)


def heard_request(b: Bot) -> bool:
    lead = state(b)
    if lead.sound_from is None:
        return lead.pending
    try:
        batch = sounds.events(b.a, lead.sound_from)
    except Exception:
        return lead.pending
    lead.sound_from = batch["now"]
    if any(e["id"] == REQUEST_SOUND for e in batch["events"]):
        lead.pending = True
    return lead.pending


def open_action_menu(b: Bot) -> dict[str, Any] | None:
    """The Action menu's reading, or None when it didn't open."""
    b.close_dialogs()
    b.key(b.p.get("action_menu_key", "lctrl+enter"))
    d = b.wait_dialog(3.0)
    top = (d or {}).get("top")
    if not top or b.find_entry(top, LAG.pattern) is None:
        b.close_dialogs()
        return None
    return top


def lag_ms(b: Bot, top: dict[str, Any]) -> int | None:
    where = b.find_entry(top, LAG.pattern)
    if where is None:
        return None
    for t in b.entry_texts(top["rows"][where[0]][where[1]]):
        m = LAG.match(t)
        if m:
            return int(m.group(1))
    return None


def team_entry(b: Bot, top: dict[str, Any]) -> tuple[int, str] | None:
    """(row, "<name> team") of the team row, or None without a team."""
    for r, row in enumerate(rows(top)):
        first = row[0] if row else None
        texts = b.entry_texts(first) if first else []
        if any(re.match(NO_TEAM, t, re.I) for t in texts):
            return None
        for t in texts:
            if re.match(TEAM_ROW, t, re.I) and not t.strip().lower().startswith("view "):
                return r, t.strip()
    return None


def leads(b: Bot, top: dict[str, Any], row: int) -> bool:
    return any(e and any(re.match(DISBAND, t, re.I) for t in b.entry_texts(e)) for e in rows(top)[row])


# ------------------------------------------------------------------ who may join
def allowed(b: Bot, name: str, pid: int | None) -> bool:
    who = b.p.get("team_accept_who", "trusted")
    if who == "list":
        return trust.Trusted(b.p.get("team_accept_list", [])).has(name, pid)
    if who == "friends":
        return friends(b).has(name, pid)
    return b.trusted.has(name, pid, link=b.a)


def friends(b: Bot) -> trust.Trusted:
    """The game's friends list (Action menu, "friends"), read once a run: "<name>; <class>; ..."
    rows whose cmd is [32, 10, <id>, 0]."""
    lead = state(b)
    if lead.friends is not None:
        return lead.friends
    names: list[str | int] = []
    if open_action_menu(b) is not None and b.choose(FRIENDS) is not None:
        d = b.wait_dialog(3.0)
        for row in rows((d or {}).get("top")):
            e = row[0] if row else None
            cmd = cmd_of(e)
            if len(cmd) >= 3 and cmd[:2] == [32, 10]:
                names.append(int(cmd[2]))
                texts = b.entry_texts(e or {})
                if texts:
                    names.append(texts[0].split(";")[0].strip())
    b.close_dialogs()
    lead.friends = trust.Trusted(names)
    b.log(_("read {n} friends from the game for team requests", n=len(names) // 2))
    return lead.friends


# ------------------------------------------------------------------ taking requests
def applicants(b: Bot, top: dict[str, Any]) -> list[tuple[int, str, int | None, bool]]:
    """(row, name, id, selected) for each player in the "apply to join" list."""
    out: list[tuple[int, str, int | None, bool]] = []
    for r, row in enumerate(rows(top)[:MOST_APPLICANTS + 2]):
        e = row[0] if row else None
        cmd = cmd_of(e)
        if len(cmd) >= 4 and cmd[:2] == [1, 6]:
            texts = b.entry_texts(e or {})
            out.append((r, texts[0].strip() if texts else "?", int(cmd[2]), cmd[3] == 1))
    return out


def take_requests(b: Bot) -> list[str]:
    """Look at the team's requests and let in those allowed. The names that joined."""
    state(b).pending = False
    if b.p.get("team_accept_who", "trusted") == "friends":
        friends(b)                 # its own menu: read before the request list is open
    top = open_action_menu(b)
    if top is None:
        _once(b, "no menu", _("team requests: the Action menu didn't open"))
        return []
    team = team_entry(b, top)
    if team is None or not leads(b, top, team[0]):
        b.close_dialogs()
        _once(b, "not leader", _("team requests: this character doesn't lead a team"))
        return []
    if b.find_in_row(top, re.escape(team[1]), APPLY) is None:
        b.close_dialogs()
        return []
    if b.choose(APPLY, row=re.escape(team[1])) is None:
        return []
    return _pick_and_agree(b)


def _pick_and_agree(b: Bot) -> list[str]:
    d = b.wait_dialog(3.0)
    top = (d or {}).get("top")
    if not top:
        return []
    lead = state(b)
    wanted: list[str] = []
    for _r, name, pid, chosen in applicants(b, top):
        if not allowed(b, name, pid):
            if name not in lead.left_waiting:
                lead.left_waiting.add(name)
                b.log(_("{name} ({id}) asked to join the team and isn't allowed in: left waiting", name=name, id=pid))
            continue
        if not chosen and b.choose(r"^\s*%s\s*$" % re.escape(name)) is None:
            return []
        wanted.append(name)
    if not wanted:
        b.close_dialogs()
        return []
    now: dict[str, Any] = b.dialog().get("top") or {}
    if not all(chosen for _r, name, _pid, chosen in applicants(b, now) if name in wanted):
        b.close_dialogs()
        b.log(_("team requests: the game didn't select {names}", names=", ".join(wanted)), speak=True)
        return []
    mark = chat_mark(b)
    if b.choose(AGREE) is None:
        return []
    return _joined(b, mark, wanted)


def _joined(b: Bot, mark: int, wanted: list[str]) -> list[str]:
    end = b.clock.time() + JOIN_WAIT
    got: list[str] = []
    while b.clock.time() < end and len(got) < len(wanted):
        for line in game_lines(b, mark):
            m = JOINED.match(line)
            if m and m.group(1).strip().lower() in (w.lower() for w in wanted) and m.group(1).strip() not in got:
                got.append(m.group(1).strip())
        b.sleep(0.25)
    b.close_dialogs()
    if got:
        b.log(_("accepted into the team: {names}", names=", ".join(got)), speak=True)
    missing = [w for w in wanted if w.lower() not in (g.lower() for g in got)]
    if missing:
        b.log(_("agreed to {names} joining, and the game didn't say they joined", names=", ".join(missing)))
    return got


# ------------------------------------------------------------------ members who went offline
def plain(text: str) -> str:
    """A spoken line without the game's markup: "(1(Bob)1)|||((,12;))|||Elf" -> "Bob,12;Elf"."""
    text = text.replace(">||<", "").replace("|||", "")
    return re.sub(r"\(\d?\(|\)\d?\)", "", text).strip()


def answer_wait(lag: int | None) -> float:
    """How long the info key's answer may take: it came within a second at 120 ms of lag; four
    times the lag on top of half a second, never under 1.5 s nor over 6 s (the script's cap)."""
    return min(INFO_MOST, max(INFO_LEAST, 0.5 + 4 * (lag or 0) / 1000.0))


def members(b: Bot, top: dict[str, Any]) -> list[tuple[int, str, int]]:
    """(row, name, id) of each member in the team's member list ("<name>;<id>" first in a row)."""
    out: list[tuple[int, str, int]] = []
    for r, row in enumerate(rows(top)):
        first = row[0] if row else None
        texts = b.entry_texts(first) if first else []
        m = MEMBER.match(texts[0]) if texts else None
        if m:
            out.append((r, m.group(1).strip(), int(m.group(2))))
    return out


def _member_list(b: Bot) -> tuple[dict[str, Any], int | None] | None:
    """The member list of the team we lead, open, and the lag; None (closed) when we don't lead one."""
    top = open_action_menu(b)
    if top is None:
        return None
    lag = lag_ms(b, top)
    team = team_entry(b, top)
    if team is None or not leads(b, top, team[0]):
        b.close_dialogs()
        _once(b, "not leader", _("team: this character doesn't lead a team"))
        return None
    if b.choose(r"^\s*%s\s*$" % re.escape(team[1])) is None:
        return None
    d = b.wait_dialog(3.0)
    top = (d or {}).get("top")
    if not top or not members(b, top):
        b.close_dialogs()
        return None
    return top, lag


def online(b: Bot, top: dict[str, Any], row: int, name: str, pid: int, wait: float) -> bool | None:
    """Ask the game about the member on row (the info key with the cursor on them): True when it
    answers "<name>,<id>;", False when it says nothing in time, None when the cursor didn't get there."""
    if b.cursor_to(top, row, 0) is None:
        return None
    from . import chores          # (chores needs the Bot, which imports this module)
    want = "%s,%d;" % (name.lower(), pid)
    mark = chores.said_mark(b)
    b.key(gamekeys.info_key())
    end = b.clock.time() + wait
    while True:
        if any(plain(s).lower().startswith(want) for s in chores.said_since(b, mark)):
            return True
        if b.clock.time() >= end:
            return False
        b.sleep(0.1)


def _remove(b: Bot, name: str, pid: int) -> bool:
    mark = chat_mark(b)
    if b.choose(REMOVE, row=r"^\s*%s;%d\b" % (re.escape(name), pid)) is None:
        return False
    if b.wait_dialog(3.0) is None or b.choose(CONFIRM) is None:
        b.close_dialogs()
        return False
    gone = re.compile(r"^\s*%s has been removed from the team" % re.escape(name), re.I)
    end = b.clock.time() + JOIN_WAIT
    while b.clock.time() < end:
        if any(gone.match(line) for line in game_lines(b, mark)):
            return True
        b.sleep(0.25)
    return False


def remove_offline(b: Bot) -> list[str]:
    """Ask about each member in turn and remove those the game says nothing about - twice, the
    second time with twice the wait, so a slow answer never costs anyone their place. The list is
    opened again after each removal (the game closes it). The names removed."""
    lead = state(b)
    lead.offline_at = b.clock.time()
    me = int(b.me().get("id", -1))
    checked: set[int] = set()
    removed: list[str] = []
    for _opening in range(MOST_OPENINGS):
        got = _member_list(b)
        if got is None:
            break
        top, lag = got
        todo = [m for m in members(b, top) if m[2] != me and m[2] not in checked]
        gone = _first_offline(b, top, todo, answer_wait(lag), checked) if todo else None
        if gone is None:
            b.close_dialogs()
            break
        name, pid = gone
        if _remove(b, name, pid):
            removed.append(name)
            b.log(_("removed {name} from the team: offline", name=name), speak=True)
        else:
            b.close_dialogs()
            b.log(_("{name} seems offline, and the game didn't remove them from the team", name=name))
    return removed


def _first_offline(b: Bot, top: dict[str, Any], todo: list[tuple[int, str, int]], wait: float,
                   checked: set[int]) -> tuple[str, int] | None:
    for row, name, pid in todo:
        checked.add(pid)
        here = online(b, top, row, name, pid, wait)
        if here is False:
            top = b.dialog().get("top") or top
            here = online(b, top, row, name, pid, wait * 2)
        if here is None:
            b.log(_("team: couldn't move to {name} in the member list", name=name))
            return None
        if not here:
            return name, pid
        top = b.dialog().get("top") or top
    return None


def offline_due(b: Bot) -> bool:
    if not removing(b) or b.p.get("team_offline_town_only", False):
        return False
    last = state(b).offline_at
    every = max(1, int(b.p.get("team_offline_every", 10))) * 60
    return last is None or b.clock.time() - last >= every


# ------------------------------------------------------------------ in the run
def on(b: Bot) -> bool:
    return bool(b.p.get("team_accept", False)) and not state(b).off


def removing(b: Bot) -> bool:
    return bool(b.p.get("team_remove_offline", False)) and not state(b).off


def start_farm(b: Bot) -> None:
    """A farm begins: listen for requests, take the ones that came while we weren't farming, and
    look for members who went offline."""
    if on(b):
        listen(b)
        take_requests(b)
    if removing(b):
        remove_offline(b)


def after_trip(b: Bot) -> None:
    """In town on a trip of our own: the "only through a town" offline check."""
    if removing(b) and b.p.get("team_offline_town_only", False):
        remove_offline(b)


def between_fights(b: Bot) -> bool:
    """The farm loop between fights, with nobody hitting us: a request was heard, or the offline
    check is due. True when the menus were used (the loop looks around again)."""
    if not (b.p.get("team_accept", False) or b.p.get("team_remove_offline", False)):
        return False
    lead = state(b)
    if b.clock.time() - lead.looked_at < LOOK_EVERY or b.attackers():
        return False
    lead.looked_at = b.clock.time()
    watch(b)
    if on(b) and heard_request(b):
        take_requests(b)
        return True
    if offline_due(b):
        remove_offline(b)
        return True
    return False


# ------------------------------------------------------------------ handing the team over
def heir(b: Bot, found: list[tuple[int, str, int]]) -> tuple[str, int] | None:
    """The first of team_hand_to (names or ids, in the player's order) who is in the team."""
    wanted = cast(list[object], b.p.get("team_hand_to", []) or [])
    for want in wanted:
        for _row, name, pid in found:
            if str(want).strip().lower() in (name.lower(), str(pid)):
                return name, pid
    return None


def hand_over(b: Bot) -> str | None:
    """The run is over: the team goes to the first player of the list who is in it ("Transfer team
    leader", confirmed; "<name> has become the team leader" for everyone). The new leader's name."""
    got = _member_list(b)
    if got is None:
        return None
    top, _lag = got
    me = int(b.me().get("id", -1))
    found = [m for m in members(b, top) if m[2] != me]
    to = heir(b, found)
    b.close_dialogs()
    if to is None:
        b.log(_("nobody to hand the team to is in it: it stays with this character"), speak=True)
        return None
    mark = chat_mark(b)
    ok, said = team.make_leader(b, to[0])
    b.log(said, speak=True)
    if not ok:
        return None
    state(b).handed = True
    became = re.compile(r"^\s*%s has become the team leader" % re.escape(to[0]), re.I)
    end = b.clock.time() + JOIN_WAIT
    while b.clock.time() < end:
        if any(became.match(line) for line in game_lines(b, mark)):
            return to[0]
        b.sleep(0.25)
    b.log(_("the game didn't say {name} leads the team now", name=to[0]))
    return None


def at_end(b: Bot) -> None:
    """The run's last gestures, on its last map: the team handed over, or (still ours) managed
    from the town until the player stops the run."""
    if b.p.get("team_hand_over", False) and hand_over(b):
        return
    if b.p.get("team_stay_in_town", False) and not state(b).off:
        if in_town(b):
            stay(b)
        else:
            b.log(_("the run ended outside a town: not staying to manage the team"), speak=True)


# ------------------------------------------------------------------ the team at the start
def in_town(b: Bot) -> bool:
    me = b.me()
    return atlas.in_town(b.map_id(), float(me.get("x", 0)), float(me.get("y", 0)))


def _confirm_and_hear(b: Bot, said: re.Pattern[str]) -> bool:
    """Confirm the question the last choice asked, then hear the game's line about it."""
    mark = chat_mark(b)
    if b.wait_dialog(3.0) is None or b.choose(CONFIRM) is None:
        b.close_dialogs()
        return False
    end = b.clock.time() + JOIN_WAIT
    while b.clock.time() < end:
        if any(said.search(line) for line in game_lines(b, mark)):
            return True
        b.sleep(0.25)
    return False


def leave_team(b: Bot, team_name: str) -> bool:
    """Leave the team another player leads ("quit", confirmed; "<us> has quit the team")."""
    if b.choose(QUIT, row=r"^\s*%s\s*$" % re.escape(team_name)) is None:
        return False
    if not _confirm_and_hear(b, QUIT_DONE):
        b.log(_("the game didn't say this character left {team}", team=team_name), speak=True)
        return False
    b.log(_("left {team}, which another player leads", team=team_name), speak=True)
    return True


def disband(b: Bot, team_name: str) -> bool:
    if b.choose(DISBAND, row=r"^\s*%s\s*$" % re.escape(team_name)) is None:
        return False
    if not _confirm_and_hear(b, DISBANDED):
        b.log(_("the game didn't say {team} was disbanded", team=team_name), speak=True)
        return False
    state(b).own_disband = True
    b.log(_("disbanded {team}: the team is to be called {name}", team=team_name, name=b.p.get("team_name", "")))
    return True


def create(b: Bot) -> bool:
    """Make the team with the profile's name ("Create a team", the name typed in its box; "<us>
    set up a team named <name>"). Anywhere: the game doesn't ask for a town (said by a player,
    2026-11-21; the script waited for one)."""
    name = str(b.p.get("team_name", "")).strip()
    top = open_action_menu(b)
    if top is None or b.choose(NO_TEAM) is None:
        b.close_dialogs()
        return False
    from . import town            # (town needs the Bot, which imports this module)
    mark = chat_mark(b)
    if not town.type_text(b, name):
        b.close_dialogs()
        b.log(_("the game didn't take the team name {name}", name=name), speak=True)
        return False
    made = re.compile(r"set up a team named %s\s*$" % re.escape(name), re.I)
    end = b.clock.time() + JOIN_WAIT
    while b.clock.time() < end:
        if any(made.search(line) for line in game_lines(b, mark)):
            b.log(_("made the team {name}", name=name), speak=True)
            return True
        b.sleep(0.25)
    b.log(_("the game didn't say the team {name} was made", name=name), speak=True)
    return False


def at_start(b: Bot) -> None:
    """Before a farming run sets out: out of a team another player leads, into our own team with
    its name - as the Team tab says. Nothing at all when none of it is ticked."""
    p = b.p
    if not (p.get("team_create", False) or p.get("team_remake", False) or p.get("team_leave_others", False)):
        return
    top = open_action_menu(b)
    if top is None:
        return
    team = team_entry(b, top)
    if team is not None and not leads(b, top, team[0]):
        if not p.get("team_leave_others", False):
            b.close_dialogs()
            b.log(_("this character is in {team}, which another player leads", team=team[1]))
            return
        if not leave_team(b, team[1]):
            return
        team = None
    elif team is not None and _remade(b, team[1]):
        team = None
        b.sleep(AFTER_DISBAND)       # the game needs a moment before "Create a team" comes back
    elif team is not None:
        b.close_dialogs()
    if team is None and (p.get("team_create", False) or p.get("team_remake", False)):
        create(b)


def _remade(b: Bot, team_name: str) -> bool:
    """Our team has another name and the profile says to make it again: disbanded."""
    want = str(b.p.get("team_name", "")).strip()
    if not b.p.get("team_remake", False) or not want or team_name.lower() == ("%s team" % want).lower():
        return False
    return disband(b, team_name)


# ------------------------------------------------------------------ staying in town
def stay(b: Bot) -> None:
    """Manage the team until the player stops the run: take requests as they come and remove
    members who went offline, every so many minutes (the Team tab's, five at least)."""
    if not (on(b) or removing(b)):
        b.log(_("nothing to manage: tick taking requests or removing offline members on the Team tab"),
              speak=True)
        return
    b.log(_("staying here managing the team until you stop the run"), speak=True)
    if on(b):
        listen(b)
        take_requests(b)
    every = max(STAY_EVERY_LEAST, int(b.p.get("team_offline_every", 10))) * 60
    if removing(b):
        remove_offline(b)
    while not state(b).off:
        watch(b)
        if on(b) and heard_request(b):
            take_requests(b)
        last = state(b).offline_at
        if removing(b) and (last is None or b.clock.time() - last >= every):
            remove_offline(b)
        b.sleep(STAY_LOOK)
