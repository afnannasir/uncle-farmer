"""Where everything lives. The app is portable: all of it sits in one folder next to the exe."""

from __future__ import annotations

import os
import sys


def frozen() -> bool:
    """Running as the built UncleFarmer.exe, not from the source: the one answer every module asks.
    build_exe.py builds with Nuitka, which sets no sys.frozen; it puts __compiled__ in the globals
    of every module it compiles, this one too. sys.frozen still counts, for another tool's build."""
    return "__compiled__" in globals() or bool(getattr(sys, "frozen", False))


def app_folder() -> str:
    """The folder that holds data/, agent/, lang/: in a build the exe's own (Nuitka's sys.executable
    is a python.exe beside UncleFarmer.exe), from source the checkout's. Never the working folder."""
    if frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


APP = app_folder()

DATA = os.path.join(APP, "data")
LANG = os.path.join(APP, "lang")
DOCS = os.path.join(APP, "docs")
AGENT_DIR = os.path.join(APP, "agent")
AGENT_JAR = os.path.join(AGENT_DIR, "unclefarmer-agent.jar")
AGENT_RUN = os.path.join(AGENT_DIR, "run")        # the agent's port file and log
PROFILES = os.path.join(APP, "profiles")
PLANS = os.path.join(APP, "plans")
SCRIPTS = os.path.join(APP, "scripts")
SETTINGS = os.path.join(APP, "settings.json")
LOGS = os.path.join(APP, "logs")

for _d in (PROFILES, PLANS, SCRIPTS, LOGS):
    os.makedirs(_d, exist_ok=True)


def safe_name(name: str) -> str:
    """A file name from a character or plan name; keeps letters, digits, space, - and _."""
    keep = "".join(c if c.isalnum() or c in " -_" else "_" for c in name).strip()
    return keep or "unnamed"
