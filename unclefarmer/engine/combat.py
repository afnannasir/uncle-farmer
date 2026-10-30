"""Picking the skill to press, from what the game says about the character's own skills.

Sources, all the game's own:
  - `mem skills` (agent v6+): the learned combat skills and which attack key holds which skill
    (world.ak: key index 1-10 = page one, 11-20 = page two, Caps Lock flips the page).
  - `mem ui` attack_page (agent v7+): which page is up now (Win.y; 0 = keys 1-10).
  - data/skills.json (from the game's skill texts and help pages): each
    skill's class, role, range, cooldown, targets and the condition that makes it worth pressing.
  - config/input.ini: which keyboard key each attack slot is (W_ATTACK1..10).

The client keeps no cooldowns for combat skills, so we time them from our own presses with the
cooldown in the skill text. Pressing a skill that isn't ready only costs one key press.

Rules (help pages, 2 Basic Rules of the Game): bosses and city guards ignore control skills;
same-kind buffs replace each other; repeated control of one type makes the target immune for 15 s.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any, TYPE_CHECKING
import math
import os
import re

from .. import gamekeys, jsonio as json, paths
from ..clock import clock_for
from ..i18n import _
from . import combos as combo_rules

if TYPE_CHECKING:
    from .botparts.parts import Parts

# A skill: its record from data/skills.json (name, role, range, cd, text...) with the key it sits on
# ("key", "still", "slot", "page") and the player's choice for it ("use").
Skill = dict[str, Any]
Press = tuple[str, str]          # (key, skill name) to press

DEFAULT_KEYS = gamekeys.ATTACK_DEFAULTS
PACK_RADIUS = 8.0        # monsters this close to the target count as one pack
# What an area skill hits, by the shape its own text gives (every class's, data/skills.json):
#   an arc in front, aimed at the target: "4 targets within 20 yards and 90 degrees" (Multi Shot,
#     Ice Storm, Ice Orb, Icicle, Fire Surge, Blazing Storm, Holy Light Wave, Fan of Knives);
#   straight ahead: "3 enemies within 5 yards ahead" (Sweep Attack, Flesh Cleave, Rampage Hack),
#     counted as a 90-degree arc;
#   a ring round us: "4 enemies within a 5-yard radius" (Whirlwind Slash, Blade Fury Dance, Frost
#     Nova, Divine Nova "around you", Dead Toxin spreading out on the ground);
#   an area where it lands, round the target: Blizzard ("in an area of 10-yard radius"), Skeleton
#     Explosion ("additional targets within an 8-yard radius").
# Counted only round the target, the arcs were under-counted: the Hunter's Multi Shot went off 37
# times in a 15-minute Scorpion Cave (one-hit kills) where ~150 were possible.
CONE = re.compile(r"within (\d+(?:\.\d+)?) yards and (\d+) degrees", re.I)
AHEAD = re.compile(r"(\d+(?:\.\d+)?) yards ahead", re.I)
RADIUS = re.compile(r"(\d+(?:\.\d+)?)-yard radius", re.I)
AT_TARGET = re.compile(r"in an area of|on contact|additional targets", re.I)
ADDITIONAL = re.compile(r"up to (\d+) additional targets", re.I)   # Skeleton Explosion: 1 + 5
AHEAD_DEGREES = 90.0     # "yards ahead": the arc a melee sweep covers
CONE_SLACK = 5.0         # degrees past the arc's edge still counted: positions are a step behind
LEAD = 0.1               # press this much before the cooldown ends: the press still travels to the server
RETRY = 0.3              # a press the game didn't cast (sent too early, lost) is pressed again this soon
BURST_EARLY = 0.15       # with press_burst, start pressing this long before the cooldown should end
BURST_GAP = 0.05         # ... and press again this often until the game casts it
SLOT_GAP = 0.08          # the same through agent 45's `slot`: a key's 30 ms hold plus BURST_GAP, so the
                         # server gets no more cast requests (OUT op=15, one per press) than from keys
BURST_MAX = 1.2          # ... for at most this long (out of range, no target: the cast never comes)
LONG_BUFF = 60.0         # a buff lasting this long (or longer) is kept up all the time
BUFF_RETRY = 20.0        # a buff press the game didn't cast (no rage, no combos yet) waits this long
POINTS_FRESH = 6.0       # a T-key reading (rage, marks, combos) older than this isn't trusted
MARK_SHOTS = ("Marked Shot", "Burst Shot")   # hit every marked target; the marks go
REFUSED_WAIT = 3.0       # a skill the game wouldn't cast for a whole burst is left this long...
REFUSED_WAIT_RAGE = 8.0  # ... or this long when it spends rage or combos (they take several hits to build)
LONG_FIGHT_HP = 3.0      # a target with this many times our max HP is a long fight
MELEE = {"Warrior", "Orc", "Assassin"}
# How far inside its base attack's range a class fights: at the very edge a monster stepping back
# makes the cast "out of range". It was 2 tiles (a Hunter shot from 18 of its 20) and the player
# wants the class's own distance: half a tile is that, without the edge.
REACH_MARGIN = 0.5


def base_reach(skill_range: float) -> float:
    """The distance a class fights from, given its base attack's range: just inside it, and never
    under 4 (a melee class's 5 would otherwise put it on the monster's own tile)."""
    return max(4.0, float(skill_range) - REACH_MARGIN)
def damage_rate(s: Skill, weight: float, gcd: float) -> float:
    """Attack per global cooldown a press takes: a channel is that much longer, so a 4.5 s
    Pyroblast is no filler beside an instant Fireball of the same attack."""
    return (s.get("attack") or 0) * weight / (1 + (s.get("cast") or 0) / gcd)


# roles never pressed by the farming bot (solo): they need a party, a place, or a moment we can't see
NEVER = {"teleport", "resurrect", "party_buff", "taunt", "heal", "dispel", "anti_heal", "reactive"}
# Roles whose cast hits nothing: the no-damage count ignores them, and one the game wouldn't cast
# says nothing about whether the monster can be hit.
CALM_ROLES = ("heal", "party_buff", "self_buff", "dispel", "defensive", "mobility")
# What the player may say about each skill (profile skill_use): the bot decides; never; only when HP
# is low; whenever it is ready in a fight, ahead of the bot's own choice.
SKILL_USES = ("auto", "never", "emergency", "ready")
# Roles a "ready" skill still isn't pressed for: they move us or need a dead teammate.
NEVER_READY = {"teleport", "resurrect"}
# Roles aimed at ourselves: pressed with the stationary key, so they never walk us anywhere.
SELF_ROLES = ("heal", "party_buff", "self_buff", "dispel", "defensive")
_know: dict[str, list[Skill]] | None = None
_talents: dict[str, str] | None = None


def is_foe(e: dict[str, Any]) -> bool:
    """A living monster: the game's own kind when the agent reports it (v22: NPCs, critters, guards
    and others' summons are not monsters), else a negative id with an ordinary monster's HP."""
    if "side" in e:
        return e.get("kind") == "monster" and (e.get("hp") or 0) > 0
    return e["id"] < 0 and 0 < (e.get("hp") or 0) < 20000000


def hp_pct(me: dict[str, Any]) -> float:
    """HP as a percentage of max HP, from a `mem self` / snapshot record."""
    return 100.0 * (me.get("hp") or 0) / max(1, me.get("maxhp") or 1)


def knowledge() -> dict[str, list[Skill]]:
    """{class: [its skills]}, from data/skills.json."""
    global _know
    if _know is None:
        know: dict[str, list[Skill]]
        try:
            with open(os.path.join(paths.DATA, "skills.json"), encoding="utf-8") as f:
                know = json.load(f)["classes"]
        except (OSError, ValueError, KeyError):
            know = {}
        _know = know
    return _know


def talent_texts() -> dict[str, str]:
    """{talent name: its text} for every class, from data/skills.json."""
    global _talents
    if _talents is None:
        texts: dict[str, str]
        try:
            with open(os.path.join(paths.DATA, "skills.json"), encoding="utf-8") as f:
                talents: dict[str, list[dict[str, Any]]] = json.load(f)["talents"]
            texts = {t["name"]: t.get("text", "") for rows in talents.values() for t in rows}
        except (OSError, ValueError, KeyError):
            texts = {}
        _talents = texts
    return _talents


class _PickView:
    """What one pick weighs, worked out once: the target's distance, our HP, the pack round the
    target and the monsters on top of us, and the skills' scores."""

    def __init__(self, book: SkillBook, snap: dict[str, Any], target: dict[str, Any], skills: list[Skill], up: int,
                 now: float) -> None:
        me = snap["self"]
        self.book, self.target, self.skills, self.up, self.now = book, target, skills, up, now
        self.dist = math.hypot(target["x"] - me["x"], target["y"] - me["y"])
        self.hp_share = hp_pct(me)
        # monsters only: drops come in as nameless 1-HP "monsters" (eight lay round the character
        # in turkey cave) and our own traps carry a skill's name
        from .bot import skill_things
        foes: list[dict[str, Any]] = [e for e in snap["entities"] if is_foe(e)
                and (e.get("maxhp") or 0) > 1 and (e.get("name") or "").strip()
                and e["name"].strip().lower() not in skill_things()]
        self.me, self.foes = me, foes
        self.pack = sum(1 for e in foes if math.hypot(e["x"] - target["x"], e["y"] - target["y"]) <= PACK_RADIUS)
        self.near_me = sum(1 for e in foes if math.hypot(e["x"] - me["x"], e["y"] - me["y"]) <= 3.0)
        self.big = (target.get("maxhp") or target.get("hp") or 0) >= LONG_FIGHT_HP * max(1, me["maxhp"])
        self.weight: Callable[[Skill], float] = book.combos.weight if book.combos else (lambda s: 1.0)
        self.gcd = book.gcd()

    def in_range(self, s: Skill) -> bool:
        r = s.get("range")
        return r is None or self.dist <= r + 0.5

    def area_hits(self, s: Skill) -> int:
        """How many monsters an area skill would hit now, by its shape (see CONE): an arc towards
        the target, a ring round us, or an area round the target. A skill whose text gives no
        shape counts the pack round the target, as before. At most its number of targets."""
        text = s.get("text", "")
        n: int | None = None
        cone, ahead, ring = CONE.search(text), AHEAD.search(text), RADIUS.search(text)
        if cone:
            n = self._in_arc(float(cone.group(1)), float(cone.group(2)))
        elif ahead:
            n = self._in_arc(float(ahead.group(1)), AHEAD_DEGREES)
        elif ring and AT_TARGET.search(text):
            n = self._round(self.target, float(ring.group(1)))
        elif ring:
            n = self._round(self.me, float(ring.group(1)))
        if n is None:
            return self.pack
        more = ADDITIONAL.search(text)
        most = 1 + int(more.group(1)) if more else int(s.get("targets") or n)
        return min(n, most)

    def _in_arc(self, reach: float, degrees: float) -> int:
        """Monsters within reach of us and inside the arc of that many degrees aimed at the target."""
        reach, half = reach + 0.5, degrees / 2 + CONE_SLACK
        mx, my = self.me["x"], self.me["y"]
        aim = math.atan2(self.target["y"] - my, self.target["x"] - mx)
        n = 0
        for e in self.foes:
            d = math.hypot(e["x"] - mx, e["y"] - my)
            if d > reach:
                continue
            off = abs((math.degrees(math.atan2(e["y"] - my, e["x"] - mx) - aim) + 180) % 360 - 180)
            if d < 0.5 or off <= half:
                n += 1
        return n

    def _round(self, centre: dict[str, Any], radius: float) -> int:
        """Monsters within radius of centre (us, or the target)."""
        return sum(1 for e in self.foes if math.hypot(e["x"] - centre["x"], e["y"] - centre["y"]) <= radius + 0.5)

    def score(self, s: Skill) -> float:
        """Attack per global cooldown its press takes, a channel counting longer (damage_rate)."""
        return damage_rate(s, self.weight(s), self.gcd)

    def first(self, role: str | tuple[str, ...], cond: Callable[[Skill], bool] = lambda s: True) -> Skill | None:
        """The hardest hitter of these roles."""
        roles = role if isinstance(role, tuple) else (role,)
        c = [s for s in self.skills if s.get("role") in roles and self.in_range(s) and cond(s)]
        return max(c, key=lambda s: (self.score(s), s.get("bonus_pct") or 0, s["page"] == self.up), default=None)

    def running(self, s: Skill) -> bool:
        """Our damage over time from this skill is still on the target (its text's "for N
        seconds"): casting it again only restarts it. Mind Whip has no cooldown at all."""
        d = self.book.duration(s)
        t = self.book.landed.get((s["name"], self.target.get("id")))
        return d is not None and t is not None and self.now - t < d - 1.5


class SkillBook:
    """The character's skills on keys, with cooldown timing and a pick() for each moment."""

    pts: int | None = None   # see __init__: the last T-key reading of rage / marks / combos
    pts_at = 0.0
    pts_delta = 0
    combos: combo_rules.Combos | None = None     # see __init__
    last_gcd_cast: tuple[str, float] | None = None
    target_id: int | None = None
    on_the_move = False      # picking for a shot on the way: instant skills only (move_shot)
    cls: str | None = None                       # the character's class, from its learned skills
    slots: dict[int, list[Skill]]                # page -> the skills on its keys
    agent_version = 0
    me_id: int | None = None
    want_follow = False
    alt = False
    trips: list[float]
    casts_done = 0
    _page = 0
    _page_at = 0.0
    _uses_points: bool
    _healer: Skill | None

    def __init__(self, bot: Parts) -> None:
        self.b = bot
        self.ok = False
        self.used: dict[str, float] = {}          # skill name -> last press time
        self.marked_at = 0.0    # last time we pressed a skill that can mark (Hunter)
        self.can_flip = True    # Caps Lock reaches the game (agent v8 knows the key)
        # Casts the game confirmed (agent v9 `casts`: IN op=41 "caster,seq,Level N Skill"). Live
        # 2026-10-04: one cast per ~1.2 s while the bot pressed every 0.35 s, so a press is not a
        # cast. With casts we time cooldowns from the real cast and wait out the global cooldown.
        self.confirm = False
        self.cast_seen = 0       # agent clock (ms) of the newest cast read
        self.last_cast = 0.0     # clock_for(self.b).time() of our newest confirmed cast inside the global cooldown
        self.last_gcd_cast = None   # (name, time) of that cast
        self.pending_target: dict[str, int | None] = {}    # skill name -> the target id it was pressed at
        self.landed: dict[tuple[str, int | None], float] = {}     # (skill name, target id) -> when our cast landed there
        self.downed: dict[int, float] = {}       # monster id -> when a cast of ours left it at 0 HP
        self.target_id = None
        self.combos = None
        self.gaps: list[float] = []           # seconds between our consecutive casts -> the global cooldown
        self.pending: dict[str, float] = {}   # skill name -> press time, until a cast confirms it or it lapses
        self.last_press = 0.0
        self.press_confirmed = True   # the game cast something since our last press
        self.buff_tried: dict[str, float] = {}     # buff name -> last press, so one the game won't cast isn't spammed
        self.dispel_try: tuple[float, int] | None = None   # (press time, debuffs before) of the last dispel
        self.no_dispel: int | None = None    # a debuff count our dispel couldn't lower: not tried again at that count
        self.refused: dict[str, float] = {}  # skill name -> not offered again until (the game wouldn't cast it)
        # Accumulation points from the T key (agent v22 `points`): rage, marked targets, combos.
        # Between two readings our own confirmed casts move them (rage gained or spent, marks used).
        self.pts = None          # the last reading
        self.pts_at = 0.0        # when the game answered it (clock_for(self.b).time())
        self.pts_delta = 0       # what our casts since then added (rage) or took away
        self.load()

    def load(self) -> None:
        try:
            self.agent_version = int(self.b.a.send("version").split()[1])
        except (ValueError, IndexError):
            self.agent_version = 0
        self.confirm = self.agent_version >= 9
        self.combos = None
        self.me_id = None
        sk = self.b.a.json("mem skills")
        if not json.is_object(sk) or "combat" not in sk:
            return
        names: dict[int, str] = {r["id"]: r["name"] for r in sk["combat"]}
        # the class is the one whose skill list holds most of ours
        know = knowledge()
        learned = set(names.values())
        self.cls = max(know, key=lambda c: len(learned & {s["name"] for s in know[c]}), default=None)
        info: dict[str, Skill] = {s["name"]: s for s in know.get(self.cls or "", [])}
        keys = gamekeys.attack_key_list()
        self.slots = {}          # page -> [skill dict with "key"]
        attack_keys: dict[str, int] = sk["attack_keys"]
        for idx, sid in attack_keys.items():
            i = int(idx)
            name = names.get(sid)
            if not name or not 1 <= i <= 20:
                continue
            page, n = divmod(i - 1, 10)
            s = dict(info.get(name, {"name": name, "role": "single_target"}))
            s["key"] = keys[n]
            s["slot"] = i
            s["use"] = self.use_of(name)
            have = [x for x in self.slots.setdefault(page, []) if x["name"] == name]
            if not have:
                self.slots[page].append(s)
        self.ok = bool(self.slots)
        # Follow attack (walks to the target) or stationary attack: the plain attack key does one,
        # Alt + key the other, and the F1 option "swap hot keys for follow enemy attack and
        # stationary attack" (world.p bit 1024, agent v10 `mem modes`) decides which.
        # Ranged players tick it so plain keys shoot standing still; melee players untick it so
        # plain keys walk them in. We read it and press whatever gives the wanted behaviour.
        style = self.b.p.get("attack_style", "auto")
        want_follow = (self.cls in MELEE) if style == "auto" else (style == "follow")
        self.want_follow = want_follow    # the attack keys walk us to the target (fighting.follows)
        modes = self.b.a.json("mem modes")
        swap = modes.get("swap_attack") if json.is_object(modes) else None
        self.alt = swap is not None and want_follow == bool(swap)   # plain key does the other one
        for pg in self.slots.values():
            for x in pg:
                # heals, buffs and dispels never walk us anywhere: always the stationary key
                x["still"] = x["key"] if swap is None or swap else "alt+" + x["key"]
                if self.alt:
                    x["key"] = "alt+" + x["key"]
        self.tell_missing(learned)
        if self.ok:
            on = ", ".join("%s=%s" % (s["key"], s["name"]) for s in self.slots.get(0, []))
            self.b.log(_("{cls} skills on keys: {keys}", cls=self.cls or "?", keys=on))
            self.b.log(_("attacks {how}", how=_("walk to the enemy") if want_follow else _("stand still")))
            if self.b.p.get("use_combos", True):
                # combos only build on skills the player lets the bot press
                on_keys = {x["name"] for row in self.slots.values() for x in row if x.get("use") != "never"}
                c = combo_rules.Combos(self, combo_rules.active_talents(sk), on_keys,
                                       crits=self.agent_version >= combo_rules.CRIT_AGENT)
                c.tell(self.b.log)
                self.combos = c if c.rules or c.bonus else None

    def use_of(self, name: str) -> str:
        """The player's choice for this skill (SKILL_USES): skill_use by name, else
        skill_use_others for a skill the list doesn't name."""
        p = self.b.p
        uses: dict[str, str] = p.get("skill_use") or {}
        use = uses.get(name) or p.get("skill_use_others", "auto")
        return use if use in SKILL_USES else "auto"

    @staticmethod
    def allowed(x: Skill, urgent: bool = False) -> bool:
        """The player lets the bot press it now: not "never", and "emergency" ones only when urgent."""
        use = x.get("use", "auto")
        return use != "never" and (use != "emergency" or urgent)

    def low_hp(self, me: dict[str, Any]) -> bool:
        """HP is low enough for the skills kept for emergencies."""
        p = self.b.p
        return hp_pct(me) < max(p.get("emergency_below", 50), p.get("defend_below", 40))

    def follow_key(self) -> str | None:
        """The key that walks us to the target and hits it there: the base attack (the hardest
        hitter on the page that is up if there are several) on its follow key. None without one."""
        up = self.page()
        base = [x for pg in sorted(self.slots, key=lambda pg: pg != up) for x in self.slots[pg]
                if x.get("role") == "base_attack" and self.allowed(x)]
        return base[0]["key"] if base and base[0] in self.slots.get(up, []) else None

    def page(self) -> int:
        now = clock_for(self.b).time()
        if now - self._page_at < 0.5:
            return self._page
        u: Any = self.b.ui()
        p = u.get("attack_page", 0) if u else 0
        self._page, self._page_at = (0 if p not in (0, 1) else p), now
        return self._page

    def flip_to(self, want: int) -> bool:
        """Caps Lock (W_ATTACK_PAGETURN) until the game shows page want. False if it can't."""
        if not self.can_flip:
            return False
        for _i in range(2):
            if self.page() == want:
                return True
            self.b.wait_keys_free()
            self._page_at = 0           # the page is about to change: read it again
            self.b.note_page_turn()
            r = self.b.a.send("key capslock 60")
            if not r.startswith("tapped"):
                self.can_flip = False   # an agent older than v8: no Caps Lock key
                self.b.log(_("can't switch the skill page yet ({why}); using the page that is up", why=r))
                return False
            self.b.sleep(0.15)
        return self.page() == want

    @staticmethod
    def cd_group(s: Skill) -> str:
        """Skills whose text says "share CD with X" cool down together with X."""
        m = re.search(r"share[sd]? CD with ([A-Z][\w' ]+?)(?:[,.;]|$)", s.get("text", ""), re.I)
        return m.group(1).strip() if m else s["name"]

    def ready(self, s: Skill, now: float) -> bool:
        cd = s.get("cd") or 0
        combos = getattr(self, "combos", None)
        if combos:
            cd = max(0.0, cd - combos.cd_less(s["name"]))
        refused: dict[str, float] = getattr(self, "refused", {})     # a book a test made without __init__
        if refused.get(s["name"], 0) > now:
            return False
        if now - self.pending.get(s["name"], 0) < 1.5 + (s.get("cast") or 0):
            return False          # pressed a moment ago; its cast may still be on the way
        return now - self.used.get(self.cd_group(s), 0) >= cd + 0.3

    def skill(self, name: str) -> Skill | None:
        slots: dict[int, list[Skill]] = getattr(self, "slots", {})     # none before load() read the keys
        return next((x for row in slots.values() for x in row if x["name"] == name), None)

    def ready_by(self, name: str, at: float) -> bool:
        """The skill on our keys will be ready by then (ignoring a press still on its way)."""
        x = self.skill(name)
        if x is None or x.get("use") == "never":
            return False
        combos: combo_rules.Combos | None = getattr(self, "combos", None)
        cd = max(0.0, (x.get("cd") or 0) - (combos.cd_less(name) if combos else 0))
        return at - self.used.get(self.cd_group(x), 0) >= cd + 0.3

    def clear_cooldown(self, name: str) -> None:
        """The game ended this skill's cooldown early (a talent's critical-hit reset)."""
        x = self.skill(name) or {"name": name}
        self.used.pop(name, None)
        self.used.pop(self.cd_group(x), None)
        refused: dict[str, float] = getattr(self, "refused", {})
        refused.pop(name, None)

    def cut_cooldown(self, name: str, seconds: float) -> None:
        """The game took seconds off this skill's cooldown."""
        x = self.skill(name) or {"name": name}
        for k in {name, self.cd_group(x)}:
            if k in self.used:
                self.used[k] -= seconds

    def burst_time(self, name: str) -> float:
        """How long to keep pressing for a cast: a channelled skill's report comes when the
        channel ends (Ice Arrow's arrives 1.5 s after the press), so it gets that much longer."""
        x: dict[str, Any] = self.skill(name) or {}
        return BURST_MAX + (x.get("cast") or 0)

    @staticmethod
    def cost(x: Skill) -> int:
        """The rage or combos a skill spends (skills.json), 0 for the rest."""
        return x.get("rage_cost") or x.get("combo_cost") or 0

    def uses_points(self) -> bool:
        """True when this character's keys hold skills that need points: rage or combo spenders, or
        a Hunter's Marked Shot / Burst Shot. Only then is the T key worth pressing."""
        if not hasattr(self, "_uses_points"):
            self._uses_points = any(self.cost(x) or x["name"] in MARK_SHOTS
                                    for row in getattr(self, "slots", {}).values() for x in row)
            combos: combo_rules.Combos | None = getattr(self, "combos", None)
            if combos and combos.of("release"):
                self._uses_points = True      # fire elements for Ignition
        return self._uses_points

    def take_points(self, me: dict[str, Any]) -> None:
        """Fold a new T-key answer from `mem self` in."""
        p: dict[str, Any] = me.get("points") or {}
        if "n" not in p:
            return
        at = clock_for(self.b).time() - p.get("age_ms", 0) / 1000.0
        # The same answer comes back at every look until the next one, its time worked out again each
        # time: a few tenths apart. Taken for a new one, it wiped the rage our casts spent since (an
        # Execute just cast still looked affordable). Answers come at least points_every apart.
        if abs(at - self.pts_at) > 0.5 or int(p["n"]) != self.pts:
            self.pts, self.pts_at, self.pts_delta = int(p["n"]), at, 0

    def points(self, now: float | None = None) -> int | None:
        """Rage / marks / combos now: the last reading plus what our casts did since; None when
        there is no fresh reading (no T key, an older agent): then nothing is held back for them."""
        now = now or clock_for(self.b).time()
        if self.pts is None or now - self.pts_at > POINTS_FRESH:
            return None
        return max(0, self.pts + self.pts_delta)

    def affordable(self, x: Skill, now: float | None = None) -> bool:
        """A skill that spends rage or combos only when we have that many. With no fresh reading
        (a fight's start: the T key isn't pressed between fights) it waits for one - the answer is
        a moment away, and a guess cost a whole refused burst each time (live: a Warrior's Battle
        Cry at every fight's start, Execute after every Cleave). Without the T key: yes."""
        need = self.cost(x)
        if not need:
            return True
        have = self.points(now)
        if have is None:
            return not self.b.p.get("points_key")
        return have >= need

    def refuse(self, name: str, now: float) -> None:
        """A whole burst of presses and the game cast nothing: most often a skill that spends rage
        or combos we don't have yet (Execute, Whirlwind Slash). Picked again at once, it would take
        every turn between two other skills, each burst a global cooldown not spent hitting.
        Leave it out of the picks for a while; the next best skill goes instead."""
        none: Skill = {}
        x = next((x for row in self.slots.values() for x in row if x["name"] == name), none)
        wait = REFUSED_WAIT_RAGE if (x.get("rage_cost") or x.get("combo_cost")) else REFUSED_WAIT
        if not hasattr(self, "refused"):
            self.refused = {}
        self.refused[name] = now + wait

    def gcd(self) -> float:
        """The game's global cooldown as we see it: the shortest gaps between our own consecutive
        casts (pauses and our own waiting only make gaps longer, never shorter, so the low end is
        the game's real limit; the 2nd shortest guards against one odd reading)."""
        g = sorted(x for x in self.gaps if 0.3 < x < 5)
        return g[1] if len(g) >= 4 else 1.0

    def read_casts(self, me_id: int | None) -> None:
        """Fold the game's confirmed casts into cooldown timing."""
        if not self.confirm:
            return
        r = self.b.a.json("casts %d" % self.cast_seen)
        if not json.is_object(r):
            return
        casts: list[dict[str, Any]] = r.get("casts", [])
        for c in casts:
            self.cast_seen = max(self.cast_seen, c["t"])
            if c["caster"] == me_id:
                self._fold_cast(c, me_id)

    def _fold_cast(self, c: dict[str, Any], me_id: int | None) -> None:
        """One cast of ours: its timing, whom it hit, the points it spent or made, its cooldown."""
        name = re.sub(r"^Level \d+ ", "", c["skill"]).strip()
        t = c["t"] / 1000.0
        info: Skill = next((x for pg in self.slots.values() for x in pg if x["name"] == name), {"name": name})
        self._time_cast(name, t, info)
        self.pending.pop(name, None)
        target = self.pending_target.pop(name, None)
        # agent 44: whom the cast struck and their HP after it - a 0 there is a kill the fight can
        # end on now, without waiting for its next look at the world
        struck: list[list[Any]] = c.get("targets") or []
        downed: dict[int, float] = self.__dict__.setdefault("downed", {})    # (a test's book made without __init__)
        for row in struck:
            if len(row) >= 2 and isinstance(row[0], int) and row[0] < 0 and row[1] == 0:
                downed[row[0]] = t
        if len(downed) > 200:
            self.downed = dict(sorted(downed.items(), key=lambda kv: kv[1])[-100:])
        # hit results (agent v28): [target, result, amount]; result & 7 == 5 is a critical hit
        raw: list[list[int]] = c.get("hits", [])
        hits = [h for h in raw if len(h) >= 2 and h[0] != me_id]
        crit = any(h[1] & 7 == combo_rules.CRIT for h in hits)
        touched = sum(1 for h in hits if h[1] & 7 != 2) if "hits" in c else None   # 2 = dodged
        if target is None and hits:
            target = hits[0][0]
        if target is not None:
            self.landed[(name, target)] = t
            if len(self.landed) > 200:      # targets long dead
                for k in sorted(self.landed, key=lambda k: self.landed[k])[:100]:
                    del self.landed[k]
        self._count_points(name, t, info, touched)
        self.used[name] = t
        if info.get("role") not in CALM_ROLES:
            self.casts_done += 1      # attacks: the no-damage check counts these
        self.used[self.cd_group(info)] = t
        if self.combos:
            # after the cooldown starts: a critical hit may end it again (Wisdom: Divine Nova)
            self.combos.cast(name, t, target, crit, touched, info)

    def _time_cast(self, name: str, t: float, info: Skill) -> None:
        """The global cooldown measured from the gaps between our casts, and the press confirmed."""
        prev = self.last_gcd_cast
        if not info.get("no_gcd"):
            # a skill outside the global cooldown (Ignition) neither starts nor measures it
            if prev and not (self.combos and self.combos.odd_gap(*prev)):
                self.gaps = (self.gaps + [t - prev[1]])[-30:]
            self.last_gcd_cast = (name, t)
            self.last_cast = t
        if t >= self.last_press - 0.05:
            if not self.press_confirmed and t - self.last_press < 1.0:
                # press to cast report: a trip there and back, so the next press can go early by it
                trips: list[float] = getattr(self, "trips", [])
                self.trips = (trips + [t - self.last_press])[-15:]
            self.press_confirmed = True

    def _count_points(self, name: str, t: float, info: Skill, touched: int | None) -> None:
        """Rage, marks or fire elements the cast spent or made since the last reading."""
        releases: list[tuple[str, list[str]]] = [rule["release"] for rule in self.combos.of("release")] if self.combos else []
        if self.pts is not None and t > self.pts_at:
            if name in MARK_SHOTS or any(name == r[0] for r in releases):
                self.pts_delta = -self.pts          # "the marks disappear after the attack"
            elif any(name in r[1] for r in releases):
                self.pts_delta = min(5 - self.pts, self.pts_delta + (touched or 1))   # fire elements
            else:
                self.pts_delta += int(info.get("rage_gain") or 0) - int(self.cost(info) or 0)

    @staticmethod
    def debuff_worth(s: Skill, hp_share: float) -> int:
        """Debuffs that make the target take more damage always pay on a long fight; one that only
        blunts the target's attack (accuracy) pays when it is hurting us; the rest (more bleeding
        damage with no bleed of our own...) we leave."""
        t = s.get("text", "").lower()
        if re.search(r"more damage|more magic damage|physical defense|toughness|critical hit resist|dodge", t):
            return 2
        if "accuracy" in t and hp_share < 70:
            return 1
        return 0

    @staticmethod
    def duration(x: Skill) -> float | None:
        """How long a buff lasts, from its text ("for 2 minutes", "for 10 seconds"), or None."""
        m = re.search(r"for (\d+(?:\.\d+)?) (second|minute)", x.get("text", ""), re.I)
        if not m:
            return None
        return float(m.group(1)) * (60 if m.group(2).lower() == "minute" else 1)

    @classmethod
    def long_buff(cls, x: Skill) -> bool:
        """A buff worth keeping up all the time (Strong-Arm Aura, Battle Cry, Requiem...): a party
        or self buff of a minute or more that isn't a totem."""
        d = cls.duration(x)
        return (x.get("role") in ("party_buff", "self_buff") and d is not None and d >= LONG_BUFF
                and "totem" not in x.get("text", "").lower())

    @classmethod
    def fight_buff(cls, x: Skill) -> bool:
        """A short buff for a hard fight (Bull's Eye, Scorch, Cold Winter, Winter Horn): one that
        only helps us, so not one that also lowers our defence (Reckless)."""
        d = cls.duration(x)
        t = x.get("text", "").lower()
        return (x.get("role") in ("party_buff", "self_buff") and d is not None and d < LONG_BUFF
                and "totem" not in t and not re.search(r"reduce \d+% defen", t))

    @staticmethod
    def self_dispel(x: Skill) -> bool:
        """A dispel for one ally (Holy Light, Soul Purify, Purification, Dispel Magic)."""
        return x.get("role") == "dispel" and "from 1 ally" in x.get("text", "")

    def support_skills(self, urgent: bool = False) -> list[Skill]:
        """[(skill, page)] on our keys that heal, buff, dispel or shield us, that the player lets
        the bot press (urgent: the ones kept for emergencies too)."""
        return [dict(x, page=pg) for pg, row in self.slots.items() for x in row
                if x.get("role") in SELF_ROLES and self.allowed(x, urgent)]

    @staticmethod
    def quick_heal(x: Skill) -> bool:
        """Single target, no channelling, no cooldown: Healing Wave, Heal, Recover."""
        return x.get("role") == "heal" and x.get("targets") == 1 and not x.get("cast") and not x.get("cd")

    @staticmethod
    def big_heal(x: Skill) -> bool:
        """A heal for when it's going badly, cast at once (a channelled one is broken by every hit):
        the Sorceress' totems (Healing Fountain, Healing Tide), the Priest's Light of Miracles."""
        t = x.get("text", "").lower()
        return (x.get("role") == "heal" and not x.get("cast")
                and ("totem" in t or (x.get("targets") or 0) != 1))

    @staticmethod
    def shield(x: Skill) -> str | None:
        """A skill that takes damage off us: "reduce N% damage taken", a shield that absorbs, an
        immunity. "stops"' says what it costs: "attack" (Turtle Guardian), "move" (Ice Barrier,
        Soul of Redemption) or None (Formless, Ignore the Pain, Summon Spirit, Ice Shield...).
        Skills that only cure one effect (Elven Ascension) aren't shields."""
        t = x.get("text", "").lower()
        if x.get("role") != "defensive" or not re.search(r"reduc(e|ing) \d+% (of )?(all |physical |magic )?damage|absorbs|"
                                                          r"immune to all damage|be attacked", t):
            return None
        if "cannot move" in t or "cannot attack, move" in t or "cannot be targeted" in t:
            return "move"
        if "cannot attack" in t:
            return "attack"
        return "free"

    def emergency(self, me: dict[str, Any], now: float) -> Press | None:
        """(key, name) for a bad moment - HP under emergency_below, or a pack on us with HP going -
        else None: first a shield that doesn't stop us fighting (Formless...), then a big heal
        (a totem, Light of Miracles). Under 30% HP a shield that stops attacks is fine too
        (Turtle Guardian); one that stops us moving only as the last thing, under 25%."""
        p = self.b.p
        share = hp_pct(me)
        hitting = len(self.b.attackers()) if share < 75 else 0
        bad = share < p.get("emergency_below", 50) or (hitting >= 3 and share < 75)
        if not bad:
            return None
        mine = self.support_skills(urgent=True)
        ok_kinds = {"free"} | ({"attack"} if share < 30 else set[str]()) | ({"move"} if share < 25 else set[str]())
        shields = [x for x in mine if self.shield(x) in ok_kinds and self.ready(x, now)]
        if shields:
            x = max(shields, key=lambda x: (self.shield(x) == "free", x.get("cd") or 0))
            if x.get("no_gcd") or not self.gcd_wait(now):
                return self.press_support(x, now)
        big = [x for x in mine if self.big_heal(x) and self.ready(x, now)]
        if big and not self.gcd_wait(now):
            x = min(big, key=lambda x: x.get("cd") or 0)
            return self.press_support(x, now)
        return None

    def tell_missing(self, learned: set[str]) -> None:
        """Name, once, the class's own heal, dispel and long buffs we have learned but put on no key:
        the bot can only press what is on a key (either page, any key the player chose)."""
        on = {x["name"] for row in self.slots.values() for x in row}
        info = {x["name"]: x for x in knowledge().get(self.cls or "", [])}
        want: list[str] = []
        for name in sorted(learned - on):
            if self.use_of(name) == "never":
                continue          # the player doesn't want it used: not missing
            x: dict[str, Any] = info.get(name) or {}
            if self.quick_heal(x) or self.big_heal(x) or self.shield(x) or self.self_dispel(x) or self.long_buff(x):
                want.append(name)
        if want:
            self.b.log(_("learned but on no skill key, so never used: {skills} - put them on a key "
                         "(Ctrl+Alt+key in the skills panel)", skills=", ".join(want)), speak=True)

    def press_support(self, x: Skill, now: float) -> Press | None:
        """Get the page and record the press; the stationary key for a skill aimed at us."""
        if self.page() != x["page"] and not self.flip_to(x["page"]):
            return None
        self.pressed(x, now)
        return x.get("still") or x["key"], x["name"]

    def dispel_now(self, me: dict[str, Any], now: float) -> Press | None:
        """(key, name) of our dispel when the game says a debuff is on us (the own-HP key's answer
        carries how many), else None. If a dispel didn't lower the count, that debuff isn't one
        ours removes (poison, say): not again until the count changes."""
        if not self.b.p.get("use_dispel", True):
            return None
        v: dict[str, Any] = me.get("vitals") or {}
        if "debuffs" not in v or v.get("age_ms", 1e9) > 15000:
            return None
        n, read_at = v["debuffs"], now - v["age_ms"] / 1000.0
        if self.dispel_try and read_at > self.dispel_try[0] + 1.5:
            if n >= self.dispel_try[1] and n > 0:
                self.no_dispel = n
            self.dispel_try = None
        if n == 0:
            self.no_dispel = None     # all gone: the next debuff gets a try
        if n <= 0 or n == self.no_dispel or self.dispel_try:
            return None
        cands = [x for x in self.support_skills() if self.self_dispel(x) and self.ready(x, now)]
        if not cands:
            return None
        x = min(cands, key=lambda x: (x.get("mana_pct") or 99, x.get("cd") or 0))
        self.dispel_try = (now, n)
        return self.press_support(x, now)

    def buff_now(self, me: dict[str, Any], now: float, fight: tuple[bool, int] | None = None) -> Press | None:
        """(key, name) of a buff to put up now, else None. Long buffs are kept up all the time:
        again when their time is up, or sooner when the game's buff count says one is gone. Short
        ones only at the start of a hard fight (fight = (big target, pack size))."""
        if not self.b.p.get("use_buffs", True):
            return None
        if getattr(self.b, "following", None) and not getattr(self.b, "follow_buffs", False):
            return None           # following someone: buffs only after their `buff` message
        v: dict[str, Any] = me.get("vitals") or {}
        fresh = "buffs" in v and v.get("age_ms", 1e9) < 15000
        read_at = now - v.get("age_ms", 0) / 1000.0
        for x in self.support_skills():
            name = x["name"]
            if (not self.ready(x, now) or now - self.buff_tried.get(name, 0) < BUFF_RETRY
                    or not self.affordable(x, now)):
                continue
            last = self.used.get(name, 0)
            if not fight and self.buff_tried.get(name, 0) > last + 0.5:
                continue              # the last press wasn't cast (rage, combos): try in a fight
            if self.long_buff(x):
                up = now - last < (self.duration(x) or 0) - 3
                if up and fresh and v["buffs"] == 0 and read_at > last + 2:
                    up = False        # the game says no buff is on us: it was stripped or ran out
                if up:
                    continue
            elif self.fight_buff(x):
                if not fight or not (fight[0] or fight[1] >= 3) or now - last < (self.duration(x) or 0):
                    continue
            else:
                continue
            self.buff_tried[name] = now
            return self.press_support(x, now)
        return None

    def support(self, me: dict[str, Any], now: float, fight: tuple[bool, int] | None = None) -> Press | str | None:
        """What to press for ourselves before attacking: heal, dispel, then buffs. (key, name),
        "wait" while the global cooldown runs, or None."""
        if not self.ok:
            return None
        urgent = self.emergency(me, now)
        if urgent:
            return urgent
        heal = self.heal_now(me, now)
        if heal:
            return heal
        if not any(x.get("role") in ("dispel", "party_buff", "self_buff") for x in self.support_skills()):
            return None
        if self.gcd_wait(now):
            return "wait"
        return self.dispel_now(me, now) or self.buff_now(me, now, fight)

    def healer(self) -> Skill | None:
        """The class's own quick heal on our keys, or None: a single-target heal with no
        channelling and no cooldown (Sorceress Healing Wave, Priest Heal), the cheapest in mana.
        Farming alone with a monster selected it lands on us (the game's own casts show it), and
        at one cast per global cooldown it heals several times faster than a red potion."""
        if not hasattr(self, "_healer"):
            cands = [dict(x, page=pg) for pg, row in self.slots.items() for x in row
                     if self.quick_heal(x) and self.allowed(x, urgent=True)]
            self._healer = min(cands, key=lambda x: (x.get("mana_pct") or 99, x["page"]), default=None)
        return self._healer

    def ready_in(self, now: float) -> float:
        """Seconds until a press can be cast. Every character has its own global cooldown - the
        shortest gap between two of its casts, measured from the game's cast reports (a Warrior
        ~1.0 s, a Hunter ~1.2 s, a Sorceress ~1.5 s; buffs that speed attacks shorten it) - and it
        runs from the game's last cast, not from our key press. A press the game didn't take is
        tried again soon."""
        if not self.confirm:
            return 0.0
        if self.press_confirmed:
            early = BURST_EARLY if self.b.p.get("press_burst", True) else 0.0
            gcd = self.gcd() * (self.combos.gcd_factor(now) if self.combos else 1.0)
            return max(0.0, self.last_cast + gcd - self.lead() - early - now)
        return max(0.0, self.last_press + RETRY - now)

    def lead(self) -> float:
        """How early to press: most of the measured trip from press to cast report (the report
        arrives that late after the cast, and the press needs that long to arrive), so the press
        reaches the server as the cooldown ends - a little after, never before."""
        trips: list[float] = getattr(self, "trips", [])
        t = sorted(trips)
        if len(t) < 3:
            return LEAD
        return min(0.4, max(0.05, 0.8 * t[len(t) // 2]))

    def gcd_wait(self, now: float) -> bool:
        """True while the game's global cooldown from our last cast still runs."""
        return self.ready_in(now) > 0

    def pressed(self, choice: Skill, now: float) -> None:
        self.last_press = now
        self.press_confirmed = False
        if self.confirm:
            self.pending[choice["name"]] = now      # counted as used when the game says it cast
            self.pending_target[choice["name"]] = self.target_id
        else:
            self.used[choice["name"]] = now
            self.used[self.cd_group(choice)] = now

    def heal_now(self, me: dict[str, Any], now: float) -> Press | str | None:
        """(key, name) of our quick heal when HP is under the profile's heal_below, "wait" while
        the global cooldown runs, else None."""
        h = self.healer()
        if not h or not self.b.p.get("heal_with_skill", True):
            return None
        mark = self.b.p.get("heal_below", 85)
        if getattr(self.b, "resting", False):
            mark = max(mark, self.b.p.get("rest_until", 90) + 1)   # a rest waits for this much HP
        if hp_pct(me) >= mark or not self.allowed(h, self.low_hp(me)):
            return None
        if self.gcd_wait(now):
            return "wait"
        if not self.ready(h, now):
            return None
        return self.press_support(h, now)

    def pick(self, snap: dict[str, Any], target: dict[str, Any]) -> Press | str | None:
        """(key, skill name) to press now, or None to fall back to the profile's attack keys."""
        if not self.ok:
            return None
        now = clock_for(self.b).time()
        me = snap["self"]
        self.target_id = target.get("id")
        self.read_casts(me["id"])
        if self.gcd_wait(now):
            # A panic button outside the global cooldown (Concentrated Will, Summon Spirit) still
            # goes now: emergency() lets only those through while the cooldown runs.
            return self.emergency(me, now) or "wait"
        # 0. ourselves first: a heal while HP is down, a dispel when a debuff is on us, buffs
        tgt_big = (target.get("maxhp") or target.get("hp") or 0) >= LONG_FIGHT_HP * max(1, me["maxhp"])
        near_target = sum(1 for e in snap["entities"] if is_foe(e)
                          and math.hypot(e["x"] - target["x"], e["y"] - target["y"]) <= PACK_RADIUS)
        mine = self.support(me, now, fight=(tgt_big, near_target))
        if mine:
            return mine
        up = self.page()
        skills, wanted = self._usable(up, self.low_hp(me), now)
        if not skills and not wanted:
            return None
        v = _PickView(self, snap, target, skills, up, now)
        # 1. in trouble: a shield support() didn't already use; 2. a ranged class with a monster on
        # top of it: jump away
        choice = self._trouble_choice(v)
        # the player's own: skills set to "whenever ready" go before the bot's choices
        if not choice and wanted:
            done, answer = self._wanted_choice(v, wanted)
            if done:
                return answer
        # combos: what our last casts set up (a frozen target for Ice Lance, a cooldown a critical hit
        # ended), what a talent wants kept on the target, fire elements to release
        if not choice and self.combos:
            elements = self.points(now) if self.cls == "Elf" else None
            choice = self.combos.pick(skills, target, now, v.in_range, elements)
        marks = self.points(now) if self.cls == "Hunter" else None
        if not choice:
            choice = self._attack_choice(v, marks)
        if self.combos:
            choice = self.combos.filler(self.combos.before(choice, skills, v.in_range), skills, v.in_range)
        if not choice:
            return None
        return self._press_pick(choice, skills, up, now, v.in_range)

    def _usable(self, up: int, urgent: bool, now: float) -> tuple[list[Skill], list[Skill]]:
        """(skills the bot may pick now, the player's "whenever ready" ones), from both pages; a
        skill on both counts as on the page that is up (no flip needed for it)."""
        pages = [up] + ([1 - up] if self.can_flip else [])
        skills: list[Skill] = []
        seen: set[str] = set()
        wanted: list[Skill] = []
        for pg in pages:
            for s in self.slots.get(pg, []):
                if (s["name"] in seen or not self.allowed(s, urgent) or not self.ready(s, now)
                        or not self.affordable(s, now)):
                    continue
                if s.get("cast") and getattr(self, "on_the_move", False):
                    continue          # a channel (Pyroblast, Blazing Storm) breaks the moment we walk on
                seen.add(s["name"])
                if s.get("use") == "ready" and s.get("role") not in NEVER_READY:
                    wanted.append(dict(s, page=pg))
                if s.get("role") not in NEVER:
                    skills.append(dict(s, page=pg))
        return skills, wanted

    def _trouble_choice(self, v: _PickView) -> Skill | None:
        choice = None
        if v.hp_share < self.b.p.get("defend_below", 40):
            choice = v.first("defensive", lambda s: self.shield(s) == "free" or (self.shield(s) == "attack"
                                                                                 and v.hp_share < 30))
        if not choice and v.near_me and v.hp_share < 60 and self.cls in ("Hunter", "Mage", "Elf", "Priest", "Witcher"):
            choice = v.first("mobility", lambda s: "jump backward" in s.get("text", "").lower())
        return choice

    def _wanted_choice(self, v: _PickView, wanted: list[Skill]) -> tuple[bool, Press | None]:
        """The hardest hitter of the player's "whenever ready" skills (a buff or heal among them on
        the stationary key, at us). (True, the answer) when pick is done, (False, None) to go on."""
        mine = [s for s in wanted if s.get("role") in SELF_ROLES or v.in_range(s)]
        if not mine:
            return False, None
        pick = max(mine, key=lambda s: (v.score(s), s["page"] == v.up))
        if pick.get("role") not in SELF_ROLES:
            return True, self._press_pick(pick, v.skills, v.up, v.now, v.in_range)
        if pick["page"] == v.up or self.flip_to(pick["page"]):
            self.pressed(pick, v.now)
            return True, (pick.get("still") or pick["key"], pick["name"])
        return False, None

    def _attack_choice(self, v: _PickView, marks: int | None) -> Skill | None:
        """What to attack with when nothing above chose: a long fight's debuffs, a pack's area
        skill (a Hunter's marked shots), else the strongest single-target skill or the base attack.
        (Control skills are left out on purpose for farming: a stun costs a global cooldown that
        damage would have used, and bosses ignore it.)"""
        skills, now, up = v.skills, v.now, v.up
        choice = None
        # 3. a long fight: debuffs once per cooldown (Viper Sting, Sear, Armor Break ...)
        if v.big:
            worth = [x for x in skills if x.get("role") == "debuff" and v.in_range(x)
                     and self.debuff_worth(x, v.hp_share)]
            choice = (max(worth, key=lambda x: (self.debuff_worth(x, v.hp_share), x["page"] == up), default=None)
                      or v.first("dot", lambda s: not v.running(s)))
        # 4. a pack: the area skill that hits the most (a Hunter spends its marks with Marked Shot)
        need = getattr(self.b, "pack_min", 3)
        if not choice and v.pack >= need and self.cls == "Hunter" and (
                marks >= 2 if marks is not None else now - self.marked_at < 8):
            choice = v.first("aoe", lambda s: s["name"] in MARK_SHOTS)
        if not choice:
            area = [(v.area_hits(s), s.get("targets") or 0, s) for s in skills
                    if s.get("role") == "aoe" and v.in_range(s) and s["name"] not in MARK_SHOTS]
            best = max(area, key=lambda a: (a[0], a[1]), default=None)
            if best is not None and best[0] >= need:
                choice = best[2]
        # several targets marked (the T key counts them): one shot hits them all
        if not choice and marks is not None and marks >= 3:
            choice = v.first("aoe", lambda s: s["name"] in MARK_SHOTS)
        # 5. one target: the strongest ready single-target skill, then the base attack
        if not choice:
            # the Orc's Bloodthirsty (rage, heals 30% of its damage) is its hardest single hit
            choice = v.first(("single_target", "lifesteal")) or v.first("finisher")
            base = v.first("base_attack")
            if choice and base and v.score(choice) < v.score(base):
                choice = None         # a long channel that hits no harder than the base attack
            choice = choice or base
        return choice

    def fallback(self, now: float) -> Press | None:
        """A press when pick() found nothing: the hardest-hitting attack the player allows whose own
        cooldown is over, even one refuse() set aside for a while. Pressing the profile's attack
        keys instead would cast whatever sits on them, a skill set to Never included; pressing
        nothing let a simulated Hunter whose two skills were both set aside stand and die. None
        when there is no such skill (only Never ones, or all cooling down)."""
        up = self.page()
        weight: Callable[[Skill], float] = self.combos.weight if self.combos else (lambda s: 1.0)
        gcd = self.gcd()
        cands = [dict(s, page=pg) for pg, row in self.slots.items() for s in row
                 if self.allowed(s) and s.get("role") not in NEVER and s.get("role") not in CALM_ROLES
                 and now - self.used.get(self.cd_group(s), 0) >= (s.get("cd") or 0) + 0.3]
        if not cands:
            return None
        best = max(cands, key=lambda s: (s["page"] == up, damage_rate(s, weight(s), gcd)))
        return self._press_pick(best, cands, up, now, lambda s: True)

    def _press_pick(self, choice: Skill, skills: list[Skill], up: int, now: float,
                    in_range: Callable[[Skill], bool]) -> Press | None:
        """Get the chosen skill's page up (else the best of its kind on this page) and record the
        press: (key, name), or None."""
        if choice["page"] != up and not self.flip_to(choice["page"]):
            # can't reach it: the best of the same kind on this page, else nothing special
            same = [s for s in skills if s["page"] == up and s.get("role") == choice.get("role") and in_range(s)]
            if not same:
                return None
            # the hardest hitter of that kind there, not simply the first one on the page
            weight: Callable[[Skill], float] = self.combos.weight if self.combos else (lambda s: 1.0)
            gcd = self.gcd()
            choice = max(same, key=lambda s: damage_rate(s, weight(s), gcd))
        self.pressed(choice, now)
        if self.cls == "Hunter":
            if choice["name"] in ("Multi Shot", "Rapid Arrows"):
                self.marked_at = now      # several targets carry marks now
            elif choice["name"] in ("Marked Shot", "Burst Shot"):
                self.marked_at = 0.0      # "the marks disappear after the attack"
        return choice["key"], choice["name"]
