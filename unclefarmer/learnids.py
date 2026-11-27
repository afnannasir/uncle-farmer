"""The game's own numbers as the agent's answers show them, noted on this PC for a shared table.

The game keeps no table of its items, monsters or skills: the server sends each one with its name
when it is needed (searched in memory, 2026-11-27). So the table is built from what the games see.
Every agent answer goes through link.AgentLink.json; note() looks at the few that carry numbers:

  - mem bagfull / mem equipment: each row's item type and name;
  - a shop's menu (dialog): cmd [1, item type, x] with the name, "unit price N arnebia" and often
    the item's description (a Storehouse's take-out row is [1, bag uid, 0] too, without a price:
    never taken for a shop row);
  - mem state / mem: a living monster's type (the agent's "facing", field g), name, map and HP;
  - mem skills: skill, profession and talent numbers and names, with the class the skills say.

Nothing about players is noted: no player, no pet (its name is the player's), no NPC (their number
is an appearance), no character name or id, no position, no chat. Names are kept under the game's
language (language\\lang.txt): one turned back into English from another language may be wrong, so
the table prefers English ones. The file stays on this PC (profiles\\_learned_ids.json): nothing is
sent anywhere; a player may send it to the developers, who fold it into data/ids.json
(python -m unclefarmer --merge-ids).

Never raises: a note that goes wrong is dropped, the bot carries on.
"""

from __future__ import annotations

import atexit
import datetime
import os
import re
import threading
import time
from typing import Any

from . import gamelang, jsonio, paths
from .errors import TESTING_ENV

FILE = "_learned_ids.json"
FORMAT = 1
SAVE_EVERY = 300.0          # seconds between saves while there is something new
STATE_EVERY = 5.0           # mem state comes several times a second: monsters looked at this often
MAPS_MOST = 30              # maps kept for one monster
TYPE_MOST = 1_000_000       # item and monster types are far below; bag uids far above
SECTIONS = ("items", "monsters", "skills", "professions", "talents")
_PRICE = re.compile(r"(?i)\bunit price\b\D*(\d+)")
_lock = threading.Lock()
_save_lock = threading.Lock()      # one save at a time (the notes' lock isn't held while it writes)


class Notes:
    def __init__(self) -> None:
        self.new: dict[str, dict[str, Any]] = {s: {} for s in SECTIONS}
        self.seen: set[tuple[Any, ...]] = set()     # what this run already counted
        self.values: dict[tuple[str, str], Any] = {}    # (number, field): the last value noted this run
        self.dirty = False
        self.saved_at = time.monotonic()
        self.state_at: dict[int, float] = {}        # per link: when its monsters were last looked at
        self.failed = False


_notes = Notes()


def _language() -> str:
    try:
        return gamelang.language()
    except Exception:  # noqa: BLE001 - never stops anything
        return "unknown"


def _entry(section: str, key: str, cls: str = "") -> dict[str, Any]:
    table = _notes.new[section]
    if cls:
        table = table.setdefault(cls, {})
    return table.setdefault(key, {})


def _name(section: str, key: str, name: object, lang: str, cls: str = "") -> bool:
    """Count one name of one number for this run (once). True when it is new to this run."""
    if not isinstance(name, str) or not name.strip():
        return False
    name = name.strip()
    mark = (section, cls, key, name, lang)
    entry = _entry(section, key, cls)
    if mark in _notes.seen:
        return False
    _notes.seen.add(mark)
    names: dict[str, dict[str, int]] = entry.setdefault("names", {})
    names.setdefault(lang, {})
    names[lang][name] = names[lang].get(name, 0) + 1
    _notes.dirty = True
    return True


def _value(section: str, key: str, field: str, value: Any, better: bool = False) -> None:
    """Note a field of one number (a price, a description, the most HP, with better) when this run
    hasn't noted it yet: after a save the entry starts empty again, the value seen stays here."""
    mark = (section + ":" + key, field)
    old = _notes.values.get(mark)
    if old == value or (better and old is not None and value <= old):
        return
    _notes.values[mark] = value
    _entry(section, key)[field] = value
    _notes.dirty = True


def _number(value: object) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and 0 < value < TYPE_MOST else None


def _items(rows: object, lang: str) -> None:
    if not jsonio.is_array(rows):
        return
    for row in rows:
        if jsonio.is_object(row) and (t := _number(row.get("type"))) is not None:
            _name("items", str(t), row.get("name"), lang)


def _shop(data: object, lang: str) -> None:
    top = data.get("top") if jsonio.is_object(data) else None
    rows = top.get("rows") if jsonio.is_object(top) else None
    if not jsonio.is_array(rows):
        return
    for e in (e for row in rows if jsonio.is_array(row) for e in row):
        if not jsonio.is_object(e):
            continue
        cmd, options = e.get("cmd"), e.get("options")
        if not (jsonio.is_array(cmd) and len(cmd) == 3 and cmd[0] == 1 and jsonio.is_array(options) and options):
            continue
        price = next((m for o in options[1:] if isinstance(o, str) and (m := _PRICE.search(o))), None)
        t = _number(cmd[1])
        if price is None or t is None:
            continue            # a Storehouse's take-out row ([1, uid, 0]) has no price
        key = str(t)
        _name("items", key, options[0], lang)
        about = next((o.strip() for o in options[2:] if isinstance(o, str) and o.strip()), "")
        if about and lang == "english":     # (a description turned back from another language: no)
            _value("items", key, "about", about)
        if cmd[2] == 0:     # a shop's fixed price (the Shard Market's rows carry the day's price instead)
            _value("items", key, "price", int(price.group(1)))


def _monsters(data: object, lang: str, link: int) -> None:
    if not jsonio.is_object(data) or not jsonio.is_array(data.get("entities")):
        return
    now = time.monotonic()
    if now - _notes.state_at.get(link, -STATE_EVERY) < STATE_EVERY:
        return
    _notes.state_at[link] = now
    where = data.get("map")
    map_id = _number(where.get("id")) if jsonio.is_object(where) else None
    for e in data["entities"]:
        if not (jsonio.is_object(e) and e.get("kind") == "monster"):
            continue
        t, hp = _number(e.get("facing")), e.get("maxhp")
        if t is None or not isinstance(hp, int) or hp <= 0:
            continue            # things on the ground have no HP
        key = str(t)
        _name("monsters", key, e.get("name"), lang)
        _value("monsters", key, "maxhp", hp, better=True)
        if map_id is not None and ("map", key, map_id) not in _notes.seen:
            _notes.seen.add(("map", key, map_id))
            maps: list[int] = _entry("monsters", key).setdefault("maps", [])
            if len(maps) < MAPS_MOST:
                maps.append(map_id)
                _notes.dirty = True


def _skills(data: object, lang: str) -> None:
    if not jsonio.is_object(data):
        return
    from .engine import combat     # (the engine imports the link: not at the top)
    rows = data.get("combat")
    learned = {str(r.get("name")) for r in rows if jsonio.is_object(r)} if jsonio.is_array(rows) else set[str]()
    cls = combat.class_of_skills(learned) or ""
    for section, key_name in (("skills", "combat"), ("professions", "profession")):
        found = data.get(key_name)
        if not jsonio.is_array(found):
            continue
        for r in found:
            if jsonio.is_object(r) and (n := _number(r.get("id"))) is not None:
                _name(section, str(n), r.get("name"), lang)
                if section == "skills" and cls and ("class", n, cls) not in _notes.seen:
                    _notes.seen.add(("class", n, cls))
                    _entry(section, str(n)).setdefault("classes", []).append(cls)
                    _notes.dirty = True
    talents = data.get("talents")
    if cls and jsonio.is_array(talents):
        for r in talents:
            if jsonio.is_object(r) and (n := _number(r.get("id"))) is not None:
                _name("talents", str(n), r.get("name"), lang, cls)


def note(line: str, data: Any, link: int = 0) -> None:
    """Look at one agent answer (after it is turned back into English). Never raises."""
    if _notes.failed:
        return
    parts = line.split()
    if not parts:
        return
    verb, what = parts[0], parts[1] if len(parts) > 1 else ""
    if verb not in ("mem", "dialog"):
        return
    try:
        with _lock:
            lang = _language()
            if verb == "dialog":
                _shop(data, lang)
            elif what in ("bagfull", "equipment"):
                _items(data, lang)
            elif what in ("", "state"):
                _monsters(data, lang, link)
            elif what == "skills":
                _skills(data, lang)
        if _notes.dirty and time.monotonic() - _notes.saved_at >= SAVE_EVERY:
            save()
    except Exception:  # noqa: BLE001 - collecting must never stop a run
        _notes.failed = True     # (one bad shape: stop collecting for this run rather than fail again)


# ---------------------------------------------------------------- the file
def path() -> str:
    return os.path.join(paths.PROFILES, FILE)


def _empty() -> dict[str, Any]:
    return {"format": FORMAT,
            "about": "Game numbers seen on this PC by Uncle Farmer: item, monster, skill and talent "
                     "numbers with their names. No character, player or chat. Nothing is sent.",
            **{s: {} for s in SECTIONS}}


def read() -> dict[str, Any]:
    """The file as it is (an empty table when there is none or it can't be used)."""
    try:
        got = jsonio.read(path(), None)
    except (OSError, ValueError):
        return _empty()
    if not jsonio.is_object(got) or got.get("format") != FORMAT:
        return _empty()
    out = _empty()
    for s in SECTIONS:
        if jsonio.is_object(got.get(s)):
            out[s] = got[s]
    return out


def merge_entry(old: dict[str, Any], new: dict[str, Any]) -> None:
    """new's names, maps, classes and numbers added into old (in place)."""
    every: dict[str, dict[str, int]] = new.get("names") or {}
    for lang, names in every.items():
        mine: dict[str, int] = old.setdefault("names", {}).setdefault(lang, {})
        for name, n in names.items():
            mine[name] = mine.get(name, 0) + int(n)
    for key in ("maps", "classes"):
        values: list[Any] = new.get(key) or []
        for v in values:
            got: list[Any] = old.setdefault(key, [])
            if v not in got and len(got) < MAPS_MOST:
                got.append(v)
    if new.get("maxhp", 0) > old.get("maxhp", 0):
        old["maxhp"] = new["maxhp"]
    for key in ("about", "price"):
        if key in new:
            old[key] = new[key]


def merge_into(table: dict[str, Any], section: str, new: dict[str, Any]) -> None:
    target: dict[str, Any] = table.setdefault(section, {})
    if section == "talents":
        for cls, entries in new.items():
            for key, entry in entries.items():
                merge_entry(target.setdefault(cls, {}).setdefault(key, {}), entry)
        return
    for key, entry in new.items():
        merge_entry(target.setdefault(key, {}), entry)


def _base(p: str) -> dict[str, Any] | None:
    """The file's table to add to; None when it can't be read just now or is a newer bot's (never
    written over unread: an antivirus or OneDrive holding it must not cost what it holds)."""
    try:
        got = jsonio.read(p, None)
    except OSError:
        return None
    except ValueError:
        jsonio.copy_aside(p)                # not JSON: kept aside, a fresh file begun
        return _empty()
    if got is None:
        return _empty()
    if jsonio.is_object(got) and isinstance(got.get("format"), int) and got["format"] > FORMAT:
        return None
    if not jsonio.is_object(got) or got.get("format") != FORMAT:
        jsonio.copy_aside(p)
        return _empty()
    out = _empty()
    for s in SECTIONS:
        if jsonio.is_object(got.get(s)):
            out[s] = got[s]
    return out


def _write(pending: dict[str, dict[str, Any]]) -> bool:
    p = path()
    table = _base(p)
    if table is None:
        return False
    try:
        for s in SECTIONS:
            merge_into(table, s, pending[s])
    except (AttributeError, KeyError, TypeError, ValueError):
        jsonio.copy_aside(p)        # the file broken inside (edited by hand?): kept aside, begun again
        table = _empty()
        for s in SECTIONS:
            merge_into(table, s, pending[s])
    table["updated"] = datetime.date.today().isoformat()
    try:
        os.makedirs(os.path.dirname(p), exist_ok=True)
        jsonio.save(p, table, indent=1)
    except OSError:
        return False
    return True


def save() -> None:
    """Add what this run noted to the file (read again first, so nothing in it is lost). The file
    is read and written outside the notes' lock: the bot's readings never wait for it."""
    with _save_lock:
        with _lock:
            if not _notes.dirty:
                return
            pending, _notes.new, _notes.dirty = _notes.new, {s: {} for s in SECTIONS}, False
            _notes.saved_at = time.monotonic()      # (a failed save is tried again in SAVE_EVERY)
        try:
            written = _write(pending)
        except Exception:  # noqa: BLE001 - a save that goes wrong keeps the notes for the next one
            written = False
        if not written:
            with _lock:
                for s in SECTIONS:
                    merge_into(pending, s, _notes.new[s])
                _notes.new, _notes.dirty = pending, True


def _at_exit() -> None:
    if os.environ.get(TESTING_ENV):
        return                      # (the tests' notes are their own, in their own folders)
    try:
        save()
    except Exception:  # noqa: BLE001 - closing the app never fails over this
        pass


atexit.register(_at_exit)
