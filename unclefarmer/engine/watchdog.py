"""The no-kill watchdog: a farm that goes no_kill_minutes without a single kill is doing nothing.

On an unwatched night a run can farm on for hours and kill nothing: every monster taken by other
players, a part of the map with nothing left, a target the game keeps refusing, a walk that ends
in the same corner. The farm loop asks this module each time round (check):

- after no_kill_minutes with no kill, it says so and walks to another part of the map: the
  reachable cell furthest from where we are, of a few drawn over the whole farm area;
- the same again after another no_kill_minutes;
- after STOP_AFTER of them in a row with still no kill, the run ends the way a used-up drop time
  ends it (finish(): the plan's end place) - except on a farm that fights only some names (a
  rare monster may be a long while turning up): that one only tries elsewhere.

Only time spent farming this map counts: a trip away (town, a boss left behind, a death), a long
pause or a new farm starts the minutes again, and a kill starts everything again. Never in a
dungeon (its timer ends the visit) nor on the walking assistant. 0 minutes: off.
"""

from __future__ import annotations

import math
from collections.abc import Sequence
from typing import Any, TYPE_CHECKING

from ..i18n import _
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

STOP_AFTER = 3        # warnings in a row with no kill before the run ends
GAP = 120.0           # seconds between two looks that mean we were away (a trip, a long pause)
CELLS = 12            # cells drawn when looking for another part of the map


def start(bot: Bot, map_id: int | None = None) -> dict[str, Any]:
    """A farm of map_id begins: count from now. Returns the count's state."""
    now = bot.clock.time()
    st: dict[str, Any] = {"kills": bot.kills, "since": now, "tick": now, "map": map_id, "warned": 0}
    bot.__dict__["no_kill"] = st
    return st


def check(bot: Bot, map_id: int, area: Sequence[float] | None, hunting: bool = False) -> bool:
    """Once each time round the farm loop. True when it walked us somewhere else (the loop looks
    again); raises Stopped after STOP_AFTER warnings with no kill between them - unless hunting
    (the farm fights only some names: a rare monster can take longer than that to turn up)."""
    minutes = bot.p.get("no_kill_minutes", 10)
    if not minutes or getattr(bot, "in_instance", False) or getattr(bot, "walk_only", False):
        return False
    got: dict[str, Any] | None = bot.__dict__.get("no_kill")
    st = got if got is not None else start(bot, map_id)
    now = bot.clock.time()
    if bot.kills != st["kills"]:
        st.update(kills=bot.kills, since=now, warned=0, map=map_id)     # a kill: all is well
    elif st["map"] != map_id or now - st["tick"] > GAP:
        st.update(since=now, map=map_id)    # back from elsewhere (a trip, a death, a long pause)
    st["tick"] = now
    if now - st["since"] < minutes * 60:
        return False
    st["warned"] += 1
    st["since"] = now
    if st["warned"] >= STOP_AFTER and not hunting:
        bot.log(_("no kill for {n} minutes - stopping the run", n=int(minutes * STOP_AFTER)), speak=True)
        bot.finish()
        raise Stopped(_("no kill for {n} minutes", n=int(minutes * STOP_AFTER)))
    warned: int = st["warned"]
    bot.log(_("no kill for {n} minutes - trying another part of the map", n=int(minutes * warned)),
            speak=True)
    cell = elsewhere(bot, map_id, area)
    if cell is None:
        return False
    with bot.timed("search"):
        bot.walk_to(cell)
    return True


def elsewhere(bot: Bot, map_id: int, area: Sequence[float] | None) -> tuple[int, int] | None:
    """The reachable cell furthest from us, of a few drawn over the whole farm area."""
    me = bot.snap()["self"]
    from . import bosses
    mine = bosses.player_no_go(bot)      # the player's no-go circles: never walked into
    cells = [c for c in (bot.random_cell(map_id, area, me, radius=1000, clear_of=mine) for _i in range(CELLS)) if c]
    return max(cells, key=lambda c: math.hypot(c[0] - me["x"], c[1] - me["y"]), default=None)
