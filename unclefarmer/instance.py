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


def focus_existing(title: str = "Uncle Farmer") -> bool:
    """Bring the already running window to the front, if Windows can find it."""
    if sys.platform != "win32":
        return False
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.FindWindowW.argtypes = (ctypes.c_wchar_p, ctypes.c_wchar_p)
    user32.FindWindowW.restype = ctypes.c_void_p
    user32.ShowWindow.argtypes = (ctypes.c_void_p, ctypes.c_int)
    user32.SetForegroundWindow.argtypes = (ctypes.c_void_p,)
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        return False
    user32.ShowWindow(hwnd, 9)           # SW_RESTORE
    user32.SetForegroundWindow(hwnd)
    return True


_LOCK = InstanceLock()


def acquire() -> bool:
    return _LOCK.acquire()
