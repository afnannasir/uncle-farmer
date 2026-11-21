"""Tools, Potion Maker (engine/potionmaker.py does the work).

How many of each potion to have, how Medium and Large potions are made (Smalls fused up, or their
own recipe), what is fused up afterwards, where missing Bloods and Pelts come from and the most a
piece the market may charge for each. Check only walks to the Pharmacist, reads the real fees and
says what it would do; Start runs it.
"""

from __future__ import annotations

from typing import Any

import wx

from ..engine import potionmaker as pm
from ..i18n import _
from .common import Form, group, labeled, page, pick, tabs
from .crafting import START, ToolDialog

WHERE = ("gang", "other_gang")


class PotionMakerDialog(ToolDialog):
    KEY = "potion_maker"

    def __init__(self, parent: wx.Window, character: str) -> None:
        super().__init__(parent, character, _("Potion Maker"))
        c = self.cfg = pm.config({"potion_maker": self.cfg})
        targets: dict[str, Any] = c["targets"]
        nb = tabs(self)
        self.wants: dict[str, wx.SpinCtrl] = {}
        for kind, title in (("player", _("Potions")), ("pet", _("Pet potions"))):
            panel, box = page(nb, title)
            box.Add(wx.StaticText(panel, label=_("How many of each to have in the bag (0: don't make it).")), 0,
                    wx.BOTTOM, 6)
            f = Form(panel)
            for name in pm.names_for(kind):
                self.wants[name] = f.number(self.want_label(name), int(targets.get(name) or 0), 0, 1000000)
            box.Add(f.grid, 0, wx.EXPAND)

        panel, box = page(nb, _("How"))
        f = group(panel, box, _("Medium and Large potions"))
        self.way = f.choice(_("&Make them:"), [_("From Small ones, fused up (5 for 1, usually cheaper)"),
                                                _("Each from its own recipe")], pick(pm.WAYS, c["way"]))
        f = group(panel, box, _("Afterwards"))
        self.fuse_up = f.choice(_("&Fuse spare potions up to:"), [_("Don't fuse"), _("Medium"), _("Large")],
                                pick(pm.FUSE_UP, c["fuse_up"]))
        self.keep_small = f.number(_("Keep at least this many of each &Small potion:"), c["keep_small"], 0, 1000000)
        self.keep_medium = f.number(_("Keep at least this many of each M&edium potion:"), c["keep_medium"], 0,
                                    1000000)
        self.store = f.check(_("Put the potions made into the town st&orehouse, then make more, a bagful at a time "
                               "(your numbers then count the storehouse too)"), c["store"])
        f = group(panel, box, _("Pharmacist and spending"))
        where = c.get("where", "gang")
        self.where = f.choice(_("&Pharmacist:"), [_("My own gang's (gangs pay less)"), _("Another gang's, by its name")],
                              WHERE.index(where) if where in WHERE else 0)
        self.gang_name = f.text(_("&Gang name (another gang's):"), c.get("gang_name", ""))
        self.gang_name.Enable(self.where.GetSelection() == 1)
        self.where.Bind(wx.EVT_CHOICE, self.on_where)
        self.keep_arnebia = f.number(_("Never spend arnebia belo&w:"), self.p.get("gem_keep_arnebia", 20000),
                                     0, 1000000000)

        panel, box = page(nb, _("Materials"))
        f = group(panel, box, _("Missing Bloods and Pelts"))
        self.supply = f.choice(_("&Take them from:"), [_(s) for s in pm.SUPPLY_LABELS], pick(pm.SUPPLIES, c["supply"]))
        self.gold_cap = f.number(_("Most gold coins to spend on materials in one run (0: no limit):"),
                                 int(c["gold_cap"] or 0), 0, 2000000000)
        f = group(panel, box, _("Most gold coins a piece at the market (0: the cheapest first, any price)"))
        prices: dict[str, Any] = c["prices"]
        self.prices: dict[str, wx.SpinCtrl] = {}
        for mat in pm.MATERIALS:
            have = self.bag_count(mat)
            label = _("{item} (you have {n}):", item=mat, n=have) if have else _("{item}:", item=mat)
            self.prices[mat] = f.number(label, int(prices.get(mat) or 0), 0, 100000000)
        self.plan = labeled(panel, box, _("Plan, from the last scan of your bag:"),
                            lambda w: wx.TextCtrl(w, style=wx.TE_MULTILINE | wx.TE_READONLY, size=wx.Size(-1, 140)), 1)

        def changed(_evt: wx.Event) -> None:
            self.sync()
        for spin in [*self.wants.values(), self.keep_small, self.keep_medium]:
            spin.Bind(wx.EVT_SPINCTRL, changed)
        for choice in (self.way, self.fuse_up, self.supply):
            choice.Bind(wx.EVT_CHOICE, changed)
        self.store.Bind(wx.EVT_CHECKBOX, changed)
        self.root.Add(nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 12)
        self.finish_layout()
        self.sync()

    def bag_count(self, name: str) -> int:
        return sum(n for k, n in self.bag.items() if k.lower() == name.lower())

    def want_label(self, name: str) -> str:
        have = self.bag_count(name)
        return _("{potion} (you have {n}):", potion=name, n=have) if have else _("{potion}:", potion=name)

    def settings_now(self) -> dict[str, Any]:
        return {"targets": {n: s.GetValue() for n, s in self.wants.items() if s.GetValue()},
                "way": pm.WAYS[self.way.GetSelection()], "fuse_up": pm.FUSE_UP[self.fuse_up.GetSelection()],
                "keep_small": self.keep_small.GetValue(), "keep_medium": self.keep_medium.GetValue(),
                "supply": pm.SUPPLIES[self.supply.GetSelection()], "gold_cap": self.gold_cap.GetValue(),
                "prices": {m: s.GetValue() for m, s in self.prices.items() if s.GetValue()},
                "store": self.store.GetValue()}

    def plan_lines(self) -> list[str]:
        p = pm.plan(self.bag, self.settings_now())
        if p.empty():
            return [_("Nothing to make: say how many of a potion to have, or choose what to fuse.")]
        where = "gang" if self.where.GetSelection() == 0 else "gang:" + self.gang_name.GetValue().strip()
        fees: dict[str, dict[str, int]] = self.p.get("potion_fees") or {}
        lines = pm.describe(p, fees.get(where) or {})
        if p.batches and not fees.get(where):
            lines.append(_("The fees are read off the Pharmacist's pages (Check only shows them)."))
        if self.store.GetValue():
            lines.append(_("The storehouse's potions count too: Check only reads them. It goes a bagful at a time."))
        return lines

    def sync(self) -> None:
        on = self.fuse_up.GetSelection() > 0
        self.keep_small.Enable(on)
        self.keep_medium.Enable(on and self.fuse_up.GetSelection() > 1)
        self.plan.ChangeValue("\n".join(self.plan_lines()))

    def collect(self) -> bool:
        now = self.settings_now()
        if not now["targets"] and now["fuse_up"] == "no":
            wx.MessageBox(_("Say how many of at least one potion to have, or choose what to fuse."), _("Potion Maker"),
                          wx.OK | wx.ICON_INFORMATION, self)
            return False
        self.cfg.update(now)
        return True

    def done(self, code: int) -> None:
        if code == START and self.settings_now()["supply"] in ("buy", "both") and not self.settings_now()["prices"] \
                and wx.MessageBox(_("No most price is set for any material, so the market's cheapest lots are bought "
                                    "at any price. Go on?"), _("Potion Maker"),
                                  wx.YES_NO | wx.NO_DEFAULT | wx.ICON_QUESTION, self) != wx.YES:
            return
        super().done(code)
