"""Buying and selling by the menu's verified index (agent 53: `menu` and `pick`), and falling back.

PretendShop is the game behind the agent: a Shard Broker's list and its buy/sell menu, or a potion
seller's list, as dialogs.log recorded them, with the agent's answers to `menu` and `pick` (the
buy/sell reply is agent/test/contract.txt's own line, which AgentTests checks the agent writes).
It also moves the menu cursor a row per walking key, the way the bot chooses by text, so the two
ways can be compared.
"""

from __future__ import annotations

import json as stdjson
import threading
from pathlib import Path
from typing import Any

import pytest

from unclefarmer import settings
from unclefarmer.engine import bagrules, crafting, menus, town
from conftest import Clock, as_bot, returning

CONTRACT = Path(__file__).resolve().parent.parent / "agent" / "test" / "contract.txt"
SHARDS = [("Weapon Enhance Crystal Shard", 20985, 4000), ("Equipment Enhance Crystal Shard", 21015, 1000),
          ("Level 1 Ruby Shard", 20385, 2550), ("Level 1 Sapphire Shard", 20405, 20),
          ("Level 1 Tiger Eye Stone Shard", 20425, 2175), ("Level 1 Opal Shard", 20445, 2540),
          ("Level 1 Spinel Shard", 20465, 20), ("Level 1 Aquamarine Shard", 20485, 22),
          ("Level 1 Garnet Shard", 20505, 4020), ("Level 1 Blond Jade Shard", 20525, 5850),
          ("Level 1 Grape Stone Shard", 20545, 56), ("Level 1 Olivine Shard", 20565, 22),
          ("Level 1 Topaz Shard", 20585, 535), ("Level 1 Obsidian Shard", 20605, 2635),
          ("Level 1 Sunlight Stone Shard", 20625, 26), ("Level 1 Moonstone Shard", 20645, 2455),
          ("Level 1 Turquoise Shard", 20665, 1405)]
POTIONS = [("Small Red Potion", 23385, 60), ("Small Blue Potion", 23395, 60), ("Medium Red Potion", 23386, 150),
           ("Medium Blue Potion", 23396, 150), ("Large Red Potion", 23387, 450), ("Large Blue Potion", 23397, 450),
           ("Pet Small Red Potion", 23435, 120), ("Pet Small Blue Potion", 23445, 120),
           ("Pet Medium Red Potion", 23436, 300), ("Pet Medium Blue Potion", 23446, 300),
           ("Pet Large Red Potion", 23437, 1000), ("Pet Large Blue Potion", 23447, 1000)]


def contract(key: str) -> str:
    for line in CONTRACT.read_text(encoding="utf-8").splitlines():
        if line.startswith(key + "="):
            return line[len(key) + 1:]
    raise KeyError(key)


class PretendShop:
    """The game's menus and the agent's menu commands. screen: "market" | "trade" | "shop" | "input"
    | "none"; trade_id: whose buy/sell menu is open; changed: the game moved two rows of the list."""

    def __init__(self, kind: str, bag: dict[str, int], version: int = 53, changed: bool = False,
                 trade_for_other: bool = False) -> None:
        self.kind, self.bag, self.version, self.changed = kind, bag, version, changed
        self.trade_for_other = trade_for_other      # the buy/sell menu that opens is another shard's
        self.screen = kind
        self.trade_id = 0
        self.action = ""
        self.cursor: tuple[int, int] = (0, 0)
        self.sent: list[str] = []
        self.typed = 0
        self.picks: list[str] = []
        self.walking_keys = 0
        self.trades: list[tuple[str, str, int]] = []      # (buy|sell, item, how many)
        self.direct_takes: int | None = None    # the server takes only this many of a direct plan's sales
        self.shardsell_reply: str | None = None  # what `shardsell` answers after it has entered the number

    # ------------------------------------------------------------ the menus as the game holds them
    def rows(self) -> list[tuple[str, int, int]]:
        listed = list(SHARDS if self.kind == "market" else POTIONS)
        if self.changed:
            listed[2], listed[3] = listed[3], listed[2]
        return listed

    def top(self) -> dict[str, Any] | None:
        if self.screen == "market":
            rows: list[list[dict[str, Any]]] = [[{"flags": 0, "options": ["Welcome to Shard  Market"]},
                                                 {"flags": 8, "options": ["View description"], "cmd": [4]}]]
            rows += [[{"flags": 8, "options": [n, "Unit price %d arnebia" % p], "cmd": [1, i, p]}]
                     for n, i, p in self.rows()]
        elif self.screen == "shop":
            rows = [[{"flags": 9, "options": [n, "unit price %d arnebia" % p, "Recover HP"], "cmd": [1, i, 0]}]
                    for n, i, p in self.rows()]
        elif self.screen == "trade":
            rows = [[{"flags": 13, "options": ["buy"], "cmd": [2, self.trade_id, 2175, 0]}],
                    [{"flags": 13, "options": ["sell"], "cmd": [3, self.trade_id, 2175, 0]}]]
        else:
            return None
        return {"kind": 0, "row": self.cursor[0], "col": self.cursor[1], "rows": rows}

    def layout(self) -> str:
        if self.screen == "trade":
            return "shard_trade"
        return {"market": "shard_market", "shop": "potion_shop"}.get(self.screen, "unknown")

    def menu_reply(self) -> dict[str, Any]:
        if self.screen == "trade":
            reply: dict[str, Any] = stdjson.loads(contract("menu_trade"))
            for it in reply["items"]:
                it["id"] = self.trade_id
            return reply
        if self.screen in ("market", "shop"):
            first = 1 if self.screen == "market" else 0
            items = [{"row": r + first, "col": 0, "id": i, "name": n, "price": p} for r, (n, i, p) in enumerate(self.rows())]
            return {"ok": True, "layout": self.layout(), "verified": not self.changed, "items": items}
        return {"ok": True, "layout": "none", "verified": False, "items": []}

    def name_of(self, item_id: int) -> str:
        return next(n for n, i, _p in SHARDS + POTIONS if i == item_id)

    def choose_at(self, row: int) -> None:
        """Enter on the entry at row: what the game does with it."""
        if self.screen == "market":
            _n, i, _p = self.rows()[row - 1]
            self.trade_id = 20425 if self.trade_for_other else i
            self.screen = "trade"
        elif self.screen == "trade":
            self.action = "buy" if row == 0 else "sell"
            self.screen = "input"
        elif self.screen == "shop":
            self.trade_id, self.action = self.rows()[row][1], "buy"
            self.screen = "input"

    # ------------------------------------------------------------ the agent link
    def send(self, line: str) -> str:
        self.sent.append(line)
        if line == "version":
            return "mwagent %d" % self.version
        if line.startswith("pick "):
            return self.pick(line.split()[1:])
        if line.startswith("shardsell "):
            _verb, item_id, count = line.split()
            item = self.name_of(int(item_id))
            if self.screen != "market" or item_id not in [str(i) for _n, i, _p in self.rows()]:
                return "refused: the open menu is %s, not shard_market" % self.layout()
            self.trades.append(("sell", item, int(count)))
            self.bag[item] = self.bag.get(item, 0) - int(count)
            self.screen = self.kind
            return self.shardsell_reply or "sold %s %s" % (item_id, count)
        if line.startswith("sharddirectall "):
            values = line.split()[1:]
            if self.screen != "market" or len(values) % 2:
                return "refused: the open menu is %s, not shard_market" % self.layout()
            pairs = list(zip(values[::2], values[1::2]))
            for item_id, count in pairs[:self.direct_takes]:
                item = self.name_of(int(item_id))
                self.trades.append(("sell", item, int(count)))
                self.bag[item] = self.bag.get(item, 0) - int(count)
            self.screen = "none"            # a finished sale closes the broker
            return "sent all %d" % len(pairs)
        if line.startswith("type "):
            self.typed = int(line.split()[1])
            return "typed"
        if line.startswith("number ") and self.screen == "input":
            self.typed = int(line.split()[1])
            line = "typeenter"
        if line == "typeenter" and self.screen == "input":
            item = self.name_of(self.trade_id)
            self.bag[item] = self.bag.get(item, 0) + (self.typed if self.action == "buy" else -self.typed)
            self.trades.append((self.action, item, self.typed))
            self.screen = self.kind
            return "entered"
        return "ok"

    def pick(self, args: list[str]) -> str:
        if self.version < 53:
            return "unknown command: pick"
        layout, item_id = args[0], int(args[1])
        action = args[2] if len(args) > 2 else None
        if layout != self.layout():
            return "refused: the open menu is %s, not %s" % (self.layout(), layout)
        if self.changed and layout != "shard_trade":
            return "refused: %s changed: its items are not the verified ones" % layout
        if layout == "shard_trade":
            if item_id != self.trade_id:
                return "refused: row 1 holds item %d, not %d" % (self.trade_id, item_id)
            row = 0 if action == "buy" else 1
        else:
            row = [i for _n, i, _p in self.rows()].index(item_id) + (1 if layout == "shard_market" else 0)
        self.picks.append(" ".join(args))
        self.cursor = (row, 0)
        self.choose_at(row)
        return "picked %s %d 0 %d" % (layout, row, item_id)

    def json(self, line: str) -> Any:
        if line == "menu":
            if self.version < 53:
                return {"ok": False, "error": "unknown command"}
            return self.menu_reply()
        if line == "input":
            return {"ok": True, "open": self.screen == "input"}
        return {}


class ShopBot:
    """The Bot's surface the shop and market errands use, on the pretend game."""

    def __init__(self, game: PretendShop) -> None:
        self.a = game
        self.agent_version = game.version
        self.clock = Clock()
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.lines: list[str] = []
        self.spoken: list[str] = []
        self.notes: list[str] = []
        self.stop_event = threading.Event()
        self.restock_town: int | None = None
        self.sheet: dict[str, Any] | None = None    # the character sheet's load, when a test sets one

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)
        if speak:
            self.spoken.append(line)

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        return self.sheet

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": n, "count": c} for n, c in self.a.bag.items()]

    def dialog(self) -> dict[str, Any]:
        top = self.a.top()
        return {"ok": True, "open": 1 if top else 0, "top": top}

    def note_dialog(self, d: dict[str, Any], why: str) -> None:
        self.notes.append(why)

    @staticmethod
    def entry_texts(e: dict[str, Any]) -> list[str]:
        return [t for t in [e.get("text"), e.get("raw")] + list(e.get("options") or []) if t]

    def find_entry(self, top: dict[str, Any] | None, pattern: str) -> tuple[int, int] | None:
        from unclefarmer import travel
        for r, row in enumerate((top or {}).get("rows") or []):
            for c, e in enumerate(row):
                if e and any(travel.matches(pattern, t) for t in self.entry_texts(e)):
                    return r, c
        return None

    def wait_dialog(self, seconds: float = 3.0, want_open: bool = True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if bool(d["open"]) == want_open else None

    def choose(self, pattern: str) -> dict[str, Any] | None:
        """By text, as the real Bot does: walk the cursor a row per key, then Enter."""
        top = self.a.top()
        where = self.find_entry(top, pattern)
        if top is None or where is None:
            return None
        self.a.walking_keys += abs(where[0] - self.a.cursor[0])
        self.a.cursor = where
        self.a.choose_at(where[0])
        return top["rows"][where[0]][where[1]]

    def close_dialogs(self) -> None:
        self.a.screen = "none"

    def talk(self, name: str, at: tuple[float, float] | None = None) -> dict[str, Any] | None:
        self.a.screen = self.a.kind
        return self.dialog()


def reopening(game: PretendShop, monkeypatch: pytest.MonkeyPatch) -> None:
    """Going to the broker opens the market again (a finished sale closes it)."""
    def visit(*_args: Any, **_kw: Any) -> str:
        game.screen = game.kind
        return "Shard Broker"
    monkeypatch.setattr(town, "visit", visit)


PHILLIPS = (1041, "Apothecary Phillips", 0.0, 0.0)


@pytest.fixture(autouse=True)
def nowhere_to_walk(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "visit", returning(""))
    monkeypatch.setattr(town, "note_shop", returning(None))
    monkeypatch.setattr(town, "nearest", returning(PHILLIPS))


def sell(game: PretendShop, shard: str, n: int) -> ShopBot:
    b = ShopBot(game)
    town.sell_shards(as_bot(b), [shard], at_most={shard.lower(): n})
    return b


# ================================================================ by index
def test_a_shard_is_sold_by_its_verified_rows_without_walking_down_the_list() -> None:
    game = PretendShop("market", {"Level 1 Turquoise Shard": 30})
    b = sell(game, "Level 1 Turquoise Shard", 30)
    assert game.picks == ["shard_market 20665", "shard_trade 20665 sell"]
    assert game.trades == [("sell", "Level 1 Turquoise Shard", 30)]
    assert game.walking_keys == 0
    assert "sold 30 Level 1 Turquoise Shard at the Shard Market" in b.lines


def test_new_agent_sells_verified_shards_in_one_agent_transaction() -> None:
    game = PretendShop("market", {"Level 1 Turquoise Shard": 30, "Level 1 Sapphire Shard": 5}, version=56)
    b = ShopBot(game)
    town.sell_shards(as_bot(b), ["Level 1 Turquoise Shard", "Level 1 Sapphire Shard"])
    assert "sharddirectall 20665 30 20405 5" in game.sent
    assert game.picks == [] and game.trades == [("sell", "Level 1 Turquoise Shard", 30),
                                                 ("sell", "Level 1 Sapphire Shard", 5)]
    assert "sold 30 Level 1 Turquoise Shard at the Shard Market" in b.lines
    assert "sold 5 Level 1 Sapphire Shard at the Shard Market" in b.lines


def test_direct_shard_sale_splits_more_than_the_trade_limit_in_order() -> None:
    game = PretendShop("market", {"Level 1 Turquoise Shard": 2001}, version=56)
    b = ShopBot(game)
    town.sell_shards(as_bot(b), ["Level 1 Turquoise Shard"])
    assert "sharddirectall 20665 2000 20665 1" in game.sent
    assert game.trades == [("sell", "Level 1 Turquoise Shard", 2000),
                           ("sell", "Level 1 Turquoise Shard", 1)]


def test_what_a_direct_plan_left_unsold_goes_step_by_step_and_never_more_than_planned(
        monkeypatch: pytest.MonkeyPatch) -> None:
    # The server took only the first sale of the plan; the bag says so, and the rest is sold the
    # menu way - only what is left, so a "sell N, keep the rest" rule still holds.
    game = PretendShop("market", {"Level 1 Turquoise Shard": 50, "Level 1 Sapphire Shard": 40}, version=56)
    game.direct_takes = 1
    reopening(game, monkeypatch)
    b = ShopBot(game)
    town.sell_shards(as_bot(b), ["Level 1 Turquoise Shard", "Level 1 Sapphire Shard"],
                     at_most={"level 1 turquoise shard": 30, "level 1 sapphire shard": 25})
    assert game.trades == [("sell", "Level 1 Turquoise Shard", 30), ("sell", "Level 1 Sapphire Shard", 25)]
    assert game.bag["Level 1 Turquoise Shard"] == 20 and game.bag["Level 1 Sapphire Shard"] == 15
    assert "sold 25 Level 1 Sapphire Shard at the Shard Market" in b.lines


def test_a_shard_sale_the_agent_is_unsure_of_is_never_sold_again_another_way(
        monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("market", {"Level 1 Turquoise Shard": 30}, version=56)
    game.direct_takes = 0
    reopening(game, monkeypatch)
    game.shardsell_reply = "refused: the Shard Market did not finish the sale"
    sell(game, "Level 1 Turquoise Shard", 30)
    assert game.trades == [("sell", "Level 1 Turquoise Shard", 30)]
    assert game.picks == []


def test_buying_shards_never_uses_the_agent_s_sell_command(monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("market", {"Arnebia": 10_000_000}, version=56)
    b = ShopBot(game)
    monkeypatch.setattr(crafting, "fee_per_shard", returning(0))
    monkeypatch.setattr(crafting, "spare_arnebia", returning(10_000_000))
    assert crafting.buy_shards(as_bot(b), "Level 1 Topaz Shard", 40) == 40
    assert game.trades == [("buy", "Level 1 Topaz Shard", 40)]
    assert not any(line.startswith(("shardsell", "sharddirect")) for line in game.sent)


def test_shard_chain_uses_the_next_menu_state_instead_of_fixed_pick_pauses() -> None:
    game = PretendShop("market", {"Level 1 Turquoise Shard": 30})
    b = ShopBot(game)
    before = b.clock.now
    item = menus.pick(as_bot(b), menus.SHARD_MARKET, name="Level 1 Turquoise Shard", settle=False)
    assert item is not None
    assert menus.pick(as_bot(b), menus.SHARD_TRADE, item_id=item["id"], action="sell", settle=False) is not None
    assert game.picks == ["shard_market 20665", "shard_trade 20665 sell"]
    assert b.clock.now == before


def test_by_index_takes_no_walking_keys_where_by_text_took_one_per_row() -> None:
    by_index = PretendShop("market", {"Level 1 Turquoise Shard": 5})
    sell(by_index, "Level 1 Turquoise Shard", 5)
    by_text = PretendShop("market", {"Level 1 Turquoise Shard": 5}, version=52)
    sell(by_text, "Level 1 Turquoise Shard", 5)
    assert by_text.trades == by_index.trades == [("sell", "Level 1 Turquoise Shard", 5)]
    assert by_index.walking_keys == 0 and by_text.walking_keys >= 17      # row 17, then the sell row


def test_more_than_the_box_takes_is_sold_in_steps_each_by_index() -> None:
    game = PretendShop("market", {"Level 1 Sapphire Shard": 4500})
    sell(game, "Level 1 Sapphire Shard", 4500)
    assert [n for _a, _i, n in game.trades] == [2000, 2000, 500]
    assert game.picks.count("shard_trade 20405 sell") == 3 and game.bag["Level 1 Sapphire Shard"] == 0


def test_a_shard_is_bought_by_index_at_the_list_s_own_price(monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("market", {"Arnebia": 10_000_000})
    b = ShopBot(game)
    monkeypatch.setattr(crafting, "fee_per_shard", returning(0))
    monkeypatch.setattr(crafting, "spare_arnebia", returning(10_000_000))
    got = crafting.buy_shards(as_bot(b), "Level 1 Topaz Shard", 40)
    assert got == 40 and game.trades == [("buy", "Level 1 Topaz Shard", 40)]
    assert game.picks == ["shard_market 20585", "shard_trade 20585 buy"] and game.walking_keys == 0


@pytest.mark.parametrize(("room", "steps"), [(2000, [2000]), (2500, [2000, 500]), (4500, [2000, 2000, 500]),
                                             (6000, [2000, 2000, 2000]), (10_000, [2000, 2000, 2000, 2000, 1000])])
def test_a_big_shard_buy_goes_in_batches_the_bag_has_room_for(monkeypatch: pytest.MonkeyPatch, room: int,
                                                               steps: list[int]) -> None:
    # The market's box takes 2000 at a time; the bag's room (load limit / the shard's weight) and
    # what is wanted decide the rest: each batch is the smallest of them, worked out afresh.
    game = PretendShop("market", {"Arnebia": 10_000_000})
    b = ShopBot(game)
    b.sheet = {"load": 0, "maxload": room}
    b.p["bag_full_at"] = 100
    monkeypatch.setattr(bagrules, "weights", returning({"level 1 topaz shard": 1.0}))
    monkeypatch.setattr(crafting, "fee_per_shard", returning(0))
    monkeypatch.setattr(crafting, "spare_arnebia", returning(10_000_000))
    assert crafting.buy_shards(as_bot(b), "Level 1 Topaz Shard", 9000) == min(9000, room)
    assert [n for _a, _i, n in game.trades] == steps
    assert all(n <= town.TRADE_MOST for _a, _i, n in game.trades)
    assert game.bag["Level 1 Topaz Shard"] == min(9000, room)


def test_a_potion_is_bought_by_its_row_in_a_potion_seller_s_list(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "shops_for", returning([PHILLIPS]))
    game = PretendShop("shop", {"Large Red Potion": 3})
    b = ShopBot(game)
    assert town.buy(as_bot(b), "Large Red Potion", 200) == 200
    assert game.picks == ["potion_shop 23387"] and game.trades == [("buy", "Large Red Potion", 200)]
    assert game.walking_keys == 0


def test_a_shop_in_another_language_is_picked_by_the_game_s_item_id(monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("shop", {})
    monkeypatch.setattr(game, "rows", returning([("Büyük Kırmızı İksir", 23387, 450), ("Küçük Mavi İksir", 23395, 60)]))
    b = ShopBot(game)
    assert menus.item_id(as_bot(b), menus.POTION_SHOP, "Small Blue Potion") == 23395     # found by its id
    it = menus.pick(as_bot(b), menus.POTION_SHOP, name="Large Red Potion")     # the id table knows 23387
    assert it is not None and game.picks == ["potion_shop 23387"]


# ================================================================ checks and falling back
def test_a_changed_list_is_not_trusted_the_bot_goes_by_text_instead() -> None:
    game = PretendShop("market", {"Level 1 Ruby Shard": 9}, changed=True)
    b = sell(game, "Level 1 Ruby Shard", 9)
    assert game.picks == []
    assert game.trades == [("sell", "Level 1 Ruby Shard", 9)]       # by text: the name is still checked
    assert any("not choosing by index" in line and "verified" in line for line in b.lines)
    assert any("not chosen by index" in note for note in b.notes)


def test_an_older_agent_goes_by_text_as_before() -> None:
    game = PretendShop("market", {"Level 1 Ruby Shard": 2}, version=52)
    b = sell(game, "Level 1 Ruby Shard", 2)
    assert game.trades == [("sell", "Level 1 Ruby Shard", 2)]
    assert not any(s.startswith("pick") for s in game.sent) and not any("menu" == s for s in game.sent)
    assert not any("not choosing by index" in line for line in b.lines)


def test_the_wrong_shard_s_buy_and_sell_menu_is_never_answered() -> None:
    """The list was chosen by index, but the buy/sell menu that came up is another shard's: nothing
    is sold, and the bot doesn't fall back to choosing "sell" by its text there."""
    game = PretendShop("market", {"Level 1 Ruby Shard": 9, "Level 1 Tiger Eye Stone Shard": 50}, trade_for_other=True)
    b = sell(game, "Level 1 Ruby Shard", 9)
    assert game.trades == []
    assert game.bag == {"Level 1 Ruby Shard": 9, "Level 1 Tiger Eye Stone Shard": 50}
    assert any("didn't come up as expected" in line for line in b.spoken)


def test_a_menu_that_isn_t_the_expected_one_is_refused_and_nothing_is_chosen() -> None:
    game = PretendShop("shop", {})
    b = ShopBot(game)
    assert menus.pick(as_bot(b), menus.SHARD_MARKET, name="Level 1 Ruby Shard") is None
    assert game.picks == [] and game.screen == "shop"
    assert any("the open menu is potion_shop" in line for line in b.lines)


def test_an_item_that_isn_t_on_the_verified_list_is_not_picked() -> None:
    game = PretendShop("shop", {})
    b = ShopBot(game)
    assert menus.pick(as_bot(b), menus.POTION_SHOP, name="Huge Red Potion") is None
    assert game.picks == []


def test_a_refusal_from_the_agent_chooses_nothing_and_says_why(monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("market", {})
    b = ShopBot(game)
    monkeypatch.setattr(game, "pick", returning("refused: row 3 holds item 20405, not 20385"))
    assert menus.pick(as_bot(b), menus.SHARD_MARKET, name="Level 1 Ruby Shard") is None
    assert game.screen == "market"
    assert any("row 3 holds item 20405" in line for line in b.lines)
    assert sum("not choosing by index" in line for line in b.lines) == 1
    menus.pick(as_bot(b), menus.SHARD_MARKET, name="Level 1 Ruby Shard")
    assert sum("not choosing by index" in line for line in b.lines) == 1     # said once a run


def test_names_are_matched_whatever_their_spacing_and_case() -> None:
    assert menus.same_name("Welcome to Shard  Market", "welcome to shard market")
    assert not menus.same_name("Level 1 Ruby Shard", "Level 2 Ruby Shard")


def test_the_buy_and_sell_menu_reply_is_read_as_the_agent_writes_it() -> None:
    reply = stdjson.loads(contract("menu_trade"))
    game = PretendShop("market", {})
    game.screen, game.trade_id = "trade", 20425
    b = ShopBot(game)
    it = menus.find(as_bot(b), menus.SHARD_TRADE, item_id=20425, action="sell")
    assert it == reply["items"][1] and it is not None and it["row"] == 1
    assert menus.find(as_bot(b), menus.SHARD_TRADE, item_id=20425, action="steal") is None
