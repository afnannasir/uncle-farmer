"""Scheduled runs: a plan or script started for a character at a set time - once, every day, or
on chosen days of the week.

Kept in schedules.json beside settings.json: the player's own, never in a release (the updater
leaves it alone). Times are the computer's local time; a time the clock skips when summer time
begins counts as the first minute after the gap (real()). Nothing here starts anything itself: the
main window calls tick() every few seconds with a start() of its own, the same start the Start
button uses, so a scheduled run is an ordinary run - its log, results, daily stop and end place.

The rules:
  - An occurrence runs at most once. Before a run is started its occurrence is written down as
    handled (and saved), so a crash or a second tick can't start it again; if the start then
    can't happen, the mark is taken back.
  - A time that passed while the app was closed, or while the schedule was off, is not made up
    for later: an occurrence more than LATE old is written down as missed, never run late. One
    entry covers every occurrence missed in a row.
  - One run per game: while the character's game is running something else (another schedule, a
    run the player started, the walking assistant) the occurrence waits; if it still can't start
    after LATE it is written down as skipped, with why. The same goes for a game that isn't open.
  - A plan or script that no longer exists, or doesn't pass its check, is an error for that
    occurrence (written down, not retried).
  - Every start, end, miss, skip and error goes into the schedule's history: when it was due,
    when it happened, what happened.
"""

from __future__ import annotations

import datetime as dt
import os
import secrets
import threading
from collections.abc import Callable, Iterator
from typing import Any, TypeGuard

from . import jsonio as json, paths
from .i18n import N_, _

SCHEDULES = os.path.join(paths.APP, "schedules.json")
LATE = dt.timedelta(minutes=15)     # an occurrence still starts this long after its time, never later
HISTORY_KEEP = 50                   # entries kept per schedule, newest last
STAMP = "%Y-%m-%d %H:%M"            # how times are written in the file
ZONE: dt.tzinfo | None = None       # the time zone times are in; None: the computer's own
ONCE, DAILY, WEEKLY = "once", "daily", "weekly"
REPEATS = (ONCE, DAILY, WEEKLY)
REPEAT_NAMES = {ONCE: N_("Once"), DAILY: N_("Every day"), WEEKLY: N_("On chosen days")}
DAY_NAMES = (N_("Monday"), N_("Tuesday"), N_("Wednesday"), N_("Thursday"), N_("Friday"), N_("Saturday"), N_("Sunday"))

# what start() answers
STARTED = "started"     # the run is going
WAIT = "wait"           # can't now (the game is busy or not open): try again until LATE
FAILED = "error"        # can't ever (the plan is gone, the script is broken): written down, not retried
# what the history says
OUTCOMES = {STARTED: N_("started"), "finished": N_("finished"), "stopped": N_("stopped"), FAILED: N_("error"),
            "missed": N_("missed"), "skipped": N_("skipped")}

Schedule = dict[str, Any]
Starter = Callable[[Schedule, dt.datetime], "tuple[str, str]"]     # (STARTED | WAIT | FAILED, detail)


# ---------------------------------------------------------------- times
def parse_time(text: str) -> tuple[int, int]:
    """"6:30" or "06:30" -> (6, 30). ValueError with a message for the player otherwise."""
    h, sep, m = text.strip().partition(":")
    if not sep or not h.strip().isdigit() or not m.strip().isdigit():
        raise ValueError(_("Time: write it as hours:minutes, for example 06:30"))
    hour, minute = int(h), int(m)
    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        raise ValueError(_("Time: hours go from 0 to 23 and minutes from 0 to 59"))
    return hour, minute


def parse_date(text: str) -> dt.date:
    try:
        return dt.datetime.strptime(text.strip(), "%Y-%m-%d").date()
    except ValueError:
        raise ValueError(_("Date: write it as year-month-day, for example 2026-11-05")) from None


def stamp(t: dt.datetime) -> str:
    return t.strftime(STAMP)


def unstamp(text: object) -> dt.datetime | None:
    try:
        return dt.datetime.strptime(str(text), STAMP)
    except ValueError:
        return None


def real(t: dt.datetime) -> dt.datetime:
    """t, or when the clock never shows it (the hour skipped when summer time begins), the first
    minute after the gap: 02:30 on that night is 03:00. Times the clock shows twice (when summer
    time ends) are left as they are: the first of the two counts."""
    minute = dt.timedelta(minutes=1)
    for _i in range(3 * 60):            # no zone skips more than a couple of hours
        if _wall(t) == t:
            return t
        t = (t + minute).replace(second=0, microsecond=0)
    return t


def _wall(t: dt.datetime) -> dt.datetime:
    """What the clock reads at the moment the wall time t is taken for: t itself, unless t is in
    a gap."""
    try:
        if ZONE is None:
            return t.astimezone().replace(tzinfo=None)
        return t.replace(tzinfo=ZONE).astimezone(dt.timezone.utc).astimezone(ZONE).replace(tzinfo=None)
    except (OverflowError, OSError, ValueError):     # a hand-edited year the computer can't place
        return t


def occurrences(s: Schedule, after: dt.datetime, until: dt.datetime) -> Iterator[dt.datetime]:
    """Every time the schedule comes round in (after, until], oldest first."""
    try:
        hour, minute = parse_time(str(s.get("time", "")))
    except ValueError:
        return
    repeat = s.get("repeat")
    if repeat == ONCE:
        try:
            day = parse_date(str(s.get("date", "")))
        except ValueError:
            return
        t = real(dt.datetime.combine(day, dt.time(hour, minute)))
        if after < t <= until:
            yield t
        return
    listed: list[object] = s.get("days") or []
    days: set[int] = set(range(7)) if repeat == DAILY else {d for d in listed if _day(d)}
    if repeat not in (DAILY, WEEKLY) or not days:
        return
    day = after.date()
    while True:
        t = dt.datetime.combine(day, dt.time(hour, minute))
        if t > until:
            return
        if day.weekday() in days:
            t = real(t)
            if after < t <= until:
                yield t
        day += dt.timedelta(days=1)


def _day(d: object) -> TypeGuard[int]:
    return isinstance(d, int) and not isinstance(d, bool) and 0 <= d <= 6


def next_due(s: Schedule, now: dt.datetime) -> dt.datetime | None:
    """When it runs next: the occurrence waiting to start, else the first one after now. None
    when it is off or never comes round again."""
    if not s.get("enabled"):
        return None
    handled = unstamp(s.get("handled")) or now
    waiting = latest_due(s, now)
    if waiting is not None and now - waiting <= LATE:
        return waiting
    return next(occurrences(s, max(handled, now), now + dt.timedelta(days=400)), None)


def latest_due(s: Schedule, now: dt.datetime) -> dt.datetime | None:
    """The newest occurrence that has come round and not been dealt with yet, or None."""
    handled = unstamp(s.get("handled"))
    if handled is None:
        return None
    last = None
    for t in occurrences(s, handled, now):
        last = t
    return last


# ---------------------------------------------------------------- the file
def clean(raw: object) -> Schedule | None:
    """A schedule read from the file, or None for one that isn't usable (a hand-edited file must
    cost only its broken entry)."""
    if not json.is_object(raw):
        return None
    sid, item, who, repeat = raw.get("id"), raw.get("item"), raw.get("character"), raw.get("repeat")
    if not (isinstance(sid, str) and sid and isinstance(item, str) and ":" in item
            and isinstance(who, str) and who.strip() and repeat in REPEATS):
        return None
    try:
        parse_time(str(raw.get("time", "")))
        if repeat == ONCE:
            parse_date(str(raw.get("date", "")))
    except ValueError:
        return None
    kept: object = raw.get("history")
    history: list[Any] = kept if json.is_array(kept) else []
    days: object = raw.get("days")
    return {"id": sid, "item": item, "character": who.strip(), "repeat": repeat, "time": str(raw["time"]).strip(),
            "date": str(raw.get("date") or ""), "days": sorted({d for d in days if _day(d)}) if json.is_array(days) else [],
            "enabled": bool(raw.get("enabled", True)),
            "handled": raw.get("handled") if unstamp(raw.get("handled")) else None,
            "history": [h for h in history if json.is_object(h)][-HISTORY_KEEP:]}


def load(path: str = SCHEDULES) -> list[Schedule]:
    try:
        with open(path, encoding="utf-8-sig") as f:
            raw: object = json.load(f)
    except (OSError, ValueError):
        return []
    rows: list[Any] = raw.get("schedules", []) if json.is_object(raw) else []
    out = [s for s in (clean(r) for r in rows) if s is not None]
    return out


def save(items: list[Schedule], path: str = SCHEDULES) -> None:
    json.save(path, {"format": 1, "schedules": items}, indent=2)


# ---------------------------------------------------------------- the schedules
def item_of(s: Schedule) -> tuple[str, str]:
    kind, _sep, name = str(s["item"]).partition(":")
    return kind, name


def describe_when(s: Schedule) -> str:
    """"every day at 06:30", "on Monday, Friday at 22:00", "once, 2026-11-05 at 06:30"."""
    at = str(s.get("time"))
    if s.get("repeat") == DAILY:
        return _("every day at {time}", time=at)
    if s.get("repeat") == WEEKLY:
        listed: list[object] = s.get("days") or []
        days = ", ".join(_(DAY_NAMES[d]) for d in listed if _day(d))
        return _("on {days} at {time}", days=days, time=at)
    return _("once, {date} at {time}", date=s.get("date"), time=at)


class Scheduler:
    """The schedules and their file. Every change is saved straight away. Used from the main
    window's thread; the lock keeps a run ending on its own thread (ended()) from writing at the
    same moment."""

    def __init__(self, path: str = SCHEDULES) -> None:
        self.path = path
        self.items = load(path)
        self.lock = threading.RLock()
        self.waiting: dict[str, str] = {}        # schedule id -> why its due occurrence is waiting (said once)

    def save(self) -> None:
        with self.lock:
            save(self.items, self.path)

    def get(self, sid: str) -> Schedule | None:
        return next((s for s in self.items if s["id"] == sid), None)

    # ------------------------------------------------------------ editing
    def add(self, fields: Schedule, now: dt.datetime) -> Schedule:
        """A new schedule from the editor's fields. Times before now never run: it starts counting
        from now."""
        with self.lock:
            s = clean({**fields, "id": secrets.token_hex(4), "history": []})
            if s is None:
                raise ValueError(_("The schedule isn't complete"))
            s["handled"] = stamp(now)
            self.items.append(s)
            self.save()
            return s

    def update(self, sid: str, fields: Schedule, now: dt.datetime) -> Schedule:
        """Change a schedule (its history stays). From now on its new times count: an occurrence
        the change puts in the past is not run."""
        with self.lock:
            old = self.get(sid)
            if old is None:
                raise ValueError(_("That schedule is gone"))
            s = clean({**old, **fields, "id": sid, "history": old.get("history") or []})
            if s is None:
                raise ValueError(_("The schedule isn't complete"))
            s["handled"] = stamp(max(now, unstamp(old.get("handled")) or now))
            self.items[self.items.index(old)] = s
            self.waiting.pop(sid, None)
            self.save()
            return s

    def set_enabled(self, sid: str, on: bool, now: dt.datetime) -> None:
        """Turn a schedule on or off. Turned on again, it counts from now: what came round while it
        was off doesn't run."""
        with self.lock:
            s = self.get(sid)
            if s is None or s["enabled"] == on:
                return
            s["enabled"] = on
            if on:
                s["handled"] = stamp(now)
            self.waiting.pop(sid, None)
            self.save()

    def remove(self, sid: str) -> None:
        with self.lock:
            self.items = [s for s in self.items if s["id"] != sid]
            self.waiting.pop(sid, None)
            self.save()

    # ------------------------------------------------------------ history
    def note(self, s: Schedule, due: dt.datetime | str, outcome: str, detail: str, now: dt.datetime) -> None:
        with self.lock:
            h: list[dict[str, Any]] = s.setdefault("history", [])
            h.append({"due": due if isinstance(due, str) else stamp(due), "at": now.strftime("%Y-%m-%d %H:%M:%S"),
                      "outcome": outcome, "detail": detail})
            del h[:-HISTORY_KEEP]

    def ended(self, sid: str, due: str, outcome: str, detail: str, now: dt.datetime) -> None:
        """A scheduled run has ended: finished, stopped or error, and why."""
        with self.lock:
            s = self.get(sid)
            if s is None:
                return
            self.note(s, due, outcome, detail, now)
            self.save()

    # ------------------------------------------------------------ running
    def tick(self, now: dt.datetime, start: Starter) -> list[tuple[Schedule, str]]:
        """Start what is due. Returns [(schedule, what to tell the player)] for what happened: a
        start, a miss, a skip, an error, and once for each occurrence that has to wait."""
        said: list[tuple[Schedule, str]] = []
        with self.lock:
            for s in list(self.items):
                if s.get("enabled"):
                    said += self._tick_one(s, now, start)
        return said

    def _tick_one(self, s: Schedule, now: dt.datetime, start: Starter) -> list[tuple[Schedule, str]]:
        handled = unstamp(s.get("handled"))
        if handled is None:                       # a hand-written entry: count from now
            s["handled"] = stamp(now)
            self.save()
            return []
        due_all = list(occurrences(s, handled, now))
        if not due_all:
            return []
        due = due_all[-1]
        told: list[tuple[Schedule, str]] = []
        older = due_all[:-1]
        if now - due > LATE:
            older = due_all
        if older:
            why = self.waiting.pop(s["id"], None) if older[-1] == due else None
            outcome = "skipped" if why else "missed"
            detail = why or _("the app wasn't running then")
            if len(older) > 1:
                detail = _("{n} times, {first} to {last}: {why}", n=len(older), first=stamp(older[0]),
                           last=stamp(older[-1]), why=detail)
            self.note(s, older[-1], outcome, detail, now)
            s["handled"] = stamp(older[-1])
            told.append((s, _("{what} for {who}: {outcome} ({why})", what=item_of(s)[1], who=s["character"],
                              outcome=_(OUTCOMES[outcome]), why=detail)))
            if s.get("repeat") == ONCE:
                s["enabled"] = False
            self.save()
            if older[-1] == due:
                return told
        # mark it handled before starting, so nothing can start it twice
        before = s["handled"]
        s["handled"] = stamp(due)
        self.save()
        try:
            outcome, detail = start(s, due)
        except Exception as e:     # a start that blew up is that occurrence's error, not the app's end
            outcome, detail = FAILED, repr(e)
        if outcome == WAIT:
            s["handled"] = before
            self.save()
            if self.waiting.get(s["id"]) != detail:
                self.waiting[s["id"]] = detail
                told.append((s, _("{what} for {who} is waiting: {why}", what=item_of(s)[1], who=s["character"],
                                  why=detail)))
            return told
        self.waiting.pop(s["id"], None)
        self.note(s, due, STARTED if outcome == STARTED else FAILED, detail, now)
        if s.get("repeat") == ONCE:
            s["enabled"] = False
        self.save()
        if outcome == STARTED:
            told.append((s, _("Scheduled: starting {what} for {who}", what=item_of(s)[1], who=s["character"])))
        else:
            told.append((s, _("Scheduled {what} for {who} couldn't start: {why}", what=item_of(s)[1],
                              who=s["character"], why=detail)))
        return told
