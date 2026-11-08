"""Single instance: a named mutex allows only one Uncle Farmer per Windows session, and a second
launch finds the open window by its mark, whatever language its title is in."""

import ctypes
import sys
import uuid
from collections.abc import Iterator

import pytest

from unclefarmer import instance

pytestmark = pytest.mark.skipif(sys.platform != "win32", reason="Windows named mutex")


def test_only_one_instance_can_hold_the_mutex() -> None:
    name = "Local\\UncleFarmerTest-" + uuid.uuid4().hex
    first, second = instance.InstanceLock(name), instance.InstanceLock(name)
    try:
        assert first.acquire()
        assert not second.acquire()
        first.release()
        assert second.acquire()
    finally:
        first.release()
        second.release()


@pytest.fixture
def window() -> Iterator[int]:
    """A real top-level window of this process (hidden, never shown), with a made-up title."""
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.CreateWindowExW.argtypes = (ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint32,
                                       ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
                                       ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p, ctypes.c_void_p)
    user32.CreateWindowExW.restype = ctypes.c_void_p
    user32.DestroyWindow.argtypes = (ctypes.c_void_p,)
    hwnd = user32.CreateWindowExW(0, "STATIC", "Vieux Fermier " + uuid.uuid4().hex, 0x80000000,     # WS_POPUP
                                  0, 0, 10, 10, None, None, None, None)
    assert hwnd
    try:
        yield hwnd
    finally:
        user32.DestroyWindow(hwnd)


def test_a_second_launch_finds_the_marked_window_whatever_its_title(window: int) -> None:
    mark = "UncleFarmerTest." + uuid.uuid4().hex
    nowhere = "No such window " + uuid.uuid4().hex
    assert instance.find_existing(mark, nowhere) is None
    assert instance.mark_window(window, mark)
    assert instance.find_existing(mark, nowhere) == window


def test_an_unmarked_copy_is_still_found_by_its_title(window: int) -> None:
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    user32.SetWindowTextW.argtypes = (ctypes.c_void_p, ctypes.c_wchar_p)
    title = "Uncle Farmer test " + uuid.uuid4().hex
    user32.SetWindowTextW(window, title)
    assert instance.find_existing("UncleFarmerTest." + uuid.uuid4().hex, title) == window


def test_nothing_to_mark_and_nothing_found_is_no_error() -> None:
    assert not instance.mark_window(0)
    assert not instance.focus_existing("UncleFarmerTest." + uuid.uuid4().hex, "No such window " + uuid.uuid4().hex)
