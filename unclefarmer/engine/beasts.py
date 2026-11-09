"""Fusion beasts: ride the fastest Swift one while travelling, fight in the best Fierce one.

What the game showed in testing:
  - Using a beast on the fusion beast panel (panel 2, the use key) fuses it; using the active one
    un-fuses. Using another one while fused swaps in one press.
  - Fusing, swapping and un-fusing cost nothing that showed: no per-beast cooldown, no fusion
    power message. BUT the server allows one change per ~6.2 s: a use pressed sooner is sent and
    silently ignored (11 ignored presses per toggle in the test). So we wait out that gap and press
    once - never spam the server.
  - Nightmare Beast took us from 4.14 to 5.83 tiles/s (the server sends the new speed, IN op=39) and
    added its max HP; Werewolf added its +6000 max HP.
  - Swift beasts disable every skill (the game's own description), so they must come off before a
    fight. Fierce beasts add attack, defence and HP and keep skills.
  - Fusion power (the T key's answer, "points;fusion"): about 3 a minute goes while fused, about 6 a
    minute comes back while not (two characters, one night). From 100 that is half an hour of
    riding; the game's guide says no fusing under 10, and at 0 the beast comes off by itself.
Kinds, speeds and levels come from data/beasts.json, built from the game's fusion beast texts.

When a beast goes on: only after QUIET seconds with nothing striking us or our pet and our HP not
falling (Bot.last_attacked, kept by Bot.attackers and look_after). A change asked for in combat is
ignored by the server and costs the presses' waits. Getting OFF a Swift beast for a fight is not
held back: its skills are off. When the game doesn't take a beast, it is left alone for a while that
grows with each refusal in a row (REFUSED_FOR, doubled up to REFUSED_MAX), the run goes on, and the
refusal is logged once until the beast is taken again.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any, TYPE_CHECKING
import os

from .. import jsonio as json, paths
from ..clock import CLOCK, Clock, clock_for
from ..i18n import _

if TYPE_CHECKING:
    from .bot import Bot

FIGHT, TRAVEL = "fight", "travel"
GAP = 6.4          # seconds the server wants between two fusion changes (measured 6.2)
TRIES = 3
AFTER_FIGHT = 20   # no mounting this soon after combat (Orcus 2026-10-04: 3 presses ignored in the
                   # 20 s after a fight - the server lets no Swift beast on while you're in combat)
REFUSED_FOR = 60   # a beast the game wouldn't take is tried again after this long, not never
WALK_REFUSED_FOR = 15   # the walking assistant asks again sooner: riding is all it does
REFUSED_MAX = 600  # each refusal in a row doubles the wait, up to this (WALK_REFUSED_MAX walking)
WALK_REFUSED_MAX = 60
QUIET = 6.0        # seconds with no attack on us or our pet before a beast is put on
RIDE_CHECK = 4.0   # seconds between "still on the Swift beast?" checks while walking
MIN_POWER = 15     # fusion power under which only "costs no fusion power" beasts are used
POWER_EVERY = 20.0 # while riding, read fusion power with the T key this often (it costs nothing)
_known: dict[str, dict[str, Any]] | None = None


def known() -> dict[str, dict[str, Any]]:
    """{beast kind: what the game texts say about it (kind, speed, free...)}, from data/beasts.json."""
    global _known
    if _known is None:
        found: dict[str, dict[str, Any]]
        try:
            with open(os.path.join(paths.DATA, "beasts.json"), encoding="utf-8") as f:
                found = json.load(f)["beasts"]
        except (OSError, ValueError, KeyError):
            found = {}
        _known = found
    return _known


def owned(b: Bot) -> list[dict[str, Any]]:
    """The character's beasts in panel order, each with what the game texts say about its kind."""
    rows = b.a.json("mem beasts")
    if not json.is_array(rows):
        return []
    info = known()
    for r in rows:
        about: dict[str, Any] = info.get(r["name"], {})
        r.update({k: v for k, v in about.items() if k != "text"})
        r.setdefault("kind", "unknown")
    return rows


def usable(r: dict[str, Any], refused: Mapping[int, float], clock: Clock = CLOCK) -> bool:
    now = clock.time() * 1000
    return (refused.get(r["id"], 0) < clock.time()
            and not (r.get("usable_at") and r["usable_at"] > now)
            and not (r.get("expires_at") and r["expires_at"] < now))


def choose(rows: list[dict[str, Any]], purpose: str, refused: Mapping[int, float] | None = None,
           clock: Clock = CLOCK) -> dict[str, Any] | None:
    """The beast that fits: fastest Swift for travel; strongest Fierce for a fight (by level, then
    HP). None when nothing fits - then travel goes on foot and a fight goes without a beast."""
    refused = refused or {}
    if purpose == TRAVEL:
        pool = [r for r in rows if r["kind"] == "swift" and usable(r, refused, clock)]
        return max(pool, key=lambda r: (r.get("speed", 0), r.get("free", False)), default=None)
    pool = [r for r in rows if r["kind"] == "fierce" and usable(r, refused, clock)]
    return max(pool, key=lambda r: (r.get("level", 0), r.get("hp", 0)), default=None)


def power(me: dict[str, Any]) -> int | None:
    """Fusion power now, or None when no recent reading: the T key's answer (agent v24) if under a
    minute old, else the character sheet if under five."""
    pts: dict[str, Any] = me.get("points") or {}
    if pts.get("fusion", -1) >= 0 and pts.get("age_ms", 1e9) < 60000:
        return pts["fusion"]
    sheet: dict[str, Any] = me.get("sheet") or {}
    if sheet.get("fusion", -1) >= 0 and sheet.get("age", 999) < 300:
        return sheet["fusion"]
    return None


def attacked_since(b: Bot) -> float:
    """Seconds since something last struck us or our pet, or our HP fell (Bot.last_attacked).
    attackers() is asked first: it notes the newest strike as it reads the casts."""
    look = getattr(b, "attackers", None)
    if look is not None:
        look()
    return clock_for(b).time() - getattr(b, "last_attacked", 0.0)


def _too_soon(b: Bot) -> bool:
    """Attacked within QUIET seconds: no beast goes on now. Said once until it has been quiet."""
    since = attacked_since(b)
    if since >= QUIET:
        b.said_beast_waits = False
        return False
    if not getattr(b, "said_beast_waits", False):
        b.said_beast_waits = True
        b.log(_("attacked {secs} s ago - the fusion beast waits until {quiet} s without an attack",
                secs=round(max(since, 0.0), 1), quiet=int(QUIET)))
    return True


def _refused(b: Bot, beast: dict[str, Any]) -> None:
    """The game didn't take beast: leave it alone for a while that grows with each refusal in a
    row, and say so the first time only."""
    walking = getattr(b, "walk_only", False)
    counts: dict[int, int] = b.__dict__.setdefault("beast_refusals", {})
    n = counts[beast["id"]] = counts.get(beast["id"], 0) + 1
    base, top = (WALK_REFUSED_FOR, WALK_REFUSED_MAX) if walking else (REFUSED_FOR, REFUSED_MAX)
    wait = min(top, base * 2 ** (n - 1))
    b.beasts_refused[beast["id"]] = clock_for(b).time() + wait
    if n == 1:
        b.log(_("the game wouldn't take {beast} now; trying again later", beast=beast["name"]))


def _taken(b: Bot, beast: dict[str, Any]) -> None:
    counts: dict[int, int] = b.__dict__.setdefault("beast_refusals", {})
    n = counts.pop(beast["id"], 0)
    if n > 1:
        b.log(_("{beast} taken after {n} refusals", beast=beast["name"], n=n))


def ensure(b: Bot, purpose: Any = None) -> None:
    """Get into the right beast for purpose (or out of a Swift one before a fight). Quiet and
    cheap when it is already right; never stops the run if the panel can't be worked. A beast goes
    on only after QUIET seconds without an attack."""
    rows = owned(b)
    if purpose == TRAVEL and _travel_waits(b, rows):
        return
    if not rows:
        return
    refused = b.beasts_refused
    pw = power(b.me())
    low_power = pw is not None and pw < MIN_POWER
    pick = b.p.get("speed_beast" if purpose == TRAVEL else "fight_beast", "auto" if purpose == TRAVEL else "none")
    if pick == "none" and purpose == TRAVEL and getattr(b, "walk_only", False):
        pick = "auto"     # the walking assistant always rides: that is what it is for
    if pick == "none":
        want = None       # the player's choice (for fighting: only get off the Swift one)
    elif pick == "auto":
        want = choose(rows, purpose, refused, clock_for(b))
    else:                 # one beast by name, while it is usable
        want = next((r for r in rows if r["name"] == pick and usable(r, refused, clock_for(b))), None)
    if want is not None and low_power and not want.get("free"):
        # The game's guide: transforming uses fusion power, below 10 you can't transform, at 0 it
        # ends, and it recovers while not transformed. Let it recover instead of riding.
        if not getattr(b, "said_low_power", False):
            b.said_low_power = True
            b.log(_("fusion power {n} - on foot until it has come back", n=int(pw or 0)))
        want = None
    elif pw is not None and pw >= MIN_POWER:
        b.said_low_power = False
    active = next((r for r in rows if r["active"]), None)
    if want is None:
        if purpose == TRAVEL and not (low_power and pick != "none"):
            _say_why_no_mount(b, rows, pick)
        # Nothing fits. A Swift beast must not stay on for a fight: its skills are off.
        if purpose == FIGHT and active and active["kind"] == "swift":
            if _press(b, rows, active):
                b.log(_("got off {beast} for the fight", beast=active["name"]))
        return
    if active and active["id"] == want["id"]:
        return
    _put_on(b, rows, want, active, purpose)


def _travel_waits(b: Bot, rows: list[dict[str, Any]]) -> bool:
    """True when travel must stay on foot before any beast is looked at: the panel unread, the
    server still counting the last fight as combat (walks check again), monsters on us."""
    if not rows:
        _on_foot(b, "panel", _("on foot: the fusion beast list couldn't be read"))
        return True
    since = clock_for(b).time() - getattr(b, "last_combat", 0)
    if since < AFTER_FIGHT:
        _on_foot(b, "fight", _("on foot for {secs} s more: the server still counts the last fight as combat",
                               secs=int(AFTER_FIGHT - since + 0.99)))
        return True
    return _on_foot_for_a_fight(b)


def _on_foot(b: Bot, reason: str, line: str) -> None:
    """Travel stays on foot for `reason`: logged once per reason in a run, so a slow trip says
    why without a line at every leg."""
    said: set[str] = b.__dict__.setdefault("on_foot_said", set())
    if reason not in said:
        said.add(reason)
        b.log(line)


def _say_why_no_mount(b: Bot, rows: list[dict[str, Any]], pick: str) -> None:
    """No beast fits for travel: say which rule kept us on foot (see _on_foot)."""
    if pick == "none":
        _on_foot(b, "setting", _("on foot: the travel fusion beast is set to none in the character settings"))
        return
    clock = clock_for(b)
    swift = [r for r in rows if r["kind"] == "swift" and (pick == "auto" or r["name"] == pick)]
    if not swift:
        _on_foot(b, "none", _("on foot: no Swift fusion beast to ride") if pick == "auto"
                 else _("on foot: {beast} isn't among our fusion beasts", beast=pick))
        return
    now_ms = clock.time() * 1000
    waits = [r for r in swift if r.get("usable_at") and r["usable_at"] > now_ms]
    if waits:
        r = min(waits, key=lambda r: r["usable_at"])
        _on_foot(b, "cooldown", _("on foot: the game won't let {beast} on for {secs} s more",
                                  beast=r["name"], secs=int((r["usable_at"] - now_ms) / 1000 + 0.99)))
    elif all(r.get("expires_at") and r["expires_at"] < now_ms for r in swift):
        _on_foot(b, "expired", _("on foot: our Swift fusion beast has expired"))
    # otherwise refused a moment ago: _refused already said so


def _put_on(b: Bot, rows: list[dict[str, Any]], want: dict[str, Any], active: dict[str, Any] | None,
            purpose: Any) -> None:
    """Fuse with want, once QUIET seconds have passed without an attack (see the notes)."""
    if _too_soon(b):
        # In combat the server ignores the change. A Swift beast still comes off for a fight.
        if purpose == FIGHT and active and active["kind"] == "swift" and _press(b, rows, active):
            b.log(_("got off {beast} for the fight", beast=active["name"]))
        return
    if _press(b, rows, want, until_attacked=True):
        _taken(b, want)
        b.log(_("riding {beast}", beast=want["name"]) if purpose == TRAVEL
              else _("fighting as {beast}", beast=want["name"]))
    elif attacked_since(b) < QUIET:
        _too_soon(b)              # an attack came while pressing: not a refusal, just not now
    else:
        _refused(b, want)         # level, combat the agent didn't see...: try again later


def _on_foot_for_a_fight(b: Bot) -> bool:
    """Monsters on us (something cast at us or our pet lately, Bot.attackers): the fight comes
    first, on foot - the game refuses a mount in combat, and a Swift beast can't use skills. Said
    once while it lasts. True: no mounting now."""
    look = getattr(b, "attackers", None)      # (a stand-in bot without cast reports: never)
    if not (look is not None and look()):
        b.said_no_mount = False
        return False
    if not getattr(b, "said_no_mount", False):
        b.said_no_mount = True
        b.log(_("attacked - staying on foot, no fusion beast until the fight is over"))
    return True


def keep_riding(b: Bot, read_power: bool = True) -> None:
    """While travelling: back onto the Swift beast if we came off it, the game refused it a moment
    ago, or a fight just before the walk kept it off (AFTER_FIGHT: a dungeon that puts us out
    seconds after the last kill), checked every few seconds so it costs almost nothing. The walking
    assistant (read_power) reads fusion power with the T key every POWER_EVERY, so once it runs
    out, ensure() waits on foot until it has come back over MIN_POWER instead of asking the game
    every few seconds; other travel leaves the T key alone (what it does differs by class)."""
    now = clock_for(b).time()
    if now - getattr(b, "last_ride_check", 0) < RIDE_CHECK:
        return
    b.last_ride_check = now
    key = b.p.get("points_key")
    if read_power and key and now - getattr(b, "last_points_key", 0) >= POWER_EVERY:
        b.last_points_key = now
        b.key(key, 30)
        b.sleep(0.4)          # the answer is a round trip away
    ensure(b, TRAVEL)


def ride_any(b: Bot) -> bool:
    """On a fusion beast, any kind, for poisoned ground (Polson Wetland): a fused character takes
    no poison damage there. The fastest Swift one, else the strongest Fierce one; the travel-beast
    setting doesn't matter here, the poison does. True once one is on (or already was)."""
    rows = owned(b)
    active = next((r for r in rows if r["active"]), None)
    if active is not None:
        return True
    if not rows or clock_for(b).time() - getattr(b, "last_combat", 0) < AFTER_FIGHT:
        return False      # nothing to ride, or the server still counts the last fight as combat
    clock = clock_for(b)
    want = choose(rows, TRAVEL, b.beasts_refused, clock) or choose(rows, FIGHT, b.beasts_refused, clock)
    if want is None:
        return False
    _put_on(b, rows, want, None, TRAVEL)
    return any(r["active"] for r in owned(b))


def off(b: Bot) -> None:
    """Get off whatever beast is on (the end of a run, somewhere safe)."""
    rows = owned(b)
    active = next((r for r in rows if r["active"]), None)
    if active and _press(b, rows, active):
        b.log(_("got off {beast}", beast=active["name"]))


def _press(b: Bot, rows: list[dict[str, Any]], beast: dict[str, Any], until_attacked: bool = False) -> bool:
    """Put the fusion beast panel's cursor on beast and press the use key. True once the game
    shows the change (the beast became active, or stopped being active). until_attacked: give up
    as soon as something attacks us (putting a beast on), so the fight is never kept waiting."""
    index = next(i for i, r in enumerate(rows) if r["id"] == beast["id"])
    was = beast["active"]
    for _try in range(TRIES):
        wait = getattr(b, "last_beast_change", 0) + GAP - clock_for(b).time()
        while wait > 0:                # counted down by what was slept: never longer than the gap
            step = min(wait, 0.5) if until_attacked else wait
            b.sleep(step)
            wait -= step
            if until_attacked and attacked_since(b) < QUIET:
                return False
        if until_attacked and attacked_since(b) < QUIET:
            return False
        if not b.panel_cursor(2, index, len(rows)):
            return False
        b.key(b.p["use_key"])
        for _i in range(10):
            b.sleep(0.2)
            now = next((r for r in owned(b) if r["id"] == beast["id"]), None)
            if now and now["active"] != was:
                b.last_beast_change = clock_for(b).time()
                return True
        # Not taken: most likely a change made just before we started (by us or by hand).
        b.last_beast_change = clock_for(b).time()
    return False
