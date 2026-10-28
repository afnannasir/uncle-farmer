"""Going into a gang base: our own, or another gang's by name, from the Gang Entrance's menus."""

from __future__ import annotations

import threading

import pytest

from unclefarmer.clock import CLOCK

from unclefarmer.engine import crafting, town
from unclefarmer.engine.bot import Bot
from conftest import as_bot
from typing import Any


def entry(text: str, cmd: int=0) -> dict[str, Any]:
    return {"flags": 12, "options": [text], "cmd": [cmd]}


ENTRANCE = [[entry("Enter the Outer hall of the gang", 1)], [entry("Enter the inner  hall of the gang", 2)],
            [entry("Allied gangs", 3)], [entry("all gangs", 4)]]


class FakeGame:
    """The Gang Entrance menu and what each choice leads to."""

    def __init__(self, after_all: list[Any], land: tuple[int, int]=(41, 85), other_land: tuple[int, int]=(41, 85)) -> None:
        self.map = 1041
        self.pos = {"x": 100.0, "y": 100.0}
        self.rows = None
        self.after_all = after_all          # list of screens after "all gangs"
        self.land, self.other_land = land, other_land
        self.input_open = False
        self.typed = ""
        self.screen: str | None = None
        self.screen_idx = -1                # which of after_all is showing (none until "all gangs")

    def open(self, rows: list[list[dict[str, Any]]] | None, name: str) -> None:
        self.rows, self.screen = rows, name


class FB:
    find_entry = Bot.find_entry
    entry_texts = staticmethod(Bot.entry_texts)

    def __init__(self, g: FakeGame) -> None:
        self.g = g
        self.logs: list[str] = []
        self.stop_event = threading.Event()          # never set: the run goes on
        self.a = self

    # agent link
    def json(self, c: str) -> dict[str, Any]:
        if c == "input":
            return {"ok": True, "open": self.g.input_open, "mode": 3}
        return {}

    def send(self, c: str) -> str:
        if c == "typeclear":
            self.g.typed = ""
            return "cleared"
        if c.startswith("type "):
            self.g.typed += c[5:]
            return "typed %d" % len(c[5:])
        if c == "typeenter":
            self.g.input_open = False
            self.g.screen_idx += 1
            self.g.open(*self.g.after_all[self.g.screen_idx])
            return "entered"
        return "?"

    def log(self, m: str, speak: bool=False) -> None:
        self.logs.append(m)

    def map_id(self) -> int:
        return self.g.map

    def me(self) -> dict[str, Any]:
        return dict(self.g.pos)

    def snap(self) -> dict[str, Any]:
        return {"self": self.me(), "map": {"id": self.g.map, "name": "Some Guild encampment"}}

    def close_dialogs(self) -> None:
        self.g.rows = None

    def dialog(self) -> dict[str, Any]:
        if self.g.rows is None:
            return {"open": 0, "top": None}
        return {"open": 1, "top": {"row": 0, "col": 0, "rows": self.g.rows}}

    def wait_dialog(self, seconds: float=3.0, want_open: bool=True) -> dict[str, Any] | None:
        d = self.dialog()
        return d if (d["open"] > 0) == want_open else None

    def sleep(self, s: float) -> None:
        pass

    def wait_map_change(self, mid: int, secs: int) -> bool:
        return self.g.map != mid

    def choose(self, pattern: str) -> dict[str, Any] | None:
        top = self.dialog().get("top")
        if not top:
            return None
        where = self.find_entry(top, pattern)
        if where is None:
            self.close_dialogs()
            return None
        e = top["rows"][where[0]][where[1]]
        text = e["options"][0]
        g = self.g
        if text.startswith("Enter the Outer hall"):
            g.map, g.rows = 4004, None
            g.pos = {"x": float(g.land[0] if g.screen == "entrance" else g.other_land[0]),
                     "y": float(g.land[1] if g.screen == "entrance" else g.other_land[1])}
        elif text.startswith("Enter the inner"):
            g.map, g.rows, g.pos = 4004, None, {"x": 41.0, "y": 45.0}
        elif text in ("all gangs", "Allied gangs"):
            g.screen_idx = 0
            g.open(*g.after_all[0])
        else:
            g.screen_idx += 1
            if g.screen_idx < len(g.after_all):
                g.open(*g.after_all[g.screen_idx])
                if g.screen == "input":
                    g.input_open, g.rows = True, None
            else:
                g.rows = None
        self.chosen = getattr(self, "chosen", []) + [text]
        return e


def visit(b: FB, role: str, candidates: None=None) -> None:
    b.g.map = 1041
    b.g.open(ENTRANCE, "entrance")


class Ticks:
    """A clock that moves 0.2 s on every read: town.wait_input's real-time deadline runs out in
    a dozen reads instead of making each test spin until its timeout."""

    def __init__(self) -> None:
        self.now = 1000.0

    def time(self) -> float:
        self.now += 0.2
        return self.now


@pytest.fixture(autouse=True)
def gang_entrance(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(town, "visit", visit)
    monkeypatch.setattr(CLOCK, "time", Ticks().time)


def test_own_gang_through_the_outer_hall() -> None:
    b = FB(FakeGame([]))
    assert crafting.enter_gang(as_bot(b)) is True and b.chosen == ["Enter the Outer hall of the gang"]


def test_landing_in_the_inner_half_is_caught() -> None:
    b = FB(FakeGame([], land=(41, 45)))
    assert crafting.enter_gang(as_bot(b)) is False and any("walled off" in line for line in b.logs)


def test_from_the_inner_half_it_leaves_and_comes_back_in() -> None:
    g = FakeGame([])
    g.map, g.pos = 4004, {"x": 41.0, "y": 45.0}
    b = FB(g)
    assert crafting.enter_gang(as_bot(b)) is True and b.chosen == ["Enter the Outer hall of the gang"]


def test_another_gang_from_a_paged_list() -> None:
    pages = [([[entry("Alpha")], [entry("Beta")], [entry("next page")]], "p1"),
             ([[entry("Gamma")], [entry("Iron Wolves")], [entry("next page")]], "p2"),
             ([[entry("Enter the Outer hall of the gang")], [entry("Enter the inner  hall of the gang")]], "gang")]
    b = FB(FakeGame(pages))
    assert crafting.enter_gang(as_bot(b), "iron wolves") is True
    assert b.chosen == ["all gangs", "next page", "Iron Wolves", "Enter the Outer hall of the gang"]


def test_another_gang_through_the_name_box() -> None:
    screens = [([[entry("Search gang by name")]], "search"), (None, "input"),
               ([[entry("Iron Wolves")]], "result"),
               ([[entry("Enter the Outer hall of the gang")]], "gang")]
    g = FakeGame(screens)
    b = FB(g)
    assert crafting.enter_gang(as_bot(b), "Iron Wolves") is True and g.typed == "Iron Wolves"


def test_a_gang_that_isnt_listed() -> None:
    b = FB(FakeGame([([[entry("Alpha")]], "p1")]))
    assert crafting.enter_gang(as_bot(b), "Nobody") is False and any("no gang called Nobody" in line for line in b.logs)


def test_place() -> None:
    assert crafting.place({"where": "other_gang", "gang_name": " Iron Wolves "}) == "gang:Iron Wolves"
    assert crafting.place({"where": "other_gang", "gang_name": ""}) == "gang"
    assert crafting.place({}) == "gang" and crafting.place({"where": "city"}) == "city"
