"""The sounds the run's own connection listens for, all of them at once.

The agent keeps ONE list of sound ids a connection listens for (`soundlisten`), and each call puts a
new list in place of the old one. Three parts of a run listen on the same connection: the farm for
the out-of-range sound (refusals.py, 1023), the team we lead for a request to join (teamlead.py,
1057), a cable car ride for its arrival (ridedoor.py, 1152) - and a script's sound_wait for its own.
Each used to set its own list, and the last one silenced the others: leading a team, the farm's
out-of-range sound was never heard again once the team listened.

So each part says what it wants here (want), by name, and the connection listens for all of it;
a script's wait listens for its own ids on top (with_ours), and the run's are put back after it
(wanted).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import sounds

if TYPE_CHECKING:
    from .bot import Bot


def _wants(b: Bot) -> dict[str, str]:
    return b.__dict__.setdefault("sound_wants", {})


def _all(b: Bot, extra: str = "") -> str:
    """The ids of every part (and extra), merged range by range: "all" alone is all of them, and
    "off" adds none (the text "1023,all" is no list the agent - or IdSet - takes)."""
    spans = [span for ids in list(_wants(b).values()) + [extra] if ids
             for span in sounds.IdSet.parse(ids).ranges]
    if not spans:
        return "off"
    return sounds.IdSet.parse(",".join("%d-%d" % span for span in spans)).text


def want(b: Bot, owner: str, ids: str | None) -> dict[str, Any]:
    """owner listens for ids from now on (None: no longer), and the connection for everything the
    run wants. The agent's answer (hooked or not). AgentError, OSError, ValueError as sounds.listen."""
    wants = _wants(b)
    if ids:
        wants[owner] = ids
    else:
        wants.pop(owner, None)
    return sounds.listen(b.a, _all(b))


def wanted(b: Bot) -> str:
    """What the run listens for now ("off": nothing)."""
    return _all(b)


def with_ours(b: Bot, ids: str) -> str:
    """ids and everything the run listens for (a script's sound wait: its own events are picked out
    of them, and the run's parts keep hearing theirs meanwhile)."""
    return _all(b, ids)
