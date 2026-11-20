"""Save and apply character hotkeys through the game's existing binding controls."""

from __future__ import annotations

import shlex
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .. import gameinstall, gamekeys, jsonio, phrases, settings
from ..clock import clock_for
from ..i18n import _
from ..link import JsonLink
from . import combat, keyslots
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

COMMAND = "keyboard_profile"
ACTION_PREFIX = "keyboard:"
SLOT_COUNTS = {"skills": 20, "profession": 3, "items": 12, "phrases": 12}


class Binding(BaseModel):
    id: int = Field(gt=0, strict=True)
    name: str


class Profile(BaseModel):
    # Keys are game slot numbers so the same profile can use different physical keys.
    skills: dict[int, Binding] = Field(default_factory=dict[int, Binding])
    profession: dict[int, Binding] = Field(default_factory=dict[int, Binding])
    items: dict[int, Binding] = Field(default_factory=dict[int, Binding])
    phrases: dict[int, str] = Field(default_factory=dict[int, str])
    prepare: list[int] = Field(default_factory=list[int])

    @field_validator("skills", "profession", "items", "phrases", "prepare")
    @classmethod
    def valid_slots(cls, values: Any, info: ValidationInfo) -> Any:
        limit = SLOT_COUNTS.get(info.field_name or "", SLOT_COUNTS["skills"])
        if any(not 1 <= slot <= limit for slot in values):
            raise ValueError(_("Invalid keyboard slot."))
        if info.field_name == "phrases":
            return {slot: phrase_text(value) for slot, value in values.items()}
        return values


def profile_name(value: str) -> str:
    name = value.strip()
    if not name or not value.isprintable():
        raise ValueError(_("Enter a keyboard profile name on one line."))
    return name


def phrase_text(value: str) -> str:
    text = value.strip()
    if not text or not value.isprintable():
        raise ValueError(_("Enter a quick phrase on one line."))
    return text


def names(p: dict[str, Any]) -> list[str]:
    return sorted(p.get("keyboard_profiles", {}), key=str.casefold)


def command(name: str) -> str:
    return COMMAND + " " + shlex.quote(profile_name(name))


def can_prepare(name: str) -> bool:
    """Use the combat data's self-buffs and shields, which can be applied before a fight."""
    return any(
        skill["name"] == name and (
            combat.SkillBook.long_buff(skill) or combat.SkillBook.fight_buff(skill)
            or combat.SkillBook.shield(skill) is not None
        )
        for skills in combat.knowledge().values() for skill in skills
    )


def client_folder(pid: int | None) -> str:
    return gameinstall.folder_of(pid, settings.load_app().get("game_folder", ""))


@dataclass
class Keys:
    folder: str
    binds: dict[str, str]
    skills: list[str]
    profession: list[str]
    items: list[str]
    phrases: list[str]

    @classmethod
    def read(cls, folder: str) -> Keys:
        binds = gamekeys.read(folder)
        return cls(folder, binds,
                   gamekeys.attack_keys_with_defaults(binds),
                   [binds.get(f"W_SKILL{i}") or default for i, default in enumerate(("z", "x", "c"), 1)],
                   [key or default for key, default in zip(gamekeys.slot_keys(binds), gamekeys.SLOT_DEFAULTS)],
                   [key or "shift+" + default for key, default in zip(gamekeys.phrase_keys(binds), gamekeys.SLOT_DEFAULTS)])

    def label(self, kind: str, slot: int) -> str:
        if kind == "skills":
            page, index = divmod(slot - 1, 10)
            return _("Page {page}, {key}", page=page + 1, key=self.skills[index])
        return {"items": self.items, "phrases": self.phrases, "profession": self.profession}[kind][slot - 1]


def _object(a: JsonLink, request: str) -> dict[str, Any]:
    response = a.json(request)
    if not jsonio.is_object(response) or "error" in response:
        raise ValueError(_("Couldn't read keyboard settings from the game."))
    return response


def _skill_slots(sk: dict[str, Any], kind: str) -> tuple[list[dict[str, Any]], dict[int, int]]:
    rows_key, slots_key = ("combat", "attack_keys") if kind == "skills" else ("profession", "profession_keys")
    if rows_key not in sk or slots_key not in sk:
        raise ValueError(_("Couldn't read keyboard settings from the game."))
    return sk[rows_key], {int(i): sid for i, sid in sk[slots_key].items() if 1 <= int(i) <= SLOT_COUNTS[kind] and sid}


def _items(a: JsonLink) -> tuple[dict[int, Binding], dict[int, int]]:
    rows = a.json("mem bagfull")
    if not jsonio.is_array(rows):
        raise ValueError(_("Couldn't read keyboard settings from the game."))
    inventory = {row["type"]: Binding(id=row["type"], name=row["name"]) for row in rows
                 if row.get("type", 0) > 0 and row.get("name") and row.get("count", 0) > 0}
    slots = {int(i): row[0] for i, row in _object(a, "mem slots").items()
             if 1 <= int(i) <= 12 and row and row[0] > 0}
    return inventory, slots


def _phrases(folder: str, char_id: int) -> dict[int, str]:
    if not phrases.known(folder, char_id):
        raise ValueError(_("Couldn't find this character's quick phrases in the game folder."))
    return phrases.read(folder, char_id, strict=True)


@dataclass
class Capture:
    profile: Profile
    skills: list[Binding]
    items: list[Binding]
    profession: list[Binding]


def capture(a: JsonLink, folder: str, character: str) -> Capture:
    me = _object(a, "mem self")
    char_id = me.get("id")
    expected_id = settings.profile_id(character)
    if not char_id or str(me.get("name", "")).casefold() != settings.profile_name(character).casefold() or (
        expected_id and str(char_id) != expected_id
    ):
        raise ValueError(_("Log in to the character in the game first."))
    snapshot = _object(a, "mem skills")
    rows, skills = _skill_slots(snapshot, "skills")
    profession_rows, profession = _skill_slots(snapshot, "profession")
    learned = {row["id"]: Binding(id=row["id"], name=row["name"]) for row in rows}
    learned_profession = {row["id"]: Binding(id=row["id"], name=row["name"]) for row in profession_rows}
    inventory, slots = _items(a)
    items = dict(inventory)
    for tid in slots.values():
        if tid not in items:
            items[tid] = Binding(id=tid, name=_("Item type {id}", id=tid))
    saved_skills = {slot: learned[sid] for slot, sid in skills.items() if sid in learned}
    profile = Profile(skills=saved_skills, profession={slot: learned_profession[sid] for slot, sid in profession.items()
                                                     if sid in learned_profession},
                      items={slot: items[tid] for slot, tid in slots.items()},
                      phrases=_phrases(folder, char_id))
    return Capture(profile, list(learned.values()), list(items.values()), list(learned_profession.values()))


def _ensure_idle(b: Bot) -> None:
    if b.in_fight or b.attackers() or b.dialog().get("open"):
        raise Stopped(_("Apply a keyboard profile while out of combat with game menus closed."))


def _prepare(b: Bot, book: combat.SkillBook, keys: Keys, profile: Profile, char_id: int) -> None:
    done: set[int] = set()
    for slot in profile.prepare:
        binding = profile.skills[slot]
        if binding.id in done:
            continue
        done.add(binding.id)
        page, index = divmod(slot - 1, 10)
        skill = next((x for x in book.slots.get(page, []) if x["name"] == binding.name), None)
        if not skill or not book.flip_to(page):
            raise Stopped(_("Couldn't prepare {skill}.", skill=binding.name))
        for _attempt in range(20):
            _ensure_idle(b)
            book.read_casts(char_id)
            now = clock_for(b).time()
            # This is one deliberate press, so leave the full global cooldown between casts.
            if now >= book.last_cast + book.gcd() and book.ready_in(now) <= 0 and book.ready(skill, now):
                break
            b.sleep(0.25)
        else:
            raise Stopped(_("{skill} isn't ready to prepare.", skill=binding.name))
        before = book.used.get(binding.name, 0.0)
        book.pressed(skill, clock_for(b).time())
        key = keys.binds.get("W_SELF_ATTACK%d" % (index + 1)) or combat.SkillBook.self_key(
            {"name": binding.name, "key": keys.skills[index]})
        b.tap_key(key)
        for _attempt in range(20):
            b.sleep(0.25)
            book.read_casts(char_id)
            if book.used.get(binding.name, 0.0) > before and binding.name not in book.pending:
                break
        else:
            raise Stopped(_("The game didn't confirm {skill}.", skill=binding.name))


def apply(b: Bot, name: str) -> None:
    saved = b.p.get("keyboard_profiles", {}).get(name)
    if saved is None:
        raise Stopped(_("Keyboard profile {name} was not found.", name=name))
    try:
        _apply(b, Profile.model_validate(saved))
    except (ValueError, OSError) as error:
        raise Stopped(str(error)) from error
    b.log(_("Applied keyboard profile {name}.", name=name), speak=True)


def _skill_changes(bindings: dict[int, Binding], rows: list[dict[str, Any]], skills: dict[int, int]) -> dict[int, Binding]:
    learned = {row["id"]: row["name"] for row in rows}
    for slot, binding in bindings.items():
        if binding.id not in learned:
            raise ValueError(_("The character hasn't learned {skill}.", skill=binding.name))
        bindings[slot] = Binding(id=binding.id, name=learned[binding.id])
    return {slot: binding for slot, binding in bindings.items() if skills.get(slot) != binding.id}


def _changes(b: Bot, profile: Profile, snapshot: dict[str, Any], keys: Keys, char_id: int) -> Profile:
    """Preflight game state and only keep slots whose assignments would change."""
    inventory, items = _items(b.a) if profile.items else ({}, {})
    current_phrases = _phrases(keys.folder, char_id) if profile.phrases else {}
    changes = Profile(
        skills=_skill_changes(profile.skills, *_skill_slots(snapshot, "skills")),
        profession=_skill_changes(profile.profession, *_skill_slots(snapshot, "profession")) if profile.profession else {},
        items={slot: binding for slot, binding in profile.items.items() if items.get(slot) != binding.id},
        phrases={slot: value for slot, value in profile.phrases.items() if current_phrases.get(slot) != value},
    )
    for binding in changes.items.values():
        if binding.id not in inventory:
            raise ValueError(_("{item} is missing from the bag.", item=binding.name))
    for slot in profile.prepare:
        binding = profile.skills.get(slot)
        if binding is None or not can_prepare(binding.name):
            raise ValueError(_("Choose a bound self-buff or shield to prepare."))
    if profile.phrases:
        b.phrases = current_phrases
    return changes


def _bind_profession(b: Bot, keys: Keys, rows: list[dict[str, Any]], bindings: dict[int, Binding]) -> None:
    for slot, binding in sorted(bindings.items()):
        _ensure_idle(b)
        key = keys.profession[slot - 1]
        index = next(i for i, row in enumerate(rows) if row["id"] == binding.id)
        if b.panel_cursor(6, index, len(rows)):
            b.tap_key(keys.binds.get(f"W_SET_SKILL{slot}") or "lctrl+alt+" + key)
            b.sleep(0.6)
            _rows, assigned = _skill_slots(_object(b.a, "mem skills"), "profession")
            if assigned.get(slot) == binding.id:
                continue
        raise Stopped(_("Couldn't assign {binding} to {key}.", binding=binding.name, key=key))


def _bind_items(b: Bot, keys: Keys, bindings: dict[int, Binding]) -> None:
    for slot, binding in sorted(bindings.items()):
        _ensure_idle(b)
        if b.bag_cursor(binding.name, item_type=binding.id) is not None:
            b.tap_key(keys.binds.get(f"W_SET_USE{slot}") or "lctrl+" + keys.items[slot - 1])
            b.sleep(0.5)
        current = _object(b.a, "mem slots").get(str(slot), [])
        if not current or current[0] != binding.id:
            raise Stopped(_("Couldn't assign {binding} to {key}.", binding=binding.name,
                            key=keys.label("items", slot)))


def _bind_phrases(b: Bot, keys: Keys, char_id: int, bindings: dict[int, str]) -> None:
    for slot, value in sorted(bindings.items()):
        _ensure_idle(b)
        result = b.a.send("phrase %d %s" % (slot, value))
        b.phrases = _phrases(keys.folder, char_id)
        if result != "set" or b.phrases.get(slot) != value:
            raise Stopped(_("Couldn't set the quick phrase on {key}.", key=keys.label("phrases", slot)))


def _apply(b: Bot, profile: Profile) -> None:
    _ensure_idle(b)
    char_id = _object(b.a, "mem self").get("id")
    if not char_id:
        raise Stopped(_("Log in to the character in the game first."))
    keys = Keys.read(client_folder(getattr(b.a, "pid", None)))
    snapshot = _object(b.a, "mem skills")
    changes = _changes(b, profile, snapshot, keys, char_id)
    if not (changes.skills or changes.profession or changes.items or changes.phrases or profile.prepare):
        return
    # Refresh the running bot's lookup after assigning slots, including on partial failure.
    book = b.skillbook
    if book is None or book.game_folder != keys.folder:
        book = combat.SkillBook(b, game_folder=keys.folder)
    if (changes.phrases and book.agent_version < phrases.SET_AGENT) or (profile.prepare and not book.confirm):
        raise ValueError(_("Restart the game with the current agent to apply these keyboard settings."))
    ui = b.ui()
    page, panel = ui.get("attack_page", 0), ui.get("panel", 0)
    skill_write = False
    item_write = False
    try:
        for slot, binding in sorted(changes.skills.items()):
            _ensure_idle(b)
            skill_write = True
            index = (slot - 1) % 10
            hotkey = keys.skills[index]
            bind_key = keys.binds.get(f"W_SET_ATTACK{index + 1}") or "lctrl+alt+" + hotkey
            if not keyslots.bind_skill(b, book, snapshot["combat"], binding.id, binding.name,
                                       slot, hotkey, bind_key=bind_key):
                raise Stopped(_("Couldn't assign {binding} to {key}.", binding=binding.name,
                                key=keys.label("skills", slot)))
        item_write = bool(changes.items)
        _bind_profession(b, keys, snapshot.get("profession", []), changes.profession)
        _bind_items(b, keys, changes.items)
        _bind_phrases(b, keys, char_id, changes.phrases)
    finally:
        if item_write:
            b.potion_slots = None
        if skill_write:
            b.skillbook = None
        moved_panel = skill_write or item_write or bool(changes.profession)
        _restore(b, book, page if skill_write else None, panel if moved_panel else None)
    if skill_write:
        book = b.skillbook = combat.SkillBook(b, game_folder=keys.folder)
    if profile.prepare:
        b.skillbook = book
        try:
            _prepare(b, book, keys, profile, char_id)
        finally:
            _restore(b, book, page, None)


def _restore(b: Bot, book: combat.SkillBook, page: int | None, panel: int | None) -> None:
    if (page is None and panel is None) or b.stop_event.is_set() or b.in_fight:
        return
    if b.attackers() or b.dialog().get("open"):
        return
    if page is not None:
        book.flip_to(page)
    if panel is not None:
        b.goto_panel(panel)
