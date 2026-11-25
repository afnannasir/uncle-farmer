"""The Market searcher and buyer window's helpers (gui/market.py): what the lists show, what is
remembered, and what a purchase comes to before the player says yes."""

from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("wx")

from unclefarmer.engine.market import ListRow, Lot, Order  # noqa: E402
from unclefarmer.gui import market as window  # noqa: E402


def test_the_items_list_shows_lots_opening_soon_and_a_price_that_changed() -> None:
    cells = window.row_cells(ListRow(100, "Lesser Blood", lots=12, qty=3400, cheapest=5, usual=10, opening=2, before=8))
    assert cells == ["Lesser Blood", "12 (+2 opening soon)", "3,400", "5 (was 8)", "10", "?"]
    equip = window.row_cells(ListRow(0, "Bow", "perfect", lots=1, qty=1, cheapest=20000, usual=20000, sellers=1))
    assert equip[0] == "Bow (perfect)" and equip[-1] == "1"


def test_a_lot_on_display_says_when_it_opens() -> None:
    cells = window.lot_cells(Lot(7, 100, "Gel essence", 2, 9500, opens_in=600, page=1))
    assert cells[1:4] == ["2", "9,500", "19,000"] and cells[-1] == "opens in 10 min"


def test_the_buy_question_names_the_items_and_the_gold() -> None:
    orders = [Order(Lot(1, 100, "X", 10, 5), 4), Order(Lot(2, 0, "Bow", 1, 50, equip=True), 1)]
    assert window.buy_summary(orders) == (5, 70)


def test_searched_items_are_remembered_newest_first_once_each() -> None:
    saved: dict[str, list[dict[str, object]]] = {"items": [], "sellers": []}
    window.remember_items(saved, ["Lesser Blood"], 10)
    window.remember_items(saved, ["Bow", "lesser blood"])
    assert saved["items"] == [{"name": "lesser blood", "max": 10}, {"name": "Bow"}]
    window.remember_items(saved, ["x%d" % i for i in range(150)])
    assert len(saved["items"]) == window.SAVED_MOST


def test_searched_sellers_are_remembered_with_their_note() -> None:
    saved: dict[str, list[dict[str, object]]] = {"items": [], "sellers": [{"id": 5, "note": "cheap bloods"}]}
    window.remember_sellers(saved, [9, 5])
    assert saved["sellers"] == [{"id": 5, "note": "cheap bloods"}, {"id": 9, "note": ""}]


def _host():  # type: ignore[no-untyped-def]
    import wx
    host = wx.Frame(None)
    host.runs = {}                           # type: ignore[attr-defined]
    host.market_windows = {}                 # type: ignore[attr-defined]
    host.started = []                        # type: ignore[attr-defined]
    host.on_game = []                        # type: ignore[attr-defined]
    host.refuse = False                      # type: ignore[attr-defined]  # as run_errand does at the login screen

    def run_errand(text, title, pid=None):  # type: ignore[no-untyped-def]
        if host.refuse:                      # type: ignore[attr-defined]
            return False
        host.started.append(text)            # type: ignore[attr-defined]
        host.on_game.append(pid)             # type: ignore[attr-defined]
        return True
    host.run_errand = run_errand             # type: ignore[attr-defined]
    host.stop = lambda pid=None: None        # type: ignore[attr-defined]
    return host


def test_the_window_shows_only_the_boxes_a_search_kind_uses_and_stays_at_the_market() -> None:
    import wx
    app = wx.GetApp() or wx.App(False)
    host = _host()
    try:
        w = window.MarketFrame(host, 0, "someone")              # type: ignore[arg-type]
        assert w.category.GetCount() == 2 + len(window.CATEGORIES)
        assert w.nb.GetPageCount() == 2
        assert w.custom.IsShown() and w.items.IsShown() and not w.seller_ids.IsShown() and not w.pages.IsShown()
        w.kind.SetSelection(2)
        w.sync_kind()
        assert w.seller_ids.IsShown() and not w.custom.IsShown() and not w.items.IsShown()
        assert not w.max_price.IsShown()
        w.seller_ids.SetValue("1234")
        w.start_search()
        # the bot stays at the marketplace afterwards, ready for the next search
        assert host.started == ["end_at here\nmarket search\n"]   # type: ignore[attr-defined]
        assert host.on_game == [0]           # type: ignore[attr-defined]  # this window's game, not the selected one
        assert w.nb.GetSelection() == 1
        w.Destroy()
    finally:
        host.Destroy()
        del app


def test_a_refused_search_keeps_the_last_results_and_waits_on_nothing() -> None:
    import wx
    app = wx.GetApp() or wx.App(False)
    host = _host()
    try:
        w = window.MarketFrame(host, 0, "someone")              # type: ignore[arg-type]
        w.show_results([Lot(1, 100, "X", 5, 30)])
        host.refuse = True                                      # type: ignore[attr-defined]
        w.kind.SetSelection(2)
        w.sync_kind()
        w.seller_ids.SetValue("1234")
        w.start_search()
        assert [x.id for x in w.lots] == [1] and w.results.GetItemCount() == 1
        assert w.job == "" and not w.progress.GetValue().startswith("searching")
        w.Destroy()
    finally:
        host.Destroy()
        del app


def test_new_finds_go_in_at_their_place_without_emptying_the_list() -> None:
    import wx
    app = wx.GetApp() or wx.App(False)
    host = _host()
    try:
        w = window.MarketFrame(host, 0, "someone")              # type: ignore[arg-type]
        w.show_results([Lot(1, 100, "X", 5, 30), Lot(2, 100, "X", 5, 10)])
        assert [x.id for x in w.lots] == [2, 1]
        w.results.CheckItem(1, True)                            # lot 1 ticked
        assert w.ticked == {1}
        deleted: list[int] = []
        w.results.Bind(wx.EVT_LIST_DELETE_ALL_ITEMS, lambda e: deleted.append(1))
        w.merge_results([Lot(1, 100, "X", 5, 30), Lot(2, 100, "X", 4, 10), Lot(3, 100, "X", 1, 20)])
        assert not deleted
        assert [x.id for x in w.lots] == [2, 3, 1]
        assert w.results.GetItemText(0, 1) == "4"               # the quantity that changed is rewritten
        assert w.results.IsItemChecked(2) and not w.results.IsItemChecked(1)
        assert [o.lot.id for o in w.orders()] == [1]
        w.Destroy()
    finally:
        host.Destroy()
        del app


def test_a_status_caught_while_being_written_is_read_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import os
    import time

    import wx

    from unclefarmer.engine import market
    app = wx.GetApp() or wx.App(False)
    host = _host()
    monkeypatch.setattr(market, "folder", lambda: str(tmp_path))
    try:
        w = window.MarketFrame(host, 0, "someone")              # type: ignore[arg-type]
        w.job, w.status_seen = "search", time.time() - 5
        path = tmp_path / market.job_file(market.STATUS_FILE, 0)
        path.write_text("{", encoding="utf-8")                  # half written
        w.poll()
        assert w.lots == []
        market.write_file(market.job_file(market.STATUS_FILE, 0), {
            "job": "search", "state": "done", "started": time.time(), "summary": "1 lot found",
            "lots": [Lot(9, 100, "X", 2, 50).to_json()], "search": {"kind": "item"}})
        os.utime(path, None)
        w.poll()
        assert [x.id for x in w.lots] == [9] and w.progress.GetValue() == "1 lot found"
        assert w.job == ""                                      # said once
        w.Destroy()
    finally:
        host.Destroy()
        del app
