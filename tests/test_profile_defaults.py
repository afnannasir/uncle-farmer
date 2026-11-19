"""Profile defaults live in settings.PROFILE_DEFAULTS only (master plan, Phase 5). A profile is loaded
through settings' model, so every key is there; an inline default on a read is only what a stand-in
profile in a test sees, and one that disagrees with settings misleads whoever reads the code (three
did: level_keep_xp 0 against 100000, attack_gap 0.2 against 0.35, respawn "map" against "city")."""
from __future__ import annotations

import ast
import glob
import os
import re

from unclefarmer import settings

# reads of the character's profile (a plan spot's own dict in gui/plan.py is not one)
READ = re.compile(r"""\b(?:self\.p|self\.b\.p|b\.p|bot\.p)\.get\(\s*["']([a-z_0-9]+)["']\s*,\s*([^()\n]+?)\)""")
ROOT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "unclefarmer")


def test_every_inline_default_of_a_profile_read_is_the_settings_one() -> None:
    wrong: list[str] = []
    seen = 0
    for path in glob.glob(os.path.join(ROOT, "**", "*.py"), recursive=True):
        with open(path, encoding="utf-8") as f:
            src = f.read()
        for m in READ.finditer(src):
            key, text = m.group(1), m.group(2).strip()
            try:
                value = ast.literal_eval(text)
            except (ValueError, SyntaxError):
                continue          # a name or an expression: not a literal to compare
            seen += 1
            if key not in settings.PROFILE_DEFAULTS:
                continue          # run state or a tool's own dict read the same way
            want = settings.PROFILE_DEFAULTS[key]
            same = want == value or (isinstance(want, (int, float)) and isinstance(value, (int, float))
                                     and not isinstance(want, bool) and float(want) == float(value))
            if not same:
                line = src[:m.start()].count("\n") + 1
                wrong.append("%s:%d %s=%r, settings say %r" % (os.path.relpath(path, ROOT), line, key, value, want))
    assert seen > 100                 # the pattern still finds the reads
    assert not wrong, "\n".join(wrong)
