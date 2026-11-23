# pyright: reportPrivateUsage=false
"""The menu box on the Keys page (menu_keys_retry), for one player's computer where menus worked by
hand and did nothing for the bot, which chose the moment they opened: with it ticked every choice
gives the menu a moment first (dialogs.MENU_WAIT), and a shop's "Purchase" that leaves its menu as
it was is tried once more with the keys. Unticked, nothing changes."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot, stand_in
from test_dialogs import DlgBot, entry, menu
from test_town import TownBot, menu as town_menu, opt

from unclefarmer.engine import town
from unclefarmer.engine.botparts import dialogs as dialogs_mod


def ticked(b: Any, on: bool = True) -> Any:
    b.p["menu_keys_retry"] = on
    return b


def test_a_ticked_box_gives_the_menu_a_moment_before_the_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    b = ticked(DlgBot(top={"sig": 3, **menu([entry("Alpha")])}))
    chose_at: list[float] = []

    def choose_at(bot: Any, top: dict[str, Any], row: int, col: int) -> bool:
        chose_at.append(b.test_clock.now)
        return True
    monkeypatch.setattr(dialogs_mod.menus, "choose_at", choose_at)
    start = b.test_clock.now
    assert b.choose(r"^Alpha$") is not None
    assert chose_at and chose_at[0] - start >= dialogs_mod.MENU_WAIT


def test_unticked_the_choice_is_made_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    b = DlgBot(top={"sig": 3, **menu([entry("Alpha")])})
    chose_at: list[float] = []

    def choose_at(bot: Any, top: dict[str, Any], row: int, col: int) -> bool:
        chose_at.append(b.test_clock.now)
        return True
    monkeypatch.setattr(dialogs_mod.menus, "choose_at", choose_at)
    start = b.test_clock.now
    assert b.choose(r"^Alpha$") is not None
    assert chose_at == [start]


class Shop:
    """A shop's top menu whose "Purchase Medicine" opens the list only when chosen with the keys."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch, tb: TownBot) -> None:
        self.ways: list[bool] = []
        self.top = town_menu(opt("Purchase Medicine"), opt("Make Medicine"))
        tb.dialog = lambda: {"open": 1, "top": self.top}

        def open_section(b: Any, pattern: str, keys: bool = False) -> bool:
            if tb.find_entry(self.top, pattern) is None:
                return False
            self.ways.append(keys)
            return keys
        monkeypatch.setattr(town, "open_section", stand_in(town.open_section, open_section))


def test_a_purchase_left_as_it_was_is_tried_once_more_with_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = ticked(TownBot())
    shop = Shop(monkeypatch, tb)
    assert town._buy_section(as_bot(tb)) is True
    assert shop.ways == [False, True]
    assert any("\"Purchase Medicine\" did nothing - once more with the keys" in line for line in tb.lines)


def test_unticked_a_purchase_is_tried_once(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = TownBot()
    shop = Shop(monkeypatch, tb)
    assert town._buy_section(as_bot(tb)) is False
    assert shop.ways == [False]


def test_a_menu_with_no_buying_section_is_not_tried_with_the_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    tb = ticked(TownBot())
    shop = Shop(monkeypatch, tb)
    shop.top = town_menu(opt("welcome"))
    assert town._buy_section(as_bot(tb)) is False
    assert shop.ways == []
