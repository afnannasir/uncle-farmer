"""Build the shareable Uncle Farmer folder: dist\\Uncle Farmer\\UncleFarmer.exe plus its data.

    python build_exe.py
    python build_exe.py --full     every core at normal priority (faster; CI builds this way)

Needs Nuitka (uv sync installs it), a JDK (the game agent is built from source) and UPX: the folder
in the UPX_DIR environment variable, else the upx found on PATH (winget install UPX.UPX puts it
there). Without UPX the build stops and says so: it never quietly makes a bigger, different build.
Builds its own copy of the game agent every time, never over the one a running game has loaded.
The result needs nothing installed: zip "dist\\Uncle Farmer" and share it. Character settings,
plans, scripts, settings and logs are NOT copied, so your own setup never ends up in someone else's
copy. The finished folder is checked (verify) before the build counts as done.
"""

from __future__ import annotations

import glob
import os
import shutil
import subprocess
import sys

from docs_html import build as build_docs
from unclefarmer import __version__, native

ROOT = os.path.dirname(os.path.abspath(__file__))
DIST = os.path.join(ROOT, "dist")
OUT = os.path.join(DIST, "Uncle Farmer")
# The release's jar is built here, never over agent/unclefarmer-agent.jar: a running game has that
# one loaded, and a release can be built while you play.
JAR = os.path.join(DIST, "unclefarmer-agent.jar")
BUILT = os.path.join(DIST, "UncleFarmer.dist")
# What a finished folder must hold (verify): the exe and Python, the GUI and number libraries, the
# game agent, the screen reader bridge, the C map searches, and the data, translations and guides.
REQUIRED = ("UncleFarmer.exe", "python3*.dll", "wxmsw*_core_*.dll", "wxbase*.dll", "agent/unclefarmer-agent.jar",
            "nvdaControllerClient64.dll", "unclefarmer/native/_routing-*.dll", "data/knowledge.json",
            "data/atlas.json", "data/skills.json", "lang/unclefarmer.pot", "lang/ur/LC_MESSAGES/unclefarmer.mo",
            "docs/USER_GUIDE.md", "README.html", "LICENSE", "numpy", "orjson", "pydantic_core", "wx")


def upx_dir() -> str:
    """The folder holding upx.exe: UPX_DIR, else the upx on PATH. It used to fall back on a folder
    on the developer's own drive (F:\\upx-...), which no other PC (and no CI runner) has."""
    exe = "upx.exe" if os.name == "nt" else "upx"
    given = os.environ.get("UPX_DIR")
    if given:
        if not os.path.isfile(os.path.join(given, exe)):
            sys.exit("UPX_DIR is %s, but there is no %s in it" % (given, exe))
        return given
    on_path = shutil.which("upx")
    if on_path:
        # winget's upx on PATH is a link: Nuitka gets the real file's folder
        return os.path.dirname(os.path.realpath(on_path))
    sys.exit("UPX not found: install it (winget install UPX.UPX) or set UPX_DIR to the folder holding upx.exe")


def windows_version(version: str) -> str:
    """Nuitka's version resource wants up to four numbers: 0.3.1 -> 0.3.1.0."""
    return ".".join((version.split(".") + ["0", "0", "0"])[:4])


CPP_RUNTIME = ("msvcp140.dll",)   # Nuitka found no copy it may ship, and a module of ours needs it


def carry_cpp_runtime(out: str) -> None:
    """Most PCs have the Visual C++ runtime in System32, but not every one, and without it the
    exe doesn't start. Its licence lets an app carry the DLLs next to its exe, so the folder does."""
    system = os.path.join(os.environ.get("SystemRoot", r"C:\Windows"), "System32")
    for dll in CPP_RUNTIME:
        src = os.path.join(system, dll)
        if not os.path.exists(os.path.join(out, dll)) and os.path.exists(src):
            shutil.copy2(src, out)


def main(full: bool = False) -> None:
    """full: every core at normal priority (faster, but the PC is busy meanwhile)."""
    sys.path.insert(0, ROOT)
    upx = upx_dir()           # before the half-hour compile, not after it
    # The jar is rebuilt every time: it is gitignored, so "it exists" says nothing about whether
    # it matches the sources (a stale jar ships the previous agent version and missing commands).
    os.makedirs(DIST, exist_ok=True)
    subprocess.check_call([sys.executable, os.path.join(ROOT, "agent", "build_agent.py"), JAR])
    if os.path.exists(BUILT):
        shutil.rmtree(BUILT)
    # The C compile takes half an hour or more and every core it is given: it gets half of them, and
    # it runs (with every compiler it starts) below normal priority, so the PC stays usable meanwhile.
    # full: all of them, at normal priority.
    jobs = (os.cpu_count() or 4) if full else max(1, (os.cpu_count() or 4) // 2)
    priority = 0 if full else getattr(subprocess, "BELOW_NORMAL_PRIORITY_CLASS", 0)
    subprocess.check_call([
        sys.executable, "-m", "nuitka", os.path.join(ROOT, "UncleFarmer.pyw"),
        "--standalone", "--windows-console-mode=disable", "--assume-yes-for-downloads",
        "--output-filename=UncleFarmer.exe", "--output-dir=" + DIST, "--remove-output",
        "--windows-product-name=Uncle Farmer",
        "--windows-product-version=" + windows_version(__version__),
        "--jobs=%d" % jobs, "--lto=auto", "--python-flag=-OO",
        "--enable-plugin=upx", "--upx-binary=" + upx,
        "--include-package=unclefarmer",
    ], creationflags=priority)
    if os.path.exists(OUT):
        shutil.rmtree(OUT)
    os.rename(BUILT, OUT)
    # game_texts.json is built from the player's own game folder on first use, never shipped
    shutil.copytree(os.path.join(ROOT, "data"), os.path.join(OUT, "data"),
                    ignore=shutil.ignore_patterns("game_texts.json"))
    shutil.copytree(os.path.join(ROOT, "lang"), os.path.join(OUT, "lang"))
    shutil.copytree(os.path.join(ROOT, "docs"), os.path.join(OUT, "docs"))
    os.makedirs(os.path.join(OUT, "agent"))
    shutil.copy2(JAR, os.path.join(OUT, "agent"))
    os.remove(JAR)
    shutil.copy2(os.path.join(ROOT, "nvdaControllerClient64.dll"), OUT)
    # The C map searches: loaded with ctypes from beside the compiled unclefarmer.native module,
    # which Nuitka doesn't know to carry. Without it the app runs the (slower) Python searches.
    searches = native.dll_path()
    if searches and os.path.exists(searches):
        os.makedirs(os.path.join(OUT, "unclefarmer", "native"), exist_ok=True)
        shutil.copy2(searches, os.path.join(OUT, "unclefarmer", "native"))
    else:
        # the DLL is committed (build_native.py makes it): a build without it is a mistake
        sys.exit("no DLL for routing.c (python build_native.py): a build without it routes in Python")
    carry_cpp_runtime(OUT)
    # the guides as web pages too (headings to move by), and the user guide as README.html beside the exe
    pages = build_docs(os.path.join(OUT, "docs"))
    shutil.copy2(pages["USER_GUIDE.md"], os.path.join(OUT, "README.html"))
    for name in ("LICENSE", "THIRD_PARTY_NOTICES.md"):
        if os.path.exists(os.path.join(ROOT, name)):
            shutil.copy2(os.path.join(ROOT, name), OUT)
    verify(OUT)
    print("built:", OUT)


def verify(folder: str) -> None:
    """Stop the build when the finished folder lacks something it must hold (REQUIRED): a Nuitka run
    that "succeeded" can still leave out a library, and a missing jar or data file is only found
    by a player otherwise."""
    missing = [p for p in REQUIRED if not glob.glob(os.path.join(folder, *p.split("/")))]
    if missing:
        sys.exit("the built folder %s lacks: %s" % (folder, ", ".join(missing)))


if __name__ == "__main__":
    main(full="--full" in sys.argv[1:])
