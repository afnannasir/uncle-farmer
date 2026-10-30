"""A check of a built (or source) copy without opening a window: UncleFarmer.exe --self-test <file>.

CI runs it on the freshly built folder before the build is uploaded: Nuitka finishing without an
error doesn't prove the exe starts, finds its libraries or reads its own data. Each check is one
line of the report; nothing is shown, no game is touched and nothing waits for input, so it can't
hang a build. The report goes to a file because the exe has no console (--windows-console-mode=
disable). The exit code is 0 only when every required check passed.
"""

from __future__ import annotations

import json
import os
import sys
import traceback
from collections.abc import Callable
from typing import Any

from . import __version__, paths


def _imports() -> str:
    # the GUI library, without wx.App: no window, no message loop
    import numpy
    import orjson
    import pydantic
    import wx
    gui: str = wx.VERSION_STRING
    return "wx %s, numpy %s, orjson %s, pydantic %s" % (gui, numpy.__version__, orjson.__version__, pydantic.VERSION)


def _native() -> str:
    from . import native
    dll = native.load()
    if dll is None:
        raise RuntimeError("the C map searches didn't load (unclefarmer/native/_routing-*.dll)")
    return "routing DLL ABI %d" % native.ABI


def _data() -> str:
    from . import atlas, knowledge
    npcs = len(knowledge.data().get("npcs", []))
    maps = len(atlas.maps())
    if not npcs or not maps:
        raise RuntimeError("data/ is empty: %d NPCs, %d maps" % (npcs, maps))
    return "%d NPCs, %d maps" % (npcs, maps)


def _translations() -> str:
    from . import i18n
    mo = os.path.join(paths.LANG, "ur", "LC_MESSAGES", "unclefarmer.mo")
    if not os.path.isfile(mo):
        raise RuntimeError("no compiled catalog at %s" % mo)
    i18n.load("ur")
    try:
        translated = i18n._("paused")
    finally:
        i18n.load("en")
    if translated == "paused":
        raise RuntimeError("the Roman Urdu catalog loaded but translates nothing")
    return "Roman Urdu catalog loads"


def _agent() -> str:
    if not os.path.isfile(paths.AGENT_JAR):
        raise RuntimeError("no game agent at %s" % paths.AGENT_JAR)
    return "%d bytes" % os.path.getsize(paths.AGENT_JAR)


def frozen() -> bool:
    """Running as the built exe (Nuitka standalone), not from the source."""
    return bool(getattr(sys, "frozen", False)) or "__compiled__" in globals()


def checks() -> list[tuple[str, Callable[[], str], bool]]:
    """(name, check, required). The agent jar is gitignored in a source checkout (build_agent.py
    makes it), so there it is reported but not required."""
    return [("imports", _imports, True), ("native routing", _native, True), ("data", _data, True),
            ("translations", _translations, True), ("game agent", _agent, frozen())]


def run(report_path: str) -> int:
    """Run every check, write the report (JSON) and return the exit code."""
    rows: list[dict[str, Any]] = []
    ok = True
    for name, check, required in checks():
        try:
            rows.append({"check": name, "ok": True, "detail": check(), "required": required})
        except Exception as e:      # every failure is a line of the report, not a crash
            rows.append({"check": name, "ok": False, "required": required,
                         "detail": "%s: %s" % (type(e).__name__, e),
                         "trace": traceback.format_exc(limit=3)})
            ok = ok and not required
    report = {"ok": ok, "version": __version__, "frozen": frozen(), "app": paths.APP,
              "python": sys.version.split()[0], "checks": rows}
    with open(report_path, "w", encoding="utf-8") as f:
        json.dump(report, f, indent=1)
    return 0 if ok else 1
