# pyright: reportPrivateUsage=false
"""Regressions for fighting attackers back while heading toward an exit during restock/travel.

1. Leaving Polson Wetland on foot (e.g. restock exit walk) or with Poison Immunity must fight back
   instead of letting monsters pile up behind the player; and riding across on a beast must not
   break off the walk every 5 s just to skip fight_back().
2. On any map, an HP drop caught right after an empty attackers() poll (< 0.5 s cache window) must
   read fresh casts inside fight_back() instead of walking on without fighting.
"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import ME, stand_in
from test_fighting import Bot as FightBot, Clock as FightClock, Link as FightLink, monster
from test_poison_wetland import ACTIVE, FERTILE_WILDS, INACTIVE, POLSON, exit_to, riding, traveller
from test_travel_combat import Mover
from test_walking import Clock as WalkClock
from unclefarmer import travel as travel_mod
from unclefarmer.clock import CLOCK


def test_leaving_polson_on_foot_for_restock_fights_back(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, _crossed = traveller(INACTIVE, here=POLSON)
    riding(monkeypatch, b, takes=False)          # on foot: no beast / combat cooldown
    seen_crossing: list[bool] = []

    def cross(leg: dict[str, Any]) -> None:
        seen_crossing.append(b.crossing_poison)
        b._map = leg["dest"]

    b.cross_exit = cross
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(POLSON, FERTILE_WILDS)]))
    b.travel(FERTILE_WILDS)
    assert seen_crossing == [False]              # on foot: must fight attackers on the way to the exit


def test_polson_with_poison_immunity_allows_fighting_back(monkeypatch: pytest.MonkeyPatch) -> None:
    b, _asked, _crossed = traveller(ACTIVE, here=POLSON)
    seen_crossing: list[bool] = []

    def cross(leg: dict[str, Any]) -> None:
        seen_crossing.append(b.crossing_poison)
        b._map = leg["dest"]

    b.cross_exit = cross
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(POLSON, FERTILE_WILDS)]))
    b.travel(FERTILE_WILDS)
    assert seen_crossing == [False]


def test_crossing_polson_on_beast_does_not_pause_walk_only_to_skip_fight(monkeypatch: pytest.MonkeyPatch) -> None:
    c = WalkClock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    m = Mover(c)
    m.p["fight_back"] = True
    m.traveling = True
    m.crossing_poison = True
    m.casting = {-41}
    assert m.attacked_on_the_way() is False


@pytest.mark.parametrize("src_map, dst_map", [(5121, 1061), (5125, 1061), (1061, 5110)])
def test_other_maps_restock_exit_travel_allows_fighting_back(
        monkeypatch: pytest.MonkeyPatch, src_map: int, dst_map: int) -> None:
    b, _asked, _crossed = traveller(INACTIVE, here=src_map)
    seen_crossing: list[bool] = []

    def cross(leg: dict[str, Any]) -> None:
        seen_crossing.append(b.crossing_poison)
        b._map = leg["dest"]

    b.cross_exit = cross
    monkeypatch.setattr(travel_mod, "plan", stand_in(travel_mod.plan, lambda *a, **k: [exit_to(src_map, dst_map)]))
    b.travel(dst_map)
    assert seen_crossing == [False]


def test_other_maps_fight_back_when_hit_right_after_empty_attacker_poll(monkeypatch: pytest.MonkeyPatch) -> None:
    c = FightClock()
    monkeypatch.setattr(CLOCK, "time", c.time)
    wolf = monster(-9, x=112.0, name="Wolf")
    casts: list[dict[str, Any]] = []
    b = FightBot(
        link=FightLink(json=lambda line: {"now": int(c.now * 1000), "casts": list(casts)}
                       if line.startswith("casts") else {}),
        entities=[wolf],
        me={"id": ME, "name": "Jorvanthor", "x": 100.0, "y": 100.0, "hp": 100.0, "maxhp": 100.0},
        map_id=5121,
    )
    b.__dict__["_snap_self"] = b.self_
    b.traveling = True
    b.walk_only = False
    b.crossing_poison = False
    b.last_hp = 100.0
    assert b.attacked_on_the_way() is False      # empty casts read caches _attackers_at = c.now
    c.advance(0.2)                               # < 0.5 s later: monster hits us and drops HP
    casts.append({"t": int(c.now * 1000), "caster": -9, "seq": 1, "hits": [[ME, 1, 20]], "targets": [[ME, 80]]})
    assert b.hit({"hp": 80.0}) is True
    fought: list[int] = []

    def fake_fight(mob: dict[str, Any]) -> None:
        fought.append(mob["id"])
        b.entities.clear()
        casts.clear()

    b.fight = fake_fight                         # type: ignore[assignment]
    b.fight_back()
    assert fought == [-9]
