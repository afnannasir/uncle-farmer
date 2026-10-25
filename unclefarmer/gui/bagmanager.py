"""Bag and supplies: what happens to every shard and item, how supplies are refilled, and what a
full bag means.

Shards are sorted into four lists - throw away, keep, store, sell - by the player, whatever their
price (prices change; the player knows them). Other items into the same four.
A shard or item the player never sorted stays in Keep. Native controls only, each with its label
created before it, for screen readers.
"""

import wx

from .. import settings, speech, stats
from ..engine import bagrules
from ..engine.town import shard_catalog
from ..i18n import _
from .common import Form, bind, buttons, enable_label, group, labeled, ok_cancel, page, pick, save_file, tabs

SOURCE_LABELS = (("both", "Storehouse, then the shop"), ("storage", "Storehouse only"),
                 ("buy", "Shop only"), ("tell", "Don't refill, just tell me"))
FULL_LABELS = (("stop", "Go somewhere safe and stop"),
               ("unload", "Sell and store by these lists, then come back"),
               ("nothing", "Keep farming, just tell me"))


class SortLists:
    """Side-by-side lists, one per action; the player moves things between them.

    Moving: the "Move to" buttons, or the context menu (Applications key) inside a list. The
    focused list's selection moves to the next item, so a player can sort a list top to bottom
    without leaving it.
    """

    def __init__(self, parent, sizer, columns, names, action_of, describe, on_move, label=None, on_select=None):
        """columns: [(action, list label, button label)]. action_of(name) -> action now.
        describe(name) -> the details line. on_move(name, action) records the move. label(name) ->
        the list's text for it. on_select() is called when the selection or focused list changes."""
        self.columns = columns
        self.label = label or (lambda n: n)
        self.on_select = on_select
        self.names = names
        self.action_of = action_of
        self.describe = describe
        self.on_move = on_move
        self.lists = {}
        self.current = columns[0][0]
        row = wx.BoxSizer(wx.HORIZONTAL)
        for action, label, _button in columns:
            col = wx.BoxSizer(wx.VERTICAL)
            lb = labeled(parent, col, label, lambda w: wx.ListBox(w), 1)
            lb.SetMinSize(wx.Size(150, 230))       # equal columns, whatever their contents
            lb.Bind(wx.EVT_SET_FOCUS, lambda e, a=action: self.focused(e, a))
            lb.Bind(wx.EVT_LISTBOX, lambda e, a=action: self.show(a))
            lb.Bind(wx.EVT_CONTEXT_MENU, lambda e, a=action: self.menu(a))
            self.lists[action] = lb
            row.Add(col, 1, wx.EXPAND | wx.RIGHT, 10)
        sizer.Add(row, 1, wx.EXPAND)
        self.details = labeled(parent, sizer, _("Details:"),
                               lambda w: wx.TextCtrl(w, style=wx.TE_READONLY | wx.TE_MULTILINE, size=wx.Size(-1, 48)))
        self.buttons = buttons(parent, sizer, *[(b, lambda e, a=a: self.move(a)) for a, _l, b in columns])
        self.fill()

    def focused(self, evt, action):
        self.current = action
        self.show(action)
        evt.Skip()

    def members(self, action):
        return [n for n in self.names if self.action_of(n) == action]

    def fill(self, keep=None):
        for action, lb in self.lists.items():
            names = self.members(action)
            lb.Set([self.label(n) for n in names] or [])
            lb.names = names
            if names:
                want = keep.get(action, 0) if keep else 0
                lb.SetSelection(max(0, min(want, len(names) - 1)))
        self.show(self.current)

    def selected(self, action=None):
        lb = self.lists[action or self.current]
        i = lb.GetSelection()
        return lb.names[i] if 0 <= i < len(lb.names) else None

    def show(self, action):
        self.current = action
        name = self.selected(action)
        self.details.ChangeValue(self.describe(name) if name else "")
        for (a, _l, _b), b in zip(self.columns, self.buttons):
            b.Enable(bool(name) and a != action)
        if self.on_select:
            self.on_select()

    def menu(self, action):
        self.current = action
        if not self.selected(action):
            return
        m = wx.Menu()
        for a, _l, b in self.columns:
            if a != action:
                item = m.Append(wx.ID_ANY, b)
                m.Bind(wx.EVT_MENU, lambda e, a=a: self.move(a), item)
        self.lists[action].PopupMenu(m)
        m.Destroy()

    def move(self, to):
        src = self.current
        name = self.selected(src)
        if not name or to == src:
            return
        pos = {a: lb.GetSelection() for a, lb in self.lists.items()}
        if self.on_move(name, to) is False:
            return
        self.fill(keep=pos)
        lb = self.lists[src]
        lb.SetFocus()
        title = next(l for a, l, _b in self.columns if a == to).replace("&", "").rstrip(":")
        speech.speak(_("{name} moved to {list}", name=name, list=title), interrupt=True)


class BagManagerDialog(wx.Dialog):
    def __init__(self, parent, character, profile=None):
        super().__init__(parent, title=_("Bag and supplies - {name}", name=character),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER, size=wx.Size(820, 600))
        self.character = character
        self.p = profile if profile is not None else settings.load_profile(character)
        p = self.p
        scan = p.get("scan") or {}
        self.hot = {h["item"].lower() for h in scan.get("hotkeys", []) if h.get("item")}
        self.counts = {}
        for b in scan.get("bag", []):
            self.counts[b["name"]] = self.counts.get(b["name"], 0) + int(b.get("count") or 0)
        self.rules = {k: dict(v) for k, v in (p.get("bag_rules") or {}).items()}
        self.shard_info, _m = shard_catalog()
        names = {n.lower(): n for n in list(self.counts) + list(self.shard_info)}
        for k in self.rules:
            names.setdefault(k, k)
        everything = sorted(names.values(), key=str.lower)
        self.shards = [n for n in everything if stats.is_shard(n)]
        self.others = [n for n in everything if not stats.is_shard(n)
                       and (n in self.counts or n.lower() in self.rules)]
        self.supplies = [dict(r) for r in (p.get("restock") or [])]
        known = {r["item"].lower() for r in self.supplies}
        for r in bagrules.suggest_supplies(scan):
            if r["item"].lower() not in known:
                r["on"] = False          # offered, not refilled until the player ticks it
                self.supplies.append(r)

        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)
        self.shards_page(nb)
        self.items_page(nb)
        self.supplies_page(nb)
        self.full_page(nb)
        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        ok_cancel(self, root, _("Save"))
        self.SetSizer(root)
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.show_supplies(0)

    # ------------------------------------------------------------ rules
    def action_of(self, name):
        r = self.rules.get(name.lower())
        if r:
            return r.get("action", "keep")
        return bagrules.suggest(name, name.lower() in self.hot)[0]

    def set_action(self, name, action):
        old = self.rules.get(name.lower(), {})
        self.rules[name.lower()] = {"action": action, "keep": int(old.get("keep", 0))}

    def in_bag(self, name):
        n = self.counts.get(name)
        return _("{n} in the bag", n=n) if n else _("None in the bag")

    def describe(self, name):
        bits = [self.in_bag(name)]
        price = (self.shard_info.get(name) or {}).get("price")
        if price:
            bits.append(_("Shard Market price when last seen: {price} arnebia", price=price))
        facts = bagrules.about(name, self.counts.get(name), name.lower() in self.hot)
        if facts != "nothing known yet" and not price:
            bits.append(facts)
        if self.action_of(name) == "sell" and not bagrules.can_sell(name):
            bits.append(_("No known buyer, so it will be stored instead"))
        when = (FULL_LABELS[max(0, self.when_full.GetSelection())][0] if hasattr(self, "when_full")
                else self.p.get("when_bag_full", "stop"))      # the Full bag page is built after the lists
        if self.action_of(name) in ("store", "sell") and when != "unload":
            bits.append(_("Stored or sold on a bag trip: on the Full bag page, choose to sell and store "
                          "when the bag is full"))
        return ". ".join(bits) + "."

    # ------------------------------------------------------------ shards
    def shards_page(self, nb):
        panel, box = page(nb, _("Shards"))
        self.shard_lists = SortLists(panel, box, [
            ("throw", _("&Throw away:"), _("Move to throw a&way")),
            ("keep", _("&Keep:"), _("Move to kee&p")),
            ("store", _("St&ore:"), _("Move to sto&rehouse")),
            ("sell", _("&Sell:"), _("Move to se&ll")),
        ], self.shards, self.action_of, self.describe, self.set_action, self.short_label)

    def short_label(self, name):
        n = self.counts.get(name)
        return _("{name} ({n})", name=name, n=n) if n else name

    # ------------------------------------------------------------ other items
    def items_page(self, nb):
        panel, box = page(nb, _("Other items"))
        self.item_lists = SortLists(panel, box, [
            ("keep", _("&Keep:"), _("Move to kee&p")),
            ("store", _("S&tore:"), _("Move to st&orehouse")),
            ("sell", _("&Sell:"), _("Move to se&ll")),
            ("throw", _("Throw awa&y:"), _("Move to thro&w away")),
        ], self.others, self.action_of, self.describe, self.set_action, self.short_label,
            lambda: self.show_leave())
        f = Form(panel)
        self.leave = f.number(_("Always leave this many in the &bag:"), 0, 0, 1000000)
        box.Add(f.grid, 0, wx.TOP, 10)
        self.leave.Bind(wx.EVT_SPINCTRL, lambda e: self.set_leave())
        buttons(panel, box, (_("&Add an item..."), lambda e: self.add_item()))
        self.show_leave()

    def show_leave(self):
        if not hasattr(self, "leave"):
            return              # the lists fill before the field exists
        name = self.item_lists.selected()
        on = bool(name) and self.action_of(name) != "keep"
        self.leave.Enable(on)
        enable_label(self.leave, on)
        self.leave.SetValue(int((self.rules.get(name.lower()) or {}).get("keep", 0)) if name else 0)

    def set_leave(self):
        name = self.item_lists.selected()
        if name and self.action_of(name) != "keep":
            self.set_action(name, self.action_of(name))
            self.rules[name.lower()]["keep"] = self.leave.GetValue()

    def add_item(self):
        with wx.TextEntryDialog(self, _("Item name, exactly as the game writes it:"), _("Add an item")) as d:
            if d.ShowModal() != wx.ID_OK or not d.GetValue().strip():
                return
            name = d.GetValue().strip()
        if name.lower() not in {n.lower() for n in self.others + self.shards}:
            if stats.is_shard(name):
                self.shards.append(name)
                self.shards.sort(key=str.lower)
            else:
                self.others.append(name)
                self.others.sort(key=str.lower)
        self.shard_lists.fill()
        self.item_lists.fill()

    # ------------------------------------------------------------ supplies
    def supplies_page(self, nb):
        panel, box = page(nb, _("Supplies"))
        self.supply_list = labeled(panel, box, _("&Supplies (ticked ones are refilled):"),
                                   lambda w: wx.CheckListBox(w, size=wx.Size(-1, 170)), 1)
        self.supply_list.Bind(wx.EVT_LISTBOX, lambda e: self.on_supply())
        self.supply_list.Bind(wx.EVT_CHECKLISTBOX, lambda e: self.set_supply(checked=e.GetInt()))
        f = Form(panel)
        self.low = f.number(_("Refill when fewer &than:"), 30, 0, 100000)
        self.fill = f.number(_("Fill &up to:"), 200, 0, 100000)
        self.source = f.choice(_("&Get more from:"), [_(l) for _s, l in SOURCE_LABELS])
        self.supply_about = f.readonly(_("Shop:"))
        box.Add(f.grid, 0, wx.EXPAND | wx.TOP, 10)
        for c, ev in ((self.low, wx.EVT_SPINCTRL), (self.fill, wx.EVT_SPINCTRL), (self.source, wx.EVT_CHOICE)):
            bind(c, ev, lambda e: self.set_supply())
        buttons(panel, box, (_("&Add a supply..."), lambda e: self.add_supply()),
                (_("&Remove"), lambda e: self.remove_supply()))

    def supply_label(self, r):
        src = dict(SOURCE_LABELS).get(bagrules.supply_rule(r), SOURCE_LABELS[0][1])
        return _("{item}: below {low}, fill to {fill}, {src}", item=r["item"], low=r.get("min", 0),
                 fill=r.get("stock", 0), src=_(src).lower())

    def show_supplies(self, select):
        self.supply_list.Set([self.supply_label(r) for r in self.supplies])
        for i, r in enumerate(self.supplies):
            self.supply_list.Check(i, bool(r.get("on", True)))
        if self.supplies:
            self.supply_list.SetSelection(max(0, min(select, len(self.supplies) - 1)))
        self.on_supply()

    def on_supply(self):
        i = self.supply_list.GetSelection()
        on = 0 <= i < len(self.supplies)
        for c in (self.low, self.fill, self.source):
            c.Enable(on)
        if not on:
            self.supply_about.ChangeValue("")
            return
        r = self.supplies[i]
        self.low.SetValue(int(r.get("min", 0)))
        self.fill.SetValue(int(r.get("stock", 0)))
        self.source.SetSelection(pick([s for s, _l in SOURCE_LABELS], bagrules.supply_rule(r)))
        sl = bagrules.seller(r["item"])
        self.supply_about.ChangeValue(_("{npc}, {price} {cur} each", npc=sl[0], price=sl[1], cur=sl[2])
                                      if sl else _("No shop seen selling it yet"))

    def set_supply(self, checked=None):
        if checked is not None:
            if 0 <= checked < len(self.supplies):
                self.supplies[checked]["on"] = self.supply_list.IsChecked(checked)
            return
        i = self.supply_list.GetSelection()
        if not 0 <= i < len(self.supplies):
            return
        r = self.supplies[i]
        src = SOURCE_LABELS[max(0, self.source.GetSelection())][0]
        r.update({"min": self.low.GetValue(), "stock": self.fill.GetValue(), "source": src,
                  "storage_first": src in ("both", "storage"), "buy": src in ("both", "buy")})
        self.supply_list.SetString(i, self.supply_label(r))

    def add_supply(self):
        with wx.TextEntryDialog(self, _("Item name, exactly as the game writes it:"), _("Add a supply")) as d:
            if d.ShowModal() != wx.ID_OK or not d.GetValue().strip():
                return
            name = d.GetValue().strip()
        self.supplies.append({"item": name, "min": 30, "stock": 200, "source": "tell", "on": True})
        self.show_supplies(len(self.supplies) - 1)
        self.supply_list.SetFocus()

    def remove_supply(self):
        i = self.supply_list.GetSelection()
        if 0 <= i < len(self.supplies):
            del self.supplies[i]
            self.show_supplies(i)
            self.supply_list.SetFocus()

    # ------------------------------------------------------------ full bag
    def full_page(self, nb):
        panel, box = page(nb, _("Full bag"))
        p = self.p
        f = group(panel, box, _("Full bag"))
        self.full_at = f.number(_("The bag is full at (% of its load):"), p.get("bag_full_at", 100), 30, 100)
        acts = [a for a, _l in FULL_LABELS]
        self.when_full = f.choice(_("&When the bag is full:"), [_(l) for _a, l in FULL_LABELS],
                                  pick(acts, p.get("when_bag_full", "stop")))
        f = group(panel, box, _("Drop time"))
        self.drop_stop = f.check(_("Stop when the day's &drop time runs out"), p.get("stop_on_drop_time", True))
        self.drop_keep = f.number(_("Drop time to &save (minutes):"), p.get("drop_keep_minutes", 0), 0, 1440)
        self.drop_stop.Bind(wx.EVT_CHECKBOX, lambda e: self.drop_keep.Enable(self.drop_stop.GetValue()))
        self.drop_keep.Enable(self.drop_stop.GetValue())

    # ------------------------------------------------------------ save
    def on_ok(self, _evt):
        self.set_leave()          # typed (not clicked) spin values are read from the controls
        self.set_supply()
        p = self.p
        # a rule that only says "keep" with nothing to leave is the default: don't store it
        p["bag_rules"] = {k: v for k, v in self.rules.items()
                          if v.get("action") != "keep" or bagrules.suggest(k, k in self.hot)[0] != "keep"}
        p["restock"] = [r for r in self.supplies if r.get("on", True) or r.get("source") != "tell"]
        p["bag_full_at"] = self.full_at.GetValue()
        p["when_bag_full"] = FULL_LABELS[max(0, self.when_full.GetSelection())][0]
        p["stop_on_drop_time"] = self.drop_stop.GetValue()
        p["drop_keep_minutes"] = self.drop_keep.GetValue()
        if save_file(self, settings.save_profile, self.character, p):
            self.EndModal(wx.ID_OK)
