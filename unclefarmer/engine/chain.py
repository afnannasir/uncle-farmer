"""Character chain (Run, Character chain): several characters farm one after the other in one game.

Each row is a character and what it does: one of the player's plans, or its Autopilot day. The chain
takes the first row not done this game day (00:00 GMT), logs the character in the game out (the
setting menu's Switch account, as `wait` does) when it isn't that row's, and starts the row's run,
which logs the row's character in by itself (the saved account, as a schedule does). A run that
ends by itself marks its row done for the day; the next row follows. Stop ends the whole chain.
A run that failed (an error, an account the game doesn't have) leaves its row undone and the chain
goes on; a row that fails TRIES times in one chain is left for the day. A row done while its drop
time had more than REDO_SPARE left past what is kept (its plan ended early) is done again, at most
REDO_MOST times a day, once every other row is done - the drop time is what the day is for.

The rows and the choice at the end (stay logged in, or close the game) are in chain.json beside the
schedules; the marks in profiles/_chain_done.json, for the game's day only.
"""

from __future__ import annotations

import os
import threading
from dataclasses import dataclass, field
from typing import Any, cast

from .. import daily, jsonio as json, paths

CHAIN_FILE = os.path.join(paths.APP, "chain.json")
DONE_FILE = "_chain_done.json"         # in the profiles folder
ENDS = ("stay", "close")
TRIES = 2                              # failed runs of a row in one chain before it is left for the day
REDO_SPARE = 600.0                     # seconds of drop time left past the kept time: done again
REDO_MOST = 2                          # ... at most this many times a game day
_lock = threading.Lock()


@dataclass(frozen=True)
class Row:
    """One character of the chain and what it does: do = "plan" (plan = its name) or "autopilot"."""
    profile: str
    do: str
    plan: str = ""


def _rows(raw: object) -> list[Row]:
    out: list[Row] = []
    for r in cast("list[object]", raw) if isinstance(raw, list) else []:
        if not json.is_object(r) or not r.get("profile"):
            continue
        do = str(r.get("do") or "")
        if do == "autopilot" or (do == "plan" and r.get("plan")):
            out.append(Row(str(r["profile"]), do, str(r.get("plan") or "")))
    return out


def load() -> tuple[list[Row], str]:
    """(the rows, what happens at the end: "stay" or "close")."""
    try:
        with open(CHAIN_FILE, encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return [], "stay"
    if not json.is_object(got):
        return [], "stay"
    end = str(got.get("end") or "stay")
    return _rows(got.get("rows")), end if end in ENDS else "stay"


def save(rows: list[Row], end: str) -> None:
    data = {"format": 1, "end": end if end in ENDS else "stay",
            "rows": [{"profile": r.profile, "do": r.do, **({"plan": r.plan} if r.do == "plan" else {})} for r in rows]}
    with _lock:
        with open(CHAIN_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------- the day's marks
def _marks() -> dict[str, dict[str, Any]]:
    """{profile: {"spare": seconds of drop time left past the kept time, "redone": n}} for today."""
    try:
        with open(os.path.join(paths.PROFILES, DONE_FILE), encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {}
    if not json.is_object(got) or got.get("day") != daily.game_day():
        return {}
    done = got.get("done")
    if not json.is_object(done):
        return {}
    return {str(k): v for k, v in done.items() if json.is_object(v)}


def _write(marks: dict[str, dict[str, Any]]) -> None:
    try:
        with open(os.path.join(paths.PROFILES, DONE_FILE), "w", encoding="utf-8") as f:
            json.dump({"day": daily.game_day(), "done": marks}, f, ensure_ascii=False, indent=1)
    except OSError:
        pass


def done_today() -> dict[str, dict[str, Any]]:
    with _lock:
        return _marks()


def mark(profile: str, spare: float | None = None) -> None:
    """The row's character is done for the game day (spare: its drop time left past the kept
    time when its run ended, None when not known)."""
    with _lock:
        marks = _marks()
        was = marks.get(profile) or {}
        marks[profile] = {"spare": float(spare) if spare is not None else 0.0,
                          "redone": int(was.get("redone") or 0) + (1 if was else 0)}
        _write(marks)


def clear() -> None:
    """Let every character of the chain farm again today."""
    with _lock:
        _write({})


# ---------------------------------------------------------------- the order
def next_row(rows: list[Row], marks: dict[str, dict[str, Any]], failed: dict[str, int]) -> Row | None:
    """The row the chain does next: the first not done today (and not failed TRIES times in this
    chain), else the first done one with enough drop time left to be done again; None when the day
    is over for every row."""
    for r in rows:
        if r.profile not in marks and failed.get(r.profile, 0) < TRIES:
            return r
    for r in rows:
        m = marks.get(r.profile)
        if (m and float(m.get("spare") or 0) > REDO_SPARE and int(m.get("redone") or 0) < REDO_MOST
                and failed.get(r.profile, 0) < TRIES):
            return r
    return None


# ---------------------------------------------------------------- what a run's end means
@dataclass
class State:
    """A chain going on one game."""
    pid: int
    rows: list[Row]
    end: str = "stay"
    current: Row | None = None
    phase: str = ""                    # "logout" while the game is logged out, "run" while a row runs
    failed: dict[str, int] = field(default_factory=lambda: dict[str, int]())
    advance: bool = False              # `next` by private message: this row is done, go on
    waiting: float = 0.0               # a lost game being opened again since then (0: not waiting)


def after_run(state: State, stopped: bool, ended_by_itself: bool, game_lost: bool, spare: float | None) -> str:
    """What the chain does once the run of its current row has ended: "next" (go on), "wait" (its
    lost game is being opened again: the carried-on run's end decides), or "stop" (the player
    stopped it). The row's mark is made here."""
    row = state.current
    if row is None:
        return "next"
    if state.advance:
        state.advance = False
        mark(row.profile, 0.0)
        return "next"
    if stopped:
        return "stop"
    if ended_by_itself:
        mark(row.profile, spare)
        return "next"
    if game_lost:
        return "wait"
    state.failed[row.profile] = state.failed.get(row.profile, 0) + 1
    return "next"
