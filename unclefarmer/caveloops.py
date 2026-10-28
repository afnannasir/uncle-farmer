"""How to go round the once-a-day caves (engine/dungeon.py), worked out in simulated visits.

In a cave the monsters are the whole game: 44 to 48 of one kind, and one killed comes back about
30 seconds later at a random cell anywhere in the cave (hand runs logged 2026-10-27). The first
bot run (a caster that hits from 19.5 tiles, Scorpion Cave) made 298 kills, and its fight log
says where the 15 minutes went: 397 s walking up to targets, 450 s killing, only 51 s on the lap
itself - a monster was nearly always within the chase range, so the lap hardly mattered.

This simulates a visit with the farm loop's own rules - walk the lap; with a monster within the
chase range (walking steps), walk at it until it is in reach, kill it, then carry on to the next
stop - on the cave's real walls, and compares the ways of going round. Calibrated on that run:
walking 4.5 path steps a second (4-way steps, about 3.2 tiles straight, as measured), a caster's
kill 1.36 s once in reach; the simulation then gives 293 kills for the bot's own settings against
the 298 the game counted.

What it showed (6 visits each; python -m unclefarmer.caveloops):
  - the lap barely matters: the ready quick, full and short loops and laps built for the
    character's reach all come within a few kills of each other;
  - the chase range does: a caster went from 290 to 304 kills in Scorpion Cave and from 268 to
    302 in Spider cave with 90 steps instead of farming's 40. A melee character came out the same
    either way, but its numbers are not calibrated yet (KINDS["melee"] is a guess: a Warrior made
    297 by hand where this says about 170) - a bot run with one will tell;
  - sweeping on (only taking monsters ahead, leaving those behind to the next lap) loses kills -
    a tenth to a third on the quick loop, more on the short one: turning back costs less than
    walking past;
  - taking a monster already in reach before a nearer one that needs walking changes nothing.
So dungeon visits chase 90 steps (dungeon.CHASE) on the quick loop.
"""

from __future__ import annotations

import math
import random
import sys
from collections.abc import Callable, Sequence

import numpy as np
import numpy.typing as npt

from . import atlas, shortloop
from .atlaslib import Cell

CAVES = {1539: 44, 1503: 48}                    # cave map -> monsters alive at once
LANDINGS = {1539: (155, 90), 1503: (90, 177)}   # where the game puts us in: the player spawn point
RESPAWN = 30.0                        # seconds from a kill to the monster's return
MINUTES = 15
SPEED = 4.5                           # path steps a second (4-way steps: about 3.2 tiles straight)
KINDS = {"caster": (19.5, 1.36), "melee": (3.0, 2.5)}   # reach in tiles, seconds a kill takes in reach
CHASES = (40, 60, 90, 150)
SEEDS = (1, 2, 3, 4, 5, 6)
LOOK_EVERY = 4                        # cells walked between looks for monsters on the way


def spawn_cells(mid: int) -> npt.NDArray[np.int32]:
    """The walkable cells monsters spawn on, as an (n, 2) array of x, y."""
    m = atlas.model(mid)
    _start, _walkable, cells = shortloop.ground(m, mid, {})
    return np.array(cells, dtype=np.int32)


class Visit:
    """One simulated visit: the bot on a lap among respawning monsters, for MINUTES.
    ahead: only take monsters in reach or no further from the next stop than we are (plus this
    slack) - a sweep that never turns back; None takes the nearest, as farming does.
    in_reach_first: a monster already in reach before a nearer one that needs walking."""

    def __init__(self, mid: int, loop: Sequence[Sequence[int]], reach: float, kill_s: float, chase: int, seed: int,
                 speed: float = SPEED, ahead: float | None = None, in_reach_first: bool = False) -> None:
        self.m = atlas.model(mid)
        self.W = self.m.W
        self.loop: list[Cell] = [(c[0], c[1]) for c in loop]
        self.reach, self.kill_s, self.chase, self.speed = reach, kill_s, chase, speed
        self.ahead, self.in_reach_first = ahead, in_reach_first
        self.rng = random.Random(seed)
        self.cells = spawn_cells(mid)
        n = CAVES.get(int(mid), 44)
        self.mobs: list[Cell | None] = [self._cell() for _i in range(n)]   # None while dead
        self.back_at = [0.0] * n
        self.t = self.end = 0.0
        self.kills = 0
        self.spent = {"walk_up": 0.0, "kill": 0.0, "lap": 0.0}
        self.pos: Cell = LANDINGS.get(int(mid)) or shortloop.ground(self.m, mid, {})[0]
        self.goal = self.pos

    def _cell(self) -> Cell:
        x, y = self.cells[self.rng.randrange(len(self.cells))]
        return int(x), int(y)

    def _respawn(self) -> None:
        for i, at in enumerate(self.back_at):
            if self.mobs[i] is None and at <= self.t:
                self.mobs[i] = self._cell()

    def _in_reach(self, c: Cell) -> bool:
        return (c[0] - self.pos[0]) ** 2 + (c[1] - self.pos[1]) ** 2 <= self.reach * self.reach

    def _pick(self) -> int | None:
        """The index of the monster to take now, or None: walk on."""
        field = self.m.steps_arr(self.pos, limit=self.chase)
        here = math.dist(self.pos, self.goal)
        best: tuple[int, int] | None = None
        for i, c in enumerate(self.mobs):
            if c is None:
                continue
            d = int(field[c[1] * self.W + c[0]])
            if d < 0:
                continue
            near = self._in_reach(c)
            if self.ahead is not None and not near and math.dist(c, self.goal) > here + self.ahead:
                continue
            if self.in_reach_first and near:
                d -= 10000          # no walking at all: before every one that needs some
            if best is None or d < best[0]:
                best = (d, i)
        return None if best is None else best[1]

    def _walk(self, path: list[Cell], stop: Callable[[int], bool], phase: str) -> None:
        """Walk a cell path until stop(walked) says so; the time it took goes on the clock."""
        for walked, c in enumerate(path[1:], 1):
            if self.t >= self.end:
                return
            self.pos = c
            self.t += 1.0 / self.speed
            self.spent[phase] += 1.0 / self.speed
            if stop(walked):
                return

    def _fight(self, i: int) -> None:
        target = self.mobs[i]
        if target is None:
            return              # _pick takes only the living
        if not self._in_reach(target):
            path = self.m.gated_route(self.pos, target) or [self.pos]
            self._walk(path, lambda _w: self._in_reach(target), "walk_up")
        self.t += self.kill_s
        self.spent["kill"] += self.kill_s
        if self.t <= self.end:
            self.kills += 1
        self.mobs[i] = None
        self.back_at[i] = self.t + RESPAWN

    def run(self, minutes: float = MINUTES) -> int:
        self.end = minutes * 60.0
        i = min(range(len(self.loop)), key=lambda k: math.dist(self.loop[k], self.pos))
        while self.t < self.end:
            self.goal = self.loop[i]
            self._respawn()
            hit = self._pick()
            if hit is not None:
                self._fight(hit)
                continue
            path = self.m.gated_route(self.pos, self.goal)
            if not path or len(path) < 2:
                i = (i + 1) % len(self.loop)
                self.t += 0.2
                continue

            def look(walked: int) -> bool:
                if walked % LOOK_EVERY:
                    return False
                self._respawn()
                return self._pick() is not None
            self._walk(path, look, "lap")
            if self.pos == self.goal:
                i = (i + 1) % len(self.loop)
        return self.kills


def score(mid: int, loop: Sequence[Sequence[int]], kind: str, chase: int, seeds: Sequence[int] = SEEDS,
          ahead: float | None = None, in_reach_first: bool = False) -> float:
    """The mean kills of a way of going round over a few simulated visits."""
    reach, kill_s = KINDS[kind]
    return sum(Visit(mid, loop, reach, kill_s, chase, s, ahead=ahead, in_reach_first=in_reach_first).run()
               for s in seeds) / len(seeds)


def main(argv: list[str]) -> None:
    """The comparison table behind dungeon.CHASE: every ready loop, chase range and rule."""
    caves = [int(a) for a in argv if a.isdigit()] or sorted(CAVES)
    # (ahead, in_reach_first) of each rule: see Visit
    rules: dict[str, tuple[float | None, bool]] = {"nearest": (None, False), "sweep on": (0, False),
                                                  "in reach first": (None, True)}
    for mid in caves:
        for kind in KINDS:
            for name in ("quick", "full", "short"):
                loop = atlas.patrol_loop(mid, name)
                if not loop:
                    continue
                for chase in CHASES:
                    row = "  ".join("%s %3.0f" % (r, score(mid, loop, kind, chase, ahead=a, in_reach_first=f))
                                    for r, (a, f) in rules.items())
                    print("%s %-6s %-5s chase %3d: %s" % (atlas.name(mid), kind, name, chase, row), flush=True)


if __name__ == "__main__":
    main(sys.argv[1:])
