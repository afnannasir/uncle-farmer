"""The game's "out of range" sound while farming: stop pressing at that monster, go to the nearest.

Measured live (2026-11-26, two characters, an hour, castwatch): when an attack key is pressed at a
target the server finds out of range, the game plays sound 1023 - every refused press, about every
0.3 s, the "beep, beep, beep" a player hears. It came in bursts of 4 to 7 with the selected monster
alive at 20.4 to 22.5 yards for a 20-yard skill, as it moved away after a fight broke off. The bot's
own range check uses positions that can be a moment old; the game's refusal is the server's verdict.

So the farm listens for that one sound on the bot's own connection (agent `soundlisten`, released
when the connection closes). Heard, the monster selected then is left alone for REFUSED_FOR seconds
(targets() skips it; one that attacks us is fought all the same) and the farm loop takes the nearest
one - the same one again after that, walked up to from where it is.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from .. import sounds
from ..i18n import _
from ..link import AgentError

if TYPE_CHECKING:
    from .bot import Bot

OUT_OF_RANGE = 1023           # the sound of an attack the server refused for the distance
REFUSED_FOR = 3.0             # seconds the monster is left alone after it


def start(b: Bot) -> None:
    """Listen for the out-of-range sound on this run's connection (once a farm; an agent without
    sounds leaves it off, and nothing changes)."""
    if getattr(b, "refusal_listening", False):
        return
    try:
        b.__dict__["refusal_cursor"] = sounds.events(b.a, 0)["now"]   # the cursor first: older ones never count
        status = sounds.listen(b.a, str(OUT_OF_RANGE))
    except (AgentError, OSError, ValueError):
        b.__dict__["refusal_listening"] = False
        return
    b.__dict__["refusal_listening"] = bool(status.get("hooked"))


def resume(b: Bot) -> None:
    """After a script's own sound_wait released this connection's listening: listen again."""
    if getattr(b, "refusal_listening", False):
        try:
            sounds.listen(b.a, str(OUT_OF_RANGE))
        except (AgentError, OSError, ValueError):
            b.__dict__["refusal_listening"] = False


def heard(b: Bot) -> bool:
    """The game refused an attack for the distance since the last look (one agent call)."""
    if not getattr(b, "refusal_listening", False):
        return False
    try:
        batch = sounds.events(b.a, int(getattr(b, "refusal_cursor", 0) or 0))
    except (AgentError, OSError, ValueError):
        return False
    b.__dict__["refusal_cursor"] = batch["now"]
    return any(e["id"] == OUT_OF_RANGE for e in batch["events"])


def note(b: Bot, mid: int, name: str) -> None:
    """Say it, once a monster: the game refused the attack on it from here."""
    told: set[int] = b.__dict__.setdefault("refusals_told", set())
    b.__dict__["refusals"] = int(getattr(b, "refusals", 0) or 0) + 1
    if mid not in told:
        told.add(mid)
        b.log(_("the game refused the attack on {name}: out of range - going to the nearest monster", name=name))
