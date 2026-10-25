"""Farming plans: what the plan editor makes. A plan is saved as JSON and runs as a script.

    {
      "name": "Turkey cave",
      "spots": [ {"map": 5302, "minutes": 0, "kills": 0, "patrol": "full", "area": null,
                  "only": "", "skip": "", "elites": "fight"} ],
      "rotation": "round",               # several spots: "round" (in turn, again and again) or "once"
      "stop_after_minutes": 300,         # 0 = no limit
      "stop_at_clock": "",               # "HH:MM", empty = no
      "stop_after_kills": 0,
      "stop_when_item": "", "stop_when_item_below": 0,   # e.g. potions running out
      "drop_keep_minutes": 0,            # drop time to leave unused; 0 = the character's setting
      "end": {"how": "start"}            # where every end goes: start | place (+ map, cell) |
    }                                    # hearthstone | safe | here

How long a plan farms is decided in one place, its stop rules (plus the character's own: drop
time, a full bag, deaths, broken gear). With one spot it farms there until it stops; a spot's own
minutes or kills only matter with several spots: how long before moving on to the next.
"""

import os

from pydantic import BaseModel, ConfigDict, Field

from .. import atlas, filelock, jsonio as json, paths

PLAN_DEFAULTS = {
    "name": "",
    "spots": [],
    "rotation": "round",
    "stop_after_minutes": 0,
    "stop_at_clock": "",
    "stop_after_kills": 0,
    "stop_when_item": "",
    "stop_when_item_below": 0,
    "drop_keep_minutes": 0,
    "end": {"how": "start"},
}

STEP_DEFAULTS = {"map": 5121, "minutes": 30, "kills": 0, "patrol": "random", "area": None,
                 "only": "", "skip": "", "elites": "fight"}
END_HOWS = ("start", "place", "hearthstone", "safe", "here")


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
    patrol: str = STEP_DEFAULTS["patrol"]
    area: list[int] | None = STEP_DEFAULTS["area"]
    only: str = STEP_DEFAULTS["only"]
    skip: str = STEP_DEFAULTS["skip"]
    elites: str = STEP_DEFAULTS["elites"]


class Plan(BaseModel):
    """The shape of a plan file, checked by Pydantic when one is loaded or saved. Unknown keys are
    kept: load() runs upgrade() on them afterwards, and a newer version's key must survive."""
    model_config = ConfigDict(extra="allow")

    name: str = PLAN_DEFAULTS["name"]
    spots: list[Spot] = Field(default_factory=list)
    rotation: str = PLAN_DEFAULTS["rotation"]
    stop_after_minutes: int = PLAN_DEFAULTS["stop_after_minutes"]
    stop_at_clock: str = PLAN_DEFAULTS["stop_at_clock"]
    stop_after_kills: int = PLAN_DEFAULTS["stop_after_kills"]
    stop_when_item: str = PLAN_DEFAULTS["stop_when_item"]
    stop_when_item_below: int = PLAN_DEFAULTS["stop_when_item_below"]
    drop_keep_minutes: int = PLAN_DEFAULTS["drop_keep_minutes"]
    end: End = Field(default_factory=End)


def new_step(map_id=None):
    s = dict(STEP_DEFAULTS)
    if map_id:
        s["map"] = int(map_id)
    return s


def upgrade(p):
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


def _q(text):
    """Quote a value for a script line."""
    return '"%s"' % str(text).replace('"', "'")


def end_line(end):
    how = (end or {}).get("how", "start")
    if how == "place" and end.get("map"):
        cell = end.get("cell")
        return "end_at %s%s" % (end["map"], (" at %d,%d" % tuple(cell)) if cell else "")
    return "end_at %s" % (how if how in END_HOWS and how != "place" else "start")


def to_script(plan):
    lines = ["# %s" % (plan.get("name") or "plan"),
             "# Made with the plan editor. Saving it as a script lets you edit it by hand.", "",
             end_line(plan.get("end"))]
    if plan.get("drop_keep_minutes"):
        lines.append("keep_drop %d" % int(plan["drop_keep_minutes"]))
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
    spots = plan.get("spots", [])
    several = len(spots) > 1
    body = []
    for s in spots:
        opts = ["map=%s" % s["map"]]
        if several and s.get("minutes"):
            opts.append("minutes=%s" % s["minutes"])
        if several and s.get("kills"):
            opts.append("kills=%s" % s["kills"])
        if s.get("patrol", "random") != "random":
            opts.append("patrol=%s" % s["patrol"])
        if s.get("area"):
            opts.append("area=%s" % ",".join(str(int(v)) for v in s["area"]))
        if s.get("only"):
            opts.append("only=%s" % _q(s["only"]))
        if s.get("skip"):
            opts.append("skip=%s" % _q(s["skip"]))
        if s.get("elites") == "avoid":
            opts.append("elites=avoid")
        body.append("# %s" % atlas.label(s["map"]))
        body.append("farm " + " ".join(opts))
    if several and plan.get("rotation", "round") == "round":
        lines.append("repeat")
        lines += ["  " + b for b in body]
        lines.append("end")
    else:
        lines += body
    return "\n".join(lines) + "\n"


def path(name):
    return os.path.join(paths.PLANS, paths.safe_name(name) + ".json")


def names():
    return sorted(n[:-5] for n in os.listdir(paths.PLANS) if n.endswith(".json"))


def load(name):
    with open(path(name), encoding="utf-8") as f:
        checked = Plan.model_validate(json.load(f)).model_dump()
    p = dict(PLAN_DEFAULTS)
    p.update(upgrade(checked))
    p["spots"] = [dict(STEP_DEFAULTS, **s) for s in p["spots"]]
    return p


def save(plan):
    json.save(path(plan["name"]), Plan.model_validate(plan).model_dump(), indent=2)


def delete(name):
    filelock.delete(path(name))


def same_file(a, b):
    """True when two paths point at the same file on this disk (Windows ignores case), so a
    rename that only changes the case must not delete the file it just saved."""
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def script_path(name):
    return os.path.join(paths.SCRIPTS, paths.safe_name(name) + ".mwf")


def script_names():
    return sorted(n[:-4] for n in os.listdir(paths.SCRIPTS) if n.endswith(".mwf"))
