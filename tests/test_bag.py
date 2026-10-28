# pyright: reportPrivateUsage=false
# White-box tests: they check this module's internal helpers (leading-underscore names) directly.
"""The bag panels and backpack: stepping between panels, walking the panel and bag cursors, using
and throwing items, the bag-full rules, and throwing by the bag manager's rules.

Everything runs against a stand-in game: a link whose "mem bag" answer is a list of rows, and a
panel / category / cursor that the stand-in key presses move the way the real game's arrow keys do.
"""

from __future__ import annotations

import pytest
from conftest import as_kind, returning, stand_in, Clock

from unclefarmer.clock import CLOCK
from unclefarmer import settings, stats
from unclefarmer.engine import town
from unclefarmer.engine.botparts.bag import BagMixin
from unclefarmer.engine.botparts.base import Stopped
from typing import Any


class Link:
    def __init__(self, bag: list[dict[str, Any]] | None=None) -> None:
        self.bag = [dict(r) for r in (bag or [])]

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem bag":
            return [dict(r) for r in self.bag]
        return {}

    def send(self, line: str) -> str:
        return "ok"


class Tally:
    def __init__(self) -> None:
        self.owners: list[Bot] = []

    def update(self, owner: Bot) -> None:
        self.owners.append(owner)


class Bot(BagMixin):
    """Only the pieces the bag mixin touches, with a game whose keys move the cursors."""

    def __init__(self, bag: list[dict[str, Any]] | None=None, panel: int=0, category: int=0, cursor: int=0) -> None:
        self.link = Link(bag)
        self.a = self.link
        self.p = dict(settings.PROFILE_DEFAULTS)
        self.panel, self.category, self.cursor = panel, category, cursor
        self.cursor_size = 0           # the panel's row count, when there is no bag to size it
        self.lines: list[str] = []
        self.keys: list[str] = []
        self.sleeps: list[float] = []
        self.ui_reads = True
        self.ui_freeze = False         # key presses move nothing at all
        self.ui_goes_dark = False      # the game stops answering after the first press
        self.panel_wanders = False     # a press also drifts the panel
        self.category_stuck = False    # the category key does nothing
        self.tally = None
        self.load_seen = []
        self.last_sheet = 0.0
        self.kills = 0
        self.load_share = None
        self.said_bag_full = False
        self.bag_start = None
        self.finish_calls = 0
        self._dropped: list[dict[str, Any]] = []
        self._sheet_calls: list[bool] = []
        self._sheet_queue: list[dict[str, Any] | None] = []
        self._sheet_fixed: dict[str, Any] | None = None

    # --- the bot's own small surface
    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def ui(self) -> dict[str, Any]:
        if not self.ui_reads:
            return {}                   # the game told us nothing
        return {"panel": self.panel, "category": self.category, "cursor": self.cursor}

    def key(self, k: str, ms: int = 60) -> None:
        self.keys.append(k)
        if self.ui_goes_dark:
            self.ui_reads = False
        if self.ui_freeze:
            return
        if self.panel_wanders:
            self.panel = (self.panel + 1) % 7
        if k == self.p["bag_right"]:
            self.panel = (self.panel + 1) % 7
        elif k == self.p["bag_left"]:
            self.panel = (self.panel - 1) % 7
        elif k == self.p["category_key"] and not self.category_stuck:
            self.category = (self.category + 1) % 7
        elif k == self.p["bag_down"]:
            size = len(self.link.bag) or self.cursor_size
            if size:
                self.cursor = (self.cursor + 1) % size
        elif k == self.p["bag_up"]:
            size = len(self.link.bag) or self.cursor_size
            if size:
                self.cursor = (self.cursor - 1) % size

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    # --- the parts bag_check leans on
    def refresh_sheet(self, force: bool=False) -> dict[str, Any] | None:
        self._sheet_calls.append(force)
        if self._sheet_queue:
            return self._sheet_queue.pop(0)
        return self._sheet_fixed

    def drop_time_check(self, sheet: dict[str, Any]) -> None:
        self._dropped.append(sheet)

    def finish(self) -> None:
        self.finish_calls += 1


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> Clock:
    c = Clock()
    monkeypatch.setattr(CLOCK, "time", c)
    return c


# ---------------------------------------------------------------- goto_panel
def test_goto_panel_already_there_needs_no_keys() -> None:
    b = Bot(panel=2)
    assert b.goto_panel(2) == {"panel": 2, "category": 0, "cursor": 0}
    assert b.keys == [] and b.sleeps == []


def test_goto_panel_steps_right_the_short_way() -> None:
    b = Bot(panel=0)
    moved = b.goto_panel(3)
    assert moved is not None and moved["panel"] == 3
    assert b.keys == ["right", "right", "right"]


def test_goto_panel_steps_back_when_that_is_shorter() -> None:
    b = Bot(panel=0)
    moved = b.goto_panel(6)
    assert moved is not None and moved["panel"] == 6
    assert b.keys == ["left"]
    b2 = Bot(panel=5)
    moved = b2.goto_panel(0)
    assert moved is not None and moved["panel"] == 0
    assert b2.keys == ["right", "right"]


def test_goto_panel_gives_up_on_a_panel_that_never_comes() -> None:
    b = Bot(panel=0)
    assert b.goto_panel(7) is None       # 7 is not a panel; the arrow keys just wrap
    assert len(b.keys) == 8


def test_goto_panel_returns_none_without_a_ui() -> None:
    b = Bot(panel=0)
    b.ui_reads = False
    assert b.goto_panel(3) is None
    assert b.keys == []


def test_goto_panel_returns_none_when_the_ui_goes_dark() -> None:
    b = Bot(panel=0)
    b.ui_goes_dark = True
    assert b.goto_panel(3) is None
    assert len(b.keys) == 1


# ---------------------------------------------------------------- panel_cursor
def test_panel_cursor_already_on_the_row() -> None:
    b = Bot(panel=0, cursor=3)
    assert b.panel_cursor(0, 3, 10) is True
    assert b.keys == []


def test_panel_cursor_walks_down_and_up_the_short_way() -> None:
    b = Bot(panel=0, cursor=0)
    b.cursor_size = 10
    assert b.panel_cursor(0, 2, 10) is True
    assert b.keys == ["down", "down"]
    b2 = Bot(panel=0, cursor=1)
    b2.cursor_size = 10
    assert b2.panel_cursor(0, 9, 10) is True
    assert b2.keys == ["up", "up"]


def test_panel_cursor_with_a_cursor_off_the_top() -> None:
    b = Bot(panel=0, cursor=-1)
    b.cursor_size = 5
    assert b.panel_cursor(0, 0, 5) is True
    assert b.keys == ["down"]


def test_panel_cursor_logs_when_the_panel_is_out_of_reach() -> None:
    b = Bot(panel=0)
    b.goto_panel = lambda panel: None
    assert b.panel_cursor(3, 0, 5) is False
    assert any("couldn't get to panel" in line for line in b.lines)


def test_panel_cursor_gives_up_when_the_cursor_will_not_move() -> None:
    b = Bot(panel=0, cursor=0)
    b.ui_freeze = True
    assert b.panel_cursor(0, 3, 10) is False
    assert len(b.keys) == 13             # size + 3 tries


def test_panel_cursor_stops_when_the_panel_moves_under_it() -> None:
    b = Bot(panel=0, cursor=0)
    b.panel_wanders = True
    assert b.panel_cursor(0, 3, 10) is False


# ---------------------------------------------------------------- use_item
def test_use_item_returns_false_when_the_bag_has_no_such_item() -> None:
    b = Bot()
    assert b.use_item("Mystery Rock") is False
    assert b.keys == []
    assert any("there's no Mystery Rock in the bag" in line for line in b.lines)


def test_use_item_presses_the_use_key_on_the_item() -> None:
    b = Bot(bag=[{"name": "Large Red Potion", "count": 3}])
    assert b.use_item("Large Red Potion") is True
    assert b.keys == ["rctrl"]
    assert any("using Large Red Potion from the bag" in line for line in b.lines)


# ---------------------------------------------------------------- bag_rows
def test_bag_rows_reads_the_mem_bag_list() -> None:
    b = Bot(bag=[{"name": "Junk", "count": 2}])
    assert b.bag_rows() == [{"name": "Junk", "count": 2}]


def test_bag_rows_is_empty_when_the_game_sends_something_else() -> None:
    b = Bot()
    b.a.json = lambda line: {"not": "a list"}
    assert b.bag_rows() == []


# ---------------------------------------------------------------- bag_cursor
def test_bag_cursor_on_the_item_already() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}, {"name": "Beta", "count": 1}], cursor=1)
    assert b.bag_cursor("Beta") == "Beta"
    assert b.keys == []


def test_bag_cursor_walks_to_the_item() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}, {"name": "Beta", "count": 1},
                 {"name": "Gamma", "count": 1}], cursor=0)
    assert b.bag_cursor("gamma") == "Gamma"
    assert b.keys == ["up"]              # the short way round the three rows


def test_bag_cursor_matches_a_part_of_a_name() -> None:
    b = Bot(bag=[{"name": "Large Red Potion", "count": 4}])
    assert b.bag_cursor("red potion") == "Large Red Potion"


def test_bag_cursor_unknown_item() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}])
    assert b.bag_cursor("Nope") is None
    assert any("there's no Nope in the bag" in line for line in b.lines)
    assert b.keys == []


def test_bag_cursor_needs_a_readable_ui() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}])
    b.ui_reads = False
    assert b.bag_cursor("Alpha") is None
    assert any("can't read the game's bag cursor" in line for line in b.lines)


def test_bag_cursor_finds_its_way_to_panel_and_category_zero() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}], panel=1, category=1, cursor=0)
    assert b.bag_cursor("Alpha") == "Alpha"
    assert b.panel == 0 and b.category == 0


def test_bag_cursor_gives_up_when_the_category_will_not_set() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}], panel=0, category=1)
    b.category_stuck = True
    assert b.bag_cursor("Alpha") is None
    assert any("couldn't get to the bag with the arrow keys" in line for line in b.lines)


def test_bag_cursor_gives_up_when_it_cannot_reach_the_item() -> None:
    b = Bot(bag=[{"name": "Alpha", "count": 1}, {"name": "Beta", "count": 1},
                 {"name": "Gamma", "count": 1}], cursor=0)
    b.goto_panel = lambda panel: {"panel": 0, "category": 0, "cursor": 0}
    b.ui_freeze = True
    assert b.bag_cursor("Gamma") is None
    assert any("couldn't reach Gamma in the bag" in line for line in b.lines)


# ---------------------------------------------------------------- bag_stalled
def test_bag_stalled_off_below_the_mark(clock: Clock) -> None:
    b = Bot()
    b.load_seen = [(1.0, 5, 90)]
    assert b.bag_stalled({"load": 50, "maxload": 100}, 90) is False
    assert b.load_seen == []


def test_bag_stalled_when_the_load_stops_rising(clock: Clock) -> None:
    b = Bot()
    b.last_sheet, b.kills = clock.now, 10
    assert b.bag_stalled({"load": 98, "maxload": 100}, 100) is False
    b.last_sheet, b.kills = clock.now + 30, 12
    assert b.bag_stalled({"load": 98, "maxload": 100}, 100) is False
    b.last_sheet, b.kills = clock.now + 60, 15
    assert b.bag_stalled({"load": 98, "maxload": 100}, 100) is True


def test_bag_stalled_needs_a_minute_of_readings(clock: Clock) -> None:
    # read without the key (agent 43) a sheet comes every few seconds: three of them 10 s apart
    # with no loot in between are no sign of a full bag
    b = Bot()
    for i, kills in enumerate((10, 13, 16)):
        b.last_sheet, b.kills = clock.now + 10 * i, kills
        assert b.bag_stalled({"load": 98, "maxload": 100}, 100) is False


def test_bag_stalled_needs_five_kills_between_readings(clock: Clock) -> None:
    b = Bot()
    b.last_sheet, b.kills = clock.now, 10
    b.bag_stalled({"load": 98, "maxload": 100}, 100)
    b.last_sheet, b.kills = clock.now + 1, 11
    b.bag_stalled({"load": 98, "maxload": 100}, 100)
    b.last_sheet, b.kills = clock.now + 2, 12
    assert b.bag_stalled({"load": 98, "maxload": 100}, 100) is False


def test_bag_stalled_ignores_readings_older_than_five_minutes(clock: Clock) -> None:
    b = Bot()
    b.last_sheet, b.kills = clock.now, 10
    b.bag_stalled({"load": 98, "maxload": 100}, 100)
    clock.advance(301)
    b.last_sheet, b.kills = clock.now, 20
    b.bag_stalled({"load": 98, "maxload": 100}, 100)
    b.last_sheet, b.kills = clock.now + 1, 30
    assert b.bag_stalled({"load": 98, "maxload": 100}, 100) is False


# ---------------------------------------------------------------- bag_check
def test_bag_check_without_a_sheet_does_nothing_except_tally() -> None:
    b = Bot()
    assert b.bag_check() is False
    tally = Tally()
    b.tally = as_kind(stats.Tally, tally)
    assert b.bag_check() is False
    assert tally.owners == [b]


def test_bag_check_ignores_a_sheet_with_no_load_limit() -> None:
    b = Bot()
    b._sheet_fixed = {"load": 50, "maxload": 0}
    assert b.bag_check() is False
    b._sheet_fixed = {"load": -1, "maxload": 100}
    assert b.bag_check() is False


def test_bag_check_leaves_a_bag_below_the_mark_alone() -> None:
    b = Bot()
    b._sheet_fixed = {"load": 50, "maxload": 100}
    assert b.bag_check() is False
    assert b.load_share == 50.0


def test_bag_check_says_the_bag_is_full_once() -> None:
    b = Bot()
    b.p["when_bag_full"] = "nothing"
    b.p["bag_full_at"] = 50
    b._sheet_fixed = {"load": 100, "maxload": 100}
    assert b.bag_check() is False
    assert b.said_bag_full is True
    assert any("the bag is 100% full (load 100 of 100)" in line for line in b.lines)
    n = len(b.lines)
    assert b.bag_check() is False
    assert len(b.lines) == n             # said once


def test_bag_check_stops_when_the_bag_is_full_of_kept_things() -> None:
    b = Bot()
    b.p["when_bag_full"] = "stop"
    b.p["bag_full_at"] = 50
    b._sheet_fixed = {"load": 100, "maxload": 100}
    with pytest.raises(Stopped):
        b.bag_check()
    assert b.finish_calls == 1
    assert any("going somewhere safe and stopping" in line for line in b.lines)


def test_bag_check_throws_first_and_carries_on_when_that_frees_room() -> None:
    b = Bot()
    b.p["when_bag_full"] = "trip"
    b.p["bag_full_at"] = 50
    b._sheet_queue = [{"load": 100, "maxload": 100}, {"load": 10, "maxload": 100}]
    b.throw_by_rules = lambda: True
    assert b.bag_check() is False
    assert b._sheet_calls == [False, True]


def test_bag_check_trips_to_town_and_reports_success(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.p["when_bag_full"] = "trip"
    b.p["bag_full_at"] = 50
    b._sheet_queue = [{"load": 100, "maxload": 100}, {"load": 100, "maxload": 100}]
    b.throw_by_rules = lambda: False
    monkeypatch.setattr(town, "unload", stand_in(town.unload, lambda b: 2))
    assert b.bag_check() is True
    assert b.finish_calls == 0
    assert any("going to sell and store" in line for line in b.lines)


def test_bag_check_stops_when_the_trip_frees_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.p["when_bag_full"] = "trip"
    b.p["bag_full_at"] = 50
    b._sheet_queue = [{"load": 100, "maxload": 100}, {"load": 100, "maxload": 100}]
    b.throw_by_rules = lambda: False
    monkeypatch.setattr(town, "unload", stand_in(town.unload, lambda b: 0))
    with pytest.raises(Stopped) as caught:
        b.bag_check()
    assert b.finish_calls == 1
    assert "still full and nothing could be sold or stored" in str(caught.value)


def test_bag_check_survives_a_blank_sheet_after_the_trip(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot()
    b.p["when_bag_full"] = "trip"
    b.p["bag_full_at"] = 50
    b._sheet_queue = [{"load": 100, "maxload": 100}, None]
    b.throw_by_rules = lambda: False
    monkeypatch.setattr(town, "unload", stand_in(town.unload, lambda b: 0))
    assert b.bag_check() is True


# ---------------------------------------------------------------- throw_by_rules
def test_throw_by_rules_throws_what_the_player_chose() -> None:
    b = Bot(bag=[{"name": "Junk", "count": 4}])
    b.p["bag_rules"] = {"junk": {"action": "throw", "keep": 0}}
    b.p["only_this_run"] = False
    calls: list[tuple[str, int]] = []
    b.throw_item = lambda name, n: calls.append((name, n)) or n
    assert b.throw_by_rules() is True
    assert calls == [("Junk", 4)]


def test_throw_by_rules_leaves_sell_and_store_alone() -> None:
    b = Bot(bag=[{"name": "Junk", "count": 4}, {"name": "Pelt", "count": 3}])
    b.p["bag_rules"] = {"junk": {"action": "throw", "keep": 0},
                        "pelt": {"action": "sell", "keep": 0}}
    b.p["only_this_run"] = False
    calls: list[str] = []
    b.throw_item = lambda name, n: calls.append(name) or n
    assert b.throw_by_rules() is True
    assert calls == ["Junk"]


def test_throw_by_rules_with_nothing_to_throw() -> None:
    b = Bot(bag=[{"name": "Junk", "count": 4}])
    assert b.throw_by_rules() is False


# ---------------------------------------------------------------- throw_item
def test_throw_item_of_something_that_is_not_there() -> None:
    b = Bot(bag=[{"name": "Junk", "count": 3}])
    assert b.throw_item("Mystery", 2) == 0
    assert b.keys == []


def test_throw_item_discards_and_confirms(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Junk", "count": 5}])
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(False)))
    b.dialog = lambda: {"open": True, "top": "are you sure?"}
    b.find_entry = lambda top, pattern: (0, 0)
    b.note_dialog = lambda d, why: None

    def choose(pattern: str) -> dict[str, Any] | None:
        b.link.bag = [{"name": "Junk", "count": 1}]
        return {"text": pattern}

    b.choose = choose
    assert b.throw_item("Junk", 4) == 4
    assert b.keys == ["shift+delete"]


def test_throw_item_answers_how_many(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Junk", "count": 5}])
    typed: list[int] = []
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(True)))

    def type_number(bot: Bot, n: int) -> bool:
        typed.append(n)
        b.link.bag = []
        return False                # as before: the box never says it took the number

    monkeypatch.setattr(town, "type_number", type_number)
    b.dialog = lambda: {"open": False}

    def choose(pattern: str) -> dict[str, Any] | None:
        b.link.bag = []
        return {"text": pattern}

    b.choose = choose
    assert b.throw_item("Junk", 5) == 5
    assert typed == [5]


def test_throw_item_says_when_the_game_did_not_throw(monkeypatch: pytest.MonkeyPatch) -> None:
    b = Bot(bag=[{"name": "Junk", "count": 5}])
    monkeypatch.setattr(town, "wait_input", stand_in(town.wait_input, returning(False)))
    b.dialog = lambda: {"open": False}
    assert b.throw_item("Junk", 2) == 0
    assert any("didn't throw Junk away" in line for line in b.lines)


class UseLink(Link):
    """The agent's `use` by uid, answering as scripted."""

    def __init__(self, replies: list[str]) -> None:
        super().__init__()
        self.replies = list(replies)
        self.sent: list[str] = []

    def json(self, line: str) -> Any:           # an agent answer: any JSON, as the real link
        if line == "mem bagfull":
            return [{"uid": 7, "name": "Large Red Potion", "count": 5}]
        return super().json(line)

    def send(self, line: str) -> str:
        self.sent.append(line)
        return self.replies.pop(0)


def test_an_odd_use_answer_waits_for_the_next_try_and_three_give_up() -> None:
    b = Bot()
    b.agent_act = True
    b.a = UseLink(["error: busy", "used", "error: busy", "error: busy", "error: busy"])
    assert b.use_item("Large Red Potion") is False       # not this time - and no arrow keys for it
    assert b.agent_act is True and b.keys == []
    assert b.use_item("Large Red Potion") is True        # a good answer forgives the odd one
    assert b.use_item("Large Red Potion") is False
    assert b.use_item("Large Red Potion") is False
    assert b.agent_act is True
    b.in_fight = True
    assert b.use_item("Large Red Potion") is False       # the third in a row: given up for the run
    assert b.agent_act is False
    assert b.keys == []                                  # and in a fight never the arrow keys
    assert any("go by keys again" in ln for ln in b.lines)
