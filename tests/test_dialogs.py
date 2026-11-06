"""The game's menus and NPCs: reading and choosing entries, popups, and talking to an NPC.

Fake menus carry their texts in `options`/`f`, as the real dialogs do (see tests/test_gang.py).
"""

from __future__ import annotations

import pytest

from unclefarmer import atlas, knowledge, paths, trust
from conftest import JsonOnly, stand_in, Clock

from unclefarmer.engine.botparts import dialogs as dialogs_mod
from unclefarmer.engine.botparts.dialogs import DialogsMixin
from unclefarmer.engine.botparts.base import Stopped
from pathlib import Path
from collections.abc import Callable, Iterable
from typing import Any
from unclefarmer.atlaslib import Cell


@pytest.fixture(autouse=True)
def dialogs_log(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """note_dialog writes logs/dialogs.log: keep it out of the repo."""
    monkeypatch.setattr(paths, "LOGS", str(tmp_path))


def entry(text: str, cmd: int=0) -> dict[str, Any]:
    return {"flags": 12, "options": [text], "cmd": [cmd]}


class DlgBot(DialogsMixin):
    NPC_SPOT = DialogsMixin.NPC_SPOT
    entry_texts = staticmethod(DialogsMixin.entry_texts)
    find_entry = DialogsMixin.find_entry
    choose = DialogsMixin.choose
    wait_dialog = DialogsMixin.wait_dialog
    note_dialog = DialogsMixin.note_dialog
    clear_popups = DialogsMixin.clear_popups
    find_npc = DialogsMixin.find_npc
    look_for_npc = DialogsMixin.look_for_npc
    talk = DialogsMixin.talk
    approach = DialogsMixin.approach

    def __init__(self, clock: Clock | None=None, top: dict[str, Any] | None=None, frozen: bool=False, invert: bool=False) -> None:
        self.test_clock = clock or Clock()      # the pretend clock, under its own type
        self.clock = self.test_clock
        self.p: dict[str, Any] = {"move_keys": {"up": "w", "down": "s", "left": "a", "right": "d"},
                  "esc_key": "esc", "enter_key": "enter", "close_popups": True}
        self.top = top
        self.open = top is not None
        self.frozen, self.invert, self.opposite_used = frozen, invert, False
        self.keys: list[str] = []
        self.logs: list[str] = []
        self.closed = 0
        self.character = "Hero"
        self.last_popup_check = 0.0
        self.trusted = trust.Trusted()
        self.a = JsonOnly(lambda line: {})        # each test that reads the agent gives its answers
        self.entities: list[dict[str, Any]] = []
        self.stuck_in_a_row = 7

    def log(self, line: str, speak: bool=False) -> None:
        self.logs.append(line)

    def map_id(self) -> int:
        return 1

    def me(self) -> dict[str, Any]:
        return {"x": 0.0, "y": 0.0}

    def snap(self) -> dict[str, Any]:
        return {"self": self.me(), "map": {"id": 1}, "entities": list(self.entities)}

    def sleep(self, seconds: float) -> None:
        self.test_clock.advance(seconds)

    def dialog(self) -> dict[str, Any]:
        if not self.open or self.top is None:
            return {"ok": True, "open": 0, "top": None}
        return {"ok": True, "open": 1, "top": self.top}

    def close_dialogs(self) -> None:
        self.closed += 1
        self.open = False
        self.top = None

    def key(self, k: str, ms: int = 60) -> None:
        self.keys.append(k)
        if self.top is None or self.frozen:
            return
        move = {"w": ("row", -1), "s": ("row", 1), "a": ("col", -1), "d": ("col", 1)}.get(k)
        if move is None:
            if k == "enter":
                self.open = False
            return
        axis, step = move
        if self.invert:
            step = -step
        self.top[axis] = max(0, self.top[axis] + step)


def menu(*rows: Iterable[dict[str, Any]]) -> dict[str, Any]:
    """A single open dialog with these rows."""
    return {"row": 0, "col": 0, "rows": [list(r) for r in rows]}


# ---------------------------------------------------------------- reading entries
def test_entry_texts_reads_every_field() -> None:
    e = {"text": "t", "raw": "r", "f": "f", "g": "g", "h": "h", "i": "i", "options": ["o"]}
    assert DialogsMixin.entry_texts(e) == ["t", "r", "f", "g", "h", "i", "o"]


def test_entry_texts_drops_the_empty_ones() -> None:
    assert DialogsMixin.entry_texts({"options": ["", None]}) == []


def test_find_entry_matches_by_regex_case_insensitively() -> None:
    b = DlgBot(top=menu([entry("my mission")], [entry("Harvest team")]))
    assert b.find_entry(b.top, r"harvest\s+team") == (1, 0)
    assert b.find_entry(b.top, r"nothing like this") is None


def test_dialog_raises_when_the_game_cannot_be_read() -> None:
    b = DlgBot()
    b.a = JsonOnly(lambda line: {"ok": False, "error": "old agent"})
    with pytest.raises(Stopped) as e:
        DialogsMixin.dialog(b)
    assert e.value.final is False
    assert "old agent" in str(e.value)


def test_dialog_retries_while_the_game_is_busy() -> None:
    b = DlgBot()
    answers = [{"ok": False, "error": "busy: the game kept changing a dialog"},
               {"ok": True, "open": 1, "top": menu([entry("hi")])}]
    b.a = JsonOnly(lambda line: answers.pop(0))
    b.TRANSIENT = ("busy:",)
    b.sleep = lambda seconds: None
    d = DialogsMixin.dialog(b)
    assert d["open"] == 1 and answers == []


def test_dialog_returns_the_open_dialog() -> None:
    b = DlgBot()
    b.a = JsonOnly(lambda line: {"ok": True, "open": 1, "top": menu([entry("hi")])})
    d = DialogsMixin.__dict__["dialog"](b)
    assert d["open"] == 1


# ---------------------------------------------------------------- choosing
def test_choose_walks_to_the_entry_and_enters() -> None:
    top = menu([entry("Alpha")], [entry("Beta")], [entry("Quit")])
    b = DlgBot(top=top)
    e = b.choose(r"^Beta$")
    assert e is not None and e["options"] == ["Beta"]
    assert "s" in b.keys and b.keys[-1] == "enter"
    assert any('choosing "Beta"' in line for line in b.logs)


def test_choose_already_on_the_entry_only_enters() -> None:
    b = DlgBot(top=menu([entry("Alpha")]))
    chosen = b.choose(r"^Alpha$")
    assert chosen is not None and chosen["options"] == ["Alpha"]
    assert b.keys == ["enter"]


def test_choose_no_match_closes_and_says_so() -> None:
    b = DlgBot(top=menu([entry("Alpha")]))
    assert b.choose(r"^Nope$") is None
    assert b.closed == 1 and any("no entry like" in line for line in b.logs)


def test_choose_gives_up_when_the_cursor_wont_move() -> None:
    b = DlgBot(top=menu([entry("Alpha")], [entry("Beta")]), frozen=True)
    assert b.choose(r"^Beta$") is None
    assert any("couldn't move to the menu entry" in line for line in b.logs)


def test_choose_turns_round_when_a_press_moves_the_wrong_way() -> None:
    top = menu([entry("Alpha")], [entry("Beta")], [entry("Gamma")])
    top["row"] = 2                                   # sitting on Gamma, heading for Alpha
    b = DlgBot(top=top, invert=True)
    e = b.choose(r"^Alpha$")
    assert e is not None and e["options"] == ["Alpha"]


def test_choose_with_no_dialog_open() -> None:
    b = DlgBot()
    assert b.choose(r"anything") is None


def test_wait_dialog_times_out_without_a_dialog(clock: Clock) -> None:
    b = DlgBot(clock=clock)
    assert b.wait_dialog(0.5) is None


def test_wait_dialog_returns_when_open(clock: Clock) -> None:
    b = DlgBot(clock=clock, top=menu([entry("yes")]))
    d = b.wait_dialog(0.5)
    assert d is not None and d["open"] == 1


def test_close_dialogs_presses_escape_until_shut() -> None:
    b = DlgBot(top=menu([entry("x")]))

    def key(k: str, ms: int = 60) -> None:
        b.keys.append(k)
        b.open = False
    b.key = key
    DialogsMixin.close_dialogs(b)
    assert b.keys == ["esc"]


# ---------------------------------------------------------------- popups
def invite_top() -> dict[str, Any]:
    return menu([entry('Friend invites you to join the "Harvest" team. Do you want to accept it?')],
                [entry("yes"), entry("no")])


def test_clear_popups_accepts_a_trusted_invite() -> None:
    b = DlgBot(top=invite_top())
    b.trusted = trust.Trusted(["friend"])
    assert b.clear_popups(force=True) is True
    assert any("joined Friend's team" in line for line in b.logs)


def test_clear_popups_declines_an_untrusted_invite() -> None:
    b = DlgBot(top=invite_top())
    b.trusted = trust.Trusted()
    assert b.clear_popups(force=True) is True
    assert b.closed == 1 and any("closed a window" in line for line in b.logs)


def test_clear_popups_closes_a_plain_notice() -> None:
    b = DlgBot(top=menu([entry("Server maintenance soon")]))
    assert b.clear_popups(force=True) is True
    assert b.closed == 1


def test_clear_popups_nothing_to_do() -> None:
    b = DlgBot()
    assert b.clear_popups(force=True) is False


def test_clear_popups_respects_the_profile_switch() -> None:
    b = DlgBot(top=menu([entry("notice")]))
    b.p["close_popups"] = False
    assert b.clear_popups(force=True) is False
    assert b.closed == 0


def test_clear_popups_waits_between_checks(clock: Clock) -> None:
    b = DlgBot(clock=clock, top=menu([entry("notice")]))
    b.last_popup_check = clock.now
    assert b.clear_popups() is False                  # under two seconds
    clock.advance(2.5)
    assert b.clear_popups() is True


# ---------------------------------------------------------------- NPCs
def test_find_npc_nearest_to_a_spot_and_within() -> None:
    b = DlgBot()
    b.entities = [{"name": "Guard", "x": 10.0, "y": 10.0},
                  {"name": "Guard", "x": 3.0, "y": 3.0},
                  {"name": "Merchant", "x": 1.0, "y": 1.0}]
    npc = b.find_npc("guard", near=(4.0, 3.0))
    assert npc is not None and npc["x"] == 3.0
    assert b.find_npc("Guard", near=(4.0, 3.0), within=0.5) is None
    assert b.find_npc("Nobody") is None


class FakeModel:
    def ok(self, x: int, y: int) -> bool:
        return True

    def nearest_walkable(self, x: int, y: int, r: int, comp: int | None=None) -> Cell | None:
        return (int(x), int(y) + 1)

    def route(self, a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]]:
        return [a, b]

    def line(self, a: Cell, b: Cell) -> list[Cell]:
        return [a, b]

    def comp_of(self, x: int, y: int) -> int:
        return 0


@pytest.fixture
def fake_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: FakeModel()))


def test_look_for_npc_walks_to_where_the_data_puts_it(fake_model: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": [{"map": 1, "name": "Apothecary",
                                                              "x": 5, "y": 5}]}))
    monkeypatch.setattr(knowledge, "cities", stand_in(knowledge.cities, lambda: []))
    b = DlgBot()
    walked: list[tuple[float, ...]] = []
    b.walk_to = lambda cell, map_change=False, near=None: walked.append(tuple(cell)) or "arrived"
    b.find_npc = lambda name, near=None, within=None: {"name": "Apothecary", "x": 5.0, "y": 5.0}
    npc = DialogsMixin.look_for_npc(b, "Apothecary")
    assert npc is not None and npc["name"] == "Apothecary"
    assert walked and any("isn't in sight" in line for line in b.logs)


def test_look_for_npc_with_nowhere_to_look(fake_model: None, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": []}))
    monkeypatch.setattr(knowledge, "cities", stand_in(knowledge.cities, lambda: []))
    b = DlgBot()
    assert DialogsMixin.look_for_npc(b, "Nobody") is None


class SearchBot(DlgBot):
    """A bot that records where look_for_npc walks and can change map mid-sweep."""

    def __init__(self, model: SearchModel | None=None, **kw: Any) -> None:
        super().__init__(**kw)
        self.model = model or SearchModel()
        self.walks: list[tuple[float, float]] = []
        self.current_map = 1
        self.change_map_after: int | None = None

    def map_id(self) -> int:
        return self.current_map

    def me(self) -> dict[str, Any]:
        return {"x": 0.0, "y": 0.0}

    def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        self.walks.append((cell[0], cell[1]))
        if self.change_map_after is not None and len(self.walks) >= self.change_map_after:
            self.current_map = 2
        return "arrived"

    def find_npc(self, name: str, near: tuple[float, float] | None = None, within: float | None = None) -> dict[str, Any] | None:
        return None


class SearchModel:
    def __init__(self, no_route: set[tuple[int, int]] | tuple[()]=(), no_walkable: set[tuple[int, int]] | tuple[()]=(), nearest: dict[tuple[int, int], tuple[int, int]] | None=None) -> None:
        self.no_route = set(no_route)
        self.no_walkable = set(no_walkable)
        self.nearest = nearest or {}

    def ok(self, x: int, y: int) -> bool:
        return (x, y) not in self.no_walkable

    def nearest_walkable(self, x: int, y: int, r: int, comp: None=None) -> tuple[int, int] | None:
        if (x, y) in self.no_walkable:
            return self.nearest.get((x, y))
        return (int(x), int(y))

    def route(self, a: tuple[int, int], b: tuple[int, int]) -> list[tuple[int, int]] | None:
        return None if tuple(b) in self.no_route else [a, b]


def _search_data(monkeypatch: pytest.MonkeyPatch, npcs: list[dict[str, Any]], cities: list[dict[str, Any]] | tuple[()]=()) -> None:
    monkeypatch.setattr(knowledge, "data", stand_in(knowledge.data, lambda: {"npcs": list(npcs)}))
    monkeypatch.setattr(knowledge, "cities", stand_in(knowledge.cities, lambda: list(cities)))


def _patch_model(monkeypatch: pytest.MonkeyPatch, bot: SearchBot) -> None:
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, lambda mid: bot.model))


def test_look_for_npc_visits_data_spots_square_then_sweep_nearest_first(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(
        monkeypatch,
        [{"map": 1, "name": "Baker", "x": 5, "y": 5},
         {"map": 2, "name": "Baker", "x": 99, "y": 99},
         {"map": 1, "name": "Other", "x": 88, "y": 88}],
        [{"map": 1, "square_cell": [50, 50], "town_rect": [0, 0, 100, 100]},
         {"map": 2, "square_cell": [1, 1], "town_rect": [0, 0, 100, 100]}])
    monkeypatch.setattr(dialogs_mod, "steps", stand_in(dialogs_mod.steps, lambda lo, hi, most: [10, 20]))
    b = SearchBot(SearchModel())
    _patch_model(monkeypatch, b)
    # The map's data spot comes first, then the town square, then the sweep. The sweep starts
    # from us at (0, 0), not from the square, and always takes the nearest remaining cell.
    assert b.look_for_npc("Baker") is None
    assert b.walks == [(5, 5), (50, 50), (10, 10), (10, 20), (20, 20), (20, 10)]


def test_look_for_npc_stops_at_the_first_one_that_comes_into_view(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(monkeypatch, [{"map": 1, "name": "Baker", "x": 5, "y": 5},
                               {"map": 1, "name": "Baker", "x": 6, "y": 6}])
    b = SearchBot()
    _patch_model(monkeypatch, b)
    calls: list[str] = []

    def find(name: str, near: tuple[float, float] | None = None, within: float | None = None) -> dict[str, Any] | None:
        calls.append(name)
        return {"name": name, "x": 6.0, "y": 6.0} if len(b.walks) >= 2 else None

    b.find_npc = find
    npc = b.look_for_npc("Baker")
    assert npc == {"name": "Baker", "x": 6.0, "y": 6.0}
    assert b.walks == [(5, 5), (6, 6)]
    assert calls.count("Baker") == 2


def test_look_for_npc_bails_out_when_the_walk_changes_map(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(monkeypatch, [{"map": 1, "name": "Baker", "x": 5, "y": 5},
                               {"map": 1, "name": "Baker", "x": 6, "y": 6}])
    b = SearchBot()
    _patch_model(monkeypatch, b)
    b.change_map_after = 1
    assert b.look_for_npc("Baker") is None
    assert b.walks == [(5, 5)]          # no second spot after leaving the map


def test_look_for_npc_skips_a_spot_with_no_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(monkeypatch, [{"map": 1, "name": "Baker", "x": 5, "y": 5},
                               {"map": 1, "name": "Baker", "x": 6, "y": 6}])
    model = SearchModel(no_route={(5, 5)})
    b = SearchBot(model)
    _patch_model(monkeypatch, b)
    assert b.look_for_npc("Baker") is None
    assert b.walks == [(6, 6)]


def test_look_for_npc_returns_none_when_no_spot_has_a_route(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(monkeypatch, [{"map": 1, "name": "Baker", "x": 5, "y": 5}])
    b = SearchBot(SearchModel(no_route={(5, 5)}))
    _patch_model(monkeypatch, b)
    assert b.look_for_npc("Baker") is None
    assert b.walks == []


def test_look_for_npc_skips_a_spot_with_no_walkable_cell(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(monkeypatch, [{"map": 1, "name": "Baker", "x": 5, "y": 5}])
    b = SearchBot(SearchModel(no_walkable={(5, 5)}))
    _patch_model(monkeypatch, b)
    assert b.look_for_npc("Baker") is None
    assert b.walks == []


def test_look_for_npc_moves_to_walkable_cells_before_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    _search_data(monkeypatch, [{"map": 1, "name": "Baker", "x": 5, "y": 5},
                               {"map": 1, "name": "Baker", "x": 6, "y": 6}])
    model = SearchModel(no_walkable={(0, 0), (6, 6)}, nearest={(0, 0): (1, 1), (6, 6): (7, 7)})
    b = SearchBot(model)
    _patch_model(monkeypatch, b)
    seen_routes: list[tuple[tuple[int, ...], tuple[int, ...]]] = []
    real_route = model.route
    model.route = lambda a, b: (seen_routes.append((tuple(a), tuple(b))), real_route(a, b))[1]
    assert b.look_for_npc("Baker") is None
    assert seen_routes == [((1, 1), (5, 5)), ((1, 1), (7, 7))]
    assert b.walks == [(5, 5), (7, 7)]


# ---------------------------------------------------------------- talking
class FakeNpcBot(DlgBot):
    def __init__(self, **kw: Any) -> None:
        super().__init__(**kw)
        self.target_modes: list[int] = []
        self.selects: list[dict[str, Any]] = []
        self.dialog_ready = True
        self.npc: dict[str, Any] | None = None
        self.approach_result: dict[str, Any] | None = None
        self.walk_result = "arrived"

    def set_target_mode(self, want: int) -> bool:
        self.target_modes.append(want)
        return True

    def select_target(self, mob: dict[str, Any], by_keys: bool=False) -> bool:
        self.selects.append(mob)
        self.by_keys = getattr(self, "by_keys", []) + [by_keys]
        return True

    def approach(self, name: str, at: tuple[float, float] | None=None, stand: tuple[float, float] | None=None) -> dict[str, Any] | None:
        return self.approach_result

    def walk_to(self, cell: tuple[float, float], map_change: bool = False,
                near: Callable[[dict[str, Any]], bool] | None = None) -> str:
        return self.walk_result


def test_talk_opens_the_dialog() -> None:
    b = FakeNpcBot()
    b.approach_result = {"name": "Guard", "x": 2.0, "y": 0.0}
    b.dialog_ready = True
    b.wait_dialog = lambda seconds=3.0, want_open=True: {"open": 1, "top": menu([entry("hi")])}
    assert DialogsMixin.talk(b, "Guard", at=(2, 0)) is True
    assert b.target_modes == [3]
    assert b.selects and b.keys[-1] == "enter"


def test_talk_when_the_npc_isnt_found() -> None:
    b = FakeNpcBot()
    assert DialogsMixin.talk(b, "Guard") is False
    assert b.talk_problem == "not found"


def test_talk_when_it_stops_out_of_reach() -> None:
    b = FakeNpcBot()
    b.approach_result = {"name": "Guard", "x": 100.0, "y": 0.0}
    assert DialogsMixin.talk(b, "Guard") is False
    assert b.talk_problem == "out of reach"


def test_talk_when_the_npc_never_answers() -> None:
    b = FakeNpcBot()
    b.approach_result = {"name": "Guard", "x": 2.0, "y": 0.0}
    b.wait_dialog = lambda seconds=3.0, want_open=True: None
    assert DialogsMixin.talk(b, "Guard") is False
    assert b.talk_problem == "no answer"
    assert b.by_keys == [False, False, True]     # the last try with the game's own target keys


def test_approach_walks_to_a_cell_beside_the_npc(fake_model: None) -> None:
    b = FakeNpcBot()
    b.npc = {"name": "Guard", "x": 5.0, "y": 5.0}
    b.find_npc = lambda name, near=None, within=None: b.npc
    walked: list[tuple[float, ...]] = []
    b.walk_to = lambda cell, map_change=False, near=None: walked.append(tuple(cell)) or "arrived"
    npc = DialogsMixin.approach(b, "Guard", at=(5, 5), stand=(5, 7))
    assert npc is b.npc and walked == [(5, 7)]


def test_approach_without_finding_anyone(fake_model: None) -> None:
    b = FakeNpcBot()
    b.find_npc = lambda name, near=None, within=None: None
    b.look_for_npc = lambda name: None
    assert DialogsMixin.approach(b, "Nobody") is None
    assert any("can't find Nobody" in line for line in b.logs)
