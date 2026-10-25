"""How full a game's address space is.

Mist World is a 32-bit program: Windows gives it 2 GB of addresses, or 4 GB when mw.exe is marked
large-address aware. The Java heap takes 1 GB of that the moment the game starts; threads, sounds and
the game's own native buffers take the rest. When nothing is left the game can't start a thread, and
its sound and world timer dies (the character freezes where it stands). Reading this now and then
during a run shows how close a game is to that.
"""

import ctypes
import ctypes.wintypes as wt
import struct

MEM_FREE = 0x10000
MB = 1024 * 1024


class _MBI(ctypes.Structure):
    _fields_ = [("BaseAddress", ctypes.c_void_p), ("AllocationBase", ctypes.c_void_p),
                ("AllocationProtect", wt.DWORD), ("PartitionId", wt.WORD), ("RegionSize", ctypes.c_size_t),
                ("State", wt.DWORD), ("Protect", wt.DWORD), ("Type", wt.DWORD)]


def _image(kernel, h):
    buf = ctypes.create_unicode_buffer(1024)
    size = wt.DWORD(1024)
    return buf.value if kernel.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)) else None


def large_address_aware(exe):
    """True when the program may use addresses above 2 GB (IMAGE_FILE_LARGE_ADDRESS_AWARE)."""
    with open(exe, "rb") as f:
        head = f.read(4096)
    pe = struct.unpack_from("<I", head, 0x3C)[0]
    return bool(struct.unpack_from("<H", head, pe + 22)[0] & 0x20)


def address_space(pid: int) -> tuple[int, int] | None:
    """(used MB, limit MB) of a 32-bit game's address space, or None when it can't be read."""
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.VirtualQueryEx.restype = ctypes.c_size_t
    kernel.VirtualQueryEx.argtypes = [wt.HANDLE, ctypes.c_void_p, ctypes.POINTER(_MBI), ctypes.c_size_t]
    kernel.OpenProcess.restype = wt.HANDLE
    h = kernel.OpenProcess(0x0400 | 0x1000, False, pid)   # QUERY_INFORMATION | QUERY_LIMITED_INFORMATION
    if not h:
        return None
    try:
        exe = _image(kernel, h)
        if not exe:
            return None
        limit = (4096 if large_address_aware(exe) else 2048) * MB
        free, addr, mbi = 0, 0x10000, _MBI()
        while addr < limit:
            if not kernel.VirtualQueryEx(h, ctypes.c_void_p(addr), ctypes.byref(mbi), ctypes.sizeof(mbi)):
                break
            end = min(limit, (mbi.BaseAddress or 0) + mbi.RegionSize)
            if mbi.State == MEM_FREE:
                free += end - addr
            if end <= addr:
                break
            addr = end
        return (limit - free) // MB, limit // MB
    except OSError:
        return None
    finally:
        kernel.CloseHandle(h)
