"""Combos: one skill setting up the next, as the game's own texts describe them.

Most of them come from talents. Each class has two talent paths that exclude each other, and a
talent only works while it is activated (IN op=14 / `mem skills` talents: "id,1,name"; the client
plays its "worn" sound for those). So a combo is used only when its talent is activated AND every
skill it names is on one of our keys. A few come from the skill texts themselves and need no
talent (Frost Nova freezes for Ice Lance, Pyroblast's fire elements for Ignition...).

What a rule can say, with the texts that ask for it:
  keep      press it whenever it is ready (and, with lasts, not already on the target):
            Corrosion Spell for Enhanced Explosion, Aqua Jet for Ice Finger, Mind Whip for Divine
            Whip, Leap Slash for Powerful Leap Attack.
  while_on  press it while another of our skills is on the target: Skeleton Explosion on a
            corroded target always crits.
  then      after one skill, the next is this one (within a few seconds): Sweep Attack after
            Cleave, Ice Lance on a target Frost Nova froze or Ice Arrow condensed.
  reset     a critical hit of one skill ends another's cooldown (Call of the Wilderness, Flame
            Combo, Crisp Wisdom, Wisdom, Dead Toxin Surge, Divine Nova itself): pressed next.
  before    this one goes first when the other is about to be pressed: Aimed Shot's 22% for the
            attack after it is spent on Rapid Arrows or Marked Shot, not on Arcane Shot.
  release   spend what builders built: Ignition once Pyroblast and Blazing Storm made 3 fire
            elements.
  filler    in place of the base attack: Ice Arrow, whose Ice Spikes add 20% each to Ice Lance.
  gcd       after this skill the global cooldown is shorter (Leap Slash halves it).
  fast      after a critical hit of this skill, attacks are faster for a while (Enrage).
  cd_less   a shorter cooldown (Freezing Cold: Ice Storm 1.5 s).
  spend_cd  each combo point spent takes a second off this skill's cooldown (Shadow of Erosion).
  streak    after this many critical hits in a row, one of these is cast at once (Blazing Combo).

Critical hits come in the cast packet itself: IN op=41's fifth section lists, for each target
our cast touched, "target,result,amount", and result & 7 == 5 is the game's critical hit (2 a
dodge, 4 a defence break, 6 a heal; the client's combat announcer reads the same field). The
agent passes them on from v28; with an older one the rules that need a critical hit stay off.
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable, Sequence
from typing import TYPE_CHECKING, Any, TypeGuard

from ..i18n import _

if TYPE_CHECKING:
    from .combat import SkillBook

Rule = dict[str, Any]       # one of RULES: its talent, its kind's value, and options (window, lasts...)
Skill = dict[str, Any]      # a skill record on our keys (combat.Skill)

CRIT = 5                 # hit result (& 7) the game gives a critical hit
CRIT_AGENT = 28          # the first agent that reports hit results with our casts
WINDOW = 4.0             # a "then" follow-up waits this long for its skill, no longer
RESET_WINDOW = 8.0       # a skill whose cooldown a critical hit just ended is pressed within this
RELEASE_AT = 3           # fire elements before Ignition goes (5 at most)
ON_MARGIN = 1.5          # a skill kept on the target is cast again this long before it runs out

# Talents name skills by the names the skill list uses, except here.
ALIASES: dict[str, str] = {"Divine Whip": "Mind Whip"}   # the Priest's only whip; the talent's own text calls it this

RULES: list[Rule] = [
    # Warrior
    {"talent": "Cleave Combo", "then": ("Cleave", "Sweep Attack")},          # +15% Sweep Attack
    {"talent": "Powerful Leap Attack", "keep": "Leap Slash"},
    {"talent": "Powerful Leap Attack", "gcd": ("Leap Slash", 0.5)},          # and +33% damage for 1 s
    # Orc
    {"talent": "Enrage", "fast": ("Bloodthirsty", 2.0, 0.5)},                # +100% attack speed, 2 s
    # Assassin
    {"talent": "Shadow of Erosion", "spend_cd": "Blade Fury Dance"},
    # Hunter
    {"talent": "Call of the Wilderness", "reset": ("Arcane Shot", "Rapid Arrows")},
    {"talent": "Steady Hands", "before": ("Aimed Shot", ("Rapid Arrows", "Burst Shot", "Marked Shot"))},
    # Mage
    {"talent": None, "then": ("Frost Nova", "Ice Lance"), "window": 4.5},   # frozen: 3x damage
    {"talent": None, "filler": "Ice Arrow"},
    {"talent": "Ice Finger", "keep": "Aqua Jet"},
    # one arrow into the jet condenses the target, and only when Ice Lance is there to use it
    {"talent": "Ice Finger", "while_on": ("Ice Arrow", "Aqua Jet"), "lasts": 6.0, "once": True,
     "lance": "Ice Lance"},
    {"talent": "Ice Finger", "then": ("Ice Arrow", "Ice Lance"), "if_on": ("Aqua Jet", 6.0)},  # condensed
    {"talent": "Ice Finger", "reset": ("Ice Arrow", "Ice Lance"), "keep_cd": True},   # a crit condenses too
    {"talent": "Ice Finger", "reset": ("Ice Orb", "Ice Lance"), "keep_cd": True},
    {"talent": "Freezing Cold", "cd_less": ("Ice Storm", 1.5)},
    {"talent": "Crisp Wisdom", "reset": ("*", "Ice Orb")},
    # Elf
    {"talent": None, "release": ("Ignition", ("Pyroblast", "Blazing Storm"))},
    {"talent": "Flame Combo", "reset": ("Fireball", "Fire Impact")},
    {"talent": "Blazing Combo", "streak": (2, ("Blazing Storm", "Pyroblast"))},
    # Priest
    {"talent": None, "reset": ("Divine Nova", "Divine Fire")},
    {"talent": "Wisdom", "reset": ("*", "Divine Nova")},
    {"talent": "Divine Whip", "keep": "Mind Whip", "lasts": 15.0},
    {"talent": "Divine Whip", "while_on": ("Divine Nova", "Mind Whip"), "lasts": 15.0},
    # Witcher
    {"talent": "Enhanced Explosion", "keep": "Corrosion Spell"},
    {"talent": "Enhanced Explosion", "while_on": ("Skeleton Explosion", "Corrosion Spell"), "lasts": 15.0},
    {"talent": "Dead Toxin Surge", "reset": ("Shadow Arrow", "Dead Toxin")},
]

# what a combo is called in the log and the scan
SAY: dict[str, str] = {
    "then": "{a} then {b}", "keep": "{a} on every target", "while_on": "{a} while {b} is on the target",
    "reset": "{b} when {a} crits", "before": "{a} before {b}", "release": "{a} after {b}",
    "filler": "{a} instead of the base attack", "gcd": "{a}: shorter global cooldown after it",
    "fast": "faster attacks after {a} crits", "cd_less": "{a}: shorter cooldown",
    "spend_cd": "{a}: shorter cooldown as combos are spent", "streak": "{b} at once after critical hits",
}
KINDS = ("then", "keep", "while_on", "reset", "before", "release", "filler", "gcd", "fast", "cd_less",
         "spend_cd", "streak")
NEEDS_CRIT = ("reset", "fast", "streak")


def _is_seq(v: object) -> TypeGuard[Sequence[object]]:
    """A tuple or a list of a rule's value (its parts)."""
    return isinstance(v, (tuple, list))


def _parts(v: object) -> Sequence[object]:
    """A rule's value as its parts: a tuple or list as it is, anything else alone."""
    return v if _is_seq(v) else (v,)


def _names(v: object) -> list[str]:
    """Every skill name a rule's value mentions ("*" = any of ours)."""
    out: list[str] = []
    for x in _parts(v):
        if isinstance(x, str):
            out.append(x)
        elif _is_seq(x):
            out += _names(x)
    return out


def active_talents(skills_mem: dict[str, Any] | None) -> set[str]:
    """Names of the activated talents in a `mem skills` answer."""
    rows: list[dict[str, Any]] = (skills_mem or {}).get("talents", [])
    return {r["name"] for r in rows if r.get("b") == 1 and r.get("name")}


def _on(part: object, on_keys: Collection[str]) -> bool:
    """A rule's part is there: a skill on a key, any of a list of alternatives, "*", a number."""
    if isinstance(part, str):
        return part == "*" or part in on_keys
    if _is_seq(part):
        return any(_on(x, on_keys) for x in part)
    return True


assert all(sum(k in r for k in KINDS) == 1 for r in RULES), "one kind per rule"


def rules_for(talents: Collection[str], on_keys: Collection[str]) -> list[Rule]:
    """The rules that apply: talent activated (or none needed) and the skills it names on keys
    (of a list of alternatives - Rapid Arrows, Burst Shot or Marked Shot - one is enough)."""
    out: list[Rule] = []
    for r in RULES:
        if r["talent"] and r["talent"] not in talents:
            continue
        kind = next(k for k in KINDS if k in r)
        v = r[kind]
        parts: Sequence[object] = (v if _is_seq(v) and isinstance(v, tuple) else (v,))     # a list is one part here
        if all(_on(x, on_keys) for x in parts):
            out.append(dict(r, kind=kind))
    return out


def describe(rule: Rule) -> str:
    v = rule[rule["kind"]]
    names = [n for n in _parts(v) if not isinstance(n, (int, float))]
    a = names[0] if names else ""
    b = names[1] if len(names) > 1 else ""
    if rule["kind"] == "streak":
        a, b = "", v[1]
    def fmt(x: object) -> object:
        return " / ".join(_names(x)) if _is_seq(x) else ("any skill" if x == "*" else x)
    text = SAY[rule["kind"]].format(a=fmt(a), b=fmt(b))
    return "%s (%s)" % (text, rule["talent"]) if rule["talent"] else text


def damage_bonus(talents: Collection[str], on_keys: Collection[str]) -> dict[str, int]:
    """{skill: extra %} from activated talents that make one skill hit harder ("Increase 22% Ice
    Lance Damage", "increasing 12% Blade Strike damage"): a tie-break between similar skills."""
    from .combat import talent_texts
    out: dict[str, int] = {}
    for name, text in talent_texts().items():
        if name not in talents:
            continue
        for pct, skill in re.findall(r"increas\w* (\d+)% ([A-Z][\w' ]+?) damage", text, re.I):
            name = str(skill).strip()
            name = ALIASES.get(name, name)
            if name in on_keys:
                out[name] = out.get(name, 0) + int(pct)
    return out


class Combos:
    """The combos for one character, with what our last casts set up."""

    def __init__(self, book: SkillBook, talents: Collection[str], on_keys: Collection[str], crits: bool) -> None:
        self.book = book
        self.crits = crits                      # the agent reports critical hits
        self.rules = [r for r in rules_for(talents, on_keys) if crits or r["kind"] not in NEEDS_CRIT]
        self.missing_crit = [r for r in rules_for(talents, on_keys) if not crits and r["kind"] in NEEDS_CRIT]
        self.bonus = damage_bonus(talents, on_keys)
        self.primed: dict[str, float] = {}        # skill -> until: pressed next if it is ready
        self.last: tuple[str, float] | None = None    # (name, time) of our newest cast
        self.fast_until = 0.0
        self.streak = 0
        self.built = 0          # fire elements our builders made since the last release (no T reading)

    def of(self, kind: str) -> list[Rule]:
        return [r for r in self.rules if r["kind"] == kind]

    def lines(self) -> list[str]:
        return [describe(r) for r in self.rules]

    # ---------------------------------------------------------------- what the game told us
    def cast(self, name: str, t: float, target: int | None, crit: bool, hits: int | None,
             info: Skill) -> tuple[str, float] | None:
        """One of our casts, confirmed by the game. target = the id it was aimed at (or None);
        crit = it landed a critical hit; hits = how many targets it touched (None: unknown)."""
        prev = self.last
        self.last = (name, t)
        for r in self.of("then"):
            a, b = r["then"]
            if a != name:
                continue
            if "if_on" in r and not self.is_on(r["if_on"][0], target, t, r["if_on"][1]):
                continue
            self.primed[b] = t + r.get("window", WINDOW)
        self.primed.pop(name, None)
        attack = info.get("role") not in ("heal", "party_buff", "self_buff", "dispel", "defensive", "mobility")
        if crit:
            self._critical(name, t, attack)
        if attack and info.get("role") != "dot":
            self._streak(t, crit)
        for r in self.of("release"):
            skill, builders = r["release"]
            if name in builders:
                self.built = min(5, self.built + (hits or 1))
            elif name == skill:
                self.built = 0
        spent = info.get("combo_cost") or 0
        if spent:
            for r in self.of("spend_cd"):
                self.book.cut_cooldown(r["spend_cd"], spent)
        return prev

    def _critical(self, name: str, t: float, attack: bool) -> None:
        """A critical hit: the cooldowns it ends (or primes), and a stretch of shorter global
        cooldowns."""
        for r in self.of("reset"):
            a, b = r["reset"]
            if name in _names(a) or (a == "*" and attack):
                if not r.get("keep_cd"):
                    self.book.clear_cooldown(b)
                self.primed[b] = t + (WINDOW if r.get("keep_cd") else RESET_WINDOW)
        for r in self.of("fast"):
            if r["fast"][0] == name:
                self.fast_until = t + r["fast"][1]

    def _streak(self, t: float, crit: bool) -> None:
        """Critical hits in a row: enough of them prime the streak's skills."""
        self.streak = self.streak + 1 if crit else 0
        for r in self.of("streak"):
            if self.streak >= r["streak"][0]:
                for b in r["streak"][1]:
                    self.primed[b] = t + RESET_WINDOW
                self.streak = 0

    def is_on(self, skill: str, target: int | None, now: float, lasts: float) -> bool:
        t = self.book.landed.get((skill, target))
        return t is not None and now - t < lasts

    # ---------------------------------------------------------------- timing
    def gcd_factor(self, now: float) -> float:
        """The global cooldown runs this much of its usual length now."""
        f = 1.0
        if self.last:
            for r in self.of("gcd"):
                if r["gcd"][0] == self.last[0]:
                    f = min(f, r["gcd"][1])
        if now < self.fast_until:
            f = min(f, min(r["fast"][2] for r in self.of("fast")))
        return f

    def odd_gap(self, prev_name: str, prev_t: float) -> bool:
        """A gap after a skill that shortens the global cooldown says nothing about its usual
        length: leave it out of the measuring."""
        return (any(r["gcd"][0] == prev_name for r in self.of("gcd"))
                or prev_t < self.fast_until)

    def cd_less(self, name: str) -> float:
        return sum(r["cd_less"][1] for r in self.of("cd_less") if r["cd_less"][0] == name)

    def weight(self, s: Skill) -> float:
        return 1.0 + self.bonus.get(s["name"], 0) / 100.0

    # ---------------------------------------------------------------- choosing
    def pick(self, skills: Iterable[Skill], target: dict[str, Any], now: float, in_range: Callable[[Skill], bool],
             elements: int | None) -> Skill | None:
        """The skill a combo wants pressed now, from skills (ready, affordable, on our keys), or
        None. target = the monster we fight; elements = fire elements the T key reported."""
        have = {s["name"]: s for s in skills if in_range(s)}
        tid = target.get("id")

        def ok(name: str) -> Skill | None:
            return have.get(name)

        # something a cast just set up: frozen / condensed targets, cooldowns a critical hit ended
        for name, until in sorted(self.primed.items(), key=lambda kv: -kv[1]):
            if until < now:
                self.primed.pop(name, None)
            elif ok(name):
                return ok(name)
        for r in self.of("release"):
            skill, _builders = r["release"]
            n = elements if elements is not None else self.built
            if n >= RELEASE_AT and ok(skill):
                return ok(skill)
        for r in self.of("while_on"):
            skill, debuff = r["while_on"]
            if not ok(skill) or not self.is_on(debuff, tid, now, r.get("lasts", 15.0)):
                continue
            if r.get("once") and self.book.landed.get((skill, tid), 0) >= self.book.landed[(debuff, tid)]:
                continue              # already cast into this one
            if r.get("lance") and not self.book.ready_by(r["lance"], now + 2.0 * self.book.gcd() + 1.0):
                continue              # the skill it sets up couldn't follow in time
            return ok(skill)
        for r in self.of("keep"):
            skill = r["keep"]
            if ok(skill) and not self.is_on(skill, tid, now, r.get("lasts", 0) - ON_MARGIN):
                return ok(skill)
        return None

    def before(self, choice: Skill | None, skills: Iterable[Skill], in_range: Callable[[Skill], bool]) -> Skill | None:
        """Swap in the skill that should go just before the chosen one."""
        if not choice:
            return choice
        for r in self.of("before"):
            first, bigs = r["before"]
            if choice["name"] in bigs:
                f = next((s for s in skills if s["name"] == first and in_range(s)), None)
                if f:
                    return f
        return choice

    def filler(self, choice: Skill | None, skills: Iterable[Skill], in_range: Callable[[Skill], bool]) -> Skill | None:
        """A filler in place of the base attack."""
        if not choice or choice.get("role") != "base_attack":
            return choice
        for r in self.of("filler"):
            f = next((s for s in skills if s["name"] == r["filler"] and in_range(s)), None)
            if f:
                return f
        return choice

    def tell(self, log: Callable[[str], object]) -> None:
        if self.rules:
            log(_("combos: {list}", list="; ".join(self.lines())))
        if self.missing_crit:
            log(_("combos that start on a critical hit wait for the new agent: {list}",
                  list="; ".join(describe(r) for r in self.missing_crit)))
