"""How much a potion heals and how long its cooldown is, from the game's own item text (the
Apothecary's shop menu, kept in data/knowledge.json): "Recover 15% Max HP and 1500 points of HP,
11 seconds CD." From that the bot works out when to drink: as soon as one potion's worth is
missing, so HP and MP stay near full and no potion is wasted (a potion that recovers 15% is
drunk at 85%)."""

from typing import Any
import re

from .. import knowledge
from ..i18n import _

DESC = re.compile(r"recover\s+(\d+(?:\.\d+)?)\s*%\s*max\s*(hp|mp)\s*and\s*(\d+)\s*points?\b.*?"
                  r"(\d+(?:\.\d+)?)\s*seconds?\s*cd", re.I | re.S)
LOWEST_MARK = 30.0     # never wait below this for a potion, whatever the numbers say
HIGHEST_MARK = 97.0    # nor drink above this (a tiny potion on a huge HP pool)
FALLBACK_MARK = 80.0   # a potion the game hasn't described yet
FALLBACK_GAP = 3.0


def _parse(item):
    m = DESC.search((item or {}).get("description") or "")
    if not m:
        return None
    return {"pct": float(m.group(1)), "flat": int(m.group(3)), "cd": float(m.group(4)), "from": item["name"]}


def info(name: str | None) -> dict[str, Any] | None:
    """{"pct", "flat", "cd", "from", "guessed"?} for a potion, or None when the game hasn't told us.
    A potion whose own text we haven't seen borrows its twin's of the same size (the shop has shown
    Small Blue but not Small Red; the game's sizes heal the same for both colours)."""
    if not name:
        return None
    got = _parse(knowledge.item(name))
    if got:
        return got
    n = name.lower()
    twin = n.replace("red", "\0").replace("blue", "red").replace("\0", "blue")
    if twin != n:
        got = _parse(knowledge.item(twin))
        if got:
            got["guessed"] = True
            return got
    return None


def recovers(inf: dict[str, Any], top: float) -> float:
    """Points one potion gives back, for a pool of `top` (max HP or max MP)."""
    return inf["pct"] / 100.0 * top + inf["flat"]


def mark(inf: dict[str, Any] | None, top: float) -> float | None:
    """Drink when below this percent: the moment a whole potion's worth is missing."""
    if not inf or not top or top <= 0:
        return None
    return max(LOWEST_MARK, min(HIGHEST_MARK, 100.0 - 100.0 * recovers(inf, top) / top))


def describe(name, what="HP"):
    """One line for the player about a potion."""
    inf = info(name)
    if not inf:
        return _("{item}: the game hasn't shown what it heals yet. Until you type your own numbers below, the "
                 "bot drinks it when {what} is below {pct}%, every {secs} seconds at most.", item=name, what=what,
                 pct=int(FALLBACK_MARK), secs="%g" % FALLBACK_GAP)
    s = _("{item} recovers {pct}% of your max {what} plus {flat}, cooldown {cd} seconds.", item=name,
          pct="%g" % inf["pct"], what=what, flat=inf["flat"], cd="%g" % inf["cd"])
    if inf.get("guessed"):
        s += " " + _("(Taken from {twin}, the same size.)", twin=inf["from"])
    return s + " " + _("The bot drinks it as soon as that much {what} is missing.", what=what)


def auto(value: Any) -> bool:
    """Profile numbers for potions: "auto" (or 0 / empty) means work it out from the potion."""
    return value in (None, "", "auto", 0, 0.0)
