"""Choosing in a shop or the Shard Market by the menu's verified index (agent 53: `menu`, `pick`).

The agent knows the layouts the game showed in every city it was seen in (agent Menus.java): the
Shard Broker's list (shard_market), the buy/sell menu a shard opens (shard_trade) and a potion
seller's list (potion_shop). `menu` says which of them is open, whether it is exactly the verified
one, and every item's row, id and price; `pick <layout> <id> [buy|sell]` chooses that item - the
agent checks the whole menu, the row's item id and the buy/sell command first, under the game's
own lock, and answers `refused: <why>` without touching anything otherwise.

One call instead of a walk down the list a key at a time (the Shard Market's last shard is 17 rows
down: 17 presses, each followed by a reading of the menu), and no text to match on every step.

Every other menu (agent 55, `choose`): the bot finds the entry it wants in its `dialog` reading by
text, as before, and chooses it where it is - choose_at() sends its row and column with the
reading's signature, and the agent chooses it as Enter does once the menu is still the one read.
No key presses, no reading after each. Where that can't be done, the keys walk to it the shorter
way round (row_keys): the game's rows wrap (game.i.e/f, javap: Up on the first row goes to the
last, Down on the last to the first), so an entry near the bottom is reached from the top by going
up.

The shops' item ids never change (the agent's verified tables), so once a verified `menu` reading
gave a name's id, the next pick of that name goes straight to `pick` by the id, without reading
the menu and matching the names again; the agent still checks the whole menu and the row first.

Falling back: with an older agent, or a menu that isn't the verified layout (the game changed it,
or it is a shop that was never recorded), pick() chooses nothing and returns None, and the caller
chooses by the entry's text as before. Once a trade has begun by index it goes on by index or
stops: a buy/sell menu that isn't the one for the shard just chosen is never answered by its text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import gamelang, jsonio as json
from ..clock import clock_for
from ..errors import carry_on
from ..i18n import _
from .botparts.base import STOPPING

if TYPE_CHECKING:
    from .bot import Bot

MENU_AGENT = 53                 # the agent version with `menu` and `pick`
CHOOSE_AGENT = 55               # `choose <row> <col> <sig>` and `dialog` "sig"
SHARD_SELL_AGENT = 56           # agent-side market row -> sell -> quantity transaction
SHARD_DIRECT_AGENT = 56         # agent sends a validated direct-sale plan from the open market
WAITING = ("refused: no menu is open", "refused: the open menu is ")   # pick: the menu hasn't come up yet
MENU_POLL = 0.01              # retry direct picks promptly; the agent refuses an unready/wrong menu
SHARD_MARKET = "shard_market"
SHARD_TRADE = "shard_trade"
POTION_SHOP = "potion_shop"
Item = dict[str, Any]           # one row of `menu`: row, col, id, name, price (and action in shard_trade)


def can_pick(b: Bot) -> bool:
    return int(getattr(b, "agent_version", 0) or 0) >= MENU_AGENT


def same_name(a: object, b: object) -> bool:
    """The game's names with spacing and case set aside ("Welcome to Shard  Market")."""
    return " ".join(str(a or "").split()).casefold() == " ".join(str(b or "").split()).casefold()


def read(b: Bot) -> dict[str, Any] | None:
    """The agent's `menu` reply, or None with an agent too old for it or a reply that isn't one."""
    if not can_pick(b):
        return None
    m: object = b.a.json("menu")
    if not json.is_object(m) or not m.get("ok"):
        return None
    return m if json.is_array(m.get("items")) else None


def _matching(m: dict[str, Any], name: str | None, item_id: int | None, action: str | None) -> list[Item]:
    out: list[Item] = []
    rows: list[Any] = m.get("items") or []
    for it in rows:
        if not json.is_object(it) or not isinstance(it.get("id"), int):
            continue
        if item_id is not None and it["id"] != item_id:
            continue
        if name is not None and not same_name(it.get("name"), name):
            continue
        if action is not None and it.get("action") != action:
            continue
        out.append(it)
    return out


def find(b: Bot, layout: str, name: str | None = None, item_id: int | None = None,
         action: str | None = None, wait: float = 0.0) -> Item | None:
    """The item's row in the open menu when that menu is exactly the verified layout, else None.
    Reads only, and notes the id found for that name (known_ids). wait: seconds to wait for that
    layout to open (the server answers a choice a moment later)."""
    end = clock_for(b).time() + wait
    while True:
        m = read(b)
        if m is None:
            return None
        if m.get("layout") == layout and m.get("verified"):
            hits = _matching(m, name, item_id, action)
            if len(hits) != 1:
                return None
            if name:
                known_ids(b)[_key(layout, name)] = int(hits[0]["id"])     # the next pick goes by it
            return hits[0]
        if clock_for(b).time() >= end:
            return None
        b.sleep(MENU_POLL)


def why_not(b: Bot, layout: str) -> str:
    """Why the open menu can't be used by index, for the log."""
    m = read(b)
    if m is None:
        return _("the agent can't read menus by layout (agent {n} or newer needed)", n=MENU_AGENT)
    if m.get("layout") != layout:
        return _("the open menu is {what}", what=m.get("layout") or "?")
    if not m.get("verified"):
        return _("its items aren't the verified ones (the game may have changed it)")
    return _("the item isn't on it")


def _key(layout: str, name: str) -> tuple[str, str]:
    return layout, " ".join(name.split()).casefold()


def known_ids(b: Bot) -> dict[tuple[str, str], int]:
    """(layout, name) -> item id, from verified `menu` readings this run (the ids never change)."""
    ids: dict[tuple[str, str], int] = b.__dict__.setdefault("menu_ids", {})
    return ids


def item_id(b: Bot, layout: str, name: str) -> int | None:
    """The verified id for a named item, learning it from the open menu once when needed."""
    known = known_ids(b).get(_key(layout, name))
    if known is not None:
        return known
    item = find(b, layout, name=name)
    if item is None:
        typed = gamelang.item_type(name)        # another language: the game's id for that name
        item = find(b, layout, item_id=typed) if typed is not None else None
    return int(item["id"]) if item is not None else None


SOLD, UNSURE, NOT_SOLD = "sold", "unsure", "not sold"


def shard_sell(b: Bot, item_id: int, count: int) -> str:
    """Sell through the agent's stateful shard transaction. SOLD; NOT_SOLD when nothing was entered
    (an older agent, or a menu that wasn't the verified one), which leaves the caller's own menu
    path in charge; UNSURE once the quantity may have gone to the game, which must never be sold
    again by another path - only the bag can tell."""
    if int(getattr(b, "agent_version", 0) or 0) < SHARD_SELL_AGENT:
        return NOT_SOLD
    answer = b.a.send("shardsell %d %d" % (item_id, count))
    if answer.startswith("sold "):
        return SOLD
    if answer.startswith("refused: the Shard Market did not finish the sale") or answer.startswith("entered"):
        return UNSURE
    return NOT_SOLD


def shard_sell_direct_all(b: Bot, sales: list[tuple[int, int]]) -> bool:
    """Queue one verified Shard Market plan through the direct-sale command."""
    if not sales or int(getattr(b, "agent_version", 0) or 0) < SHARD_DIRECT_AGENT:
        return False
    words = " ".join("%d %d" % pair for pair in sales)
    return b.a.send("sharddirectall " + words).startswith("sent all ")


def market_item_ids(b: Bot, names: list[str]) -> list[int] | None:
    """The requested names' IDs from one verified Shard Market reading, in the same order."""
    menu = read(b)
    if menu is None or menu.get("layout") != SHARD_MARKET or not menu.get("verified"):
        return None
    out: list[int] = []
    for name in names:
        hits = _matching(menu, name, None, None)
        if len(hits) != 1:
            return None
        item = int(hits[0]["id"])
        known_ids(b)[_key(SHARD_MARKET, name)] = item
        out.append(item)
    return out


def _pick_by_id(b: Bot, layout: str, item_id: int, action: str | None, wait: float) -> str:
    """`pick` by the id, without reading the menu first; while the menu hasn't come up yet (the
    server answers a choice a moment later), again until wait runs out. The agent's answer."""
    end = clock_for(b).time() + wait
    cmd = "pick %s %d" % (layout, item_id) + (" " + action if action else "")
    while True:
        answer = b.a.send(cmd)
        if not answer.startswith(WAITING) or clock_for(b).time() >= end:
            return answer
        b.sleep(MENU_POLL)


def _picked(answer: str, item_id: int, name: str | None, action: str | None) -> Item:
    """The item the agent's `picked <layout> <row> <col> <id>` names."""
    parts = answer.split()
    row, col = (int(parts[2]), int(parts[3])) if len(parts) >= 5 and parts[2].isdigit() and parts[3].isdigit() else (-1, -1)
    it: Item = {"row": row, "col": col, "id": item_id, "name": name or action}
    if action:
        it["action"] = action
    return it


def pick(b: Bot, layout: str, name: str | None = None, item_id: int | None = None,
         action: str | None = None, wait: float = 0.0, then_text: bool = True,
         settle: bool = True) -> Item | None:
    """Choose the item by its verified index. Returns the item chosen, or None when the index can't
    be used - then nothing was chosen. The reason is logged once per run and layout (then_text:
    the caller goes on by the menu's text), and the menu goes to logs/dialogs.log, so a changed
    menu can be learned from.

    With the id known - given, or learned for that name from an earlier verified reading - it goes
    to the agent's `pick` at once; the menu is read and searched by name only the first time."""
    if not can_pick(b):
        return None
    ids = known_ids(b)
    # the id given, else the one learned this run, else the game's id from the bot's table: the
    # agent checks it against the whole menu and refuses (touching nothing) when it isn't there
    known = item_id if item_id is not None else (ids.get(_key(layout, name)) or gamelang.item_type(name)) if name else None
    if known is not None:
        answer = _pick_by_id(b, layout, known, action, wait)
        if answer.startswith("picked "):
            it = _picked(answer, known, name, action)
            b.log(_("choosing \"{entry}\"", entry=it.get("name") or known))
            if settle:
                b.sleep(0.4)          # as after Enter: the game's answer comes a moment later
            return it
        if item_id is not None:
            _fell_back(b, layout, answer, then_text)
            return None
        ids.pop(_key(layout, name or ""), None)     # read the menu again, as the first time
    it = find(b, layout, name, item_id, action, wait)
    if it is None:
        _fell_back(b, layout, why_not(b, layout), then_text)
        return None
    cmd = "pick %s %d" % (layout, int(it["id"])) + (" " + action if action else "")
    answer = b.a.send(cmd)
    if not answer.startswith("picked "):
        _fell_back(b, layout, answer, then_text)
        return None
    b.log(_("choosing \"{entry}\"", entry=it.get("name") or it["id"]))
    if settle:
        b.sleep(0.4)              # as after Enter: the game's answer comes a moment later
    return it


# ---------------------------------------------------------------- any menu, by row and column
def choose_at(b: Bot, top: dict[str, Any], row: int, col: int) -> bool:
    """Choose the entry at row, col of the menu `top` was read from, as Enter on it does, without
    the keys. False when it can't be done that way (an agent before 55, which gives no "sig"; the
    menu changed since the reading) - then nothing was chosen and the caller walks to it with the
    keys. Said once per run why."""
    sig = top.get("sig")
    if not isinstance(sig, int) or isinstance(sig, bool) or int(getattr(b, "agent_version", 0) or 0) < CHOOSE_AGENT:
        return False
    answer = b.a.send("choose %d %d %d" % (row, col, sig))
    if answer.startswith("chosen "):
        return True
    told: set[str] = b.__dict__.setdefault("menu_fallbacks", set())
    if "choose" not in told:
        told.add("choose")
        b.log(_("not choosing in the menu directly ({why}) - moving to the entry with the keys",
                why=answer))
    return False


def row_keys(at: int, want: int, rows: int) -> tuple[int, int]:
    """(direction, presses) from row `at` to row `want` of a menu with that many rows, the shorter
    way round: the game's rows wrap, so +1 (down) or -1 (up, past the first row to the last).
    A tie goes down."""
    if rows <= 0:
        return 1, 0
    down = (want - at) % rows
    up = (at - want) % rows
    return (1, down) if down <= up else (-1, up)


def _fell_back(b: Bot, layout: str, why: str, then_text: bool) -> None:
    try:
        b.note_dialog(b.dialog(), "not chosen by index in %s: %s" % (layout, why))
    except STOPPING:
        raise
    except Exception as e:     # a note for later; never a reason to stop
        carry_on(e, "noting a menu not chosen by index")
    if not then_text:
        return                 # the caller says what it does instead
    told: set[str] = b.__dict__.setdefault("menu_fallbacks", set())
    if layout not in told:
        told.add(layout)
        b.log(_("{menu}: not choosing by index ({why}) - choosing by the menu's text instead",
                menu=layout.replace("_", " "), why=why))
