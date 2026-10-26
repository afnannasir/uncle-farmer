"""Gathering: fishing, mining and collecting herbs, then selling what they bring in.

What the game says about it (its help pages and texts, and sessions logged in the game):
  - A character learns one production skill (Mining, Collect Herbs or Fishing, at the gang's Guru
    Elder or Master Pepper) and it ranks up: Newbie, Oldhand, Elite, Expert, Master, Grand Master.
    The server's login list carries the rank ("13,5,Fishing"); agent 49 keeps it for us
    (`mem skills` profession_ranks), the client itself drops it.
  - The skill works only in its own areas: waters, mining areas, herbal gardens, and the gang's
    production area. It is started from the bag's Profession skills panel (the skill, the use key,
    "Confirm"); from then on the game casts it by itself, about every two seconds (IN op=41 "<our
    id>,<n>,Fishing"), and each cast puts its catch into the bag ("4 Ice Crab"). It stops when we
    move or fight, when the bag is too heavy ("overweight"), or when Vitality runs out ("lack of
    vitality"); more Vitality costs diamonds, which the bot never spends.
  - The gang's production area (Lake, Mining Area, Herbal Garden): its entrance stands in the
    inner hall ("lake of the gang; level 3; saturation 99.9%", "Entry costs 0 arnebia", "Enter"),
    one entry a day for a fee the gang sets; after 30 minutes the game puts the player back
    outside. The higher its level and saturation (best above 50%), the more it gives.
  - Buyers: the Aquaculture, Ore and Herb Buyers in the capitals (town.sell_collection).

Places: gangs' areas first, each once a game day, in the player's order - our own gang's and other
gangs' by name (players gather in friends' and allied gangs too, which find them through the Gang
Entrance's "all gangs" list); then outside, at one of the player's own places (Tools, Gathering:
"Add where I stand") or one where the game has been seen to let players gather
(data/gathering.json). The best is the highest-level area whose monsters the character can take;
a place where gathering never starts is remembered for the skill's rank and passed over until the
rank rises.
"""

import math
import os
import re
from typing import Any

from .. import atlas, jsonio as json, paths
from ..i18n import N_, _
from . import beasts, chores, crafting, town
from .bot import CONFIRM, Respawned, Stopped

SKILLS: dict[str, dict[str, str]] = {
    "Fishing": {"kind": "fish", "entrance": "Lake Entrance"},
    "Mining": {"kind": "ore", "entrance": "Mining Area Entrance"},
    "Collect Herbs": {"kind": "herb", "entrance": "Herbal Garden Entrance"},
}
SKILL_NAMES = (N_("Fishing"), N_("Mining"), N_("Collect Herbs"))     # SKILLS' names, for translators
RANKS = (N_("Newbie"), N_("Oldhand"), N_("Elite"), N_("Expert"), N_("Master"), N_("Grand Master"))
ENTRANCE_CELLS = {"Lake Entrance": (2, 40)}   # seen in a gang base; every base has the same layout
GANG_AREAS = range(4201, 4224)                # the production areas' maps: mine valleys, gardens, lakes
PANEL = 6                 # the bag's Profession skills panel
START_WAIT = 6.0          # seconds a start has to bring its first cast
QUIET = 9.0               # no cast of ours for this long (the game casts every ~2 s): it stopped
LOOK = 1.0                # seconds between looks while gathering
STARTS = 3                # starts in a row that bring no cast: the place doesn't take us
LEFT_MOST = 5             # times the character may be moved off the place (deaths...) in one run
GANG_DONE = "_gang_area"  # today's gang area visit in the chores' done list
VITALITY = re.compile(r"lack of vitality", re.I)
OVERWEIGHT = re.compile(r"overweight", re.I)
AREA_LINE = re.compile(r"level\s+(\d+)\s*;\s*saturation\s+([\d.]+)\s*%", re.I)
FEE = re.compile(r"Entry costs\s+(\d+)\s+arnebia", re.I)
DEFAULTS: dict[str, Any] = {
    "gang": True,             # gangs' production areas first, each once a game day
    "gangs": [""],            # ... these, in order: "" our own gang, else a gang's name
    "gang_fee_max": 20000,    # ... for an entry fee of at most this
    "outside": True,          # outside places (after the gang's area, or without one)
    "place": "",              # "": the best for the character; else a place's name
    "places": [],             # the player's own: {"name", "skill", "map", "x", "y"}
    "known_places": True,     # the places the bot knows from the game
    "above": 5,               # an area's monsters at most this many levels above the character
    "minutes": 0,             # 0: until Vitality runs out
    "stop_vitality": 0,       # outside: stop once the sheet's Vitality is down to this (0: until it runs out)
    "sell": True,             # sell the collection at the buyer
    "when_full": "sell",      # the bag full outside: "sell" and come back, or "stop"
    "free_load": 1000,        # before setting out: less room left than this (load) is a full bag
    "refused": {},            # place key -> the rank the game didn't let us gather there at
    "entrances": {},          # gang area entrance -> [x, y], once found
}


def config(p: dict[str, Any]) -> dict[str, Any]:
    """The profile's gathering settings, with every setting filled in."""
    return dict(DEFAULTS, **(p.get("gathering") or {}))


def rank_name(rank: int | None) -> str:
    if rank is None or not 0 <= rank < len(RANKS):
        return _("rank unknown")
    return _(RANKS[rank])


def kind_of(skill: str) -> str:
    return SKILLS[skill]["kind"]


# ---------------------------------------------------------------------------------------- the skill
def skill_of(skills: dict[str, Any]) -> tuple[str | None, int | None]:
    """(the character's gathering skill, its rank or None) from a `mem skills` answer."""
    ranks = skills.get("profession_ranks") or {}
    for r in skills.get("profession") or []:
        if r.get("name") in SKILLS:
            rank = ranks.get(str(r.get("id")))
            return r["name"], (int(rank) if rank is not None else None)
    return None, None


def read_skill(b: Any) -> tuple[str | None, int | None]:
    sk = b.a.json("mem skills")
    return skill_of(sk if isinstance(sk, dict) and "error" not in sk else {})


# ---------------------------------------------------------------------------------------- places
def place_key(p: dict[str, Any]) -> str:
    return "%s:%d:%d,%d" % (p["skill"], int(p["map"]), int(p["x"]), int(p["y"]))


def area_level(mid: int, x: float, y: float) -> int | None:
    """The level of the area a cell is in: its monster zone's, or the nearest zone's on that map."""
    best = None
    for z in atlas.zones(mid):
        lv = z.get("level")
        if lv is None:
            continue
        x0, y0, x1, y1 = z["rect"]
        dx = max(x0 - x, 0, x - x1)
        dy = max(y0 - y, 0, y - y1)
        d = math.hypot(dx, dy)
        if best is None or d < best[0] or (d == best[0] and lv > best[1]):
            best = (d, lv)
    return best[1] if best else None


def known_places(skill: str) -> list[dict[str, Any]]:
    """The places where the game has been seen letting players gather with this skill."""
    try:
        with open(os.path.join(paths.DATA, "gathering.json"), encoding="utf-8") as f:
            rows = json.load(f).get("places", [])
    except (OSError, ValueError):
        return []
    return [dict(r, source="known") for r in rows if r.get("skill") == skill]


def label(p: dict[str, Any]) -> str:
    """How a place is named to the player."""
    if p.get("name"):
        return p["name"]
    return _("{map} at {x},{y}", map=atlas.label(int(p["map"])), x=int(p["x"]), y=int(p["y"]))


def places(cfg: dict[str, Any], skill: str) -> list[dict[str, Any]]:
    """Every outside place for the skill, the player's own first, each with its area's level."""
    own: list[dict[str, Any]] = [dict(p, source="mine") for p in cfg.get("places") or [] if p.get("skill") == skill]
    known = known_places(skill) if cfg.get("known_places", True) else []
    seen = {place_key(p) for p in own}
    out: list[dict[str, Any]] = []
    for row in own + [k for k in known if place_key(k) not in seen]:
        if int(row["map"]) in GANG_AREAS:
            continue              # a gang's area is entered by its entrance, never walked to
        p: dict[str, Any] = dict(row)
        p.setdefault("level", area_level(int(p["map"]), float(p["x"]), float(p["y"])))
        p["label"] = label(p)
        out.append(p)
    return out


def usable(p: dict[str, Any], level: int | None, rank: int | None, cfg: dict[str, Any]) -> bool:
    """Not refused at this rank (a better rank tries again), its monsters within reach."""
    refused = (cfg.get("refused") or {}).get(place_key(p))
    if refused is not None and (rank is None or rank <= int(refused)):
        return False
    lv = p.get("level")
    return not (level and lv and lv > level + int(cfg.get("above", 5)))


def choose(found: list[dict[str, Any]], level: int | None, rank: int | None,
           cfg: dict[str, Any]) -> dict[str, Any] | None:
    """The place to gather at: the one the player named, else the highest-level area the
    character can take, the player's own before the known ones."""
    want = (cfg.get("place") or "").strip().lower()
    if want:
        named = [p for p in found if p["label"].lower() == want]
        if named:
            return named[0] if usable(named[0], level, rank, cfg) or named[0]["source"] == "mine" else None
    ok = [p for p in found if usable(p, level, rank, cfg)]
    return max(ok, key=lambda p: (p.get("level") or 0, p["source"] == "mine"), default=None)


def refuse(b: Any, cfg: dict[str, Any], p: dict[str, Any], rank: int | None) -> None:
    """Remember a place that didn't take us, for this rank, in the profile."""
    cfg.setdefault("refused", {})[place_key(p)] = -1 if rank is None else rank
    _save(b, cfg)


def _save(b: Any, cfg: dict[str, Any]) -> None:
    b.p["gathering"] = cfg
    save = getattr(b, "save_profile", None)
    if save:
        save(b.p)


# ---------------------------------------------------------------------------------------- the game's answers
def _ms(b: Any) -> int:
    return int(b.clock.time() * 1000)


def own_casts(b: Any, skill: str, since: int, me: int | None) -> list[dict[str, Any]]:
    """Our own casts of the skill since a moment (ms): the game gathering for us. me: our id."""
    r = b.a.json("casts %d" % since)
    if not isinstance(r, dict) or "error" in r:
        return []
    return [c for c in r.get("casts", []) if c.get("skill") == skill and (me is None or c.get("caster") == me)]


def said(b: Any, since: int) -> list[str]:
    """What the game said (IN op=60) since a moment (ms): "lack of vitality", "overweight"..."""
    r = b.a.json("said %d" % since)
    if not isinstance(r, dict) or "error" in r:
        return []
    return [s.get("text", "") for s in r.get("said", [])]


def bag_load(b: Any, force: bool = False) -> tuple[int, int] | None:
    """(load, most it takes) of the bag, when the sheet says (read now and then, or now: force)."""
    sheet = b.refresh_sheet(force=force) or {}
    if sheet.get("maxload", 0) > 0 and sheet.get("load", -1) >= 0:
        b.load_share = 100.0 * sheet["load"] / sheet["maxload"]   # the sheet is read sooner near full
        return int(sheet["load"]), int(sheet["maxload"])
    return None


def bag_full(b: Any) -> bool:
    """Full while gathering: past the profile's bag_full_at percent (the game's own "overweight"
    ends it too)."""
    got = bag_load(b)
    return got is not None and 100.0 * got[0] / got[1] >= b.p.get("bag_full_at", 100)


def vitality(b: Any, force: bool = False) -> float | None:
    """The Vitality left, from the character sheet (agent 50: "Vitality 44.7"); None when unknown."""
    v = (b.refresh_sheet(force=force) or {}).get("vitality")
    return float(v) if v is not None and v >= 0 else None


def vitality_kept(b: Any, cfg: dict[str, Any], force: bool = False) -> float | None:
    """The Vitality left when it is down to the player's stop mark (stop_vitality), else None."""
    mark = float(cfg.get("stop_vitality") or 0)
    if mark <= 0:
        return None
    v = vitality(b, force)
    if v is None and not getattr(b, "said_no_vitality", False):
        b.said_no_vitality = True
        b.log(_("the character sheet shows no Vitality yet (restart the game so it loads the new agent) - "
                "gathering until it runs out"), speak=True)
    return v if v is not None and v <= mark else None


def no_room(b: Any, cfg: dict[str, Any]) -> tuple[int, int] | None:
    """Looked at before setting out to gather: (load, most) when less than free_load is left, the
    safe room for a gathering trip; None when there is room (or the sheet can't be read)."""
    got = bag_load(b, force=True)
    if got is None or got[1] - got[0] >= int(cfg.get("free_load", 1000)):
        return None
    return got


def warn_full(b: Any, got: tuple[int, int]) -> str:
    msg = _("the bag is full: load {load} of {max}", load=got[0], max=got[1])
    b.log(msg, speak=True)
    return msg


# ---------------------------------------------------------------------------------------- starting
START = r"(?i)^\s*start\b"
STOP = r"(?i)^\s*stop\b"


def start(b: Any, skill: str) -> bool:
    """Start the skill from the bag's Profession skills panel: the cursor onto it, the use key
    (its menu: "Start <skill>"), then "Confirm". From then on the game goes on by itself until it
    is stopped or another skill or item is used (outside, a potion or a fight does; it is started
    again after). A menu
    that offers "Stop" instead means it is running already: closed, nothing chosen. True when the
    game took the start (whether it casts is seen from the casts that follow)."""
    sk = b.a.json("mem skills")
    rows = (sk.get("profession") or []) if isinstance(sk, dict) else []
    index = next((i for i, r in enumerate(rows) if r.get("name") == skill), None)
    if index is None:
        return False
    b.close_dialogs()
    b.close_text_box()
    if not b.panel_cursor(PANEL, index, len(rows)):
        return False
    b.key(b.p.get("use_key") or "rctrl")
    d = b.wait_dialog(1.5)
    if not d or not d.get("top"):
        return True                       # no menu: the use key started it straight away
    if b.find_entry(d["top"], START) is None:
        b.note_dialog(d, "starting %s" % skill)
        b.close_dialogs()                 # "Stop": running already; anything else: not ours
        return b.find_entry(d["top"], STOP) is not None
    b.choose(START)
    for _i in range(2):                   # "Confirm" (and an "are you sure?", if the game asks)
        d = b.wait_dialog(1.5)
        if not d or not d.get("top") or b.find_entry(d["top"], CONFIRM) is None:
            break
        b.choose(CONFIRM)
    b.close_dialogs()
    return True


def gather_here(b: Any, cfg: dict[str, Any], skill: str, place: dict[str, Any] | None,
                end_at: float | None) -> str:
    """Gather where we stand until something ends it. place: the outside place, None in a gang's
    area. A gang's area is left to the game for its half hour: started, and only watched for its
    end (no potions, no fights there: a key would stop the gathering). Outside, monsters come: the
    bot looks after itself the whole time - potions, heals - fights whatever attacks it, walks
    back onto the spot and starts again.
    Returns why it ended: "vitality", "kept" (outside, down to the player's Vitality mark), "full",
    "time", "left" (we are on another map: the gang's half hour is over, or we died), "refused"
    (the game never started it here) or "lost" (pushed off the place and couldn't walk back)."""
    mid = b.map_id()
    seen_from = said_from = _ms(b)
    last_cast = None
    tries, tried_at = 0, -math.inf
    casts = 0
    b.log(_("{skill} at {where}", skill=_(skill), where=place["label"] if place else atlas.label(mid)))
    while True:
        b.check()
        now = b.clock.time()
        if end_at is not None and now >= end_at:
            return "time"
        if b.map_id() != mid:
            return "left"
        snap = b.snap()
        if place is not None or snap["self"].get("hp", 1) <= 0:
            b.look_after(snap["self"])    # outside: potions and heals as in any fight; a death anywhere
        why = _stop_reason(b, cfg, place, said_from)
        said_from = _ms(b)
        if why:
            return why
        if place is not None and b.attackers():
            b.fight_back()
            last_cast, tries = None, 0
            if place is not None and not _back_to(b, place):
                return "lost"
            continue
        got = own_casts(b, skill, seen_from, snap["self"].get("id"))
        seen_from = _ms(b)
        if got:
            casts += len(got)
            b.gather_casts = getattr(b, "gather_casts", 0) + len(got)
            last_cast, tries = now, 0
        elif (last_cast is None or now - last_cast > QUIET) and now - tried_at > START_WAIT:
            if tries >= STARTS:
                return "refused"
            tries += 1
            tried_at = now
            if casts:
                b.log(_("{skill} stopped - starting it again", skill=_(skill)))
            start(b, skill)
            continue
        b.sleep(LOOK)


def _stop_reason(b: Any, cfg: dict[str, Any], place: dict[str, Any] | None, said_from: int) -> str | None:
    """What ends gathering now, if anything: the game saying Vitality ran out or the bag is too
    heavy (since said_from), the bag past bag_full_at, or - outside - the Vitality the player keeps."""
    for line in said(b, said_from):
        if VITALITY.search(line):
            return "vitality"
        if OVERWEIGHT.search(line):
            return "full"
    if bag_full(b):
        return "full"
    if place is not None and vitality_kept(b, cfg) is not None:
        return "kept"
    return None


def _back_to(b: Any, place: dict[str, Any]) -> bool:
    """After a fight: back onto the place's own cell (a step off it may be out of the water)."""
    me = b.me()
    if b.map_id() == int(place["map"]) and math.hypot(me["x"] - place["x"], me["y"] - place["y"]) <= 1.5:
        return True
    try:
        b.travel(int(place["map"]), (int(place["x"]), int(place["y"])))
    except Stopped as e:
        if b.stop_event.is_set() or getattr(e, "final", False):
            raise
        b.log(_("couldn't get back to {place}: {why}", place=place["label"], why=e))
        return False
    return True


# ---------------------------------------------------------------------------------------- the gang's area
def gangs(cfg: dict[str, Any]) -> list[str]:
    """The gangs to gather in, in the player's order: "" is the character's own gang, else a
    gang's name. Each once, whatever the list says."""
    out: list[str] = []
    for g in cfg.get("gangs") or [""]:
        g = (g or "").strip()
        if g.lower() not in [x.lower() for x in out]:
            out.append(g)
    return out


def gang_name(gang: str) -> str:
    return gang or _("our own gang")


def _gang_key(gang: str) -> str:
    """The gang's entry in the chores' done list: each gang's area is entered once a game day."""
    return GANG_DONE + (":" + gang.lower() if gang else "")


def gang_done_today(b: Any, gang: str = "") -> dict[str, Any] | None:
    return chores.done_today(getattr(b, "character", None)).get(_gang_key(gang))


def _into_gang(b: Any, gang: str) -> None:
    """Into a gang's base where its production area entrances stand: our own gang's inner hall, or
    the gang named `gang`, found by name at the Gang Entrance ("all gangs"), its inner hall when the
    game offers it to us, else its outer hall."""
    if not gang:
        chores._inner_hall(b)
        return
    b.close_dialogs()
    town.visit(b, "gang_entrance")
    if not crafting.pick_at_entrance(b, gang):
        raise Stopped(_("the Gang Entrance lists no gang called {gang}", gang=gang))
    mid = b.map_id()
    d = b.wait_dialog(2.0)
    for hall in (r"(?i)inner\s+hall", r"(?i)outer\s+hall", r"(?i)^\s*enter\b"):
        if d and d.get("top") and b.find_entry(d["top"], hall) is not None:
            b.choose(hall)
            break
    chores._confirm(b)
    if not b.wait_map_change(mid, 15) or not crafting.in_gang(b):
        b.close_dialogs()
        raise Stopped(_("couldn't get into the gang {gang}'s base", gang=gang))


def _entrance(b: Any, cfg: dict[str, Any], name: str) -> tuple[float, float] | None:
    """Where the gang area's entrance stands in the part of the base we are in: known, in sight,
    or found by a walk round it (and remembered: every gang base has the same layout)."""
    cell = (cfg.get("entrances") or {}).get(name) or ENTRANCE_CELLS.get(name)
    if cell and crafting.reachable_cell(b, (int(cell[0]), int(cell[1]))):
        return float(cell[0]), float(cell[1])
    for stop in [None] + list(chores.INNER_SWEEP) + list(chores.OUTER_SWEEP):
        if stop is not None:
            if not crafting.reachable_cell(b, stop):
                continue                  # the other half of the base, walled off from ours
            b.walk_to(stop)
        npc = b.find_npc(name)
        if npc is not None:
            cfg.setdefault("entrances", {})[name] = [int(npc["x"]), int(npc["y"])]
            _save(b, cfg)
            return float(npc["x"]), float(npc["y"])
    return None


def gang_area(b: Any, cfg: dict[str, Any], skill: str, check_only: bool = False,
              gang: str = "") -> tuple[bool, str, bool]:
    """Into a gang's production area for the skill (gang: "" our own, else that gang by name),
    through its entrance in the base. (in, what happened, whether today's visit there is over: in,
    or the game itself said no). check_only reads the entrance's menu and enters nothing."""
    name = SKILLS[skill]["entrance"]
    _into_gang(b, gang)
    cell = _entrance(b, cfg, name)
    if cell is None:
        return False, _("the gang has no {npc}", npc=name), True
    if not b.talk(name, cell):
        return False, _("couldn't talk to the {npc}", npc=name), False
    b.wait_dialog(3.0)
    text = town._menu_text(b)
    area = AREA_LINE.search(text)
    fee_m = FEE.search(text)
    fee = int(fee_m.group(1)) if fee_m else 0
    about = _("level {level}, saturation {sat}%, entry {fee} arnebia",
              level=area.group(1) if area else "?", sat=area.group(2) if area else "?", fee=fee)
    if b.find_entry(b.dialog().get("top"), r"^\s*Enter\s*$") is None:
        b.note_dialog(b.dialog(), "gang area entrance")
        b.close_dialogs()
        return False, _("the {npc} offers no way in ({about})", npc=name, about=about), True
    if check_only:
        b.close_dialogs()
        return False, _("the gang's area: {about}", about=about), False
    if fee > int(cfg.get("gang_fee_max", 0)):
        b.close_dialogs()
        return False, _("the gang's area costs {fee} arnebia, more than your limit of {max}", fee=fee,
                        max=int(cfg.get("gang_fee_max", 0))), False
    if fee and crafting.spare_arnebia(b) < fee:
        b.close_dialogs()
        return False, _("not enough arnebia above your reserve for the gang area's {fee} arnebia", fee=fee), False
    mid = b.map_id()
    since = _ms(b)
    b.choose(r"^\s*Enter\s*$")
    chores._confirm(b)
    if not b.wait_map_change(mid, 15):
        b.close_dialogs()
        why = "; ".join(said(b, since)) or _("no reason given")
        return False, _("the gang's area didn't let us in: {why}", why=why), True
    return True, about, True


# ---------------------------------------------------------------------------------------- the run
def _sell(b: Any, cfg: dict[str, Any], skill: str) -> int:
    if not cfg.get("sell", True):
        return 0
    try:
        return town.sell_collection(b, (kind_of(skill),))
    except Stopped as e:
        if b.stop_event.is_set() or getattr(e, "final", False):
            raise
        b.log(_("couldn't sell the collection: {why}", why=e), speak=True)
        return 0


def _holding(b: Any, skill: str) -> bool:
    """The bag holds something of the skill's kind: by the item's name, or because the game has
    shown that kind's buyer buying it ("Butterfly shell" is no fish by its name)."""
    from .. import knowledge
    kind = kind_of(skill)
    role = town.COLLECTION_BUYERS[kind]
    buyers = {n["name"] for n in knowledge.data().get("npcs", []) if n.get("role") == role}
    for r in b.bag_rows():
        name = r.get("name")
        if not r.get("count") or not name:
            continue
        if knowledge.category(name) == kind or any(t[1] in buyers for t in knowledge.traders(name, "buy")):
            return True
    return False


def check(b: Any, cfg: dict[str, Any], skill: str, rank: int | None, level: int | None) -> list[str]:
    """What a run would do, walking only to read the gang area's entrance."""
    notes = [_("{skill}, {rank}; character level {level}", skill=_(skill), rank=rank_name(rank),
               level=level or "?")]
    for gang in gangs(cfg) if cfg.get("gang", True) else []:
        done = gang_done_today(b, gang)
        if done:
            notes.append(_("{gang}: already today ({note})", gang=gang_name(gang), note=done.get("note", "")))
            continue
        try:
            notes.append(_("{gang}: {about}", gang=gang_name(gang),
                           about=gang_area(b, cfg, skill, check_only=True, gang=gang)[1]))
        except Stopped as e:
            if b.stop_event.is_set() or getattr(e, "final", False):
                raise
            notes.append(_("{gang}: {about}", gang=gang_name(gang), about=e))
    if cfg.get("outside", True):
        found = places(cfg, skill)
        p = choose(found, level, rank, cfg)
        notes.append(_("outside: {place} (area level {level})", place=p["label"], level=p.get("level") or "?")
                     if p else _("outside: no place for this character - add one where you stand"))
    if cfg.get("sell", True):
        role = town.COLLECTION_BUYERS[kind_of(skill)]
        from .. import knowledge
        notes.append(_("selling to the nearest {role}", role=role.replace("_", " "))
                     if knowledge.service_npcs(role) else _("no {role} known yet", role=role.replace("_", " ")))
    return notes


def run(b: Any, cfg: dict[str, Any], check_only: bool = False) -> list[str]:
    """Gather as the settings say: the gang's area once a game day, then outside, selling the
    collection when the bag fills and at the end. Returns what happened, in a few lines."""
    skill, rank = read_skill(b)
    if skill is None:
        raise Stopped(_("this character has no gathering skill: learn Fishing, Mining or Collect Herbs "
                        "(one of them) from the gang's Guru Elder or Master Pepper"))
    level = chores._level(b) or None
    if check_only:
        notes = check(b, cfg, skill, rank, level)
        b.close_dialogs()
        b.log(_("gathering check: {notes}", notes="; ".join(notes)), speak=True)
        return notes
    b.log(_("gathering with {skill} ({rank})", skill=_(skill), rank=rank_name(rank)), speak=True)
    if getattr(b, "bag_start", None) is None:
        b.bag_start = town.counts(b)
    minutes = int(cfg.get("minutes") or 0)
    end_at = b.clock.time() + 60 * minutes if minutes else None
    notes: list[str] = []
    why = ""
    for gang in gangs(cfg) if cfg.get("gang", True) else []:
        if gang_done_today(b, gang):
            continue
        why = _gang_round(b, cfg, skill, end_at, notes, gang)
        if why in ("vitality", "time", "full"):
            break
    if why not in ("vitality", "time", "full") and cfg.get("outside", True):
        why = _outside(b, cfg, skill, rank, level, end_at, notes)
    if getattr(b, "gather_casts", 0) or _holding(b, skill):
        _sell(b, cfg, skill)
    if why == "vitality":
        notes.append(_("Vitality is used up for today"))
    elif why == "kept":
        notes.append(_("stopped with {n} Vitality left, as you set", n=vitality(b) or cfg.get("stop_vitality")))
    elif why == "time":
        notes.append(_("{n} minutes are up", n=minutes))
    b.log(_("gathering finished: {notes}", notes="; ".join(notes) or _("nothing done")), speak=True)
    return notes


def _gang_round(b: Any, cfg: dict[str, Any], skill: str, end_at: float | None, notes: list[str],
                gang: str = "") -> str:
    """Today's visit to one gang's area. Why it ended ("" when we never got in)."""
    if cfg.get("sell", True) and _holding(b, skill):
        _sell(b, cfg, skill)              # the half hour inside can't be left to sell and come back
    if not _room(b, cfg, skill, notes):
        return "full"
    try:
        entered, about, over = gang_area(b, cfg, skill, gang=gang)
    except Stopped as e:
        if b.stop_event.is_set() or getattr(e, "final", False):
            raise
        entered, about, over = False, str(e), False
    about = _("{gang}: {about}", gang=gang_name(gang), about=about)
    if over:
        chores.mark(getattr(b, "character", None), _gang_key(gang), about)
    if not entered:
        b.close_dialogs()
        b.log(about)
        notes.append(about)
        return ""
    beasts.off(b)
    why = ""
    for _round in range(LEFT_MOST):
        try:
            why = gather_here(b, cfg, skill, None, end_at)
            break
        except Respawned:
            why = "left"
            break                          # back from a death outside the area: the half hour is lost
    if why == "refused":
        b.log(_("the game didn't start {skill} in the gang's area", skill=_(skill)), speak=True)
    if b.map_id() in GANG_AREAS and why not in ("vitality", "time"):
        # out to sell or to gather outside; with Vitality or the time used up, the half hour ends it
        if why == "full":
            b.log(_("the bag is full - leaving the gang's area"), speak=True)
        if not b.leave_instance():
            b.log(_("couldn't leave the gang's area - waiting for its half hour to end"))
            while b.map_id() in GANG_AREAS:
                b.check()
                b.look_after()
                if b.attackers():
                    b.fight_back()
                b.sleep(5.0)
    notes.append(about)
    return why if why in ("vitality", "time") else ""


def _room(b: Any, cfg: dict[str, Any], skill: str, notes: list[str]) -> bool:
    """Before setting out to gather: a look at the bag's load. With less than free_load left it
    says the bag is full, sells first when the player lets it, and looks again. False when there
    still isn't room."""
    got = no_room(b, cfg)
    if got is None:
        return True
    msg = warn_full(b, got)
    if cfg.get("sell", True):
        _sell(b, cfg, skill)
        got = no_room(b, cfg)
        if got is None:
            return True
        msg = warn_full(b, got)
    notes.append(msg)
    return False


def _ready(b: Any, cfg: dict[str, Any], skill: str, notes: list[str]) -> str:
    """Before setting out to an outside place: room in the bag ("full" when there is none, even
    after selling) and Vitality above the player's mark ("kept"). "" when ready."""
    if not _room(b, cfg, skill, notes):
        return "full"
    v = vitality_kept(b, cfg, force=True)
    if v is not None:
        b.log(_("Vitality is down to {n}, where you set gathering outside to stop", n=v), speak=True)
        return "kept"
    return ""


def _outside(b: Any, cfg: dict[str, Any], skill: str, rank: int | None, level: int | None,
             end_at: float | None, notes: list[str]) -> str:
    """Gather at the outside places, one after another as they refuse us. Why it ended."""
    left = 0
    why = ""
    unreachable: set[str] = set()         # places the way to failed this run
    while True:
        b.check()
        if end_at is not None and b.clock.time() >= end_at:
            return "time"
        p = choose([q for q in places(cfg, skill) if place_key(q) not in unreachable], level, rank, cfg)
        if p is None:
            msg = _("no outside place to gather at for this character - stand on one and add it in "
                    "Tools, Gathering")
            b.log(msg, speak=True)
            notes.append(msg)
            return why
        ready = _ready(b, cfg, skill, notes)
        if ready:
            return ready
        try:
            b.travel(int(p["map"]), (int(p["x"]), int(p["y"])))
        except Respawned:
            continue
        except Stopped as e:
            if b.stop_event.is_set() or getattr(e, "final", False):
                raise
            b.log(_("couldn't get to {place}: {why}", place=p["label"], why=e), speak=True)
            unreachable.add(place_key(p))
            continue
        try:
            beasts.off(b)
            why = gather_here(b, cfg, skill, p, end_at)
        except Respawned:
            why = "left"
        if why == "refused":
            b.log(_("the game doesn't let us gather at {place} - trying another place", place=p["label"]), speak=True)
            refuse(b, cfg, p, rank)
            continue
        if why in ("left", "lost"):
            left += 1
            if left >= LEFT_MOST:
                notes.append(_("moved off {place} too often", place=p["label"]))
                return why
            continue
        if why == "full":
            if cfg.get("sell", True) and cfg.get("when_full", "sell") == "sell":
                b.log(_("the bag is full - off to sell, then back to {place}", place=p["label"]))
                if _sell(b, cfg, skill) > 0:
                    continue
            notes.append(_("the bag is full"))
            return why
        notes.append(_("gathered at {place}", place=p["label"]))
        return why
