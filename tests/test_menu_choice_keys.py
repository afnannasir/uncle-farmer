# pyright: reportPrivateUsage=false
"""A menu choice the game didn't act on. On one player's computer the agent's direct choice was
made and nothing happened (chests stayed, the airship never left, a dungeon didn't let us in),
while the same menus worked on another computer. The same menu still up after a direct choice is
said with the agent's answer, and the choices whose result is checked are tried once more with
the keys (the cursor walked to the entry, then Enter) - only when the character's
menu_keys_retry is ticked: it is off by default, and then nothing is tried again with the keys."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import JsonOnly, as_bot

from unclefarmer.engine import chests, dungeon
from unclefarmer.engine.botparts import dialogs as dialogs_mod
from unclefarmer.engine.botparts.travel import TravelMixin

from test_chests import Bot as ChestBot, chest, go
from test_dialogs import DlgBot, TimelineBot, entry, menu
from test_dungeon import Game, shop  # noqa: F401  # pyright: ignore[reportUnusedImport]  (the fixture, used by name)
from test_ride_level import Rider, TULIP, VIOLET, airship, leg_of


@pytest.fixture(autouse=True)
def dialogs_log(tmp_path: Any, monkeypatch: pytest.MonkeyPatch) -> None:
    """note_dialog writes logs/dialogs.log: keep it out of the repo."""
    from unclefarmer import paths
    monkeypatch.setattr(paths, "LOGS", str(tmp_path))


def direct(b: DlgBot, monkeypatch: pytest.MonkeyPatch, answer: str, then: Any = None) -> list[int]:
    """The agent's direct choose answers `answer`; then(b) is what the game does after it."""
    calls: list[int] = []

    def choose_at(bot: Any, top: dict[str, Any], row: int, col: int) -> bool:
        calls.append(row)
        bot.__dict__["last_choice"] = answer
        if then is not None:
            then(b)
        return True
    monkeypatch.setattr(dialogs_mod.menus, "choose_at", choose_at)
    return calls


# ---------------------------------------------------------------- the note after a direct choice
def test_a_menu_still_up_after_a_direct_choice_is_said_with_the_agents_answer(monkeypatch: pytest.MonkeyPatch) -> None:
    top = {"sig": 4, **menu([entry("Open the chest")])}
    b = TimelineBot(top, [(0.0, {"ok": True, "open": 1, "top": top})])     # the game did nothing

    def chosen(bot: Any) -> None:
        b.chosen_at = b.test_clock.now
    direct(b, monkeypatch, "chosen 0 0 enter false selected -129", chosen)
    assert b.choose(r"open the chest") is not None
    said = [line for line in b.logs if "left the menu open" in line]
    assert said and "enter false selected -129" in said[0]


def test_a_menu_the_game_closed_says_nothing_more(monkeypatch: pytest.MonkeyPatch) -> None:
    b = TimelineBot({"sig": 4, **menu([entry("Alpha")])}, [(0.0, {"ok": True, "open": 0, "top": None})])

    def chosen(bot: Any) -> None:
        b.chosen_at = b.test_clock.now
    direct(b, monkeypatch, "chosen 0 0 enter true selected -5", chosen)
    assert b.choose(r"^Alpha$") is not None
    assert not any("left the menu open" in line for line in b.logs)


def test_a_quantity_box_over_the_menu_is_no_menu_left_open(monkeypatch: pytest.MonkeyPatch) -> None:
    top = {"sig": 4, **menu([entry("Alpha")])}
    b = TimelineBot(top, [(0.0, {"ok": True, "open": 1, "top": top})])
    b.a = JsonOnly(lambda line: {"ok": True, "open": True} if line == "input" else {})

    def chosen(bot: Any) -> None:
        b.chosen_at = b.test_clock.now
    direct(b, monkeypatch, "chosen 0 0 enter true selected 0", chosen)
    assert b.choose(r"^Alpha$") is not None
    assert not any("left the menu open" in line for line in b.logs)


def test_choosing_with_the_keys_never_goes_through_the_agent(monkeypatch: pytest.MonkeyPatch) -> None:
    b = DlgBot(top={"sig": 9, **menu([entry("Alpha")], [entry("Beta")])})
    calls = direct(b, monkeypatch, "chosen 1 0 enter true selected 0")
    assert b.choose(r"^Beta$", keys=True) is not None
    assert calls == []                                       # not the direct choose
    assert "s" in b.keys and b.keys[-1] == "enter"           # walked down to it, then Enter


# ---------------------------------------------------------------- a chest
class StubbornChest(ChestBot):
    """The direct choice leaves the chest there; the keys open it (or not, opens_by_keys)."""

    def __init__(self, entities: list[dict[str, Any]], opens_by_keys: bool = True, retry: bool = True) -> None:
        super().__init__(entities)
        self.p["menu_keys_retry"] = retry
        self.opens = False
        self.opens_by_keys = opens_by_keys
        self.ways: list[bool] = []
        self.a = JsonOnly(lambda line: {"now": 7, "said": [{"text": "You are too far away"}]})

    def choose(self, pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        self.ways.append(keys)
        self.opens = keys and self.opens_by_keys
        return super().choose(pattern, row, keys)


def test_a_chest_the_direct_choice_left_is_opened_with_the_keys() -> None:
    b = StubbornChest([chest(x=110, y=104)])
    assert go(b)
    assert b.ways == [False, True] and len(b.talks) == 2
    assert b.chests.opened == 1
    assert any("once more with the keys" in line for line, _speak in b.lines)


def test_a_chest_that_stays_either_way_is_left_and_what_the_game_said_is_logged() -> None:
    b = StubbornChest([chest(x=110, y=104)], opens_by_keys=False)
    assert go(b)
    assert b.ways == [False, True]                           # no third try
    assert b.chests.opened == 0 and -129 in b.chests.skip
    lines = [line for line, _speak in b.lines]
    assert any(line.endswith("still there") for line in lines)
    assert any("the game said: You are too far away" in line for line in lines)


def test_without_the_setting_a_chest_that_stays_takes_one_try() -> None:
    b = StubbornChest([chest(x=110, y=104)], retry=False)
    assert go(b)
    assert b.ways == [False] and len(b.talks) == 1
    assert b.chests.opened == 0 and -129 in b.chests.skip
    lines = [line for line, _speak in b.lines]
    assert not any("once more with the keys" in line for line in lines)
    assert any("the game said: You are too far away" in line for line in lines)   # still noted


def test_the_setting_is_off_by_default() -> None:
    from unclefarmer import settings
    assert settings.PROFILE_DEFAULTS["menu_keys_retry"] is False


def test_a_chest_opened_at_once_takes_one_try() -> None:
    b = StubbornChest([chest(x=110, y=104)])
    b.opens_by_keys = True

    def at_once(pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        b.ways.append(keys)
        b.entities = [e for e in b.entities if not chests.is_chest(e)]
        return {"options": ["Open the chest"]}
    b.choose = at_once
    assert go(b)
    assert b.ways == [False] and b.chests.opened == 1


# ---------------------------------------------------------------- a dungeon's entry
class KeyedGame(Game):
    """Lets us in only when the entry is chosen with the keys."""

    def __init__(self, retry: bool = True, **kw: Any) -> None:
        super().__init__(lets_in=False, **kw)
        self.p["menu_keys_retry"] = retry
        self.ways: list[bool] = []

    def choose(self, pattern: str, row: str | None = None, keys: bool = False) -> dict[str, Any] | None:
        self.ways.append(keys)
        self.lets_in = keys
        return super().choose(pattern, row, keys)


@pytest.mark.usefixtures("shop")
def test_the_second_try_at_a_dungeon_chooses_with_the_keys() -> None:
    g = KeyedGame()
    assert dungeon.enter(as_bot(g), "Spider cave") == dungeon.IN
    assert g.ways == [False, True]


@pytest.mark.usefixtures("shop")
def test_without_the_setting_every_try_at_a_dungeon_is_the_direct_choice() -> None:
    g = KeyedGame(retry=False)
    assert dungeon.enter(as_bot(g), "Spider cave") == dungeon.MISSED
    assert g.ways == [False] * dungeon.TRIES


# ---------------------------------------------------------------- a ride
class KeyedRider(Rider):
    def __init__(self, retry: bool = True) -> None:
        super().__init__([])
        self.p["menu_keys_retry"] = retry
        self.ways: list[bool] = []

    def _ride(self, leg: dict[str, Any], mid: int, keys: bool = False) -> bool:
        self.ways.append(keys)
        return False


def test_the_second_try_at_a_transport_chooses_with_the_keys() -> None:
    who = KeyedRider()
    leg = leg_of(airship(TULIP, VIOLET))
    avoid: set[Any] = set()
    failed: dict[Any, int] = {}
    TravelMixin._take_leg(as_bot(who), leg, avoid, failed)
    TravelMixin._take_leg(as_bot(who), leg, avoid, failed)
    assert who.ways == [False, True] and leg["id"] in avoid


def test_without_the_setting_the_second_try_at_a_transport_is_the_direct_choice() -> None:
    who = KeyedRider(retry=False)
    leg = leg_of(airship(TULIP, VIOLET))
    avoid: set[Any] = set()
    failed: dict[Any, int] = {}
    TravelMixin._take_leg(as_bot(who), leg, avoid, failed)
    TravelMixin._take_leg(as_bot(who), leg, avoid, failed)
    assert who.ways == [False, False] and leg["id"] in avoid


def test_a_ride_that_failed_logs_what_the_game_said() -> None:
    who = Rider(["The airship has already left"])
    TravelMixin._take_leg(as_bot(who), leg_of(airship(TULIP, VIOLET)), set(), {})
    assert any("the game said: The airship has already left" in line for line in who.lines)
