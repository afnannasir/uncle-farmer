"""Optional team hunting on the bot's farming thread.

Player reports use the existing team channel. Only the game's own leader-change
line can rally the team after a transfer. The leader shortcut verifies the
destination, and the team menu supplies member IDs for transfers.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..chat import MOST
from ..link import AgentError
from . import follow, team, teamlead

if TYPE_CHECKING:
    from .bot import Bot

NOTICE = "Team Monster Hunting is enabled. Subscribe to Team Hunting in the Team tab."
STOPPED = "Team Monster Hunting is disabled."
RULES = re.compile(re.escape(NOTICE) + r" \[(\d{1,10}),(\d{1,3}),([01])\] (.+)")
DEPARTED = re.compile(r"^(.+?) has (?:quit|been removed from) the team$", re.I)
TEAM_CHANNEL = 12           # GameChat.java: inbound channel is three times the outgoing one
LEADER_CHANNEL = 46         # live team transfers, received by both clients via the agent's chat feed
LEADER_KEY = "shift+'"      # live: the leader's fresh position arrives in the agent's `team` reply
CHECK_EVERY = 1.0
DIRECT_AGENT = 79
ROSTER_RETRY = 10.0
REPORT_FOR = 75.0           # includes the game's approximately 50-second teleport cooldown
HANDOFF_FOR = 12.0
WELCOME_FOR = 90.0           # an opt-in client can start after the team's welcome reached the game
MAX_THRESHOLD = 999
MAX_MESSAGE = MOST - len(NOTICE) - 22


@dataclass(frozen=True)
class Roster:
    name: str
    leader: str
    players: dict[str, int]

    def member(self, name: str) -> str | None:
        return next((player for player in self.players if player.casefold() == name.casefold()), None)


@dataclass(frozen=True)
class Rules:
    original: int
    threshold: int
    return_leader: bool
    message: str

    def welcome(self) -> str:
        return f"{NOTICE} [{self.original},{self.threshold},{int(self.return_leader)}] {self.message}"


@dataclass(frozen=True)
class TimedPlayer:
    name: str
    until: float


@dataclass(frozen=True)
class Report:
    serial: int
    until: float


@dataclass(frozen=True)
class Change(TimedPlayer):
    serial: int


@dataclass(frozen=True)
class PendingWelcome:
    rules: Rules
    until: float
    replay_after: int


@dataclass
class Hunt:
    cursor: int | None = None
    next_check: float = 0.0
    next_chat: float = 0.0
    next_probe: float = 0.0
    roster: Roster | None = None
    rules: Rules | None = None
    pending_welcomes: dict[str, PendingWelcome] = field(default_factory=dict[str, PendingWelcome])
    welcome_retry_at: float = 0.0
    leader: str = ""       # initialized from the menu, updated only on a verified game line
    reports: dict[str, Report] = field(default_factory=dict[str, Report])
    unverified_reports: dict[str, Report] = field(default_factory=dict[str, Report])
    changed: Change | None = None
    transfer: TimedPlayer | None = None
    direct_pending: bool = False
    direct_disabled: bool = False
    rally: TimedPlayer | None = None
    pack: set[int] = field(default_factory=set[int])
    pack_map: int | None = None
    quiet_since: float | None = None
    working: bool = False
    arrived: bool = False
    welcome: bool = False


def profile_rules(profile: dict[str, Any], pid: int) -> Rules | None:
    """Return a valid local configuration; profiles edited by hand must obey the same bounds."""
    threshold = profile.get("team_hunt_threshold")
    message = str(profile.get("team_hunt_message") or "").strip()
    if (type(threshold) is not int or not 0 <= threshold <= MAX_THRESHOLD or
            not message or len(message) > MAX_MESSAGE or "\r" in message or "\n" in message or
            message.startswith(NOTICE) or message == STOPPED):
        return None
    return Rules(pid, threshold, bool(profile.get("team_hunt_return")), message)


def read_roster(b: Bot) -> Roster | None:
    """Read team membership. A follower identifies the leader with the shortcut."""
    action = teamlead.open_action_menu(b)
    entry = teamlead.team_entry(b, action) if action else None
    if entry is None or action is None:
        b.close_dialogs()
        return None
    index, name = entry
    leads = teamlead.leads(b, action, index)
    try:
        if b.choose(r"^\s*" + re.escape(name) + r"\s*$") is None:
            return None
        top = (b.wait_dialog(3.0) or {}).get("top")
        if not top:
            return None
        players = {player: pid for _, player, pid in teamlead.members(b, top)}
        sig = top.get("sig")
        if leads and getattr(b, "agent_version", 0) >= DIRECT_AGENT and type(sig) is int:
            try:
                b.a.send(f"teamcache {sig}")
            except AgentError:
                pass
    finally:
        b.close_dialogs()
    me = b.me().get("id")
    mine = next((player for player, pid in players.items() if pid == me), None)
    if mine is None:
        return None
    roster = Roster(name, mine, players)
    # The leader's own shortcut is silent. The leader-only Disband action proves
    # we can lead here, without reading any leader marker in the member list.
    leader = mine if leads else _verified_name(roster, _leader_reply(b))
    return Roster(name, leader, players) if leader else None


def _leader_reply(b: Bot) -> dict[str, Any] | None:
    """Shift+Quote asks for the leader. Ignore cached replies left by any earlier team key."""
    try:
        before = b.a.json("team").get("now")
        if not isinstance(before, int):
            return None
        b.key(LEADER_KEY)
        deadline = b.clock.time() + 1.2
        while b.clock.time() < deadline:
            b.sleep(0.15)
            batch = b.a.json("team")
            fresh: list[dict[str, Any]] = [member for member in batch.get("members", [])
                                          if batch["now"] - member["age_ms"] > before]
            if fresh:
                return fresh[0] if len(fresh) == 1 else None
    except (AgentError, ValueError):
        return None
    return None


def _verified_name(roster: Roster, reply: dict[str, Any] | None) -> str | None:
    if reply is None or not isinstance(reply.get("name"), str):
        return None
    name = roster.member(reply["name"])
    return name if name is not None and roster.players[name] == reply.get("id") else None


def _locked(b: Bot, snap: dict[str, Any], *, fresh: bool = False) -> bool:
    """Keep fighting anything still attacking us or already engaged by us or our pet."""
    if b.in_fight:
        return True
    live: set[int] = set()
    dead = set(b.kill_tracker.died)
    for monster in snap["entities"]:
        if monster.get("kind") == "monster" and isinstance(monster.get("hp"), (int, float)):
            (live if monster["hp"] > 0 else dead).add(monster["id"])
    tracker = b.kill_tracker
    attackers = b.fresh_attackers() if fresh else b.attackers()
    return (bool(attackers - (dead - live)) or not live.isdisjoint(tracker.hit)
            or not live.isdisjoint(tracker.pet_struck))


def _send(b: Bot, text: str) -> bool:
    try:
        return b.a.send("say 4 " + text) == "sent"
    except AgentError:
        return False


def _refresh(b: Bot, h: Hunt) -> Roster | None:
    if b.in_fight or b.attackers():
        return None
    roster = read_roster(b)
    if roster is None:
        return None
    if h.roster is not None and roster.name != h.roster.name:
        _reset(h)
        return None
    h.roster = roster
    return roster


def _reset(h: Hunt) -> None:
    h.rules = h.roster = None
    h.pending_welcomes.clear()
    h.leader = ""
    h.reports.clear()
    h.unverified_reports.clear()
    h.changed = h.transfer = h.rally = None
    h.direct_pending = h.direct_disabled = False
    h.pack.clear()
    h.pack_map = h.quiet_since = None
    h.working = h.arrived = h.welcome = False


def _queue_welcome(b: Bot, h: Hunt, sender: str, text: str, until: float, replay_after: int) -> None:
    match = RULES.fullmatch(text)
    if match is None or len(match[4]) > MAX_MESSAGE:
        return
    rules = Rules(int(match[1]), int(match[2]), match[3] == "1", match[4])
    if rules.original == b.me().get("id") and not b.p.get("team_hunt"):
        return
    if h.rules == rules or (h.rules is not None and h.rules.original != rules.original):
        return
    key = sender.casefold()
    pending = h.pending_welcomes.get(key)
    if pending and pending.rules == rules and pending.until > b.clock.time():
        return
    if not h.pending_welcomes:
        h.welcome_retry_at = 0.0
    h.pending_welcomes[key] = PendingWelcome(rules, until, replay_after)


def _retry_welcome(b: Bot, h: Hunt, now: float) -> None:
    if not h.pending_welcomes:
        return
    h.pending_welcomes = {name: welcome for name, welcome in h.pending_welcomes.items() if now < welcome.until}
    if not h.pending_welcomes or now < h.welcome_retry_at or b.in_fight or b.attackers():
        return
    h.welcome_retry_at = now + ROSTER_RETRY
    roster = _refresh(b, h)
    if roster is None:
        return
    pending = h.pending_welcomes.get(roster.leader.casefold())
    h.pending_welcomes.clear()
    if pending is None or b.clock.time() >= pending.until:
        return
    rules = pending.rules
    if rules.original not in roster.players.values():
        return
    starting = h.rules is None
    h.rules = rules
    h.leader = roster.leader
    # Recover any still-current reports that arrived before we could verify the
    # welcome. The first read of a run still excludes older encounters.
    if starting and h.cursor is not None and pending.replay_after < h.cursor:
        _read_chat(b, h, b.clock.time(), after=pending.replay_after)


def _player_line(b: Bot, h: Hunt, line: dict[str, Any], sender: str, text: str,
                 now: float, age: float, history_end: int | None) -> None:
    if line.get("channel") != TEAM_CHANNEL or line.get("voice"):
        return
    if text.startswith(NOTICE):
        after = int(line["n"]) if history_end is None else history_end
        _queue_welcome(b, h, sender, text, now + WELCOME_FOR - age, after)
        return
    if text == STOPPED:
        h.pending_welcomes.pop(sender.casefold(), None)
    rules, roster = h.rules, h.roster
    if rules is None or roster is None:
        return
    member = roster.member(sender)
    if text == STOPPED and member is not None and roster.players[member] == rules.original:
        _reset(h)
    elif text == rules.message:
        report = Report(int(line["n"]), now + REPORT_FOR - age)
        if member is None:
            h.unverified_reports[sender] = report
        else:
            _accept_report(b, h, member, report)


def _accept_report(b: Bot, h: Hunt, member: str, report: Report) -> None:
    old = h.reports.get(member)
    if old and old.until >= report.until - HANDOFF_FOR:
        return
    if h.changed is None and member == h.leader and member != b.me().get("name"):
        h.rally = TimedPlayer(member, report.until)
    h.reports[member] = report


def _check_report_members(b: Bot, h: Hunt, now: float) -> None:
    if not h.unverified_reports or now < h.next_probe or b.in_fight or b.attackers():
        return
    h.next_probe = now + ROSTER_RETRY
    roster = _refresh(b, h)
    if roster is None:
        return
    for sender, report in h.unverified_reports.items():
        member = roster.member(sender)
        if member is not None and report.until > now:
            _accept_report(b, h, member, report)
    h.unverified_reports.clear()


def _game_line(b: Bot, h: Hunt, line: dict[str, Any], text: str, now: float, age: float) -> None:
    if teamlead.DISBANDED.fullmatch(text):
        _reset(h)
        return
    departed = DEPARTED.fullmatch(text)
    if departed:
        h.pending_welcomes.pop(departed[1].casefold(), None)
        for sender in tuple(h.unverified_reports):
            if sender.casefold() == departed[1].casefold():
                del h.unverified_reports[sender]
        roster = h.roster
        member = roster.member(departed[1]) if roster else None
        if departed[1].casefold() == str(b.me().get("name")).casefold():
            _reset(h)
        elif member is not None:
            h.reports.pop(member, None)
            if h.rally and h.rally.name == member:
                h.rally = None
        return
    joined = teamlead.JOINED.fullmatch(text)
    if joined:
        if joined[1].casefold() == str(b.me().get("name")).casefold():
            _reset(h)
        elif h.rules is not None:
            h.welcome = True
    changed = teamlead.NEW_LEADER.fullmatch(text) if line.get("channel") == LEADER_CHANNEL else None
    last = h.changed.name if h.changed else h.leader
    if changed and h.rules is not None and changed[1].casefold() != last.casefold() and age < REPORT_FOR:
        # A newer game line replaces an older one even while the roster is catching up.
        h.changed = Change(changed[1].strip(), now + REPORT_FOR - age, int(line["n"]))


def _read_chat(b: Bot, h: Hunt, now: float, *, after: int | None = None) -> None:
    previous = h.cursor
    cursor = previous if after is None else after
    try:
        batch = b.a.json(f"chat {cursor or 0}")
    except (AgentError, ValueError):
        return
    initial = previous is None
    h.cursor = int(batch.get("last", previous or 0))
    for line in batch.get("chat", []):
        text = str(line.get("text") or "").strip()
        sender = str(line.get("name") or "").strip()
        age = (line.get("age_ms") or 0) / 1000.0
        if after is not None and text.startswith(NOTICE) and int(line["n"]) <= (previous or 0):
            continue
        if initial:
            lifecycle = not sender and any(pattern.fullmatch(text) for pattern in
                                           (teamlead.DISBANDED, DEPARTED, teamlead.JOINED))
            if not (text.startswith(NOTICE) or text == STOPPED or lifecycle):
                continue
        if sender and age <= (WELCOME_FOR if text.startswith(NOTICE) else REPORT_FOR):
            _player_line(b, h, line, sender, text, now, age, h.cursor if initial else None)
        elif not sender and age <= WELCOME_FOR:
            _game_line(b, h, line, text, now, age)


def _confirm(b: Bot, h: Hunt, now: float, me: str) -> dict[str, Any] | None:
    changed = h.changed
    if changed is None:
        return None
    if now >= changed.until:
        h.changed = None
        return None
    roster = h.roster
    new = roster.member(changed.name) if roster else None
    if roster is None or new is None:
        return None
    # The game's dedicated change notice can name us as leader without a lookup.
    reply = _leader_reply(b) if new != me else None
    if new != me and _verified_name(roster, reply) != new:
        return None
    h.rally = None
    report = h.reports.get(new)
    if report and report.until > now and report.serial <= changed.serial:
        if new == me:
            h.working = bool(h.pack)
        else:
            h.rally = TimedPlayer(new, report.until)
    h.leader = new
    h.changed = h.transfer = None
    h.reports = {member: r for member, r in h.reports.items() if r.serial > changed.serial}
    if new != me:
        h.working = False
    return reply


def _recover(h: Hunt, roster: Roster, me: str) -> None:
    """The menu can restore coordination after a missed line. It never authorizes a rally."""
    if roster.leader != h.leader:
        h.leader = roster.leader
        h.changed = h.transfer = h.rally = None
        h.reports.clear()
        h.working = h.leader == me and bool(h.pack)


def _check_missed_change(b: Bot, h: Hunt, now: float, me: str) -> None:
    if h.changed or (h.transfer and now < h.transfer.until) or not h.reports or now < h.next_probe:
        return
    if any(report.until > now + REPORT_FOR - HANDOFF_FOR for report in h.reports.values()):
        return
    h.next_probe = now + ROSTER_RETRY
    roster = h.roster
    leader = _verified_name(roster, _leader_reply(b)) if roster else None
    if roster is not None and leader is not None:
        _recover(h, Roster(roster.name, leader, roster.players), me)
    elif me in h.reports:
        # A missing change notice can leave a finder in charge. Confirm our own
        # authority before taking a new transfer, since the shortcut won't name us.
        fresh = _refresh(b, h)
        if fresh and fresh.leader == me:
            _recover(h, fresh, me)


def _teleport(b: Bot, h: Hunt, now: float, me: str, reply: dict[str, Any] | None) -> bool:
    rally = h.rally
    if rally is None or h.changed is not None:
        return False
    if now >= rally.until or rally.name == me:
        h.rally = None
        return False
    timing = b.__dict__.get("_teleport", {})
    if now < timing.get("next", 0):
        return False
    roster = h.roster
    if roster is None:
        return False
    verified = reply if reply is not None else _leader_reply(b)
    if _verified_name(roster, verified) != rally.name or _locked(b, b.snap(), fresh=True):
        return False
    h.rally = None
    moved = follow.teleport(b, rally.name, verified, verified_leader=True)
    timing = b.__dict__.get("_teleport", {})
    if not moved and timing.get("server_cooldown") and timing.get("next", 0) < rally.until:
        h.rally = rally
    h.arrived = moved
    return moved


def _transfer(b: Bot, h: Hunt, target: str, me: str) -> bool:
    roster = _refresh(b, h)
    if roster is None or roster.leader != me or h.rules is None:
        return False
    if target not in roster.players:
        h.reports.pop(target, None)
        return False
    if _send_direct_transfer(b, h, target, roster.players[target]):
        return True
    # Keep the record until the matching game event (or until its verification times out).
    ok, said = team.make_leader(b, target, member_id=roster.players[target])
    if ok:
        _track_transfer(b, h, target)
    else:
        b.log(said)
        h.reports.pop(target, None)
    return ok


def _track_transfer(b: Bot, h: Hunt, target: str, *, direct: bool = False) -> None:
    until = b.clock.time() + HANDOFF_FOR
    h.transfer = TimedPlayer(target, until)
    h.direct_pending = direct
    teamlead.note_hunt_handoff(b, target, until)


def _can_transfer(h: Hunt, me: str, now: float) -> bool:
    if h.leader != me or h.changed is not None:
        return False
    if h.transfer is not None:
        if now < h.transfer.until:
            return False
        if h.direct_pending:
            # A request without a matching notice might have been refused. A later menu
            # fallback verifies that we still lead before trying it for the live report.
            h.direct_disabled = True
        else:
            h.reports.pop(h.transfer.name, None)
        h.transfer = None
    return True


def _next_finder(h: Hunt, me: str, now: float) -> str | None:
    return next((name for name, report in h.reports.items() if name != me and report.until > now), None)


def _direct_transfer(b: Bot, h: Hunt, me: str, now: float) -> None:
    roster = h.roster
    if getattr(b, "agent_version", 0) < DIRECT_AGENT or roster is None or not _can_transfer(h, me, now):
        return
    target = _next_finder(h, me, now)
    if target is not None and target in roster.players:
        _send_direct_transfer(b, h, target, roster.players[target])


def _send_direct_transfer(b: Bot, h: Hunt, target: str, member_id: int) -> bool:
    if h.direct_disabled or getattr(b, "agent_version", 0) < DIRECT_AGENT:
        return False
    try:
        result = b.a.send(f"teamtransfer {member_id}")
    except AgentError:
        result = "uncertain"
    if result not in ("sent", "uncertain"):
        return False
    _track_transfer(b, h, target, direct=True)  # uncertain: the request may have reached the game
    return True


def _monsters(b: Bot, h: Hunt, snap: dict[str, Any], mobs: Sequence[dict[str, Any]], now: float) -> set[int]:
    ids = {int(m["id"]) for m in mobs}
    if h.arrived:
        h.pack, h.pack_map, h.arrived = ids, b.map_id(), False
        return set()
    if h.pack:
        here = b.map_id()
        h.pack.difference_update(b.kill_tracker.died)
        living = {e["id"] for e in snap["entities"] if e.get("kind") == "monster" and e.get("hp") != 0}
        if here == h.pack_map and h.pack.intersection(living):
            h.quiet_since = None
        elif h.quiet_since is None:
            h.quiet_since = now
        elif now - h.quiet_since >= CHECK_EVERY:
            h.pack.clear()
    return ids


def _lead(b: Bot, h: Hunt, rules: Rules, now: float, me: str) -> bool:
    if not _can_transfer(h, me, now):
        return False
    target = _next_finder(h, me, now)
    if target is not None:
        return _transfer(b, h, target, me)
    if h.working and not h.pack:
        h.working = False
        if rules.return_leader and h.roster is not None:
            original = next((name for name, pid in h.roster.players.items() if pid == rules.original), None)
            if original and original != me:
                return _transfer(b, h, original, me)
    return False


def _poll(b: Bot, h: Hunt, me: str, now: float) -> None:
    if now < h.next_chat:
        return
    h.next_chat = now + CHECK_EVERY
    _read_chat(b, h, now)
    h.reports = {name: report for name, report in h.reports.items() if report.until > now}
    h.unverified_reports = {name: report for name, report in h.unverified_reports.items() if report.until > now}
    if h.rules is None:
        return
    if h.changed and h.changed.name.casefold() == me.casefold():
        _confirm(b, h, now, me)     # a notice naming us needs no menu or key
    _direct_transfer(b, h, me, now)


def observe(b: Bot, me: dict[str, Any]) -> None:
    """On the farming thread during combat or a walk: read and queue, or send a direct handoff."""
    h = getattr(b, "team_hunt_state", None)
    name = me.get("name")
    if b.in_instance or not isinstance(h, Hunt) or not isinstance(name, str) or not name:
        return
    enabled = bool(b.p.get("team_hunt"))
    if not enabled and (not b.p.get("team_hunt_subscribe") or (h.rules and h.rules.original == me.get("id"))):
        return
    _poll(b, h, name, b.clock.time())


def interrupt_patrol(b: Bot, snap: dict[str, Any]) -> bool:
    """Give pending team work to the farm loop, which fights its attackers before a rally."""
    h = getattr(b, "team_hunt_state", None)
    if b.in_instance or not isinstance(h, Hunt) or h.rules is None:
        return False
    me = snap["self"].get("name")
    now = b.clock.time()
    if not isinstance(me, str) or now < h.next_check or not (b.p.get("team_hunt") or b.p.get("team_hunt_subscribe")):
        return False
    if h.rules.original == snap["self"].get("id") and not b.p.get("team_hunt"):
        return False
    return ((h.changed is not None and now < h.changed.until)
            or (h.rally is not None and now < h.rally.until and now >= b.__dict__.get("_teleport", {}).get("next", 0))
            or (_next_finder(h, me, now) is not None and _can_transfer(h, me, now)))


def _session(b: Bot, snap: dict[str, Any]) -> tuple[Hunt, str] | None:
    enabled = bool(b.p.get("team_hunt"))
    subscribed = bool(b.p.get("team_hunt_subscribe"))
    current = b.__dict__.get("team_hunt_state")
    if b.in_instance or getattr(b, "agent_version", 0) < 32:
        return None
    if not (enabled or subscribed) and current is None:
        return None
    me = str(snap["self"].get("name") or "")
    pid = snap["self"].get("id")
    if not me or not isinstance(pid, int) or b.in_fight:
        return None
    h = current if isinstance(current, Hunt) else Hunt()
    b.__dict__["team_hunt_state"] = h
    now = b.clock.time()
    if now < h.next_check and getattr(b, "last_combat", 0.0) <= h.next_check - CHECK_EVERY:
        return None
    h.next_check = now + CHECK_EVERY
    if h.rules and h.rules.original == pid and not enabled:
        if _refresh(b, h) is not None:
            _send(b, STOPPED)
        _reset(h)
    if not (enabled or subscribed):
        b.__dict__.pop("team_hunt_state", None)
        return None

    _poll(b, h, me, now)
    _retry_welcome(b, h, now)
    _check_report_members(b, h, now)
    if enabled and h.rules is None and now >= h.next_probe:
        h.next_probe = now + ROSTER_RETRY
        rules = profile_rules(b.p, pid)
        roster = _refresh(b, h) if rules else None
        if roster and roster.leader == me:
            h.rules, h.leader, h.welcome = rules, me, True
    if h.rules is not None and (subscribed or (enabled and h.rules.original == pid)):
        return h, me
    return None


def _scout(b: Bot, h: Hunt, rules: Rules, ids: set[int], now: float, me: str) -> None:
    if h.pack or h.changed is not None or h.rally is not None or len(ids) <= rules.threshold:
        return
    # A report uses the existing team channel. Transfers verify the member ID again.
    roster = h.roster
    if roster is None or h.rules is None or me not in roster.players:
        return
    if _send(b, rules.message):
        h.pack, h.pack_map, h.quiet_since = ids, b.map_id(), None
        h.working = h.leader == me
        h.reports[me] = Report(h.cursor or 0, now + REPORT_FOR)


def tick(b: Bot, snap: dict[str, Any], targets: Sequence[dict[str, Any]]) -> bool:
    """Read the team's messages and act between fights. True when the farm needs a new snapshot."""
    session = _session(b, snap)
    if session is None:
        return False
    h, me = session
    rules = h.rules
    if rules is None:
        return False
    now = b.clock.time()

    locked = _locked(b, snap)
    reply = _confirm(b, h, now, me) if not locked else None
    if not locked:
        _check_missed_change(b, h, now, me)
    if h.welcome and h.leader == me and h.changed is None:
        roster = _refresh(b, h)
        if roster is not None and roster.leader == me and h.rules is not None:
            h.welcome = not _send(b, rules.welcome())
    if not locked and _teleport(b, h, now, me, reply):
        return True
    ids = _monsters(b, h, snap, targets, now)
    if not locked and _lead(b, h, rules, now, me):
        return True
    _scout(b, h, rules, ids, now, me)
    return False
