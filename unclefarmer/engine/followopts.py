"""How the bot follows a player (the `follow` message and script line): the player's choices.

A player soloing a dungeon with a tank and a healer saw the healer attack, then heal the tank too
late when it took a big hit: an attack holds the global cooldown, so the heal waited for it. Their
asks, each a choice on the character's Team page - and, for one follow, an option on the script's
`follow` line (`follow Bob role=heal distance=10 pull=8 help=off skip="Boss One, Boss Two"`):

- role: "both" (help the fights and heal, as before), "heal" (heal only: never attack, not even
  what attacks us - a healer that fights back stops healing; its heals and potions keep it up),
  "attack" (never heal the team);
- pull: also attack what comes within that many tiles of the player, before anyone is hit (None:
  only what the team is already fighting, as before);
- help: help with the team's fights (else only what attacks us);
- distance: the tiles kept from the player (3 as before), never past the heal's reach for a
  character that heals;
- skip: names of monsters never attacked, whatever they do (a dungeon's bosses: the field bosses
  are already left alone).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from .bot import Bot

ROLES = ("both", "heal", "attack")
NEAR = 3                  # tiles kept from the player unless the player chose otherwise
DISTANCE_MOST = 30        # the most a player may choose
PULL_MOST = 30


def _opt(b: Bot, key: str, profile_key: str, default: Any) -> Any:
    """The script line's option for this follow, else the character's setting, else default."""
    opts: dict[str, Any] = getattr(b, "follow_opts", None) or {}
    if key in opts:
        return opts[key]
    return b.p.get(profile_key, default)


def role(b: Bot) -> str:
    """The follower's role (ROLES); "both" when not following."""
    if not getattr(b, "following", None):
        return "both"
    r = _opt(b, "role", "follow_role", "both")
    return r if r in ROLES else "both"


def heal_only(b: Bot) -> bool:
    return role(b) == "heal"


def attack_only(b: Bot) -> bool:
    return role(b) == "attack"


def helps_team(b: Bot) -> bool:
    """Help with the team's fights (the player's, a teammate's), not only what attacks us."""
    return bool(_opt(b, "help", "follow_help_team", True))


def pull(b: Bot) -> int | None:
    """Tiles from the player within which a monster is attacked before anyone is hit; None: off."""
    if "pull" in (getattr(b, "follow_opts", None) or {}):
        v = b.follow_opts["pull"]
    else:
        v = b.p.get("follow_pull_range", 10) if b.p.get("follow_pull", False) else None
    return max(1, min(PULL_MOST, int(v))) if isinstance(v, (int, float)) and not isinstance(v, bool) and v > 0 else None


def distance(b: Bot, heal_reach: float | None) -> int:
    """Tiles kept from the player: the choice, no less than 1, and inside heal_reach (the heal's
    range, for a follower that heals) so the player stays in reach of the heals."""
    v = _opt(b, "distance", "follow_distance", NEAR)
    d = int(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else NEAR
    d = max(1, min(DISTANCE_MOST, d))
    if heal_reach and not attack_only(b):
        d = min(d, max(1, int(heal_reach) - 1))
    return d


def names(text: object) -> list[str]:
    """The monster names in a comma list, tidied."""
    return [" ".join(n.split()) for n in str(text or "").split(",") if n.strip()]


def skip(b: Bot) -> str | None:
    """The monsters never attacked while following, as a whole-name pattern (picking skip); None
    when there are none."""
    listed = names(_opt(b, "skip", "follow_skip", ""))
    if not listed:
        return None
    return r"^\s*(?:%s)\s*$" % "|".join(re.escape(n).replace(r"\ ", r"\s+") for n in listed)
