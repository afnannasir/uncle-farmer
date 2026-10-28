"""All of the app's log files, in one place: the day's run log and field log, errors.log and dialogs.log.

The level (Settings, Logging) decides which lines are written; the size and number of old files
decide when each file is rotated. Every write is best-effort: a full disk or a locked file must
never take the app down. The first failure says so once on stderr, and a traceback that cannot be
written goes to stderr instead - a crash must never be lost.
"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
import traceback
from collections.abc import Mapping

from . import paths
from .errors import carry_on

LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR")
DEFAULT_LEVEL = "INFO"
DEFAULT_MAX_MB = 5          # a new file after this many megabytes (0: never)
DEFAULT_BACKUPS = 5         # old files kept as name.1 ... name.N

_lock = threading.Lock()
_warned = False
_level = LEVELS.index(DEFAULT_LEVEL)
_max_bytes = int(DEFAULT_MAX_MB * 1024 * 1024)
_backups = DEFAULT_BACKUPS


def configure(settings: Mapping[str, object] | None = None) -> None:
    """Take the level and the rotation from the app settings (log_level, log_max_mb, log_backups).

    Values from settings.json are not trusted: anything odd falls back to the defaults, so a typo
    can neither silence the logs nor make every line rotate the file."""
    global _level, _max_bytes, _backups
    data: Mapping[str, object] = settings if isinstance(settings, Mapping) else {}
    name = str(data.get("log_level", DEFAULT_LEVEL)).upper()
    _level = LEVELS.index(name) if name in LEVELS else LEVELS.index(DEFAULT_LEVEL)
    _max_bytes = _megabytes(data.get("log_max_mb", DEFAULT_MAX_MB))
    _backups = _count(data.get("log_backups", DEFAULT_BACKUPS))


def _megabytes(value: object) -> int:
    try:
        mb = float(value) if isinstance(value, (int, float, str)) else DEFAULT_MAX_MB
    except ValueError:
        mb = DEFAULT_MAX_MB
    return int(min(max(mb, 0.0), 1000.0) * 1024 * 1024)


def _count(value: object) -> int:
    try:
        kept = int(value) if isinstance(value, (int, float, str)) else DEFAULT_BACKUPS
    except ValueError:
        kept = DEFAULT_BACKUPS
    return min(max(kept, 1), 50)


def _enabled(level: str) -> bool:
    return LEVELS.index(level) >= _level


def _complain(what: str, exc: BaseException, fallback: str = "") -> None:
    """Say it once: a broken log must not fill the screen (or the crash log) with itself."""
    global _warned
    if not _warned:
        _warned = True
        try:
            sys.stderr.write("Uncle Farmer: can't write the %s: %s\n" % (what, exc))
        except Exception as e:
            carry_on(e, "warning about a broken log")
    if fallback:
        try:
            sys.stderr.write(fallback)
        except Exception as e:
            carry_on(e, "writing the crash traceback to stderr")


def _make_room(path: str) -> None:
    """Rotate path to path.1 (shifting the old ones) once it has reached the set size."""
    if not _max_bytes:
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < _max_bytes:
        return
    for i in range(_backups, 1, -1):
        try:
            os.replace("%s.%d" % (path, i - 1), "%s.%d" % (path, i))
        except FileNotFoundError:
            pass
    try:
        os.replace(path, path + ".1")
    except FileNotFoundError:
        pass


def _append(what: str, path: str, text: str, fallback: str = "") -> OSError | None:
    """Append under the lock (and rotate if needed). Returns the OSError when it failed."""
    try:
        with _lock:
            _make_room(path)
            with open(path, "a", encoding="utf-8") as f:
                f.write(text)
        return None
    except OSError as e:
        _complain(what, e, fallback)
        return e


def write(line: str, level: str = "INFO") -> None:
    """One line to today's log, at the given level (INFO unless the caller knows better)."""
    if _enabled(level):
        _append("run log", os.path.join(paths.LOGS, time.strftime("%Y-%m-%d") + ".log"), line + "\n")


def field(record: dict[str, object]) -> None:
    """One farming record (a fight from the pick on, or a search walk) as a JSON line in
    logs/field-<day>.jsonl: where we stood and how far we walked, for nightwatch.py's report."""
    if _enabled("INFO"):
        line = json.dumps({"at": time.strftime("%Y-%m-%d %H:%M:%S"), **record}, separators=(",", ":"))
        _append("field log", os.path.join(paths.LOGS, "field-%s.jsonl" % time.strftime("%Y-%m-%d")), line + "\n")


def thread(what: str) -> None:
    """Which OS thread does what ("run 1234 Name"), to logs/threads.log: a profiler attached from
    outside (nightwatch.py, py-spy) sees thread ids only, and this names them."""
    _append("thread log", os.path.join(paths.LOGS, "threads.log"),
            "%s  %d  %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"), threading.get_native_id(), what))


def crash(title: str, who: str = "") -> None:
    """The current exception's traceback to logs/errors.log. Call it inside the except block."""
    tb = traceback.format_exc()          # captured before any file work: a failed open loses nothing
    text = "%s  %s (%s)\n%s" % (time.strftime("%Y-%m-%d %H:%M:%S"), title, who, tb)
    _append("error log", os.path.join(paths.LOGS, "errors.log"), text, fallback=text)


def dialog(text: str) -> OSError | None:
    """A dialog dump to logs/dialogs.log, rotated the same way. Returns the OSError or None."""
    if not _enabled("INFO"):
        return None
    return _append("dialog log", os.path.join(paths.LOGS, "dialogs.log"), text)
