"""Tools: the Gem Maker and the Equipment Enhancer (engine/crafting.py does the work).

Each window sets up its tool for one character and saves it in the character's profile; Check
only runs it without spending anything (it walks there, reads the craftsman's real menus and
prices, and says what it would do), Start runs it. Both run as a job on that character's game.
"""

import re

import wx

from .. import settings
from ..engine.crafting import CRYSTAL_LEVELS, MAX_ENHANCE, shortfall
from ..engine.town import shard_catalog
from ..i18n import _
from .common import Form, bind, group, labeled, ok_cancel, page, pick, save_file, tabs

CHECK = wx.ID_HIGHEST + 101
START = wx.ID_HIGHEST + 102
WHERE = ("gang", "other_gang", "city")
PROTECT = ("always", "from", "never")
GEM = re.compile(r"^\s*Level (\d+) ([A-Za-z' ]+?)\s*$")


def where_names():
    return [_("My own gang's (gangs pay less)"), _("Another gang's, by its name"), _("The nearest city's")]


def bag_counts(p):
    out = {}
    for r in (p.get("scan") or {}).get("bag", []):
        out[r["name"]] = out.get(r["name"], 0) + int(r.get("count") or 0)
    return out


class ToolDialog(wx.Dialog):
    """Shared parts: the profile, the craftsman choice, the arnebia reserve, Check only / Start."""

    KEY = ""

    def __init__(self, parent, character, title):
        super().__init__(parent, title=_("{tool} - {name}", tool=title, name=character),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.p = settings.load_profile(character)
        self.cfg = dict(self.p.get(self.KEY) or {})
        self.bag = bag_counts(self.p)
        self.root = wx.BoxSizer(wx.VERTICAL)

    def finish_layout(self):
        ok_cancel(self, self.root, _("Save"), extra=[(_("&Check only"), lambda e: self.done(CHECK)),
                                                     (_("Sta&rt"), lambda e: self.done(START))])
        self.SetSizerAndFit(self.root)
        self.SetMinSize(wx.Size(620, 520))
        self.Bind(wx.EVT_BUTTON, lambda e: self.done(wx.ID_OK), id=wx.ID_OK)

    def collect(self) -> bool:
        """Read the fields and say whether they can be used; each tool implements it."""
        raise NotImplementedError

    def common(self, f):
        where = self.cfg.get("where", "gang")
        self.where = f.choice(_("Cra&ftsman:"), where_names(), WHERE.index(where) if where in WHERE else 0)
        self.gang_name = f.text(_("&Gang name (another gang's):"), self.cfg.get("gang_name", ""))
        self.gang_name.Enable(self.where.GetSelection() == 1)
        self.where.Bind(wx.EVT_CHOICE, self.on_where)
        self.keep_arnebia = f.number(_("Never spend arnebia belo&w:"), self.p.get("gem_keep_arnebia", 20000),
                                     0, 1000000000)

    def on_where(self, event):
        self.gang_name.Enable(self.where.GetSelection() == 1)
        event.Skip()

    def done(self, code):
        if not self.collect():
            return
        self.cfg["where"] = WHERE[self.where.GetSelection()]
        self.cfg["gang_name"] = self.gang_name.GetValue().strip()
        if self.cfg["where"] == "other_gang" and not self.cfg["gang_name"]:
            wx.MessageBox(_("Type the name of the gang whose craftsmen to use."), _("Gang name"),
                          wx.OK | wx.ICON_INFORMATION, self)
            self.gang_name.SetFocus()
            return
        self.p[self.KEY] = self.cfg
        self.p["gem_keep_arnebia"] = self.keep_arnebia.GetValue()
        if save_file(self, settings.save_profile, self.character, self.p):
            self.EndModal(code)


SUPPLY = ("none", "both", "storage", "buy")
AMOUNT = ("count", "most")
MODES = ("gem", "bag")


def colour_of(name):
    m = re.match(r"^\s*Level \d+ ([A-Za-z' ]+?)(?: Shard)?\s*$", name or "", re.I)
    return m.group(1).strip() if m and "crystal" not in m.group(1).lower() else None


class GemMakerDialog(ToolDialog):
    """Two jobs: make a gem (which one, which level, how many - fetching Level 1 shards when the bag
    runs short), or make the most of the bag (every shard and gem of the ticked colours as high as
    it goes). Boost shards, protection runes, craftsman and arnebia reserve serve both."""

    KEY = "gem_maker"

    def __init__(self, parent, character):
        super().__init__(parent, character, _("Gem Maker"))
        c = self.cfg
        # profiles saved before the three questions: take what they had ("kind" made one shard's gems)
        colour = c.get("colour") or colour_of(c.get("kind")) or ""
        amount = "most" if c.get("amount") in ("most", "fill") else "count"
        mode = "bag" if c.get("mode") == "bag" else "gem"
        nb = tabs(self)

        panel, box = page(nb, _("Gems"))
        f = Form(panel)
        self.mode = f.choice(_("&What to do:"), [_("Make a gem: choose which, its level and how many"),
                                                 _("Make the most of my bag: every shard and gem as high as it goes")],
                             MODES.index(mode))
        box.Add(f.grid, 0, wx.EXPAND)

        # ------------------------------------------------------------ a gem
        f = group(panel, box, _("Make a gem"))
        known = {colour_of(n) for n in shard_catalog()[0]} | {colour_of(n) for n in self.bag}
        self.gem_colours = sorted({col for col in known if col} | ({colour} if colour else set()), key=str.lower)
        self.colour = f.choice(_("&Gem:"), [self.colour_label(col) for col in self.gem_colours],
                               self.gem_colours.index(colour) if colour in self.gem_colours else 0)
        self.level = f.number(_("&Level:"), c.get("level", 2), 1, 30)
        self.amount = f.choice(_("&How many:"), [_("This many"), _("As many as I can")], AMOUNT.index(amount))
        self.count = f.number(_("&Number of gems:"), c.get("count", 10), 1, 100000)
        self.supply = f.choice(_("Get more Level 1 shards &from:"), [_("Nowhere, use what's in the bag"),
                                                                    _("The storehouse first, then the Shard Market"),
                                                                    _("The storehouse only"),
                                                                    _("The Shard Market only")],
                               pick(SUPPLY, c.get("supply", "both")))
        self.price_on, self.max_price = f.optional(_("Limit the &price at the market"), bool(c.get("max_price")),
                                                   _("Most to pay per shard (arnebia):"),
                                                   c.get("max_price") or 2000, 1, 100000000)

        # ------------------------------------------------------------ the whole bag
        f = group(panel, box, _("Make the most of my bag"))
        self.colours = sorted({name_colour for n in self.bag if (name_colour := colour_of(n))}
                              | set(c.get("colours", [])), key=lambda colour: colour.lower())
        self.colour_list = labeled(f.parent, f.box, _("Colo&urs (none ticked: all of them):"),
                                   lambda w: wx.CheckListBox(w, size=wx.Size(-1, 110)), 1)
        self.colour_list.Set([self.colour_label(col) for col in self.colours])
        for i, col in enumerate(self.colours):
            self.colour_list.Check(i, col in c.get("colours", []))
        f2 = Form(f.parent)
        self.up_to = f2.number(_("Highest l&evel to make:"), c.get("up_to", 20), 2, 30)
        self.keep = f2.number(_("&Keep at least this many of each level:"), c.get("keep", 0), 0, 1000)
        f.box.Add(f2.grid, 0, wx.EXPAND | wx.ALL, 8)

        # ------------------------------------------------------------ combining, craftsman, plan
        panel, box = page(nb, _("Combining and craftsman"))
        f = group(panel, box, _("Combining gems (3 make 1, 50% chance)"))
        self.boost = f.check(_("Add a leftover matching shard for +20% (level 2 and up)"), c.get("boost", True))
        self.protect = f.check(_("Use Gem &Protection Runes (a failure breaks no gem)"), c.get("protect", False))
        f = group(panel, box, _("Craftsman and spending"))
        self.common(f)
        self.plan = labeled(panel, box, _("Plan, from the last scan of your bag:"),
                            lambda w: wx.TextCtrl(w, style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 140)), 1)

        for ctrl, ev in ((self.mode, wx.EVT_CHOICE), (self.colour, wx.EVT_CHOICE), (self.level, wx.EVT_SPINCTRL),
                         (self.amount, wx.EVT_CHOICE), (self.count, wx.EVT_SPINCTRL), (self.supply, wx.EVT_CHOICE),
                         (self.price_on, wx.EVT_CHECKBOX), (self.colour_list, wx.EVT_CHECKLISTBOX),
                         (self.up_to, wx.EVT_SPINCTRL), (self.keep, wx.EVT_SPINCTRL),
                         (self.boost, wx.EVT_CHECKBOX), (self.protect, wx.EVT_CHECKBOX)):
            bind(ctrl, ev, lambda e: (self.sync(), self.show_plan()))
        self.root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.finish_layout()
        self.sync()
        self.show_plan()

    @staticmethod
    def _enable(ctrl, on):
        ctrl.Enable(on)
        if hasattr(ctrl, "label_ctrl"):
            ctrl.label_ctrl.Enable(on)

    def sync(self):
        gem_mode = self.mode.GetSelection() == 0
        for ctrl in (self.colour, self.level, self.amount, self.supply):
            self._enable(ctrl, gem_mode)
        for ctrl in (self.colour_list, self.up_to, self.keep):
            self._enable(ctrl, not gem_mode)
        market = gem_mode and self.supply.GetSelection() not in (0, 2)
        self._enable(self.count, gem_mode and self.amount.GetSelection() == 0)
        self._enable(self.price_on, market)
        self._enable(self.max_price, market and self.price_on.GetValue())

    def levels(self, colour, shards=False):
        out = {}
        for n, k in self.bag.items():
            if colour_of(n) == colour and (("Shard" in n) == shards):
                m = re.match(r"^\s*Level (\d+)", n)
                lv = int(m.group(1)) if m else 0
                out[lv] = out.get(lv, 0) + k
        return out

    def colour_label(self, colour):
        gems, shards = self.levels(colour), self.levels(colour, True)
        bits = [_("{n} gems at level {lv}", n=gems[k], lv=k) for k in sorted(gems)]
        bits += [_("{n} shards at level {lv}", n=shards[k], lv=k) for k in sorted(shards)]
        if not bits:
            return _("{colour} (none in the bag)", colour=colour)
        return _("{colour}: {what}", colour=colour, what=", ".join(bits))

    def simulate(self, colour, gems, shards, up_to, keep=0):
        """Expected results for one colour, the way the Gem Maker works: shards into gems, then up."""
        lines = []
        boost = self.boost.GetValue()
        for _round in range(6):
            moved = False
            for lv in sorted(shards):
                if lv <= up_to and shards[lv] >= 4:
                    gems[lv] = gems.get(lv, 0) + shards[lv] // 4
                    shards[lv] %= 4
                    moved = True
            for lv in range(1, up_to):
                tries = int(max(0, gems.get(lv, 0) - keep) // 3)
                if not tries:
                    continue
                boosted = boost and lv >= 2 and shards.get(lv, 0) > 0
                if boosted:
                    shards[lv] -= min(tries, shards[lv])          # a boost shard goes in each try
                chance = 0.7 if boosted else 0.5
                made, failed = tries * chance, tries * (1 - chance)
                # success: 3 gems become 1 a level up; failure: one of the 3 shatters into a shard
                # (a protection rune saves it)
                gems[lv] = gems.get(lv, 0) - 3 * made - (0 if self.protect.GetValue() else failed)
                if not self.protect.GetValue():
                    shards[lv] = shards.get(lv, 0) + failed
                gems[lv + 1] = gems.get(lv + 1, 0) + made
                moved = True
            if not moved:
                break
        top = {lv: int(round(n)) for lv, n in gems.items() if int(round(n)) > 0}
        if top:
            lines.append(_("{colour}: about {gems}", colour=colour,
                           gems=", ".join(_("{n} at level {lv}", n=top[lv], lv=lv) for lv in sorted(top))))
        return lines

    def show_plan(self):
        self.plan.ChangeValue("\n".join(self.gem_plan() if self.mode.GetSelection() == 0 else self.bag_plan()))

    def bag_plan(self):
        lines = []
        ticked = [col for i, col in enumerate(self.colours) if self.colour_list.IsChecked(i)] or self.colours
        for col in ticked:
            lines += self.simulate(col, dict(self.levels(col)), dict(self.levels(col, True)),
                                   self.up_to.GetValue(), self.keep.GetValue())
        if lines:
            lines.insert(0, _("Every shard into gems, then each colour up as far as it goes (failures give back "
                              "a shard, which is used again). Expected:"))
        return lines or [_("Nothing to do: no gems or shards in the bag yet.")]

    def gem_plan(self):
        if not self.gem_colours:
            return [_("No gem kinds known yet.")]
        col = self.gem_colours[max(0, self.colour.GetSelection())]
        level = self.level.GetValue()
        gem, shard1 = "Level %d %s" % (level, col), "Level 1 %s Shard" % col
        supply = SUPPLY[self.supply.GetSelection()]
        gems, shards = self.levels(col), self.levels(col, True)
        lines = [_("In the bag: {n} {gem}.", n=gems.get(level, 0), gem=gem)]
        source = {"none": "", "both": _("the storehouse, then the Shard Market"), "storage": _("the storehouse"),
                  "buy": _("the Shard Market")}[supply]
        if self.amount.GetSelection() == 0:
            n = self.count.GetValue()
            _short, need = shortfall(gems, shards, level, n, self.protect.GetValue())
            lines.append(_("Make {n} more {gem}.", n=n, gem=gem))
            if not need:
                lines.append(_("What's in the bag should be enough (a combine works half the time, so maybe not)."))
            else:
                price = (shard_catalog()[0].get(shard1) or {}).get("price")
                cost = _(", about {total} arnebia at the market's last seen price", total=need * price) if price else ""
                lines.append(_("That takes about {k} more {shard}{cost}, plus the craftsman's fees.",
                               k=need, shard=shard1, cost=cost))
                lines.append(_("They come from {source}, a bagful at a time.", source=source) if source else
                             _("Nowhere to get them from: it makes what the bag allows."))
        else:
            lines.append(_("Make as many {gem} as I can.", gem=gem))
            lines += self.simulate(col, dict(gems), dict(shards), level)
            if source:
                lines.append(_("Then fill the bag with {shard} from {source} and make them up, again and again.",
                               shard=shard1, source=source))
        return lines

    def collect(self):
        c = self.cfg
        c["mode"] = MODES[self.mode.GetSelection()]
        c["colour"] = self.gem_colours[max(0, self.colour.GetSelection())] if self.gem_colours else ""
        c["level"] = self.level.GetValue()
        c["amount"] = AMOUNT[self.amount.GetSelection()]
        c["count"] = self.count.GetValue()
        c["supply"] = SUPPLY[self.supply.GetSelection()]
        c["max_price"] = self.max_price.GetValue() if self.price_on.GetValue() else 0
        c["colours"] = [col for i, col in enumerate(self.colours) if self.colour_list.IsChecked(i)]
        c["up_to"] = self.up_to.GetValue()
        c["keep"] = self.keep.GetValue()
        c["boost"] = self.boost.GetValue()
        c["protect"] = self.protect.GetValue()
        for old in ("kind", "each", "raise_after", "shards", "raise"):
            c.pop(old, None)
        return True


class EnhancerDialog(ToolDialog):
    KEY = "enhancer"

    def __init__(self, parent, character):
        super().__init__(parent, character, _("Equipment Enhancer"))
        c = self.cfg
        nb = tabs(self)

        panel, box = page(nb, _("Equipment"))
        # the game marks some names up ("lzs0lzs2243lzs1lzsdiamond wedding ring"): keep the words
        gear = [re.sub(r"(lzs\d*)+", "", g).strip() for g in (self.p.get("scan") or {}).get("gear", [])]
        gear = [g for g in gear if g]
        self.gear = sorted(set(gear + [g for g in c.get("items", []) if g not in gear]), key=str.lower)
        self.items = labeled(panel, box, _("&Equipment to enhance:"), lambda w: wx.CheckListBox(w, size=wx.Size(-1, 170)), 1)
        self.items.Set(self.gear)
        for i, g in enumerate(self.gear):
            self.items.Check(i, g in c.get("items", []))
        f = Form(panel)
        self.up_to = f.number(_("Enhance &up to (+):"), min(MAX_ENHANCE, c.get("up_to", 10)), 1, MAX_ENHANCE)
        box.Add(f.grid, 0, wx.EXPAND | wx.TOP, 10)

        panel, box = page(nb, _("Crystals"))
        f = group(panel, box, _("Enhance Crystals"))
        self.crystals = f.check(_("&Make Enhance Crystals from crystal shards when needed"),
                                c.get("make_crystals", True))
        self.supply = f.choice(_("Get crystal shards &from:"), [_("Nowhere, use what's in the bag"),
                                                                _("The storehouse first, then the Shard Broker"),
                                                                _("The storehouse only"), _("The Shard Broker only")],
                               pick(SUPPLY, c.get("crystal_supply", "both")))
        self.price_on, self.max_price = f.optional(_("Limit the &price at the Shard Broker"),
                                                   bool(c.get("crystal_max_price")),
                                                   _("Most to pay per shard (arnebia):"),
                                                   c.get("crystal_max_price") or 5000, 1, 100000000)
        self.stock = f.number(_("&Keep this many crystals in the bag before starting (0: only as needed):"),
                              c.get("crystal_stock", 0), 0, 100000)
        self.refill = f.number(_("When crystals run &out, make this many more:"), c.get("crystal_refill", 10),
                               1, 100000)
        have = ["%s: %d" % (n, k) for n, k in sorted(self.bag.items())
                if re.search(r"Enhance (Crystal|Protection Rune|Boost Rune)", n)]
        self.have = labeled(panel, box, _("You have (last scan):"),
                            lambda w: wx.TextCtrl(w, value="\n".join(have) or _("No crystals or runes seen yet."),
                                                  style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 90)), 1)
        self.levels = labeled(panel, box, _("Which crystal level an item takes:"),
                              lambda w: wx.TextCtrl(w, value=crystal_notes(),
                                                    style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 90)), 1)

        panel, box = page(nb, _("Runes and limits"))
        f = group(panel, box, _("Runes"))
        self.protect = f.choice(_("&Protection runes (a failure keeps the level):"),
                                [_("On every try"), _("From a level on"), _("Never")],
                                pick(PROTECT, c.get("protect", "always")))
        self.protect_from = f.number(_("From (+):"), min(MAX_ENHANCE, c.get("protect_from", 8)), 1, MAX_ENHANCE)
        self.boost = f.check(_("Use &boost runes when I have enough (+5%)"), c.get("boost", False))
        f = group(panel, box, _("Limits"))
        self.max_fails = f.number(_("Stop after this many failures &in a row:"), c.get("max_fails", 5), 1, 100)
        f = group(panel, box, _("Craftsman and spending"))
        self.common(f)
        for ctrl, ev in ((self.protect, wx.EVT_CHOICE), (self.crystals, wx.EVT_CHECKBOX),
                         (self.supply, wx.EVT_CHOICE), (self.price_on, wx.EVT_CHECKBOX)):
            ctrl.Bind(ev, lambda e: self.sync())
        self.root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.finish_layout()
        self.sync()

    @staticmethod
    def _enable(ctrl, on):
        ctrl.Enable(on)
        if hasattr(ctrl, "label_ctrl"):
            ctrl.label_ctrl.Enable(on)

    def sync(self):
        self._enable(self.protect_from, self.protect.GetSelection() == 1)
        making = self.crystals.GetValue()
        for ctrl in (self.supply, self.stock, self.refill):
            self._enable(ctrl, making)
        broker = making and self.supply.GetSelection() not in (0, 2)
        self._enable(self.price_on, broker)
        self._enable(self.max_price, broker and self.price_on.GetValue())

    def collect(self):
        items = [g for i, g in enumerate(self.gear) if self.items.IsChecked(i)]
        self.cfg.update(items=items, up_to=self.up_to.GetValue(), protect=PROTECT[self.protect.GetSelection()],
                        protect_from=self.protect_from.GetValue(), boost=self.boost.GetValue(),
                        make_crystals=self.crystals.GetValue(), max_fails=self.max_fails.GetValue(),
                        crystal_supply=SUPPLY[self.supply.GetSelection()],
                        crystal_max_price=self.max_price.GetValue() if self.price_on.GetValue() else 0,
                        crystal_stock=self.stock.GetValue(), crystal_refill=self.refill.GetValue())
        return True


def crystal_notes():
    """The crystal-level table as lines for the window."""
    rows, top = [], None
    for lo, lv in CRYSTAL_LEVELS:
        rows.append(_("Gear of level {lo} and up: Level {lv} crystals", lo=lo, lv=lv) if top is None else
                    _("Gear of levels {lo} to {hi}: Level {lv} crystals", lo=lo, hi=top - 1, lv=lv))
        top = lo
    return "\n".join(reversed(rows)) + "\n" + _("The bot reads the real one from the Forger every try.")
