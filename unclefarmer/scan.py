"""Read a character's whole setup from the game and keep it in the character's profile.

Every character is scanned automatically and its profile filled in
from what the game says - skills and the keys they sit on, potions on hotkeys, fusion beasts, pets,
gear, the game's own keymap - and the profile window offers choices built from what the character
really has. Nothing here presses a key: it only reads the agent's `mem` sections.

profile["scan"] holds the last scan; apply() turns it into settings without overwriting the
player's own choices (a beast or pet they picked stays picked while they still own it).
"""

from __future__ import annotations

import time
from typing import Any

from . import gamekeys, jsonio, phrases, settings
from .engine import beasts, combat, combos, gathering
from .link import JsonLink

Row = dict[str, Any]            # one row of an agent `mem` list

RESCAN_AFTER = 30 * 60      # the main window rescans a logged-in character this often


def _json(link: JsonLink, line: str) -> Any:
    """The agent's answer as JSON (whatever shape that section has), or None when it failed."""
    try:
        v = link.json(line)
    except (OSError, ValueError):
        return None
    return None if jsonio.is_object(v) and "error" in v else v


def scan(link: JsonLink, game_folder: str="") -> dict[str, Any]:
    """Everything we can read about the logged-in character, as plain data."""
    snap: dict[str, Any] = _json(link, "mem") or {}
    me: dict[str, Any] = snap.get("self") or {}
    sheet: dict[str, Any] = me.get("sheet") or {}
    out: dict[str, Any] = {"when": time.strftime("%Y-%m-%d %H:%M"), "name": me.get("name"),
                           "maxhp": me.get("maxhp"), "level": sheet.get("level")}

    binds = gamekeys.read(game_folder)
    out["keys"] = gamekeys.profile_keys(binds) if binds else {}
    attack_keys = gamekeys.attack_keys(binds) if binds else []
    slot_keys = gamekeys.slot_keys(binds) if binds else []

    # skills, the class, and which keyboard key holds which skill
    sk: dict[str, Any] = _json(link, "mem skills") or {}
    learned_rows: list[Row] = sk.get("combat", [])
    names: dict[int, str] = {r["id"]: r["name"] for r in learned_rows}
    know = combat.knowledge()
    learned = set(names.values())
    cls = max(know, key=lambda c: len(learned & {s["name"] for s in know[c]}), default=None) if learned else None
    out["class"] = cls
    out["skills"] = sorted(learned)
    on_keys: list[dict[str, Any]] = []
    keyed: dict[str, int] = sk.get("attack_keys") or {}
    for idx, sid in sorted(keyed.items(), key=lambda kv: int(kv[0])):
        i = int(idx)
        page, n = divmod(i - 1, 10)
        key = attack_keys[n] if n < len(attack_keys) and attack_keys[n] else combat.DEFAULT_KEYS[n]
        on_keys.append({"slot": i, "page": page + 1, "key": key, "skill": names.get(sid, str(sid))})
    out["skill_keys"] = on_keys
    talents: list[Row] = sk.get("talents", [])
    out["talents"] = [r["name"] for r in talents]
    ranks: dict[str, Any] = sk.get("profession_ranks") or {}     # agent 49; an older one leaves the rank out
    profession: list[Row] = sk.get("profession", [])
    out["profession"] = [{"name": r["name"], "rank": ranks.get(str(r.get("id")))}
                         for r in profession if r.get("name")]
    active = combos.active_talents(sk)
    out["active_talents"] = sorted(active)
    out["combos"] = [combos.describe(r) for r in combos.rules_for(active, {x["skill"] for x in on_keys})]

    # hotkey slots (W_USE1..12) and what is in them
    bag: list[Row] = _json(link, "mem bagfull") or []
    slots: dict[str, Any] = _json(link, "mem slots") or {}
    by_type: dict[object, Row] = {}
    for r in bag:
        if r.get("count"):
            by_type.setdefault(r.get("type"), r)
    by_uid: dict[object, Row] = {r["uid"]: r for r in bag}
    hot: list[dict[str, Any]] = []
    for idx, v in sorted(slots.items(), key=lambda kv: int(kv[0])):
        if not jsonio.is_array(v) or len(v) < 2 or not v[0]:
            continue
        r = by_type.get(v[0]) or by_uid.get(v[1])
        i = int(idx) - 1
        hot.append({"slot": int(idx), "key": slot_keys[i] if i < len(slot_keys) else str(idx),
                    "item": r["name"] if r else None, "count": r.get("count") if r else 0, "type": v[0]})
    out["hotkeys"] = hot

    # quick phrases: only what each slot does - the chat lines themselves are the player's own words
    out["phrases"] = {str(k): (phrases.code_of(v) or "text") + ("+text" if phrases.code_of(v) and v.strip()[2:].strip()
                                                                 else "")
                      for k, v in phrases.read(game_folder, me.get("id")).items()}
    out["potions_in_bag"] = sorted({r["name"] for r in bag if "potion" in r["name"].lower() and r.get("count")})
    totals: dict[str, int] = {}
    for r in bag:
        if r.get("name") and r.get("count"):
            totals[r["name"]] = totals.get(r["name"], 0) + int(r["count"])
    out["bag"] = [{"name": n, "count": c} for n, c in sorted(totals.items())]

    # fusion beasts and pets
    info = beasts.known()
    owned_beasts: list[Row] = _json(link, "mem beasts") or []
    out["beasts"] = [{"name": r["name"], "kind": info.get(r["name"], {}).get("kind", "unknown"),
                      "speed": info.get(r["name"], {}).get("speed", 0),
                      "level": info.get(r["name"], {}).get("level", 0)}
                     for r in owned_beasts]
    owned_pets: list[Row] = _json(link, "mem pets") or []
    out["pets"] = [r["name"] for r in owned_pets]
    gear: list[Row] = _json(link, "mem equipment") or []
    out["gear"] = [r["name"] for r in gear if r.get("equipped") == 1]
    modes: dict[str, Any] = _json(link, "mem modes") or {}
    if "options" in modes:              # the F1 settings menu (agent v10)
        out["game_options"] = {k: modes[k] for k in ("swap_attack", "auto_lock_target", "skip_enemy_corpses")}
    return out


def apply(profile: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    """Fold a scan into a profile. Keys from the game's keymap; beast and pet choices kept when
    still owned, cleared when not; new pets added at the end of the list, not ticked."""
    profile["scan"] = s
    keys: dict[str, Any] = s.get("keys") or {}
    for k, v in keys.items():
        profile[k] = v
    if s.get("skill_keys"):
        first_page = [x["key"] for x in s["skill_keys"] if x["page"] == 1]
        if first_page:
            profile["attack_keys"] = first_page      # the fallback when smart skills can't decide
    scanned_beasts: list[Row] = s.get("beasts", [])
    owned = {b["name"] for b in scanned_beasts}
    for k in ("speed_beast", "fight_beast"):
        if profile.get(k) not in ("auto", "none") and profile.get(k) not in owned:
            profile[k] = "auto" if k == "speed_beast" else "none"
    have: list[str] = s.get("pets", [])
    chosen: list[dict[str, Any]] = profile.get("pets", [])
    pets = [p for p in chosen if p["name"] in have]
    for name in have:
        if name not in {p["name"] for p in pets}:
            pets.append({"name": name, "use": False})
    profile["pets"] = pets
    return profile


def scan_and_save(link: JsonLink, character: str, game_folder: str="") -> tuple[dict[str, Any], dict[str, Any]]:
    """Scan, fold into the character's profile and save. Returns (profile, scan)."""
    s = scan(link, game_folder)
    p = apply(settings.load_profile(character), s)
    settings.save_profile(character, p)
    return p, s


def summary(s: dict[str, Any] | None) -> str:
    """The scan in a few lines a screen reader reads well."""
    if not s:
        return ""
    lines: list[str] = []
    lines.append("%s, %s%s - scanned %s" % (s.get("name"), s.get("class") or "class unknown",
                                           (", level %s" % s["level"]) if s.get("level") else "", s.get("when")))
    if s.get("skill_keys"):
        for page in (1, 2):
            row = ["%s %s" % (x["key"], x["skill"]) for x in s["skill_keys"] if x["page"] == page]
            if row:
                lines.append("Skills, page %d: %s" % (page, ", ".join(row)))
    if s.get("active_talents"):
        lines.append("Activated talents: " + ", ".join(s["active_talents"]))
    if s.get("profession"):
        lines.append("Profession skills: " + ", ".join(
            r["name"] + ("" if r.get("rank") is None else " (%s)" % gathering.rank_name(r["rank"]))
            for r in s["profession"]))
    if s.get("combos"):
        lines.append("Combos used in fights: " + "; ".join(s["combos"]))
    if s.get("hotkeys"):
        lines.append("Hotkeys: " + ", ".join("%s %s%s" % (h["key"], h["item"] or "(empty)",
                                                        (" x%s" % h["count"]) if h["item"] else "")
                                              for h in s["hotkeys"]))
    if s.get("phrases"):
        keys = gamekeys.phrase_key_list()
        lines.append("Quick phrases: " + ", ".join("%s %s" % (keys[int(k) - 1], v) for k, v in
                                                   sorted(s["phrases"].items(), key=lambda kv: int(kv[0])))
                     + "; empty: " + (", ".join(keys[i - 1] for i in range(1, 13) if str(i) not in s["phrases"])
                                      or "none"))
    if s.get("beasts"):
        lines.append("Fusion beasts: " + ", ".join("%s (%s%s)" % (b["name"], b["kind"],
                                                                 (", speed +%s" % b["speed"]) if b["speed"] else "")
                                                    for b in s["beasts"]))
    if s.get("pets"):
        lines.append("Pets: " + ", ".join(s["pets"]))
    return "\n".join(lines)
