"""Character settings, Triggers: Tools jobs the bot runs by itself before farming starts and after
it ends - Sell shards now, Clean the bag now, Update shard prices, Gathering and Store items now.

Each job has two ticks of its own, one per stage (profile keys `<job>_before_farming` and
`<job>_after_farming`); the two never touch each other, so a job ticked at both stages runs once
before farming and once after. The jobs run one after the other in JOBS order, each the same code
the Tools menu and its script command run (run_job), and each inside its own results part
(stats.doing), so what it brings in or spends is kept apart from the farming loot.

When (engine/runner.py):
  before  a plan or a script that farms, just before its first line - after the scan and the
          results' start, before the day's chores. Never for a Tools job, the walking assistant,
          `come`/`follow`, or a script without a farm or dungeon command.
  after   a farming run that ended by itself (the plan done, a stop rule, the daily stop), before
          the results close and before the game is closed or the PC shut down. Never after Stop, a
          lost or frozen game, `come`/`follow`, a tool or the walking assistant.
Each stage runs at most once a run, however often it is asked.

None of these jobs is something farming needs to start (farming has its own bag trips and
restocks), so a job that doesn't finish is said aloud and logged with why, and the next one and
farming go on. The player's Stop, a frozen game or a lost connection ends the stage there - and
before farming, the run with it."""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any

from .. import stats
from ..errors import carry_on
from ..i18n import N_, _
from ..link import AgentError
from . import gathering, town
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
)
# the town function each job is (looked up when it runs); gathering is gathering.run with its settings
TOWN_JOBS = {"sell_shards": "sell_shards_now", "clean_bag": "clean_bag",
             "update_shard_prices": "update_shard_prices", "store_items": "store_items"}
FARMING_COMMANDS = ("farm", "dungeon")


def setting(job: str, stage: str) -> str:
    """The profile key of a job's tick at a stage: "sell_shards_before_farming"."""
    return "%s_%s_farming" % (job, stage)


SETTINGS: tuple[str, ...] = tuple(setting(job, stage) for stage in STAGES for job, _w in JOBS)


def ticked(p: dict[str, Any], stage: str) -> list[str]:
    """The jobs ticked for that stage, in the order they run. Only a real True counts."""
    return [job for job, _w in JOBS if p.get(setting(job, stage)) is True]


def what(job: str) -> str:
    """What the log calls the job ("selling shards")."""
    return _(next(w for j, w in JOBS if j == job))


def run_job(b: Bot, job: str, check_only: bool = False) -> object:
    """One job, the same whoever asks for it: the Tools menu (its script command), a trigger, or a
    player's script. Inside the job's own results part."""
    with stats.doing(b, job):
        if job == "gather":
            return gathering.run(b, gathering.config(b.p), check_only)
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
    """Every job ticked for the stage, one after the other: [(job, finished)]. log(line, speak)
    is the run's log; stopped() says the player pressed Stop. A job that fails is said and the next
    still runs; Stop, a frozen game or a lost game ends the stage (before farming they are raised
    again, so the run ends as it would have; after farming the run is ending anyway)."""
    jobs = ticked(b.p, stage)
    done: list[tuple[str, bool]] = []
    if not jobs:
        return done
    head = _("before farming") if stage == "before" else _("after farming")
    for job in jobs:
        if stopped():
            break
        log(_("{stage}, automatically: {job}", stage=head, job=what(job)))
        try:
            run_job(b, job)
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
    ok = [what(j) for j, fine in done if fine]
    bad = [what(j) for j, fine in done if not fine]
    if bad:
        log(_("{stage}: done {ok}; didn't finish {bad}", stage=head, ok=", ".join(ok) or _("nothing"),
              bad=", ".join(bad)))
    else:
        log(_("{stage}: done {ok}", stage=head, ok=", ".join(ok) or _("nothing")))
    return done
