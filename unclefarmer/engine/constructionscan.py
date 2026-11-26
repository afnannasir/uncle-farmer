"""Tools, Construction scanner: how far the gang's constructions are, for its elders to pass on.

The bot reads, repairing and building nothing, the Magic Cannon and Supply Camp under construction
in the inner hall ("Level 5 Magic Cannon Construction; Completed 62.0%") and every Crystal Turret and
Wall barricade of the gang's own highlands under Occupied Highlands ("the completeness is 97.5%"), and
of those it took from other gangs too when asked (setting construction_taken, or `taken=yes`). The
highlands can come first, in the walk and in the report (construction_highlands_first, `highlands_first=yes`). The result
is kept in profiles/_construction_scan.json, so it can be read, copied or published afterwards.

Publishing takes an elder's rights. The Action menu's gang line has "send system message", "send
broadcast" (the same with a sound) and "send an announcement", next to "transfer the gang" and
"disband": only those three exact entries are ever chosen. Each opens a text box. The game says
"sent" for an announcement; the other two come back in the chat under our own name (seen live
2026-11-18: channels 10 and 43).

The notices key (W_NOTICE) -> "gang announcement" lists the announcements: a title row, then a row
"<author> issued at <date>" with "delete" beside it, then "Are you sure ...?" -> Confirm. An elder
is offered "delete" on everyone's, so a new report replaces only this character's own old ones.

An announcement can go a line to each camp and highland (setting construction_lines, or `lines=yes`):
the announcement box keeps a pasted line break (agent 80's `typepaste`; typing one does nothing, and
Shift+Enter is no line break there), while a broadcast and a system message turn it into a space (seen
live 2026-11-25), so only an announcement is laid out that way.

A schedule (or `construction_scan auto=<kind>`) scans and publishes at once. It stays quiet when
everything is at 100% and when the line is the one this character last sent that way.
"""

from __future__ import annotations

import os
import re
import time
from typing import Any

from .. import gamekeys, jsonio, paths, settings
from ..i18n import _
from . import chores, town
from .bot import Bot, Stopped

TITLE = "Gang constructions, least complete first"
OLD_TITLES = (TITLE, "Highland components most damaged to least")     # our earlier reports too
ALL_FINE = "All scanned constructions are at 100%."
FILE = "_construction_scan.json"
MOST_TEXT = 180               # characters sent: no limit is known, and the least complete come first
OLD_REPORTS_MOST = 5          # old reports of ours removed in one go
SHORT = {"Crystal Turret": "turret", "Wall barricade": "wall"}
ENTRIES = {"announcement": r"^\s*send an announcement\s*$", "broadcast": r"^\s*send broadcast\s*$",
           "system": r"^\s*send system message\s*$"}
ANNOUNCEMENTS = r"(?i)^\s*gang announcement"
DELETE = r"(?i)^\s*delete\s*$"
SENT = re.compile(r"^\s*sent\s*[.!]?\s*$", re.I)
SENT_WAIT = 3.0               # seconds the game gets to show that a message went out

Row = tuple[str, float | None]      # (what it is, percent; None: couldn't be read)


def _path() -> str:
    return os.path.join(paths.PROFILES, FILE)


def load() -> dict[str, Any] | None:
    """The last report kept, or None."""
    try:
        got: object = jsonio.read(_path(), None, encoding="utf-8")
    except (OSError, ValueError):
        return None
    return got if jsonio.is_object(got) and got.get("rows") is not None else None


def _is_highland(what: str) -> bool:
    return re.match(r"^highland \d+ ", what) is not None


def _least_first(report: dict[str, Any]) -> list[tuple[str, float]]:
    """The rows read, the least complete first; the highlands before the camps when the report was
    scanned that way (highlands_first)."""
    rows = [(str(r[0]), float(r[1])) for r in report["rows"] if r[1] is not None]
    if report.get("highlands_first"):
        return sorted(rows, key=lambda r: (not _is_highland(r[0]), r[1], r[0]))
    return sorted(rows, key=lambda r: (r[1], r[0]))


def message(report: dict[str, Any]) -> str:
    """The one line to publish: what isn't finished, the least complete first, within MOST_TEXT."""
    open_rows = [r for r in _least_first(report) if r[1] < 100]
    if not open_rows:
        return ALL_FINE
    text = TITLE + ":"
    for i, (what, percent) in enumerate(open_rows):
        part = "%s %s %.1f%%" % ("" if i == 0 else ";", what, percent)
        if len(text) + len(part) + len(" and %d more" % len(open_rows)) > MOST_TEXT:
            return text + " and %d more" % (len(open_rows) - i)
        text += part
    return text


def message_lines(report: dict[str, Any]) -> list[str]:
    """The same report a line to a place, for an announcement (the game keeps a pasted line break there):
    the title, then each camp, and each highland with its open turret and wall together, the least
    complete first, within MOST_TEXT counting the line breaks. No line ends in a space."""
    open_rows = [r for r in _least_first(report) if r[1] < 100]
    if not open_rows:
        return [ALL_FINE]
    places: dict[str, list[tuple[str, float]]] = {}      # in order of the least complete part of each
    for what, percent in open_rows:
        m = re.match(r"^(highland \d+) (\w+)$", what)
        places.setdefault(m.group(1) if m else what, []).append((m.group(2) if m else "", percent))
    lines = [TITLE + ":"]
    used = len(lines[0])
    for i, (place, parts) in enumerate(places.items()):
        if parts[0][0]:
            ordered = sorted(parts, key=lambda p: list(SHORT.values()).index(p[0]) if p[0] in SHORT.values() else 99)
            line = "%s: %s" % (place, ", ".join("%s %.1f%%" % (name, percent) for name, percent in ordered))
        else:
            line = "%s %.1f%%" % (place, parts[0][1])
        if used + 1 + len(line) + len(" and %d more" % (len(places) - i)) > MOST_TEXT:
            lines.append("and %d more" % (len(places) - i))
            break
        lines.append(line)
        used += 1 + len(line)
    return lines


def details(report: dict[str, Any]) -> str:
    """Everything read, for the window: the least complete first, then what couldn't be read."""
    lines = [_("{what}: {percent}%", what=what, percent="%.1f" % percent) for what, percent in _least_first(report)]
    lines += [_("{what}: couldn't be read", what=str(r[0])) for r in report["rows"] if r[1] is None]
    return "\n".join(lines)


def _read_place(b: Bot, name: str, stand: tuple[float, float] | None = None) -> float | None:
    if stand is not None:
        b.walk_to(stand)
    try:
        if not b.talk(name, None, stand):
            return None
        b.wait_dialog(3.0)
        m = chores.COMPLETE.search(town.menu_text(b))
        return float(m.group(1)) if m else None
    finally:
        b.close_dialogs()


def _camp_label(name: str) -> str:
    return re.sub(r"(?i)\s+construction\s*$", "", name)


def _taken(taken: bool | None) -> bool:
    """Whether highlands taken from other gangs are scanned: `taken` None, the app setting decides."""
    return bool(settings.load_app().get("construction_taken", False)) if taken is None else taken


def _highlands_first(first: bool | None) -> bool:
    """Whether highlands are scanned and listed before the camps: `first` None, the app setting decides."""
    return bool(settings.load_app().get("construction_highlands_first", False)) if first is None else first


def _read_camps(b: Bot) -> list[Row]:
    """The Magic Cannon and Supply Camp under construction, read in the inner hall."""
    chores.inner_hall(b)
    return [(_camp_label(name), _read_place(b, name)) for name in chores.gang_camps(b)]


def _read_highlands(b: Bot, held: bool) -> list[Row]:
    """The turret and wall of each highland, one after the other, back to the inner hall after each.
    Once every highland on the list has been read, no last trip to the Highland Entrance to find
    nothing left (only when the whole list was seen: with more pages, that trip still checks)."""
    rows: list[Row] = []
    seen: set[int] = set()
    whole: set[int] = set()
    while True:
        number = chores.next_highland(b, seen, held, whole)
        if number is None:
            return rows
        seen.add(number)
        try:
            rows += [("highland %d %s" % (number, SHORT[name]), _read_place(b, name, stand))
                     for name, stand in chores.HIGHLAND_SITES]
        finally:
            chores.leave_highland(b)
        if whole and whole <= seen:
            return rows


def scan(b: Bot, taken: bool | None = None, first: bool | None = None) -> dict[str, Any]:
    """Read what the gang is building and every defence of its highlands, and keep the report.
    taken: highlands taken from other gangs too. first: the highlands before the camps, both in the
    walk and in the report. None: the app setting decides."""
    held, highlands_first = _taken(taken), _highlands_first(first)
    if highlands_first:
        rows = _read_highlands(b, held)          # next_highland goes into the inner hall itself
        rows += _read_camps(b)
    else:
        rows = _read_camps(b)
        rows += _read_highlands(b, held)
    if not rows:
        raise Stopped(_("no construction of the gang was found to read"), final=True)
    before = load() or {}
    report: dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M"), "by": _name(b), "rows": rows,
                              "sent": before.get("sent") or {}}
    if highlands_first:
        report["highlands_first"] = True
    jsonio.save(_path(), report, indent=1)
    unfinished = sum(1 for _what, percent in rows if percent is not None and percent < 100)
    b.log(_("construction scanner: {n} places read, {open} under 100%; open Tools, Construction scanner "
            "to read the report", n=len(rows), open=unfinished), speak=True)
    return report


def _name(b: Bot) -> str:
    return settings.profile_name(chores.who(b)) or ""


def _chat_mark(b: Bot) -> int:
    try:
        return int(b.a.json("chat 0").get("last", 0))
    except Exception:              # an agent without it: nothing to compare, never a crash
        return 0


def _went_out(b: Bot, said_from: int, chat_from: int, text: str) -> bool:
    """The game's "sent", or our own line back in the chat."""
    end = b.clock.time() + SENT_WAIT
    while True:
        if any(SENT.search(s) for s in chores.said_since(b, said_from)):
            return True
        lines: list[dict[str, Any]] = b.a.json("chat %d" % chat_from).get("chat", [])
        if any(m.get("name") == _name(b) and str(m.get("text") or "").strip() == text for m in lines):
            return True
        if b.clock.time() >= end:
            return False
        b.sleep(0.25)


def _own_reports(b: Bot, top: dict[str, Any]) -> list[str]:
    """The "<us> issued at <date>" rows of this character's reports, newest first."""
    who = re.escape(_name(b))
    rows: list[list[dict[str, Any] | None]] = top.get("rows", [])

    def first(row: list[dict[str, Any] | None]) -> str:
        return next(iter(b.entry_texts(row[0])), "") if row and row[0] else ""
    return [first(after) for row, after in zip(rows, rows[1:])
            if any(t in first(row) for t in OLD_TITLES) and re.match(who + r"\s+issued at\b", first(after))]


def _drop_old_reports(b: Bot) -> int:
    """This character's older reports, keeping the newest (the one just sent). How many went."""
    gone = 0
    for _try in range(OLD_REPORTS_MOST):
        b.close_dialogs()
        b.key(gamekeys.notice_key())
        if b.wait_dialog(3.0) is None or b.choose(ANNOUNCEMENTS) is None:
            break
        d = b.wait_dialog(3.0)
        own = _own_reports(b, d.get("top") or {}) if d else []
        if len(own) < 2:
            break
        if b.choose(DELETE, row="^%s$" % re.escape(own[-1])) is None:
            break
        chores.confirm(b)
        b.sleep(0.5)
        gone += 1
    b.close_dialogs()
    return gone


def _open_action_menu(b: Bot, kind: str) -> None:
    """The Action menu, open, with this way of publishing in it; else Stopped. Other characters'
    menus differ (only the gang's leader has "disband"): the entry is found by its own name."""
    b.close_dialogs()
    b.key(b.p.get("action_menu_key", "lctrl+enter"))
    d = b.wait_dialog(3.0)
    if d is None or b.find_entry(d.get("top"), ENTRIES[kind]) is None:
        b.close_dialogs()
        raise Stopped(_("this character can't publish that: it takes a gang elder's rights"), final=True)


def _sent_key(b: Bot, kind: str) -> str:
    return "%s|%s" % (_name(b), kind)


def _note_sent(b: Bot, report: dict[str, Any], kind: str, text: str | None) -> None:
    sent: dict[str, str] = report.setdefault("sent", {})
    if text is None:
        sent.pop(_sent_key(b, kind), None)
    else:
        sent[_sent_key(b, kind)] = text
    jsonio.save(_path(), report, indent=1)


def _per_line(b: Bot, kind: str, lines: bool | None) -> bool:
    """Whether this report goes out a line to a place. Only an announcement keeps a line break (a broadcast
    and a system message turn it into a space), so `lines=yes` for those is left unheeded. `lines` None:
    the app setting decides (Tools, Construction scanner)."""
    wanted = settings.load_app().get("construction_lines", False) if lines is None else lines
    if not wanted:
        return False
    if kind != "announcement":
        b.log(_("construction scanner: only a gang announcement keeps line breaks, so this goes out as one line"))
        return False
    if not town.can_paste(b):
        b.log(_("construction scanner: the game's agent is too old for line breaks, so this goes out as one line; "
                "restart Mist World so it loads the new one"), speak=True)
        return False
    return True


def _outgoing(b: Bot, report: dict[str, Any], kind: str, lines: bool | None) -> list[str]:
    """What goes out, a line to each entry: one entry unless the report is to go a line to a place."""
    if message(report) != ALL_FINE and _per_line(b, kind, lines):
        return message_lines(report)
    return [message(report)]


def scan_and_publish(b: Bot, kind: str, lines: bool | None = None, taken: bool | None = None,
                     first: bool | None = None) -> None:
    """A schedule's run: scan, then publish, unless there is nothing new to tell the gang."""
    _open_action_menu(b, kind)          # no tour of the highlands for a character who can't send it
    b.close_dialogs()
    report = scan(b, taken, first)
    text = "\n".join(_outgoing(b, report, kind, lines))
    sent: dict[str, str] = report.get("sent") or {}
    if text == ALL_FINE:
        _note_sent(b, report, kind, None)
        b.log(_("construction scanner: everything is at 100%, nothing published"), speak=True)
    elif sent.get(_sent_key(b, kind)) == text:
        b.log(_("construction scanner: nothing changed since the last report, nothing published"), speak=True)
    else:
        publish(b, kind, lines)


def publish(b: Bot, kind: str, lines: bool | None = None) -> None:
    """Send the kept report as an announcement, a broadcast or a system message."""
    report = load()
    if report is None:
        raise Stopped(_("no construction report yet: scan first"), final=True)
    parts = _outgoing(b, report, kind, lines)
    text = "\n".join(parts)
    _open_action_menu(b, kind)
    said_from = chores.said_mark(b)
    chat_from = _chat_mark(b)
    b.choose(ENTRIES[kind])
    if not (town.type_lines(b, parts) if len(parts) > 1 else town.type_text(b, text)):
        b.a.send("typeesc")
        b.close_dialogs()
        raise Stopped(_("the game didn't take the construction report"), final=True)
    if not _went_out(b, said_from, chat_from, text):
        b.log(_("the construction report was typed, but the game didn't confirm it - check before sending it again"),
              speak=True)
        return
    _note_sent(b, report, kind, text)
    gone = _drop_old_reports(b) if kind == "announcement" else 0
    b.log(_("construction report published; {n} older reports of yours removed", n=gone) if gone
          else _("construction report published"), speak=True)
