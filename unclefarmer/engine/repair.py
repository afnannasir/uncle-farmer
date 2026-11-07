"""Keeping the gear repaired, with the repair toolboxes the player carries.

What the game says, all from its own data:
  - Every hit wears the gear down. When an item is low, the combat packet (IN op=41) carries
    "uid,0" after the skill name ("about to break"); when it breaks, "uid,1", and the client marks
    that item's equipped field negative (game.N.I -> game.i.e(uid, -1)). A broken item gives
    nothing, so the character gets weaker and weaker. Agent v18 reports the warnings (`wear`);
    with an older agent only the broken mark in `mem equipment` is seen.
  - Toolboxes (item texts): "Can repair all the currently outfitted equipment" - Small Toolbox
    up to level 60, Large Toolbox up to level 80, Alloy Toolbox any level. Used, the game asks
    "Do you want to repair all the equipment that is already equipped?" -> Confirm. Pliers mend one
    item at a time, so they are never used: one toolbox mends everything.

So at the first warning the bot repairs everything with the cheapest toolbox that covers the
character's level. The Alloy Toolbox covers every level, and it is the one a high-level character
needs: Small and Large stop at levels 60 and 80, and the game then mends only what is that low.
Three things kept it unused: a character whose level wasn't known yet (no sheet read, no level in
the scan) took every toolbox as fitting, so the cheapest went first and the Alloy Toolbox never
came; a toolbox the game turned down ("too high to be repaired") was given up for ten minutes and
then the same one tried again, never the next, stronger one; and the check after a repair only
looked for broken items, so a Small or Large Toolbox that left high-level gear worn counted as a
full repair. Now the level is read before choosing (the sheet, between fights), an unknown level
takes the strongest toolbox first, and a toolbox that can't do it (turned down, a level limit
under the character's level, gear still broken) hands over to the next, stronger one at once. Only a warning that comes during this run counts: the agent keeps each item's
newest warning for as long as the game is open, so a run's first `wear 0` also hands back warnings
an earlier run already repaired. Every tool is a run of its own (Sell shards, Update shard prices,
Clean the bag now), and each took last night's "about to break" for a new one and spent a toolbox on
gear that was fine. Gear that really broke before the run is still repaired: the game marks it
broken in `mem equipment`, which is read at the run's first gear check and every BROKEN_EVERY. One on a hotkey (1 .. =) is pressed at once, in a fight too; one that is only in
the bag is used the moment the fight is over (the bag cursor needs its own keys). A player who'd
rather not can turn it off (profile repair_with "off").
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any
import re

from .. import jsonio
from ..clock import clock_for
from ..errors import carry_on
from ..i18n import _

if TYPE_CHECKING:
    from .bot import Bot

# (name, highest equipment level it repairs, None: every level), cheapest first - the item texts:
# "...not higher than level 60", "...level 80 and below", "Can repair all the currently outfitted equipment"
TOOLBOXES: tuple[tuple[str, int | None], ...] = (("Small Toolbox", 60), ("Large Toolbox", 80), ("Alloy Toolbox", None))
# The question has two forms: "...already equipped?Restrict repairable equipment level: 80" (Small,
# Large) and the same without the limit (the language packs list both) - both start like this.
ASK = r"repair all the equipment"
LEVEL_LIMIT = re.compile(r"Restrict repairable equipment level:\s*(\d+)", re.I)
NONE_TO_DO = r"No repairable equipment"
TOO_HIGH = r"too high to be repaired"
AFTER_REPAIR = 120.0      # no second repair this soon after one, unless something broke
LATE_WARNING = 10.0       # an "about to break" this soon after a repair was sent before it landed
CHECK_EVERY = 2.0         # seconds between looks at the wear warnings
BROKEN_EVERY = 30.0       # seconds between looks for broken items when nothing warned


class Gear:
    """What the bot knows about its gear's wear during a run."""

    def __init__(self) -> None:
        self.wear_seen = 0          # agent clock (ms) of the newest wear warning read
        self.started_at = 0.0       # clock_for(bot).time() of the run's first gear check: warnings
                                    # from before it belong to an earlier run (or the player's play)
        self.older = 0              # how many of those were left out
        self.warned: dict[int, str] = {}     # uid -> "low" or "broken", since the last repair
        self.repaired_at = 0.0      # clock_for(bot).time() of the last repair
        self.failed_at = 0.0        # last repair that didn't work, so it isn't retried every loop
        self.checked_at = 0.0
        self.broken_checked_at = 0.0
        self.said_no_tool = False
        self.said_old_agent = False
        self.said_wait = False


def equipment(bot: Bot) -> list[dict[str, Any]]:
    rows = bot.a.json("mem equipment")
    return rows if jsonio.is_array(rows) else []


def broken(bot: Bot) -> list[str]:
    """Names of the equipped items the game marked broken."""
    return [r.get("name") or "?" for r in equipment(bot) if (r.get("equipped") or 0) < 0]


def read_wear(bot: Bot, g: Gear) -> bool:
    """Fold the agent's new wear warnings into g.warned. False on an agent without `wear`."""
    r = bot.a.json("wear %d" % g.wear_seen)
    if not jsonio.is_object(r) or "wear" not in r:
        return False
    mine: dict[object, object] | None = None      # uid -> name of what we wear
    warnings: list[dict[str, Any]] = r["wear"]
    for w in warnings:
        g.wear_seen = max(g.wear_seen, w["t"])
        if mine is None:
            mine = {row.get("uid"): row.get("name") for row in equipment(bot)}
        if w["uid"] not in mine:
            continue              # not one of our items
        if w["t"] / 1000.0 < g.started_at:
            g.older += 1          # from before this run: repaired then, or out of date (see the notes)
            continue
        if not w["broken"] and w["t"] / 1000.0 < g.repaired_at + LATE_WARNING:
            # Already on its way when the toolbox mended everything (live: one came 2 s after a
            # repair). Kept, it only waited out AFTER_REPAIR and then spent a second toolbox.
            continue
        state = "broken" if w["broken"] else "low"
        if g.warned.get(w["uid"]) != state:
            g.warned[w["uid"]] = state
            bot.log(_("{item} is about to break", item=mine[w["uid"]]) if state == "low"
                    else _("{item} is broken", item=mine[w["uid"]]), speak=state == "broken")
    return True


def level(bot: Bot) -> int:
    """The character's level: the last character sheet, else the scan."""
    me: dict[str, Any] = bot.last_me or {}
    sheet: dict[str, Any] = me.get("sheet") or {}
    lv: int = sheet.get("level") or 0
    if lv <= 0:
        scan: dict[str, Any] = bot.p.get("scan") or {}
        lv = scan.get("level") or 0
    return lv


def covers(top: int | None, lv: int) -> bool:
    """Does a toolbox that repairs up to level `top` (None: any level) mend a level-lv character's
    gear? An unknown level (0) is covered only by the toolbox without a limit."""
    return top is None or 0 < lv <= top


def limit_of(name: str) -> int | None:
    """The highest level a toolbox of that name repairs (None: any level, and for an unknown name)."""
    return next((top for n, top in TOOLBOXES if n.lower() == name.strip().lower()), None)


def toolboxes(bot: Bot) -> list[tuple[str, str | None, int]]:
    """The toolboxes we have, best choice first: [(name, hotkey or None, count)].
    Those that cover the character's level come first, cheapest first; within that a toolbox on a
    hotkey beats one only in the bag. One that doesn't cover the level is a last resort (the game
    still mends what it can), the strongest of those first. With the level unknown only the Alloy
    Toolbox surely covers it, so it comes first and the others follow, strongest first."""
    have: dict[str, tuple[str, int]] = {}
    for r in bot.bag_rows():
        n = (r.get("name") or "").strip()
        if n and r.get("count"):
            have[n.lower()] = (n, have.get(n.lower(), (n, 0))[1] + int(r["count"]))
    lv = level(bot)
    out: list[tuple[bool, str, str | None, int]] = []
    for name, top in TOOLBOXES:
        if name.lower() in have:
            n, count = have[name.lower()]
            out.append((covers(top, lv), n, bot.hotkey_of(n), count))
    fits = [x for x in out if x[0]]
    rest = [x for x in out if not x[0]][::-1]          # the strongest of the others first
    fits = [x for x in fits if x[2]] + [x for x in fits if not x[2]]
    return [(n, key, count) for _f, n, key, count in fits + rest]


def learn_level(bot: Bot) -> int:
    """The character's level, reading the character sheet first when it isn't known yet (never in
    a fight: the sheet's keys would browse its window there). 0 when it still isn't known."""
    lv = level(bot)
    if lv <= 0 and not bot.in_fight:
        refresh = getattr(bot, "refresh_sheet", None)
        if refresh is not None:
            try:
                refresh(force=True)
            except Exception as e:           # a failed reading only leaves the level unknown
                carry_on(e, "reading the level for a repair")
            lv = level(bot)
    return lv


def check(bot: Bot) -> bool:
    """Repair at the game's first warning (or when something broke). Called from look_after, so
    walking, travelling and fighting all get it. True when a repair was made. A broken item with
    nothing to repair it ends the run safely, if the profile says so (stop_when_gear_broken):
    broken gear makes every fight harder until the character dies."""
    p = bot.p
    if (p.get("repair_with") or "toolbox") == "off" or bot.held or getattr(bot, "repairing", False):
        return False
    g = bot.gear
    now = clock_for(bot).time()
    if now - g.checked_at < CHECK_EVERY:
        return False
    g.checked_at = now
    first = not g.started_at
    if first:
        g.started_at = now
    new_agent = read_wear(bot, g)
    if first and g.older:
        bot.log(_("left out {n} gear warnings from before this run (they were dealt with then); "
                  "broken gear is still repaired", n=g.older))
    if not new_agent and not g.said_old_agent:
        g.said_old_agent = True
        bot.log(_("this game's agent can't warn before gear breaks - restart Mist World so the new "
                  "agent loads; until then gear is repaired only once something breaks"))
    gone: list[str] = []
    if g.warned or now - g.broken_checked_at >= BROKEN_EVERY:
        g.broken_checked_at = now
        gone = broken(bot)
    if not g.warned and not gone:
        return False
    if now - g.failed_at < 600:
        return False
    if now - g.repaired_at < AFTER_REPAIR and not gone:
        return False
    bot.repairing = True
    try:
        return repair(bot, gone)
    finally:
        bot.repairing = False


def repair(bot: Bot, gone: list[str] | None = None) -> bool:
    """Use a toolbox (hotkey first, else the bag between fights) and confirm. True when done. A
    toolbox that can't mend this gear - the game turns it down, its level limit is under the
    character's level, or gear is still broken after it - hands over to the next, stronger one."""
    g = bot.gear
    learn_level(bot)
    tools = toolboxes(bot)
    if not tools:
        g.failed_at = clock_for(bot).time()
        if not g.said_no_tool:
            g.said_no_tool = True
            bot.log(_("the gear needs repairing, but there's no toolbox on a hotkey or in the bag"), speak=True)
        if gone and bot.p.get("stop_when_gear_broken", True) and not bot.in_fight:
            from .bot import Stopped
            bot.log(_("{items} broken and nothing to repair it with - going somewhere safe and stopping",
                      items=", ".join(gone)), speak=True)
            bot.finish()
            raise Stopped(_("gear broken, no toolbox"))
        return False
    i = 0
    while i < len(tools):
        name, key, count = tools[i]
        stronger = [t for t in tools[i + 1:] if _stronger(t[0], name)]
        outcome = _use_toolbox(bot, name, key, count, bool(stronger))
        if outcome != WEAK:
            return outcome == DONE
        bot.log(_("trying the {tool} instead", tool=stronger[0][0]))
        i = tools.index(stronger[0])         # WEAK only comes with a stronger one at hand
    return False


DONE, WEAK, FAILED = "done", "weak", "failed"


def _stronger(a: str, b: str) -> bool:
    """Does toolbox a repair higher gear than toolbox b?"""
    ta, tb = limit_of(a), limit_of(b)
    return tb is not None and (ta is None or ta > tb)


def _use_toolbox(bot: Bot, name: str, key: str | None, count: int, can_hand_over: bool) -> str:
    """One toolbox: DONE (all repaired), WEAK (it can't mend this gear and a stronger one is at
    hand: try that) or FAILED (anything else: nothing more now)."""
    g = bot.gear
    if not key and bot.in_fight:
        if not g.said_wait:
            g.said_wait = True
            bot.log(_("the gear needs repairing: the {tool} is only in the bag, so after this fight "
                      "(put it on a hotkey to repair at once)", tool=name))
        return FAILED
    if bot.dialog().get("open"):
        return FAILED             # some menu of ours is open (a shop, the respawn menu): later
    where = _("key {key}", key=key) if key else _("the bag")
    bot.log(_("repairing all the gear with a {tool} from {where} ({n} left)", tool=name, where=where, n=count))
    if key:
        bot.key(key)
    elif not bot.use_item(name):
        g.failed_at = clock_for(bot).time()
        return FAILED
    d = bot.wait_dialog(3.0)
    if d is None or not d.get("top"):
        g.failed_at = clock_for(bot).time()
        bot.log(_("the {tool} didn't open its question - not repaired", tool=name), speak=True)
        return FAILED
    texts = [t for row in d["top"]["rows"] for e in row if e for t in bot.entry_texts(e)]
    bot.note_dialog(d, "repair with %s" % name)
    if any(re.search(NONE_TO_DO, t, re.I) for t in texts):
        bot.close_dialogs()
        g.warned.clear()
        g.repaired_at = clock_for(bot).time()
        return FAILED
    if any(re.search(TOO_HIGH, t, re.I) for t in texts) or not any(re.search(ASK, t, re.I) for t in texts):
        bot.close_dialogs()
        line = _("the {tool} can't repair this gear: {what}", tool=name, what=(texts[0] if texts else "?")[:90])
        if can_hand_over:
            bot.log(line)
            return WEAK
        g.failed_at = clock_for(bot).time()
        bot.log(line, speak=True)
        return FAILED
    limit = next((int(m.group(1)) for t in texts for m in [LEVEL_LIMIT.search(t)] if m), 0)
    lv = level(bot)
    if can_hand_over and limit and lv > limit:
        # the game's own limit for this toolbox is under the character's level: its gear would
        # stay worn, and a stronger toolbox is at hand
        bot.close_dialogs()
        bot.log(_("the {tool} only repairs gear up to level {limit}, and this character is level {level}",
                  tool=name, limit=limit, level=lv))
        return WEAK
    from .bot import CONFIRM
    if bot.choose(CONFIRM) is None:
        g.failed_at = clock_for(bot).time()
        return FAILED
    bot.sleep(1.0)
    bot.close_dialogs()
    g.warned.clear()
    g.repaired_at = clock_for(bot).time()
    g.said_wait = False
    left = _count(bot, name)
    if left is not None and left >= count:
        bot.log(_("the {tool} is still in the bag after the repair ({n}) - the game may not have used it",
                  tool=name, n=left))
    still = broken(bot)
    if still:
        line = _("still broken after the repair: {items}", items=", ".join(still))
        if can_hand_over:
            bot.log(line)
            return WEAK
        g.failed_at = clock_for(bot).time()
        bot.log(line, speak=True)
        return FAILED
    bot.log(_("all the gear repaired with a {tool}", tool=name))
    return DONE


def _count(bot: Bot, name: str) -> int | None:
    """How many of that toolbox the bag holds now (None when the bag can't be read)."""
    try:
        rows = bot.bag_rows()
    except Exception as e:      # only a note after the repair: never a reason to fail it
        carry_on(e, "counting the toolboxes")
        return None
    want = name.strip().lower()
    return sum(int(r.get("count") or 0) for r in rows if (r.get("name") or "").strip().lower() == want)
