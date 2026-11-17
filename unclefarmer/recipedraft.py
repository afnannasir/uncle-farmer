"""A draft dungeon recipe from one recorded hand run (master plan, Phase 3: "record once, then
build").

The recorder's packet log of a run through a dungeon says everything a recipe needs, in the game's
own words: which maps we went through and in what order (IN op=6), the way we walked on each
(OUT op=12), whom we talked to and which menu entries we chose (OUT op=30 `<npc id> <entry code>`,
answered by the menu itself in IN op=62), the other menu sends (OUT op=34, op=37), the game's
spoken lines with times in them (IN op=60), the door sounds (IN op=44) and the end report
(IN op=66). This turns that into a draft: one stage per map, each with where we came in, the way
walked (thinned to its turns), the talks and choices, and where we left from. A person reads the
draft and finishes the recipe; nothing here guesses a menu.

    uv run python -m unclefarmer.recipedraft <packet log (.log or .log.gz)> [--from HH:MM[:SS]] [--to HH:MM[:SS]]
        [--json out.json]

The packet log's first lines hold the login: they are never read into the draft.
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any

from . import atlas
from .pickreplay import i32, packets, u16

THIN = 1.0               # tiles: a walked point this close to the straight line between its neighbours is dropped
MIN_TEXT = 4             # a menu piece shorter than this isn't text
TIME_WORDS = re.compile(r"\b(minutes?|seconds?|hours?)\b", re.I)
REPORT_ROW = 49


@dataclass
class Talk:
    at: float
    npc: int
    npc_name: str
    where: tuple[float, float] | None
    choices: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    said: list[str] = field(default_factory=list[str])


@dataclass
class Stage:
    map: int
    name: str
    arrived: float
    left: float | None = None
    path: list[tuple[float, float]] = field(default_factory=list[tuple[float, float]])
    talks: list[Talk] = field(default_factory=list[Talk])
    menus: list[dict[str, Any]] = field(default_factory=list[dict[str, Any]])
    door_sounds: list[float] = field(default_factory=list[float])
    timers: list[str] = field(default_factory=list[str])


def clock(t: float) -> str:
    t %= 86400
    return "%02d:%02d:%02d" % (t // 3600, t % 3600 // 60, t % 60)


def seconds_of(text: str) -> float:
    """'21:05' or '21:05:30' as seconds of the day."""
    bits = [int(v) for v in text.split(":")]
    while len(bits) < 3:
        bits.append(0)
    return bits[0] * 3600 + bits[1] * 60 + bits[2]


def menu_lines(m: bytes) -> list[tuple[int | None, str]]:
    r"""An IN op=62 menu's lines in order as (entry code, text). Each line is
    `<flags>\r<code>\r...\r<text>`, lines end in \n; the code is what OUT op=30 sends when the
    entry is chosen (recorded: "264\r51\r...Enter the secret room to practice", then OUT op=30
    <npc> 51). A line with no code (a header) gets None."""
    out: list[tuple[int | None, str]] = []
    for line in m[1:].decode("utf-8", "replace").split("\n"):
        parts = [p.strip() for p in line.split("\r")]
        texts = [p for p in parts if len(p) >= MIN_TEXT and sum(c.isalpha() for c in p) >= 2]
        if not texts:
            continue
        code = parts[1] if len(parts) > 2 else ""
        out.append((int(code) if re.fullmatch(r"-?\d+", code) else None, texts[-1]))
    return out


def menu_entries(lines: Sequence[tuple[int | None, str]]) -> tuple[list[str], dict[int, str]]:
    """(every line's text, {entry code: entry text})."""
    said = [t for _c, t in lines]
    entries: dict[int, str] = {}
    for code, text in lines:
        if code is not None:
            entries.setdefault(code, text)
    return said, entries


def entity_name(m: bytes) -> str | None:
    """An IN op=32 entity's name (Protocol.entityName)."""
    if len(m) < 38:
        return None
    for base in ((41, 45) if i32(m, 1) > 0 else (37, 41)):
        end = base
        while end < len(m) and m[end] != 0x2C and m[end] >= 0x20:
            end += 1
        name = m[base:end].decode("utf-8", "replace").strip()
        if name and name[0].isalpha():
            return name
    return None


def thin(path: Sequence[tuple[float, float]], tol: float = THIN) -> list[tuple[float, float]]:
    """The walked points without those on a straight stretch (Ramer-Douglas-Peucker)."""
    if len(path) < 3:
        return list(path)
    (x1, y1), (x2, y2) = path[0], path[-1]
    dx, dy = x2 - x1, y2 - y1
    norm = (dx * dx + dy * dy) ** 0.5
    far, worst = 0, -1.0
    for i in range(1, len(path) - 1):
        px, py = path[i]
        d = abs(dy * (px - x1) - dx * (py - y1)) / norm if norm else ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
        if d > worst:
            far, worst = i, d
    if worst <= tol:
        return [path[0], path[-1]]
    return thin(path[:far + 1], tol)[:-1] + thin(path[far:], tol)


def report(m: bytes) -> dict[str, Any] | None:
    """IN op=66, as the agent's Report reads it (one-side reports: dungeons)."""
    if len(m) < 16 or m[1] != 7:
        return None
    at = 12
    rows: list[dict[str, int]] = []
    while at + 4 <= len(m) and i32(m, at) != 0:
        if at + REPORT_ROW > len(m):
            return None
        rows.append({"id": i32(m, at), "level": m[at + 6], "kills": u16(m, at + 13),
                     "damage": i32(m, at + 15), "healing": i32(m, at + 19)})
        at += REPORT_ROW
    names = m[at + 4:].decode("gbk", "replace").split(",")
    return {"place": names[0].strip() if names else "", "part": names[1].strip() if len(names) > 1 else "",
            "total_kills": u16(m, 10), "players": rows}


class Drafter:
    """Reads one log's messages in order and keeps the stages of the run."""

    def __init__(self) -> None:
        self.stages: list[Stage] = []
        self.names: dict[int, str] = {}
        self.where: dict[int, tuple[float, float]] = {}
        self.me: tuple[float, float] | None = None
        self.talk: Talk | None = None
        self.entries: dict[int, str] = {}
        self.reports: list[dict[str, Any]] = []

    @property
    def stage(self) -> Stage | None:
        return self.stages[-1] if self.stages else None

    def feed(self, t: float, way: str, op: int, m: bytes) -> None:
        if way == "IN":
            self._inbound(t, op, m)
        else:
            self._outbound(t, op, m)

    def _inbound(self, t: float, op: int, m: bytes) -> None:
        if op == 6 and len(m) >= 7:
            mid = u16(m, 1)
            if mid == 0xFFFF:                      # leaving: the next op=6 says where to
                if self.stage and self.stage.left is None:
                    self.stage.left = t
                return
            if self.stage and self.stage.left is None:
                self.stage.left = t
            name = m[7:].decode("utf-8", "replace").strip()
            self.stages.append(Stage(mid, atlas.name(mid) if mid in atlas.maps() else name, t))
            self.names.clear()
            self.where.clear()
            self.talk = None
        elif op == 32:
            name = entity_name(m)
            if name and len(m) >= 37:
                self.names[i32(m, 1)] = name
                self.where[i32(m, 1)] = (u16(m, 33) / 100.0, u16(m, 35) / 100.0)
        elif op == 62 and self.stage:
            said, entries = menu_entries(menu_lines(m))
            self.entries = entries
            if self.talk is not None:
                self.talk.said += [s for s in said if s not in self.talk.said]
        elif op == 60 and self.stage and len(m) > 1:
            text = m[2:].decode("utf-8" if m[1] >= 10 else "gbk", "replace").strip("\x00 ")
            if TIME_WORDS.search(text) and text not in self.stage.timers:
                self.stage.timers.append(text)
        elif op == 44 and self.stage:
            self.stage.door_sounds.append(t)
        elif op == 66:
            r = report(m)
            if r:
                r["at"] = clock(t)
                self.reports.append(r)

    def _outbound(self, t: float, op: int, m: bytes) -> None:
        if op == 12 and len(m) >= 26:
            self.me = (i32(m, 17) / 1000.0, i32(m, 21) / 1000.0)
            if self.stage:
                self.stage.path.append(self.me)
        elif op == 30 and len(m) >= 9 and self.stage:
            npc, code = i32(m, 1), i32(m, 5)
            reopened = code == 0 and self.talk is not None and self.talk.npc == npc and not self.talk.choices
            if not reopened and (code == 0 or self.talk is None or self.talk.npc != npc):
                self.talk = Talk(t, npc, self.names.get(npc, "?"), self.where.get(npc) or self.me)
                self.stage.talks.append(self.talk)
                self.entries = {}
            if code and self.talk is not None:
                self.talk.choices.append({"code": code, "entry": self.entries.get(code, "?")})
        elif op in (34, 37) and self.stage and len(m) > 1:
            self.stage.menus.append({"at": clock(t), "op": op, "hex": m.hex()})


def draft(messages: Iterable[tuple[float, str, int, bytes]], start: float | None = None,
          end: float | None = None) -> dict[str, Any]:
    """The draft recipe of the messages between start and end (seconds of the day)."""
    d = Drafter()
    for t, way, op, m in messages:
        tod = t % 86400
        if start is not None and tod < start:
            continue
        if end is not None and tod > end:
            break
        d.feed(t, way, op, m)
    stages: list[dict[str, Any]] = []
    for s in d.stages:
        stages.append({
            "map": s.map, "name": s.name, "arrived": clock(s.arrived),
            "seconds": round((s.left - s.arrived) if s.left is not None else 0.0, 1),
            "came_in_at": [round(v, 1) for v in s.path[0]] if s.path else None,
            "left_from": [round(v, 1) for v in s.path[-1]] if s.path and s.left is not None else None,
            "way": [[round(x, 1), round(y, 1)] for x, y in thin(s.path)],
            "talks": [{"at": clock(k.at), "npc": k.npc, "npc_name": k.npc_name,
                       "where": [round(v, 1) for v in k.where] if k.where else None,
                       "choices": k.choices, "said": k.said[:6]} for k in s.talks],
            "menus": s.menus, "door_sounds": len(s.door_sounds),
            "timers": s.timers if len(s.timers) < 3 else [s.timers[0], s.timers[-1]],
        })
    return {"format": "Uncle Farmer recipe draft 1", "stages": stages, "reports": d.reports}


def summary(dr: dict[str, Any]) -> list[str]:
    """The draft as lines a person reads to finish the recipe."""
    out: list[str] = []
    for i, s in enumerate(dr["stages"], 1):
        out.append("%d. %s (%s) from %s, %s s" % (i, s["name"], s["map"], s["arrived"], s["seconds"]))
        if s["came_in_at"]:
            out.append("   came in at %s, %d turns walked, left from %s" % (
                tuple(s["came_in_at"]), max(0, len(s["way"]) - 2), tuple(s["left_from"]) if s["left_from"] else "-"))
        for k in s["talks"]:
            chosen = "; ".join("%s (%d)" % (c["entry"], c["code"]) for c in k["choices"]) or "nothing chosen"
            out.append("   talked to %s at %s: %s" % (k["npc_name"], tuple(k["where"]) if k["where"] else "?", chosen))
        if s["menus"]:
            out.append("   %d other menu sends (op=34/37)" % len(s["menus"]))
        if s["door_sounds"]:
            out.append("   %d door sounds" % s["door_sounds"])
        for line in s["timers"]:
            out.append("   timer: %s" % line)
    for r in dr["reports"]:
        out.append("end report %s: %s %s, %d kills in all" % (r["at"], r["place"], r["part"], r["total_kills"]))
    return out


def main(argv: Sequence[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="python -m unclefarmer.recipedraft", description=(__doc__ or "").split("\n\n")[0])
    ap.add_argument("packets", help="the recorder's packet log (.log or .log.gz)")
    ap.add_argument("--from", dest="start", help="first time of the run, HH:MM[:SS]")
    ap.add_argument("--to", dest="end", help="last time of the run, HH:MM[:SS]")
    ap.add_argument("--json", help="write the draft here")
    a = ap.parse_args(argv)
    dr = draft(packets(a.packets), seconds_of(a.start) if a.start else None, seconds_of(a.end) if a.end else None)
    for line in summary(dr):
        print(line)
    if a.json:
        with open(a.json, "w", encoding="utf-8") as f:
            json.dump(dr, f, ensure_ascii=False, indent=1)
    return 0


if __name__ == "__main__":
    sys.exit(main())
