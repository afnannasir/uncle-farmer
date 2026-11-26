"""Tools, Buy collection items: what to buy at the market, the most a unit, the day's count, and what to
do afterwards and when the bag is too heavy (engine/buycollection.py)."""

from __future__ import annotations

from typing import Any

import wx

from .. import settings
from ..engine import buycollection
from ..i18n import N_, _
from .common import Dialog, Form, labeled, ok_cancel, save_file
from .crafting import CHECK, START

HEAVY_LABELS = (N_("Stop buying"), N_("Sell the collection to the town's buyers, then buy on"))   # WHEN_HEAVY's order


class BuyCollectionDialog(Dialog):
    """The tool's settings, kept in the character's profile (buy_collection); Save, Check only, Start."""

    def __init__(self, parent: wx.Window, character: str) -> None:
        super().__init__(parent, title=_("{tool} - {name}", tool=_("Buy collection items"), name=character),
                         style=wx.DEFAULT_DIALOG_STYLE | wx.RESIZE_BORDER)
        self.character = character
        self.p = settings.load_profile(character)
        self.cfg = buycollection.config(self.p)
        c = self.cfg
        root = self.root = wx.BoxSizer(wx.VERTICAL)
        panel = self
        known = buycollection.known_items()
        lower = {n.lower() for n in known}
        others = [n for n in c["items"] if n.lower() not in lower]
        self.names = known
        self.item_list = labeled(panel, root, _("&Items to buy (the market's collections):"),
                                 lambda w: wx.CheckListBox(w, size=wx.Size(-1, 170)), 1)
        self.item_list.Set(known)
        chosen = {n.lower() for n in c["items"]}
        for i, n in enumerate(known):
            self.item_list.Check(i, n.lower() in chosen)
        f = Form(panel)
        self.others = f.text(_("&Other names (by commas, as the game writes them):"), ", ".join(others))
        self.max_price = f.number(_("&Most gold coins a unit:"), c["max_price"], 1, 100000000)
        self.daily = f.number(_("Items a &day, at most (the game allows {n}):", n=buycollection.DAILY_MOST),
                              c["daily"], 1, buycollection.DAILY_MOST)
        self.today = f.number(_("&Bought today (the game's day, from 00:00 GMT):"),
                              buycollection.bought_today(self.key()), 0, buycollection.DAILY_MOST)
        self.sell_after = f.check(_("&Sell the collection to the town's buyers when done"), c["sell_after"])
        heavy = buycollection.WHEN_HEAVY.index(c["when_heavy"])
        self.when_heavy = f.choice(_("When the bag is too &heavy:"), [_(t) for t in HEAVY_LABELS], heavy)
        root.Add(f.grid, 0, wx.EXPAND | wx.TOP, 8)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(root, 1, wx.EXPAND | wx.ALL, 12)
        ok_cancel(self, outer, _("Save"), extra=[(_("&Check only"), lambda e: self.done(CHECK)),
                                                (_("Sta&rt"), lambda e: self.done(START))])
        self.SetSizerAndFit(outer)
        self.SetMinSize(wx.Size(560, 520))
        self.Bind(wx.EVT_BUTTON, lambda e: self.done(wx.ID_OK), id=wx.ID_OK)

    def key(self) -> str:
        """The character the day's count is kept for (as the run's bot knows it: its profile key)."""
        return self.character

    def chosen(self) -> list[str]:
        ticked = [n for i, n in enumerate(self.names) if self.item_list.IsChecked(i)]
        extra = [n.strip() for n in self.others.GetValue().replace(";", ",").split(",") if n.strip()]
        seen: set[str] = set()
        out: list[str] = []
        for n in ticked + extra:
            if n.lower() not in seen:
                seen.add(n.lower())
                out.append(n)
        return out

    def settings_now(self) -> dict[str, Any]:
        return {"items": self.chosen(), "max_price": self.max_price.GetValue(), "daily": self.daily.GetValue(),
                "sell_after": self.sell_after.GetValue(),
                "when_heavy": buycollection.WHEN_HEAVY[max(0, self.when_heavy.GetSelection())]}

    def done(self, code: int) -> None:
        now = self.settings_now()
        if code != wx.ID_OK and not now["items"]:
            wx.MessageBox(_("Tick at least one item, or type its name."), _("Buy collection items"),
                          wx.OK | wx.ICON_INFORMATION, self)
            self.item_list.SetFocus()
            return
        self.p["buy_collection"] = now
        if save_file(self, settings.save_profile, self.character, self.p):
            buycollection.set_bought_today(self.key(), self.today.GetValue())
            self.cfg = now
            self.EndModal(code)
