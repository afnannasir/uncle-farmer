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
"""

from __future__ import annotations

from collections.abc import Callable
from typing import IO, Any, TypeGuard

import os
import tempfile
import threading

import orjson

from . import filelock

_save_lock = threading.RLock()       # serializes release/replace/reacquire for one open file


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


def save_text(path: str, text: str, encoding: str = "utf-8") -> None:
    """Write text to path atomically: the new file is complete before the old one is replaced.

    The temporary file is unique (mkstemp) so two writers - the GUI and a run, say - cannot
    overwrite each other's half-written file, and it lives in the target's folder so the final
    os.replace stays on one volume (a move across volumes is not atomic). A missing folder is
    made first: profiles/ is created on the first save."""
    folder = os.path.dirname(path) or "."
    os.makedirs(folder, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=folder, prefix=os.path.basename(path) + ".", suffix=".tmp")
    protected = filelock.is_protected(path)
    with _save_lock:
        if protected:
            filelock.release(path)           # os.replace needs delete access to the target
        try:
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(text)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp, path)
        finally:
            if os.path.exists(tmp):          # a failed write or replace must not leave a temp file
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
            if protected:
                filelock.acquire(path)       # take the lock again, now on the new file
