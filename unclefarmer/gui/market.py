"""Tools, Market searcher and buyer (engine/market.py does the work).

The window stays open beside the game. It shows the market list (what is for sale, by category),
starts a search or a refresh as a job on the game, shows the finds while that job reads (it writes
market/status.json after every page; a timer here reads it), and buys the lots ticked in the
results, each found again by its listing id. Nothing is bought without Buy and a Yes to its total.
"""

from __future__ import annotations

import csv
import os
import time
from collections.abc import Sequence
from typing import TYPE_CHECKING, Any

import wx

from .. import gamelang, jsonio as json
from ..engine import market
from ..engine.market import ALL, CATEGORIES, ListRow, Lot, Order, Search
from ..i18n import _
from ..link import AgentLink, server_name
from .common import ask, error, first_selected, info, labeled, modal, save_file, select_row

if TYPE_CHECKING:
    from .mainparts.base import FrameParts

KINDS = ("item", "cheapest", "seller", "browse")
MY_LIST, ALL_ITEMS = -2, -3             # the category list's first two entries
SAVED_MOST = 100
POLL_MS = 700


def kind_names() -> list[str]:
    return [_("By item"), _("Cheapest first"), _("By seller ID"), _("Browse a category")]


def money(n: int) -> str:
    return "{:,}".format(n)


def row_cells(r: ListRow) -> list[str]:
    """One line of the items list: name, lots (and how many open soon), in all, cheapest (and the
    last refresh's when it changed), usual, sellers."""
    lots = str(r.lots) + (" " + _("(+{n} opening soon)", n=r.opening) if r.opening else "")
    cheapest = money(r.cheapest)
    if r.before is not None and r.before != r.cheapest:
        cheapest += " " + (_("(was {n})", n=money(r.before)))
    return [gamelang.shown(r.name) + (" (%s)" % r.quality if r.quality else ""), lots, money(r.qty), cheapest,
            money(r.usual), "?" if r.sellers is None else str(r.sellers)]


def lot_cells(x: Lot) -> list[str]:
    when = (_("opens in {min} min", min=max(1, x.opens_in // 60)) if x.opens_in else x.listed)
    return [gamelang.shown(x.name) + (" (%s)" % x.quality if x.equip and x.quality else ""), money(x.qty),
            money(x.price), money(x.total), "" if x.seller is None else str(x.seller),
            market.category_name(x.cat), str(x.page), when]


def buy_summary(orders: Sequence[Order]) -> tuple[int, int]:
    """(items, gold coins) the orders come to."""
    return sum(o.qty for o in orders), sum(o.qty * o.lot.price for o in orders)


def load_saved() -> dict[str, Any]:
    data = market.read_file(market.SAVED_FILE)
    items: list[Any] = data.get("items") or []
    sellers: list[Any] = data.get("sellers") or []
    return {"items": [i for i in items if json.is_object(i) and i.get("name")],
            "sellers": [s for s in sellers if json.is_object(s) and isinstance(s.get("id"), int)]}


def remember_items(saved: dict[str, Any], names: Sequence[str], max_price: int = 0) -> None:
    """Newest first, each name once (case set aside), at most SAVED_MOST."""
    items: list[dict[str, Any]] = saved["items"]
    for name in names:
        old = next((i for i in items if str(i["name"]).casefold() == name.casefold()), None)
        if old is not None:
            items.remove(old)
        entry: dict[str, Any] = {"name": name}
        keep = max_price or (int(old.get("max") or 0) if old else 0)
        if keep:
            entry["max"] = keep
        items.insert(0, entry)
    del items[SAVED_MOST:]


def remember_sellers(saved: dict[str, Any], ids: Sequence[int]) -> None:
    sellers: list[dict[str, Any]] = saved["sellers"]
    for sid in ids:
        old = next((s for s in sellers if s["id"] == sid), None)
        if old is not None:
            sellers.remove(old)
        sellers.insert(0, old or {"id": sid, "note": ""})
    del sellers[SAVED_MOST:]


class MarketFrame(wx.Frame):
    """host: the main window (run_errand, stop, the selected game)."""

    def __init__(self, host: FrameParts, pid: int, character: str | None) -> None:
        super().__init__(host, title=_("Market searcher and buyer - {name}", name=character or _("game")),
                         size=wx.Size(900, 760))
        self.host, self.pid = host, pid
        self.server = self._server()
        self.rows: list[ListRow] = []
        self.head: dict[str, Any] = {}
        self.shown_rows: list[ListRow] = []
        self.lots: list[Lot] = []
        self.qty: dict[int, int] = {}            # listing id -> how many to buy
        self.ticked: set[int] = set()
        self.saved = load_saved()
        self.status_seen = 0.0
        self.job = ""                            # the job this window started and waits on

        panel = wx.Panel(self)
        box = wx.BoxSizer(wx.VERTICAL)
        self.category = labeled(panel, box, _("&Category:"), lambda w: wx.Choice(w))
        self.items = labeled(panel, box, _("&Items for sale (Enter searches):"),
                             lambda w: wx.ListCtrl(w, style=wx.LC_REPORT | wx.LC_SINGLE_SEL), 2)
        for i, (title, width) in enumerate([(_("Item"), 260), (_("Lots"), 120), (_("In all"), 80),
                                            (_("Cheapest"), 130), (_("Usual"), 90), (_("Sellers"), 70)]):
            self.items.InsertColumn(i, title, width=width)
        self.list_age = labeled(panel, box, _("Market list:"),
                                lambda w: wx.TextCtrl(w, style=wx.TE_READONLY | wx.TE_MULTILINE | wx.TE_NO_VSCROLL,
                                                      size=wx.Size(-1, 40)))
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh = wx.Button(panel, label=_("&Refresh the market list"))
        self.count_sellers = wx.CheckBox(panel, label=_("Also count se&llers (slow the first time)"))
        row.Add(self.refresh, 0, wx.RIGHT, 12)
        row.Add(self.count_sellers, 0, wx.ALIGN_CENTER_VERTICAL)
        box.Add(row, 0, wx.TOP | wx.BOTTOM, 6)

        self.kind = wx.RadioBox(panel, label=_("Search &kind"), choices=kind_names(), majorDimension=4)
        box.Add(self.kind, 0, wx.EXPAND | wx.TOP, 4)
        self.custom = labeled(panel, box, _("C&ustom name (several: a | b):"), lambda w: wx.TextCtrl(w))
        self.seller_ids = labeled(panel, box, _("Seller &IDs (several: 1234, 5678):"),
                                  lambda w: wx.ComboBox(w, style=wx.CB_DROPDOWN))
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.max_price = labeled(panel, row, _("&Most a unit (0 = any):"),
                                 lambda w: wx.SpinCtrl(w, min=0, max=2000000000, initial=0))
        self.pages = labeled(panel, row, _("&Pages at most (0 = all):"),
                             lambda w: wx.SpinCtrl(w, min=0, max=500, initial=0))
        box.Add(row, 0, wx.TOP, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.search_btn = wx.Button(panel, label=_("&Search"))
        self.stop_btn = wx.Button(panel, label=_("S&top"))
        self.remember_btn = wx.Button(panel, label=_("Remem&ber this item"))
        for b in (self.search_btn, self.stop_btn, self.remember_btn):
            row.Add(b, 0, wx.RIGHT, 8)
        box.Add(row, 0, wx.TOP | wx.BOTTOM, 6)
        self.progress = labeled(panel, box, _("Pro&gress:"),
                                lambda w: wx.TextCtrl(w, style=wx.TE_READONLY | wx.TE_MULTILINE, size=wx.Size(-1, 70)))
        self.results = labeled(panel, box, _("&Results (Space ticks a lot to buy):"),
                               lambda w: wx.ListCtrl(w, style=wx.LC_REPORT | wx.LC_SINGLE_SEL), 3)
        self.results.EnableCheckBoxes(True)
        for i, (title, width) in enumerate([(_("Item"), 230), (_("Quantity"), 70), (_("Unit price"), 90),
                                            (_("Total"), 100), (_("Seller ID"), 80), (_("Category"), 110),
                                            (_("Page"), 50), (_("Listed"), 150)]):
            self.results.InsertColumn(i, title, width=width)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.buy_qty = labeled(panel, row, _("How &many of the selected lot:"),
                               lambda w: wx.SpinCtrl(w, min=1, max=1000000, initial=1))
        self.cap = labeled(panel, row, _("Spending &limit (0 = none):"),
                           lambda w: wx.SpinCtrl(w, min=0, max=2000000000, initial=0))
        box.Add(row, 0, wx.TOP, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.buy_btn = wx.Button(panel, label=_("Bu&y the ticked lots"))
        self.check_btn = wx.Button(panel, label=_("Check o&nly"))
        self.csv_btn = wx.Button(panel, label=_("Save as CS&V"))
        close = wx.Button(panel, label=_("Close"))
        for b in (self.buy_btn, self.check_btn, self.csv_btn, close):
            row.Add(b, 0, wx.RIGHT, 8)
        box.Add(row, 0, wx.TOP, 6)
        outer = wx.BoxSizer(wx.VERTICAL)
        outer.Add(box, 1, wx.EXPAND | wx.ALL, 10)
        panel.SetSizer(outer)

        self.category.Bind(wx.EVT_CHOICE, lambda e: self.show_items())
        self.items.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self.search_item())
        self.refresh.Bind(wx.EVT_BUTTON, lambda e: self.start_scan())
        self.kind.Bind(wx.EVT_RADIOBOX, lambda e: self.sync_kind())
        self.search_btn.Bind(wx.EVT_BUTTON, lambda e: self.start_search())
        self.stop_btn.Bind(wx.EVT_BUTTON, lambda e: self.host.stop(self.pid))
        self.remember_btn.Bind(wx.EVT_BUTTON, lambda e: self.remember())
        self.results.Bind(wx.EVT_LIST_ITEM_CHECKED, self.on_check)
        self.results.Bind(wx.EVT_LIST_ITEM_UNCHECKED, self.on_check)
        self.results.Bind(wx.EVT_LIST_ITEM_SELECTED, lambda e: self.show_qty())
        self.buy_qty.Bind(wx.EVT_SPINCTRL, lambda e: self.set_qty())
        self.buy_btn.Bind(wx.EVT_BUTTON, lambda e: self.start_buy(False))
        self.check_btn.Bind(wx.EVT_BUTTON, lambda e: self.start_buy(True))
        self.csv_btn.Bind(wx.EVT_BUTTON, lambda e: self.save_csv())
        close.Bind(wx.EVT_BUTTON, lambda e: self.Close())
        self.Bind(wx.EVT_CHAR_HOOK, self.on_key)
        self.timer = wx.Timer(self)
        self.Bind(wx.EVT_TIMER, lambda e: self.poll(), self.timer)
        self.Bind(wx.EVT_CLOSE, self.on_close)

        self.load_list()
        self.fill_sellers()
        self.sync_kind()
        self.timer.Start(POLL_MS)
        if self.head.get("shipped") or not self.rows:
            self.progress.SetValue(_("This is the list Uncle Farmer came with. Press Refresh the market list for "
                                     "today's: the bot walks to the nearest marketplace and reads every category "
                                     "(about a minute or two)."))
        self.category.SetFocus()

    # ------------------------------------------------------------ the market list
    def _server(self) -> str:
        try:
            with AgentLink(pid=self.pid, timeout=3) as a:
                return server_name(a)
        except Exception:          # no game reading: the list without a server name
            return ""

    def load_list(self) -> None:
        self.rows, self.head = market.load_list(self.server)
        made = str(self.head.get("made") or "?")
        if self.head.get("shipped"):
            text = _("the list Uncle Farmer came with (from {made}) - press Refresh for today's", made=made)
        elif self.rows:
            text = _("read {made}: {items} items in {lots} lots", made=made, items=len(self.rows),
                     lots=self.head.get("lots", "?"))
            if not self.head.get("sellers_counted"):
                text += " " + _("(sellers not counted)")
        else:
            text = _("no market list yet - press Refresh the market list")
        self.list_age.SetValue(text)
        counts = {c: sum(1 for r in self.rows if r.cat == c) for c, _n in CATEGORIES}
        names = [_("My list ({n})", n=len(self.saved["items"])), _("All items ({n})", n=len(self.rows))]
        names += ["%s (%d)" % (_(n), counts[c]) for c, n in CATEGORIES]
        at = max(0, self.category.GetSelection())
        self.category.Set(names)
        self.category.SetSelection(min(at, len(names) - 1))
        self.show_items()

    def selected_category(self) -> int:
        i = self.category.GetSelection()
        if i <= 0:
            return MY_LIST
        if i == 1:
            return ALL_ITEMS
        return CATEGORIES[i - 2][0]

    def show_items(self) -> None:
        cat = self.selected_category()
        if cat == MY_LIST:
            names = [str(i["name"]) for i in self.saved["items"]]
            rows: list[ListRow] = []
            for name in names:
                hits = [r for r in self.rows if r.label.casefold() == name.casefold()]
                rows.extend(hits or [ListRow(ALL, name)])
        elif cat == ALL_ITEMS:
            rows = list(self.rows)
        else:
            rows = [r for r in self.rows if r.cat == cat]
        self.shown_rows = rows
        self.items.DeleteAllItems()
        for r in rows:
            cells = row_cells(r) if r.lots else [gamelang.shown(r.name), "", "", "", "", ""]
            i = self.items.InsertItem(self.items.GetItemCount(), cells[0])
            for c, text in enumerate(cells[1:], 1):
                self.items.SetItem(i, c, text)
        if rows:
            select_row(self.items, 0)

    def selected_row(self) -> ListRow | None:
        i = first_selected(self.items)
        return self.shown_rows[i] if 0 <= i < len(self.shown_rows) else None

    # ------------------------------------------------------------ starting jobs
    def busy(self) -> bool:
        if self.pid in self.host.runs:
            info(self, _("The bot is busy on this game. Stop it first (Stop), then try again."))
            return True
        return False

    def run(self, line: str, title: str) -> None:
        if self.busy():
            return
        self.job = line.split()[1] if len(line.split()) > 1 else line
        self.status_seen = time.time()
        self.host.run_errand(line + "\n", title)

    def start_scan(self) -> None:
        sellers = self.count_sellers.GetValue()
        self.run("market scan" + (" sellers" if sellers else ""), _("Market list"))

    def sync_kind(self) -> None:
        kind = KINDS[self.kind.GetSelection()]
        self.seller_ids.Enable(kind == "seller")
        self.custom.Enable(kind in ("item", "cheapest"))

    def search_item(self) -> None:
        """Enter on an item: search for it (By item unless another kind is chosen)."""
        r = self.selected_row()
        if r is None:
            return
        if KINDS[self.kind.GetSelection()] not in ("item", "cheapest"):
            self.kind.SetSelection(0)
            self.sync_kind()
        self.custom.SetValue(r.name)
        self.start_search(r)

    def start_search(self, picked: ListRow | None = None) -> None:
        kind = KINDS[self.kind.GetSelection()]
        cat = self.selected_category()
        s = Search(kind=kind, max_price=self.max_price.GetValue(), pages=self.pages.GetValue())
        if kind in ("item", "cheapest"):
            s.names = market.names_of(self.custom.GetValue())
            if not s.names and (r := picked or self.selected_row()) is not None:
                s.names = [r.name]
            if not s.names:
                error(self, _("Pick an item in the list or type a name in Custom name."))
                return
            s.cat = picked.cat if picked is not None and picked.cat != ALL else (cat if cat >= ALL else None)
            remember_items(self.saved, s.names, s.max_price)
        elif kind == "seller":
            s.sellers = market.ids_of(self.seller_ids.GetValue())
            if not s.sellers:
                error(self, _("Type one or more seller IDs."))
                return
            s.cat = cat if cat >= 0 else None
            remember_sellers(self.saved, s.sellers)
            self.fill_sellers()
        else:
            if cat < 0:
                error(self, _("Choose a category to browse."))
                return
            s.cat = cat
        market.write_file(market.SAVED_FILE, self.saved)
        market.write_file(market.job_file(market.REQUEST_FILE, self.pid), s.to_json())
        self.lots, self.ticked, self.qty = [], set(), {}
        self.results.DeleteAllItems()
        self.run("market search", _("Market search"))

    def remember(self) -> None:
        names = market.names_of(self.custom.GetValue())
        r = self.selected_row()
        if not names and r is not None:
            names = [r.label]
        if not names:
            return
        remember_items(self.saved, names, self.max_price.GetValue())
        market.write_file(market.SAVED_FILE, self.saved)
        self.load_list()
        self.progress.SetValue(_("remembered: {names}", names=", ".join(names)))

    def fill_sellers(self) -> None:
        text = self.seller_ids.GetValue()
        self.seller_ids.Set(["%d%s" % (s["id"], " - " + s["note"] if s.get("note") else "") for s in self.saved["sellers"]])
        self.seller_ids.SetValue(text)

    # ------------------------------------------------------------ results and buying
    def on_check(self, evt: wx.ListEvent) -> None:
        i = evt.GetIndex()
        if 0 <= i < len(self.lots):
            lot = self.lots[i]
            if self.results.IsItemChecked(i):
                self.ticked.add(lot.id)
                self.qty.setdefault(lot.id, 1 if lot.equip else lot.qty)
            else:
                self.ticked.discard(lot.id)

    def show_qty(self) -> None:
        i = first_selected(self.results)
        if 0 <= i < len(self.lots):
            lot = self.lots[i]
            self.buy_qty.SetRange(1, max(1, lot.qty))
            self.buy_qty.SetValue(self.qty.get(lot.id, 1 if lot.equip else lot.qty))

    def set_qty(self) -> None:
        i = first_selected(self.results)
        if 0 <= i < len(self.lots):
            self.qty[self.lots[i].id] = self.buy_qty.GetValue()

    def show_results(self, lots: list[Lot]) -> None:
        at = first_selected(self.results)
        self.lots = lots
        self.results.DeleteAllItems()
        for x in lots:
            cells = lot_cells(x)
            i = self.results.InsertItem(self.results.GetItemCount(), cells[0])
            for c, text in enumerate(cells[1:], 1):
                self.results.SetItem(i, c, text)
            if x.id in self.ticked:
                self.results.CheckItem(i, True)
        if 0 <= at < len(lots):
            select_row(self.results, at)

    def orders(self) -> list[Order]:
        cap_each = self.max_price.GetValue()
        return [Order(x, 1 if x.equip else max(1, min(self.qty.get(x.id, x.qty), x.qty)), cap_each)
                for x in self.lots if x.id in self.ticked]

    def start_buy(self, check: bool) -> None:
        orders = self.orders()
        if not orders:
            error(self, _("Tick the lots to buy in Results first (Space)."))
            return
        opening = [o for o in orders if o.lot.opens_in]
        items, gold = buy_summary(orders)
        cap = self.cap.GetValue()
        if not check:
            text = _("Buy {lots} lots, {items} items, {gold} gold coins in all?", lots=len(orders), items=items,
                     gold=money(gold))
            if cap:
                text += "\n" + _("The bot stops buying before it would spend more than {cap}.", cap=money(cap))
            if opening:
                text += "\n" + _("{n} of them are still on display and will be skipped.", n=len(opening))
            if not ask(self, text, _("Market searcher and buyer")):
                return
        market.write_file(market.job_file(market.BUY_FILE, self.pid), {"orders": [o.to_json() for o in orders], "cap": cap})
        self.run("market buy" + (" check" if check else ""), _("Market check") if check else _("Market buying"))

    def save_csv(self) -> None:
        if not self.lots:
            return

        def write(path: str) -> None:
            with open(path, "w", newline="", encoding="utf-8-sig") as f:
                w = csv.writer(f)
                w.writerow([_("Item"), _("Quantity"), _("Unit price"), _("Total"), _("Seller ID"), _("Category"),
                            _("Page"), _("Listed"), _("Listing")])
                for x in self.lots:
                    w.writerow(lot_cells(x) + [str(x.id)])

        with modal(wx.FileDialog(self, _("Save the results"), defaultDir=market.folder(),
                                 defaultFile="market results.csv", wildcard="CSV (*.csv)|*.csv",
                                 style=wx.FD_SAVE | wx.FD_OVERWRITE_PROMPT)) as dlg:
            if dlg.ShowModal() != wx.ID_OK:
                return
            path: str = dlg.GetPath()
        save_file(self, write, path)

    # ------------------------------------------------------------ the job's progress
    def poll(self) -> None:
        name = market.job_file(market.STATUS_FILE, self.pid)
        path = os.path.join(market.folder(), name)
        try:
            changed = os.path.getmtime(path)
        except OSError:
            return
        if changed <= self.status_seen:
            return
        self.status_seen = changed
        data = market.read_file(name)
        if not data or data.get("started", 0) < time.time() - 24 * 3600:
            return
        said: list[object] = data.get("lines") or []
        lines = [str(x) for x in said]
        head = _("{query}: {pages} pages, {lots} lots read, {looked} sellers looked up",
                 query=data.get("query", ""), pages=data.get("pages", 0), lots=data.get("lots_read", 0),
                 looked=data.get("looked_up", 0))
        summary = str(data.get("summary") or "")
        self.progress.SetValue("\n".join([summary or head] + lines[-4:][::-1]))
        job = str(data.get("job") or "")
        if job == "search":
            raw: list[Any] = data.get("lots") or []
            found: list[Lot] = []
            for r in raw:
                lot = Lot.from_json(r) if json.is_object(r) else None
                if lot is not None:
                    found.append(lot)
            search: object = data.get("search")
            s = Search.from_json(search if json.is_object(search) else {})
            if s.kind == "seller":
                found.sort(key=lambda x: (x.seller or 0, x.price, x.id))
            else:
                found.sort(key=lambda x: (x.price, x.id))
            if [x.id for x in found] != [x.id for x in self.lots] or data.get("state") != "reading":
                self.show_results(found)
        if data.get("state") in ("done", "stopped") and job == "scan":
            self.load_list()

    def on_key(self, evt: wx.KeyEvent) -> None:
        if evt.GetKeyCode() == wx.WXK_ESCAPE:
            self.Close()
            return
        evt.Skip()

    def on_close(self, evt: wx.CloseEvent) -> None:
        self.timer.Stop()
        windows: dict[int, MarketFrame] = getattr(self.host, "market_windows", {})
        windows.pop(self.pid, None)
        evt.Skip()
