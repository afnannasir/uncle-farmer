"""Elf fire combo rules, cast state, and channeling decisions."""

from __future__ import annotations

from collections.abc import Callable, Collection
from typing import TYPE_CHECKING, Any, Protocol

if TYPE_CHECKING:
    from .combat import SkillBook
    from .combos import Rule, Skill


PYROBLAST = "Pyroblast"
BLAZING_STORM = "Blazing Storm"
FIRE_IMPACT = "Fire Impact"
IGNITION = "Ignition"
BLAZING_COMBO = "Blazing Combo"
FLAME_COMBO = "Flame Combo"
CHANNEL_SKILLS = (BLAZING_STORM, PYROBLAST)
ELF_ATTACKS = ("Fireball", FIRE_IMPACT, "Fire Surge", *CHANNEL_SKILLS, IGNITION, "Sear", "Magma", "Dragon's Breath")
RELEASE_AT = 3  # fire elements needed for Ignition
FIRE_ELEMENT_CAP = 5

ELF_RULES: tuple[Rule, ...] = (
    {"talent": None, "release": (IGNITION, (PYROBLAST, BLAZING_STORM))},
    {"talent": FLAME_COMBO, "reset": ("Fireball", FIRE_IMPACT), "prime": False},
    {"talent": BLAZING_COMBO, "streak": (2, CHANNEL_SKILLS)},
)


class ElfCombos:
    """Elf rules and state for Combos, which supplies the book and shared priming state."""

    book: SkillBook
    rules: list[Rule]
    primed: dict[str, float]
    _reset_window: float

    def __init__(self, talents: Collection[str]) -> None:
        self.talents = set(talents)
        self.streak = 0
        self.built = 0  # fire elements since the last release, when there is no T-key reading
        self.has_blazing_combo = BLAZING_COMBO in self.talents
        self.pending_channelling = False
        self.available_to_channel_without_penalty = False
        self.fire_impact_crit = False
        self.saving_pyroblast_for_next = False

    def has_talent(self, name: str) -> bool:
        t: set[str] = getattr(self, "talents", set[str]())
        return name in t

    def _update_elf_chain(self, name: str, t: float, attack: bool, crit: bool) -> bool:
        """Update Elf pending channeling and channel permission. Returns True if streak handling
        was performed."""
        cls = getattr(self.book, "cls", None)
        is_elf = cls == "Elf" or name in ELF_ATTACKS
        if not (is_elf and attack):
            return False
        if not self.has_talent(BLAZING_COMBO):
            return False
        if name in CHANNEL_SKILLS:
            self.saving_pyroblast_for_next = False
            self.primed.pop(name, None)
            self.available_to_channel_without_penalty = crit
            self.fire_impact_crit = False
            self.pending_channelling = crit
            self.streak = int(crit)
            return True
        if self.available_to_channel_without_penalty:
            # Channeling already unlocked: preserve permission until Pyroblast/Blazing Storm is cast
            return True
        was_pending = self.pending_channelling
        had_fire_impact_crit = self.fire_impact_crit
        saving = getattr(self, "saving_pyroblast_for_next", False)
        if crit:
            if was_pending or had_fire_impact_crit:
                # 2nd crit landed! Unlocks channeling without penalty (whether Fire Impact or fallback normal skill)
                self.pending_channelling = False
                self.available_to_channel_without_penalty = True
                self.fire_impact_crit = False
                self._grant_channeling(t)
                return True
            elif name == FIRE_IMPACT:
                # Fire Impact crits first: cannot loop into itself, wait for another skill to crit
                self.fire_impact_crit = True
                self.pending_channelling = False
                self.streak = 1
                self.primed.pop(FIRE_IMPACT, None)
                return True
            else:
                # First crit of another skill: sets pending_channelling to True (primes Fire Impact)
                if not saving:
                    self.pending_channelling = True
                    self.streak = 1
                return True
        else:
            # Non-crit (miss or normal hit)
            if was_pending or had_fire_impact_crit or name == FIRE_IMPACT:
                # Follow-up attack missed or hit normally (did not crit): whole sequence restarts
                self.pending_channelling = False
                self.available_to_channel_without_penalty = False
                self.fire_impact_crit = False
                self.streak = 0
            return True

    def _grant_channeling(self, t: float) -> None:
        self.streak = 2
        until = float("inf") if self.has_talent(BLAZING_COMBO) else t + self._reset_window
        self._prime_streak(until)

    def _prime_streak(self, until: float) -> None:
        for rule in self.rules:
            if rule["kind"] == "streak" and self.streak >= rule["streak"][0]:
                for name in rule["streak"][1]:
                    self.primed[name] = until
                if self.has_talent(BLAZING_COMBO):
                    self.available_to_channel_without_penalty = True
                    self.pending_channelling = False
                    self.fire_impact_crit = False
                self.streak = 0

    def reset_chain(self) -> None:
        """Reset pending channeling state, channel permissions, and critical streak."""
        self.pending_channelling = False
        self.available_to_channel_without_penalty = False
        self.fire_impact_crit = False
        self.saving_pyroblast_for_next = False
        self.streak = 0

    def channeling_restricted(self, skill: Skill) -> bool:
        """Blazing Combo gates the channeling skills until the two-crit sequence completes."""
        return (self.has_talent(BLAZING_COMBO) and skill["name"] in CHANNEL_SKILLS
                and not self.available_to_channel_without_penalty)

    def _elf_pick(self, ok: Callable[[str], Skill | None], now: float) -> Skill | None:
        if self.pending_channelling and (fire_impact := ok(FIRE_IMPACT)):
            return fire_impact
        return self._channel_pick(ok)

    def _elf_blocks_primed(self, name: str) -> bool:
        return name == FIRE_IMPACT and self.fire_impact_crit  # Fire Impact cannot loop into itself

    def _channel_pick(self, ok: Callable[[str], Skill | None]) -> Skill | None:
        """Channeling priority without penalty: packs prefer Blazing Storm, single target prefers Pyroblast."""
        if not self.available_to_channel_without_penalty and not all(name in self.primed for name in CHANNEL_SKILLS):
            return None
        pack = getattr(self.book, "_last_pack_size", 1)
        if pack >= 2 and (storm := ok(BLAZING_STORM)):
            return storm
        return ok(PYROBLAST) or ok(BLAZING_STORM)


def unpenalized(skill: Skill, available: bool) -> bool:
    return available and skill.get("name") in CHANNEL_SKILLS


def pyroblast_follow_key(slots: dict[int, list[Skill]], up: int, allowed: Callable[[Skill], bool]) -> str | None:
    """The allowed Pyroblast key, if it is on the page that is up."""
    pyro = [x for pg in sorted(slots, key=lambda pg: pg != up) for x in slots[pg]
            if x.get("name") == PYROBLAST and allowed(x)]
    if pyro and pyro[0] in slots.get(up, []):
        return pyro[0]["key"]
    return None


class _ChannelView(Protocol):
    pack: int

    def first(self, role: str, /) -> Skill | None: ...


def pyroblast_wasted_on(book: SkillBook, target: dict[str, Any], v: _ChannelView) -> bool:
    """True if Pyroblast should not be used on the current monster because it is already
    dying or expected to die from remaining normal attacks."""
    hp = target.get("hp")
    if hp is None or hp <= 0:
        return True
    if hasattr(book, "downed") and target.get("id") in book.downed:
        return True
    base = v.first("base_attack") or v.first("single_target")
    if base:
        weight = book.combos.weight(base) if book.combos else 1.0
        base_damage = (base.get("attack") or 0) * weight
        if base_damage > 0 and hp <= base_damage:
            return True
    return False


def adjust_pyroblast_target(book: SkillBook, choice: Skill | None, v: _ChannelView,
                           wasted_on: Callable[[], bool]) -> Skill | None:
    """Avoid casting Pyroblast or Blazing Storm on a nearly dying target, falling back to normal attack."""
    if choice and choice["name"] in CHANNEL_SKILLS:
        if (choice["name"] == PYROBLAST or v.pack < 2) and wasted_on():
            book.saving_pyroblast_for_next = True
            alt = v.first("base_attack") or v.first("single_target")
            if alt and alt["name"] not in CHANNEL_SKILLS:
                return alt
        else:
            book.saving_pyroblast_for_next = False
    return choice
