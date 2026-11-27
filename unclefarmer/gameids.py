"""What the game's numbers are: one place the bot asks, by number, instead of reading names.

data/ids.json, shipped with the bot, holds the game's numbers with the English name the server
gives each one (see learnids for where they are seen). What this PC noted itself
(profiles/_learned_ids.json) fills the gaps, its English names only: a name turned back into English
from another language pack may be wrong. A number nobody knows gives None: the caller does what it
did before (the name stays the fallback and what the player sees).

  item_name(23387)     -> "Large Red Potion"      (a bag row's "type")
  monster_name(611)    -> "Pineapple Sea Urchin"  (a monster entity's "facing")
  skill_name(1101)     -> "Fireball"; skill_class(1101) -> "Elf"
  numbers_named("items", "Large Red Potion") -> [23387]
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

from . import jsonio, learnids, paths

FILE = "ids.json"
SECTIONS = ("items", "monsters", "skills", "professions")
LEARNED_EVERY = 60.0        # seconds between looks at what this PC noted

_lock = threading.Lock()


class _Tables:
    def __init__(self) -> None:
        self.shipped: dict[str, dict[str, Any]] | None = None
        self.learned: dict[str, dict[str, str]] = {}
        self.learned_at = -LEARNED_EVERY


_tables = _Tables()


def _read_shipped() -> dict[str, dict[str, Any]]:
    out: dict[str, dict[str, Any]] = {s: {} for s in SECTIONS}
    try:
        got = jsonio.read(os.path.join(paths.DATA, FILE), None)
    except (OSError, ValueError):
        return out
    if jsonio.is_object(got):
        for s in SECTIONS:
            if jsonio.is_object(got.get(s)):
                out[s] = got[s]
    return out


def english_name(entry: object) -> str | None:
    """The English name a learned entry was seen with most, or None."""
    names = entry.get("names") if jsonio.is_object(entry) else None
    english = names.get("english") if jsonio.is_object(names) else None
    if not jsonio.is_object(english) or not english:
        return None
    best = max(english.items(), key=lambda kv: (kv[1] if isinstance(kv[1], int) else 0, kv[0]))
    return best[0] or None


def _read_learned() -> dict[str, dict[str, str]]:
    got = learnids.read()
    out: dict[str, dict[str, str]] = {}
    for s in SECTIONS:
        section: dict[str, Any] = got.get(s) or {}
        out[s] = {k: n for k, e in section.items() if (n := english_name(e))}
    return out


def _section(section: str) -> tuple[dict[str, Any], dict[str, str]]:
    with _lock:
        if _tables.shipped is None:
            _tables.shipped = _read_shipped()
        now = time.monotonic()
        if now - _tables.learned_at >= LEARNED_EVERY:
            _tables.learned_at = now
            _tables.learned = _read_learned()
        return _tables.shipped.get(section, {}), _tables.learned.get(section, {})


def forget() -> None:
    """Read both tables again on the next question (tests; a new data/ids.json)."""
    with _lock:
        _tables.shipped = None
        _tables.learned_at = -LEARNED_EVERY


def name(section: str, number: object) -> str | None:
    """The English name of that number, or None when neither table knows it."""
    if not isinstance(number, int) or isinstance(number, bool):
        return None
    shipped, learned = _section(section)
    entry = shipped.get(str(number))
    got = entry.get("name") if jsonio.is_object(entry) else None
    return got if isinstance(got, str) and got else learned.get(str(number))


def item_name(item_type: object) -> str | None:
    return name("items", item_type)


def monster_name(monster_type: object) -> str | None:
    return name("monsters", monster_type)


def skill_name(skill_id: object) -> str | None:
    return name("skills", skill_id)


def skill_class(skill_id: object) -> str | None:
    shipped, _learned = _section("skills")
    entry = shipped.get(str(skill_id))
    got = entry.get("class") if jsonio.is_object(entry) else None
    return got if isinstance(got, str) and got else None


def numbers_named(section: str, wanted: str) -> list[int]:
    """Every number with that English name (case aside), the shipped table's first."""
    shipped, learned = _section(section)
    key = wanted.strip().casefold()
    out = [int(k) for k, e in shipped.items()
           if k.isdigit() and jsonio.is_object(e) and isinstance(e.get("name"), str) and e["name"].casefold() == key]
    out += [int(k) for k, n in learned.items() if k.isdigit() and n.casefold() == key and int(k) not in out]
    return out
