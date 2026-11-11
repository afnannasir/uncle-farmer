"""Daily chores: what a character can do once a game day, done for it in one run.

The game's day starts at 00:00 GMT (daily.game_day). Each chore is written down per character in
logs/chores/<name>.json once it is done, or once the game shows there is nothing left of it today,
so a restart never donates twice or enters a second secret room. "check only" walks to each NPC,
reads the menu and says what it would do, pressing nothing that collects, spends or enters.

Menus as the game shows them (logged sessions and the survey, 2026-10-11):
  bank (any city's Bank receptionist)   "get deposit income"; "Value Product" -> "get income" per card
  hunting bounty (Chancellor Bren, General Paul, Colonel Demar)   "Collect Hunting Bounty"
  pet rebirth (any city's Pet Trainer)  "Get Rebirth" -> "Please choose a pet" ("Wild Eagle>><< max":
                                        the kind, then the name the player gave it; the pet panel and
                                        so the character scan shorten the kind: "Eagle max") -> "You
                                        still have 2 free Pet Rebirth times today; press here to
                                        Rebirth". Each rebirth closes the menu: the next one starts
                                        from the trainer again. Once the free ones are used it reads
                                        "fee 50 diamonds": never pressed
  gang donation (Head of inner hall, 40,40)   "Donation" -> "Are you sure you want to donate 10000
                                        Arnebia to the gang?" (20000 Arnebia, 20 diamonds); once a day
  gang construction                     building costs nothing: it PAYS ("construction general, When
                                        build, each time can get 3000gang contributions; 3000gang
                                        points; 6000 Arnebia" - a soldier's rank gets 1000/1000/0).
                                        The player chooses where: the inner hall's West / East
                                        encampment reinforced construction (6,11 / 75,11, "build"), or
                                        the gang's highland, whose number differs from gang to gang and
                                        so is typed in the settings (Highland Entrance 41,2: "Occupied
                                        Highlands" -> "the own Gang's Highland 38"; "Gang Highlands"
                                        lists every gang's). There the Crystal Turret and the Wall
                                        barricade say "Each time of repair can get ..." with "repair";
                                        the Teleportation circle's "Return to Main mansion" goes back to
                                        the inner hall. Builds and repairs share one limit, TWO a day
                                        wherever they are made: the third answers "you've already built
                                        twice today". A refusal is said (IN op=60); the reward comes as
                                        another message the agent doesn't keep. A day without building
                                        lowers the player's gang construction level.
  breed beast (Animal Shed, 29,50)      "Breeding King beetle; 85.0% complete; press to feed" ("each
                                        feed can get 500 gang contributions, 500 gang points and 2000
                                        arnebia. Are you sure you want to feed?"): free, apart from
                                        the two builds.
  secret room (Instructor Hargard, Cody or Kay)   "Secret room training" -> "Enter the secret room to
                                        practice" -> "Today's secret room training time is 3 hours, and
                                        you can enter 8 more times" and "Level 70 ocean shard secret
                                        room 1 Number of people 9/12". Inside, the Secret room Manager:
                                        "Please select the assistant you want to summon" ->
                                        "Summoned snowflake prince" ...; afterwards "Share Arnebia" ->
                                        "Choose a player; 0/5" -> "Ally; Sorceress; female ; level
                                        79". The assistant kills for you (you can't fight or call pets
                                        there). Sharing is 5 times a day and once in each half hour of
                                        the clock (:00-:30, :30-:00), and both players get a random
                                        amount of arnebia: in at 10:28, one share, another at 10:30,
                                        and out. Time inside counts against the day's drop time, so a
                                        farming run leaves for such a visit just before a half hour
                                        and comes back.
"""

from __future__ import annotations

import copy
import json
import os
import re
import time
from collections.abc import Collection, Iterable
from typing import Any

from .. import atlas, daily, knowledge, paths, settings
from ..i18n import _
from . import crafting, town
from .bot import CONFIRM, Bot, Stopped

CHORES = ("bank", "bounty", "pet_rebirth", "donation", "construction", "feed", "secret_room")
DEFAULTS: dict[str, Any] = {
    "bank": True, "bounty": True,
    "pet_rebirth": False, "pet": "", "rebirths": 2,
    "donation": "off",                # off, 10000, 20000 or diamonds
    "construction": 0,                # builds a day, over every place to build (the game allows 2)
    "feed": False,                    # feed the gang's breed beast at the Animal Shed
    "build_at": "posts",              # posts: the inner hall's East and West; highland: the gang's
    "highland": 0,                    # ... highland, by its number under Occupied Highlands
    "secret_room": False, "room": "", "assistant": 1,
    "share_with": "", "share_anyone": True,
    "practice_minutes": 0,            # stay this long to practise (0: in, share, out)
    "share_trips": True,              # a farming run leaves to share just before a half hour
    "trip_lead": 10,                  # ... this many minutes before it (the walk there included)
    "before_farming": False,          # a farming run does the day's chores first
}
DONATIONS = {"10000": (r"donate 10000 Arnebia", 10000), "20000": (r"donate 20000 Arnebia", 20000),
             "diamonds": (r"donate 20 diamonds", 0)}
BOUNTY_OFFICERS = ("Chancellor Bren", "General Paul", "Colonel Demar")
HEAD = ("Head of inner hall", (40, 40))          # the same cells in every gang base
SHED = ("Animal Shed", (29, 50))
FEED = r"press to feed|want to feed"
DAILY_BUILDS = 2              # builds and repairs together, wherever they are made
TWICE = re.compile(r"built twice today", re.I)
POSTS = (("West encampment reinforced construction", (6, 11)),
         ("East encampment reinforced construction", (75, 11)))
BUILD = r"^\s*(build|repair)\s*$"          # the entry that builds (a site) or repairs (a highland defence)
REWARD = re.compile(r"each time (?:of repair )?can get[^;]*;[^;]*;\s*(\d+)\s*Arnebia", re.I)
HIGHLAND_GATE = ("Highland Entrance", (41, 2))
HIGHLAND_HOME = ("Teleportation circle", r"Return to Main mansion")
# A walk round a gang base (gathering looks for its area entrances this way): the game shows what is
# within about 30 tiles, so a half (80 x 60) takes four stops; the inner hall is the north half.
INNER_SWEEP = ((20, 15), (60, 15), (60, 45), (20, 45))
OUTER_SWEEP = ((20, 75), (60, 75), (60, 105), (20, 105))
MANAGER = "Secret room Manager"
SLOT = 1800                   # the clock's half hours: one share in each
SHARES_A_DAY = 5
WAIT_MAX = 6 * 60             # in the room, wait this long at most for the next half hour's share
LOOK_EVERY = 30.0             # seconds between looks while the assistant practises
TRIP_CHECK = 20.0             # seconds between a farming run's looks at whether a share trip is due
ROOM = re.compile(r"Level\s+(\d+)\s+(.+?)\s+secret room\s+(\d+).*?Number of people\s*(\d+)\s*/\s*(\d+)", re.I)
PLAYER = re.compile(r"^\s*([^;]+?)\s*;\s*[A-Za-z]+\s*;\s*(?:male|female)\b", re.I)
FREE_REBIRTH = r"free Pet Rebirth times today"


def label(chore: str) -> str:
    return {"bank": _("bank income"), "bounty": _("hunting bounty"), "pet_rebirth": _("pet rebirth"),
            "donation": _("gang donation"), "construction": _("gang construction"),
            "feed": _("Animal Shed feeding"),
            "secret_room": _("secret room")}[chore]


def config(p: dict[str, Any]) -> dict[str, Any]:
    """The profile's chores, with every setting filled in."""
    own: dict[str, Any] = p.get("chores") or {}
    return {**copy.deepcopy(DEFAULTS), **own}     # never the defaults' own lists and dicts


def wanted(cfg: dict[str, Any], chore: str) -> bool:
    if chore == "donation":
        return cfg.get("donation", "off") in DONATIONS
    if chore == "construction":
        return int(cfg.get("construction") or 0) > 0 and (cfg.get("build_at") != "highland"
                                                          or int(cfg.get("highland") or 0) > 0)
    if chore == "pet_rebirth":
        return bool(cfg.get("pet_rebirth")) and bool((cfg.get("pet") or "").strip())
    return bool(cfg.get(chore))


# ---------------------------------------------------------------------------------------- the done list
def who(b: object) -> str | None:
    """The character a bot's chores are kept for: its own profile key when the run knows it (two
    characters can share a name on different servers), else its name."""
    return getattr(b, "profile_key", None) or getattr(b, "character", None)


def _ledger(name: str | None) -> str:
    """The done list's file: one for each confirmed character identity, or per name if unknown."""
    server = settings.profile_server(name or "")
    entity_id = settings.profile_id(name)
    name = settings.profile_name(name)
    stem = paths.safe_name(name or "unknown")
    if entity_id:
        if server:
            stem += "--" + paths.safe_name(server)
        stem += "--" + paths.safe_name(entity_id)
    return os.path.join(paths.LOGS, "chores", stem + ".json")


def _other_server_ledger(name: str, entity_id: str, own: str) -> bool:
    """Whether an old ID-only ledger could belong to another now-known server character."""
    prefix = paths.safe_name(name) + "--"
    suffix = "--" + paths.safe_name(entity_id) + ".json"
    try:
        filenames = os.listdir(os.path.dirname(own))
    except OSError:
        return False
    for filename in filenames:
        middle = filename[len(prefix):-len(suffix)] if filename.startswith(prefix) and filename.endswith(suffix) else ""
        if middle and os.path.join(os.path.dirname(own), filename) != own:
            return True
    return False


def done_today(name: str | None, day: str | None = None) -> dict[str, Any]:
    """{chore: {"at", "note"}} of what is done this game day. A character with its own key and no
    list of its own yet starts from a safe older list, so chores done earlier today aren't done
    twice without accidentally importing another server's work."""
    day = day or daily.game_day()
    path = _ledger(name)
    if not os.path.exists(path) and settings.is_character_profile(name or ""):
        display_name = settings.profile_name(name)
        server = settings.profile_server(name or "")
        entity_id = settings.profile_id(name)
        id_only = settings.character_profile(display_name, entity_id)
        old_path = _ledger(id_only)
        if not server or not os.path.exists(old_path) or not _other_server_ledger(display_name, entity_id or "", path):
            path = old_path if os.path.exists(old_path) else _ledger(display_name)
        else:
            path = _ledger(display_name)
    try:
        with open(path, encoding="utf-8") as f:
            data: dict[str, Any] = json.load(f)
    except (OSError, ValueError):
        return {}
    done: dict[str, Any] = data.get("done") or {}
    return dict(done) if data.get("day") == day else {}


def mark(name: str | None, chore: str, note: str, day: str | None = None) -> None:
    put(name, chore, {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note}, day)


def put(name: str | None, key: str, value: object, day: str | None = None) -> None:
    """One entry of this game day's list (a chore, or "_shares": the half hours shared in)."""
    day = day or daily.game_day()
    done = done_today(name, day)
    done[key] = value
    path = _ledger(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"day": day, "done": done}, f, indent=1)
    os.replace(tmp, path)


def due(name: str | None, cfg: dict[str, Any]) -> list[str]:
    """The chores cfg turns on that aren't done this game day."""
    done = done_today(name)
    return [c for c in CHORES if wanted(cfg, c) and c not in done and not c.startswith("_")]


# ---------------------------------------------------------------------------------------- running them
def run(b: Bot, cfg: dict[str, Any], check_only: bool = False, only: Collection[str] | None = None) -> list[tuple[str, str]]:
    """Every chore cfg turns on (only: just those) that isn't done today. [(chore, what happened)]."""
    name = who(b)
    done = done_today(name)
    out: list[tuple[str, str]] = []
    # A chores run is all walking from one NPC to the next, much of it on one map (a town, the gang
    # hall, the highland), where travel() has nothing to plan: travelling on, every walk puts the
    # Swift fusion beast on, as a trip between maps does.
    was = getattr(b, "traveling", False)
    b.traveling = True
    try:
        _run(b, cfg, check_only, only, done, out)
    finally:
        b.traveling = was
    return out


def _run(b: Bot, cfg: dict[str, Any], check_only: bool, only: Collection[str] | None,
         done: dict[str, Any], out: list[tuple[str, str]]) -> None:
    name = who(b)
    for chore in CHORES:
        if (only and chore not in only) or not wanted(cfg, chore):
            continue
        if chore in done and not check_only:
            out.append((chore, _("already done today")))
            continue
        try:
            note, finished = DO[chore](b, cfg, check_only)
        except Stopped as e:
            if b.stop_event.is_set() or getattr(e, "final", False):
                raise
            note, finished = str(e), False
        b.close_dialogs()
        b.log(_("{chore}: {note}", chore=label(chore), note=note))
        if finished and not check_only:
            mark(name, chore, note)
        out.append((chore, note))
    if not out:
        b.log(_("no daily chores to do: turn some on in Tools, Daily chores"))
    else:
        b.log(_("daily chores finished: {list}", list="; ".join("%s - %s" % (label(c), n) for c, n in out)),
              speak=True)


# ---------------------------------------------------------------------------------------- menu helpers
def _has(b: Bot, pattern: str) -> bool:
    d = b.dialog()
    return bool(d["open"]) and b.find_entry(d.get("top"), pattern) is not None


def _texts(b: Bot, pattern: str) -> list[str]:
    """The texts of the open menu's entries that match pattern, in order."""
    d = b.dialog()
    top: dict[str, Any] = d.get("top") or {}
    rx = re.compile(pattern, re.I)
    out: list[str] = []
    rows: list[list[Any]] = top.get("rows", [])
    for row in rows:
        for e in row:
            if e:
                out += [t for t in b.entry_texts(e) if rx.search(t) and t not in out]
    return out


def _count(b: Bot, pattern: str) -> int:
    """How many of the open menu's entries match pattern: each card's "get income" is its own
    entry, worded the same."""
    d = b.dialog()
    rx = re.compile(pattern, re.I)
    top: dict[str, Any] = d.get("top") or {}
    rows: list[list[Any]] = top.get("rows", [])
    return sum(1 for row in rows for e in row
               if e and any(rx.search(t) for t in b.entry_texts(e)))


def confirm(b: Bot) -> None:
    """Answer the game's "are you sure?" when it asks (twice at most)."""
    for _i in range(2):
        d = b.wait_dialog(1.0)
        if d is None or not d.get("top") or b.find_entry(d["top"], CONFIRM) is None:
            return
        b.choose(CONFIRM)


def _arnebia(b: Bot) -> int:
    return town.counts(b).get("arnebia", 0)


def said_mark(b: Bot) -> int:
    """The agent's clock now (ms), to read what the game says after an action."""
    try:
        return int(b.a.json("said 0").get("now", 0))
    except Exception:              # an agent that can't say: nothing heard, never a crash
        return 0


def said_since(b: Bot, since: int) -> list[str]:
    """What the game said (IN op=60) after since: refusals such as "already donated today"."""
    try:
        lines: list[dict[str, Any]] = b.a.json("said %d" % since).get("said", [])
    except Exception:
        return []
    return [str(s.get("text") or "").strip() for s in lines if s.get("text")]


def _talk(b: Bot, npc: str, cell: tuple[float, float] | None = None) -> None:
    if not b.talk(npc, cell):
        raise Stopped(_("couldn't talk to {npc}", npc=npc))
    b.wait_dialog(3.0)


def inner_hall(b: Bot) -> None:
    """Into our gang base's inner hall (Head of inner hall, Guru Elder, the construction posts),
    through the nearest Gang Entrance."""
    if crafting.in_gang(b) and crafting.reachable_cell(b, HEAD[1]):
        return
    b.close_dialogs()
    town.visit(b, "gang_entrance")
    mid = b.map_id()
    if b.choose(r"inner\s+hall") is None:
        raise Stopped(_("the Gang Entrance has no way into a gang's inner hall - is this character in a gang?"))
    confirm(b)
    if not b.wait_map_change(mid, 15) or not crafting.in_gang(b):
        b.close_dialogs()
        raise Stopped(_("couldn't get into the gang's inner hall"))


# ---------------------------------------------------------------------------------------- the chores
def bank(b: Bot, cfg: dict[str, Any], check_only: bool) -> tuple[str, bool]:
    """Deposit income, then each Value Product card's daily income, at the nearest bank."""
    town.visit(b, "bank")
    b.wait_dialog(3.0)
    got: list[str] = []
    if _has(b, r"^\s*get deposit income"):
        if check_only:
            got.append(_("the deposit income can be collected"))
        else:
            b.choose(r"^\s*get deposit income")
            confirm(b)
            got.append(_("collected the deposit income"))
            b.close_dialogs()
            town.visit(b, "bank")
            b.wait_dialog(3.0)
    if _has(b, r"^\s*Value Product"):
        b.choose(r"^\s*Value Product")
        b.wait_dialog(3.0)
        cards = _count(b, r"^\s*get income")
        if cards and check_only:
            got.append(_("{n} Value Product incomes can be collected", n=cards))
        n = taken = 0
        for _i in range(0 if check_only else cards):
            if not _has(b, r"^\s*get income"):
                break
            t0 = said_mark(b)
            b.choose(r"^\s*get income")
            confirm(b)
            b.sleep(0.5)
            if any(re.search(r"gotten the income", s, re.I) for s in said_since(b, t0)):
                taken += 1                 # "You have gotten the income of this value product today"
            else:
                n += 1
            if b.wait_dialog(1.5) is None:
                break
        if n:
            got.append(_("collected {n} Value Product incomes", n=n))
        if taken:
            got.append(_("{n} Value Product incomes were already collected today", n=taken))
    return ("; ".join(got) or _("nothing to collect today")), not check_only


def bounty(b: Bot, cfg: dict[str, Any], check_only: bool) -> tuple[str, bool]:
    """Hunting bounties from the nearest city officer who pays them."""
    officers = [knowledge.describe_npc(n) for n in BOUNTY_OFFICERS]
    cands = [(o["map"], o["name"], o["x"], o["y"]) for o in officers if o]
    town.visit(b, "bounty officer", candidates=cands)
    b.wait_dialog(3.0)
    if not _has(b, r"Collect Hunting Bounty"):
        return _("the officer offers no hunting bounty"), False
    if check_only:
        return _("hunting bounties can be collected here"), False
    b.choose(r"Collect Hunting Bounty")
    confirm(b)
    for _i in range(10):                  # a list of bounties to take, if the game shows one
        d = b.wait_dialog(1.0)
        if d is None or not d.get("top") or b.find_entry(d["top"], r"^\s*(collect|claim|receive|get)\b") is None:
            break
        b.choose(r"^\s*(collect|claim|receive|get)\b")
        confirm(b)
    return _("collected the hunting bounty"), True


def _assets(b: Bot) -> int | None:
    m = re.search(r"Current assets\s+(\d+)", town.menu_text(b))
    return int(m.group(1)) if m else None


def same_pet(entry: str, pet: str) -> bool:
    """The Pet Trainer's "Pixie>><< mimi" is the pet the player chose as "Pixie mimi" (the scan's
    name), "mimi" (the name they gave it) or "Pixie" (its kind). The pet panel, and so the scan,
    shortens some kinds: the trainer's "Wild Eagle>><< max" is the scan's "Eagle max" - the given
    name the same, and every other word of the choice one of the kind's."""
    kind, _sep, given = entry.partition(">><<")
    kind, given, pet = " ".join(kind.split()).lower(), " ".join(given.split()).lower(), " ".join(pet.split()).lower()
    if not pet:
        return False
    if pet in ((kind + " " + given).strip(), given, kind):
        return True
    words, own = pet.split(), given.split()
    if own and words[-len(own):] == own:
        rest = words[:-len(own)]
        return bool(rest) and all(w in kind.split() for w in rest)
    return False


def _pet_page(b: Bot, pet: str) -> str | None:
    """Open the chosen pet's rebirth page at the trainer: None when it is up, else why not."""
    b.close_dialogs()
    town.visit(b, "pet_trainer")
    b.wait_dialog(3.0)
    if b.choose(r"^\s*Get Rebirth") is None:
        return _("the Pet Trainer offers no rebirth")
    b.wait_dialog(3.0)
    listed = _texts(b, r">><<")
    entry = next((t for t in listed if same_pet(t, pet)), None)
    if entry is None:
        pets = [" ".join(t.replace(">><<", " ").split()) for t in listed]
        return _("no pet called {pet} (pets: {pets})", pet=pet, pets=", ".join(pets) or "-")
    b.choose(r"^\s*%s\s*$" % re.escape(entry))
    b.wait_dialog(3.0)
    return None


def pet_rebirth(b: Bot, cfg: dict[str, Any], check_only: bool) -> tuple[str, bool]:
    """The day's free rebirths of the pet the player chose (by the name they gave it, or its kind).
    A rebirth closes the trainer's menu, so each one starts from the trainer again."""
    pet = (cfg.get("pet") or "").strip()
    why = _pet_page(b, pet)
    if why is not None:
        return why, False
    before = _assets(b)
    if check_only:
        free = _texts(b, FREE_REBIRTH)
        return (_("{pet}: assets {assets}; {free}", pet=pet, assets=before, free=free[0] if free
                  else _("no free rebirth left today")), False)
    n = 0
    for i in range(max(0, int(cfg.get("rebirths") or 2))):
        if i and _pet_page(b, pet) is not None:
            break
        if not _has(b, FREE_REBIRTH):     # what is left costs diamonds: never
            break
        b.choose(FREE_REBIRTH)
        confirm(b)
        n += 1
        b.sleep(0.6)
    if not n:
        return _("{pet}: no free rebirth left today", pet=pet), True
    after = _assets(b) if _pet_page(b, pet) is None else None      # what the rebirths made of it
    return _("{pet}: {n} free rebirths, assets {before} -> {after}", pet=pet, n=n, before=before,
             after=after), True


def donation(b: Bot, cfg: dict[str, Any], check_only: bool) -> tuple[str, bool]:
    """The once-a-day gang donation the player chose, never below the arnebia reserve."""
    kind = cfg.get("donation", "off")
    pattern, cost = DONATIONS[kind]
    if cost and not check_only and crafting.spare_arnebia(b) < cost:
        return _("not enough arnebia above your reserve for a {n} arnebia donation", n=cost), False
    inner_hall(b)
    _talk(b, *HEAD)
    if b.choose(r"^\s*Donation") is None:
        return _("the Head of the inner hall offers no donation"), False
    b.wait_dialog(3.0)
    if not _has(b, pattern):
        return _("no donation offered now - already donated today?"), True
    what = re.sub(r"(?i)^\s*are you sure you want to\s*|\?\s*$", "", _texts(b, pattern)[0])
    if check_only:
        return _("would {what}", what=what), False
    before = _arnebia(b)
    t0 = said_mark(b)
    b.choose(pattern)
    confirm(b)
    b.sleep(1.0)
    heard = said_since(b, t0)
    if any(re.search(r"already donated", s, re.I) for s in heard):
        return _("already donated today"), True
    if cost and _arnebia(b) >= before:
        return _("the donation didn't go through: {why}",
                 why="; ".join(heard) or _("no answer from the game")), False
    return _("done: {what}", what=what), True


def sites_in_view(b: Bot) -> list[tuple[str, float, float]]:
    """[(name, x, y)] of what the game shows round us in a highland: every NPC but the way home and
    the way out - the Crystal Turret and the Wall barricade that take repairs, and whatever else
    there is to build."""
    out: list[tuple[str, float, float]] = []
    entities: list[dict[str, Any]] = b.snap().get("entities", [])
    for e in entities:
        name = " ".join((e.get("name") or "").split())
        if e.get("kind") in ("npc", "other") and name and name not in (HIGHLAND_HOME[0], HIGHLAND_GATE[0]):
            out.append((name, float(e["x"]), float(e["y"])))
    return out


REFUSED = re.compile(r"not enough|can'?t|cannot|already|no more|limit|today|insufficient", re.I)
PAID_WAIT = 3.0               # the reward's arnebia comes about a second after the choice


def reward(b: Bot) -> int | None:
    """The arnebia one build or repair here pays, as the open menu says ("... 6000 Arnebia")."""
    m = REWARD.search(town.menu_text(b))
    return int(m.group(1)) if m else None


def _paid(b: Bot, before: int) -> bool:
    end = b.clock.time() + PAID_WAIT
    while True:
        if _arnebia(b) > before:
            return True
        if b.clock.time() >= end:
            return False
        b.sleep(0.25)


# Where a player stands to talk to a place: the Wall barricade (31,55) answers from its south side
# only (hand runs stood at 31,57-59); from the north, where the turret is, the game won't select it.
STANDS = {"Wall barricade": (31, 58)}


def _build_at(b: Bot, name: str, cell: tuple[float, float] | None, check_only: bool) -> tuple[int, bool]:
    """One build (or repair) at one place: each takes one a day ("You've repaired the crystal
    turret today"). (1 when the game took it, else 0; whether it said the day's two builds are
    made). Building costs nothing - it pays."""
    stand = STANDS.get(name)
    if stand is not None:
        b.walk_to(stand)
    if not b.talk(name, cell, stand):
        return 0, False
    b.wait_dialog(3.0)
    if not _has(b, BUILD):
        b.close_dialogs()
        return 0, False                    # nothing to build or repair here (now)
    pays = reward(b)
    if check_only:
        b.close_dialogs()
        return 1, False
    before = _arnebia(b)
    t0 = said_mark(b)
    b.choose(BUILD)
    confirm(b)
    if pays:
        got = _paid(b, before)
    else:
        b.sleep(1.5)                       # time for a refusal to be said
        got = True
    b.close_dialogs()
    refused = [s for s in said_since(b, t0) if REFUSED.search(s)]
    if any(TWICE.search(s) for s in refused):
        return 0, True
    if refused:
        b.log(_("the {post}: {what}", post=name, what="; ".join(refused)))
        return 0, False
    if not got:
        return 0, False                    # paid nothing: the game took nothing here today
    b.log(_("built at the {post}", post=name))
    return 1, False


def highland_entry(number: int) -> str:
    """The Occupied Highlands entry of the gang's highland: "the own Gang's Highland 38"."""
    return r"\bHighland\s+%d(?!\d)" % number


def _to_highland(b: Bot, number: int) -> None:
    """From the inner hall into the gang's highland: the Highland Entrance's "Occupied Highlands"
    lists it with its number, which differs from gang to gang (the player types it)."""
    inner_hall(b)
    _talk(b, *HIGHLAND_GATE)
    mid = b.map_id()
    if b.choose(r"^\s*Occupied Highlands") is None:
        b.close_dialogs()
        raise Stopped(_("the Highland Entrance offers no Occupied Highlands"))
    b.wait_dialog(3.0)
    listed = _texts(b, r"Highland\s+\d+")
    if b.choose(highland_entry(number)) is None:
        b.close_dialogs()
        raise Stopped(_("Occupied Highlands has no highland {n} (it lists: {list}) - check the number in "
                        "Tools, Daily chores", n=number, list=", ".join(listed) or "-"))
    confirm(b)
    if not b.wait_map_change(mid, 15):
        b.close_dialogs()
        raise Stopped(_("couldn't get into highland {n}", n=number))


def _from_highland(b: Bot) -> None:
    """Back to the inner hall by the highland's Teleportation circle ("Return to Main mansion")."""
    mid = b.map_id()
    b.close_dialogs()
    if b.talk(HIGHLAND_HOME[0]) and b.choose(HIGHLAND_HOME[1]) is not None:
        confirm(b)
        b.wait_map_change(mid, 15)
    b.close_dialogs()


def _whole_map(b: Bot) -> list[tuple[int, int]]:
    """Stops a walk round this whole map (a highland) takes, 40 tiles apart."""
    try:
        m = atlas.model(b.map_id())
        return [(x, y) for x in range(20, m.W, 40) for y in range(20, m.h, 40) if m.ok(x, y)]
    except Exception:              # no map data for it: what is in view from here
        return []


def _highland_places(b: Bot) -> list[tuple[str, tuple[float, float] | None]]:
    """The highland's places to build or repair: what is in view, else what a walk round finds."""
    found: dict[str, tuple[float, float] | None] = {}
    for cell in [None, *_whole_map(b)]:
        if cell is not None:
            if found:
                break
            b.walk_to(cell)
        for name, x, y in sites_in_view(b):
            found.setdefault(name, (x, y))
    return list(found.items())


def construction(b: Bot, cfg: dict[str, Any], check_only: bool) -> tuple[str, bool]:
    """The day's builds (the game allows two, builds and repairs together) where the player chose:
    the inner hall's East and West encampment reinforced constructions, or the gang's highland
    (its number typed in the settings). Each pays gang contribution, gang points and arnebia."""
    want = max(0, min(int(cfg.get("construction") or 0), DAILY_BUILDS))
    in_highland = cfg.get("build_at") == "highland"
    if in_highland:
        number = int(cfg.get("highland") or 0)
        if number <= 0:
            return _("type your gang's highland number in Tools, Daily chores"), False
        _to_highland(b, number)
    else:
        inner_hall(b)
    built = 0
    places: list[str] = []
    try:
        targets: list[tuple[str, tuple[float, float] | None]] = _highland_places(b) if in_highland else list(POSTS)
        for name, cell in targets:
            places.append(name)
            n, day_done = _build_at(b, name, cell, check_only)
            if check_only and n:
                return _("can build at the {post}", post=name), False
            built += n
            if day_done:
                return _("built {n} times; the game's two builds a day are made", n=built), True
            if built >= want:
                return _("built {n} times", n=built), True
    finally:
        if in_highland:
            _from_highland(b)
    if not places:
        return _("found no place to build"), False
    if check_only:
        return _("nowhere to build now (places: {places})", places=", ".join(places)), False
    return _("built {n} times; no place took more today (places: {places})", n=built,
             places=", ".join(places)), True


def feed(b: Bot, cfg: dict[str, Any], check_only: bool) -> tuple[str, bool]:
    """Feed the gang's breed beast at the inner hall's Animal Shed once (it pays: 500 gang
    contribution, 500 gang points, 2000 arnebia)."""
    inner_hall(b)
    _talk(b, *SHED)
    what = next(iter(_texts(b, r"press to feed")), "")
    beast = re.sub(r"(?i);?\s*press to feed\s*$", "", what).strip()
    if not _has(b, FEED):
        return _("the Animal Shed has nothing to feed now"), False
    if check_only:
        return _("would feed: {beast}", beast=beast or "?"), False
    before = _arnebia(b)
    t0 = said_mark(b)
    b.choose(FEED)
    confirm(b)
    paid = _paid(b, before)
    heard = [s for s in said_since(b, t0) if REFUSED.search(s)]
    if heard:
        return _("not fed: {why}", why="; ".join(heard)), bool(re.search(r"today", " ".join(heard), re.I))
    if not paid:
        return _("fed {beast}, but no arnebia came in", beast=beast or "?"), True
    return _("fed {beast}", beast=beast or "?"), True


# ---------------------------------------------------------------------------------------- the secret room
def char_level(b: Bot) -> int:
    sheet: dict[str, Any] = b.refresh_sheet() or {}
    seen: dict[str, Any] = b.me().get("sheet") or {}
    return int(sheet.get("level") or seen.get("level") or 0)


Room = tuple[int, str, int, int, int, str]       # (level, name, number, people, places, text)


def rooms(texts: Iterable[str]) -> list[Room]:
    """[(level, name, number, people, places, text)] of the room list's entries."""
    out: list[Room] = []
    for t in texts:
        m = ROOM.search(t)
        if m:
            out.append((int(m.group(1)), m.group(2).strip(), int(m.group(3)), int(m.group(4)), int(m.group(5)), t))
    return out


def pick_room(found: Iterable[Room], level: int, want: str = "") -> Room | None:
    """The room to enter: one whose name has want in it (the player's choice) or else the highest
    level the character can take, the emptier first among equals; only rooms with a free place."""
    free = [r for r in found if r[3] < r[4] and (not level or r[0] <= level)]
    if want:
        free = [r for r in free if want.lower() in ("%s %s" % (r[1], r[2])).lower()
                or want.lower() in r[1].lower()]
    return max(free, key=lambda r: (r[0], -r[3]), default=None)


def _manager(b: Bot) -> None:
    _talk(b, MANAGER)


def practice_left(text: str) -> int | None:
    """Seconds of practice time the Manager says are left, or None."""
    m = re.search(r"(?:still have|remaining practice time for today is)\s+(?:(\d+)\s*hours?)?\s*(?:(\d+)\s*minutes?)?"
                  r"\s*(?:(\d+)\s*seconds?)?", text, re.I)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def _summon(b: Bot, cfg: dict[str, Any]):
    """The assistant that practises for us: the player's choice (1-10), once per entry."""
    _manager(b)
    choices = _texts(b, r"^\s*Summoned\b")
    if not choices:
        return _has(b, r"assistant has killed")       # one is out already
    i = min(max(1, int(cfg.get("assistant") or 1)), len(choices)) - 1
    b.choose(r"^\s*%s\s*$" % re.escape(choices[i]))
    confirm(b)
    b.log(_("secret room: {who}", who=choices[i]))
    return True


def _share(b: Bot, cfg: dict[str, Any]):
    """Share arnebia once, with the first of the player's names in the room (or anyone, if they
    allow it). (the shares made today as the game counts them, or None when it didn't say; whether
    this one was made)."""
    _manager(b)
    if b.choose(r"^\s*Share Arnebia") is None:
        return None, False
    b.wait_dialog(3.0)
    m = re.search(r"Choose a player;\s*(\d+)\s*/\s*(\d+)", town.menu_text(b))
    made = int(m.group(1)) if m else None
    if m and int(m.group(1)) >= int(m.group(2)):
        return made, False
    if b.choose(r"^\s*Choose a player") is None:
        return made, False
    b.wait_dialog(3.0)
    players = [(p.group(1), t) for t in _texts(b, PLAYER.pattern) for p in [PLAYER.match(t)] if p]
    names = [w.strip().lower() for w in (cfg.get("share_with") or "").split(",") if w.strip()]
    pick = next((p for w in names for p in players if p[0].lower() == w), None)
    if pick is None and cfg.get("share_anyone", True) and players:
        pick = players[0]
    if pick is None:
        b.log(_("secret room: nobody to share arnebia with yet"))
        return made, False
    b.choose(r"^\s*%s\s*;" % re.escape(pick[0]))
    confirm(b)
    b.log(_("secret room: shared arnebia with {name}", name=pick[0]), speak=False)
    return (made or 0) + 1, True


def _enter_room(b: Bot, cfg: dict[str, Any], check_only: bool, back_to: town.Place | None = None):
    """From an instructor into the chosen secret room. (entered, what to say). back_to: where the
    trip goes afterwards (a farming run's farm): the instructor cheapest there and back is chosen."""
    town.visit(b, "instructor", back_to=back_to)
    if b.choose(r"^\s*Secret room training") is None:
        return False, _("the instructor offers no secret room training")
    b.wait_dialog(3.0)
    if b.choose(r"^\s*Enter the secret room to practice") is None:
        return False, _("the instructor offers no secret room now")
    b.wait_dialog(3.0)
    text = town.menu_text(b)
    today = re.search(r"training time is ([^,|]+), and you can enter (\d+) more times", text, re.I)
    found = rooms(_texts(b, r"secret room"))
    room = pick_room(found, char_level(b), (cfg.get("room") or "").strip())
    say = today.group(0) if today else ""
    if check_only:
        return False, "; ".join(x for x in (say, _("would enter {room}", room="%s %d" % (room[1], room[2]))
                                            if room else _("no room with a free place for this level"))
                                if x)
    if today and int(today.group(2)) <= 0:
        return False, say
    if room is None:
        return False, _("no secret room for this level has a free place")
    mid = b.map_id()
    # the number runs straight into stray characters ("room 2ï¿½ï¿½ Number of people"): no \b
    b.choose(r"Level\s+%d\s+%s\s+secret room\s+%d(?!\d)" % (room[0], re.escape(room[1]), room[2]))
    confirm(b)
    if not b.wait_map_change(mid, 15):
        b.close_dialogs()
        return False, _("couldn't get into the secret room")
    return True, "%s %d" % (room[1], room[2])


def slot(t: float) -> int:
    """Which half hour of the clock t is in: each takes one share."""
    return int(t // SLOT)


def to_next_slot(t: float) -> float:
    return SLOT - t % SLOT


def shares(name: str | None) -> tuple[int, set[int]]:
    """(shares made today, the half hours they were made in)."""
    s: dict[str, Any] = done_today(name).get("_shares") or {}
    slots: list[int] = s.get("slots", [])
    return int(s.get("count", 0)), set(slots)


def _note_share(name: str | None, count: int, t: float) -> None:
    made, used = shares(name)
    used.add(slot(t))
    put(name, "_shares", {"count": max(made, count), "slots": sorted(used)})


def _share_here(b: Bot, cfg: dict[str, Any]) -> int:
    """One share in this half hour, unless today's are made or this half hour's is. The day's count."""
    name = who(b)
    made, used = shares(name)
    now = b.clock.time()
    if made >= SHARES_A_DAY or slot(now) in used:
        return made
    count, shared = _share(b, cfg)
    b.close_dialogs()
    if shared:
        _note_share(name, count or made + 1, now)
    elif count is not None and count > made:
        put(name, "_shares", {"count": count, "slots": sorted(used)})    # the game's own count
    return shares(name)[0]


def _wait_slot(b: Bot):
    """Wait in the room for the next half hour to start (and a few seconds more)."""
    end = b.clock.time() + to_next_slot(b.clock.time()) + 5
    while b.clock.time() < end:
        b.check()
        if atlas.kind(b.map_id()) != "secret_room":
            return False
        b.clear_popups()
        if b.clock.wait(b.stop_event, min(LOOK_EVERY, max(1.0, end - b.clock.time()))):
            raise Stopped(_("stopped by you"), final=True)
    return True


def share_visit(b: Bot, cfg: dict[str, Any], practise: int = 0,
                back_to: town.Place | None = None) -> tuple[int, str]:
    """One visit: into a room, an assistant out (the game lets us share only then), a share in this
    half hour, another as the next one starts when that is near (or every half hour while
    practising for `practise` minutes), then out. (shares made today, where). back_to: where the
    trip goes afterwards (a farming run's farm), so the instructor is chosen for there and back."""
    name = getattr(b, "character", None)
    b.share_tried = slot(b.clock.time())
    where = atlas.label(b.map_id())
    if atlas.kind(b.map_id()) != "secret_room":
        entered, where = _enter_room(b, cfg, False, back_to)
        if not entered:
            raise Stopped(where)
    try:
        if not _summon(b, cfg):
            raise Stopped(_("the Secret room Manager summoned no assistant"))
        b.close_dialogs()
        made = _share_here(b, cfg)
        start = b.clock.time()
        while made < SHARES_A_DAY:
            left = start + 60 * practise - b.clock.time()
            if to_next_slot(b.clock.time()) > max(WAIT_MAX, left):
                break
            if not _wait_slot(b):
                break                         # practice time ran out: the game sent us out
            made = _share_here(b, cfg)
        while b.clock.time() < start + 60 * practise and atlas.kind(b.map_id()) == "secret_room":
            b.check()
            b.clear_popups()
            if b.clock.wait(b.stop_event, LOOK_EVERY):
                raise Stopped(_("stopped by you"), final=True)
    finally:
        if atlas.kind(b.map_id()) == "secret_room":
            b.close_dialogs()
            b.leave_instance()
    return shares(name)[0], where


def trip_due(b: Bot) -> bool:
    """A farming run should leave for the secret room now: sharing is on, today's shares aren't all
    made, this half hour's isn't, and the next half hour starts within trip_lead minutes - so one
    short visit shares in both. Looked at every TRIP_CHECK seconds, once per half hour."""
    now = b.clock.time()
    if now - getattr(b, "trip_checked", 0.0) < TRIP_CHECK:
        return False
    b.trip_checked = now
    cfg = config(b.p)
    if not (cfg.get("secret_room") and cfg.get("share_trips")):
        return False
    if getattr(b, "share_tried", None) == slot(now):
        return False
    made, used = shares(getattr(b, "character", None))
    return (made < SHARES_A_DAY and slot(now) not in used
            and to_next_slot(now) <= 60 * max(1, int(cfg.get("trip_lead") or 10)))


def secret_room(b: Bot, cfg: dict[str, Any], check_only: bool):
    """A visit to share arnebia (and practise, when practice_minutes is set). Done for the day once
    the day's five shares are made."""
    if check_only:
        if atlas.kind(b.map_id()) == "secret_room":
            _manager(b)
            left = practice_left(town.menu_text(b))
            return _("in {room}; practice time left: {m} minutes", room=atlas.label(b.map_id()),
                     m="?" if left is None else left // 60), False
        return _enter_room(b, cfg, True)[1], False
    made, where = share_visit(b, cfg, max(0, int(cfg.get("practice_minutes") or 0)))
    return _("{room}: shared arnebia {n} of {all} times today", room=where, n=made, all=SHARES_A_DAY), \
        made >= SHARES_A_DAY


DO = {"bank": bank, "bounty": bounty, "pet_rebirth": pet_rebirth, "donation": donation,
      "construction": construction, "feed": feed, "secret_room": secret_room}
