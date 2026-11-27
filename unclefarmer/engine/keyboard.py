"""Save and apply character hotkeys through the game's existing binding controls.

A layout belongs to one character (its profile's keyboard_profiles), or is shared, in the profiles
folder's SHARED_FILE, with every character of one class or with every character. Applying one puts
on what it can and names what it couldn't: a skill the character hasn't learned, an item missing from
the bag, a fusion beast or pet it doesn't own, or a key the game didn't take.

Skill and profession ids are the game's own, one per skill whatever its level and the same for every
character of the class (seen live on three Elves). A number key holds [item type, bag uid], or
[0, beast id] for a fusion beast and [1, pet id] for a pet (seen live); beast and pet ids are each
character's own (one Wind Spirit Guardian was 62203, another 64034), so those are matched by name
on another character, a pet by its exact name (the player's own name for it is part of it).
"""

from __future__ import annotations

import os
import shlex
import threading
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

from pydantic import BaseModel, Field, ValidationInfo, field_validator

from .. import gameinstall, gamekeys, jsonio, paths, phrases, settings
from ..clock import clock_for
from ..i18n import _
from ..link import JsonLink
from . import beasts, combat, keyslots, pets
from .botparts.base import Stopped

if TYPE_CHECKING:
    from .bot import Bot

COMMAND = "keyboard_profile"
ACTION_PREFIX = "keyboard:"
SLOT_COUNTS = {"skills": 20, "profession": 3, "items": 12, "phrases": 12}
BEAST_SLOT, PET_SLOT = 0, 1        # a number key's first number for a fusion beast / a pet
BEAST_PANEL, PET_PANEL = 2, 3      # their bag panels
SCOPES = ("character", "class", "all")
SHARED_FILE = "_keyboard_layouts.json"   # in the profiles folder: {"class": {class: {name: layout}}, "all": {name: layout}}

_shared_lock = threading.Lock()


class Binding(BaseModel):
    id: int = Field(gt=0, strict=True)
    name: str
    kind: Literal["item", "beast", "pet"] = "item"     # what a number key holds (skills are always "item")


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


# ---------------------------------------------------------------- where layouts are kept
def class_of(p: dict[str, Any]) -> str:
    """The character's class as its last scan read it ("" when never scanned)."""
    scan: dict[str, Any] = p.get("scan") or {}
    return str(scan.get("class") or "")


def _shared_path() -> str:
    return os.path.join(paths.PROFILES, SHARED_FILE)


def _read_shared(strict: bool = False) -> dict[str, Any]:
    """The shared layouts. Unreadable: empty, or with strict (before saving over it) the error."""
    got: object
    try:
        got = jsonio.read(_shared_path(), None)
    except (OSError, ValueError):
        if strict:
            raise ValueError(_("Couldn't read the shared keyboard profiles ({file}).", file=SHARED_FILE)) from None
        got = {}
    if not jsonio.is_object(got):
        got = {}
    classes, every = got.get("class"), got.get("all")
    return {"class": {str(c): dict(t) for c, t in classes.items() if jsonio.is_object(t)}
            if jsonio.is_object(classes) else {},
            "all": dict(every) if jsonio.is_object(every) else {}}


def layouts(p: dict[str, Any], cls: str | None = None) -> list[tuple[str, str]]:
    """(scope, name) of every layout this character may use: its own, its class's, everyone's. A
    name is listed once, the closest one winning (its own before its class's before everyone's)."""
    cls = cls or class_of(p)
    shared = _read_shared()
    own: dict[str, Any] = p.get("keyboard_profiles") or {}
    out: list[tuple[str, str]] = []
    seen: set[str] = set()
    for scope, table in (("character", own), ("class", shared["class"].get(cls, {}) if cls else {}),
                         ("all", shared["all"])):
        for name in sorted(table, key=str.casefold):
            if name not in seen:
                seen.add(name)
                out.append((scope, name))
    return out


def names(p: dict[str, Any]) -> list[str]:
    return sorted((name for _scope, name in layouts(p)), key=str.casefold)


def find(p: dict[str, Any], name: str, cls: str | None = None) -> tuple[str, dict[str, Any]] | None:
    """(scope, layout) of the layout this character uses under that name, or None."""
    cls = cls or class_of(p)
    for scope, found in layouts(p, cls):
        if found != name:
            continue
        if scope == "character":
            return scope, p["keyboard_profiles"][name]
        shared = _read_shared()
        table = shared["class"].get(cls, {}) if scope == "class" else shared["all"]
        if name in table:
            return scope, table[name]
    return None


def saved_in(p: dict[str, Any], scope: str, name: str, cls: str = "") -> bool:
    """True when that scope's table holds the name (even one hidden by a closer profile)."""
    if scope == "character":
        return name in (p.get("keyboard_profiles") or {})
    shared = _read_shared()
    return name in (shared["class"].get(cls, {}) if scope == "class" and cls else {} if scope == "class"
                    else shared["all"])


def store(character: str, scope: str, name: str, layout: dict[str, Any] | None, cls: str = "") -> dict[str, Any]:
    """Save a layout (None deletes it) for the character, its class (cls) or everyone. Returns the
    character's profile as it now is."""
    cfg = settings.load_profile(character)
    if scope == "character":
        own = cfg.setdefault("keyboard_profiles", {})
        if layout is None:
            own.pop(name, None)
        else:
            own[name] = layout
        settings.save_profile(character, cfg)
        return cfg
    if scope == "class" and not cls:
        raise ValueError(_("The bot doesn't know this character's class yet. Capture its keys first."))
    with _shared_lock:
        shared = _read_shared(strict=True)
        table: dict[str, Any] = shared["class"].setdefault(cls, {}) if scope == "class" else shared["all"]
        if layout is None:
            table.pop(name, None)
        else:
            table[name] = layout
        shared["class"] = {c: t for c, t in shared["class"].items() if t}
        jsonio.save(_shared_path(), shared, indent=1)
    return cfg


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


# ---------------------------------------------------------------- the number keys
def _number_keys(a: JsonLink) -> dict[int, tuple[str, int]]:
    """What each number key holds: ("item", item type), ("beast", beast id) or ("pet", pet id)."""
    out: dict[int, tuple[str, int]] = {}
    for i, row in _object(a, "mem slots").items():
        if not (1 <= int(i) <= SLOT_COUNTS["items"] and jsonio.is_array(row) and row):
            continue
        code, ref = int(row[0]), int(row[1]) if len(row) > 1 else 0
        if code == BEAST_SLOT and ref > 0:
            out[int(i)] = ("beast", ref)
        elif code == PET_SLOT and ref > 0:
            out[int(i)] = ("pet", ref)
        elif code > PET_SLOT:
            out[int(i)] = ("item", code)
    return out


def _inventory(a: JsonLink) -> dict[int, Binding]:
    rows = a.json("mem bagfull")
    if not jsonio.is_array(rows):
        raise ValueError(_("Couldn't read keyboard settings from the game."))
    return {row["type"]: Binding(id=row["type"], name=row["name"]) for row in rows
            if row.get("type", 0) > 0 and row.get("name") and row.get("count", 0) > 0}


def _list(a: JsonLink, request: str) -> list[dict[str, Any]]:
    rows = a.json(request)
    return [r for r in rows if jsonio.is_object(r) and isinstance(r.get("id"), int) and r["id"] > 0] \
        if jsonio.is_array(rows) else []


def _owned(a: JsonLink) -> dict[tuple[str, int], Binding]:
    """Everything a number key can hold for this character, by (kind, id)."""
    owned: dict[tuple[str, int], Binding] = {("item", t): b for t, b in _inventory(a).items()}
    for kind, request in (("beast", "mem beasts"), ("pet", "mem pets")):
        for r in _list(a, request):
            owned[(kind, r["id"])] = Binding(id=r["id"], name=str(r.get("name") or "").strip() or str(r["id"]),
                                             kind="beast" if kind == "beast" else "pet")
    return owned


def _unknown(kind: str, ref: int) -> Binding:
    label = {"beast": _("Fusion beast {id}", id=ref), "pet": _("Pet {id}", id=ref)}.get(kind) \
        or _("Item type {id}", id=ref)
    return Binding(id=ref, name=label, kind="beast" if kind == "beast" else "pet" if kind == "pet" else "item")


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
    cls: str | None = None       # the class its learned skills say


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
    owned = _owned(a)
    items: dict[int, Binding] = {}
    for slot, held in _number_keys(a).items():
        items[slot] = owned.setdefault(held, _unknown(*held))
    saved_skills = {slot: learned[sid] for slot, sid in skills.items() if sid in learned}
    profile = Profile(skills=saved_skills, profession={slot: learned_profession[sid] for slot, sid in profession.items()
                                                     if sid in learned_profession},
                      items=items, phrases=_phrases(folder, char_id))
    return Capture(profile, list(learned.values()), list(owned.values()), list(learned_profession.values()),
                   combat.class_of_skills({b.name for b in learned.values()}))


# ---------------------------------------------------------------- applying
@dataclass
class _Work:
    """What a layout changes on this character, and what it can't put on."""
    profile: Profile
    missed: list[str] = field(default_factory=list[str])
    unbound: set[int] = field(default_factory=set[int])     # skill slots the game didn't take

    def miss(self, name: str, key: str) -> None:
        self.missed.append(f"{name} ({key})")


def _ensure_idle(b: Bot) -> None:
    if b.in_fight or b.attackers() or b.dialog().get("open"):
        raise Stopped(_("Apply a keyboard profile while out of combat with game menus closed."))


def _prepare(b: Bot, book: combat.SkillBook, keys: Keys, profile: Profile, char_id: int) -> None:
    done: set[int] = set()
    for slot in profile.prepare:
        binding = profile.skills.get(slot)
        if binding is None or binding.id in done:
            continue                    # (a skill this character hasn't learned: already named)
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
    sk = b.a.json("mem skills")
    learned = {str(r.get("name")) for r in sk.get("combat", []) if jsonio.is_object(r)} if jsonio.is_object(sk) else set[str]()
    found = find(b.p, name, combat.class_of_skills(learned) or class_of(b.p))
    if found is None:
        raise Stopped(_("Keyboard profile {name} was not found.", name=name))
    try:
        work = _Work(Profile.model_validate(found[1]))
    except ValueError as error:
        raise Stopped(str(error)) from error
    try:
        _apply(b, work)
    except (Stopped, ValueError, OSError) as error:
        if work.missed:             # what it had already found it couldn't put on
            b.log(_("Keyboard profile {name} stopped; it couldn't put on: {missed}.",
                    name=name, missed=", ".join(work.missed)), speak=True)
        if isinstance(error, Stopped):
            raise
        raise Stopped(str(error)) from error
    missed = work.missed
    if missed:
        b.log(_("Applied keyboard profile {name}, except what couldn't be put on: {missed}.",
                name=name, missed=", ".join(missed)), speak=True)
    else:
        b.log(_("Applied keyboard profile {name}.", name=name), speak=True)


def _skill_changes(work: _Work, kind: str, keys: Keys, rows: list[dict[str, Any]],
                   skills: dict[int, int]) -> dict[int, Binding]:
    """The layout's skills on this character (by id, else by name), the ones it lacks named."""
    by_id = {row["id"]: row["name"] for row in rows}
    by_name = {str(row["name"]).casefold(): row["id"] for row in rows}
    bindings: dict[int, Binding] = getattr(work.profile, kind)
    resolved: dict[int, Binding] = {}
    for slot, binding in sorted(bindings.items()):
        sid = binding.id if binding.id in by_id else by_name.get(binding.name.casefold())
        if sid is None:
            work.miss(binding.name, keys.label(kind, slot))
            continue
        resolved[slot] = Binding(id=sid, name=by_id[sid])
    setattr(work.profile, kind, resolved)
    return {slot: binding for slot, binding in resolved.items() if skills.get(slot) != binding.id}


def _held_here(binding: Binding, inventory: dict[int, Binding], beast_rows: list[dict[str, Any]],
               pet_rows: list[dict[str, Any]]) -> Binding | None:
    """The thing on this character that a layout's number key means, or None when it has none."""
    if binding.kind == "item":
        return binding if binding.id in inventory else None
    rows = beast_rows if binding.kind == "beast" else pet_rows
    row = next((r for r in rows if r["id"] == binding.id), None)
    if row is None and binding.kind == "beast":
        row = next((r for r in rows if str(r.get("name") or "").strip() == binding.name.strip()), None)
    elif row is None:
        row = next((r for r in rows if pets.same_pet(r.get("name"), binding.name)), None)
    return Binding(id=row["id"], name=binding.name, kind=binding.kind) if row else None


def _number_key_changes(b: Bot, work: _Work, keys: Keys) -> dict[int, Binding]:
    if not work.profile.items:
        return {}
    inventory, current = _inventory(b.a), _number_keys(b.a)
    kinds = {binding.kind for binding in work.profile.items.values()}
    beast_rows = _list(b.a, "mem beasts") if "beast" in kinds else []
    pet_rows = _list(b.a, "mem pets") if "pet" in kinds else []
    out: dict[int, Binding] = {}
    for slot, binding in sorted(work.profile.items.items()):
        if current.get(slot) == (binding.kind, binding.id):
            continue                # already on (a potion that ran out keeps its key)
        here = _held_here(binding, inventory, beast_rows, pet_rows)
        if here is None:
            work.miss(binding.name, keys.label("items", slot))
        elif current.get(slot) != (here.kind, here.id):
            out[slot] = here
    return out


def _changes(b: Bot, work: _Work, snapshot: dict[str, Any], keys: Keys, char_id: int) -> Profile:
    """Preflight game state and only keep slots whose assignments would change."""
    profile = work.profile
    current_phrases = _phrases(keys.folder, char_id) if profile.phrases else {}
    changes = Profile(
        skills=_skill_changes(work, "skills", keys, *_skill_slots(snapshot, "skills")),
        profession=_skill_changes(work, "profession", keys, *_skill_slots(snapshot, "profession"))
        if profile.profession else {},
        items=_number_key_changes(b, work, keys),
        phrases={slot: value for slot, value in profile.phrases.items() if current_phrases.get(slot) != value},
    )
    for slot in profile.prepare:
        binding = profile.skills.get(slot)
        if binding is not None and not can_prepare(binding.name):
            raise ValueError(_("Choose a bound self-buff or shield to prepare."))
    if profile.phrases:
        b.phrases = current_phrases
    return changes


def _bind_profession(b: Bot, work: _Work, keys: Keys, rows: list[dict[str, Any]], bindings: dict[int, Binding]) -> None:
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
        work.miss(binding.name, key)


def _put_on_number_key(b: Bot, keys: Keys, slot: int, binding: Binding) -> bool:
    """An item from the bag panel, a fusion beast or a pet from theirs, onto the number key; True
    once the game shows it there."""
    if binding.kind == "item":
        placed = b.bag_cursor(binding.name, item_type=binding.id) is not None
    else:
        panel, rows = (BEAST_PANEL, beasts.owned(b)) if binding.kind == "beast" else (PET_PANEL, pets.owned(b))
        index = next((i for i, r in enumerate(rows) if r.get("id") == binding.id), None)
        placed = index is not None and b.panel_cursor(panel, index, len(rows))
    if placed:
        b.tap_key(keys.binds.get(f"W_SET_USE{slot}") or "lctrl+" + keys.items[slot - 1])
        b.sleep(0.5)
    return _number_keys(b.a).get(slot) == (binding.kind, binding.id)


def _bind_items(b: Bot, work: _Work, keys: Keys, bindings: dict[int, Binding]) -> None:
    for slot, binding in sorted(bindings.items()):
        _ensure_idle(b)
        if not _put_on_number_key(b, keys, slot, binding):
            work.miss(binding.name, keys.label("items", slot))


def _bind_phrases(b: Bot, work: _Work, keys: Keys, char_id: int, bindings: dict[int, str]) -> None:
    for slot, value in sorted(bindings.items()):
        _ensure_idle(b)
        result = b.a.send("phrase %d %s" % (slot, value))
        b.phrases = _phrases(keys.folder, char_id)
        if result != "set" or b.phrases.get(slot) != value:
            work.miss(_("the quick phrase"), keys.label("phrases", slot))


def _apply(b: Bot, work: _Work) -> None:
    """Put the layout on; what couldn't be put on goes into work.missed (each "name (key)")."""
    profile = work.profile
    _ensure_idle(b)
    char_id = _object(b.a, "mem self").get("id")
    if not char_id:
        raise Stopped(_("Log in to the character in the game first."))
    keys = Keys.read(client_folder(getattr(b.a, "pid", None)))
    snapshot = _object(b.a, "mem skills")
    changes = _changes(b, work, snapshot, keys, char_id)
    prepare = [slot for slot in profile.prepare if slot in profile.skills]
    if not (changes.skills or changes.profession or changes.items or changes.phrases or prepare):
        return
    # Refresh the running bot's lookup after assigning slots, including on partial failure.
    # The profile may hold page-2 keys the player asked for: this book sees both pages whatever
    # skill_page_one says (the farm's own book comes back after, _farm_book), and the page is left on
    # page 1 when that box is ticked.
    page_one = bool(b.p.get("skill_page_one"))
    book = b.skillbook
    if book is None or book.game_folder != keys.folder or (page_one and not getattr(book, "both_pages", False)):
        book = combat.SkillBook(b, game_folder=keys.folder, both_pages=True)
    if (changes.phrases and book.agent_version < phrases.SET_AGENT) or (prepare and not book.confirm):
        raise ValueError(_("Restart the game with the current agent to apply these keyboard settings."))
    ui = b.ui()
    page, panel = (0 if page_one else ui.get("attack_page", 0)), ui.get("panel", 0)
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
                work.miss(binding.name, keys.label("skills", slot))
                work.unbound.add(slot)
        item_write = bool(changes.items)
        _bind_profession(b, work, keys, snapshot.get("profession", []), changes.profession)
        _bind_items(b, work, keys, changes.items)
        _bind_phrases(b, work, keys, char_id, changes.phrases)
    finally:
        if item_write:
            b.potion_slots = None
        if skill_write:
            b.skillbook = None
        moved_panel = skill_write or item_write or bool(changes.profession)
        _restore(b, book, page if skill_write else None, panel if moved_panel else None)
    if skill_write:
        book = b.skillbook = combat.SkillBook(b, game_folder=keys.folder, both_pages=page_one)
    for slot in work.unbound:                   # not on its key: nothing to cast it from
        profile.skills.pop(slot, None)
    prepare = [slot for slot in prepare if slot in profile.skills]
    if prepare:
        b.skillbook = book
        try:
            _prepare(b, book, keys, profile, char_id)
        finally:
            _restore(b, book, page, None)
            _farm_book(b, page_one)
    _farm_book(b, page_one)


def _farm_book(b: Bot, page_one: bool) -> None:
    """With skill_page_one, the bot's own book is read again without page 2 (the Keyboard Manager's
    sees both): the farm must never pick a page-2 skill from it."""
    if page_one and getattr(b.skillbook, "both_pages", False):
        b.skillbook = None


def _restore(b: Bot, book: combat.SkillBook, page: int | None, panel: int | None) -> None:
    if (page is None and panel is None) or b.stop_event.is_set() or b.in_fight:
        return
    if b.attackers() or b.dialog().get("open"):
        return
    if page is not None:
        book.flip_to(page)
    if panel is not None:
        b.goto_panel(panel)
