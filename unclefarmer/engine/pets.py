"""Pets: keep one out while farming, and keep it alive.

What the game showed in testing:
  - The pet panel is bag panel 3 (world.t, game.af: a id, b state, c "Kind name"). The use key
    on a pet sends OUT op=18 `03 <pet id> <0 summon | 1 recall>`; the client picks recall when b < 0.
  - b < 0: the pet is out. b > 0: the epoch second it can be summoned again (recalling the Frog
    put it on a ~2 minute cooldown). b = 0: ready.
  - IN op=29 is the pet list; IN op=30 `<pet id> <state>` a change (-1 = out).
  - Out in the world the pet is an entity with the pet's own (positive) id, named
    "<Kind> <name> belongs to <owner>", with its own HP. A positive id means the bot's monster
    filter never takes it for a target.
  - Pet potions (Pet Small/Medium/Large Red and Blue Potion, sold by the apothecary) sit in hotkey
    slots like ours; the game's help pages say Shift+R reads the pet's HP, and that adventure and
    level dungeons and competitions don't allow pets (the game puts them away there).
  - The pet HP key (W_PET_HP, Shift + the own-HP key) asks the server about id 0 and gets the out
    pet's HP and MP back, no window (agent v31 `pet_vitals`). The pet's maximum MP is only in its
    description, which the server sends when the pet panel's cursor lands on it (`pet_info`, with
    the amiability and HP too).
  - Amiability: the points key (T) answers it after the points and fusion power, while a pet is
    out. The game's item text for Pet Rice Roll is "Increase 30% Amiability"; the other pet foods
    and juices heal the pet's HP or MP over a few minutes.
"""

from typing import Any

from ..clock import CLOCK, Clock, clock_for
from ..i18n import _
from . import potions, teamheal

REFUSED_FOR = 90    # a summon the game didn't take (dungeon, cooldown we didn't see) - try later
CHECK_EVERY = 5     # seconds between looks at the pet list
VITALS_EVERY = 10   # seconds between presses of the pet HP key while a pet is out (MP comes from it)
VITALS_FRESH = 30   # a pet HP key answer older than this isn't trusted for MP
AMIABILITY_EVERY = 60    # seconds between readings of the amiability (the points key)
FOOD_GAP = 90       # seconds after feeding before feeding again (the next reading must show it)
# Healing the pet with a skill: one heal at most this often, and a heal that landed shows in the
# pet's HP this soon. A heal that raised nothing this many times running went elsewhere.
HEAL_GAP = 3.0
HEAL_CHECK = 2.5
HEAL_MISSES = 3
DESCRIBE_RETRY = 300     # seconds before trying again to read a description that didn't come


def owned(b):
    rows = b.a.json("mem pets")
    return rows if isinstance(rows, list) else []


def same_pet(a, b):
    """Pet names from different places ("Shorthair Cat>><< Mittens", "Shorthair Cat Mittens")."""
    def norm(s):
        return " ".join((s or "").replace(">><<", " ").replace("|||", " ").lower().split())
    return norm(a) == norm(b)


def state(r, clock: Clock = CLOCK):
    now = clock.time()
    if r["usable_at"] < 0:
        return "out"
    if r["usable_at"] > now:
        return "cooldown"
    return "ready"


def entity(b, snap):
    """Our pet out in the world, or None. Found by id: the pet list's id is the id the pet has out
    in the world. The name ("... belongs to <owner>") is only the fallback, for a pet list not read
    yet."""
    out = getattr(b, "pet_out", None)
    if out:
        hit = next((e for e in snap["entities"] if e.get("id") == out["id"]), None)
        if hit is not None:
            return hit
    me = snap["self"].get("name") or ""
    tail = "belongs to " + me
    return next((e for e in snap["entities"] if me and e.get("kind") == "player"
                 and " ".join(e.get("name", "").split()).endswith(tail)), None)


def chosen(b):
    """The pets the player ticked, in their order (profile "pets": [{"name", "use"}])."""
    return [x["name"] for x in b.p.get("pets", []) if x.get("use")]


def wanted(b, rows):
    """The first pet in the player's order that is ready now: when the first is dead or cooling
    down, the second comes out, and so on."""
    now = clock_for(b).time()
    by_name = {r["name"]: r for r in rows}
    for name in chosen(b):
        r = by_name.get(name)
        if r and state(r, clock_for(b)) == "ready" and b.pets_refused.get(r["id"], 0) < now:
            return r
    return None


def _rank(b, r):
    """A pet's place in the player's order (lower is better); unticked pets rank last."""
    order = chosen(b)
    return order.index(r["name"]) if r["name"] in order else len(order)


def _press(b, rows, pet, want_state):
    """The pet panel cursor onto pet, the use key, and wait for the game to show want_state."""
    if not b.panel_cursor(3, rows.index(pet), len(rows)):
        return False
    b.key(b.p["use_key"])
    for _i in range(12):
        b.sleep(0.25)
        r = next((x for x in owned(b) if x["id"] == pet["id"]), None)
        if r and (state(r, clock_for(b)) == "out") == (want_state == "out"):
            return True
    return False


def keep_out(b: Any) -> None:
    """Keep the best pet out, by the player's order. How the game works:
    every pet has its own cooldown after dying (paid pets 10 minutes, free ones 30), and the pet
    list tells us when each one is ready. So the moment a pet higher in the order is ready again,
    it replaces whatever is out - even if that one is still alive."""
    if not chosen(b):
        return
    now = clock_for(b).time()
    if now - getattr(b, "pet_checked", 0) < CHECK_EVERY:
        return
    b.pet_checked = now
    rows = owned(b)
    if not rows:
        return
    out = next((r for r in rows if state(r, clock_for(b)) == "out"), None)
    b.pet_out = out
    best = wanted(b, rows)            # the first ticked pet that is ready right now
    if out and (best is None or _rank(b, out) <= _rank(b, best)):
        return                        # what is out is already the best we can have
    if best is None:
        if not getattr(b, "pet_said_waiting", False):
            b.pet_said_waiting = True
            b.log(_("no pet ready to summon yet (cooldown); farming on without one"))
        return
    b.pet_said_waiting = False
    if out:
        b.log(_("{better} is ready again - putting {pet} away for it", better=best["name"], pet=out["name"]))
        if not _press(b, rows, out, "away"):
            b.log(_("couldn't put {pet} away; keeping it out", pet=out["name"]))
            return
        b.pet_out = None
        rows = owned(b)
        best = next((r for r in rows if r["id"] == best["id"]), best)
    if _press(b, rows, best, "out"):
        b.log(_("summoned {pet}", pet=best["name"]))
        b.pet_out = dict(best, usable_at=-1)
        b.pet_described = {best["id"]: clock_for(b).time()}   # the cursor landed on it: its description came
        return
    b.pets_refused[best["id"]] = clock_for(b).time() + REFUSED_FOR
    b.log(_("{pet} didn't come out (no pets allowed here?) - trying again later", pet=best["name"]))


def pet_key(b):
    """The pet HP key: the profile's, else Shift + the own-HP key (the game's default binding)."""
    k = b.p.get("pet_vitals_key")
    if k:
        return k
    own = b.p.get("vitals_key")
    return "shift+" + own if own else None


def numbers(b, snap, pet):
    """{"hp", "maxhp", "mp", "maxmp", "amiability"} of the pet out, each None when unknown. HP comes
    from the pet out in the world (live), MP from the pet HP key's answer, the maximum MP from its
    description (else the most seen), amiability from the points key or the description, whichever
    is newer."""
    me = snap["self"]
    out = {"hp": pet.get("hp"), "maxhp": pet.get("maxhp"), "mp": None, "maxmp": None, "amiability": None,
           "described": False}
    v = me.get("pet_vitals") or {}
    info = me.get("pet_info") or {}
    out_row = getattr(b, "pet_out", None)
    mine = bool(info) and (out_row is None or same_pet(info.get("name"), out_row["name"]))
    if mine and info.get("maxmp"):
        out["maxmp"] = info["maxmp"]
        out["described"] = True
    if v.get("mp", -1) >= 0 and v.get("age_ms", 1e9) < VITALS_FRESH * 1000:
        out["mp"] = v["mp"]
        peak = getattr(b, "pet_mp_peak", {})
        key = out_row["id"] if out_row else None
        peak[key] = max(peak.get(key, 0), v["mp"])
        b.pet_mp_peak = peak
        out["maxmp"] = out["maxmp"] or peak[key]
    best = None                        # (age ms, amiability)
    pts = me.get("points") or {}
    if "pet" in pts:
        best = (pts.get("age_ms", 1e9), pts["pet"])
    if mine and info.get("amiability", -1) >= 0 and (best is None or info["age_ms"] < best[0]):
        best = (info["age_ms"], info["amiability"])
    if best is not None:
        out["amiability"] = best[1]
    return out


def _below(b, kind, inf, top):
    """The percent under which a pet potion of this kind ("pet_hp", "pet_mp") is drunk: the
    profile's number, else - as for our own potions - the moment one potion's worth is missing."""
    v = b.p.get(kind + "_below", "auto")
    if not potions.auto(v):
        return float(v)
    return potions.mark(inf, top) or potions.FALLBACK_MARK


def _drink(b, kind, share, top, label):
    """A pet potion of this kind when the pet is below the mark. True when one was used."""
    key, name = b.potion(kind)
    if not (key or name):
        if not getattr(b, "_told_no_pet_potion", {}).get(kind):
            b.__dict__.setdefault("_told_no_pet_potion", {})[kind] = True
            b.log(_("no {what} potion for the pet on a hotkey or in the bag", what=label), speak=True)
        return False
    inf = potions.info(name)
    gap = b.p.get(kind + "_cooldown", "auto")
    gap = potions.FALLBACK_GAP if potions.auto(gap) else float(gap)
    if inf:
        gap = max(gap, inf["cd"] + 0.3)     # the game's own cooldown (Pet Large Red Potion: 22 s)
    last = getattr(b, "pet_potion_at", {})
    now = clock_for(b).time()
    if share >= _below(b, kind, inf, top) or now - last.get(kind, 0) < gap:
        return False
    if not key and b.in_fight and not getattr(b, "agent_act", False):
        return False      # the bag needs the arrow keys, which walk us in a fight: after it (as drink() does)
    last[kind] = now
    b.pet_potion_at = last
    b.log(_("pet {what} {pct}% - {item}", what=label, pct=int(share), item=key or name))
    if key:
        b.key(key)
    else:
        b.use_item(name)
    return True


def _read(b, me, now):
    """Ask for the pet's HP and MP now and then (no window): without a key from agent 39, else
    with the pet HP key. And the points key for the amiability when no fight is pressing it."""
    k = pet_key(b)
    asking = getattr(b, "agent_ask", False)
    if (asking or k) and now - getattr(b, "pet_vitals_at", 0) >= VITALS_EVERY:
        asked = b.ask_vitals("petvitals") if asking else False
        if asked is not None:              # None: our own request's answer is due, ask next tick
            b.pet_vitals_at = now
            if not asked and k:
                b.key(k, 30)
    pk = b.p.get("points_key")
    if pk and b.p.get("pet_food_below", 70) and now - getattr(b, "pet_points_at", 0) >= AMIABILITY_EVERY:
        if (me.get("points") or {}).get("age_ms", 1e9) > AMIABILITY_EVERY * 1000:
            b.pet_points_at = now
            b.key(pk, 30)


def describe(b):
    """Between fights, once per pet: the pet panel's cursor onto the pet, so the game sends its
    description (the maximum MP is nowhere else). A summon does this by itself."""
    out = getattr(b, "pet_out", None)
    if not out or b.in_fight or b.attackers():
        return
    done = b.__dict__.setdefault("pet_described", {})
    if clock_for(b).time() - done.get(out["id"], 0) < DESCRIBE_RETRY:
        return
    done[out["id"]] = clock_for(b).time()
    rows = owned(b)
    i = next((n for n, r in enumerate(rows) if r["id"] == out["id"]), None)
    if i is None:
        return
    u = b.goto_panel(3)
    if u is not None and u.get("cursor") == i and len(rows) > 1:
        # already on it: the description only comes when the cursor lands, so step off first
        b.key(b.p["bag_down"])
        b.sleep(0.12)
    if b.panel_cursor(3, i, len(rows)):
        b.sleep(0.5)
    b.goto_panel(0)


def feed(b, amiability):
    """A Pet Rice Roll ("Increase 30% Amiability") when the amiability is under pet_food_below:
    from a hotkey if one holds it, else from the bag between fights."""
    below = b.p.get("pet_food_below", 70)
    if not below or amiability is None or amiability >= below:
        return False
    now = clock_for(b).time()
    if now - getattr(b, "pet_fed_at", 0) < FOOD_GAP:
        return False
    food = (b.p.get("pet_food") or "Pet Rice Roll").strip()
    key = b.hotkey_of(food)
    if not key:
        if b.in_fight and not getattr(b, "agent_act", False):
            return False
        have = any((r.get("name") or "").strip().lower() == food.lower() and r.get("count") for r in b.bag_rows())
        if not have:
            if not getattr(b, "_told_no_pet_food", False):
                b._told_no_pet_food = True
                b.log(_("the pet's amiability is {n} but there's no {item} in the bag", n="%g" % amiability,
                        item=food), speak=True)
            b.pet_fed_at = now
            return False
    b.pet_fed_at = now
    b.log(_("pet amiability {n} - {item}", n="%g" % amiability, item=key or food))
    if key:
        b.key(key)
    else:
        b.use_item(food)
    return True


def heal(b: Any, pet: dict[str, Any], share: float, now: float) -> bool:
    """The class's quick heal on the pet (Priest Heal or Recover, Sorceress Healing Wave): the
    pet selected, the heal pressed, our target selected again. The game heals the ally that is
    selected (with a monster selected the same heal lands on us), and it costs no potion. Before
    any pet potion; only with the agent's `select` (agent 40), which picks the pet by its id. A
    heal that hasn't raised the pet's HP HEAL_MISSES times running is going somewhere else: then
    pet potions only, for the rest of the run. True when a heal was pressed."""
    if not b.p.get("pet_heal_with_skill", True) or getattr(b, "pet_heal_off", False):
        return False
    if not getattr(b, "agent_act", False) or getattr(b, "walk_only", False):
        return False
    sb = getattr(b, "skillbook", None)
    if sb is None or not getattr(sb, "ok", False):
        return False
    h = sb.healer()
    if not h:
        return False
    tried = getattr(b, "pet_heal_try", None)
    if tried:
        if now - tried[0] < HEAL_CHECK:
            return False                  # the last heal's effect isn't in yet
        b.pet_heal_try = None
        if pet["hp"] > tried[1]:
            b.pet_heal_misses = 0
        else:
            b.pet_heal_misses = getattr(b, "pet_heal_misses", 0) + 1
            if b.pet_heal_misses >= HEAL_MISSES:
                b.pet_heal_off = True
                b.log(_("{skill} doesn't reach the pet - pet potions only", skill=h["name"]), speak=True)
                return False
    below = b.p.get("pet_heal_below", 85)
    below = 85.0 if potions.auto(below) else float(below)
    if share >= below or now - getattr(b, "pet_healed_at", 0.0) < HEAL_GAP:
        return False
    if sb.gcd_wait(now) or not sb.ready(h, now):
        return False
    if not teamheal.cast_on(b, sb, h, pet["id"], now):
        return False
    b.pet_healed_at = now
    b.pet_heal_try = (now, pet["hp"])
    if not getattr(b, "_told_pet_heal", False):
        b._told_pet_heal = True
        b.log(_("heals the pet with {skill} below {pct}% of its HP", skill=h["name"], pct="%g" % below))
    return True


def look_after(b: Any, snap: dict[str, Any]) -> None:
    """Keep the pet out alive and willing: its red potion by its HP, its blue potion by its MP,
    and a Rice Roll when its amiability is low."""
    if not chosen(b):
        return
    pet = entity(b, snap)
    if not pet or not pet.get("maxhp"):
        if getattr(b, "pet_out", None) and not getattr(b, "_told_pet_unseen", False):
            b._told_pet_unseen = True
            b.log(_("{pet} is out, but not in sight - its potions wait until it is", pet=b.pet_out["name"]))
        return
    now = clock_for(b).time()
    _read(b, snap["self"], now)
    n = numbers(b, snap, pet)
    if not getattr(b, "_told_pet_seen", None) == pet["id"]:
        b._told_pet_seen = pet["id"]
        b.log(_("looking after {pet}: HP {hp}/{max}", pet=pet.get("name", "").split(" belongs to ")[0],
                hp=pet["hp"], max=pet["maxhp"]))
    share = 100.0 * n["hp"] / n["maxhp"]
    if heal(b, pet, share, now):
        return
    if _drink(b, "pet_hp", share, n["maxhp"], _("HP")):
        return
    if n["mp"] is not None and n["maxmp"]:
        if _drink(b, "pet_mp", 100.0 * n["mp"] / n["maxmp"], n["maxmp"], _("MP")):
            b.pet_vitals_at = 0.0       # read the MP again soon after
            return
    feed(b, n["amiability"])
    if not n["described"]:
        describe(b)


def recall(b: Any) -> None:
    """Put the pet away (used only when the profile asks for it at the end of a run)."""
    rows = owned(b)
    out = next((r for r in rows if state(r, clock_for(b)) == "out"), None)
    if out and b.panel_cursor(3, rows.index(out), len(rows)):
        b.key(b.p["use_key"])
        b.log(_("put {pet} away", pet=out["name"]))
