# pyright: reportPrivateUsage=false
"""Escape as a kite (Phase 1b item 6, the Hunter's part): a monster on top that has struck lately,
HP under 90%: jump the 12 tiles away from it, but only when the facing says the jump goes away from
it, the ground behind is walkable and the target stays within reach."""
from __future__ import annotations

from typing import Any

import pytest
from conftest import ME, Clock, as_kind, fight, stand_in
from test_walking import Map

from unclefarmer import atlas
from unclefarmer.atlaslib import MapModel
from unclefarmer.engine import combat


def swing(t: float, caster: int) -> dict[str, Any]:
    return {"t": int(t * 1000), "caster": caster, "skill": "Bite", "hits": [[ME, 3, 500]], "targets": [[ME, 90000]]}


@pytest.fixture
def grid(monkeypatch: pytest.MonkeyPatch) -> Map:
    m = Map(W=120)

    def model(mid: int | str) -> MapModel:
        return as_kind(MapModel, m)
    monkeypatch.setattr(atlas, "model", stand_in(atlas.model, model))
    return m


def kite(monkeypatch: pytest.MonkeyPatch, clock: Clock, *, compass: int | None = 4, hp: int = 70000, wolf_x: float = 51.0,
         swung: bool = True, target_x: float = 54.0) -> str | None:
    _g, b, sb = fight(clock, ["Arcane Shot", "Escape"], [], 0.1)
    monkeypatch.setattr(b, "reach", lambda: 20.0, raising=False)
    monkeypatch.setattr(b, "map_id", lambda: 1, raising=False)
    sb.swings.clear()
    now = clock.now + 30
    me: dict[str, Any] = {"id": ME, "x": 50.0, "y": 50.0, "hp": hp, "maxhp": 100000}
    if compass is not None:
        me["compass"] = compass
    wolf = {"id": -5, "x": wolf_x, "y": 50.0, "hp": 9000, "maxhp": 9000, "name": "Wolf", "kind": "monster", "side": 1}
    target = {"id": -6, "x": target_x, "y": 50.0, "hp": 9000, "maxhp": 9000, "name": "Wolf", "kind": "monster", "side": 1}
    snap = {"self": me, "entities": [me, wolf, target]}
    if swung:
        sb._note_swing(swing(now - 0.5, -5), ME)
    up = sb.page()
    got = sb._kite_choice(combat._PickView(sb, snap, target, sb._usable(up, False, now)[0], up, now))
    return got["name"] if got else None


def test_a_monster_on_top_that_strikes_is_jumped_away_from(grid: Map, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    assert kite(monkeypatch, clock) == "Escape"                    # facing x+ (the wolf): the jump goes to x-


def test_no_kite_without_the_facing_at_full_hp_or_without_a_swing(grid: Map, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    assert kite(monkeypatch, clock, compass=None) is None           # an older agent
    assert kite(monkeypatch, clock, hp=95000) is None
    assert kite(monkeypatch, clock, swung=False) is None


def test_no_kite_toward_the_monster_into_a_wall_or_out_of_reach(grid: Map, clock: Clock, monkeypatch: pytest.MonkeyPatch) -> None:
    assert kite(monkeypatch, clock, compass=1) is None              # facing away: the jump would land past the wolf
    grid.walk[50, 44] = False                          # a wall 6 tiles behind
    assert kite(monkeypatch, clock) is None
    grid.walk[50, 44] = True
    assert kite(monkeypatch, clock, target_x=60.0) is None          # 22 from the landing: past our 20
