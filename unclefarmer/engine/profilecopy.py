"""Copying a part of one character's settings to another, from the character window: its Before or
After farming list, its fighting settings, its team settings (the bag's parts are engine/bagcopy.py).
Setting the same lists and choices again by hand for each character was a lot of work.

Plain data in, plain data out: nothing is saved here.
"""

from __future__ import annotations

import copy
from typing import Any

from . import triggers

# The fighting settings: the character window's fighting boxes, Advanced settings' fine tuning and
# each skill's use - the ones "Reset the fighting settings" puts back too. Never the keys: they are
# the character's own bar.
FIGHTING_KEYS = ("attack_style", "fight_back", "nearest_monster", "fight_locked", "shoot_while_walking",
                 "skills_by_slot", "use_buffs", "use_dispel", "use_combos", "fill_empty_keys", "skill_page_one",
                 "attack_range", "attack_gap", "pull_range", "fight_timeout", "no_damage_timeout",
                 "no_damage_casts", "press_burst", "chase_range", "target_mode", "smart_skills",
                 "points_every", "mp_check_every", "skill_use", "skill_use_others")
SKILL_KEYS = ("skill_use", "skill_use_others")      # named by skill: only for the same class
TEAM_KEYS = ("team_accept", "team_accept_who", "team_accept_list", "team_remove_offline", "team_offline_every",
             "team_offline_town_only", "team_hand_over", "team_hand_to", "team_create", "team_name",
             "team_remake", "team_leave_others", "team_stay_in_town", "team_heal", "team_heal_below",
             "follow_role", "follow_pull", "follow_pull_range", "follow_help_team", "follow_distance", "follow_skip")


def class_of(p: dict[str, Any]) -> str:
    """The character's class as its last scan read it ("" when never scanned)."""
    scan: dict[str, Any] = p.get("scan") or {}
    return str(scan.get("class") or "")


def same_class(mine: dict[str, Any], theirs: dict[str, Any]) -> bool:
    """Both of one class (or one of them never scanned: nothing says they differ)."""
    a, b = class_of(mine), class_of(theirs)
    return not a or not b or a == b


def fighting(theirs: dict[str, Any], with_skills: bool) -> dict[str, Any]:
    """The other character's fighting settings to set here. with_skills: also each skill's use
    (only between characters of one class: the skills are named)."""
    keys = [k for k in FIGHTING_KEYS if with_skills or k not in SKILL_KEYS]
    return {k: copy.deepcopy(theirs[k]) for k in keys if k in theirs}


def team(theirs: dict[str, Any]) -> dict[str, Any]:
    """The other character's team settings to set here."""
    return {k: copy.deepcopy(theirs[k]) for k in TEAM_KEYS if k in theirs}


def steps(mine: dict[str, Any], theirs: dict[str, Any], stage: str,
          replace: bool) -> tuple[list[dict[str, Any]], int, int]:
    """(this character's list for the stage after the copy, steps put in, steps already there).
    Adding: each of the other's ticked steps is ticked here too - in its place when this list has
    that action (a list never edited holds every job, unticked), else after this one's steps; the
    other's unticked steps change nothing. Counted: the enabled steps."""
    theirs_steps = [dict(s) for s in triggers.sequence(theirs, stage)["steps"]]
    if replace:
        return theirs_steps, sum(1 for s in theirs_steps if s.get("enabled")), 0
    out = [dict(s) for s in triggers.sequence(mine, stage)["steps"]]
    added = same = 0
    for s in theirs_steps:
        if not s.get("enabled"):
            continue
        at = next((i for i, m in enumerate(out) if m["action"] == s["action"]), None)
        if at is None:
            out.append(s)
            added += 1
        elif out[at].get("enabled"):
            same += 1
        else:
            out[at] = s
            added += 1
    return out, added, same
