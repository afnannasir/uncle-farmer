"""Opening the treasure chests that turn up while farming.

What the game does, watched live on Margit Rainforest (2026-11-14, chestwatch.py):
  - Now and then a chest appears somewhere on a field map, and the game tells the players on that
    map (channel 37): "There's a Shining Small Chest in Margit Rainforest. Go find it and see
    what's inside!" - only a big loot, once opened, is told to the whole server. Players know of
    about twenty field maps they turn up on (Margit Rainforest, Wolf Prairie, Carter Plain, Fred
    Forest...); any map is looked at here, and chesthunt.py goes looking for an announced one.
  - To the agent the chest is a monster named "Chest" (side -2, as monsters are) with 20,000,000
    max hp: exactly a town NPC's, which is why targets() never took it (NPC_HP) and the bot walked
    past every chest. Its hp goes down a little as players hit it by mistake; it doesn't die.
  - It opens like an NPC: from about 4 steps, select it and press Enter; a menu offers "Open the
    chest", and choosing it opens it at once, with no wait. The chest leaves sight, the loot is in
    the bag straight away (9190 arnebia and 24 Level 1 Obsidian Shard, that time), and channel 37
    says "The lucky adventurer has opened the treasure chest in Margit Rainforest.".
  - The first player there gets it: one that is gone before we arrive was someone else's.

So between fights the farm loop looks for a chest in sight - or one seen in the last few minutes,
even in the middle of a fight, and out of sight since - and, when there is one we can walk to
with no boss near it and nothing hitting us, goes and opens it the way talk() opens an NPC. A chest
that wouldn't open is left alone for a while, so a chest behind a wall can't hold up the farm. A
player who'd rather not can turn it off (profile open_chests).
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from ..i18n import _
from . import bosses, transactions

if TYPE_CHECKING:
    from .bot import Bot

NAME = "Chest"                # the chest's name to the agent, whatever the broadcast calls it
OPEN = r"open the chest"      # its menu entry
MOST_STEPS = 80               # walking steps we go out of our way for a chest
REMEMBERED_STEPS = 160        # ... for one seen earlier (in a fight) that is out of sight now
REMEMBER = 300.0              # seconds a chest seen and gone out of sight is still gone back for
GIVE_UP = 300.0               # seconds a chest that wouldn't open is left alone
GONE_WAIT = 2.0               # seconds the game is given to take the opened chest away


class Chests:
    """What the bot knows about the chests it has met during a run."""

    def __init__(self) -> None:
        self.opened = 0
        self.skip: dict[int, float] = {}     # chest id -> left alone until (clock time)
        self.seen: dict[int, tuple[Any, float, float, float]] = {}   # chest id -> (map, x, y, when)

    def note(self, snap: dict[str, Any], now: float) -> None:
        """Every look at the game (snap): the chests in sight, kept with where and when. A chest
        that came into sight in the middle of a fight and went out of it before the fight ended
        was forgotten: live, one went unopened for three minutes that way."""
        where: dict[str, Any] = snap.get("map") or {}
        mid = where.get("id")
        for e in snap.get("entities", ()):
            if is_chest(e):
                self.seen[e["id"]] = (mid, e["x"], e["y"], now)

    def remembered(self, mid: Any, now: float, sight: set[int]) -> list[dict[str, Any]]:
        """Chests seen on this map within REMEMBER seconds that are out of sight now, as entities
        to walk back to."""
        for k in [k for k, v in self.seen.items() if now - v[3] > REMEMBER]:
            del self.seen[k]
        return [{"id": k, "kind": "monster", "name": NAME, "x": x, "y": y, "remembered": True}
                for k, (m, x, y, _t) in self.seen.items() if m == mid and k not in sight]

    def forget(self, mid: Any = None, cid: int | None = None) -> None:
        """A chest opened (by us or, as the game announces, by someone): no going back for it."""
        for k in [k for k, v in self.seen.items() if k == cid or (cid is None and v[0] == mid)]:
            del self.seen[k]


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
    known: Chests | None = getattr(bot, "chests", None)
    if (not found and not (known and known.seen)) or not bot.p.get("open_chests", True) or bot.in_instance:
        return None
    now = bot.clock.time()
    where: dict[str, Any] = snap.get("map") or {}
    found += bot.chests.remembered(where.get("id"), now, {e["id"] for e in found})
    if not found or bot.attackers():
        return None
    keep = bot.p.get("boss_distance", 40)
    boss_list = bosses.in_sight(snap) if keep else []
    best: tuple[float, dict[str, Any]] | None = None
    for e in found:
        if bot.chests.skip.get(e["id"], 0) > now or (boss_list and bosses.too_close(e, boss_list, keep)):
            continue
        steps = bot.walk_dist(snap, e, REMEMBERED_STEPS if e.get("remembered") else MOST_STEPS)
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
    n = int(round(math.hypot(chest["x"] - me["x"], chest["y"] - me["y"])))
    if chest.get("remembered"):
        bot.log(_("going back to the chest seen {n} steps away: going to open it", n=n))
    else:
        bot.log(_("a chest in sight, {n} steps away: going to open it", n=n))
    before = _bag(bot)
    try:
        with bot.timed("approach"):
            opened = _open(bot, chest)
    finally:
        bot.close_dialogs()
        bot.set_modes()           # talk() left the target key on NPCs
        bot.modes_done = True
    c = bot.chests
    if opened or not in_sight(bot.snap(), chest["id"]):
        c.forget(cid=chest["id"])
    if opened:
        c.opened += 1
        if bot.tally is not None:
            bot.tally.note_chest()
        bot.log(_("opened a chest ({n} this run)", n=c.opened), speak=True)
        _say_loot(bot, before)
    elif in_sight(bot.snap(), chest["id"]):
        c.skip[chest["id"]] = bot.clock.time() + GIVE_UP
    else:
        bot.log(_("the chest is gone: someone else opened it"))
    return True


LOOT_WAIT = 3.0               # seconds the chest's arnebia and items get to reach the bag


def _bag(bot: Bot) -> dict[str, int] | None:
    """The bag before a chest is opened (None when it can't be read: no loot line then)."""
    try:
        return transactions.counts(bot)
    except Exception:          # a reading that fails must never stop the opening
        return None


def _say_loot(bot: Bot, before: dict[str, int] | None) -> None:
    """What the chest gave, into the log: the game puts it in the bag at once, the arnebia a
    moment later (as a sale's). Kept to measure what a chest is worth against the time a hunt
    costs (a night of 15 hunts on Margit Rainforest could only be judged on two old readings)."""
    if before is None:
        return
    try:
        deal = transactions.InventoryTransaction(before)
        after = deal.paid(bot, transactions.counts(bot), LOOT_WAIT)
        names = {(r.get("name") or "").strip().lower(): (r.get("name") or "").strip() for r in bot.bag_rows()}
    except Exception:
        return
    got = [(names.get(n) or n, after[n] - before.get(n, 0)) for n in after if after[n] > before.get(n, 0)]
    got.sort(key=lambda g: (g[0].lower() != transactions.ARNEBIA, g[0].lower()))
    if got:
        bot.log(_("the chest gave: {loot}", loot=", ".join("+{:,} {}".format(d, n) for n, d in got)))
    else:
        bot.log(_("the chest gave nothing that reached the bag"))


def _open(bot: Bot, chest: dict[str, Any]) -> bool:
    """Walk up to it, Enter, "Open the chest"; True once it has gone from sight. A chest still
    there after that is tried once more, choosing with the keys (bot.choose keys), when the
    character's menu_keys_retry is ticked (off by default): on one
    player's computer the direct choice was made and the chest stayed, 24 times in 25, while
    the same menu opened chests on another computer. What the game said meanwhile goes to the
    log when it still didn't open."""
    from . import chores           # chores uses the town errands, which use the whole Bot
    since = chores.said_mark(bot)
    tries = (False, True) if bot.p.get("menu_keys_retry", False) else (False,)
    for keys in tries:
        if not bot.talk(NAME, at=(chest["x"], chest["y"])):
            return False
        if (bot.choose(OPEN, keys=True) if keys else bot.choose(OPEN)) is None:
            return False
        end = bot.clock.time() + GONE_WAIT
        while bot.clock.time() < end:
            if not in_sight(bot.snap(), chest["id"]):
                return True
            bot.sleep(0.25)
        if not keys and len(tries) > 1:
            bot.log(_("chose \"Open the chest\" but the chest is still there - once more with the keys"))
    bot.log(_("chose \"Open the chest\" but the chest is still there"))
    said = chores.said_since(bot, since)
    if said:
        bot.log(_("the game said: {lines}", lines=" / ".join(said[-3:])))
    return False
