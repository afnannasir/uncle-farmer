"""The C searches beside this file (routing.c), loaded with ctypes - or None, and the Python ones run.

atlaslib uses `lib` when it is here and falls back to its own Python code when it is not, so a
missing or stale DLL only costs speed, never a route. build_native.py builds routing.c into
_routing-<hash>.dll, named by (and carrying) routing.c's sha256: from a source checkout only the
DLL of the routing.c beside it is loaded, so an edited routing.c never runs with an old DLL, and a
rebuild never has to overwrite a DLL that a running app has loaded. A release has no routing.c;
build_exe.py copies the one DLL beside this file.

    UNCLEFARMER_NO_NATIVE=1    run the Python searches only (benchmarks, checking the two agree)

ctypes releases the GIL during each call, so a long search no longer holds up the window or the
message thread.
"""

import ctypes
import glob
import hashlib
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
SOURCE = os.path.join(HERE, "routing.c")
EXT = ".dll" if os.name == "nt" else ".so"
ABI = 3

lib = None
why_not = ""          # why lib is None, for the log and the benchmarks


def source_hash(path=SOURCE):
    """sha256 of routing.c with its line endings made LF (a checkout may turn them to CRLF)."""
    with open(path, "rb") as f:
        return hashlib.sha256(f.read().replace(b"\r\n", b"\n")).hexdigest()


def dll_path(source=SOURCE, folder=HERE):
    """The DLL to load: the one built from routing.c when it is here, else (a release) the newest
    _routing-*.dll in the folder; None when there is none."""
    if os.path.exists(source):
        return os.path.join(folder, "_routing-%s%s" % (source_hash(source)[:12], EXT))
    built = sorted(glob.glob(os.path.join(folder, "_routing-*" + EXT)), key=os.path.getmtime)
    return built[-1] if built else None


def _declare(dll):
    i32, f64, u32, p = ctypes.c_int32, ctypes.c_double, ctypes.c_uint32, ctypes.c_void_p
    b = ctypes.c_char_p             # the model's flat bytes: passed without a copy
    dll.uf_abi.restype = ctypes.c_int
    dll.uf_abi.argtypes = []
    dll.uf_source_hash.restype = ctypes.c_char_p
    dll.uf_source_hash.argtypes = []
    dll.uf_dijkstra.restype = ctypes.c_int
    dll.uf_dijkstra.argtypes = [i32, i32, b, b, b, b, p, i32, p, i32, i32, f64, p, p, p]
    dll.uf_astar_rect.restype = ctypes.c_int
    dll.uf_dijkstra_to.restype = ctypes.c_int
    dll.uf_dijkstra_to.argtypes = [i32, i32, b, b, b, b, p, i32, p, i32, i32, f64, p, p, p, p, u32, p, p]
    dll.uf_astar_rect.argtypes = [i32, i32, b, b, b, b, b, p, f64, i32, p, i32,
                                  i32, i32, i32, i32, i32, i32, i32, i32,
                                  p, p, p, p, u32, i32, p, i32, p, p]
    dll.uf_bfs.restype = ctypes.c_int
    dll.uf_bfs.argtypes = [i32, i32, b, b, i32, p, i32, i32, p]
    dll.uf_components.restype = ctypes.c_int
    dll.uf_components.argtypes = [i32, i32, b, p, p, i32]
    dll.uf_diag_ok.restype = ctypes.c_int
    dll.uf_diag_ok.argtypes = [i32, i32, i32, i32, b, b, b, b, i32, i32, i32, i32]
    dll.uf_diag_hops.restype = ctypes.c_int
    dll.uf_diag_hops.argtypes = [i32, i32, i32, i32, b, b, b, b, p, i32, i32, i32, p]
    dll.uf_line_ok.restype = ctypes.c_int
    dll.uf_line_ok.argtypes = [i32, i32, i32, i32, b, b, b, b, i32, i32, i32, i32, i32, i32]


def load(path=None, source=SOURCE):
    """The DLL (dll_path() unless given), checked and declared, or None (why_not says why)."""
    global why_not
    if os.environ.get("UNCLEFARMER_NO_NATIVE"):
        why_not = "turned off (UNCLEFARMER_NO_NATIVE)"
        return None
    path = path or dll_path(source)
    if not path or not os.path.exists(path):
        why_not = "not built for this routing.c (python build_native.py)"
        return None
    try:
        dll = ctypes.CDLL(path)
        _declare(dll)
    except (OSError, AttributeError) as e:
        why_not = "can't load %s: %s" % (os.path.basename(path), e)
        return None
    if dll.uf_abi() != ABI:
        why_not = "built for another version of this module"
        return None
    if os.path.exists(source) and not getattr(sys, "frozen", False):
        built = (dll.uf_source_hash() or b"").decode("ascii", "replace")
        if built != source_hash(source):
            why_not = "built from another routing.c (python build_native.py)"
            return None
    why_not = ""
    return dll


lib = load()
