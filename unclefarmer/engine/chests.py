"""Opening the treasure chests that turn up while farming.

What the game does, watched live on Margit Rainforest (2026-11-14, chestwatch.py):
  - Now and then a chest appears somewhere on a field map, and the game tells the whole server on
    its broadcast channel (37): "There's a Shining Small Chest in Margit Rainforest. Go find it and
    see what's inside!". Players know of about twenty field maps they turn up on (Margit
    Rainforest, Wolf Prairie, Carter Plain, Fred Forest...); any map is looked at here.
  - To the agent the chest is a monster named "Chest" (side -2, as monsters are) with 20,000,000
    max hp: exactly a town NPC's, which is why targets() never took it (NPC_HP) and the bot walked
    past every chest. Its hp goes down a little as players hit it by mistake; it doesn't die.
  - It opens like an NPC: from about 4 steps, select it and press Enter; a menu offers "Open the
    chest", and choosing it opens it at once, with no wait. The chest leaves sight, the loot is in
    the bag straight away (9190 arnebia and 24 Level 1 Obsidian Shard, that time), and channel 37
    says "The lucky adventurer has opened the treasure chest in Margit Rainforest.".
  - The first player there gets it: one that is gone before we arrive was someone else's.

So between fights the farm loop looks for a chest in sight and, when there is one we can walk to
with no boss near it and nothing hitting us, goes and opens it the way talk() opens an NPC. A chest
that wouldn't open is left alone for a while, so a chest behind a wall can't hold up the farm. A
player who'd rather not can turn it off (profile open_chests).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ..i18n import _
from . import bosses

if TYPE_CHECKING:
    from .bot import Bot

NAME = "Chest"                # the chest's name to the agent, whatever the broadcast calls it
OPEN = r"open the chest"      # its menu entry
MOST_STEPS = 80               # walking steps we go out of our way for a chest
GIVE_UP = 300.0               # seconds a chest that wouldn't open is left alone
GONE_WAIT = 2.0               # seconds the game is given to take the opened chest away


class Chests:
    """What the bot knows about the chests it has met during a run."""

    def __init__(self) -> None:
        self.opened = 0
        self.skip: dict[int, float] = {}     # chest id -> left alone until (clock time)


def is_chest(e: dict[str, Any]) -> bool:
    return (e.get("kind") == "monster" and (e.get("name") or "").strip().lower() == NAME.lower()
            and e.get("hp", 0) > 0)


def in_sight(snap: dict[str, Any], cid: int | None = None) -> list[dict[str, Any]]:
    """The chests in this look; cid: only that one."""
    return [e for e in snap.get("entities", ()) if is_chest(e) and (cid is None or e["id"] == cid)]


def pick(bot: Bot, snap: dict[str, Any]) -> dict[str, Any] | None:
    """The chest to open now: the nearest one in sight we can walk to, no boss near it and not
    left alone. None when there is none, or when something is hitting us (that fight first)."""
    found = in_sight(snap)
    if not found or not bot.p.get("open_chests", True) or bot.in_instance or bot.attackers():
        return None
    now = bot.clock.time()
    keep = bot.p.get("boss_distance", 40)
    boss_list = bosses.in_sight(snap) if keep else []
    best: tuple[float, dict[str, Any]] | None = None
    for e in found:
        if bot.chests.skip.get(e["id"], 0) > now or (boss_list and bosses.too_close(e, boss_list, keep)):
            continue
        steps = bot.walk_dist(snap, e, MOST_STEPS)
        if steps != math.inf and (best is None or steps < best[0]):
            best = (steps, e)
    return best[1] if best else None


def open_nearby(bot: Bot, snap: dict[str, Any]) -> bool:
    """Open the chest pick() finds. True when it went for one (opened or not): the farm loop
    looks again before anything else."""
    chest = pick(bot, snap)
    if chest is None:
        return False
    me = snap["self"]
    bot.log(_("a chest in sight, {n} steps away: going to open it",
              n=int(round(math.hypot(chest["x"] - me["x"], chest["y"] - me["y"])))))
    try:
        with bot.timed("approach"):
            opened = _open(bot, chest)
    finally:
        bot.close_dialogs()
        bot.set_modes()           # talk() left the target key on NPCs
        bot.modes_done = True
    c = bot.chests
    if opened:
        c.opened += 1
        if bot.tally is not None:
            bot.tally.note_chest()
        bot.log(_("opened a chest ({n} this run)", n=c.opened), speak=True)
    elif in_sight(bot.snap(), chest["id"]):
        c.skip[chest["id"]] = bot.clock.time() + GIVE_UP
    else:
        bot.log(_("the chest is gone: someone else opened it"))
    return True


def _open(bot: Bot, chest: dict[str, Any]) -> bool:
    """Walk up to it, Enter, "Open the chest"; True once it has gone from sight."""
    if not bot.talk(NAME, at=(chest["x"], chest["y"])) or bot.choose(OPEN) is None:
        return False
    end = bot.clock.time() + GONE_WAIT
    while bot.clock.time() < end:
        if not in_sight(bot.snap(), chest["id"]):
            return True
        bot.sleep(0.25)
    bot.log(_("chose \"Open the chest\" but the chest is still there"))
    return False
