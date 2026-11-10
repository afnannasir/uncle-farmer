"""The game in another language: turn its translated text back into English for the bot.

The game translates on the client (game.Trans): names of monsters, items, pets and skills, and
every NPC menu line, are put through the chosen language (language\\lang.txt) as they arrive. The
bot matches English everywhere - "Red Potion", skill names, the respawn menu, cable cars - so
with any other language pack it would find no potions and no menus. Instead of teaching every rule
every language, the agent's answers are turned back into English here, once, where they arrive
(link.AgentLink.json). With English chosen nothing is done at all.

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

from . import jsonio, settings

_lock = threading.Lock()


class _LangState:
    """Which language the table is for, when lang.txt was last looked at, and the table."""

    def __init__(self) -> None:
        self.key: tuple[str, str] | None = None      # (game folder, language)
        self.checked = 0.0
        self.table: "Table | None" = None


_state = _LangState()
CHECK_EVERY = 30.0          # seconds between looks at lang.txt (a change needs a game restart anyway)
CACHE_MOST = 20000
# commands whose answers carry the game's (translated) words; never chat, casts or raw readings
COMMANDS = ("mem", "dialog", "said")

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
    """translated -> English, for one language."""

    def __init__(self, lang_dir: str, english_dir: str) -> None:
        here, eng = _folder(lang_dir), _folder(english_dir)
        self.exact: dict[str, str] = {}
        # (regex of the translated value, English template)
        self.templates: list[tuple[re.Pattern[str], str]] = []
        # templates whose original starts with a space (" belongs to {s}")
        self.suffixes: list[tuple[re.Pattern[str], str]] = []
        english_keyed: dict[str, bool] = {}
        for key, value in here.items():
            if not value or value == key:
                continue
            original = eng.get(key) if _CJK.search(key) else key
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
            # one translation for several originals: an English key (what the server sends) wins
            if value not in self.exact or (not _CJK.search(key) and not english_keyed.get(value)):
                self.exact[value] = original
                english_keyed[value] = not _CJK.search(key)
        self.cache: dict[str, str] = {}

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
        got = self.cache.get(s)
        if got is not None:
            return got
        got = self._work(s)
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


def table() -> Table | None:
    """The table for the language the game is set to, or None for English (or no game folder)."""
    now = time.time()
    with _lock:
        if now - _state.checked < CHECK_EVERY:
            return _state.table
        _state.checked = now
        game = _game_folder()
        lang = ""
        try:
            with open(os.path.join(game, "language", "lang.txt"), "rb") as f:
                lang = f.read().decode("utf-8", "replace").strip().strip("﻿").strip()
        except OSError:
            pass
        lang_dir = os.path.join(game, "language", lang) if lang else ""
        key = (game, lang)
        if key == _state.key:
            return _state.table
        _state.key = key
        if not lang or lang.lower() == "english" or not os.path.isdir(lang_dir):
            _state.table = None
        else:
            _state.table = Table(lang_dir, os.path.join(game, "language", "english"))
        return _state.table


def english(data: Any, t: Table | None = None) -> Any:
    """Every text in an agent answer (lists and dicts inside) back in English. Any: an agent
    answer is JSON of whatever shape its command gives, and comes back in the same shape."""
    t = t if t is not None else table()
    if t is None:
        return data
    if isinstance(data, str):
        return t.word(data)
    if jsonio.is_array(data):
        return [english(x, t) for x in data]
    if jsonio.is_object(data):
        out = {k: english(v, t) for k, v in data.items()}
        if data.get("kind") == "player" and isinstance(data.get("name"), str):
            # a player's own name stays as it is, even when it is also a word of the language; only
            # a pet's ("<kind> <name> belongs to <owner>", players to the game) is turned back
            if " belongs to " not in out["name"]:
                out["name"] = data["name"]
        return out
    return data


def warm() -> None:
    """Build the table in the background at start (it reads a few thousand lines: about 2 s), so
    the first reading of the game doesn't wait for it."""
    threading.Thread(target=table, daemon=True).start()
