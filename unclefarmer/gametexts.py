"""The game's own texts: every menu label, instruction page and mission text the game has words for.

Where they come from, all in the player's own game folder:
  - version/language.gz: plain gzip, a section per language ("russian/basic.txt"...). The Russian
    section pairs each English text the server can send with its translation, so it carries the
    text of menus, NPC instruction pages and missions that no English file on disk has.
  - language/<language>/: the translation folders; their English source lines add more.
  - language/english/: the English texts themselves.

Built once from the game folder into data/game_texts.json (not shipped: it is the game's own
text, so each player's copy is built from their own game), and rebuilt when the game updates.

    python -m unclefarmer.gametexts [game folder]      build it now
    python -m unclefarmer.gametexts --find <regex>     search it
"""

from __future__ import annotations

import gzip
import os
import re
import sys
import threading
from collections.abc import Iterator
from typing import Any

from . import jsonio as json, paths, settings

OUT = os.path.join(paths.DATA, "game_texts.json")
_FOREIGN = re.compile("[Ѐ-ӿ一-鿿぀-ヿ가-힯]")   # Cyrillic, CJK, kana, Hangul
_ENGLISH = re.compile(r"\b(the|you|your|to|of|and|is|can|will|with|for|are|this|level|please|enter)\b", re.I)
_NOT_ENGLISH = re.compile(r"\b(de|que|para|com|não|est|bir|ve|và|của|aap|hai|ke|ki|ko|mein|se|el|la|los|"
                          r"las|una|un|por|ele|ela)\b", re.I)
# Language folders whose files pair English source lines with a translation.
PAIRED = ("russian", "Russian beta", "en_uk", "public english translation", "turkish", "vietnamese", "chinese", "urdu")

_cache: dict[str, Any] | None = None     # {"stamp", "texts"} as game_texts.json holds them
_lock = threading.Lock()


def _english(line: str) -> bool:
    line = line.strip()
    if not line or _FOREIGN.search(line):
        return False
    if len(line) < 25:                       # short labels: keep only plain ASCII ones
        return line.isascii()
    return len(_ENGLISH.findall(line)) >= 2 and not _NOT_ENGLISH.search(line)


def _stamp(game: str) -> int:
    try:
        return int(os.path.getmtime(os.path.join(game, "version", "language.gz")))
    except OSError:
        return 0


def build(game: str) -> int:
    """Read the game folder and write data/game_texts.json. Returns the number of texts."""
    texts: list[str] = []
    seen: set[str] = set()

    def add(line: str) -> None:
        line = line.strip()
        if line and line not in seen:
            seen.add(line)
            texts.append(line)

    for line in _txt_lines(os.path.join(game, "language", "english")):
        if line.strip() and not _FOREIGN.search(line):
            add(line)
    for line in _packed_lines(game):
        if _english(line) and not re.match(r"^[\w ]+/[\w .()-]+\.txt$", line):
            add(line)
    for lang in PAIRED:
        for line in _txt_lines(os.path.join(game, "language", lang)):
            if _english(line):
                add(line)
    os.makedirs(paths.DATA, exist_ok=True)
    with open(OUT, "w", encoding="utf-8") as f:
        json.dump({"stamp": _stamp(game), "texts": texts}, f, ensure_ascii=False, indent=0)
    return len(texts)


def _txt_lines(folder: str) -> Iterator[str]:
    """Every line of every .txt file under folder, file by file in name order."""
    for root, _d, files in os.walk(folder):
        for name in sorted(files):
            if name.endswith(".txt"):
                with open(os.path.join(root, name), encoding="utf-8", errors="replace") as f:
                    yield from f.read().replace("\r", "").split("\n")


def _packed_lines(game: str) -> list[str]:
    """The lines of version/language.gz (none when it can't be read)."""
    try:
        with open(os.path.join(game, "version", "language.gz"), "rb") as f:
            raw = gzip.decompress(f.read()).decode("utf-8", "replace")
    except (OSError, EOFError):
        return []
    return raw.replace("\r", "").split("\n")


def texts(game: str | None = None) -> list[str]:
    """Every English text of the game. Built (or rebuilt after a game update) when needed."""
    global _cache
    with _lock:
        if game is None:
            game = settings.load_app().get("game_folder", "")
        if _cache is None or (game and _cache.get("stamp") != _stamp(game) and _stamp(game)):
            data: dict[str, Any] | None = None
            if os.path.exists(OUT):
                try:
                    with open(OUT, encoding="utf-8") as f:
                        data = json.load(f)
                except (OSError, ValueError):
                    data = None
            if (data is None or (game and _stamp(game) and data.get("stamp") != _stamp(game))) \
                    and game and os.path.isdir(game):
                build(game)
                with open(OUT, encoding="utf-8") as f:
                    data = json.load(f)
            _cache = data or {"stamp": 0, "texts": []}
        found: list[str] = _cache["texts"]
        return found


def find(pattern: str, game: str | None = None) -> list[str]:
    """The game's texts matching a regex (any case)."""
    rx = re.compile(pattern, re.I)
    return [t for t in texts(game) if rx.search(t)]


def has(text: str, game: str | None = None) -> bool:
    """True if the game has this exact text (a menu label, say)."""
    return text.strip() in set(texts(game))


def main(argv: list[str]) -> None:
    if argv[:1] == ["--find"] and len(argv) > 1:
        for t in find(" ".join(argv[1:])):
            print(t)
        return
    game = argv[0] if argv else settings.load_app().get("game_folder", "")
    if not os.path.isdir(game):
        sys.exit("give the Mist World folder, or set it in Settings first")
    print("%d texts written to %s" % (build(game), OUT))


if __name__ == "__main__":
    main(sys.argv[1:])
