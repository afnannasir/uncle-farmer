"""Survey: learn every NPC from the game itself - where it stands, and every menu it has.

The game client holds no NPC data (checked 2026-10-04: map.gz, files.gz, language.gz and all game
classes). The server reports NPCs only to a character on their map, out to about 78 tiles
(IN op=32), and sends a menu only when it is opened (IN op=62). So the only proper source is to go
there and ask - which is what this does:

  1. sweep   travel to the map and walk a grid of points so every walkable cell comes within
             SIGHT tiles of one; record every NPC the game knows about (from its own memory)
  2. talk    walk up to each NPC, open its menu, and explore the menu tree, pressing ONLY entries
             that just show more information (see safe()); never anything that buys, sells, takes,
             confirms, teleports or asks for a number. Every menu page is recorded as it is.
  3. save    survey/<map id>.json beside the app, after every NPC, so a stopped survey resumes

Recorded besides the NPCs: the game's own description of each (its NPC list,
language/english/more/role_description_npc.txt), and every monster seen on the map with its level
and description. Guards and gate keepers count as NPCs when the game's list describes them as one;
other players' pets and summons, traps and loot on the ground are left out. Maps you can't walk to
(a gang's base, a dungeon) are surveyed with `survey here` once the character is inside.

What an entry's flags mean (bit fields, worked out from 836 logged dialogs):
   1  asks for a number (buy / sell / take out quantity)      2  "are you sure?" follows
   4  the server sends another page                             8  can be chosen
  16, 32  special pickers (gem synthesis)                     256  a final action (go, summon, yes)
  plain text = 0. Safe to press while surveying: exactly 8 or 12, and text not on the deny list.
"""

import os
import re
import time

from .. import applog, atlas, jsonio as json, knowledge, paths, settings, travel
from ..clock import clock_for
from ..errors import carry_on
from ..i18n import _
from ..link import AgentError
from . import beasts
from .bot import Respawned, Stopped
from .botparts.base import STOPPING, note_failure

SIGHT = 55            # walk so every cell is this close to a stop (the server reports out to ~78)
NPC_HP = 20000000
MAX_PAGES = 40        # menu pages recorded per NPC
MAX_DEPTH = 3
SAFE_FLAGS = (8, 12)
# never pressed, whatever the flags say. Whole words (short ones: "no" must not match "normal"),
# and word starts that cover every form ("synthes" - synthesize, synthesis; "claim" - Claims):
# with a word end after them as well, "synthesize 30 Pet Pizza" was pressed (2026-10-11).
_WORDS = (r"enter|go to|leave|return|join|quit|invite|apply|accept|refuse|agree|buy|sell|take|use|"
          r"insert|remove|break|learn|forget|reset|summon|confirm|yes|no|ok|start|auction|issue|"
          r"kill|open the|get|change|draw|share|send|chat|private|block|ride|fly|board|fuse|merge")
_STEMS = (r"delet|disband|transfer|donat|renam|recharg|top-?up|purchas|deposit|withdraw|exchang|"
          r"synthes|forg|enhanc|repair|cultivat|respawn|resurrect|register|marr[yi]|divorc|claim|"
          r"refresh|matchmak|teleport|depress|receiv|collect|redeem|upgrad|income|smelt|elevat|"
          r"refin|decompos|dismantl|recycl|identif|unseal|unlock|unbind|rebirth|reroll|bid|socket(?!ing instr)")
DENY = re.compile(r"\b(?:(?:%s)\b|(?:%s))" % (_WORDS, _STEMS), re.I)

OUT = os.environ.get("MW_SURVEY_DIR") or os.path.join(paths.APP, "survey")

_TEXTS = {}


def _pairs(name):
    """The translated line of each entry in one of the game's language files (an entry is the
    original line, its translation and a blank line), placeholders like $1 taken out."""
    folder = settings.load_app().get("game_folder") or ""
    path = os.path.join(folder, "language", "english", "more", name)
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            blocks = fh.read().replace("\r", "").split("\n\n")
    except OSError:
        return []
    out = []
    for blk in blocks:
        lines = [x for x in blk.split("\n") if x.strip()]
        if lines:
            out.append(re.sub(r"\$\d+", "", lines[-1]).strip())
    return out


def described():
    """name (lower case) -> the game's descriptions of it: [(level or None, kind, text)]."""
    if "npc" not in _TEXTS:
        about = {}
        for line in _pairs("role_description_npc.txt"):
            parts = [p.strip() for p in line.split(";")]
            if len(parts) < 2 or not parts[0]:
                continue
            m = re.match(r"level\s*(\d*)\s*(.*)$", parts[1], re.I)
            if m:
                entry = (int(m.group(1)) if m.group(1) else None, m.group(2).strip(), "; ".join(parts[2:]))
            else:
                entry = (None, "npc", "; ".join(parts[1:]))
            about.setdefault(parts[0].lower(), [])
            if entry not in about[parts[0].lower()]:
                about[parts[0].lower()].append(entry)
        _TEXTS["npc"] = about
    return _TEXTS["npc"]


def plain(text):
    """A name to compare: lower case, numbers and extra spaces out (the item list says
    "Level $1 Opal Shard" where a menu says "Level 1 Opal Shard")."""
    return " ".join(re.sub(r"\d+", " ", text or "").lower().split())


def item_names():
    if "items" not in _TEXTS:
        _TEXTS["items"] = {plain(n) for n in _pairs("item.txt") if plain(n)}
    return _TEXTS["items"]


def out_dir():
    d = os.path.abspath(OUT)
    os.makedirs(d, exist_ok=True)
    return d


def words(e):
    """Every text an entry carries: its own, the question behind it (f..i) and its options. The
    game now sends most entries with text and raw empty and the words in options."""
    out = [e.get("text"), e.get("raw")] + [e.get(k) for k in ("f", "g", "h", "i")]
    return [w for w in out + list(e.get("options") or []) if w]


def label(e):
    """What the entry says on its line: its text, else its first option."""
    return e.get("text") or e.get("raw") or next(iter(e.get("options") or []), "") or ""


# A row of a list rather than a menu topic: "1; Bluish White Horse" in the beast storage, an item
# with its rarity ("Heavenly Inferno Belt;rare"). Pressing one acts on that thing - in the
# Space-Time Storehouse it took the fusion beast out (2026-10-11), with the same flags a submenu has.
LIST_ROW = re.compile(";")


def is_thing(text):
    """The entry names an item (the game's item list), or is a row of a list of things."""
    t = (text or "").strip()
    return bool(LIST_ROW.search(t)) or plain(t) in item_names()


def safe(entry):
    """Only shows more: the right flags, nothing it says - nor the question behind it - is on the
    deny list, and it is a menu topic, not one of a list of things (items, beasts, goods)."""
    return (entry.get("flags") in SAFE_FLAGS and not any(DENY.search(w) for w in words(entry))
            and not is_thing(label(entry)))


# ------------------------------------------------------------------------------------ sweep points
def sweep_points(mid, start=None, rect=None):
    """Stops covering the walkable region we can reach, SIGHT apart, in a short walking order."""
    m = atlas.model(mid)
    comp = m.comp_of(int(start[0]), int(start[1])) if start and m.ok(int(start[0]), int(start[1])) else None
    x0, y0, x1, y1 = rect or (1, 1, m.w, m.h)
    step = int(SIGHT * 1.2)
    pts = []
    for gy in range(y0 + step // 2, y1 + step // 2 + 1, step):
        for gx in range(x0 + step // 2, x1 + step // 2 + 1, step):
            c = m.nearest_walkable(min(gx, x1), min(gy, y1), step // 2, comp)
            if c and m.directional[c[1], c[0]]:
                c = m.nearest_floor(c[0], c[1], 3, comp) or c     # never stop on a ledge
            if c and c not in pts and not m.portal[c[1], c[0]]:   # a stop on an exit leaves the map
                pts.append(c)
    if not pts:
        return []
    # nearest-neighbour tour from where we start
    here = (int(start[0]), int(start[1])) if start else pts[0]
    order = []
    while pts:
        nxt = min(pts, key=lambda p: abs(p[0] - here[0]) + abs(p[1] - here[1]))
        pts.remove(nxt)
        order.append(nxt)
        here = nxt
    return order


# ------------------------------------------------------------------------------------ the record
class Record:
    def __init__(self, mid):
        self.mid = mid
        self.path = os.path.join(out_dir(), "%d.json" % mid)
        if os.path.exists(self.path):
            with open(self.path, encoding="utf-8") as f:
                self.data = json.load(f)
            self.merge_walkers()
        else:
            self.data = {"map": mid, "name": atlas.name(mid), "npcs": {}, "swept": False, "started": time.strftime("%Y-%m-%d %H:%M")}

    def key(self, e):
        return "%s@%d,%d" % (e["name"], int(round(e["x"])), int(round(e["y"])))

    def same(self, e):
        """The entry for this NPC seen before at another place: one that walks about (guards on
        patrol, civilians, ducks) keeps its id, so it is one NPC, not one per place it was seen."""
        return next((n for n in self.data["npcs"].values()
                     if n["id"] == e["id"] and n["name"] == e["name"]), None)

    @staticmethod
    def moved(n, x, y):
        """Where a walking NPC was last seen, and the box it walks in."""
        x0, y0, x1, y1 = n.get("walks") or (n["x"], n["y"], n["x"], n["y"])
        n["walks"] = [min(x0, x), min(y0, y), max(x1, x), max(y1, y)]
        n["x"], n["y"] = x, y

    def merge_walkers(self):
        """Records written before same() existed hold a walking NPC once per place: fold them into
        one, keeping the read menus if any of them has them."""
        keep = {}
        for k in list(self.data["npcs"]):
            n = self.data["npcs"][k]
            first = keep.setdefault((n["id"], n["name"]), k)
            if first == k:
                continue
            f = self.data["npcs"][first]
            self.moved(f, n["x"], n["y"])
            if f.get("menus") is None and n.get("menus") is not None:
                f["menus"], f["read"] = n["menus"], n.get("read")
            del self.data["npcs"][k]

    def saw(self, e):
        k = self.key(e)
        n = self.data["npcs"].get(k) or self.same(e)
        if n is not None and self.key(n) != k:
            self.moved(n, round(e["x"], 2), round(e["y"], 2))
        if n is None:
            n = self.data["npcs"][k] = {"name": e["name"], "x": round(e["x"], 2), "y": round(e["y"], 2),
                                        "id": e["id"], "type": e.get("type"), "hp": e.get("hp"),
                                        "kind": e.get("kind"), "side": e.get("side"),
                                        "first_seen": time.strftime("%Y-%m-%d %H:%M"), "menus": None}
            about = [a[2] for a in described().get(e["name"].strip().lower(), []) if a[2]]
            if about:
                n["about"] = about
        return n

    def saw_monster(self, e):
        """Each kind of monster once per map: how many were seen, their HP, where, and what the
        game says it is (level, ordinary / elite / super, description)."""
        mons = self.data.setdefault("monsters", {})
        name = e["name"].strip()
        m = mons.get(name)
        if m is None:
            m = mons[name] = {"ids": [], "maxhp": e.get("maxhp") or e.get("hp"), "cells": []}
            about = described().get(name.lower(), [])
            if about:
                m["about"] = [{"level": a[0], "kind": a[1], "text": a[2]} for a in about]
        if e["id"] not in m["ids"]:
            m["ids"].append(e["id"])
        m["maxhp"] = max(m.get("maxhp") or 0, e.get("maxhp") or e.get("hp") or 0)
        cell = [int(round(e["x"])), int(round(e["y"]))]
        if len(m["cells"]) < 12 and cell not in m["cells"]:
            m["cells"].append(cell)

    def save(self):
        self.data["updated"] = time.strftime("%Y-%m-%d %H:%M")
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, ensure_ascii=False, indent=1)
        os.replace(tmp, self.path)


def is_npc(e):
    """Town NPCs and objects: the game's own kind when the agent reports it (v22), else a negative
    id with either no hp at all or exactly NPC_HP."""
    if "side" in e:
        return e.get("kind") == "npc"
    return e["id"] < 0 and (e.get("hp") in (None, NPC_HP) or e.get("maxhp") == NPC_HP)


def critter(n):
    """A town animal (ducks, roosters: about a thousand HP where townsfolk have NPC_HP or more): it
    wanders off and has no menu, so walking up to talk to it is wasted time."""
    return n.get("hp") is not None and 0 < n["hp"] < NPC_HP // 100


def worth_noting(e):
    """An NPC, a guard or gate keeper the game's NPC list describes as one, or an object (a circle,
    a portal, a sign) - not loot lying on the ground, not another player's pet or summon, not a trap."""
    name = (e.get("name") or "").strip()
    if not name or e.get("id", 0) >= 0:
        return False
    if is_npc(e):
        return "side" in e or plain(name) not in item_names()      # an object, not a dropped item
    if e.get("kind") == "other" and 0 <= (e.get("side") or 0) < 1000:
        return any(a[0] is None and a[1] == "npc" for a in described().get(name.lower(), []))
    return False


def look(b, rec):
    seen = 0
    for e in b.snap()["entities"]:
        if worth_noting(e):
            rec.saw(e)
            seen += 1
        elif e.get("kind") == "monster" and "side" in e and e.get("name"):
            rec.saw_monster(e)
    return seen


# ------------------------------------------------------------------------------------ menus
def page(d):
    """The open dialog as a plain record."""
    top = d.get("top") or {}
    rows = []
    for r, row in enumerate(top.get("rows", [])):
        for c, e in enumerate(row):
            if e:
                rec = {"row": r, "col": c, "flags": e.get("flags"), "text": label(e)}
                for k in ("f", "g", "h", "i"):
                    if e.get(k):
                        rec[k] = e[k]
                if e.get("options"):
                    rec["options"] = e["options"]
                if e.get("cmd"):
                    rec["cmd"] = e["cmd"]
                rows.append(rec)
    return {"kind": top.get("kind"), "entries": rows}


def exact(text):
    return r"^\s*" + re.escape(text.strip()) + r"\s*$"


def open_npc(b, n, path):
    """Talk to the NPC and follow path (entry texts) down the menu. The open dialog, or None."""
    b.close_dialogs()
    if not b.talk(n["name"], (n["x"], n["y"])):
        return None
    for text in path:
        if b.choose(exact(text)) is None:
            return None
        if not b.wait_dialog(3.0):
            return None
    return b.dialog()


def heard(b, since):
    """What the game said aloud since then (agent v25 `said`): an NPC or object that answers
    without a menu ("Requirements not met"), and the description spoken when it is selected."""
    try:
        r = b.a.json("said %d" % int(since * 1000))
    except (Stopped, Respawned):
        raise
    except AgentError as e:        # an older agent has no such command: worth knowing, not fatal
        note_failure(b, "reading what the game said", e)
        return []
    except Exception as e:
        note_failure(b, "reading what the game said", e)
        return []
    return [x["text"] for x in r.get("said", [])] if isinstance(r, dict) else []


def acted(b, n, path, said):
    """The game answered a press aloud ("taken out"): the press did something, so the entries
    beside it may too. Leave the rest of this NPC's menus unread and say what happened."""
    b.log(_("{npc}: \"{entry}\" got the answer \"{said}\" - reading no more of its menus",
            npc=n["name"], entry=" / ".join(path), said=" ".join(said)), speak=True)
    return True


def explore(b, rec, n):
    """Record the NPC's menu tree, breadth first, pressing only safe() entries, and what the game
    says aloud along the way."""
    pages = []
    todo = [[]]
    seen = set()
    while todo and len(pages) < MAX_PAGES:
        path = todo.pop(0)
        asked = clock_for(b).time()
        d = open_npc(b, n, path)
        if d is None or not d.get("open"):
            b.sleep(1.0)           # a spoken answer comes a moment after the talk
            said = heard(b, asked)
            why = getattr(b, "talk_problem", None) if not path else None
            pages.append(dict({"path": path, "error": why or "didn't open"}, **({"said": said} if said else {})))
            if said and path and acted(b, n, path, said):
                break
            continue
        p = page(d)
        said = heard(b, asked)
        if said:
            p["said"] = said
            if path and acted(b, n, path, said):
                pages.append(dict(p, path=path))
                break
        sig = json.dumps([(e["flags"], e["text"]) for e in p["entries"]], ensure_ascii=False)
        if sig in seen:
            continue
        seen.add(sig)
        p["path"] = path
        pages.append(p)
        if len(path) < MAX_DEPTH:
            for e in p["entries"]:
                if safe(e) and e["text"].strip() and e["text"] not in path:
                    todo.append(path + [e["text"]])
    b.close_dialogs()
    return pages


# ------------------------------------------------------------------------------------ the survey
def survey_map(b, mid, talk=True, rect=None, redo=False, go=True):
    """Survey one map (go=False: the one we stand on, wherever that is). Returns its record."""
    mid = int(mid)
    rec = Record(mid)
    if go:
        b.travel(mid)
    me = b.me()
    if redo or not rec.data.get("swept"):
        pts = sweep_points(mid, (me["x"], me["y"]), rect)
        b.log(_("surveying {map}: {n} stops", map=atlas.label(mid), n=len(pts)), speak=True)
        # on the mount between stops, back on it after any fight on the way (as travel does)
        b.traveling = True
        try:
            for i, p in enumerate(pts):
                b.walk_to(p)
                look(b, rec)
                if i % 4 == 3:
                    rec.save()
        finally:
            b.traveling = False
        rec.data["swept"] = True
        rec.data["sweep_rect"] = rect
        rec.save()
    b.log(_("{map}: {n} NPCs", map=atlas.label(mid), n=len(rec.data["npcs"])))
    if talk and any(n.get("menus") is None or redo or retry(n) for n in rec.data["npcs"].values()):
        # Off the beast while reading menus: standing at NPCs for many minutes would only burn
        # fusion power (about 3 a minute fused, 6 a minute back unfused), which the next sweep
        # needs. On foot, whatever attacks us here is fought back as usual.
        was = b.walk_only
        b.walk_only = False
        try:
            beasts.off(b)
            read_menus(b, rec, redo)
        finally:
            b.walk_only = was
    return rec.data


def silent(n):
    """Talked to, and nothing opened: a guard, a sign."""
    return bool(n.get("menus")) and all(p.get("error") == "no answer" for p in n["menus"])


def retry(n):
    """Its menus weren't read because we never got to it (stuck on the way: NPCs block the
    path, and the map doesn't show them), and it hasn't had its second try yet. Records from
    before the reason was kept say "didn't open" for this; one that spoke did answer."""
    m = n.get("menus")
    return (bool(m) and n.get("tries", 1) < 2 and not any(p.get("said") for p in m)
            and all(p.get("error") in ("out of reach", "not found", "didn't open") for p in m))


def read_menus(b, rec, redo=False):
    b.set_target_mode(3)
    for k, n in sorted(rec.data["npcs"].items()):
        if n.get("menus") is not None and not redo and not retry(n):
            continue
        if n.get("menus") is not None and not redo:
            n["tries"] = n.get("tries", 1) + 1
        if critter(n):
            n["menus"], n["critter"] = [], True
            rec.save()
            continue
        # The town's guards all say nothing: once one of a name hasn't answered, the rest of
        # that name on this map are left (each costs three tries to talk).
        twin = next((o for o in rec.data["npcs"].values()
                     if o is not n and o["name"] == n["name"] and silent(o)), None)
        if twin is not None:
            n["menus"], n["same_as"] = [], rec.key(twin)
            rec.save()
            continue
        b.log(_("reading {npc}'s menus", npc=n["name"]))
        try:
            n["menus"] = explore(b, rec, n)
        except Stopped:
            raise
        except Exception as e:      # one odd NPC must not end the survey
            carry_on(e, "reading an NPC's menus")
            n["menus"] = [{"error": str(e)}]
        n["read"] = time.strftime("%Y-%m-%d %H:%M")
        rec.save()
    b.set_target_mode(b.p.get("target_mode", 2))


def city_rect(mid, pad=12):
    """The town zones of a map, merged and padded (NPCs live in towns), or None for the whole map."""
    towns = atlas.maps().get(mid, {}).get("towns", [])
    if not towns:
        return None
    m = atlas.maps()[mid]
    x0 = max(1, min(t[1] for t in towns) - pad)
    y0 = max(1, min(t[2] for t in towns) - pad)
    x1 = min(m["w"], max(t[3] for t in towns) + pad)
    y1 = min(m["h"], max(t[4] for t in towns) + pad)
    return (x0, y0, x1, y1)


_hint_notes = set()          # (map, error kind) already reported by hints()


def hints(mid):
    """Where the game's own data puts NPCs on this map, as (x1, y1, x2, y2) boxes: its town zones,
    the NPC spots of the map table, dungeon entrances (teleportation circles), the transport NPCs
    (cable cars, airships) and NPCs known from the game texts. Empty for a plain farming field:
    of the first 26 maps surveyed, the 11 without any of these had no NPCs but a stray
    teleportation circle (corpse cave) - over an hour of sweeping monster fields for nothing."""
    from .. import atlaslib
    mid = int(mid)
    boxes = [tuple(t[1:5]) for t in atlas.maps().get(mid, {}).get("towns", [])]
    spots = []
    try:
        spots += [(mk["x1"], mk["y1"]) for mk in atlas.model(mid).markers
                  if atlaslib.marker_kind(mk["label"]) == "npc"]
    except Exception as e:         # no model for the map: the sweep is worse, not wrong - say it once
        carry_on(e, "reading the map model for survey hints")
        key = (mid, type(e).__name__)
        if key not in _hint_notes:
            _hint_notes.add(key)
            applog.write("survey hints for map %s: %r" % (mid, e))
    for name in ("dungeons.json", "transports.json"):
        try:
            with open(os.path.join(paths.DATA, name), encoding="utf-8") as f:
                data = json.load(f)
        except (OSError, ValueError):
            continue
        for e in data.get("entrances") or data.get("transports") or []:
            if e.get("map") == mid:
                spots += [tuple(s[:2]) for s in e.get("spots") or []]
                spots += [tuple(e[k]) for k in ("at", "stand") if e.get(k)]
    spots += [(n["x"], n["y"]) for n in knowledge.data().get("npcs", [])
              if n.get("map") == mid and n.get("x") is not None]
    return boxes + [(int(x), int(y), int(x), int(y)) for x, y in spots]


def hint_rect(mid, pad=SIGHT):
    """The part of the map the hints lie in, padded by what one stop sees, or None (no hints)."""
    hs = hints(mid)
    if not hs:
        return None
    m = atlas.maps()[int(mid)]
    return (max(1, min(h[0] for h in hs) - pad), max(1, min(h[1] for h in hs) - pad),
            min(m["w"], max(h[2] for h in hs) + pad), min(m["h"], max(h[3] for h in hs) + pad))


def plan_targets(scope):
    """Which maps a survey visits: 'cities' (every map with a town or village, the undergrounds,
    lifting platforms and ports), 'all' (every map the game's data puts an NPC, a town, a dungeon
    entrance or a transport on - not the plain farming fields), 'everything' (every map you can
    walk out of), or a list of ids."""
    if isinstance(scope, (list, tuple)):
        return [int(m) for m in scope]
    maps = atlas.maps()
    open_world = [mid for mid in maps if not travel.is_instance(mid)]
    if scope == "everything":
        return sorted(open_world)
    if scope == "all":
        return sorted(mid for mid in open_world if hints(mid))
    extra = {1042, 1082, 1122, 1702, 1703, 1704, 1701, 5143, 1001}
    return sorted(mid for mid in open_world if maps[mid].get("towns") or mid in extra)


def tour(start, targets):
    """The targets in visiting order: each next one the fewest walking exits away from the last
    (in map-number order a whole-world survey crossed the world between neighbouring numbers).
    Maps no walking exit leads to (airship and cable-car stops) come last, in number order."""
    maps = atlas.maps()
    links = {mid: {e.get("dest") for e in m.get("exits") or [] if e.get("dest") in maps}
             for mid, m in maps.items()}
    left = set(targets)
    order = []
    here = start
    while left:
        seen = {here}
        ring = [here]
        found = None
        while ring and found is None:
            hits = sorted(mid for mid in ring if mid in left)
            if hits:
                found = hits[0]
                break
            ring = [n for mid in ring for n in sorted(links.get(mid, ())) if n not in seen and not seen.add(n)]
        if found is None:
            break
        order.append(found)
        left.discard(found)
        here = found
    return order + sorted(left)


def done(mid, talk=True):
    """A map whose record is complete: swept, and (talk) every NPC's menus read."""
    data = Record(int(mid)).data
    return bool(data.get("swept")) and (not talk or all(n.get("menus") is not None and not retry(n)
                                                         for n in data["npcs"].values()))


def survey(b, scope="cities", talk=True, redo=False):
    """Survey the maps in scope, riding the Swift fusion beast the whole way as the walking
    assistant does: a survey only looks, so it never stops to fight - it outruns what attacks it,
    and potions keep its HP up."""
    was = b.walk_only
    b.walk_only = True
    try:
        if not [r for r in beasts.owned(b) if r.get("kind") == "swift"]:
            b.log(_("no Swift fusion beast on this character - walking on foot"), speak=True)
        _survey(b, scope, talk, redo)
    finally:
        b.walk_only = was


def _survey(b, scope, talk, redo):
    if scope == "here":
        survey_map(b, b.map_id(), talk=talk, redo=redo, go=False)
        b.log(_("survey finished"), speak=True)
        return
    try:
        start = b.map_id()
    except STOPPING:
        raise
    except Exception as e:
        note_failure(b, "reading the map", e)
        start = None
    targets = plan_targets(scope)
    if start is not None and not isinstance(scope, (list, tuple)):
        targets = tour(start, targets)
    b.log(_("survey of {n} maps", n=len(targets)), speak=True)
    # A survey farms nothing, so the day's farming stop (kept clear of the drop-time reset) doesn't
    # end it; a whole-world survey takes more than a night. Back on for whatever the script does next.
    day_end, b.daily_stop_at = getattr(b, "daily_stop_at", None), None
    try:
        for mid in targets:
            if not redo and done(mid, talk):
                continue          # finished on an earlier run: no trip there again
            rect = city_rect(mid) if scope == "cities" else hint_rect(mid) if scope == "all" else None
            for attempt in range(3):
                try:
                    survey_map(b, mid, talk=talk, rect=rect, redo=redo)
                    break
                except Respawned:
                    # died on the way or on the map and came back: carry on with this map, where
                    # its record left off. The profile's death limit still ends the run.
                    if attempt == 2:
                        b.log(_("skipping {map}: died there every time", map=atlas.label(mid)), speak=True)
                except Stopped as e:
                    if e.final or b.stop_event.is_set():
                        raise         # Stop, a stop rule, the death limit: the survey ends too
                    b.log(_("skipping {map}: {why}", map=atlas.label(mid), why=e), speak=True)
                    break
    finally:
        b.daily_stop_at = day_end
    b.log(_("survey finished"), speak=True)
