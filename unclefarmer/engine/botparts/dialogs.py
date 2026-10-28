"""NPCs and the game's menus: reading and choosing entries, finding and talking to NPCs, popups.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

import math
import time
from typing import Any

from ... import applog, atlas, jsonio as json, knowledge, travel
from ...i18n import _
from .. import team
from .base import (Stopped, note_failure, steps)
from ...link import GameLink
from .parts import as_bot, Parts


class DialogsMixin(Parts):
    NPC_SPOT = 10.0       # an NPC sought at a known spot is the one of that name this close to it
    TRANSIENT: tuple[str, ...]
    last_popup_check: float
    talk_problem: str | None
    modes_done: bool

    @property
    def _game(self) -> GameLink:
        return self.a

    def clear_popups(self, force: bool = False) -> bool:
        """A window the bot didn't open - a team or trade invite, a notice - turns the walking keys
        into its cursor keys, so the character stops dead (in testing, "<name> invites you to join
        the team" ended a run as six stucks in a row). Close it with Escape; an invite is
        declined. Only called where no dialog of ours can be open: walking and fighting."""
        if not self.p.get("close_popups", True):
            return False
        now = self.clock.time()
        if not force and now - self.last_popup_check < 2.0:
            return False
        self.last_popup_check = now
        d = self.dialog()
        if not d.get("open"):
            return False
        top: dict[str, Any] = d.get("top") or {}
        rows: list[list[dict[str, Any] | None]] = top.get("rows", [])
        texts = [t for row in rows for e in row if e for t in self.entry_texts(e)[:1]]
        inv = team.invite_in(texts)
        if inv and self.trusted.has(inv[0], link=self.a):
            self.note_dialog(d, "team invite from a trusted player")
            if team.accept_invite(as_bot(self)):
                self.log(_("joined {who}'s team ({team}): {who} is trusted", who=inv[0], team=inv[1]), speak=True)
                return True
        self.note_dialog(d, "popup closed")
        self.log(_("closed a window the game opened: {what}", what=(texts[0] if texts else "?")[:90]))
        self.close_dialogs()
        return True

    def dialog(self) -> dict[str, Any]:
        """The open dialog. Reads while the game is mid-change ("busy: ...") are retried: the game
        shows and closes these windows on its own, so a single busy answer is not a fatal error."""
        end = self.clock.time() + 5.0
        while True:
            d = self._game.json("dialog")
            if d.get("ok"):
                return d
            why = d.get("error", "?")
            transient = getattr(self, "TRANSIENT", ())
            if not str(why).startswith(transient) or self.clock.time() > end:
                raise Stopped(_("the game's dialogs can't be read ({why}) - restart the game so the new agent loads",
                                why=why))
            self.sleep(0.25)

    def note_dialog(self, d: dict[str, Any], why: str) -> None:
        """Every dialog the bot meets goes to logs/dialogs.log: that's how menus get learned."""
        top = d.get("top")
        if not top:
            return
        map_id = self.map_id()          # a lost agent must surface, not look like a write failure
        lines = ["%s  %s  map %s  %s  row %s col %s\n" % (time.strftime("%Y-%m-%d %H:%M:%S"),
                                                         getattr(self, "character", "") or "?",
                                                         map_id, why, top["row"], top["col"])]
        for r, row in enumerate(top["rows"]):
            for c, e in enumerate(row):
                if e:
                    lines.append("    [%d,%d] %s\n" % (r, c, json.dumps(e, ensure_ascii=False)))
        problem = applog.dialog("".join(lines))
        if problem is not None:
            note_failure(self, "writing logs/dialogs.log", problem)

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
        out = [e.get("text"), e.get("raw")] + [e.get(k) for k in ("f", "g", "h", "i")]
        return [t for t in out + list(e.get("options") or []) if t]

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        if not top:
            return None
        for r, row in enumerate(top["rows"]):
            for c, e in enumerate(row):
                if e and any(travel.matches(pattern, t) for t in self.entry_texts(e)):
                    return r, c
        return None

    def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None:
        end = self.clock.time() + seconds
        while self.clock.time() < end:
            d = self.dialog()
            if (d["open"] > 0) == want_open:
                return d
            self.sleep(0.15)
        return None

    def close_dialogs(self) -> None:
        for _i in range(6):
            if self.dialog()["open"] == 0:
                return
            self.key(self.p.get("esc_key", "esc"))
            self.sleep(0.3)

    def find_in_row(self, top: dict[str, Any] | None, row: str, pattern: str) -> tuple[int, int] | None:
        """The entry matching pattern in the row whose first entry matches row (a member list: one
        row per player, their name first and what can be done to them beside it)."""
        if not top:
            return None
        rows: list[list[dict[str, Any] | None]] = top["rows"]
        for r, cells in enumerate(rows):
            first = cells[0] if cells else None
            if not first or not any(travel.matches(row, t) for t in self.entry_texts(first)):
                continue
            for c, e in enumerate(cells):
                if not (c and e):
                    continue
                options: list[str] = e.get("options") or []
                if any(travel.matches(pattern, t) for t in options):
                    return r, c
        return None

    def choose(self, pattern: str, row: str | None = None) -> dict[str, Any] | None:
        """Pick the entry matching pattern in the open dialog with the walking keys, then Enter.
        row: only in the row whose first entry matches it (find_in_row). Returns the entry, or None
        (dialog closed again) if nothing matches."""
        d = self.dialog()
        top = d.get("top")
        if not top:
            return None
        self.note_dialog(d, "choosing /%s/" % pattern if row is None else "choosing /%s/ in /%s/" % (pattern, row))
        where = self.find_entry(top, pattern) if row is None else self.find_in_row(top, row, pattern)
        if where is None:
            self.log(_("the menu has no entry like \"{want}\"", want=pattern), speak=True)
            self.close_dialogs()
            return None
        r, c = where
        mk = self.p["move_keys"]
        for axis, want, back, fwd in (("row", r, mk["up"], mk["down"]), ("col", c, mk["left"], mk["right"])):
            flips = 0
            key = back if top[axis] > want else fwd
            for _press in range(len(top["rows"]) * 2 + 6):
                if top[axis] == want:
                    break
                before = top[axis]
                self.key(key)
                self.sleep(0.12)
                top = self.dialog().get("top") or top
                # Not sure which way W moves the cursor, and lists can wrap: if a press moved us
                # further away, turn round once.
                if abs(top[axis] - want) > abs(before - want) and flips == 0:
                    key, flips = (fwd if key == back else back), 1
            if top[axis] != want:
                self.log(_("couldn't move to the menu entry"), speak=True)
                self.close_dialogs()
                return None
        entry = top["rows"][r][c]
        self.log(_("choosing \"{entry}\"", entry=(entry.get("options") or [None])[0] or entry.get("text") or entry.get("raw")))
        self.key(self.p.get("enter_key", "enter"))
        self.sleep(0.4)
        return entry

    def find_npc(self, name: str, near: tuple[float, float] | None = None, within: float | None = None) -> dict[str, Any] | None:
        """The NPC with that name the game knows about, the one nearest `near` (or us). within:
        only one that close to near - two NPCs share a name (a city's two airship administrators
        fly to different cities), and the one in sight is not the one at the spot we want."""
        snap = self.snap()
        me = snap["self"]
        x0, y0 = near if near else (me["x"], me["y"])
        hits = [e for e in snap["entities"] if (e.get("name") or "").strip().lower() == name.strip().lower()]
        if near and within is not None:
            hits = [e for e in hits if math.hypot(e["x"] - x0, e["y"] - y0) <= within]
        hits.sort(key=lambda e: math.hypot(e["x"] - x0, e["y"] - y0))
        return hits[0] if hits else None

    def look_for_npc(self, name: str) -> dict[str, Any] | None:
        """The NPC isn't in sight and its place isn't in the transport table: the game only shows
        what is within about 30 tiles, so arriving at a town's far edge it can be out of view. Walk
        to where the game data puts an NPC of that name on this map, then to the town square, then
        across the whole town, stopping as soon as it comes into view."""
        mid = self.map_id()
        want = name.strip().lower()
        npcs: list[dict[str, Any]] = knowledge.data().get("npcs", [])
        spots: list[tuple[int, int]] = [(int(n["x"]), int(n["y"])) for n in npcs
                 if n.get("map") == mid and (n.get("name") or "").strip().lower() == want]
        sweep: list[tuple[int, int]] = []
        for c in knowledge.cities():
            if c.get("map") == mid:
                if c.get("square_cell"):
                    spots.append((int(c["square_cell"][0]), int(c["square_cell"][1])))
                r = c.get("town_rect")
                if r:
                    # then the whole town, in steps the game's view (about 30 tiles) covers
                    sweep += [(x, y) for x in steps(int(r[0]) + 20, int(r[2]) - 20, 40)
                              for y in steps(int(r[1]) + 20, int(r[3]) - 20, 40)]
        me = self.me()
        x, y = me["x"], me["y"]
        while sweep:          # nearest next, so the sweep doesn't cross the town back and forth
            nxt = min(sweep, key=lambda c: math.hypot(c[0] - x, c[1] - y))
            sweep.remove(nxt)
            spots.append(nxt)
            x, y = nxt
        if not spots:
            return None
        self.log(_("{npc} isn't in sight - looking for it", npc=name))
        m = atlas.model(mid)
        def seen(_me: Any) -> bool:
            return self.find_npc(name) is not None
        for spot in spots:
            me = self.me()
            here = (int(me["x"]), int(me["y"]))
            here = here if m.ok(*here) else (m.nearest_walkable(here[0], here[1], 4) or here)
            cell = spot if m.ok(*spot) else m.nearest_walkable(spot[0], spot[1], 20)
            if not cell or not m.route(here, (cell[0], cell[1])):
                continue
            self.walk_to(cell, near=seen)
            if self.map_id() != mid:
                return None
            npc = self.find_npc(name)
            if npc is not None:
                return npc
        return None

    def talk(self, name: str, at: tuple[float, float] | None = None, stand: tuple[float, float] | None = None) -> bool:
        """Walk next to the NPC, select it with the target keys and press Enter. True once its
        dialog is open."""
        self.talk_problem = None
        npc = self.approach(name, at, stand)
        if npc is None:
            self.talk_problem = "not found"
            return False
        me = self.me()
        if math.hypot(me["x"] - npc["x"], me["y"] - npc["y"]) > 5:
            # the walk ended short of it (stuck): from here the target keys only reach the NPCs
            # round us, and three rounds of them were wasted before every "didn't answer"
            self.talk_problem = "out of reach"
            self.log(_("couldn't get to {npc}", npc=name), speak=True)
            return False
        self.close_dialogs()
        # The target keys must pick NPCs now; whoever called talk() puts the mode back once the
        # dialog is closed (set_modes() runs again before the next fight).
        self.modes_done = False
        if not self.set_target_mode(3):
            self.log(_("couldn't switch the target key to NPCs"), speak=True)
        for attempt in range(3):
            npc = self.find_npc(name, at, self.NPC_SPOT if at else None) or npc
            # the last try with the game's own target keys: whatever selecting by id leaves out,
            # the keys did it before (live 2026-10-22 a Cable Car manager never answered)
            if not self.select_target(npc, by_keys=attempt == 2):
                continue
            self.key(self.p.get("enter_key", "enter"))
            if self.wait_dialog(3.0):
                return True
        self.talk_problem = "no answer"
        self.log(_("{npc} didn't answer", npc=name), speak=True)
        return False

    def approach(self, name: str, at: tuple[float, float] | None = None, stand: tuple[float, float] | None = None) -> dict[str, Any] | None:
        """Walk up to the NPC on this map, into talking range. The NPC as the game sees it, or None
        (said aloud) when it can't be found or reached."""
        within = self.NPC_SPOT if at else None
        npc = self.find_npc(name, at, within)
        if npc is None and at:
            # stop as soon as it comes into view: from there we walk to a cell beside it. Only the
            # one at that spot counts (the survey talked to Giant Rock's left airship administrator
            # twice, the right one never, 2026-10-11).
            def in_view(_me: dict[str, Any]) -> bool:
                return self.find_npc(name, at, within) is not None
            spot = stand or at
            self.walk_to((spot[0], spot[1]), near=in_view)
            npc = self.find_npc(name, at, within)
        if npc is None:
            npc = self.look_for_npc(name)
        if npc is None:
            self.log(_("can't find {npc} here", npc=name), speak=True)
            return None
        m = atlas.model(self.map_id())
        if not stand or math.hypot(stand[0] - npc["x"], stand[1] - npc["y"]) > 3:
            me = self.me()
            stand = m.nearest_walkable(int(round(npc["x"])), int(round(npc["y"])) + 1, 4,
                                       m.comp_of(int(me["x"]), int(me["y"])))
            if not stand:
                self.log(_("can't get next to {npc}", npc=name), speak=True)
                return None
        # Stop once in talking range: the cells right by an NPC are often blocked by the NPC or its
        # counter (live 2026-10-04: four stucks in a row walking up to Apothecary Phillips).
        def in_range(me: dict[str, Any]) -> bool:
            if math.hypot(me["x"] - npc["x"], me["y"] - npc["y"]) > 3.5:
                return False
            line = list(m.line((int(me["x"]), int(me["y"])), (int(round(npc["x"])), int(round(npc["y"])))))
            return all(m.ok(x, y) for x, y in line[:-1])   # the NPC's own cell may be a wall
        if self.walk_to((stand[0], stand[1]), near=in_range) == "stuck":
            self.stuck_in_a_row = 0   # a near miss by a counter is not a lost bot
        return npc
