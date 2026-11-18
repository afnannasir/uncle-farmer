# pyright: reportPrivateUsage=false
"""Fusion beasts on a trip: only for a walk long enough to be worth one (beasts.RIDE_MIN)."""

from __future__ import annotations

from typing import Any

import pytest

from conftest import as_bot
from test_beast_after_death import clock as clock
from test_beast_after_death import riding, swift
from unclefarmer.engine import beasts
from unclefarmer.engine.botparts.travel import TravelMixin
from unclefarmer.engine.botparts.walking import WalkingMixin


@pytest.mark.parametrize("left, rides", [(6.0, False), (29.0, False), (30.0, True), (float("inf"), True), (None, True)])
def test_a_beast_goes_on_only_for_a_long_enough_walk(clock: Any, left: float | None, rides: bool) -> None:
    g = swift(clock)
    as_bot(g).walk_left = left
    beasts.ensure(as_bot(g), beasts.TRAVEL)
    assert riding(g) is rides


def test_a_beast_already_on_stays_on_for_the_last_steps(clock: Any) -> None:
    g = swift(clock)
    beasts.ensure(as_bot(g), beasts.TRAVEL)
    assert riding(g)
    pressed = len(g.presses)
    as_bot(g).walk_left = 3.0
    beasts.ensure(as_bot(g), beasts.TRAVEL)
    assert riding(g) and len(g.presses) == pressed


class Trip:
    traveling = True
    walk_only = False
    walk_left: float | None = None
    walk_target: tuple[int, tuple[int, int]] | None = None

    def __init__(self) -> None:
        self.asked: list[tuple[int, Any]] = []

    def me(self) -> dict[str, Any]:
        return {"x": 10.0, "y": 10.0}

    def walk_dist(self, snap: dict[str, Any], e: dict[str, Any], limit: float | None = None) -> float:
        self.asked.append((snap["map"]["id"], (e["x"], e["y"])))
        return 12.0


@pytest.mark.parametrize("leg, goal", [({"do": "exit", "stand": [5, 6]}, (5, 6)), ({"do": "npc", "stand": [7, 8]}, (7, 8)),
                                       ({"do": "walk", "cell": [9, 1]}, (9, 1))])
def test_a_leg_measures_the_walk_to_where_it_goes(leg: dict[str, Any], goal: tuple[int, int]) -> None:
    t = Trip()
    assert TravelMixin._leg_walk(as_bot(t), 1081, leg) == 12.0
    assert t.asked == [(1081, goal)]


def test_a_teleport_needs_no_walk_and_an_unknown_leg_says_so() -> None:
    assert TravelMixin._leg_walk(as_bot(Trip()), 1, {"do": "item"}) == 0.0
    assert TravelMixin._leg_walk(as_bot(Trip()), 1, {"do": "skill"}) == 0.0
    assert TravelMixin._leg_walk(as_bot(Trip()), 1, {"do": "leave"}) is None


def test_the_walk_under_way_keeps_the_distance_current() -> None:
    t = Trip()
    t.walk_target = (5, (20, 30))
    WalkingMixin._note_walk_left(as_bot(t))
    assert t.walk_left == 12.0 and t.asked == [(5, (20, 30))]


def test_no_measuring_off_a_trip() -> None:
    t = Trip()
    t.traveling = False
    t.walk_target = (5, (20, 30))
    WalkingMixin._note_walk_left(as_bot(t))
    assert t.walk_left is None and t.asked == []
