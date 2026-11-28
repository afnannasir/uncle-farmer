"""Farming medals: wear the medals ticked for farming before a run farms, and the usual ones again
when it ends (Character settings, Medals). No NPC, no cost, anywhere.

Measured live (2026-11-26, by hand, agent `outlog`):
- the bag's equipment tab's use key sends op 18, panel 1, the piece's id, then 0 to wear it or 1 to take
  it off (agent 81 `gearwear <uid> on|off`); `mem equipment` shows the change at once;
- a medal of a kind already worn (one Contribution Medal, one star medal, one of the honour medals...)
  takes the place of the one worn: the game takes that one off by itself, in the same second;
- every piece has its own cooldown, about 10 s from its last change ("1 seconds, wear cooldown"):
  other pieces can be changed meanwhile;
- a "(locked)" medal comes off and goes on like any other.
Medals are kept by their id: every Contribution Medal has the same name, and only its bonus ("plus:
..." in its details, the G key) tells them apart. Those bonuses are read once per medal and kept in
profiles/_learned_medal_bonus.json for the settings window.
"""

from __future__ import annotations

import os
import re
import threading
from collections.abc import Sequence
from typing import Any, cast

from .. import jsonio as json, paths
from ..i18n import _
from .bot import Bot, Stopped

GEAR_WEAR_AGENT = 81          # `gearwear <uid> on|off`
COOLDOWN = 10.5               # seconds a piece waits after its own last change
CHANGE_WAIT = 2.0             # seconds `mem equipment` gets to show a change
COOLDOWN_SAID = re.compile(r"(\d+)\s*seconds?,\s*wear cooldown", re.I)
MEDAL = re.compile(r"medal", re.I)     # as the scan reads them (scan.py): Medalla, Medalha in other languages
CODES = re.compile(r"lzs\d+lzs\d*lzs\d+lzs")   # the game's colour marks in a name ("lzs0lzs2250lzs1lzs")
PLUS = re.compile(r"plus:\s*([^>|;]+)", re.I)
BONUS_FILE = "_learned_medal_bonus.json"
DETAILS_WAIT = 2.0
_lock = threading.Lock()


def display(name: str) -> str:
    """A medal's name as a player reads it: the game's colour marks left out."""
    return CODES.sub("", name).strip()


def medals(b: Bot) -> list[dict[str, Any]] | None:
    """Every medal the character has, worn or in the bag: {"uid", "name", "worn"}; None when the
    agent can't say."""
    try:
        got: object = b.a.json("mem equipment")
    except Stopped:
        raise
    except Exception:
        return None
    if not isinstance(got, list):
        return None
    out: list[dict[str, Any]] = []
    for row in cast("list[object]", got):
        if not json.is_object(row):
            continue
        name = str(row.get("name") or "")
        if name and row.get("uid") is not None and MEDAL.search(name):
            out.append({"uid": int(row["uid"]), "name": name, "worn": int(row.get("equipped") or 0) == 1})
    return out


def wanted(p: dict[str, Any], which: str) -> list[dict[str, Any]] | None:
    """The medals a character wants worn ("farm": while farming, "back": after), as saved
    [{"uid", "name"}], or None when that isn't asked for."""
    on = bool(p.get("farm_medals", False)) and (which == "farm" or bool(p.get("medals_back", False)))
    got: object = p.get("farm_medal_list" if which == "farm" else "usual_medal_list")
    rows: list[object] = list(cast("list[object]", got)) if isinstance(got, list) else []
    picked = [r for r in rows if json.is_object(r) and r.get("uid") is not None]
    return picked if on and picked else None


# ---------------------------------------------------------------- the bonus of medals that share a name
def bonuses() -> dict[str, str]:
    """{medal id: its bonus}, as read from the game before."""
    try:
        with open(os.path.join(paths.PROFILES, BONUS_FILE), encoding="utf-8") as f:
            got = json.load(f)
    except (OSError, ValueError):
        return {}
    return {str(k): str(v) for k, v in got.items()} if json.is_object(got) else {}


def _keep_bonuses(found: dict[str, str]) -> None:
    with _lock:
        known = bonuses()
        known.update(found)
        try:
            with open(os.path.join(paths.PROFILES, BONUS_FILE), "w", encoding="utf-8") as f:
                json.dump(known, f, ensure_ascii=False, indent=1)
        except OSError:
            pass


def bonus_of(text: str) -> str:
    """The bonus in a piece's details ("plus: When attacked critical hit resistance increase 3"), or ""."""
    m = PLUS.search(text)
    return ("plus: " + m.group(1).strip()) if m else ""


def details(b: Bot, uid: int, name: str) -> str | None:
    """A piece's details as the game says them for the details key, or None when none came."""
    from .chores import said_mark, said_since
    mark = said_mark(b)
    if not mark:                     # no clock from the agent: every old line would count as the answer
        return None
    if not b.a.send("gearinfo %d" % uid).startswith("asked"):
        return None
    end = b.clock.time() + DETAILS_WAIT
    start = name.strip().lower()
    while True:
        for text in said_since(b, mark):
            if text.strip().lower().startswith(start):
                return text
        if b.clock.time() >= end:
            return None
        b.sleep(0.2)


def learn_bonuses(b: Bot) -> None:
    """The bonus of each medal whose name another of the character's medals has too (Contribution
    Medals), once per medal: the settings window shows it, so the right one can be ticked. One that
    gets no answer in time ends it for this run: the late answer would be read as the next one's
    (they share the name), and kept for good."""
    if int(getattr(b, "agent_version", 0) or 0) < GEAR_WEAR_AGENT or b.__dict__.get("medal_bonus_tried"):
        return
    b.__dict__["medal_bonus_tried"] = True
    rows = medals(b) or []
    names = [display(r["name"]) for r in rows]
    known = bonuses()
    found: dict[str, str] = {}
    for r in rows:
        if names.count(display(r["name"])) < 2 or str(r["uid"]) in known:
            continue
        text = details(b, r["uid"], r["name"])
        if text is None:
            break
        found[str(r["uid"])] = bonus_of(text)
    if found:
        _keep_bonuses(found)


# ---------------------------------------------------------------- wearing
def for_farming(b: Bot) -> bool:
    """Before a run farms: the medals ticked for farming worn, the others taken off."""
    want = wanted(getattr(b, "p", {}) or {}, "farm")
    return True if want is None else wear(b, want, _("the medals for farming"))


def put_back(b: Bot) -> bool:
    """When a run ends: the usual medals worn again, the others taken off."""
    want = wanted(getattr(b, "p", {}) or {}, "back")
    return True if want is None else wear(b, want, _("the usual medals"))


def wear(b: Bot, want: Sequence[dict[str, Any]], what: str) -> bool:
    """Wear exactly these medals: each one not worn put on (one of its kind comes off by itself),
    then each worn one not wanted taken off. Every change checked; a piece still cooling down waits
    for its own cooldown while the others go on. True when the medals are as wanted."""
    if int(getattr(b, "agent_version", 0) or 0) < GEAR_WEAR_AGENT:
        b.log(_("changing medals needs the agent that comes with this version: restart the game once"), speak=True)
        return False
    rows = medals(b)
    if rows is None:
        b.log(_("the medals can't be read - they stay as they are"), speak=True)
        return False
    have = {r["uid"]: r for r in rows}
    gone = [display(str(w.get("name") or w["uid"])) for w in want if int(w["uid"]) not in have]
    if gone:
        b.log(_("no longer in the bag, left out: {names}", names=", ".join(gone)))
    wanted_ids = [int(w["uid"]) for w in want if int(w["uid"]) in have]
    if not wanted_ids:
        b.log(_("none of {what} is in the bag - the medals stay as they are", what=what), speak=True)
        return False
    if all(have[u]["worn"] for u in wanted_ids) and not [r for r in rows if r["worn"] and r["uid"] not in wanted_ids]:
        b.log(_("{what} are already worn", what=what))
        return True
    b.log(_("wearing {what}: {names}", what=what, names=", ".join(display(have[u]["name"]) for u in wanted_ids)))
    failed: list[str] = []
    for uid in wanted_ids:
        if not _is_worn(b, uid) and not _change(b, uid, True):
            failed.append(display(have[uid]["name"]))
    for row in medals(b) or []:
        if row["worn"] and row["uid"] not in wanted_ids and not _change(b, row["uid"], False):
            failed.append(display(row["name"]))
    if failed:
        b.log(_("couldn't change these medals: {names}", names=", ".join(failed)), speak=True)
        return False
    now = {r["uid"]: r["worn"] for r in medals(b) or []}
    off = [display(have[u]["name"]) for u in wanted_ids if not now.get(u)]
    if off:
        # two ticked medals of one kind: the second took the first one's place
        b.log(_("not worn in the end, a medal of the same kind took its place: {names}", names=", ".join(off)),
              speak=True)
        return False
    b.log(_("{what} are worn", what=what))
    return True


def _is_worn(b: Bot, uid: int) -> bool:
    return any(r["uid"] == uid and r["worn"] for r in medals(b) or [])


def _change(b: Bot, uid: int, on: bool) -> bool:
    """Wear (on) or take off one piece, after its own cooldown; checked in `mem equipment`. A refusal
    for the cooldown ("N seconds, wear cooldown") waits that long and tries once more."""
    from .chores import said_mark, said_since
    last: dict[int, float] = b.__dict__.setdefault("gear_changed", {})
    for _try in range(2):
        if b.stop_event.is_set():
            return False
        ready = min(last.get(uid, 0.0) + COOLDOWN - b.clock.time(), COOLDOWN + 1.0)   # (a clock set back)
        if ready > 0:
            b.sleep(ready)
        before = {r["uid"]: r["worn"] for r in medals(b) or []}
        if before.get(uid) == on:
            return True               # already so: the swap of a medal of its kind showed late
        mark = said_mark(b)
        answer = b.a.send("gearwear %d %s" % (uid, "on" if on else "off"))
        if not answer.startswith(("wearing", "taking off")):
            b.log(_("the game wasn't asked to change a medal ({why})", why=answer))
            return False
        end = b.clock.time() + CHANGE_WAIT
        while b.clock.time() < end:
            now = {r["uid"]: r["worn"] for r in medals(b) or []}
            if now.get(uid) == on:
                t = b.clock.time()
                for other, worn in now.items():       # and the one its kind took off, if any
                    if before.get(other) != worn:
                        last[other] = t
                last[uid] = t
                return True
            heard = " ".join(said_since(b, mark))
            wait = COOLDOWN_SAID.search(heard)
            if wait:
                last[uid] = b.clock.time() - COOLDOWN + float(wait.group(1)) + 1.0
                break
            b.sleep(0.2)
        else:
            # no change and no English cooldown line (a game in another language says it otherwise):
            # the next try waits a whole cooldown
            last[uid] = b.clock.time()
    return False
