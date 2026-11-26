# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""Farming medals (engine/medals.py), against a pretend game that does what the live one did
(2026-11-26): `gearwear` wears a piece or takes it off at once, a medal of a kind already worn takes
that one's place, and each piece has its own ~10 s cooldown ("N seconds, wear cooldown")."""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

import pytest
from conftest import Clock, as_bot

from unclefarmer import paths, settings
from unclefarmer.engine import medals

KIND = {"Contribution Medal": "contribution", "Four Star Attack Medal": "stars", "Five Star Attack Medal": "stars",
        "Benevolent Medal": "mentor", "Tutor Medal": "mentor", "Medal of adored by Female Soldiers": "honour",
        "Medal of the demon Hunting boy": "honour", "Blue medal of Hunting": "hunting"}
CONTRIB_A, CONTRIB_B, FOUR, FIVE, BENEVOLENT, TUTOR, SOLDIERS, BOY = 1, 2, 3, 4, 5, 6, 7, 8


class Game:
    def __init__(self, bot: "GearBot") -> None:
        self.bot = bot

    def json(self, line: str) -> Any:
        b = self.bot
        if line == "mem equipment":
            return [{"uid": u, "name": n, "equipped": int(u in b.worn), "o": 9} for u, n in b.pieces.items()]
        if line.startswith("said"):
            since = int(line.split()[1])
            return {"now": len(b.heard), "said": [{"text": t} for t in b.heard[since:]]}
        raise AssertionError(line)

    def send(self, line: str) -> str:
        b = self.bot
        verb, *rest = line.split()
        if verb == "gearinfo":
            uid = int(rest[0])
            b.heard.append("%s; durability 50.0 of 50.0; >||<plus: %s>||<; level 1" % (b.pieces[uid], b.plus.get(uid, "")))
            return "asked %d" % uid
        assert verb == "gearwear"
        uid, on = int(rest[0]), rest[1] == "on"
        b.sent.append((uid, on))
        left = b.changed.get(uid, -99.0) + 10.0 - b.clock.time()
        if left > 0:
            b.heard.append("%d seconds, wear cooldown" % int(left))
            return "wearing %d" % uid if on else "taking off %d" % uid
        if on:
            kind = KIND[b.pieces[uid]]
            for other in [w for w in b.worn if KIND[b.pieces[w]] == kind]:
                b.worn.discard(other)              # the one of its kind comes off by itself
                b.changed[other] = b.clock.time()
            b.worn.add(uid)
        else:
            b.worn.discard(uid)
        b.changed[uid] = b.clock.time()
        return "wearing %d" % uid if on else "taking off %d" % uid


class GearBot:
    def __init__(self, worn: set[int], p: dict[str, Any] | None = None) -> None:
        self.pieces = {CONTRIB_A: "Contribution Medal", CONTRIB_B: "Contribution Medal", FOUR: "Four Star Attack Medal",
                       FIVE: "Five Star Attack Medal", BENEVOLENT: "Benevolent Medal", TUTOR: "Tutor Medal",
                       SOLDIERS: "Medal of adored by Female Soldiers", BOY: "Medal of the demon Hunting boy"}
        self.worn = set(worn)
        self.changed: dict[int, float] = {}
        self.plus = {CONTRIB_A: "When attacked critical hit resistance increase 3",
                     CONTRIB_B: "When casting skills magic damage increase 2"}
        self.heard: list[str] = []
        self.sent: list[tuple[int, bool]] = []
        self.lines: list[str] = []
        self.p: dict[str, Any] = dict(settings.PROFILE_DEFAULTS, **(p or {}))
        self.agent_version = medals.GEAR_WEAR_AGENT
        self.clock = Clock()
        self.stop_event = threading.Event()
        self.a = Game(self)

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def sleep(self, s: float) -> None:
        self.clock.sleep(s)


def listed(*uids: int) -> list[dict[str, Any]]:
    names = GearBot(set()).pieces
    return [{"uid": u, "name": names[u]} for u in uids]


USUAL = {CONTRIB_A, BENEVOLENT, SOLDIERS, FOUR}


def farm(want: list[dict[str, Any]], back: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"farm_medals": True, "farm_medal_list": want, "medals_back": back is not None,
            "usual_medal_list": back or []}


def test_the_farming_medals_go_on_and_the_others_come_off() -> None:
    b = GearBot(USUAL, farm(listed(CONTRIB_B, FIVE, TUTOR)))
    assert medals.for_farming(as_bot(b)) is True
    assert b.worn == {CONTRIB_B, FIVE, TUTOR}           # Soldiers taken off: not ticked
    # each one put on took its kind's place by itself: nothing taken off by hand but the Soldiers
    assert [s for s in b.sent if not s[1]] == [(SOLDIERS, False)]


def test_medals_already_as_wanted_send_nothing() -> None:
    b = GearBot(USUAL, farm(listed(*sorted(USUAL))))
    assert medals.for_farming(as_bot(b)) is True
    assert b.sent == [] and any("already worn" in line for line in b.lines)


def test_the_usual_medals_go_back_on_wherever_the_run_ends() -> None:
    b = GearBot({CONTRIB_B, FIVE, TUTOR}, farm(listed(CONTRIB_B, FIVE, TUTOR), listed(*sorted(USUAL))))
    assert medals.put_back(as_bot(b)) is True
    assert b.worn == USUAL


def test_a_medal_still_cooling_down_waits_its_own_cooldown_then_goes() -> None:
    b = GearBot(USUAL, farm(listed(CONTRIB_A, BENEVOLENT, SOLDIERS, FIVE)))
    b.changed[FIVE] = b.clock.time() - 3.0               # changed by hand 3 s ago
    start = b.clock.time()
    assert medals.for_farming(as_bot(b)) is True
    assert FIVE in b.worn and b.clock.time() - start >= 7.0


def test_a_cooldown_the_game_announces_is_waited_for_once_more() -> None:
    b = GearBot(USUAL, farm(listed(CONTRIB_A, BENEVOLENT, SOLDIERS, FIVE)))
    b.changed[FIVE] = b.clock.time() - 2.0
    as_bot(b).__dict__["gear_changed"] = {}             # the bot didn't see that change
    assert medals.for_farming(as_bot(b)) is True
    assert FIVE in b.worn
    assert any("wear cooldown" in h for h in b.heard)


def test_two_ticked_medals_of_one_kind_are_said() -> None:
    b = GearBot(USUAL, farm(listed(FOUR, FIVE, CONTRIB_A)))
    assert medals.for_farming(as_bot(b)) is False
    assert any("same kind" in line for line in b.lines)


def test_a_medal_no_longer_in_the_bag_is_left_out() -> None:
    b = GearBot(USUAL, farm(listed(*sorted(USUAL)) + [{"uid": 99, "name": "Gold medal of Hunting"}]))
    assert medals.for_farming(as_bot(b)) is True
    assert any("Gold medal of Hunting" in line and "no longer" in line for line in b.lines)


def test_nothing_happens_until_the_box_is_ticked() -> None:
    b = GearBot(USUAL, {"farm_medal_list": listed(FIVE)})
    assert medals.for_farming(as_bot(b)) is True and b.sent == []
    b.p["farm_medals"] = True
    assert medals.wanted(b.p, "back") is None            # its own box too


def test_an_older_agent_changes_nothing_and_says_so() -> None:
    b = GearBot(USUAL, farm(listed(FIVE)))
    b.agent_version = 80
    assert medals.for_farming(as_bot(b)) is False
    assert b.sent == [] and any("restart the game" in line for line in b.lines)


def test_the_bonus_of_medals_that_share_a_name_is_read_once(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.setattr(paths, "PROFILES", str(tmp_path))
    b = GearBot(USUAL)
    medals.learn_bonuses(as_bot(b))
    got = medals.bonuses()
    assert got == {"1": "plus: When attacked critical hit resistance increase 3",
                   "2": "plus: When casting skills magic damage increase 2"}    # only the two Contribution Medals
    asked = len(b.heard)
    medals.learn_bonuses(as_bot(b))
    assert len(b.heard) == asked                          # known: not asked again


def test_colour_marks_are_left_out_of_a_medal_s_name() -> None:
    assert medals.display("_Arena lzs0lzs2250lzs1lzsGold medal") == "_Arena Gold medal"
