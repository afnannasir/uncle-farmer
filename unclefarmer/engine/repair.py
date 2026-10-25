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
character's level. One on a hotkey (1 .. =) is pressed at once, in a fight too; one that is only in
the bag is used the moment the fight is over (the bag cursor needs its own keys). A player who'd
rather not can turn it off (profile repair_with "off").
"""

from typing import Any
import re

from ..clock import clock_for
from ..i18n import _

# (name, highest equipment level it repairs), cheapest first
TOOLBOXES = (("Small Toolbox", 60), ("Large Toolbox", 80), ("Alloy Toolbox", 10 ** 6))
ASK = r"repair all the equipment"
NONE_TO_DO = r"No repairable equipment"
TOO_HIGH = r"too high to be repaired"
AFTER_REPAIR = 120.0      # no second repair this soon after one, unless something broke
LATE_WARNING = 10.0       # an "about to break" this soon after a repair was sent before it landed
CHECK_EVERY = 2.0         # seconds between looks at the wear warnings
BROKEN_EVERY = 30.0       # seconds between looks for broken items when nothing warned


class Gear:
    """What the bot knows about its gear's wear during a run."""

    def __init__(self):
        self.wear_seen = 0          # agent clock (ms) of the newest wear warning read
        self.warned = {}            # uid -> "low" or "broken", since the last repair
        self.repaired_at = 0.0      # clock_for(bot).time() of the last repair
        self.failed_at = 0.0        # last repair that didn't work, so it isn't retried every loop
        self.checked_at = 0.0
        self.broken_checked_at = 0.0
        self.said_no_tool = False
        self.said_old_agent = False
        self.said_wait = False


def equipment(bot):
    rows = bot.a.json("mem equipment")
    return rows if isinstance(rows, list) else []


def broken(bot):
    """Names of the equipped items the game marked broken."""
    return [r.get("name") or "?" for r in equipment(bot) if (r.get("equipped") or 0) < 0]


def read_wear(bot, g):
    """Fold the agent's new wear warnings into g.warned. False on an agent without `wear`."""
    r = bot.a.json("wear %d" % g.wear_seen)
    if not isinstance(r, dict) or "wear" not in r:
        return False
    mine = None
    for w in r["wear"]:
        g.wear_seen = max(g.wear_seen, w["t"])
        if mine is None:
            mine = {row.get("uid"): row.get("name") for row in equipment(bot)}
        if w["uid"] not in mine:
            continue              # not one of our items
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


def level(bot):
    """The character's level: the last character sheet, else the scan."""
    lv = ((bot.last_me or {}).get("sheet") or {}).get("level") or 0
    if lv <= 0:
        lv = (bot.p.get("scan") or {}).get("level") or 0
    return lv


def toolboxes(bot):
    """The toolboxes we have, best choice first: [(name, hotkey or None, count)].
    Those that cover the character's level come first, cheapest first; within that a toolbox on a
    hotkey beats one only in the bag. One that doesn't cover the level is a last resort (the game
    still mends what it can)."""
    have = {}
    for r in bot.bag_rows():
        n = (r.get("name") or "").strip()
        if n and r.get("count"):
            have[n.lower()] = (n, have.get(n.lower(), (n, 0))[1] + int(r["count"]))
    lv = level(bot)
    out = []
    for name, top in TOOLBOXES:
        if name.lower() in have:
            n, count = have[name.lower()]
            out.append((not lv or lv <= top, n, bot.hotkey_of(n), count))
    fits = [x for x in out if x[0]]
    rest = [x for x in out if not x[0]][::-1]          # the strongest of the others first
    fits = [x for x in fits if x[2]] + [x for x in fits if not x[2]]
    return [(n, key, count) for _f, n, key, count in fits + rest]


def check(bot: Any) -> bool:
    """Repair at the game's first warning (or when something broke). Called from look_after, so
    walking, travelling and fighting all get it. True when a repair was made. A broken item with
    nothing to repair it ends the run safely, if the profile says so (stop_when_gear_broken):
    broken gear makes every fight harder until the character dies."""
    p = bot.p
    if (p.get("repair_with") or "toolbox") == "off" or bot.held or getattr(bot, "_repairing", False):
        return False
    g = bot.gear
    now = clock_for(bot).time()
    if now - g.checked_at < CHECK_EVERY:
        return False
    g.checked_at = now
    new_agent = read_wear(bot, g)
    if not new_agent and not g.said_old_agent:
        g.said_old_agent = True
        bot.log(_("this game's agent can't warn before gear breaks - restart Mist World so the new "
                  "agent loads; until then gear is repaired only once something breaks"))
    gone = []
    if g.warned or now - g.broken_checked_at >= BROKEN_EVERY:
        g.broken_checked_at = now
        gone = broken(bot)
    if not g.warned and not gone:
        return False
    if now - g.failed_at < 600:
        return False
    if now - g.repaired_at < AFTER_REPAIR and not gone:
        return False
    bot._repairing = True
    try:
        return repair(bot, gone)
    finally:
        bot._repairing = False


def repair(bot, gone=None):
    """Use a toolbox (hotkey first, else the bag between fights) and confirm. True when done."""
    g = bot.gear
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
    name, key, count = tools[0]
    if not key and bot.in_fight:
        if not g.said_wait:
            g.said_wait = True
            bot.log(_("the gear needs repairing: the {tool} is only in the bag, so after this fight "
                      "(put it on a hotkey to repair at once)", tool=name))
        return False
    if bot.dialog().get("open"):
        return False              # some menu of ours is open (a shop, the respawn menu): later
    where = _("key {key}", key=key) if key else _("the bag")
    bot.log(_("repairing all the gear with a {tool} from {where} ({n} left)", tool=name, where=where, n=count))
    if key:
        bot.key(key)
    elif not bot.use_item(name):
        g.failed_at = clock_for(bot).time()
        return False
    d = bot.wait_dialog(3.0)
    if d is None or not d.get("top"):
        g.failed_at = clock_for(bot).time()
        bot.log(_("the {tool} didn't open its question - not repaired", tool=name), speak=True)
        return False
    texts = [t for row in d["top"]["rows"] for e in row if e for t in bot.entry_texts(e)]
    bot.note_dialog(d, "repair with %s" % name)
    if any(re.search(NONE_TO_DO, t, re.I) for t in texts):
        bot.close_dialogs()
        g.warned.clear()
        g.repaired_at = clock_for(bot).time()
        return False
    if any(re.search(TOO_HIGH, t, re.I) for t in texts) or not any(re.search(ASK, t, re.I) for t in texts):
        bot.close_dialogs()
        g.failed_at = clock_for(bot).time()
        bot.log(_("the {tool} can't repair this gear: {what}", tool=name, what=(texts[0] if texts else "?")[:90]),
                speak=True)
        return False
    from .bot import CONFIRM
    if bot.choose(CONFIRM) is None:
        g.failed_at = clock_for(bot).time()
        return False
    bot.sleep(1.0)
    bot.close_dialogs()
    g.warned.clear()
    g.repaired_at = clock_for(bot).time()
    g.said_wait = False
    still = broken(bot)
    if still:
        g.failed_at = clock_for(bot).time()
        bot.log(_("still broken after the repair: {items}", items=", ".join(still)), speak=True)
        return False
    bot.log(_("all the gear repaired"))
    return True
