"""Choosing in a shop or the Shard Market by the menu's verified index (agent 53: `menu`, `pick`).

The agent knows the layouts the game showed in every city it was seen in (agent Menus.java): the
Shard Broker's list (shard_market), the buy/sell menu a shard opens (shard_trade) and a potion
seller's list (potion_shop). `menu` says which of them is open, whether it is exactly the verified
one, and every item's row, id and price; `pick <layout> <id> [buy|sell]` chooses that item - the
agent checks the whole menu, the row's item id and the buy/sell command first, under the game's
own lock, and answers `refused: <why>` without touching anything otherwise.

One call instead of a walk down the list a key at a time (the Shard Market's last shard is 17 rows
down: 17 presses, each followed by a reading of the menu), and no text to match on every step.

Falling back: with an older agent, or a menu that isn't the verified layout (the game changed it,
or it is a shop that was never recorded), pick() chooses nothing and returns None, and the caller
chooses by the entry's text as before. Once a trade has begun by index it goes on by index or
stops: a buy/sell menu that isn't the one for the shard just chosen is never answered by its text.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from .. import jsonio as json
from ..clock import clock_for
from ..errors import carry_on
from ..i18n import _
from .botparts.base import STOPPING

if TYPE_CHECKING:
    from .bot import Bot

MENU_AGENT = 53                 # the agent version with `menu` and `pick`
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
    Reads only. wait: seconds to wait for that layout to open (the server answers a choice a moment
    later)."""
    end = clock_for(b).time() + wait
    while True:
        m = read(b)
        if m is None:
            return None
        if m.get("layout") == layout and m.get("verified"):
            hits = _matching(m, name, item_id, action)
            return hits[0] if len(hits) == 1 else None
        if clock_for(b).time() >= end:
            return None
        b.sleep(0.15)


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


def pick(b: Bot, layout: str, name: str | None = None, item_id: int | None = None,
         action: str | None = None, wait: float = 0.0, then_text: bool = True) -> Item | None:
    """Choose the item by its verified index. Returns the item chosen, or None when the index can't
    be used - then nothing was chosen. The reason is logged once per run and layout (then_text:
    the caller goes on by the menu's text), and the menu goes to logs/dialogs.log, so a changed
    menu can be learned from."""
    if not can_pick(b):
        return None
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
    b.sleep(0.4)                  # as after Enter: the game's answer comes a moment later
    return it


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
