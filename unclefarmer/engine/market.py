"""The player market (the Ansible Space-time Marketplace): reading it, the market list, finding a
seller's lots, and buying the lots the player picked (Tools, Market searcher and buyer).

Everything here was read off two recorded hand visits (docs/MARKET_TOOL_DESIGN.md, section 1b),
not guessed:

- The market is an ordinary NPC menu. Every entry carries the command it sends ("cmd" in the
  agent's `dialog` reading), and the commands say everything the bot needs, in any language:
  `2,<category>,<order>,<page>` opens a page (order 0 newest first, 1 cheapest unit price first),
  `2,<category>,<order>,0` is "Jump to..." (a number box), `1` opens the category list, and a lot
  is `3,<listing id>,0` (an item: a number box asks how many) or `3,<listing id>` (equipment: the
  game asks yes or no). Any page opens with one choice.
- A lot's price belongs to its listing id: a seller who wants another price takes the lot back and
  lists it again under a new id. So a lot found again by its id costs what it cost when read.
- The details key (agent 71 `detail`: the game's own method for G) answers with spoken text,
  "...; Seller ID: <id>", which `said` gives. A seller never changes, so it is kept by listing id.
- A player has at most 10 lots on sale at once (game rule): a seller search is over for an id
  once 10 of its lots are found.

The rows' words come back in English through gamelang (any language pack), but nothing here needs
them except the item name, the quantity and the price: the ids, categories, orders and pages are
all read from the commands.
"""

from __future__ import annotations

import os
import re
import statistics
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from .. import gamelang, jsonio as json, paths
from ..i18n import N_, _
from ..link import server_name
from . import menus, town
from .botparts.base import CONFIRM, STOPPING, Stopped

if TYPE_CHECKING:
    from .bot import Bot

ROLE = "market"                 # knowledge.json's service role of the marketplace in every city
DETAIL_AGENT = 71               # `detail <row> <col> <sig>`
ALL = -1                        # the "all goods" category
CHRONO, PRICE = 0, 1            # the two orders: newest first, cheapest unit price first
# The category list in the game's order, with the ids the menu sends (recorded 2026-11-18)
CATEGORIES: tuple[tuple[int, str], ...] = (
    (0, N_("weapons")), (1, N_("helmets")), (2, N_("hangings")), (3, N_("shawls")), (4, N_("armors")),
    (5, N_("belts")), (6, N_("shoes")), (7, N_("pant legs")), (8, N_("handwears")), (9, N_("rings")),
    (12, N_("other equipments")), (100, N_("materials")), (102, N_("collections")), (101, N_("usable items")))
CATEGORY_IDS = tuple(c for c, _n in CATEGORIES)
EQUIPMENT_CATEGORIES = frozenset(range(0, 13))
MOST_LOTS_A_SELLER = 10         # a player can have at most this many lots on sale at once
DETAILS_FLAG = 0x40             # the entry has details for the G key
PAGE_WAIT = 4.0                 # seconds for the server's next page (measured: 0.25 s)
SAID_WAIT = 2.5                 # seconds for a spoken answer (details: 0.26 s, a purchase: 0.27 s)
POLL = 0.03
BOUGHT_TEXT = re.compile(r"(?i)successfully\s+purchased")
SELLER_TEXT = re.compile(r"(?i)seller\s+id\s*:\s*(\d+)")
KEEP_SELLERS = 20000            # listing ids kept in the seller cache (the newest)

# Row texts (the game's english wording; gamelang turns other packs' rows back into it)
_ITEM = re.compile(r"^\s*(?:New;\s*)?(\d+)\s+(.+?)\s*;\s*Unit price\s+(\d+)\s+gold coins?\s*;\s*(.*)$", re.I | re.S)
_EQUIP = re.compile(r"^\s*(?:New;\s*)?(.+?)\s*;\s*(?:([^;]*?)\s*;\s*)?Price:\s*(\d+)\s+gold coins?\s*;\s*(.*)$",
                    re.I | re.S)
_NEW = re.compile(r"^\s*New;", re.I)
_LEFT = re.compile(r"(?:(\d+)\s+minutes?\s+and\s+)?(\d+)\s+seconds?\s+remaining", re.I)
_SALE_TIME = re.compile(r"Sale time\s+(.+?)\s*$", re.I | re.S)


# ---------------------------------------------------------------- lots and pages (pure)
@dataclass
class Lot:
    id: int                     # the listing id: the lot's identity, in any language
    cat: int                    # the category it was read in (ALL when read from all goods)
    name: str
    qty: int
    price: int                  # a unit's price; equipment: the piece's
    equip: bool = False
    quality: str = ""           # equipment only ("sophisticated", "perfect" ...)
    listed: str = ""            # the sale time as the game wrote it ("Nov 18, 04:58, 2026")
    opens_in: int = 0           # seconds left of its first hour on display; 0 = can be bought
    page: int = 0
    order: int = CHRONO
    seller: int | None = None
    seen: float = 0.0

    @property
    def total(self) -> int:
        return self.qty * self.price

    @property
    def label(self) -> str:
        return "%s (%s)" % (self.name, self.quality) if self.equip and self.quality else self.name

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "cat": self.cat, "name": self.name, "qty": self.qty,
                               "price": self.price, "page": self.page, "order": self.order, "seen": round(self.seen)}
        if self.equip:
            out["equip"] = True
            out["quality"] = self.quality
        if self.listed:
            out["listed"] = self.listed
        if self.opens_in:
            out["opens_in"] = self.opens_in
        if self.seller is not None:
            out["seller"] = self.seller
        return out

    @staticmethod
    def from_json(d: Mapping[str, Any]) -> Lot | None:
        try:
            seller = d.get("seller")
            return Lot(id=int(d["id"]), cat=int(d.get("cat", ALL)), name=str(d["name"]), qty=int(d["qty"]),
                       price=int(d["price"]), equip=bool(d.get("equip")), quality=str(d.get("quality") or ""),
                       listed=str(d.get("listed") or ""), opens_in=int(d.get("opens_in") or 0),
                       page=int(d.get("page") or 0), order=int(d.get("order") or 0),
                       seller=int(seller) if isinstance(seller, int) and not isinstance(seller, bool) else None,
                       seen=float(d.get("seen") or 0.0))
        except (KeyError, TypeError, ValueError):
            return None


def _cmd(e: Mapping[str, Any] | None) -> tuple[int, ...] | None:
    cmd: object = (e or {}).get("cmd")
    if not json.is_array(cmd):
        return None
    out: list[int] = []
    items: list[object] = list(cmd)     # a JSON list of anything
    for x in items:
        if not isinstance(x, int) or isinstance(x, bool):
            return None
        out.append(x)
    return tuple(out)


def _texts(e: Mapping[str, Any]) -> list[str]:
    """Every text of an entry. A server menu's entry has its words in "options" (text and raw are
    empty: the agent's reading of an NPC menu, logs/dialogs.log), its prompt in one of f..i."""
    out: list[str] = []
    options: object = e.get("options")
    values: list[object] = list(options) if json.is_array(options) else []
    values += [e.get(k) for k in ("text", "raw", "f", "g", "h", "i")]
    for v in values:
        if isinstance(v, str) and v.strip():
            out.append(v)
    return out


def _opens_in(text: str) -> int:
    m = _LEFT.search(text)
    if not m:
        return 1 if _NEW.match(text) else 0
    return int(m.group(1) or 0) * 60 + int(m.group(2))


def parse_lot(e: Mapping[str, Any], cat: int = ALL, page: int = 0, order: int = CHRONO,
              now: float = 0.0) -> Lot | None:
    """A lot from one menu entry, or None when the entry isn't one (or its text can't be read)."""
    cmd = _cmd(e)
    if cmd is None or len(cmd) not in (2, 3) or cmd[0] != 3 or cmd[1] <= 0:
        return None
    equip = len(cmd) == 2
    for text in _texts(e):
        m = (_EQUIP if equip else _ITEM).match(text)
        if not m:
            continue
        tail = m.group(4)
        sale = _SALE_TIME.search(tail)
        if equip:
            name, quality, price, qty = m.group(1), (m.group(2) or "").strip(), int(m.group(3)), 1
        else:
            qty, name, price, quality = int(m.group(1)), m.group(2), int(m.group(3)), ""
        return Lot(id=cmd[1], cat=cat, name=" ".join(name.split()), qty=qty, price=price, equip=equip,
                   quality=quality, listed=sale.group(1).strip() if sale else "",
                   opens_in=0 if sale else _opens_in(text), page=page, order=order, seen=now)
    return None


Where = tuple[int, int]


@dataclass
class Page:
    """One page of a category, as the menu shows it."""
    cat: int
    order: int
    page: int
    last: int
    lots: list[Lot]
    unread: int                             # lot entries whose text couldn't be read
    top: dict[str, Any]
    links: dict[tuple[int, ...], Where] = field(default_factory=lambda: {})
    lot_at: dict[int, Where] = field(default_factory=lambda: {})
    jump: Where | None = None
    header: Where | None = None


def _cells(top: Mapping[str, Any]) -> Iterable[tuple[int, int, dict[str, Any]]]:
    rows: list[list[dict[str, Any] | None]] = top.get("rows") or []
    for r, row in enumerate(rows):
        for c, e in enumerate(row):
            if e:
                yield r, c, e


def is_home(top: Mapping[str, Any] | None) -> bool:
    """The marketplace's first menu: buy goods (2,-1,0,1), sell goods (4), my lots (8), the
    trading description (10)."""
    if not top:
        return False
    cmds = {_cmd(e) for _r, _c, e in _cells(top)}
    return (2, ALL, CHRONO, 1) in cmds and (8,) in cmds and (10,) in cmds


def is_category_list(top: Mapping[str, Any] | None) -> bool:
    if not top:
        return False
    cats = {cmd[1] for _r, _c, e in _cells(top) if (cmd := _cmd(e)) and len(cmd) == 4 and cmd[0] == 2
            and cmd[2] == CHRONO and cmd[3] == 1}
    return len(cats) >= 10 and not any((cmd := _cmd(e)) and cmd[0] == 3 for _r, _c, e in _cells(top))


def read_page(top: Mapping[str, Any] | None, now: float = 0.0) -> Page | None:
    """The page the menu shows, or None when it isn't a page of the market's lots."""
    if not top:
        return None
    links: dict[tuple[int, ...], Where] = {}
    jump: Where | None = None
    header: Where | None = None
    lot_entries: list[tuple[int, int, dict[str, Any]]] = []
    for r, c, e in _cells(top):
        cmd = _cmd(e)
        if cmd is None:
            continue
        if cmd == (1,):
            header = (r, c)
        elif cmd[0] == 3:
            lot_entries.append((r, c, e))
        elif len(cmd) == 4 and cmd[0] == 2:
            if cmd[3] == 0:
                jump = (r, c)
            links.setdefault(cmd, (r, c))
    if header is None or (jump is None and not links):
        return None
    if jump is not None:
        jc = _cmd(top["rows"][jump[0]][jump[1]]) or (2, ALL, CHRONO, 0)
        cat, order = jc[1], jc[2]
    else:
        # no "Jump to...": the order switch is the only link (one page): it names the other order
        sw = next(iter(links))
        cat, order = sw[1], 1 - sw[2]
    other = [cmd for cmd in links if cmd[1] == cat and cmd[2] != order]
    page = other[0][3] if other else 1           # the order switch keeps the page
    pages = [cmd[3] for cmd in links if cmd[1] == cat and cmd[2] == order and cmd[3] > 0]
    last = max(pages + [page])
    lots: list[Lot] = []
    lot_at: dict[int, Where] = {}
    unread = 0
    for r, c, e in lot_entries:
        lot = parse_lot(e, cat, page, order, now)
        if lot is None:
            unread += 1
            continue
        lots.append(lot)
        lot_at[lot.id] = (r, c)
    twice = {x.id for x in lots if sum(1 for y in lots if y.id == x.id) > 1}
    if twice:                      # one listing id on two rows: neither can be told apart, so neither is read
        unread += sum(1 for x in lots if x.id in twice)
        lots = [x for x in lots if x.id not in twice]
        for lot_id in twice:
            del lot_at[lot_id]
    return Page(cat, order, page, last, lots, unread, dict(top), links, lot_at, jump, header)


def names_of(text: str) -> list[str]:
    """The names typed in a search box: several joined by | or a comma."""
    return [w.strip() for w in re.split(r"[|,]", text or "") if w.strip()]


def matches(lot: Lot, names: Sequence[str]) -> bool:
    """A lot whose name holds any of the names (case and spacing set aside), in English or as the
    player's language pack shows it."""
    if not names:
        return True
    have = [" ".join(lot.label.split()).casefold(), " ".join(gamelang.shown(lot.name).split()).casefold()]
    return any(" ".join(n.split()).casefold() in h for n in names for h in have)


def ids_of(text: str) -> list[int]:
    return [int(w) for w in re.findall(r"\d+", text or "")]


# ---------------------------------------------------------------- the market list (pure)
@dataclass
class ListRow:
    cat: int
    name: str
    quality: str = ""
    lots: int = 0
    qty: int = 0
    cheapest: int = 0
    usual: int = 0
    opening: int = 0                    # lots still in their display hour
    sellers: int | None = None          # None: not counted
    before: int | None = None           # the cheapest price at the refresh before

    @property
    def label(self) -> str:
        return "%s (%s)" % (self.name, self.quality) if self.quality else self.name

    def to_json(self) -> dict[str, Any]:
        out: dict[str, Any] = {"cat": self.cat, "name": self.name, "lots": self.lots, "qty": self.qty,
                               "cheapest": self.cheapest, "usual": self.usual}
        if self.quality:
            out["quality"] = self.quality
        if self.opening:
            out["opening"] = self.opening
        if self.sellers is not None:
            out["sellers"] = self.sellers
        if self.before is not None:
            out["before"] = self.before
        return out

    @staticmethod
    def from_json(d: Mapping[str, Any]) -> ListRow | None:
        try:
            sellers, before = d.get("sellers"), d.get("before")
            return ListRow(cat=int(d["cat"]), name=str(d["name"]), quality=str(d.get("quality") or ""),
                           lots=int(d.get("lots") or 0), qty=int(d.get("qty") or 0),
                           cheapest=int(d.get("cheapest") or 0), usual=int(d.get("usual") or 0),
                           opening=int(d.get("opening") or 0),
                           sellers=int(sellers) if isinstance(sellers, int) else None,
                           before=int(before) if isinstance(before, int) else None)
        except (KeyError, TypeError, ValueError):
            return None


def build_list(lots: Iterable[Lot], counted_sellers: bool, old: Sequence[ListRow] = ()) -> list[ListRow]:
    """The market list from one full read: one row per category and item (equipment: per quality
    too). counted_sellers: every lot's seller was looked up, so the sellers column means something."""
    groups: dict[tuple[int, str, str], list[Lot]] = {}
    for lot in lots:
        groups.setdefault((lot.cat, lot.name, lot.quality if lot.equip else ""), []).append(lot)
    before = {(r.cat, r.name, r.quality): r.cheapest for r in old}
    out: list[ListRow] = []
    for (cat, name, quality), group in groups.items():
        buyable = [x for x in group if not x.opens_in] or group
        prices = [x.price for x in buyable]
        sellers = {x.seller for x in group if x.seller is not None}
        out.append(ListRow(cat, name, quality, lots=len(group), qty=sum(x.qty for x in group),
                           cheapest=min(prices), usual=int(statistics.median(prices)),
                           opening=sum(1 for x in group if x.opens_in),
                           sellers=len(sellers) if counted_sellers else None,
                           before=before.get((cat, name, quality))))
    out.sort(key=lambda r: (CATEGORY_IDS.index(r.cat) if r.cat in CATEGORY_IDS else 99, r.name.casefold(), r.quality))
    return out


def category_of(name: str, rows: Iterable[ListRow]) -> int | None:
    """The one category the market list has an item of that name in, or None (unknown, or more
    than one: then all goods is read)."""
    names = names_of(name)
    cats = {r.cat for r in rows if any(" ".join(n.split()).casefold() in r.label.casefold() for n in names)}
    return next(iter(cats)) if len(cats) == 1 else None


def category_name(cat: int) -> str:
    if cat == ALL:
        return _("all goods")
    return next((_(n) for c, n in CATEGORIES if c == cat), str(cat))


# ---------------------------------------------------------------- files
def folder() -> str:
    path = paths.MARKET
    os.makedirs(path, exist_ok=True)
    return path


def _server_key(server: str) -> str:
    return paths.safe_name(server or "any")


def list_path(server: str) -> str:
    return os.path.join(folder(), "list-%s.json" % _server_key(server))


SHIPPED_LIST = os.path.join(paths.DATA, "market_list.json")
# One of each per game (its pid), so two games' windows don't read each other's searches
STATUS_FILE = "status"                  # what the window shows while a job runs, and its results
REQUEST_FILE = "request"                # the window's search
BUY_FILE = "buy"                        # the window's ticked lots
SAVED_FILE = "saved.json"               # my list and my sellers (every game)


def job_file(name: str, pid: int) -> str:
    return "%s-%d.json" % (name, pid)


def pid_of(b: Bot) -> int:
    pid: object = getattr(b.a, "pid", 0)
    return pid if isinstance(pid, int) else 0


def load_list(server: str) -> tuple[list[ListRow], dict[str, Any]]:
    """The market list for that server (rows, its header), else the one Uncle Farmer ships."""
    for path in (list_path(server), SHIPPED_LIST):
        data: object = json.read(path, None)
        if json.is_object(data) and json.is_array(data.get("rows")):
            rows = [r for r in (ListRow.from_json(x) for x in data["rows"] if json.is_object(x)) if r is not None]
            head = {k: v for k, v in data.items() if k != "rows"}
            head["shipped"] = path == SHIPPED_LIST
            return rows, head
    return [], {}


def save_list(server: str, rows: Sequence[ListRow], lots: int, sellers: bool) -> None:
    json.save(list_path(server), {"server": server, "made": time.strftime("%Y-%m-%d %H:%M"), "lots": lots,
                                  "sellers_counted": sellers, "rows": [r.to_json() for r in rows]}, indent=1)


def read_file(name: str) -> dict[str, Any]:
    data: object = json.read(os.path.join(folder(), name), None)
    return data if json.is_object(data) else {}


def write_file(name: str, data: Mapping[str, Any]) -> None:
    json.save(os.path.join(folder(), name), dict(data), indent=1)


class SellerCache:
    """listing id -> seller id, for one server: a seller never changes, so each lot is looked up
    once."""

    def __init__(self, server: str) -> None:
        self.path = os.path.join(folder(), "sellers-%s.json" % _server_key(server))
        data: object = json.read(self.path, None)
        raw: dict[str, Any] = data if json.is_object(data) else {}
        self.of: dict[int, int] = {}
        for k, v in raw.items():
            if k.isdigit() and isinstance(v, int) and not isinstance(v, bool):
                self.of[int(k)] = v
        self.dirty = False

    def get(self, lot_id: int) -> int | None:
        return self.of.get(lot_id)

    def put(self, lot_id: int, seller: int) -> None:
        if self.of.get(lot_id) != seller:
            self.of[lot_id] = seller
            self.dirty = True

    def save(self) -> None:
        if not self.dirty:
            return
        keep = sorted(self.of)[-KEEP_SELLERS:]
        json.save(self.path, {str(k): self.of[k] for k in keep}, indent=None)
        self.dirty = False


class Status:
    """status.json: what a job is doing and what it has found so far, written after every page so
    the window can show finds while the search goes on."""

    def __init__(self, job: str, query: str, pid: int = 0) -> None:
        self.file = job_file(STATUS_FILE, pid)
        self.data: dict[str, Any] = {"job": job, "query": query, "state": "reading", "started": time.time(),
                                     "pages": 0, "lots_read": 0, "looked_up": 0, "unread": 0,
                                     "lots": [], "lines": []}
        self.found: dict[int, Lot] = {}
        self.last_write = 0.0

    def add(self, lot: Lot) -> None:
        self.found[lot.id] = lot

    def line(self, text: str) -> None:
        lines: list[str] = self.data["lines"]
        lines.append(text)
        del lines[:-40]

    def write(self, force: bool = False) -> None:
        now = time.time()
        if not force and now - self.last_write < 0.5:
            return
        self.last_write = now
        self.data["updated"] = now
        self.data["lots"] = [x.to_json() for x in self.found.values()]
        try:
            write_file(self.file, self.data)
        except OSError:
            if force:          # the end: the window must get it
                raise
            self.last_write = 0.0      # the window held it a moment: the next page writes it again

    def end(self, state: str, summary: str) -> None:
        self.data["state"] = state
        self.data["summary"] = summary
        self.write(force=True)


# ---------------------------------------------------------------- in the game
def _top(b: Bot) -> dict[str, Any] | None:
    d = b.dialog()
    top: dict[str, Any] | None = d.get("top") if d.get("open") else None
    return top


def _wait_new(b: Bot, before: Mapping[str, Any] | None, want: Callable[[dict[str, Any]], bool],
              seconds: float = PAGE_WAIT) -> dict[str, Any] | None:
    """After a choice: the first menu that isn't `before` and passes want, or None."""
    sig = (before or {}).get("sig")
    end = b.clock.time() + seconds
    while True:
        top = _top(b)
        if top is not None and top.get("sig") != sig and want(top):
            return top
        if b.clock.time() >= end:
            return None
        b.sleep(POLL)


def _choose(b: Bot, top: dict[str, Any], at: Where) -> None:
    if not menus.choose_at(b, top, at[0], at[1]):
        raise Stopped(_("the market can't be used with this agent: restart the game so agent {n} loads",
                        n=DETAIL_AGENT))


def lot_command(lot: Lot) -> tuple[int, ...]:
    """The command the lot's entry sends: (3, id, 0) for an item (a number box comes next), (3, id)
    for equipment (a yes or no comes next)."""
    return (3, lot.id) if lot.equip else (3, lot.id, 0)


def lot_cell(pg: Page, lot: Lot) -> Where | None:
    """Where the page's menu has the lot: the one entry whose command is exactly the lot's, read
    from the menu itself. None when no entry has it, when more than one does (which lot would that
    be?), or when it is there in the other shape (an item read as equipment, or the other way)."""
    want = lot_command(lot)
    cells = [(r, c) for r, c, e in _cells(pg.top) if _cmd(e) == want]
    return cells[0] if len(cells) == 1 else None


class NotOnPage(Exception):
    """The lot's command isn't on the page exactly once: nothing was chosen."""


def choose_lot(b: Bot, pg: Page, lot: Lot) -> None:
    """Choose a lot of the page by its listing command (the game asks how many, or yes or no,
    next). NotOnPage, and nothing chosen, when the menu doesn't hold that command exactly once."""
    at = lot_cell(pg, lot)
    if at is None:
        raise NotOnPage(lot.id)
    _choose(b, pg.top, at)


def open_market(b: Bot) -> dict[str, Any]:
    """The marketplace's first menu, walking to the nearest one when it isn't open."""
    top = _top(b)
    if top is not None and (is_home(top) or read_page(top) or is_category_list(top)):
        return top
    if top is not None:
        b.close_dialogs()
    town.visit(b, ROLE)
    top = _wait_new(b, None, lambda t: is_home(t) or read_page(t) is not None, 5.0)
    if top is None:
        raise Stopped(_("the marketplace didn't open its menu"))
    return top


def goto(b: Bot, cat: int, order: int, page: int) -> Page | None:
    """Open that page of that category in that order, from wherever the market's menus are. None
    when the category has fewer pages."""
    for _step in range(10):
        top = _top(b)
        if top is None or not (is_home(top) or is_category_list(top) or read_page(top)):
            top = open_market(b)
        pg = read_page(top, time.time())
        if pg is None:
            if is_home(top):
                at = next(((r, c) for r, c, e in _cells(top) if _cmd(e) == (2, ALL, CHRONO, 1)), None)
            else:                                          # the category list
                at = next(((r, c) for r, c, e in _cells(top) if _cmd(e) == (2, cat, CHRONO, 1)), None)
            if at is None:
                raise Stopped(_("the market has no category {cat}", cat=category_name(cat)))
            _choose(b, top, at)
            _wait_new(b, top, lambda t: read_page(t) is not None)
            continue
        if pg.cat != cat:
            if pg.header is None:
                raise Stopped(_("the market's category choice isn't on the page"))
            _choose(b, top, pg.header)
            _wait_new(b, top, is_category_list)
            continue
        if pg.order != order:
            switch = pg.links.get((2, cat, order, pg.page))
            if switch is None:
                raise Stopped(_("the market's order switch isn't on the page"))
            _choose(b, top, switch)
            _wait_new(b, top, lambda t: (p := read_page(t)) is not None and p.order == order)
            continue
        if pg.page == page:
            return pg
        if page > pg.last:
            return None
        link = pg.links.get((2, cat, order, page))
        if link is not None:
            _choose(b, top, link)
        elif pg.jump is not None:
            _choose(b, top, pg.jump)
            if not town.type_number(b, page):
                raise Stopped(_("the market didn't take the page number"))
        else:
            return None
        _wait_new(b, top, lambda t: (p := read_page(t)) is not None and p.page == page)
    raise Stopped(_("couldn't get to page {page} of {cat}", page=page, cat=category_name(cat)))


def read_pages(b: Bot, cat: int, order: int, on_page: Callable[[Page], bool], first: int = 1,
               most: int = 0) -> int:
    """Every page of a category from `first` (most: at most that many, 0 = all), handing each to
    on_page, which says whether to go on. The number of pages read."""
    page, read = first, 0
    while True:
        pg = goto(b, cat, order, page)
        if pg is None:
            return read
        read += 1
        if not on_page(pg) or pg.page >= pg.last or (most and read >= most):
            return read
        page = pg.page + 1


def said_mark(b: Bot) -> int:
    try:
        return int(b.a.json("said 0").get("now", 0))
    except STOPPING:
        raise
    except Exception:              # an agent that can't say: nothing heard
        return 0


def said_after(b: Bot, mark: int, want: Callable[[str], bool], seconds: float = SAID_WAIT) -> str | None:
    """The first thing the game says after mark that want takes, or None."""
    end = b.clock.time() + seconds
    while True:
        try:
            lines: list[dict[str, Any]] = b.a.json("said %d" % mark).get("said", [])
        except STOPPING:
            raise
        except Exception:
            lines = []
        for s in lines:
            text = str(s.get("text") or "").strip()
            if text and want(text):
                return text
        if b.clock.time() >= end:
            return None
        b.sleep(POLL)


def seller_of(b: Bot, pg: Page, lot: Lot) -> int | None:
    """Ask the game who sells the lot (its details), or None."""
    if int(getattr(b, "agent_version", 0) or 0) < DETAIL_AGENT:
        raise Stopped(_("finding sellers needs agent {n}: restart the game so it loads", n=DETAIL_AGENT))
    at = lot_cell(pg, lot)
    sig = pg.top.get("sig")
    if at is None or not isinstance(sig, int):
        return None
    mark = said_mark(b)
    answer = b.a.send("detail %d %d %d" % (at[0], at[1], sig))
    if not answer.startswith("detailed"):
        b.log(_("the market's details weren't asked for: {why}", why=answer))
        return None
    text = said_after(b, mark, lambda t: SELLER_TEXT.search(t) is not None)
    m = SELLER_TEXT.search(text or "")
    return int(m.group(1)) if m else None


def _look_up(b: Bot, pg: Page, cache: SellerCache, status: Status, then: Callable[[Lot], bool] | None = None) -> bool:
    """Fill in every lot's seller on the page, from the cache or by asking. then(lot) after each,
    and False from it stops at once. Lots listed in the same minute as a lot `then` liked are asked
    about first (a seller often lists several things at once)."""
    queue = list(pg.lots)
    while queue:
        lot = queue.pop(0)
        seller = cache.get(lot.id)
        if seller is None:
            seller = seller_of(b, pg, lot)
            status.data["looked_up"] += 1
            if seller is not None:
                cache.put(lot.id, seller)
        lot.seller = seller
        if then is not None:
            before = len(status.found)
            if not then(lot):
                return False
            if len(status.found) > before and lot.listed:
                queue.sort(key=lambda x: x.listed != lot.listed)
    return True


# ---------------------------------------------------------------- the jobs
def server_of(b: Bot) -> str:
    return server_name(b.a)


def scan(b: Bot, sellers: bool = False) -> list[ListRow]:
    """Read every category, all pages, into the market list for this server (and every lot's
    seller with sellers=True). The list rows."""
    server = server_of(b)
    status = Status("scan", _("the whole market") + (" + " + _("sellers") if sellers else ""), pid_of(b))
    cache = SellerCache(server)
    lots: list[Lot] = []
    t0 = time.time()
    old, _head = load_list(server)
    try:
        for cat, name in CATEGORIES:
            status.line(_("reading {cat}", cat=_(name)))

            def on_page(pg: Page) -> bool:
                if sellers:
                    _look_up(b, pg, cache, status)
                    cache.save()
                lots.extend(pg.lots)
                status.data["pages"] += 1
                status.data["lots_read"] += len(pg.lots)
                status.data["unread"] += pg.unread
                status.write()
                return True

            read_pages(b, cat, CHRONO, on_page)
    except STOPPING:
        cache.save()
        status.end("stopped", _("stopped after {n} pages", n=status.data["pages"]))
        raise
    rows = build_list(lots, sellers, old)
    save_list(server, rows, len(lots), sellers)
    minutes = (time.time() - t0) / 60
    summary = _("market list made: {items} items in {lots} lots, {pages} pages, {min:.1f} minutes",
                items=len(rows), lots=len(lots), pages=status.data["pages"], min=minutes)
    if status.data["unread"]:
        summary += " " + _("({n} lots couldn't be read: see logs\\dialogs.log)", n=status.data["unread"])
    status.data["rows"] = len(rows)
    status.end("done", summary)
    b.log(summary, speak=True)
    return rows


@dataclass
class Search:
    kind: str = "item"                  # item, cheapest, seller, browse
    names: list[str] = field(default_factory=lambda: [])
    sellers: list[int] = field(default_factory=lambda: [])
    cat: int | None = None              # None: from the market list, else all goods
    max_price: int = 0                  # 0: no limit
    pages: int = 0                      # 0: all

    @staticmethod
    def from_json(d: Mapping[str, Any]) -> Search:
        names: list[Any] = d.get("names") or []
        sellers: list[Any] = d.get("sellers") or []
        cat = d.get("cat")
        return Search(kind=str(d.get("kind") or "item"),
                      names=[str(n) for n in names if str(n).strip()],
                      sellers=[int(s) for s in sellers if isinstance(s, int) and not isinstance(s, bool)],
                      cat=int(cat) if isinstance(cat, int) and not isinstance(cat, bool) else None,
                      max_price=int(d.get("max_price") or 0), pages=int(d.get("pages") or 0))

    def to_json(self) -> dict[str, Any]:
        return {"kind": self.kind, "names": self.names, "sellers": self.sellers, "cat": self.cat,
                "max_price": self.max_price, "pages": self.pages}

    def describe(self) -> str:
        if self.kind == "seller":
            return _("lots of seller {ids}", ids=", ".join(str(s) for s in self.sellers))
        if self.kind == "browse":
            return _("browsing {cat}", cat=category_name(self.cat if self.cat is not None else ALL))
        what = " | ".join(self.names) or _("anything")
        return (_("cheapest {what}", what=what) if self.kind == "cheapest" else what) + (
            " " + _("at most {n} gold coins", n=self.max_price) if self.max_price else "")


def search(b: Bot, s: Search) -> list[Lot]:
    """One search; its finds go to status.json as they come. The lots found."""
    server = server_of(b)
    status = Status("search", s.describe(), pid_of(b))
    status.data["search"] = s.to_json()
    t0 = time.time()
    try:
        found = _search(b, s, server, status)
    except STOPPING:
        status.end("stopped", _("stopped: {n} found so far", n=len(status.found)))
        raise
    minutes = (time.time() - t0) / 60
    if s.kind == "seller":
        per = {sid: sum(1 for x in found if x.seller == sid) for sid in s.sellers}
        summary = "; ".join(_("{id} sells {n} lots", id=sid, n=n) for sid, n in per.items())
    else:
        summary = _("{n} lots found", n=len(found))
    summary += " " + _("({pages} pages, {min:.1f} minutes)", pages=status.data["pages"], min=minutes)
    status.end("done", summary)
    b.log(_("market: {what}: {summary}", what=s.describe(), summary=summary), speak=True)
    return found


def _search(b: Bot, s: Search, server: str, status: Status) -> list[Lot]:
    rows, _head = load_list(server)
    if s.kind == "seller":
        return _seller_search(b, s, server, status)
    cat = s.cat
    if cat is None:
        cat = category_of("|".join(s.names), rows) if s.names else None
        cat = ALL if cat is None else cat
    order = PRICE if s.kind == "cheapest" else CHRONO
    status.line(_("reading {cat}", cat=category_name(cat)))

    def on_page(pg: Page) -> bool:
        status.data["pages"] += 1
        status.data["lots_read"] += len(pg.lots)
        status.data["unread"] += pg.unread
        hits = [x for x in pg.lots if s.kind == "browse" or matches(x, s.names)]
        if s.max_price:
            hits = [x for x in hits if x.price <= s.max_price]
        for x in hits:
            status.add(x)
        status.write()
        if s.kind == "cheapest" and s.max_price and pg.lots and min(x.price for x in pg.lots) > s.max_price:
            return False               # cheapest first: nothing later can be under the limit
        return True

    read_pages(b, cat, order, on_page, most=s.pages)
    return sorted(status.found.values(), key=lambda x: (x.price, x.id))


def _seller_search(b: Bot, s: Search, server: str, status: Status) -> list[Lot]:
    cache = SellerCache(server)
    want = set(s.sellers)
    if not want:
        raise Stopped(_("which seller? type a seller ID"))
    cats = [s.cat] if s.cat is not None and s.cat != ALL else list(CATEGORY_IDS)

    def complete() -> bool:
        return all(sum(1 for x in status.found.values() if x.seller == sid) >= MOST_LOTS_A_SELLER for sid in want)

    def then(lot: Lot) -> bool:
        if lot.seller in want:
            status.add(lot)
            status.write()
        return not complete()

    try:
        for cat in cats:
            status.line(_("reading {cat}", cat=category_name(cat)))
            going = True

            def on_page(pg: Page) -> bool:
                nonlocal going
                status.data["pages"] += 1
                status.data["lots_read"] += len(pg.lots)
                status.data["unread"] += pg.unread
                going = _look_up(b, pg, cache, status, then)
                cache.save()
                status.write()
                return going

            read_pages(b, cat, CHRONO, on_page, most=s.pages)
            if not going:
                status.line(_("all {n} lots of each seller found", n=MOST_LOTS_A_SELLER))
                break
    finally:
        cache.save()
    return sorted(status.found.values(), key=lambda x: (x.seller or 0, x.price, x.id))


# ---------------------------------------------------------------- buying
@dataclass
class Order:
    lot: Lot
    qty: int
    max_price: int = 0

    def to_json(self) -> dict[str, Any]:
        return {"lot": self.lot.to_json(), "qty": self.qty, "max_price": self.max_price}

    @staticmethod
    def from_json(d: Mapping[str, Any]) -> Order | None:
        raw: object = d.get("lot")
        lot = Lot.from_json(raw) if json.is_object(raw) else None
        if lot is None:
            return None
        try:
            return Order(lot, max(1, int(d.get("qty") or 1)), int(d.get("max_price") or 0))
        except (TypeError, ValueError):
            return None


BOUGHT, GONE, FEWER, OPENING, OVER, CAP, REFUSED, UNKNOWN, NOT_TRIED, WOULD = (
    "bought", "gone", "fewer", "opening", "over", "cap", "refused", "unknown", "not tried", "would buy")
ARRIVE = 2.5                    # seconds for a purchase the game said nothing about to show in the bag


def find_again(b: Bot, lot: Lot) -> tuple[Page, Lot] | None:
    """The lot on the market now, by its listing id: its page first, then the pages either side,
    then the whole category (lots move as others buy and list)."""
    cat = lot.cat
    tried: set[int] = set()
    for page in [lot.page, lot.page - 1, lot.page + 1, lot.page - 2, lot.page + 2]:
        if page < 1 or page in tried:
            continue
        tried.add(page)
        pg = goto(b, cat, lot.order, page)
        if pg is None:
            continue
        hit = next((x for x in pg.lots if x.id == lot.id), None)
        if hit is not None:
            return pg, hit
    found: list[tuple[Page, Lot]] = []

    def on_page(pg: Page) -> bool:
        hit = next((x for x in pg.lots if x.id == lot.id), None)
        if hit is not None:
            found.append((pg, hit))
            return False
        return True

    read_pages(b, cat, lot.order, on_page)
    return found[0] if found else None


def _confirm_equipment(b: Bot, price: int) -> str | None:
    """Answer the game's "Purchase requires N gold coins, are you sure?": Yes only when it names
    the price read. None when answered Yes, else why not (and the question is closed)."""
    end = b.clock.time() + 3.0
    while b.clock.time() < end:
        d = b.dialog()
        top: dict[str, Any] | None = d.get("top") if d.get("open") else None
        if top is not None and b.find_entry(top, CONFIRM) is not None:
            texts = " ".join(t for _r, _c, e in _cells(top) for t in b.entry_texts(e))
            numbers = {int(n) for n in re.findall(r"\d+", texts.replace(",", ""))}
            if numbers and price not in numbers:
                b.close_dialogs()
                return _("the game asked another price ({words})", words=texts[:120])
            b.choose(CONFIRM)
            return None
        b.sleep(POLL)
    return _("no yes/no question came up")


def _held_back(o: Order, lot: Lot, want: int, cost: int, spent: int, cap: int) -> tuple[str, str] | None:
    """Why the lot found again isn't bought, or None: still on display, a price not the one read
    (can't happen: the price belongs to the id), over the most set, fewer left, over the cap."""
    if lot.opens_in:
        return OPENING, _("still on display for {min} minutes", min=max(1, lot.opens_in // 60))
    if lot.price != o.lot.price:
        return REFUSED, _("its price isn't the one read ({now} now)", now=lot.price)
    if o.max_price and lot.price > o.max_price:
        return OVER, _("{price} a unit is over the most you set ({most})", price=lot.price, most=o.max_price)
    if lot.qty < want:
        return FEWER, _("only {n} left (you asked for {want})", n=lot.qty, want=want)
    if cap and spent + cost > cap:
        return CAP, _("{cost} gold coins would go over the spending limit ({cap}, {spent} spent)",
                      cost=cost, cap=cap, spent=spent)
    return None


def in_bag(b: Bot, name: str) -> int | None:
    """How many of name the bag holds, or None when the game sent no bag (an empty reading then
    proves nothing)."""
    have = town.counts(b).get(" ".join(name.split()).lower(), 0)
    return None if getattr(b, "bag_unread", False) else have


def _by_the_bag(b: Bot, lot: Lot, before: int | None, want: int, why: str) -> tuple[str, str, int]:
    """The game didn't say how the purchase went: the bag says. (BOUGHT, words, how many came)
    when some came within ARRIVE seconds, else (UNKNOWN, words, 0) - and then the batch stops."""
    if before is None:
        return UNKNOWN, _("{why}, and the bag couldn't be read before buying - look in the bag", why=why), 0
    end = b.clock.time() + ARRIVE
    while True:
        now = in_bag(b, lot.name)
        got = 0 if now is None else now - before
        if got > 0:
            got = min(got, want)
            return BOUGHT, _("{why}, but {n} came into the bag: bought for {cost} gold coins",
                             why=why, n=got, cost=got * lot.price), got
        if b.clock.time() >= end:
            return UNKNOWN, _("{why}, and nothing came into the bag - look in the bag", why=why), 0
        b.sleep(0.1)


def _purchase(b: Bot, pg: Page, lot: Lot, want: int, cost: int) -> tuple[str, str, int]:
    """Choose the lot on its page by its listing command and answer the game's question: how many
    (an item), or yes or no (equipment). Then the game's spoken answer says whether it was bought;
    when the game doesn't answer as it should, the bag does. (outcome, words, how many bought)."""
    before = in_bag(b, lot.name)
    mark = said_mark(b)
    try:
        choose_lot(b, pg, lot)
    except NotOnPage:
        return REFUSED, _("not bought: its listing isn't on the page exactly once"), 0
    if lot.equip:
        why = _confirm_equipment(b, lot.price)
        if why is not None:
            return REFUSED, _("not bought: {why}", why=why), 0
    elif not town.type_number(b, want):
        # The number may have gone in, or the box may still come late and take the next lot's
        # number: either way the batch can't go on blind.
        b.close_dialogs()
        return _by_the_bag(b, lot, before, want, _("the game's quantity box didn't take the number"))
    heard = said_after(b, mark, lambda t: not SELLER_TEXT.search(t))
    if heard is None:
        return _by_the_bag(b, lot, before, want, _("the game said nothing"))
    if BOUGHT_TEXT.search(heard):
        return BOUGHT, _("bought {n} for {cost} gold coins", n=want, cost=cost), want
    return REFUSED, _("not bought: the game said \"{words}\"", words=heard), 0


@dataclass
class Batch:
    """What a batch of purchases did: [(order, outcome, words)], the gold coins it spent (only what
    the game or the bag proved bought, at the price read), and whether it stopped on a purchase
    whose outcome nobody gave (then the caller buys nothing more either)."""
    out: list[tuple[Order, str, str]]
    spent: int
    unsure: bool


def buy(b: Bot, orders: Sequence[Order], cap: int = 0, check: bool = False) -> list[tuple[Order, str, str]]:
    """Buy each order's lot, found again by its listing id. [(order, outcome, words)] - outcome one
    of BOUGHT, GONE, FEWER, OPENING, OVER, CAP, REFUSED (WOULD with check), or UNKNOWN: the game
    didn't say and the bag didn't show it, so nothing more is bought and every order after it is
    NOT_TRIED (a purchase is never made blind on top of one that may have gone through)."""
    return buy_batch(b, orders, cap, check).out


def buy_batch(b: Bot, orders: Sequence[Order], cap: int = 0, check: bool = False) -> Batch:
    """buy, with what it spent and whether it stopped unsure."""
    status = Status("buy", _("buying {n} lots", n=len(orders)) if not check else _("checking {n} lots", n=len(orders)),
                    pid_of(b))
    out: list[tuple[Order, str, str]] = []
    spent = 0
    results: list[dict[str, Any]] = []
    status.data["results"] = results

    def note(o: Order, outcome: str, words: str) -> None:
        out.append((o, outcome, words))
        results.append({"id": o.lot.id, "name": o.lot.label, "qty": o.qty, "outcome": outcome, "words": words})
        status.line("%s: %s" % (o.lot.label, words))
        status.write(force=True)
        b.log(_("market: {lot}: {words}", lot=o.lot.label, words=words))

    unsure: Order | None = None
    try:
        for o in orders:
            if unsure is not None:
                note(o, NOT_TRIED, _("not tried: the purchase of {lot} may have gone through", lot=unsure.lot.label))
                continue
            if o.lot.opens_in:
                note(o, OPENING, _("still on display - it can't be bought in its first hour"))
                continue
            again = find_again(b, o.lot)
            if again is None:
                note(o, GONE, _("gone (sold or taken back)"))
                continue
            pg, lot = again
            want = 1 if lot.equip else o.qty
            cost = want * lot.price
            held = _held_back(o, lot, want, cost, spent, cap)
            if held is not None:
                note(o, *held)
                continue
            if check:
                note(o, WOULD, _("would buy {n} for {cost} gold coins", n=want, cost=cost))
                continue
            outcome, words, got = _purchase(b, pg, lot, want, cost)
            if outcome == BOUGHT:
                spent += got * lot.price
            note(o, outcome, words)
            if outcome == UNKNOWN:
                unsure = o
    except STOPPING:
        status.end("stopped", _("stopped: {spent} gold coins spent", spent=spent))
        raise
    bought = [x for x in out if x[1] == BOUGHT]
    summary = (_("checked {n} lots", n=len(out)) if check else
               _("bought {n} of {all} lots for {spent} gold coins", n=len(bought), all=len(out), spent=spent))
    if unsure is not None:
        summary += " " + _("- stopped after {lot}: the game didn't say whether it was bought; look in the bag",
                           lot=unsure.lot.label)
    status.end("done", summary)
    b.log(_("market: {summary}", summary=summary), speak=True)
    return Batch(out, spent, unsure is not None)


def cheapest_orders(found: Sequence[Lot], qty: int, max_price: int) -> list[Order]:
    """Orders for qty items from the cheapest buyable lots at or under max_price (a script's buy)."""
    out: list[Order] = []
    left = qty
    for lot in sorted(found, key=lambda x: (x.price, x.id)):
        if left <= 0:
            break
        if lot.opens_in or (max_price and lot.price > max_price):
            continue
        n = 1 if lot.equip else min(left, lot.qty)
        out.append(Order(lot, n, max_price))
        left -= n
    return out


# ---------------------------------------------------------------- the script command
def run(b: Bot, what: str, opts: Mapping[str, Any]) -> object:
    """`market scan [sellers]`, `market search [...]`, `market seller <ids>`, `market buy [check]`
    (the window's files) or `market buy item=<name> count=<n> max=<price>` (cheapest first)."""
    if what == "scan":
        return scan(b, bool(opts.get("sellers")))
    if what == "search":
        if opts.get("names") or opts.get("kind"):
            s = Search(kind=str(opts.get("kind") or "item"), names=list(opts.get("names") or []),
                       cat=opts.get("cat"), max_price=int(opts.get("max_price") or 0), pages=int(opts.get("pages") or 0))
        else:
            s = Search.from_json(read_file(job_file(REQUEST_FILE, pid_of(b))))
        return search(b, s)
    if what == "seller":
        return search(b, Search(kind="seller", sellers=list(opts.get("sellers") or []), cat=opts.get("cat")))
    if what == "buy":
        if opts.get("names"):
            names: list[str] = list(opts["names"])
            found = search(b, Search(kind="cheapest", names=names, cat=opts.get("cat"),
                                     max_price=int(opts.get("max_price") or 0)))
            orders = cheapest_orders(found, int(opts.get("count") or 1), int(opts.get("max_price") or 0))
            if not orders:
                b.log(_("market: nothing to buy at that price"), speak=True)
                return []
            return buy(b, orders, int(opts.get("cap") or 0), bool(opts.get("check")))
        data = read_file(job_file(BUY_FILE, pid_of(b)))
        raw: list[Any] = data.get("orders") or []
        orders = [o for o in (Order.from_json(x) for x in raw if json.is_object(x)) if o is not None]
        if not orders:
            raise Stopped(_("nothing is ticked to buy"))
        return buy(b, orders, int(data.get("cap") or 0), bool(opts.get("check")))
    raise Stopped(_("market what? scan, search, seller or buy"))
