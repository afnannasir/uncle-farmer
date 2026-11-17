"""Experience cards (engine/xpcard.py) against a pretend game shaped like the hand recordings of
2026-11-11: a card is a bag item whose use starts a 30-minute bonus, the character sheet (agent 60)
shows it running or paused with its time left, and `xpbonus pause|resume` toggles it only when
the sheet shows it the other way. A pause costs a minute and is invalid in a dungeon."""

from __future__ import annotations

import threading
from typing import Any

from unclefarmer.engine import xpcard
from conftest import as_bot
from unclefarmer.clock import Waitable


class Clock:
    def __init__(self) -> None:
        self.now = 1_700_000_000.0

    def time(self) -> float:
        return self.now

    def wait(self, event: Waitable, seconds: float) -> bool:
        self.now += seconds
        return False


class Agent:
    def __init__(self, game: Game) -> None:
        self.g = game

    def send(self, line: str) -> str:
        g = self.g
        g.sent.append(line)
        verb, _sp, want = line.partition(" ")
        if verb != "xpbonus":
            return "ok"
        if g.refuse:
            return g.refuse
        if g.bonus <= 0:
            return "refused: no experience card is running"
        if (want == "pause") == g.paused:
            return "refused: the card is already " + ("paused" if g.paused else "running")
        if want == "pause":
            g.seconds -= 60                       # the game's price for a pause
        g.paused = want == "pause"
        return "sent"


class Game:
    """What xpcard uses of the Bot, and the game's card behind the sheet."""

    def __init__(self, bonus: int = 0, paused: bool = False, seconds: int = 1800,
                 bag: list[dict[str, Any]] | None = None) -> None:
        self.clock = Clock()
        self.p: dict[str, Any] = {"xp_card_name": "2x experience acceleration card", "xp_card_pause": True}
        self.a = Agent(self)
        self.agent_version = 60
        self.stop_event = threading.Event()
        self.bonus, self.paused, self.seconds = bonus, paused, seconds
        self.bag: list[dict[str, Any]] = bag if bag is not None else []
        self.in_instance = False
        self.here = 101
        self.old_sheet = False          # an agent before 60: no card fields on the sheet
        self.refuse = ""
        self.read_at = self.clock.now
        self.reads = 0
        self.used: list[str] = []
        self.sent: list[str] = []
        self.lines: list[tuple[str, bool]] = []

    # the Bot's side
    def log(self, msg: str, speak: bool = False) -> None:
        self.lines.append((msg, speak))

    def sleep(self, seconds: float) -> None:
        self.clock.now += seconds

    def map_id(self) -> int:
        return self.here

    def refresh_sheet(self, force: bool = False) -> dict[str, Any]:
        if force:
            self.read_at = self.clock.now
            self.reads += 1
        sheet: dict[str, Any] = {"level": 79, "age": int(self.clock.now - self.read_at)}
        if not self.old_sheet:
            sheet.update(exp_bonus=self.bonus, exp_paused=self.paused,
                         exp_seconds=self.seconds if self.bonus else -1)
        return sheet

    def bag_rows(self) -> list[dict[str, Any]]:
        return self.bag

    def use_item(self, name: str) -> bool:
        for r in self.bag:
            if r["name"] == name and r["count"] > 0:
                r["count"] -= 1
                self.used.append(name)
                m = xpcard.strength(name)
                self.bonus, self.paused, self.seconds = int(round(((m or 1) - 1) * 100)), False, 1800
                return True
        return False

    def said(self, part: str) -> list[tuple[str, bool]]:
        return [(m, s) for m, s in self.lines if part in m]


BAG = [{"name": "Red Potion", "count": 50},
       {"name": "2x experience acceleration card", "count": 3},
       {"name": "3x experience acceleration card", "count": 1},
       {"name": "Experience Book", "count": 2}]


def fresh_bag() -> list[dict[str, Any]]:
    return [dict(r) for r in BAG]


# ---------------------------------------------------------------------------------------- reading
def test_the_cards_in_the_bag_come_weakest_first() -> None:
    rows = fresh_bag() + [{"name": "2x experience acceleration card", "count": 2},
                          {"name": "1.3x Experience Acceleration Card", "count": 1},
                          {"name": "1.75x experience acceleration card", "count": 4},
                          {"name": "2.5x experience acceleration card", "count": 1},
                          {"name": "3x experience acceleration card", "count": 0}]
    got = xpcard.cards_in(rows)
    assert [(s, n) for s, n, _c in got] == [(1.3, "1.3x Experience Acceleration Card"),
                                             (1.75, "1.75x experience acceleration card"),
                                             (2.0, "2x experience acceleration card"),
                                             (2.5, "2.5x experience acceleration card"),
                                             (3.0, "3x experience acceleration card")]
    assert got[2][2] == 5               # two stacks of one card count together
    assert xpcard.strength("Experience Book") is None


def test_the_sheet_says_whether_a_card_runs() -> None:
    assert xpcard.card_of(None) is None
    assert xpcard.card_of({"level": 79}) is None          # an agent before 60 can't tell
    assert xpcard.card_of({"exp_bonus": 0, "exp_paused": False, "exp_seconds": -1}) == {
        "bonus": 0, "paused": False, "seconds": -1}
    running = xpcard.card_of({"exp_bonus": 30, "exp_paused": False, "exp_seconds": 1735, "age": 5})
    assert running == {"bonus": 30, "paused": False, "seconds": 1730}    # its time went on since the sheet
    paused = xpcard.card_of({"exp_bonus": 30, "exp_paused": True, "exp_seconds": 1735, "age": 50})
    assert paused == {"bonus": 30, "paused": True, "seconds": 1735}


# ---------------------------------------------------------------------------------------- on the farm
def test_on_the_farm_a_paused_card_runs_again() -> None:
    g = Game(bonus=30, paused=True, seconds=1735)
    xpcard.on_farm(as_bot(g))
    assert g.sent == ["xpbonus resume"]
    assert not g.paused
    assert g.said("experience card running again (30% more experience")


def test_with_no_card_running_the_chosen_kind_starts_never_the_strongest() -> None:
    g = Game(bag=fresh_bag())                 # a 3x card is in the bag too
    xpcard.on_farm(as_bot(g))
    assert g.used == ["2x experience acceleration card"]
    assert g.bonus == 100
    assert g.said("experience card started: 2x experience acceleration card (2 more in the bag")
    g.bonus = 0                               # its half hour is over
    g.clock.now += xpcard.LOOK_EVERY
    xpcard.on_farm(as_bot(g))
    assert g.used == ["2x experience acceleration card"] * 2


def test_the_chosen_card_is_matched_whatever_its_case() -> None:
    g = Game(bag=fresh_bag())
    g.p["xp_card_name"] = "3X Experience Acceleration Card "
    xpcard.on_farm(as_bot(g))
    assert g.used == ["3x experience acceleration card"]


def test_with_the_chosen_kind_gone_no_other_card_is_used() -> None:
    g = Game(bag=fresh_bag())
    g.p["xp_card_name"] = "1.5x experience acceleration card"
    xpcard.on_farm(as_bot(g))
    assert g.used == []
    assert g.said("no 1.5x experience acceleration card in the bag") == [
        ("no 1.5x experience acceleration card in the bag - farming on without an experience card", True)]


def test_only_pausing_never_starts_a_card_but_still_resumes_one() -> None:
    g = Game(bag=fresh_bag())
    g.p["xp_card_name"] = ""
    xpcard.on_farm(as_bot(g))
    assert g.used == []
    g.bonus, g.paused = 30, True
    g.clock.now += xpcard.LOOK_EVERY
    xpcard.on_farm(as_bot(g))
    assert g.sent == ["xpbonus resume"]


def test_both_ticks_off_leave_the_card_alone() -> None:
    g = Game(bonus=30, paused=True, bag=fresh_bag())
    g.p = {}
    xpcard.on_farm(as_bot(g))
    xpcard.away(as_bot(g), "town")
    assert g.sent == [] and g.used == [] and g.reads == 0


def test_no_card_in_the_bag_is_said_once_and_looked_for_later() -> None:
    g = Game(bag=[{"name": "Red Potion", "count": 5}])
    xpcard.on_farm(as_bot(g))
    assert len(g.said("no 2x experience acceleration card in the bag")) == 1
    g.clock.now += 20 * xpcard.REST
    xpcard.on_farm(as_bot(g))
    assert len(g.said("no 2x experience acceleration card in the bag")) == 1
    g.bag.append({"name": "2x experience acceleration card", "count": 1})   # one bought on a town trip
    g.clock.now += 20 * xpcard.REST
    xpcard.on_farm(as_bot(g))
    assert g.used == ["2x experience acceleration card"]


def test_it_looks_at_the_card_only_now_and_then() -> None:
    g = Game(bonus=30, paused=False, bag=fresh_bag())
    xpcard.on_farm(as_bot(g))
    g.paused = True                           # the player paused it by hand
    g.clock.now += 5
    xpcard.on_farm(as_bot(g))
    assert g.sent == []
    g.clock.now += xpcard.LOOK_EVERY
    xpcard.on_farm(as_bot(g))
    assert g.sent == ["xpbonus resume"]


# ---------------------------------------------------------------------------------------- away
def test_leaving_the_farm_pauses_a_running_card() -> None:
    g = Game(bonus=30, paused=False, seconds=1735)
    xpcard.away(as_bot(g), "going to Giant Rock")
    assert g.sent == ["xpbonus pause"]
    assert g.paused and g.seconds == 1675
    assert g.said("experience card paused (30% more experience, 27 min 55 s left): going to Giant Rock")
    g.clock.now += 60
    xpcard.away(as_bot(g), "going to Giant Rock")     # still away: nothing more to pause
    assert g.sent == ["xpbonus pause"]
    xpcard.on_farm(as_bot(g))                         # back: it runs at the first look
    assert g.sent == ["xpbonus pause", "xpbonus resume"]


def test_no_pause_in_a_dungeon_near_the_end_or_without_the_tick() -> None:
    g = Game(bonus=30)
    g.in_instance = True                     # the game: pause is invalid in a dungeon
    xpcard.away(as_bot(g), "x")
    g.in_instance = False
    g.seconds = xpcard.MIN_LEFT - 1          # a pause would cost most of what is left
    xpcard.away(as_bot(g), "x")
    g.seconds = 1800
    g.p["xp_card_pause"] = False
    xpcard.away(as_bot(g), "x")
    assert g.sent == []
    assert not g.paused


def test_an_old_agent_is_said_once_and_nothing_is_sent() -> None:
    g = Game(bonus=30, paused=True, bag=fresh_bag())
    g.agent_version = 59
    xpcard.on_farm(as_bot(g))
    xpcard.away(as_bot(g), "x")
    g.clock.now += 600
    xpcard.on_farm(as_bot(g))
    assert g.sent == [] and g.used == []
    assert len(g.said("restart the game so the new agent loads")) == 1


def test_a_refused_toggle_is_said_once_and_tried_again_after_a_rest() -> None:
    g = Game(bonus=30, paused=True)
    g.refuse = "refused: the character sheet is too old - read it again"
    xpcard.on_farm(as_bot(g))
    g.clock.now += xpcard.LOOK_EVERY
    xpcard.on_farm(as_bot(g))                 # resting: nothing sent
    assert g.sent == ["xpbonus resume"]
    assert len(g.said("couldn't resume the experience card")) == 1
    g.refuse = ""
    g.clock.now += xpcard.REST
    xpcard.on_farm(as_bot(g))
    assert g.sent[-1] == "xpbonus resume" and not g.paused
    assert len(g.said("couldn't resume the experience card")) == 1


def test_a_card_the_player_changed_meanwhile_is_looked_at_again() -> None:
    g = Game(bonus=30, paused=True)
    g.refuse = "refused: the card is already running"
    xpcard.on_farm(as_bot(g))
    assert g.said("couldn't") == []           # nothing wrong: the player got there first
