"""Equipment picked up while farming, thrown away by its rarity (the bag manager's Full bag page).

Farming fills the bag with equipment nobody keeps: the player ticks the rarities to throw away
(none by default) and every gear_throw_minutes of farming, and whenever the bag is full, the
pieces of those rarities are thrown away where the character stands, as a bag rule's throw is
(the agent's direct discard, no menu).

Only what this run picked up: a piece already in the bag when the run began is never thrown,
whatever its rarity (the bag's pieces are noted by their own id when farming starts), and every
piece thrown is written in the log with its name and rarity.

The game's rarities, lowest first, as its own English text has them (language\\english\\basic.txt):
crude, common, sophisticated, perfect, rare, legendary. The game's bag menu reads a piece as
"Name;rare"; where `mem bagfull` keeps the rarity is still to be read live, so until the agent
reports it (a row's "rarity") nothing is thrown.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import jsonio
from ..i18n import N_, _

if TYPE_CHECKING:
    from .bot import Bot

RARITIES = ("crude", "common", "sophisticated", "perfect", "rare", "legendary")
LABELS = {"crude": N_("Crude"), "common": N_("Common"), "sophisticated": N_("Sophisticated"),
          "perfect": N_("Perfect"), "rare": N_("Rare"), "legendary": N_("Legendary")}
DEFAULT_MINUTES = 30
MOST = 30                 # pieces in one sweep, as the community script
DIRECT_DISCARD_AGENT = 57


def chosen(p: dict[str, Any]) -> set[str]:
    """The rarities the character's profile throws away (gear_throw), lower case."""
    got: list[Any] = p.get("gear_throw") or []
    return {str(r).lower() for r in got if str(r).lower() in RARITIES}


def rarity_of(row: dict[str, Any]) -> str | None:
    """A bag row's rarity, lower case, or None (not equipment, or an agent that doesn't say)."""
    r = str(row.get("rarity") or "").strip().lower()
    return r if r in RARITIES else None


def rows(bot: Bot) -> list[dict[str, Any]]:
    got = bot.a.json("mem bagfull")
    return got if jsonio.is_array(got) else []


def note_start(bot: Bot) -> None:
    """When farming starts: the pieces already in the bag, by their own id - never thrown."""
    if not chosen(bot.p) or getattr(bot, "gear_start", None) is not None:
        return              # nothing chosen, or noted already this run (a plan's next spot)
    bot.__dict__["gear_start"] = {int(r["uid"]) for r in rows(bot) if isinstance(r.get("uid"), int)}
    bot.__dict__["gear_swept"] = bot.clock.time()


def picked_up(bot: Bot) -> list[dict[str, Any]]:
    """The pieces of the chosen rarities this run picked up, MOST at most."""
    want = chosen(bot.p)
    start: set[int] | None = getattr(bot, "gear_start", None)
    if not want or start is None:
        return []           # nothing chosen, or farming never noted the bag: nothing is the run's
    out = [r for r in rows(bot) if isinstance(r.get("uid"), int) and r["uid"] not in start
           and rarity_of(r) in want]
    return out[:MOST]


def sweep(bot: Bot) -> int:
    """Throw away the picked-up pieces of the chosen rarities. How many left the bag."""
    if not chosen(bot.p) or getattr(bot, "agent_version", 0) < DIRECT_DISCARD_AGENT:
        return 0
    bot.__dict__["gear_swept"] = bot.clock.time()
    todo = picked_up(bot)
    if not todo:
        return 0
    uids: list[int] = []
    for r in todo:
        uids += [int(r["uid"])] * max(1, int(r.get("count") or 1))
    reply = bot.a.send("discarddirectall " + " ".join(str(u) for u in uids))
    if not reply.startswith("sent all "):
        bot.log(_("the game didn't take the equipment to throw away: {why}", why=reply))
        return 0
    from .. import stats
    for r in todo:
        rarity = rarity_of(r) or ""
        bot.log(_("threw away {name} ({rarity}), picked up while farming", name=r.get("name") or "?",
                  rarity=_(LABELS.get(rarity, rarity))))
        stats.moved(bot, "thrown", str(r.get("name") or "?"), max(1, int(r.get("count") or 1)))
    return len(todo)


def due(bot: Bot) -> bool:
    """Whether gear_throw_minutes of farming have passed since the last sweep."""
    minutes = int(bot.p.get("gear_throw_minutes") or 0)
    last: float | None = getattr(bot, "gear_swept", None)
    return bool(chosen(bot.p) and minutes > 0 and last is not None and bot.clock.time() - last >= minutes * 60)


def between_fights(bot: Bot) -> bool:
    """The farm loop's look: a sweep when one is due. True when something was thrown."""
    return due(bot) and sweep(bot) > 0
