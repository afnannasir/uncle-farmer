"""The walking assistant's window: pick a dungeon entrance or a map, and the bot takes the character
there on the fastest Swift fusion beast, never stopping to fight (the `go` script command)."""

import wx

from .. import atlas, dungeons, travel
from ..errors import carry_on
from ..i18n import _
from .common import Form, enable_label, error, labeled, ok_cancel, page, tabs

NOT_SEEN = _("The game isn't readable. Is it running, with a character logged in?")


def _quote(text):
    return '"%s"' % text.replace('"', "").replace("\r", " ").replace("\n", " ")


def entrance_line(e):
    team = _(", team of 2-3") if e.get("team") else ""
    return _("{name} - {map}{team}", name=e["name"], map=atlas.label(e["map"]), team=team)


class WalkDialog(wx.Dialog):
    """script: the `go` line to run once OK is pressed; title: what the run is called."""

    def __init__(self, parent, game):
        super().__init__(parent, title=_("Walk to"), style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.game = game
        self.script = self.title = None
        self.entrances = dungeons.entrances()
        # only the open world: a closed map or one inside a dungeon can't be walked to (dungeons
        # have their own tab, by entrance)
        open_world = travel.open_maps()
        self.all_maps = [(mid, lab) for mid, lab in atlas.all_maps() if mid in open_world]
        root = wx.BoxSizer(wx.VERTICAL)
        self.nb = tabs(self)

        panel, box = page(self.nb, _("Dungeon entrance"))
        self.d_search = labeled(panel, box, _("&Find:"), lambda p: wx.TextCtrl(p))
        self.d_list = labeled(panel, box, _("&Dungeon:"), lambda p: wx.ListBox(p, size=wx.Size(-1, 200)), 1)
        f = Form(panel)
        self.talk = f.check(_("Open its &menu when I get there"), True)
        box.Add(f.grid, 0, wx.TOP, 8)
        self.d_about = labeled(panel, box, _("About this entrance:"),
                               lambda p: wx.TextCtrl(p, style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 90)))

        panel, box = page(self.nb, _("Map"))
        self.m_search = labeled(panel, box, _("&Find:"), lambda p: wx.TextCtrl(p))
        self.m_list = labeled(panel, box, _("&Map:"), lambda p: wx.ListBox(p, size=wx.Size(-1, 200)), 1)
        f = Form(panel)
        self.cell_on = f.check(_("Go to a &cell on the map"), False)
        self.cx = f.number(_("Cell x:"), 0, 0, 2000)
        self.cy = f.number(_("Cell y:"), 0, 0, 2000)
        box.Add(f.grid, 0, wx.TOP, 8)

        root.Add(self.nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        below = wx.BoxSizer(wx.VERTICAL)
        self.route = labeled(self, below, _("Route from where you are:"),
                             lambda p: wx.TextCtrl(p, style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 90)))
        root.Add(below, 0, wx.EXPAND | wx.LEFT | wx.RIGHT, 12)
        ok_cancel(self, root, _("&Go"), extra=(_("Show the &route"), self.on_route))
        self.SetSizerAndFit(root)
        self.SetMinSize(wx.Size(560, 560))

        self.d_search.Bind(wx.EVT_TEXT, lambda e: self.fill_dungeons())
        self.d_list.Bind(wx.EVT_LISTBOX, lambda e: self.describe())
        self.m_search.Bind(wx.EVT_TEXT, lambda e: self.fill_maps())
        self.m_list.Bind(wx.EVT_LISTBOX, lambda e: self.route.SetValue(""))
        self.cell_on.Bind(wx.EVT_CHECKBOX, lambda e: self.sync_cell())
        self.nb.Bind(wx.EVT_NOTEBOOK_PAGE_CHANGED, lambda e: (self.route.SetValue(""), e.Skip()))
        # Bound on the dialog, not the button: Enter in a text box sends OK to the dialog directly.
        self.Bind(wx.EVT_BUTTON, self.on_ok, id=wx.ID_OK)
        self.fill_dungeons()
        self.fill_maps()
        self.sync_cell()
        self.d_list.SetFocus()

    # ------------------------------------------------------------ lists
    def fill_dungeons(self):
        q = self.d_search.GetValue().strip().lower()
        self.d_items = [e for e in self.entrances if not q or q in entrance_line(e).lower()
                        or q in (e.get("npc") or "").lower()]
        self.d_list.Set([entrance_line(e) for e in self.d_items])
        if self.d_items:
            self.d_list.SetSelection(0)
        self.describe()

    def fill_maps(self):
        q = self.m_search.GetValue().strip().lower()
        self.m_items = [(mid, lab) for mid, lab in self.all_maps if not q or q in lab.lower()]
        words = [atlas.kind_words(atlas.kind(mid)) for mid, _l in self.m_items]
        self.m_list.Set(["%s%s" % (lab, (", " + w) if w else "") for (_m, lab), w in zip(self.m_items, words)])
        if self.m_items:
            self.m_list.SetSelection(0)

    def sync_cell(self):
        on = self.cell_on.GetValue()
        for c in (self.cx, self.cy):
            c.Enable(on)
            enable_label(c, on)

    def entrance(self):
        i = self.d_list.GetSelection()
        return self.d_items[i] if 0 <= i < len(self.d_items) else None

    def map_choice(self):
        i = self.m_list.GetSelection()
        return self.m_items[i][0] if 0 <= i < len(self.m_items) else None

    def describe(self):
        self.route.SetValue("")
        e = self.entrance()
        lines = []
        if e:
            if e.get("at"):
                lines.append(_("Entrance: {npc}, on {map} at {x},{y}.", npc=e["npc"], map=atlas.label(e["map"]),
                               x=e["at"][0], y=e["at"][1]))
            else:
                spots = e.get("spots") or []
                lines.append(_("Entrance: on {map}, at one of the {n} entrances the game marks there "
                               "({spots}). The bot looks at each until the game shows the {name} circle, "
                               "and remembers it for next time.", map=atlas.label(e["map"]),
                               n=len(spots), name=e["name"],
                               spots="; ".join("%d,%d %s" % (x, y, about) for x, y, about in spots)))
            if e.get("about"):
                lines.append(_("The game's map says: {about}", about=e["about"]))
            if e.get("dungeon_maps"):
                lines.append(_("Leads to: {maps}", maps=", ".join(atlas.label(m) for m in e["dungeon_maps"])))
            if e.get("team"):
                lines.append(_("A team dungeon: the game lets in groups of 2 to 3 players."))
        missing = dungeons.not_found()
        if missing:
            lines.append(_("Dungeons whose entrance the game's files don't show: {names}",
                           names=", ".join(d["name"] for d in missing)))
        self.d_about.SetValue("\n".join(lines))

    # ------------------------------------------------------------ the goal
    def on_dungeon_page(self):
        return self.nb.GetSelection() == 0

    def goal(self):
        """(map, cell, npc, words) for what is chosen, or None."""
        if self.on_dungeon_page():
            e = self.entrance()
            if not e:
                return None
            if e.get("at"):
                cell = tuple(e["at"])
            else:
                spots = e.get("spots") or []
                if not spots:
                    return None
                cell = tuple(spots[0][:2])          # the route shown goes to the first marked spot
            return e["map"], cell, e.get("npc"), e["name"]
        mid = self.map_choice()
        if mid is None:
            return None
        cell = (self.cx.GetValue(), self.cy.GetValue()) if self.cell_on.GetValue() else None
        return mid, cell, None, atlas.label(mid)

    def on_route(self, _evt):
        g = self.goal()
        if not g:
            return
        snap = self.game()
        me = (snap or {}).get("self") or {}
        here = ((snap or {}).get("map") or {}).get("id")
        if not me or here is None:
            error(self, NOT_SEEN)
            return
        mid, cell, npc, _words = g
        try:
            legs = travel.plan(here, (me["x"], me["y"]), mid, cell, items=False)
        except Exception as e:      # a map the atlas can't read: say so, don't crash the window
            carry_on(e, "working out a walking route")
            legs = None
            self.route.SetValue(_("Couldn't work out a route: {err}", err=e))
            return
        if legs is None:
            self.route.SetValue(_("No way found from {map} - travel part of the way yourself first.",
                                  map=atlas.label(here)))
        elif not legs:
            self.route.SetValue(_("You are already on {map}.", map=atlas.label(mid)))
        else:
            self.route.SetValue("\n".join(travel.describe(legs)))
        self.route.SetFocus()

    def on_ok(self, _evt):
        g = self.goal()
        if not g:
            error(self, _("Choose where to go first."))
            return
        mid, cell, npc, words = g
        if self.on_dungeon_page():
            self.script = "go dungeon %s%s\n" % (_quote(words), " talk" if self.talk.GetValue() else "")
        else:
            self.script = "go %d%s\n" % (mid, (" at %d,%d" % cell) if cell else "")
        self.title = _("Walk to {where}", where=words)
        self.EndModal(wx.ID_OK)
