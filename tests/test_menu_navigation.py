"""Choosing a menu entry where it is (agent 55 `choose`), walking to it the shorter way round when
that can't be done, and picking a shop item by the id the menu gave it the first time.

The game's rows wrap (game.i.e/f in the game's code: Up on the first row goes to the last, Down on
the last to the first; a row move puts the cursor on the row's first column and option). Columns
don't. WrapBot is DlgBot (tests/test_dialogs.py) on a menu that moves that way, and an agent that
answers `choose` as Dialogs.choose does: the entry at that row and column of the menu read with
that signature.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from unclefarmer import paths
from unclefarmer.engine import menus, town
from conftest import as_bot, returning
from test_dialogs import DlgBot, entry, menu
from test_menu_index import PHILLIPS, PretendShop, ShopBot, nowhere_to_walk, sell

__all__ = ["nowhere_to_walk"]      # its fixture: no walking to the shop

SIG = 424242


@pytest.fixture(autouse=True)
def dialogs_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(paths, "LOGS", str(tmp_path))


class Agent:
    """The agent's plain answers: `choose` as agent 55 gives it (refuse: why it refuses)."""

    def __init__(self, bot: WrapBot, refuse: str = "") -> None:
        self.bot, self.refuse = bot, refuse
        self.sent: list[str] = []

    def send(self, line: str) -> str:
        self.sent.append(line)
        if not line.startswith("choose "):
            return "ok"
        if self.refuse:
            return "refused: " + self.refuse
        _v, row, col, sig = line.split()
        top = self.bot.top
        if top is None or int(sig) != top.get("sig"):
            return "refused: the menu changed since it was read"
        self.bot.top = dict(top, row=int(row), col=int(col))
        self.bot.open = False                     # chosen: the game's answer replaces the menu
        return "chosen %s %s" % (row, col)

    def json(self, line: str) -> Any:
        return {}


class WrapBot(DlgBot):
    """A menu whose rows wrap as the game's do; `wraps=False`: one that stops at its ends."""

    def __init__(self, top: dict[str, Any], version: int = 55, wraps: bool = True, invert: bool = False,
                 refuse: str = "") -> None:
        super().__init__(top=top, invert=invert)
        self.agent_version = version
        self.wraps = wraps
        self.agent = Agent(self, refuse)
        self.a = self.agent
        self.reads = 0
        if version >= menus.CHOOSE_AGENT:
            top["sig"] = SIG

    def dialog(self) -> dict[str, Any]:
        self.reads += 1
        return super().dialog()

    def key(self, k: str, ms: int = 60) -> None:
        if self.top is None or k not in ("w", "s") or not self.wraps:
            super().key(k, ms)
            return
        self.keys.append(k)
        step = 1 if k == "s" else -1
        self.top["row"] = (self.top["row"] + (-step if self.invert else step)) % len(self.top["rows"])
        self.top["col"] = 0


def thirty() -> dict[str, Any]:
    """A menu of 30 rows, "Entry 1" to "Entry 30", the cursor on the first."""
    return menu(*[[entry("Entry %d" % n)] for n in range(1, 31)])


def presses(b: WrapBot) -> list[str]:
    return [k for k in b.keys if k in ("w", "s")]


# ================================================================ the shorter way round
@pytest.mark.parametrize(("at", "want", "rows", "expect"), [
    (0, 3, 30, (1, 3)),        # Entry 4 from Entry 1: down 1 -> 2 -> 3 -> 4
    (0, 24, 30, (-1, 6)),      # Entry 25: up past the top, 30 -> 29 -> ... -> 25
    (0, 14, 30, (1, 14)),      # Entry 15: 14 down against 16 up
    (0, 15, 30, (1, 15)),      # Entry 16: halfway, either way 15 presses - down
    (20, 2, 30, (1, 12)),      # from Entry 21 to Entry 3: down past the bottom is shorter
    (5, 5, 30, (1, 0)),        # already there
    (0, 1, 2, (1, 1)),
    (0, 0, 0, (1, 0)),         # no rows
])
def test_the_rows_are_walked_from_the_nearer_end(at: int, want: int, rows: int, expect: tuple[int, int]) -> None:
    assert menus.row_keys(at, want, rows) == expect


def test_never_more_presses_than_half_the_menu() -> None:
    for at in range(30):
        for want in range(30):
            way, n = menus.row_keys(at, want, 30)
            assert n <= 15 and (at + way * n) % 30 == want


# ================================================================ choosing where it is
def test_an_entry_is_chosen_where_it_is_without_a_key() -> None:
    b = WrapBot(thirty())
    e = b.choose(r"^Entry 27$")
    assert e is not None and e["options"] == ["Entry 27"]
    assert b.agent.sent == ["choose 26 0 %d" % SIG]
    # Later readings are the wait for the game's answer to the choice; only the first reads the menu.
    assert b.keys == [] and b.reads > 1                  # no walk or Enter press
    assert any('choosing "Entry 27"' in line for line in b.logs)


def test_an_entry_in_a_member_row_is_chosen_at_its_column() -> None:
    top = menu([entry("Ali"), entry("Kick"), entry("Promote")], [entry("Kim"), entry("Kick"), entry("Promote")])
    b = WrapBot(top)
    assert b.choose(r"Promote", row=r"^Kim$") is not None
    assert b.agent.sent == ["choose 1 2 %d" % SIG] and b.keys == []


def test_a_menu_that_changed_since_it_was_read_is_walked_to_with_the_keys() -> None:
    b = WrapBot(thirty(), refuse="the menu changed since it was read")
    e = b.choose(r"^Entry 27$")
    assert e is not None and b.keys[-1] == "enter"
    assert presses(b) == ["w"] * 4                       # up past the top: 30, 29, 28, 27
    assert sum("moving to the entry with the keys" in line for line in b.logs) == 1


def test_why_it_wasn_t_chosen_directly_is_said_once_a_run() -> None:
    b = WrapBot(thirty(), refuse="no menu is open")
    for want in (27, 3, 27):
        b.top = thirty()
        b.top["sig"] = SIG
        b.open = True
        assert b.choose(r"^Entry %d$" % want) is not None
    assert sum("moving to the entry with the keys" in line for line in b.logs) == 1


# ================================================================ with an older agent: the keys
def test_an_older_agent_walks_up_past_the_top_to_an_entry_near_the_bottom() -> None:
    b = WrapBot(thirty(), version=54)
    e = b.choose(r"^Entry 25$")
    assert e is not None and b.agent.sent == []
    assert presses(b) == ["w"] * 6 and b.keys[-1] == "enter"      # 1 -> 30 -> ... -> 25


def test_an_older_agent_walks_down_to_an_entry_near_the_top() -> None:
    b = WrapBot(thirty(), version=54)
    assert b.choose(r"^Entry 4$") is not None
    assert presses(b) == ["s"] * 3


def test_from_the_middle_the_shorter_way_is_taken() -> None:
    top = thirty()
    top["row"] = 20                                                 # on Entry 21
    b = WrapBot(top, version=54)
    assert b.choose(r"^Entry 3$") is not None
    assert presses(b) == ["s"] * 12                                 # 21 -> 30 -> 1 -> 3


def test_a_list_that_doesn_t_wrap_is_walked_straight_after_one_press() -> None:
    b = WrapBot(thirty(), version=54, wraps=False)
    e = b.choose(r"^Entry 25$")
    assert e is not None and b.top is not None
    assert presses(b) == ["w"] + ["s"] * 24                         # Up didn't move: straight down


def test_keys_that_move_the_other_way_are_swapped_once() -> None:
    b = WrapBot(thirty(), version=54, invert=True)
    assert b.choose(r"^Entry 25$") is not None
    keys = presses(b)
    assert keys[0] == "w" and set(keys[1:]) == {"s"} and len(keys) == 1 + 7   # 1 -> 2, then back up 7


def test_fewer_presses_than_the_straight_walk_on_every_row() -> None:
    straight = nearest = 0
    for want in range(1, 31):
        b = WrapBot(thirty(), version=54)
        assert b.choose(r"^Entry %d$" % want) is not None
        nearest += len(presses(b))
        straight += want - 1                                        # the old walk: down from Entry 1
    assert nearest == 225 and straight == 435


# ================================================================ shop items by the id found once
def menu_reads(game: PretendShop) -> list[str]:
    reads: list[str] = []
    answer = game.json

    def counted(line: str) -> Any:
        reads.append(line)
        return answer(line)
    game.json = counted                  # type: ignore[method-assign]
    return reads


def test_a_shard_sold_in_steps_reads_the_list_once() -> None:
    game = PretendShop("market", {"Level 1 Sapphire Shard": 4500})
    reads = menu_reads(game)
    sell(game, "Level 1 Sapphire Shard", 4500)
    assert [n for _a, _i, n in game.trades] == [2000, 2000, 500]
    assert game.picks == ["shard_market 20405", "shard_trade 20405 sell"] * 3
    assert reads.count("menu") == 1                  # the check that the list has it; then by the id


def test_a_learned_id_that_is_refused_is_forgotten_and_the_menu_read_again() -> None:
    game = PretendShop("market", {"Level 1 Ruby Shard": 10})
    b = ShopBot(game)
    ids = menus.known_ids(as_bot(b))
    ids[("shard_market", "level 1 ruby shard")] = 20385
    game.changed = True                               # the game moved two rows: the agent refuses
    reads = menu_reads(game)
    assert menus.pick(as_bot(b), menus.SHARD_MARKET, name="Level 1 Ruby Shard") is None
    assert reads.count("menu") >= 1 and ids == {} and game.picks == []


def test_the_buy_and_sell_menu_is_waited_for_by_the_id(monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("market", {})
    b = ShopBot(game)
    game.screen = "none"                              # the server hasn't answered the shard's row yet
    opened: list[int] = []
    sleep = b.sleep

    def later(seconds: float) -> None:
        sleep(seconds)
        if not opened:
            opened.append(1)
            game.screen, game.trade_id = "trade", 20405
    b.sleep = later                                   # type: ignore[method-assign]
    reads = menu_reads(game)
    before = b.clock.now
    it = menus.pick(as_bot(b), menus.SHARD_TRADE, item_id=20405, action="sell", wait=3.0,
                    then_text=False, settle=False)
    assert it is not None and it["action"] == "sell" and it["row"] == 1
    assert reads == [] and game.picks == ["shard_trade 20405 sell"]
    assert b.clock.now - before == pytest.approx(menus.MENU_POLL)


def test_a_potion_bought_twice_reads_the_seller_s_list_once(monkeypatch: pytest.MonkeyPatch) -> None:
    game = PretendShop("shop", {"Large Red Potion": 3})

    def visit(*_a: object, **_k: object) -> str:
        game.screen = "shop"                          # talking to the seller opens the list
        return ""
    monkeypatch.setattr(town, "visit", visit)
    monkeypatch.setattr(town, "shops_for", returning([PHILLIPS]))
    b = ShopBot(game)
    reads = menu_reads(game)
    assert town.buy(as_bot(b), "Large Red Potion", 100) == 100
    assert town.buy(as_bot(b), "Large Red Potion", 100) == 100
    # the id table gives the potion's id, so the list isn't read even once; the agent still checks
    # the whole menu before it picks
    assert game.picks == ["potion_shop 23387"] * 2 and reads.count("menu") == 0
