"""Turret firing: the daily shot of a highland's Crystal Turret, for its keeper (Tools, Daily chores).

A character can be the turret keeper ("administrator") of one or more of the gang's highlands; each
turret fires once a game day. The game, as read on 2026-11-28 by a keeper of two turrets:

  turret menu (any highland, 30,27)   "the completeness is 95.0%", the repair line, "repair", "Fire".
                                      Every member sees "Fire"; for anyone but the keeper choosing it
                                      is refused at once: "You are not the turret administer".
  "Fire"                              "Aim at <our gang> gang area" | "Aim at other highlands"
  our gang's area                     "Aim at the main mansion" | "Aim at the mining area" | "Aim at the
                                      lake" -> "<gang> lake; currently 1 people online" and a row a
                                      player there: "<name>;<id>; can be bombarded; <class>;<sex> ;
                                      level 35; Foreign player" (or "in non-shelled areas ... own gang
                                      player"). Choosing one asks "Are you sure you want to shoot?",
                                      Confirm | cancel. The shot KILLS that player: chat (channel 10)
                                      "<name> was killed by a shell from highland 67". Nobody there:
                                      nothing to choose, nothing fired.
  other highlands                     every gang, 20 a page ("Next page: 2"): "1; <gang>; leader: <name>;
                                      has 4 Gang Highlands and 13 occupied Highlands" (the number is the
                                      ranking: a gang is known by its name). A gang -> its highlands, one
                                      drop-down row each: "highland 7, the completeness of crystal turret
                                      is 99.0%; the completeness of Wall barricade is 100.0%". Choosing a
                                      row opens "Please select" | "Hit Crystal Turret" | "Hit Wall
                                      barricade"; a Hit fires at once (5 points off), no cost, and chat
                                      says "<us> shelled the crystal turret of Highland 7".
  a second shot that day              said (IN op=60) "already fired today"; nothing fired.

Nothing is said when a shot lands, so the chat line is the proof. With neither the proof nor a
refusal the turret is not marked: the next run tries again, and a turret that did fire then answers
"already fired today" (a try costs nothing).
"""

from __future__ import annotations

import re
from collections.abc import Callable, Collection, Iterable
from dataclasses import dataclass, field, replace
from typing import Any, NamedTuple, cast

from .. import jsonio
from ..clock import clock_for
from ..i18n import _
from . import chores, town
from .bot import Bot, Stopped

AIMS = ("own", "other")
AREAS = ("main mansion", "mining area", "lake")
PARTS = ("turret", "wall", "highest")
PART_NAMES = {"turret": "Crystal Turret", "wall": "Wall barricade"}
DEFAULT_LIMIT = 50

FIRE = r"^\s*Fire\s*$"
AIM_OWN = r"^\s*Aim at .+ gang area\s*$"
AIM_OTHER = r"^\s*Aim at other highlands\s*$"
AIMS_SHOWN = r"^\s*Aim at (other highlands|.+ gang area)\s*$"
GANG_HIGHLANDS = r"^\s*Gang Highlands\s*$|the completeness of crystal turret"
PEOPLE_ONLINE = r"currently\s+\d+\s+people online"
PLEASE_SELECT = r"^\s*Please select\s*$"
CONFIRM = r"^\s*Confirm\s*$"
HIGHLAND_ROW = re.compile(r"\bhighland\s+(\d+)\s*,\s*the completeness of crystal turret is\s*([\d.]+)\s*%\s*;"
                          r"\s*the completeness of Wall barricade is\s*([\d.]+)\s*%"
                          r"(?:\s*;\s*Occupied by the\s+(.+?)\s+gang\b)?", re.I)
PLAYER_ROW = re.compile(r"^\s*([^;]+?)\s*;\s*(\d+)\s*;(.*)$")
CAN_HIT = re.compile(r"\bcan be bombarded\b", re.I)
FOREIGN = re.compile(r"\bforeign player\b", re.I)
NOT_KEEPER = re.compile(r"not the turret administ", re.I)
FIRED_TODAY = re.compile(r"already fired today", re.I)

ANSWER_WAIT = 2.0             # seconds a refusal gets to be said after "Fire"
PAGE_WAIT = 3.0               # seconds the next page of the Fire menus gets to show
PROOF_WAIT = 4.0              # seconds the chat line of a shot gets to come

# What happened at a turret. FINAL ones are written down for the day; the others are tried again.
FIRED, ALREADY, NOT_KEEPER_, NOTHING, UNKNOWN, TROUBLE, CAN = (
    "fired", "already", "not keeper", "nothing", "unknown", "trouble", "can")
FINAL = (FIRED, ALREADY, NOT_KEEPER_)
AGAIN = (UNKNOWN, TROUBLE)            # worth one more try in the same run


@dataclass(frozen=True)
class Target:
    """A highland to fire at. turret: the turret it is paired with, or None (a turret can have
    several). occupied: fire at it even when another gang occupies it. Two entries for the same
    highland are the same target, whatever their pairs."""
    gang: str
    highland: int
    turret: int | None = field(default=None, compare=False)
    occupied: bool = field(default=False, compare=False)


class Reading(NamedTuple):
    """One highland of a gang's list: its parts' completeness, and the gang occupying it (or None)."""
    turret: float
    wall: float
    occupier: str | None = None


@dataclass(frozen=True)
class Player:
    name: str
    id: int
    text: str

    @property
    def can_hit(self) -> bool:
        """A foreign player the game lets be shot: never one of our gang, never in a sheltered place."""
        return bool(CAN_HIT.search(self.text) and FOREIGN.search(self.text))


@dataclass
class Settings:
    turrets: list[int]
    aim: str
    area: str
    part: str
    limit: int
    targets: list[Target]
    fallback: list[Target]
    spare: list[str]


# ---------------------------------------------------------------------------------------- settings
def _number(v: object) -> int | None:
    try:
        n = int(str(v).strip())
    except (TypeError, ValueError):
        return None
    return n if n > 0 else None


def highland_number(text: str) -> int | None:
    """A highland number the player typed, or None when it isn't one."""
    return _number(text)


def _items(raw: object) -> list[object]:
    return list(cast("list[object]", raw)) if isinstance(raw, list) else []


def numbers(raw: object) -> list[int]:
    """Highland numbers as typed or saved: whole numbers above 0, each once, in order."""
    out: list[int] = []
    for v in _items(raw):
        n = _number(v)
        if n is not None and n not in out:
            out.append(n)
    return out


def targets(raw: object, turrets: Collection[int] = ()) -> list[Target]:
    """(gang, highland) entries as saved: {"gang": ..., "highland": ..., "turret": ...}; bad ones are
    left out. A pair is kept only with one of turrets."""
    out: list[Target] = []
    for v in _items(raw):
        if not jsonio.is_object(v):
            continue
        gang, n, turret = " ".join(str(v.get("gang") or "").split()), _number(v.get("highland")), _number(v.get("turret"))
        if not gang or n is None or Target(gang, n) in out:
            continue
        if turret is not None and turret not in turrets:
            turret = None
        out.append(Target(gang, n, turret, v.get("occupied") is True))
    return out


def saved_targets(entries: Iterable[Target]) -> list[dict[str, Any]]:
    """Targets as the profile keeps them."""
    return [{"gang": t.gang, "highland": t.highland, **({"turret": t.turret} if t.turret is not None else {}),
             **({"occupied": True} if t.occupied else {})} for t in entries]


def pair(entries: list[Target], i: int, turret: int | None) -> list[Target]:
    """entries with entries[i] paired with turret (None: no pair)."""
    return [replace(t, turret=turret) if j == i else t for j, t in enumerate(entries)]


def order_for(s: Settings, number: int) -> list[tuple[Target, bool]]:
    """What turret number tries, in order, and whether the limit applies: its paired targets, the
    targets paired with no turret (both in list order), then the random gangs (no limit). Another
    turret's pair: never."""
    mine = [t for t in s.targets if t.turret == number]
    free = [t for t in s.targets if t.turret is None]
    return [(t, True) for t in mine + free] + [(t, False) for t in s.fallback]


def names(raw: object) -> list[str]:
    out: list[str] = []
    for v in _items(raw):
        name = " ".join(str(v).split())
        if name and name.casefold() not in (n.casefold() for n in out):
            out.append(name)
    return out


def settings_of(cfg: dict[str, Any]) -> Settings:
    """The firing settings of a chores config, every value checked."""
    def one_of(key: str, allowed: tuple[str, ...], default: str) -> str:
        v = cfg.get(key)
        return v if isinstance(v, str) and v in allowed else default
    limit = _number(cfg.get("fire_limit"))
    turrets = numbers(cfg.get("fire_turrets"))
    return Settings(turrets=turrets, aim=one_of("fire_aim", AIMS, "other"),
                    area=one_of("fire_area", AREAS, "lake"), part=one_of("fire_part", PARTS, "turret"),
                    limit=min(limit if limit is not None else DEFAULT_LIMIT, 100),
                    targets=targets(cfg.get("fire_targets"), turrets), fallback=targets(cfg.get("fire_fallback")),
                    spare=names(cfg.get("fire_spare")))


def wanted(cfg: dict[str, Any]) -> bool:
    """Firing is on, a turret is listed, and there is something to aim at."""
    if not cfg.get("fire"):
        return False
    s = settings_of(cfg)
    return bool(s.turrets) and (s.aim == "own" or bool(s.targets or s.fallback))


def entry_label(t: Target) -> str:
    return _("{gang}, highland {n}", gang=t.gang, n=t.highland)


def target_line(t: Target) -> str:
    """A target as the settings list shows it, with its pair and whether it is fired at when occupied."""
    line = entry_label(t)
    if t.turret is not None:
        line = _("{entry} - paired with our turret in highland {turret}", entry=line, turret=t.turret)
    if t.occupied:
        line = _("{entry} - fires even if occupied", entry=line)
    return line


def turret_line(number: int, entries: Iterable[Target]) -> str:
    """A turret as the settings list shows it, with the targets paired with it."""
    pairs = [_("{gang} highland {n}", gang=t.gang, n=t.highland) for t in entries if t.turret == number]
    if not pairs:
        return _("Highland {n}", n=number)
    return _("Highland {n} - paired with: {targets}", n=number, targets=", ".join(pairs))


# ---------------------------------------------------------------------------------------- deciding
def parse_highlands(text: str) -> dict[int, Reading]:
    """A gang's highland list: {number: Reading}. The game still lists a highland another gang has
    taken under its gang, ending in "; Occupied by the <gang> gang"."""
    return {int(m.group(1)): Reading(float(m.group(2)), float(m.group(3)), m.group(4))
            for m in HIGHLAND_ROW.finditer(text)}


def choose_part(part: str, turret: float, wall: float, limit: float | None) -> str | None:
    """"turret" or "wall" to hit, or None when the part to hit is at or below the limit (None: no limit).
    "highest" hits the higher one, the wall when both are the same (0 included)."""
    if part == "highest":
        part = "turret" if turret > wall else "wall"
    value = turret if part == "turret" else wall
    return None if limit is not None and value <= limit else part


def parse_players(texts: Iterable[str]) -> list[Player]:
    out: list[Player] = []
    for text in texts:
        m = PLAYER_ROW.match(text)
        if m and not any(p.id == int(m.group(2)) for p in out):
            out.append(Player(m.group(1), int(m.group(2)), text))
    return out


def pick_player(players: Iterable[Player], spare: Iterable[str]) -> Player | None:
    """The first foreign player who can be shot and isn't one of the names spared."""
    never = {n.casefold() for n in spare}
    return next((p for p in players if p.can_hit and p.name.casefold() not in never), None)


def answer(said: Iterable[str]) -> str | None:
    """ALREADY or NOT_KEEPER_ when the game said so, else None."""
    lines = list(said)
    if any(NOT_KEEPER.search(s) for s in lines):
        return NOT_KEEPER_
    if any(FIRED_TODAY.search(s) for s in lines):
        return ALREADY
    return None


def shelled(who: str, part: str, number: int) -> re.Pattern[str]:
    """The game's chat line of our shot at another gang's highland."""
    whom = re.escape(who) + r"\s+" if who else ""
    return re.compile(r"%sshelled the %s of Highland %d\b" % (whom, re.escape(PART_NAMES[part]), number), re.I)


def killed(player: str, turret: int) -> re.Pattern[str]:
    """The game's chat line of our shot at a player in our gang's area."""
    return re.compile(r"%s\s+was killed by a shell from highland %d\b" % (re.escape(player), turret), re.I)


# ---------------------------------------------------------------------------------------- the game
def _has(b: Bot, pattern: str) -> bool:
    d = b.dialog()
    return bool(d.get("open")) and b.find_entry(d.get("top"), pattern) is not None


def _pick(b: Bot, pattern: str) -> bool:
    """Choose the entry, when the open menu has it (b.choose would close the menu and say so)."""
    if not _has(b, pattern):
        return False
    b.choose(pattern)
    b.wait_dialog(3.0)
    return True


def _texts(b: Bot) -> list[str]:
    top: dict[str, Any] = b.dialog().get("top") or {}
    rows: list[list[Any]] = top.get("rows", [])
    return [t for row in rows for e in row if e for t in b.entry_texts(e)]


def _chat_mark(b: Bot) -> int:
    try:
        return int(b.a.json("chat 0").get("last", 0))
    except Exception:              # an agent without it: nothing heard, never a crash
        return 0


def _game_lines(b: Bot, since: int) -> list[str]:
    """The game's own chat lines (no player's name) after since."""
    try:
        lines: list[dict[str, Any]] = b.a.json("chat %d" % since).get("chat", [])
    except Exception:
        return []
    return [str(m.get("text") or "").strip() for m in lines if not m.get("name") and m.get("text")]


def _shown_or_said(b: Bot, pattern: str, said_from: int, seconds: float) -> str | None:
    """Wait until the open menu has an entry like pattern (None) or the game says a refusal
    (ALREADY / NOT_KEEPER_). Raises Stopped when neither comes within seconds."""
    clock = clock_for(b)
    end = clock.time() + seconds
    while True:
        if _has(b, pattern):
            return None
        got = answer(chores.said_since(b, said_from))
        if got is not None:
            b.close_dialogs()
            return got
        if clock.time() >= end:
            b.close_dialogs()
            raise Stopped(_("the turret's menu didn't show what was expected and the game said nothing"))
        b.sleep(0.25)


def _proof(b: Bot, said_from: int, chat_from: int, proof: re.Pattern[str]) -> str:
    """FIRED when the chat tells of the shot, ALREADY / NOT_KEEPER_ when the game refused, else UNKNOWN."""
    clock = clock_for(b)
    end = clock.time() + PROOF_WAIT
    while True:
        got = answer(chores.said_since(b, said_from))
        if got is not None:
            return got
        if any(proof.search(line) for line in _game_lines(b, chat_from)):
            return FIRED
        if clock.time() >= end:
            return UNKNOWN
        b.sleep(0.25)


def open_fire(b: Bot) -> str | None:
    """At the turret of the highland we are in, choose "Fire". None: the aim menu is open; else
    NOT_KEEPER_ or ALREADY, as the game said."""
    name, stand = chores.HIGHLAND_SITES[0]
    b.close_dialogs()
    b.walk_to(stand)
    said_from = chores.said_mark(b)
    if not b.talk(name, None, stand):
        raise Stopped(_("couldn't talk to the {npc}", npc=name))
    b.wait_dialog(3.0)
    if not _pick(b, FIRE):
        b.close_dialogs()
        raise Stopped(_("the {npc} has no Fire entry", npc=name))
    return _shown_or_said(b, AIMS_SHOWN, said_from, ANSWER_WAIT)


def _gang_highlands(b: Bot, gang: str) -> dict[int, Reading] | None:
    """With the aim menu open: the gang's highlands from "Aim at other highlands", left open on its
    list. None when no gang of that name is in the list."""
    if not _pick(b, AIM_OTHER):
        raise Stopped(_("the Fire menu has no \"Aim at other highlands\""))
    row = r"^\s*\d+\s*;\s*(?-i:%s)\s*;" % re.escape(gang)      # the gang's name, capitals and all
    for _page in range(chores.MOST_PAGES):
        _wait_shown(b, r"^\s*\d+\s*;[^;]+;\s*leader:", PAGE_WAIT)     # a page of gangs
        if _pick(b, row):
            _wait_shown(b, GANG_HIGHLANDS, PAGE_WAIT)
            return parse_highlands(town.menu_text(b))
        if not _pick(b, chores.NEXT_PAGE):
            break
    return None


def _skip(b: Bot, s: Settings, t: Target, got: dict[int, Reading] | None,
          limited: bool, told: set[Target]) -> str | None:
    """The part of t to hit, or None (said once) when it can't be hit today."""
    def say(line: str) -> None:
        if t not in told:
            told.add(t)
            b.log(line)
    if got is None:
        say(_("no gang named {gang} in the turret's list - {entry} skipped", gang=t.gang, entry=entry_label(t)))
        return None
    if t.highland not in got:
        say(_("highland {n} isn't {gang}'s any more: its owner has changed - skipped", n=t.highland, gang=t.gang))
        return None
    turret, wall, occupier = got[t.highland]
    if occupier and not t.occupied:
        say(_("highland {n} is occupied by the {occupier} gang: its owner has changed - skipped",
              n=t.highland, occupier=occupier))
        return None
    part = choose_part(s.part, turret, wall, s.limit if limited else None)
    if part is None:
        say(_("{entry} is at or below {limit}% - not fired at", entry=entry_label(t), limit=s.limit))
    return part


def _shoot_highland(b: Bot, s: Settings, number: int, check_only: bool) -> tuple[str, str]:
    """From turret number: the first that can be hit of its pair, the unpaired targets, then the
    random gangs (order_for)."""
    read: dict[str, dict[int, Reading] | None] = {}
    told: set[Target] = set()
    for t, limited in order_for(s, number):
        key = t.gang
        if key in read and _skip(b, s, t, read[key], limited, told) is None:
            continue                       # known from an earlier reading this visit: no need to open
        refused = open_fire(b)
        if refused is not None:
            return refused, _note(refused, number)
        read[key] = _gang_highlands(b, t.gang)
        part = _skip(b, s, t, read[key], limited, told)
        if part is None:
            b.close_dialogs()
            continue
        what = _("{entry} ({part})", entry=entry_label(t), part=PART_NAMES[part])
        if check_only:
            b.close_dialogs()
            return CAN, _("turret {n}: would hit {what}", n=number, what=what)
        return _hit(b, t, part, number, what)
    return NOTHING, _("turret {n}: no highland in the lists can be hit today", n=number)


def _hit(b: Bot, t: Target, part: str, number: int, what: str) -> tuple[str, str]:
    said_from, chat_from = chores.said_mark(b), _chat_mark(b)
    b.choose(r"\bhighland\s+%d\s*," % t.highland)       # opens its "Please select" list
    if not _popup(b):
        got = answer(chores.said_since(b, said_from))
        b.close_dialogs()
        if got is not None:
            return got, _note(got, number)
        raise Stopped(_("the choice of turret or wall didn't open for {entry}", entry=entry_label(t)))
    b.choose(r"^\s*Hit %s\s*$" % re.escape(PART_NAMES[part]))
    got = _proof(b, said_from, chat_from, shelled(str(getattr(b, "character", "") or ""), part, t.highland))
    b.close_dialogs()
    return got, _note(got, number, what)


def _wait_shown(b: Bot, pattern: str, seconds: float) -> bool:
    """Wait until the open menu has an entry like pattern; False when it doesn't come in time."""
    clock = clock_for(b)
    end = clock.time() + seconds
    while not _has(b, pattern):
        if clock.time() >= end:
            return False
        b.sleep(0.25)
    return True


def _popup(b: Bot) -> bool:
    """The highland row's own list (Please select, Hit ...) is open, within PAGE_WAIT."""
    clock = clock_for(b)
    end = clock.time() + PAGE_WAIT
    while True:
        if _has(b, PLEASE_SELECT) and not HIGHLAND_ROW.search(town.menu_text(b)):
            return True
        if clock.time() >= end:
            return False
        b.sleep(0.25)


def _shoot_area(b: Bot, s: Settings, number: int, check_only: bool) -> tuple[str, str]:
    """From turret number: the first foreign player in our gang's area who can be shot."""
    refused = open_fire(b)
    if refused is not None:
        return refused, _note(refused, number)
    if not _pick(b, AIM_OWN) or not _pick(b, r"^\s*Aim at the %s\s*$" % s.area):
        b.close_dialogs()
        raise Stopped(_("the Fire menu has no \"Aim at the {area}\"", area=s.area))
    _wait_shown(b, PEOPLE_ONLINE, PAGE_WAIT)
    who = pick_player(parse_players(_texts(b)), s.spare)
    if who is None:
        b.close_dialogs()
        return NOTHING, _("turret {n}: nobody at the {area} can be shot now", n=number, area=s.area)
    what = _("{player} at the {area}", player=who.name, area=s.area)
    if check_only:
        b.close_dialogs()
        return CAN, _("turret {n}: would shoot {what}", n=number, what=what)
    said_from, chat_from = chores.said_mark(b), _chat_mark(b)
    b.choose(r"^\s*%s\s*;\s*%d\s*;" % (re.escape(who.name), who.id))
    refused = _shown_or_said(b, CONFIRM, said_from, PAGE_WAIT)
    if refused is not None:
        return refused, _note(refused, number)
    if not _pick(b, CONFIRM):
        b.close_dialogs()
        raise Stopped(_("the game didn't ask to confirm the shot"))
    got = _proof(b, said_from, chat_from, killed(who.name, number))
    b.close_dialogs()
    return got, _note(got, number, what)


def _note(outcome: str, number: int, what: str = "") -> str:
    if outcome == FIRED:
        return _("turret {n}: fired at {what}", n=number, what=what)
    if outcome == ALREADY:
        return _("turret {n}: already fired today", n=number)
    if outcome == NOT_KEEPER_:
        return _("turret {n}: this character isn't its keeper", n=number)
    return _("turret {n}: shot at {what}, but the game didn't confirm it - tried again next time",
             n=number, what=what)


def fire_here(b: Bot, s: Settings, number: int, check_only: bool) -> tuple[str, str]:
    """The day's shot of turret number, the highland we are in: (outcome, what to tell)."""
    if s.aim == "own":
        return _shoot_area(b, s, number, check_only)
    return _shoot_highland(b, s, number, check_only)


# ---------------------------------------------------------------------------------------- the day
def fired_today(name: str | None) -> dict[str, str]:
    """{turret number: what happened} of the turrets written down this game day."""
    got: object = chores.done_today(name).get("_fired")
    return {str(k): str(v) for k, v in got.items()} if jsonio.is_object(got) else {}


@dataclass
class Day:
    """What this chores run did at each turret, shared by construction's highland visits and the
    firing chore, so a turret is fired at most once a run."""
    results: dict[int, str] = field(default_factory=dict[int, str])
    notes: dict[int, str] = field(default_factory=dict[int, str])

    def record(self, b: Bot, number: int, outcome: str, note: str, check_only: bool) -> None:
        self.results[number], self.notes[number] = outcome, note
        if outcome in FINAL and not check_only:
            name = chores.who(b)
            chores.put(name, "_fired", {**fired_today(name), str(number): note})

    def fire(self, b: Bot, s: Settings, number: int, check_only: bool) -> str:
        """Fire turret number from the highland we are in; a failure is told, never raised (unless
        it stops the run)."""
        try:
            outcome, note = fire_here(b, s, number, check_only)
        except Stopped as e:
            if b.stop_event.is_set() or getattr(e, "final", False):
                raise
            b.close_dialogs()
            outcome, note = TROUBLE, _("turret {n}: {why}", n=number, why=e)
        self.record(b, number, outcome, note, check_only)
        return note

    def along(self, b: Bot, cfg: dict[str, Any], check_only: bool) -> Callable[[int], None]:
        """For construction's highland visits: fire the turret of the highland we are in, when it is
        one of the listed ones and hasn't fired today."""
        s = settings_of(cfg)

        def fire(number: int) -> None:
            if number in s.turrets and number not in self.results and str(number) not in fired_today(chores.who(b)):
                b.log(self.fire(b, s, number, check_only))
        return fire


def firing(b: Bot, cfg: dict[str, Any], check_only: bool, day: Day | None = None) -> tuple[str, bool]:
    """The chore: each listed turret that hasn't fired today (nor in this run, on construction's
    visits) is visited and fires once."""
    day = day or Day()
    s = settings_of(cfg)
    done = fired_today(chores.who(b))
    left = [n for n in s.turrets if n in day.results or str(n) not in done]     # this run's too, to tell
    if not left:
        return _("every turret has fired today"), True
    notes: list[str] = []
    for number in left:
        if day.results.get(number, UNKNOWN) in AGAIN:      # not tried this run, or construction's try failed
            _visit(b, s, number, check_only, day)
        notes.append(day.notes[number])
    finished = not check_only and all(day.results[n] in FINAL for n in left)
    return "; ".join(notes), finished


def _visit(b: Bot, s: Settings, number: int, check_only: bool, day: Day) -> None:
    try:
        if not chores.enter_highland(b, number):
            day.record(b, number, TROUBLE, _("turret {n}: highland {n} isn't in our gang's list", n=number),
                       check_only)
            return
    except Stopped as e:
        if b.stop_event.is_set() or getattr(e, "final", False):
            raise
        day.record(b, number, TROUBLE, _("turret {n}: {why}", n=number, why=e), check_only)
        return
    try:
        day.fire(b, s, number, check_only)
    finally:
        chores.leave_highland(b)
