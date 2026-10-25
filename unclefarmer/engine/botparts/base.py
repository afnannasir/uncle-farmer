"""What every part of the bot shares: its exceptions, the walking directions, the game's
fixed texts and a few small helpers."""

import math
from typing import Any

from ...errors import carry_on
from ...i18n import _
from ...link import AgentError

DIRECTIONS: dict[tuple[int, int], str] = {(1, 0): "right", (-1, 0): "left", (0, 1): "down", (0, -1): "up"}  # y grows south
NPC_HP = 20000000   # town NPCs report exactly this much HP; never a target
CONFIRM = r"(?i)^\s*(ok|yes|confirm|sure|accept|确定|是)\b"   # the only follow-up entry ride() accepts


_SKILL_THINGS = None


def skill_things():
    """Lower-case names of every class skill. Traps and totems a player puts down (Hunter's Tar
    Trap and Vine Trap, Shaman totems) come in as monsters named after the skill, with 100 HP; live
    2026-10-04 the bot shot its own traps for 15 s each. No monster in the atlas has a skill's name."""
    global _SKILL_THINGS
    if _SKILL_THINGS is None:
        from .. import combat
        _SKILL_THINGS = {s["name"].lower() for skills in combat.knowledge().values() for s in skills if s.get("name")}
    return _SKILL_THINGS


def snap_map(snap: dict[str, Any]) -> Any:
    """The map id of a world snapshot. The agent sends the map as {"id": ..., ...}; a bare
    number is accepted too. The id itself, not the dict, is what keys a lookup."""
    m = snap.get("map")
    return m.get("id") if isinstance(m, dict) else m


def steps(lo: int, hi: int, most: int) -> list[int]:
    """Evenly spaced whole numbers from lo to hi, both ends included, at most `most` apart."""
    if hi <= lo:
        return [(lo + hi) // 2]
    n = int(math.ceil((hi - lo) / float(most)))
    return [int(round(lo + (hi - lo) * i / float(n))) for i in range(n + 1)]


_steps = steps


def potion_kind(name: str) -> str | None:
    """"hp" for a red potion, "mp" for a blue one, "pet_hp"/"pet_mp" for the pet's, else None."""
    name = (name or "").lower()
    if "potion" not in name:
        return None
    k = "hp" if "red" in name else "mp" if ("blue" in name or "magic" in name) else None
    if k and "pet" in name:
        k = "pet_" + k        # Pet Large Red Potion heals the pet, never us
    return k


class Stopped(Exception):
    """The run has to end: the user pressed Stop, a stop rule fired, or something went wrong.
    final: nothing more can be done this run (a stop rule, the game unreadable, keys refused), so a
    task that goes on past a failed part - a survey skipping a map it can't reach - must end too."""

    stay: bool      # a follow/come run ends beside the player, not at its end place
    asked: bool     # a player asked for it (home): not farming ending by itself, so no after-stop action

    def __init__(self, *args, final=False):
        super().__init__(*args)
        self.final = final


class GameBroken(Stopped):
    """The game itself stopped working (it ran out of memory): nothing the bot does there works any
    more, so no guarding, no walk to the end place, and no closing the game or the PC as if farming
    had ended - the player restarts the game."""

    def __init__(self, *args):
        super().__init__(*args, final=True)


class Respawned(Exception):
    """We died and came back (map respawn or the city): whatever we were doing starts over."""


# A best-effort action may ignore a failure, but never one of these: the run has to stop (Stopped),
# start over (Respawned), or report the lost agent (AgentError) - not carry on as if nothing happened.
STOPPING = (Stopped, Respawned, AgentError)


AGENT_PATIENCE = 3    # wrong answers in a row before an agent command is given up for the run


def agent_strike(bot: Any, what: str, ok: bool) -> bool:
    """Count one answer to an agent command (slot, use, select): a good one forgives the misses
    before it; True once AGENT_PATIENCE wrong ones came in a row - only then is the command given
    up for the rest of the run. One odd answer (the game changing the bag just as it is read)
    used to send the bot back to the keyboard and the arrow keys for the whole night."""
    misses: dict[str, int] | None = getattr(bot, "agent_misses", None)
    if misses is None:
        misses = bot.agent_misses = {}
    if ok:
        misses.pop(what, None)
        return False
    misses[what] = misses.get(what, 0) + 1
    return misses[what] >= AGENT_PATIENCE


def note_failure(bot: Any, where: str, exc: BaseException) -> None:
    """Log an ignored failure once per kind per run: a broken corner must not be silent, and must
    not fill the log either (the same failure in a 25 Hz loop would). Tests re-raise it."""
    carry_on(exc, where)
    seen = getattr(bot, "failure_notes", None)
    if seen is None:
        seen = bot.failure_notes = set()
    key = (where, type(exc).__name__)
    if key not in seen:
        seen.add(key)
        log = getattr(bot, "log", None)
        if log:
            log(_("{where} failed: {err} (carrying on)", where=where, err=exc))


# The respawn menu (IN op=62 after death, seen in our logs on normal maps, crystal maps and the
# pyramid): "Please select your respawn method" with some of
#   Map Respawn | Respawn back to the City (<square>) (asks "Are you sure...?")
#   Respawn On-Spot with Resurrection Crystal; Costs 2 ... | Map Respawn with Resurrection Crystal; ...
#   Purchase Resurrection Crystal in the mall (never)
RESPAWN_MENU = r"select your respawn method"
RESPAWN = {"map": r"^\s*Map Respawn\s*$", "city": r"back to the City",
           "crystal_spot": r"On-Spot with Resurrection Crystal", "crystal_map": r"Map Respawn with Resurrection Crystal"}
