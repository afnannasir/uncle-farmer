"""The Market searcher and buyer (engine/market.py) against a pretend marketplace.

The pretend market answers exactly as the recorded visits showed (docs/MARKET_TOOL_DESIGN.md,
section 1b): a menu per page whose entries carry their commands, 20 lots a page, the order switch
keeping the page, "Jump to..." with a number box, details as spoken text, an item bought through the
quantity box with no yes/no, equipment through a yes/no naming the price, and "successfully
purchased" spoken after.
"""

from __future__ import annotations

import re
from typing import Any, cast

import pytest

from unclefarmer.engine import market
from unclefarmer.engine.botparts.base import Stopped
from unclefarmer.engine.market import ALL, CHRONO, PRICE, ListRow, Lot, Order, Search

# ---------------------------------------------------------------- recorded rows
ITEM_ROW = {"flags": 73, "cmd": [3, 261429, 0], "f": "Please enter the purchase quantity",
            "text": "4 Insulating glue;Unit price 15000 gold coins;Sale time Nov 18, 04:58, 2026"}
NEW_ROW = {"flags": 73, "cmd": [3, 261463, 0], "f": "Please enter the purchase quantity",
           "text": "New; 46 Green Shipwreck Chest;Unit price 350 gold coins;58 minutes and 19 seconds remaining "
                   "in the display time"}
EQUIP_ROW = {"flags": 74, "cmd": [3, 261290], "f": "Purchase requires 2000 gold coins, are you sure you want to buy?",
             "text": "Scepter of Mercy;sophisticated ;Price: 2000 gold coins;Sale time Nov 17, 22:32, 2026"}


def test_an_item_row_reads_its_listing_id_quantity_name_and_unit_price() -> None:
    lot = market.parse_lot(ITEM_ROW, cat=100, page=3)
    assert lot is not None
    assert (lot.id, lot.qty, lot.name, lot.price, lot.total) == (261429, 4, "Insulating glue", 15000, 60000)
    assert (lot.cat, lot.page, lot.equip, lot.opens_in, lot.listed) == (100, 3, False, 0, "Nov 18, 04:58, 2026")


def test_a_new_row_is_still_on_display_for_the_time_it_says() -> None:
    lot = market.parse_lot(NEW_ROW)
    assert lot is not None and lot.qty == 46 and lot.name == "Green Shipwreck Chest"
    assert lot.opens_in == 58 * 60 + 19 and lot.listed == ""
    seconds = market.parse_lot({**NEW_ROW, "text": "New; 2 Gel essence;Unit price 9500 gold coins;"
                                                   "16 seconds remaining in the display time"})
    assert seconds is not None and seconds.opens_in == 16


def test_an_equipment_row_is_one_piece_with_its_quality() -> None:
    lot = market.parse_lot(EQUIP_ROW, cat=0)
    assert lot is not None
    assert (lot.id, lot.equip, lot.qty, lot.name, lot.quality, lot.price) == (
        261290, True, 1, "Scepter of Mercy", "sophisticated", 2000)
    assert lot.label == "Scepter of Mercy (sophisticated)"
    plain = market.parse_lot({**EQUIP_ROW, "text": "Odd Ring;Price: 50 gold coins;Sale time Nov 17, 22:32, 2026"})
    assert plain is not None and plain.name == "Odd Ring" and plain.quality == "" and plain.price == 50


def test_entries_that_are_not_lots_are_not_read_as_lots() -> None:
    assert market.parse_lot({"flags": 12, "cmd": [2, 100, 0, 2], "text": "Next page: 2"}) is None
    assert market.parse_lot({"flags": 8, "cmd": [1], "text": "Materials; Press to select category"}) is None
    assert market.parse_lot({"flags": 73, "cmd": [3, 5, 0], "text": "something unreadable"}) is None
    assert market.parse_lot({"flags": 73, "cmd": [3, True, 0], "text": ITEM_ROW["text"]}) is None


def test_a_server_menus_entry_has_its_words_in_options() -> None:
    lot = market.parse_lot({"flags": 73, "cmd": [3, 261429, 0], "raw": None, "text": None,
                            "options": [ITEM_ROW["text"]], "f": "Please enter the purchase quantity"})
    assert lot is not None and (lot.id, lot.qty, lot.price) == (261429, 4, 15000)


def test_a_lot_survives_the_round_trip_through_its_file() -> None:
    lot = market.parse_lot(EQUIP_ROW, cat=0, page=2, order=PRICE)
    assert lot is not None
    lot.seller = 4242
    again = Lot.from_json(lot.to_json())
    assert again == lot


# ---------------------------------------------------------------- the real recorded menus
def recorded(name: str) -> dict[str, Any]:
    import json
    import os
    path = os.path.join(os.path.dirname(__file__), "data", "market_menus.json")
    with open(path, encoding="utf-8") as f:
        menus: dict[str, Any] = json.load(f)["menus"]
    return menus[name]


def test_the_recorded_home_and_category_list_are_known() -> None:
    assert market.is_home(recorded("home")) and market.read_page(recorded("home")) is None
    cats = recorded("categories")
    assert market.is_category_list(cats) and market.read_page(cats) is None
    ids = [e["cmd"][1] for (e,) in cats["rows"] if e.get("cmd")]
    assert ids == [ALL] + [c for c, _n in market.CATEGORIES]


@pytest.mark.parametrize("name, cat, order, page, last, lots, first_name, first_price", [
    ("all goods page 1", ALL, CHRONO, 1, 74, 20, "Green Shipwreck Chest", 350),
    ("materials page 25", 100, CHRONO, 25, 25, 12, "Higher blood", 6000),
    ("materials price order page 25", 100, PRICE, 25, 25, 12, "Resurrection Crystal", 300000),
    ("weapons page 1", 0, CHRONO, 1, 2, 20, "Scepter of Mercy", 2000),
])
def test_every_recorded_page_reads_whole(name: str, cat: int, order: int, page: int, last: int, lots: int,
                                         first_name: str, first_price: int) -> None:
    pg = market.read_page(recorded(name))
    assert pg is not None and pg.unread == 0
    assert (pg.cat, pg.order, pg.page, pg.last, len(pg.lots)) == (cat, order, page, last, lots)
    assert (pg.lots[0].name, pg.lots[0].price) == (first_name, first_price)
    assert len(set(pg.lot_at)) == lots


def test_the_recorded_price_order_is_cheapest_first() -> None:
    pg = market.read_page(recorded("materials price order page 25"))
    assert pg is not None
    assert [x.price for x in pg.lots] == sorted(x.price for x in pg.lots)


def test_the_recorded_weapons_are_single_pieces_with_a_quality() -> None:
    pg = market.read_page(recorded("weapons page 1"))
    assert pg is not None and all(x.equip and x.qty == 1 and x.quality for x in pg.lots)
    assert pg.lots[0].label == "Scepter of Mercy (sophisticated)"


def test_the_recorded_new_lots_are_still_on_display() -> None:
    pg = market.read_page(recorded("all goods page 1"))
    assert pg is not None and all(x.opens_in > 0 for x in pg.lots)       # that page was all fresh listings


# ---------------------------------------------------------------- the pretend market
def row(name: str, qty: int, price: int, lot_id: int, new: bool = False, equip: str = "") -> dict[str, Any]:
    """A lot as the agent's `dialog` reads an NPC menu's entry: its words in options, no text."""
    if equip:
        text = "%s;%s ;Price: %d gold coins;Sale time Nov 17, 22:32, 2026" % (name, equip, price)
        return {"flags": 74, "cmd": [3, lot_id], "raw": None, "text": None, "options": [("New; " if new else "") + text],
                "f": "Purchase requires %d gold coins, are you sure you want to buy?" % price}
    tail = "50 minutes and 1 seconds remaining in the display time" if new else "Sale time Nov 18, 04:58, 2026"
    return {"flags": 73, "cmd": [3, lot_id, 0], "f": "Please enter the purchase quantity", "raw": None, "text": None,
            "options": ["%s%d %s;Unit price %d gold coins;%s" % ("New; " if new else "", qty, name, price, tail)]}


class Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        return self.now


class PretendMarket:
    """The marketplace's menus as the game shows them, and the agent commands the tool sends."""

    def __init__(self, lots: dict[int, list[dict[str, Any]]], sellers: dict[int, int] | None = None) -> None:
        self.lots = lots                    # category -> lots, newest first: {id, name, qty, price, equip, new}
        self.sellers = sellers or {}
        self.screen: tuple[Any, ...] = ("home",)
        self.input: tuple[Any, ...] | None = None
        self.said: list[tuple[int, str]] = []
        self.ms = 0
        self.details = 0
        self.pages_shown = 0
        self.refuse: str | None = None      # the game's words instead of a purchase
        self.ask_price: int | None = None   # an equipment question naming another price
        self.bought: list[tuple[int, int]] = []

    # -- what the menus hold
    def ordered(self, cat: int, order: int) -> list[dict[str, Any]]:
        every = [x for xs in self.lots.values() for x in xs] if cat == ALL else list(self.lots.get(cat, []))
        return sorted(every, key=lambda x: x["price"]) if order == PRICE else every

    def last(self, cat: int, order: int) -> int:
        return max(1, (len(self.ordered(cat, order)) + 19) // 20)

    def top(self) -> dict[str, Any] | None:
        kind = self.screen[0]
        if kind == "home":
            rows = [{"flags": 12, "cmd": [2, -1, 0, 1], "text": "buy goods"}, {"flags": 8, "cmd": [4], "text": "sell goods"},
                    {"flags": 12, "cmd": [8], "text": "the goods I'm selling now"},
                    {"flags": 8, "cmd": [10], "text": "trading description"}]
        elif kind == "cats":
            rows = [{"flags": 0, "text": "please choose"}, {"flags": 12, "cmd": [2, -1, 0, 1], "text": "all goods"}]
            rows += [{"flags": 12, "cmd": [2, c, 0, 1], "text": n} for c, n in market.CATEGORIES]
        elif kind == "confirm":
            lot = self.screen[1]
            asked = self.ask_price if self.ask_price is not None else lot["price"]
            rows = [{"flags": 0, "text": "Purchase requires %d gold coins, are you sure you want to buy?" % asked},
                    {"flags": 12, "text": "yes"}, {"flags": 12, "text": "no"}]
        else:
            _k, cat, order, page = self.screen
            chrono = ({"flags": 0, "text": "in chronological order; Selected"} if order == CHRONO else
                      {"flags": 12, "cmd": [2, cat, CHRONO, page], "text": "in chronological order"})
            price = ({"flags": 0, "text": "in price order; Selected"} if order == PRICE else
                     {"flags": 12, "cmd": [2, cat, PRICE, page], "text": "in price order"})
            rows = [{"flags": 8, "cmd": [1], "text": "Category; Press to select category"}, chrono, price]
            chunk = self.ordered(cat, order)[(page - 1) * 20:page * 20]
            rows += [row(x["name"], x["qty"], x["price"], x["id"], x.get("new", False), x.get("equip", ""))
                     for x in chunk]
            last = self.last(cat, order)
            if page > 1:
                rows.append({"flags": 12, "cmd": [2, cat, order, 1], "text": "page 1"})
            if page > 2:
                rows.append({"flags": 12, "cmd": [2, cat, order, page - 1], "text": "Previous page: %d" % (page - 1)})
            if page < last:
                rows.append({"flags": 12, "cmd": [2, cat, order, page + 1], "text": "Next page: %d" % (page + 1)})
            if last > 2 and page < last - 1:
                rows.append({"flags": 12, "cmd": [2, cat, order, last], "text": "Last page: %d" % last})
            rows.append({"flags": 13, "cmd": [2, cat, order, 0], "f": "Please enter the number of pages", "text": "Jump to..."})
        grid = [[r] for r in rows]
        return {"kind": 0, "row": 0, "col": 0, "sig": hash(repr(grid)) & 0x7FFFFFFF, "rows": grid}

    # -- what the tool sends
    def say(self, text: str) -> None:
        self.ms += 1
        self.said.append((self.ms, text))

    def find(self, lot_id: int) -> dict[str, Any] | None:
        return next((x for xs in self.lots.values() for x in xs if x["id"] == lot_id), None)

    def entry(self, r: int, c: int, sig: int) -> dict[str, Any] | str:
        top = self.top()
        if top is None or top["sig"] != sig:
            return "refused: the menu changed since it was read"
        return top["rows"][r][c]

    def choose(self, r: int, c: int, sig: int) -> str:
        e = self.entry(r, c, sig)
        if isinstance(e, str):
            return e
        cmd = tuple(e.get("cmd") or ())
        if cmd == (1,):
            self.screen = ("cats",)
        elif len(cmd) == 4 and cmd[0] == 2 and cmd[3] == 0:
            self.input = ("page", cmd[1], cmd[2])
        elif len(cmd) == 4 and cmd[0] == 2:
            self.screen = ("page", cmd[1], cmd[2], cmd[3])
            self.pages_shown += 1
        elif cmd and cmd[0] == 3:
            lot = self.find(cmd[1])
            assert lot is not None
            if len(cmd) == 3:
                self.input = ("lot", lot)
            else:
                self.screen = ("confirm", lot, self.screen)
        elif self.screen[0] == "confirm" and e.get("text") == "yes":
            lot, back = self.screen[1], self.screen[2]
            self.screen = back
            self.purchase(lot, 1)
        return "chosen %d %d" % (r, c)

    def purchase(self, lot: dict[str, Any], n: int) -> None:
        if self.refuse:
            self.say(self.refuse)
            return
        lot["qty"] -= n
        self.bought.append((lot["id"], n))
        if lot["qty"] <= 0:
            for xs in self.lots.values():
                if lot in xs:
                    xs.remove(lot)
        self.say("successfully purchased")

    def number(self, n: int) -> str:
        assert self.input is not None
        what, self.input = self.input, None
        if what[0] == "page":
            self.screen = ("page", what[1], what[2], n)
            self.pages_shown += 1
        else:
            self.purchase(what[1], n)
        return "entered %d" % len(str(n))

    def detail(self, r: int, c: int, sig: int) -> str:
        e = self.entry(r, c, sig)
        if isinstance(e, str):
            return e
        cmd: tuple[int, ...] = tuple(e.get("cmd") or (0, 0))
        self.details += 1
        name = re.sub(r"^(New; )?\d+ ", "", str(e["options"][0]).split(";")[0])
        self.say("1 %s; a thing to sell; Seller ID: %d" % (name, self.sellers.get(cmd[1], 1)))
        return "detailed %d %d" % (r, c)


class Link:
    def __init__(self, m: PretendMarket) -> None:
        self.m = m

    def send(self, line: str) -> str:
        words = line.split()
        if words[0] == "choose":
            return self.m.choose(int(words[1]), int(words[2]), int(words[3]))
        if words[0] == "detail":
            return self.m.detail(int(words[1]), int(words[2]), int(words[3]))
        if words[0] == "number":
            return self.m.number(int(words[1]))
        return "error: " + line

    def json(self, line: str) -> Any:
        words = line.split()
        if words[0] == "said":
            since = int(words[1])
            return {"now": self.m.ms, "said": [{"t": t, "text": s} for t, s in self.m.said if t > since]}
        if words[0] == "input":
            return {"ok": True, "open": self.m.input is not None}
        if words[0] == "server":
            return {"port": 0}
        return {}


class Shopper:
    """Just the bot parts the market uses."""
    agent_version = 71

    def __init__(self, m: PretendMarket) -> None:
        self.m = m
        self.a = Link(m)
        self.clock = Clock()
        self.logged: list[str] = []
        self.p: dict[str, Any] = {}

    def dialog(self) -> dict[str, Any]:
        top = self.m.top()
        return {"ok": True, "open": 1 if top else 0, "top": top}

    def sleep(self, seconds: float) -> None:
        self.clock.now += seconds

    def log(self, text: str, speak: bool = False) -> None:
        self.logged.append(text)

    def close_dialogs(self) -> None:
        if self.m.screen[0] == "confirm":
            self.m.screen = self.m.screen[2]

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
        return [str(e[k]) for k in ("text", "raw", "f") if e.get(k)] + list(e.get("options") or [])

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        for r, cells in enumerate((top or {}).get("rows", [])):
            for c, e in enumerate(cells):
                if e and any(re.search(pattern, t) for t in self.entry_texts(e)):
                    return r, c
        return None

    def choose(self, pattern: str) -> dict[str, Any] | None:
        top = self.m.top()
        at = self.find_entry(top, pattern)
        if top is None or at is None:
            return None
        self.m.choose(at[0], at[1], top["sig"])
        return top["rows"][at[0]][at[1]]


def lots_of(name: str, n: int, start_id: int, price: int = 10, qty: int = 5, **extra: Any) -> list[dict[str, Any]]:
    return [{"id": start_id - i, "name": name, "qty": qty, "price": price + i, **extra} for i in range(n)]


def shopper(lots: dict[int, list[dict[str, Any]]], sellers: dict[int, int] | None = None) -> Shopper:
    return Shopper(PretendMarket(lots, sellers))


# ---------------------------------------------------------------- reading pages
def test_a_page_says_its_category_order_page_and_last_page_from_the_commands() -> None:
    b = shopper({100: lots_of("Lesser Blood", 65, 9000)})
    pg = market.goto(b, 100, CHRONO, 1)  # type: ignore[arg-type]
    assert pg is not None and (pg.cat, pg.order, pg.page, pg.last) == (100, CHRONO, 1, 4)
    assert len(pg.lots) == 20 and pg.lots[0].id == 9000
    third = market.goto(b, 100, CHRONO, 3)  # type: ignore[arg-type]
    assert third is not None and third.page == 3 and third.lots[0].id == 9000 - 40
    last = market.goto(b, 100, CHRONO, 4)  # type: ignore[arg-type]
    assert last is not None and last.page == last.last == 4 and len(last.lots) == 5


def test_the_market_home_and_the_category_list_are_told_apart_from_pages() -> None:
    m = PretendMarket({})
    assert market.is_home(m.top()) and market.read_page(m.top()) is None
    m.screen = ("cats",)
    assert market.is_category_list(m.top()) and not market.is_home(m.top())


def test_goto_switches_category_and_order_and_jumps_to_a_far_page() -> None:
    b = shopper({100: lots_of("Lesser Blood", 30, 9000), 0: lots_of("Bow", 200, 5000, price=100, equip="common")})
    pg = market.goto(b, 0, PRICE, 9)  # type: ignore[arg-type]
    assert pg is not None and (pg.cat, pg.order, pg.page) == (0, PRICE, 9)
    assert [x.price for x in pg.lots] == sorted(x.price for x in pg.lots)
    assert all(x.equip and x.quality == "common" for x in pg.lots)
    assert market.goto(b, 0, PRICE, 99) is None  # type: ignore[arg-type]


def test_a_category_with_one_page_reads_too() -> None:
    b = shopper({101: lots_of("Teleportation stone", 3, 700, price=5000)})
    pages: list[int] = []
    market.read_pages(b, 101, CHRONO, lambda pg: pages.append(pg.page) or True)  # type: ignore[arg-type]
    assert pages == [1]


# ---------------------------------------------------------------- the market list
def test_the_market_list_counts_lots_quantity_and_prices_per_item_and_quality() -> None:
    lots = [Lot(1, 100, "Lesser Blood", 10, 5), Lot(2, 100, "Lesser Blood", 30, 9), Lot(3, 100, "Lesser Blood", 4, 2,
                                                                                        opens_in=600),
            Lot(4, 0, "Bow", 1, 900, equip=True, quality="perfect"), Lot(5, 0, "Bow", 1, 100, equip=True, quality="common")]
    rows = market.build_list(lots, counted_sellers=False, old=[ListRow(100, "Lesser Blood", cheapest=8)])
    bow = [r for r in rows if r.name == "Bow"]
    assert [r.quality for r in bow] == ["common", "perfect"]          # weapons first, then by name and quality
    blood = next(r for r in rows if r.name == "Lesser Blood")
    assert (blood.lots, blood.qty, blood.cheapest, blood.usual, blood.opening) == (3, 44, 5, 7, 1)
    assert blood.before == 8 and blood.sellers is None
    counted = market.build_list([Lot(1, 100, "X", 1, 1, seller=7), Lot(2, 100, "X", 1, 1, seller=7),
                                 Lot(3, 100, "X", 1, 1, seller=8)], counted_sellers=True)
    assert counted[0].sellers == 2


def test_a_full_scan_reads_every_category_and_saves_the_list() -> None:
    b = shopper({100: lots_of("Lesser Blood", 25, 9000) + lots_of("Worthless Blood", 3, 8000, price=5),
                 0: lots_of("Bow", 2, 5000, price=100, equip="perfect")})
    rows = market.scan(b)  # type: ignore[arg-type]
    names = {(r.cat, r.name): r for r in rows}
    assert names[(100, "Lesser Blood")].lots == 25 and names[(100, "Worthless Blood")].cheapest == 5
    assert names[(0, "Bow")].quality == "perfect"
    saved, head = market.load_list("")
    assert len(saved) == 3 and head["lots"] == 30 and not head["shipped"]
    status = market.read_file(market.job_file(market.STATUS_FILE, 0))
    assert status["state"] == "done" and status["pages"] == 1 + len(market.CATEGORIES)   # materials has 2


def test_without_a_list_of_its_own_the_shipped_one_is_used(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    shipped = tmp_path / "market_list.json"
    shipped.write_text('{"made": "2026-11-18", "rows": [{"cat": 100, "name": "Lesser Blood", "lots": 3, "qty": 9,'
                       ' "cheapest": 5, "usual": 6}]}', encoding="utf-8")
    monkeypatch.setattr(market, "SHIPPED_LIST", str(shipped))
    rows, head = market.load_list("S1")
    assert head["shipped"] and rows[0].name == "Lesser Blood" and rows[0].cheapest == 5


# ---------------------------------------------------------------- searching
def test_searching_by_item_reads_its_category_and_keeps_only_matching_lots() -> None:
    b = shopper({100: lots_of("Lesser Blood", 10, 9000) + lots_of("Worthless Blood", 10, 8000)})
    found = market.search(b, Search(names=["lesser blood"], cat=100))  # type: ignore[arg-type]
    assert len(found) == 10 and all(x.name == "Lesser Blood" for x in found)
    assert [x.price for x in found] == sorted(x.price for x in found)


def test_cheapest_first_stops_at_the_first_page_over_the_most_a_unit() -> None:
    b = shopper({100: lots_of("Lesser Blood", 100, 9000, price=1)})       # prices 1..100, five pages
    found = market.search(b, Search(kind="cheapest", names=["Lesser Blood"], cat=100, max_price=30))  # type: ignore[arg-type]
    assert [x.price for x in found] == list(range(1, 31))
    assert market.read_file(market.job_file(market.STATUS_FILE, 0))["pages"] == 3           # page 3 starts at 41: nothing later fits


def test_the_category_comes_from_the_market_list_when_not_chosen() -> None:
    rows = [ListRow(100, "Lesser Blood"), ListRow(0, "Bow", "perfect"), ListRow(0, "Bow", "common")]
    assert market.category_of("lesser blood", rows) == 100
    assert market.category_of("Bow", rows) == 0
    assert market.category_of("Lesser Blood | Bow", rows) is None        # two categories: all goods
    assert market.category_of("nothing", rows) is None


def test_names_and_ids_are_split_the_way_people_type_them() -> None:
    assert market.names_of(" Lesser Blood | worthless blood, Bow ") == ["Lesser Blood", "worthless blood", "Bow"]
    assert market.ids_of("1234, 5678 9") == [1234, 5678, 9]
    assert market.matches(Lot(1, 0, "Savage invader", 1, 1, equip=True, quality="perfect"), ["invader (perfect)"])


def test_a_seller_search_asks_each_lot_once_and_stops_at_ten_lots() -> None:
    lots = lots_of("Lesser Blood", 60, 9000)
    sellers = {x["id"]: 2 for x in lots}
    for x in lots[5:15]:                                    # seller 77's ten lots, on pages 1 and 1-2
        sellers[x["id"]] = 77
    b = shopper({100: lots, 101: lots_of("Stone", 30, 7000)}, sellers)
    found = market.search(b, Search(kind="seller", sellers=[77]))  # type: ignore[arg-type]
    assert len(found) == 10 and {x.seller for x in found} == {77}
    assert b.m.details <= 20                                # stopped once ten were found: page 1 only
    asked = b.m.details
    again = market.search(b, Search(kind="seller", sellers=[77], cat=100))  # type: ignore[arg-type]
    assert len(again) == 10 and b.m.details == asked        # every lot's seller came from the cache


def test_a_seller_with_fewer_lots_is_only_known_after_reading_everything() -> None:
    lots = lots_of("Lesser Blood", 30, 9000)
    sellers = {x["id"]: (5 if x["id"] in (9000, 8975) else 2) for x in lots}
    b = shopper({100: lots}, sellers)
    found = market.search(b, Search(kind="seller", sellers=[5], cat=100))  # type: ignore[arg-type]
    assert sorted(x.id for x in found) == [8975, 9000] and b.m.details == 30


def test_a_seller_search_needs_the_detail_agent() -> None:
    b = shopper({100: lots_of("Lesser Blood", 3, 9000)})
    b.agent_version = 70
    with pytest.raises(Stopped):
        market.search(b, Search(kind="seller", sellers=[5], cat=100))  # type: ignore[arg-type]


# ---------------------------------------------------------------- buying
def first(b: Shopper, cat: int, order: int = CHRONO, page: int = 1) -> list[Lot]:
    pg = market.goto(b, cat, order, page)  # type: ignore[arg-type]
    assert pg is not None
    return pg.lots


def test_an_item_lot_is_bought_through_the_quantity_box() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000, qty=8)})
    lot = first(b, 100)[2]
    done = market.buy(b, [Order(lot, 3)])  # type: ignore[arg-type]
    assert [(o.lot.id, outcome) for o, outcome, _w in done] == [(lot.id, market.BOUGHT)]
    assert b.m.bought == [(lot.id, 3)]
    assert market.read_file(market.job_file(market.STATUS_FILE, 0))["summary"].startswith("bought 1 of 1 lots for %d" % (3 * lot.price))


def test_a_lot_that_moved_pages_is_found_again_by_its_listing_id() -> None:
    b = shopper({100: lots_of("Lesser Blood", 30, 9000)})
    lot = first(b, 100)[19]                                  # the last lot on page 1
    b.m.lots[100][:0] = lots_of("Fresh", 5, 9900)           # five newer lots push it onto page 2
    done = market.buy(b, [Order(lot, 1)])  # type: ignore[arg-type]
    assert done[0][1] == market.BOUGHT and b.m.bought == [(lot.id, 1)]


def test_a_sold_lot_is_gone_and_nothing_else_is_bought() -> None:
    b = shopper({100: lots_of("Lesser Blood", 30, 9000)})
    lot = first(b, 100)[0]
    b.m.lots[100].pop(0)
    done = market.buy(b, [Order(lot, 1)])  # type: ignore[arg-type]
    assert done[0][1] == market.GONE and b.m.bought == []


def test_fewer_left_over_the_most_and_over_the_spending_limit_are_not_bought() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000, qty=4, price=100)})
    lots = first(b, 100)
    done = market.buy(b, [Order(lots[0], 9), Order(lots[1], 1, max_price=50),  # type: ignore[arg-type]
                          Order(lots[2], 4), Order(lots[3], 4)], cap=700)
    assert [outcome for _o, outcome, _w in done] == [market.FEWER, market.OVER, market.BOUGHT, market.CAP]
    assert b.m.bought == [(lots[2].id, 4)]


def test_a_lot_still_on_display_is_never_bought() -> None:
    b = shopper({100: lots_of("Lesser Blood", 3, 9000, new=True)})
    lot = first(b, 100)[0]
    assert lot.opens_in > 0
    done = market.buy(b, [Order(lot, 1)])  # type: ignore[arg-type]
    assert done[0][1] == market.OPENING and b.m.bought == []


def test_check_only_finds_every_lot_and_buys_nothing() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    lots = first(b, 100)
    done = market.buy(b, [Order(lots[0], 2), Order(lots[1], 1)], check=True)  # type: ignore[arg-type]
    assert [outcome for _o, outcome, _w in done] == [market.WOULD, market.WOULD] and b.m.bought == []


def test_equipment_is_bought_with_a_yes_only_to_the_price_read() -> None:
    b = shopper({0: lots_of("Bow", 3, 5000, price=50, equip="common")})
    lots = first(b, 0)
    done = market.buy(b, [Order(lots[0], 1)])  # type: ignore[arg-type]
    assert done[0][1] == market.BOUGHT and b.m.bought == [(lots[0].id, 1)]
    b.m.ask_price = 999
    done = market.buy(b, [Order(lots[1], 1)])  # type: ignore[arg-type]
    assert done[0][1] == market.REFUSED and "another price" in done[0][2] and len(b.m.bought) == 1
    assert b.m.screen[0] == "page"                         # the question was closed, not answered


def test_the_games_own_refusal_is_reported_in_its_words() -> None:
    b = shopper({100: lots_of("Lesser Blood", 3, 9000)})
    b.m.refuse = "Not enough gold coins"
    done = market.buy(b, [Order(first(b, 100)[0], 1)])  # type: ignore[arg-type]
    assert done[0][1] == market.REFUSED and "Not enough gold coins" in done[0][2]


def test_a_scripts_buy_takes_the_cheapest_lots_under_the_most() -> None:
    found = [Lot(1, 100, "X", 5, 30), Lot(2, 100, "X", 5, 10), Lot(3, 100, "X", 5, 20, opens_in=60), Lot(4, 100, "X", 5, 99)]
    orders = market.cheapest_orders(found, 7, max_price=50)
    assert [(o.lot.id, o.qty) for o in orders] == [(2, 5), (1, 2)]


def test_the_script_command_runs_the_windows_files() -> None:
    b = shopper({100: lots_of("Lesser Blood", 5, 9000)})
    market.write_file(market.job_file(market.REQUEST_FILE, 0), Search(names=["Lesser Blood"], cat=100).to_json())
    found = market.run(b, "search", {"what": "search"})  # type: ignore[arg-type]
    assert isinstance(found, list) and len(cast(list[Lot], found)) == 5
    lot = first(b, 100)[0]
    market.write_file(market.job_file(market.BUY_FILE, 0), {"orders": [Order(lot, 1).to_json()], "cap": 0})
    market.run(b, "buy", {"what": "buy"})  # type: ignore[arg-type]
    assert b.m.bought == [(lot.id, 1)]


# ---------------------------------------------------------------- the script command
def test_the_script_command_reads_every_form() -> None:
    from unclefarmer.engine import script
    main, _subs, _stop = script.parse(
        'market scan sellers\n'
        'market search item="Lesser Blood | Worthless Blood" cheapest max=50 category=materials pages=5\n'
        'market seller 1234,5678\n'
        'market buy item="Lesser Blood" count=20 max=10 cap=500 check\n'
        'market buy\n')
    scan, find, seller, buy, window_buy = (n.opts for n in main)
    assert scan == {"what": "scan", "sellers": True}
    assert find["names"] == ["Lesser Blood", "Worthless Blood"] and find["kind"] == "cheapest"
    assert (find["max_price"], find["cat"], find["pages"]) == (50, 100, 5)
    assert seller["sellers"] == [1234, 5678]
    assert (buy["count"], buy["max_price"], buy["cap"], buy["check"]) == (20, 10, 500, True)
    assert window_buy == {"what": "buy"}


@pytest.mark.parametrize("line", ["market", "market sell", "market seller", "market buy item=X",
                                  "market search colour=red", 'market search item="unclosed'])
def test_a_wrong_market_line_is_refused_when_the_script_is_read(line: str) -> None:
    from unclefarmer.engine import script
    with pytest.raises(script.ScriptError):
        script.parse(line + "\n")


def test_a_status_write_refused_for_a_moment_does_not_end_the_search(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []

    def refuse(name: str, data: object) -> None:
        calls.append(name)
        raise PermissionError("held by the window")

    monkeypatch.setattr(market, "write_file", refuse)
    st = market.Status("search", "x")
    st.write()                                   # skipped, tried again on the next page
    assert calls and st.last_write == 0.0
    with pytest.raises(OSError):
        st.end("done", "1 lot found")            # the last one must arrive
