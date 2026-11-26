"""Farming talents: put on the talents ticked for farming before a run farms, and the usual ones
back when it ends in a town with a talent master (Character settings, Talents).

The talent masters are the cities' officers (and Karon Village's Master's assistant): "Reset Talents"
in their menu opens one row per learned talent, "<name>; takes N talent points", whose entry carries
cmd [talent id, 1 when it is on]. Enter on a row turns it on or off at once (the menu stays open), then
"submit", "Are you sure you want to reset?", Confirm, and the game says "reset complete" - or "not enough
talent points". A reset costs 200 arnebia for every point the talents put on take, even when nothing
changes (measured live, 2026-11-25: 183 points, 36 600 arnebia, both times), so the bot only goes when
the talents on (read from `mem skills`, for nothing) are not the ones wanted.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Sequence
from dataclasses import dataclass
from typing import Any, cast

from .. import atlas, jsonio, knowledge, travel
from ..i18n import _
from . import combos, town
from .bot import CONFIRM, Bot, Stopped

MASTERS = ("Chancellor Bren", "Guard Captain Suke", "Priest Diraf", "Knight Commander George", "General Paul",
           "Colonel Demar", "Guard Captain Bifee", "High Inquisitor Shawi", "Chief Max", "Master's assistant")
COST_PER_POINT = 200          # arnebia, as the menu says: "it costs 200 arnebia for every point taking"
RESET = r"(?i)^\s*Reset Talents"
TITLE = r"(?i)^\s*reset talent skills"
SUBMIT = r"(?i)^\s*submit\s*$"
ROW = re.compile(r"^\s*(.+?);\s*takes\s+(\d+)\s+talent points", re.I)
POINTS = re.compile(r"You have\s+(\d+)\s+talent points", re.I)
SHORT = re.compile(r"not enough talent points", re.I)
SETTLE = 5.0                  # seconds the new talents get to show in `mem skills` after Confirm


@dataclass(frozen=True)
class Row:
    """One talent row of the Reset Talents menu."""
    r: int
    name: str
    points: int
    on: bool


def wanted(p: dict[str, Any], which: str) -> list[str] | None:
    """The talents a character wants on ("farm": for farming, "back": when the run ends), or None
    when that isn't asked for."""
    on = bool(p.get("farm_talents", False)) and (which == "farm" or bool(p.get("talents_back", False)))
    got: object = p.get("farm_talent_list" if which == "farm" else "usual_talent_list")
    names: list[object] = list(cast("list[object]", got)) if isinstance(got, list) else []
    return [str(n) for n in names] if on and names else None


def read(b: Bot) -> tuple[list[str], set[str]] | None:
    """(the learned talents, the ones on now), from the agent - or None when it can't say."""
    try:
        sk = b.a.json("mem skills")
    except Stopped:
        raise
    except Exception:
        return None
    if not jsonio.is_object(sk):
        return None
    rows: list[dict[str, Any]] = sk.get("talents") or []
    return [str(r.get("name") or "") for r in rows if r.get("name")], combos.active_talents(sk)


def masters() -> list[town.Npc]:
    """Every talent master the data knows, as (map, name, x, y)."""
    out: list[town.Npc] = []
    for name in MASTERS:
        d = knowledge.describe_npc(name)
        if d:
            out.append((int(d["map"]), str(d["name"]), float(d["x"]), float(d["y"])))
    return out


def menu_rows(top: dict[str, Any] | None) -> tuple[list[Row], int | None]:
    """The talent rows of an open Reset Talents menu and the character's talent points."""
    rows: list[Row] = []
    points: int | None = None
    for r, cells in enumerate((top or {}).get("rows") or []):
        e = cells[0] if cells else None
        if not e:
            continue
        text = " ".join(str(t) for t in (e.get("options") or [e.get("text") or ""]) if t)
        m = ROW.match(text)
        got: object = e.get("cmd")
        cmd: list[object] = list(cast("list[object]", got)) if isinstance(got, list) else []
        if m and len(cmd) >= 2:
            rows.append(Row(r, m.group(1).strip(), int(m.group(2)), bool(cmd[1])))
            continue
        got = POINTS.search(text)
        if got:
            points = int(got.group(1))
    return rows, points


def plan(rows: Sequence[Row], want: Collection[str]) -> tuple[set[str], list[str], list[Row]]:
    """(the talents to have on, the wanted ones this character hasn't learned, the rows to switch)."""
    known = {r.name.lower(): r.name for r in rows}
    target = {known[w.lower()] for w in want if w.lower() in known}
    missing = [w for w in want if w.lower() not in known]
    return target, missing, [r for r in rows if r.on != (r.name in target)]


def same(on: Collection[str], want: Collection[str], learned: Collection[str]) -> bool:
    """The talents on are the wanted ones (those learned: an unlearned one can't be put on)."""
    have = {n.lower() for n in learned}
    return {n.lower() for n in on} == {w.lower() for w in want if w.lower() in have}


def for_farming(b: Bot, back_to: town.Place | None = None) -> bool:
    """Before a run farms: the talents ticked for farming on, at the nearest talent master, when they
    aren't already. True when they are on (already, or now)."""
    want = wanted(getattr(b, "p", {}) or {}, "farm")
    if want is None:
        return True
    now = read(b)
    if now is None:
        b.log(_("the talents can't be read - they stay as they are"), speak=True)
        return False
    learned, on = now
    if not {w.lower() for w in want} & {n.lower() for n in learned}:
        b.log(_("none of the talents ticked for farming is learned by this character - they stay as they are"),
              speak=True)
        return False
    if same(on, want, learned):
        b.log(_("the talents for farming are already on"))
        return True
    b.log(_("putting on the talents for farming: {names}", names=", ".join(want)))
    try:
        town.visit(b, None, candidates=masters(), back_to=back_to)
    except Stopped as e:
        if getattr(e, "final", False) or b.stop_event.is_set():
            raise
        b.log(_("couldn't get to a talent master ({why}) - farming with the talents as they are", why=e),
              speak=True)
        return False
    return reset(b, want)


def put_back(b: Bot) -> bool:
    """When a run ends: the usual talents back on, if it ended in a town whose talent master is on
    this map - no trip for it (the next run puts on the farming ones anyway). True when they are on."""
    want = wanted(getattr(b, "p", {}) or {}, "back")
    if want is None:
        return True
    now = read(b)
    if now is None:
        return False
    learned, on = now
    if same(on, want, learned):
        return True
    here = b.map_id()
    me = b.me()
    near = [m for m in masters() if m[0] == here]
    if not near or not atlas.in_town(here, float(me.get("x", 0)), float(me.get("y", 0))):
        b.log(_("the usual talents aren't back on: the run didn't end in a town with a talent master "
                "(the next run that ends in one puts them back)"))
        return False
    b.log(_("putting the usual talents back on: {names}", names=", ".join(want)))
    try:
        town.visit(b, None, candidates=near)
    except Stopped as e:
        if getattr(e, "final", False) or b.stop_event.is_set():
            raise
        b.log(_("couldn't get to the talent master ({why}) - the talents stay as they are", why=e))
        return False
    return reset(b, want)


def reset(b: Bot, want: Collection[str]) -> bool:
    """At a talent master whose menu is open: Reset Talents, the rows that differ switched, submit,
    Confirm. Checked against the points and the arnebia first, and against `mem skills` after."""
    try:
        return _reset(b, want)
    finally:
        b.close_dialogs()


def _reset(b: Bot, want: Collection[str]) -> bool:
    if b.choose(RESET) is None:
        b.log(_("the talent master's menu has no Reset Talents"), speak=True)
        return False
    d = b.wait_dialog(3.0)
    rows, points = menu_rows((d or {}).get("top"))
    if not rows:
        b.log(_("the Reset Talents menu didn't open"), speak=True)
        return False
    target, missing, switch = plan(rows, want)
    if not target:
        b.log(_("none of these talents is in the Reset Talents menu - nothing changed"), speak=True)
        return False
    if missing:
        b.log(_("not learned by this character, left out: {names}", names=", ".join(missing)))
    need = sum(r.points for r in rows if r.name in target)
    if points is not None and need > points:
        b.log(_("these talents take {need} talent points, the character has {have} - nothing changed",
                need=need, have=points), speak=True)
        return False
    if not switch:
        return True
    cost = need * COST_PER_POINT
    purse = travel.arnebia_in(b.bag_rows())
    if purse is not None and purse < cost:        # (not known: the game says so itself)
        b.log(_("a talent reset costs {cost} arnebia, there is {have} - nothing changed", cost=cost, have=purse),
              speak=True)
        return False
    for row in switch:
        if not _switch(b, row):
            b.log(_("couldn't switch {talent} in the Reset Talents menu - nothing changed", talent=row.name),
                  speak=True)
            return False
    return _submit(b, target, cost)


def _switch(b: Bot, row: Row) -> bool:
    """Enter on one talent row; True once the menu shows it switched (the keys tried once more)."""
    pattern = r"^\s*" + re.escape(row.name) + r";\s*takes\b"
    for keys in (False, True):
        if b.choose(pattern, keys=keys) is None:
            return False
        end = b.clock.time() + 2.0
        while b.clock.time() < end:
            rows, _points = menu_rows(b.dialog().get("top"))
            now = next((r for r in rows if r.name == row.name), None)
            if now is not None and now.on != row.on:
                return True
            b.sleep(0.15)
    return False


def _submit(b: Bot, target: set[str], cost: int) -> bool:
    from .chores import said_mark, said_since
    mark = said_mark(b)
    if b.choose(SUBMIT) is None:
        return False
    d = b.wait_dialog(3.0)
    if d is None or b.find_entry(d.get("top"), CONFIRM) is None or b.choose(CONFIRM) is None:
        b.log(_("the game didn't ask to confirm the talent reset - nothing changed"), speak=True)
        return False
    end = b.clock.time() + SETTLE
    while b.clock.time() < end:
        heard = said_since(b, mark)
        if any(SHORT.search(t) for t in heard):
            b.log(_("the game said: not enough talent points - nothing changed"), speak=True)
            return False
        now = read(b)
        if now is not None and same(now[1], target, now[0]):
            b.log(_("talents set: {names} ({cost} arnebia)", names=", ".join(sorted(target)) or _("none"),
                    cost=f"{cost:,}"), speak=True)
            return True
        b.sleep(0.3)
    b.log(_("the talents didn't change after the reset"), speak=True)
    return False
