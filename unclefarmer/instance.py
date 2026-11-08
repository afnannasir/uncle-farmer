"""One Uncle Farmer per Windows session; a second launch brings the open window forward."""

from __future__ import annotations
import ctypes
import sys
from typing import Any

_ERROR_ALREADY_EXISTS = 183


class InstanceLock:
    """A named Windows mutex, held for the life of the process."""

    def __init__(self, name: str = r"Local\UncleFarmer") -> None:
        self.name = name
        self._handle: int | None = None
        self._kernel32: Any = None

    def acquire(self) -> bool:
        """True when this process got the mutex; False when another instance holds it."""
        if sys.platform != "win32":
            return True
        if self._handle is not None:
            return True
        kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel32.CreateMutexW.argtypes = (ctypes.c_void_p, ctypes.c_bool, ctypes.c_wchar_p)
        kernel32.CreateMutexW.restype = ctypes.c_void_p
        kernel32.CloseHandle.argtypes = (ctypes.c_void_p,)
        kernel32.CloseHandle.restype = ctypes.c_bool
        handle = kernel32.CreateMutexW(None, False, self.name)
        if not handle:
            return True                  # a broken API call must not stop the app
        if ctypes.get_last_error() == _ERROR_ALREADY_EXISTS:
            kernel32.CloseHandle(handle)
            return False
        self._handle, self._kernel32 = handle, kernel32
        return True

    def release(self) -> None:
        if self._handle is None:
            return
        self._kernel32.CloseHandle(self._handle)
        self._handle = None
        self._kernel32: Any = None


MARK = "UncleFarmer.MainWindow"     # a property on the main window: found whatever language its title is in
TITLE = "Uncle Farmer"              # the English title, for a copy from before the mark


def mark_window(hwnd: int, mark: str = MARK) -> bool:
    """Mark the main window (its handle) so a second launch finds it: its title is translated,
    and the second launch looks before it has read the language."""
    if sys.platform != "win32" or not hwnd:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetPropW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p, ctypes.c_void_p)
    user32.SetPropW.restype = ctypes.c_bool
    return bool(user32.SetPropW(hwnd, mark, 1))


def find_existing(mark: str = MARK, title: str = TITLE) -> int | None:
    """The handle of the running copy's main window: the one with the mark, else (a copy from
    before the mark) the one with the English title. None when Windows has neither."""
    if sys.platform != "win32":
        return None
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.GetPropW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    user32.GetPropW.restype = ctypes.c_void_p
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.FindWindowW.restype = ctypes.c_void_p
    found: list[int] = []

    def each(hwnd: int | None, _param: int | None) -> int:
        if hwnd and user32.GetPropW(hwnd, mark):
            found.append(hwnd)
            return 0                     # found: stop
        return 1
    enum_proc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_void_p, ctypes.c_void_p)    # WNDENUMPROC
    user32.EnumWindows.argtypes = (enum_proc, ctypes.c_void_p)
    user32.EnumWindows(enum_proc(each), None)
    if found:
        return found[0]
    return user32.FindWindowW(None, title) or None


def focus_existing(mark: str = MARK, title: str = TITLE) -> bool:
    """Bring the already running window to the front, if Windows can find it."""
    hwnd = find_existing(mark, title)
    if hwnd is None:
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    user32.ShowWindow(hwnd, 9)           # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


_LOCK = InstanceLock()


def acquire() -> bool:
    return _LOCK.acquire()


def release() -> None:
    _LOCK.release()
