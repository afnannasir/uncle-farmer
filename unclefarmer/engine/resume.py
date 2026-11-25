"""Where each unfinished run was, so it can carry on from there.

A plan or script keeps its place - the line it is on, the turn of each loop, what the current farm
has already done, the run's minutes and kills, its own `set` values - in profiles/_interrupted_runs.json,
one row per character, every SAVE_EVERY seconds while it runs.

The row stays when the run didn't end: the game was lost (it closed, froze or logged our character
out for good), or Uncle Farmer was closed under it (by the player, an update or the computer
restarting). Then the next start of Uncle Farmer offers to carry on, the same game day only (the
drop time starts again at 00:00 GMT). A run that ended by itself, one stopped with Stop, and one
that failed some other way (a script error, a bug) take their row away: carrying those on would
only repeat what ended them.

A row is only taken up by the same plan or script, unchanged: script.Runner skips straight to the
saved line, still reading the lines on the way that set how the run ends (stop_when, keep_drop...).
"""

from __future__ import annotations

import hashlib
import os
import threading
from typing import Any

from .. import daily, jsonio, paths

FILE = "_interrupted_runs.json"     # in the profiles folder
SAVE_EVERY = 20.0                   # seconds between two saves of a run's place
COUNTDOWN = 10                      # seconds the offer to carry on waits before it does
RESTARTS = "_game_restarts.json"    # in the profiles folder: how often each character's game was opened again
RESTARTS_MOST = 3                   # ... at most this many times a game day: a game that keeps failing is left
# Lines that only set how the run goes or ends: run again on the way to the saved line, since
# skipping them would lose a time limit, the end place or the drop time kept.
KEPT = frozenset({"stop_when", "end_at", "keep_drop", "keep_level", "triggers", "sound_watch", "sound_mute",
                  "login"})
OPENS = frozenset({"repeat", "while", "if", "call"})   # lines with a block of their own to go into

_lock = threading.Lock()


def _path() -> str:
    return os.path.join(paths.PROFILES, FILE)


def fingerprint(text: str) -> str:
    """Which version of a plan or script a row was saved by: an edited one doesn't take it up."""
    return hashlib.sha1(text.encode("utf-8")).hexdigest()


def rows() -> dict[str, dict[str, Any]]:
    """Every saved place, by character (the profile key)."""
    try:
        got = jsonio.read(_path(), {})
    except (OSError, ValueError):
        return {}
    return {k: v for k, v in got.items() if jsonio.is_object(v)} if jsonio.is_object(got) else {}


def _to_write(path: str) -> dict[str, Any] | None:
    """A file's rows, to change and write back - None when it couldn't be read just now (another
    program holding it a moment): written over then, it lost every other character's row (their
    place to carry on from, their restarts of the day). A file that isn't JSON any more is {}."""
    try:
        got = jsonio.read(path, {})
    except OSError:
        return None
    except ValueError:
        return {}
    return {k: v for k, v in got.items() if jsonio.is_object(v)} if jsonio.is_object(got) else {}


def save(key: str, row: dict[str, Any]) -> None:
    """This character's place now (best effort: a run never stops for it, and the next save
    comes soon after one skipped)."""
    with _lock:
        every = _to_write(_path())
        if every is None:
            return
        every[key] = dict(row, day=row.get("day") or daily.game_day())
        try:
            jsonio.save(_path(), every, indent=1)
        except OSError:
            pass


def clear(key: str | None) -> None:
    """This character has no unfinished run any more."""
    if not key:
        return
    with _lock:
        every = _to_write(_path())
        if every is None or every.pop(key, None) is None:
            return
        try:
            jsonio.save(_path(), every, indent=1)
        except OSError:
            pass


def get(key: str | None) -> dict[str, Any] | None:
    return rows().get(key) if key else None


def pending(day: str | None = None) -> list[tuple[str, dict[str, Any]]]:
    """The places that can still be carried on today: those of an earlier game day are dropped
    (the day's drop time, the shares of a plan and the chores started again since)."""
    today = day or daily.game_day()
    out: list[tuple[str, dict[str, Any]]] = []
    stale: list[str] = []
    for key, row in rows().items():
        if row.get("day") == today and row.get("item") and row.get("text"):
            out.append((key, row))
        else:
            stale.append(key)
    for key in stale:
        clear(key)
    return out


def restarts_today(key: str, day: str | None = None) -> int:
    """How many times this character's lost game was opened again this game day."""
    try:
        got = jsonio.read(os.path.join(paths.PROFILES, RESTARTS), {})
    except (OSError, ValueError):
        return 0
    row = got.get(key) if jsonio.is_object(got) else None
    if not jsonio.is_object(row) or row.get("day") != (day or daily.game_day()):
        return 0
    count = row.get("count")
    return count if isinstance(count, int) else 0


def note_restart(key: str, day: str | None = None) -> int:
    """One more restart today for this character; how many there were today, this one included."""
    today = day or daily.game_day()
    with _lock:
        count = restarts_today(key, today) + 1
        path = os.path.join(paths.PROFILES, RESTARTS)
        got = _to_write(path)
        if got is None:
            return count                 # not noted this time: the others' counts are kept
        every = {k: v for k, v in got.items() if v.get("day") == today}
        every[key] = {"day": today, "count": count}
        try:
            jsonio.save(path, every, indent=1)
        except OSError:
            pass
    return count


def item_of(row: dict[str, Any]) -> tuple[str, str] | None:
    """("plan" or "script", its name) from a saved row."""
    kind, _sep, name = str(row.get("item") or "").partition(":")
    return (kind, name) if kind in ("plan", "script") and name else None
