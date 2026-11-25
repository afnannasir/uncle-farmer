# pyright: reportPrivateUsage=false
# White-box tests: they check internal helpers (leading-underscore names) directly.
"""Choosing the nearest of several places from a map whose way out isn't known yet (a gang base, a
secret room, a dungeon the run started in): every place cost the same unknown "leave" and the data's
first won (live, 2026-11-25: an XP loser started in a gang base went for Mario Basin's village, a cable
car and an airship away, with Tuneful Wetland's villages beside the base's way out). Now the bot gets
out first and chooses from there - unless a travel item or a teleport spell gives real ways from here.

The real map data is used: which place is nearest is a question about that data.
"""

from __future__ import annotations

import threading
from typing import Any

from conftest import as_bot

from unclefarmer import settings, travel
from unclefarmer.engine import town, xploss
from unclefarmer.engine.botparts.travel import note_way_out

GANG = 4001                        # Fragrance of Grass, a gang base
OUTER = (59.0, 93.0)               # its outer hall, beside its way out
TULIP = 1081                       # Tuneful Wetland: Tulip City and its Gang Entrance
OUT_AT_TULIP = (388.0, 181.0)      # just south of that Gang Entrance
CENTRAL = 1041
DUNGEON = 1503                     # Spider cave


class PlaceBot:
    """A character standing somewhere: map_id, me, the bag and profile; leave_here moves it."""

    def __init__(self, mid: int, at: tuple[float, float], leave_to: tuple[int, tuple[float, float]] | None = None,
                 bag: list[dict[str, Any]] | None = None) -> None:
        self.map, self.at, self.leave_to = mid, at, leave_to
        self.p: dict[str, Any] = dict(settings.PROFILE_DEFAULTS)
        self.bag = bag or []
        self.lines: list[str] = []
        self.left = 0
        self.stop_event = threading.Event()

    def log(self, line: str, speak: bool = False) -> None:
        self.lines.append(line)

    def map_id(self) -> int:
        return self.map

    def me(self) -> dict[str, Any]:
        return {"x": self.at[0], "y": self.at[1], "level": 69}

    def bag_rows(self) -> list[dict[str, Any]]:
        return self.bag

    def leave_here(self) -> bool:
        self.left += 1
        if self.leave_to is None:
            return False
        note_way_out(self, self.map, *self.leave_to)
        self.map, self.at = self.leave_to
        return True


def storehouses() -> list[town.Npc]:
    return [n for n in town.knowledge.service_npcs("storehouse") if not travel.is_instance(n[0])]


def test_from_a_gang_base_with_no_known_way_out_we_get_out_first_and_choose_from_there() -> None:
    b = PlaceBot(GANG, OUTER, leave_to=(TULIP, OUT_AT_TULIP))
    got = town.nearest(as_bot(b), None, storehouses())
    assert b.left == 1
    assert got is not None and got[0] == TULIP
    assert any("leaving" in line and "first" in line for line in b.lines)


def test_the_xp_loser_s_village_is_chosen_from_where_the_gang_base_lets_us_out() -> None:
    # The live case: started in the gang base, out on Tuneful Wetland - its village, not Mario Basin's.
    b = PlaceBot(GANG, OUTER, leave_to=(TULIP, OUT_AT_TULIP))
    v = xploss._Run(as_bot(b), {"where": "village"}).pick_village()
    assert b.left == 1
    assert v is not None and v[0] == TULIP


def test_a_known_way_out_is_used_without_leaving() -> None:
    b = PlaceBot(GANG, OUTER, leave_to=(TULIP, OUT_AT_TULIP))
    note_way_out(b, GANG, CENTRAL, (253.0, 181.0))
    got = town.nearest(as_bot(b), None, storehouses())
    assert b.left == 0
    assert got is not None and got[0] == CENTRAL


def test_a_dungeon_with_no_travel_item_is_left_first_too() -> None:
    b = PlaceBot(DUNGEON, (10.0, 10.0), leave_to=(CENTRAL, (253.0, 181.0)))
    got = town.nearest(as_bot(b), None, storehouses())
    assert b.left == 1
    assert got is not None and got[0] == CENTRAL


def test_a_travel_item_that_goes_somewhere_known_makes_it_a_real_choice_from_here() -> None:
    b = PlaceBot(DUNGEON, (10.0, 10.0), leave_to=(CENTRAL, (253.0, 181.0)),
                 bag=[{"name": "Tulip City Teleportation stone", "count": 1}])
    b.p["use_travel_items"] = True
    got = town.nearest(as_bot(b), None, storehouses())
    assert b.left == 0
    assert got is not None and got[0] == TULIP


def test_a_travel_item_left_unused_by_the_profile_doesn_t_count() -> None:
    b = PlaceBot(DUNGEON, (10.0, 10.0), leave_to=(CENTRAL, (253.0, 181.0)),
                 bag=[{"name": "Tulip City Teleportation stone", "count": 1}])
    b.p["use_travel_items"] = False
    town.nearest(as_bot(b), None, storehouses())
    assert b.left == 1


def test_one_place_only_is_no_choice_so_nothing_moves() -> None:
    b = PlaceBot(GANG, OUTER, leave_to=(TULIP, OUT_AT_TULIP))
    one = [n for n in storehouses() if n[0] == CENTRAL][:1]
    assert town.nearest(as_bot(b), None, one) == one[0]
    assert b.left == 0


def test_a_choice_on_this_very_map_is_made_from_here() -> None:
    b = PlaceBot(GANG, OUTER, leave_to=(TULIP, OUT_AT_TULIP))
    here = (GANG, "Gem Craftsman", 59.0, 93.0)
    assert town.nearest(as_bot(b), None, [here, *storehouses()]) == here
    assert b.left == 0


def test_without_step_out_nothing_moves() -> None:
    b = PlaceBot(GANG, OUTER, leave_to=(TULIP, OUT_AT_TULIP))
    assert town.nearest(as_bot(b), None, storehouses(), step_out=False) is not None
    assert b.left == 0


def test_when_the_way_out_can_t_be_taken_the_choice_is_made_from_here_as_before() -> None:
    b = PlaceBot(GANG, OUTER)                   # leave_here fails (an inner hall walled off, say)
    assert town.nearest(as_bot(b), None, storehouses()) is not None
    assert b.left == 1 and b.map == GANG


def test_outside_any_closed_map_nothing_moves() -> None:
    b = PlaceBot(TULIP, OUT_AT_TULIP, leave_to=(CENTRAL, (253.0, 181.0)))
    got = town.nearest(as_bot(b), None, storehouses())
    assert b.left == 0
    assert got is not None and got[0] == TULIP
