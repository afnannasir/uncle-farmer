"""The three repair toolboxes, each on its own and handing over to a stronger one: Small Toolbox up
to level 60, Large Toolbox up to level 80, Alloy Toolbox any level (the game's item texts). The
Alloy Toolbox went unused: an unknown level put the cheapest box first, a box the game turned down
was never followed by the next one, and gear a weak box left worn passed as repaired."""

from __future__ import annotations

from typing import Any

import pytest
from conftest import as_bot
from test_repair import Bot, Link, dlg

from unclefarmer.engine import repair

# The game's two forms of the question (both in the language packs): Small/Large carry their level
# limit, the Alloy Toolbox's has none.
ASK_60 = "Do you want to repair all the equipment that is already equipped?Restrict repairable equipment level: 60 "
ASK_80 = "Do you want to repair all the equipment that is already equipped?Restrict repairable equipment level: 80 "
ASK_ANY = "Do you want to repair all the equipment that is already equipped?"
QUESTION = {"Small Toolbox": ASK_60, "Large Toolbox": ASK_80, "Alloy Toolbox": ASK_ANY}


class GameBot(Bot):
    """A bot whose game answers each toolbox with that toolbox's own question, uses one up when the
    repair is confirmed, and mends gear up to the box's level limit (gear above it stays broken)."""

    def __init__(self, level: int, bag: dict[str, int], gear_level: int | None = None,
                 hotkeys: dict[str, str] | None = None, sheet_level: int | None = None) -> None:
        self.link = Link([{"uid": 1, "name": "Helm", "equipped": -1}], None)   # broken: it needs a repair
        super().__init__(self.link)
        if level:
            self.last_me = {"sheet": {"level": level}}
        self.p["scan"] = {}
        self.gear_level = gear_level if gear_level is not None else level
        self.counts = dict(bag)
        self.hotkeys = {k.lower(): v for k, v in (hotkeys or {}).items()}
        self.by_key = {v: k for k, v in (hotkeys or {}).items()}
        self.uses: list[str] = []
        self.current: str | None = None
        self.sheet_level = sheet_level
        self.sheet_reads = 0

    def bag_rows(self) -> list[dict[str, Any]]:
        return [{"name": n, "count": c} for n, c in self.counts.items()]

    def key(self, k: str) -> None:
        super().key(k)
        self.current = self.by_key[k]
        self.uses.append(self.current)

    def use_item(self, name: str) -> bool:
        self.current = name
        self.uses.append(name)
        return True

    def wait_dialog(self, t: float) -> dict[str, Any] | None:
        return dlg(QUESTION[self.current or ""], "Confirm", "cancel")

    def choose(self, pat: str) -> dict[str, Any] | None:
        self.chosen.append(pat)
        name = self.current or ""
        self.counts[name] -= 1                              # the game uses the toolbox up
        top = repair.limit_of(name)
        if top is None or self.gear_level <= top:
            self.link.equipment[0]["equipped"] = 1          # mended
        return {"text": "picked"}

    def refresh_sheet(self, force: bool = False) -> dict[str, Any] | None:
        self.sheet_reads += 1
        if self.sheet_level:
            self.last_me = {"sheet": {"level": self.sheet_level}}
        return self.last_me.get("sheet")


ALL = {"Small Toolbox": 3, "Large Toolbox": 3, "Alloy Toolbox": 3}


@pytest.mark.parametrize("level, want", [
    (1, "Small Toolbox"), (55, "Small Toolbox"), (60, "Small Toolbox"),
    (61, "Large Toolbox"), (75, "Large Toolbox"), (80, "Large Toolbox"),
    (81, "Alloy Toolbox"), (95, "Alloy Toolbox"), (100, "Alloy Toolbox")])
def test_the_cheapest_toolbox_that_covers_the_level_repairs_and_is_used_up(level: int, want: str) -> None:
    b = GameBot(level, ALL)
    assert repair.repair(as_bot(b)) is True
    assert b.uses == [want]                                 # one toolbox, the right one
    assert b.counts[want] == 2 and sum(b.counts.values()) == 8
    assert b.link.equipment[0]["equipped"] == 1
    assert "all the gear repaired with a %s" % want in b.lines


@pytest.mark.parametrize("tool, level", [("Small Toolbox", 40), ("Large Toolbox", 70), ("Alloy Toolbox", 99)])
def test_each_toolbox_works_on_its_own(tool: str, level: int) -> None:
    b = GameBot(level, {tool: 1})
    assert repair.repair(as_bot(b)) is True
    assert b.uses == [tool] and b.counts[tool] == 0


@pytest.mark.parametrize("level", [61, 81, 90, 100])
def test_an_alloy_toolbox_alone_repairs_any_level(level: int) -> None:
    b = GameBot(level, {"Alloy Toolbox": 1}, hotkeys={"Alloy Toolbox": "5"})
    assert repair.toolboxes(as_bot(b)) == [("Alloy Toolbox", "5", 1)]
    assert repair.repair(as_bot(b)) is True
    assert b.keys == ["5"] and b.chosen and b.counts["Alloy Toolbox"] == 0


def test_the_alloy_question_without_a_level_limit_is_confirmed() -> None:
    b = GameBot(95, {"Alloy Toolbox": 2})
    assert repair.repair(as_bot(b)) is True
    assert b.chosen and not any("can't repair" in line for line in b.lines)


def test_an_unknown_level_is_read_from_the_sheet_before_choosing() -> None:
    b = GameBot(0, ALL, gear_level=70, sheet_level=70)
    assert repair.repair(as_bot(b)) is True
    assert b.sheet_reads == 1 and b.uses == ["Large Toolbox"]


def test_a_level_that_stays_unknown_takes_the_alloy_toolbox_first() -> None:
    b = GameBot(0, ALL, gear_level=95)
    assert [n for n, _k, _c in repair.toolboxes(as_bot(b))] == ["Alloy Toolbox", "Large Toolbox", "Small Toolbox"]
    assert repair.repair(as_bot(b)) is True
    assert b.uses == ["Alloy Toolbox"]                      # never a cheaper box that leaves gear worn


def test_no_sheet_reading_in_a_fight() -> None:
    b = GameBot(0, {"Alloy Toolbox": 1}, sheet_level=70, hotkeys={"Alloy Toolbox": "5"})
    b.in_fight = True
    assert repair.repair(as_bot(b)) is True
    assert b.sheet_reads == 0


def test_a_toolbox_the_game_turns_down_hands_over_to_a_stronger_one() -> None:
    b = GameBot(50, {"Small Toolbox": 1, "Alloy Toolbox": 1})
    real = b.wait_dialog

    def answer(t: float) -> dict[str, Any] | None:
        if b.current == "Small Toolbox":
            return dlg("Level 85 equipment is too high to be repaired")
        return real(t)
    b.wait_dialog = answer                                  # type: ignore[method-assign]
    assert repair.repair(as_bot(b)) is True
    assert b.uses == ["Small Toolbox", "Alloy Toolbox"]
    assert b.counts == {"Small Toolbox": 1, "Alloy Toolbox": 0}   # the turned-down box isn't spent
    assert "trying the Alloy Toolbox instead" in b.lines
    assert b.gear.failed_at == 0.0


def test_a_level_limit_under_the_characters_level_hands_over_before_confirming() -> None:
    # a level-90 character with only a Large Toolbox on its hotkey and an Alloy one in the bag
    b = GameBot(90, {"Large Toolbox": 1, "Alloy Toolbox": 1}, hotkeys={"Large Toolbox": "6"})
    order = repair.toolboxes(as_bot(b))
    assert [n for n, _k, _c in order] == ["Alloy Toolbox", "Large Toolbox"]   # what fits comes first
    assert repair.repair(as_bot(b)) is True and b.uses == ["Alloy Toolbox"]


def test_a_weak_box_tried_first_by_a_wrong_level_is_cancelled_by_its_own_limit() -> None:
    # the sheet says 75 but the gear is level 85 (an old reading): the Large Toolbox's question
    # shows its limit of 80, under nothing we know - it is confirmed, the helm stays broken, and the
    # Alloy Toolbox finishes the job
    b = GameBot(75, {"Large Toolbox": 1, "Alloy Toolbox": 1}, gear_level=85)
    assert repair.repair(as_bot(b)) is True
    assert b.uses == ["Large Toolbox", "Alloy Toolbox"]
    assert "still broken after the repair: Helm" in b.lines
    assert b.link.equipment[0]["equipped"] == 1


def test_the_limit_shown_stops_a_weak_box_for_a_higher_character(monkeypatch: pytest.MonkeyPatch) -> None:
    b = GameBot(85, {"Small Toolbox": 1, "Alloy Toolbox": 1})
    tools = [("Small Toolbox", None, 1), ("Alloy Toolbox", None, 1)]   # as if the Small one came first

    def first_small(bot: Any) -> list[tuple[str, str | None, int]]:
        return list(tools)
    monkeypatch.setattr(repair, "toolboxes", first_small)
    assert repair.repair(as_bot(b)) is True
    assert b.uses == ["Small Toolbox", "Alloy Toolbox"]
    assert b.counts["Small Toolbox"] == 1                   # cancelled at its question, not used up
    assert any("only repairs gear up to level 60, and this character is level 85" in line for line in b.lines)


def test_the_strongest_box_left_still_fails_loudly() -> None:
    b = GameBot(95, {"Large Toolbox": 1}, gear_level=95)    # no Alloy Toolbox at all
    assert repair.repair(as_bot(b)) is False
    assert b.uses == ["Large Toolbox"]                      # the last resort, still tried
    assert "still broken after the repair: Helm" in b.lines and b.gear.failed_at > 0


def test_a_toolbox_that_wasnt_used_up_is_said() -> None:
    b = GameBot(95, {"Alloy Toolbox": 2})
    real = b.choose

    def not_spent(pat: str) -> dict[str, Any] | None:
        got = real(pat)
        b.counts["Alloy Toolbox"] += 1
        return got
    b.choose = not_spent                                    # type: ignore[method-assign]
    assert repair.repair(as_bot(b)) is True
    assert any("still in the bag after the repair (2)" in line for line in b.lines)


def test_covers_and_limits() -> None:
    assert repair.covers(60, 60) and not repair.covers(60, 61) and not repair.covers(80, 0)
    assert repair.covers(None, 0) and repair.covers(None, 100)
    assert [repair.limit_of(n) for n in ("Small Toolbox", " large toolbox ", "Alloy Toolbox")] == [60, 80, None]
