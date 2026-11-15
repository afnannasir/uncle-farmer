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

from .. import jsonio, stats
from ..errors import carry_on
from ..i18n import N_, _
from ..link import AgentError
from . import gathering, town, xploss
from .botparts.base import GameBroken, Stopped

if TYPE_CHECKING:
    from .bot import Bot

STAGES = ("before", "after")
# (job = its script command and results part, what the log calls it), in the order they run
JOBS: tuple[tuple[str, str], ...] = (
    ("sell_shards", N_("selling shards")),
    ("clean_bag", N_("cleaning the bag")),
    ("update_shard_prices", N_("updating shard prices")),
    ("gather", N_("gathering")),
    ("store_items", N_("storing items")),
    ("restock", N_("restocking supplies")),
    ("lose_xp", N_("losing experience")),
)
# the town function each job is (looked up when it runs); gathering is gathering.run with its settings
TOWN_JOBS = {"sell_shards": "sell_shards_now", "clean_bag": "clean_bag",
             "update_shard_prices": "update_shard_prices", "store_items": "store_items", "restock": "restock_now"}
FARMING_COMMANDS = ("farm", "dungeon")
TOOLS = JOBS + (("chores", N_("daily chores")), ("gem_maker", N_("making gems")),
                ("enhance", N_("enhancing equipment")), ("dungeon", N_("running a dungeon")),
                ("sell_collection", N_("selling the collection")))


class TriggerFailed(Stopped):
    """A deterministic dependency failed; farming must not start after failed preparation."""


def step(action: str, delay: object = 0, enabled: object = True, deterministic: object = False) -> dict[str, Any]:
    if action not in dict(TOOLS) and not (action.startswith("script:") and action[7:].strip()):
        raise ValueError(_("Choose a tool or a saved script."))
    if isinstance(delay, bool) or not isinstance(delay, (int, float)) or not math.isfinite(delay) or delay < 0:
        raise ValueError(_("The delay must be a finite number of seconds, zero or greater."))
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


def ticked(p: dict[str, Any], stage: str) -> list[str]:
    """The jobs ticked for that stage, in the order they run. Only a real True counts."""
    rows: list[dict[str, Any]] = sequence(p, stage)["steps"]
    return [row["action"] for row in rows if row["enabled"]]


def what(job: str) -> str:
    """What the log calls the job ("selling shards")."""
    if job.startswith("script:"):
        return _("script {name}", name=job[7:])
    return _(dict(TOOLS).get(job, job))


def run_action(b: Bot, action: str) -> None:
    if action in dict(JOBS):
        # Existing returns are counts, summaries or 'a trip was made', not success booleans.
        run_job(b, action)
        return
    from .script import Runner, ScriptError
    from .plan import script_path
    if action.startswith("script:"):
        try:
            with open(script_path(action[7:]), encoding="utf-8-sig") as source:
                text = source.read()
        except OSError as e:
            raise Stopped(_("Couldn't open it: {err}", err=e)) from e
    elif action in dict(TOOLS):
        text = action + "\n"
    else:
        raise ValueError(_("Choose a tool or a saved script."))
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
    with stats.doing(b, job):
        if job == "gather":
            return gathering.run(b, gathering.config(b.p), check_only)
        if job == "lose_xp":
            return xploss.run(b, xploss.config(b.p), check_only)
        fn: Callable[[Bot], object] = getattr(town, TOWN_JOBS[job])
        return fn(b)


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


def run_stage(b: Bot, stage: str, log: Callable[..., None], stopped: Callable[[], bool]) -> list[tuple[str, bool]]:
    """Execute enabled steps in saved order, returning [(action, finished)] for attempted actions.
    A failed deterministic action raises TriggerFailed; unchecked ordinary failures continue.
    Stop/fatal connection errors end the stage. An interrupted delay propagates to the enclosing
    Run, so no subsequent action starts and cleanup plus scheduled history still run."""
    cfg = sequence(b.p, stage)
    rows: list[dict[str, Any]] = [row for row in cfg["steps"] if row["enabled"]]
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
            run_action(b, job)
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
            b.sleep(delay)  # cancellation/lost game ends the enclosing Run, never advances a step
    ok = [what(j) for j, fine in done if fine]
    bad = [what(j) for j, fine in done if not fine]
    if bad:
        log(_("{stage}: done {ok}; didn't finish {bad}", stage=head, ok=", ".join(ok) or _("nothing"),
              bad=", ".join(bad)))
    else:
        log(_("{stage}: done {ok}", stage=head, ok=", ".join(ok) or _("nothing")))
    return done
