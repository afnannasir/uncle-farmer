"""Farming plans: what the plan editor makes. A plan is saved as JSON and runs as a script.

    {
      "name": "Turkey cave",
      "spots": [ {"map": 5302, "minutes": 0, "kills": 0, "drop_minutes": 0, "patrol": "full",
                  "area": null, "only": "", "skip": "", "elites": "fight"} ],
      "rotation": "round",               # several spots: "round" (in turn, again and again) or "once"
      "stop_after_minutes": 300,         # 0 = no limit
      "stop_at_clock": "",               # "HH:MM", empty = no
      "stop_after_kills": 0,
      "stop_when_item": "", "stop_when_item_below": 0,   # e.g. potions running out
      "drop_keep_minutes": 0,            # drop time to leave unused; 0 = the character's setting
      "level_keep_xp": 0,                # stop when the next level needs less; 0 = the character's setting
      "end": {"how": "start"}            # where every end goes: start | place (+ map, cell) |
    }                                    # hearthstone | safe | here

How long a plan farms is decided in one place, its stop rules (plus the character's own: drop
time, the next level, a full bag, deaths, broken gear). With one spot it farms there until it stops; a spot's own
minutes or kills only matter with several spots: how long before moving on to the next.

A spot's drop_minutes is its share of the day's drop time (DROP_DAY, 7 hours a day for everyone):
the spots share it out in their order - three hours on the first, the rest on the second - counted
on the game's own drop clock, so a run restarted in the middle carries on where the day is, and a
spot whose share is already used up is skipped. A plan with shares runs its spots once, in order:
going round again would find every share used up.
"""

from __future__ import annotations

import os
import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from .. import atlas, filelock, jsonio as json, paths

PLAN_DEFAULTS: dict[str, Any] = {
    "name": "",
    "spots": [],
    "rotation": "round",
    "stop_after_minutes": 0,
    "stop_at_clock": "",
    "stop_after_kills": 0,
    "stop_when_item": "",
    "stop_when_item_below": 0,
    "drop_keep_minutes": 0,
    "level_keep_xp": 0,
    "end": {"how": "start"},
}

STEP_DEFAULTS: dict[str, Any] = {"map": 5121, "minutes": 30, "kills": 0, "drop_minutes": 0, "patrol": "random",
                 "area": None, "only": "", "skip": "", "elites": "fight", "points": [], "nogo": []}
DROP_DAY = 7 * 60     # minutes of drop time the game gives a day, the same for every character
END_HOWS = ("start", "place", "hearthstone", "safe", "here")
PLAN_EXPORT_FORMAT = "unclefarmer-plan"
PLAN_EXPORT_VERSION = 1


class End(BaseModel):
    """Where every end of a plan goes."""
    model_config = ConfigDict(extra="allow")

    how: str = "start"
    map: int | None = None
    cell: list[int] | None = None


class Spot(BaseModel):
    """One farming spot."""
    model_config = ConfigDict(extra="allow")

    map: int = STEP_DEFAULTS["map"]
    minutes: int = STEP_DEFAULTS["minutes"]
    kills: int = STEP_DEFAULTS["kills"]
    drop_minutes: int = STEP_DEFAULTS["drop_minutes"]
    patrol: str = STEP_DEFAULTS["patrol"]
    area: list[int] | None = STEP_DEFAULTS["area"]
    only: str = STEP_DEFAULTS["only"]
    skip: str = STEP_DEFAULTS["skip"]
    elites: str = STEP_DEFAULTS["elites"]
    points: list[list[int]] = Field(default_factory=list[list[int]])   # patrol "points": x, y each
    nogo: list[list[int]] = Field(default_factory=list[list[int]])     # no-go circles: x, y, radius each


class Plan(BaseModel):
    """The shape of a plan file, checked by Pydantic when one is loaded or saved. Unknown keys are
    kept: load() runs upgrade() on them afterwards, and a newer version's key must survive."""
    model_config = ConfigDict(extra="allow")

    name: str = PLAN_DEFAULTS["name"]
    spots: list[Spot] = Field(default_factory=list[Spot])
    rotation: str = PLAN_DEFAULTS["rotation"]
    stop_after_minutes: int = PLAN_DEFAULTS["stop_after_minutes"]
    stop_at_clock: str = PLAN_DEFAULTS["stop_at_clock"]
    stop_after_kills: int = PLAN_DEFAULTS["stop_after_kills"]
    stop_when_item: str = PLAN_DEFAULTS["stop_when_item"]
    stop_when_item_below: int = PLAN_DEFAULTS["stop_when_item_below"]
    drop_keep_minutes: int = PLAN_DEFAULTS["drop_keep_minutes"]
    level_keep_xp: int = PLAN_DEFAULTS["level_keep_xp"]
    end: End = Field(default_factory=End)


def new_step(map_id: int | None = None) -> dict[str, Any]:
    s = dict(STEP_DEFAULTS)
    if map_id:
        s["map"] = int(map_id)
    return s


def upgrade(p: dict[str, Any]) -> dict[str, Any]:
    """A plan saved by the old editor: "steps" with a "repeat" box and "finish". A single step's
    minutes and kills were the whole plan's length: they become its stop rules. The old ending
    (the hearthstone, or staying) becomes going back where the run started - the new default,
    which walks the character back to where the player started it."""
    if "steps" not in p:
        return p
    steps = [dict(STEP_DEFAULTS, **s) for s in p.pop("steps")]
    repeat = p.pop("repeat", True)
    p.pop("finish", None)
    if len(steps) == 1:
        s = steps[0]
        if not repeat:
            if s.get("minutes") and not p.get("stop_after_minutes"):
                p["stop_after_minutes"] = s["minutes"]
            if s.get("kills") and not p.get("stop_after_kills"):
                p["stop_after_kills"] = s["kills"]
        s["minutes"] = s["kills"] = 0
    p["spots"] = steps
    p["rotation"] = "round" if repeat else "once"
    p.setdefault("end", {"how": "start"})
    return p


def _q(text: str | float) -> str:
    """Quote a value for a script line."""
    return '"%s"' % str(text).replace('"', "'")


def end_line(end: dict[str, Any] | None) -> str:
    """The script line for a plan's end place (its "end": how, and map and cell for a place)."""
    end = end or {}
    how = end.get("how", "start")
    if how == "place" and end.get("map"):
        cell = end.get("cell")
        return "end_at %s%s" % (end["map"], (" at %d,%d" % tuple(cell)) if cell else "")
    return "end_at %s" % (how if how in END_HOWS and how != "place" else "start")


def keep_lines(plan: dict[str, Any]) -> list[str]:
    """The plan's own drop time to save and next-level stop, ahead of the character's (0: theirs)."""
    lines: list[str] = []
    if plan.get("drop_keep_minutes"):
        lines.append("keep_drop %d" % int(plan["drop_keep_minutes"]))
    if plan.get("level_keep_xp"):
        lines.append("keep_level %d" % int(plan["level_keep_xp"]))
    return lines


def to_script(plan: dict[str, Any]) -> str:
    lines = ["# %s" % (plan.get("name") or "plan"),
             "# Made with the plan editor. Saving it as a script lets you edit it by hand.", "",
             end_line(plan.get("end"))]
    lines += keep_lines(plan)
    if plan.get("stop_after_minutes"):
        lines.append("stop_when minutes >= %s : time limit reached" % plan["stop_after_minutes"])
    if plan.get("stop_at_clock"):
        lines.append('stop_when clock == "%s" : it is %s' % (plan["stop_at_clock"], plan["stop_at_clock"]))
    if plan.get("stop_after_kills"):
        lines.append("stop_when kills >= %s : kill limit reached" % plan["stop_after_kills"])
    if plan.get("stop_when_item"):
        lines.append("stop_when bag(%s) < %s : %s is running out" % (
            _q(plan["stop_when_item"]), plan.get("stop_when_item_below", 0), plan["stop_when_item"]))
    lines.append("")
    spots: list[dict[str, Any]] = plan.get("spots", [])
    several = len(spots) > 1
    shared = several and has_drop_shares(plan)
    body: list[str] = []
    used = 0
    for s in spots:
        until = ""
        if shared and s.get("drop_minutes"):
            used += int(s["drop_minutes"])
            # its share ends once the day's drop time is down to what the spots after it get
            until = "until=drop_left >= 0 and drop_left <= %d" % max(0, DROP_DAY - used)
        body.append("# %s" % atlas.label(s["map"]))
        body.append(farm_line(s, several, until))
    if several and plan.get("rotation", "round") == "round" and not shared:
        lines.append("repeat")
        lines += ["  " + b for b in body]
        lines.append("end")
    else:
        lines += body
    return "\n".join(lines) + "\n"


def farm_line(s: dict[str, Any], several: bool, until: str = "") -> str:
    """One spot's farm line (its minutes and kills only matter with several spots)."""
    opts = ["map=%s" % s["map"]]
    if several and s.get("minutes"):
        opts.append("minutes=%s" % s["minutes"])
    if several and s.get("kills"):
        opts.append("kills=%s" % s["kills"])
    if s.get("patrol", "random") != "random":
        opts.append("patrol=%s" % s["patrol"])
    if s.get("area"):
        opts.append("area=%s" % ",".join(str(int(v)) for v in s["area"]))
    if s.get("patrol") == "points" and s.get("points"):
        opts.append("points=%s" % ";".join("%d,%d" % (int(p[0]), int(p[1])) for p in s["points"]))
    if s.get("nogo"):
        opts.append("nogo=%s" % ";".join("%d,%d,%d" % (int(c[0]), int(c[1]), int(c[2])) for c in s["nogo"]))
    if s.get("only"):
        opts.append("only=%s" % _q(s["only"]))
    if s.get("skip"):
        opts.append("skip=%s" % _q(s["skip"]))
    if s.get("elites") == "avoid":
        opts.append("elites=avoid")
    if until:
        opts.append(until)                  # until= takes the rest of the line: last
    return "farm " + " ".join(opts)


def endless_spot(plan: dict[str, Any]) -> int | None:
    """The first spot (from 0) that never ends while a later spot has a share of the drop time:
    no share, no minutes and no kills of its own. A plan with shares goes through its spots once,
    so the spots after it - and their shares - would never come. None when every spot can end
    (the last one may farm on: nothing waits for it)."""
    spots: list[dict[str, Any]] = plan.get("spots", [])
    if len(spots) < 2 or not has_drop_shares(plan):
        return None
    for i, s in enumerate(spots[:-1]):
        if not (s.get("drop_minutes") or s.get("minutes") or s.get("kills")):
            return i
    return None


def has_drop_shares(plan: dict[str, Any]) -> bool:
    """Whether a spot of this plan has a share of the day's drop time (then it runs once, in order)."""
    return any(s.get("drop_minutes") for s in plan.get("spots", []))


def path(name: str) -> str:
    return os.path.join(paths.PLANS, paths.safe_name(name) + ".json")


def names() -> list[str]:
    return sorted(n[:-5] for n in os.listdir(paths.PLANS) if n.endswith(".json"))


def load(name: str) -> dict[str, Any]:
    with open(path(name), encoding="utf-8") as f:
        checked = Plan.model_validate(json.load(f)).model_dump()
    p = dict(PLAN_DEFAULTS)
    p.update(upgrade(checked))
    p["spots"] = [dict(STEP_DEFAULTS, **s) for s in p["spots"]]
    return p


def save(plan: dict[str, Any]) -> None:
    json.save(path(plan["name"]), Plan.model_validate(plan).model_dump(), indent=2)


def export(plan_name: str, target: str) -> None:
    """Write one plan as a versioned portable file, atomically."""
    json.save(target, {"format": PLAN_EXPORT_FORMAT, "version": PLAN_EXPORT_VERSION,
                       "plan": load(plan_name)}, indent=2)


def read_import(source: str) -> dict[str, Any]:
    """Read and validate a portable export or an ordinary plan file without writing anything."""
    try:
        with open(source, encoding="utf-8-sig") as f:
            raw = json.load(f)
    except (OSError, ValueError) as e:
        raise ValueError("couldn't read the file: %s" % e) from None
    if not json.is_object(raw):
        raise ValueError("this isn't a plan file")
    if "format" in raw:
        if raw.get("format") != PLAN_EXPORT_FORMAT:
            raise ValueError("this isn't an Uncle Farmer plan export")
        if raw.get("version") != PLAN_EXPORT_VERSION:
            raise ValueError("this plan export is from an unsupported version")
        data = raw.get("plan")
        if not json.is_object(data):
            raise ValueError("the plan export has no plan")
    else:
        # Existing plans are plain JSON.  Requiring a plan field avoids importing an unrelated
        # object such as app settings, which Pydantic could otherwise fill with defaults.
        if not set(raw).intersection(PLAN_DEFAULTS):
            raise ValueError("this JSON file doesn't contain a plan")
        data = raw
    try:
        checked = Plan.model_validate(upgrade(copy.deepcopy(data))).model_dump()
    except ValidationError as e:
        fields = ", ".join(str(err["loc"][0]) for err in e.errors() if err["loc"])
        raise ValueError("the plan has invalid field%s: %s" %
                         ("s" if "," in fields else "", fields or "data")) from None
    plan = dict(PLAN_DEFAULTS)
    plan.update(checked)
    plan["spots"] = [dict(STEP_DEFAULTS, **spot) for spot in plan["spots"]]
    if not isinstance(plan["name"], str) or not plan["name"].strip():
        raise ValueError("the plan has no name")
    endless = endless_spot(plan)
    if endless is not None:
        raise ValueError("spot %d never ends, and the spots after it have a share of the drop time: give it "
                         "minutes, kills or a share" % (endless + 1))
    plan["name"] = plan["name"].strip()
    return plan


def delete(name: str) -> None:
    filelock.delete(path(name))


def same_file(a: str, b: str) -> bool:
    """True when two paths point at the same file on this disk (Windows ignores case), so a
    rename that only changes the case must not delete the file it just saved."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def script_path(name: str) -> str:
    return os.path.join(paths.SCRIPTS, paths.safe_name(name) + ".mwf")


def script_names() -> list[str]:
    return sorted(n[:-4] for n in os.listdir(paths.SCRIPTS) if n.endswith(".mwf"))
