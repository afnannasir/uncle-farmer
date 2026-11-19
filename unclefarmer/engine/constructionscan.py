"""Tools, Construction scanner: how far the gang's constructions are, for its elders to pass on.

The bot reads, repairing and building nothing, the Magic Cannon and Supply Camp under construction
in the inner hall ("Level 5 Magic Cannon Construction; Completed 62.0%") and every Crystal Turret and
Wall barricade of every highland under Occupied Highlands ("the completeness is 97.5%"). The result
is kept in profiles/_construction_scan.json, so it can be read, copied or published afterwards.

Publishing takes an elder's rights. The Action menu's gang line has "send system message", "send
broadcast" (the same with a sound) and "send an announcement", next to "transfer the gang" and
"disband": only those three exact entries are ever chosen. Each opens a text box. The game says
"sent" for an announcement; the other two come back in the chat under our own name (seen live
2026-11-18: channels 10 and 43).

The notices key (W_NOTICE) -> "gang announcement" lists the announcements: a title row, then a row
"<author> issued at <date>" with "delete" beside it, then "Are you sure ...?" -> Confirm. An elder
is offered "delete" on everyone's, so a new report replaces only this character's own old ones.

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


def _least_first(report: dict[str, Any]) -> list[tuple[str, float]]:
    rows = [(str(r[0]), float(r[1])) for r in report["rows"] if r[1] is not None]
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


def scan(b: Bot) -> dict[str, Any]:
    """Read what the gang is building and every defence of its highlands, and keep the report."""
    chores.inner_hall(b)
    rows: list[Row] = [(_camp_label(name), _read_place(b, name)) for name in chores.gang_camps(b)]
    seen: set[int] = set()
    while True:
        number = chores.next_highland(b, seen)
        if number is None:
            break
        seen.add(number)
        try:
            rows += [("highland %d %s" % (number, SHORT[name]), _read_place(b, name, stand))
                     for name, stand in chores.HIGHLAND_SITES]
        finally:
            chores.leave_highland(b)
    if not rows:
        raise Stopped(_("no construction of the gang was found to read"), final=True)
    before = load() or {}
    report: dict[str, Any] = {"at": time.strftime("%Y-%m-%d %H:%M"), "by": _name(b), "rows": rows,
                              "sent": before.get("sent") or {}}
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


def scan_and_publish(b: Bot, kind: str) -> None:
    """A schedule's run: scan, then publish, unless there is nothing new to tell the gang."""
    _open_action_menu(b, kind)          # no tour of the highlands for a character who can't send it
    b.close_dialogs()
    report = scan(b)
    text = message(report)
    sent: dict[str, str] = report.get("sent") or {}
    if text == ALL_FINE:
        _note_sent(b, report, kind, None)
        b.log(_("construction scanner: everything is at 100%, nothing published"), speak=True)
    elif sent.get(_sent_key(b, kind)) == text:
        b.log(_("construction scanner: nothing changed since the last report, nothing published"), speak=True)
    else:
        publish(b, kind)


def publish(b: Bot, kind: str) -> None:
    """Send the kept report as an announcement, a broadcast or a system message."""
    report = load()
    if report is None:
        raise Stopped(_("no construction report yet: scan first"), final=True)
    text = message(report)
    _open_action_menu(b, kind)
    said_from = chores.said_mark(b)
    chat_from = _chat_mark(b)
    b.choose(ENTRIES[kind])
    if not town.type_text(b, text):
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
