"""Daily chores: what a character can do once a game day, done for it in one run.

The game's day starts at 00:00 GMT (daily.game_day). Each chore is written down per character in
logs/chores/<name>.json once it is done, or once the game shows there is nothing left of it today,
so a restart never donates twice or enters a second secret room. "check only" walks to each NPC,
reads the menu and says what it would do, pressing nothing that collects, spends or enters.

Menus as the game shows them (logged sessions and the survey, 2026-10-11):
  bank (any city's Bank receptionist)   "get deposit income"; "Value Product" -> "get income" per card
  hunting bounty (Chancellor Bren, General Paul, Colonel Demar)   "Collect Hunting Bounty"
  pet rebirth (any city's Pet Trainer)  "Get Rebirth" -> "Please choose a pet" ("Pixie>><< mimi": the
                                        kind, then the name the player gave it) -> "You still have 2
                                        free Pet Rebirth times today; press here to Rebirth"; once
                                        the free ones are used it reads "fee 50 diamonds": never pressed
  gang donation (Head of inner hall, 40,40)   "Donation" -> "Are you sure you want to donate 10000
                                        Arnebia to the gang?" (20000 Arnebia, 20 diamonds); once a day
  gang construction                     every NPC named "... construction" is a place to build ("build":
                                        10000 arnebia for 2500 gang contribution each time, the menu
                                        "construction X, When build, each time can get ..."): the
                                        West / East encampment reinforced construction (6,11 / 75,11)
                                        always, and the ones a leader starts ("Pharmacy room
                                        Construction", "Level 2 Supply Camp Construction", "Highland
                                        Construction" ...), which the Information Board lists ("View
                                        existing supply camps and offensive and defensive facilities";
                                        "<what>;<who> Upgrading to level N construction started"). The
                                        gang's highland is reached by the Highland Entrance (41,2):
                                        "Gang Highlands". A day without building lowers the player's
                                        gang construction level (the game's help).
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

import json
import os
import re
import time
from typing import Any

from .. import atlas, daily, knowledge, paths
from ..i18n import _
from . import crafting, town
from .bot import CONFIRM, Stopped

CHORES = ("bank", "bounty", "pet_rebirth", "donation", "construction", "secret_room")
DEFAULTS: dict[str, Any] = {
    "bank": True, "bounty": True,
    "pet_rebirth": False, "pet": "", "rebirths": 2,
    "donation": "off",                # off, 10000, 20000 or diamonds
    "construction": 0,                # builds a day, over every place to build
    "build_highland": True,           # ... the gang's highland's too
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
POSTS = (("West encampment reinforced construction", (6, 11)),
         ("East encampment reinforced construction", (75, 11)))
BUILD_COST = 10000
BOARD = "Information Board"
HIGHLAND_GATE = ("Highland Entrance", (41, 2))
# Where to look for places to build: the game shows what is within about 30 tiles, so a sweep of a
# gang base half (80 x 60) needs four stops; the inner hall is the north half, the outer the south.
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


def label(chore):
    return {"bank": _("bank income"), "bounty": _("hunting bounty"), "pet_rebirth": _("pet rebirth"),
            "donation": _("gang donation"), "construction": _("gang construction"),
            "secret_room": _("secret room")}[chore]


def config(p: dict[str, Any]) -> dict[str, Any]:
    """The profile's chores, with every setting filled in."""
    return dict(DEFAULTS, **(p.get("chores") or {}))


def wanted(cfg, chore):
    if chore == "donation":
        return cfg.get("donation", "off") in DONATIONS
    if chore == "construction":
        return int(cfg.get("construction") or 0) > 0
    if chore == "pet_rebirth":
        return bool(cfg.get("pet_rebirth")) and bool((cfg.get("pet") or "").strip())
    return bool(cfg.get(chore))


# ---------------------------------------------------------------------------------------- the done list
def _ledger(name):
    return os.path.join(paths.LOGS, "chores", paths.safe_name(name or "unknown") + ".json")


def done_today(name, day=None):
    """{chore: {"at", "note"}} of what is done this game day."""
    day = day or daily.game_day()
    try:
        with open(_ledger(name), encoding="utf-8") as f:
            data = json.load(f)
    except (OSError, ValueError):
        return {}
    return dict(data.get("done") or {}) if data.get("day") == day else {}


def mark(name, chore, note, day=None):
    put(name, chore, {"at": time.strftime("%Y-%m-%d %H:%M:%S"), "note": note}, day)


def put(name, key, value, day=None):
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


def due(name, cfg):
    """The chores cfg turns on that aren't done this game day."""
    done = done_today(name)
    return [c for c in CHORES if wanted(cfg, c) and c not in done and not c.startswith("_")]


# ---------------------------------------------------------------------------------------- running them
def run(b, cfg, check_only=False, only=None):
    """Every chore cfg turns on (only: just those) that isn't done today. [(chore, what happened)]."""
    name = getattr(b, "character", None)
    done = done_today(name)
    out = []
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
    return out


# ---------------------------------------------------------------------------------------- menu helpers
def _has(b, pattern):
    d = b.dialog()
    return bool(d["open"]) and b.find_entry(d.get("top"), pattern) is not None


def _texts(b, pattern):
    """The texts of the open menu's entries that match pattern, in order."""
    d = b.dialog()
    top = d.get("top") or {}
    rx = re.compile(pattern, re.I)
    out = []
    for row in top.get("rows", []):
        for e in row:
            if e:
                out += [t for t in b.entry_texts(e) if rx.search(t) and t not in out]
    return out


def _count(b, pattern):
    """How many of the open menu's entries match pattern: each card's "get income" is its own
    entry, worded the same."""
    d = b.dialog()
    rx = re.compile(pattern, re.I)
    return sum(1 for row in (d.get("top") or {}).get("rows", []) for e in row
               if e and any(rx.search(t) for t in b.entry_texts(e)))


def _confirm(b):
    """Answer the game's "are you sure?" when it asks (twice at most)."""
    for _i in range(2):
        d = b.wait_dialog(1.0)
        if d is None or not d.get("top") or b.find_entry(d["top"], CONFIRM) is None:
            return
        b.choose(CONFIRM)


def _arnebia(b):
    return town.counts(b).get("arnebia", 0)


def _talk(b, npc, cell=None):
    if not b.talk(npc, cell):
        raise Stopped(_("couldn't talk to {npc}", npc=npc))
    b.wait_dialog(3.0)


def _inner_hall(b):
    """Into our gang base's inner hall (Head of inner hall, Guru Elder, the construction posts),
    through the nearest Gang Entrance."""
    if crafting.in_gang(b) and crafting.reachable_cell(b, HEAD[1]):
        return
    b.close_dialogs()
    town.visit(b, "gang_entrance")
    mid = b.map_id()
    if b.choose(r"inner\s+hall") is None:
        raise Stopped(_("the Gang Entrance has no way into a gang's inner hall - is this character in a gang?"))
    _confirm(b)
    if not b.wait_map_change(mid, 15) or not crafting.in_gang(b):
        b.close_dialogs()
        raise Stopped(_("couldn't get into the gang's inner hall"))


# ---------------------------------------------------------------------------------------- the chores
def bank(b, cfg, check_only):
    """Deposit income, then each Value Product card's daily income, at the nearest bank."""
    town.visit(b, "bank")
    b.wait_dialog(3.0)
    got = []
    if _has(b, r"^\s*get deposit income"):
        if check_only:
            got.append(_("the deposit income can be collected"))
        else:
            b.choose(r"^\s*get deposit income")
            _confirm(b)
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
        n = 0
        for _i in range(0 if check_only else cards):
            if not _has(b, r"^\s*get income"):
                break
            b.choose(r"^\s*get income")
            _confirm(b)
            n += 1
            b.sleep(0.5)
            if b.wait_dialog(1.5) is None:
                break
        if n:
            got.append(_("collected {n} Value Product incomes", n=n))
    return ("; ".join(got) or _("nothing to collect today")), not check_only


def bounty(b, cfg, check_only):
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
    _confirm(b)
    for _i in range(10):                  # a list of bounties to take, if the game shows one
        d = b.wait_dialog(1.0)
        if d is None or not d.get("top") or b.find_entry(d["top"], r"^\s*(collect|claim|receive|get)\b") is None:
            break
        b.choose(r"^\s*(collect|claim|receive|get)\b")
        _confirm(b)
    return _("collected the hunting bounty"), True


def _assets(b):
    m = re.search(r"Current assets\s+(\d+)", town._menu_text(b))
    return int(m.group(1)) if m else None


def same_pet(entry, pet):
    """The Pet Trainer's "Pixie>><< mimi" is the pet the player chose as "Pixie mimi" (the scan's
    name), "mimi" (the name they gave it) or "Pixie" (its kind)."""
    kind, _sep, given = entry.partition(">><<")
    kind, given, pet = " ".join(kind.split()).lower(), " ".join(given.split()).lower(), " ".join(pet.split()).lower()
    return bool(pet) and pet in ((kind + " " + given).strip(), given, kind)


def pet_rebirth(b, cfg, check_only):
    """The day's free rebirths of the pet the player chose (by the name they gave it, or its kind)."""
    pet = (cfg.get("pet") or "").strip()
    town.visit(b, "pet_trainer")
    b.wait_dialog(3.0)
    if b.choose(r"^\s*Get Rebirth") is None:
        return _("the Pet Trainer offers no rebirth"), False
    b.wait_dialog(3.0)
    listed = _texts(b, r">><<")
    entry = next((t for t in listed if same_pet(t, pet)), None)
    if entry is None:
        pets = [" ".join(t.replace(">><<", " ").split()) for t in listed]
        return _("no pet called {pet} (pets: {pets})", pet=pet, pets=", ".join(pets) or "-"), False
    b.choose(r"^\s*%s\s*$" % re.escape(entry))
    b.wait_dialog(3.0)
    before = _assets(b)
    if check_only:
        free = _texts(b, FREE_REBIRTH)
        return (_("{pet}: assets {assets}; {free}", pet=pet, assets=before, free=free[0] if free
                  else _("no free rebirth left today")), False)
    n = 0
    for _i in range(max(0, int(cfg.get("rebirths") or 2))):
        if not _has(b, FREE_REBIRTH):     # what is left costs diamonds: never
            break
        b.choose(FREE_REBIRTH)
        _confirm(b)
        n += 1
        b.sleep(0.6)
        b.wait_dialog(3.0)
    if not n:
        return _("{pet}: no free rebirth left today", pet=pet), True
    return _("{pet}: {n} free rebirths, assets {before} -> {after}", pet=pet, n=n, before=before,
             after=_assets(b)), True


def donation(b, cfg, check_only):
    """The once-a-day gang donation the player chose, never below the arnebia reserve."""
    kind = cfg.get("donation", "off")
    pattern, cost = DONATIONS[kind]
    if cost and not check_only and crafting.spare_arnebia(b) < cost:
        return _("not enough arnebia above your reserve for a {n} arnebia donation", n=cost), False
    _inner_hall(b)
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
    b.choose(pattern)
    _confirm(b)
    b.sleep(1.0)
    if cost and _arnebia(b) >= before:
        return _("the donation didn't go through - already donated today?"), True
    return _("done: {what}", what=what), True


def is_site(name):
    """A place to build: "West encampment reinforced construction", "Pharmacy room Construction",
    "Level 2 Supply Camp Construction", "Highland Construction"..."""
    return bool(re.search(r"\bconstruction\s*$", name or "", re.I))


def sites_in_view(b):
    """[(name, x, y)] of the places to build the game shows round us."""
    out = []
    for e in b.snap().get("entities", []):
        name = " ".join((e.get("name") or "").split())
        if e.get("kind") in ("npc", "other") and is_site(name):
            out.append((name, float(e["x"]), float(e["y"])))
    return out


def _sweep(b, cells):
    """Walk round this part of the map and note every place to build (and the Information Board,
    when we pass it): {name: (x, y)}, the board under BOARD."""
    found = {}
    for cell in [None] + list(cells):
        if cell is not None:
            b.walk_to(cell)
        for name, x, y in sites_in_view(b):
            found.setdefault(name, (x, y))
        board = b.find_npc(BOARD)
        if board is not None:
            found.setdefault(BOARD, (float(board["x"]), float(board["y"])))
    return found


def board_constructions(b, cell):
    """What the Information Board says is being built: its "... construction started" lines and
    its list of supply camps and facilities. Read only, for the log and to know what to look for."""
    b.talk(BOARD, cell)
    if b.choose(r"View existing supply camps") is not None:
        b.wait_dialog(3.0)
    lines = [t for t in _texts(b, r"construction|supply camp|cannon|tower") if not re.match(r"\s*View ", t)]
    b.close_dialogs()
    return lines


def _build_at(b, name, cell, left, check_only):
    """Build at one place, up to left times. (builds, can't afford more)."""
    n = 0
    while n < left:
        if not check_only and crafting.spare_arnebia(b) < BUILD_COST:
            return n, True
        if not b.talk(name, cell):
            return n, False
        b.wait_dialog(3.0)
        if not _has(b, r"^\s*build\b"):
            b.close_dialogs()
            return n, False                # nothing to build here (now)
        if check_only:
            b.close_dialogs()
            return 1, False
        before = _arnebia(b)
        b.choose(r"^\s*build\b")
        _confirm(b)
        b.sleep(1.0)
        b.close_dialogs()
        if _arnebia(b) >= before:
            return n, False                # the game took no more here today
        n += 1
        b.log(_("built at the {post}", post=name))
    return n, False


def _to_highland(b):
    """From the inner hall into our gang's highland (the Highland Entrance's "Gang Highlands")."""
    _inner_hall(b)
    _talk(b, *HIGHLAND_GATE)
    mid = b.map_id()
    if b.choose(r"^\s*Gang Highlands") is None:
        return False
    for _i in range(3):                    # a list of highlands, or "are you sure?"
        if b.map_id() != mid:
            break
        d = b.wait_dialog(1.5)
        if d is None or not d.get("top"):
            break
        if b.find_entry(d["top"], CONFIRM) is not None:
            b.choose(CONFIRM)
        elif b.find_entry(d["top"], r"(?i)enter") is not None:
            b.choose(r"(?i)enter")
        else:
            break
    return b.wait_map_change(mid, 15)


def _go_to(b, where, go):
    """Into one part of the gang (go() does it): True when there, else said why."""
    try:
        if go():
            return True
        b.log(_("couldn't get to {where} to build", where=where))
    except Stopped as e:
        if b.stop_event.is_set() or getattr(e, "final", False):
            raise
        b.log(_("couldn't get to {where} to build: {why}", where=where, why=e))
    return False


def _whole_map(b):
    """Stops a sweep of this whole map (a highland) takes, 40 tiles apart."""
    try:
        m = atlas.model(b.map_id())
        return [(x, y) for x in range(20, m.W, 40) for y in range(20, m.h, 40) if m.ok(x, y)]
    except Exception:              # no map data for it: what is in view from here
        return []


def _places(b, cells, board):
    """The places to build in this part, the reinforced posts first; the Information Board is read
    (once) when we pass it."""
    found = _sweep(b, cells if cells is not None else _whole_map(b))
    cell = found.pop(BOARD, None)
    if cell is not None and not board:
        board += board_constructions(b, cell) or [""]
        if board[0]:
            b.log(_("the Information Board: {lines}", lines="; ".join(board)))
    return sorted(found.items(), key=lambda kv: kv[0] not in dict(POSTS))


def construction(b, cfg, check_only):
    """Up to the chosen number of builds a day, over every place to build: the inner hall's two
    reinforced constructions and whatever a leader has started there, in the outer hall and in the
    gang's highland, which the Information Board lists."""
    want = int(cfg.get("construction") or 0)
    built, places, board = 0, [], []
    areas = [(_("the inner hall"), lambda: (_inner_hall(b), True)[1], INNER_SWEEP),
             (_("the outer hall"), lambda: crafting.enter_gang(b, None, "forger"), OUTER_SWEEP)]
    if cfg.get("build_highland", True):
        areas.append((_("the highland"), lambda: _to_highland(b), None))
    for where, go, cells in areas:
        if not _go_to(b, where, go):
            continue
        for name, cell in _places(b, cells, board):
            places.append(name)
            n, broke = _build_at(b, name, cell, want - built, check_only)
            if check_only and n:
                return _("can build at the {post} (10000 arnebia, 2500 gang contribution each)", post=name), False
            built += n
            if broke:
                return _("built {n} times; not enough arnebia above your reserve for more", n=built), built > 0
            if built >= want:
                return _("built {n} times", n=built), True
    if not places:
        return _("found no place to build"), False
    if check_only:
        return _("nowhere to build now (places: {places})", places=", ".join(places)), False
    return _("built {n} times; no place took more today (places: {places})", n=built,
             places=", ".join(places)), True


# ---------------------------------------------------------------------------------------- the secret room
def _level(b):
    sheet = b.refresh_sheet() or {}
    return int(sheet.get("level") or ((b.me().get("sheet") or {}).get("level")) or 0)


def rooms(texts):
    """[(level, name, number, people, places, text)] of the room list's entries."""
    out = []
    for t in texts:
        m = ROOM.search(t)
        if m:
            out.append((int(m.group(1)), m.group(2).strip(), int(m.group(3)), int(m.group(4)), int(m.group(5)), t))
    return out


def pick_room(found, level, want=""):
    """The room to enter: one whose name has want in it (the player's choice) or else the highest
    level the character can take, the emptier first among equals; only rooms with a free place."""
    free = [r for r in found if r[3] < r[4] and (not level or r[0] <= level)]
    if want:
        free = [r for r in free if want.lower() in ("%s %s" % (r[1], r[2])).lower()
                or want.lower() in r[1].lower()]
    return max(free, key=lambda r: (r[0], -r[3]), default=None)


def _manager(b):
    _talk(b, MANAGER)


def practice_left(text):
    """Seconds of practice time the Manager says are left, or None."""
    m = re.search(r"(?:still have|remaining practice time for today is)\s+(?:(\d+)\s*hours?)?\s*(?:(\d+)\s*minutes?)?"
                  r"\s*(?:(\d+)\s*seconds?)?", text, re.I)
    if not m or not any(m.groups()):
        return None
    h, mi, s = (int(x or 0) for x in m.groups())
    return h * 3600 + mi * 60 + s


def _summon(b, cfg):
    """The assistant that practises for us: the player's choice (1-10), once per entry."""
    _manager(b)
    choices = _texts(b, r"^\s*Summoned\b")
    if not choices:
        return _has(b, r"assistant has killed")       # one is out already
    i = min(max(1, int(cfg.get("assistant") or 1)), len(choices)) - 1
    b.choose(r"^\s*%s\s*$" % re.escape(choices[i]))
    _confirm(b)
    b.log(_("secret room: {who}", who=choices[i]))
    return True


def _share(b, cfg):
    """Share arnebia once, with the first of the player's names in the room (or anyone, if they
    allow it). (the shares made today as the game counts them, or None when it didn't say; whether
    this one was made)."""
    _manager(b)
    if b.choose(r"^\s*Share Arnebia") is None:
        return None, False
    b.wait_dialog(3.0)
    m = re.search(r"Choose a player;\s*(\d+)\s*/\s*(\d+)", town._menu_text(b))
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
    _confirm(b)
    b.log(_("secret room: shared arnebia with {name}", name=pick[0]), speak=False)
    return (made or 0) + 1, True


def _enter_room(b, cfg, check_only):
    """From an instructor into the chosen secret room. (entered, what to say)."""
    town.visit(b, "instructor")
    if b.choose(r"^\s*Secret room training") is None:
        return False, _("the instructor offers no secret room training")
    b.wait_dialog(3.0)
    if b.choose(r"^\s*Enter the secret room to practice") is None:
        return False, _("the instructor offers no secret room now")
    b.wait_dialog(3.0)
    text = town._menu_text(b)
    today = re.search(r"training time is ([^,|]+), and you can enter (\d+) more times", text, re.I)
    found = rooms(_texts(b, r"secret room"))
    room = pick_room(found, _level(b), (cfg.get("room") or "").strip())
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
    _confirm(b)
    if not b.wait_map_change(mid, 15):
        b.close_dialogs()
        return False, _("couldn't get into the secret room")
    return True, "%s %d" % (room[1], room[2])


def slot(t):
    """Which half hour of the clock t is in: each takes one share."""
    return int(t // SLOT)


def to_next_slot(t):
    return SLOT - t % SLOT


def shares(name):
    """(shares made today, the half hours they were made in)."""
    s = done_today(name).get("_shares") or {}
    return int(s.get("count", 0)), set(s.get("slots", []))


def _note_share(name, count, t):
    made, used = shares(name)
    used.add(slot(t))
    put(name, "_shares", {"count": max(made, count), "slots": sorted(used)})


def _share_here(b, cfg):
    """One share in this half hour, unless today's are made or this half hour's is. The day's count."""
    name = getattr(b, "character", None)
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


def _wait_slot(b):
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


def share_visit(b: Any, cfg: dict[str, Any], practise: int = 0) -> tuple[int, str]:
    """One visit: into a room, an assistant out (the game lets us share only then), a share in this
    half hour, another as the next one starts when that is near (or every half hour while
    practising for `practise` minutes), then out. (shares made today, where)."""
    name = getattr(b, "character", None)
    b.share_tried = slot(b.clock.time())
    where = atlas.label(b.map_id())
    if atlas.kind(b.map_id()) != "secret_room":
        entered, where = _enter_room(b, cfg, False)
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


def trip_due(b: Any) -> bool:
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


def secret_room(b, cfg, check_only):
    """A visit to share arnebia (and practise, when practice_minutes is set). Done for the day once
    the day's five shares are made."""
    if check_only:
        if atlas.kind(b.map_id()) == "secret_room":
            _manager(b)
            left = practice_left(town._menu_text(b))
            return _("in {room}; practice time left: {m} minutes", room=atlas.label(b.map_id()),
                     m="?" if left is None else left // 60), False
        return _enter_room(b, cfg, True)[1], False
    made, where = share_visit(b, cfg, max(0, int(cfg.get("practice_minutes") or 0)))
    return _("{room}: shared arnebia {n} of {all} times today", room=where, n=made, all=SHARES_A_DAY), \
        made >= SHARES_A_DAY


DO = {"bank": bank, "bounty": bounty, "pet_rebirth": pet_rebirth, "donation": donation,
      "construction": construction, "secret_room": secret_room}
