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

So a visit is: the supplies topped up before setting out (the pet's blue potions and Rice Rolls,
our own blues and reds - a pet that fights and a lower character's potions are a good part of the
damage), the walk to the entrance, "Enter the dungeon", then the farming fight in dungeon mode:
no trips out (the visit can't be taken up again), no resting (drink instead), area skills from two
monsters together, chasing whatever is within 90 steps (caveloops.py: the chase range, not the
loop, is what decides the kills here), until the game moves us out. With no pet ticked, the pet
that is out is looked after for the run - the game counts its kills as ours. Each cave once a game day, written down in the
chores' done list. Where the run ends is the script's end_at, as for farming.
"""

from __future__ import annotations

from typing import Any

from .. import atlas, dungeons, jsonio, knowledge
from ..i18n import N_, _
from . import chores, pets, town
from .bot import Bot, Stopped
from .botparts.base import potion_kind

CAVES: dict[str, dict[str, Any]] = {
    "Scorpion Cave": {"map": 1539, "level": 60},
    "Spider cave": {"map": 1503, "level": 40},
}
CAVE_NAMES = (N_("Scorpion Cave"), N_("Spider cave"))     # CAVES' names, for translators
MINUTES = 15              # the game's timer inside
OVERTIME = 2.0            # minutes past the timer before leaving through the Action menu ourselves
CHASE = 90                # walking steps to chase a monster from the loop: farming's 40 left a
                          # caster 10-13% short in simulated visits (caveloops.py); the loop itself
                          # hardly matters with a monster nearly always that close
ENTER = r"^\s*Enter the dungeon"
LANDING = 10.0            # seconds the game has to move us in after "Enter the dungeon"
SUPPLIES = ("pet_mp", "pet_food", "mp", "hp")
SUPPLY_NAMES = {"pet_mp": N_("Pet blue potions"), "pet_food": N_("Pet Rice Rolls"),
                "mp": N_("Our blue potions"), "hp": N_("Our red potions")}
SOURCES = ("both", "storage", "buy")
DEFAULTS: dict[str, Any] = {
    "caves": ["Scorpion Cave", "Spider cave"],   # these, in this order, each once a game day
    "supplies": {"pet_mp": {"item": "", "min": 25},     # item "": the one on the hotkey (or the
                 "pet_food": {"item": "", "min": 3},    # strongest in the bag); min 0: not looked at
                 "mp": {"item": "", "min": 50},
                 "hp": {"item": "", "min": 30}},
    "source": "both",         # topping up: the storehouse, then the shop
    "when_short": "stop",     # still short after topping up: "stop", or "go" anyway
    "patrol": "quick",        # the loop through the cave: quick, full or short
    "pack": 2,                # area skills on this many monsters close together
    "chase": CHASE,           # walking steps to chase a monster from the loop
    "end": {"how": "start"},  # where the run ends, as a plan's end (plan.end_line)
}
PATROLS = ("quick", "full", "short")


def config(p: dict[str, Any]) -> dict[str, Any]:
    """The profile's dungeon settings, with every setting filled in."""
    mine: dict[str, Any] = p.get("dungeon") or {}
    cfg: dict[str, Any] = {**DEFAULTS, **mine}
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
    """The caves this character has been into this game day."""
    done = chores.done_today(name)
    return [c for c in CAVES if done_key(c) in done]


def due(name: str | None, cfg: dict[str, Any]) -> list[str]:
    """The player's caves, in their order, not yet visited this game day."""
    done = done_today(name)
    return [c for c in cfg["caves"] if c in CAVES and c not in done]


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
    """The run the window starts: its end place, then the caves."""
    from .plan import end_line
    return end_line(cfg.get("end")) + "\ndungeon\n"


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


def enter(b: Bot, cave: str) -> bool:
    """Walk to the cave's entrance, talk, choose "Enter the dungeon" and wait to land inside. True
    when inside; False when the game didn't let us in (the day's entry used, a level too low)."""
    entry = dungeons.find(cave)
    if entry is None:
        raise Stopped(_("I don't know where {cave} is entered", cave=cave))
    b.go_dungeon(entry, talk=True)
    b.wait_dialog(3.0)
    mid = b.map_id()
    if b.choose(ENTER) is None:
        b.close_dialogs()
        b.log(_("{npc} offered no \"Enter the dungeon\"", npc=entry.get("npc") or cave), speak=True)
        return False
    b.wait_map_change(mid, LANDING)
    if b.map_id() != CAVES[cave]["map"]:
        b.close_dialogs()
        return False
    return True


def visit(b: Bot, cfg: dict[str, Any], cave: str) -> str:
    """One cave: in, the fight until the game moves us out, then a line about it."""
    name = getattr(b, "character", None)
    if not enter(b, cave):
        chores.mark(name, done_key(cave), "not let in")
        return _("{cave}: the game didn't let us in (once a day)", cave=cave)
    chores.mark(name, done_key(cave), "entered")
    mid = CAVES[cave]["map"]
    began, kills = b.clock.time(), b.kills
    b.log(_("in {cave}: {n} minutes to kill all we can", cave=cave, n=MINUTES), speak=True)

    def out() -> bool:
        return b.map_id() != mid or b.clock.time() - began >= (MINUTES + OVERTIME) * 60

    b.farm(mid, patrol=cfg["patrol"], until=out, instance=True, pack=int(cfg["pack"]), chase=int(cfg["chase"]))
    if b.map_id() == mid:              # the timer didn't move us: leave as players do
        b.log(_("{cave}: still inside after the timer - leaving", cave=cave))
        b.leave_instance()
    made = b.kills - kills
    minutes = max(1.0, (b.clock.time() - began) / 60.0)
    line = _("{cave}: {n} kills in {m} minutes", cave=cave, n=made, m=int(round(minutes)))
    b.log(line, speak=True)
    return line


def run(b: Bot, cfg: dict[str, Any], check_only: bool = False) -> list[str]:
    """Each of the player's caves not yet visited today, supplies first. [what happened]."""
    name = getattr(b, "character", None)
    todo = due(name, cfg)
    if not todo:
        b.log(_("every chosen dungeon is done for today"), speak=True)
        return []
    level = level_of(b)
    able = [c for c in todo if not level or level >= CAVES[c]["level"]]
    for c in todo:
        if c not in able:
            b.log(_("{cave} needs level {n}", cave=c, n=CAVES[c]["level"]))
    if not able:
        return []
    pet = pets.adopt(b)
    if pet:
        b.log(_("no pet ticked in Character, Settings - looking after {pet}, the one out now", pet=pet))
    elif not pets.chosen(b):
        b.log(_("no pet out and none ticked in Character, Settings - going without one"), speak=True)
    get_supplies(b, cfg, check_only)
    if check_only:
        return [_("would go to: {caves}", caves=", ".join(able))]
    notes: list[str] = []
    for i, cave in enumerate(able):
        if i:
            get_supplies(b, cfg)        # the first cave used some: top up again between them
        notes.append(visit(b, cfg, cave))
    return notes


def cave_of(map_id: int | None) -> str | None:
    """The cave a map id is, or None."""
    return next((c for c, v in CAVES.items() if v["map"] == map_id), None)


def entrance_text(cave: str) -> str:
    """Where a cave is entered, for the window."""
    e = dungeons.find(cave)
    if not e:
        return _("entrance unknown")
    at = e.get("at")
    return "%s%s" % (atlas.label(e["map"]), (" (%d,%d)" % tuple(at)) if at else "")

