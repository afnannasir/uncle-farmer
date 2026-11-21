"""Run, Autopilot: a character's day as one script (master plan, Phase 4).

The window (gui/autopilot.py) runs the day the way any farming run goes: the Before farming list,
the farming, the After farming list. The player can also have the day written out as a script, to
read, change and schedule like any other (2026-11-19: "keep all the options"). That script says
`triggers off` at the top, so the Triggers lists don't run around it a second time: its steps are
in it, in order, each disabled step left out and each wait written as `wait`. The After steps go
in an on_stop block, which runs when the farming ended by itself or at the daily stop and never
after the player's Stop: when the After farming list runs too.

Farming is the character's plan (its script text, given by the window) or `farm map=best`: the
best field map for the level the character has when that line runs.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from typing import Any

from ..i18n import _
from . import triggers

BEST = "farm map=best"
INDENT = "    "


def farm_line(best: bool, plan_text: str | None) -> str:
    """What the day farms with: the plan's own script, or the best map for the level."""
    if best or not plan_text:
        return BEST + "\n"
    return plan_text if plan_text.endswith("\n") else plan_text + "\n"


def farms(text: str) -> bool:
    """A step's script farms (a farm or dungeon line, however deep): such a step is no After step,
    since the day's farming may be over by then (the daily stop: never farm across the reset)."""
    from .script import ScriptError, parse
    try:
        main, subs, _on_stop = parse(text)
    except ScriptError:
        return False
    return triggers.farms(main, subs)


def _steps_text(p: dict[str, Any], stage: str, read: Callable[[str], str]) -> list[str]:
    """One stage's enabled steps as script lines (the After ones indented for their on_stop)."""
    pad = INDENT if stage == "after" else ""
    out: list[str] = []
    for row in triggers.sequence(p, stage)["steps"]:
        if not row.get("enabled", True):
            continue
        action = str(row["action"])
        text = read(action).rstrip("\n")
        if stage == "after" and farms(text):
            out.append(pad + "# " + _("left out: {step} farms, and the day's farming may be over by then",
                                      step=triggers.what(action)))
            continue
        out.append(pad + "# " + triggers.what(action))
        out.extend(pad + line if line.strip() else line for line in text.splitlines())
        delay = float(row.get("delay") or 0)
        if delay > 0:
            out.append(pad + "wait %g" % delay)
    return out


def day_script(p: dict[str, Any], farm: str, read: Callable[[str], str], title: str) -> str:
    """The day as a script: `triggers off`, the Before farming steps, the farming, the After
    farming steps in an on_stop block (without any that farm). read(action) gives a step's script
    (triggers.action_text for a real bot). A `login` line of the plan's goes to the very top, so
    every step runs on that character."""
    farm_lines = farm.rstrip("\n").splitlines()
    logins = [line for line in farm_lines if line.strip().split(" ", 1)[0] == "login"]
    farm_lines = [line for line in farm_lines if line not in logins]
    lines = ["# " + title, "# " + _("written by Run, Autopilot - the Triggers lists don't run around it"), ""]
    lines += logins[:1]
    lines += ["triggers off", "", "# " + _("Before farming")]
    lines += _steps_text(p, "before", read)
    lines += ["", "# " + _("Farming")] + farm_lines
    after = _steps_text(p, "after", read)
    if any(line.strip() and not line.lstrip().startswith("#") for line in after):
        lines += ["", "# " + _("After farming"), "on_stop"] + after + ["end"]
    elif after:
        lines += ["", "# " + _("After farming")] + [line.strip() for line in after]
    return "\n".join(lines) + "\n"


# ---------------------------------------------------------------------------------------- the choices
FARMS = ("plan", "best")          # farm with the character's plan, or the best map for its level
HOWS = ("lists", "script")        # the Triggers lists around the farming, or the script of the day
STARTS = ("manual", "daily")      # only on Start, or every day at autopilot_time (Run, Schedules)
DAY_SCRIPT = "Autopilot day - %s"         # the saved script of the day (the player may edit it)
FARM_SCRIPT = "Autopilot farming - %s"    # `farm map=best` alone, for the lists around it


def choices(p: dict[str, Any]) -> dict[str, str]:
    """The window's choices from the profile, each one known (a hand-edited value: the default)."""
    def one(key: str, allowed: tuple[str, ...]) -> str:
        v = str(p.get(key) or "")
        return v if v in allowed else allowed[0]
    return {"farm": one("autopilot_farm", FARMS), "how": one("autopilot_how", HOWS),
            "start": one("autopilot_start", STARTS), "time": str(p.get("autopilot_time") or "06:00"),
            "plan": str(p.get("autopilot_plan") or "")}


def step_text(p: dict[str, Any], action: str) -> str:
    """A Triggers step's script, as triggers.action_text gives it, from the profile alone."""
    from .plan import script_path
    if action.startswith("script:"):
        with open(script_path(action[7:]), encoding="utf-8-sig") as source:
            return source.read()
    if action == "dungeon":
        from . import dungeon
        return dungeon.script(dungeon.config(p))
    return action + "\n"


def _write(name: str, text: str) -> None:
    from .. import jsonio
    from .plan import script_path
    jsonio.save_text(script_path(name), text)


def write_day(p: dict[str, Any], display: str, plan_text: str | None) -> str:
    """(Re)write the script of the day; its name."""
    c = choices(p)
    name = DAY_SCRIPT % display
    _write(name, day_script(p, farm_line(c["farm"] == "best", plan_text), lambda a: step_text(p, a),
                            _("the day of {name}", name=display)))
    return name


def item_for(p: dict[str, Any], display: str, plan_text: str | None) -> tuple[str, str] | None:
    """What Start (or the daily schedule) runs for these choices: the plan, the best-map script
    with the lists around it, or the script of the day (written only when it isn't there yet, so a
    player's own changes to it stay). None when the day farms with a plan and none is chosen."""
    from .plan import script_path
    c = choices(p)
    if c["how"] == "script":
        name = DAY_SCRIPT % display
        if not os.path.exists(script_path(name)):
            write_day(p, display, plan_text)
        return "script", name
    if c["farm"] == "best":
        name = FARM_SCRIPT % display
        _write(name, "# " + _("written by Run, Autopilot: the best map for the level, with the Triggers lists "
                              "around it") + "\n" + BEST + "\n")
        return "script", name
    kind, _sep, plan = c["plan"].partition(":")
    return (kind, plan) if kind == "plan" and plan else None
