"""Tools, Gathering: fishing, mining or collecting herbs for one character (engine/gathering.py).

Saved in the character's profile. The character's own gathering skill decides what it does; the
window says which skill and rank the last scan found. Places: gangs' production areas, each once a
game day in the player's order (our own gang's, other gangs' by name), and outside places - the
player's own, added where they stand in the game, and the ones the bot knows. Check only reads the gang area's entrance and says where it would gather; Start
gathers, fights what attacks, and sells the collection.
"""

import wx

from .. import atlas, settings
from ..engine import gathering
from ..i18n import _
from .common import Form, buttons, error, group, info, labeled, page, save_file, tabs
from .crafting import CHECK, START
from .plan import NOT_SEEN

WHEN_FULL = ("sell", "stop")


def skill_line(p):
    """The character's gathering skill as the last scan saw it, for the window's first line."""
    rows = [r for r in (p.get("scan") or {}).get("profession", []) if r.get("name") in gathering.SKILLS]
    if not rows:
        return None, _("The last scan found no gathering skill. Learn Fishing, Mining or Collect Herbs "
                       "(one of them) from the gang's Guru Elder or Master Pepper, then scan again.")
    r = rows[0]
    return r["name"], _("Gathering skill: {skill}, {rank}.", skill=_(r["name"]), rank=gathering.rank_name(r.get("rank")))


class GatheringDialog(wx.Dialog):
    def __init__(self, parent, character, game):
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Gathering"), name=character),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.game = game
        self.p = settings.load_profile(character)
        c = self.cfg = gathering.config(self.p)
        self.own = [dict(x) for x in c["places"]]
        self.skill, line = skill_line(self.p)
        root = wx.BoxSizer(wx.VERTICAL)
        nb = tabs(self)

        panel, box = page(nb, _("Gangs"))
        box.Add(wx.StaticText(panel, label=line), 0, wx.BOTTOM, 6)
        f = group(panel, box, _("Gangs' production areas (a Lake, Mining Area or Herbal Garden)"), 1)
        self.gang = f.check(_("Gather in &gangs' areas first, each once a game day (30 minutes inside)"), c["gang"])
        self.gangs = list(gathering.gangs(c))
        self.gang_list = labeled(f.parent, f.box, _("Gangs, in &order (the bot goes to each in turn):"),
                                 lambda w: wx.ListBox(w, size=wx.Size(-1, 110)), 1)
        buttons(f.parent, f.box, (_("Add our &own gang"), self.on_add_own), (_("Add a gang by &name..."), self.on_add_gang),
                (_("Move &up"), lambda e: self.move_gang(-1)), (_("Move &down"), lambda e: self.move_gang(1)),
                (_("&Remove"), self.on_remove_gang))
        f = group(panel, box, _("Paying to go in"))
        self.fee = f.number(_("Pay at most this &entry fee (arnebia):"), c["gang_fee_max"], 0, 1000000000)
        self.keep_arnebia = f.number(_("Never spend arnebia belo&w:"), self.p.get("gem_keep_arnebia", 20000),
                                     0, 1000000000)
        self.fill_gangs(0)

        panel, box = page(nb, _("Outside"))
        f = group(panel, box, _("Outside"), 1)
        self.outside = f.check(_("Gather &outside (after the gangs' areas, or without them)"), c["outside"])
        self.known = f.check(_("Use the places the bot &knows from the game, besides your own"), c["known_places"])
        self.known.Bind(wx.EVT_CHECKBOX, lambda e: self.fill_places())
        self.above = f.number(_("Only areas whose monsters are at most this many levels &above you:"),
                              c["above"], 0, 50)
        self.where = f.choice(_("&Place:"), [], 0)
        self.list = labeled(f.parent, f.box, _("&Your places:"),
                            lambda w: wx.ListBox(w, size=wx.Size(-1, 110)), 1)
        buttons(f.parent, f.box, (_("&Add where I stand"), self.on_add), (_("Re&name..."), self.on_rename),
                (_("Re&move"), self.on_remove))
        self.fill_places(c["place"])

        panel, box = page(nb, _("Selling"))
        f = group(panel, box, _("The collection"))
        self.sell = f.check(_("&Sell what is gathered to the Aquaculture, Ore or Herb Buyer"), c["sell"])
        self.full = f.choice(_("When the bag is &full outside:"),
                             [_("Sell it and come back"), _("Stop")], WHEN_FULL.index(c["when_full"])
                             if c["when_full"] in WHEN_FULL else 0)
        self.free = f.number(_("Before setting out, the bag is full with less than this much load &left (900 to 1000 is safe):"),
                             c["free_load"], 0, 100000)
        box.Add(wx.StaticText(panel, label=_(
            "Everything the buyer lists is sold, except what you keep in Character, Bag and supplies "
            "(an item set to Keep, or the number you keep of it). Before the gang's area the bot "
            "sells what it carries, since that half hour can't be left and taken up again.")), 0, wx.TOP, 10)

        panel, box = page(nb, _("When to stop"))
        f = Form(panel)
        self.minutes = f.number(_("Gather for this many &minutes (0: until Vitality runs out):"), c["minutes"], 0, 1440)
        self.vitality = f.number(_("Outside, stop when &Vitality is down to (the character sheet's; 0: until it runs out):"),
                                 int(c["stop_vitality"]), 0, 100)
        box.Add(f.grid, 0, wx.EXPAND)
        box.Add(wx.StaticText(panel, label=_(
            "Gathering also stops when Vitality runs out for the day; more Vitality costs diamonds, "
            "which the bot never spends.")), 0, wx.TOP, 10)

        root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        row = wx.BoxSizer(wx.HORIZONTAL)
        for label, code in ((_("Save"), wx.ID_OK), (_("&Check only"), CHECK), (_("Sta&rt"), START)):
            btn = wx.Button(self, label=label)
            btn.Bind(wx.EVT_BUTTON, lambda e, c=code: self.done(c))
            row.Add(btn, 0, wx.RIGHT, 8)
        row.Add(wx.Button(self, wx.ID_CANCEL, _("Cancel")), 0)
        root.Add(row, 0, wx.ALL | wx.ALIGN_RIGHT, 12)
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(640, 560))

    # ------------------------------------------------------------ gangs
    def fill_gangs(self, select=None):
        self.gang_list.Set([g or _("Our own gang") for g in self.gangs])
        if self.gangs and select is not None:
            self.gang_list.SetSelection(max(0, min(select, len(self.gangs) - 1)))

    def _add_gang(self, gang):
        if gang.lower() in [g.lower() for g in self.gangs]:
            info(self, _("{gang} is in the list already.", gang=gathering.gang_name(gang)))
            return
        self.gangs.append(gang)
        self.fill_gangs(len(self.gangs) - 1)

    def on_add_own(self, _evt):
        self._add_gang("")

    def on_add_gang(self, _evt):
        with wx.TextEntryDialog(self, _("The gang's name, as the Gang Entrance's list shows it:"),
                                _("Add a gang")) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            name = " ".join(d.GetValue().split())
        if name:
            self._add_gang(name)

    def move_gang(self, step):
        i = self.gang_list.GetSelection()
        j = i + step
        if i < 0 or not 0 <= j < len(self.gangs):
            return
        self.gangs[i], self.gangs[j] = self.gangs[j], self.gangs[i]
        self.fill_gangs(j)

    def on_remove_gang(self, _evt):
        i = self.gang_list.GetSelection()
        if i < 0:
            return
        del self.gangs[i]
        self.fill_gangs(i)

    # ------------------------------------------------------------ places
    def fill_places(self, chosen=None):
        """The place choice (the best one, or one of every place for the skill) and the own list."""
        if chosen is None:
            i = self.where.GetSelection()
            chosen = self.choices[i] if 0 < i < len(self.choices) else ""
        cfg = dict(self.cfg, places=self.own, known_places=self.known.GetValue())
        found = gathering.places(cfg, self.skill) if self.skill else []
        self.choices = [""] + [p["label"] for p in found]
        names = [_("The best for this character")] + [
            "%s - %s" % (p["label"], _("area level {n}", n=p["level"]) if p.get("level") else _("level unknown"))
            for p in found]
        self.where.Set(names)
        self.where.SetSelection(self.choices.index(chosen) if chosen in self.choices else 0)
        self.list.Set(["%s - %s" % (gathering.label(p), _(p["skill"])) for p in self.own])

    def on_add(self, _evt):
        snap = self.game()
        me = (snap or {}).get("self") or {}
        mid = ((snap or {}).get("map") or {}).get("id")
        if not me or mid is None:
            error(self, NOT_SEEN)
            return
        if int(mid) in gathering.GANG_AREAS:
            error(self, _("The gang's area is used on its own, once a day: no need to add it."))
            return
        if not self.skill:
            error(self, _("This character has no gathering skill to add a place for."))
            return
        p = {"skill": self.skill, "map": int(mid), "x": int(me["x"]), "y": int(me["y"])}
        if any(gathering.place_key(p) == gathering.place_key(q) for q in self.own):
            info(self, _("That place is in your list already."))
            return
        with wx.TextEntryDialog(self, _("A name for this place:"), _("Add a place"), gathering.label(p)) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            name = d.GetValue().strip()
        if name and name != gathering.label(p):
            p["name"] = name
        self.own.append(p)
        self.fill_places()
        self.list.SetSelection(len(self.own) - 1)
        info(self, _("Added {place} on {map}. Stand where you gather by hand: the bot stands on that very spot.",
                     place=gathering.label(p), map=atlas.label(int(mid))))

    def on_rename(self, _evt):
        i = self.list.GetSelection()
        if i < 0:
            return
        p = self.own[i]
        with wx.TextEntryDialog(self, _("A name for this place:"), _("Rename"), gathering.label(p)) as d:
            if d.ShowModal() != wx.ID_OK:
                return
            name = d.GetValue().strip()
        p.pop("name", None)
        if name and name != gathering.label(p):
            p["name"] = name
        self.fill_places()
        self.list.SetSelection(i)

    def on_remove(self, _evt):
        i = self.list.GetSelection()
        if i < 0:
            return
        del self.own[i]
        self.fill_places()
        if self.own:
            self.list.SetSelection(min(i, len(self.own) - 1))

    # ------------------------------------------------------------ saving
    def done(self, code):
        i = self.where.GetSelection()
        if self.gang.GetValue() and not self.gangs:
            error(self, _("Add a gang to gather in, or untick gathering in gangs' areas."))
            self.gang_list.SetFocus()
            return
        self.cfg.update(gang=self.gang.GetValue(), gangs=self.gangs, gang_fee_max=self.fee.GetValue(),
                        outside=self.outside.GetValue(),
                        known_places=self.known.GetValue(), above=self.above.GetValue(),
                        place=self.choices[i] if 0 < i < len(self.choices) else "", places=self.own,
                        sell=self.sell.GetValue(), when_full=WHEN_FULL[self.full.GetSelection()],
                        free_load=self.free.GetValue(), stop_vitality=self.vitality.GetValue(),
                        minutes=self.minutes.GetValue())
        self.p["gathering"] = self.cfg
        self.p["gem_keep_arnebia"] = self.keep_arnebia.GetValue()
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)
