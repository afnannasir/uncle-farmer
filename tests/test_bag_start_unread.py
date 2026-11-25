"""What was ours before the run is never taken for what the run picked up: an error reply when
farming starts used to read as an empty bag, and then the rules sold, stored or threw away what the
player already had, and every piece of equipment of the ticked rarities was thrown."""

from __future__ import annotations

from typing import Any

from conftest import Clock, as_bot

from unclefarmer.engine import bagrules, gearthrow, town
from unclefarmer.engine.botparts.bag import BagMixin


class Link:
    def __init__(self, replies: list[Any]) -> None:
        self.replies = replies

    def json(self, line: str) -> Any:
        assert line in ("mem bag", "mem equipment")
        return self.replies.pop(0) if len(self.replies) > 1 else self.replies[0]


class Bot:
    def __init__(self, replies: list[Any], p: dict[str, Any] | None = None) -> None:
        self.a = Link(replies)
        self.p: dict[str, Any] = dict(p or {})
        self.clock = Clock()
        self.lines: list[str] = []
        self.bag_start: dict[str, int] | None = None
        self.bag_unread = False

    bag_rows = BagMixin.bag_rows

    def log(self, text: str, speak: bool = False) -> None:
        self.lines.append(text)

    def sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)


ERROR = {"ok": False, "error": "noworld: the connection is closed"}
BAG = [{"name": "Wolf Fur", "count": 40}, {"name": "Medium Red Potion", "count": 200}]


def test_an_error_reply_is_not_an_empty_bag() -> None:
    b = Bot([ERROR])
    assert b.bag_rows() == [] and b.bag_unread
    b.a.replies = [[]]
    assert b.bag_rows() == [] and not b.bag_unread         # a bag that really is empty


def test_the_start_is_read_again_until_the_game_sends_the_bag() -> None:
    b = Bot([ERROR, ERROR, BAG])
    began = b.clock.time()
    town.note_bag_start(as_bot(b))
    assert b.bag_start == {"wolf fur": 40, "medium red potion": 200}
    assert not getattr(b, "bag_start_unknown", False) and b.lines == []
    assert b.clock.time() - began == 2 * town.START_WAIT


def test_a_start_never_read_counts_nothing_as_picked_up() -> None:
    b = Bot([ERROR])
    town.note_bag_start(as_bot(b))
    assert b.bag_start == {} and getattr(b, "bag_start_unknown", False)
    assert any("couldn't read the bag before farming" in line for line in b.lines)
    bag = {"Wolf Fur": 40, "Medium Red Potion": 200}
    assert town.run_gains(as_bot(b), bag) == {"wolf fur": 0, "medium red potion": 0}
    p: dict[str, Any] = {"only_this_run": True, "bag_rules": {}}
    assert bagrules.plan(p, bag, gained=town.run_gains(as_bot(b), bag)) == []      # nothing touched


def test_gains_as_before_with_a_start_and_outside_a_run() -> None:
    b = Bot([BAG])
    b.bag_start = {"wolf fur": 10}
    assert town.run_gains(as_bot(b), {"Wolf Fur": 40}) == {"wolf fur": 30}
    b.bag_start = None                                       # a tool, no run: the whole bag counts
    assert town.run_gains(as_bot(b), {"Wolf Fur": 40}) == {"wolf fur": 40}


def piece(uid: int, name: str = "Firestorm Tabard", rarity: str = "crude") -> dict[str, Any]:
    return {"uid": uid, "type": 30000 + uid, "name": name, "equipped": 0, "n": 0,
            "o": gearthrow.RARITIES.index(rarity), "p": 0, "q": 0}


def test_equipment_unread_at_the_start_is_never_thrown_then_noted_at_the_next_spot() -> None:
    b = Bot([ERROR], {"gear_throw": ["crude"], "gear_keep_bonus": False})
    gearthrow.note_start(as_bot(b))
    assert getattr(b, "gear_start", None) is None
    assert any("couldn't read the equipment" in line for line in b.lines)
    b.a.replies = [[piece(1), piece(2)]]
    assert gearthrow.picked_up(as_bot(b)) == []              # nothing is the run's yet
    gearthrow.note_start(as_bot(b))                          # the plan's next spot
    assert getattr(b, "gear_start", None) == {1, 2}
    b.a.replies = [[piece(1), piece(2), piece(3)]]
    assert [r["uid"] for r in gearthrow.picked_up(as_bot(b))] == [3]


def test_no_details_in_time_leaves_the_rest_for_the_next_sweep(monkeypatch: Any) -> None:
    b = Bot([[piece(1), piece(2), piece(3)]], {"gear_throw": ["crude"], "gear_keep_bonus": True})
    b.__dict__["gear_start"] = set()
    asked: list[int] = []
    answers = {1: False, 2: None, 3: False}           # piece 2's details are late

    def has_bonus(_bot: Any, row: dict[str, Any]) -> bool | None:
        asked.append(row["uid"])
        return answers[row["uid"]]
    monkeypatch.setattr(gearthrow, "has_bonus", has_bonus)
    assert [r["uid"] for r in gearthrow.picked_up(as_bot(b))] == [1]
    assert asked == [1, 2]                            # piece 3 not asked: 2's late answer could be read as its
