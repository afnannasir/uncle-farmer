"""Tools, Market searcher and buyer (engine/market.py does the work).

The window stays open beside the game, in two tabs. Search: what to look for (only the boxes the
chosen kind uses are shown), the market list by category, and its Refresh. Results: the finds as the
job reads them (it writes market/status-<pid>.json after every page; a timer here reads it), with
new finds put in at their place so the list never jumps under the screen reader, then buying the
ticked lots, each found again by its listing id. Nothing is bought without Buy and a Yes to its total.
The bot stays at the marketplace after each job, so the next search starts at once.
"""

from __future__ import annotations

import bisect
import csv
import os
import time
from collections.abc import Callable, Sequence
from typing import TYPE_CHECKING, Any

import wx

from .. import gamelang, jsonio as json, speech
from ..engine import market
from ..engine.market import ALL, CATEGORIES, ListRow, Lot, Order, Search
from ..i18n import _
from ..link import AgentLink, server_name
from .common import W, ask, error, first_selected, info, labeled, modal, page, save_file, select_row, tabs

if TYPE_CHECKING:
    from .mainparts.base import FrameParts

KINDS = ("item", "cheapest", "seller", "browse")
MY_LIST, ALL_ITEMS = -2, -3             # the category list's first two entries
SAVED_MOST = 100
POLL_MS = 700
STAY = "end_at here\n"              # market jobs end at the marketplace: no walk back between searches


def kind_names() -> list[str]:
    return [_("By item"), _("Cheapest first"), _("By seller ID"), _("Browse a category")]


# which boxes each search kind uses (the others are hidden, so Tab only meets what matters)
USES = {"item": {"items", "custom", "price"},
        "cheapest": {"items", "custom", "price", "pages"},
        "seller": {"seller"},
        "browse": {"price", "pages"}}


def result_key(lot: Lot, by_seller: bool) -> tuple[int, ...]:
    """Results order: cheapest unit price first (by seller, then price, for a seller search)."""
    return (lot.seller or 0, lot.price, lot.id) if by_seller else (lot.price, lot.id)


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
                         size=wx.Size(820, 640))
        self.host, self.pid = host, pid
        self.server = self._server()
        self.rows: list[ListRow] = []
        self.head: dict[str, Any] = {}
        self.shown_rows: list[ListRow] = []
        self.lots: list[Lot] = []
        self.by_seller = False                   # the results' order (result_key)
        self.qty: dict[int, int] = {}            # listing id -> how many to buy
        self.ticked: set[int] = set()
        self.saved = load_saved()
        self.status_seen = 0.0
        self.job = ""                            # the job this window started and waits on
        self.told = 0.0                          # the start of the last job whose end was said

        root = wx.Panel(self)
        outer = wx.BoxSizer(wx.VERTICAL)
        self.nb = tabs(root)
        self._search_tab()
        self._results_tab()
        outer.Add(self.nb, 1, wx.EXPAND | wx.LEFT | wx.RIGHT | wx.TOP, 8)
        close = wx.Button(root, wx.ID_CLOSE, _("Close"))
        outer.Add(close, 0, wx.ALIGN_RIGHT | wx.ALL, 8)
        root.SetSizer(outer)

        self.kind.Bind(wx.EVT_CHOICE, lambda e: self.sync_kind())
        self.category.Bind(wx.EVT_CHOICE, lambda e: self.show_items())
        self.items.Bind(wx.EVT_LIST_ITEM_ACTIVATED, lambda e: self.search_item())
        self.custom.Bind(wx.EVT_TEXT_ENTER, lambda e: self.start_search())
        self.refresh.Bind(wx.EVT_BUTTON, lambda e: self.start_scan())
        self.search_btn.Bind(wx.EVT_BUTTON, lambda e: self.start_search())
        self.remember_btn.Bind(wx.EVT_BUTTON, lambda e: self.remember())
        self.stop_btn.Bind(wx.EVT_BUTTON, lambda e: self.host.stop(self.pid))
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
        self.kind.SetFocus()

    # ------------------------------------------------------------ the two tabs
    def _part(self, panel: wx.Window, box: wx.BoxSizer, label: str, make: Callable[[wx.Window], W],
              proportion: int = 0) -> tuple[wx.BoxSizer, W]:
        """A label and its control in a sizer of their own, so both hide together."""
        sub = wx.BoxSizer(wx.VERTICAL)
        ctrl = labeled(panel, sub, label, make, proportion)
        box.Add(sub, proportion, wx.EXPAND)
        return sub, ctrl

    def _search_tab(self) -> None:
        panel, box = page(self.nb, _("Search"))
        self.search_panel, self.search_box = panel, box
        self.kind = labeled(panel, box, _("Search &for:"), lambda w: wx.Choice(w, choices=kind_names()))
        self.kind.SetSelection(0)
        self.category = labeled(panel, box, _("&Category:"), lambda w: wx.Choice(w))
        self.items_part, self.items = self._part(
            panel, box, _("&Items for sale (Enter searches):"),
            lambda w: wx.ListCtrl(w, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=wx.Size(-1, 220)), 1)
        for i, (title, width) in enumerate([(_("Item"), 260), (_("Lots"), 110), (_("In all"), 80),
                                            (_("Cheapest"), 120), (_("Usual"), 90), (_("Sellers"), 70)]):
            self.items.InsertColumn(i, title, width=width)
        self.custom_part, self.custom = self._part(panel, box, _("Or type a &name (several: a | b):"),
                                                   lambda w: wx.TextCtrl(w, style=wx.TE_PROCESS_ENTER))
        self.seller_part, self.seller_ids = self._part(panel, box, _("Seller &IDs (several: 1234, 5678):"),
                                                       lambda w: wx.ComboBox(w, style=wx.CB_DROPDOWN))
        self.price_part, self.max_price = self._part(panel, box, _("&Most a unit (0 = any):"),
                                                     lambda w: wx.SpinCtrl(w, min=0, max=2000000000, initial=0))
        self.pages_part, self.pages = self._part(panel, box, _("&Pages at most (0 = all):"),
                                                 lambda w: wx.SpinCtrl(w, min=0, max=500, initial=0))
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.search_btn = wx.Button(panel, label=_("&Search"))
        self.remember_btn = wx.Button(panel, label=_("Remem&ber this item"))
        row.Add(self.search_btn, 0, wx.RIGHT, 8)
        row.Add(self.remember_btn, 0)
        box.Add(row, 0, wx.TOP, 10)
        self.list_age = labeled(panel, box, _("Market list:"), lambda w: wx.TextCtrl(w, style=wx.TE_READONLY))
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.refresh = wx.Button(panel, label=_("&Refresh the market list"))
        self.count_sellers = wx.CheckBox(panel, label=_("Also count se&llers (slow the first time)"))
        row.Add(self.refresh, 0, wx.RIGHT, 12)
        row.Add(self.count_sellers, 0, wx.ALIGN_CENTER_VERTICAL)
        box.Add(row, 0, wx.TOP, 6)

    def _results_tab(self) -> None:
        panel, box = page(self.nb, _("Results"))
        self.progress = labeled(panel, box, _("Sta&tus:"), lambda w: wx.TextCtrl(w, style=wx.TE_READONLY))
        self.progress.SetValue(_("no search yet"))
        self.results = labeled(panel, box, _("&Results (Space ticks a lot to buy):"),
                               lambda w: wx.ListCtrl(w, style=wx.LC_REPORT | wx.LC_SINGLE_SEL, size=wx.Size(-1, 260)), 1)
        self.results.EnableCheckBoxes(True)
        for i, (title, width) in enumerate([(_("Item"), 230), (_("Quantity"), 70), (_("Unit price"), 90),
                                            (_("Total"), 100), (_("Seller ID"), 80), (_("Category"), 110),
                                            (_("Page"), 50), (_("Listed"), 150)]):
            self.results.InsertColumn(i, title, width=width)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.buy_qty = labeled(panel, row, _("How &many of the selected lot:"),
                               lambda w: wx.SpinCtrl(w, min=1, max=1000000, initial=1))
        row.AddSpacer(16)
        self.cap = labeled(panel, row, _("Spending &limit (0 = none):"),
                           lambda w: wx.SpinCtrl(w, min=0, max=2000000000, initial=0))
        box.Add(row, 0, wx.TOP, 4)
        row = wx.BoxSizer(wx.HORIZONTAL)
        self.buy_btn = wx.Button(panel, label=_("Bu&y the ticked lots"))
        self.check_btn = wx.Button(panel, label=_("Check o&nly"))
        self.stop_btn = wx.Button(panel, label=_("St&op"))
        self.csv_btn = wx.Button(panel, label=_("Save as CS&V"))
        for b in (self.buy_btn, self.check_btn, self.stop_btn, self.csv_btn):
            row.Add(b, 0, wx.RIGHT, 8)
        box.Add(row, 0, wx.TOP, 10)

    def kind_now(self) -> str:
        return KINDS[max(0, self.kind.GetSelection())]

    def sync_kind(self) -> None:
        """Show only the boxes the chosen search kind uses."""
        uses = USES[self.kind_now()]
        for name, part in (("items", self.items_part), ("custom", self.custom_part), ("seller", self.seller_part),
                           ("price", self.price_part), ("pages", self.pages_part)):
            self.search_box.Show(part, name in uses, recursive=True)
        self.remember_btn.Show("custom" in uses)
        self.search_box.Layout()
        self.search_panel.Layout()
        self.search_panel.FitInside()

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
        at = self.category.GetSelection()
        self.category.Set(names)
        # the first time: My list when there is one, else All items
        self.category.SetSelection(min(at, len(names) - 1) if at >= 0 else (0 if self.saved["items"] else 1))
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

    def run(self, line: str, title: str) -> bool:
        if self.busy():
            return False
        # this window's game, not the selected one; refused (no character logged in): nothing waited on
        if not self.host.run_errand(STAY + line + "\n", title, pid=self.pid):
            return False
        self.job = line.split()[1] if len(line.split()) > 1 else line
        self.status_seen = time.time()
        return True

    def start_scan(self) -> None:
        sellers = self.count_sellers.GetValue()
        if self.run("market scan" + (" sellers" if sellers else ""), _("Market list")):
            self.list_age.SetValue(_("reading the whole market..."))

    def search_item(self) -> None:
        """Enter on an item: search for it (By item unless Cheapest first is chosen)."""
        r = self.selected_row()
        if r is None:
            return
        if self.kind_now() not in ("item", "cheapest"):
            self.kind.SetSelection(0)
            self.sync_kind()
        self.custom.SetValue("")
        self.start_search(r)

    def start_search(self, picked: ListRow | None = None) -> None:
        kind = self.kind_now()
        cat = self.selected_category()
        uses = USES[kind]
        s = Search(kind=kind, max_price=self.max_price.GetValue() if "price" in uses else 0,
                   pages=self.pages.GetValue() if "pages" in uses else 0)
        if kind in ("item", "cheapest"):
            s.names = [] if picked is not None else market.names_of(self.custom.GetValue())
            if not s.names and (r := picked or self.selected_row()) is not None:
                s.names = [r.name]
                picked = r
            if not s.names:
                error(self, _("Pick an item in the list or type a name."))
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
        if self.busy():
            return
        market.write_file(market.SAVED_FILE, self.saved)
        market.write_file(market.job_file(market.REQUEST_FILE, self.pid), s.to_json())
        if not self.run("market search", _("Market search")):
            return                      # refused: the last results stay as they were
        self.lots, self.ticked, self.qty = [], set(), {}
        self.by_seller = kind == "seller"
        self.results.DeleteAllItems()
        self.progress.SetValue(_("searching: {what}", what=s.describe()))
        self.nb.SetSelection(1)
        self.results.SetFocus()

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
        speech.speak(_("remembered: {names}", names=", ".join(names)))

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

    def _set_cells(self, i: int, x: Lot) -> None:
        for c, text in enumerate(lot_cells(x)):
            self.results.SetItem(i, c, text)

    def show_results(self, lots: list[Lot]) -> None:
        """The whole list again (a new search, or lots gone from it)."""
        at = first_selected(self.results)
        self.lots = sorted(lots, key=lambda x: result_key(x, self.by_seller))
        self.results.DeleteAllItems()
        for x in self.lots:
            i = self.results.InsertItem(self.results.GetItemCount(), "")
            self._set_cells(i, x)
            if x.id in self.ticked:
                self.results.CheckItem(i, True)
        if 0 <= at < len(self.lots):
            select_row(self.results, at)

    def merge_results(self, found: list[Lot]) -> None:
        """New finds go in at their place and changed ones are rewritten; the rows already there stay
        put, so the list never empties under the screen reader while the search goes on."""
        now = {x.id: x for x in found}
        if any(x.id not in now for x in self.lots):
            self.show_results(found)
            return
        for i, x in enumerate(self.lots):
            if now[x.id] != x:
                if result_key(now[x.id], self.by_seller) != result_key(x, self.by_seller):
                    self.show_results(found)      # its place changed (a seller found later)
                    return
                self.lots[i] = now[x.id]
                self._set_cells(i, now[x.id])
        keys = [result_key(x, self.by_seller) for x in self.lots]
        known = {x.id for x in self.lots}
        for x in sorted((x for x in found if x.id not in known), key=lambda x: result_key(x, self.by_seller)):
            key = result_key(x, self.by_seller)
            i = bisect.bisect_right(keys, key)
            keys.insert(i, key)
            self.lots.insert(i, x)
            self.results.InsertItem(i, "")
            self._set_cells(i, x)

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
        if self.run("market buy" + (" check" if check else ""), _("Market check") if check else _("Market buying")):
            self.progress.SetValue(_("checking the ticked lots...") if check else _("buying the ticked lots..."))

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
        try:
            data = market.read_file(name)
        except (OSError, ValueError):
            data = {}
        if not data:
            # caught while the job replaced it (Windows refuses the read then): read it again on the
            # next tick. Never marked seen, or a last page missed this way would leave the results stuck.
            return
        self.status_seen = changed
        if data.get("started", 0) < time.time() - 24 * 3600:
            return
        self.show_status(data)

    def show_status(self, data: dict[str, Any]) -> None:
        said: list[object] = data.get("lines") or []
        state = str(data.get("state") or "")
        job = str(data.get("job") or "")
        summary = str(data.get("summary") or "")
        if not summary:
            summary = _("{query}: {pages} pages, {lots} lots read", query=data.get("query", ""),
                        pages=data.get("pages", 0), lots=data.get("lots_read", 0))
            if data.get("looked_up"):
                summary += ", " + _("{n} sellers looked up", n=data.get("looked_up", 0))
            if said:
                summary += " - " + str(said[-1])
        if job == "scan":
            self.list_age.SetValue(summary)
        else:
            self.progress.SetValue(summary)
        if job == "search":
            raw: list[Any] = data.get("lots") or []
            found = [lot for lot in (Lot.from_json(r) if json.is_object(r) else None for r in raw) if lot is not None]
            search: object = data.get("search")
            by_seller = Search.from_json(search if json.is_object(search) else {}).kind == "seller"
            if by_seller != self.by_seller or not self.lots:
                self.by_seller = by_seller
                self.show_results(found)
            else:
                self.merge_results(found)
        if state in ("done", "stopped"):
            if job == "scan":
                self.load_list()
            started = float(data.get("started") or 0)
            if self.job and started > self.told:
                self.told = started
                self.job = ""
                speech.speak(summary)        # the end of a job started from this window, said once
                if job == "search" and self.lots and first_selected(self.results) < 0:
                    select_row(self.results, 0)

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
