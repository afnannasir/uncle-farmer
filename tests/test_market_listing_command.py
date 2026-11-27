# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""A market lot is chosen by its listing command, read from the menu at the moment of choosing,
and a purchase whose outcome the game doesn't give is settled by the bag or stops the batch: the
bot never buys on, blind, after a purchase that may have gone through (engine/market.py, and Buy
collection items, engine/buycollection.py)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import as_bot, stand_in
from test_market import first, lots_of, row, shopper

from unclefarmer import paths
from unclefarmer.engine import buycollection as bc, market
from unclefarmer.engine.market import CHRONO, Order


@pytest.fixture(autouse=True)
def _market_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "MARKET", str(tmp_path / "market"))


def nowhere(pg: market.Page, lot: market.Lot) -> None:
    """lot_cell when the menu doesn't hold the lot's command exactly once."""
    return None


def page_with(rows: list[dict[str, Any]]) -> dict[str, Any]:
    """A page's menu: the category choice, the order switch, the given lot rows, "Jump to..."."""
    grid = [[{"flags": 8, "cmd": [1], "text": "Category"}],
            [{"flags": 12, "cmd": [2, 100, 1, 1], "text": "in price order"}]]
    grid += [[r] for r in rows]
    grid.append([{"flags": 13, "cmd": [2, 100, CHRONO, 0], "text": "Jump to..."}])
    return {"kind": 0, "row": 0, "col": 0, "sig": 7, "rows": grid}


# ---------------------------------------------------------------- the listing command
def test_a_listing_id_on_two_rows_is_read_as_neither() -> None:
    pg = market.read_page(page_with([row("Lesser Blood", 4, 10, 500), row("Lesser Blood", 9, 12, 500),
                                     row("Gel", 2, 30, 501)]))
    assert pg is not None
    assert [x.id for x in pg.lots] == [501] and pg.unread == 2 and 500 not in pg.lot_at


def test_the_lot_is_found_by_its_exact_command_in_the_menu() -> None:
    pg = market.read_page(page_with([row("Lesser Blood", 4, 10, 500), row("Bow", 1, 50, 502, equip="common")]))
    assert pg is not None
    item, bow = pg.lots
    assert market.lot_command(item) == (3, 500, 0) and market.lot_command(bow) == (3, 502)
    assert market.lot_cell(pg, item) == (2, 0) and market.lot_cell(pg, bow) == (3, 0)


def test_a_lot_in_the_other_shape_or_off_the_page_is_not_found() -> None:
    pg = market.read_page(page_with([row("Lesser Blood", 4, 10, 500)]))
    assert pg is not None
    item = pg.lots[0]
    as_equipment = market.Lot(id=500, cat=100, name="Lesser Blood", qty=1, price=10, equip=True)
    elsewhere = market.Lot(id=999, cat=100, name="Lesser Blood", qty=1, price=10)
    assert market.lot_cell(pg, as_equipment) is None and market.lot_cell(pg, elsewhere) is None
    pg.top["rows"].append([dict(pg.top["rows"][2][0])])    # the same command twice in the menu
    assert market.lot_cell(pg, item) is None
    with pytest.raises(market.NotOnPage):
        market.choose_lot(as_bot(object()), pg, item)


def test_a_lot_whose_command_is_not_on_the_page_is_not_bought(monkeypatch: pytest.MonkeyPatch) -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    monkeypatch.setattr(market, "lot_cell", nowhere)
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.REFUSED, market.REFUSED]
    assert "exactly once" in done[0][2] and b.m.bought == []


# ---------------------------------------------------------------- an outcome the game doesn't give
def test_silence_with_the_lot_in_the_bag_is_bought_and_the_batch_goes_on() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000, price=100)})
    lots = first(b, 100)
    b.m.bag["Lesser Blood"] = 7                              # some were in the bag already
    b.m.silent = True
    done = market.buy(b, [Order(lots[0], 3), Order(lots[1], 2)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.BOUGHT, market.BOUGHT]
    assert "3 came into the bag" in done[0][2] and b.m.bag["Lesser Blood"] == 12
    summary = market.read_file(market.job_file(market.STATUS_FILE, 0))["summary"]
    assert summary.startswith("bought 2 of 2 lots for %d" % (3 * lots[0].price + 2 * lots[1].price))


def test_silence_with_nothing_in_the_bag_stops_the_batch() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.lost = True
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1), Order(lots[2], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.UNKNOWN, market.NOT_TRIED, market.NOT_TRIED]
    assert "look in the bag" in done[0][2] and "may have gone through" in done[1][2]
    assert b.m.pages_shown <= 2                              # the lots after it weren't even looked for
    summary = market.read_file(market.job_file(market.STATUS_FILE, 0))["summary"]
    assert "bought 0 of 3 lots" in summary and "stopped after Lesser Blood" in summary


def test_a_quantity_box_that_never_opens_stops_the_batch() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.no_box = True
    done = market.buy(b, [Order(lots[0], 2), Order(lots[1], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.UNKNOWN, market.NOT_TRIED]
    assert "quantity box" in done[0][2] and b.m.bought == []


def test_an_unread_bag_never_proves_a_purchase() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.silent = True
    b.m.bag_gone = True                                      # the bag read empty: it isn't
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.UNKNOWN, market.NOT_TRIED]
    assert "couldn't be read" in done[0][2] and len(b.m.bought) == 1


def test_a_refusal_the_game_says_is_sure_and_the_batch_goes_on() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.refuse = "Not enough gold coins"
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.REFUSED, market.REFUSED]


def test_silence_after_the_yes_to_equipment_asks_the_bag_too() -> None:
    b = shopper({0: lots_of("Bow", 3, 5000, price=50, equip="common")})
    lots = first(b, 0)
    b.m.lost = True
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.UNKNOWN, market.NOT_TRIED]


def test_check_only_is_untouched() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.lost = True
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1)], check=True)  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.WOULD, market.WOULD] and b.m.bought == []


# ---------------------------------------------------------------- Buy collection items
def collection_page(b: Any) -> tuple[market.Page, market.Lot]:
    pg = market.goto(b, 102, CHRONO, 1)
    assert pg is not None
    return pg, pg.lots[0]


def test_buy_collection_skips_a_lot_not_on_the_page_once(monkeypatch: pytest.MonkeyPatch) -> None:
    b = shopper({102: lots_of("Milkfish", 3, 7000)})
    pg, lot = collection_page(b)
    monkeypatch.setattr(market, "lot_cell", nowhere)
    outcome, words = bc._buy_lot(b, pg, lot, 1)  # type: ignore[arg-type]
    assert outcome == bc.SKIP and "exactly once" in words and b.m.bought == []


def test_buy_collection_settles_a_quantity_box_that_never_opens_by_the_bag() -> None:
    b = shopper({102: lots_of("Milkfish", 3, 7000)})
    pg, lot = collection_page(b)
    b.m.no_box = True
    outcome, _words = bc._buy_lot(b, pg, lot, 1)  # type: ignore[arg-type]
    assert outcome == bc.LOST                                # not SKIP: the run must not buy on blind


def test_buy_collection_never_counts_a_purchase_on_an_unread_bag(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_buy_collection import Bot, Market, cfg, lot as clot
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(bc.daily, "game_day", stand_in(bc.daily.game_day, lambda now=None: "2026-11-26"))
    m = Market([[clot(1, "Milkfish", 10, 50)]])
    monkeypatch.setattr(bc.market, "goto", stand_in(market.goto, m.goto))

    def quiet(bot: Any, pg: market.Page, x: market.Lot, want: int) -> tuple[str, str]:
        m.buy(bot, pg, x, want)                              # the bag gains them...
        return bc.LOST, "the game said nothing"
    monkeypatch.setattr(bc, "_buy_lot", quiet)
    b = Bot()
    b.__dict__["bag_unread"] = True                          # ...but the bag before was no reading
    assert bc.run(as_bot(b), cfg()) == 0 and bc.bought_today("Hero") == 0
    assert any("look in the bag" in line for line in b.lines)


def test_buy_collection_counts_the_games_word_when_the_bag_before_was_unread(tmp_path: Path,
                                                                             monkeypatch: pytest.MonkeyPatch) -> None:
    from test_buy_collection import Bot, Market, cfg, lot as clot
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(bc.daily, "game_day", stand_in(bc.daily.game_day, lambda now=None: "2026-11-26"))
    m = Market([[clot(1, "Milkfish", 10, 50)]])
    monkeypatch.setattr(bc.market, "goto", stand_in(market.goto, m.goto))
    monkeypatch.setattr(bc, "_buy_lot", m.buy)
    b = Bot()
    b.bag["milkfish"] = 40                                   # already in the bag, and the bag before read as none
    b.__dict__["bag_unread"] = True
    assert bc.run(as_bot(b), cfg()) == 10 and bc.bought_today("Hero") == 10    # not 50


# ---------------------------------------------------------------- "you act too fast"
def test_too_fast_is_tried_again_and_bought() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.too_fast = 1
    start = b.clock.now
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 2)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.BOUGHT, market.BOUGHT]
    assert b.m.bought == [(lots[0].id, 1), (lots[1].id, 2)]
    assert b.clock.now - start >= market.FAST_WAIT
    assert any("act too fast - trying again" in line for line in b.logged)


def test_too_fast_every_time_is_refused_and_the_batch_goes_on() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    b.m.too_fast = market.FAST_TRIES + 1
    done = market.buy(b, [Order(lots[0], 1), Order(lots[1], 1)])  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.REFUSED, market.BOUGHT]
    assert "act too fast" in done[0][2] and b.m.bought == [(lots[1].id, 1)]
    assert sum("trying again" in line for line in b.logged) == market.FAST_TRIES


def test_buy_collection_tries_a_too_fast_lot_again(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_buy_collection import Bot, Market, cfg, lot as clot
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(bc.daily, "game_day", stand_in(bc.daily.game_day, lambda now=None: "2026-11-26"))
    m = Market([[clot(1, "Milkfish", 10, 50)]], {1: [(bc.FAST, "you act too fast")]})
    monkeypatch.setattr(bc.market, "goto", stand_in(market.goto, m.goto))
    monkeypatch.setattr(bc, "_buy_lot", m.buy)
    b = Bot()
    assert bc.run(as_bot(b), cfg()) == 10 and m.bought == [(1, 10)]
    assert any("trying again" in line for line in b.lines)


def test_buy_collection_passes_a_lot_that_is_always_too_fast(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from test_buy_collection import Bot, Market, cfg, lot as clot
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    monkeypatch.setattr(bc.daily, "game_day", stand_in(bc.daily.game_day, lambda now=None: "2026-11-26"))
    fast = [(bc.FAST, "you act too fast")] * (market.FAST_TRIES + 1)
    m = Market([[clot(1, "Milkfish", 10, 50), clot(2, "Milkfish", 5, 60)]], {1: list(fast)})
    monkeypatch.setattr(bc.market, "goto", stand_in(market.goto, m.goto))
    monkeypatch.setattr(bc, "_buy_lot", m.buy)
    b = Bot()
    assert bc.run(as_bot(b), cfg()) == 5 and m.bought == [(2, 5)]


def test_buy_collection_reads_too_fast_from_the_game() -> None:
    b = shopper({102: lots_of("Milkfish", 3, 7000)})
    pg, lot = collection_page(b)
    b.m.too_fast = 1
    outcome, words = bc._buy_lot(b, pg, lot, 1)  # type: ignore[arg-type]
    assert outcome == bc.FAST and "too fast" in words and b.m.bought == []
