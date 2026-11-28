"""Every job the bot does besides farming, in one list (master plan, Phase 4: one Activity shape).

The Tools menu, the Before/After farming triggers, schedules and a player's script all name a job
by the same key and run it through `run`, so a new activity is one entry here: its key (the script
command and the results part), what the log calls it, whether a trigger runs it straight (a town
job with nothing to choose) or through its script command, whether it farms (held to the day's
farming window), and how it runs. Before this, triggers.JOBS/TOOLS/TOWN_JOBS and the script's
`_do_*` commands each kept their own copy.
"""
from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .. import stats
from ..i18n import N_, _
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

Run = Callable[["Bot", bool, Mapping[str, Any]], object]


@dataclass(frozen=True)
class Activity:
    key: str                # the script command, the trigger step and the results part
    label: str              # what the log calls it (N_: translated when shown)
    run: Run                # (bot, check only, the script command's options) -> its result
    direct: bool = False    # a trigger runs it straight (a town job); else through its script command
    farms: bool = False     # it farms: held to the day's farming window, its loot is farming loot
    checks: bool = False    # it has a Check only (walks there, reads, changes nothing)
    town: str = ""          # the engine.town function a town job is
    listed: bool = True     # offered as it is in the trigger and schedule lists


def _town(fn: str) -> Run:
    def run(b: Bot, _check: bool, _o: Mapping[str, Any]) -> object:
        from . import town          # looked up when it runs, so tests can stand in for it
        job: Callable[[Bot], object] = getattr(town, fn)
        return job(b)
    return run


def _gather(b: Bot, check: bool, _o: Mapping[str, Any]) -> object:
    from . import gathering
    return gathering.run(b, gathering.config(b.p), check)


def _lose_xp(b: Bot, check: bool, _o: Mapping[str, Any]) -> object:
    from . import xploss
    return xploss.run(b, xploss.config(b.p), check)


def _chores(b: Bot, check: bool, o: Mapping[str, Any]) -> object:
    from . import chores
    return chores.run(b, chores.config(b.p), check, o.get("only") or None)


def _bounties(b: Bot, check: bool, _o: Mapping[str, Any]) -> object:
    from . import chores
    return chores.collect_bounties(b, chores.config(b.p), check)


def _construction_scan(b: Bot, _check: bool, o: Mapping[str, Any]) -> object:
    from . import constructionscan
    if o.get("auto"):
        return constructionscan.scan_and_publish(b, o["auto"], o.get("lines"), o.get("taken"))
    if o.get("publish"):
        return constructionscan.publish(b, o["publish"], o.get("lines"))
    return constructionscan.scan(b, o.get("taken"))


def _crafting(key: str, setting: str, tool: str) -> Run:
    def run(b: Bot, check: bool, _o: Mapping[str, Any]) -> object:
        from . import crafting
        cfg: dict[str, Any] = b.p.get(setting) or {}
        if not cfg:
            raise Stopped(_("set it up first: Tools, {tool}", tool=_(tool)))
        return (crafting.gem_maker if key == "gem_maker" else crafting.enhance)(b, cfg, check)
    return run


def _gem_breaker(b: Bot, check: bool, _o: Mapping[str, Any]) -> object:
    from . import gembreak          # (it says "set it up first" itself)
    return gembreak.run(b, gembreak.config(b.p), check)


def _potion_maker(b: Bot, check: bool, _o: Mapping[str, Any]) -> object:
    from . import potionmaker      # (it says "set it up first" itself)
    return potionmaker.run(b, potionmaker.config(b.p), check)


def _dungeon(b: Bot, check: bool, o: Mapping[str, Any]) -> object:
    from . import dungeon
    return dungeon.run(b, dungeon.config(b.p), check, o.get("cave"))


def _sell_collection(b: Bot, _check: bool, o: Mapping[str, Any]) -> object:
    from . import town
    sold = town.sell_collection(b, o["kinds"])
    if not sold:
        b.log(_("nothing of the collection was sold"))
    return sold


# In the order the Tools menu and the trigger lists show them (the first seven: the old JOBS order)
ACTIVITIES: tuple[Activity, ...] = (
    Activity("sell_shards", N_("selling shards"), _town("sell_shards_now"), direct=True, town="sell_shards_now"),
    Activity("clean_bag", N_("cleaning the bag"), _town("clean_bag"), direct=True, town="clean_bag"),
    Activity("update_shard_prices", N_("updating shard prices"), _town("update_shard_prices"), direct=True, town="update_shard_prices"),
    Activity("gather", N_("gathering"), _gather, direct=True, checks=True),
    Activity("store_items", N_("storing items"), _town("store_items"), direct=True, town="store_items"),
    Activity("restock", N_("restocking supplies"), _town("restock_now"), direct=True, town="restock_now"),
    Activity("lose_xp", N_("losing experience"), _lose_xp, direct=True, checks=True),
    Activity("chores", N_("daily chores"), _chores, checks=True),
    Activity("gem_maker", N_("making gems"), _crafting("gem_maker", "gem_maker", N_("Gem Maker")), checks=True),
    Activity("gem_breaker", N_("breaking gems"), _gem_breaker, checks=True),
    Activity("potion_maker", N_("making potions"), _potion_maker, checks=True),
    Activity("enhance", N_("enhancing equipment"), _crafting("enhance", "enhancer", N_("Equipment Enhancer")),
             checks=True),
    # a dungeon farms: its loot is farming loot, so it has no results part of its own
    Activity("dungeon", N_("running a dungeon"), _dungeon, farms=True, checks=True),
    Activity("sell_collection", N_("selling the collection"), _sell_collection),
    # on request only (Tools, Hunting bounties); the scanner is listed by how it publishes (triggers.TOOLS)
    Activity("bounties", N_("asking for hunting bounties"), _bounties, checks=True, listed=False),
    Activity("construction_scan", N_("scanning the constructions"), _construction_scan, listed=False),
)
BY_KEY: dict[str, Activity] = {a.key: a for a in ACTIVITIES}


def get(key: str) -> Activity:
    return BY_KEY[key]


def run(b: Bot, key: str, check: bool = False, options: Mapping[str, Any] | None = None) -> object:
    """One job, the same whoever asks for it (the Tools menu, a trigger, a schedule, a script):
    inside its own results part unless it farms."""
    a = BY_KEY[key]
    if a.farms:
        return a.run(b, check, options or {})
    with stats.doing(b, key):
        return a.run(b, check, options or {})
