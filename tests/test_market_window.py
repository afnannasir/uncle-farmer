"""The Market searcher and buyer window's helpers (gui/market.py): what the lists show, what is
remembered, and what a purchase comes to before the player says yes."""

from __future__ import annotations

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


def test_the_window_opens_with_the_shipped_list_and_closes() -> None:
    import wx
    app = wx.GetApp() or wx.App(False)
    host = wx.Frame(None)
    host.runs = {}                           # type: ignore[attr-defined]
    host.market_windows = {}                 # type: ignore[attr-defined]
    started: list[str] = []
    host.run_errand = lambda text, title: started.append(text)   # type: ignore[attr-defined]
    host.stop = lambda pid=None: None        # type: ignore[attr-defined]
    try:
        w = window.MarketFrame(host, 0, "someone")              # type: ignore[arg-type]
        assert w.category.GetCount() == 2 + len(window.CATEGORIES)
        w.kind.SetSelection(2)
        w.sync_kind()
        assert w.seller_ids.IsEnabled() and not w.custom.IsEnabled()
        w.seller_ids.SetValue("1234")
        w.start_search()
        assert started == ["market search\n"]
        w.Destroy()
    finally:
        host.Destroy()
        del app
