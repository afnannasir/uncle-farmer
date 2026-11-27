"""Fold players' profiles/_learned_ids.json files into data/ids.json.

    python -m unclefarmer --merge-ids <file> [<file> ...]

Only what was seen in an English game is taken (a name turned back from another language may be
wrong), and, when the game folder is known, only a name the game's own english texts hold. A number
the table already has keeps its name: a different one is listed, never put in its place. A skill
gets its class when the files agree on one. Talents are not taken yet, only checked: whether one
talent has several numbers (numbers each character's own) or one number several names.
"""

from __future__ import annotations

import os
import re
from typing import Any

from . import gameids, gamelang, jsonio, learnids, paths

TAKEN = ("items", "monsters", "skills", "professions")
# a template of the game's texts ("Level {d} Ruby Shard", "$1x experience acceleration card"): what it
# stands for
# (in re.escape()d text: "\{sub2\{\$1\}sub2\}", "\{d\}", "\{s\}", "\$1")
_HOLES = re.compile(r"\\\{sub\d\\\{\\\$\d+\\\}sub\d\\\}|\\\{[dsc]\\\}|\\\$\d+")


class Known:
    """The names the game's english texts hold: plain ones, and the ones its templates make."""

    def __init__(self, texts: set[str]) -> None:
        self.plain = texts
        made = [_HOLES.sub(lambda m: r"\d+" if m.group(0) == r"\{d\}" else ".+", re.escape(t))
                for t in texts if "{" in t or "$" in t]
        self.made = re.compile("|".join("(?:%s)" % m for m in made)) if made else None

    def __bool__(self) -> bool:
        return bool(self.plain)

    def __contains__(self, name: str) -> bool:
        return name in self.plain or bool(self.made and self.made.fullmatch(name))


def _combined(files: list[str], report: list[str]) -> dict[str, Any]:
    combined: dict[str, Any] = {s: {} for s in learnids.SECTIONS}
    for path in files:
        try:
            got = jsonio.read(path)
        except (OSError, ValueError) as e:
            report.append("skipped %s: %s" % (path, e))
            continue
        if not jsonio.is_object(got) or got.get("format") != learnids.FORMAT:
            report.append("skipped %s: not a learned ids file of format %d" % (path, learnids.FORMAT))
            continue
        one: dict[str, Any] = {s: {} for s in learnids.SECTIONS}
        try:                            # a file broken inside: skipped whole, the others go on
            for s in learnids.SECTIONS:
                if jsonio.is_object(got.get(s)):
                    learnids.merge_into(one, s, got[s])
        except (AttributeError, KeyError, TypeError, ValueError) as e:
            report.append("skipped %s: %s" % (path, e))
            continue
        for s in learnids.SECTIONS:
            learnids.merge_into(combined, s, one[s])
    return combined


def _take(section: str, table: dict[str, Any], key: str, entry: dict[str, Any], texts: Known,
          report: list[str]) -> bool:
    """One number into the table; True when it is new there."""
    name = gameids.english_name(entry)
    if not key.isdigit() or name is None:
        return False                    # seen only in another language: not trusted
    english: dict[str, int] = entry["names"]["english"]
    if len(english) > 1:
        report.append("%s %s: seen as %s; %r taken" % (section, key, ", ".join(sorted(english)), name))
    if texts and name.strip().casefold() not in texts:
        report.append("%s %s: %r isn't in the game's english texts - not taken" % (section, key, name))
        return False
    old = table.get(key)
    added = not jsonio.is_object(old)
    if added:
        old = table[key] = {"name": name}
    elif old.get("name") != name:
        report.append("%s %s: the table says %r, a file says %r - kept %r" % (
            section, key, old.get("name"), name, old.get("name")))
    if section == "items" and isinstance(entry.get("about"), str) and not old.get("about"):
        old["about"] = entry["about"]
    if section == "skills":
        seen_on: list[Any] = entry.get("classes") or []
        classes = [c for c in seen_on if isinstance(c, str)]
        if len(classes) == 1 and not old.get("class"):
            old["class"] = classes[0]
        elif len(classes) > 1:
            report.append("skills %s: seen on %s" % (key, ", ".join(classes)))
    return added


def _talents(talents: dict[str, Any], report: list[str]) -> None:
    for cls, entries in sorted(talents.items()):
        ids_of: dict[str, set[str]] = {}
        for key, entry in entries.items():
            if not key.isdigit():
                continue
            every: dict[str, dict[str, int]] = entry.get("names") or {}
            names = every.get("english") or {}
            if len(names) > 1:
                report.append("talents %s %s: seen as %s" % (cls, key, ", ".join(sorted(names))))
            for n in names:
                ids_of.setdefault(n, set()).add(key)
        for n, ids in sorted(ids_of.items()):
            if len(ids) > 1:
                report.append("talents %s %r: numbers %s (each character's own?)" % (
                    cls, n, ", ".join(sorted(ids, key=int))))


def merge(files: list[str], game: str = "", target: str = "") -> list[str]:
    """Fold the files in; returns the report, its first line the count of what was added."""
    target = target or os.path.join(paths.DATA, gameids.FILE)
    report: list[str] = []
    base = jsonio.read(target)
    if not jsonio.is_object(base):
        raise ValueError("%s isn't a table" % target)
    texts = Known(gamelang.english_texts(game) if game else set[str]())
    if game and not texts:
        report.append("the game's english texts weren't found in %s: names not checked" % game)
    combined = _combined(files, report)
    added: dict[str, int] = {}
    for s in TAKEN:
        table: dict[str, Any] = base.setdefault(s, {})
        added[s] = sum(_take(s, table, key, entry, texts, report) for key, entry in sorted(combined[s].items()))
        base[s] = dict(sorted(table.items(), key=lambda kv: int(kv[0]) if kv[0].isdigit() else -1))
    _talents(combined["talents"], report)
    jsonio.save(target, base)
    gameids.forget()
    return ["added: " + ", ".join("%d %s" % (added[s], s) for s in TAKEN)] + report
