# pyright: strict
"""Staying alive: the look-after step of every loop, death, MP, potions, the class's own heal and resting.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

import re
from typing import Any, Literal, Protocol, cast

from ... import atlas, gamekeys
from ...i18n import _
from .. import combat, potions, repair
from .base import (CONFIRM, RESPAWN, RESPAWN_MENU, Respawned, Stopped, potion_kind)
from .parts import Parts


class HealthLink(Protocol):
    def json(self, line: str) -> Any: ...


ASK_EVERY = 3.0       # seconds between keyless HP/MP requests (agent 39): no key, so no reason to wait long
ASK_QUIET = 10.0      # the same while MP is well above the potion mark
SHEET_ASK_EVERY = 5.0  # seconds between keyless sheet requests (agent 43): fresh after every kill
SHEET_WAIT = 2.0      # how long a forced keyless reading waits for the sheet (it comes in ~0.3 s)


class HealthMixin(Parts):
    POTIONS_LOW = 20      # warn once when this few are left
    agent_ask: bool = False
    agent_sheet: bool = False
    _keys_to_fill: bool
    _potion_keys: tuple[float, dict[str, tuple[str, str]], dict[str, str], dict[str, int]] | None
    _heals: bool
    _support_at: float
    _potions_stopped: bool
    _told_potion: dict[str, tuple[str | None, int, float]]
    _potions_had: dict[str, bool]
    _potions_out: dict[str, float]
    _potions_warned: set[str]
    _potions_warned_out: set[str]
    deaths: int
    last_hp_key: float
    last_mp_key: float
    last_vitals_key: float
    maxmp: int
    mp_peak: int
    mp_seen: float
    mp_added: float
    mp_mark: float
    low_since: float | None
    no_rest_until: float
    resting: bool
    skillbook: Any

    @property
    def _game(self) -> HealthLink:
        return cast(HealthLink, self.a)

    def potion(self, kind: str) -> tuple[str | None, str | None]:
        """(key, potion name) for an HP ("hp"), MP ("mp") or pet ("pet_hp", "pet_mp") potion. The
        profile's <kind>_potion names the potion to use, or "auto": the strongest red (HP) or blue
        (MP) potion sitting in the game's hotkey slots (world.aj: slot -> [type, bag uid]), never a
        pet potion for us; with none on a hotkey, the strongest one in the bag. Slot keys come from
        input.ini (W_USE1..12 = 1..0 - =). A potion that isn't on a hotkey comes back as
        (None, name): look_after then uses it from the bag between fights, and keyslots.refill
        puts it on an empty hotkey. A <kind>_key typed in an old profile still wins. Re-read every
        minute, so a slot emptied or changed by hand is noticed."""
        want = (self.p.get(kind + "_potion") or "auto").strip()
        key = self.p.get(kind + "_key", "auto") or ""
        if key != "auto":
            return key, (None if want.lower() == "auto" else want)
        now = self.clock.time()
        cache = getattr(self, "_potion_keys", None)
        if not cache or now - cache[0] >= 60:
            cache = self._read_potion_slots(cache)
        if want.lower() == "auto":
            hit = cache[1].get(kind)
            if hit or kind not in ("hp", "mp", "pet_hp", "pet_mp"):
                return hit or (None, None)
            return None, self.strongest_in_bag(kind, cache[3])
        hit = cache[2].get(want.lower())
        if hit:
            return hit, want
        bag = cache[3]
        return None, (want if bag.get(want.lower()) else None)

    def hotkey_of(self, name: str) -> str | None:
        """The key (1 .. =) of the hotkey slot holding this item, or None. Read with the potion
        slots, again at most every minute."""
        cache = getattr(self, "_potion_keys", None)
        if not cache or self.clock.time() - cache[0] >= 60:
            cache = self._read_potion_slots(cache)
        return cache[2].get(name.lower())

    def _read_potion_slots(self, old: tuple[float, dict[str, tuple[str, str]], dict[str, str], dict[str, int]] | None) -> tuple[float, dict[str, tuple[str, str]], dict[str, str], dict[str, int]]:
        now = self.clock.time()
        keys = gamekeys.slot_key_list()
        raw_slots = self._game.json("mem slots")
        raw_rows = self._game.json("mem bagfull")
        found: dict[str, tuple[int, str, str]] = {}
        by_name: dict[str, str] = {}
        bag: dict[str, int] = {}
        if isinstance(raw_slots, dict) and isinstance(raw_rows, list):
            slots = cast(dict[str, Any], raw_slots)
            rows = cast(list[dict[str, Any]], raw_rows)
            by_uid = {r["uid"]: r for r in rows}
            by_type: dict[Any, dict[str, Any]] = {}
            for r in rows:
                if r.get("count"):
                    bag[r["name"].lower()] = bag.get(r["name"].lower(), 0) + r["count"]
                if r.get("type") is not None and r.get("count"):
                    by_type.setdefault(r["type"], r)
            rank = ("small", "medium", "large", "super", "potent")
            for idx, v in slots.items():
                # a slot is [item type, bag uid]; the uid goes stale when the stack changes, the
                # type doesn't (live: slot 2's Large Blue Potion uid was gone, the potions weren't)
                r: dict[str, Any] | None = None
                if isinstance(v, list):
                    slot = cast(list[Any], v)
                    if len(slot) > 1:
                        r = by_type.get(slot[0]) or by_uid.get(slot[1])
                i = int(idx) - 1
                if not r or not r.get("count") or not 0 <= i < len(keys):
                    continue
                name = r["name"].lower()
                by_name.setdefault(name, keys[i])    # any item: toolboxes are looked up here too
                k = potion_kind(name)
                if not k:
                    continue
                strength = max([n for n, w in enumerate(rank) if w in name] or [0])
                if k not in found or strength > found[k][0]:
                    found[k] = (strength, keys[i], r["name"])
        picked: dict[str, tuple[str, str]] = {k: (v[1], v[2]) for k, v in found.items()}
        if not old or old[1] != picked:
            for k, v in found.items():
                self.log(_("{what} potion: {item} on key {key}", what=k.upper(), item=v[2], key=v[1]))
            if old:
                self.potion_keys_lost(old[1], picked, bag)
        self._potion_keys = (now, picked, by_name, bag)
        return self._potion_keys

    def potion_keys_lost(self, before: dict[str, tuple[str, str]], now: dict[str, tuple[str, str]], bag: dict[str, int]) -> None:
        """Say so when the red or blue potion we drank from a hotkey is no longer there, and have
        keyslots.refill put what the bag still holds on an empty hotkey between fights. Used up
        is the common case; a hotkey can also be taken over: Left Ctrl + a hotkey puts the panel
        item under the cursor on it (live: a pet summoned through the pet panel took the blue
        potions' key, and the bot drank no blue for the rest of the night with 345 in the bag)."""
        for kind in ("hp", "mp", "pet_hp", "pet_mp"):
            old = before.get(kind)
            if not old or now.get(kind) == old:
                continue
            key, item = old
            if bag.get(item.lower()):
                self.log(_("{item} is no longer on key {key} - the game put something else there",
                           item=item, key=key), speak=True)
            elif not now.get(kind):
                self.log(_("the {item} on key {key} are used up", item=item, key=key), speak=True)
            if not now.get(kind) and self.strongest_in_bag(kind, bag):
                self._keys_to_fill = True

    @staticmethod
    def strongest_in_bag(kind: str, bag: dict[str, int]) -> str | None:
        """The strongest potion of that kind in the bag (name -> count): red ("hp"), blue ("mp"),
        or the pet's ("pet_hp", "pet_mp") - never a pet's for us; None without one."""
        rank = ("small", "medium", "large", "super", "potent")
        names = [n for n, c in bag.items() if c and potion_kind(n) == kind]
        if not names:
            return None
        best = max(names, key=lambda n: (max([i for i, w in enumerate(rank) if w in n] or [0]), bag[n]))
        return best.title()          # (the bag panel matches names in any case)

    def potion_stock(self, kind: str) -> int | None:
        """How many potions of that kind ("hp", "mp") the bag holds, any size, from the last slot
        reading (at most a minute old); None before the first reading."""
        cache = getattr(self, "_potion_keys", None)
        if not cache:
            return None
        return sum(n for name, n in cache[3].items() if potion_kind(name) == kind)

    def watch_potions(self, now: float) -> None:
        """Running out of potions ends the run instead of farming on without them: a melee class
        can drink one every half minute, and with none left the next pack kills it. A warning
        first, when few are left; at none, read the bag again a few seconds later to be sure, then
        stop - the guard then defends and ends at the plan's end place. Potions the restock rules
        top up are left to the restock trip; a class that heals itself only gets the warnings for
        reds."""
        if not self.p.get("stop_when_out_of_potions", True) or self._potions_stopped:
            return        # once: the guard's defend() and finish() run through here too
        restock = cast(list[dict[str, Any]], self.p.get("restock") or [])
        restocked = {str(r.get("item") or "").lower() for r in restock}
        for kind, label in (("hp", _("red")), ("mp", _("blue"))):
            if kind == "mp" and not self.p.get("mp_use", True):
                continue
            stock = self.potion_stock(kind)
            if stock is None:
                continue
            had = self._potions_had
            if stock > 0:
                had[kind] = True
                self._potions_out.pop(kind, None)
                if stock <= self.POTIONS_LOW and kind not in self._potions_warned:
                    self._potions_warned.add(kind)
                    self.log(_("only {n} {kind} potions left", n=stock, kind=label), speak=True)
                continue
            if not had.get(kind) or any(potion_kind(str(n)) == kind for n in restocked):
                continue      # never carried any (the start says so), or a restock trip brings more
            since = self._potions_out.get(kind)
            if since is None:
                self._potions_out[kind] = now
                if self._potion_keys is not None:
                    self._potion_keys = (0.0, self._potion_keys[1], self._potion_keys[2], self._potion_keys[3])   # read again next time
                continue
            if now - since < 3:
                continue
            if kind == "hp" and getattr(self, "_heals", False):
                if kind not in self._potions_warned_out:
                    self._potions_warned_out.add(kind)
                    self.log(_("no red potions left - healing with skills only"), speak=True)
                continue
            self._potions_stopped = True
            raise Stopped(_("out of {kind} potions - stopping", kind=label), final=True)

    def potion_plan(self, kind: str, top: float) -> tuple[float, float, dict[str, Any] | None]:
        """(drink below percent, seconds between, potion info or None) for "hp"/"mp": the profile's
        numbers where the player typed some, else worked out from the potion the game describes."""
        _key, name = self.potion(kind)
        inf = potions.info(name)
        below = self.p.get(kind + "_below", "auto")
        if potions.auto(below):
            below = potions.mark(inf, top) or potions.FALLBACK_MARK
        gap = self.p.get(kind + "_cooldown", "auto")
        if potions.auto(gap):
            gap = inf["cd"] + 0.3 if inf else potions.FALLBACK_GAP
        told = getattr(self, "_told_potion", {})
        say = (name, int(below), gap)
        if name and told.get(kind) != say:
            told[kind] = say
            self._told_potion = told
            self.log(_("{what}: {item} - drinking below {pct}%, every {secs} seconds at most",
                       what=kind.upper(), item=name, pct=int(below), secs="%g" % gap))
        return float(below), float(gap), inf

    def drink(self, kind: str, name: str | None, key: str | None) -> bool:
        """Press the potion's hotkey; a potion that isn't on a hotkey is used from the bag - between
        fights, or in one too when the agent uses it by uid (bag_in_fight: no arrow keys then)."""
        if key:
            self.key(key)
            return True
        if name and self.bag_in_fight():
            return self.use_item(name)
        return False

    def heal_with_skill(self, me: dict[str, Any], share: float, now: float) -> bool:
        """A class with its own quick heal (Sorceress, Priest) heals with it (the skill picker in
        fights, support_between_fights otherwise); red potions are then only the backup under
        heal_potion_below. True when this character has such a heal on its keys."""
        p = self.p
        if self.walk_only or not p.get("heal_with_skill", True) or not p.get("smart_skills", True):
            return False     # (a Swift beast turns every skill off)
        if self.skillbook is None:
            if share >= p.get("heal_below", 85) and not self.resting:
                return bool(getattr(self, "_heals", False))
            self.skillbook = cast(Any, combat.SkillBook(self))
        sb = self.skillbook
        healer = sb.healer()
        if not (sb.ok and healer):
            return False
        if not getattr(self, "_heals", False):
            self._heals = True
            self.log(_("heals with {skill} below {pct}% HP; red potions only below {low}%",
                       skill=healer["name"], pct=p.get("heal_below", 85), low=p.get("heal_potion_below", 50)))
        return True

    def support_between_fights(self, me: dict[str, Any], now: float) -> None:
        """Out of a fight the skill picker doesn't run, so heal, dispel and keep long buffs up here
        (every class: buffs and dispels aren't only for healers). Never into a menu of ours."""
        p = self.p
        if self.walk_only or not p.get("smart_skills", True):
            return           # a Swift beast turns every skill off
        # Inside a fight the skill picker presses everything - except while we walk up to the
        # target: then only shields and big heals, here. Walking into a pack is when a melee
        # character takes the most damage, and the picker isn't running then.
        if self.in_fight and not getattr(self, "approaching", False):
            return
        if now - getattr(self, "_support_at", 0) < 0.5:
            return
        self._support_at = now
        if self.skillbook is None:
            share = combat.hp_pct(me)
            vitals = cast(dict[str, Any], me.get("vitals") or {})
            if share >= p.get("heal_below", 85) and not vitals.get("debuffs") \
                    and not getattr(self, "modes_done", False):
                return            # nothing to do yet, and no fight seen: don't read the skills early
            self.skillbook = cast(Any, combat.SkillBook(self))
        sb = self.skillbook
        if not sb.ok:
            return
        sb.read_casts(me.get("id"))
        r = sb.emergency(me, now) if self.in_fight else sb.support(me, now)
        if isinstance(r, tuple) and not self.dialog().get("open"):
            self.key(r[0])

    def handle_death(self) -> None:
        """We are dead: answer the respawn menu the way the profile says, wait to be alive again,
        then raise Respawned so the farm starts over (travelling back if we are elsewhere).
        Resurrection Crystals are bag items, so only with use_resurrection_crystal (the player's
        rule: no bag items unless allowed). Stops after max_deaths, so a map that is too hard
        doesn't kill us all night - but only once we are alive again in the city, never leaving
        the character dead where it fell. With stop_on_deaths off (or max_deaths 0) it never
        stops for deaths: it respawns and goes back every time."""
        self.deaths += 1
        self.let_go()
        where = atlas.label(self.map_id())
        self.log(_("died on {map} ({n} so far)", map=where, n=self.deaths), speak=True)
        limit = self.p.get("max_deaths", 3) if self.p.get("stop_on_deaths", True) else 0
        last = bool(limit) and self.deaths > limit
        order: list[str] = []
        if self.p.get("use_resurrection_crystal", False) and not last:
            order += ["crystal_spot", "crystal_map"]
        if last or self.p.get("respawn", "map") != "map":
            order += ["city", "map"]
        else:
            order += ["map", "city"]
        for _try in range(30):            # the menu takes a moment to open after dying
            d = self.dialog()
            top = d.get("top")
            if top and any(re.search(RESPAWN_MENU, t, re.I) for row in top["rows"] for e in row if e
                           for t in self.entry_texts(e)) or (top and any(
                               self.find_entry(top, RESPAWN[k]) for k in ("map", "city"))):
                break
            self.sleep(0.5)
        else:
            raise Stopped(_("dead, but the respawn menu never opened - please respawn by hand"), final=True)
        for k in order:
            if self.find_entry(top, RESPAWN[k]) is None:
                continue
            self.log(_("respawning: {how}", how={"map": _("on this map"), "city": _("in the city"),
                                                "crystal_spot": _("here, with Resurrection Crystals"),
                                                "crystal_map": _("on this map, with Resurrection Crystals")}[k]))
            if self.choose(RESPAWN[k]) is None:
                continue
            for _i in range(3):           # "Are you sure ...?"
                d = self.dialog()
                if d["open"] and d.get("top") and self.find_entry(d["top"], CONFIRM) is not None:
                    self.choose(CONFIRM)
                else:
                    break
            for _i in range(40):
                me = self.me()
                if me.get("hp", 0) > 0:
                    self.last_hp = None
                    self.log(_("alive again on {map}", map=atlas.label(self.map_id())))
                    if last:
                        raise Stopped(_("died {n} times - stopping; this place may be too hard", n=self.deaths), final=True)
                    raise Respawned()
                self.sleep(0.5)
        raise Stopped(_("couldn't respawn - please do it by hand"), final=True)

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        """Open the character sheet (W_SELF_INFO, the backquote key), let the agent read it, close
        it. Only between fights: in a fight W/S would browse the window instead of walking. The
        sheet is where the bag's load, the drop time, fusion power, contribution and experience come
        from (e.g. "MP 30000/45000 ... fusion power 99.8 ... load 2000/4000"). It opens a
        window, so it is read rarely: every sheet_every (10 minutes), sooner only when the bag is
        close to full. MP comes from the own-HP key instead (check_mp).

        From agent 43 the agent asks for the sheet itself and keeps it off the screen (ask_sheet):
        no key, no window, so it is read every few seconds - after every kill - and contribution,
        experience and the drop time stay current. The key is the way back when that fails."""
        if self.agent_sheet:
            got = self.ask_sheet(force)
            if got is not False:
                return got
        key = self.p.get("sheet_key")
        now = self.clock.time()
        every = self.p.get("sheet_every", 600)
        full = self.p.get("bag_full_at", 100)
        if self.load_share is not None and full:
            if self.load_share >= full - 1:
                every = min(every, 30)
            elif self.load_share >= full - 3:
                every = min(every, 60)
            elif self.load_share >= full - 10:
                every = min(every, 180)
        if not (self.p.get("vitals_key") or self.agent_ask) and self.p.get("mp_use", True) and self.mp_share is not None:
            # No own-HP key: MP is only known from here, so near the potion mark look again sooner,
            # or a skill-heavy class runs dry between two readings.
            if self.mp_share < getattr(self, "mp_mark", 80.0) + 10:
                every = min(every, 15)
        if not key or (not force and now - self.last_sheet < every):
            return self.me().get("sheet")
        self.last_sheet = now
        self.key(key)
        sheet = None
        for _i in range(12):
            self.sleep(0.25)
            sheet = self.me().get("sheet")
            if sheet and sheet.get("age", 999) <= 3:
                break
        self.close_dialogs()
        return sheet

    def ask_sheet(self, force: bool = False) -> dict[str, Any] | None | Literal[False]:
        """The sheet without the key (agent 43): every SHEET_ASK_EVERY seconds a request goes out,
        and the reading it brings lands in `mem self` a moment later. Nobody waits for it - this
        call answers with the last reading, so the kill cycle loses no time - except with force,
        where the caller needs it now (after selling, at the start). False when the agent can't
        send it: then the key does it, for the rest of the run."""
        now = self.clock.time()
        if not force and now - self.last_sheet < SHEET_ASK_EVERY:
            return self.me().get("sheet")
        try:
            reply = self.a.send("ask sheet")
        except Exception:      # an old fake link, or the game closing: the key is the way
            return False
        if reply == "error: too soon":
            return self.me().get("sheet")
        if reply != "asked":
            if "id isn't known" not in reply:
                self.agent_sheet = False
                self.log(_("the character sheet is read with the key again: {why}", why=reply))
            return False
        self.last_sheet = now
        if not force:
            return self.me().get("sheet")
        sheet = None
        end = now + SHEET_WAIT
        while self.clock.time() < end:
            self.sleep(0.1)
            sheet = self.me().get("sheet")
            if sheet and sheet.get("age", 999) <= 0:
                break
        return sheet

    def mp_now(self, me: dict[str, Any], now: float) -> tuple[float, float] | None:
        """(mp, max mp) from the freshest reading, or None. The own-HP key's answer (agent
        "vitals") has MP but not max MP; that comes from any character sheet seen this game, else
        from the highest MP read this run."""
        sheet = cast(dict[str, Any], me.get("sheet") or {})
        if sheet.get("maxmp"):
            self.maxmp = sheet["maxmp"]
        best = None          # (arrived at, mp)
        v = cast(dict[str, Any], me.get("vitals") or {})
        if v and v.get("mp", -1) >= 0 and v.get("age_ms", 1e9) < 120000:
            best = (now - v["age_ms"] / 1000.0, v["mp"])
        if sheet.get("maxmp") and sheet.get("age", 999) < 120:
            at = now - sheet["age"]
            if best is None or at > best[0] + 1.5:      # sheet ages are whole seconds
                best = (at, sheet["mp"])
        if best is None:
            return None
        self.mp_peak = max(self.mp_peak, best[1])
        # No sheet seen in this game yet: the highest MP read so far stands in for the maximum,
        # or MP would never be looked after (a run that never reads the sheet, like following a
        # player, emptied a Warrior's MP down to nothing that way). The runner reads the sheet at
        # the start; this covers a sheet that couldn't be read.
        top = self.maxmp or self.mp_peak
        if not top:
            return None
        if abs(best[0] - self.mp_seen) > 0.75:          # a new reading replaces the potion guess
            self.mp_seen, self.mp_added = best[0], 0.0
        return best[1], top

    def check_mp(self, now: float) -> None:
        """Ask the server for our HP and MP now and then: it answers HP, MP, buffs and debuffs and
        nothing opens. From agent 39 the request goes without a key (`ask vitals`), so it can come
        every few seconds; before that, or when the agent can't send it, the own-HP key does it.
        Often near the potion mark, rarely when MP is high. Fights included: it doesn't move the
        target (the client keeps its own selected target)."""
        key = self.p.get("vitals_key")
        if not (key or self.agent_ask):
            return
        sb = self.skillbook
        dispels = sb is not None and sb.ok and any(sb.self_dispel(x) for x in sb.support_skills())
        quiet = self.mp_share is not None and self.mp_share >= getattr(self, "mp_mark", 80.0) + 10
        quiet = quiet and not (dispels and self.in_fight)   # a class with a dispel looks for debuffs in fights
        if self.agent_ask:
            every = ASK_QUIET if quiet else ASK_EVERY
        else:
            every = self.p.get("mp_check_every", 10)
            if quiet:
                every = max(every, 30)
        if now - self.last_vitals_key < every:
            return
        asked = self.ask_vitals("vitals") if self.agent_ask else False
        if asked is None:
            return                         # another request's answer is on its way: next tick
        self.last_vitals_key = now
        if not asked and key:
            self.key(key)

    def bag_in_fight(self) -> bool:
        """May a bag item be used now? Always outside a fight; in one only when the agent uses it
        by uid (agent 40) - the arrow keys would walk us around the monster."""
        return not self.in_fight or bool(getattr(self, "agent_act", False))

    def ask_vitals(self, what: str) -> bool | None:
        """`ask vitals` (ours) or `ask petvitals` (the pet out): True when the request went, None
        when the agent wants a moment (an answer to the last one is due), False when it can't be
        sent - then the key does it. A game whose connection the agent can't find falls back to
        the keys for the rest of the run."""
        try:
            reply = self.a.send("ask " + what)
        except Exception:      # an old fake link, or the game closing: the key is the way
            return False
        if reply == "asked":
            return True
        if reply == "error: too soon":
            return None
        if "id isn't known" not in reply:
            self.agent_ask = False
            self.log(_("HP and MP are read with the key again: {why}", why=reply))
        return False

    def look_after(self, me: dict[str, Any] | None = None) -> dict[str, Any]:
        """Heal, refresh the sheet, check stop rules. Runs inside every loop."""
        self.check()
        me = cast(dict[str, Any], me or self.me())
        if me.get("hp", 1) <= 0:
            self.handle_death()
        now = self.clock.time()
        hp, top = me.get("hp", 0), me.get("maxhp", 0) or 1
        share = 100.0 * hp / top
        p = self.p

        # Keep HP near full: drink as soon as one potion's worth is missing (potion_plan), as
        # often as the potion's cooldown allows.
        healer = self.heal_with_skill(me, share, now)
        self.support_between_fights(me, now)
        self._hp_potion(hp, top, share, now, healer)
        self._mp_potion(me, now)
        if p.get("mp_use", True):
            self.check_mp(now)
        self.watch_potions(now)

        # The game's first "about to break" warning: one toolbox mends everything we wear.
        repair.check(self)

        if not self.requests.empty():
            self.run_requests()

        if share < p["panic_below"] and p.get("stop_on_panic", False):
            self.low_since = self.low_since or now
            if now - self.low_since > p["panic_after"]:
                raise Stopped(_("HP stayed under {pct}% for {secs} seconds - stopping so you can take over",
                                pct=p["panic_below"], secs=int(p["panic_after"])), final=True)
        else:
            self.low_since = None

        for cond, message in self.watches:
            if cond():
                raise Stopped(message, final=True)
        assert me is not None
        return me

    def _hp_potion(self, hp: float, top: float, share: float, now: float, healer: Any) -> None:
        hp_key, hp_name = self.potion("hp")
        if not (hp_key or hp_name):
            return
        below, gap, _inf = self.potion_plan("hp", top)
        if healer:
            below = min(below, self.p.get("heal_potion_below", 50))   # the heal skill does the rest
        if share < below and now - self.last_hp_key >= gap and (hp_key or self.bag_in_fight()):
            self.log(_("HP {hp}/{max} ({pct}%) - {item}", hp=hp, max=top, pct=int(share),
                       item=hp_key or hp_name))
            if self.drink("hp", hp_name, hp_key):
                self.last_hp_key = now

    def _mp_potion(self, me: dict[str, Any], now: float) -> None:
        """MP comes from the own-HP key (R: the server answers HP and MP, no window) or, without that
        key, from the character sheet. Between two readings we add what each potion gave back
        (the game's own numbers), so it can keep drinking every cooldown until MP is up again
        instead of once per reading. A new reading replaces the guess."""
        self.mp_share = None
        reading = self.mp_now(me, now) if self.p.get("mp_use", True) else None
        if not reading:
            return
        mp_now, top_mp = reading
        mshare = 100.0 * min(top_mp, mp_now + self.mp_added) / top_mp
        self.mp_share = mshare
        mp_key, mp_name = self.potion("mp")
        if not (mp_key or mp_name):
            return
        below, gap, inf = self.potion_plan("mp", top_mp)
        self.mp_mark = below
        if mshare < below and now - self.last_mp_key >= gap and (mp_key or self.bag_in_fight()):
            self.log(_("MP about {pct}% - {item}", pct=int(mshare), item=mp_key or mp_name))
            if self.drink("mp", mp_name, mp_key):
                self.last_mp_key = now
                # unknown potion: assume it filled us up until the next reading
                self.mp_added += potions.recovers(inf, top_mp) if inf else top_mp

    def rest_up(self, me: dict[str, Any]) -> bool:
        self.resting = True
        try:
            with self.timed("rest"):
                return self._rest_up(me)
        finally:
            self.resting = False

    def _rest_up(self, me: dict[str, Any]) -> bool:
        """Nothing on us but HP well down: drink and wait before taking on the next monster,
        instead of pulling a new one (and its friends) at half HP."""
        share = combat.hp_pct(me)
        if share >= self.p.get("rest_below", 60) or self.attackers():
            return False
        if self.clock.time() < getattr(self, "no_rest_until", 0):
            return False          # resting just did nothing: fight (and try resting again later)
        self.log(_("HP {pct}% - resting before the next fight", pct=int(share)))
        start = self.clock.time()
        end = start + 45
        until = self.p.get("rest_until", 90)
        best = share
        while self.clock.time() < end:
            self.sleep(0.5)
            me = self.look_after()
            now = combat.hp_pct(me)
            if now >= until or self.attackers():
                break
            best = max(best, now)
            # HP doesn't come back by standing still: only potions, heals and - for a class that
            # heals itself from the damage it deals (a Hunter's Survival Expert) - hitting. With
            # none of those working, resting only stands there (live: 15 minutes at 40%).
            if self.clock.time() - start >= 12 and best <= share + 1:
                self.log(_("HP isn't coming back while resting ({pct}%) - fighting on", pct=int(now)))
                self.no_rest_until = self.clock.time() + 120
                return False
        return True
