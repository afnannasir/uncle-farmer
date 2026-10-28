"""Build the C map searches (unclefarmer/native/routing.c) into unclefarmer/native/_routing-<hash>.dll.

    python build_native.py

The app runs without the DLL (atlaslib falls back to its Python searches: the same answers at a
fraction of the speed), so this is a speed-up, not a requirement. The DLL is committed, so a source
checkout runs at full speed without a compiler. Rebuild it after every edit of routing.c: the DLL is
named by routing.c's hash, the loader only takes the one built from the routing.c beside it, and
tests/test_native.py fails without it. Older builds are deleted (one a running app has loaded stays
until the next build).

The compiler is the first of: $CC, gcc on PATH, MSYS2's mingw64 gcc, zig (python -m ziglang).
The DLL needs only the C runtime every Windows has: libgcc is linked in, nothing else is.
Floating point stays strict (-ffp-contract=off, no -ffast-math): the searches must add their
costs exactly as Python does, or a tie between two equal routes could be broken another way.
"""

import glob
import importlib.util
import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from unclefarmer import native  # noqa: E402

MSYS_GCC = r"C:\msys64\mingw64\bin\gcc.exe"
FLAGS = ["-O2", "-shared", "-std=c11", "-Wall", "-Wextra", "-Werror", "-ffp-contract=off",
         "-fno-fast-math", "-s"]


def compiler() -> list[str] | None:
    """The command that compiles C, as a list, or None."""
    if os.environ.get("CC"):
        return [os.environ["CC"]]
    gcc = shutil.which("gcc") or (MSYS_GCC if os.path.exists(MSYS_GCC) else None)
    if gcc:
        return [gcc, "-static-libgcc"]
    if importlib.util.find_spec("ziglang") is not None:      # pip install ziglang: a C compiler as a package
        return [sys.executable, "-m", "ziglang", "cc", "-target", "x86_64-windows-gnu"]
    return None


def build() -> int:
    cc = compiler()
    if cc is None:
        print("no C compiler found: install MSYS2's mingw-w64 gcc, or pip install ziglang")
        return 1
    env = dict(os.environ)
    if os.path.isabs(cc[0]):
        env["PATH"] = os.path.dirname(cc[0]) + os.pathsep + env.get("PATH", "")   # gcc finds its helpers
    out = native.dll_path()
    assert out is not None                  # routing.c is here: the path is named by its hash
    tmp = out + ".new"
    cmd = cc + FLAGS + ['-DUF_SOURCE_HASH="%s"' % native.source_hash(), native.SOURCE, "-o", tmp]
    print(" ".join(cmd))
    subprocess.check_call(cmd, env=env)
    os.replace(tmp, out)
    for old in glob.glob(os.path.join(native.HERE, "_routing*" + native.EXT + "*")):
        if os.path.abspath(old) != os.path.abspath(out):
            try:
                os.remove(old)
            except OSError:
                pass             # loaded by a running app: it goes with the next build
    print("built:", out)
    return 0


if __name__ == "__main__":
    sys.exit(build())
