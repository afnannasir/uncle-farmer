"""Dungeons: the once-a-day caves, run for as many kills as their 15 minutes give.

What the game showed (hand runs logged 2026-10-27, one with a Hunter, one with a Warrior):
  - Scorpion Cave: its NPC "Scorpion Cave" stands at 171,184 on thousand miles of rivers; Spider
    cave: its NPC "Spider cave" at 54,4 on water curtained cave. Talking opens "<name>", "Enter
    the dungeon; Limited to Level 60 (Spider cave: 40) and above; The dungeon can be entered once
    a day" and "Check Dungeon Description". Entering lands on the dungeon's player spawn point,
    where no monster spawns (Scorpion Cave 155,90; Spider cave 90,178).
  - Inside there is a 15 minute timer (the Action menu's time key answers "9 minutes 22 seconds
    remaining"); at its end the server moves the character out by itself - no exit chest, nothing
    to choose. Scorpion Cave puts it back on thousand miles of rivers, Spider cave on central
    plains.
  - One kind of monster each ("Scorpion", "poisonous spider"), 44 to 48 of them at a time. One
    killed comes back about 30 seconds later at a random cell anywhere in the cave, so no corner
    fills faster than another: what kills most is never stopping - a loop through the whole cave,
    taking whatever is in reach, area skills on whatever is close together.
  - Pets are allowed (a pet was out in both runs); the loot was only Arnebia.
  - A character goes into ONE cave a game day, whichever it is, not one of each: the player
    chooses which (or the highest one the character's level lets in). Two more come at levels 80
    and 90: the Bottomless pit (its NPC "bottomless pit" at 150,19 on Valley of Luxuriant forests)
    and the White Bone Cave (its NPC "White Bone Cave" at 61,110 on ancient Sealed Land, reached
    by the cable car). The game's files mark only the pit's team entrances and not the White Bone
    Cave's at all, so their rows in CAVES carry the entrance. Each cave is one row in CAVES.
    The White Bone Cave was run by the bot (2026-11-14: 204 kills); the Bottomless pit is
    untested - its entrance is known, but no run of ours has gone in yet.
  - Turned away because the day's cave is used, the game says "You have already entered an
    experience dungeon today".

What the bot's own visits showed (2026-11-01 to 11-15, thirteen of them, field logs and packets):
  - Everything done after landing is taken off the 15 minutes. Every visit with a pet stood 11 to
    13 seconds on the spawn point first: a second for the game to settle, a second of target-mode
    presses, the pet called - and then the "get off the Swift beast" press, which the server
    ignores right after a pet came out (pets.keep_out), so 8.5 s more until the next one. The
    hand runs called the pet at the door, ten seconds before going in, and it came in with them.
  - The rest of a visit has no idle time worth the name: walking up to monsters (60-70% of a
    Hunter's visit, on foot at 4.1 tiles a second), the game's own 0.7 s stand after each cast,
    and 3 to 8 fights a visit that the server refuses or that end without a kill (3 to 6 s each).

So a visit is: the supplies topped up before setting out (the pet's blue potions and Rice Rolls,
our own blues and reds - a pet that fights and a lower character's potions are a good part of the
damage), the walk to the entrance, getting ready at the door (make_ready: the fight's beast, the
pet out, buffs up, keys filled - none of it on the timer), "Enter the dungeon", then the farming
fight in dungeon mode:
no trips out (the visit can't be taken up again), no resting (drink instead), area skills from two
monsters together, chasing whatever is within 90 steps (caveloops.py: the chase range, not the
loop, is what decides the kills here), until the game moves us out. The pet is the one chosen in
Tools, Dungeons, or none; by default the ones ticked in Character, Settings, and with none ticked
the pet that is out is looked after for the run - the game counts its kills as ours. One cave a
game day, written down in the chores' done list. Where the run ends is the script's end_at, as for
farming.
"""

from __future__ import annotations

import copy
import re
from typing import Any, cast

from .. import atlas, dungeons, jsonio, knowledge
from ..clock import clock_for
from ..link import AgentError
from ..i18n import N_, _
from . import beasts, chores, combat, keyslots, pets, town, xpcard
from .bot import Bot, Stopped
from .botparts.base import potion_kind

CAVES: dict[str, dict[str, Any]] = {          # lowest level first
    "Spider cave": {"map": 1503, "level": 40},
    "Scorpion Cave": {"map": 1539, "level": 60},
    # entrance: where the game's files (data/dungeons.json) don't say
    "Bottomless pit": {"map": 1636, "level": 80,
                       "entrance": {"map": 1631, "npc": "bottomless pit", "at": [150, 19]}},
    "White Bone Cave": {"map": 1792, "level": 90,
                        "entrance": {"map": 1761, "npc": "White Bone Cave", "at": [61, 110]}},
}
CAVE_NAMES = (N_("Scorpion Cave"), N_("Spider cave"), N_("Bottomless pit"),
              N_("White Bone Cave"))     # CAVES' names, for translators
ALREADY = r"already entered an experience dungeon today"    # the game's answer when the day's cave is used
BEST = "best"             # the cave setting's default: the highest one the character's level lets in
MINUTES = 15              # the game's timer inside
OVERTIME = 2.0            # minutes past the timer before leaving through the Action menu ourselves
CHASE = 90                # walking steps to chase a monster from the loop: farming's 40 left a
                          # caster 10-13% short in simulated visits (caveloops.py); the loop itself
                          # hardly matters with a monster nearly always that close
ENTER = r"^\s*Enter the dungeon"
LANDING = 10.0            # seconds the game has to move us in after "Enter the dungeon"
LANDING_LOOK = 0.1        # ... looked at this often: the timer runs from the landing
PET_WAIT = 180.0          # seconds to wait at the door for the run's pet to be ready to come out
                          # (one put away comes back in about two minutes; one that died takes 10
                          # to 30, and then we go in without it)
DOOR_LOOKS = 5            # looks at ourselves at the door, DOOR_LOOK apart: each puts up one buff
DOOR_LOOK = 0.6           # or drinks one potion, so none of it costs a cast inside
TRIES = 3                 # goes at the entrance before giving up (none is written down for the day)
RETRY_WAIT = 2.0          # seconds between two goes: a late move in shows meanwhile
IN, USED, MISSED = "in", "used", "missed"     # what enter() got
SETTLE = 5.0              # seconds to let the game finish moving us out before going on
SUPPLIES = ("pet_mp", "pet_food", "mp", "hp")
SUPPLY_NAMES = {"pet_mp": N_("Pet blue potions"), "pet_food": N_("Pet Rice Rolls"),
                "mp": N_("Our blue potions"), "hp": N_("Our red potions")}
SOURCES = ("both", "storage", "buy")
DEFAULTS: dict[str, Any] = {
    "cave": BEST,             # the one cave a game day: a CAVES name, or BEST
    "supplies": {"pet_mp": {"item": "", "min": 25},     # item "": the one on the hotkey (or the
                 "pet_food": {"item": "", "min": 3},    # strongest in the bag); min 0: not looked at
                 "mp": {"item": "", "min": 50},
                 "hp": {"item": "", "min": 30}},
    "source": "both",         # topping up: the storehouse, then the shop
    "when_short": "stop",     # still short after topping up: "stop", or "go" anyway
    "patrol": "quick",        # the loop through the cave: quick, full or short
    "pack": 2,                # area skills on this many monsters close together
    "chase": CHASE,           # walking steps to chase a monster from the loop
    "move_shots": True,       # a character that fights from afar shoots on the way, never stopping
    "pet": "",                # "": the pets ticked in Character, Settings (else the one out);
                              # NO_PET: none; else that pet, for the dungeons only
    "end": {"how": "start"},  # where the run ends, as a plan's end (plan.end_line)
}
NO_PET = "none"
PATROLS = ("quick", "full", "short")


def config(p: dict[str, Any]) -> dict[str, Any]:
    """The profile's dungeon settings, with every setting filled in."""
    mine: dict[str, Any] = p.get("dungeon") or {}
    cfg: dict[str, Any] = {**copy.deepcopy(DEFAULTS), **mine}   # never the defaults' own lists and dicts
    old: object = cfg.pop("caves", None)   # before one cave a day: a ticked list, the first was the one
    if "cave" not in mine and jsonio.is_array(old):
        cfg["cave"] = next((str(c) for c in old if str(c) in CAVES), BEST)
    if cfg["cave"] != BEST and cfg["cave"] not in CAVES:
        cfg["cave"] = BEST
    supplies: dict[str, dict[str, Any]] = {k: dict(v) for k, v in DEFAULTS["supplies"].items()}
    chosen: dict[str, Any] = cfg.get("supplies") or {}
    for k, v in chosen.items():
        if k in supplies and jsonio.is_object(v):
            supplies[k].update(v)
    cfg["supplies"] = supplies
    return cfg


def done_key(cave: str) -> str:
    return "_dungeon " + cave


def done_today(name: str | None) -> list[str]:
    """The caves this character has been into (or been turned away from) this game day: one at
    most, as the game lets a character into one a day."""
    done = chores.done_today(name)
    return [c for c in CAVES if done_key(c) in done]


def pick(cfg: dict[str, Any], level: int) -> str | None:
    """The cave to go to: the one chosen, or with BEST the highest one the level lets in (the
    highest of all when the level isn't known). None when the level is too low for any."""
    chosen = cfg.get("cave", BEST)
    if chosen in CAVES:
        return chosen
    able = [c for c, v in CAVES.items() if not level or level >= v["level"]]
    return max(able, key=lambda c: CAVES[c]["level"], default=None)


# ------------------------------------------------------------------------------------- supplies
def known_items(kind: str) -> list[str]:
    """The game's items a supply row can name (its potions of that kind, or the pet foods that
    raise amiability), weakest first as the shop lists them."""
    items: dict[str, dict[str, Any]] = knowledge.data().get("_items") or {}
    names: list[str] = [r["name"] for r in items.values() if r.get("name")]
    if kind == "pet_food":
        return [n for n in names if "amiability" in ((knowledge.item(n) or {}).get("description") or "").lower()
                or n.lower() == "pet rice roll"]
    return [n for n in names if potion_kind(n.lower()) == kind]


def script(cfg: dict[str, Any]) -> str:
    """The run the window starts: its end place, then the cave. A cave the player chose is named in
    the script, so the run goes where the window said - not where a profile saved earlier (or
    another copy of it) happens to point."""
    from .plan import end_line
    cave = cfg.get("cave")
    return end_line(cfg.get("end")) + "\ndungeon" + (" " + cave if cave in CAVES else "") + "\n"


def supply_item(b: Bot, kind: str, cfg: dict[str, Any]) -> str | None:
    """The item a supply row stands for: the player's choice, else the pet food the profile names
    (a Pet Rice Roll), else the potion the character drinks (on a hotkey, else the strongest in the
    bag); None when there is none to go by."""
    item = (cfg["supplies"][kind].get("item") or "").strip()
    if item:
        return item
    if kind == "pet_food":
        return (b.p.get("pet_food") or "Pet Rice Roll").strip()
    return b.potion(kind)[1]


def supply_rules(b: Bot, cfg: dict[str, Any]) -> tuple[list[dict[str, Any]], list[str]]:
    """Restock rules for the supplies to have before going ([{"item", "min", "stock", "source"}]),
    and what can't be looked at (a potion the character has none of and the player named none).
    The pet's rows only with a pet ticked in the profile, the blues only for a character that uses
    MP."""
    rules: list[dict[str, Any]] = []
    unknown: list[str] = []
    for kind in SUPPLIES:
        want = int(cfg["supplies"][kind].get("min") or 0)
        if want <= 0:
            continue
        if kind.startswith("pet_") and not pets.chosen(b):
            continue
        if kind == "mp" and not b.p.get("mp_use", True):
            continue
        item = supply_item(b, kind, cfg)
        if not item:
            unknown.append(_(SUPPLY_NAMES[kind]))
            continue
        rules.append({"item": item, "min": want, "stock": want, "on": True,
                      "source": cfg["source"] if cfg["source"] in SOURCES else "both"})
    return rules, unknown


def get_supplies(b: Bot, cfg: dict[str, Any], check_only: bool = False) -> list[str]:
    """Top up the supplies before setting out (check_only: only say what is short). What is still
    short afterwards, as "Pet Large Blue Potion 12/25"; with when_short "stop" that ends the run."""
    rules, unknown = supply_rules(b, cfg)
    for what in unknown:
        b.log(_("{what}: none in the bag and none chosen in Tools, Dungeons - not looked at", what=what),
              speak=True)
    if not check_only and town.needs(b, rules, True):
        town.restock(b, rules, start=True)
    short = ["%s %d/%d" % (r["item"], have, want) for r, have, want in town.needs(b, rules, True)]
    if short:
        b.log(_("short of supplies for the dungeon: {what}", what=", ".join(short)), speak=True)
        if not check_only and cfg["when_short"] == "stop":
            raise Stopped(_("not enough supplies for the dungeon"), final=True)
    elif rules:
        b.log(_("supplies for the dungeon: {what}", what=", ".join(
            "%s %d" % (r["item"], town.counts(b).get(r["item"].lower(), 0)) for r in rules)))
    return short


# ------------------------------------------------------------------------------------- a visit
def level_of(b: Bot) -> int:
    sheet: dict[str, Any] = b.refresh_sheet() or {}
    return int(sheet.get("level") or b.me().get("level") or 0)


def entrance(cave: str) -> dict[str, Any] | None:
    """Where a cave is entered, as dungeons.entrances() gives it: the cave's own row in CAVES where
    the game's files don't say, else data/dungeons.json. None when unknown."""
    row = CAVES.get(cave, {}).get("entrance")
    if row:
        return {"name": cave, "dungeon_maps": [CAVES[cave]["map"]], "spots": [], **row}
    return dungeons.find(cave)


def turned_away(b: Bot, since: int) -> bool:
    """The game said, after since, that the day's experience dungeon is used."""
    return any(re.search(ALREADY, line, re.I) for line in chores.said_since(b, since))


def pet_ready(b: Bot) -> None:
    """At the door: the run's pet out before we go in - it comes in with us (the hand runs called
    theirs ten seconds before the door). One that is still cooling down is waited for when that is
    soon (PET_WAIT), looking after ourselves meanwhile; a summon the game didn't take is tried
    once more after the server's gap between such changes. A pet that was out already has its
    description read here, not between two fights inside."""
    names = pets.chosen(b)
    if not names:
        return
    clock = clock_for(b)
    rows = pets.owned(b)
    mine = [r for r in rows if r.get("name") in names]
    out_now = any(pets.state(r, clock) == "out" for r in rows)
    if mine and not out_now and not any(pets.state(r, clock) == "ready" for r in mine):
        soon = min(mine, key=lambda r: r["usable_at"])
        wait = soon["usable_at"] - clock.time()
        if wait > PET_WAIT:
            b.log(_("{pet} can't come out for {n} more minutes - going in without it; it is called inside "
                    "as soon as it can", pet=soon["name"], n=int(wait // 60) + 1), speak=True)
        else:
            b.log(_("{pet} can come out in {n} seconds - waiting for it at the door, where no timer runs",
                    pet=soon["name"], n=int(wait) + 1), speak=True)
            until = soon["usable_at"] + 1.0
            while clock.time() < until:
                b.look_after()
                b.sleep(min(1.0, max(0.1, until - clock.time())))
    for attempt in range(2):
        b.pet_checked = 0.0               # look now, whenever the farm loop last did
        pets.keep_out(b)
        rows = pets.owned(b)
        if any(pets.state(r, clock) == "out" for r in rows):
            break
        left = next((r for r in rows if r.get("name") in names and pets.state(r, clock) == "ready"), None)
        if left is None or attempt:
            break
        b.pets_refused.pop(left["id"], None)      # not taken: once more after the server's gap
        b.sleep(beasts.GAP)
    out: dict[str, Any] | None = getattr(b, "pet_out", None)
    if out and out.get("id") not in getattr(b, "pet_described", {}):
        pets.describe(b)


def make_ready(b: Bot, cave: str, cfg: dict[str, Any]) -> None:
    """At the door, before "Enter the dungeon": everything the farm loop would otherwise do on the
    spawn point inside, while the 15 minutes run (11 to 13 s of every visit with a pet, measured).
      - the fight's beast: off the Swift one the trip ended on, or onto the Fierce one the
        character fights in - first, because the server ignores a fusion change right after a
        pet came out;
      - the run's pet out (pet_ready);
      - the skills read, and a few looks at ourselves: long buffs up, HP and MP topped up;
      - potions back on empty keys, a paused experience card running again;
      - the cave's map and loop worked out.
    The target key can't be set here: talking to the entrance turns it to NPCs, so that is the
    one thing left for the first moment inside (a few hundredths of a second a press)."""
    b.log(_("getting ready at the door of {cave}: its timer starts the moment we are in", cave=_(cave)))
    beasts.ensure(b, beasts.FIGHT)
    pet_ready(b)
    if b.skillbook is None and b.p.get("smart_skills", True):
        b.skillbook = combat.SkillBook(b)
    for _look in range(DOOR_LOOKS):
        b.look_after()
        b.sleep(DOOR_LOOK)
    keyslots.refill(b)
    xpcard.at_the_door(b)
    mid = CAVES[cave]["map"]
    atlas.model(mid)
    if cfg.get("patrol") in PATROLS:
        atlas.patrol_loop(mid, cfg["patrol"])


def knock(b: Bot, entry: dict[str, Any]) -> bool:
    """Talk to the entrance we stand next to (go_dungeon brought us): the NPC at the place the
    entrance names, else the one in sight that is this dungeon's. True once its menu is open."""
    if entry.get("at") and entry.get("npc"):
        return b.talk(entry["npc"], (entry["at"][0], entry["at"][1]))
    seen: list[dict[str, Any]] = b.snap()["entities"]
    npc = next((e for e in seen if e.get("id", 0) < 0 and dungeons.is_entrance(entry, e.get("name"))), None)
    return npc is not None and b.talk(npc["name"], (int(round(npc["x"])), int(round(npc["y"]))))


def landed(b: Bot, mid: int) -> bool:
    """After "Enter the dungeon": wait for the game to move us off map mid, looking ten times a
    second, and go on the moment the new map can be read. True once it has. Not the trips'
    wait_map_change: that one gives every new map a settling second, and here the timer runs
    from the landing."""
    end = b.clock.time() + LANDING
    while b.clock.time() < end:
        if b.map_id() != mid:
            b.settled_map()
            return True
        b.sleep(LANDING_LOOK)
    return False


def enter(b: Bot, cave: str, cfg: dict[str, Any] | None = None) -> str:
    """Walk to the cave's entrance, get ready there (make_ready), then talk, choose "Enter the
    dungeon" and wait to land inside, up to TRIES times: a menu that came late, a row not found or
    a slow move in is tried again, and a move in that came after LANDING is seen before the next
    try. IN when inside; USED when the game said the day's entry is used; MISSED when TRIES tries
    got nowhere (the day may still be free)."""
    entry = entrance(cave)
    if entry is None:
        raise Stopped(_("I don't know where {cave} is entered", cave=cave))
    b.go_dungeon(entry)
    make_ready(b, cave, cfg if cfg is not None else config(b.p))
    entry = entrance(cave) or entry       # a circle found on the way is remembered with its place
    inside = CAVES[cave]["map"]
    said: list[str] = []
    for n in range(TRIES):
        if b.map_id() == inside:
            return IN                  # the last try's move in came late
        if n:
            b.log(_("not in {cave} yet - trying again ({n} of {all})", cave=cave, n=n + 1, all=TRIES))
        if not knock(b, entry):
            b.log(_("couldn't talk to {npc}", npc=entry.get("npc") or cave))
            b.sleep(RETRY_WAIT)
            continue
        b.wait_dialog(3.0)
        mid = b.map_id()
        since = chores.said_mark(b)
        if b.choose(ENTER) is None:
            b.close_dialogs()
            b.log(_("{npc} offered no \"Enter the dungeon\"", npc=entry.get("npc") or cave), speak=True)
        else:
            landed(b, mid)
            if b.map_id() == inside:
                return IN
            b.close_dialogs()
            if turned_away(b, since):
                b.log(_("the game says this character has already been in an experience dungeon today"),
                      speak=True)
                return USED
            said = chores.said_since(b, since) or said
        b.sleep(RETRY_WAIT)
    if b.map_id() == inside:
        return IN
    if said:
        b.log(_("the game said: {lines}", lines=" / ".join(said[-3:])))
    return MISSED


def visit(b: Bot, cfg: dict[str, Any], cave: str) -> str:
    """One cave: in, the fight until the game moves us out, then a line about it. Only an entry the
    game said is used (or our own way in) is written down for the day: one that just didn't work
    is tried again at the next run."""
    name = chores.who(b)
    got = enter(b, cave, cfg)
    if got == USED:
        chores.mark(name, done_key(cave), "not let in")
        return _("{cave}: the game didn't let us in (once a day)", cave=cave)
    if got == MISSED:
        line = _("{cave}: couldn't get in after {n} tries", cave=cave, n=TRIES)
        b.log(line, speak=True)
        return line
    chores.mark(name, done_key(cave), "entered")
    b.log(_("in {cave}: {n} minutes to kill all we can", cave=cave, n=MINUTES), speak=True)
    return hunt(b, cfg, cave)


def hunt(b: Bot, cfg: dict[str, Any], cave: str) -> str:
    """Inside a cave: the fight until the game moves us out, then a line about it."""
    mid = CAVES[cave]["map"]
    began, kills = b.clock.time(), b.kills
    seen = report_at(b)

    def out() -> bool:
        return b.map_id() != mid or b.clock.time() - began >= (MINUTES + OVERTIME) * 60

    was = b.move_shots
    b.move_shots = bool(cfg.get("move_shots", True))
    try:
        b.farm(mid, patrol=cfg["patrol"], until=out, instance=True, pack=int(cfg["pack"]), chase=int(cfg["chase"]))
    finally:
        b.move_shots = was
    if b.map_id() == mid:              # the timer didn't move us: leave as players do
        b.log(_("{cave}: still inside after the timer - leaving", cave=cave))
        b.leave_instance()
    else:
        # The game is still moving us out (its end-of-dungeon report comes a moment after the
        # map changes): a hearthstone used then was spent and took us nowhere.
        b.sleep(SETTLE)
    made = b.kills - kills
    minutes = max(1.0, (b.clock.time() - began) / 60.0)
    line = _("{cave}: {n} kills in {m} minutes", cave=cave, n=made, m=int(round(minutes)))
    told = game_report(b, seen)
    if told:
        line += "; " + told
    b.log(line, speak=True)
    return line


def report_at(b: Bot) -> int | None:
    """When the game's newest end-of-dungeon report arrived (agent 73 `report`), or None: one
    newer than this after a visit is that visit's."""
    try:
        r: Any = b.a.json("report")
    except AgentError:         # the link broke: the visit goes on without it
        return None
    if not isinstance(r, dict):
        return None
    at: Any = cast(dict[str, Any], r).get("at")
    return at if isinstance(at, int) else None


def game_report(b: Bot, seen: int | None) -> str | None:
    """What the game's own end report (IN op=66) says about this visit: our kills, damage and
    healing, and the kills of everyone in it. None when no new report came (an older agent, or
    the game sent none). The bot's own count misses kills it can't see; the game's doesn't."""
    try:
        got: Any = b.a.json("report")
    except AgentError:
        return None
    if not isinstance(got, dict):
        return None
    r = cast(dict[str, Any], got)
    players: list[dict[str, Any]] = r.get("players") or []
    if not players or r.get("at") == seen:
        return None
    me_id = b.me().get("id")
    mine: dict[str, Any] = next((p for p in players if p.get("id") == me_id), dict[str, Any]())
    if not mine:
        return _("the game's report: {total} kills", total=r.get("total_kills", 0))
    line = _("the game's report: {n} of {total} kills, {dmg} damage", n=mine.get("kills", 0),
             total=r.get("total_kills", 0), dmg=f"{int(mine.get('damage') or 0):,}")
    if mine.get("healing"):
        line += _(", {heal} healing", heal=f"{int(mine['healing']):,}")
    return line


def run(b: Bot, cfg: dict[str, Any], check_only: bool = False, cave: str | None = None) -> list[str]:
    """Today's cave (the one a script names, else the window's), supplies first, unless this
    character has been in one today. [what happened]."""
    if cave in CAVES:
        cfg = dict(cfg, cave=cave)
    inside = cave_of(b.map_id())
    if inside is not None:
        return rejoin(b, cfg, inside, check_only)
    name = chores.who(b)
    done = done_today(name)
    if done:
        b.log(_("{cave} is done for today - a character goes into one cave a day", cave=_(done[0])), speak=True)
        return []
    level = level_of(b)
    cave = pick(cfg, level)
    if cave is None:
        b.log(_("no cave lets in level {n} yet (the lowest needs level {low})", n=level,
                low=min(v["level"] for v in CAVES.values())), speak=True)
        return []
    if level and level < CAVES[cave]["level"]:
        b.log(_("{cave} needs level {n}", cave=_(cave), n=CAVES[cave]["level"]), speak=True)
        return []
    if too_close_to_level(b, cave):
        return []
    was = getattr(b, "pets_for_run", None)
    try:
        choose_pet(b, cfg)
        get_supplies(b, cfg, check_only)
        if check_only:
            return [_("would go to: {cave}", cave=_(cave))]
        return [visit(b, cfg, cave)]
    finally:
        b.pets_for_run = was            # the rest of the run goes by Character, Settings again


def too_close_to_level(b: Bot, cave: str) -> bool:
    """With "Stop before the next level" (or a plan's keep_level), a cave isn't gone into once the
    next level needs less than that: a visit gives a lot of experience, and inside there is no
    stopping without losing it. True (and said) when it stays out."""
    goal = b.level_goal()
    if not goal:
        return False
    sheet: dict[str, Any] = b.refresh_sheet(force=True) or {}
    need = sheet.get("xp_needed")
    if not isinstance(need, (int, float)) or not 0 <= need < goal:
        return False
    b.log(_("the next level needs only {n} experience - not going into {cave}", n=int(need), cave=_(cave)),
          speak=True)
    return True


def rejoin(b: Bot, cfg: dict[str, Any], cave: str, check_only: bool = False) -> list[str]:
    """A run started inside a cave (the bot or the game restarted mid-visit): the day's cave is
    this one, used or not, so it is hunted till the game moves us out - leaving to check the day
    or top up the supplies would lose the rest of the visit. How long is left isn't known: the
    timer's full length is the longest wait before leaving ourselves."""
    if check_only:
        return [_("already inside {cave}", cave=_(cave))]
    chores.mark(chores.who(b), done_key(cave), "entered")
    b.log(_("already inside {cave}: hunting until the game moves us out", cave=_(cave)), speak=True)
    was = getattr(b, "pets_for_run", None)
    try:
        choose_pet(b, cfg)
        return [hunt(b, cfg, cave)]
    finally:
        b.pets_for_run = was


def choose_pet(b: Bot, cfg: dict[str, Any]) -> None:
    """The pet for the caves: the one chosen in Tools, Dungeons, none, or (by default) the ones
    ticked in Character, Settings - with none ticked, the one that is out now."""
    pick = str(cfg.get("pet") or "").strip()
    if pick == NO_PET:
        b.pets_for_run = []
        b.log(_("going into the dungeons without a pet (Tools, Dungeons)"))
        return
    if pick:
        b.pets_for_run = [pick]
        b.log(_("taking {pet} into the dungeons (Tools, Dungeons)", pet=pick))
        return
    pet = pets.adopt(b)
    if pet:
        b.log(_("no pet ticked in Character, Settings - looking after {pet}, the one out now", pet=pet))
    elif not pets.chosen(b):
        b.log(_("no pet out and none ticked in Character, Settings - going without one"), speak=True)


def cave_of(map_id: int | None) -> str | None:
    """The cave a map id is, or None."""
    return next((c for c, v in CAVES.items() if v["map"] == map_id), None)


def entrance_text(cave: str) -> str:
    """Where a cave is entered, for the window."""
    e = entrance(cave)
    if not e:
        return _("entrance unknown")
    at = e.get("at")
    return "%s%s" % (atlas.label(e["map"]), (" (%d,%d)" % tuple(at)) if at else "")

