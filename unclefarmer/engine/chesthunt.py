"""Looking for an announced treasure chest (chest_hunt_minutes).

What the game does (chestwatch.py, Margit Rainforest, 2026-11-14 and 2026-11-20):
  - When a chest appears on a field map, the players on that map alone are told, on channel 37
    with no sender: "There's a Shining Small Chest in Margit Rainforest. Go find it and see what's
    inside!". The line says where on the map no more than the game does: a player has to go and
    find it (the radar keys reach 20 steps; the agent sees things up to about 60).
  - "The lucky adventurer has opened the treasure chest in Margit Rainforest." follows when someone
    opens it.
  - On Margit Rainforest a chest came about every 21-24 minutes, and stayed unopened 3 to more than
    18 minutes (one player farming there opened 2 of 4, by chance). On a crowded map they go fast.

So when our map's chest is announced, the farm loop stops picking monsters (it still fights what
attacks us) and sweeps the farm area in lanes LANE steps apart - the ground the agent sees from
each lane overlaps the next - riding the Swift fusion beast as on a trip, nearest unseen ground
first. chests.open_nearby opens the chest once it is in sight. The hunt ends when the chest is
opened (by us or as the game announces), when the whole area has been seen, or after
chest_hunt_minutes (0: never hunt, chests are only opened when they turn up in sight).
"""

from __future__ import annotations

import math
import re
from collections.abc import Sequence
from typing import Any, TYPE_CHECKING

from .. import atlas
from ..i18n import _
from . import chests

if TYPE_CHECKING:
    from .bot import Bot

CHANNEL = 37
APPEARED = re.compile(r"There's a .*?Chest in (.+?)\. Go find it", re.I)
OPENED = re.compile(r"opened the treasure chest in (.+?)\.", re.I)
LISTEN_EVERY = 2.0      # seconds between two reads of the chat
LANE = 100              # steps between two sweep lanes (the agent sees about 60 each side)
VIEW = 50               # a sweep point within this of where we have been counts as seen


class Hunt:
    """One map's announced chest, and the ground looked over for it."""

    def __init__(self, mid: int, since: float, opened: int = 0) -> None:
        self.mid = mid
        self.since = since
        self.opened = opened          # chests we had opened when it began: one more ends it
        self.points: list[tuple[int, int]] | None = None
        self.seen: list[tuple[float, float]] = []


def _state(bot: Bot) -> dict[str, Any]:
    st: dict[str, Any] = bot.__dict__.setdefault("chest_hunt", {"mark": None, "listened": -1e9, "hunt": None})
    return st


def listen(bot: Bot, mid: int) -> Hunt | None:
    """Read the chat's new lines (every LISTEN_EVERY seconds): our map's chest announced starts a
    hunt, its opening ends it. Returns the hunt under way, if any."""
    st = _state(bot)
    now = bot.clock.time()
    hunt: Hunt | None = st["hunt"]
    if hunt is not None and hunt.mid != mid:
        hunt = st["hunt"] = None          # another map: that chest is not ours to look for
    if now - st["listened"] < LISTEN_EVERY:
        return hunt
    st["listened"] = now
    try:
        if st["mark"] is None:            # only what is said from now on
            st["mark"] = int(bot.a.json("chat 0").get("last", 0))
            return hunt
        got = bot.a.json("chat %d" % st["mark"])
    except Exception:                     # an agent without the chat: no hunts, never a crash
        return hunt
    st["mark"] = int(got.get("last", st["mark"]) or st["mark"])
    here = atlas.name(mid).strip().lower()
    lines: list[dict[str, Any]] = got.get("chat") or []
    for line in lines:
        if line.get("channel") != CHANNEL or line.get("name"):
            continue
        text = str(line.get("text") or "")
        m = APPEARED.search(text)
        if m and m.group(1).strip().lower() == here:
            hunt = st["hunt"] = Hunt(mid, now, bot.chests.opened)
            continue
        m = OPENED.search(text)
        if m and m.group(1).strip().lower() == here:
            hunt = st["hunt"] = None
            bot.chests.forget(mid)
    return hunt


def sweep_points(mid: int, area: Sequence[float] | None, me: dict[str, Any],
                 lane: int = LANE) -> list[tuple[int, int]]:
    """Walkable points along lanes `lane` apart over the area (the whole map without one), lane by
    lane, back and forth: what the hunt walks to, nearest unseen first. Only those in the part of
    the map we stand in: walk_to finds no way into a part walled off from ours."""
    m = atlas.model(mid)
    mx, my = int(me["x"]), int(me["y"])
    ours = m.comp_of(mx, my) if m.ok(mx, my) else None
    x0, y0, x1, y1 = (int(v) for v in (area or (1, 1, m.w, m.h)))
    out: list[tuple[int, int]] = []
    for i, y in enumerate(range(y0 + lane // 2, y1 + 1, lane)):
        xs = list(range(x0 + lane // 2, x1 + 1, lane))
        for x in (xs if i % 2 == 0 else xs[::-1]):
            c = m.nearest_walkable(x, y, radius=lane // 3)
            if c is None or m.portal[c[1], c[0]] or (ours is not None and m.comp_of(c[0], c[1]) != ours):
                continue
            out.append((int(c[0]), int(c[1])))
    return out


def hunt(bot: Bot, snap: dict[str, Any], mid: int, area: Sequence[float] | None) -> bool:
    """Once each time round the farm loop, after chests in sight had their chance: our map's chest
    has been announced and is still to be found, so walk on to the nearest ground not looked over
    yet. True when we walked (the loop looks again). Not while something attacks us (that fight
    first), nor in a dungeon, nor with chests switched off."""
    minutes = bot.p.get("chest_hunt_minutes", 5)
    if not minutes or not bot.p.get("open_chests", True) or getattr(bot, "in_instance", False):
        return False
    h = listen(bot, mid)
    if h is not None and bot.chests.opened > h.opened:
        _state(bot)["hunt"] = h = None    # we opened it: found
    if h is None or chests.in_sight(snap) or bot.attackers():
        return False
    now = bot.clock.time()
    me = snap["self"]
    if h.points is None:
        h.points = sweep_points(mid, area, me)
        bot.log(_("a chest turned up on {map} - looking for it (up to {n} minutes)",
                  map=atlas.label(mid), n=int(minutes)), speak=True)
    h.seen.append((me["x"], me["y"]))
    if now - h.since > minutes * 60:
        return _give_up(bot, _("no chest found in {n} minutes - back to farming", n=int(minutes)))
    left = [p for p in h.points if all(math.hypot(p[0] - x, p[1] - y) > VIEW for x, y in h.seen)]
    if not left:
        return _give_up(bot, _("looked over the whole area, no chest - back to farming"))
    goal = min(left, key=lambda p: math.hypot(p[0] - me["x"], p[1] - me["y"]))
    h.seen.append(goal)               # tried once, whatever the walk does: never the same point twice

    def found(_me: dict[str, Any]) -> bool:
        return bool(chests.in_sight(bot.snap())) or listen(bot, mid) is not h
    was = getattr(bot, "traveling", False)
    bot.traveling = True              # the Swift fusion beast, and a stop for whatever attacks us
    try:
        with bot.timed("search"):
            bot.walk_to((float(goal[0]), float(goal[1])), near=found)
    finally:
        bot.traveling = was
    return True


def _give_up(bot: Bot, why: str) -> bool:
    _state(bot)["hunt"] = None
    bot.log(why)
    return False
