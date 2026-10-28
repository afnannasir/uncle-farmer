"""A Priest or a Sorceress following a player heals them, and the rest of the team in sight,
with the class's quick heal: the weakest teammate under the mark selected by id, healed, our
target selected again. Never while our own HP is in danger, never anyone off the team, and no
more after heals that raise nobody's HP."""

from __future__ import annotations

from unclefarmer.engine import teamheal
import pytest
from conftest import as_bot, stand_in, Clock
from typing import Any
from unclefarmer.engine.combat import Press, Skill

ME = 500
LEADER = 700
OTHER = 701
STRANGER = 702
MONSTER = -321


class Book:
    ok = True

    def __init__(self) -> None:
        self.heal = {"name": "Healing Wave", "key": "3", "still": "alt+3", "page": 1, "range": 12}
        self.cooling = False

    def healer(self) -> Skill | None:
        return self.heal

    def gcd_wait(self, now: float) -> bool:
        return self.cooling

    def ready(self, x: dict[str, int | str], now: float) -> bool:
        return True

    def press_support(self, x: Skill, now: float) -> Press | None:
        return x["still"], x["name"]


class Link:
    def __init__(self, members: list[str] | tuple[()]=(), no_team: bool=False) -> None:
        self.sent: list[str] = []
        self.members = list(members)
        self.no_team = no_team

    def send(self, line: str) -> str:
        self.sent.append(line)
        return "selected"

    def json(self, what: str) -> Any:
        if what == "team":
            return {"now": 1, "members": [{"name": n, "age_ms": 100} for n in self.members]}
        return {"said": [{"text": "no team"}] if self.no_team else []}


class Healer:
    def __init__(self, clock: Clock, following: str="Leader", members: list[str] | tuple[()]=()) -> None:
        self.team_names: set[str] = set()
        self.team_heal_misses = 0
        self.team_heal_off = False
        self.clock = clock
        self.p = {"team_heal": True, "team_heal_below": 70, "emergency_below": 50}
        self.following: str | None = following
        self.agent_act = True
        self.agent_version = 43
        self.walk_only = False
        self.in_fight = False
        self.skillbook = Book()
        self.a = Link(members)
        self.phrases = {4: "/6"}
        self.lines: list[str] = []
        self.pressed: list[str] = []

    def log(self, line: str, speak: bool=False) -> None:
        self.lines.append(line)

    def key(self, k: str, ms: int | None=None) -> None:
        self.pressed.append(k)

    def sleep(self, s: float) -> None:
        self.clock.advance(s)

    def ui(self) -> dict[str, Any]:
        return {"target": MONSTER}


def player(eid: int, name: str, hp: int, maxhp: int=10000, x: float=3.0, y: float=0.0) -> dict[str, int | str | float]:
    return {"id": eid, "kind": "player", "name": name, "hp": hp, "maxhp": maxhp, "x": x, "y": y}


def snap(*others: dict[str, Any], my_hp: int = 9000) -> dict[str, Any]:
    return {"self": {"id": ME, "x": 0.0, "y": 0.0, "hp": my_hp, "maxhp": 10000},
            "entities": [player(ME, "Me", my_hp)] + list(others)}


def test_the_player_we_follow_is_healed_and_our_target_comes_back(clock: Clock) -> None:
    b = Healer(clock)
    assert teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 5000)))
    assert b.a.sent == ["select %d" % LEADER, "select %d" % MONSTER]
    assert b.pressed == ["alt+3"]
    assert any("heals Leader with Healing Wave" in line for line in b.lines)


def test_the_weakest_teammate_goes_first_and_strangers_never(clock: Clock) -> None:
    b = Healer(clock)
    b.team_names = {"other"}
    teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 6000), player(OTHER, "Other", 3000),
                                player(STRANGER, "Stranger", 1000)))
    assert b.a.sent[0] == "select %d" % OTHER


def test_nobody_is_healed_above_the_mark_out_of_reach_or_when_not_following(clock: Clock) -> None:
    b = Healer(clock)
    assert not teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 8000)))      # 80%
    assert not teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 5000, x=20.0)))  # past the heal's 12
    b.following = None
    assert not teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 5000)))
    assert b.pressed == []


def test_our_own_low_hp_comes_first(clock: Clock) -> None:
    b = Healer(clock)
    assert not teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 3000), my_hp=4000))
    assert b.pressed == []


def test_it_off_or_no_select_by_id_means_no_heal(clock: Clock) -> None:
    b = Healer(clock)
    b.p["team_heal"] = False
    assert not teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 3000)))
    b = Healer(clock)
    b.agent_act = False
    assert not teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 3000)))


def test_heals_that_raise_nobodys_hp_stop_after_a_few(clock: Clock) -> None:
    b = Healer(clock)
    for _round in range(teamheal.TEAM_MISSES + 1):
        teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 5000)))
        clock.advance(teamheal.HEAL_CHECK + teamheal.HEAL_GAP)
    assert b.team_heal_off is True
    assert any("doesn't reach teammates" in line for line in b.lines)
    assert b.pressed == ["alt+3"] * teamheal.TEAM_MISSES


def test_a_heal_that_lands_keeps_healing(clock: Clock) -> None:
    b = Healer(clock)
    teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 5000)))
    teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 5000)))     # too soon to tell
    clock.advance(teamheal.HEAL_CHECK + teamheal.HEAL_GAP)
    teamheal.look_after(as_bot(b), snap(player(LEADER, "Leader", 6000)))
    assert b.team_heal_misses == 0 and b.pressed == ["alt+3", "alt+3"]


def test_learning_the_team_presses_the_team_phrase_and_keeps_the_names(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(teamheal.gamekeys, "phrase_key_list", stand_in(teamheal.gamekeys.phrase_key_list, lambda: ["shift+%d" % i for i in range(1, 13)]))
    b = Healer(clock, members=["Other"])
    teamheal.learn(as_bot(b))
    assert b.pressed == ["shift+4"] * teamheal.LEARN_PRESSES
    assert b.team_names == {"other"}
    teamheal.learn(as_bot(b))                                   # not again so soon
    assert len(b.pressed) == teamheal.LEARN_PRESSES


def test_no_team_ends_the_round(clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(teamheal.gamekeys, "phrase_key_list", stand_in(teamheal.gamekeys.phrase_key_list, lambda: ["shift+%d" % i for i in range(1, 13)]))
    b = Healer(clock)
    b.a.no_team = True
    teamheal.learn(as_bot(b))
    assert b.pressed == ["shift+4"]
