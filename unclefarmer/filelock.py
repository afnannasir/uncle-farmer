"""Hold the app's own data files open so Windows refuses to delete or rename them while it runs.

An open Windows file handle without FILE_SHARE_DELETE blocks DeleteFile/MoveFile. Python's open()
uses that sharing mode, so one read-only handle per protected file is enough. Saving releases the
handle around os.replace (which needs delete access), then takes it again.
"""

from __future__ import annotations
import atexit
import os
import threading
from typing import Any

from . import paths

def _folders():
    return (paths.PLANS, paths.PROFILES, paths.SCRIPTS, os.path.join(paths.APP, "results"))


def _files():
    messages = os.path.join(paths.APP, "messages")
    return (paths.SETTINGS, os.path.join(messages, "replies.txt"),
            os.path.join(messages, "goodbyes.txt"))


_guard = threading.RLock()
_held: dict[str, Any] = {}


def _key(path: str) -> str:
    return os.path.normcase(os.path.abspath(path))


def _under(path: str, folder: str) -> bool:
    path, folder = _key(path), _key(folder)
    return path == folder or path.startswith(folder + os.sep)


def is_protected(path: str) -> bool:
    """True for settings, plans, profiles, scripts, message files and results."""
    return _key(path) in {_key(p) for p in _files()} or any(_under(path, f) for f in _folders())


def acquire(path: str) -> bool:
    """Hold path open. False when it does not exist or cannot be opened."""
    key = _key(path)
    with _guard:
        if key in _held:
            return True
        try:
            handle = open(path, "rb")
        except OSError:
            return False
        _held[key] = handle
        return True


def release(path: str) -> bool:
    """Drop the handle. Returns whether one was held."""
    key = _key(path)
    with _guard:
        handle = _held.pop(key, None)
        if handle is None:
            return False
        try:
            handle.close()
        except OSError:
            pass
        return True


def delete(path: str) -> None:
    """The app's own delete: release the handle first, then remove the file."""
    was = release(path)
    try:
        os.remove(path)
    except OSError:
        if was:
            acquire(path)
        raise


def move(path: str, target: str) -> None:
    """The app's own rename/replace: release the handle first, then move the file."""
    was = release(path)
    try:
        os.replace(path, target)
    except OSError:
        if was:
            acquire(path)
        raise


def acquire_startup() -> None:
    """Lock every existing file that belongs to the user."""
    for path in _files():
        acquire(path)
    for folder in _folders():
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            path = os.path.join(folder, name)
            if os.path.isfile(path):
                acquire(path)


def release_all() -> None:
    with _guard:
        keys = list(_held)
    for key in keys:
        handle = _held.pop(key, None)
        if handle is not None:
            try:
                handle.close()
            except OSError:
                pass


atexit.register(release_all)
