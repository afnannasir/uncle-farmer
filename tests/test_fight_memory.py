"""Every note the bot keeps by monster id is forgotten on a map change (master plan, Phase 5): ids are
places in one map's list, so a note kept across maps holds something against a different monster.
The notes are listed once (FightingMixin.MAP_NOTES / DEATH_NOTES, SkillBook.MAP_NOTES); a new
id-keyed note must join a list or say why it is kept (KEPT_NOTES)."""
from __future__ import annotations

import inspect
import re

from unclefarmer.engine import combat
from unclefarmer.engine.botparts import parts
from unclefarmer.engine.botparts.fighting import FightingMixin

ID_KEYED = re.compile(r"^(dict\[int|set\[int\]|dict\[tuple\[str, int)")


def test_every_id_keyed_note_of_the_bot_is_forgotten_or_kept_on_purpose() -> None:
    keyed = {name for name, hint in parts.Parts.__annotations__.items() if ID_KEYED.match(str(hint))}
    assert {"skip", "mob_names", "unselectable", "walk_failed"} <= keyed
    left = keyed - set(FightingMixin.MAP_NOTES) - set(FightingMixin.KEPT_NOTES)
    assert not left, "add to FightingMixin.MAP_NOTES (or KEPT_NOTES with a reason): %s" % sorted(left)
    assert set(FightingMixin.DEATH_NOTES) <= set(FightingMixin.MAP_NOTES)


def test_every_id_keyed_note_of_the_skill_book_is_forgotten() -> None:
    source = inspect.getsource(combat.SkillBook.__init__)
    keyed = set(re.findall(r"self\.(\w+): (?:dict\[int|dict\[tuple\[str, int)", source))
    assert {"downed", "swings", "frozen", "landed"} <= keyed
    assert keyed <= set(combat.SkillBook.MAP_NOTES), sorted(keyed - set(combat.SkillBook.MAP_NOTES))
