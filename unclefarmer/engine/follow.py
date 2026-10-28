"""Coming to a player and following one around (the trusted-player commands `come` and `follow`).

Where the player is comes from two places:
  - the game itself: another player in view is an entity of kind "player" with their name;
  - `locate`, when the player is one of the characters Uncle Farmer runs on this PC: their own game
    knows their map and cell, wherever they are (bot.locate(name) -> (map id, x, y) or None);
  - `last_seen`, for anyone else: where any of our games last had them in view, a few minutes at
    most (bot.last_seen(name) -> (map id, x, y, seconds ago) or None).
  - the game, for a teammate on our map: the quick phrase "/6" targets the next teammate here and
    the server answers with where they stand (agent v27 `team`), out of sight or not.
A player out of view and not ours is looked for where they were last seen, on their map, or
beyond the exit they were last seen beside.

The team leader is reached faster: Shift+P (W_AMOVE) teleports to them. The server allows it only
on the same map, from further than 20 yards, not for the leader themself, and once per ~50 s; it
answers each refusal with a spoken line, which is how we learn who leads (taken to someone else, or
told the leader isn't here while our player is, means our player isn't the leader).

A healer heals the player and the rest of the team in sight on the way (teamheal).

Following fights what attacks us (the walking code's fight-back) and helps the player: a monster
near them that is casting or already wounded is one they are fighting. The agent reports who casts,
not at whom, so that is the best sign there is.
"""

from __future__ import annotations

import math
import re
from collections.abc import Callable

from .. import atlas, gamekeys
from ..clock import clock_for
from ..i18n import _
from .bot import snap_map, Bot, Stopped
from .botparts.base import STOPPING, note_failure
from .botparts.parts import Sighting
from .. import phrases as phrase_book
from . import teamheal
from typing import Any

NEAR = 3          # tiles: close enough to stand beside them
GAP = 4           # tiles: further than this and we catch up
HELP = 10         # tiles from the player: monsters this close are theirs to fight, so ours to help with
EXIT_NEAR = 6     # tiles: last seen this close to an exit and gone = they took it
LOST = 60.0       # seconds without any sign of them before following ends
TELEPORT_FROM = 25    # tiles: further than this, Shift+P to them when they lead the team
TELEPORT_GAP = 51.0   # seconds: the game's teleport cooldown, measured 50.4 s
LEADER_NEAR = 22      # tiles: after a teleport, this close to our player means they are the leader
ASK_EVERY = 3.0       # seconds between two "/6" rounds when they are out of sight
ASK_PRESSES = 5       # "/6" presses per round: each names the next teammate on the map


def find_player(snap: dict[str, Any], name: str):
    want = name.strip().lower()
    for e in snap.get("entities", []):
        if e.get("kind") == "player" and (e.get("name") or "").strip().lower() == want:
            return e
    return None


def _dist(a: dict[str, Any], b: dict[str, Any]) -> float:
    return math.hypot(a["x"] - b["x"], a["y"] - b["y"])


def _locate(bot: Bot, name: str):
    fn = getattr(bot, "locate", None)
    if not fn:
        return None
    try:
        return fn(name)
    except STOPPING:
        raise                       # a stop, a death or a lost agent is not "player not found"
    except Exception as e:
        note_failure(bot, "finding the player", e)
        return None


def _last_seen(bot: Bot, name: str):
    fn = getattr(bot, "last_seen", None)
    if not fn:
        return None
    try:
        return fn(name)
    except STOPPING:
        raise
    except Exception as e:
        note_failure(bot, "the player's last sighting", e)
        return None


def _beside(bot: Bot, p: dict[str, Any]):
    """A walkable cell next to the player (their own cell may be taken by them, or a wall edge)."""
    m = atlas.model(bot.map_id())
    x, y = int(round(p["x"])), int(round(p["y"]))
    return m.nearest_walkable(x, y, 4) or (x, y)


def _walk_up(bot: Bot, name: str, cell: tuple[int, int]) -> str:
    """Walk towards cell, stopping as soon as the player is within NEAR, or has moved on more than
    GAP from the cell (walking on to where they were left us trailing behind; the caller aims
    again at where they are now)."""
    def close(me: dict[str, Any]):
        p = find_player(bot.snap(), name)
        if p is None:
            return False
        return _dist(me, p) <= NEAR or math.hypot(p["x"] - cell[0], p["y"] - cell[1]) > GAP
    return bot.walk_to(cell, near=close)


def _agent(bot: Bot):
    return getattr(bot, "agent_version", 0)


def teammate_position(bot: Bot, name: str):
    """(x, y) of a teammate on our map, from the game's answer to the "/6" quick phrase, or None
    (no such phrase, no team, they're on another map, or a fight is on: the phrase changes our
    target)."""
    now = clock_for(bot).time()
    if _agent(bot) < 27 or bot.in_fight or now < getattr(bot, "team_ask_at", 0.0):
        return None
    bot.team_ask_at = now + ASK_EVERY
    slot = phrase_book.slot_of(getattr(bot, "phrases", None) or {}, "/6")
    if not slot:
        return None
    key = gamekeys.phrase_key_list()[slot - 1]
    want = name.strip().lower()
    t0 = bot.a.json("team").get("now", 0)
    for _press in range(ASK_PRESSES):
        bot.key(key)
        bot.sleep(0.35)
        for m in bot.a.json("team").get("members", []):
            if (m.get("name") or "").strip().lower() == want and m.get("age_ms", 1e9) < 1500:
                return m["x"], m["y"]
        if any("no team" in (s.get("text") or "").lower() for s in bot.a.json("said %d" % t0).get("said", [])):
            bot.team_ask_at = now + 60
            return None
    return None


def teleport(bot: Bot, name: str, them: dict[str, Any] | None = None) -> bool:
    """Shift+P to the team leader, when that may be our player. True when it put us within
    LEADER_NEAR of them. them: where they are, when known (decides who leads from a refusal)."""
    tp = bot.__dict__.setdefault("_teleport", {"off": None, "next": 0.0, "not_leader": set()})
    who = name.strip().lower()
    now = clock_for(bot).time()
    if _agent(bot) < 25 or tp["off"] or now < tp["next"] or who in tp["not_leader"] or bot.in_fight:
        return False
    key = bot.p.get("teleport_key") or "shift+p"
    before = bot.me()
    t0 = bot.a.json("said 0").get("now", 0)
    bot.key(key)
    text, moved = "", False
    for _i in range(8):
        bot.sleep(0.25)
        text = " ".join(s.get("text") or "" for s in bot.a.json("said %d" % t0).get("said", [])).lower()
        moved = _dist(bot.me(), before) > 5
        if text or moved:
            break
    far = them is not None and _dist(before, them) > LEADER_NEAR
    if "leader cannot teleport" in text:
        tp["off"] = "leader"              # we lead: nobody to teleport to
        return False
    if "no team" in text:
        tp["off"] = "no team"
        return False
    wait = re.search(r"(\d+) seconds?;? teleport cooldown", text)
    if wait:
        tp["next"] = now + int(wait.group(1)) + 0.5
        return False
    if "not on this map" in text or "within 20 yards" in text:
        if far or ("not on this map" in text and them is not None):
            tp["not_leader"].add(who)       # the leader is elsewhere, and our player is here
        tp["next"] = now + 10
        return False
    if not moved:
        tp["next"] = now + 10
        return False
    tp["next"] = now + TELEPORT_GAP
    p = find_player(bot.snap(), name)
    if p is not None and _dist(bot.me(), p) <= LEADER_NEAR:
        bot.log(_("teleported to {name}, the team leader", name=name))
        return True
    tp["not_leader"].add(who)
    bot.log(_("teleported to the team leader - {name} isn't the leader, walking from here", name=name))
    return False


def _look_for(bot: Bot, name: str, where: Sighting) -> None:
    """Go to where the player was last seen (map id, x, y, ...), stopping once they are in view."""
    mid, x, y = where[0], int(where[1]), int(where[2])
    bot.log(_("{name} was last seen on {map} at {x}, {y} - looking there", name=name, map=atlas.label(mid),
              x=x, y=y))
    if bot.map_id() != mid:
        bot.travel(mid, (x, y))
    else:
        bot.walk_to((x, y), near=lambda me: find_player(bot.snap(), name) is not None)


def parse_location(text: str):
    """(map id or None, (x, y) or None) from what a player sends after `come`: a map name or number,
    or what the game says for the location key E, copied with Shift+C - "<name>;<name>;<x>;<y>;<name>"
    (the client joins the parts with ";" and rounds x and y to whole tiles; the LAST part is the map's
    name, set on every map change - the first two describe the character's spot and can happen to
    match some other map's name). So the parts are tried as a map from the last one back; the first
    two whole numbers are the cell."""
    text = (text or "").strip()
    if not text:
        return None, None
    mid = atlas.resolve(text)
    if mid is not None:
        return mid, None
    parts = [p.strip() for p in re.split(r"[;\n]", text) if p.strip()]
    if len(parts) == 1:
        parts = [p.strip() for p in parts[0].split(",") if p.strip()]
    numbers = [int(p) for p in parts if re.fullmatch(r"-?\d+", p)]
    cell = (numbers[0], numbers[1]) if len(numbers) >= 2 else None
    for part in reversed(parts):
        if not re.fullmatch(r"-?\d+", part):
            mid = atlas.resolve(part)
            if mid is not None:
                return mid, cell
    # a name inside a longer part ("you are on Wolf Prairie"): the longest map name found, if only one
    low = text.lower()
    found = [(len(m["name"]), i) for i, m in atlas.maps().items()
             if len(m["name"].strip()) > 3 and m["name"].strip().lower() in low]
    if found:
        top = max(found)[0]
        best = {atlas.resolve(atlas.maps()[i]["name"]) for n, i in found if n == top} - {None}
        if len(best) == 1:
            return best.pop(), cell
    return None, cell


def come_to(bot: Bot, name: str, map_id: int | None = None, cell: tuple[int, int] | None = None) -> str:
    """Walk up to the player. map_id / cell: where they said they are (`come <map>`, or the
    location key E's text). Returns a short answer for them."""
    looked: Sighting | None = None
    went_to_cell = False

    def in_view(_me: dict[str, Any]) -> bool:
        return find_player(bot.snap(), name) is not None

    for _try in range(4):
        bot.check()
        snap = bot.snap()
        p = find_player(snap, name)
        if p:
            if _come_up(bot, name, snap, p):
                return _("Here, next to you.")
            continue                       # out of view again: look for them the other ways
        if _come_where_known(bot, name, in_view):
            continue
        if map_id is not None and bot.map_id() != map_id:
            bot.log(_("going to {map} to find {name}", map=atlas.label(map_id), name=name))
            bot.travel(map_id, cell)
            continue
        if cell and not went_to_cell and (map_id is None or bot.map_id() == map_id):
            went_to_cell = True
            bot.log(_("going to {x}, {y}, where {name} said they are", x=cell[0], y=cell[1], name=name))
            bot.walk_to((cell[0], cell[1]), near=in_view)
            continue
        seen = _last_seen(bot, name)
        if seen and looked is None and (map_id is None or seen[0] == map_id):
            looked = seen
            _look_for(bot, name, seen)
            continue
        break
    return _come_answer(bot, went_to_cell, looked, map_id)


def _come_up(bot: Bot, name: str, snap: dict[str, Any], p: dict[str, Any]):
    """The player is in view: there by the teleport phrase if far, then walking up, aiming again
    where they are now. True when we are next to them; False when they went out of view."""
    them: dict[str, Any] | None = p
    if _dist(snap["self"], p) > TELEPORT_FROM:
        teleport(bot, name, p)
        them = find_player(bot.snap(), name)
    for _aim in range(8):          # a walking player: aim again where they are now
        if them is None or _dist(bot.me(), them) <= NEAR:
            break
        _walk_up(bot, name, _beside(bot, them))
        them = find_player(bot.snap(), name)
    return them is not None


def _come_where_known(bot: Bot, name: str, in_view: Callable[[dict[str, Any]], bool]) -> bool:
    """Where one of our games plays them, or where the team phrase says they are: go there. True
    when there was such a place."""
    where = _locate(bot, name)
    if where:
        mid, x, y = where
        if bot.map_id() != mid:
            bot.log(_("going to {name} on {map}", name=name, map=atlas.label(mid)))
            bot.travel(mid, (x, y))
        elif not (_dist(bot.me(), {"x": x, "y": y}) > TELEPORT_FROM
                  and teleport(bot, name, {"x": x, "y": y})):
            _walk_up(bot, name, (x, y))
        return True
    spot = teammate_position(bot, name)
    if spot:
        them = {"x": spot[0], "y": spot[1]}
        if not (_dist(bot.me(), them) > TELEPORT_FROM and teleport(bot, name, them)):
            bot.log(_("{name} is on this map at {x}, {y} - going there", name=name, x=int(spot[0]),
                      y=int(spot[1])))
            bot.walk_to((int(spot[0]), int(spot[1])), near=in_view)
        return True
    return False


def _come_answer(bot: Bot, went_to_cell: bool, looked: Sighting | None, map_id: int | None) -> str:
    """What to tell a player we couldn't find, and how they can help."""
    me = bot.me()
    if went_to_cell:
        return _("I'm at {x}, {y} on {map}, where you said, but can't see you. Press E again and send it.",
                 x=int(me["x"]), y=int(me["y"]), map=atlas.label(bot.map_id()))
    if looked:
        return _("I went to where I last saw you ({map} at {x}, {y}) but can't see you. Press E in the game, "
                 "copy it with Shift+C and send: come <what you copied>",
                 map=atlas.label(looked[0]), x=int(looked[1]), y=int(looked[2]))
    if map_id is not None and bot.map_id() == map_id:
        return _("I'm on {map} at {x}, {y} but can't see you. Press E in the game, copy it with Shift+C and "
                 "send: come <what you copied>", map=atlas.label(map_id), x=int(me["x"]), y=int(me["y"]))
    return _("I can't see you. Press E in the game, copy it with Shift+C and send: come <what you copied>")


def help_target(bot: Bot, snap: dict[str, Any], p: dict[str, Any]):
    """A monster the player is fighting: within HELP of them and casting or wounded, nearest to
    them first. Anything attacking us is handled by the walking code's fight-back."""
    hitting = bot.attackers()
    mobs = [e for e in bot.targets(snap) if _dist(e, p) <= HELP
            and (e["id"] in hitting or e.get("hp", 0) < e.get("maxhp", 0))]
    return min(mobs, key=lambda e: _dist(e, p)) if mobs else None


def _exit_taken(mid: int, seen: tuple[float, float]) -> dict[str, Any] | None:
    """The exit whose stand is within EXIT_NEAR of where the player was last seen, or None."""
    best = None
    for e in atlas.maps().get(mid, {}).get("exits", []):
        if e.get("dest") is None or not e.get("stand"):
            continue
        d = math.hypot(e["stand"][0] - seen[0], e["stand"][1] - seen[1])
        if d <= EXIT_NEAR and (best is None or d < best[0]):
            best = (d, e)
    return best[1] if best else None


def follow(bot: Bot, name: str):
    """Stay with the player until stopped: catch up when they move off, help with their fights,
    and go along when they change map. Ends (Stopped) after LOST seconds without any sign of them."""
    bot.log(_("following {name}", name=name), speak=True)
    # Buffs are held back while following (the leader may not want them, and each one costs a
    # global cooldown and mana); the `buff` message turns them on for this follow.
    bot.following = name
    try:
        _follow(bot, name)
    finally:
        bot.following = None


def _follow(bot: Bot, name: str):
    seen: tuple[int, float, float] | None = None     # (map id, x, y) where we last saw them
    lost_since = None
    searched = False            # walked to where one of our games last saw them
    while True:
        bot.check()
        snap = bot.snap()
        me = bot.look_after(snap["self"])
        here = snap_map(snap)
        p = find_player(snap, name)
        if teamheal.look_after(bot, snap):
            continue
        teamheal.learn(bot)
        if p:
            lost_since = None
            seen = (here, p["x"], p["y"])
            _keep_up(bot, name, snap, me, p)
            continue
        found = _follow_where_known(bot, name, here, me)
        if found:
            lost_since = None
            if found is not True:
                seen = found
            continue
        lost_since = lost_since or clock_for(bot).time()
        if seen is None and not searched:
            searched = True
            spot = _last_seen(bot, name)
            if spot:
                _look_for(bot, name, spot)
                lost_since = clock_for(bot).time()      # the walk there doesn't count as time lost
                continue
        if seen and seen[0] == here:
            went = _after_them(bot, name, here, me, seen)
            if went:
                seen = None if went == "exit" else seen
                continue
        if clock_for(bot).time() - lost_since > LOST:
            raise Stopped(_("lost sight of {name}", name=name))
        bot.sleep(0.5)


def _keep_up(bot: Bot, name: str, snap: dict[str, Any], me: dict[str, Any], p: dict[str, Any]):
    """The player is in view: help with their fight, else catch up (by the teleport phrase when
    far), else wait a moment."""
    mob = help_target(bot, snap, p)
    if mob:
        bot.fight(mob)
        return
    if _dist(me, p) > TELEPORT_FROM and teleport(bot, name, p):
        return
    if _dist(me, p) > GAP:
        _walk_up(bot, name, _beside(bot, p))
        return
    bot.sleep(0.3)


def _follow_where_known(bot: Bot, name: str, here: int, me: dict[str, Any]) -> tuple[int, float, float] | bool | None:
    """Out of view, but one of our games plays them (True: we went after them), or the team
    phrase says where they are on this map ((map, x, y): where they are seen now). None: neither."""
    where = _locate(bot, name)
    if where:
        mid, x, y = where
        if here != mid:
            bot.log(_("{name} went to {map} - going too", name=name, map=atlas.label(mid)))
            bot.travel(mid, (x, y))
        elif not (_dist(me, {"x": x, "y": y}) > TELEPORT_FROM and teleport(bot, name, {"x": x, "y": y})):
            _walk_up(bot, name, (int(x), int(y)))
        return True
    spot = teammate_position(bot, name)
    if spot:
        them = {"x": spot[0], "y": spot[1]}
        if not (_dist(me, them) > TELEPORT_FROM and teleport(bot, name, them)):
            _walk_up(bot, name, (int(spot[0]), int(spot[1])))
        return (here, spot[0], spot[1])
    return None


def _after_them(bot: Bot, name: str, here: int, me: dict[str, Any], seen: tuple[int, float, float]) -> str | None:
    """They left our view on this map: through the exit they stood by ("exit"), or walk to where
    they were last seen ("walked"). None when we are there already."""
    e = _exit_taken(here, (seen[1], seen[2]))
    if e:
        bot.log(_("{name} took the exit to {map} - following", name=name, map=atlas.label(e["dest"])))
        bot.cross_exit({"map": here, "stand": e["stand"], "rect": e["rect"], "dest": e["dest"]})
        return "exit"
    if math.hypot(me["x"] - seen[1], me["y"] - seen[2]) > NEAR:
        _walk_up(bot, name, (int(seen[1]), int(seen[2])))
        return "walked"
    return None
