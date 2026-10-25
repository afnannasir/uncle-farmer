"""The plan editor: farming plans built from forms, no scripting needed."""

import re
from typing import Any

import wx

from .. import atlas, jsonio, travel
from ..engine import plan as plans
from ..engine.script import check as check_script
from ..i18n import _
from .common import Form, ask, buttons, enable_label, error, group, info, labeled, ok_cancel, page, pick, \
    save_file, show_text, tabs

PATROLS = ("random", "full", "quick", "short")
NOT_SEEN = _("The game isn't readable. Is it running, with a character logged in?")


def patrol_names():
    return [_("Wander at random"), _("Full patrol loop"), _("Quick patrol loop"),
            _("Short patrol loop (fights what comes near it)")]


class StepDialog(wx.Dialog):
    """One farming spot: which map, how to move, what to fight, and - with several spots - how long
    before moving on."""

    def __init__(self, parent, step, game):
        super().__init__(parent, title=_("Farming spot"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.step = dict(step)
        self.game = game
        self.all_farming = atlas.farming_maps()
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        panel, box = page(nb, _("Map"))
        self.search = labeled(panel, box, _("&Find:"), lambda p: wx.TextCtrl(p))
        self.maps = labeled(panel, box, _("&Map:"), lambda p: wx.ListBox(p, size=wx.Size(-1, 200)), 1)
        f = Form(panel)
        self.show_all = f.check(_("Include maps &without monsters"), False)
        box.Add(f.grid, 0, wx.TOP, 8)
        buttons(panel, box, (_("Use the &current map"), self.on_here))
        self.about = labeled(panel, box, _("About this map:"),
                             lambda p: wx.TextCtrl(p, style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 100)))

        panel, box = page(nb, _("Farming"))
        f = group(panel, box, _("Before moving on to the next spot (only with more than one spot)"))
        self.minutes_on, self.minutes = f.optional(_("&Time limit"), bool(step.get("minutes")),
                                                   _("Minutes:"), step.get("minutes") or 30, 1, 100000)
        self.kills_on, self.kills = f.optional(_("&Kill limit"), bool(step.get("kills")),
                                               _("Kills:"), step.get("kills") or 100, 1, 1000000)
        f = group(panel, box, _("Where"))
        self.patrol = f.choice(_("&Movement:"), patrol_names(), pick(PATROLS, step.get("patrol", "random")))
        area = step.get("area") or [0, 0, 0, 0]
        self.whole = f.check(_("Use the map's whole monster &area"), not step.get("area"))
        self.x1 = f.number(_("West edge (x):"), area[0], 0, 2000)
        self.y1 = f.number(_("North edge (y):"), area[1], 0, 2000)
        self.x2 = f.number(_("East edge (x):"), area[2], 0, 2000)
        self.y2 = f.number(_("South edge (y):"), area[3], 0, 2000)
        buttons(f.parent, f.box, (_("Area a&round me"), self.on_around))

        panel, box = page(nb, _("Monsters"))
        f = group(panel, box, _("Monsters"))
        self.only = f.text(_("&Only fight (names, separated by commas):"), names_text(step.get("only", "")))
        self.skip = f.text(_("&Never fight:"), names_text(step.get("skip", "")))
        self.avoid = f.check(_("Stay away from &elite zones"), step.get("elites") == "avoid")
        buttons(f.parent, f.box, (_("&Pick from monsters near me..."), self.on_mobs))

        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        ok_cancel(self, root)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(560, 520))

        self.search.Bind(wx.EVT_TEXT, lambda e: self.fill())
        self.show_all.Bind(wx.EVT_CHECKBOX, lambda e: self.fill())
        self.maps.Bind(wx.EVT_LISTBOX, lambda e: self.describe())
        self.whole.Bind(wx.EVT_CHECKBOX, lambda e: self.enable_area())
        # Bound on the dialog, not the button: Enter in a text box sends OK to the dialog directly,
        # skipping a handler bound on the button (that saved steps with the wrong map).
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.fill(select=step.get("map"))
        self.enable_area()
        self.maps.SetFocus()

    def shown(self):
        items = self.all_farming if not self.show_all.GetValue() else \
            [(mid, lab, "") for mid, lab in atlas.all_maps()]
        q = self.search.GetValue().strip().lower()
        return [t for t in items if not q or q in t[1].lower()]

    def fill(self, select=None):
        current = select if select is not None else self.selected()
        self.items = self.shown()
        self.maps.Set(["%s%s" % (lab, (", " + extra) if extra else "") for _m, lab, extra in self.items])
        ids = [m for m, _l, _e in self.items]
        if current in ids:
            self.maps.SetSelection(ids.index(current))
        elif self.items:
            self.maps.SetSelection(0)
        self.describe()

    def selected(self):
        i = self.maps.GetSelection() if hasattr(self, "items") else -1
        return self.items[i][0] if 0 <= i < len(self.items) else None

    def describe(self):
        mid = self.selected()
        if mid is None:
            self.about.SetValue("")
            return
        m = atlas.maps()[mid]
        lines = [_("{w} by {h} tiles, {n} exits.", w=m["w"], h=m["h"], n=len(m["exits"]))]
        what = atlas.kind_words(atlas.kind(mid))
        lines.append(_("Kind: {what}", what=what or _("farming map")))
        for z in atlas.zones(mid):
            lines.append(_("{kind} zone: x {x1} to {x2}, y {y1} to {y2}", kind=z["kind"], x1=z["rect"][0],
                           y1=z["rect"][1], x2=z["rect"][2], y2=z["rect"][3]))
        if m["spawn"].get("levels"):
            lines.append(_("Monster levels: {lv}", lv=", ".join(str(v) for v in m["spawn"]["levels"])))
        loops = [{"full": _("full"), "quick": _("quick"), "short": _("short")}[v] for v in ("full", "quick", "short")
                 if atlas.patrol_loop(mid, v)]
        lines.append(_("Patrol loops: {loops}", loops=", ".join(loops) or _("none")))
        self.about.SetValue("\n".join(lines))

    def enable_area(self):
        on = not self.whole.GetValue()
        for c in (self.x1, self.y1, self.x2, self.y2):
            c.Enable(on)
            enable_label(c, on)

    def on_here(self, _evt):
        snap = self.game()
        mid = ((snap or {}).get("map") or {}).get("id")
        if mid is None:
            error(self, NOT_SEEN)
            return
        self.search.ChangeValue("")
        if mid not in [m for m, _l, _e in self.all_farming]:
            self.show_all.SetValue(True)
        self.fill(select=mid)
        self.maps.SetFocus()

    def on_around(self, _evt):
        snap = self.game()
        me = (snap or {}).get("self") or {}
        if not me:
            error(self, NOT_SEEN)
            return
        x, y = int(me["x"]), int(me["y"])
        self.whole.SetValue(False)
        self.enable_area()
        self.x1.SetValue(max(1, x - 40))
        self.y1.SetValue(max(1, y - 40))
        self.x2.SetValue(x + 40)
        self.y2.SetValue(y + 40)
        info(self, _("The area is now 40 tiles around you."))

    def on_mobs(self, _evt):
        snap = self.game()
        if not snap:
            error(self, NOT_SEEN)
            return
        names = sorted({e["name"] for e in snap.get("entities") or []
                        if e.get("kind") == "monster" and e.get("name") and 1 < e.get("maxhp", 0) < 20000000})
        if not names:
            info(self, _("No monsters near you right now."))
            return
        with wx.MultiChoiceDialog(self, _("Tick the monsters to fight:"), _("Monsters near you"), names) as d:
            if d.ShowModal() == wx.ID_OK:
                old = names_list(self.only.GetValue())
                picked = [names[i] for i in d.GetSelections()]
                self.only.SetValue(", ".join(old + [p for p in picked if p not in old]))

    def on_ok(self, _evt):
        mid = self.selected()
        if mid is None:
            error(self, _("Choose a map."))
            return
        patrol = PATROLS[self.patrol.GetSelection()]
        if patrol != "random" and not atlas.patrol_loop(mid, patrol):
            error(self, _("This map has no patrol loop. Choose Wander at random."))
            return
        area = None
        if not self.whole.GetValue():
            area = [self.x1.GetValue(), self.y1.GetValue(), self.x2.GetValue(), self.y2.GetValue()]
            if area[0] >= area[2] or area[1] >= area[3]:
                error(self, _("The east edge must be greater than the west edge, and the south edge greater "
                              "than the north edge."))
                return
        self.step.update(map=mid, minutes=self.minutes.GetValue() if self.minutes_on.GetValue() else 0,
                         kills=self.kills.GetValue() if self.kills_on.GetValue() else 0, patrol=patrol,
                         area=area, only=names_pattern(self.only.GetValue()),
                         skip=names_pattern(self.skip.GetValue()),
                         elites="avoid" if self.avoid.GetValue() else "fight")
        self.EndModal(wx.ID_OK)


def _literal(name):
    """A monster name as a pattern that matches only itself (escapes just the special characters)."""
    return "".join("\\" + c if c in ".^$*+?{}[]\\|()" else c for c in name)


def names_list(text):
    return [n.strip() for n in text.replace("|", ",").split(",") if n.strip()]


def names_text(pattern):
    """A saved name pattern ("Wolf|Fox \\(Elite\\)") as the comma list the player edits."""
    parts = re.split(r"(?<!\\)\|", pattern or "")
    return ", ".join(re.sub(r"\\(.)", r"\1", p).strip() for p in parts if p.strip())


def names_pattern(text):
    """The comma list back as a pattern that matches each name (or part of one) literally."""
    return "|".join(_literal(n) for n in names_list(text))


def step_text(s, several=True):
    bits = [atlas.label(s["map"])]
    if several:
        if s.get("minutes"):
            bits.append(_("{n} minutes", n=s["minutes"]))
        if s.get("kills"):
            bits.append(_("{n} kills", n=s["kills"]))
        if not s.get("minutes") and not s.get("kills"):
            bits.append(_("until the plan stops"))
    bits.append({"random": _("random"), "full": _("full loop"), "quick": _("quick loop"),
                 "short": _("short loop")}.get(s.get("patrol", "random"), _("random")))
    if s.get("only"):
        bits.append(_("only {names}", names=names_text(s["only"])))
    if s.get("elites") == "avoid":
        bits.append(_("avoiding elites"))
    return ", ".join(bits)


def end_text(end):
    how = (end or {}).get("how", "start")
    if how == "place" and end.get("map"):
        cell = end.get("cell")
        return _("Ends at {map}{at}.", map=atlas.label(end["map"]), at=(" (%d,%d)" % tuple(cell)) if cell else "")
    return {"start": _("Ends back where the run started."),
            "hearthstone": _("Ends at home, with the hearthstone."),
            "safe": _("Ends on the nearest map without monsters."),
            "here": _("Ends where it is.")}.get(how, "")


def plan_text(p):
    """A plan in a few lines for the Plans tab's summary."""
    spots = p.get("spots", [])
    several = len(spots) > 1
    lines = ["%d. %s" % (i + 1, step_text(s, several)) for i, s in enumerate(spots)] or [_("No farming spots yet.")]
    if several:
        lines.append(_("Goes round the spots in turn.") if p.get("rotation", "round") == "round"
                     else _("Goes through the spots once."))
    stops = []
    if p.get("stop_after_minutes"):
        stops.append(_("after {n} minutes", n=p["stop_after_minutes"]))
    if p.get("stop_at_clock"):
        stops.append(_("at {time}", time=p["stop_at_clock"]))
    if p.get("stop_after_kills"):
        stops.append(_("after {n} kills", n=p["stop_after_kills"]))
    if p.get("stop_when_item"):
        stops.append(_("when {item} is below {n}", item=p["stop_when_item"], n=p.get("stop_when_item_below", 0)))
    if p.get("drop_keep_minutes"):
        stops.append(_("with {n} minutes of drop time saved", n=p["drop_keep_minutes"]))
    if stops:
        lines.append(_("Stops {rules}.", rules=", ".join(stops)))
    lines.append(end_text(p.get("end")))
    return "\n".join(lines)


ROTATIONS = ("round", "once")
END_CHOICES = ("start", "place", "hearthstone", "safe", "here")


class PlanDialog(wx.Dialog):
    """A plan in three questions: where to farm, when to stop, where to end."""

    def __init__(self, parent, plan, game):
        super().__init__(parent, title=_("Farming plan"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.plan = dict(plan)
        self.plan["spots"] = [dict(s) for s in plan.get("spots", [])]
        self.plan["end"] = dict(plan.get("end") or {"how": "start"})
        self.original_name = plan.get("name", "")
        self.game = game
        p = self.plan
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        # 1. where to farm
        panel, box = page(nb, _("Where to farm"))
        f = Form(panel)
        self.name = f.text(_("Plan &name:"), p["name"])
        box.Add(f.grid, 0, wx.EXPAND)
        self.steps = labeled(panel, box, _("Farming &spots:"), lambda w: wx.ListBox(w, size=wx.Size(520, 140)), 1)
        buttons(panel, box, (_("&Add..."), self.on_add), (_("&Edit..."), self.on_edit),
                (_("&Remove"), self.on_remove), (_("Move &up"), lambda e: self.move(-1)),
                (_("Move do&wn"), lambda e: self.move(1)))
        f = Form(panel)
        self.rotation = f.choice(_("With more than one spot:"),
                                 [_("Go round them in turn until the plan stops"),
                                  _("Go through them once, then end")],
                                 ROTATIONS.index(p.get("rotation", "round")) if p.get("rotation") in ROTATIONS else 0)
        box.Add(f.grid, 0, wx.TOP | wx.EXPAND, 10)
        self.one_note = wx.StaticText(panel, label="")
        box.Add(self.one_note, 0, wx.TOP, 6)

        # 2. when to stop
        panel, box = page(nb, _("When to stop"))
        f = group(panel, box, _("Stop the plan"))
        self.limit_min_on, self.limit_min = f.optional(_("After a &time limit"), bool(p["stop_after_minutes"]),
                                                       _("Minutes:"), p["stop_after_minutes"] or 60, 1, 100000)
        self.clock_on = f.check(_("At a &clock time"), bool(p["stop_at_clock"]))
        self.limit_clock = f.text(_("Time (HH:MM):"), p["stop_at_clock"])
        self.limit_kills_on, self.limit_kills = f.optional(_("After a &kill count"), bool(p["stop_after_kills"]),
                                                           _("Kills:"), p["stop_after_kills"] or 500, 1, 10000000)
        self.item_on = f.check(_("When an &item runs low"), bool(p["stop_when_item"]))
        self.item = f.add(_("Item:"), lambda w: wx.ComboBox(w, value=p["stop_when_item"], choices=self.bag_items()))
        self.item_below = f.number(_("Fewer than:"), p["stop_when_item_below"] or 10, 1, 1000000)
        keep = p.get("drop_keep_minutes", 0)
        self.keep_drop_on, self.keep_drop = f.optional(
            _("&Save some of the day's drop time (instead of the character's setting)"), bool(keep),
            _("Drop time to save (minutes):"), keep or 60, 1, 1440)
        for check, ctrls in ((self.clock_on, (self.limit_clock,)), (self.item_on, (self.item, self.item_below))):
            check.Bind(wx.EVT_CHECKBOX, lambda e, c=check, w=ctrls: self.sync(c, w))
            self.sync(check, ctrls)
        labeled(panel, box, _("It also stops by itself:"),
                lambda w: wx.TextCtrl(w, value=_(
                    "when the day's drop time is used up; when the bag is full of things you keep; after "
                    "the last death you allow; when broken gear can't be repaired. Those are set per "
                    "character, in Character, Settings and Bag and supplies."),
                    style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 70)))

        # 3. where to end
        panel, box = page(nb, _("Where to end"))
        f = group(panel, box, _("Every end goes here - time up, drop time, a full bag, the last death"))
        end = p["end"]
        self.end_how = f.choice(_("&End:"), [_("Back where the run started"), _("At a place I choose"),
                                             _("Home, with the hearthstone"), _("On the nearest map without monsters"),
                                             _("Where it is")],
                                END_CHOICES.index(end.get("how")) if end.get("how") in END_CHOICES else 0)
        self.all_maps = atlas.all_maps()
        ids = [m for m, _l in self.all_maps]
        self.end_map = f.choice(_("&Map:"), [lab for _m, lab in self.all_maps],
                                ids.index(end["map"]) if end.get("map") in ids else 0)
        cell = end.get("cell") or [0, 0]
        self.end_x = f.number(_("X:"), cell[0], 0, 2000)
        self.end_y = f.number(_("Y:"), cell[1], 0, 2000)
        self.end_buttons = buttons(f.parent, f.box, (_("Use where I &stand now"), self.on_end_here),
                                   (_("A city s&quare..."), self.on_end_square))
        self.end_note = wx.StaticText(panel, label="")
        box.Add(self.end_note, 0, wx.TOP, 8)
        self.end_how.Bind(wx.EVT_CHOICE, lambda e: self.sync_end())

        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        view = wx.Button(self, label=_("&View as script..."))
        view.Bind(wx.EVT_BUTTON, self.on_view)
        export = wx.Button(self, label=_("Save as scri&pt..."))
        export.Bind(wx.EVT_BUTTON, self.on_export)
        row.Add(view, 0, wx.RIGHT, 8)
        row.Add(export)
        root.Add(row, 0, wx.LEFT | wx.TOP, 12)
        ok_cancel(self, root, _("Save"))
        self.SetSizerAndFit(root)
        self.steps.Bind(wx.EVT_LISTBOX_DCLICK, self.on_edit)
        self.steps.Bind(wx.EVT_KEY_DOWN, self.on_steps_key)
        # Bound on the dialog, not the button: Enter in a text box sends OK to the dialog directly,
        # skipping a handler bound on the button (that saved steps with the wrong map).
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.refresh()
        self.sync_end()
        self.name.SetFocus()

    @staticmethod
    def sync(check, ctrls):
        for c in ctrls:
            c.Enable(check.GetValue())
            enable_label(c, check.GetValue())

    def sync_end(self):
        how = END_CHOICES[max(0, self.end_how.GetSelection())]
        on = how == "place"
        for c in (self.end_map, self.end_x, self.end_y):
            c.Enable(on)
            enable_label(c, on)
        for b in self.end_buttons:
            b.Enable(on)
        self.end_note.SetLabel({
            "start": _("Walks (and rides cable cars and airships) back to where you started the bot. If "
                       "monsters spawn there, it ends on the nearest map without them instead."),
            "place": _("Walks and rides to that map, then to the spot (0,0 = anywhere on the map)."),
            "hearthstone": _("Uses the hearthstone; if it can't, ends on the nearest map without monsters."),
            "safe": _("The nearest map where no monsters spawn."),
            "here": _("Stays where it is when it stops (monsters may still attack)."),
        }[how])
        self.end_note.Wrap(520)

    def on_end_here(self, _evt):
        snap = self.game()
        me = (snap or {}).get("self") or {}
        mid = ((snap or {}).get("map") or {}).get("id")
        if not me or mid is None:
            error(self, NOT_SEEN)
            return
        ids = [m for m, _l in self.all_maps]
        if mid not in ids:
            error(self, _("This map can't be an end place. Choose one on the open world, or another end."))
            return
        self.end_map.SetSelection(ids.index(mid))
        self.end_x.SetValue(int(me["x"]))
        self.end_y.SetValue(int(me["y"]))
        info(self, _("The end place is now where you stand: {map} ({x},{y}).", map=atlas.label(mid),
                     x=int(me["x"]), y=int(me["y"])))

    def on_end_square(self, _evt):
        names = sorted(travel.SQUARES)
        with wx.SingleChoiceDialog(self, _("Choose a city square:"), _("City squares"),
                                   [n[:1].upper() + n[1:] for n in names]) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            mid, x, y = travel.SQUARES[names[d.GetSelection()]]
        ids = [m for m, _l in self.all_maps]
        if mid in ids:
            self.end_map.SetSelection(ids.index(mid))
        self.end_x.SetValue(x)
        self.end_y.SetValue(y)

    def on_steps_key(self, evt):
        if evt.GetKeyCode() == wx.WXK_DELETE:
            self.on_remove(None)
        else:
            evt.Skip()

    def bag_items(self):
        snap = self.game()
        return sorted({r["name"] for r in (snap or {}).get("bag", []) if r.get("name")})

    def refresh(self, select=None):
        spots = self.plan["spots"]
        several = len(spots) > 1
        self.steps.Set(["%d. %s" % (i + 1, step_text(s, several)) for i, s in enumerate(spots)])
        if spots:
            self.steps.SetSelection(select if select is not None else 0)
        self.rotation.Enable(several)
        enable_label(self.rotation, several)
        self.one_note.SetLabel(_("With one spot, the bot farms there until the plan stops (When to stop).")
                               if len(spots) == 1 else "")

    def on_add(self, _evt):
        snap = self.game()
        mid = ((snap or {}).get("map") or {}).get("id")
        default = plans.new_step(mid if mid is not None and atlas.zones(mid) else None)
        with StepDialog(self, default, self.game) as d:
            if d.ShowModal() == wx.ID_OK:
                self.plan["spots"].append(d.step)
                self.refresh(len(self.plan["spots"]) - 1)
        self.steps.SetFocus()

    def on_edit(self, _evt):
        i = self.steps.GetSelection()
        if i < 0:
            return
        with StepDialog(self, self.plan["spots"][i], self.game) as d:
            if d.ShowModal() == wx.ID_OK:
                self.plan["spots"][i] = d.step
                self.refresh(i)
        self.steps.SetFocus()

    def on_remove(self, _evt):
        i = self.steps.GetSelection()
        if i >= 0:
            del self.plan["spots"][i]
            self.refresh(min(i, len(self.plan["spots"]) - 1) if self.plan["spots"] else None)
            self.steps.SetFocus()

    def move(self, d):
        i = self.steps.GetSelection()
        j = i + d
        if i < 0 or not 0 <= j < len(self.plan["spots"]):
            return
        s = self.plan["spots"]
        s[i], s[j] = s[j], s[i]
        self.refresh(j)
        self.steps.SetFocus()

    def collect(self):
        clock = self.limit_clock.GetValue().strip() if self.clock_on.GetValue() else ""
        if self.clock_on.GetValue() and not re.match(r"^([01]\d|2[0-3]):[0-5]\d$", clock):
            error(self, _("Enter the time as HH:MM, for example 22:30."))
            return None
        item = self.item.GetValue().strip() if self.item_on.GetValue() else ""
        if self.item_on.GetValue() and not item:
            error(self, _("Enter the item to watch."))
            return None
        how = END_CHOICES[max(0, self.end_how.GetSelection())]
        end: dict[str, Any] = {"how": how}
        if how == "place":
            end["map"] = self.all_maps[max(0, self.end_map.GetSelection())][0]
            x, y = self.end_x.GetValue(), self.end_y.GetValue()
            end["cell"] = [x, y] if (x or y) else None
        p = self.plan
        p.update(name=self.name.GetValue().strip(), rotation=ROTATIONS[max(0, self.rotation.GetSelection())],
                 stop_after_minutes=self.limit_min.GetValue() if self.limit_min_on.GetValue() else 0,
                 stop_at_clock=clock,
                 stop_after_kills=self.limit_kills.GetValue() if self.limit_kills_on.GetValue() else 0,
                 stop_when_item=item, stop_when_item_below=self.item_below.GetValue() if item else 0,
                 drop_keep_minutes=self.keep_drop.GetValue() if self.keep_drop_on.GetValue() else 0,
                 end=end)
        return p

    def on_view(self, _evt):
        p = self.collect()
        if p:
            show_text(self, _("The plan as a script"), plans.to_script(p))

    def on_export(self, _evt):
        p = self.collect()
        if not p:
            return
        with wx.TextEntryDialog(self, _("Script name:"), _("Save as script"), p["name"]) as d:
            if d.ShowModal() != wx.ID_OK or not d.GetValue().strip():
                return
            path = plans.script_path(d.GetValue().strip())
        if save_file(self, jsonio.save_text, path, plans.to_script(p)):
            info(self, _("Saved. It's in the Plans tab as a script."))

    def on_ok(self, _evt):
        p = self.collect()
        if not p:
            return
        if not p["name"]:
            error(self, _("Enter a name for the plan."))
            return
        if not p["spots"]:
            error(self, _("Add at least one farming spot."))
            return
        problem = check_script(plans.to_script(p))
        if problem:
            error(self, problem)
            return
        clash = [n for n in plans.names() if n.lower() == p["name"].lower()]
        if p["name"] != self.original_name and clash and \
                not plans.same_file(plans.path(self.original_name or ""), plans.path(p["name"])):
            if not ask(self, _("A plan named {name} already exists. Replace it?", name=p["name"])):
                return
        if not save_file(self, plans.save, p):
            return
        if self.original_name and self.original_name != p["name"] and \
                not plans.same_file(plans.path(self.original_name), plans.path(p["name"])) and \
                self.original_name in plans.names():
            try:
                plans.delete(self.original_name)
            except OSError as e:
                error(self, _("The plan was saved, but the old file couldn't be removed: {err}", err=e))
        self.EndModal(wx.ID_OK)
