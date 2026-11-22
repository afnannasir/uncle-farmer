# pyright: reportPrivateUsage=false
"""picking.choose: the target choice as one pure function, and the replay that feeds it a night."""
from __future__ import annotations

import json
import math
from pathlib import Path
from collections.abc import Callable
from typing import Any

import pytest

from unclefarmer import pickreplay
from unclefarmer.engine import picking

ME = 4242


def mob(mid: int, x: float, y: float = 50.0, name: str = "Wolf", hp: int = 1000, maxhp: int = 1000) -> dict[str, Any]:
    return {"id": mid, "name": name, "kind": "monster", "x": x, "y": y, "hp": hp, "maxhp": maxhp}


def snap(*mobs: dict[str, Any]) -> dict[str, Any]:
    me = {"id": ME, "x": 50.0, "y": 50.0}
    return {"map": None, "self": me, "entities": [me, *mobs]}


def straight(s: dict[str, Any]):
    me = s["self"]

    def walk(e: dict[str, Any], limit: float | None) -> float:
        d = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        return math.inf if limit is not None and d > limit else d
    return walk


def never(e: dict[str, Any], steps: float) -> bool:
    return False


def choose(s: dict[str, Any], rules: picking.Rules | None = None, notes: picking.Notes | None = None,
           walk: Any = None, doomed: Callable[[dict[str, Any], float], bool] = never, **kw: Any) -> picking.Choice:
    return picking.choose(s, rules or picking.Rules(), notes or picking.Notes(now=100.0),
                          walk or straight(s), doomed, **kw)


def ids(c: picking.Choice) -> list[int]:
    return [e["id"] for e in c.targets]


def test_nearest_first_attackers_and_our_own_close_ones_before_it() -> None:
    s = snap(mob(-1, 60), mob(-2, 55), mob(-3, 80), mob(-4, 75))
    assert ids(choose(s)) == [-2, -1, -4, -3]
    n = picking.Notes(now=100.0, hitting=frozenset({-3}), tagged=frozenset({-4}), reach=19.5)
    assert ids(choose(s, notes=n)) == [-3, -4, -2, -1]
    far = picking.Notes(now=100.0, tagged=frozenset({-3}), reach=5.0)     # hurt, but 30 out: not close
    assert ids(choose(s, notes=far)) == [-2, -1, -4, -3]


def test_why_each_monster_was_left() -> None:
    s = snap(mob(-1, 60), mob(-2, 55, name="Rabbit"), mob(-3, 52), mob(-4, 90), mob(-5, 58), mob(-6, 57),
             mob(-7, 56, maxhp=picking.NPC_HP), mob(-8, 53, name="Tar Trap"), mob(-9, 54, hp=0))
    n = picking.Notes(now=100.0, skip={-3: 150.0}, kinds={(None, "rabbit"): 999.0})
    r = picking.Rules(limit=30, avoid_rects=((57.5, 40, 59, 60),), area=(40, 40, 95, 60))
    c = choose(s, r, n, doomed=lambda e, steps: e["id"] == -6, not_monsters=frozenset({"tar trap"}))
    assert ids(c) == [-1]
    assert c.left == {"skip": 1, "kind": 1, "far": 1, "avoid": 1, "doomed": 1}
    assert c.names[-8] == "Tar Trap" and -9 not in c.names         # seen, but not a monster to fight
    assert choose(snap(mob(-4, 99)), picking.Rules(area=(40, 40, 62, 60))).left == {"area": 1}
    assert c.steps[-1] == pytest.approx(10.0)


def test_far_or_no_way_and_where_those_with_no_way_stood() -> None:
    s = snap(*(mob(-i, 60 + i) for i in range(1, 8)))

    def walk(e: dict[str, Any], limit: float | None) -> float:
        return math.inf if e["id"] <= -3 or limit is not None else 12.0
    c = choose(s, picking.Rules(limit=40), walk=walk)
    assert c.left == {"far": 2, "no way": 5} and len(c.no_way) == picking.NO_WAY_KEPT
    assert ids(choose(s, walk=walk)) == [-1, -2]


def test_an_attacker_s_old_skips_are_forgiven_by_the_caller_not_by_choose() -> None:
    s = snap(mob(-1, 60, name="Wolf"))
    skip = {-1: 500.0}
    kinds = {(None, "wolf"): 500.0}
    n = picking.Notes(now=100.0, hitting=frozenset({-1}), skip=skip, kinds=kinds)
    c = choose(s, notes=n)
    assert ids(c) == [-1] and c.forgiven == [(-1, (None, "wolf"))]
    assert skip == {-1: 500.0} and kinds == {(None, "wolf"): 500.0}      # pure: nothing changed
    stuck = picking.Notes(now=100.0, hitting=frozenset({-1}), skip=skip, walk_failed={-1: 200.0})
    c = choose(s, notes=stuck)
    assert ids(c) == [] and c.left == {"skip": 1} and c.forgiven == []
    refused = picking.Notes(now=100.0, hitting=frozenset({-1}), refused_again=frozenset({-1}))
    assert choose(s, notes=refused).left == {"skip": 1}


def test_an_attacker_outside_the_area_or_past_the_chase_is_still_fought() -> None:
    s = snap(mob(-1, 95))
    n = picking.Notes(now=100.0, hitting=frozenset({-1}))
    assert ids(choose(s, picking.Rules(area=(40, 40, 60, 60), limit=10), n)) == [-1]


def test_names_bosses_and_another_order() -> None:
    s = snap(mob(-1, 60, name="Orc Archer"), mob(-2, 55, name="Orc Mage"), mob(-3, 52, name="Wolf"))
    assert ids(choose(s, picking.Rules(only="orc", skip="mage"))) == [-1]

    def furthest(e: dict[str, Any], steps: float, ctx: picking.Context) -> tuple[Any, ...]:
        return (-steps,)
    assert ids(choose(s, order=furthest)) == [-1, -2, -3]


def test_placed_wants_a_real_position_on_the_map() -> None:
    assert picking.placed({"x": 5, "y": 5}, {"w": 10, "h": 10})
    assert not picking.placed({"x": 50, "y": 5}, {"w": 10, "h": 10})
    assert not picking.placed({"x": float("nan"), "y": 5}, {})
    assert not picking.placed({"x": True, "y": 5}, {})
    assert picking.placed({"x": 500, "y": 5}, {})


# ------------------------------------------------------------------ the replay

def line(t: float, way: str, op: int, payload: bytes) -> str:
    h, rest = divmod(t, 3600)
    m, s = divmod(rest, 60)
    return "%02d:%02d:%06.3f %s op=%d len=%d hex=%s\n" % (h, m, s, way, op, len(payload), (bytes([op]) + payload).hex())


def appear(mid: int, x: float, y: float, name: str, hp: int = 1000) -> bytes:
    """An IN op=32 payload (after the opcode byte): id, kind, flags, HP, max HP ... x, y, name."""
    body = bytearray(36)
    body[0:4] = mid.to_bytes(4, "big", signed=True)
    body[4:6] = (100).to_bytes(2, "big")
    body[7:11] = hp.to_bytes(4, "big")
    body[11:15] = hp.to_bytes(4, "big")
    body[32:34] = int(x * 100).to_bytes(2, "big")
    body[34:36] = int(y * 100).to_bytes(2, "big")
    return bytes(body) + name.encode() + b","


def cast(text: str) -> bytes:
    return text.encode()


@pytest.fixture
def night(tmp_path: Path) -> tuple[str, str]:
    t0 = 3600.0          # 01:00
    rows = ["header\n"] * 2
    rows.append(line(t0, "IN", 32, appear(-5, 70, 50, "Wolf")))
    rows.append(line(t0, "IN", 32, appear(-6, 56, 50, "Wolf")))
    rows.append(line(t0 + 1, "OUT", 15, b"\x00" * 8))
    rows.append(line(t0 + 1.2, "IN", 41, cast("%d,1,Level 1 Arcane Shot\n%d,5000\n-9,900" % (ME, ME))))
    rows.append(line(t0 + 1.3, "IN", 41, cast("-5,2,Bite\n-5,1000\r\n\r\n\r\n\r\n%d,3,40" % ME)))
    # the pick at 01:00:40 (fight record at 01:00:42, 2 s long); -6 dies to someone else just after
    rows.append(line(t0 + 40.7, "IN", 41, cast("777,9,Level 3 Fireball\n777,8000\n-6,0")))
    rows.append(line(t0 + 60, "IN", 32, appear(-7, 52, 50, "Wolf")))
    packets = tmp_path / "packets.1.log"
    packets.write_text("".join(rows), encoding="utf-8")
    field = tmp_path / "field.jsonl"
    rec = {"at": "2026-11-17 01:00:42", "kind": "fight", "who": "Tester", "map": 5302, "me": [50.0, 50.0],
           "id": -6, "name": "Wolf", "reach": 3.0, "end_s": 2.0, "steps": 6.0}
    other = dict(rec, who="Someone", id=-5)
    field.write_text(json.dumps(other) + "\n" + json.dumps(rec) + "\n", encoding="utf-8")
    return str(packets), str(field)


def test_the_replay_rebuilds_the_view_and_judges_each_order(night: tuple[str, str], monkeypatch: pytest.MonkeyPatch) -> None:
    def walk(self: Any, snap: dict[str, Any], e: dict[str, Any], limit: float | None = None) -> float:
        me = snap["self"]
        d = math.hypot(e["x"] - me["x"], e["y"] - me["y"])
        return math.inf if limit is not None and d > limit else d
    monkeypatch.setattr(pickreplay._Walker, "walk_dist", walk)  # pyright: ignore[reportPrivateUsage]
    packets, field = night
    assert pickreplay.our_id(packets) == ME
    got = pickreplay.replay(packets, field, "Tester", chase=None)
    assert len(got) == 1 and got[0]["seen"]
    # -5 struck us at 01:00:01, long before the pick: no attacker by then, so the nearer -6 goes first
    bot = got[0]["bot"]
    assert bot["id"] == -6 and bot["same"]
    assert bot["walk_s"] == pytest.approx((6.0 - 3.0) / 4.0)
    assert bot["taken"]              # someone else killed it 0.7 s later, before our first hit
    lines = pickreplay.report(got)
    assert lines[0].startswith("1 picks replayed") and "same as the bot 100%" in lines[1]


def test_the_replay_of_someone_with_no_picks_is_empty(night: tuple[str, str]) -> None:
    packets, field = night
    assert pickreplay.replay(packets, field, "Nobody") == []
    assert pickreplay.report([])[0].startswith("0 picks")


def test_cast_rows_read_every_hp_line_and_the_hit_section() -> None:
    caster, hp, hits = pickreplay.cast_rows(b"\x29" + b"-5,2,Bite\n-5,900\n-6,0\r\nx\r\n\r\n\r\n42,3,40\n43,2,0")
    assert caster == -5 and (-6, 0) in hp and (-5, 900) in hp
    assert hits == [(42, 3), (43, 2)]
