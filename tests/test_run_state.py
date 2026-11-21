"""Run state created in one place (master plan, Phase 5): the travel and supply notes a run keeps are
made in Bot.__init__, and a reader that still passes getattr a default passes that same value, so a
note that is missing (a stand-in bot in a test) and one that was just made mean the same thing."""
from __future__ import annotations

import ast
import glob
import os
import re

ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "unclefarmer", "engine")
GROUPS = ("traveling", "on_farm_run", "farmed", "crossing_poison", "warned_poison", "ways_out", "rides_too_high",
          "restocking", "restock_looked", "restock_town", "restock_failed", "restock_skipped", "storehouse_lacks",
          "supply_told", "potion_slots")      # (bag_start: BagMixin's class default)
MADE = re.compile(r"^        self\.(\w+) = ([^#\n]+?)\s*(?:#.*)?$", re.M)
READ = re.compile(r'getattr\((?:self|b|bot|self\.bot), "(\w+)", ([^()]*\(\)|[^()]*?)\)')
EMPTY_SET = re.compile(r"set(\[\w+\])?\(\)")


def literal(text: str) -> object:
    return set[object]() if EMPTY_SET.fullmatch(text) else ast.literal_eval(text)


def made_in_init() -> dict[str, object]:
    with open(os.path.join(ROOT, "bot.py"), encoding="utf-8") as f:
        src = f.read()
    return {name: literal(value.strip()) for name, value in MADE.findall(src) if name in GROUPS}


def test_every_travel_and_supply_note_is_made_in_bot_init() -> None:
    made = made_in_init()
    assert set(GROUPS) <= set(made), sorted(set(GROUPS) - set(made))


def test_every_getattr_default_of_those_notes_is_the_one_bot_init_makes() -> None:
    made = made_in_init()
    wrong: list[str] = []
    for path in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for m in READ.finditer(src):
            name, text = m.group(1), m.group(2).strip()
            if name in made and literal(text) != made[name]:
                wrong.append("%s:%d %s=%s, Bot.__init__ makes %r" % (os.path.relpath(path, ROOT),
                                                                    src[:m.start()].count("\n") + 1, name, text, made[name]))
    assert not wrong, "\n".join(wrong)
