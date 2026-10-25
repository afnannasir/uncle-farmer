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
"""

from typing import Any
import os

from .. import jsonio as json, paths
from ..clock import CLOCK, Clock, clock_for
from ..i18n import _

FIGHT, TRAVEL = "fight", "travel"
GAP = 6.4          # seconds the server wants between two fusion changes (measured 6.2)
TRIES = 3
AFTER_FIGHT = 20   # no mounting this soon after combat (Orcus 2026-10-04: 3 presses ignored in the
                   # 20 s after a fight - the server lets no Swift beast on while you're in combat)
REFUSED_FOR = 60   # a beast the game wouldn't take is tried again after this long, not never
WALK_REFUSED_FOR = 15   # the walking assistant asks again sooner: riding is all it does
RIDE_CHECK = 4.0   # seconds between "still on the Swift beast?" checks while walking
MIN_POWER = 15     # fusion power under which only "costs no fusion power" beasts are used
POWER_EVERY = 20.0 # while riding, read fusion power with the T key this often (it costs nothing)
_KNOWN = None


def known():
    global _KNOWN
    if _KNOWN is None:
        try:
            with open(os.path.join(paths.DATA, "beasts.json"), encoding="utf-8") as f:
                _KNOWN = json.load(f)["beasts"]
        except (OSError, ValueError, KeyError):
            _KNOWN = {}
    return _KNOWN


def owned(b: Any) -> list[dict[str, Any]]:
    """The character's beasts in panel order, each with what the game texts say about its kind."""
    rows = b.a.json("mem beasts")
    if not isinstance(rows, list):
        return []
    info = known()
    for r in rows:
        r.update({k: v for k, v in info.get(r["name"], {}).items() if k != "text"})
        r.setdefault("kind", "unknown")
    return rows


def usable(r, refused, clock: Clock = CLOCK):
    now = clock.time() * 1000
    return (refused.get(r["id"], 0) < clock.time()
            and not (r.get("usable_at") and r["usable_at"] > now)
            and not (r.get("expires_at") and r["expires_at"] < now))


def choose(rows, purpose, refused=(), clock: Clock = CLOCK):
    """The beast that fits: fastest Swift for travel; strongest Fierce for a fight (by level, then
    HP). None when nothing fits - then travel goes on foot and a fight goes without a beast."""
    if purpose == TRAVEL:
        pool = [r for r in rows if r["kind"] == "swift" and usable(r, refused, clock)]
        return max(pool, key=lambda r: (r.get("speed", 0), r.get("free", False)), default=None)
    pool = [r for r in rows if r["kind"] == "fierce" and usable(r, refused, clock)]
    return max(pool, key=lambda r: (r.get("level", 0), r.get("hp", 0)), default=None)


def power(me):
    """Fusion power now, or None when no recent reading: the T key's answer (agent v24) if under a
    minute old, else the character sheet if under five."""
    pts = me.get("points") or {}
    if pts.get("fusion", -1) >= 0 and pts.get("age_ms", 1e9) < 60000:
        return pts["fusion"]
    sheet = me.get("sheet") or {}
    if sheet.get("fusion", -1) >= 0 and sheet.get("age", 999) < 300:
        return sheet["fusion"]
    return None


def ensure(b: Any, purpose: Any = None) -> None:
    """Get into the right beast for purpose (or out of a Swift one before a fight). Quiet and
    cheap when it is already right; never stops the run if the panel can't be worked."""
    rows = owned(b)
    if not rows:
        return
    refused = b.beasts_refused
    if purpose == TRAVEL and clock_for(b).time() - getattr(b, "last_combat", 0) < AFTER_FIGHT:
        return      # still in combat as far as the server is concerned: walk on, mount later
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
        # Nothing fits. A Swift beast must not stay on for a fight: its skills are off.
        if purpose == FIGHT and active and active["kind"] == "swift":
            if _press(b, rows, active):
                b.log(_("got off {beast} for the fight", beast=active["name"]))
        return
    if active and active["id"] == want["id"]:
        return
    if _press(b, rows, want):
        b.log(_("riding {beast}", beast=want["name"]) if purpose == TRAVEL
              else _("fighting as {beast}", beast=want["name"]))
    else:
        later = WALK_REFUSED_FOR if getattr(b, "walk_only", False) else REFUSED_FOR
        refused[want["id"]] = clock_for(b).time() + later   # level, combat...: try again later
        b.log(_("the game wouldn't take {beast} now; trying again later", beast=want["name"]))


def keep_riding(b: Any) -> None:
    """While the walking assistant walks: back onto the Swift beast if we came off it (or the
    game refused it a moment ago), checked every few seconds so it costs almost nothing. Fusion
    power is read with the T key every POWER_EVERY, so once it runs out, ensure() waits on foot
    until it has come back over MIN_POWER instead of asking the game every few seconds."""
    now = clock_for(b).time()
    if now - getattr(b, "last_ride_check", 0) < RIDE_CHECK:
        return
    b.last_ride_check = now
    key = b.p.get("points_key")
    if key and now - getattr(b, "last_points_key", 0) >= POWER_EVERY:
        b.last_points_key = now
        b.key(key, 30)
        b.sleep(0.4)          # the answer is a round trip away
    ensure(b, TRAVEL)


def off(b: Any) -> None:
    """Get off whatever beast is on (the end of a run, somewhere safe)."""
    rows = owned(b)
    active = next((r for r in rows if r["active"]), None)
    if active and _press(b, rows, active):
        b.log(_("got off {beast}", beast=active["name"]))


def _press(b, rows, beast):
    """Put the fusion beast panel's cursor on beast and press the use key. True once the game
    shows the change (the beast became active, or stopped being active)."""
    index = next(i for i, r in enumerate(rows) if r["id"] == beast["id"])
    was = beast["active"]
    for _try in range(TRIES):
        wait = getattr(b, "last_beast_change", 0) + GAP - clock_for(b).time()
        if wait > 0:
            b.sleep(wait)
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
