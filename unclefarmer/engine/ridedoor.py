"""Out of a cable car through its door as soon as it arrives, not when the game puts us out.

Measured live (2026-11-26, Underground of Tulip City <-> Lifting Platform No. 2, both ways): a cable
car is a small map (16 x 16) the game carries us on. About 12 s after boarding the game plays sound
ARRIVED (1152) - heard in no farming record before - and from then on its door lets us out at once;
left alone, the game puts us out on the next map about 10 s later (22 s after boarding). We can walk
in the cabin all the way, only the door refuses a step until the sound (each refused press plays
11510). So the bot walks up to the door on the side of the car's destination, waits for the sound,
and steps through; with no sound to hear (an older agent) or no door in the map data, the ride is
waited out as before (botparts/travel.wait_ride).

The airships' flight halls have no door: nothing changes there.

Which door: a cable car's map holds the timer's way out - an exit in the middle of the map, the one
the game puts us out through - and doors at its ends. The door to take is the one that leads where
the timer's way out does.
"""

from __future__ import annotations

import math
from typing import TYPE_CHECKING, Any

from .. import atlas, sounds
from ..link import AgentError
from . import listening

if TYPE_CHECKING:
    from .bot import Bot

ARRIVED = 1152          # the cable car has arrived: its door opens
MIDDLE = 2.0            # cells from the map's centre: the timer's way out stands there
BEFORE_DOOR = 3         # cells short of the door to wait on, inside the cabin
DOOR_AFTER = 15.0       # seconds after boarding: with no sound to hear, try the door once then


def door(mid: int) -> tuple[tuple[int, int], tuple[int, int] | None] | None:
    """(the door to leave by, the cabin cell BEFORE_DOOR short of it, or None) on cable car mid,
    from the map data; None when it isn't a cable car or no door leads where the car goes."""
    m = atlas.maps().get(int(mid))
    if not m or "cable car" not in str(m.get("name", "")).lower():
        return None
    model = atlas.model(int(mid))
    exits = [(int(e["dest"]), (int(e["stand"][0]), int(e["stand"][1]))) for e in model.exits
             if e.get("dest") is not None and e.get("stand")]
    cx, cy = (int(m["w"]) - 1) / 2.0, (int(m["h"]) - 1) / 2.0
    middle = [x for x in exits if math.hypot(x[1][0] - cx, x[1][1] - cy) <= MIDDLE]
    if len(middle) != 1:
        return None
    goes = middle[0][0]
    doors = [cell for dest, cell in exits if dest == goes and math.hypot(cell[0] - cx, cell[1] - cy) > MIDDLE]
    if len(doors) != 1:
        return None
    d = doors[0]
    dx, dy = cx - d[0], cy - d[1]
    step = max(abs(dx), abs(dy)) or 1.0
    wait = (round(d[0] + BEFORE_DOOR * dx / step), round(d[1] + BEFORE_DOOR * dy / step))
    return d, (wait if model.ok(*wait) else None)


def listen(b: Bot) -> int | None:
    """Listen for ARRIVED on the bot's connection, with the run's other sounds (listening): the
    sound cursor to read from, or None when the agent can't."""
    try:
        cursor = sounds.events(b.a, 0)["now"]
        status: dict[str, Any] = listening.want(b, "cable car", str(ARRIVED))
    except (AgentError, OSError, ValueError):
        return None
    return cursor if status.get("hooked") else None


def heard(b: Bot, cursor: int) -> tuple[bool, int]:
    """(ARRIVED was played since cursor, the cursor to read from next)."""
    try:
        batch = sounds.events(b.a, cursor)
    except (AgentError, OSError, ValueError):
        return False, cursor
    return any(e["id"] == ARRIVED for e in batch["events"]), batch["now"]


def done(b: Bot) -> None:
    """The ride is over: the connection listens for the run's other sounds only (listening)."""
    try:
        listening.want(b, "cable car", None)
    except (AgentError, OSError, ValueError):
        pass
