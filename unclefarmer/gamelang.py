"""The game in another language: turn its translated text back into English for the bot.

The game translates on the client (game.Trans): names of monsters, items, pets and skills, and
every NPC menu line, are put through the chosen language (language\\lang.txt) as they arrive. The
bot matches English everywhere - "Red Potion", skill names, the respawn menu, cable cars - so
with any other language pack it would find no potions and no menus. Instead of teaching every rule
every language, the agent's answers are turned back into English here, once, where they arrive
(link.AgentLink.json). With English chosen nothing is done at all.

"English" means what the game's own english folder shows, not what the server sends: that pack
rewords thousands of server texts ("Bone Cookie" is shown as "Pet Bone Cookie", "Lesser Cattle Soul
Stone" as "Lesser Cow Soul Stone", "{d} Arnebia" as "$1 Arnebia"), and the bot's names were all
learned from it. So another pack - a translation, or another English one such as en_uk - is turned
into the english folder's wording: what it translated, back through the original to the english
folder's text, and what it left as the server sent it, into the english folder's text too.

How the game reads a language folder (javap of game.Trans, 2026-10-21):
  - style.txt: "key=value" lines (client templates; not needed here).
  - basic.txt, mapname.txt, terrain.txt, and every file in more\\: the text has runs of 3+ line
    breaks collapsed, is split into blocks at "\\r\\n\\r\\n", and each block's first two lines are
    key and value. Only CRLF counts: a file saved with bare line feeds is one block, ignored.
  - Keys are the original: Chinese for the client's own words (their English is in the english
    folder under the same key) or English for what the server sends. Keys with {d} {s} {c} are
    templates; their values use $1, $2 ... (or {sub2{$1}sub2}: that part translated again).
  - A name made of parts ("Kind>><< name||| belongs to owner") is translated part by part and
    joined without the marks, so it is turned back part by part too.
"""

from __future__ import annotations

import os
import re
import threading
import time
from collections.abc import Iterator
from typing import Any

from . import jsonio, paths, settings

_lock = threading.Lock()


class _LangState:
    """Which language the table is for, when lang.txt was last looked at, and the table."""

    def __init__(self) -> None:
        self.key: tuple[str, str] | None = None      # (game folder, language)
        self.checked = 0.0
        self.table: "Table | None" = None


_state = _LangState()                       # the settings' game folder (a reading with no game named)
_states: dict[str, _LangState] = {}         # each other game folder: several copies can run at once
CHECK_EVERY = 30.0          # seconds between looks at lang.txt (a change needs a game restart anyway)
CACHE_MOST = 20000
# commands whose answers carry the game's (translated) words; never chat, casts or raw readings,
# nor `said`: its lines are the server's own packet text (IN op=60), English already - turning
# them "back" could only change them. `menu` rows carry the item's id too, which settles them.
COMMANDS = ("mem", "dialog", "menu")
# fields inside those answers that hold the server's own text, never put through the game's
# translation: a menu entry's "raw" (what the server sent, beside the shown "text") and the
# character sheet read from its packet
SERVER_TEXT = frozenset(("raw", "sheet"))

_HOLE = re.compile(r"\{sub\d\{\$(\d+)\}sub\d\}|\$(\d+)")
_KEY_HOLE = re.compile(r"\{[a-z]\}")
_CJK = re.compile("[㐀-鿿]")


def _blocks(path: str) -> Iterator[tuple[str, str]]:
    """(key, value) of every block of a language file, as the game reads it."""
    try:
        with open(path, "rb") as f:
            text = f.read().decode("utf-8", "replace")
    except OSError:
        return
    if text.startswith("﻿"):
        text = text[1:]
    text = re.sub(r"(\r\n){3,100}", "\r\n\r\n", text)
    for block in text.split("\r\n\r\n"):
        lines = block.split("\r\n")
        if len(lines) >= 2 and lines[0]:
            yield lines[0], lines[1]


def _folder(path: str) -> dict[str, str]:
    """{key: value} of one language folder (style.txt left out)."""
    out: dict[str, str] = {}
    for name in ("basic.txt", "mapname.txt", "terrain.txt"):
        for k, v in _blocks(os.path.join(path, name)):
            out.setdefault(k, v)
    more = os.path.join(path, "more")
    try:
        names = sorted(os.listdir(more))
    except OSError:
        names = []
    for name in names:
        full = os.path.join(more, name)
        if os.path.isfile(full):
            for k, v in _blocks(full):
                out.setdefault(k, v)
    return out


_SUB = re.compile(r"\{sub(\d)\{(.*?)\}sub\1\}", re.S)


def _shown(key: str, eng: dict[str, str]) -> str:
    """What the english folder shows for an original (Chinese or server text): its value, with any
    {subN{text}subN} part translated again as the game does; the original when it has no entry."""
    value = eng.get(key)
    if not value:
        return key
    return _SUB.sub(lambda m: eng.get(m.group(2)) or m.group(2), value)


def _key_regex(key: str) -> re.Pattern[str] | None:
    """A regex for server text as it arrives when no pack translated it: the key with its {d} (a
    number) and {s} {c} (any text) holes as groups g1, g2 ... in order."""
    parts: list[str] = []
    pos = 0
    for n, m in enumerate(_KEY_HOLE.finditer(key), 1):
        parts.append(re.escape(key[pos:m.start()]))
        parts.append("(?P<g%d>-?\\d+(?:\\.\\d+)?)" % n if m.group(0) == "{d}" else "(?P<g%d>.+?)" % n)
        pos = m.end()
    parts.append(re.escape(key[pos:]))
    try:
        return re.compile("^" + "".join(parts) + "$", re.S)
    except re.error:
        return None


def _squash(s: str) -> str:
    return " ".join(s.split()).lower()


def known_names() -> dict[str, str]:
    """{name evened out (lower case, single spaces): the name as the bot writes it} for every NPC,
    item, map, skill and fusion beast in the bot's data. Where a translation stands for several
    English originals, the one the bot knows is the one meant; and a name that comes back in
    another case or spacing ("armory", "Demon  Cave") is written as the bot writes it."""
    names: list[object] = []
    k = _data("knowledge.json")
    for part in ("npcs", "items", "maps"):
        names += _names(k.get(part))
    classes: object = _data("skills.json").get("classes")
    groups: list[object] = list(classes.values()) if jsonio.is_object(classes) else []
    for rows in groups:
        names += _names(rows)
    beasts: object = _data("beasts.json").get("beasts")
    names += list(beasts) if jsonio.is_object(beasts) else []
    out: dict[str, str] = {}
    for name in names:
        if isinstance(name, str) and name.strip():
            out.setdefault(_squash(name), name)
    return out


def _data(name: str) -> dict[str, Any]:
    """One of the bot's data files as a dict; {} when it can't be read."""
    try:
        with open(os.path.join(paths.DATA, name), encoding="utf-8") as f:
            got: object = jsonio.load(f)
    except (OSError, ValueError):
        return {}
    return got if jsonio.is_object(got) else {}


def _names(rows: object) -> list[object]:
    """The "name" of every record in a list of them."""
    return [r.get("name") for r in rows if jsonio.is_object(r)] if jsonio.is_array(rows) else []


def _english_template(key: str, eng: str | None) -> str:
    """The English form of a template entry with $1, $2 ... holes: the english folder's own value,
    else the key with its {d} {s} holes numbered in order."""
    if eng and _HOLE.search(eng):
        return eng
    n = [0]

    def number(_m: re.Match[str]) -> str:
        n[0] += 1
        return "$%d" % n[0]
    return _KEY_HOLE.sub(number, key)


class Table:
    """translated -> English (the english folder's wording), for one language."""

    def __init__(self, lang_dir: str, english_dir: str, known: dict[str, str] | None = None) -> None:
        here, eng = _folder(lang_dir), _folder(english_dir)
        self.known = known if known is not None else known_names()
        self.exact: dict[str, str] = {}
        # (regex of the translated value, English template)
        self.templates: list[tuple[re.Pattern[str], str]] = []
        # templates whose original starts with a space (" belongs to {s}")
        self.suffixes: list[tuple[re.Pattern[str], str]] = []
        english_keyed: dict[str, bool] = {}
        for key, value in here.items():
            if not value or value == key:
                continue
            original = _shown(key, eng)
            if not original or _CJK.search(original):
                continue
            if _KEY_HOLE.search(key) or _HOLE.search(value):
                literal = _HOLE.sub("", value).strip()
                if len(literal) < 3:
                    continue        # "$1" alone, "$1 $2": would match anything
                rx = self._regex(value)
                if rx is None:
                    continue
                entry = (rx, _english_template(key, eng.get(key)))
                (self.suffixes if key.startswith(" ") else self.templates).append(entry)
                continue
            # one translation for several originals: a name the bot knows wins, then an English key
            # (what the server sends)
            original = self.canon(original)
            was = self.exact.get(value)
            mine, theirs = self.is_known(original), was is not None and self.is_known(was)
            if was is None or (mine and not theirs) or (
                    mine == theirs and not _CJK.search(key) and not english_keyed.get(value)):
                self.exact[value] = original
                english_keyed[value] = not _CJK.search(key)
        self._untranslated(here, eng)
        # the most particular template first: "Level {d} Bash Skill Crystal" before "Level {d} {s}"
        def particular(entry: tuple[re.Pattern[str], str]) -> int:
            return -len(re.sub(r"\(\?P<g\d+>[^)]*\)|\(\?P=g\d+\)|\\", "", entry[0].pattern))
        self.templates.sort(key=particular)
        self.suffixes.sort(key=particular)
        # the same text in another case: only where one entry fits ("" marks two that differ)
        self.folded: dict[str, str] = {}
        for value, original in self.exact.items():
            k = _squash(value)
            self.folded[k] = original if self.folded.get(k, original) == original else ""
        self.cache: dict[str, str] = {}

    def is_known(self, name: str) -> bool:
        return _squash(name) in self.known

    def canon(self, name: str) -> str:
        """A name as the bot writes it, when it differs only in case or spacing."""
        return self.known.get(_squash(name), name)

    def _untranslated(self, here: dict[str, str], eng: dict[str, str]) -> None:
        """Server text this pack shows as it arrives (no entry, or one that keeps it) but the
        english folder rewords: into the english folder's wording. Never a text that is itself
        what the english folder shows for something else, nor one this pack's own entries give."""
        shown = set(eng.values())
        for key in eng:
            if _CJK.search(key) or (here.get(key) or key) != key:
                continue
            target = _shown(key, eng) if not _KEY_HOLE.search(key) else None
            if target is not None:
                if target != key and key not in shown and key not in self.exact:
                    self.exact[key] = self.canon(target)
                continue
            value = eng.get(key) or ""
            if not _HOLE.search(value) or len(_KEY_HOLE.sub("", key).strip()) < 3:
                continue            # "{s}" alone would match anything
            rx = _key_regex(key)
            if rx is not None and _english_template(key, value) != _english_template(key, None):
                entry = (rx, _english_template(key, value))
                (self.suffixes if key.startswith(" ") else self.templates).append(entry)

    @staticmethod
    def _regex(value: str) -> re.Pattern[str] | None:
        parts: list[str] = []
        pos = 0
        seen: set[str] = set()
        for m in _HOLE.finditer(value):
            parts.append(re.escape(value[pos:m.start()]))
            n = m.group(1) or m.group(2)
            if n in seen:
                parts.append("(?P=g%s)" % n)
            else:
                seen.add(n)
                parts.append("(?P<g%s>.+?)" % n)
            pos = m.end()
        parts.append(re.escape(value[pos:]))
        try:
            return re.compile("^" + "".join(parts) + "$", re.S)
        except re.error:
            return None

    def _fill(self, template: str, m: re.Match[str], words: bool = True) -> str:
        """The English template with the holes filled; words=False keeps what filled them as it
        is (a player's name)."""
        def put(h: re.Match[str]) -> str:
            n = h.group(1) or h.group(2)
            got = m.groupdict().get("g" + n)
            if got is None:
                return h.group(0)
            return self.word(got) if words else got
        return _HOLE.sub(put, template)

    def word(self, s: str) -> str:
        """One piece of text back in English (unchanged when the table doesn't know it)."""
        if not s:
            return s
        hit = self.exact.get(s)
        if hit is not None:
            return hit
        folded = self.folded.get(_squash(s))
        if folded:
            return folded
        got = self.cache.get(s)
        if got is not None:
            return got
        got = self.canon(self._work(s))
        if len(self.cache) > CACHE_MOST:
            self.cache.clear()
        self.cache[s] = got
        return got

    def _work(self, s: str) -> str:
        for rx, eng in self.templates:
            m = rx.match(s)
            if m:
                return self._fill(eng, m)
        # a name joined from parts: the longest known first part, then the rest
        cuts = [i for i in range(len(s) - 1, 0, -1) if s[i] == " "]
        for i in cuts:
            head = self.exact.get(s[:i])
            if head is not None:
                return head + self._rest(s[i:])
        rest = self._rest(s)
        return rest

    def _rest(self, s: str) -> str:
        """What follows a name's first part: " name", and maybe the owner (" belongs to X"). The
        shortest ending that is the owner wins, at any letter: Roman Urdu puts the owner first
        (" X ka hai") and Vietnamese joins the words on without a space ("thuộc về X")."""
        for i in range(len(s) - 1, -1, -1):
            tail = s[i:]
            for rx, eng in self.suffixes:
                m = rx.match(tail)
                if m:
                    return s[:i] + self._fill(eng, m, words=False)    # the owner is a player
        return s


def _game_folder() -> str:
    return settings.load_app().get("game_folder", "")


def forget() -> None:
    """Look at lang.txt again on the next call (a language changed while the app runs)."""
    with _lock:
        _state.key, _state.checked = None, 0.0
        _states.clear()


def language_of(game: str) -> str:
    """The language a game folder is set to (language\\lang.txt), "" when it can't be read."""
    try:
        with open(os.path.join(game, "language", "lang.txt"), "rb") as f:
            return f.read().decode("utf-8", "replace").strip().strip("\ufeff").strip()
    except OSError:
        return ""


def table(game: str | None = None) -> Table | None:
    """The table for the language a game folder is set to (the settings' game folder when none is
    given), or None for English (or no game folder)."""
    now = time.time()
    with _lock:
        folder = game if game else _game_folder()
        state = _state if not game else _states.setdefault(os.path.normcase(game), _LangState())
        if now - state.checked < CHECK_EVERY:
            return state.table
        state.checked = now
        lang = language_of(folder)
        lang_dir = os.path.join(folder, "language", lang) if lang else ""
        key = (folder, lang)
        if key == state.key:
            return state.table
        state.key = key
        if not lang or lang.lower() == "english" or not os.path.isdir(lang_dir):
            state.table = None
        else:
            state.table = Table(lang_dir, os.path.join(folder, "language", "english"))
        return state.table


# The english pack files the bot's names were learned from: without them, the english pack shows
# much of the server's text unreworded, and names differ from the bot's.
ENGLISH_PACK_FILES = ("item_looted.txt", "equipment_dropped.txt", "shops.txt", "system.txt", "gang_strings.txt")


def notes(game: str) -> list[str]:
    """What to tell the player once about a game folder's language: another pack (names are turned
    into English: ids first, then words), or an english pack older than the bot's (names may
    differ). [] for the stock English."""
    from .i18n import _
    lang = language_of(game)
    out: list[str] = []
    if lang and lang.lower() != "english":
        out.append(_("the game's language is {lang}: names are turned into English by the game's ids "
                     "where it has them, else by its language files", lang=lang))
    more = os.path.join(game, "language", "english", "more")
    if os.path.isdir(more):
        missing = [f for f in ENGLISH_PACK_FILES if not os.path.isfile(os.path.join(more, f))]
        if missing:
            out.append(_("the game's english language pack is older than the one Uncle Farmer's names come "
                         "from ({files} missing): some names may not match", files=", ".join(missing)))
    return out


def english(data: Any, t: Table | None = None, game: str | None = None) -> Any:
    """Every text in an agent answer (lists and dicts inside) back in English. Any: an agent
    answer is JSON of whatever shape its command gives, and comes back in the same shape. game:
    the folder of the game that answered (each copy may be set to another language)."""
    t = t if t is not None else table(game)
    if t is None:
        return data
    if isinstance(data, str):
        return t.word(data)
    if jsonio.is_array(data):
        return _item_rows([english(x, t, game) for x in data])
    if jsonio.is_object(data):
        out = _by_id(data, {k: v if k in SERVER_TEXT else english(v, t, game) for k, v in data.items()})
        if data.get("kind") == "player" and isinstance(data.get("name"), str):
            # a player's own name stays as it is, even when it is also a word of the language; only
            # a pet's ("<kind> <name> belongs to <owner>", players to the game) is turned back
            if " belongs to " not in out["name"]:
                out["name"] = data["name"]
        return out
    return data


class EnglishWording:
    """Server text -> what the english folder shows for it ("Bone Cookie" -> "Pet Bone Cookie",
    "Level 2 Obsidian" through "Level {d} Obsidian"). Going this way is never ambiguous: the game
    does the same with the english pack chosen. Used to build the id tables from recorded packets,
    which carry the server's own wording."""

    def __init__(self, english_dir: str) -> None:
        self.eng = _folder(english_dir)
        self.templates: list[tuple[re.Pattern[str], str]] = []
        for key, value in self.eng.items():
            if _CJK.search(key) or not _KEY_HOLE.search(key) or not value:
                continue
            if len(_KEY_HOLE.sub("", key).strip()) < 3:
                continue
            rx = _key_regex(key)
            if rx is not None:
                self.templates.append((rx, value))
        self.templates.sort(key=lambda e: -len(re.sub(r"\(\?P<g\d+>[^)]*\)|\\", "", e[0].pattern)))

    def shown(self, raw: str) -> str:
        if not raw:
            return raw
        if raw in self.eng:
            return _shown(raw, self.eng)
        for rx, value in self.templates:
            m = rx.match(raw)
            if m:
                def put(h: re.Match[str]) -> str:
                    got = m.groupdict().get("g" + (h.group(1) or h.group(2)))
                    if got is None:
                        return h.group(0)
                    return self.shown(got) if h.group(0).startswith("{sub") else got
                return _HOLE.sub(put, value)
        return raw


IDS_FILE = "ids.json"
NPC_NEAR = 3.0          # tiles: an NPC with a known id counts as that NPC only this close to its place


class _Ids:
    """The game's ids for what the bot names (data/ids.json): items by type, skills by id, NPCs by
    map and id. Read once."""

    def __init__(self) -> None:
        raw = _data(IDS_FILE)
        self.items: dict[int, str] = _by_number(raw.get("items"))
        self.by_name: dict[str, int | None] | None = None     # English name -> item id, made when asked
        self.skills: dict[int, str] = _by_number(raw.get("skills"))
        # (map, id) -> (name, x, y): an NPC's id is the order its map lists NPCs in, and shifts when
        # a game update adds some - so it counts only for an NPC standing where that one stands
        self.npcs: dict[tuple[int, int], tuple[str, float, float]] = {}
        rows: object = raw.get("npcs")
        for r in (rows if jsonio.is_array(rows) else []):
            if jsonio.is_array(r) and len(r) >= 5 and isinstance(r[0], int) and isinstance(r[1], int)                     and isinstance(r[2], str) and isinstance(r[3], (int, float)) and isinstance(r[4], (int, float)):
                self.npcs[(r[0], r[1])] = (r[2], float(r[3]), float(r[4]))


def _by_number(part: object) -> dict[int, str]:
    """{"23386": "Medium Red Potion"} as {23386: "Medium Red Potion"}; anything else left out."""
    out: dict[int, str] = {}
    if jsonio.is_object(part):
        for k, v in part.items():
            if isinstance(v, str) and str(k).lstrip("-").isdigit():
                out[int(k)] = v
    return out


_ids: _Ids | None = None


def ids() -> _Ids:
    global _ids
    if _ids is None:
        _ids = _Ids()
    return _ids


def _by_id(data: Any, out: Any) -> Any:
    """Names the game's ids settle, put over what the words gave: NPCs (map and id), bag and
    equipment rows (item type), skill, talent and profession rows (skill id). An id the table
    doesn't hold keeps the translated name."""
    if not jsonio.is_object(data) or not jsonio.is_object(out):
        return out
    table = ids()
    entities: object = data.get("entities")
    where: object = data.get("map")
    mid = where.get("id") if jsonio.is_object(where) else None
    if jsonio.is_array(entities) and isinstance(mid, int) and jsonio.is_array(out.get("entities")):
        for e, o in zip(entities, out["entities"]):
            if jsonio.is_object(e) and jsonio.is_object(o) and e.get("kind") == "npc" and isinstance(e.get("id"), int):
                known = table.npcs.get((mid, e["id"]))
                x, y = e.get("x"), e.get("y")
                if known and isinstance(x, (int, float)) and isinstance(y, (int, float))                         and abs(known[1] - x) <= NPC_NEAR and abs(known[2] - y) <= NPC_NEAR:
                    o["name"] = known[0]
    for part, by in (("combat", table.skills), ("talents", table.skills), ("profession", table.skills)):
        rows: object = out.get(part)
        if jsonio.is_array(rows):
            for r in rows:
                if jsonio.is_object(r) and isinstance(r.get("id"), int) and r["id"] in by:
                    r["name"] = by[r["id"]]
    return out


def _item_rows(out: Any) -> Any:
    """Bag and equipment rows (`mem bagfull`, `mem equipment`: uid, type, name) and shop and market
    rows (`menu`: row, id, name, price): the item's English name from its type."""
    if jsonio.is_array(out):
        table = ids().items
        for r in out:
            if not jsonio.is_object(r):
                continue
            kind = r.get("type") if "uid" in r else r.get("id") if "row" in r and "price" in r else None
            if isinstance(kind, int) and kind in table:
                r["name"] = table[kind]
    return out


def item_type(name: str | None) -> int | None:
    """The game's id for an item the bot names, from the id table; None when unknown, or when two
    items go by that name."""
    if not name:
        return None
    table = ids()
    if table.by_name is None:
        found: dict[str, int | None] = {}
        for k, v in table.items.items():
            n = _squash(v)
            found[n] = k if n not in found else None
        table.by_name = found
    return table.by_name.get(_squash(name))


def warm() -> None:
    """Build the table in the background at start (it reads a few thousand lines: about 2 s), so
    the first reading of the game doesn't wait for it."""
    threading.Thread(target=table, daemon=True).start()
