"""Kills per target-picking policy, worked out forward in simulated visits (master plan, Phase 2).

pickreplay.py judges each recorded pick at that moment only: which monster an order would have
taken first, and the walk to it. What it can't say is where that choice leaves the character, and
so what the next pick and every one after would have been. This plays whole visits instead: the
caves' real walls and spawn cells, their 44-48 monsters coming back 30 s after each kill
(caveloops.Visit, calibrated on a bot run: 293 kills simulated against 298 counted), with every
pick made by the bot's own `engine.picking.choose` and the order under test.

    uv run python -m unclefarmer.picksim [cave map id ...] [--seeds N] [--minutes M]

Prints the mean kills of each order (pickreplay.ORDERS, and "toward the crowd": the walk to a
monster less a few steps for each other monster close to it, so a fight leaves us among the next
ones), for a caster and a melee character, at the bot's dungeon chase range.

First-order still in one way: the monsters stand where they spawn (caves' monsters barely move
until they are pulled) and nobody else is in the cave, so "doomed" never happens; what this
compares is the order of picks and the walking it makes.
"""

from __future__ import annotations

import math
import sys
from collections.abc import Sequence
from typing import Any

from . import atlas, caveloops, pickreplay
from .engine import picking

CROWD_RADIUS = 8.0       # tiles: monsters this close to one count as its crowd
CROWD_STEPS = 6.0        # steps a monster's walk is shortened by for each one in its crowd
CHASE = 90               # dungeon.CHASE: the chase range the caves are farmed with


def toward_the_crowd(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
    """Attackers first, then the walk less CROWD_STEPS for every other monster near it (PickVisit
    puts how many on each monster as "crowd")."""
    return (e["id"] not in ctx.notes.hitting, steps - CROWD_STEPS * int(e.get("crowd", 0)))


ORDERS: dict[str, picking.Order] = {**pickreplay.ORDERS, "toward the crowd": toward_the_crowd}


class PickVisit(caveloops.Visit):
    """A caveloops visit whose picks go through picking.choose with the order under test."""

    def __init__(self, mid: int, loop: Sequence[Sequence[int]], reach: float, kill_s: float, chase: int,
                 seed: int, order: picking.Order = picking.nearest) -> None:
        super().__init__(mid, loop, reach, kill_s, chase, seed)
        self.mid, self.order = mid, order

    def snap(self) -> dict[str, Any]:
        """The cave as the bot would read it now: us and the living monsters (ids -1, -2, ...)."""
        living = [(i, c) for i, c in enumerate(self.mobs) if c is not None]
        mobs: list[dict[str, Any]] = []
        for i, c in living:
            crowd = sum(1 for j, o in living if j != i and math.dist(c, o) <= CROWD_RADIUS)
            mobs.append({"id": -(i + 1), "kind": "monster", "name": "Monster", "x": float(c[0]), "y": float(c[1]),
                         "hp": 1000, "maxhp": 1000, "crowd": crowd})
        me = {"id": 1, "kind": "player", "x": float(self.pos[0]), "y": float(self.pos[1]), "hp": 100, "maxhp": 100}
        return {"map": {"id": self.mid}, "self": me, "entities": [me] + mobs}

    def _pick(self) -> int | None:
        field = self.m.steps_arr(self.pos, limit=self.chase)

        def walk(e: dict[str, Any], limit: float | None) -> float:
            d = int(field[int(e["y"]) * self.W + int(e["x"])])
            if d < 0 or (limit is not None and d > limit):
                return math.inf
            return float(d)
        got = picking.choose(self.snap(), picking.Rules(limit=self.chase, boss_keep=0),
                             picking.Notes(now=self.t, reach=self.reach), walk=walk,
                             doomed=lambda e, steps: False, order=self.order)
        return -int(got.targets[0]["id"]) - 1 if got.targets else None


def score(mid: int, kind: str, order: picking.Order, seeds: Sequence[int] = caveloops.SEEDS,
          minutes: float = caveloops.MINUTES, loop_name: str = "quick") -> float:
    """The mean kills of an order over a few simulated visits."""
    loop = atlas.patrol_loop(mid, loop_name)
    if not loop:
        raise ValueError("no %s loop for map %s" % (loop_name, mid))
    reach, kill_s = caveloops.KINDS[kind]
    return sum(PickVisit(mid, loop, reach, kill_s, CHASE, s, order).run(minutes) for s in seeds) / len(seeds)


def table(caves: Sequence[int], seeds: Sequence[int], minutes: float) -> list[str]:
    lines: list[str] = []
    for mid in caves:
        for kind in caveloops.KINDS:
            row = "  ".join("%s %3.0f" % (name, score(mid, kind, order, seeds, minutes)) for name, order in ORDERS.items())
            lines.append("%s %-6s: %s" % (atlas.name(mid), kind, row))
    return lines


def main(argv: Sequence[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    seeds: Sequence[int] = caveloops.SEEDS
    minutes = float(caveloops.MINUTES)
    if "--seeds" in args:
        i = args.index("--seeds")
        seeds = range(1, int(args[i + 1]) + 1)
        del args[i:i + 2]
    if "--minutes" in args:
        i = args.index("--minutes")
        minutes = float(args[i + 1])
        del args[i:i + 2]
    caves = [int(a) for a in args if a.isdigit()] or sorted(caveloops.CAVES)
    for line in table(caves, seeds, minutes):
        print(line, flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
