"""Never out of a dungeon by the bot's own doing.

Every dungeon puts the player out by itself: its timer (the experience dungeons), the chest at its end,
or the time it allows running out. Leaving one ourselves (the Action menu's "leave", a teleport stone,
a spell) loses the day's visit, and players lost theirs that way: a run started inside a dungeon
with its farm elsewhere, a tool, a Before or After farming list, the end of a run going to its end
place - every trip out started with a "leave" leg. So a trip (travel) that starts in a dungeon
farms it instead, until the game moves us out (or our own end-of-dungeon chest takes us out, as it
is there for), then goes on from wherever that is. Farming a dungeon costs no drop time: the game
counts only the monsters killed on public maps.

A wedding hall is kept too: leaving it through "leave" loses the gift its NPC gives at the end of
the wedding, and it has no monsters to farm while waiting. The run stops there instead, for the
player to take the gift.

Not kept: the walking assistant (the player asked to go), following a team leader or coming to a
player who called (they decide), the bot's own end of an experience dungeon's visit after its timer
(dungeon.py). Secret rooms, the gang's areas and events are left as before.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING, Any, NoReturn

from .. import atlas
from ..i18n import _
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

HOLD_MOST = 2 * 3600.0        # seconds farmed inside before giving up waiting for the game (said by a player:
                              # a dungeon ends well before)
CHEST_TRIES = 3               # our end chest that didn't take us out: tried this many times, then farmed past
DUNGEON_KINDS = ("dungeon", "dungeon?")
# maps data/map_kinds.json calls "instance" that are dungeons: data/dungeons.json lists them as a
# dungeon's maps, the experience dungeons, and those a player recognised (2026-11-28)
MORE_DUNGEONS = frozenset({
    1564, 1633, 1636, 1742, 1743, 3804,      # open sea, zagrama canyon, bottomless pit, Gomez manor, Centralia,
                                             # Blood Mountain Villa
    1792,                                    # White Bone Cave
    1568, 1067, 1766, 1767, 1768, 1664,      # Beast Mountain, Tiger Mountain, dark monastery (3), ghost hotel
    1789, 1786, 4531,                        # Dark Demon Tower, Soul Realm, Demon Barracks
})
WEDDING = "wedding"
# what a farm sets for itself and clears when it ends: the farm inside is held within a trip, which may
# itself be the way to another farm (its travel comes before it sets them) - put back as they were
FARM_STATE = ("farm_filter", "spot_no_go", "on_farm_run", "farm_top_hp", "farming_on", "traveling")


def kept(mid: int) -> str | None:
    """"dungeon" or "wedding" for a map the bot never leaves by itself, else None."""
    kind = atlas.kind(mid)
    if kind in DUNGEON_KINDS or int(mid) in MORE_DUNGEONS:
        return "dungeon"
    if kind == WEDDING:
        return WEDDING
    return None


def applies(bot: Bot) -> bool:
    """Not for the walking assistant, following a leader, or coming to a player who called."""
    return not (getattr(bot, "walk_only", False) or getattr(bot, "following", None)
                or getattr(bot, "coming", False))


def hold(bot: Bot, mid: int, dst: int | None) -> None:
    """A trip to dst (None: the end of the run, wherever it goes) that starts on mid, a map kept: a
    dungeon is farmed until the game moves us out (then the trip goes on from there); a wedding hall
    stops the run where it is. Stopped (staying put) after HOLD_MOST seconds still inside. A stop here
    is never the end of farming (bot.held_inside): no After farming list, no closing the game, no next
    character of a chain - the character is waiting for its player."""
    what = kept(mid)
    if what == WEDDING:
        _stay(bot, _("in {map}: not leaving it - the wedding's gift would be lost. Take the gift from its NPC, "
                     "then start again", map=atlas.label(mid)))
    if dst is None:
        bot.log(_("in {map}: not leaving the dungeon by ourselves (the day's visit would be lost) - farming it "
                  "until the game moves us out, then ending the run", map=atlas.label(mid)), speak=True)
    else:
        bot.log(_("in {map}: not leaving the dungeon by ourselves (the day's visit would be lost) - farming it "
                  "until the game moves us out, then on to {dest}", map=atlas.label(mid), dest=atlas.label(dst)),
                speak=True)
    began = bot.clock.time()
    tries = 0

    def timed_out() -> bool:
        return bot.clock.time() - began >= HOLD_MOST

    def over() -> bool:
        return bot.map_id() != mid or (tries < CHEST_TRIES and own_chest(bot)) or timed_out()
    while True:
        _farm_inside(bot, mid, over)
        if bot.map_id() != mid:
            return                    # the game moved us out (or a death did): the trip goes on from there
        if tries < CHEST_TRIES and own_chest(bot):
            tries += 1
            # the dungeon is done: its chest is the way out it gives (and, should talking to it fail,
            # the Action menu's leave: nothing is lost any more)
            if bot.leave_instance():
                return
            continue
        if timed_out():
            _stay(bot, _("still in {map} after {h} hours - the game hasn't moved us out; leave it yourself",
                         map=atlas.label(mid), h=int(HOLD_MOST // 3600)))
        bot.sleep(1.0)                # the farm gave back early (nothing to fight): look again shortly


def _stay(bot: Bot, why: str) -> NoReturn:
    """The run ends here, inside, waiting for the player: no end place, nothing after it."""
    bot.held_inside = True
    stop = Stopped(why)
    stop.stay = True
    raise stop


def _farm_inside(bot: Bot, mid: int, over: Callable[[], bool]) -> None:
    """The dungeon farmed as a dungeon, not as a trip (a trip walks on a Swift beast and stops for
    what attacks it), and the farm's own state put back as the trip had it."""
    held = {name: getattr(bot, name, None) for name in FARM_STATE if hasattr(bot, name)}
    bot.traveling = False
    try:
        bot.farm(mid, until=over, instance=True)
    finally:
        for name, value in held.items():
            setattr(bot, name, value)


def own_chest(bot: Bot) -> bool:
    """Our own end-of-dungeon chest ("Treasure chest belongs to <us>") in sight: the dungeon is done."""
    snap: dict[str, Any] = bot.snap()
    me_row: dict[str, Any] = snap.get("self") or {}
    me = " ".join(str(me_row.get("name") or "").split()).lower()
    if not me:
        return False
    want = "treasure chest belongs to " + me
    rows: list[dict[str, Any]] = snap.get("entities") or []
    return any(" ".join(str(e.get("name") or "").split()).lower() == want for e in rows)
