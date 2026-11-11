"""All of the app's log files, in one place: the day's run log and field log, errors.log,
dialogs.log, update.log (the updater), and how each start and close of the app went (bootup.log,
teardown.log: Lifecycle).

The level (Settings, Logging) decides which lines are written; the size and number of old files
decide when each file is rotated. Every write is best-effort: a full disk or a locked file must
never take the app down (a file that can't be rotated just grows until it can). The first failure
says so once on stderr, and a traceback that cannot be written goes to stderr instead - a crash
must never be lost.
"""

from __future__ import annotations

import contextlib
import json
import os
import re
import sys
import threading
import time
import traceback
from collections.abc import Callable, Generator, Mapping

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
ROTATE_RETRY = 60.0         # seconds before a rotation that failed is tried again
_rotate_after: dict[str, float] = {}     # path -> when its failed rotation may be tried again


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
    """Rotate path to path.1 (shifting the old ones) once it has reached the set size.

    A rename that fails (another program - an antivirus, an editor - holding one of the files) stops
    the rotation without losing a line: the caller still appends, the file grows a little past the
    size, and the rotation is tried again ROTATE_RETRY seconds later. Only the old files up to the
    first one missing are shifted, so a rotation tried again never pushes the older ones out."""
    if not _max_bytes or time.monotonic() < _rotate_after.get(path, 0.0):
        return
    try:
        size = os.path.getsize(path)
    except OSError:
        return
    if size < _max_bytes:
        return
    try:
        gap = next((i for i in range(1, _backups) if not os.path.exists("%s.%d" % (path, i))), _backups)
        for i in range(gap, 1, -1):
            os.replace("%s.%d" % (path, i - 1), "%s.%d" % (path, i))
        os.replace(path, path + ".1")
    except OSError:
        _rotate_after[path] = time.monotonic() + ROTATE_RETRY
    else:
        _rotate_after.pop(path, None)


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


def update(text: str, level: str = "INFO", logs: str | None = None) -> OSError | None:
    """One line to logs/update.log: the updater's checks, downloads, unpacking, installing and
    cleanup, in the same folder as every other log so a staging cleanup never takes it away.

    Written whatever the level, as bootup.log is: an update that went wrong is rare and must be
    explained. logs is the app's logs folder; the update copier runs from the staging folder,
    where paths.LOGS is not the app's, so it passes the app's own. The folder is made when missing
    (as paths makes it at the start), and the file is rotated like the others."""
    folder = logs or paths.LOGS
    try:
        os.makedirs(folder, exist_ok=True)
    except OSError as e:
        _complain("update log", e)
        return e
    now = time.time()
    line = "%s.%03d  %-7s [%d] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                                       int(now % 1 * 1000), level, os.getpid(), text)
    return _append("update log", os.path.join(folder, "update.log"), line)


def _took(seconds: float) -> str:
    return "%d ms" % (seconds * 1000) if seconds < 1 else "%.1f s" % seconds


def _trace(exc: BaseException) -> str:
    return "".join(traceback.format_exception(exc))


_MARK = re.compile(r"\[(\d+)\] (?:(BEGIN|END) |(.+): (started|done|finished with problems|FAILED))")


class Lifecycle:
    """logs/bootup.log or logs/teardown.log: one block per start (or close) of the app. BEGIN, then
    each stage as it starts and as it ends - done, or FAILED with its traceback - and an END line
    that says how the whole went: it is never "ready" or "closed" with a failed stage behind it.

    Written whatever the level, as errors.log is: a start that dies before the window opens has
    nowhere else to say why. Every line carries the process id, so a second copy started
    meanwhile can't be taken for this one. Writing is best-effort like every log here."""

    def __init__(self, name: str, what: str, clock: Callable[[], float] = time.monotonic) -> None:
        self.name, self.what, self.clock = name, what, clock     # "bootup.log", "startup"
        self.open = False                # between BEGIN and END
        self.problems: list[str] = []    # what went wrong this time, for the END line
        self.failed: str | None = None   # the stage whose failure ended it
        self.stage: str | None = None    # the stage under way
        self._began = 0.0
        self._told: BaseException | None = None     # the exception a FAILED line already showed

    def path(self) -> str:
        return os.path.join(paths.LOGS, self.name)

    def _line(self, level: str, text: str, exc: BaseException | None = None) -> None:
        try:
            now = time.time()
            out = "%s.%03d  %-7s [%d] %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(now)),
                                               int(now % 1 * 1000), level, os.getpid(), text)
            if exc is not None:
                out += "".join("    %s\n" % row for row in _trace(exc).rstrip("\n").split("\n"))
            _append(self.what + " log", self.path(), out, fallback=out if exc is not None else "")
        except Exception as e:           # a log line must never be what stops a start or a close
            _complain(self.what + " log", e)

    def begin(self, text: str) -> None:
        """BEGIN a new block, first saying so if the last one in the file never reached its END."""
        self._unfinished()
        self.open, self.problems, self.failed, self.stage = True, [], None, None
        self._began, self._told = self.clock(), None
        self._line("INFO", "BEGIN %s: %s" % (self.what, text))

    def _unfinished(self) -> None:
        """A block without an END: that copy died or hung there (or is still open - a second copy
        started while the first was starting). Only the file's last 64 KB are read."""
        try:
            with open(self.path(), "rb") as f:
                f.seek(0, os.SEEK_END)
                f.seek(max(f.tell() - 65536, 0))
                tail = f.read().decode("utf-8", "replace")
        except OSError:
            return                       # no file yet: nothing came before
        blocks: dict[str, tuple[str | None, str | None]] = {}    # pid -> (stage under way, last done)
        for row in tail.splitlines():
            m = _MARK.search(row)
            if not m:
                continue
            pid, mark, stage, how = m.groups()
            if mark == "BEGIN":
                blocks[pid] = (None, None)
            elif mark == "END":
                blocks.pop(pid, None)
            elif pid in blocks:
                blocks[pid] = (stage, None) if how == "started" else (None, stage)
        for pid, (during, after) in blocks.items():
            where = "during '%s'" % during if during else "after '%s'" % after if after else "at once"
            self._line("WARNING", "the %s of pid %s before this one has no END: still open, or it stopped "
                                  "%s" % (self.what, pid, where))

    def note(self, text: str, level: str = "INFO") -> None:
        self._line(level, text)

    def problem(self, text: str) -> None:
        """Something went wrong that doesn't stop this start or close: a WARNING, and the END says so."""
        self.problems.append(text)
        self._line("WARNING", text)

    @contextlib.contextmanager
    def step(self, name: str, must: bool = True) -> Generator[None]:
        """One stage: "started", then "done", or "FAILED" with its traceback. A stage that must work
        raises its error on (the start can't go on); one that needn't is written down and the next
        stage goes ahead (a close must close)."""
        self.stage = name
        before = len(self.problems)
        self._line("INFO", "%s: started" % name)
        began = self.clock()
        try:
            yield
        except BaseException as e:
            self._line("ERROR", "%s: FAILED after %s: %s: %s" % (name, _took(self.clock() - began),
                                                              type(e).__name__, e), e)
            self._told = e
            self.problems.append(name)
            if must or not isinstance(e, Exception):     # Ctrl+C and exit are never held back
                self.failed = name
                raise
        else:
            if len(self.problems) > before:
                self._line("WARNING", "%s: finished with problems (%s)" % (name, _took(self.clock() - began)))
            else:
                self._line("INFO", "%s: done (%s)" % (name, _took(self.clock() - began)))
        finally:
            self.stage = None

    def crashed(self, exc: BaseException) -> None:
        """An error ended it: its traceback (unless a FAILED line already has it), then END."""
        if exc is not self._told:
            self.failed = self.failed or self.stage or "outside any stage"
            self._line("ERROR", "unexpected error during '%s': %s: %s" % (self.failed, type(exc).__name__, exc), exc)
        self.failed = self.failed or "outside any stage"
        self.end("")

    def end(self, outcome: str) -> bool:
        """The END line: FAILED at the stage that stopped it, else the outcome, with the problems
        met on the way. True only when nothing went wrong."""
        took = _took(self.clock() - self._began)
        if self.failed:
            self._line("ERROR", "END %s: FAILED at '%s' after %s" % (self.what, self.failed, took))
        elif self.problems:
            self._line("WARNING", "END %s: %s in %s, with %d problem%s: %s" % (
                self.what, outcome, took, len(self.problems), "" if len(self.problems) == 1 else "s",
                "; ".join(self.problems)))
        else:
            self._line("INFO", "END %s: %s in %s" % (self.what, outcome, took))
        self.open = False
        return not self.failed and not self.problems


bootup = Lifecycle("bootup.log", "startup")
teardown = Lifecycle("teardown.log", "shutdown")
