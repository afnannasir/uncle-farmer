"""The panels and the backpack: cursors, using and throwing items, bag checks while farming.

Part of the Bot class (engine/bot.py), which puts these mixins together; methods call each other
through self whatever part they live in.
"""

from __future__ import annotations

from typing import Any

from ... import jsonio
from ...i18n import _
from .. import bagrules
from .base import (CONFIRM, Stopped, agent_strike)
from .parts import as_bot, Parts


class BagMixin(Parts):
    agent_act: bool = False
    said_bag_full: bool = False
    bag_start: dict[str, int] | None = None
    load_seen: list[tuple[float, int, int]]

    def goto_panel(self, panel: int) -> dict[str, Any] | None:
        """Step the bag panels (0 items .. 2 fusion beasts .. 6 profession skills, wrapping) to
        panel with the left/right bag keys. Returns the game's ui state, or None if it won't go."""
        p = self.p
        u = self.ui()
        for _i in range(8):
            if not u:
                return None
            if u["panel"] == panel:
                return u
            right = (panel - u["panel"]) % 7
            self.key(p["bag_right"] if right <= 3 else p["bag_left"])
            self.sleep(0.25)
            u = self.ui()
        return u if u and u["panel"] == panel else None

    def panel_cursor(self, panel: int, index: int, size: int) -> bool:
        """The panel's cursor onto row index (of size rows), the short way round."""
        p = self.p
        u = self.goto_panel(panel)
        if u is None:
            self.log(_("couldn't get to panel {n} with the arrow keys", n=panel), speak=True)
            return False
        for _i in range(size + 3):
            cur = u["cursor"]
            if cur == index:
                return True
            down = (index - cur) % size if cur >= 0 else index + 1
            up = (cur - index) % size if cur >= 0 else size - index
            self.key(p["bag_down"] if down <= up else p["bag_up"])
            self.sleep(0.12)
            u = self.ui()
            if not u or u["panel"] != panel:
                return False
        return False

    def use_item(self, name: str) -> bool:
        """Use an item straight from the bag. From agent 40 by its uid (`use`), as the game's use
        key and hotkeys send it: no arrow keys, so nothing moves us and it works in a fight too.
        Else, or when the agent can't, the arrow keys to the bag panel and the item, then the use
        key, all checked against the game's own cursor. Returns False if it can't."""
        if self.agent_act:
            used = self._use_by_uid(name)
            if used is not None:
                return used
        if getattr(self, "in_fight", False):
            return False          # the arrow keys would walk us about in the fight: not now
        row = self.bag_cursor(name)
        if row is None:
            return False
        self.log(_("using {item} from the bag", item=row))
        self.key(self.p["use_key"])
        return True

    def _use_by_uid(self, name: str) -> bool | None:
        """`use <uid>` for the bag row of that name (an exact name first, as bag_cursor picks).
        True when used, False when the bag has none, None when the agent couldn't: the keys then."""
        got = self.a.json("mem bagfull")
        rows: list[dict[str, Any]] = got if jsonio.is_array(got) else []
        want = name.strip().lower()
        named = [r for r in rows if r.get("count") and (r.get("name") or "").strip().lower() == want]
        named = named or [r for r in rows if r.get("count") and want in (r.get("name") or "").strip().lower()]
        if not named:
            self.log(_("there's no {item} in the bag", item=name), speak=True)
            return False
        reply = self.a.send("use %d" % named[0]["uid"])
        if reply == "used":
            agent_strike(self, "use", True)
            self.log(_("using {item} from the bag", item=named[0]["name"]))
            return True
        if reply == "error: too soon":
            return False
        if not agent_strike(self, "use", False):
            return False                   # not this time (the bag changed as it was read): again soon
        self.agent_act = False             # this game's agent can't: keys for the rest of the run
        self.log(_("bag items and targets go by keys again: {why}", why=reply))
        return None

    def throw_item(self, name: str, n: int) -> int:
        """Throw n of an item away the game's way (help: "Discard item: SHIFT+DELETE"): the bag
        cursor onto it, Shift+Delete, the amount if the game asks how many, then yes to "are you
        sure". Only ever for items the player set to "throw away" in the bag manager. Returns how
        many left the bag."""
        from .. import town
        before = town.counts(as_bot(self)).get(name.lower(), 0)
        if before <= 0 or self.bag_cursor(name) is None:
            return 0
        self.log(_("throwing away {n} {item} (the bag manager says so)", n=n, item=name))
        self.key(self.p.get("discard_key", "shift+delete"))
        self.sleep(0.6)
        if town.wait_input(as_bot(self), True, 2.0):
            town.type_number(as_bot(self), n)
        for _i in range(3):
            d = self.dialog()
            if d["open"] and d.get("top") and self.find_entry(d["top"], CONFIRM) is not None:
                self.note_dialog(d, "throw away %s" % name)
                self.choose(CONFIRM)
                self.sleep(0.5)
            else:
                break
        gone = before - town.counts(as_bot(self)).get(name.lower(), 0)
        if gone <= 0:
            self.log(_("the game didn't throw {item} away - see logs\\dialogs.log", item=name), speak=True)
            return 0
        from ... import stats
        stats.moved(self, "thrown", name, gone)      # picked up, then thrown: the results say so
        return gone

    def bag_cursor(self, name: str) -> str | None:
        """Put the bag panel's cursor on the item (panel 0, category 0, the short way round).
        Returns the bag row's name, or None."""
        p = self.p

        def bag() -> list[dict[str, Any]]:
            rows = self.a.json("mem bag")
            return rows if jsonio.is_array(rows) else []

        want = name.strip().lower()
        rows = bag()
        names = [(r.get("name") or "").strip().lower() for r in rows]
        if want not in names:
            hits = [i for i, n in enumerate(names) if want in n]
            if not hits:
                self.log(_("there's no {item} in the bag", item=name), speak=True)
                return None
        u = self.ui()
        if not u:
            self.log(_("can't read the game's bag cursor - restart the game so the new agent loads"), speak=True)
            return None
        # 1. the bag is panel 0; left/right step through the 7 panels and wrap
        u = self.goto_panel(0) or u
        # 2. category 0 shows the whole bag
        for _i in range(7):
            if u["category"] == 0:
                break
            self.key(p["category_key"])
            self.sleep(0.25)
            u = self.ui()
            if not u:
                self.log(_("lost the game's bag cursor"), speak=True)
                return None
        if u["panel"] != 0 or u["category"] != 0:
            self.log(_("couldn't get to the bag with the arrow keys"), speak=True)
            return None
        # 3. walk the cursor to the item, the short way round
        for _i in range(len(rows) + 3):
            rows = bag()
            names = [(r.get("name") or "").strip().lower() for r in rows]
            targets = [i for i, n in enumerate(names) if n == want] or [i for i, n in enumerate(names) if want in n]
            if not targets:
                self.log(_("there's no {item} in the bag", item=name), speak=True)
                return None
            cur = u["cursor"]
            if cur in targets:
                break
            size = len(rows)
            best = min(targets, key=lambda t: min((t - cur) % size, (cur - t) % size))
            down_steps = (best - cur) % size if cur >= 0 else best + 1
            up_steps = (cur - best) % size if cur >= 0 else size - best
            self.key(p["bag_down"] if down_steps <= up_steps else p["bag_up"])
            self.sleep(0.12)
            u = self.ui()
            if not u:
                self.log(_("lost the game's bag cursor"), speak=True)
                return None
        else:
            self.log(_("couldn't reach {item} in the bag", item=name), speak=True)
            return None
        cur: int = u["cursor"]
        if not 0 <= cur < len(rows):
            self.log(_("the bag cursor is out of range"), speak=True)
            return None
        return rows[cur]["name"]

    def bag_rows(self) -> list[dict[str, Any]]:
        b = self.a.json("mem bag")
        return b if jsonio.is_array(b) else []

    def bag_check(self) -> bool:
        """Between fights: read the sheet now and then; end the run when the drop time is used up;
        when the bag passes bag_full_at percent of its load, first throw away what the bag manager
        says to throw (right here, no trip), and if the bag is still full do what the player chose
        - by default go somewhere safe and stop, as players do once the things they keep fill the
        bag. True when a trip to town was made."""
        sheet = self.refresh_sheet()
        if self.tally:
            self.tally.update(self)       # the bag is a memory read: costs no key press
        if not sheet:
            return False
        self.drop_time_check(sheet)
        if not sheet.get("maxload") or sheet["maxload"] <= 0 or sheet.get("load", -1) < 0:
            return False
        full = self.p.get("bag_full_at", 100)
        share = 100.0 * sheet["load"] / sheet["maxload"]
        self.load_share = share
        if share < full and not self.bag_stalled(sheet, full):
            return False
        if self.throw_by_rules():
            sheet = self.refresh_sheet(force=True) or sheet
            if sheet.get("maxload") and 100.0 * sheet.get("load", 0) / sheet["maxload"] < full:
                return False
            share = 100.0 * sheet["load"] / sheet["maxload"]
        what = self.p.get("when_bag_full", "stop")
        if what == "nothing":
            if not self.said_bag_full:
                self.said_bag_full = True
                self.log(_("the bag is {pct}% full (load {load} of {max})", pct=int(share), load=sheet["load"],
                           max=sheet["maxload"]), speak=True)
            return False
        if what == "stop":
            self.log(_("the bag is {pct}% full with things you keep - going somewhere safe and stopping",
                       pct=int(share)), speak=True)
            self.finish()
            raise Stopped(_("the bag is full"))
        from .. import town
        self.log(_("the bag is {pct}% full - going to sell and store by the bag manager's rules", pct=int(share)),
                 speak=True)
        moved = town.unload(as_bot(self))
        after: dict[str, Any] = self.refresh_sheet(force=True) or {}
        if after.get("maxload") and 100.0 * after.get("load", 0) / after["maxload"] >= full and not moved:
            self.finish()
            raise Stopped(_("the bag is still full and nothing could be sold or stored - stopping"))
        return True

    def bag_stalled(self, sheet: dict[str, Any], full: float) -> bool:
        """Full in practice: the last bit of room is smaller than what drops (the game gives no
        item weights), so the load stops rising a hair under the limit while monsters keep dying.
        True when, within 3% of the mark, the load hasn't moved over the last five minutes'
        readings although five or more kills were made. The readings must span a minute or more:
        read without the key (agent 43) they come every few seconds, and three of them 15 s apart
        say nothing about a full bag."""
        if full < 100 or 100.0 * sheet["load"] / sheet["maxload"] < full - 3:
            self.load_seen = []
            return False
        now = self.clock.time()
        seen = [r for r in self.load_seen if now - r[0] <= 300]
        if not seen or seen[-1][0] != self.last_sheet:      # a new reading, not the same one again
            seen.append((self.last_sheet, self.kills, sheet["load"]))
        self.load_seen = seen
        same = [r for r in seen if r[2] == sheet["load"]]
        return (len(seen) >= 3 and len(same) == len(seen) and self.kills - seen[0][1] >= 5
                and seen[-1][0] - seen[0][0] >= 60)

    def throw_by_rules(self) -> bool:
        """Throw away, in place, everything the bag manager's rules say to throw (keeping the
        number the player set). True if anything left the bag."""
        scan: dict[str, Any] = self.p.get("scan") or {}
        hotkeys: list[dict[str, Any]] = scan.get("hotkeys", [])
        hot: list[str] = [h["item"] for h in hotkeys if h.get("item")]
        bag: dict[str, int] = {}
        for r in self.bag_rows():
            if r.get("name") and r.get("count"):
                bag[r["name"]] = bag.get(r["name"], 0) + int(r["count"])
        start: dict[str, Any] = self.bag_start or {}
        gained = {k.lower(): v - start.get(k.lower(), 0) for k, v in bag.items()}
        thrown = 0
        for name, action, n in bagrules.plan(self.p, bag, hot, gained):
            if action == "throw":
                thrown += self.throw_item(name, n)
        return thrown > 0
