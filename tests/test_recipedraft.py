"""The recipe draft from a recorded hand run (Phase 3): maps in order, the way walked on each, the
talks with the menu entries chosen (by the code OUT op=30 sends), timers and the end report."""
from __future__ import annotations

import struct

from unclefarmer import recipedraft as rd

NPC = -70


def map_change(mid: int, name: str) -> bytes:
    return bytes([6]) + struct.pack(">HHBB", mid, 9001, 0, 5) + name.encode()


def leaving() -> bytes:
    return bytes.fromhex("06ffff00000000")


def walk(x: float, y: float) -> bytes:
    return bytes([12]) + bytes(16) + struct.pack(">ii", int(x * 1000), int(y * 1000)) + bytes(1)


def npc(eid: int, name: str, x: float, y: float) -> bytes:
    m = bytearray(bytes([32]) + struct.pack(">i", eid) + bytes(32))
    m[33:37] = struct.pack(">HH", int(x * 100), int(y * 100))
    return bytes(m) + name.encode() + b",title,"


def talk(eid: int, code: int) -> bytes:
    return bytes([30]) + struct.pack(">ii", eid, code)


def menu(*lines: tuple[int, int, str]) -> bytes:
    body = "\n".join("%d\r%d\r\r\r\r\r\r\r\r\r\r%s" % line for line in lines)
    return bytes([62]) + bytes.fromhex("000002ffffffba01") + b"\r" + body.encode()


def spoken(text: str) -> bytes:
    return bytes([60, 10]) + text.encode()


def end_report() -> bytes:
    head = bytes([66, 7]) + struct.pack(">hhhhh", 0x0402, 0, 0, 0, 12)
    row = struct.pack(">ihB", 1001, 7, 79) + bytes(6) + struct.pack(">hii", 12, 3000000, 0) + bytes(10) + bytes(16)
    return head + row + bytes(4) + "Some Cave,Some Cave floor 2,Someone".encode("gbk")


def run() -> list[tuple[float, str, int, bytes]]:
    msgs = [
        (100.0, "IN", 6, map_change(1042, "underground")),
        (100.5, "IN", 32, npc(NPC, "Cave Keeper", 10, 6)),
        (101.0, "OUT", 12, walk(30, 4)), (102.0, "OUT", 12, walk(30, 20)), (103.0, "OUT", 12, walk(30, 40)),
        (104.0, "OUT", 12, walk(11, 40)),
        (105.0, "OUT", 30, talk(NPC, 0)),
        (105.3, "IN", 62, menu((8, 1, "Which floor?"), (264, 13, "Floor 13; Level 88 monsters"), (8, 9, "return"))),
        (106.0, "OUT", 30, talk(NPC, 13)),
        (106.5, "IN", 6, leaving()),
        (107.0, "IN", 6, map_change(1862, "demon cave")),
        (108.0, "OUT", 12, walk(2, 20)),
        (109.0, "IN", 60, spoken("56 minutes 38 seconds remaining")),
        (110.0, "IN", 44, bytes([44, 0, 0, 0, 1])),
        (500.0, "IN", 66, end_report()),
    ]
    return msgs


def test_a_run_becomes_stages_with_talks_and_choices() -> None:
    dr = rd.draft(run())
    first, cave = dr["stages"]
    assert first["map"] == 1042 and cave["map"] == 1862
    assert first["came_in_at"] == [30.0, 4.0] and first["left_from"] == [11.0, 40.0]
    assert first["way"] == [[30.0, 4.0], [30.0, 40.0], [11.0, 40.0]]        # the straight stretch thinned
    assert first["seconds"] == 6.5
    (k,) = first["talks"]
    assert k["npc_name"] == "Cave Keeper" and k["where"] == [10.0, 6.0]
    assert k["choices"] == [{"code": 13, "entry": "Floor 13; Level 88 monsters"}]
    assert cave["timers"] == ["56 minutes 38 seconds remaining"] and cave["door_sounds"] == 1
    (r,) = dr["reports"]
    assert r["place"] == "Some Cave" and r["total_kills"] == 12 and r["players"][0]["kills"] == 12
    text = "\n".join(rd.summary(dr))
    assert "Floor 13; Level 88 monsters (13)" in text and "12 kills in all" in text


def test_a_time_window_keeps_only_that_part() -> None:
    dr = rd.draft(run(), start=106.9, end=200.0)
    assert [s["map"] for s in dr["stages"]] == [1862] and dr["reports"] == []


def test_seconds_of_and_thin() -> None:
    assert rd.seconds_of("21:05") == 21 * 3600 + 5 * 60 and rd.seconds_of("00:00:07") == 7
    assert rd.thin([(0, 0), (1, 0.2), (2, 0), (2, 5)]) == [(0, 0), (2, 0), (2, 5)]
