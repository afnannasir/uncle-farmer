"""Ordered per-character Before/After farming sequences of Tools jobs and saved scripts.

Each stage has independent ordered steps with enabled, delay-after-success and deterministic
fields. A failed deterministic step stops the sequence; other ordinary failures are logged and
allow the next step. Legacy checkbox profiles retain JOBS order until edited. Tools use their
existing result contracts: counts (including zero), summaries and 'a trip was made' booleans
are not failure flags; failure is an exception. Scripts use the existing interpreter and isolate
their stop conditions. No new thread or timer is created for an individual step.

When (engine/runner.py):
  before  a plan or a script that farms, just before its first line - after the scan and the
          results' start, before the day's chores. Never for a Tools job, the walking assistant,
          `come`/`follow`, or a script without a farm or dungeon command.
  after   a farming run that ended by itself (the plan done, a stop rule, the daily stop), before
          the results close and before the game is closed or the PC shut down. Never after Stop, a
          lost or frozen game, `come`/`follow`, a tool or the walking assistant.
Each stage runs at most once a run, however often it is asked.

A deterministic preparation failure also prevents farming from starting. The player's Stop,
a frozen game or a lost connection always ends the sequence. Scheduled stages use the same
executor independently of the farming cycle."""

from __future__ import annotations

from collections.abc import Callable
import math
from typing import TYPE_CHECKING, Any

from .. import jsonio
from ..errors import carry_on
from ..i18n import N_, _
from ..link import AgentError
from . import activities, town
from .botparts.base import GameBroken, Stopped

if TYPE_CHECKING:
    from .bot import Bot

STAGES = ("before", "after")
# (job = its script command and results part, what the log calls it), in the order they run: the
# jobs a trigger runs straight, then those it runs through their script command (engine/activities.py)
JOBS: tuple[tuple[str, str], ...] = tuple((a.key, a.label) for a in activities.ACTIVITIES if a.direct)
# the town function each town job is (looked up when it runs)
TOWN_JOBS = {a.key: a.town for a in activities.ACTIVITIES if a.town}
FARMING_COMMANDS = ("farm",) + tuple(a.key for a in activities.ACTIVITIES if a.farms)
TOOLS = tuple((a.key, a.label) for a in activities.ACTIVITIES if a.listed) + (
    ("construction_scan auto=announcement", N_("scanning the constructions, then a gang announcement")),
    ("construction_scan auto=broadcast", N_("scanning the constructions, then a broadcast")),
    ("construction_scan auto=system", N_("scanning the constructions, then a system message")))


MAX_DELAY = 3600.0       # seconds: a longer wait is a typo, not a plan (a schedule does long gaps)


class TriggerFailed(Stopped):
    """A deterministic dependency failed; farming must not start after failed preparation."""


def step(action: str, delay: object = 0, enabled: object = True, deterministic: object = False) -> dict[str, Any]:
    if action not in dict(TOOLS) and not (action.startswith("script:") and action[7:].strip()):
        raise ValueError(_("Choose a tool or a saved script."))
    if (isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0
            or delay > MAX_DELAY):
        raise ValueError(_("The delay must be between 0 and {most} seconds.", most=int(MAX_DELAY)))
    if not isinstance(enabled, bool) or not isinstance(deterministic, bool):
        raise ValueError(_("The trigger's enabled setting must be true or false."))
    return {"action": action, "delay": float(delay), "enabled": enabled, "deterministic": deterministic}


def sequence(p: dict[str, Any], stage: str) -> dict[str, Any]:
    """Read a stage without mutating the profile. Missing stages retain legacy checkbox settings."""
    if stage not in STAGES:
        raise ValueError(_("Choose Before farming or After farming."))
    raw = p.get("trigger_sequences", {})
    if not jsonio.is_object(raw):
        raise ValueError(_("The trigger sequence settings are invalid."))
    if stage not in raw:
        return {"steps": [step(job, enabled=p.get(setting(job, stage)) is True) for job, _w in JOBS]}
    cfg = raw[stage]
    if not jsonio.is_object(cfg) or not jsonio.is_array(cfg.get("steps")):
        raise ValueError(_("The trigger sequence settings are invalid."))
    rows: list[dict[str, Any]] = []
    for row in cfg["steps"]:
        if not jsonio.is_object(row) or not isinstance(row.get("action"), str):
            raise ValueError(_("The trigger sequence settings are invalid."))
        rows.append(step(row["action"], row.get("delay", 0), row.get("enabled", True), row.get("deterministic", False)))
    return {"steps": rows}


def setting(job: str, stage: str) -> str:
    """The profile key of a job's tick at a stage: "sell_shards_before_farming"."""
    return "%s_%s_farming" % (job, stage)


SETTINGS: tuple[str, ...] = tuple(setting(job, stage) for stage in STAGES for job, _w in JOBS)


def steps(p: dict[str, Any], stage: str) -> list[dict[str, Any]]:
    """The stage's saved steps for a run. A list that can't be read (a hand-edited profile) ends
    the stage with a line saying where to fix it, not an 'unexpected error'."""
    try:
        rows: list[dict[str, Any]] = sequence(p, stage)["steps"]
    except ValueError as e:
        raise TriggerFailed(_("Character settings, Triggers: the {stage} list can't be read ({err}) - open "
                              "Character settings and save it again",
                              stage=_("Before farming") if stage == "before" else _("After farming"), err=e)) from e
    return rows


def ticked(p: dict[str, Any], stage: str) -> list[str]:
    """The jobs ticked for that stage, in the order they run. Only a real True counts."""
    return [row["action"] for row in steps(p, stage) if row["enabled"]]


def what(job: str) -> str:
    """What the log calls the job ("selling shards")."""
    if job.startswith("script:"):
        return _("script {name}", name=job[7:])
    return _(dict(TOOLS).get(job, job))


def action_text(b: Bot, action: str) -> str:
    """The script a step that isn't one of JOBS runs: a saved script's text, or the tool's own
    command (Dungeons with the ending place saved in Tools, Dungeons, as the Tools menu runs it)."""
    from .plan import script_path
    if action.startswith("script:"):
        try:
            with open(script_path(action[7:]), encoding="utf-8-sig") as source:
                return source.read()
        except OSError as e:
            raise Stopped(_("Couldn't open it: {err}", err=e)) from e
    if action == "dungeon":
        from . import dungeon
        return dungeon.script(dungeon.config(b.p))
    if action in dict(TOOLS):
        return action + "\n"
    raise ValueError(_("Choose a tool or a saved script."))


def step_farms(b: Bot, action: str) -> bool:
    """Does the step farm (a dungeon, or a saved script with a farm or dungeon line)? Such a step
    is held to the day's farming window like any farming run. A script that can't be read or
    parsed farms nothing: running it fails on its own."""
    if action in dict(JOBS):
        return False
    from .script import ScriptError, parse
    try:
        main, subs, _on_stop = parse(action_text(b, action))
    except (Stopped, ScriptError, ValueError):
        return False
    return farms(main, subs)


def run_action(b: Bot, action: str) -> None:
    if action in dict(JOBS):
        # Existing returns are counts, summaries or 'a trip was made', not success booleans.
        run_job(b, action)
        return
    from .script import Runner, ScriptError
    text = action_text(b, action)
    watches = b.watches
    b.watches = []
    try:
        try:
            Runner(b, text).run()
        except ScriptError as e:
            raise Stopped(str(e)) from e
    finally:
        b.watches = watches


def run_job(b: Bot, job: str, check_only: bool = False) -> object:
    """One job, the same whoever asks for it: the Tools menu (its script command), a trigger, or a
    player's script. Inside the job's own results part."""
    return activities.run(b, job, check_only)


def farms(nodes: list[Any], subs: dict[str, list[Any]] | None = None) -> bool:
    """Does a parsed script (its main lines and subs) farm somewhere: a farm or dungeon command,
    however deep in its blocks? A plan always does."""
    todo: list[Any] = list(nodes) + [n for body in (subs or {}).values() for n in body]
    while todo:
        node = todo.pop()
        if getattr(node, "kind", "") in FARMING_COMMANDS:
            return True
        todo.extend(getattr(node, "body", []) or [])
        for _cond, body in getattr(node, "branches", []) or []:
            todo.extend(body)
    return False


HERE = 0                 # first_farm_map: the first farm line names no map, so it farms where it stands


def first_farm_map(nodes: list[Any], subs: dict[str, list[Any]] | None = None) -> int | None:
    """The map a parsed script farms on first: its first farm line, in reading order and however
    deep in its blocks (a plan's spots sit inside a repeat), then its subs'. HERE when that line
    names no map; None when the script first goes into a dungeon, or doesn't farm."""
    def walk(body: list[Any]) -> tuple[bool, int | None]:
        for node in body:
            kind = getattr(node, "kind", "")
            if kind == "farm":
                mid = (getattr(node, "opts", {}) or {}).get("map")
                if isinstance(mid, str):
                    return True, None     # map=best: known only when the line runs
                return True, int(mid) if mid else HERE
            if kind == "dungeon":
                return True, None
            inner = list(getattr(node, "body", []) or [])
            for _cond, branch in getattr(node, "branches", []) or []:
                inner += branch
            found, mid = walk(inner)
            if found:
                return found, mid
        return False, None

    for body in [nodes] + list((subs or {}).values()):
        found, mid = walk(body)
        if found:
            return mid
    return None


def run_step(b: Bot, action: str, farming_window: Callable[[], None] | None,
             log: Callable[..., None] = lambda *_a: None) -> None:
    """One step: a step that farms first asks farming_window (the day's farming window), then it
    runs - unless it would read shard prices that are still fresh (_prices_fresh)."""
    if _prices_fresh(b, action, log):
        return
    if farming_window is not None and step_farms(b, action):
        farming_window()
    run_action(b, action)


def wait(b: Bot, seconds: float) -> None:
    """The wait after a step. It farms nothing, so the daily stop may pass during it (a step that
    farms after it is held to the window by run_stage's farming_window); Stop and a lost game
    still end it, and end the enclosing Run - the next step never starts."""
    deadline: float = getattr(b, "daily_stop_at", 0.0)     # (a stand-in bot may have none)
    b.daily_stop_at = 0.0
    try:
        b.sleep(seconds)
    finally:
        b.daily_stop_at = deadline


def _prices_fresh(b: Bot, job: str, log: Callable[..., None]) -> bool:
    """Updating shard prices in a Before or After farming list is a walk to the Shard Broker every
    time: a run started on its farming map went to town and back for prices read minutes before.
    Prices read less than town.SHARD_PRICES_FRESH ago are kept (True, and said)."""
    if job != "update_shard_prices":
        return False
    age = town.shard_prices_age(b)
    if age is None or age >= town.SHARD_PRICES_FRESH:
        return False
    log(_("the shard prices were read {hours} ago - not going to the Shard Broker for them again "
          "before {most} hours", hours=_hours(age), most=int(town.SHARD_PRICES_FRESH // 3600)))
    return True


def _hours(seconds: float) -> str:
    minutes = int(seconds // 60)
    return "%d h %02d min" % (minutes // 60, minutes % 60) if minutes >= 60 else "%d min" % minutes


def run_stage(b: Bot, stage: str, log: Callable[..., None], stopped: Callable[[], bool],
              farming_window: Callable[[], None] | None = None) -> list[tuple[str, bool]]:
    """Execute enabled steps in saved order, returning [(action, finished)] for attempted actions.
    A failed deterministic action raises TriggerFailed; unchecked ordinary failures continue.
    Stop/fatal connection errors end the stage. An interrupted delay propagates to the enclosing
    Run, so no subsequent action starts and cleanup plus scheduled history still run.
    farming_window (the runner's): called before a step that farms (step_farms); it raises
    Stopped when the day's farming is over and otherwise arms the daily stop for the step, so no
    step farms across the drop-time reset. A delay alone may run past the daily stop."""
    rows: list[dict[str, Any]] = [row for row in steps(b.p, stage) if row["enabled"]]
    jobs = [row["action"] for row in rows]
    done: list[tuple[str, bool]] = []
    if not jobs:
        return done
    head = _("before farming") if stage == "before" else _("after farming")
    for index, job in enumerate(jobs):
        if stopped():
            break
        log(_("{stage}, automatically: {job}", stage=head, job=what(job)))
        try:
            run_step(b, job, farming_window, log)
            done.append((job, True))
        except GameBroken:
            done.append((job, False))
            if stage == "before":
                raise
            break
        except Stopped as e:
            done.append((job, False))
            if stopped() or getattr(e, "final", False):
                if stage == "before":
                    raise
                break
            log(_("{job} didn't finish: {why}", job=what(job), why=e), True)
        except (AgentError, OSError) as e:
            done.append((job, False))
            log(_("{job} didn't finish: {why}", job=what(job), why=e), True)
            if stage == "before":
                raise
            break                    # the game is gone: nothing else can be done in it
        except Exception as e:       # a bug in one job must not keep the others from running
            carry_on(e, "the %s-farming job %s" % (stage, job))
            done.append((job, False))
            log(_("{job} didn't finish: {why}", job=what(job), why=repr(e)), True)
        if not done[-1][1] and rows[index]["deterministic"]:
            raise TriggerFailed(_("{stage}: sequence stopped because {job} failed", stage=head, job=what(job)))
        if done[-1][1] and index + 1 < len(rows) and rows[index]["delay"]:
            delay = rows[index]["delay"]
            log(_("Waiting {seconds} seconds after {job}", seconds=delay, job=what(job)))
            wait(b, delay)
    ok = [what(j) for j, fine in done if fine]
    bad = [what(j) for j, fine in done if not fine]
    if bad:
        log(_("{stage}: done {ok}; didn't finish {bad}", stage=head, ok=", ".join(ok) or _("nothing"),
              bad=", ".join(bad)))
    else:
        log(_("{stage}: done {ok}", stage=head, ok=", ".join(ok) or _("nothing")))
    return done
