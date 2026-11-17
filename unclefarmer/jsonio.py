"""JSON through orjson: the standard library's json interface, in one place.

orjson is much faster on our big files (atlas, game texts, the data built from the game), but it
differs from json in two ways that would otherwise reach every call site: dumps() returns bytes, and
it takes its formatting as an option bitmask instead of indent/sort_keys keywords. This module keeps
the shape the rest of the app already uses, so a module reads:

    from . import jsonio as json

and then calls json.load(f), json.dump(obj, f, indent=2), json.loads(text) as before. indent maps to
orjson's own two-space option (orjson only offers two), sort_keys and default pass through, and
ensure_ascii is accepted and ignored: orjson always writes UTF-8, which is what our ensure_ascii=False
call sites wanted and never worse for the rest. A non-string key (an int, a float) is stringified the
way json does, unless the keys are being sorted - orjson forbids the two together, and our sorted
files have string keys. String keys stay on orjson's fast path; the non-string-key option is only
added after orjson says a dict key isn't a string, so the usual dumps never pays for it.

save() and save_text() write a file atomically: a unique temporary file in the target's folder is
flushed to the disk, then renamed over the target. A crash mid-write, or a second writer doing the
same, leaves the old file whole instead of a half-written one.

An atomic write alone doesn't stop lost updates: two threads that each read a file, change it and
save it can still overwrite each other's change. lock_for(path) is the one lock per file for that:
hold it around the whole read-change-save (and around reads that must not race a save). read()
tells a file that isn't there (the default) from one that is there but can't be read right now
(OSError, after a short wait on a Windows sharing violation) and from one that is broken
(ValueError): an unreadable file must never be taken for an empty one and saved over.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import IO, Any, TypeGuard

import os
import shutil
import tempfile
import threading
import time

import orjson

from . import filelock

_save_lock = threading.RLock()       # serializes release/replace/reacquire for one open file

# Windows errors that last a moment: another program (an antivirus, the search indexer, a backup, a
# reader without delete sharing) has the file open. ERROR_SHARING_VIOLATION and ERROR_LOCK_VIOLATION
# for opening; a replace over such a file fails with ERROR_ACCESS_DENIED as well.
READ_BUSY = (32, 33)
REPLACE_BUSY = (5, 32, 33)
RETRY_WAITS = (0.02, 0.05, 0.1, 0.2, 0.4)     # about 0.8 s in all, then the error stands

_locks_guard = threading.Lock()
_locks: dict[str, threading.RLock] = {}
_MISSING: Any = object()


def lock_for(path: str) -> threading.RLock:
    """The one lock for this file (by its full, case-folded path): hold it around a whole
    read-change-save so two writers can't lose each other's change. Re-entrant."""
    key = os.path.normcase(os.path.abspath(path))
    with _locks_guard:
        lock = _locks.get(key)
        if lock is None:
            lock = _locks[key] = threading.RLock()
        return lock


def busy(error: BaseException, codes: tuple[int, ...] = READ_BUSY) -> bool:
    """True for a Windows error that passes by itself (see READ_BUSY)."""
    return isinstance(error, OSError) and getattr(error, "winerror", None) in codes


def retrying(action: Callable[[], Any], codes: tuple[int, ...] = READ_BUSY) -> Any:
    """action(), tried again after a short wait while it fails with one of the passing Windows
    errors (codes); any other error, or the same one after RETRY_WAITS, is raised."""
    for wait in RETRY_WAITS:
        try:
            return action()
        except OSError as e:
            if not busy(e, codes):
                raise
        time.sleep(wait)
    return action()


def read_text(path: str, encoding: str = "utf-8") -> str:
    """The whole file as text, waiting out a sharing violation. FileNotFoundError when it isn't
    there, OSError when it can't be read, UnicodeDecodeError (a ValueError) when it isn't text."""
    def read_all() -> str:
        with open(path, encoding=encoding) as f:
            return f.read()
    return retrying(read_all)


def read(path: str, default: Any = _MISSING, encoding: str = "utf-8-sig") -> Any:
    """The file's JSON. A file that isn't there gives default (FileNotFoundError without one); one
    that can't be read raises OSError, one that isn't JSON ValueError - never the default, so a
    caller can't save over a file it only failed to read. utf-8-sig: Notepad's byte order mark."""
    try:
        text = read_text(path, encoding)
    except FileNotFoundError:
        if default is _MISSING:
            raise
        return default
    return orjson.loads(text)


def aside_name(path: str) -> str:
    """<name>.corrupt, or .corrupt.2, .corrupt.3 ... when that is taken: a second broken file never
    overwrites the copy of the first."""
    name = path + ".corrupt"
    n = 1
    while os.path.exists(name):
        n += 1
        name = "%s.corrupt.%d" % (path, n)
    return name


def copy_aside(path: str) -> str | None:
    """A copy of a broken file as <name>.corrupt, before anything is written over it: the copy's
    name, or None when the copy failed."""
    try:
        target = aside_name(path)
        shutil.copyfile(path, target)
        return target
    except OSError:
        return None


def is_object(value: object) -> TypeGuard[dict[str, Any]]:
    """A JSON object (a dict): isinstance(value, dict), told to the type checker as the dict JSON
    gives - string keys, values to check where they are used."""
    return isinstance(value, dict)


def is_array(value: object) -> TypeGuard[list[Any]]:
    """A JSON array (a list), as is_object() is for objects."""
    return isinstance(value, list)


def loads(text: str | bytes) -> Any:
    """Text or bytes to Python objects. Any: JSON holds whatever the file or the agent wrote; the
    callers check its shape (or validate it with Pydantic) where they use it."""
    return orjson.loads(text)


def dumps(obj: Any, indent: int | None = None, sort_keys: bool = False, ensure_ascii: bool = True,
          default: Callable[[Any], Any] | None = None) -> str:
    """Python objects to JSON text. indent: any truthy value means orjson's two spaces.

    String keys stay on orjson's fast path; only a dict with a non-string key pays for
    OPT_NON_STR_KEYS (orjson forbids it together with sorting, and our sorted files have
    string keys).
    """
    option = orjson.OPT_SORT_KEYS if sort_keys else 0
    if indent:
        option |= orjson.OPT_INDENT_2
    try:
        return orjson.dumps(obj, default=default, option=option).decode("utf-8")
    except TypeError as e:
        if sort_keys or "Dict key must be str" not in str(e):
            raise
        option |= orjson.OPT_NON_STR_KEYS
        return orjson.dumps(obj, default=default, option=option).decode("utf-8")


def load(f: IO[str] | IO[bytes]) -> Any:
    """The file object's whole contents, parsed (Any: see loads)."""
    return orjson.loads(f.read())


def dump(obj: Any, f: IO[str], indent: int | None = None, sort_keys: bool = False,
         ensure_ascii: bool = True) -> None:
    """dumps(), written to the file object."""
    f.write(dumps(obj, indent=indent, sort_keys=sort_keys, ensure_ascii=ensure_ascii))


def save(path: str, obj: Any, indent: int | None = 2, sort_keys: bool = False) -> None:
    """dumps(obj) into path atomically. See save_text()."""
    save_text(path, dumps(obj, indent=indent, sort_keys=sort_keys))


def save_text(path: str, text: str, encoding: str = "utf-8", newline: str | None = None) -> None:
    """Write text to path atomically: the new file is complete before the old one is replaced.

    The temporary file is unique (mkstemp) so two writers - the GUI and a run, say - cannot
    overwrite each other's half-written file, and it lives in the target's folder so the final
    os.replace stays on one volume (a move across volumes is not atomic). A missing folder is
    made first: profiles/ is created on the first save.

    newline as open() takes it: None writes the system's line ends, "" the text's own. The old
    file's permission bits carry over. A replace that meets a file another program holds for a
    moment is tried again (REPLACE_BUSY); any failure leaves the old file as it was and raises."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=os.path.basename(path) + ".", suffix=".tmp")
    protected = filelock.is_protected(path)
    with _save_lock:
        if protected:
            filelock.release(path)           # os.replace needs delete access to the target
        try:
            try:
                f = os.fdopen(fd, "w", encoding=encoding, newline=newline)
            except BaseException:
                os.close(fd)
                raise
            with f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            if os.name != "nt" and os.path.exists(path):     # Windows keeps only read-only, which no replace gets past
                try:
                    shutil.copymode(path, tmp)
                except OSError:
                    pass                     # the default bits then: never a reason to fail the save
            retrying(lambda: os.replace(tmp, path), REPLACE_BUSY)
        finally:
            if os.path.exists(tmp):          # a failed write or replace must not leave a temp file
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if protected:
                filelock.acquire(path)       # take the lock again, now on the new file
