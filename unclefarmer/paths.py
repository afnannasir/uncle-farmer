"""Where everything lives. The app is portable: all of it sits in one folder next to the exe."""

from __future__ import annotations

import os
import sys

# A PyInstaller one-folder build: the exe's folder holds data/, agent/, lang/ beside it.
APP = (os.path.dirname(sys.executable) if getattr(sys, "frozen", False)
       else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

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
